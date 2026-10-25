import importlib.util
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "generate-jetbrains-profiles.py"
SPEC = importlib.util.spec_from_file_location("junie_catalogue_test", SCRIPT)
PROFILES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILES)


def test_shared_free_and_compatibility_resolve_before_gateway_twins():
    cfg = json.loads(
        (SCRIPT.parent.parent / "configs/junie/model-groups.json").read_text()
    )
    registry = PROFILES.tier_registry.load_registry()
    groups = PROFILES.resolve_model_groups(cfg, registry)
    roles = PROFILES.tier_registry.materialize_role_models(registry, "free", {})
    assert (
        groups["free"]["provider"] + "/" + groups["free"]["primaryModel"]
        == roles["orchestrator"]
    )
    assert (
        groups["free"]["fasterProvider"] + "/" + groups["free"]["fasterModel"]
        == roles["librarian"]
    )
    for alias, canonical in cfg["compatibility"].items():
        assert groups[alias] == groups[canonical]
    twins = PROFILES.derive_litellm_groups(groups)
    assert twins["litellm-meridian-opus"] == twins["litellm-anthropic-opus"]
    assert groups["ollama-cloud-deepseek"]["primaryModel"] == "deepseek-v4-pro:0813"


def test_frozen_group_input_does_not_implicitly_derive_free():
    cfg = {"groups": {"free": {"provider": "custom", "primaryModel": "frozen"}}}
    assert PROFILES.resolve_model_groups(cfg, {}) == cfg["groups"]
    with pytest.raises(ValueError, match="Invalid compatibility"):
        PROFILES.resolve_model_groups({**cfg, "compatibility": {"free": "free"}}, {})


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


def test_routed_groups_derive_twins_including_local_and_verify_pairing():
    groups = {
        "google-gemini-flash": {
            "provider": "google",
            "primaryModel": "gemini-3.8-flash",
            "fasterModel": "gemini-3.5-flash-lite",
            "fasterProvider": "google",
        },
        "local-quiet": {"provider": "ollama", "primaryModel": "_local:code-gen"},
        "anthropic-sonnet": {
            "provider": "meridian",
            "primaryModel": "claude-sonnet-5-5",
        },
    }
    derived = PROFILES.derive_litellm_groups(groups)
    assert derived["litellm-google-gemini-flash"] == {
        "provider": "litellm",
        "primaryModel": "google/models/gemini-3.8-flash",
        "fasterModel": "google/models/gemini-3.5-flash-lite",
        "fasterProvider": "litellm",
    }
    assert derived["litellm-local-quiet"]["primaryModel"] == "ollama/_local:code-gen"
    assert derived["litellm-anthropic-sonnet"] == {
        "provider": "litellm",
        "primaryModel": "meridian/claude-sonnet-5-5",
    }
    assert PROFILES.select_model_groups(groups, True)["litellm-anthropic-sonnet"] == (
        derived["litellm-anthropic-sonnet"]
    )
    assert PROFILES.select_model_groups(groups, False) == groups
    changed = {
        **groups,
        "anthropic-sonnet": {
            **groups["anthropic-sonnet"],
            "primaryModel": "claude-sonnet-next",
        },
    }
    assert (
        PROFILES.derive_litellm_groups(changed)["litellm-anthropic-sonnet"][
            "primaryModel"
        ]
        == "meridian/claude-sonnet-next"
    )
    assert (
        PROFILES.select_model_groups(groups, True)["litellm-local-quiet"]
        == derived["litellm-local-quiet"]
    )


def test_twin_temperature_identity_preserves_model_family():
    temperature = PROFILES.lookup_model_temperature
    overrides = {"gemini": 1, "gpt": 0.9}
    plain_identity = PROFILES.temperature_identity("google-gemini-flash")
    twin_identity = PROFILES.temperature_identity("litellm-google-gemini-flash")
    assert plain_identity == twin_identity
    assert temperature("gemini-3.8-flash", overrides) == 1
    assert temperature("google/models/gemini-3.8-flash", overrides) == 1


def test_routed_group_verification_rejects_suppressed_twin():
    groups = {"openai-gpt": {"provider": "openai", "primaryModel": "gpt-6-luna"}}
    assert PROFILES.routed_group_violations(groups, {}) == [
        "Routed Junie group openai-gpt has no matching twin litellm-openai-gpt"
    ]
    assert PROFILES.routed_group_violations(groups) == []


