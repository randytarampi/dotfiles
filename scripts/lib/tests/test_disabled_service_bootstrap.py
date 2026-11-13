"""Reject modified or foreign user units in the disabled Linux service path."""

from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = {
    "litellm": ROOT / ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl",
    "openwebui": ROOT / ".chezmoiscripts/run_onchange_30-openwebui.sh.tmpl",
}
UNITS = {"litellm": "litellm.service", "openwebui": "open-webui.service"}
BINARIES = {
    "litellm": ".local/share/litellm/venv/bin/litellm",
    "openwebui": ".local/share/openwebui/venv/bin/open-webui",
}


def _generated_unit(home, service, payload):
    """Mirror the active Description/ExecStart written by each template."""
    if service == "litellm":
        command = (
            "ExecStart=/usr/bin/env -i HOME=%h "
            "PATH=/usr/bin:/bin:%h/.local/share/litellm/venv/bin "
            "/bin/bash --noprofile --norc -c 'set -a; source "
            f'"{home}/.local/share/litellm/service.env"; set +a; '
            f'exec "{payload}" '
            f'--config "{home}/.local/share/litellm/config.yaml" '
            "--host 127.0.0.1 --port 4000 --num_workers 1'"
        )
        description = "LiteLLM proxy gateway"
    else:
        command = (
            "ExecStart=/usr/bin/env -i HOME=%h "
            "PATH=/usr/bin:/bin:%h/.local/share/openwebui/venv/bin "
            f"DATA_DIR={home}/.local/share/openwebui/data "
            "GLOBAL_LOG_LEVEL=INFO /bin/bash --noprofile --norc -c "
            "'set -a; source "
            f'"{home}/.local/share/openwebui/service.env"; set +a; '
            f'exec "{payload}" serve --host 127.0.0.1 --port 8080'
            "'"
        )
        description = "Open WebUI"
    return f"[Unit]\nDescription={description}\n\n[Service]\nType=simple\n{command}\n"


def _modified_unit(text, payload, kind):
    lines = text.splitlines(keepends=True)
    if kind == "comment_exec":
        return (
            "".join(
                f"# {line}" if line.startswith("ExecStart=") else line for line in lines
            )
            + "ExecStart=/usr/bin/false\n"
        )
    if kind == "comment_description":
        return (
            "".join(
                f"# {line}" if line.startswith("Description=") else line
                for line in lines
            )
            + "Description=Other user service\n"
        )
    if kind == "suffix":
        return text.replace(f'exec "{payload}"', f'exec "{payload}-foreign"')
    if kind == "wrong_section":
        return text.replace("[Service]\nType=simple\n", "[Install]\n")
    if kind == "path_as_argument":
        return "".join(
            (
                f"ExecStart=/usr/bin/false --notice {payload}\n"
                if line.startswith("ExecStart=")
                else line
            )
            for line in lines
        )
    if kind == "duplicate_exec":
        return text + "ExecStart=/usr/bin/false\n"
    if kind == "foreign":
        return "[Unit]\nDescription=Unrelated service\n[Service]\nExecStart=/usr/bin/false\n"
    raise AssertionError(f"Unknown variant: {kind}")


def _run(tmp_path, service, *, installed=False, linked_unit=False, modified=None):
    home = tmp_path / "home"
    home.mkdir()
    stubs = tmp_path / "bin"
    stubs.mkdir()
    trace = tmp_path / "calls"
    for command in ("systemctl", "pkill", "launchctl", "curl"):
        stub = stubs / command
        stub.write_text(
            '#!/bin/sh\nprintf "%s %s\\n" "$0" "$*" >> "$TRACE"\nexit 0\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

    unit = home / ".config/systemd/user" / UNITS[service]
    payload = home / BINARIES[service]
    if installed or linked_unit or modified:
        unit.parent.mkdir(parents=True)
        if linked_unit:
            owner_file = tmp_path / "other-user-managed-unit"
            owner_file.write_text("owned by someone else")
            unit.symlink_to(owner_file)
        else:
            # Even a foreign unit may have a coincidentally present executable.
            payload.parent.mkdir(parents=True)
            payload.write_text("#!/bin/sh\nexit 0\n")
            payload.chmod(0o755)
            generated = _generated_unit(home, service, payload)
            unit.write_text(
                _modified_unit(generated, payload, modified) if modified else generated,
                encoding="utf-8",
            )

    rendered = tmp_path / "rendered.sh"
    source = TEMPLATES[service].read_text()
    source = source.replace("{{ .chezmoi.sourceDir }}", str(ROOT))
    source = source.replace("{{ .chezmoi.os }}", "linux")
    rendered.write_text(source)

    # Credentials and host shell hooks must not enter synthetic subprocesses.
    env = {
        "HOME": str(home),
        "USER": "dotfiles-test",
        "PATH": f"{stubs}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "TRACE": str(trace),
        "NO_COLOR": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "0",
        "DOTFILES_RUN_OPENWEBUI_SETUP": "0",
        "DOTFILES_RUN_JUNIE_CLI_SETUP": "0",
    }
    result = subprocess.run(
        ["bash", str(rendered)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = trace.read_text().splitlines() if trace.exists() else []
    return result, calls, unit, payload


@pytest.mark.parametrize("service", ["litellm", "openwebui"])
def test_empty_home_has_no_service_or_process_side_effects(tmp_path, service):
    result, calls, unit, payload = _run(tmp_path, service)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert not unit.exists()
    assert not payload.exists()


@pytest.mark.parametrize("service", ["litellm", "openwebui"])
def test_legacy_generated_unit_is_disabled_but_data_is_preserved(tmp_path, service):
    result, calls, unit, payload = _run(tmp_path, service, installed=True)
    assert result.returncode == 0, result.stderr
    assert not unit.exists()
    assert payload.exists()
    assert any("disable --now " + UNITS[service] in call for call in calls)
    assert any("daemon-reload" in call for call in calls)
    assert any("pkill" in call for call in calls)


@pytest.mark.parametrize("service", ["litellm", "openwebui"])
def test_symlinked_foreign_unit_is_preserved(tmp_path, service):
    result, calls, unit, _ = _run(tmp_path, service, linked_unit=True)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert unit.is_symlink()
    assert unit.resolve().read_text() == "owned by someone else"


@pytest.mark.parametrize("service", ["litellm", "openwebui"])
@pytest.mark.parametrize(
    "modified",
    [
        "comment_exec",
        "comment_description",
        "suffix",
        "wrong_section",
        "path_as_argument",
        "duplicate_exec",
        "foreign",
    ],
)
def test_foreign_or_modified_unit_is_not_disabled_or_signalled(
    tmp_path, service, modified
):
    result, calls, unit, payload = _run(tmp_path, service, modified=modified)
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert unit.exists()
    assert payload.exists()
