import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from constants import OMLX_DEFAULT_PORT

import local_engines
import selected_service
from selected_service import Operation, Qualification, Transport

ROOT = Path(__file__).resolve().parents[2]

# Ambient OMLX_*/gate vars from a developer's sourced ~/.env leak into tests
# that assume an env-free ambient (default ports, no live daemon, gates off).
# Strip them so results are hermetic regardless of the invoking shell; tests
# that need them set them explicitly via monkeypatch.setenv.
HERMETIC_ENV_VARS = (
    "OMLX_BASE_URL",
    "OMLX_HOST",
    "OMLX_PORT",
    "OMLX_API_KEY",
    "OMLX_MCP_EXPOSE_TOOLS",
    "DOTFILES_RUN_OMLX_SETUP",
    "DOTFILES_USE_LOCAL_OMLX",
)


@pytest.fixture(autouse=True)
def _isolate_omlx_env(monkeypatch):
    for name in HERMETIC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_local_stt_uses_registered_engine_and_opt_out(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "true")
    monkeypatch.setenv("OMLX_API_KEY", "secret")
    # Voice-LLM resolution is fail-closed (PR1) and is not this test's
    # subject. Stub its inputs so the STT assertions stay hermetic and do
    # not depend on a live Ollama daemon or on tier-registry contents.
    monkeypatch.setattr(
        voice,
        "list_local_ollama_models",
        lambda: [{"name": "smol-coder:4b", "provider": "ollama"}],
    )
    monkeypatch.setattr(
        voice,
        "resolve_roles_from_list",
        lambda *args, **kwargs: {"lightweight": "ollama/smol-coder:4b"},
    )
    monkeypatch.setattr(
        voice,
        "get_model_details",
        lambda name, provider="ollama": {
            "param_count": 4,
            "capabilities": ["completion", "tools"],
            "is_moe": False,
            "model_type": "llm",
        },
    )
    with (
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "HTTP 200"),
                "audio_discovery": lambda: [
                    {"name": "omlx/whisper", "model_type": "audio_stt"}
                ],
                "list_models": lambda include_cloud=False: [],
                "base_url": lambda: "http://127.0.0.1:8000",
            },
        ),
        patch.object(
            voice.tier_registry,
            "load_registry",
            return_value={
                "preset": "local",
                "presets": {
                    "local": {
                        "librarian": {"model": "_local:lightweight"},
                    }
                },
            },
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        config = voice.get_voice_config("local")
    # Local STT remains omitted without qualification evidence.
    assert "sttEndpoint" not in config

    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "false")
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    with patch.object(
        voice.tier_registry, "uses_local_placeholders", return_value=False
    ):
        assert "sttEndpoint" not in voice.get_voice_config("local")


def test_voice_local_stt_absent_falls_back(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with patch.dict(
        local_engines.LOCAL_ENGINES["omlx"],
        {
            "audio_discovery": lambda: [],
            "list_models": lambda include_cloud=False: [],
        },
    ):
        assert "sttEndpoint" not in voice.get_voice_config("local")


def test_voice_empty_pool_is_unresolved_without_partial_config(monkeypatch, caplog):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(
        voice,
        "iter_engine_discovery",
        lambda provider: local_engines.DiscoveryResult("ok_empty", ()),
    )
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice.tier_registry, "materialize_role_models", return_value={}),
        caplog.at_level("WARNING"),
    ):
        result = voice.get_voice_config("local")
    assert result["_voice_unresolved"] is True
    assert result["_voice_state"] is None
    assert "unresolved" in caplog.text


@pytest.mark.parametrize("status", ["unknown_error", "ok_models"])
def test_voice_unverified_or_unknown_pool_is_unresolved(monkeypatch, status):
    voice = load_script("configure-opencode-voice.py")
    records = (
        ({"name": "omlx/audio", "provider": "omlx"},) if status == "ok_models" else ()
    )
    monkeypatch.setattr(
        voice,
        "iter_engine_discovery",
        lambda provider: local_engines.DiscoveryResult(status, records),
    )
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(
        voice, "get_model_details", lambda *a, **k: {"capabilities": ["audio"]}
    )
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice.tier_registry, "materialize_role_models", return_value={}),
    ):
        assert voice.get_voice_config("local")["_voice_unresolved"] is True