@pytest.mark.parametrize("inventory", ["omlx", "untyped", "mixed", "absent", "unknown"])
def test_junie_pool_excludes_specialized_inventory(inventory):
    names = [
        "ornith-1.5:35b-a3b",
        "mellum2:12b-a2.5b",
        "gemma4:12b-mxfp8",
        "whisper-2",
        "qwen3-asr:1.7b",
        "qwen3-tts",
        "chatterbox-tts",
        "nomic-embed-text",
        "markitdown",
        "strands-decider:2b",
    ]
    specialized = {
        "whisper-2": "audio_stt",
        "qwen3-asr:1.7b": "audio_stt",
        "qwen3-tts": "audio_tts",
        "chatterbox-tts": "audio_tts",
        "nomic-embed-text": "embedding",
        "markitdown": "conversion",
        "strands-decider:2b": "decision",
    }
    # append_pool_profile_specs processes only non-legacy pools (engines
    # without resolve_model — oMLX): ollama pools keep group-driven specs.
    pools = {}
    if inventory != "absent":
        typed_pool = [
            {
                "name": name,
                "primary_category": specialized.get(name, "code-gen"),
                "model_type": specialized.get(name, "llm"),
            }
            for name in names
        ]
        if inventory == "unknown":
            typed_pool = [
                {**entry, "primary_category": "unknown"} for entry in typed_pool
            ]
        pools["omlx"] = typed_pool
        if inventory == "mixed":
            # Ollama pools are legacy: skipped by the resolve_model guard.
            pools["ollama"] = [
                {"name": name, "provider": "ollama", "size_gb": 8.0} for name in names
            ]
    # Real discovery shape: untyped records carry name/provider/size only —
    # neither primary_category nor model_type exists for the filters to see.
    if inventory == "untyped":
        pools = {
            "omlx": [
                {"name": name, "provider": "omlx", "size_gb": 8.0} for name in names
            ]
        }
    specs = []
    PROFILES.append_pool_profile_specs(specs, pools)
    selected = {primary for _, primary, *_ in specs}
    assert not any(name in ref for name in names[3:] for ref in selected)
    if inventory in {"omlx", "untyped", "mixed"}:
        # A real chat model must survive the pool filter.
        assert any("ornith-1.5:35b-a3b" in ref for ref in selected)


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


def test_unavailable_catalogue_omits_litellm_groups_and_keeps_direct(monkeypatch):
    groups = {
        "litellm-openai": {"provider": "litellm", "primaryModel": "openai/gpt-6"},
        "litellm-google": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
        },
        "google-direct": {"provider": "google", "primaryModel": "gemini-3.8-flash"},
    }
    monkeypatch.setattr(
        PROFILES,
        "open_same_origin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    )
    catalogue = PROFILES.litellm_catalogue_models("http://127.0.0.1:4000/v1", "dummy")
    assert catalogue is None
    failed = PROFILES.filter_litellm_groups(groups, catalogue)
    assert set(failed) == {"google-direct"}


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


def test_missing_faster_alias_drops_only_faster_fields():
    groups = {
        "litellm-google": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
            "fasterModel": "google/models/gemini-3.5-flash-lite",
            "fasterProvider": "litellm",
        },
        "litellm-openai": {
            "provider": "litellm",
            "primaryModel": "openai/gpt-6-luna",
            "fasterModel": "openai/gpt-6-mini",
            "fasterProvider": "litellm",
        },
    }
    catalogue = {
        "google/models/gemini-3.8-flash",
        "openai/gpt-6-luna",
        "openai/gpt-6-mini",
    }
    filtered = PROFILES.filter_litellm_groups(groups, catalogue)
    assert filtered["litellm-google"] == {
        "provider": "litellm",
        "primaryModel": "google/models/gemini-3.8-flash",
    }
    assert filtered["litellm-openai"] == groups["litellm-openai"]


def test_missing_primary_alias_omits_group_and_keeps_available_siblings():
    groups = {
        "litellm-google": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
        },
        "litellm-openai": {
            "provider": "litellm",
            "primaryModel": "openai/gpt-6-luna",
            "fasterModel": "openai/gpt-6-mini",
            "fasterProvider": "litellm",
        },
    }
    filtered = PROFILES.filter_litellm_groups(groups, {"openai/gpt-6-luna"})
    assert set(filtered) == {"litellm-openai"}


def test_derive_omits_unrouted_faster_provider_with_diagnostic(caplog):
    groups = {
        "openai-gpt": {
            "provider": "openai",
            "primaryModel": "gpt-6-luna",
            "fasterModel": "gpt-6-mini",
            "fasterProvider": "direct-fast-lane",
        }
    }
    derived = PROFILES.derive_litellm_groups(groups)
    assert derived["litellm-openai-gpt"] == {
        "provider": "litellm",
        "primaryModel": "openai/gpt-6-luna",
    }
    assert "direct-fast-lane" in caplog.text
    assert "faster" in caplog.text


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
    import hashlib

    owned = {"litellm-missing": hashlib.sha256(b"owned").hexdigest()}
    PROFILES.cleanup_profiles(target, owned, set(), set(), False)
    assert not stale.exists()
    assert user.read_text(encoding="utf-8") == "user"

    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    stale.symlink_to(outside)
    with pytest.raises(OSError, match="unsafe stale"):
        PROFILES.cleanup_profiles(target, owned, set(), set(), False)
    assert outside.read_text(encoding="utf-8") == "keep"
