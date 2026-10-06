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
