import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "litellm-oauth.py"
SPEC = importlib.util.spec_from_file_location("litellm_oauth", SCRIPT)
OAUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OAUTH)


def test_help_exits_zero():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True
    )
    assert result.returncode == 0


def test_non_tty_refuses_before_subprocess(monkeypatch):
    monkeypatch.setattr(OAUTH.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(OAUTH.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        OAUTH.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not invoke venv")),
    )
    assert OAUTH.main(["--provider", "chatgpt"]) == 2


def test_bootstrap_hardens_cache_under_permissive_umask(monkeypatch, tmp_path):
    import os
    import stat
    from types import SimpleNamespace

    home = tmp_path / "isolated-home"
    home.mkdir()
    cache_dir = home / ".config/litellm/github_copilot"
    cache_file = cache_dir / "api-key.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(cache_dir))
    monkeypatch.setattr(OAUTH, "load_env", lambda: None)
    monkeypatch.setattr(OAUTH.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(OAUTH.sys.stdin, "isatty", lambda: True)
    calls = []

    def stub_adapter(command, **kwargs):
        calls.append(command)
        code = command[-1]
        if "Authenticator" in code:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text('{"token":"stub","expires_at":4102444800}')
            cache_dir.chmod(0o755)
            cache_file.chmod(0o644)
            return SimpleNamespace(returncode=0)
        assert "litellm.completion" in code
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(OAUTH.subprocess, "run", stub_adapter)
    previous_umask = os.umask(0o022)
    try:
        assert OAUTH.main(["--provider", "github_copilot"]) == 0
    finally:
        os.umask(previous_umask)
    assert (
        len(calls) == 2
    )  # Authenticator and verification were both stubbed; no flow ran.
    assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
