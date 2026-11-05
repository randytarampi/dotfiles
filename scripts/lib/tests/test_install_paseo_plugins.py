"""Owner-gated, bounded Paseo plugin installation policy tests."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[2] / "install-paseo-plugins.py"
SPEC = importlib.util.spec_from_file_location("install_paseo_plugins", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_codexbar_requires_executable_and_machine_status_unsupported_on_015():
    selected, omitted = module.plan(
        lambda name: "/usr/bin/" + name if name == "codexbar" else None,
        (0, 15, 0),
        node_version=(22, 18, 0),
        daemon_codexbar_verified=True,
    )
    assert "uiyzzi/codexbar-usage" in selected
    assert "dutchakdev/machine-status" not in selected
    assert (
        "dutchakdev/machine-status",
        "published support ends at Paseo 0.13",
    ) in omitted
    without_cli, skipped = module.plan(lambda name: None, (0, 15, 0))
    assert "uiyzzi/codexbar-usage" not in without_cli
    assert len(skipped) == 3


def test_preview_and_gate_refuse_all_mutations(monkeypatch):
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/local/bin/paseo" if name == "paseo" else None
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        assert argv == ["/usr/local/bin/paseo", "--version"]
        return Mock(stdout="0.15.0", returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.delenv(module.GATE, raising=False)
    assert module.main([]) == 0
    assert module.main(["--dry-run"]) == 0
    assert module.main(["--apply"]) == 1
    assert all(c == ["/usr/local/bin/paseo", "--version"] for c in calls)


def test_apply_skips_unsupported_plugin_and_installs_exact_registry_ids(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setenv(module.GATE, "1")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Mock(
            stdout=("v22.18.0" if argv[0] == "/bin/node" else "0.15.0")
            if argv[1:] == ["--version"]
            else "",
            returncode=0,
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main(["--apply", "--codexbar-daemon-verified"]) == 0
    assert calls[0] == ["/bin/paseo", "--version"]
    added = [cmd[3] for cmd in calls[2:]]
    assert added == [
        name for name, req in module.PLUGINS if req != "paseo-0.8-through-0.13"
    ]
    assert all(cmd[1:3] == ["plugin", "add"] for cmd in calls[2:])


def test_invalid_version_and_unsupported_option_fail_closed(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda _: "/bin/paseo")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: Mock(stdout="development", returncode=0),
    )
    assert module.main(["--apply"]) == 1
    import pytest

    with pytest.raises(SystemExit) as failure:
        module.main(["--ap"])
    assert failure.value.code == 2


def test_plugin_compatibility_is_strict_about_paseo_node_and_platform():
    import pytest

    with pytest.raises(ValueError, match="version unknown"):
        module.version_tuple("0.15.0-dev")
    with pytest.raises(ValueError, match="version unknown"):
        module.version_tuple("development 0.15.0")
    assert module.version_tuple("paseo 0.15.1") == (0, 15, 1)

    selected, omitted = module.plan(
        lambda _: "/bin/tool",
        (0, 11, 0),
        platform="linux",
        node_version=(22, 17, 9),
    )
    assert "stevecastaneda/progress" not in selected
    assert "roundreddot/better-paseo" not in selected
    assert "dutchakdev/machine-status" not in selected
    assert "uiyzzi/codexbar-usage" not in selected
    reasons = dict(omitted)
    assert reasons["stevecastaneda/progress"] == "Node >=22.18 required"
    assert reasons["roundreddot/better-paseo"] == "Paseo >=0.12 required"
    assert reasons["dutchakdev/machine-status"] == "macOS required"
    assert "daemon PATH" in reasons["uiyzzi/codexbar-usage"]

    selected, _ = module.plan(
        lambda _: "/bin/tool",
        (0, 12, 0),
        platform="darwin",
        node_version=(22, 18, 0),
        daemon_codexbar_verified=True,
    )
    assert "stevecastaneda/progress" in selected
    assert "roundreddot/better-paseo" in selected
    assert "dutchakdev/machine-status" in selected
    assert "uiyzzi/codexbar-usage" in selected
