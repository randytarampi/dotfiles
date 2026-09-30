import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "litellm_diagnose", ROOT / "scripts/litellm-diagnose.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def setup_home(tmp_path, monkeypatch, *, with_key=True, subname="litellm"):
    home = tmp_path / subname
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(MODULE.Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    root = home / ".local/share/litellm"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("config")
    if with_key:
        (root / "service.env").write_text("LITELLM_MASTER_KEY='dtf-not-a-real-key'\n")


def test_gate_off_lifecycle(tmp_path, monkeypatch):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.delenv("DOTFILES_RUN_LITELLM_SETUP", raising=False)
    code, summary = MODULE.diagnose()
    assert code == 0
    assert "GATE-OFF" in summary


def test_healthy_and_redacted(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(MODULE, "_loaded", lambda: True)
    monkeypatch.setattr(MODULE, "_request", lambda url, key="": (200, {"data": []}, ""))
    code, summary = MODULE.diagnose()
    assert code == 0
    assert "HEALTHY" in summary
    assert "dtf-not-a-real-key" not in summary


def test_auth_failure_is_classified(monkeypatch, tmp_path):
    for status in (401, 403):
        setup_home(tmp_path, monkeypatch, subname=f"home-{status}")
        monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
        monkeypatch.setattr(
            MODULE,
            "_request",
            lambda url, key="", status=status: ((status if key else 200), {}, ""),
        )
        code, summary = MODULE.diagnose()
        assert code == 1
        assert "MISSING-OR-INVALID-MASTER-KEY" in summary


def test_no_key_does_not_send_models_request(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch, with_key=False)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    calls = []
    monkeypatch.setattr(
        MODULE,
        "_request",
        lambda url, key="": calls.append((url, key)) or (200, {}, ""),
    )
    code, summary = MODULE.diagnose()
    assert code == 1 and "MISSING-OR-INVALID-MASTER-KEY" in summary
    assert not any(url.endswith("/v1/models") for url, _ in calls)


def test_db_less_auth_backend_rejection(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")

    def request(url, key=""):
        if url.endswith("/v1/models"):
            # Production shape: _request() discards HTTPError bodies, so a DB-less
            # LiteLLM's no_db_connection rejection arrives as (400, None, "http-error"),
            # never as a parsed error dict.
            return 400, None, "http-error"
        return 200, {}, ""

    monkeypatch.setattr(MODULE, "_request", request)
    code, summary = MODULE.diagnose()
    assert code == 1
    assert "MISSING-OR-INVALID-MASTER-KEY" in summary


def test_error_dict_body_is_provider_failure(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")

    def request(url, key=""):
        if url.endswith("/v1/models"):
            # Parsed dict-error bodies (e.g. provider upstream failure relayed in
            # a 2xx-adjacent payload) still classify as PROVIDER-FAILURE.
            return 502, {"error": {"message": "upstream unavailable"}}, ""
        return 200, {}, ""

    monkeypatch.setattr(MODULE, "_request", request)
    code, summary = MODULE.diagnose()
    assert code == 1 and "PROVIDER-FAILURE" in summary


def test_remote_base_url_is_refused_without_key_attachment(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://example.com")
    calls = []
    monkeypatch.setattr(
        MODULE,
        "_request",
        lambda url, key="": calls.append((url, key)) or (200, {}, ""),
    )
    code, summary = MODULE.diagnose()
    assert code == 1 and "PROXY-UNAVAILABLE" in summary
    assert calls == []


def test_proxy_unavailable_and_provider_failure(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(MODULE, "_request", lambda url, key="": (0, None, "URLError"))
    code, summary = MODULE.diagnose()
    assert code == 1 and "PROXY-UNAVAILABLE" in summary
    monkeypatch.setattr(
        MODULE, "_request", lambda url, key="": (500, {"error": "provider"}, "")
    )
    code, summary = MODULE.diagnose()
    assert code == 1 and "PROVIDER-FAILURE" in summary
