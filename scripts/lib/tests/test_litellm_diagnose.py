import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "litellm_diagnose", ROOT / "scripts/litellm-diagnose.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


# noqa: E402  # pytest import follows the module bootstrap above, as in
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def loaded_service(monkeypatch):
    monkeypatch.setattr(MODULE, "_loaded", lambda: True)


def setup_home(tmp_path, monkeypatch, *, with_key=True, subname="litellm"):
    home = tmp_path / subname
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(MODULE.Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    root = home / ".local/share/litellm"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("config")
    if with_key:
        key = "dtf-" + "not-a-real-key"
        (root / "service.env").write_text(f"LITELLM_MASTER_KEY='{key}'\n")


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
    calls = []

    def request(url, key=""):
        calls.append((url, key))
        return (200, {"data": []}, "") if url.endswith("/v1/models") else (200, {}, "")

    monkeypatch.setattr(MODULE, "_request", request)
    code, summary = MODULE.diagnose()
    assert code == 0
    assert "HEALTHY" in summary
    assert "dtf-not-a-real-key" not in summary
    assert [url.rsplit("/", 1)[-1] for url, _ in calls] == [
        "liveliness",
        "readiness",
        "models",
    ]
    assert calls[0][1] == ""
    assert all(key == "dtf-not-a-real-key" for _, key in calls[1:])


def test_non_json_200_is_unhealthy(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")

    def request(url, key=""):
        if url.endswith("/health/readiness"):
            return 200, None, "invalid-json"
        return (200, {"data": []}, "") if url.endswith("/v1/models") else (200, {}, "")

    monkeypatch.setattr(MODULE, "_request", request)
    code, summary = MODULE.diagnose()
    assert code == 1
    assert "reason=invalid-json" in summary


def test_redirect_response_is_not_followed_and_is_unhealthy(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    handlers = []

    class Response:
        status = 302

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"location": "https://remote.example"}'

    class Opener:
        def open(self, request, timeout):
            return Response()

    def build_opener(*args):
        handlers.extend(args)
        return Opener()

    monkeypatch.setattr(MODULE.urllib.request, "build_opener", build_opener)
    status, parsed, reason = MODULE._request(
        "http://127.0.0.1:4000/health/readiness", "key"
    )
    assert (status, parsed, reason) == (302, {"location": "https://remote.example"}, "")
    assert isinstance(handlers[0], MODULE._NoRedirectHandler)
    assert (
        handlers[0].redirect_request(
            None, None, 302, "Found", {}, "https://remote.example"
        )
        is None
    )

    monkeypatch.setattr(
        MODULE, "_request", lambda url, key="": (302, None, "http-error")
    )
    code, summary = MODULE.diagnose()
    assert code == 1 and "PROXY-UNAVAILABLE" in summary


def test_master_key_flag_precedes_environment_and_service_env(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "environment-key")
    calls = []
    monkeypatch.setattr(
        MODULE,
        "_request",
        lambda url, key="": calls.append((url, key))
        or ((200, {"data": []}, "") if url.endswith("/v1/models") else (200, {}, "")),
    )

    code, summary = MODULE.diagnose("flag-key")

    assert code == 0 and "HEALTHY" in summary
    assert all(key in {"", "flag-key"} for _, key in calls)


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


def test_models_400_is_classified_with_healthy_readiness(monkeypatch, tmp_path):
    setup_home(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")

    def request(url, key=""):
        if url.endswith("/v1/models"):
            return 400, None, "http-error"
        return 200, {}, ""

    monkeypatch.setattr(MODULE, "_request", request)
    code, summary = MODULE.diagnose()
    assert code == 1 and "MISSING-OR-INVALID-MASTER-KEY" in summary


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
    assert [url.rsplit("/", 1)[-1] for url, _ in calls] == ["liveliness"]


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
    assert code == 1 and "PROXY-UNAVAILABLE" in summary
