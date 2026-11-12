"""Exercise plain-ID WinGet manifests without installing any real package."""

import os
import subprocess
from pathlib import Path

LIBRARY = Path(__file__).resolve().parents[1] / "winget_manifest.sh"


def run_manifest(tmp_path, manifest, *, ignored="", winget_exit=0):
    binary = tmp_path / "winget"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" >> "$WINGET_TRACE"\n'
        'exit "$WINGET_EXIT"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    path = tmp_path / "wingetfile.dev"
    path.write_bytes(manifest)
    trace = tmp_path / "commands.txt"
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
        "WINGET_TRACE": str(trace),
        "WINGET_EXIT": str(winget_exit),
        "DOTFILES_WINGETFILE_IGNORE": ignored,
        "WINGET_MANIFEST_LIB": str(LIBRARY),
        "WINGET_MANIFEST_FILE": str(path),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; warn() { printf "%s\\n" "$*" >&2; }; '
            'source "$WINGET_MANIFEST_LIB"; wingetfile_install "$WINGET_MANIFEST_FILE"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result, calls


def test_exact_package_ids_and_comments_are_passed_to_winget(tmp_path):
    result, calls = run_manifest(
        tmp_path, b"# comment\r\n\r\nOllama.Ollama\r\nGit.Git\n"
    )
    assert result.returncode == 0
    expected = [
        "install",
        "--id",
        "Ollama.Ollama",
        "--exact",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    assert calls == expected + [
        "install",
        "--id",
        "Git.Git",
        *expected[3:],
    ]


def test_ignore_matches_exact_tokens_not_regex(tmp_path):
    result, calls = run_manifest(
        tmp_path,
        b"Git.Git\nGitXGit\nOllama.Ollama\n",
        ignored=" Git.Git , Ollama.Ollama ",
    )
    assert result.returncode == 0
    assert calls == [
        "install",
        "--id",
        "GitXGit",
        "--exact",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]


def test_invalid_id_fails_closed_but_other_ids_are_attempted(tmp_path):
    result, calls = run_manifest(
        tmp_path, b"Git.Git\n--source\nMalicious;touch bad\nOllama.Ollama\n"
    )
    assert result.returncode == 1
    assert "Invalid WinGet package ID" in result.stderr
    assert "no installations attempted" in result.stderr
    assert calls == []


def test_package_failure_propagates(tmp_path):
    result, calls = run_manifest(tmp_path, b"Ollama.Ollama\nGit.Git\n", winget_exit=17)
    assert result.returncode == 1
    assert calls.count("install") == 2
    assert "2 WinGet package(s)" in result.stderr


def test_comment_only_and_fully_ignored_manifests_are_noops(tmp_path):
    result, calls = run_manifest(
        tmp_path, b"# only comments\nGit.Git\n", ignored="Git.Git"
    )
    assert result.returncode == 0
    assert calls == []


def test_malformed_last_line_without_newline_cannot_install_prior_ids(tmp_path):
    result, calls = run_manifest(tmp_path, b"Git.Git\nnot a package")
    assert result.returncode == 1
    assert calls == []
