"""Owner-gated, bounded Paseo plugin installation policy tests."""

import importlib.util
import json
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
    # Node-missing and pre-0.16 daemons leave progress, codexbar, machine-status,
    # devices, cheapskate and shared-browser out; skills/mermaid/doc-viewer/
    # context-mode/better-paseo remain.
    assert len(skipped) == 6


def test_preview_and_gate_refuse_all_mutations(monkeypatch):
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "/usr/local/bin/paseo" if name == "paseo" else None,
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:] == ["--version"]:
            return Mock(stdout="0.15.0", returncode=0)
        if argv[1:] == ["plugin", "ls", "--json"]:
            return Mock(stdout="[]", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout='{"daemonVersion": "0.15.0"}', returncode=0)
        raise AssertionError("unexpected command")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.delenv(module.GATE, raising=False)
    assert module.main([]) == 0
    assert module.main(["--dry-run"]) == 0
    assert module.main(["--apply"]) == 1
    assert all("add" not in c and "enable" not in c for c in calls)


def test_apply_skips_unsupported_plugin_and_installs_exact_registry_ids(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(
        module,
        "resolve_chromium",
        lambda *args, **kwargs: (
            "resolved",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ),
    )
    monkeypatch.setattr(module, "managed_wrapper", lambda *args, **kwargs: "written")
    monkeypatch.setattr(module.platform_module, "system", lambda: "Darwin")
    monkeypatch.setenv(module.GATE, "1")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:] == ["--version"]:
            return Mock(
                stdout="v22.18.0" if argv[0] == "/bin/node" else "0.15.0",
                returncode=0,
            )
        if argv[1:] == ["plugin", "ls", "--json"]:
            return Mock(stdout="[]", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout='{"daemonVersion": "0.15.0"}', returncode=0)
        return Mock(stdout="", returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main(["--apply", "--codexbar-daemon-verified"]) == 0
    assert calls[0] == ["/bin/paseo", "--version"]
    added = [call[3] for call in calls if call[1:3] == ["plugin", "add"]]
    # Paseo 0.15.0 + Node 22.18: machine-status (Paseo <0.14) and shared-browser
    # (Node >=24) are the two prerequisites that must keep them out.
    assert added == [
        "stevecastaneda/progress",
        "uiyzzi/codexbar-usage",
        "roundreddot/better-paseo",
        "gpambrozio/skills",
        "dutchakdev/mermaid",
        "nkwib/doc-viewer",
        "omercnet/context-mode",
    ]
    assert "dutchakdev/machine-status" not in added
    assert "asherlc/devices" not in added
    assert "asherlc/cheapskate" not in added
    assert "omercnet/shared-browser" not in added
    assert not any(call[:2] == ["launchctl", "setenv"] for call in calls)


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


def test_registry_mapping_and_reconciliation():
    installed = [
        {
            "id": "runtime-doc",
            "enabled": False,
            "installation": {"identity": {"registry": {"id": "nkwib/doc-viewer"}}},
        },
        {
            "id": "legacy-runtime",
            "enabled": True,
            "installation": {"identity": {"registry": {"id": "old/plugin"}}},
        },
    ]
    adds, enables, unlisted = module.plan(installed)
    assert "nkwib/doc-viewer" not in adds
    assert ("nkwib/doc-viewer", "runtime-doc") in enables
    assert ("old/plugin", "legacy-runtime") in unlisted


def test_chromium_resolver_platform_candidates():
    def exists(path):
        return path in {
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            r"C:\Users\x\Chromium\Application\chrome.exe",
            "/usr/bin/chromium",
        }

    assert module.resolve_chromium("Darwin", exists=exists)[0] == "resolved"
    status, path = module.resolve_chromium(
        "Windows", exists=exists, environ={"LOCALAPPDATA": r"C:\Users\x"}
    )
    assert (status, path) == (
        "resolved",
        r"C:\Users\x\Chromium\Application\chrome.exe",
    )
    assert module.resolve_chromium("Linux", exists=exists)[1] == "/usr/bin/chromium"
    assert module.resolve_chromium("Linux", exists=lambda _: False)[0] == "unresolved"


def test_managed_wrapper_is_idempotent_and_requires_recognizable_exec(tmp_path):
    wrapper = tmp_path / "launchd-daemon.sh"
    wrapper.write_text('exec "$HOME/bin/paseo" daemon run\n')
    assert (
        module.managed_wrapper(
            wrapper, "/Applications/Chromium.app/Contents/MacOS/Chromium", backup=False
        )
        == "written"
    )
    first = wrapper.read_text()
    module.managed_wrapper(
        wrapper, "/Applications/Chromium.app/Contents/MacOS/Chromium", backup=False
    )
    assert wrapper.read_text() == first
    invalid = tmp_path / "invalid.sh"
    invalid.write_text("echo hello\n")
    assert (
        module.managed_wrapper(invalid, "/chromium", backup=False)
        == "unrecognized-wrapper"
    )


def test_dry_run_reconciliation_does_not_mutate(monkeypatch):
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/bin/paseo" if name == "paseo" else None
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1:] == ["--version"]:
            return Mock(stdout="0.16.2", returncode=0)
        if argv[1:] == ["plugin", "ls", "--json"]:
            return Mock(stdout="[]", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout='{"daemonVersion": "0.16.2"}', returncode=0)
        raise AssertionError("dry-run attempted mutation")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main(["--dry-run"]) == 0
    assert all(
        "add" not in call and "enable" not in call and "remove" not in call
        for call in calls
    )


