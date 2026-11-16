"""Windows path-with-spaces is a first-class deploy configuration contract."""

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github/workflows/ci.yml"


def test_make_has_separate_quoted_chezmoi_config_argument():
    source = MAKEFILE.read_text(encoding="utf-8")
    assert "CHEZMOI_CONFIG ?=" in source
    assert 'CHEZMOI_CONFIG_ARG = $(if $(strip $(CHEZMOI_CONFIG)),--config "$(CHEZMOI_CONFIG)")' in source
    assert source.count('$(CHEZMOI) $(CHEZMOI_CONFIG_ARG) --source') == 3
    assert '$(CHEZMOI) --source "$(CHEZMOI_SOURCE)" apply' not in source


def test_make_dry_run_preserves_space_bearing_config_as_one_quoted_arg(tmp_path):
    config = tmp_path / "a space" / "interpreter.toml"
    config.parent.mkdir()
    config.write_text("[interpreters.sh]\n")
    result = subprocess.run(
        ["make", "-n", "diff", f"CHEZMOI_CONFIG={config}", "CHEZMOI=chezmoi"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert f'--config "{config}"' in result.stdout


def test_ci_actually_deploys_and_checks_windows_spaces_without_skipping_rows():
    src = WORKFLOW.read_text(encoding="utf-8")
    deploy = src.split("  deploy:", 1)[1].split("  bootstrap-smoke:", 1)[0]
    assert 'smoke_home="$RUNNER_TEMP/dotfiles home"' in deploy
    assert 'config_file="$RUNNER_TEMP/chezmoi config/interpreters.toml"' in deploy
    assert 'echo "CHEZMOI_CONFIG=$cz_config" >> "$GITHUB_ENV"' in deploy
    assert 'chezmoi_args+=(--config "$CHEZMOI_CONFIG")' in deploy
    assert 'chezmoi "${chezmoi_args[@]}" --source "$GITHUB_WORKSPACE" verify' in deploy
    assert 'diff_output="$(chezmoi "${chezmoi_args[@]}" --source "$GITHUB_WORKSPACE" diff)"' in deploy
    assert deploy.count("Deploy (first pass)") == 1
    assert deploy.count("Deploy (idempotency pass)") == 1
    assert "continue-on-error" not in deploy
    assert "windows-11-arm" in deploy and "windows-latest" in deploy
