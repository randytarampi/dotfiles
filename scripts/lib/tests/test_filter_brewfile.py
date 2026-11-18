"""Exact-name Brewfile filtering used by the bundle stage."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/filter-brewfile.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _manifest(tmp_path, text, name="Brewfile"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_ignored_brew_and_cask_removed_everything_else_preserved(tmp_path):
    manifest = _manifest(
        tmp_path,
        'tap "homebrew/core"\nbrew "git"\ncask "zoom"\nnpm "corepack"\n\n# comment\n',
    )
    out = tmp_path / "out"
    result = _run(
        "--manifest", manifest, "--out", out, "--ignore", "git", "--ignore", "zoom"
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == (
        'tap "homebrew/core"\nnpm "corepack"\n\n# comment\n'
    )


def test_metacharacters_never_widen_the_match(tmp_path):
    manifest = _manifest(
        tmp_path,
        'brew "jq"\nbrew "jqq"\nbrew "a.b"\nbrew "axb"\nbrew "c+1"\nbrew "cc1"\n',
    )
    out = tmp_path / "out"
    result = _run(
        "--manifest",
        manifest,
        "--out",
        out,
        "--ignore-list",
        "jq.*, a.b ,c+1",
    )
    assert result.returncode == 0, result.stderr
    assert (
        out.read_text(encoding="utf-8")
        == 'brew "jq"\nbrew "jqq"\nbrew "axb"\nbrew "cc1"\n'
    )


def test_invalid_lines_are_preserved_for_the_audit(tmp_path):
    manifest = _manifest(tmp_path, "brew git\n$(touch pwned)\n")
    out = tmp_path / "out"
    result = _run("--manifest", manifest, "--out", out, "--ignore", "git")
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "brew git\n$(touch pwned)\n"


def test_missing_and_symlinked_manifest_refused(tmp_path):
    out = tmp_path / "out"
    assert _run("--manifest", tmp_path / "absent", "--out", out).returncode == 1
    target = _manifest(tmp_path, 'brew "git"\n', "target")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert _run("--manifest", link, "--out", out).returncode == 1


def test_output_overwrite_and_symlink_are_refused(tmp_path):
    manifest = _manifest(tmp_path, 'brew "git"\n')
    out = _manifest(tmp_path, "existing\n", "out")
    assert _run("--manifest", manifest, "--out", out).returncode == 1
    assert out.read_text(encoding="utf-8") == "existing\n"
    result = _run("--manifest", manifest, "--out", out, "--force")
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == 'brew "git"\n'

    link = tmp_path / "out-link"
    link.symlink_to(out)
    assert _run("--manifest", manifest, "--out", link, "--force").returncode == 1


def test_dry_run_writes_nothing(tmp_path):
    manifest = _manifest(tmp_path, 'brew "git"\nbrew "jq"\n')
    out = tmp_path / "out"
    result = _run("--manifest", manifest, "--out", out, "--ignore", "git", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not out.exists()
    assert "would write" in result.stdout
