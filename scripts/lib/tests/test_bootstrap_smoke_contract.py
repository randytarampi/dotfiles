"""Executable negative tests for the isolated Linux bootstrap contract."""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "tests/bootstrap/smoke.sh"


def _inner_script():
    source = SMOKE.read_text(encoding="utf-8")
    marker = """cat > "$smoke_home/verify-core.sh" <<'EOF'\n"""
    return source.split(marker, 1)[1].split("\nEOF\n", 1)[0] + "\n"


def _run_inner(tmp_path, *, diff_exit=0, drift="", canary=False):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\nname = smoke\n")
    (home / ".bashrc").write_text("# smoke\n")
    bins = tmp_path / "bin"
    bins.mkdir()

    make = bins / "make"
    make.write_text("#!/bin/sh\nexit 0\n")
    make.chmod(0o755)
    chezmoi = bins / "chezmoi"
    chezmoi.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${@: -1}" == "verify" ]]; then exit 0; fi\n'
        'if [[ "${@: -1}" == "diff" ]]; then\n'
        '  printf "%s" "${FAKE_DRIFT:-}"\n'
        '  exit "${FAKE_DIFF_EXIT:-0}"\n'
        "fi\n"
        "exit 99\n"
    )
    chezmoi.chmod(0o755)

    inner = tmp_path / "verify.sh"
    inner.write_text(_inner_script(), encoding="utf-8")
    return subprocess.run(
        ["bash", str(inner)],
        env={
            "HOME": str(home),
            "SOURCE_DIR": str(ROOT),
            "PATH": f"{bins}:/usr/bin:/bin",
            "FAKE_DIFF_EXIT": str(diff_exit),
            "FAKE_DRIFT": drift,
            **({"BOOTSTRAP_SMOKE_CANARY": "synthetic-only"} if canary else {}),
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize(
    ("diff_exit", "drift", "expected"),
    [
        (0, "", 0),
        (0, "modified .bashrc", 1),
        (13, "", 1),
        (13, "partial diff", 1),
    ],
)
def test_chezmoi_diff_cannot_mask_failure(tmp_path, diff_exit, drift, expected):
    result = _run_inner(tmp_path, diff_exit=diff_exit, drift=drift)
    assert result.returncode == expected, result.stderr
    if diff_exit:
        assert "chezmoi diff failed" in result.stderr
    elif drift:
        assert "chezmoi drift" in result.stderr


def test_parent_secret_canary_must_not_reach_deploy(tmp_path):
    result = _run_inner(tmp_path, canary=True)
    assert result.returncode != 0
    assert "forbidden parent secret canary" in result.stderr


def test_smoke_refuses_root_before_touching_home(tmp_path):
    bins = tmp_path / "bin"
    bins.mkdir()
    id_command = bins / "id"
    id_command.write_text("#!/bin/sh\nprintf '0\\n'\n")
    id_command.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SMOKE)],
        env={"HOME": str(tmp_path), "PATH": f"{bins}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "must run as a non-root user" in result.stderr


def test_actual_smoke_requires_a_clean_credential_boundary():
    source = SMOKE.read_text(encoding="utf-8")
    assert "env -i" in source
    assert "BOOTSTRAP_SMOKE_TRACE" in source
    assert "BOOTSTRAP_SMOKE_CANARY=synthetic-parent-only" in source
    assert (
        "for name in curl wget brew winget sudo apt apt-get systemctl launchctl pkill"
        in source
    )