def test_one_failed_install_does_not_abort_the_rest(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(
        module, "resolve_chromium", lambda *a, **k: ("unresolved", None)
    )
    monkeypatch.setenv(module.GATE, "1")
    attempted = []
    failed = {"omercnet/context-mode"}

    def fake_run(argv, **kwargs):
        if argv[1:] == ["--version"]:
            return Mock(
                stdout="v26.0.0" if argv[0] == "/bin/node" else "0.16.2",
                returncode=0,
            )
        if argv[1:] == ["plugin", "ls", "--json"]:
            return Mock(stdout="[]", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout='{"daemonVersion": "0.16.2"}', returncode=0)
        return Mock(stdout="", returncode=0)

    def fake_plugin(paseo, args, *, env=None, run=None):
        attempted.append(args[-1])
        if args[-1] in failed:
            raise ValueError("plugin command failed (exit 1; output redacted)")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "run_plugin", fake_plugin)
    assert module.main(["--apply", "--codexbar-daemon-verified"]) == 1
    assert "omercnet/context-mode" in attempted
    assert attempted[-1] == "gpambrozio/skills" or "gpambrozio/skills" in attempted


def test_daemon_version_not_cli_version_gates_plugin_floors(monkeypatch):
    """A 0.16.2 CLI with a 0.16.0 daemon must not plan 0.16.x-only plugins."""
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(
        module, "resolve_chromium", lambda *a, **k: ("unresolved", None)
    )

    def fake_run(argv, **kwargs):
        if argv[1:] == ["--version"]:
            return Mock(stdout="0.16.2", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout='{"daemonVersion": "0.16.0"}', returncode=0)

    assert module.daemon_version("/bin/paseo", fake_run) == (0, 16, 0)
    selected, omitted = module.plan(
        module.shutil.which,
        module.daemon_version("/bin/paseo", fake_run),
        node_version=(26, 0, 0),
        daemon_codexbar_verified=True,
    )
    reasons = dict(omitted)
    assert "asherlc/devices" not in selected
    assert reasons["asherlc/devices"] == "Paseo >=0.16.2 required"
    assert reasons["asherlc/cheapskate"] == "Paseo >=0.16.1 required"
    assert "omercnet/context-mode" in selected


def test_apply_refuses_unknown_daemon_version_without_plugin_writes(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setenv(module.GATE, "1")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1:] == ["--version"]:
            return Mock(stdout="0.16.2", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout="", returncode=1)
        raise AssertionError("must refuse before listing or mutating plugins")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main(["--apply"]) == 1
    assert not any("add" in call or "enable" in call for call in calls)