@pytest.mark.parametrize("status", ["unknown_error", "ok_empty", "ok_models"])
def test_voice_main_preserves_llm_fields_when_unresolved_with_stt(
    monkeypatch, tmp_path, status
):
    voice = load_script("configure-opencode-voice.py")
    config_dir = tmp_path / status
    config_dir.mkdir()
    original = {
        "endpoint": "https://working.example/v1",
        "model": "working-model",
        "apiKeyEnv": "WORKING_KEY",
        "ttsVoice": "vivian",
        "sttEndpoint": "https://old-stt.example/v1",
        "sttModel": "old-whisper",
    }
    (config_dir / "tui.json").write_text(
        json.dumps({"plugin": [["@renjfk/opencode-voice", original]]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", "local", "--no-backup"])
    # Drive the REAL get_voice_config; stub only the adapter boundary so the
    # unresolved gate itself is exercised (a stubbed get_voice_config would
    # make the gate invisible to mutation checks).
    from local_engines import DiscoveryResult

    monkeypatch.setattr(
        voice,
        "iter_engine_discovery",
        lambda _provider: DiscoveryResult(
            status="ok_models",
            records=[{"name": f"plain-llm-{status}", "provider": "ollama"}],
        ),
    )
    monkeypatch.setattr(voice, "get_model_details", lambda *a, **k: {})
    monkeypatch.setattr(
        voice,
        "audio_models",
        lambda _provider: [{"name": f"whisper-{status}", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(
        voice,
        "local_endpoint_for",
        lambda _provider, _kind: ("http://127.0.0.1:11427/v1", "OMLX_API_KEY"),
    )
    assert voice.main() is None
    # The preserved legacy route maps onto the current manifest spec in
    # cli.json with verbatim options; the managed tui.json entry is cleaned.
    cli = json.loads((config_dir / "cli.json").read_text(encoding="utf-8"))
    entry_holder = [p for p in cli if isinstance(p, dict)]
    assert len(entry_holder) == 1
    entry = entry_holder[0]["options"]
    assert entry_holder[0]["package"].startswith("@bojackduy/opencode-voice")
    assert entry["endpoint"] == original["endpoint"]
    assert entry["model"] == original["model"]
    assert entry["apiKeyEnv"] == original["apiKeyEnv"]
    assert entry["ttsVoice"] == original["ttsVoice"]
    assert entry["sttEndpoint"] == original["sttEndpoint"]
    assert entry["sttModel"] == original["sttModel"]
    tui = json.loads((config_dir / "tui.json").read_text(encoding="utf-8"))
    assert tui["plugin"] == []


def test_voice_main_keeps_unresolved_marker_without_existing_entry(
    monkeypatch, tmp_path
):
    voice = load_script("configure-opencode-voice.py")
    config_dir = tmp_path / "no-prior-entry"
    config_dir.mkdir()
    (config_dir / "tui.json").write_text('{"plugin": []}', encoding="utf-8")
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(sys, "argv", ["voice", "--no-backup"])
    monkeypatch.setattr(
        voice, "get_voice_config", lambda *args, **kwargs: {"_voice_unresolved": True}
    )
    assert voice.main() is None
    # Routeless + cli.json absent: the file must NOT be created, so the
    # upstream tui.json->cli.json migration stays available on first start.
    assert not (config_dir / "cli.json").exists()
    tui = json.loads((config_dir / "tui.json").read_text(encoding="utf-8"))
    assert tui["plugin"] == []


def test_voice_main_preserves_unknown_options_on_resolution(monkeypatch, tmp_path):
    """User-set option keys (sttAutoSubmit etc.) survive a fresh resolution."""
    voice = load_script("configure-opencode-voice.py")
    config_dir = tmp_path / "user-options"
    config_dir.mkdir()
    (config_dir / "cli.json").write_text(
        json.dumps(
            [
                {
                    "package": "@bojackduy/opencode-voice@latest",
                    "options": {
                        "endpoint": "http://previous/v1",
                        "model": "previous",
                        "sttAutoSubmit": True,
                        "ttsVoice": "vivian",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", "pro", "--no-backup"])
    monkeypatch.setattr(
        voice,
        "get_voice_config",
        lambda *a, **k: {"endpoint": "http://resolved/v1", "model": "resolved"},
    )
    assert voice.main() is None
    cli = json.loads((config_dir / "cli.json").read_text(encoding="utf-8"))
    entries = [p for p in cli if isinstance(p, dict)]
    assert len(entries) == 1
    options = entries[0]["options"]
    assert options["endpoint"] == "http://resolved/v1"
    assert options["model"] == "resolved"
    assert options["sttAutoSubmit"] is True
    assert options["ttsVoice"] == "vivian"


def test_voice_dry_run_never_prints_foreign_plugin_values(
    monkeypatch, tmp_path, capsys
):
    voice = load_script("configure-opencode-voice.py")
    sentinel = "VOICE_SENTINEL_SECRET_92814"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "tui.json").write_text(
        json.dumps(
            {
                "plugin": [["foreign", {"nested": {"token": sentinel}}]],
                "setting": sentinel,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", "pro-plus", "--dry-run"])
    monkeypatch.setattr(
        voice,
        "get_voice_config",
        lambda *a, **kwargs: {"endpoint": "http://example", "model": "model"},
    )
    voice.main()
    assert sentinel not in capsys.readouterr().out


@pytest.mark.parametrize("existing", [False, True])
def test_voice_main_writes_resolved_llm_without_audio_state(
    monkeypatch, tmp_path, existing
):
    voice = load_script("configure-opencode-voice.py")
    original = ["@renjfk/opencode-voice", {"endpoint": "http://old/v1", "model": "old"}]
    other = ["foreign", {"enabled": True}]
    (tmp_path / "tui.json").write_text(
        json.dumps({"plugin": [other, original] if existing else [other]})
    )
    prior_state = {"sttTransport": "openai"}
    voice._save_voice_state(str(tmp_path), prior_state)
    resolved = {
        "endpoint": "http://resolved/v1",
        "model": "resolved",
        "_voice_state": None,
    }
    resolver = Mock(return_value=resolved)
    monkeypatch.setattr(voice, "get_voice_config", resolver)
    monkeypatch.setattr(voice, "load_env", lambda *args: False)
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", "pro", "--no-backup"])

    assert voice.main() is None

    assert resolver.call_args.kwargs == {"state": prior_state}
    # The resolved route lands in cli.json as the manifest-spec object; the
    # managed legacy tui entry is cleaned and foreign entries preserved.
    cli = json.loads((tmp_path / "cli.json").read_text(encoding="utf-8"))
    voice_entries = [
        p
        for p in cli
        if isinstance(p, dict)
        and p.get("package", "").startswith("@bojackduy/opencode-voice")
    ]
    assert voice_entries == [
        {
            "package": "@bojackduy/opencode-voice@latest",
            "options": {"endpoint": "http://resolved/v1", "model": "resolved"},
        },
    ]
    assert [p for p in cli if isinstance(p, dict)] == voice_entries
    tui = json.loads((tmp_path / "tui.json").read_text(encoding="utf-8"))
    assert tui["plugin"] == [other]


def test_voice_main_persists_transport_without_audio_selections(monkeypatch, tmp_path):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DOTFILES_USE_LITELLM_PROXY", raising=False)
    monkeypatch.setattr(voice, "load_env", lambda *args: False)
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "active_engines", lambda: [])
    voice._save_voice_state(str(tmp_path), {"sttTransport": "local"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["voice", "--preset", "pro", "--stt-transport", "openai", "--no-backup"],
    )

    assert voice.main() is None

    state = voice._load_voice_state(str(tmp_path))
    assert state["sttTransport"] == "openai"
    assert state["sttTransportReason"] == "explicit --stt-transport openai"
    assert "cleanup_llm" in state["selections"]
    options = json.loads((tmp_path / "cli.json").read_text(encoding="utf-8"))[0]
    assert isinstance(options, dict)
    assert options["options"]["model"] == "gemma4:31b"


@pytest.mark.parametrize(
    "tier",
    ["local", "local-solo", "pro", "plus", "pro-plus", "anthropic", "free"],
)
@pytest.mark.parametrize("gateway", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_voice_config_only_tier_matrix_main_contract(
    monkeypatch, tmp_path, caplog, tier, gateway, existing
):
    voice = load_script("configure-opencode-voice.py")
    config_dir = tmp_path / f"{tier}-{gateway}-{existing}"
    config_dir.mkdir()
    # Isolate HOME so the resolver cannot see the host's real opencode.key.
    monkeypatch.setenv("HOME", str(tmp_path))
    if existing:
        (config_dir / "tui.json").write_text(
            json.dumps({"plugin": [["foreign", {}]]}), encoding="utf-8"
        )
        voice._save_voice_state(str(config_dir), {"sttTransport": "local"})
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1" if gateway else "0")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "scoped-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-test-key")
    monkeypatch.setattr(voice, "load_env", lambda *_: False)
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "active_engines", lambda: [])
    monkeypatch.setattr(voice, "audio_models", lambda *_: [])
    monkeypatch.setattr(
        voice,
        "list_local_ollama_models",
        lambda: [{"name": "installed", "provider": "ollama"}],
    )
    monkeypatch.setattr(
        voice, "get_model_details", lambda *_a, **_k: {"capabilities": ["completion"]}
    )
    monkeypatch.setattr(
        voice,
        "resolve_roles_from_list",
        lambda *_a, **_k: {"lightweight": "ollama/installed"},
    )
    monkeypatch.setattr(
        voice.tier_registry,
        "materialize_role_models",
        lambda *_a, **_k: {"librarian": "ollama/installed"},
    )
    monkeypatch.setattr(
        voice,
        "local_endpoint_for",
        lambda *_a, **_k: ("http://localhost:11434/v1", None),
    )
    monkeypatch.setattr(voice, "get_available_tiers", lambda: [tier])
    if tier.startswith("local") and existing:
        (config_dir / "tui.json").write_text(
            json.dumps(
                {
                    "plugin": [
                        [
                            "@renjfk/opencode-voice",
                            {"endpoint": "http://old", "model": "old-model"},
                        ]
                    ]
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(voice, "_gateway_alias", lambda *_: "openai/gpt-6-luna")
    monkeypatch.setattr(
        voice.tier_registry,
        "load_registry",
        lambda: {"presets": {}, "_tiers": {}},
    )
    monkeypatch.setattr(
        voice.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {
            "orchestrator": "opencode/muse-spark-1.3-contributor-free"
        },
    )
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", tier, "--no-backup"])
    assert voice.main() is None
    # A routeless outcome with cli.json absent creates nothing (the upstream
    # migration window must stay available).
    if (config_dir / "cli.json").exists():
        cli = json.loads((config_dir / "cli.json").read_text(encoding="utf-8"))
    else:
        cli = []
    voice_entries = [
        p
        for p in cli
        if isinstance(p, dict)
        and p.get("package", "").startswith("@bojackduy/opencode-voice")
    ]
    if tier.startswith("local"):
        assert len(voice_entries) == int(existing)
        if existing:
            assert voice_entries[0]["options"]["model"] == "old-model"
        assert "unresolved" in caplog.text
        assert (
            "installed local model; config-only, runtime unverified" not in caplog.text
        )
        if existing:
            state = voice._load_voice_state(str(config_dir))
            assert state["sttTransport"] == "local"
        else:
            assert not (config_dir / "opencode-voice.json").exists()
        return
    assert len(voice_entries) == 1
    options = voice_entries[0]["options"]
    if tier not in ("local", "local-solo", "pro"):
        assert options["endpoint"] == (
            f"http://127.0.0.1:{voice._configured_litellm_port() or 4000}/v1"
            if gateway
            else options["endpoint"]
        )
    assert options["model"]
    state = voice._load_voice_state(str(config_dir))
    assert state["sttTransport"] == ("local" if existing or not gateway else "openai")
    assert state["sttTransportReason"] in (
        "saved selection" if existing else "global gateway gate",
        "global gateway gate",
        "no binding input; local default",
    )
    if tier in ("plus", "pro-plus", "anthropic", "free"):
        assert state["selections"]["cleanup_llm"]["transport"] == (
            "gateway" if gateway else "direct"
        )
        assert "runtime unverified" in state["selections"]["cleanup_llm"]["reason"]


@pytest.mark.parametrize("failure", ["missing-alias", "missing-key"])
def test_voice_gateway_tier_fails_closed(failure, monkeypatch, tmp_path):
    voice = load_script("configure-opencode-voice.py")
    config_dir = tmp_path / failure
    config_dir.mkdir()
    # Isolate HOME so the resolver cannot see the host's real opencode.key.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv(
        "LITELLM_OPENCODE_KEY", "" if failure == "missing-key" else "key"
    )
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    monkeypatch.setattr(voice, "load_env", lambda *_: False)
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "active_engines", lambda: [])
    monkeypatch.setattr(voice, "audio_models", lambda *_: [])
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(voice, "get_available_tiers", lambda: ["plus"])
    monkeypatch.setattr(voice.tier_registry, "load_registry", lambda: {"presets": {}})
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(
        voice,
        "_gateway_alias",
        lambda *_: None if failure == "missing-alias" else "openai/gpt-6-luna",
    )
    monkeypatch.setattr(sys, "argv", ["voice", "--preset", "plus", "--no-backup"])
    voice.main()
    # Failed-close resolution with cli.json absent: no file is created, so
    # the upstream migration window stays open and no route is registered.
    assert not (config_dir / "cli.json").exists()
    state = voice._load_voice_state(str(config_dir))
    record = state["selections"]["cleanup_llm"]
    assert record["transport"] == "unavailable"
    assert record["qualification"] == "unavailable"


def _voice_test_config(monkeypatch, voice, discovery, key=None):
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        local_engines, "engine_gate_active", lambda provider: provider == "omlx"
    )
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    with (
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"], {"audio_discovery": lambda: discovery}
        ),
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        return voice.get_voice_config("pro-plus")


def test_voice_auto_discovered_omlx_emits_no_tts_keys(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_VOICE_TTS_VOICE", "vivian")
    monkeypatch.setenv("OMLX_PORT", "11427")
    config = _voice_test_config(
        monkeypatch, voice, [{"name": "omlx/model-a", "model_type": "audio_tts"}]
    )
    assert not any(key.lower().startswith("tts") for key in config)


def test_voice_auto_without_local_tts_logs_consumer_note(monkeypatch, caplog):
    voice = load_script("configure-opencode-voice.py")
    with caplog.at_level("WARNING"):
        config = _voice_test_config(monkeypatch, voice, [])
    assert not any(key.lower().startswith("tts") for key in config)
    assert "synthesizes via local Piper" in caplog.text
    assert "Open WebUI audio bundle" in caplog.text


def test_voice_explicit_piper(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        config = voice.get_voice_config("pro-plus")
    assert not any(key.lower().startswith("tts") for key in config)


def test_voice_openai_credentials_do_not_emit_tts_config(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
    ):
        config = voice.get_voice_config("pro-plus")
    assert not any(key.lower().startswith("tts") for key in config)


def test_voice_parser_rejects_removed_tts_transport(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setattr(voice, "get_available_tiers", lambda: ["pro-plus"])
    monkeypatch.setattr(voice, "add_model_override_args", lambda parser: None)
    monkeypatch.setattr(voice, "add_min_reasoning_embedding_arg", lambda parser: None)
    monkeypatch.setattr(voice, "load_env", lambda *args: True)
    monkeypatch.setattr(voice.os.path, "exists", lambda path: False)
    monkeypatch.setattr(voice.os.path, "expanduser", lambda path: "/tmp/not-real-home")
    monkeypatch.setattr(voice.os, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(voice, "write_text_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys, "argv", ["configure-opencode-voice.py", "--tts-transport", "auto"]
    )
    with pytest.raises(SystemExit) as error:
        voice.main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "flag,gate,saved,expected",
    [
        (None, True, "local", "local"),
        ("auto", True, "local", "openai"),
        (None, False, "openai", "openai"),
        ("auto", False, "openai", "local"),
        ("auto", None, "openai", "openai"),
        (None, None, None, "local"),
        ("local", True, "openai", "local"),
        ("openai", False, "local", "openai"),
    ],
)
def test_voice_transport_precedence(flag, gate, saved, expected):
    load_script("configure-opencode-voice.py")
    assert (
        selected_service.resolve_stt_transport(flag, gateway_gate=gate, saved=saved)[0]
        == expected
    )


@pytest.mark.parametrize("transport,gate", [("local", "1"), ("openai", "0")])
def test_voice_transport_persists_across_tiers(monkeypatch, tmp_path, transport, gate):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", gate)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice, "load_env", lambda *args: True)
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "active_engines", lambda: [])
    monkeypatch.setattr(voice, "audio_models", lambda _: [])
    monkeypatch.setattr(voice.tier_registry, "load_registry", lambda: {"presets": {}})
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    for tier, flags in [("anthropic", ["--stt-transport", transport]), ("plus", [])]:
        monkeypatch.setattr(
            sys, "argv", ["configure-opencode-voice.py", "--preset", tier, *flags]
        )
        voice.main()
        state = voice._load_voice_state(str(tmp_path))
        assert state["sttTransport"] == transport
    assert state["sttTransportReason"] == "saved selection"


def test_voice_selector_omits_unqualified_stt_and_cleanup(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    candidates = [("omlx", "whisper", Qualification.DISCOVERED)]
    locator = voice._service_locator(
        Operation.STT,
        candidates,
        "direct",
        endpoint="http://localhost/v1",
        wire_model="whisper",
        credential_required=False,
    )
    selection, route = locator.selection, locator.route
    assert selection.qualification is Qualification.DISCOVERED
    assert route.transport is Transport.UNAVAILABLE
    with pytest.raises(AssertionError):
        selected_service.assert_renders_from(locator, {"provider": "omlx"})


def test_voice_discovered_candidates_stay_unavailable_and_state_is_not_written(
    monkeypatch, tmp_path
):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_API_KEY", "scoped-key")
    candidate = [("omlx", "whisper", Qualification.DISCOVERED)]
    locator = voice._service_locator(
        Operation.STT,
        candidate,
        "gateway",
        endpoint="http://127.0.0.1:4000/v1",
        key_reference="LITELLM_API_KEY",
        credential_present=True,
    )
    assert locator.selection.qualification is Qualification.DISCOVERED
    assert locator.route.transport is Transport.UNAVAILABLE
    assert voice._render_locator(locator) is None
    state = {"sentinel": "preserve"}
    path = tmp_path / "opencode-voice.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert voice._load_voice_state(str(tmp_path)) == state
    assert json.loads(path.read_text(encoding="utf-8")) == state


def test_voice_gateway_gate_uses_litellm_and_scoped_key(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_API_KEY", "scoped-key")
    candidate = [("openai", "whisper-1", Qualification.PROTOCOL_PROBED)]
    gateway = voice._service_locator(
        Operation.STT,
        candidate,
        "gateway",
        endpoint="http://127.0.0.1:4000/v1",
        wire_model="openai/whisper-1",
        key_reference="LITELLM_API_KEY",
        credential_present=True,
    )
    assert gateway.route.transport is Transport.GATEWAY
    assert gateway.route.endpoint == "http://127.0.0.1:4000/v1"
    assert gateway.route.key_reference == "LITELLM_API_KEY"
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    direct = voice._service_locator(
        Operation.STT,
        candidate,
        "direct",
        endpoint="https://api.openai.com/v1",
        wire_model="whisper-1",
        key_reference="OPENAI_API_KEY",
        credential_present=True,
    )
    assert direct.route.transport is Transport.DIRECT


def test_voice_openai_binding_without_credentials_is_unavailable(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    locator = voice._service_locator(
        Operation.STT,
        [("openai", "whisper-1", Qualification.PROTOCOL_PROBED)],
        "gateway",
        endpoint="http://127.0.0.1:4000/v1",
        wire_model="openai/whisper-1",
        key_reference="LITELLM_OPENCODE_KEY",
        credential_reference="LITELLM_OPENCODE_KEY",
        credential_present=False,
    )
    assert locator.route.transport is Transport.UNAVAILABLE


def test_voice_tier_switch_preserves_previous_selection():
    voice = load_script("configure-opencode-voice.py")
    candidates = [
        ("omlx", "whisper", Qualification.PROTOCOL_PROBED),
        ("ollama", "whisper", Qualification.PROTOCOL_PROBED),
    ]
    first = voice._select_voice_candidate(Operation.STT, candidates, "local")
    second = voice._select_voice_candidate(
        Operation.STT, list(reversed(candidates)), "local", previously_selected=first
    )
    assert (second.provider, second.model) == (first.provider, first.model)


def test_voice_fixture_preference_is_host_scoped_and_deterministic():
    voice = load_script("configure-opencode-voice.py")
    candidates = [
        ("omlx", "whisper-a", Qualification.PROTOCOL_PROBED),
        ("ollama", "whisper-b", Qualification.PROTOCOL_PROBED),
    ]
    first = voice._select_voice_candidate(Operation.STT, candidates)
    second = voice._select_voice_candidate(Operation.STT, candidates)
    assert (first.provider, first.model) == (second.provider, second.model)
    assert voice.VOICE_PREFERENCES == {"default": {"stt": [], "cleanup_llm": []}}


def test_voice_consumer_config_contains_only_supported_fields():
    voice = load_script("configure-opencode-voice.py")
    supported = {
        "endpoint",
        "model",
        "apiKeyEnv",
        "sttEndpoint",
        "sttModel",
        "sttApiKeyEnv",
        "sttPrompt",
        "ttsAutoPrompt",
        "ttsManualPrompt",
    }
    config = voice.get_voice_config("local")
    config.pop("_voice_state", None)
    config.pop("_voice_unresolved", None)
    config.pop("_voice_status", None)
    assert set(config) <= supported


def test_voice_unqualified_tier_does_not_replace_existing_plugin(monkeypatch, tmp_path):
    voice = load_script("configure-opencode-voice.py")
    # The legacy v1 route lives in tui.json; its options must survive the v2
    # transition instead of being replaced by an unknown route.
    existing_options = {"endpoint": "https://old", "model": "old-model"}
    (tmp_path / "tui.json").write_text(
        json.dumps(
            {
                "plugin": [
                    ["@renjfk/opencode-voice", existing_options],
                    ["unrelated", {}],
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(
        sys, "argv", ["voice", "--preset", "omo-slim-openai", "--no-backup"]
    )
    monkeypatch.setattr(voice, "load_env", lambda *args: True)
    monkeypatch.setattr(voice, "get_available_tiers", lambda: ["omo-slim-openai"])
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "active_engines", lambda: [])
    monkeypatch.setattr(voice, "audio_models", lambda *_: [])
    monkeypatch.setattr(voice, "logger", voice.logger)
    monkeypatch.setattr(
        voice, "get_provider_base_url", lambda *_: "https://api.openai.com/v1"
    )
    monkeypatch.setattr(voice, "_render_locator", lambda locator: None)
    voice.main()
    written = json.loads((tmp_path / "cli.json").read_text(encoding="utf-8"))
    voice_entries = [
        p
        for p in written
        if isinstance(p, dict)
        and p.get("package", "").startswith("@bojackduy/opencode-voice")
    ]
    assert len(voice_entries) == 1
    assert voice_entries[0]["options"] == existing_options
    # Unrelated cli/tui entries are preserved exactly.
    tui = json.loads((tmp_path / "tui.json").read_text(encoding="utf-8"))
    assert tui["plugin"] == [["unrelated", {}]]
    assert [p for p in written if isinstance(p, dict)] == voice_entries


@pytest.mark.parametrize(
    "gate,transport,endpoint,key_name",
    [
        ("1", Transport.GATEWAY, "http://127.0.0.1:4000/v1", "LITELLM_OPENCODE_KEY"),
        ("0", Transport.DIRECT, "http://127.0.0.1:8123/v1", "OMLX_API_KEY"),
    ],
)
def test_voice_stt_render_uses_resolved_route_credentials(
    monkeypatch, tmp_path, gate, transport, endpoint, key_name
):
    voice = load_script("configure-opencode-voice.py")
    # Env-only admission: isolate HOME so the host's real opencode.key file
    # cannot conflict with the env-only credential under test.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", gate)
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "gateway-secret")
    monkeypatch.setenv("OMLX_API_KEY", "backend-secret")
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice, "check_ollama_daemon", return_value=(False, False)),
        patch.object(voice, "active_engines", return_value=["omlx"]),
        patch.object(voice, "resolve_engine", return_value={}),
        patch.object(
            voice,
            "audio_models",
            return_value=[{"name": "omlx/whisper-picked", "model_type": "audio_stt"}],
        ),
        patch.object(
            voice,
            "local_endpoint_for",
            return_value=("http://127.0.0.1:8123/v1", "OMLX_API_KEY"),
        ),
        patch.object(
            voice,
            "_select_voice_candidate",
            return_value=selected_service.CanonicalSelection(
                Operation.STT,
                "omlx",
                "omlx/whisper-picked",
                Qualification.PROTOCOL_PROBED,
                "fixture probe",
            ),
        ),
    ):
        config = voice.get_voice_config("anthropic", {"stt_transport": "local"})
    expected_wire = "whisper-picked"
    assert config["sttEndpoint"] == endpoint
    assert config["sttModel"] == expected_wire
    assert config["sttApiKeyEnv"] == key_name
    locator = voice._service_locator(
        Operation.STT,
        [("omlx", "omlx/whisper-picked", Qualification.PROTOCOL_PROBED)],
        "gateway" if gate == "1" else "direct",
        endpoint=endpoint,
        wire_model=expected_wire,
        key_reference=key_name,
        credential_present=True,
        credential_required=gate == "1",
    )
    assert config["sttEndpoint"] == locator.route.endpoint
    assert config["sttModel"] == locator.route.wire_model
    assert config["sttApiKeyEnv"] == locator.route.key_reference


@pytest.mark.parametrize(
    "gate,endpoint,key_name",
    [
        ("1", "http://127.0.0.1:4000/v1", "LITELLM_OPENCODE_KEY"),
        ("0", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ],
)
def test_voice_openai_stt_render_uses_resolved_route_credentials(
    monkeypatch, tmp_path, gate, endpoint, key_name
):
    voice = load_script("configure-opencode-voice.py")
    # Env-only admission: isolate HOME so the host's real opencode.key file
    # cannot conflict with (or satisfy) the env-only credential under test.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", gate)
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "gateway-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice, "check_ollama_daemon", return_value=(False, False)),
        patch.object(
            voice, "get_provider_base_url", return_value="https://api.openai.com/v1"
        ),
        patch.object(
            voice,
            "_select_voice_candidate",
            return_value=selected_service.CanonicalSelection(
                Operation.STT,
                "openai",
                "whisper-1",
                Qualification.PROTOCOL_PROBED,
                "fixture probe",
            ),
        ),
    ):
        config = voice.get_voice_config("anthropic", {"stt_transport": "openai"})
    assert config["sttEndpoint"] == endpoint
    assert config["sttModel"] == "whisper-1"
    assert config["sttApiKeyEnv"] == key_name
    selection = selected_service.CanonicalSelection(
        Operation.STT,
        "openai",
        "whisper-1",
        Qualification.PROTOCOL_PROBED,
        "fixture probe",
    )
    locator = voice._service_locator(
        Operation.STT,
        [("openai", "whisper-1", Qualification.PROTOCOL_PROBED)],
        "gateway" if gate == "1" else "direct",
        selection=selection,
        endpoint=endpoint,
        wire_model="whisper-1",
        key_reference=key_name,
        credential_reference=key_name if gate == "1" else None,
        credential_present=True,
        credential_required=True,
    )
    assert config["sttEndpoint"] == locator.route.endpoint
    assert config["sttModel"] == locator.route.wire_model
    assert config["sttApiKeyEnv"] == locator.route.key_reference


def test_voice_saved_selection_survives_fresh_process_and_reordered_inventory(
    monkeypatch, tmp_path
):
    voice = load_script("configure-opencode-voice.py")
    candidates = [
        ("omlx", "whisper-a", Qualification.PROTOCOL_PROBED),
        ("ollama", "whisper-b", Qualification.PROTOCOL_PROBED),
    ]
    saved = voice._select_voice_candidate(Operation.STT, candidates)
    state = {
        "sttTransport": "local",
        "selections": {
            "stt": {
                "provider": saved.provider,
                "model": saved.model,
                "qualification": saved.qualification.value,
            }
        },
    }
    voice._save_voice_state(str(tmp_path), state)
    fresh = load_script("configure-opencode-voice.py")
    restored = fresh._load_voice_state(str(tmp_path))
    selected = fresh._select_voice_candidate(
        Operation.STT,
        list(reversed(candidates)),
        previously_selected=selected_service.CanonicalSelection(
            Operation.STT,
            restored["selections"]["stt"]["provider"],
            restored["selections"]["stt"]["model"],
            Qualification(restored["selections"]["stt"]["qualification"]),
            "saved",
        ),
    )
    assert (selected.provider, selected.model) == (saved.provider, saved.model)


@pytest.mark.parametrize("flag", [None, "auto"])
def test_voice_unset_gate_uses_saved_transport_in_production_path(monkeypatch, flag):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.delenv("DOTFILES_USE_LITELLM_PROXY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice, "check_ollama_daemon", return_value=(False, False)),
        patch.object(
            voice, "get_provider_base_url", return_value="https://api.openai.com/v1"
        ),
        patch.object(voice, "active_engines", return_value=[]),
        patch.object(voice, "audio_models", return_value=[]),
    ):
        config = voice.get_voice_config(
            "anthropic", {"stt_transport": flag}, state={"sttTransport": "openai"}
        )
    assert config["_voice_state"]["sttTransport"] == "openai"
    assert config["_voice_state"]["sttTransportReason"] == "saved selection"


def test_voice_unset_gate_without_saved_transport_uses_local_default(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    monkeypatch.delenv("DOTFILES_USE_LITELLM_PROXY", raising=False)
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice, "check_ollama_daemon", return_value=(False, False)),
        patch.object(voice, "active_engines", return_value=[]),
        patch.object(voice, "audio_models", return_value=[]),
    ):
        config = voice.get_voice_config("anthropic", {"stt_transport": None}, state={})
    assert config["_voice_state"]["sttTransport"] == "local"
    assert (
        config["_voice_state"]["sttTransportReason"]
        == "no binding input; local default"
    )


def _voice_probe_fixture(monkeypatch, voice, status="ok"):
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OMLX", "true")
    monkeypatch.setenv("OMLX_API_KEY", "voice-probe-secret")
    monkeypatch.setattr(voice, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(voice, "resolve_engine", lambda *_: {})
    monkeypatch.setattr(
        voice,
        "audio_models",
        lambda *_: [{"name": "voice-asr", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(
        voice,
        "local_endpoint_for",
        lambda *_: ("http://engine.local/v1", "OMLX_API_KEY"),
    )
    monkeypatch.setattr(voice, "list_local_ollama_models", lambda: [])
    results = []

    def probe(endpoint, model, key_reference, key_value=None, **kwargs):
        results.append((endpoint, model, key_reference, key_value))
        return type("Result", (), {"status": status, "notes": (status,)})()

    import route_probes

    monkeypatch.setattr(route_probes, "probe_stt", probe)
    with (
        patch.object(
            voice.tier_registry, "load_registry", return_value={"presets": {}}
        ),
        patch.object(
            voice.tier_registry, "uses_local_placeholders", return_value=False
        ),
        patch.object(voice, "check_ollama_daemon", return_value=(False, False)),
    ):
        return results, probe


def test_voice_probe_opt_in_qualification_and_secret_safety(monkeypatch, caplog):
    voice = load_script("configure-opencode-voice.py")
    calls, _probe = _voice_probe_fixture(monkeypatch, voice)
    off = voice.get_voice_config("anthropic", {"stt_transport": "local"})
    assert calls == []
    assert "sttEndpoint" not in off

    calls, _probe = _voice_probe_fixture(monkeypatch, voice)
    on = voice.get_voice_config(
        "anthropic", {"stt_transport": "local", "probe_routes": True}
    )
    assert calls == [
        ("http://engine.local/v1", "voice-asr", "OMLX_API_KEY", "voice-probe-secret")
    ]
    stt = on["_voice_state"]["selections"]["stt"]
    assert stt["qualification"] == Qualification.PROTOCOL_PROBED.value
    assert stt["reason"] == "ok"
    assert "voice-probe-secret" not in repr(on)
    assert "voice-probe-secret" not in caplog.text


def test_voice_gateway_probe_uses_gateway_route_and_credential_scope(
    monkeypatch, tmp_path
):
    voice = load_script("configure-opencode-voice.py")
    # Isolate HOME so the host's real opencode.key cannot conflict with the
    # env-only credential this probe test asserts.
    monkeypatch.setenv("HOME", str(tmp_path))
    calls, _probe = _voice_probe_fixture(monkeypatch, voice)
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "gateway-probe-secret")
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: 4000)
    config = voice.get_voice_config(
        "anthropic", {"stt_transport": "local", "probe_routes": True}
    )
    assert calls == [
        (
            "http://127.0.0.1:4000/v1",
            "voice-asr",
            "LITELLM_OPENCODE_KEY",
            "gateway-probe-secret",
        )
    ]
    assert config["sttEndpoint"] == "http://127.0.0.1:4000/v1"
    assert config["sttApiKeyEnv"] == "LITELLM_OPENCODE_KEY"
    assert "gateway-probe-secret" not in repr(config)


def test_voice_probe_connection_failure_preserves_without_claim(monkeypatch):
    voice = load_script("configure-opencode-voice.py")
    _voice_probe_fixture(monkeypatch, voice, status="unknown")
    config = voice.get_voice_config(
        "anthropic",
        {"stt_transport": "local", "probe_routes": True},
        state={
            "selections": {
                "stt": {
                    "provider": "omlx",
                    "model": "voice-asr",
                    "qualification": "discovered",
                }
            }
        },
    )
    assert "sttEndpoint" not in config
    assert config["_voice_state"] is None or "protocol_probed" not in repr(config)


def test_voice_and_webui_select_same_fixture_inventory():
    inventory = [
        ("omlx", "whisper-a", Qualification.PROTOCOL_PROBED),
        ("ollama", "whisper-b", Qualification.PROTOCOL_PROBED),
    ]
    voice = load_script("configure-opencode-voice.py")
    voice_selection = voice._select_voice_candidate(Operation.STT, inventory)
    webui_selection = selected_service.select(
        Operation.STT,
        inventory,
    )
    assert (voice_selection.provider, voice_selection.model) == (
        webui_selection.provider,
        webui_selection.model,
    )


def test_caddy_omlx_route_is_gated(monkeypatch):
    caddy = load_script("configure-caddy.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    route = caddy.build_route_block("/tmp/portal")
    assert "/omlx/*" in route
    assert "@omlx_blocked" in route
    assert f"127.0.0.1:{OMLX_DEFAULT_PORT}" in route
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    assert "/omlx/*" not in caddy.build_route_block("/tmp/portal")


def test_caddy_auth_matcher_is_defined_in_each_site_block(monkeypatch):
    caddy = load_script("configure-caddy.py")
    monkeypatch.setenv("OMLX_API_KEY", "secret")
    auth = caddy.build_auth_block([("opencode", "hashed-password")]).replace(
        "basic_auth {", "basic_auth @not_omlx {", 1
    )
    blocks = [
        caddy.build_site_block(
            "https://example.com", "0.0.0.0", "tls internal", auth, ""
        ),
        caddy.build_opencode_site_block(
            "https://opencode.example.com", "0.0.0.0", "tls internal", auth, "4096"
        ),
    ]

    assert len(blocks) == 2
    for block in blocks:
        lines = block.splitlines()
        if "opencode.example.com" in block:
            assert "  basic_auth {" in lines
            continue
        auth_index = next(
            index
            for index, line in enumerate(lines)
            if "basic_auth @not_omlx {" in line
        )
        assert "  @not_omlx not path /omlx/*" in lines[:auth_index]


def test_codex_local_profile_follows_pool_winner_engine(monkeypatch):
    codex = load_script("configure-codex.py")
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: p == "omlx")
    enabled = codex.build_provider_config("omlx/chat") + codex.build_profiles_config(
        "cloud", "omlx/chat"
    )
    assert "model_providers.omlx" in enabled
    assert "profiles.local" in enabled
    assert 'model = "chat"' in enabled
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: False)
    ollama = codex.build_provider_config("ollama/chat") + codex.build_profiles_config(
        "cloud", "ollama/chat"
    )
    assert "model_providers.omlx" not in ollama
    assert 'model_provider = "ollama"' in ollama


def test_acp_local_agent_follows_pool_winner_engine(monkeypatch):
    acp = load_script("configure-acp-agents.py")
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: True)
    agents = acp.build_local_agents("omlx/chat")
    assert agents["claude--local"]["env"]["ANTHROPIC_BASE_URL"].endswith("/v1")
    assert agents["codex--local"]["args"][:2] == ["--profile", "local"]
    assert not any(name.endswith("--omlx") for name in agents)
    # Ollama cannot serve Anthropic (/v1/messages): claude--local omits
    # itself instead of pointing at a broken endpoint; the rest survive.
    ollama_agents = acp.build_local_agents("ollama-model")
    assert "claude--local" not in ollama_agents
    assert "codex--local" in ollama_agents
    assert "pi--local" in ollama_agents


def test_acp_main_emits_pool_driven_local_agents(tmp_path, monkeypatch):
    acp = load_script("configure-acp-agents.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.delenv("OMLX_API_KEY", raising=False)
    output = tmp_path / "acp-agents.json"
    output.write_text(json.dumps({"acpAgents": {"claude--omlx": {}}}))
    with (
        patch.object(acp, "local_model", return_value="omlx/omlx-chat"),
        patch.object(acp, "active_pi_tier", return_value="pro"),
        patch.object(acp.shutil, "which", return_value="/usr/bin/fake"),
        patch.object(acp, "write_local_junie_config"),
        patch.object(acp, "write_local_codex_config"),
        patch.object(
            sys,
            "argv",
            [
                "configure-acp-agents.py",
                "--output",
                str(output),
                "--agents",
                "claude--local,codex--local",
            ],
        ),
    ):
        acp.main()
    agents = json.loads(output.read_text())["acpAgents"]
    assert "claude--local" in agents
    assert "codex--local" in agents
    assert not any(name.endswith("--omlx") for name in agents)


def test_voice_pro_gateway_admission_and_refusal(monkeypatch, tmp_path):
    """R8 P0.2/P0.3: pro tier installs no direct route in gateway mode."""
    voice = load_script("configure-opencode-voice.py")
    # Isolate HOME: admission is env-only, and the refusal phase requires
    # that no client key file can satisfy the gate once the env key is gone.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(voice, "is_meridian_configured", lambda: False)
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: 4000)
    monkeypatch.setattr(
        voice, "_gateway_alias", lambda provider, model, _auth: f"{provider}/{model}"
    )
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "scoped-key")
    config = voice.get_voice_config("pro", state={})
    assert config["model"] == "ollama/gemma4:31b:cloud"
    assert config["apiKeyEnv"] == "LITELLM_OPENCODE_KEY"
    assert config["endpoint"] == "http://127.0.0.1:4000/v1"
    assert "ollama.com" not in config["endpoint"]
    # Missing scoped key → unresolved; nothing direct rendered.
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    config = voice.get_voice_config("pro", state={})
    assert "model" not in config
    assert "endpoint" not in config


def test_voice_local_gateway_admission_and_refusal(monkeypatch, tmp_path):
    """R8 P0.2: installed local models admit via the gateway or stay unresolved."""
    voice = load_script("configure-opencode-voice.py")
    # Isolate HOME: admission is env-only, and the refusal phase requires
    # that no client key file can satisfy the gate once the env key is gone.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        voice.tier_registry, "uses_local_placeholders", lambda *_: False
    )
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "1")
    monkeypatch.setattr(
        voice,
        "_resolve_local_voice_model",
        lambda *args, **kwargs: ("omlx/qwen-a", False, False),
    )
    monkeypatch.setattr(voice, "check_ollama_daemon", lambda: (False, False))
    monkeypatch.setattr(voice, "_configured_litellm_port", lambda: 4000)
    monkeypatch.setattr(
        voice, "_gateway_alias", lambda provider, model, _auth: f"{provider}/{model}"
    )
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "scoped-key")
    config = voice.get_voice_config("local", state={})
    assert config["model"] == "omlx/qwen-a"
    assert config["apiKeyEnv"] == "LITELLM_OPENCODE_KEY"
    assert config["endpoint"] == "http://127.0.0.1:4000/v1"
    # Missing scoped key → unresolved; NO direct local endpoint rendered.
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    config = voice.get_voice_config("local", state={})
    assert "model" not in config
    assert "endpoint" not in config
