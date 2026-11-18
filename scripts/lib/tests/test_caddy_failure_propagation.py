"""Fail-closed behaviour for the managed Caddy deployment (issue #323).

The defect these tests pin: every required macOS Caddy operation was wrapped in
`|| warn` and the script printed `Caddy setup complete.` regardless, so a failed
root daemon deployment reported success. The decisive properties are ordering
(a candidate is validated before the live Caddyfile is replaced) and exit-status
propagation from the shared helpers.
"""

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_24-install-caddy.sh.tmpl"
HELPERS = ROOT / "scripts/lib/caddy_helpers.sh"


def _branches():
    source = TEMPLATE.read_text(encoding="utf-8")
    darwin, rest = source.split('{{- else if eq .chezmoi.os "linux" }}', 1)
    return darwin, rest.split("{{- else }}", 1)[0]


def test_darwin_required_steps_are_not_warning_only():
    darwin, _ = _branches()
    for swallowed in (
        '"$BREW_BIN" install caddy || warn',
        'configure-caddy.py" || warn',
        'launchctl bootstrap system/ "$PLIST_PATH" || warn',
        'caddy_validate "$CADDYFILE_PATH" || warn',
        'caddy_reload "$CADDYFILE_PATH" || warn',
    ):
        assert swallowed not in darwin
    # The steps must still run; only their failure handling changed.
    assert 'launchctl bootstrap system/ "$PLIST_PATH"' in darwin
    assert 'caddy_validate "$CADDYFILE_PATH"' in darwin
    assert 'caddy_reload "$CADDYFILE_PATH"' in darwin


def test_candidate_is_validated_before_the_live_caddyfile_is_replaced():
    darwin, _ = _branches()
    validate_at = darwin.index('caddy validate --config "$CANDIDATE"')
    install_at = darwin.index('install -m 0644 "$CANDIDATE"')
    live_validate_at = darwin.index('caddy_validate "$CADDYFILE_PATH"')
    assert validate_at < install_at < live_validate_at
    assert "existing Caddyfile was preserved" in darwin
    assert '--output "$CANDIDATE" --no-backup' in darwin


def test_symlinked_targets_are_refused_before_any_replacement():
    darwin, _ = _branches()
    assert '[[ -L "$CADDYFILE_PATH" ]]' in darwin
    assert '[[ ! -L "$OLD_PLIST_PATH" ]]' in darwin
    assert '-L "$PLIST_PATH"' in darwin


def test_linux_generation_validation_and_reload_fail_closed():
    _, linux = _branches()
    assert 'configure-caddy.py" || warn' not in linux
    assert 'caddy_validate "$CADDYFILE_PATH" || warn' not in linux
    assert 'caddy_reload "$CADDYFILE_PATH" || warn' not in linux


def _run_helpers(tmp_path, exit_code):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    caddy = fake_bin / "caddy"
    caddy.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n")
    caddy.chmod(0o755)
    script = f"""
set -euo pipefail
source "{HELPERS}"
if caddy_validate "{tmp_path}/Caddyfile"; then validate=0; else validate=$?; fi
if caddy_reload "{tmp_path}/Caddyfile"; then reload=0; else reload=$?; fi
printf 'validate=%s reload=%s\\n' "$validate" "$reload"
"""
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )


def test_helpers_propagate_a_failing_caddy(tmp_path):
    result = _run_helpers(tmp_path, 1)
    assert result.returncode == 0, result.stderr
    assert "validate=1 reload=1" in result.stdout
    combined = result.stdout + result.stderr
    assert "Caddyfile validation failed" in combined
    assert "Caddy reload failed" in combined


def test_helpers_report_success_for_a_working_caddy(tmp_path):
    result = _run_helpers(tmp_path, 0)
    assert result.returncode == 0, result.stderr
    assert "validate=0 reload=0" in result.stdout
    assert "Caddyfile validation passed" in result.stdout
    assert "Caddy reloaded" in result.stdout
