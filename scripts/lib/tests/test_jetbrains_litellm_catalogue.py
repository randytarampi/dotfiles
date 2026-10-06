import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "generate-jetbrains-profiles.py"
SPEC = importlib.util.spec_from_file_location("junie_catalogue_test", SCRIPT)
PROFILES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILES)


def test_catalogue_uses_private_key_and_exact_known_ids(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {"data": [{"id": "google/models/gemini-3.8-flash"}]}
            ).encode()

    def open_same_origin(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(PROFILES, "open_same_origin", open_same_origin)
    assert PROFILES.litellm_catalogue_models(
        "http://127.0.0.1:4000/v1/chat/completions", "dummy-private-key"
    ) == {"google/models/gemini-3.8-flash"}
    assert captured["request"].full_url == "http://127.0.0.1:4000/v1/models"
    assert captured["request"].get_header("Authorization") == "Bearer dummy-private-key"
    assert captured["timeout"] == PROFILES.MODEL_CATALOGUE_TIMEOUT == 45


def test_delayed_catalogue_succeeds_with_shared_consumer_deadline(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"data":[{"id":"openai/gpt-delayed"}]}'

    def delayed_response(_request, timeout):
        assert timeout >= 30  # Simulates the observed 30-second service response.
        return Response()

    monkeypatch.setattr(PROFILES, "open_same_origin", delayed_response)
    assert PROFILES.litellm_catalogue_models(
        "http://127.0.0.1:4000/v1", "stubbed-key"
    ) == {"openai/gpt-delayed"}


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"data": []}, set()),
        (
            {"data": [{"id": "google/models/gemini-3.8-flash"}]},
            {"google/models/gemini-3.8-flash"},
        ),
        ({"data": "malformed"}, None),
        ({"unexpected": []}, None),
        ({"data": [{"name": "missing-id"}]}, None),
    ],
)
def test_catalogue_known_empty_exact_ids_and_malformed(monkeypatch, payload, expected):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(
        PROFILES, "open_same_origin", lambda *_args, **_kwargs: Response()
    )
    assert (
        PROFILES.litellm_catalogue_models("http://127.0.0.1:4000/v1", "dummy")
        == expected
    )


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("http://127.0.0.1:4000/v1/models", 401, "unauthorized", {}, None),
        HTTPError("http://127.0.0.1:4000/v1/models", 503, "unavailable", {}, None),
        URLError("offline"),
    ],
)
def test_unavailable_catalogue_preserves_litellm_groups(monkeypatch, error):
    groups = {
        "litellm-openai": {"provider": "litellm", "primaryModel": "openai/gpt-6"},
        "litellm-google": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
        },
    }
    monkeypatch.setattr(
        PROFILES,
        "open_same_origin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    catalogue = PROFILES.litellm_catalogue_models("http://127.0.0.1:4000/v1", "dummy")
    assert catalogue is None
    assert PROFILES.filter_litellm_groups(groups, catalogue) == groups


def test_known_empty_catalogue_omits_managed_groups_but_direct_google_id_is_distinct():
    groups = {
        "google-direct": {"provider": "google", "primaryModel": "gemini-3.8-flash"},
        "litellm-google": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
        },
        "litellm-openai": {"provider": "litellm", "primaryModel": "openai/gpt-6"},
    }
    assert set(PROFILES.filter_litellm_groups(groups, set())) == {"google-direct"}
    assert (
        groups["google-direct"]["primaryModel"]
        != groups["litellm-google"]["primaryModel"]
    )


def test_catalogue_rejects_non_loopback_before_sending_key(monkeypatch):
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not send private key")

    monkeypatch.setattr(PROFILES, "open_same_origin", forbidden)
    assert (
        PROFILES.litellm_catalogue_models("http://localhost:4000/v1", "dummy") is None
    )
    assert (
        PROFILES.litellm_catalogue_models("https://127.0.0.1:4000/v1", "dummy") is None
    )
    assert not called


def test_same_origin_redirect_guard_rejects_remote_before_forwarding_key():
    from model_catalogues import SameOriginRedirectHandler

    request = Request(
        "http://127.0.0.1:4000/v1/models",
        headers={"Authorization": "Bearer dummy-private-key"},
    )
    with pytest.raises(URLError, match="cross-origin"):
        SameOriginRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://attacker.invalid/collect"
        )


def test_cleanup_removes_only_owned_stale_profile_and_rejects_symlink(tmp_path):
    target = tmp_path / "models"
    target.mkdir(mode=0o700)
    stale = target / "litellm-missing.json"
    stale.write_text("owned", encoding="utf-8")
    user = target / "manual.json"
    user.write_text("user", encoding="utf-8")
    PROFILES.cleanup_profiles(target, {"litellm-missing"}, set(), set(), False)
    assert not stale.exists()
    assert user.read_text(encoding="utf-8") == "user"

    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    stale.symlink_to(outside)
    with pytest.raises(OSError, match="unsafe stale"):
        PROFILES.cleanup_profiles(target, {"litellm-missing"}, set(), set(), False)
    assert outside.read_text(encoding="utf-8") == "keep"