def test_managed_wrapper_inserts_before_actual_daemon_exec(tmp_path):
    import shlex

    wrapper = tmp_path / "launchd-daemon.sh"
    chromium = "/Applications/Special ' $(echo leaked)/Chromium"
    wrapper.write_text(
        "# do not execute arbitrary exec text\n"
        'echo "exec is mentioned here"\n'
        'exec "$HOME/bin/paseo" daemon run\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    assert module.managed_wrapper(wrapper, chromium) == "written"
    lines = wrapper.read_text().splitlines()
    assert lines[0] == "# do not execute arbitrary exec text"
    assert lines[1] == 'echo "exec is mentioned here"'
    assert lines[2] == module.START
    assert lines[3] == (
        "export PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE=" + shlex.quote(chromium)
    )
    assert lines[-1] == 'exec "$HOME/bin/paseo" daemon run'
    assert wrapper.stat().st_mode & 0o777 == 0o700
    assert (
        (tmp_path / "launchd-daemon.sh.bak")
        .read_text()
        .startswith("# do not execute arbitrary exec text")
    )
    before = wrapper.stat().st_mtime_ns
    assert module.managed_wrapper(wrapper, chromium) == "written"
    assert wrapper.stat().st_mtime_ns == before


def test_managed_wrapper_never_overwrites_symlink_or_conflicting_backup(tmp_path):
    original = tmp_path / "private.sh"
    original.write_text('exec "$HOME/bin/paseo" daemon run\n', encoding="utf-8")
    link = tmp_path / "launchd-daemon.sh"
    link.symlink_to(original)
    assert module.managed_wrapper(link, "/bin/chromium") == "unsafe-wrapper"
    assert original.read_text() == 'exec "$HOME/bin/paseo" daemon run\n'
    link.unlink()
    link.write_text('exec "$HOME/bin/paseo" daemon run\n', encoding="utf-8")
    backup = tmp_path / "launchd-daemon.sh.bak"
    backup.write_text("owner-backup", encoding="utf-8")
    assert module.managed_wrapper(link, "/bin/chromium") == "backup-conflict"
    assert backup.read_text() == "owner-backup"
    assert link.read_text() == 'exec "$HOME/bin/paseo" daemon run\n'


def test_plugin_runtime_id_must_not_be_cli_option(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setenv(module.GATE, "1")

    def fake_run(argv, **kwargs):
        if argv[1:] == ["--version"]:
            return Mock(stdout="0.16.2", returncode=0)
        if argv[1:] == ["status", "--json"]:
            return Mock(stdout='{"daemonVersion":"0.16.2"}', returncode=0)
        if argv[1:] == ["plugin", "ls", "--json"]:
            return Mock(stdout='[{"id":"--help"}]', returncode=0)
        raise AssertionError("unsafe runtime id should stop before mutation")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main(["--apply", "--prune"]) == 1


def test_plugin_timeout_is_reported_without_aborting_other_attempts():
    import subprocess

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output="private-token")

    import pytest

    with pytest.raises(ValueError, match="output redacted"):
        module.run_plugin("/bin/paseo", ["add", "some/plugin"], run=timeout)


def test_existing_legacy_chromium_block_does_not_rewrite_host_backup(tmp_path):
    """An already-deployed marker must converge without touching the owner backup."""
    wrapper = tmp_path / "launchd-daemon.sh"
    chromium = "/Applications/Chromium.app/Contents/MacOS/Chromium"
    wrapper.write_text(
        module.START
        + '\nexport PASEO_SHARED_BROWSER_CHROMIUM_EXECUTABLE="'
        + chromium
        + '"\n'
        + module.END
        + '\nexec "$HOME/bin/paseo" daemon run\n',
        encoding="utf-8",
    )
    backup = tmp_path / "launchd-daemon.sh.bak"
    backup.write_text('exec "$HOME/bin/paseo" daemon run\n', encoding="utf-8")
    before = (wrapper.read_bytes(), wrapper.stat().st_mtime_ns)
    assert module.managed_wrapper(wrapper, chromium) == "written"
    assert (wrapper.read_bytes(), wrapper.stat().st_mtime_ns) == before
    assert backup.read_text() == 'exec "$HOME/bin/paseo" daemon run\n'


def test_context_mode_plugin_requires_its_own_gate():
    """The Paseo context-mode plugin must follow DOTFILES_RUN_CONTEXT_MODE_SETUP."""
    selected, omitted = module.plan(
        lambda name: "/bin/" + name,
        (0, 16, 2),
        node_version=(26, 0, 0),
        daemon_codexbar_verified=True,
        environ={},
    )
    reasons = dict(omitted)
    assert "omercnet/context-mode" not in selected
    assert reasons["omercnet/context-mode"] == "DOTFILES_RUN_CONTEXT_MODE_SETUP != 1"

    enabled, _ = module.plan(
        lambda name: "/bin/" + name,
        (0, 16, 2),
        node_version=(26, 0, 0),
        daemon_codexbar_verified=True,
        environ={"DOTFILES_RUN_CONTEXT_MODE_SETUP": "1"},
    )
    assert "omercnet/context-mode" in enabled


def test_repo_context_mode_never_resolves_the_bundled_copy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bundled = home / ".paseo/plugins/context-mode/abc/node_modules/context-mode/cli.mjs"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/usr/bin/env node\n")

    status, path = module.resolve_repo_context_mode(
        which=lambda _: str(bundled), exists=lambda p: True, home=home
    )
    assert (status, path) == ("bundled", None)

    global_bin = tmp_path / "prefix/bin/context-mode"
    global_bin.parent.mkdir(parents=True)
    global_bin.write_text("#!/bin/sh\n")
    (tmp_path / "prefix/lib/node_modules/context-mode").mkdir(
        parents=True, exist_ok=True
    )
    module_path = tmp_path / "prefix/lib/node_modules/context-mode/cli.mjs"
    module_path.write_text("#!/usr/bin/env node\n")

    status, path = module.resolve_repo_context_mode(
        which=lambda _: str(global_bin),
        exists=lambda p: p == str(module_path),
        realpath=lambda p: p,
        home=home,
    )
    assert (status, path) == ("resolved", str(module_path.resolve()))


def test_context_mode_settings_preserve_values_and_are_idempotent(tmp_path):
    settings = tmp_path / "plugin-settings/context-mode"
    settings.mkdir(parents=True)
    target = settings / "context-mode.json"
    target.write_text(
        json.dumps({"version": 2, "values": {"autoInject": False, "binaryPath": None}})
    )
    binary = "/opt/homebrew/lib/node_modules/context-mode/cli.mjs"

    assert module.configure_context_mode_binary(settings, binary) == "written"
    written = json.loads(target.read_text())
    assert written["version"] == 2
    assert written["values"]["binaryPath"] == binary
    assert written["values"]["autoInject"] is False
    assert written["values"]["preferNativeIntegrations"] is True
    assert (settings / "context-mode.json.bak").is_file()

    before = target.stat().st_mtime_ns
    assert module.configure_context_mode_binary(settings, binary) == "unchanged"
    assert target.stat().st_mtime_ns == before
