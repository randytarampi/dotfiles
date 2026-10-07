import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_missing_managed_venv_returns_clean_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(OAUTH, "load_env", lambda: None)
    monkeypatch.setattr(OAUTH.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(OAUTH.sys.stdin, "isatty", lambda: True)
    assert OAUTH.main(["--provider", "chatgpt"]) == 2
    captured = capsys.readouterr()
    assert "virtualenv Python is unavailable" in captured.err
    assert str(tmp_path) not in captured.err


def test_chatgpt_bootstrap_verifies_registry_models_and_atomically_writes_ids(
    tmp_path, capsys
):
    import stat

    target = tmp_path / ".local/share/litellm/chatgpt_verified_models.json"
    calls = []

    def stub(command, check=False, **kwargs):
        code = command[-1]
        calls.append(code)
        return SimpleNamespace(
            returncode=0 if "chatgpt/gpt-good" in code else 1,
            stdout="",
            stderr="The gpt-bad model is not supported when using Codex with a ChatGPT account",
        )

    verified, all_ok = OAUTH.verify_chatgpt_openai_models(
        python=tmp_path / "venv/bin/python",
        refs={"openai/gpt-good", "openai/gpt-bad", "anthropic/not-openai"},
        verified_path=target,
        deferred_path=tmp_path / "chatgpt_deferred_models.json",
        run=stub,
    )
    assert verified == ["gpt-good"] and all_ok
    assert len(calls) == 2
    assert all("max_tokens=16" in call for call in calls)
    assert json.loads(target.read_text()) == ["gpt-good"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    report = capsys.readouterr().out
    assert "openai/gpt-good: VERIFIED" in report
    assert "openai/gpt-bad: DEFERRED (entitlement)" in report


def test_classify_probe_failure():
    cases = [
        ("is not supported when using codex with a chatgpt account", "entitlement"),
        ("Unknown items in responses API response: []", "artifact"),
        ("PermissionDeniedError __cf_chl challenge", "artifact"),
        ("RateLimitError", "artifact"),
        ("ConnectionError", "artifact"),
        ("unrecognised failure", "unknown"),
        (None, "unknown"),
    ]
    for output, expected in cases:
        assert OAUTH.classify_probe_failure(1, output) == expected


def test_artifact_retries_then_succeeds(tmp_path):
    calls = []
    sleeps = []

    def stub(command, **kwargs):
        calls.append(command[-1])
        return SimpleNamespace(
            returncode=1 if len(calls) == 1 else 0,
            stdout="",
            stderr="Unknown items in responses API response: []",
        )

    verified, ok = OAUTH.verify_chatgpt_openai_models(
        refs={"openai/gpt-retry"},
        verified_path=tmp_path / "verified.json",
        deferred_path=tmp_path / "deferred.json",
        run=stub,
        sleep=sleeps.append,
    )
    assert ok and verified == ["gpt-retry"] and sleeps == [10]


def test_persistent_artifact_is_deferred(tmp_path):
    target = tmp_path / "verified.json"

    def stub(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="Timeout")

    verified, ok = OAUTH.verify_chatgpt_openai_models(
        refs={"openai/gpt-timeout"},
        verified_path=target,
        deferred_path=tmp_path / "deferred.json",
        run=stub,
        sleep=lambda _: None,
    )
    assert ok and verified == [] and json.loads(target.read_text()) == []
    assert json.loads((tmp_path / "deferred.json").read_text()) == {
        "entitlement": [],
        "artifact": ["gpt-timeout"],
    }


def test_unknown_preserves_verified_file(tmp_path):
    target = tmp_path / "verified.json"
    target.write_text('["stale"]')

    def stub(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    verified, ok = OAUTH.verify_chatgpt_openai_models(
        refs={"openai/gpt-unknown"},
        verified_path=target,
        deferred_path=tmp_path / "deferred.json",
        run=stub,
    )
    assert not ok and verified == [] and target.read_text() == '["stale"]'


def test_main_probe_entitlement_continues_to_model_verification(monkeypatch, tmp_path):
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(OAUTH, "litellm_python", lambda: python)
    monkeypatch.setattr(OAUTH, "load_env", lambda: None)
    monkeypatch.setattr(OAUTH.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(OAUTH.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path))
    deferred_path = tmp_path / ".local/share/litellm/chatgpt_deferred_models.json"
    calls = []

    def stub(command, **kwargs):
        calls.append(command[-1])
        if "Authenticator" in command[-1]:
            cache = OAUTH.cache_path("chatgpt")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text("{}")
            return SimpleNamespace(returncode=0)
        if "chatgpt/gpt-5.2" in command[-1]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="model is not supported when using Codex with a ChatGPT account",
            )
        if "litellm.completion" in command[-1]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(OAUTH.subprocess, "run", stub)
    assert OAUTH.main(["--provider", "chatgpt"]) == 0
    assert deferred_path.exists()
    assert json.loads(deferred_path.read_text()) == {"entitlement": [], "artifact": []}
    assert (
        not Path("~/.local/share/litellm/chatgpt_deferred_models.json")
        .expanduser()
        .exists()
        or Path("~/.local/share/litellm/chatgpt_deferred_models.json").expanduser()
        == deferred_path
    )
    assert any(
        "openai/" in call or "chatgpt/" in call and "gpt-5.2" not in call
        for call in calls
    )


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

    home = tmp_path / "isolated-home"
    home.mkdir()
    cache_dir = home / ".config/litellm/github_copilot"
    cache_file = cache_dir / "api-key.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(cache_dir))
    monkeypatch.setattr(OAUTH, "load_env", lambda: None)
    python = home / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(OAUTH, "litellm_python", lambda: python)
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
