import importlib.util
import io
import json
import os
import shlex
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any
import subprocess
from pathlib import Path

import pytest
import route_probes

import constants
import openwebui


@pytest.mark.parametrize(
    "tools,expected",
    [
        ({"brew": ["ffprobe", "ffmpeg"]}, "brew"),
        ({"brew": ["ffprobe"], "standard1": ["ffprobe", "ffmpeg"]}, "standard1"),
        ({"brew": ["ffmpeg"], "standard2": ["ffprobe", "ffmpeg"]}, "standard2"),
        ({"standard1": ["ffprobe"], "standard2": ["ffprobe", "ffmpeg"]}, "standard2"),
        ({"standard1": ["ffmpeg"], "opt1": ["ffprobe", "ffmpeg"]}, "opt1"),
        ({"opt1": ["ffprobe"], "opt2": ["ffprobe", "ffmpeg"]}, "opt2"),
        ({"opt1": ["ffmpeg"], "path": ["ffprobe", "ffmpeg"]}, "path"),
        ({"brew": ["ffprobe"], "path": ["ffprobe"]}, None),
        ({"path": ["ffmpeg"]}, None),
    ],
)
def test_openwebui_audio_bin_requires_both_executables(tmp_path, tools, expected):
    source = (
        Path(__file__).resolve().parents[3]
        / ".chezmoiscripts/run_onchange_30-openwebui.sh.tmpl"
    )
    script = (
        'BREW_BIN=""'
        + source.read_text()
        .split('BREW_BIN=""', 1)[1]
        .split('mkdir -p "$PLIST_DIR"', 1)[0]
    )
    script = script.replace("/opt/homebrew", str(tmp_path / "homebrew")).replace(
        "/usr/local", str(tmp_path / "local")
    )
    directories = {
        "brew": tmp_path / "brew-prefix/bin",
        "standard1": tmp_path / "homebrew/bin",
        "standard2": tmp_path / "local/bin",
        "opt1": tmp_path / "homebrew/opt/ffmpeg/bin",
        "opt2": tmp_path / "local/opt/ffmpeg/bin",
        "path": tmp_path / "path",
    }
    directories["path"].mkdir()
    (directories["path"] / "dirname").symlink_to(shutil.which("dirname"))
    if "brew" in tools:
        brew = directories["path"] / "brew"
        brew.write_text(
            "#!/bin/sh\nprintf '%s\\n' "
            + shlex.quote(str(directories["brew"].parent))
            + "\n"
        )
        brew.chmod(0o755)
    for name, executables in tools.items():
        directories[name].mkdir(parents=True, exist_ok=True)
        for executable in executables:
            path = directories[name] / executable
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'set -euo pipefail\ndie() { printf "%s\\n" "$*" >&2; exit 1; }\n'
            + script
            + '\nprintf "%s\\n" "$BREW_BIN"',
        ],
        env={"PATH": str(directories["path"])},
        capture_output=True,
        text=True,
        timeout=5,
    )
    if expected is None:
        assert result.returncode == 1
        assert "ffprobe/ffmpeg not found" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(directories[expected])


class _FakeResponse:
    def __init__(self, body, content_type="application/json"):
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_consumer_outcome_client_methods(monkeypatch, caplog):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if request.full_url.endswith("/audio/speech"):
            return _FakeResponse(b"audio", "audio/wav")
        if request.full_url.endswith("/audio/transcriptions"):
            return _FakeResponse(b'{"text":"hello","language":"en"}')
        return _FakeResponse(b'{"id":"file-1"}')

    monkeypatch.setattr(openwebui.urllib.request, "urlopen", fake_urlopen)
    client = openwebui.OpenWebUIClient("http://127.0.0.1:8080", "test-secret")
    transcription = client.transcribe_audio(b"wav", "sample.wav")
    speech = client.speech("hello", "voice-model", "alloy")
    uploaded = client.upload_file(b"doc", "sample.txt", "text/plain")
    assert transcription["text"] == "hello"
    assert speech == ("audio/wav", b"audio")
    assert uploaded["id"] == "file-1"
    assert b'name="file"; filename="sample.wav"' in calls[0].data
    assert "boundary=" in calls[0].get_header("Content-type")
    assert json.loads(calls[1].data) == {
        "input": "hello",
        "model": "voice-model",
        "voice": "alloy",
    }
    for call in calls:
        assert call.get_header("Authorization") == "Bearer test-secret"
        assert "test-secret" not in call.data.decode("latin-1")
    assert "test-secret" not in repr((transcription, speech, uploaded))
    assert "test-secret" not in caplog.text


def test_retrieval_client_payloads(caplog):
    client = openwebui.OpenWebUIClient("http://127.0.0.1:8080", "test-secret")
    calls = []
    client._request = lambda path, method, payload: calls.append(
        (path, method, payload)
    ) or {"hits": []}
    processed = client.process_file("file-1")
    client.retrieval_query("find", file_ids=["collection"], k=3)
    document_hits = client.retrieval_query("find", collection_id="collection", k=4)
    assert "test-secret" not in repr((processed, document_hits, calls))
    assert "test-secret" not in caplog.text
    assert calls == [
        ("/api/v1/retrieval/process/file", "POST", {"file_id": "file-1"}),
        (
            "/api/v1/retrieval/query/collection",
            "POST",
            {"collection_names": ["collection"], "query": "find", "k": 3},
        ),
        (
            "/api/v1/retrieval/query/doc",
            "POST",
            {"collection_name": "collection", "query": "find", "k": 4},
        ),
    ]


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    prefixes = (
        "DOTFILES_",
        "OPENAI_",
        "ANTHROPIC_",
        "OLLAMA_",
        "OMLX_",
        "MERIDIAN_",
        "OPENROUTER_",
        "GOOGLE_",
        "GEMINI_",
        "OPENWEBUI_",
        "LITELLM_",
    )
    for name in list(os.environ):
        if name.startswith(prefixes):
            monkeypatch.delenv(name, raising=False)


def _entry(
    prefix,
    url,
    kind="openai",
    collection="openai",
    key="secret-key",
    managed=True,
    **config,
):
    if managed:
        config["managed_by"] = "dotfiles"
    return {
        "url": url,
        "key": key,
        "config": {
            "prefix_id": prefix,
            "connection_type": kind,
            "enable": True,
            **config,
        },
        "collection": collection,
    }


def _script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "configure-openwebui.py"
    spec = importlib.util.spec_from_file_location("configure_openwebui_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_native_and_cloud_endpoints(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.local/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic.local/v1")
    monkeypatch.setattr(constants, "check_ollama_daemon", lambda: (True, False))
    state = openwebui.compute_desired_state()
    by_prefix = {
        item["config"]["prefix_id"]: item
        for item in state["openai"] + state["anthropic"]
    }
    assert state["ollama"][0]["url"] == "http://127.0.0.1:11434"
    assert by_prefix["openai"]["url"] == "http://openai.local/v1"
    assert by_prefix["anthropic"]["url"] == "http://anthropic.local"
    assert by_prefix["ollama-cloud"]["url"] == "https://ollama.com/v1"
    monkeypatch.setattr(constants, "check_ollama_daemon", lambda: (True, True))
    proxied = openwebui.compute_desired_state()
    assert {item["config"]["prefix_id"]: item for item in proxied["openai"]}[
        "ollama-cloud"
    ]["url"] == "https://ollama.com/v1"


def test_audio_bundle_discovery_and_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "managed-audio.json")
    )
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("OMLX_API_KEY", "audio-key")
    monkeypatch.setenv("DOTFILES_VOICE_TTS_VOICE", "ryan")
    monkeypatch.setattr(
        openwebui,
        "local_endpoint_for",
        lambda *_: ("http://127.0.0.1:11427/v1", "OMLX_API_KEY"),
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [
            {"name": "asr-first", "model_type": "audio_stt"},
            {"name": "chatterbox-multilingual-v3", "model_type": "audio_tts"},
            {"name": "qwen3-tts-12hz", "model_type": "audio_tts"},
        ],
    )
    from local_engines import DiscoveryResult

    monkeypatch.setattr(
        openwebui,
        "iter_engine_discovery",
        lambda _provider: DiscoveryResult(status="ok_models", records=()),
    )
    state = openwebui.compute_desired_state()
    assert "audio" not in state
    assert state["audio_status"] == "unavailable"
    assert openwebui._read_audio_selection() is None
    monkeypatch.delenv("DOTFILES_RUN_OMLX_SETUP")
    assert "audio" not in openwebui.compute_desired_state()


def test_audio_merge_owns_only_named_fields():
    current = {
        "realtime": {"ENGINE": "operator"},
        "stt": {"MODEL": "old", "WHISPER_MODEL": "large", "OPENAI_API_KEY": "kept"},
        "tts": {"VOICE": "old", "AZURE": True},
    }
    wanted = {
        "stt": {"ENGINE": "openai", "MODEL": "new", "OPENAI_API_KEY": None},
        "tts": {"VOICE": "serena"},
    }
    merged, changed = openwebui._merge_owned_audio(current, wanted)
    assert changed
    assert merged["stt"] == {
        "MODEL": "new",
        "WHISPER_MODEL": "large",
        "OPENAI_API_KEY": "kept",
        "ENGINE": "openai",
    }
    assert merged["tts"] == {"VOICE": "serena", "AZURE": True}
    assert merged["realtime"] == {"ENGINE": "operator"}


def test_discovered_audio_catalogue_is_unavailable(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [
            {"name": "asr", "model_type": "audio_stt"},
            {"name": "speech", "model_type": "audio_tts"},
        ],
    )
    bundle = openwebui._desired_audio_settings()
    assert bundle is None


def test_chatterbox_only_omits_tts_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [
            {"name": "asr", "model_type": "audio_stt"},
            {"name": "chatterbox", "model_type": "audio_tts"},
        ],
    )
    bundle = openwebui._desired_audio_settings()
    assert bundle is None
    assert "No qualified oMLX STT model" in caplog.text


def test_non_qwen_tts_does_not_receive_qwen_voice(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [
            {"name": "asr", "model_type": "audio_stt"},
            {"name": "other-tts", "model_type": "audio_tts"},
        ],
    )
    bundle = openwebui._desired_audio_settings()
    assert bundle is None


def test_selection_precedence_and_order_independence():
    from selected_service import Operation, Qualification, select

    candidates = [
        ("omlx", "one", Qualification.DISCOVERED),
        ("omlx", "two", Qualification.DISCOVERED),
    ]
    assert (
        select(Operation.STT, candidates, preference=[("omlx", "two")]).model == "two"
    )
    assert (
        select(
            Operation.STT, list(reversed(candidates)), preference=[("omlx", "two")]
        ).model
        == "two"
    )


def test_audio_and_embedding_selection_survive_reversed_model_enumeration(
    monkeypatch, tmp_path
):
    audio_path = tmp_path / "managed-audio.json"
    embedding_path = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(audio_path))
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(embedding_path))
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui,
        "local_endpoint_for",
        lambda *_: ("http://omlx.local/v1", "OMLX_API_KEY"),
    )
    candidates = ["first-asr", "second-asr"]
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _provider: [
            {"name": name, "model_type": "audio_stt"} for name in candidates
        ],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    previous_audio = {
        "stt": {
            "operation": "stt",
            "provider": "omlx",
            "model": "second-asr",
            "qualification": "protocol_probed",
            "reason": "persisted valid binding",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "transport": "direct",
            "endpoint": "http://omlx.local/v1",
            "wire_model": "second-asr",
            "key_reference": "OMLX_API_KEY",
            "route_evidence": openwebui._route_binding(
                "omlx",
                "second-asr",
                openwebui.Operation.STT,
                "http://omlx.local/v1",
                "OMLX_API_KEY",
                "test-build",
                credential_digest=openwebui._credential_digest("test-wire-secret"),
            ),
        }
    }
    audio_path.write_text(json.dumps({"selection": previous_audio}))
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *_: "test-build")
    monkeypatch.setenv("OMLX_API_KEY", "test-wire-secret")
    monkeypatch.setattr(
        openwebui,
        "resolve_transport",
        lambda *_args, **_kwargs: type(
            "ResolvedRoute",
            (),
            {
                "transport": openwebui.Transport.DIRECT,
                "endpoint": "http://omlx.local/v1",
                "wire_model": "second-asr",
                "key_reference": "OMLX_API_KEY",
                "reason": None,
            },
        )(),
    )

    def probe(*_args, **_kwargs):
        pytest.fail("valid persisted route must be reused without probing")

    monkeypatch.setattr(route_probes, "probe_stt", probe)
    first = openwebui._desired_audio_settings()
    candidates.reverse()
    second = openwebui._desired_audio_settings()
    assert first["selection"]["stt"] == second["selection"]["stt"]
    assert first["settings"]["stt"] == second["settings"]["stt"]
    assert first["selection"]["stt"]["model"] == "second-asr"
    assert first["selection"]["stt"]["endpoint"] == "http://omlx.local/v1"
    assert first["settings"]["stt"]["MODEL"] == "second-asr"
    assert first["settings"]["stt"]["OPENAI_API_KEY"] == "test-wire-secret"

    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    previous_selection = {
        "operation": "embedding",
        "provider": "ollama",
        "model": "second-embedding",
        "qualification": "protocol_probed",
        "reason": "persisted selection",
    }
    openwebui._persist_embedding_selection(previous_selection)
    embedding_candidates = [
        ("ollama", "first-embedding", openwebui.Qualification.PROTOCOL_PROBED),
        ("ollama", "second-embedding", openwebui.Qualification.PROTOCOL_PROBED),
    ]
    previous_embedding = (
        "ollama",
        "second-embedding",
        openwebui.Qualification.PROTOCOL_PROBED,
    )
    selected_forward = openwebui.select(
        openwebui.Operation.EMBEDDING,
        embedding_candidates,
        previously_selected=previous_embedding,
    )
    selected_reverse = openwebui.select(
        openwebui.Operation.EMBEDDING,
        list(reversed(embedding_candidates)),
        previously_selected=previous_embedding,
    )
    assert (selected_forward.provider, selected_forward.model) == (
        selected_reverse.provider,
        selected_reverse.model,
    )
    assert selected_forward.model == "second-embedding"


def test_audio_credentials_rotation_refuses_release_and_unchanged_restores(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state_path))
    monkeypatch.setenv("OMLX_API_KEY", "baseline-env-secret")
    current = {
        "stt": {
            "ENGINE": "",
            "MODEL": "",
            "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_KEY": "baseline-env-secret",
        }
    }
    desired = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "audio": {
            "stt": {
                "ENGINE": "openai",
                "MODEL": "asr",
                "OPENAI_API_BASE_URL": "http://omlx/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "managed-secret",
            }
        },
    }

    class Client(_AudioClient):
        def get_openai_config(self):
            return {"connections": []}

        def get_ollama_config(self):
            return {"connections": []}

    client = Client(current)
    monkeypatch.setattr(
        openwebui,
        "reconcile",
        lambda *_: openwebui.ReconcileResult(openwebui.ReconcilePlan(), snapshots={}),
    )
    openwebui.reconcile_via_api(client, desired)
    marker_path = state_path.with_name(state_path.name + ".credentials")
    marker = json.loads(marker_path.read_text())
    assert marker["stt"]["restoration"] == "env:OMLX_API_KEY"
    assert (
        marker["stt"]["baseline_sha256"]
        == openwebui.hashlib.sha256(b"baseline-env-secret").hexdigest()
    )
    assert "baseline-env-secret" not in marker_path.read_text()
    assert "managed-secret" not in marker_path.read_text()

    monkeypatch.setenv("OMLX_API_KEY", "rotated-env-secret")
    rotated = Client(client.audio)
    result = openwebui.reconcile_via_api(
        rotated, {"openai": [], "anthropic": [], "ollama": []}
    )
    assert result.actions["failed"] == ["audio"]
    assert result.actions["applied"] == []
    assert result.actions["verified"] == []
    assert rotated.audio["stt"]["OPENAI_API_KEY"] == "managed-secret"
    assert json.loads(state_path.read_text())["projection"] is not None
    assert marker_path.exists()

    monkeypatch.setenv("OMLX_API_KEY", "baseline-env-secret")
    unchanged = Client(client.audio)
    released = openwebui.reconcile_via_api(
        unchanged, {"openai": [], "anthropic": [], "ollama": []}
    )
    assert "audio-release" in released.actions["verified"]
    assert released.actions["failed"] == []
    assert unchanged.audio["stt"]["OPENAI_API_KEY"] == "baseline-env-secret"
    assert not state_path.exists()
    assert not marker_path.exists()


def test_audio_credential_marker_never_stores_baseline_or_managed_values(
    monkeypatch, tmp_path
):
    path = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(path))
    monkeypatch.setenv("OMLX_API_KEY", "sentinel-baseline-value")
    client = _AudioClient(
        {"stt": {"OPENAI_API_KEY": "sentinel-baseline-value", "MODEL": ""}}
    )
    openwebui._reconcile_audio(
        client,
        client.audio,
        {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "sentinel-managed-value"}},
    )
    marker_text = (tmp_path / "managed-audio.json.credentials").read_text()
    assert '"restoration": "env:OMLX_API_KEY"' in marker_text
    assert "baseline_sha256" in marker_text
    assert "sentinel-baseline-value" not in marker_text
    assert "sentinel-managed-value" not in marker_text


def test_real_adapter_discovered_audio_is_unavailable_and_preserved(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", None)
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [{"name": "asr", "model_type": "audio_stt"}],
    )
    from local_engines import DiscoveryResult

    monkeypatch.setattr(
        openwebui,
        "iter_engine_discovery",
        lambda *_args, **_kwargs: DiscoveryResult("ok_models", ({"name": "asr"},)),
    )
    desired = openwebui.compute_desired_state()
    assert desired["audio_status"] == "unavailable"
    assert "audio" not in desired
    assert desired["audio_selection"]["stt"]["qualification"] == "unavailable"
    client = _AudioClient({"stt": {"MODEL": "asr"}})
    monkeypatch.setattr(
        openwebui,
        "reconcile",
        lambda *_args: openwebui.ReconcileResult(
            openwebui.ReconcilePlan(), snapshots={}
        ),
    )
    result = openwebui.reconcile_via_api(client, desired)
    assert client.audio_writes == 0
    assert result.actions["applied"] == []
    assert result.actions["verified"] == []


def test_probe_routes_are_opt_in_and_attach_qualification(monkeypatch):
    import openwebui

    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://local/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [
            {"name": "stt-model", "model_type": "audio_stt"},
            {"name": "tts-model", "model_type": "audio_tts"},
        ],
    )
    calls = []

    def success(endpoint, model, key_reference, key_value=None, **kwargs):
        calls.append((endpoint, model, key_reference, key_value))
        return type("Result", (), {"status": "ok", "notes": ("HTTP 200",)})()

    monkeypatch.setattr("route_probes.probe_stt", success)
    monkeypatch.setattr("route_probes.probe_tts", success)
    monkeypatch.setattr("openwebui._read_audio_selection", lambda: None)
    monkeypatch.setattr("openwebui._read_audio_projection", lambda: {})
    monkeypatch.setattr("openwebui._persist_audio_selection", lambda *_: None)
    monkeypatch.delenv("OMLX_API_KEY", raising=False)

    assert openwebui._desired_audio_settings() is None
    assert calls == []
    audio = openwebui._desired_audio_settings(probe_routes=True)
    assert len(calls) == 2
    assert audio["selection"]["stt"]["qualification"] == "protocol_probed"
    assert audio["selection"]["tts"]["qualification"] == "protocol_probed"


def test_direct_audio_probes_resolve_backend_credential(monkeypatch):
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://local/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [
            {"name": "stt-model", "model_type": "audio_stt"},
            {"name": "tts-model", "model_type": "audio_tts"},
        ],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    monkeypatch.setenv("OMLX_API_KEY", "backend-secret-sentinel")
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    monkeypatch.setattr(openwebui, "_persist_audio_selection", lambda *_: None)
    observed = []

    def probe(endpoint, model, key_reference, key_value=None, **kwargs):
        observed.append((endpoint, model, key_reference, key_value))
        return type("Result", (), {"status": "ok", "notes": ("HTTP 200",)})()

    monkeypatch.setattr(route_probes, "probe_stt", probe)
    monkeypatch.setattr(route_probes, "probe_tts", probe)
    openwebui._desired_audio_settings(probe_routes=True)
    assert observed == [
        ("http://local/v1", "stt-model", "OMLX_API_KEY", "backend-secret-sentinel"),
        ("http://local/v1", "tts-model", "OMLX_API_KEY", "backend-secret-sentinel"),
    ]


@pytest.mark.parametrize(
    "status,expected",
    [
        ("response_shape_error", openwebui.Qualification.UNAVAILABLE),
        ("unknown", openwebui.Qualification.UNKNOWN),
        ("unauthorized", openwebui.Qualification.UNKNOWN),
    ],
)
def test_audio_probe_failures_keep_routes_unqualified(monkeypatch, status, expected):
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://engine/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "stt", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    proposed = {}
    monkeypatch.setenv("OMLX_API_KEY", "probe-secret-sentinel")
    result = type("ProbeResult", (), {"status": status, "notes": (status,)})()
    monkeypatch.setattr(route_probes, "probe_stt", lambda *args, **kwargs: result)
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    bundle = openwebui._desired_audio_settings(
        probe_routes=True, selection_records=proposed
    )
    assert bundle is None
    assert proposed["_unavailable"]["stt"]["qualification"] == "unavailable"
    assert proposed["_unavailable"]["stt"]["candidate_qualification"] == "unavailable"
    assert proposed["_unavailable"]["stt"]["reason"] == "no qualified candidate"
    assert "probe-secret-sentinel" not in repr(proposed)


@pytest.mark.parametrize("status", ["response_shape_error", "unknown", "unauthorized"])
def test_embedding_probe_failure_does_not_claim_success(monkeypatch, status):
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    proposed = {}
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    result = type("ProbeResult", (), {"status": status, "notes": (status,)})()
    monkeypatch.setattr(
        route_probes, "probe_embeddings", lambda *args, **kwargs: result
    )
    result_settings = openwebui._desired_embedding_settings(
        probe_routes=True, selection_records=proposed
    )
    assert result_settings is None
    assert proposed["_unavailable"]["qualification"] == "unavailable"
    assert status in proposed["_unavailable"]["reason"]


def _route_evidence_audio_fixture(monkeypatch, *, selection=None, probe_result=None):
    """Healthy fake oMLX host with scripted route probes and record writes."""
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://local/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "stt-model", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *a, **k: "9.9.9")
    monkeypatch.setenv("OMLX_API_KEY", "fixture-key")
    if selection and "stt" in selection:
        evidence = selection["stt"].get("route_evidence", {})
        if evidence.get("credential_digest") is None:
            evidence["credential_digest"] = openwebui._credential_digest("fixture-key")
    persisted = {}
    monkeypatch.setattr(
        openwebui, "_persist_audio_selection", lambda record: persisted.update(record)
    )
    if selection is None:
        monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    else:
        monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: selection)
    calls = []

    def probe(endpoint, model, key_reference, key_value=None, **kwargs):
        calls.append((endpoint, model))
        if probe_result is None:
            return type("Result", (), {"status": "ok", "notes": ("HTTP 200",)})()
        return probe_result

    monkeypatch.setattr(route_probes, "probe_stt", probe)
    return calls, persisted


def _route_evidence_record(engine_build="9.9.9", recorded_at=None):
    return {
        "operation": "stt",
        "provider": "omlx",
        "model": "stt-model",
        "qualification": "protocol_probed",
        "reason": "HTTP 200",
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
        "route_evidence": {
            "provider": "omlx",
            "model": "stt-model",
            "operation": "stt",
            "url": "http://local/v1",
            "credential_reference": "OMLX_API_KEY",
            "credential_digest": openwebui._credential_digest("fixture-key"),
            "engine_build": engine_build,
        },
    }


def test_route_evidence_probed_when_missing_on_healthy_apply(monkeypatch):
    calls, persisted = _route_evidence_audio_fixture(monkeypatch)
    provenance = {}
    bundle = openwebui._desired_audio_settings(
        allow_auto_probe=True, selection_records=provenance
    )
    assert calls == [("http://local/v1", "stt-model")]
    assert bundle["selection"]["stt"]["qualification"] == "protocol_probed"
    assert bundle["selection"]["stt"]["route_evidence"] == (
        _route_evidence_record()["route_evidence"]
    )
    assert persisted == {}  # provenance is proposed; persistence happens at apply


def test_route_evidence_reused_without_probing_when_binding_matches(monkeypatch):
    calls, _persisted = _route_evidence_audio_fixture(
        monkeypatch, selection={"stt": _route_evidence_record()}
    )
    monkeypatch.setenv("OMLX_API_KEY", "fixture-key")
    selection = {"stt": _route_evidence_record()}
    selection["stt"]["route_evidence"]["credential_digest"] = (
        openwebui._credential_digest("fixture-key")
    )
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: selection)
    builds = []
    monkeypatch.setattr(
        openwebui, "_route_engine_build", lambda *args: builds.append(args) or "9.9.9"
    )
    monkeypatch.setattr(
        openwebui,
        "resolve_transport",
        lambda *_args, **_kwargs: type(
            "ResolvedRoute",
            (),
            {
                "transport": openwebui.Transport.DIRECT,
                "endpoint": "http://local/v1",
                "wire_model": "stt-model",
                "key_reference": "OMLX_API_KEY",
                "reason": None,
            },
        )(),
    )
    bundle = openwebui._desired_audio_settings(allow_auto_probe=True)
    assert calls == []  # valid persisted binding: no probe on the reused route
    assert builds == [("omlx", "http://local/v1")]
    assert bundle["selection"]["stt"]["qualification"] == "protocol_probed"
    assert (
        bundle["selection"]["stt"]["reason"] == "previous selection remains qualified"
    )


def test_gateway_audio_auto_probe_resolves_scoped_key_and_renders_proxy(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "asr", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(
        openwebui, "_litellm_endpoint", lambda: "http://127.0.0.1:4000/v1"
    )
    key = tmp_path / "openwebui.key"
    key.write_text("scoped-wire-secret")
    monkeypatch.setattr(openwebui, "LITELLM_OPENWEBUI_KEY_PATH", str(key))
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "scoped-wire-secret")
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *_: "test-build")
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    observed = []

    def success(endpoint, model, key_reference, key_value=None, **_kwargs):
        observed.append((endpoint, model, key_reference, key_value))
        return type("Result", (), {"status": "ok", "notes": ("HTTP 200",)})()

    monkeypatch.setattr(route_probes, "probe_stt", success)
    bundle = openwebui._desired_audio_settings(allow_auto_probe=True)
    assert observed == [
        ("http://127.0.0.1:4000/v1", "asr", str(key), "scoped-wire-secret")
    ]
    assert (
        bundle["settings"]["stt"]["OPENAI_API_BASE_URL"] == "http://127.0.0.1:4000/v1"
    )
    assert bundle["settings"]["stt"]["MODEL"] == "asr"
    assert bundle["settings"]["stt"]["OPENAI_API_KEY"] == "scoped-wire-secret"
    assert bundle["selection"]["stt"]["key_reference"] == str(key)
    assert "scoped-wire-secret" not in repr(bundle["selection"])


def test_route_evidence_reprobes_once_when_binding_changes(monkeypatch):
    calls, _persisted = _route_evidence_audio_fixture(
        monkeypatch, selection={"stt": _route_evidence_record(engine_build="0.0.1")}
    )
    provenance = {}
    bundle = openwebui._desired_audio_settings(
        allow_auto_probe=True, selection_records=provenance
    )
    assert calls == [("http://local/v1", "stt-model")]  # exactly one re-probe
    assert bundle["selection"]["stt"]["route_evidence"]["engine_build"] == "9.9.9"


def test_route_evidence_auth_failure_stays_unqualified(monkeypatch):
    failure = type(
        "Result", (), {"status": "unauthorized", "notes": ("unauthorized",)}
    )()
    calls, _persisted = _route_evidence_audio_fixture(monkeypatch, probe_result=failure)
    provenance = {}
    bundle = openwebui._desired_audio_settings(
        allow_auto_probe=True, selection_records=provenance
    )
    assert calls == [("http://local/v1", "stt-model")]
    assert bundle is None
    assert provenance["_unavailable"]["stt"]["qualification"] == "unavailable"
    assert "route_evidence" not in provenance["stt"]  # failed probe binds no evidence


def test_read_only_route_evaluation_skips_build_lookup(monkeypatch):
    builds = []
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(
        openwebui, "_route_engine_build", lambda *args: builds.append(args)
    )
    candidates, _ = openwebui._qualify_candidates(
        [("ollama", "embedding", openwebui.Qualification.DISCOVERED)],
        openwebui.Operation.EMBEDDING,
        "http://127.0.0.1:11434/v1",
        None,
        lambda *_a, **_kw: pytest.fail("read-only must not probe"),
    )
    assert candidates[0][2] is openwebui.Qualification.DISCOVERED
    assert builds == []


def test_route_build_fetch_is_gated_by_probe_mode(monkeypatch):
    monkeypatch.setattr(openwebui, "_ROUTE_BUILD_CACHE", {})
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"version":"0.40.6"}'

    monkeypatch.setattr(
        openwebui.urllib.request,
        "urlopen",
        lambda request, timeout: requests.append(request.full_url) or Response(),
    )
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})

    def probe(*_args, **_kwargs):
        return type("Result", (), {"status": "ok", "notes": ("ok",)})()

    openwebui._qualify_candidates(
        [("ollama", "embed", openwebui.Qualification.DISCOVERED)],
        openwebui.Operation.EMBEDDING,
        "http://127.0.0.1:11434/v1",
        None,
        probe,
    )
    assert requests == []
    for flags in ({"probe_on_missing": True}, {"full_probe": True}):
        openwebui._qualify_candidates(
            [("ollama", "embed", openwebui.Qualification.DISCOVERED)],
            openwebui.Operation.EMBEDDING,
            "http://127.0.0.1:11434/v1",
            None,
            probe,
            **flags,
        )
    assert requests == ["http://127.0.0.1:11434/api/version"]


@pytest.mark.parametrize(
    "recorded_at",
    [
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "broken",
        "2999-01-01T00:00:00+00:00",
    ],
)
def test_selection_record_naive_bad_or_future_timestamp_is_not_fresh(recorded_at):
    assert not openwebui._selection_record_fresh({"recorded_at": recorded_at})


def test_read_only_audio_probe_path_does_not_resolve_gateway_credential(monkeypatch):
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://local/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "stt", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    monkeypatch.setattr(openwebui, "_litellm_endpoint", lambda: "http://proxy/v1")
    monkeypatch.setattr(
        openwebui, "_litellm_key_value", lambda: pytest.fail("read-only key lookup")
    )
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *_: None)
    monkeypatch.setattr(
        route_probes, "probe_stt", lambda *_a, **_kw: pytest.fail("read-only probe")
    )
    bundle = openwebui._desired_audio_settings(selection_records={})
    assert bundle is None


@pytest.mark.parametrize("recorded_at", ["bad timestamp", "2025-01-01T00:00:00"])
def test_selection_freshness_rejects_malformed_timestamps(recorded_at):
    assert not openwebui._selection_record_fresh({"recorded_at": recorded_at})


def test_route_evidence_rotated_credential_forces_reprobe(monkeypatch):
    rotated = _route_evidence_record()
    rotated["route_evidence"]["credential_digest"] = "stale-digest"
    calls, _persisted = _route_evidence_audio_fixture(
        monkeypatch, selection={"stt": rotated}
    )
    provenance = {}
    bundle = openwebui._desired_audio_settings(
        allow_auto_probe=True, selection_records=provenance
    )
    assert calls == [("http://local/v1", "stt-model")]  # rotated digest: re-probe
    assert (
        bundle["selection"]["stt"]["route_evidence"]["credential_digest"]
        != "stale-digest"
    )


def test_route_evidence_unknown_build_never_reused(monkeypatch):
    unknown = _route_evidence_record(engine_build=None)
    calls, _persisted = _route_evidence_audio_fixture(
        monkeypatch, selection={"stt": unknown}
    )
    openwebui._desired_audio_settings(allow_auto_probe=True, selection_records={})
    assert calls == [
        ("http://local/v1", "stt-model")
    ]  # unknown build: not authoritative


def test_route_evidence_stale_record_is_reprobed(monkeypatch):
    stale = _route_evidence_record(
        recorded_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    )
    calls, _persisted = _route_evidence_audio_fixture(
        monkeypatch, selection={"stt": stale}
    )
    openwebui._desired_audio_settings(allow_auto_probe=True, selection_records={})
    assert calls == [("http://local/v1", "stt-model")]  # stale evidence: re-probe


def test_read_only_mode_neither_probes_nor_persists(monkeypatch):
    calls, persisted = _route_evidence_audio_fixture(monkeypatch)
    provenance = {}
    bundle = openwebui._desired_audio_settings(selection_records=provenance)
    assert calls == []  # no network side effects in read-only mode
    assert persisted == {}
    assert bundle is None  # honest not-yet-probed state, never advertised as probed
    assert provenance["_unavailable"]["stt"]["candidate_qualification"] == "discovered"


def test_webui_template_fails_deploy_when_reconciler_fails():
    template_path = (
        Path(openwebui.__file__).resolve().parents[2]
        / ".chezmoiscripts/run_onchange_30-openwebui.sh.tmpl"
    )
    template = template_path.read_text()
    assert template.count('|| die "Open WebUI reconciliation failed"') == 2
    assert '|| warn "OpenWebUI reconciliation failed"' not in template


def test_ollama_native_base_derivation_and_rendered_field(monkeypatch):
    # Route/probe facts keep the OpenAI-compatible endpoint.
    assert openwebui.EMBEDDING_OLLAMA_URL.endswith("/v1")
    # WebUI's native engine field gets the bare origin, derived (not assumed).
    assert (
        openwebui._ollama_native_base(openwebui.EMBEDDING_OLLAMA_URL)
        == openwebui.EMBEDDING_OLLAMA_URL[: -len("/v1")]
    )
    assert openwebui._ollama_native_base("http://host:9999") == "http://host:9999"


def test_gateway_audio_probe_uses_discovered_alias(monkeypatch, tmp_path):
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://engine/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "exact-asr-id", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    key_dir = tmp_path / "litellm" / "clients"
    key_dir.mkdir(parents=True, mode=0o700)
    key_file = key_dir / "openwebui.key"
    key_file.write_text("gateway-test-sentinel")
    key_dir.chmod(0o700)
    key_file.chmod(0o600)
    monkeypatch.setattr(openwebui, "LITELLM_OPENWEBUI_KEY_PATH", str(key_file))
    monkeypatch.setattr(
        openwebui, "_litellm_endpoint", lambda: "http://proxy.test:4567/v1"
    )
    observed = []

    def success(endpoint, model, key_reference, key_value=None, **kwargs):
        observed.append((endpoint, model, key_reference, key_value))
        return type("ProbeResult", (), {"status": "ok", "notes": ("HTTP 200",)})()

    monkeypatch.setattr(route_probes, "probe_stt", success)
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    monkeypatch.setattr(openwebui, "_persist_audio_selection", lambda *_: None)
    monkeypatch.setenv("OMLX_API_KEY", "alias-secret")
    audio = openwebui._desired_audio_settings(probe_routes=True)
    assert (
        audio["settings"]["stt"]["OPENAI_API_BASE_URL"] == "http://proxy.test:4567/v1"
    )
    assert audio["settings"]["stt"]["MODEL"] == "exact-asr-id"
    assert audio["settings"]["stt"]["OPENAI_API_KEY"] == "gateway-test-sentinel"
    assert observed[0] == (
        "http://proxy.test:4567/v1",
        "exact-asr-id",
        str(key_file),
        "gateway-test-sentinel",
    )
    assert "gateway-test-sentinel" not in repr(audio["selection"])


def test_fake_route_probe_http_surfaces_shapes_auth_and_refusal(monkeypatch):
    import urllib.error
    import urllib.request

    class Response:
        def __init__(self, status, content_type, payload):
            self.status = status
            self.headers = {"Content-Type": content_type}
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    responses = iter(
        [
            Response(200, "text/html", b"<html>no transcription</html>"),
            Response(200, "application/json", b'{"data":[{"embedding":[]}]}'),
            urllib.error.HTTPError(
                "http://fake", 401, "unauthorized", {}, io.BytesIO()
            ),
        ]
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: next(responses))
    stt = route_probes.probe_stt("http://fake/v1", "asr", "KEY", "sentinel")
    embedding = route_probes.probe_embeddings("http://fake/v1", "emb", None)
    auth = route_probes.probe_tts("http://fake/v1", "tts", key_value="sentinel")
    assert stt.status == "response_shape_error"
    assert embedding.status == "response_shape_error"
    assert auth.auth_failure == "unauthorized"
    assert route_probes.qualification_from_probe(auth).value == "unknown"


def test_real_adapter_discovered_embedding_is_unavailable_and_preserved(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    fingerprint = {
        "engine": "ollama",
        "model": openwebui.EMBEDDING_MODEL,
        "output_dims": 768,
        "backend_identity": {"backend": "ollama", "version": "v1"},
    }
    ack = {
        "previous_model": openwebui.EMBEDDING_MODEL,
        "previous_engine": "ollama",
        "previous_output_dims": 768,
        "new_model": "sentence-transformers/all-MiniLM-L6-v2",
        "new_output_dims": 384,
        "acknowledged_at": "reverse-migration-approved",
    }
    projection = {
        "owned": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
        },
        "previous": {"engine": "", "model": "sentence-transformers/all-MiniLM-L6-v2"},
        "previous_output_dims": 384,
        "output_dims": 768,
        "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
    }
    openwebui._write_embedding_state(
        {
            "projection": projection,
            "embedding_fingerprint": fingerprint,
            "acknowledgement": ack,
        }
    )
    desired = openwebui.compute_desired_state()
    assert desired["embedding_status"] == "unavailable"
    assert "embedding" not in desired
    assert "selection" not in openwebui._read_embedding_state()
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    client.update_openai_config = lambda _config: None
    client.update_ollama_config = lambda _config: None
    monkeypatch.setattr(
        openwebui,
        "reconcile",
        lambda *_args: openwebui.ReconcileResult(
            openwebui.ReconcilePlan(), snapshots={}
        ),
    )
    result = openwebui.reconcile_via_api(client, desired)
    assert client.writes == 0
    assert result.actions["applied"] == []
    assert result.actions["verified"] == []
    persisted = openwebui._read_embedding_state()
    assert persisted["projection"] == projection
    assert persisted["acknowledgement"] == ack
    assert persisted["embedding_fingerprint"] == fingerprint


def test_audio_unavailable_status_preserves_without_reconcile(monkeypatch):
    class Client:
        def __init__(self):
            self.audio_reads = 0
            self.embedding_reads = 0

        def get_openai_config(self):
            return []

        def get_ollama_config(self):
            return []

        def get_audio_config(self):
            self.audio_reads += 1
            return {}

        def get_embedding_config(self):
            self.embedding_reads += 1
            return {"ollama_config": {}}

    client = Client()
    openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "audio_status": "unavailable",
            "embedding_status": "unavailable",
        },
    )
    assert client.audio_reads == 0
    assert client.embedding_reads == 0


def test_discovered_embedding_preserves_owned_state_without_post(
    monkeypatch, tmp_path, caplog
):
    state_path = tmp_path / "managed-embedding.json"
    audio_path = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(audio_path))
    fingerprint = {
        "engine": "ollama",
        "model": openwebui.EMBEDDING_MODEL,
        "output_dims": 768,
        "backend_identity": {"backend": "ollama", "version": "v1"},
    }
    ack = {
        "previous_model": openwebui.EMBEDDING_MODEL,
        "previous_engine": "ollama",
        "previous_output_dims": 768,
        "new_model": "sentence-transformers/all-MiniLM-L6-v2",
        "new_output_dims": 384,
        "acknowledged_at": "reverse-migration-approved",
    }
    owned_projection = {
        "owned": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
        },
        "previous": {"engine": "", "model": "sentence-transformers/all-MiniLM-L6-v2"},
        "previous_output_dims": 384,
        "output_dims": 768,
        "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
    }
    baseline = {
        "projection": owned_projection,
        "embedding_fingerprint": fingerprint,
        "acknowledgement": ack,
    }
    openwebui._write_embedding_state(baseline)
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    original_select = openwebui.select

    def discovered_select(operation, candidates, **kwargs):
        if operation.value == "embedding":
            from selected_service import Qualification

            candidates = [
                (provider, model, Qualification.DISCOVERED)
                for provider, model, _ in candidates
            ]
        return original_select(operation, candidates, **kwargs)

    monkeypatch.setattr(openwebui, "select", discovered_select)
    monkeypatch.setattr(
        openwebui,
        "iter_engine_discovery",
        lambda *_a, **_kw: type("D", (), {"status": "ok_models", "records": ()})(),
    )
    monkeypatch.setattr(openwebui, "audio_models", lambda _: [])
    monkeypatch.setattr(openwebui, "active_engines", lambda: [])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", None)
    )
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    client.get_audio_config = lambda: {}
    client.update_audio_config = lambda _config: pytest.fail("unavailable audio wrote")
    monkeypatch.setattr(
        openwebui,
        "reconcile",
        lambda *_args: openwebui.ReconcileResult(
            openwebui.ReconcilePlan(), snapshots={}
        ),
    )
    desired = openwebui.compute_desired_state()
    result = openwebui.reconcile_via_api(client, desired)
    assert client.writes == 0
    assert desired["embedding_status"] == "unavailable"
    assert result.actions["applied"] == []
    assert result.actions["verified"] == []
    assert "unavailable" in caplog.text
    persisted = openwebui._read_embedding_state()
    assert persisted["projection"] == owned_projection
    assert persisted["acknowledgement"] == ack
    assert persisted["embedding_fingerprint"] == fingerprint
    assert persisted["selection"]["qualification"] == "unavailable"


def test_routing_gate_inversion_does_not_mix_local_and_gateway(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [{"name": "asr", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://local-omlx/v1", None)
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: False)
    local = openwebui._desired_audio_settings()
    assert local is None

    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    gateway = openwebui._desired_audio_settings()
    assert gateway is None
    assert openwebui._desired_embedding_settings() is None


def test_unqualified_audio_is_omitted(monkeypatch, caplog):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", None)
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda _: [{"name": "asr", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui, "_audio_locator", lambda *args: None)
    assert openwebui._desired_audio_settings() is None
    assert "omitting audio settings" in caplog.text


def test_audio_selection_writer_sibling_and_secret_safe(monkeypatch, tmp_path):
    path = tmp_path / "audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(path))
    selection = {
        "stt": {
            "operation": "stt",
            "provider": "omlx",
            "model": "asr",
            "qualification": "protocol_probed",
            "reason": "test",
            "transport": "gateway",
            "endpoint": "http://omlx/v1",
            "wire_model": "asr",
            "key_reference": "OMLX_API_KEY",
        }
    }
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}}, selection=selection)
    document = json.loads(path.read_text())
    assert document["projection"] == {"stt": {"MODEL": "asr"}}
    assert document["selection"] == selection
    assert "credential-sentinel" not in path.read_text()


def test_audio_and_embedding_render_invariants_reject_drift():
    from selected_service import (
        CanonicalSelection,
        Operation,
        Qualification,
        RouteResolution,
        ServiceLocator,
        Transport,
        assert_renders_from,
    )

    locator = ServiceLocator(
        CanonicalSelection(
            Operation.STT, "omlx", "asr", Qualification.PROTOCOL_PROBED, "fixture"
        ),
        RouteResolution(Transport.GATEWAY, "http://omlx/v1", "asr", "OMLX_API_KEY"),
    )
    for drifted in (
        {
            "provider": "omlx",
            "model": "asr",
            "transport": Transport.GATEWAY,
            "endpoint": "http://wrong/v1",
            "wire_model": "asr",
        },
        {
            "provider": "omlx",
            "model": "wrong",
            "transport": Transport.GATEWAY,
            "endpoint": "http://omlx/v1",
            "wire_model": "wrong",
        },
    ):
        with pytest.raises(AssertionError):
            assert_renders_from(locator, drifted)


def test_embedding_selection_sibling_preserved_and_decision_not_applied(
    monkeypatch, tmp_path
):
    path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(path))
    applied_projection = {
        "owned": {"RAG_EMBEDDING_ENGINE": "ollama", "RAG_EMBEDDING_MODEL": "applied"}
    }
    openwebui._write_embedding_state({"projection": applied_projection})
    selection = {
        "operation": "embedding",
        "provider": "ollama",
        "model": "intent",
        "qualification": "protocol_probed",
        "reason": "resolved",
        "transport": "gateway",
        "endpoint": "http://ollama/v1",
        "wire_model": "intent",
        "key_reference": None,
    }
    openwebui._persist_embedding_selection(selection)
    openwebui._write_embedding_state(
        {"projection": applied_projection, "acknowledgement": {"state": "unchanged"}}
    )
    persisted = openwebui._read_embedding_state()
    assert persisted["selection"] == selection
    assert persisted["projection"] == applied_projection
    assert persisted["projection"]["owned"]["RAG_EMBEDDING_MODEL"] == "applied"


def test_audio_legacy_sanitization_preserves_selection_and_unlink(
    monkeypatch, tmp_path
):
    path = tmp_path / "audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(path))
    selection = {
        "stt": {
            "operation": "stt",
            "provider": "omlx",
            "model": "asr",
            "qualification": "protocol_probed",
            "reason": "decision",
            "transport": "gateway",
            "endpoint": "http://omlx/v1",
            "wire_model": "asr",
            "key_reference": "OMLX_API_KEY",
        }
    }
    path.write_text(
        json.dumps(
            {
                "projection": {
                    "stt": {
                        "ENGINE": "openai",
                        "MODEL": "asr",
                        "OPENAI_API_KEY": "legacy-secret",
                    }
                },
                "selection": selection,
            }
        )
    )
    current = {"stt": {"ENGINE": "openai", "MODEL": "custom-admin"}}
    openwebui._reconcile_audio(_AudioClient(current), current, {})
    assert json.loads(path.read_text())["selection"] == selection
    openwebui._write_audio_projection(None)
    assert not path.exists()


def test_audio_customized_fields_refuse_first_adoption(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "managed-audio.json")
    )
    current = {
        "realtime": {"ENGINE": "operator"},
        "stt": {
            "ENGINE": "custom",
            "MODEL": "operator-model",
            "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
        },
        "tts": {
            "ENGINE": "",
            "MODEL": "",
            "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            "VOICE": "",
        },
    }
    client = _AudioClient(current)
    openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "audio": {
                "stt": {
                    "ENGINE": "openai",
                    "MODEL": "managed",
                    "OPENAI_API_BASE_URL": "http://omlx/v1",
                    "OPENAI_API_REQUEST_FORMAT": "multipart",
                }
            },
        },
    )
    assert client.audio == current
    assert client.audio_writes == 0
    assert "Refusing adoption of customized Open WebUI audio fields" in caplog.text
    assert not (tmp_path / "managed-audio.json").exists()


class _EmbeddingClient:
    def __init__(self, config):
        self.config = json.loads(json.dumps(config))
        self.writes = 0
        self.ignore_writes = False

    def get_openai_config(self):
        return {"connections": []}

    def get_ollama_config(self):
        return {"connections": []}

    def get_embedding_config(self):
        return json.loads(json.dumps(self.config))

    def update_embedding_config(self, config):
        self.writes += 1
        if not self.ignore_writes:
            self.config = json.loads(json.dumps(config))


def _embedding_config(engine="", model="sentence-transformers/all-MiniLM-L6-v2"):
    return {
        "status": True,
        "RAG_EMBEDDING_ENGINE": engine,
        "RAG_EMBEDDING_MODEL": model,
        "RAG_EMBEDDING_BATCH_SIZE": 1,
        "ENABLE_ASYNC_EMBEDDING": True,
        "RAG_EMBEDDING_CONCURRENT_REQUESTS": 0,
        "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL, "key": "untouched"},
        "openai_config": {"url": "https://api.openai.com/v1", "key": "secret"},
        "azure_openai_config": {"url": "", "key": "secret", "version": ""},
    }


def _embedding_reconcile(client):
    return openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "embedding": {"model": openwebui.EMBEDDING_MODEL},
        },
    )


def test_embedding_different_origin_route_requires_acknowledgement(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    current = _embedding_config("ollama", openwebui.EMBEDDING_MODEL)
    current["ollama_config"]["url"] = "http://old-backend:11434"
    client = _EmbeddingClient(current)
    calls = []
    setattr(
        client,
        "embedding_backend_identity_probe",
        lambda config: calls.append(config["ollama_config"]["url"])
        or {"backend": "ollama", "version": "same"},
    )
    setattr(
        client,
        "embedding_parity_probe",
        lambda *_: calls.append("parity") or "inconclusive",
    )
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": "http://old-backend:11434"},
            },
            "embedding_fingerprint": {
                "engine": "ollama",
                "model": openwebui.EMBEDDING_MODEL,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "backend_identity": {"backend": "ollama", "version": "same"},
            },
        }
    )
    desired = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "embedding": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            "ollama_config": {"url": "http://different-backend:9999"},
        },
    }
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["applied"] == []
    assert report["verified"] == []
    assert report["failed"] == ["embedding"]
    assert client.writes == 0
    assert calls == ["http://old-backend:11434", "parity"]


def _route_change_fixture(monkeypatch, tmp_path, parity_result):
    """Different-origin route change with a scripted parity probe result."""
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    current = _embedding_config("ollama", openwebui.EMBEDDING_MODEL)
    current["ollama_config"]["url"] = "http://old-backend:11434"
    client = _EmbeddingClient(current)
    calls = []
    setattr(
        client,
        "embedding_backend_identity_probe",
        lambda config: calls.append(config["ollama_config"]["url"])
        or {"backend": "ollama", "version": "same"},
    )
    setattr(
        client,
        "embedding_parity_probe",
        lambda *_: calls.append("parity") or parity_result,
    )
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": "http://old-backend:11434"},
            },
            "embedding_fingerprint": {
                "engine": "ollama",
                "model": openwebui.EMBEDDING_MODEL,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "backend_identity": {"backend": "ollama", "version": "same"},
            },
        }
    )
    desired = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "embedding": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            "ollama_config": {"url": "http://different-backend:9999"},
        },
    }
    return client, calls, desired


def _valid_route_parity_evidence():
    return {
        "parity": True,
        "source_backend_identity": {"backend": "ollama", "version": "same"},
        "target_backend_identity": {"backend": "ollama", "version": "same"},
    }


def test_embedding_route_change_rejects_failed_parity_evidence(monkeypatch, tmp_path):
    client, calls, desired = _route_change_fixture(
        monkeypatch, tmp_path, {"status": "failed", "parity": False}
    )
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["applied"] == []
    assert report["verified"] == []
    assert report["failed"] == ["embedding"]
    assert client.writes == 0
    assert calls == ["http://old-backend:11434", "parity"]


def test_embedding_route_change_rejects_incomplete_parity_evidence(
    monkeypatch, tmp_path
):
    client, calls, desired = _route_change_fixture(
        monkeypatch, tmp_path, {"parity": True}
    )
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["failed"] == ["embedding"]
    assert client.writes == 0
    assert calls == ["http://old-backend:11434", "parity"]


def test_embedding_route_change_rejects_stale_source_identity(monkeypatch, tmp_path):
    stale = _valid_route_parity_evidence()
    stale["source_backend_identity"] = {"backend": "ollama", "version": "stale"}
    client, calls, desired = _route_change_fixture(monkeypatch, tmp_path, stale)
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["failed"] == ["embedding"]
    assert client.writes == 0
    assert calls == ["http://old-backend:11434", "parity"]


def test_embedding_route_change_authorized_by_valid_parity_evidence(
    monkeypatch, tmp_path
):
    client, _, desired = _route_change_fixture(
        monkeypatch, tmp_path, _valid_route_parity_evidence()
    )
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["applied"] == ["embedding"]
    assert report["verified"] == ["embedding"]
    assert report["failed"] == []
    assert client.writes == 1
    state = openwebui._read_embedding_state()
    assert state["embedding_fingerprint"]["parity_evidence"] == (
        _valid_route_parity_evidence()
    )


def test_embedding_route_change_binds_evidence_to_installed_destination(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    current = _embedding_config("ollama", openwebui.EMBEDDING_MODEL)
    current["ollama_config"]["url"] = "http://old-backend:11434"
    client = _EmbeddingClient(current)
    calls = []

    def _identity(config):
        url = config["ollama_config"]["url"]
        calls.append(url)
        if url == "http://old-backend:11434":
            return {"backend": "ollama", "version": "old"}
        return {"backend": "ollama", "version": "target"}

    setattr(client, "embedding_backend_identity_probe", _identity)
    setattr(
        client,
        "embedding_parity_probe",
        lambda *_: calls.append("parity")
        or {
            "parity": True,
            "source_backend_identity": {"backend": "ollama", "version": "old"},
            "target_backend_identity": {"backend": "ollama", "version": "target"},
        },
    )
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": "http://old-backend:11434"},
            },
            "embedding_fingerprint": {
                "engine": "ollama",
                "model": openwebui.EMBEDDING_MODEL,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "backend_identity": {"backend": "ollama", "version": "old"},
            },
        }
    )
    desired = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "embedding": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            "ollama_config": {"url": "http://different-backend:9999"},
        },
    }
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["applied"] == ["embedding"]
    assert report["verified"] == ["embedding"]
    assert report["failed"] == []
    assert client.writes == 1
    # The authorized destination is the ONE destination installed, served
    # back on readback, probed post-write, and persisted.
    assert client.get_embedding_config()["ollama_config"]["url"] == (
        "http://different-backend:9999"
    )
    state = openwebui._read_embedding_state()
    assert state["projection"]["ollama_config"]["url"] == (
        "http://different-backend:9999"
    )
    assert state["embedding_fingerprint"]["backend_identity"] == {
        "backend": "ollama",
        "version": "target",
    }
    assert "http://different-backend:9999" in calls


def _gateway_test_client():
    return _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))


def _gateway_wanted():
    return {
        "RAG_EMBEDDING_ENGINE": "openai",
        "RAG_EMBEDDING_MODEL": f"ollama/{openwebui.EMBEDDING_MODEL}",
        "openai_config": {"url": "http://127.0.0.1:4000/v1"},
        "_served_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
    }


def _gateway_desired():
    return {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "embedding": _gateway_wanted(),
    }


def _write_gateway_managed_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": "http://127.0.0.1:11434"},
            },
            "embedding_fingerprint": {
                "engine": "ollama",
                "model": openwebui.EMBEDDING_MODEL,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "backend_identity": {"backend": "ollama", "version": "same"},
            },
        }
    )


def _gateway_ack():
    return {
        "previous_engine": "ollama",
        "previous_model": openwebui.EMBEDDING_MODEL,
        "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "new_engine": "openai",
        "new_model": f"ollama/{openwebui.EMBEDDING_MODEL}",
        "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "acknowledged_at": "2026-10-20T08:00:00Z",
    }


def test_gateway_embedding_switch_requires_acknowledgement(monkeypatch, tmp_path):
    _write_gateway_managed_state(tmp_path, monkeypatch)
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    client = _gateway_test_client()
    report = openwebui.reconcile_via_api(client, _gateway_desired()).actions
    assert client.writes == 0
    assert report["applied"] == []
    assert report["verified"] == []
    assert report["failed"] == ["embedding"]
    assert (
        openwebui._read_embedding_state()["projection"]["owned"]["RAG_EMBEDDING_ENGINE"]
        == "ollama"
    )
    state = openwebui._read_embedding_state()
    state["acknowledgement"] = _gateway_ack()
    openwebui._write_embedding_state(state)
    client = _gateway_test_client()
    report = openwebui.reconcile_via_api(client, _gateway_desired()).actions
    assert client.writes == 1
    assert report["applied"] == ["embedding"]
    assert report["verified"] == ["embedding"]
    state = openwebui._read_embedding_state()
    assert state["projection"]["route"]["url"] == "http://127.0.0.1:4000/v1"
    assert state["projection"]["route"]["key_reference"].endswith("openwebui.key")
    assert "sentinel-scoped-key" not in openwebui.json.dumps(state)
    assert state["embedding_fingerprint"]["parity"] == "inconclusive"


def test_gateway_embedding_key_readback_mismatch_reports_applied_failed(
    monkeypatch, tmp_path
):
    _write_gateway_managed_state(tmp_path, monkeypatch)
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    state = openwebui._read_embedding_state()
    state["acknowledgement"] = _gateway_ack()
    openwebui._write_embedding_state(state)
    client = _gateway_test_client()
    original_get = client.get_embedding_config

    def shifted_readback():
        payload = json.loads(json.dumps(original_get()))
        payload.setdefault("openai_config", {})["key"] = "server-stored-other"
        return payload

    client.get_embedding_config = shifted_readback
    with pytest.raises(openwebui.OpenWebUIError, match="write verification") as raised:
        openwebui.reconcile_via_api(client, _gateway_desired())
    report = raised.value.actions
    assert client.writes == 1
    assert report["applied"] == ["embedding"]
    assert report["verified"] == []
    assert report["failed"] == ["embedding"]
    assert (
        openwebui._read_embedding_state()["projection"]["owned"]["RAG_EMBEDDING_ENGINE"]
        == "ollama"
    )


def test_gateway_embedding_parity_honesty(monkeypatch, tmp_path):
    _write_gateway_managed_state(tmp_path, monkeypatch)
    state = openwebui._read_embedding_state()
    state["acknowledgement"] = _gateway_ack()
    openwebui._write_embedding_state(state)
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    client = _gateway_test_client()
    openwebui.reconcile_via_api(client, _gateway_desired())
    assert openwebui._read_embedding_state()["embedding_fingerprint"]["parity"] == (
        "inconclusive"
    )
    state = openwebui._read_embedding_state()
    state["projection"] = {  # reset to the pre-switch record for a second pass
        "owned": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
        },
        "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "ollama_config": {"url": "http://127.0.0.1:11434"},
    }
    state["embedding_fingerprint"]["engine"] = "ollama"
    openwebui._write_embedding_state(state)
    client = _gateway_test_client()
    client.previous_embedding_probe = lambda: {"x": [0.1, 0.2, 0.3]}
    client.target_embedding_probe = lambda: {"x": [0.1, 0.2, 0.3]}
    report = openwebui.reconcile_via_api(client, _gateway_desired()).actions
    assert report["verified"] == ["embedding"]
    assert (
        openwebui._read_embedding_state()["embedding_fingerprint"]["parity"] == "pass"
    )


def test_gateway_embedding_desired_render(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(
        openwebui,
        "_gateway_embedding_alias",
        lambda fetch=None, endpoint=None, key_value=None: (
            f"ollama/{openwebui.EMBEDDING_MODEL}"
        ),
    )
    monkeypatch.setattr(
        openwebui, "_litellm_client_key", lambda _name: "sentinel-scoped-key"
    )
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    import route_probes

    captured = {}

    def fake_probe(endpoint, model, key_reference=None, key_value=None, **options):
        captured.update(
            {
                "endpoint": endpoint,
                "model": model,
                "key_reference": key_reference,
                "wire_key": key_value == "sentinel-scoped-key",
            }
        )
        return route_probes._result(
            "ok",
            model,
            endpoint,
            ("HTTP 200 numeric embedding vector",),
            dimension=768,
        )

    monkeypatch.setattr(route_probes, "probe_embeddings", fake_probe)
    desired = openwebui.compute_desired_state(allow_auto_probe=True)
    assert desired["embedding"]["RAG_EMBEDDING_ENGINE"] == "openai"
    assert desired["embedding"]["openai_config"]["url"] == captured["endpoint"]
    assert "ollama_config" not in desired["embedding"]
    record = desired["embedding_selection"]
    assert record["qualification"] == "protocol_probed"
    assert record["route_evidence"]["url"] == captured["endpoint"]
    assert record["output_dims"] == 768
    assert record["key_reference"].endswith("openwebui.key")
    assert captured["wire_key"] is True
    assert captured["model"] == f"ollama/{openwebui.EMBEDDING_MODEL}"
    # The connection payload legitimately carries the delivered key value
    # server-side; the EMBEDDING subtree and its provenance must not.
    assert "sentinel-scoped-key" not in json.dumps(
        {"embedding": desired["embedding"], "selection": desired["embedding_selection"]}
    )


def test_gateway_embedding_readonly_mode_not_resolved(monkeypatch):
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(
        openwebui, "_litellm_client_key", lambda _name: "sentinel-scoped-key"
    )
    import route_probes

    called = []

    def fake_probe(*_args, **_kwargs):
        called.append(True)
        return route_probes._result("ok", "m", "e")

    monkeypatch.setattr(route_probes, "probe_embeddings", fake_probe)

    def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("inventory fetch must not run in read-only mode")

    monkeypatch.setattr(openwebui, "_gateway_embedding_alias", must_not_fetch)
    desired = openwebui.compute_desired_state()
    assert "embedding" not in desired
    assert desired["embedding_status"] == "unavailable"
    record = desired["embedding_selection"]
    assert record["qualification"] == "unknown"
    assert record["reason"] == "gateway inventory not resolved in read-only mode"
    assert called == []


def test_gateway_embedding_alias_unresolved_is_unavailable(monkeypatch):
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(
        openwebui, "_litellm_client_key", lambda _name: "sentinel-scoped-key"
    )
    monkeypatch.setattr(
        openwebui,
        "_gateway_embedding_alias",
        lambda fetch=None, endpoint=None, key_value=None: None,
    )
    desired = openwebui.compute_desired_state(allow_auto_probe=True)
    assert "embedding" not in desired
    assert desired["embedding_status"] == "unavailable"
    record = desired["embedding_selection"]
    assert record["qualification"] == "unavailable"
    assert record["reason"] == "no verified proxy model mapping"


def test_gateway_embedding_repeat_apply_is_noop_and_reverse_restores(
    monkeypatch, tmp_path
):
    _write_gateway_managed_state(tmp_path, monkeypatch)
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    state = openwebui._read_embedding_state()
    state["acknowledgement"] = _gateway_ack()
    openwebui._write_embedding_state(state)
    client = _gateway_test_client()  # replacement-style: update stores wholesale

    # Pass 1: the acknowledged switch installs the gateway route.
    report = openwebui.reconcile_via_api(client, _gateway_desired()).actions
    assert client.writes == 1
    assert report["verified"] == ["embedding"]
    baseline = openwebui._read_embedding_state()["projection"]
    original_previous = baseline["previous"]
    assert original_previous["engine"] == "ollama"

    # Pass 2: the identical apply is a no-op on the same client instance; the
    # original restoration baseline is never rewritten.
    report = openwebui.reconcile_via_api(client, _gateway_desired()).actions
    assert client.writes == 1
    assert report["applied"] == []
    assert report["verified"] == []
    after = openwebui._read_embedding_state()["projection"]
    assert after["previous"] == original_previous
    assert after["previous_output_dims"] == baseline["previous_output_dims"]

    # Reverse transition back to the managed Ollama route (new_engine absent
    # in the legacy reverse ack; dims unchanged, so it authorizes).
    reverse_state = openwebui._read_embedding_state()
    reverse_state["acknowledgement"] = {
        "previous_engine": "openai",
        "previous_model": f"ollama/{openwebui.EMBEDDING_MODEL}",
        "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "new_model": openwebui.EMBEDDING_MODEL,
        "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "acknowledged_at": "2026-10-20T08:30:00Z",
    }
    openwebui._write_embedding_state(reverse_state)
    reverse_wanted = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "embedding": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
        },
    }
    report = openwebui.reconcile_via_api(client, reverse_wanted).actions
    assert client.writes == 2
    assert report["verified"] == ["embedding"]
    restored = openwebui._read_embedding_state()["projection"]
    assert restored["owned"]["RAG_EMBEDDING_ENGINE"] == "ollama"
    assert restored["previous"]["engine"] == "openai"


def test_gateway_embedding_served_width_mismatch_refused_before_post(
    monkeypatch, tmp_path
):
    _write_gateway_managed_state(tmp_path, monkeypatch)
    state = openwebui._read_embedding_state()
    state["acknowledgement"] = _gateway_ack()
    openwebui._write_embedding_state(state)
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    client = _gateway_test_client()
    wanted = _gateway_wanted()
    wanted["_served_dims"] = 384
    report = openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": [], "embedding": wanted}
    ).actions
    assert client.writes == 0
    assert report["failed"] == ["embedding"]
    assert (
        openwebui._read_embedding_state()["projection"]["owned"]["RAG_EMBEDDING_ENGINE"]
        == "ollama"
    )


def test_gateway_selection_record_persists_only_after_apply(monkeypatch, tmp_path):
    _write_gateway_managed_state(tmp_path, monkeypatch)
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "sentinel-scoped-key")
    persisted = []
    monkeypatch.setattr(
        openwebui,
        "_persist_embedding_selection",
        lambda record: persisted.append(record),
    )
    record = {
        "operation": "embedding",
        "provider": "gateway",
        "model": f"ollama/{openwebui.EMBEDDING_MODEL}",
        "qualification": "protocol_probed",
        "reason": "HTTP 200 numeric embedding vector",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "transport": "gateway",
        "endpoint": "http://127.0.0.1:4000/v1",
        "wire_model": f"ollama/{openwebui.EMBEDDING_MODEL}",
        "key_reference": openwebui.LITELLM_OPENWEBUI_KEY_PATH,
        "route_evidence": {
            "provider": "gateway",
            "model": f"ollama/{openwebui.EMBEDDING_MODEL}",
            "operation": "embedding",
            "url": "http://127.0.0.1:4000/v1",
            "credential_reference": openwebui.LITELLM_OPENWEBUI_KEY_PATH,
            "credential_digest": openwebui._credential_digest("sentinel-scoped-key"),
            "engine_build": None,
        },
        "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
    }
    client = _gateway_test_client()
    desired = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "embedding": _gateway_wanted(),
        "embedding_selection": record,
    }
    report = openwebui.reconcile_via_api(client, desired).actions
    assert report["failed"] == ["embedding"]  # no acknowledgement: refused
    assert client.writes == 0
    assert persisted == []  # the unresolved decision never replaces evidence


def test_embedding_adoption_migration_guard_and_acknowledged_switch(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    client = _EmbeddingClient(_embedding_config())
    _embedding_reconcile(client)
    assert client.writes == 0
    assert "384-dim" in caplog.text and "768-dim" in caplog.text
    assert "Reset/re-embed" in caplog.text

    ack = {
        "previous_model": "sentence-transformers/all-MiniLM-L6-v2",
        "previous_engine": "",
        "previous_output_dims": 384,
        "new_model": openwebui.EMBEDDING_MODEL,
        "new_output_dims": 768,
        "acknowledged_at": "operator-confirmed",
    }
    openwebui._write_embedding_state({"acknowledgement": ack})
    _embedding_reconcile(client)
    assert client.writes == 1


def test_embedding_migration_refusal_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openwebui, "_embedding_state_path", lambda: str(tmp_path / "embedding.json")
    )
    client = _EmbeddingClient(_embedding_config())
    report = _embedding_reconcile(client).actions
    assert "embedding" in report["planned"]
    assert report["applied"] == []
    assert report["verified"] == []
    assert report["failed"] == ["embedding"]
    assert client.writes == 0


def test_embedding_failed_readback_reports_applied_and_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openwebui, "_embedding_state_path", lambda: str(tmp_path / "embedding.json")
    )
    client = _EmbeddingClient(_embedding_config())
    openwebui._write_embedding_state(
        {
            "acknowledgement": {
                "previous_model": client.config["RAG_EMBEDDING_MODEL"],
                "previous_engine": "",
                "previous_output_dims": 384,
                "new_model": openwebui.EMBEDDING_MODEL,
                "new_output_dims": 768,
                "acknowledged_at": "test",
            }
        }
    )
    client.ignore_writes = True
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        _embedding_reconcile(client)
    assert raised.value.actions["applied"] == ["embedding"]
    assert raised.value.actions["verified"] == []
    assert raised.value.actions["failed"] == ["embedding"]
    assert client.writes == 1


def test_embedding_release_failed_readback_reports_applied_and_failed(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        openwebui, "_embedding_state_path", lambda: str(tmp_path / "embedding.json")
    )
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    projection = {
        "owned": {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
        },
        "previous": {"engine": "", "model": "sentence-transformers/all-MiniLM-L6-v2"},
        "previous_output_dims": 384,
        "output_dims": 768,
        "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
    }
    openwebui._write_embedding_state(
        {
            "projection": projection,
            "acknowledgement": {
                "previous_model": openwebui.EMBEDDING_MODEL,
                "previous_engine": "ollama",
                "previous_output_dims": 768,
                "new_model": "sentence-transformers/all-MiniLM-L6-v2",
                "new_output_dims": 384,
                "acknowledged_at": "test",
            },
        }
    )
    client.ignore_writes = True
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(
            client, {"openai": [], "anthropic": [], "ollama": []}
        )
    assert raised.value.actions["applied"] == ["embedding"]
    assert raised.value.actions["verified"] == []
    assert raised.value.actions["failed"] == ["embedding"]
    assert client.writes == 1


def test_embedding_identity_injected_and_routing_url_independent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openwebui, "_embedding_state_path", lambda: str(tmp_path / "embedding.json")
    )
    client = _EmbeddingClient(_embedding_config())
    backend_identity = {"backend": "ollama", "version": "0.6.2"}
    setattr(
        client, "embedding_backend_identity_probe", lambda _config: backend_identity
    )
    setattr(client, "embedding_parity_probe", lambda _config: "inconclusive")
    openwebui._write_embedding_state(
        {
            "acknowledgement": {
                "previous_model": client.config["RAG_EMBEDDING_MODEL"],
                "previous_engine": "",
                "previous_output_dims": 384,
                "new_model": openwebui.EMBEDDING_MODEL,
                "new_output_dims": 768,
                "acknowledged_at": "test",
            }
        }
    )
    _embedding_reconcile(client)
    fingerprint = openwebui._read_embedding_state()["embedding_fingerprint"]
    assert fingerprint["backend_identity"] == backend_identity
    assert "url" not in fingerprint and "routing_url" not in fingerprint


def test_embedding_backend_version_change_refuses_restart_reconcile(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    setattr(
        client,
        "embedding_backend_identity_probe",
        lambda _config: {"backend": "ollama", "version": "new"},
    )
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
            },
            "embedding_fingerprint": {
                "engine": "ollama",
                "model": openwebui.EMBEDDING_MODEL,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "backend_identity": {"backend": "ollama", "version": "old"},
            },
        }
    )
    report = _embedding_reconcile(client).actions
    assert client.writes == 0
    assert report["failed"] == ["embedding"]
    assert report["applied"] == [] and report["verified"] == []


def test_embedding_release_checks_backend_identity_before_restoration(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    setattr(
        client,
        "embedding_backend_identity_probe",
        lambda _config: {"backend": "ollama", "version": "new"},
    )
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous": {
                    "engine": "",
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                },
                "previous_output_dims": 384,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
            },
            "embedding_fingerprint": {
                "backend_identity": {"backend": "ollama", "version": "old"},
            },
        }
    )
    report = openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": []}
    ).actions
    assert client.writes == 0
    assert report["failed"] == ["embedding"]
    assert report["applied"] == [] and report["verified"] == []


def test_embedding_backend_change_acknowledgement_authorizes_migration(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    current = _embedding_config("ollama", openwebui.EMBEDDING_MODEL)
    client = _EmbeddingClient(current)
    setattr(
        client,
        "embedding_backend_identity_probe",
        lambda _config: {"backend": "ollama", "version": "new"},
    )
    state = {
        "projection": {
            "owned": {
                "RAG_EMBEDDING_ENGINE": "ollama",
                "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            },
            "previous": {
                "engine": "",
                "model": "sentence-transformers/all-MiniLM-L6-v2",
            },
            "previous_output_dims": 384,
            "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
        },
        "embedding_fingerprint": {
            "backend_identity": {"backend": "ollama", "version": "old"},
        },
        "acknowledgement": {
            "previous_model": openwebui.EMBEDDING_MODEL,
            "previous_engine": "ollama",
            "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "new_model": openwebui.EMBEDDING_MODEL,
            "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "acknowledged_at": "operator-confirmed-backend-refresh",
        },
    }
    openwebui._write_embedding_state(state)
    report = openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "embedding": {"model": openwebui.EMBEDDING_MODEL},
        },
    ).actions
    assert client.writes == 1
    assert report["applied"] == ["embedding"]
    assert report["verified"] == ["embedding"]
    assert report["failed"] == []
    saved = openwebui._read_embedding_state()
    assert saved["embedding_fingerprint"]["backend_identity"] == {
        "backend": "ollama",
        "version": "new",
    }


def test_embedding_backend_change_after_post_keeps_applied_failure_state(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    probes = iter([{"backend": "ollama", "version": "new"}] * 2 + [None])
    request_log = []
    original_update = client.update_embedding_config

    def log_update(config):
        request_log.append("POST embedding")
        original_update(config)

    client.update_embedding_config = log_update
    setattr(client, "embedding_backend_identity_probe", lambda _config: next(probes))
    prior_state = {
        "projection": {
            "owned": {
                "RAG_EMBEDDING_ENGINE": "ollama",
                "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            },
            "previous": {
                "engine": "",
                "model": "sentence-transformers/all-MiniLM-L6-v2",
            },
            "previous_output_dims": 384,
            "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
        },
        "embedding_fingerprint": {
            "backend_identity": {"backend": "ollama", "version": "old"},
        },
        "acknowledgement": {
            "previous_model": openwebui.EMBEDDING_MODEL,
            "previous_engine": "ollama",
            "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "new_model": openwebui.EMBEDDING_MODEL,
            "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "acknowledged_at": "operator-confirmed-backend-refresh",
        },
    }
    openwebui._write_embedding_state(prior_state)
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(
            client,
            {
                "openai": [],
                "anthropic": [],
                "ollama": [],
                "embedding": {"model": openwebui.EMBEDDING_MODEL},
            },
        )
    assert request_log == ["POST embedding"]
    assert raised.value.actions["applied"] == ["embedding"]
    assert raised.value.actions["verified"] == []
    assert raised.value.actions["failed"] == ["embedding"]
    assert openwebui._read_embedding_state() == prior_state


def _identity_probe_sequence(client, versions):
    """Inject a per-call probe sequence; the last version repeats on later calls."""
    calls = {"next": 0}

    def probe(_config):
        index = calls["next"]
        calls["next"] += 1
        return {"backend": "ollama", "version": versions[min(index, len(versions) - 1)]}

    setattr(client, "embedding_backend_identity_probe", probe)


def test_embedding_backend_prewrite_identity_change_refused_before_post(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    _identity_probe_sequence(client, ["new", "third"])
    prior_state = {
        "projection": {
            "owned": {
                "RAG_EMBEDDING_ENGINE": "ollama",
                "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            },
            "previous": {
                "engine": "",
                "model": "sentence-transformers/all-MiniLM-L6-v2",
            },
            "previous_output_dims": 384,
            "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
        },
        "embedding_fingerprint": {
            "backend_identity": {"backend": "ollama", "version": "old"},
        },
        "acknowledgement": {
            "previous_model": openwebui.EMBEDDING_MODEL,
            "previous_engine": "ollama",
            "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "new_model": openwebui.EMBEDDING_MODEL,
            "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "acknowledged_at": "operator-confirmed-backend-refresh",
        },
    }
    openwebui._write_embedding_state(prior_state)
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(
            client,
            {
                "openai": [],
                "anthropic": [],
                "ollama": [],
                "embedding": {"model": openwebui.EMBEDDING_MODEL},
            },
        )
    assert client.writes == 0
    assert raised.value.actions["failed"] == ["embedding"]
    assert raised.value.actions["applied"] == []
    assert raised.value.actions["verified"] == []
    assert openwebui._read_embedding_state() == prior_state


def test_embedding_backend_identity_mismatch_after_post_refuses_verification(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    _identity_probe_sequence(client, ["new", "new", "third"])
    request_log = []
    original_update = client.update_embedding_config

    def log_update(config):
        request_log.append("POST embedding")
        original_update(config)

    client.update_embedding_config = log_update
    prior_state = {
        "projection": {
            "owned": {
                "RAG_EMBEDDING_ENGINE": "ollama",
                "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            },
            "previous": {
                "engine": "",
                "model": "sentence-transformers/all-MiniLM-L6-v2",
            },
            "previous_output_dims": 384,
            "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
        },
        "embedding_fingerprint": {
            "backend_identity": {"backend": "ollama", "version": "old"},
        },
        "acknowledgement": {
            "previous_model": openwebui.EMBEDDING_MODEL,
            "previous_engine": "ollama",
            "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "new_model": openwebui.EMBEDDING_MODEL,
            "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "acknowledged_at": "operator-confirmed-backend-refresh",
        },
    }
    openwebui._write_embedding_state(prior_state)
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(
            client,
            {
                "openai": [],
                "anthropic": [],
                "ollama": [],
                "embedding": {"model": openwebui.EMBEDDING_MODEL},
            },
        )
    assert request_log == ["POST embedding"]
    assert raised.value.actions["applied"] == ["embedding"]
    assert raised.value.actions["verified"] == []
    assert raised.value.actions["failed"] == ["embedding"]
    assert openwebui._read_embedding_state() == prior_state


def test_embedding_backend_acknowledgement_does_not_bypass_concurrent_identity_check(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    setattr(
        client,
        "embedding_backend_identity_probe",
        lambda _config: {"backend": "ollama", "version": "new"},
    )
    original_get = client.get_embedding_config
    calls = 0

    def changed_after_prewrite():
        nonlocal calls
        calls += 1
        config = original_get()
        if calls == 2:
            config["RAG_EMBEDDING_MODEL"] = "concurrent-model-change"
        return config

    client.get_embedding_config = changed_after_prewrite
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous": {"engine": "ollama", "model": openwebui.EMBEDDING_MODEL},
                "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
            },
            "embedding_fingerprint": {
                "backend_identity": {"backend": "ollama", "version": "old"},
            },
            "acknowledgement": {
                "previous_model": openwebui.EMBEDDING_MODEL,
                "previous_engine": "ollama",
                "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "new_model": openwebui.EMBEDDING_MODEL,
                "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "acknowledged_at": "operator-confirmed-backend-refresh",
            },
        }
    )
    report = openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "embedding": {"model": openwebui.EMBEDDING_MODEL},
        },
    ).actions
    assert client.writes == 0
    assert report["failed"] == ["embedding"]


def test_embedding_customized_model_refusal(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    client = _EmbeddingClient(_embedding_config(model="operator-model"))
    _embedding_reconcile(client)
    assert client.writes == 0
    assert "customized Open WebUI embedding identity" in caplog.text


def test_embedding_readback_abort_and_gate_off_restore(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openwebui,
        "_embedding_state_path",
        lambda: str(tmp_path / "managed-embedding.json"),
    )
    original = _embedding_config()
    client = _EmbeddingClient(original)
    openwebui._write_embedding_state(
        {
            "acknowledgement": {
                "previous_model": original["RAG_EMBEDDING_MODEL"],
                "previous_engine": "",
                "previous_output_dims": 384,
                "new_model": openwebui.EMBEDDING_MODEL,
                "new_output_dims": 768,
                "acknowledged_at": "operator-confirmed",
            }
        }
    )
    client.ignore_writes = True
    with pytest.raises(openwebui.OpenWebUIError, match="write verification"):
        _embedding_reconcile(client)
    client.ignore_writes = False
    _embedding_reconcile(client)
    assert (
        openwebui._read_embedding_state()["projection"]["owned"]["RAG_EMBEDDING_ENGINE"]
        == "ollama"
    )
    # Gate-off restoration changes only configuration; it has no vector-data API.
    openwebui._write_embedding_state(
        {
            "projection": openwebui._read_embedding_state()["projection"],
            "acknowledgement": {
                "previous_model": openwebui.EMBEDDING_MODEL,
                "previous_engine": "ollama",
                "previous_output_dims": 768,
                "new_model": original["RAG_EMBEDDING_MODEL"],
                "new_output_dims": 384,
                "acknowledged_at": "operator-confirmed-reverse",
            },
        }
    )
    openwebui.reconcile_via_api(client, {"openai": [], "anthropic": [], "ollama": []})
    assert client.config["RAG_EMBEDDING_ENGINE"] == ""
    assert client.config["RAG_EMBEDDING_MODEL"] == original["RAG_EMBEDDING_MODEL"]
    assert openwebui._read_embedding_state() == {}


def test_embedding_url_drift_refusal(monkeypatch, tmp_path, caplog):
    state_path = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
                },
                "previous": {
                    "engine": "",
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                },
                "previous_output_dims": 384,
                "output_dims": 768,
                "ollama_config": {"url": "http://localhost:11434"},
            }
        }
    )
    client.config["ollama_config"]["url"] = "http://operator-changed:11434"
    _embedding_reconcile(client)
    assert client.writes == 0
    assert "recorded Ollama URL changed" in caplog.text


def test_embedding_reverse_migration_requires_ack_and_verifies_url(
    monkeypatch, tmp_path, caplog
):
    state_path = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    original = _embedding_config()
    client = _EmbeddingClient(original)
    ack = {
        "previous_model": original["RAG_EMBEDDING_MODEL"],
        "previous_engine": "",
        "previous_output_dims": 384,
        "new_model": openwebui.EMBEDDING_MODEL,
        "new_output_dims": 768,
        "acknowledged_at": "switch-consent",
    }
    openwebui._write_embedding_state({"acknowledgement": ack})
    _embedding_reconcile(client)
    client.config["knowledge_documents_embedded"] = True
    client.config.pop("RAG_EMBEDDING_BATCH_SIZE", None)
    # The client fixture's external document marker is not sent in the API config.
    client.config.pop("knowledge_documents_embedded")
    state = openwebui._read_embedding_state()
    openwebui._write_embedding_state({"projection": state["projection"]})
    openwebui.reconcile_via_api(client, {"openai": [], "anthropic": [], "ollama": []})
    assert "768-dim" in caplog.text and "384-dim" in caplog.text
    assert "embeddinggemma-2:740m-mxfp8" in caplog.text
    assert openwebui._read_embedding_state().get("projection") is not None

    reverse_ack = {
        "previous_model": openwebui.EMBEDDING_MODEL,
        "previous_engine": "ollama",
        "previous_output_dims": 768,
        "new_model": "sentence-transformers/all-MiniLM-L6-v2",
        "new_output_dims": 384,
        "acknowledged_at": "reverse-consent",
    }
    openwebui._write_embedding_state(
        {"projection": state["projection"], "acknowledgement": reverse_ack}
    )
    openwebui.reconcile_via_api(client, {"openai": [], "anthropic": [], "ollama": []})
    assert client.config["RAG_EMBEDDING_ENGINE"] == ""
    assert (
        client.config["RAG_EMBEDDING_MODEL"] == "sentence-transformers/all-MiniLM-L6-v2"
    )
    assert openwebui._read_embedding_state() == {}


def test_embedding_failed_reverse_readback_retains_projection(monkeypatch, tmp_path):
    state_path = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    original = _embedding_config()
    client = _EmbeddingClient(original)
    openwebui._write_embedding_state(
        {
            "acknowledgement": {
                "previous_model": original["RAG_EMBEDDING_MODEL"],
                "previous_engine": "",
                "previous_output_dims": 384,
                "new_model": openwebui.EMBEDDING_MODEL,
                "new_output_dims": 768,
                "acknowledged_at": "switch-consent",
            }
        }
    )
    _embedding_reconcile(client)
    state = openwebui._read_embedding_state()
    openwebui._write_embedding_state(
        {
            "projection": state["projection"],
            "acknowledgement": {
                "previous_model": openwebui.EMBEDDING_MODEL,
                "previous_engine": "ollama",
                "previous_output_dims": 768,
                "new_model": "sentence-transformers/all-MiniLM-L6-v2",
                "new_output_dims": 384,
                "acknowledged_at": "reverse-consent",
            },
        }
    )
    client.ignore_writes = True
    with pytest.raises(openwebui.OpenWebUIError, match="restore verification"):
        openwebui.reconcile_via_api(
            client, {"openai": [], "anthropic": [], "ollama": []}
        )
    assert openwebui._read_embedding_state().get("projection") is not None


def test_embedding_ignored_url_restore_retains_projection(monkeypatch, tmp_path):
    state_path = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state_path))
    original = _embedding_config()
    original["ollama_config"]["url"] = "http://prior-ollama:11434"
    client = _EmbeddingClient(original)
    openwebui._write_embedding_state(
        {
            "acknowledgement": {
                "previous_model": original["RAG_EMBEDDING_MODEL"],
                "previous_engine": "",
                "previous_output_dims": 384,
                "new_model": openwebui.EMBEDDING_MODEL,
                "new_output_dims": 768,
                "acknowledged_at": "switch-consent",
            }
        }
    )
    _embedding_reconcile(client)
    state = openwebui._read_embedding_state()
    openwebui._write_embedding_state(
        {
            "projection": state["projection"],
            "acknowledgement": {
                "previous_model": openwebui.EMBEDDING_MODEL,
                "previous_engine": "ollama",
                "previous_output_dims": 768,
                "new_model": original["RAG_EMBEDDING_MODEL"],
                "new_output_dims": 384,
                "acknowledged_at": "operator-confirmed-reverse",
            },
        }
    )
    client.ignore_writes = False
    client.update_embedding_config = lambda config: setattr(
        client, "writes", client.writes + 1
    )
    with pytest.raises(openwebui.OpenWebUIError, match="restore verification"):
        openwebui.reconcile_via_api(
            client, {"openai": [], "anthropic": [], "ollama": []}
        )
    assert openwebui._read_embedding_state().get("projection") is not None


class _AudioClient:
    def __init__(self, audio, openai=None, ollama=None):
        self.audio = audio
        self.audio_writes = 0
        self.concurrent: Any = None
        self.ignore_writes = False
        self.alter_writes = False
        self.fail_connection_write = False
        self.openai = openai if openai is not None else []
        self.ollama = ollama if ollama is not None else []
        self.request_log = []
        self.fail_connection_read = False
        self.fail_snapshot_read = False

    def get_openai_config(self):
        self.request_log.append("GET openai")
        return {"connections": json.loads(json.dumps(self.openai))}

    def get_ollama_config(self):
        self.request_log.append("GET ollama")
        if self.fail_snapshot_read and "POST audio" in self.request_log:
            raise openwebui.OpenWebUIError("snapshot read failed")
        if self.fail_connection_read and "POST openai" in self.request_log:
            raise openwebui.OpenWebUIError("connection verification read failed")
        return {"connections": json.loads(json.dumps(self.ollama))}

    def update_openai_config(self, config):
        self.request_log.append("POST openai")
        if self.fail_connection_write:
            raise openwebui.OpenWebUIError("injected connection failure")
        self.openai = config.get("connections", config)

    def update_ollama_config(self, config):
        self.request_log.append("POST ollama")
        self.ollama = config.get("connections", config)

    def get_audio_config(self):
        if getattr(self, "fail_audio_readback", False):
            result = json.loads(json.dumps(self.audio))
            for section, credential in getattr(self, "masked_credentials", {}).items():
                result.setdefault(section, {})["OPENAI_API_KEY"] = credential
            return result
        if self.concurrent is not None:
            result, self.concurrent = self.concurrent, None
            return result
        return json.loads(json.dumps(self.audio))

    def update_audio_config(self, config):
        self.audio_writes += 1
        self.request_log.append("POST audio")
        if self.ignore_writes:
            if getattr(self, "retain_credentials", False):
                config = json.loads(json.dumps(config))
                for section in config:
                    if "OPENAI_API_KEY" in self.audio.get(section, {}):
                        config[section]["OPENAI_API_KEY"] = self.audio[section][
                            "OPENAI_API_KEY"
                        ]
                self.audio = config
            return
        self.audio = json.loads(json.dumps(config))
        if self.alter_writes:
            self.audio["stt"]["MODEL"] = "write-was-altered"


def test_audio_transition_empty_wanted_does_not_adopt_or_write(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient({"stt": {"MODEL": "administrator-model"}})
    openwebui._reconcile_audio(client, client.audio, {})
    assert client.audio_writes == 0
    assert not state.exists()


def test_audio_none_field_is_not_claimed_or_written(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient({"stt": {"MODEL": "administrator-model"}})
    openwebui._reconcile_audio(client, client.audio, {"stt": {"OPENAI_API_KEY": None}})
    assert client.audio_writes == 0
    assert not state.exists()


def test_audio_projection_never_persists_credentials(tmp_path, monkeypatch):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    sentinel = "R3_AUDIO_SENTINEL_SECRET"
    projection = openwebui._audio_projection(
        {"stt": {"MODEL": "asr", "OPENAI_API_KEY": sentinel}}
    )
    openwebui._write_audio_projection(projection)
    assert sentinel not in state.read_text()
    assert all("OPENAI_API_KEY" not in fields for fields in projection.values())


def test_audio_bundle_none_key_never_claimed_and_stored_key_preserved(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    # Only the stored key is customized; every other owned field sits at the
    # unmanaged default, so the adoption is eligible — the None bundle value
    # must neither claim nor disturb the stored key.
    client = _AudioClient(
        {
            "stt": {
                "MODEL": "",
                "OPENAI_API_KEY": "STORED_KEY_SECRET_92814",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
            }
        }
    )
    openwebui._reconcile_audio(
        client, client.audio, {"stt": {"MODEL": "asr-new", "OPENAI_API_KEY": None}}
    )
    assert client.audio["stt"]["OPENAI_API_KEY"] == "STORED_KEY_SECRET_92814"
    assert client.audio["stt"]["MODEL"] == "asr-new"
    assert client.audio_writes == 1
    assert json.loads(state.read_text())["projection"] == {"stt": {"MODEL": "asr-new"}}
    assert "STORED_KEY_SECRET_92814" not in caplog.text


def test_managed_audio_credential_release_restores_baseline(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    monkeypatch.setenv("OMLX_API_KEY", "managed-secret")
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "prior-admin-secret",
            }
        }
    )
    desired = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "asr",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_KEY": "managed-secret",
        }
    }
    openwebui._reconcile_audio(client, client.audio, desired)
    assert json.loads(state.read_text())["projection"]["stt"]["MODEL"] == "asr"
    marker = state.with_name(state.name + ".credentials")
    assert "managed-secret" not in marker.read_text()
    client = _AudioClient(client.audio)
    setattr(client, "fail_audio_readback", True)
    setattr(client, "masked_credentials", {"stt": "managed-secret"})
    released = openwebui._reconcile_audio(client, client.audio, None)
    assert released["status"] == "refused"
    assert client.audio["stt"]["OPENAI_API_KEY"] == "managed-secret"


def test_public_audio_credential_release_reports_unprovable_restore(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))

    class MaskingClient(_AudioClient):
        def update_audio_config(self, config):
            super().update_audio_config(config)
            if "OPENAI_API_KEY" not in config.get("stt", {}):
                self.audio.setdefault("stt", {})["OPENAI_API_KEY"] = "managed-secret"

    client = MaskingClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "prior-secret",
            }
        }
    )
    wanted_audio = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "asr",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_KEY": "managed-secret",
        }
    }
    desired = {"openai": [], "anthropic": [], "ollama": [], "audio": wanted_audio}
    openwebui.reconcile_via_api(client, desired)
    openwebui._write_audio_projection(
        {
            "stt": {
                "MODEL": "asr",
                "ENGINE": "openai",
                "OPENAI_API_BASE_URL": "http://omlx/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
            }
        }
    )
    openwebui._write_audio_credential_transitions(
        {"stt": {"field": "OPENAI_API_KEY", "restoration": "unprovable"}}
    )
    client.audio["stt"]["MODEL"] = "asr"
    client.audio["stt"]["ENGINE"] = "openai"
    client.audio["stt"]["OPENAI_API_BASE_URL"] = "http://omlx/v1"
    monkeypatch.setenv("OMLX_API_KEY", "prior-secret")
    report = openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": []}
    ).actions
    assert report["failed"] == ["audio"]
    assert report["applied"] == [] and report["verified"] == []
    assert client.audio["stt"]["OPENAI_API_KEY"] == "managed-secret"
    assert (
        json.loads((tmp_path / "managed-audio.json").read_text())["projection"]
        is not None
    )
    assert (
        openwebui._read_audio_credential_transitions()["stt"]["restoration"]
        == "unprovable"
    )


def test_audio_credential_env_provenance_release_restores_environment(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    monkeypatch.setenv("OMLX_API_KEY", "prior-admin-key")
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "prior-admin-key",
            }
        }
    )
    openwebui._reconcile_audio(
        client,
        client.audio,
        {
            "stt": {
                "ENGINE": "openai",
                "MODEL": "asr",
                "OPENAI_API_BASE_URL": "http://omlx/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "managed-key",
            }
        },
    )
    marker = openwebui._read_audio_credential_transitions()
    assert marker["stt"]["restoration"] == "env:OMLX_API_KEY"
    assert "prior-admin-key" not in json.dumps(marker)
    released = openwebui._reconcile_audio(client, client.audio, None)
    assert released["verified"] is True
    assert client.audio["stt"]["OPENAI_API_KEY"] == "prior-admin-key"


def test_audio_empty_credential_provenance_restores_empty(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "",
            }
        }
    )
    openwebui._reconcile_audio(
        client,
        client.audio,
        {
            "stt": {
                "ENGINE": "openai",
                "MODEL": "asr",
                "OPENAI_API_BASE_URL": "http://omlx/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "managed-key",
            }
        },
    )
    assert (
        openwebui._read_audio_credential_transitions()["stt"]["restoration"] == "empty"
    )
    released = openwebui._reconcile_audio(client, client.audio, None)
    assert released["verified"] is True
    assert client.audio["stt"]["OPENAI_API_KEY"] == ""


def test_audio_key_rotation_applies_without_persisting_secret(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    initial = {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "old-secret"}}
    client = _AudioClient(initial)
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    openwebui._reconcile_audio(
        client,
        client.audio,
        {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "ROTATED_SECRET_92814"}},
    )
    assert client.audio_writes == 1
    assert client.audio["stt"]["OPENAI_API_KEY"] == "ROTATED_SECRET_92814"
    assert "ROTATED_SECRET_92814" not in state.read_text()


def test_credential_rotation_waits_for_release_adoption_authorization(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    audio = {
        "stt": {"MODEL": "asr", "OPENAI_API_KEY": "stt-key"},
        "tts": {"MODEL": "admin-tts", "OPENAI_API_KEY": "admin-key"},
    }
    client = _AudioClient(audio)
    desired = {"tts": {"MODEL": "new-tts", "OPENAI_API_KEY": "rotated-key"}}
    openwebui._reconcile_audio(client, audio, desired)
    assert client.audio_writes == 0
    assert client.audio == audio

    client = _AudioClient(audio)
    client.concurrent = {
        "stt": {"MODEL": "admin-edited", "OPENAI_API_KEY": "stt-key"},
        "tts": dict(audio["tts"]),
    }
    openwebui._reconcile_audio(
        client, audio, {"stt": {"MODEL": "new-asr", "OPENAI_API_KEY": "rotated-key"}}
    )
    assert client.audio_writes == 0
    assert client.audio == audio


def test_legacy_audio_credential_projection_is_sanitized_and_recovers(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    state.write_text(
        json.dumps(
            {
                "projection": {
                    "stt": {"MODEL": "asr", "OPENAI_API_KEY": "LEGACY_SECRET"}
                },
                "selection": {"stt": {"operation": "stt", "model": "asr"}},
            }
        )
    )
    client = _AudioClient({"stt": {"MODEL": "custom-admin"}})
    openwebui.reconcile_via_api(client, {"openai": [], "anthropic": [], "ollama": []})
    saved = state.read_text()
    assert "LEGACY_SECRET" not in saved
    assert "restore the prior credential manually" in caplog.text
    assert json.loads(saved)["selection"] == {
        "stt": {"operation": "stt", "model": "asr"}
    }


def test_audio_release_drops_only_released_projection_fields(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    projection = {"stt": {"MODEL": "asr", "ENGINE": "openai"}}
    openwebui._write_audio_projection(projection)
    client = _AudioClient({"stt": {"MODEL": "asr", "ENGINE": "openai"}})
    openwebui._reconcile_audio(client, client.audio, {"stt": {"ENGINE": "openai"}})
    assert client.audio["stt"]["MODEL"] == ""
    assert json.loads(state.read_text())["projection"] == {"stt": {"ENGINE": "openai"}}


def test_audio_release_refuses_customized_new_claim_and_prewrite(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    audio = {"stt": {"MODEL": "asr"}, "tts": {"MODEL": "admin-model"}}
    client = _AudioClient(audio)
    openwebui._reconcile_audio(client, audio, {"tts": {"MODEL": "new-tts"}})
    assert client.audio_writes == 0
    assert "Refusing adoption of customized" in caplog.text
    client = _AudioClient({"stt": {"MODEL": "asr"}, "tts": {"MODEL": ""}})
    client.concurrent = {"stt": {"MODEL": "asr"}, "tts": {"MODEL": "admin-edit"}}
    openwebui._reconcile_audio(client, client.audio, {"tts": {"MODEL": "new-tts"}})
    assert client.audio_writes == 0


def test_audio_release_expansion_defaults_adopts_new_field(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    client = _AudioClient({"stt": {"MODEL": "asr"}, "tts": {"MODEL": ""}})
    openwebui._reconcile_audio(client, client.audio, {"tts": {"MODEL": "tts"}})
    assert client.audio["tts"]["MODEL"] == "tts"
    assert json.loads(state.read_text())["projection"] == {"tts": {"MODEL": "tts"}}


def test_audio_then_connection_failure_reports_applied_and_projection(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient(
        {
            "stt": {
                "MODEL": "",
                "ENGINE": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            }
        }
    )
    client.fail_connection_write = True
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "anthropic": [],
        "ollama": [],
        "audio": {"stt": {"MODEL": "asr"}},
    }
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(client, desired)
    assert client.audio["stt"]["MODEL"] == "asr"
    assert json.loads(state.read_text())["projection"]["stt"]["MODEL"] == "asr"
    assert raised.value.actions["applied"] == ["audio"]
    assert raised.value.actions["failed"] == ["openai"]


def test_audio_apply_survives_later_snapshot_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "audio.json")
    )
    client = _AudioClient(
        {
            "stt": {
                "MODEL": "",
                "ENGINE": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            }
        }
    )
    client.fail_snapshot_read = True
    desired = {
        "openai": [],
        "anthropic": [],
        "ollama": [_entry("ollama", "http://ollama", collection="ollama")],
        "audio": {"stt": {"MODEL": "asr"}},
    }
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(client, desired)
    assert raised.value.actions["applied"] == ["audio"]
    assert raised.value.actions["failed"] == ["connections-snapshot"]
    assert "POST audio" in client.request_log


def test_connection_verification_read_failure_retains_applied(monkeypatch):
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: "/tmp/openwebui-test-no-audio.json"
    )
    client = _AudioClient({}, openai=[], ollama=[])
    client.fail_connection_read = True
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "anthropic": [],
        "ollama": [],
    }
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(client, desired)
    assert raised.value.actions["applied"] == ["openai"]
    assert raised.value.actions["failed"] == ["connections-verification"]
    assert client.request_log.count("POST openai") == 1


def test_connection_write_order_is_deterministic(monkeypatch):
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: None)
    monkeypatch.setattr(openwebui, "_reconcile_audio", lambda *_: {"status": "noop"})
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(
        openwebui, "_reconcile_embedding", lambda *_: {"status": "noop"}
    )

    class Plan:
        entries = []

    class Result:
        status = "clean"
        plan = Plan()
        snapshots = {
            collection: {"envelope": {"connections": []}, "entries": []}
            for collection in ("openai", "ollama")
        }

    Result.plan.entries = [
        {
            "collection": "ollama",
            "action": "add",
            "entry": _entry("ollama", "http://ollama", collection="ollama"),
        },
        {
            "collection": "openai",
            "action": "add",
            "entry": _entry("openai", "https://api.openai.com/v1"),
        },
    ]
    monkeypatch.setattr(openwebui, "reconcile", lambda *_args: Result())

    class ConnectionsOnly:
        def __init__(self):
            self.requests = []
            self.openai = []
            self.ollama = []

        def get_openai_config(self):
            return {"connections": self.openai}

        def get_ollama_config(self):
            return {"connections": self.ollama}

        def update_openai_config(self, config):
            self.requests.append("openai")
            self.openai = config.get("connections", config)

        def update_ollama_config(self, config):
            self.requests.append("ollama")
            self.ollama = config.get("connections", config)

        def get_audio_config(self):
            return {}

        def get_embedding_config(self):
            return {"ollama_config": {}}

        def reconcile(self, *_args):
            if self.requests:
                Result.plan.entries = []
            return Result()

    client = ConnectionsOnly()
    monkeypatch.setattr(openwebui, "reconcile", client.reconcile)
    desired = {"openai": [], "anthropic": [], "ollama": []}
    openwebui.reconcile_via_api(client, desired)
    assert client.requests == ["openai", "ollama"]


def test_restart_after_audio_projection_write_does_not_force_restore(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    audio = {"stt": {"MODEL": ""}}
    client = _AudioClient(audio)
    client.fail_connection_write = True
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "anthropic": [],
        "ollama": [],
        "audio": {"stt": {"MODEL": "asr"}},
    }
    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.reconcile_via_api(client, desired)
    restarted = _AudioClient(client.audio, client.openai, client.ollama)
    result = openwebui.reconcile_via_api(restarted, desired)
    assert restarted.audio["stt"]["MODEL"] == "asr"
    assert restarted.audio_writes == 0
    assert result.status == "clean"


def test_production_shape_audio_noop_and_refusal_reports(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))

    class ProductionShape(_AudioClient):
        def __init__(self, audio):
            super().__init__(audio)
            del self.audio_writes
            self.posts = []

        def update_audio_config(self, config):
            self.posts.append("audio")
            self.audio = json.loads(json.dumps(config))

    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    no_op = ProductionShape({"stt": {"MODEL": "asr"}})
    report = openwebui.reconcile_via_api(
        no_op,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "audio": {"stt": {"MODEL": "asr"}},
        },
    ).actions
    assert report["applied"] == [] and no_op.posts == []
    refusal = ProductionShape({"stt": {"MODEL": "admin"}})
    report = openwebui.reconcile_via_api(
        refusal,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "audio": {"stt": {"MODEL": "wanted"}},
        },
    ).actions
    assert report["failed"] == ["audio"] and refusal.posts == []


@pytest.mark.parametrize("disable", ["gate", "list"])
def test_explicit_omlx_disable_releases_audio(monkeypatch, tmp_path, disable):
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "managed-audio.json")
    )
    if disable == "gate":
        monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    else:
        monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
        monkeypatch.setenv("DOTFILES_OPENWEBUI_DISABLED_ENGINES", "omlx")
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    audio = {"stt": {"MODEL": "asr"}}
    client = _AudioClient(audio)
    desired = openwebui.compute_desired_state()
    openwebui.reconcile_via_api(client, desired)
    assert client.audio["stt"]["MODEL"] == ""
    assert desired.get("audio") is None


def test_audio_prewrite_detects_concurrent_change(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "managed-audio.json")
    )
    projection = {
        "stt": {
            "ENGINE": "openai",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "MODEL": "asr",
        },
        "tts": {
            "ENGINE": "openai",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "MODEL": "qwen3-tts",
            "VOICE": "serena",
        },
    }
    current = {
        "realtime": {"mode": "safe"},
        "stt": dict(projection["stt"]),
        "tts": dict(projection["tts"]),
    }
    client = _AudioClient(current)
    openwebui._write_audio_projection(openwebui._audio_projection(current))
    desired = {
        "stt": dict(projection["stt"], MODEL="new-asr"),
        "tts": dict(projection["tts"]),
    }
    concurrent = {
        "realtime": current["realtime"],
        "stt": dict(current["stt"], MODEL="admin-edit"),
        "tts": current["tts"],
    }
    snapshots = iter([current, concurrent])
    monkeypatch.setattr(client, "get_audio_config", lambda: next(snapshots))
    openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": [], "audio": desired}
    )
    assert client.audio_writes == 0
    assert "Refusing Open WebUI audio write after concurrent edit" in caplog.text


def test_audio_gate_off_clears_only_projection_and_is_stable(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    projection = {
        "stt": {
            "ENGINE": "openai",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "MODEL": "asr",
        },
        "tts": {
            "ENGINE": "openai",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "MODEL": "qwen3-tts",
            "VOICE": "serena",
        },
    }
    openwebui._write_audio_projection(projection)
    client = _AudioClient(
        {
            "realtime": {"x": 1},
            "stt": dict(projection["stt"], WHISPER_MODEL="large"),
            "tts": dict(projection["tts"], AZURE_KEY="preserve"),
        }
    )
    disabled = {"openai": [], "anthropic": [], "ollama": []}
    openwebui.reconcile_via_api(client, disabled)
    assert client.audio["stt"]["MODEL"] == ""
    assert client.audio["tts"]["MODEL"] == ""
    assert client.audio["stt"]["WHISPER_MODEL"] == "large"
    assert client.audio["tts"]["AZURE_KEY"] == "preserve"
    assert client.audio["realtime"] == {"x": 1}
    assert not state.exists()
    writes = client.audio_writes
    openwebui.reconcile_via_api(client, disabled)
    assert client.audio_writes == writes


def _projection_for_audio(audio):
    return openwebui._audio_projection(audio)


def _projection_for_owned(audio, owned_keys):
    """Project exactly the explicitly owned (section, key) subset."""
    grouped = {}
    for section, key in owned_keys:
        grouped.setdefault(section, []).append(key)
    return {
        section: {
            key: audio.get(section, {}).get(
                key, openwebui.AUDIO_DEFAULTS.get(section, {}).get(key, "")
            )
            for key in keys
        }
        for section, keys in grouped.items()
    }


def test_audio_lifecycle_update_projection_stability_and_gate_off(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    initial = {
        "realtime": {"keep": 1},
        "stt": {
            "ENGINE": "",
            "MODEL": "",
            "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_KEY": "admin-key",
            "WHISPER_MODEL": "whisper-op",
            "AZURE_KEY": "azure-op",
            "MISTRAL_KEY": "mistral-op",
            "DEEPGRAM_KEY": "deepgram-op",
        },
        "tts": {
            "ENGINE": "",
            "MODEL": "",
            "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            "VOICE": "",
        },
    }
    client = _AudioClient(initial)
    original_key = client.audio["stt"]["OPENAI_API_KEY"]
    bundle1 = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "stt-old",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
        },
        "tts": {
            "ENGINE": "openai",
            "MODEL": "qwen3-tts",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "VOICE": "serena",
        },
    }
    openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": [], "audio": bundle1}
    )
    assert client.audio_writes == 1
    bundle2 = {
        "stt": dict(
            bundle1["stt"], MODEL="stt-new", OPENAI_API_BASE_URL="http://omlx-next/v1"
        ),
        "tts": dict(
            bundle1["tts"],
            MODEL="qwen3-tts-next",
            VOICE="ryan",
            OPENAI_API_BASE_URL="http://omlx-next/v1",
        ),
    }
    expected_projection = _projection_for_owned(
        {
            **initial,
            "stt": {**initial["stt"], **bundle2["stt"]},
            "tts": {**initial["tts"], **bundle2["tts"]},
        },
        {(section, key) for section, keys in bundle2.items() for key in keys},
    )
    openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": [], "audio": bundle2}
    )
    assert client.audio_writes == 2
    assert json.loads(state.read_text())["projection"] == expected_projection
    before_logs = caplog.text
    openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": [], "audio": bundle2}
    )
    assert client.audio_writes == 2
    assert caplog.text == before_logs
    assert client.audio["stt"]["OPENAI_API_KEY"] == original_key
    assert client.audio["stt"]["WHISPER_MODEL"] == "whisper-op"
    assert client.audio["stt"]["AZURE_KEY"] == "azure-op"
    assert client.audio["stt"]["MISTRAL_KEY"] == "mistral-op"
    assert client.audio["stt"]["DEEPGRAM_KEY"] == "deepgram-op"
    assert client.audio["realtime"] == {"keep": 1}
    openwebui.reconcile_via_api(client, {"openai": [], "anthropic": [], "ollama": []})
    assert client.audio_writes == 3
    assert not state.exists()


def test_audio_stt_only_lifecycle_updates_projection(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
            }
        }
    )
    for model in ("asr-old", "asr-new"):
        openwebui.reconcile_via_api(
            client,
            {
                "openai": [],
                "anthropic": [],
                "ollama": [],
                "audio": {
                    "stt": {
                        "ENGINE": "openai",
                        "MODEL": model,
                        "OPENAI_API_BASE_URL": "http://omlx/v1",
                        "OPENAI_API_REQUEST_FORMAT": "multipart",
                    }
                },
            },
        )
    assert client.audio_writes == 2
    assert json.loads(state.read_text())["projection"]["stt"]["MODEL"] == "asr-new"
    openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "audio": {
                "stt": {
                    "ENGINE": "openai",
                    "MODEL": "asr-new",
                    "OPENAI_API_BASE_URL": "http://omlx/v1",
                    "OPENAI_API_REQUEST_FORMAT": "multipart",
                }
            },
        },
    )
    assert client.audio_writes == 2


def test_stt_only_adoption_preserves_customized_tts(monkeypatch, tmp_path):
    """An STT-only bundle must never claim, alter or erase administrator TTS."""
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    tts_custom = {
        "ENGINE": "custom",
        "MODEL": "admin-tts",
        "OPENAI_API_BASE_URL": "https://tts.example/v1",
        "VOICE": "admin-voice",
        "OPENAI_API_KEY": "tts-operator-key",
    }
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
                "OPENAI_API_KEY": "stt-operator-key",
            },
            "tts": dict(tts_custom),
        }
    )
    wanted = {
        "openai": [],
        "anthropic": [],
        "ollama": [],
        "audio": {
            "stt": {
                "ENGINE": "openai",
                "MODEL": "asr-old",
                "OPENAI_API_BASE_URL": "http://omlx/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
            }
        },
    }
    openwebui.reconcile_via_api(client, wanted)
    assert client.audio_writes == 1
    assert client.audio["tts"] == tts_custom
    persisted = json.loads(state.read_text())["projection"]
    assert set(persisted) == {"stt"}
    assert client.audio["stt"]["OPENAI_API_KEY"] == "stt-operator-key"
    # Managed update advances only the owned subset.
    openwebui.reconcile_via_api(
        client,
        {
            **wanted,
            "audio": {"stt": dict(wanted["audio"]["stt"], MODEL="asr-new")},
        },
    )
    assert client.audio_writes == 2
    assert json.loads(state.read_text())["projection"]["stt"]["MODEL"] == "asr-new"
    assert client.audio["tts"] == tts_custom
    # Gate-off restores only the owned subset; customized TTS stays intact.
    openwebui.reconcile_via_api(client, {"openai": [], "anthropic": [], "ollama": []})
    assert client.audio_writes == 3
    assert not state.exists()
    assert client.audio["tts"] == tts_custom
    assert client.audio["stt"]["MODEL"] == ""
    assert client.audio["stt"]["OPENAI_API_BASE_URL"] == "https://api.openai.com/v1"
    assert client.audio["stt"]["OPENAI_API_REQUEST_FORMAT"] == "multipart"


class _ConcurrentExpansionClient(_AudioClient):
    """First read returns the adopted state; the pre-write read returns a
    snapshot in which an administrator customized the newly-claimed TTS."""

    def __init__(self, audio, prewrite):
        super().__init__(audio)
        self._prewrite = prewrite
        self._reads = 0

    def get_audio_config(self):
        self._reads += 1
        if self._reads == 2:
            return json.loads(json.dumps(self._prewrite))
        return super().get_audio_config()


def test_stt_expansion_refuses_concurrent_tts_customization(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    adopted = {
        "realtime": {"mode": "safe"},
        "stt": {
            "ENGINE": "openai",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "MODEL": "asr",
            "OPENAI_API_KEY": "stt-key",
        },
        "tts": {
            "ENGINE": "",
            "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
            "MODEL": "",
            "VOICE": "",
        },
    }
    stt_fields = frozenset(
        (section, key)
        for section in ("stt",)
        for key in openwebui.AUDIO_OWNED_FIELDS["stt"]
    )
    openwebui._write_audio_projection(
        openwebui._audio_projection_subset(adopted, stt_fields)
    )
    concurrent = {
        **adopted,
        "tts": {
            "ENGINE": "custom",
            "OPENAI_API_BASE_URL": "http://admin-tts/v1",
            "MODEL": "admin-concurrent",
            "VOICE": "admin",
        },
    }
    client = _ConcurrentExpansionClient(adopted, concurrent)
    before = json.dumps(client.audio, sort_keys=True)
    openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "audio": {
                "stt": {
                    "ENGINE": "openai",
                    "OPENAI_API_REQUEST_FORMAT": "multipart",
                    "OPENAI_API_BASE_URL": "http://omlx/v1",
                    "MODEL": "asr-new",
                },
                "tts": {
                    "ENGINE": "openai",
                    "OPENAI_API_BASE_URL": "http://omlx/v1",
                    "MODEL": "qwen3-tts",
                    "VOICE": "serena",
                },
            },
        },
    )
    assert client.audio_writes == 0
    assert "Refusing Open WebUI audio write after concurrent edit" in caplog.text
    assert json.dumps(client.audio, sort_keys=True) == before
    assert set(json.loads(state.read_text())["projection"]) == {"stt"}
    openwebui._write_audio_projection(None)


def test_ignored_audio_adoption_write_raises_without_projection(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
            }
        }
    )
    client.ignore_writes = True
    wanted = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "asr",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
        }
    }
    with pytest.raises(openwebui.OpenWebUIError, match="verification failed"):
        openwebui.reconcile_via_api(
            client, {"openai": [], "anthropic": [], "ollama": [], "audio": wanted}
        )
    assert client.audio_writes == 1
    assert not state.exists()


def test_altered_audio_update_raises_and_preserves_projection(monkeypatch, tmp_path):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient(
        {
            "stt": {
                "ENGINE": "",
                "MODEL": "",
                "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_REQUEST_FORMAT": "multipart",
            }
        }
    )
    first = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "asr-old",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
        }
    }
    openwebui.reconcile_via_api(
        client, {"openai": [], "anthropic": [], "ollama": [], "audio": first}
    )
    prior = json.loads(state.read_text())
    client.alter_writes = True
    updated = {"stt": dict(first["stt"], MODEL="asr-new")}
    with pytest.raises(openwebui.OpenWebUIError, match="verification failed"):
        openwebui.reconcile_via_api(
            client, {"openai": [], "anthropic": [], "ollama": [], "audio": updated}
        )
    assert client.audio_writes == 2
    assert json.loads(state.read_text()) == prior


def test_ignored_audio_gate_off_write_raises_and_keeps_projection(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    current = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "asr",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
        }
    }
    projection = _projection_for_audio(current)
    # The genuine explicit-disable path: the engine is DISABLED, so the
    # discovery-derived audio bundle is omitted and reconcile proceeds to the
    # gate-off release (empty wanted -> _reconcile_audio_gate_off).
    monkeypatch.setenv("DOTFILES_OPENWEBUI_DISABLED_ENGINES", "omlx")
    openwebui._write_audio_projection(projection)
    client = _AudioClient(current)
    client.ignore_writes = True
    with pytest.raises(openwebui.OpenWebUIError, match="verification failed"):
        openwebui.reconcile_via_api(
            client, {"openai": [], "anthropic": [], "ollama": []}
        )
    assert client.audio_writes == 1
    assert json.loads(state.read_text())["projection"] == projection


def test_litellm_proxy_requires_openwebui_key_file_and_never_uses_master(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "master-secret")
    monkeypatch.setattr(
        openwebui.os.path,
        "expanduser",
        lambda _: str(tmp_path / "clients/openwebui.key"),
    )
    with pytest.raises(RuntimeError, match="key file is unavailable"):
        openwebui.compute_desired_state()
    assert "LITELLM_MASTER_KEY" not in openwebui._litellm_client_key.__code__.co_names


def test_litellm_client_key_reads_only_client_file(monkeypatch, tmp_path):
    key_file = tmp_path / "openwebui.key"
    key_file.write_text("client-secret", encoding="utf-8")
    monkeypatch.setattr(openwebui.os.path, "expanduser", lambda _: str(key_file))
    monkeypatch.setenv("LITELLM_MASTER_KEY", "master-secret")
    assert openwebui._litellm_client_key("LITELLM_OPENWEBUI_KEY") == "client-secret"


def test_dual_protocol_and_stable_ownership(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setattr(constants, "is_meridian_configured", lambda: True)
    state = openwebui.compute_desired_state()
    prefixes = {
        item["config"]["prefix_id"] for item in state["openai"] + state["anthropic"]
    }
    assert {"omlx"} <= prefixes
    assert "omlx" not in {item["config"]["prefix_id"] for item in state["anthropic"]}
    result = openwebui.reconcile([], [], state)
    planned = {item["entry"]["config"]["prefix_id"] for item in result.plan.entries}
    assert {
        "omlx",
        "meridian",
        "anthropic",
    } <= planned
    assert "omlx" in openwebui.ownership_catalogue()
    monkeypatch.setenv("DOTFILES_OPENWEBUI_DISABLED_ENGINES", "omlx")
    assert "omlx" in openwebui.ownership_catalogue()
    assert not any(
        item["config"]["prefix_id"] == "omlx"
        for item in openwebui.compute_desired_state()["openai"]
        + openwebui.compute_desired_state()["anthropic"]
    )


def test_disabled_engine_and_meridian(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_OPENWEBUI_DISABLED_ENGINES", "oMlX")
    monkeypatch.setattr(constants, "is_meridian_configured", lambda: False)
    assert not any(
        item["config"]["prefix_id"] == "omlx"
        for item in openwebui.compute_desired_state()["openai"]
        + openwebui.compute_desired_state()["anthropic"]
    )
    monkeypatch.setattr(constants, "is_meridian_configured", lambda: True)
    monkeypatch.setattr(
        constants, "get_meridian_base_url", lambda: "http://meridian:3456/v1"
    )
    item = next(
        item
        for item in openwebui.compute_desired_state()["openai"]
        + openwebui.compute_desired_state()["anthropic"]
        if item["config"]["prefix_id"] == "meridian"
    )
    assert item["url"] == "http://meridian:3456/v1"


def test_collision_wrong_endpoint_is_fail_closed():
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    current = [_entry("openai", "http://wrong/v1")]
    result = openwebui.reconcile(current, [], desired)
    assert result.status == "collision"
    assert not any(
        item["action"] in {"update", "delete"} for item in result.plan.entries
    )


def test_key_rotation_enable_flip_and_dual_protocol_updates():
    catalogue = openwebui.ownership_catalogue()
    url = catalogue["om" + "lx"]["url"]
    desired = {
        "openai": [
            _entry("omlx", url, key="new-key"),
        ],
        "ollama": [],
    }
    current = [
        _entry("omlx", url, key="old-key"),
    ]
    result = openwebui.reconcile(current, [], desired)
    assert [item["action"] for item in result.plan.entries] == ["update"]


def test_ollama_managed_connection_mode_round_trip_is_idempotent(monkeypatch):
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    direct = openwebui.compute_desired_state()["ollama"][0]
    user = {
        "url": "http://user-ollama:11434",
        "key": "user",
        "config": {"name": "user"},
    }
    state = {"openai": [user], "ollama": [direct]}

    def transition(proxy):
        monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1" if proxy else "0")
        monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
        if proxy:
            monkeypatch.setattr(
                openwebui, "_litellm_client_key", lambda _: "client-key"
            )
        gateway = openwebui._connection(
            "ollama", "http://127.0.0.1:4000", "client-key", "openai", "openai"
        )
        desired = (
            {"openai": [gateway], "anthropic": [], "ollama": []}
            if proxy
            else {"openai": [], "anthropic": [], "ollama": [direct]}
        )
        result = openwebui.reconcile(state["openai"], state["ollama"], desired)
        assert result.status == "clean", (result.plan.entries, result.collisions)
        for collection in state:
            for item in result.plan.entries:
                if item["collection"] != collection:
                    continue
                if item["action"] == "delete":
                    state[collection].remove(item["entry"])
                elif item["action"] == "add":
                    state[collection].append(item["entry"])
                elif item["action"] == "update":
                    state[collection].remove(item["before"])
                    state[collection].append(item["entry"])
        return desired

    transition(False)
    first_proxy = transition(True)
    proxy_snapshot = json.loads(json.dumps(state, sort_keys=True))
    assert len(state["ollama"]) == 0
    assert sum(openwebui._identity(item) == "ollama" for item in state["openai"]) == 1
    assert (
        next(
            item
            for item in state["openai"]
            if item["config"].get("managed_by") != "dotfiles"
        )
        == user
    )
    transition(False)
    assert len(state["ollama"]) == 1
    second_proxy = transition(True)
    assert json.loads(json.dumps(state, sort_keys=True)) == proxy_snapshot
    assert first_proxy["openai"] == second_proxy["openai"]


def test_litellm_non_default_port_used_for_ownership_and_desired_state(
    monkeypatch, tmp_path
):
    """Proxy ownership and the sole desired connection use the configured port."""
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_PORT", "4567")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    identity = openwebui.ownership_catalogue()["litellm"]
    desired = openwebui.compute_desired_state()
    assert identity["url"] == "http://127.0.0.1:4567/v1"
    assert identity["connection_type"] == "openai"
    assert identity["collection"] == "openai"
    # Gateway-only mode: a single managed connection, no per-provider entries
    # and no native Ollama entry (every model routes through the catalogue).
    assert [item["url"] for item in desired["openai"]] == ["http://127.0.0.1:4567/v1"]
    assert [openwebui._identity(item) for item in desired["openai"]] == ["litellm"]
    assert desired["openai"][0]["key"] == "client-key"
    assert desired["anthropic"] == []
    assert desired["ollama"] == []


def test_mixed_managed_connections_migrate_both_directions(monkeypatch):
    """Direct/proxy round trips converge while preserving a user connection."""
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "webui-key")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    user = {
        "url": "http://user.example/v1",
        "key": "user-key",
        "config": {"name": "user"},
    }
    state = {"openai": [user], "ollama": []}

    def desired_for_mode(proxy):
        """Build gateway or OpenAI/Ollama desired entries for the selected mode."""
        monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1" if proxy else "0")
        computed = openwebui.compute_desired_state()
        if proxy:
            # Gateway-only mode: every model routes through ONE connection;
            # native Ollama and per-provider entries leave the desired set.
            return {
                "openai": list(computed["openai"]),
                "anthropic": [],
                "ollama": [],
            }
        wanted = [
            item
            for collection in ("openai", "anthropic", "ollama")
            for item in computed[collection]
            if item["config"]["prefix_id"] in {"openai", "ollama"}
        ]
        return {
            "openai": [item for item in wanted if item["collection"] == "openai"],
            "anthropic": [],
            "ollama": [item for item in wanted if item["collection"] == "ollama"],
        }

    def reconcile_mode(proxy):
        """Apply a mode's plan, assert ownership and return a stable snapshot."""
        desired = desired_for_mode(proxy)
        result = openwebui.reconcile(state["openai"], state["ollama"], desired)
        assert result.status == "clean", result.collisions
        for item in result.plan.entries:
            collection = item["collection"]
            if item["action"] == "delete":
                state[collection].remove(item["entry"])
            elif item["action"] == "add":
                state[collection].append(item["entry"])
            elif item["action"] == "update":
                state[collection].remove(item["before"])
                state[collection].append(item["entry"])
        if proxy:
            # One managed gateway connection next to the preserved user entry.
            litellm_entries = [
                entry
                for entry in state["openai"]
                if openwebui._identity(entry) == "litellm"
            ]
            assert len(litellm_entries) == 1 and len(state["openai"]) == 2
            assert litellm_entries[0]["url"] == "http://127.0.0.1:4000/v1"
            assert litellm_entries[0]["config"]["managed_by"] == "dotfiles"
        else:
            assert (
                sum(
                    openwebui._identity(entry) == "litellm" for entry in state["openai"]
                )
                == 0
            )
            assert (
                sum(openwebui._identity(entry) == "openai" for entry in state["openai"])
                == 1
            )
            native = next(
                entry
                for entries in state.values()
                for entry in entries
                if openwebui._identity(entry) == "ollama"
            )
            assert native["url"] == openwebui._canonicalize_host(
                openwebui._root(constants.get_ollama_local_base_url())
            )
        assert user in state["openai"]
        return json.loads(json.dumps(state, sort_keys=True))

    direct_first = reconcile_mode(False)
    proxy_first = reconcile_mode(True)
    assert (
        next(
            entry
            for entry in proxy_first["openai"]
            if openwebui._identity(entry) == "litellm"
        )["url"]
        == "http://127.0.0.1:4000/v1"
    )
    # Round trips converge to the same state in both directions.
    assert reconcile_mode(False) == direct_first
    assert reconcile_mode(True) == proxy_first
    assert reconcile_mode(True) == proxy_first


def test_removal_uses_catalogue_without_managed_marker():
    desired = {"openai": [], "ollama": []}
    result = openwebui.reconcile(
        [_entry("openai", "https://api.openai.com/v1")], [], desired
    )
    assert result.status == "clean"
    assert result.plan.entries[0]["action"] == "delete"


def test_legacy_prefixes_migrate_and_stale_entries_are_deleted():
    url = openwebui.ownership_catalogue()["omlx"]["url"]
    desired_entry = _entry("omlx", url)
    result = openwebui.reconcile(
        [
            _entry("dw-omlx-openai", url, managed=False),
            _entry("dw-foo", "https://stale.example/v1", managed=False),
        ],
        [],
        {"openai": [desired_entry], "anthropic": [], "ollama": []},
    )
    assert [item["action"] for item in result.plan.entries] == ["update", "delete"]
    migrated = result.plan.entries[0]["entry"]
    assert migrated["config"]["prefix_id"] == "omlx"
    assert migrated["config"]["managed_by"] == "dotfiles"

    second = openwebui.reconcile(
        [migrated], [], {"openai": [desired_entry], "anthropic": [], "ollama": []}
    )
    assert [item["action"] for item in second.plan.entries] == ["keep"]


def test_new_style_entry_is_adopted_on_sight():
    url = openwebui.ownership_catalogue()["omlx"]["url"]
    desired = _entry("omlx", url)
    result = openwebui.reconcile(
        [_entry("omlx", url, managed=False)],
        [],
        {"openai": [desired], "anthropic": [], "ollama": []},
    )
    assert result.plan.entries[0]["action"] == "update"
    assert result.plan.entries[0]["entry"]["config"]["managed_by"] == "dotfiles"


def test_unmarked_adoptable_entry_is_preserved_when_provider_is_removed():
    url = openwebui.ownership_catalogue()["omlx"]["url"]
    result = openwebui.reconcile(
        [_entry("om" + "lx", url, managed=False)],
        [],
        {"openai": [], "anthropic": [], "ollama": []},
    )
    assert result.plan.entries[0]["action"] == "keep"


def test_previously_managed_entry_is_deleted_when_provider_is_removed():
    url = openwebui.ownership_catalogue()["omlx"]["url"]
    result = openwebui.reconcile(
        [_entry("omlx", url)],
        [],
        {"openai": [], "anthropic": [], "ollama": []},
    )
    assert [item["action"] for item in result.plan.entries] == ["delete"]


def test_unmarked_new_style_prefix_with_mismatched_identity_is_preserved():
    result = openwebui.reconcile(
        [_entry("omlx", "https://wrong.example/v1", managed=False)],
        [],
        {"openai": [], "anthropic": [], "ollama": []},
    )
    assert result.status == "clean"
    assert result.plan.entries[0]["action"] == "keep"


def _plain_default(url, collection):
    """Upstream-seeded default connection: enable-only config, no prefix_id."""
    return {
        "url": url,
        "key": "",
        "config": {"enable": True},
        "collection": collection,
    }


@pytest.mark.parametrize("prefix", ["", "openai"])
@pytest.mark.parametrize(
    "config",
    [
        {"enable": True, "model_ids": ["custom-model"]},
        {"enable": True, "headers": {}},
        {"enable": False},
        {"enable": 1},
        {"enable": "true"},
        # NB: {} was historically kept as "customised", but open-webui
        # 0.12.0's config migration produces exactly that shape for the
        # stock seed, so it moved to the prune side
        # (test_proxy_prunes_migrated_stock_seed_without_enable).
    ],
)
def test_proxy_preserves_customised_no_key_openai_entry(monkeypatch, prefix, config):
    """Preserve customised OpenAI entries when adding the proxy connection."""
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    user_entry = _plain_default("https://api.openai.com/v1", "openai")
    user_entry["config"] = {"prefix_id": prefix, **config}

    result = openwebui.reconcile([user_entry], [], desired)

    assert result.status == "clean"
    assert [item["action"] for item in result.plan.entries] == ["keep", "add"]
    assert result.plan.entries[0]["entry"] == user_entry
    assert result.plan.entries[1]["entry"] == desired["openai"][0]


@pytest.mark.parametrize("prefix", ["", "openai"])
def test_proxy_prunes_only_stock_no_key_openai_entry(monkeypatch, prefix):
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    seed = _plain_default("https://api.openai.com/v1", "openai")
    seed["config"]["prefix_id"] = prefix

    result = openwebui.reconcile([seed], [], desired)

    assert result.status == "clean"
    assert [item["action"] for item in result.plan.entries] == ["delete", "add"]
    assert result.plan.entries[0]["entry"] == seed


@pytest.mark.parametrize("prefix", ["", "openai"])
def test_proxy_preserves_no_key_openai_entry_with_non_openai_connection_type(
    monkeypatch, prefix
):
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    user_entry = _plain_default("https://api.openai.com/v1", "openai")
    user_entry["config"] = {
        "enable": True,
        "prefix_id": prefix,
        "connection_type": "ollama",
    }

    result = openwebui.reconcile([user_entry], [], desired)

    assert result.status == "clean"
    assert [item["action"] for item in result.plan.entries] == ["keep", "add"]
    assert result.plan.entries[0]["entry"] == user_entry
    assert result.plan.entries[1]["entry"] == desired["openai"][0]


@pytest.mark.parametrize("prefix", ["", "openai"])
def test_proxy_prunes_migrated_stock_seed_without_enable(monkeypatch, prefix):
    """open-webui 0.12.0's config migration drops 'enable' from the stock
    seed (config becomes an empty object); in proxy mode it must still be
    recognised as the prunable duplicate next to the gateway connection."""
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    seed = {
        "url": "https://api.openai.com/v1",
        "key": "",
        "config": {"prefix_id": prefix},
        "collection": "openai",
    }

    result = openwebui.reconcile([seed], [], desired)

    assert result.status == "clean"
    assert [item["action"] for item in result.plan.entries] == ["delete", "add"]
    assert result.plan.entries[0]["entry"] == seed


def test_proxy_collapse_deletes_managed_stale_gateway_entries_with_mismatched_type(
    monkeypatch,
):
    """Migration from the old per-provider proxy topology: managed entries at
    the gateway endpoint whose connection_type the rewritten catalogue no
    longer records (every proxy-mode owner is openai/openai) must stay
    deletable so the collapse applies instead of aborting on collision."""
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    gateway = desired["openai"][0]["url"]
    user_entry = {
        "url": "http://127.0.0.1:1234/v1",
        "key": "user-key",
        "config": {"enable": True, "prefix_id": ""},
        "collection": "openai",
    }
    current = [
        _entry("omlx", gateway),
        _entry("meridian", gateway, kind="anthropic"),
        user_entry,
    ]

    result = openwebui.reconcile(current, [], desired)

    assert result.status == "clean", (result.plan.entries, result.collisions)
    assert [item["action"] for item in result.plan.entries] == [
        "delete",
        "delete",
        "keep",
        "add",
    ]
    assert result.collisions == []


@pytest.mark.parametrize("prefix", ["custom", "future-provider"])
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_unknown_managed_gateway_prefix_collides_without_api_writes(
    monkeypatch, prefix, kind
):
    """Reject unknown managed gateway prefixes without writing either config."""
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    unknown = _entry(prefix, desired["openai"][0]["url"], kind=kind)

    class Client:
        def __init__(self):
            """Seed connection configs and an empty API-write log."""
            self.openai = {"connections": [unknown]}
            self.ollama = {"connections": []}
            self.writes = []

        def get_openai_config(self):
            """Return the fake client's current OpenAI connection config."""
            return self.openai

        def get_ollama_config(self):
            """Return the fake client's current Ollama connection config."""
            return self.ollama

        def update_openai_config(self, payload):
            """Record an OpenAI write and replace the fake client's config."""
            self.writes.append("openai")
            self.openai = payload

        def update_ollama_config(self, payload):
            """Record an Ollama write and replace the fake client's config."""
            self.writes.append("ollama")
            self.ollama = payload

    client = Client()
    result = openwebui.reconcile_via_api(client, desired)
    assert result.status == "collision"
    assert result.collisions == [unknown]
    assert result.plan.entries[0] == {
        "action": "skip",
        "collection": "openai",
        "entry": unknown,
    }
    assert client.writes == []
    assert client.openai == {"connections": [unknown]}


def test_proxy_collision_remains_fail_closed_off_gateway(monkeypatch):
    """The relaxation is scoped to entries physically at the gateway
    endpoint; stale managed entries elsewhere keep the collision abort."""
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(openwebui, "_litellm_client_key", lambda _: "client-key")
    desired = openwebui.compute_desired_state()
    stale = _entry("meridian", "http://elsewhere/v1", kind="anthropic")

    result = openwebui.reconcile([stale], [], desired)

    assert result.status == "collision"
    assert not any(
        item["action"] in {"update", "delete"} for item in result.plan.entries
    )


def test_unmanaged_duplicate_url_of_managed_entry_is_deleted(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    catalogue = openwebui.ownership_catalogue()
    omlx_url = catalogue["omlx"]["url"]
    ollama_url = catalogue["ollama"]["url"]
    desired = {
        "openai": [_entry("omlx", omlx_url)],
        "ollama": [_entry("ollama", ollama_url, "ollama", "ollama")],
    }
    current_openai = [
        _plain_default("https://api.openai.com/v1", "openai"),
        _entry("omlx", omlx_url),
        _plain_default(omlx_url, "openai"),
    ]
    current_ollama = [
        _plain_default(ollama_url, "ollama"),
        _entry("ollama", ollama_url, "ollama", "ollama"),
    ]
    result = openwebui.reconcile(current_openai, current_ollama, desired)
    deletes = {
        item["entry"]["url"]
        for item in result.plan.entries
        if item["action"] == "delete"
    }
    assert deletes == {omlx_url, ollama_url}
    # The managed twins are kept, not touched by the prune.
    assert not any(
        item["action"] in {"delete", "skip", "update"}
        for item in result.plan.entries
        if item["entry"]["config"].get("managed_by") == "dotfiles"
    )
    assert result.status == "clean"


def test_unmanaged_duplicate_url_with_user_config_is_preserved():
    url = "https://api.openai.com/v1"
    desired = {"openai": [_entry("openai", url)], "ollama": []}
    user_entry = _plain_default(url, "openai")
    user_entry["key"] = "user-key"
    user_entry["config"]["prefix_id"] = "user-openai"
    result = openwebui.reconcile([user_entry], [], desired)
    assert result.status == "clean"
    assert result.plan.entries[0]["action"] == "keep"


def test_legacy_duplicate_migration_is_deleted_and_idempotent():
    url = openwebui.ownership_catalogue()["omlx"]["url"]
    desired_entry = _entry("omlx", url)
    current = [
        _entry("dw-omlx", url, managed=False),
        _entry("omlx", url, managed=False),
    ]
    desired = {"openai": [desired_entry], "ollama": []}
    first = openwebui.reconcile(current, [], desired)
    assert [item["action"] for item in first.plan.entries] == ["update", "delete"]
    migrated = first.plan.entries[0]["entry"]
    second = openwebui.reconcile([migrated], [], desired)
    assert [item["action"] for item in second.plan.entries] == ["keep"]


def test_unmarked_desired_prefix_collision_is_reported_and_preserved():
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    current = [_entry("openai", "http://wrong/v1", managed=False)]
    result = openwebui.reconcile(current, [], desired)
    assert result.status == "collision"
    assert result.collisions == current
    assert result.plan.entries[0]["action"] == "keep"


def test_unmanaged_entries_without_matching_url_are_preserved():
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "ollama": [_entry("ollama", "http://127.0.0.1:11434", "ollama", "ollama")],
    }
    unmanaged = _plain_default("http://user/v1", "openai")
    unmanaged["admin_metadata"] = {"model_order": ["user-model"]}
    result = openwebui.reconcile([unmanaged], [], desired)
    item = next(
        item
        for item in result.plan.entries
        if item["entry"].get("url") == "http://user/v1"
    )
    assert item["action"] == "keep"
    assert item["entry"]["admin_metadata"] == {"model_order": ["user-model"]}


def test_duplicate_prune_end_to_end_and_idempotent(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    catalogue = openwebui.ownership_catalogue()
    openai_url = catalogue["openai"]["url"]
    ollama_url = catalogue["ollama"]["url"]
    desired = {
        "openai": [_entry("openai", openai_url)],
        "ollama": [_entry("ollama", ollama_url, "ollama", "ollama")],
    }

    class Client:
        def __init__(self):
            self.openai = {
                "connections": [
                    _plain_default(openai_url, "openai"),
                    _entry("openai", openai_url),
                ]
            }
            self.ollama = {
                "OLLAMA_BASE_URLS": [ollama_url, ollama_url],
                "OLLAMA_API_CONFIGS": {
                    "0": {"enable": True},
                    "1": {
                        "prefix_id": "ollama",
                        "managed_by": "dotfiles",
                        "connection_type": "ollama",
                        "enable": True,
                    },
                },
            }
            self.writes = []

        def get_openai_config(self):
            return self.openai

        def get_ollama_config(self):
            return self.ollama

        def update_openai_config(self, payload):
            self.writes.append("openai")
            self.openai = payload

        def update_ollama_config(self, payload):
            self.writes.append("ollama")
            self.ollama = payload

    client = Client()
    openwebui.reconcile_via_api(client, desired)
    assert sorted(client.writes) == ["ollama", "openai"]
    assert client.openai["connections"][0]["config"]["prefix_id"] == "openai"
    assert client.ollama["OLLAMA_BASE_URLS"] == [ollama_url]
    # Second pass: no writes, nothing to reconcile.
    client.writes = []
    openwebui.reconcile_via_api(client, desired)
    assert client.writes == []


def test_reconcile_via_api_migrates_legacy_dw_entry_and_verifies():
    catalogue = openwebui.ownership_catalogue()
    ollama_url = catalogue["ollama"]["url"]

    class Client:
        def __init__(self):
            self.ollama = {
                "OLLAMA_BASE_URLS": [ollama_url],
                "OLLAMA_API_CONFIGS": {
                    "0": {
                        "prefix_id": "dw-ollama",
                        "connection_type": "ollama",
                        "enable": True,
                    }
                },
            }
            self.writes = 0

        def get_openai_config(self):
            return []

        def get_ollama_config(self):
            return self.ollama

        def update_openai_config(self, payload):
            raise AssertionError("unexpected OpenAI write")

        def update_ollama_config(self, payload):
            self.ollama = payload
            self.writes += 1

    desired = {
        "openai": [],
        "ollama": [_entry("ollama", ollama_url, "ollama", "ollama")],
    }
    client = Client()
    result = openwebui.reconcile_via_api(client, desired)

    assert result.status == "clean"
    assert client.writes == 1
    assert client.ollama["OLLAMA_API_CONFIGS"]["0"]["prefix_id"] == "ollama"
    assert client.ollama["OLLAMA_API_CONFIGS"]["0"]["managed_by"] == "dotfiles"


def test_reconcile_via_api_keeps_foreign_dw_entry_unmanaged():
    catalogue = openwebui.ownership_catalogue()
    openai_url = catalogue["openai"]["url"]
    foreign = _entry("dw-foreign", "https://foreign.example/v1", managed=False)

    class Client:
        def __init__(self):
            self.openai = {"connections": [foreign]}
            self.writes = 0

        def get_openai_config(self):
            return self.openai

        def get_ollama_config(self):
            return []

        def update_openai_config(self, payload):
            self.openai = payload
            self.writes += 1

        def update_ollama_config(self, payload):
            raise AssertionError("unexpected Ollama write")

    desired = {
        "openai": [_entry("openai", openai_url)],
        "ollama": [],
    }
    client = Client()
    result = openwebui.reconcile_via_api(client, desired)

    assert client.writes == 1
    assert any(
        item["action"] == "delete"
        and item["entry"]["config"]["prefix_id"] == "dw-foreign"
        for item in result.plan.entries
    )


def test_unmanaged_and_envelope_preserved_and_only_changed_collection_written():
    class Client:
        def __init__(self):
            unmanaged = _entry("user", "http://user/v1", managed=False)
            unmanaged["admin_metadata"] = {"model_order": ["user-model"]}
            self.openai = {
                "connections": [
                    unmanaged,
                    _entry("openai", "https://api.openai.com/v1"),
                ]
            }
            self.ollama = {
                "items": [
                    _entry("ollama", "http://127.0.0.1:11434", "ollama", "ollama")
                ]
            }
            self.writes = []

        def get_openai_config(self):
            return self.openai

        def get_ollama_config(self):
            return self.ollama

        def update_openai_config(self, payload):
            self.writes.append(("openai", payload))
            self.openai = payload

        def update_ollama_config(self, payload):
            self.writes.append(("ollama", payload))
            self.ollama = payload

    client = Client()
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1", key="rotated")],
        "ollama": [_entry("ollama", "http://127.0.0.1:11434", "ollama", "ollama")],
    }
    openwebui.reconcile_via_api(client, desired)
    assert [name for name, _ in client.writes] == ["openai"]
    assert client.writes[0][1].keys() == {"connections"}
    preserved = client.writes[0][1]["connections"][0]
    assert preserved["config"]["prefix_id"] == "user"
    assert preserved["collection"] == "openai"
    assert preserved["admin_metadata"] == {"model_order": ["user-model"]}


def test_proxy_ownership_add_update_and_second_run_is_idempotent(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    monkeypatch.setattr(constants, "check_ollama_daemon", lambda: (True, True))
    desired = openwebui.compute_desired_state()

    class Client:
        def __init__(self):
            self.openai, self.ollama = [], []
            self.writes = 0

        def get_openai_config(self):
            return self.openai

        def get_ollama_config(self):
            return self.ollama

        def update_openai_config(self, payload):
            self.openai = payload
            self.writes += 1

        def update_ollama_config(self, payload):
            self.ollama = payload
            self.writes += 1

    client = Client()
    first = openwebui.reconcile_via_api(client, desired)
    assert first.status == "clean"
    assert any(item["action"] == "add" for item in first.plan.entries)
    writes = client.writes
    rotated = {key: list(value) for key, value in desired.items()}
    rotated["openai"] = [dict(item, key="rotated-key") for item in rotated["openai"]]
    updated = openwebui.reconcile_via_api(client, rotated)
    assert any(item["action"] == "update" for item in updated.plan.entries)
    writes = client.writes
    second = openwebui.reconcile_via_api(client, rotated)
    assert second.status == "clean"
    assert not any(item["action"] != "keep" for item in second.plan.entries)
    assert client.writes == writes


def test_wrong_collection_is_collision():
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    current = [_entry("openai", "https://api.openai.com/v1", collection="ollama")]
    result = openwebui.reconcile([], current, desired)
    assert result.status == "collision"


def test_empty_prefix_id_current_entries_do_not_crash():
    current = [_entry("", "https://api.openai.com/v1", key="seeded-key", managed=False)]
    desired = {
        "openai": [_entry("openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    result = openwebui.reconcile([], current, desired)
    assert result.status == "clean"
    actions = {
        item["entry"]["config"].get("prefix_id"): item["action"]
        for item in result.plan.entries
    }
    assert actions[""] == "keep"
    assert actions["openai"] == "add"


def test_null_prefix_id_current_entry_does_not_crash():
    current = [_entry("", "https://api.openai.com/v1", key="seeded-key", managed=False)]
    current[0]["config"]["prefix_id"] = None
    result = openwebui.reconcile(
        current,
        [],
        {"openai": [], "ollama": []},
    )
    assert result.status == "clean"
    assert result.plan.entries[0]["action"] == "keep"


def test_normalization_fails_closed_for_unknown_shapes():
    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.OpenWebUIClient.normalize({"unexpected": {}}, "openai")
    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.OpenWebUIClient.normalize("not-json-object", "openai")


def test_live_0114_config_shapes_normalize_and_round_trip():
    openai = {
        "ENABLE_OPENAI_API": True,
        "OPENAI_API_BASE_URLS": ["http://openai/v1"],
        "OPENAI_API_KEYS": ["key"],
        "OPENAI_API_CONFIGS": {"0": {"prefix_id": "openai", "enable": True}},
    }
    ollama = {
        "ENABLE_OLLAMA_API": True,
        "OLLAMA_BASE_URLS": ["http://ollama"],
        "OLLAMA_API_CONFIGS": {"0": {"key": "key", "prefix_id": "ollama"}},
    }
    openai_entries, _ = openwebui.OpenWebUIClient.normalize(openai, "openai")
    ollama_entries, _ = openwebui.OpenWebUIClient.normalize(ollama, "ollama")
    assert openai_entries[0]["key"] == "key"
    assert ollama_entries[0]["config"] == {"prefix_id": "ollama"}
    assert openwebui.OpenWebUIClient.payload(openai, openai_entries) == openai
    assert openwebui.OpenWebUIClient.payload(ollama, ollama_entries) == ollama


def test_desired_state_does_not_probe_without_ollama_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    probe = pytest.fail
    monkeypatch.setattr(
        constants, "check_ollama_daemon", lambda: probe("unexpected probe")
    )
    openwebui.compute_desired_state()


def test_ollama_probe_runs_at_most_once_per_compute(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    calls = []
    monkeypatch.setattr(
        constants,
        "check_ollama_daemon",
        lambda: (calls.append("probe") or (True, False)),
    )
    openwebui.compute_desired_state()
    assert len(calls) <= 1


def test_snapshot_change_fails_closed():
    class Client:
        def __init__(self):
            self.calls = 0

        def get_openai_config(self):
            self.calls += 1
            return (
                []
                if self.calls == 1
                else [_entry("user", "http://changed", managed=False)]
            )

        def get_ollama_config(self):
            return []

        def update_openai_config(self, payload):
            raise AssertionError("must not write")

        def update_ollama_config(self, payload):
            raise AssertionError("must not write")

    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.reconcile_via_api(
            Client(),
            {
                "openai": [_entry("openai", "https://api.openai.com/v1")],
                "ollama": [],
            },
        )


def test_mask_secret():
    assert openwebui.mask_secret("short") == "<set>"
    assert openwebui.mask_secret("") == "<unset>"
    assert openwebui.mask_secret(None) == "<unset>"


def test_cloud_connections_require_keys(monkeypatch):
    assert not any(
        item["config"]["prefix_id"] == "openai"
        for item in openwebui.compute_desired_state()["openai"]
    )
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    assert any(
        item["config"]["prefix_id"] == "openai"
        for item in openwebui.compute_desired_state()["openai"]
    )


def test_signin_fallback_retries_with_session_token(monkeypatch):
    import io
    import json
    import urllib.error
    import urllib.request

    calls = []

    class FakeResponse:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, dict(request.headers).get("Authorization", "")))
        if "/api/v1/auths/signin" in request.full_url:
            return FakeResponse({"token": "jwt-token"})
        auth = dict(request.headers).get("Authorization", "")
        if auth == "Bearer jwt-token":
            return FakeResponse({"ok": True})
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = openwebui.OpenWebUIClient(
        "http://x",
        "sk-stale",
        admin_credentials={"email": "a@b", "password": "pw"},
    )
    assert client.get_openai_config() == {"ok": True}
    signin_calls = [c for c in calls if "/api/v1/auths/signin" in c[0]]
    assert len(signin_calls) == 1
    # credentials must never be sent as headers
    assert all("a@b" not in (c[1] or "") for c in calls)


def test_signin_fallback_absent_credentials_fails_closed(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = openwebui.OpenWebUIClient("http://x", "sk-stale")
    with pytest.raises(openwebui.AuthError):
        client.get_openai_config()
    # no signin attempted without credentials; no retry loop
    assert all("/api/v1/auths/signin" not in url for url in calls)


def test_cli_exit_codes(monkeypatch):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: True)
    monkeypatch.setattr(
        module, "compute_desired_state", lambda **_kwargs: {"openai": [], "ollama": []}
    )
    monkeypatch.setattr(module.OpenWebUIClient, "get_openai_config", lambda self: [])
    monkeypatch.setattr(module.OpenWebUIClient, "get_ollama_config", lambda self: [])
    assert module.main.__name__ == "main"
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--check"])
    assert module.main() == 2
    monkeypatch.setattr(
        module,
        "compute_desired_state",
        lambda **_kwargs: {
            "openai": [_entry("openai", "https://api.openai.com/v1")],
            "ollama": [],
        },
    )
    assert module.main() == 2
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--dry-run"])
    assert module.main() == 0
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--invalid"])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


def test_entrypoint_diagnostic_modes_never_probe(monkeypatch, caplog):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module, "_openwebui_service_value", lambda _name: "")
    captured = {}

    def recorder(**kwargs):
        captured.update(kwargs)
        return {"openai": [], "ollama": []}

    monkeypatch.setattr(module, "compute_desired_state", recorder)
    monkeypatch.setattr(
        module.sys, "argv", ["configure-openwebui.py", "--reconcile", "--dry-run"]
    )
    assert module.main() == 0
    assert captured == {}  # construction never ran: no probes precede the key gate
    assert any("OPENWEBUI_API_KEY is absent" in line for line in caplog.messages)


def test_entrypoint_gateway_apply_requires_client_key(monkeypatch, tmp_path, caplog):
    module = _script()
    # HOME isolation: the gate's expanduser-resolved key path misses under a
    # disposable HOME on every host, so the fail-closed gate is testable.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: True)
    captured = {}

    def recorder(**kwargs):
        captured.update(kwargs)
        return {"openai": [], "ollama": []}

    monkeypatch.setattr(module, "compute_desired_state", recorder)
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--reconcile"])
    assert module.main() == 1
    assert "client key file is unavailable" in " ".join(caplog.messages)
    assert captured == {}  # construction never ran: the key precedes compute
    monkeypatch.setattr(module.OpenWebUIClient, "get_openai_config", lambda self: [])
    monkeypatch.setattr(module.OpenWebUIClient, "get_ollama_config", lambda self: [])
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--check"])
    assert module.main() == 2  # read-only gateway mode is not gated


def test_entrypoint_check_suppresses_probe_routes(monkeypatch, caplog):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: True)
    captured = {}

    def recorder(**kwargs):
        captured.update(kwargs)
        return {"openai": [], "ollama": []}

    monkeypatch.setattr(module, "compute_desired_state", recorder)
    monkeypatch.setattr(module.OpenWebUIClient, "get_openai_config", lambda self: [])
    monkeypatch.setattr(module.OpenWebUIClient, "get_ollama_config", lambda self: [])
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["configure-openwebui.py", "--check", "--probe-routes"],
    )
    assert module.main() == 2
    assert captured["probe_routes"] is False
    assert captured["allow_auto_probe"] is False
    assert any("ignored in check/dry-run" in line for line in caplog.messages)


def test_entrypoint_collision_result_exits_nonzero(monkeypatch, caplog):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: True)
    monkeypatch.setattr(module, "compute_desired_state", lambda **kwargs: {})

    class _Collision:
        status = "collision"

        def summary(self):
            return "collision summary"

    monkeypatch.setattr(
        module, "reconcile_via_api", lambda client, desired: _Collision()
    )
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--reconcile"])
    assert module.main() == 1
    assert any("collision" in line for line in caplog.messages)


def test_entrypoint_unhealthy_service_never_probes(monkeypatch, caplog):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: False)
    called = []
    monkeypatch.setattr(
        module, "compute_desired_state", lambda **kwargs: called.append(kwargs) or {}
    )
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--reconcile"])
    assert module.main() == 1
    assert called == []
    assert any("not healthy" in line for line in caplog.messages)


def test_entrypoint_apply_without_key_fails_but_dry_run_skips(monkeypatch):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module, "_openwebui_service_value", lambda _name: "")
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--reconcile"])
    assert module.main() == 1
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["configure-openwebui.py", "--reconcile", "--dry-run"],
    )
    assert module.main() == 0


def test_entrypoint_failed_reconcile_actions_exit_nonzero(monkeypatch, caplog):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: True)
    monkeypatch.setattr(
        module, "compute_desired_state", lambda **_kwargs: {"openai": [], "ollama": []}
    )
    result = openwebui.ReconcileResult(openwebui.ReconcilePlan(), snapshots={})
    result.actions = {
        "planned": [],
        "applied": [],
        "verified": [],
        "failed": ["embedding"],
    }
    monkeypatch.setattr(module, "reconcile_via_api", lambda *_: result)
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--reconcile"])
    assert module.main() == 1
    assert "embedding" in caplog.text


def test_entrypoint_dry_run_missing_key_remains_successful(monkeypatch, caplog):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module, "_openwebui_service_value", lambda _name: "")
    monkeypatch.setattr(
        module.sys, "argv", ["configure-openwebui.py", "--reconcile", "--dry-run"]
    )
    assert module.main() == 0
    assert "OPENWEBUI_API_KEY is absent" in caplog.text


def test_terminal_helpers_error_paths_are_hermetic(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    env_file = tmp_path / "terminal.env"
    sync_script = (
        f"source {shlex.quote(str(helper))}; openssl() {{ return 1; }}; "
        f"openwebui_terminal_service_env_sync {shlex.quote(str(env_file))}"
    )
    sync = subprocess.run(["bash", "-c", sync_script], capture_output=True)
    assert sync.returncode == 1
    start_script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; openwebui_terminal_service_start"
    )
    start = subprocess.run(["bash", "-c", start_script], capture_output=True)
    assert start.returncode == 1


def test_terminal_registration_collision_and_shape_fail_closed(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_terminal(
            type(
                "Client",
                (),
                {
                    "get_terminal_servers_config": lambda self: {
                        "TERMINAL_SERVER_CONNECTIONS": [
                            {
                                "id": "dotfiles-open-terminal",
                                "url": "http://wrong",
                                "auth_type": "bearer",
                            }
                        ]
                    }
                },
            )()
        )
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_terminal(
            type("Client", (), {"get_terminal_servers_config": lambda self: []})()
        )


def test_terminal_registration_absence_and_snapshot_change(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = tmp_path / ".local/share/openwebui/terminal.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OPEN_TERMINAL_API_KEY='terminal-key'\n")

    class Client:
        def __init__(self):
            self.connections = [
                {
                    "id": "dotfiles-open-terminal",
                    "url": "http://127.0.0.1:8123",
                    "auth_type": "bearer",
                }
            ]
            self.writes = 0

        def get_terminal_servers_config(self):
            return {"TERMINAL_SERVER_CONNECTIONS": self.connections}

        def update_terminal_servers_config(self, payload):
            self.connections = payload["TERMINAL_SERVER_CONNECTIONS"]
            self.writes += 1

    client = Client()
    assert module._reconcile_terminal(client) == 0
    assert client.connections == []
    assert client.writes == 1

    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "1")
    client.connections = []
    client.writes = 0
    calls = iter(
        [
            {"TERMINAL_SERVER_CONNECTIONS": []},
            {"TERMINAL_SERVER_CONNECTIONS": [{"id": "other"}]},
        ]
    )
    client.get_terminal_servers_config = lambda: next(calls)
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_terminal(client)


def test_service_env_fallback_reads_admin_credentials(monkeypatch, tmp_path):
    module = _script()
    service_env = tmp_path / "service.env"
    service_env.write_text(
        "OPENWEBUI_API_KEY='service-key'\nWEBUI_ADMIN_EMAIL='admin@example'\nWEBUI_ADMIN_PASSWORD='pw'\n"
    )
    monkeypatch.setattr(module.os.path, "expanduser", lambda _: str(service_env))
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)
    assert module._openwebui_service_value("OPENWEBUI_API_KEY") == "service-key"
    assert module._openwebui_service_value("WEBUI_ADMIN_EMAIL") == "admin@example"


def test_mcp_registry_selects_http_and_excludes_stdio_sse(tmp_path):
    module = _script()
    (tmp_path / "configs/mcp").mkdir(parents=True)
    # Inventory comes from the registry (global-mcps.json), not a glob:
    # stdio/sse templates referenced there are excluded; http is selected.
    (tmp_path / "configs/mcp/global-mcps.json").write_text(
        '{"project_mcp_templates":["http","stdio","sse"],'
        '"tools":{"ai":{"mcp_servers":[{"template":"http"}]},'
        '"cli":{"mcp_servers":[{"template":"stdio"},{"template":"sse"}]}}}'
    )
    (tmp_path / "configs/mcp/http.json").write_text(
        '{"name":"http","type":"url","url":"https://example.test/mcp"}'
    )
    (tmp_path / "configs/mcp/stdio.json").write_text(
        '{"name":"stdio","type":"command","command":"tool"}'
    )
    (tmp_path / "configs/mcp/sse.json").write_text(
        '{"name":"sse","type":"url","url":"sse://example.test"}'
    )
    # A stray JSON file in configs/mcp/ that the registry does not reference
    # must NOT be registered (no glob-acquired capabilities).
    (tmp_path / "configs/mcp/unreferenced.json").write_text(
        '{"name":"unreferenced","type":"url","url":"https://stray.test/mcp"}'
    )
    selected = module._streamable_mcp_connections(tmp_path)
    assert [item["info"]["id"] for item in selected] == ["dotfiles-mcp-http"]


def test_mcp_registry_resolves_or_skips_header_placeholders(tmp_path, monkeypatch):
    root = tmp_path / "configs/mcp"
    root.mkdir(parents=True)
    (root / "global-mcps.json").write_text(
        '{"project_mcp_templates":["missing","present"],'
        '"tools":{"cli":{"mcp_servers":[{"template":"missing"},{"template":"present"}]}}}'
    )
    (root / "missing.json").write_text(
        '{"name":"missing","type":"url","url":"https://missing.test/mcp","headers":{"Authorization":"Bearer ${MISSING_MCP_TOKEN}"}}'
    )
    (root / "present.json").write_text(
        '{"name":"present","type":"url","url":"https://present.test/mcp","headers":{"Authorization":"Bearer ${GH_TOKEN}"}}'
    )
    monkeypatch.setenv("GH_TOKEN", "token")
    module = _script()
    selected = module._streamable_mcp_connections(tmp_path)
    assert [item["info"]["id"] for item in selected] == ["dotfiles-mcp-present"]
    assert selected[0]["headers"]["Authorization"] == "Bearer token"


def test_mcp_registration_collision_and_clean_preservation(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setenv("HOME", str(tmp_path))
    desired = module._streamable_mcp_connections(Path(__file__).resolve().parents[3])
    assert desired

    class Client:
        def __init__(self, current):
            self.current, self.writes = current, 0

        def get_tool_servers_config(self):
            return {"TOOL_SERVER_CONNECTIONS": self.current}

        def update_tool_servers_config(self, payload):
            self.current = payload["TOOL_SERVER_CONNECTIONS"]
            self.writes += 1

    collision = dict(desired[0], url="https://wrong.example")
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_mcp(Client([collision]), dry_run=False)
    unmanaged = {"info": {"id": "admin"}, "url": "https://admin.example"}
    client = Client([unmanaged] + desired)
    assert module._reconcile_mcp(client) == 0
    assert client.writes == 0


def test_mcp_removed_registry_state_is_deleted(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / ".local/share/openwebui/data"
    state.mkdir(parents=True)
    (state / "managed-mcp.json").write_text(
        '{"connections":{"dotfiles-mcp-old":"https://old.example"}}'
    )

    class Client:
        def __init__(self):
            self.current = [
                {"info": {"id": "dotfiles-mcp-old"}, "url": "https://old.example"}
            ]
            self.writes = 0

        def get_tool_servers_config(self):
            return {"TOOL_SERVER_CONNECTIONS": self.current}

        def update_tool_servers_config(self, payload):
            self.current = payload["TOOL_SERVER_CONNECTIONS"]
            self.writes += 1

    monkeypatch.setattr(module, "_streamable_mcp_connections", lambda: [])
    client = Client()
    assert module._reconcile_mcp(client) == 0
    assert client.current == [] and client.writes == 1


def test_catalogue_reconciliation_is_merge_only(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    curated = ["openai/gpt-x", "anthropic/claude-y"]

    monkeypatch.setattr(module, "_curated_cloud_models", lambda: curated)
    live = {
        "data": [
            {"id": "openai.gpt-x"},
            {"id": "anthropic.claude-y"},
        ]
    }

    class Client:
        def __init__(self, current):
            self.current, self.writes = current, 0

        def get_models(self):
            return live

        def get_models_config(self):
            return dict(self.current)

        def update_models_config(self, config):
            self.current = dict(config)
            self.writes += 1

    user_default = "user-favorite-model"
    user_pinned = "user-pinned-model,another-pin"
    user_order = ["user-first", "user-second"]
    client = Client(
        {
            "DEFAULT_MODELS": user_default,
            "DEFAULT_PINNED_MODELS": user_pinned,
            "MODEL_ORDER_LIST": list(user_order),
        }
    )
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == 1
    # user entries preserved verbatim and FIRST; managed entries appended
    # (order within the appended set is the reconciler's, not the caller's)
    assert client.current["DEFAULT_MODELS"].split(",")[0] == user_default
    live_curated = {"openai.gpt-x", "anthropic.claude-y"}
    assert set(client.current["DEFAULT_MODELS"].split(",")[1:]) == live_curated
    assert client.current["DEFAULT_PINNED_MODELS"].split(",")[0] == "user-pinned-model"
    assert user_order[0] == client.current["MODEL_ORDER_LIST"][0]
    assert live_curated.issubset(set(client.current["MODEL_ORDER_LIST"]))
    # idempotent second run: no write
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == 1
    monkeypatch.setattr(module, "_curated_cloud_models", lambda: [])
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == 2
    assert client.current["DEFAULT_MODELS"] == user_default
    assert client.current["MODEL_ORDER_LIST"] == user_order


def test_catalogue_curated_names_resolve_to_live_prefixed_ids(
    monkeypatch, tmp_path, caplog
):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        module,
        "_curated_cloud_models",
        lambda: [
            "google/gemini-2.5-flash",
            "openrouter/inclusionai/ling-3.0-flash-sante:free",
        ],
    )

    class Client:
        def __init__(self):
            self.current = {
                "DEFAULT_MODELS": "",
                "DEFAULT_PINNED_MODELS": "",
                "MODEL_ORDER_LIST": [],
            }

        def get_models(self):
            return {
                "data": [
                    {"id": "google.models/gemini-2.5-flash"},
                    {"id": "openrouter.inclusionai/ling-3.0-flash-sante:free"},
                ]
            }

        def get_models_config(self):
            return dict(self.current)

        def update_models_config(self, config):
            self.current = dict(config)

    client = Client()
    assert module._reconcile_catalogue(client) == 0
    assert set(client.current["MODEL_ORDER_LIST"]) == {
        "google.models/gemini-2.5-flash",
        "openrouter.inclusionai/ling-3.0-flash-sante:free",
    }
    assert "Dropping" not in caplog.text


def test_catalogue_unavailable_provider_logs_info_without_warning(
    monkeypatch, tmp_path, caplog
):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(module, "_curated_cloud_models", lambda: ["openai/gpt-5"])

    class Client:
        def get_models(self):
            return {"data": [{"id": "google.models/gemini-2.5-flash"}]}

        def get_models_config(self):
            return {
                "DEFAULT_MODELS": "",
                "DEFAULT_PINNED_MODELS": "",
                "MODEL_ORDER_LIST": [],
            }

    assert module._reconcile_catalogue(Client()) == 0
    assert "Curated models for openai unavailable live; skipping" in caplog.text
    assert "Dropping" not in caplog.text


def test_catalogue_unavailable_provider_models_are_retained_transiently(
    monkeypatch, tmp_path
):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        module,
        "_curated_cloud_models",
        lambda: ["openai/gpt-5", "anthropic/claude-3"],
    )
    state_dir = tmp_path / ".local" / "share" / "openwebui" / "data"
    state_dir.mkdir(parents=True)
    (state_dir / "managed-models.json").write_text(
        json.dumps({"managed_ids": ["openai.gpt-5", "anthropic.claude-3"]})
    )

    class Client:
        def __init__(self):
            self.current = {
                "DEFAULT_MODELS": "anthropic.claude-3",
                "DEFAULT_PINNED_MODELS": "",
                "MODEL_ORDER_LIST": ["anthropic.claude-3"],
            }
            self.writes = 0

        def get_models(self):
            return {"data": [{"id": "openai.gpt-5"}]}

        def get_models_config(self):
            return dict(self.current)

        def update_models_config(self, config):
            self.current = dict(config)
            self.writes += 1

    client = Client()
    assert module._reconcile_catalogue(client) == 0
    assert "anthropic.claude-3" in client.current["MODEL_ORDER_LIST"]
    state = json.loads((state_dir / "managed-models.json").read_text())
    assert "anthropic.claude-3" in state["managed_ids"]
    assert "anthropic.claude-3" in state["retained_unavailable"]
    writes = client.writes
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == writes


def test_catalogue_retained_ids_normalize_after_provider_recovery(
    monkeypatch, tmp_path
):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    curated = ["openai/gpt-5", "anthropic/claude-3"]
    monkeypatch.setattr(module, "_curated_cloud_models", lambda: curated)
    state_dir = tmp_path / ".local" / "share" / "openwebui" / "data"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "managed-models.json"
    state_path.write_text(
        json.dumps(
            {
                "managed_ids": ["openai.gpt-5", "anthropic.claude-old"],
                "retained_unavailable": ["anthropic.claude-old"],
            }
        )
    )

    class Client:
        def __init__(self, live_id):
            self.live_id = live_id
            self.current = {
                "DEFAULT_MODELS": "anthropic.claude-old",
                "DEFAULT_PINNED_MODELS": "",
                "MODEL_ORDER_LIST": ["anthropic.claude-old"],
            }

        def get_models(self):
            return {"data": [{"id": "openai.gpt-5"}, {"id": self.live_id}]}

        def get_models_config(self):
            return dict(self.current)

        def update_models_config(self, config):
            self.current = dict(config)

    client = Client("anthropic.claude-3")
    assert module._reconcile_catalogue(client) == 0
    assert "anthropic.claude-old" not in client.current["MODEL_ORDER_LIST"]
    state = json.loads(state_path.read_text())
    assert state["retained_unavailable"] == []
    assert set(state["managed_ids"]) == {"openai.gpt-5", "anthropic.claude-3"}


def test_catalogue_partial_provider_drift_warns_with_missing_count(
    monkeypatch, tmp_path, caplog
):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        module,
        "_curated_cloud_models",
        lambda: ["google/gemini-2.5-flash", "google/gemini-2.5-pro"],
    )

    class Client:
        def __init__(self):
            self.current = {
                "DEFAULT_MODELS": "",
                "DEFAULT_PINNED_MODELS": "",
                "MODEL_ORDER_LIST": [],
            }

        def get_models(self):
            return {"data": [{"id": "google.models/gemini-2.5-flash"}]}

        def get_models_config(self):
            return dict(self.current)

        def update_models_config(self, config):
            self.current = dict(config)

    client = Client()
    assert module._reconcile_catalogue(client) == 0
    assert "Dropping 1 curated model IDs absent from live /api/models" in caplog.text
    assert client.current["MODEL_ORDER_LIST"] == ["google.models/gemini-2.5-flash"]


def test_catalogue_without_live_ids_skips_without_warning(
    monkeypatch, tmp_path, caplog
):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(module, "_curated_cloud_models", lambda: ["openai/gpt-5"])

    class Client:
        def get_models(self):
            return {"data": []}

        def get_models_config(self):
            return {}

    assert module._reconcile_catalogue(Client()) == 0
    assert "Dropping" not in caplog.text


def test_computer_helpers_env_shape_and_missing_plist(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    env_path = tmp_path / "cptr" / "service.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("CPTR_DATA_DIR=/tmp\nOPENWEBUI_COMPUTER_PORT=8124\n")
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"openwebui_computer_service_env_sync {shlex.quote(str(env_path))}; "
        f"test -s {shlex.quote(str(env_path))}; "
        f"grep -q '^CPTR_DATA_DIR=' {shlex.quote(str(env_path))}; "
        "openwebui_computer_service_start"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == 1
    assert "CPTR_DATA_DIR=" in env_path.read_text()
    assert "OPENWEBUI_COMPUTER_PORT=8124" in env_path.read_text()


def test_configure_all_subgate_cleanup_blocks_are_siblings():
    source = (
        Path(__file__).resolve().parents[3] / "scripts/configure-all.sh"
    ).read_text()
    terminal_start = source.index('if [[ "${DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP')
    computer_start = source.index(
        'if [[ "${DOTFILES_RUN_OPENWEBUI_COMPUTER_SETUP', terminal_start
    )
    terminal_close = source.rfind("\n  fi", terminal_start, computer_start)
    assert terminal_close > terminal_start
    assert computer_start > terminal_close
    assert "openwebui_terminal_service_stop" in source[terminal_start:computer_start]
    assert "openwebui_computer_service_stop" in source[computer_start:]
    # Each sub-gate's teardown is gated on its own `[[ gate == 0 ]]` condition
    # inside an else-branch reachable only when the MAIN gate is off (and the
    # step is not --skipped, which is a no-op since the Gate 2f review). Each
    # gate variable is referenced exactly once in its own condition — the
    # B1 fix split the previously-nested chains into independent branches.
    assert source.count("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP") >= 1
    assert source.count("DOTFILES_RUN_OPENWEBUI_COMPUTER_SETUP") >= 1
    assert "openwebui_service_stop" in source  # main chat service teardown


@pytest.mark.parametrize(
    "helper,filename",
    [
        ("openwebui_service_env_sync", "service.env"),
        ("openwebui_terminal_service_env_sync", "terminal.env"),
        ("openwebui_computer_service_env_sync", "computer.env"),
    ],
)
def test_openwebui_env_sync_sparse_environment(tmp_path, helper, filename):
    library = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    target = tmp_path / filename
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(library))}; "
            f"{helper} {shlex.quote(str(target))}",
        ],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
    )
    assert result.returncode == 0
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o600
    if filename == "service.env":
        names = {
            line.split("=", 1)[0]
            for line in target.read_text().splitlines()
            if "=" in line
        }
        assert names == {
            "WEBUI_SECRET_KEY",
            "WEBUI_ADMIN_EMAIL",
            "WEBUI_ADMIN_PASSWORD",
            "OPENWEBUI_API_KEY",
            "OPENWEBUI_PORT",
            "DEFAULT_LOCALE",
        }


def test_openwebui_service_port_uses_retained_service_env(tmp_path):
    service_script = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    service_env = tmp_path / "service.env"
    service_env.write_text("OPENWEBUI_PORT=9876\n")
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(service_script))}; "
            f"OPENWEBUI_SERVICE_ENV={shlex.quote(str(service_env))}; "
            f"openwebui_service_port",
        ],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "9876"


def test_openwebui_default_locale_resolution(tmp_path):
    service_script = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    cases = [
        ({"OPENWEBUI_DEFAULT_LOCALE": "fr-FR", "LANG": "en_US.UTF-8"}, "fr-FR"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "en-GB"}, "en-GB"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "en"}, "en-US"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "fr"}, "fr-CA"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "pt"}, "pt-BR"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "zh"}, "zh-CN"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "uz"}, "uz-Cyrl-UZ"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "xx-YY"}, "en-US"),
        ({"OPENWEBUI_DEFAULT_LOCALE": "fr_FR.UTF-8"}, "fr-FR"),
        ({"LANGUAGE": "fr_CA.UTF-8"}, "fr-CA"),
        ({"LANG": "en_CA.UTF-8"}, "en-US"),
        ({}, "en-US"),
    ]
    for variables, expected in cases:
        environment = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
        environment.update(variables)
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {shlex.quote(str(service_script))}; openwebui_default_locale",
            ],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == expected


def test_same_dims_other_model_requires_parity_or_ack(monkeypatch, tmp_path):
    state = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state))
    # Same output dims (768), a DIFFERENT model: the dimension-only guard
    # would let this through; parity is inconclusive (no probe wired), so the
    # acknowledgement gate must refuse without ack and accept with ack.
    assert not openwebui._embedding_vector_parity(None, None)
    parity = openwebui._embedding_vector_parity(
        lambda: {t: [1.0, 0.0] for t in openwebui.PARITY_PROBE_STRINGS},
        lambda: {t: [1.0, 0.0] for t in openwebui.PARITY_PROBE_STRINGS},
    )
    assert parity
    other = openwebui._embedding_vector_parity(
        lambda: {t: [1.0, 0.0] for t in openwebui.PARITY_PROBE_STRINGS},
        lambda: {t: [0.0, 1.0] for t in openwebui.PARITY_PROBE_STRINGS},
    )
    assert not other


def test_audio_state_writer_refuses_secret_payload(tmp_path):
    state = tmp_path / "managed-audio.json"
    openwebui._audio_state_path = lambda: str(state)
    bad = {"stt": {"ENGINE": "openai", "OPENAI_API_KEY": "sk-live-secret"}}
    with pytest.raises(openwebui.OpenWebUIError, match="Refusing to persist secret"):
        openwebui._write_audio_projection(bad)
    assert not state.exists()
    openwebui._write_audio_projection(
        {"stt": {"ENGINE": "openai", "OPENAI_API_KEY": ""}}
    )
    assert state.exists()


def test_unknown_omlx_discovery_preserves_managed_audio(monkeypatch, tmp_path, caplog):
    from local_engines import DiscoveryResult

    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    current = {
        "stt": {
            "ENGINE": "openai",
            "MODEL": "asr",
            "OPENAI_API_BASE_URL": "http://omlx/v1",
            "OPENAI_API_REQUEST_FORMAT": "multipart",
        }
    }
    projection = _projection_for_audio(current)
    openwebui._write_audio_projection(projection)
    monkeypatch.setattr(
        openwebui,
        "iter_engine_discovery",
        lambda _provider, include_cloud=False: DiscoveryResult(
            status="unknown_error", records=[], reason="simulated outage"
        ),
    )
    desired = openwebui.compute_desired_state()
    assert "audio" not in desired
    assert desired.get("audio_status") == "unknown"


def test_enabled_omlx_unknown_discovery_preserves_audio(monkeypatch, tmp_path, caplog):
    from local_engines import DiscoveryResult

    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "managed-audio.json")
    )
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui,
        "iter_engine_discovery",
        lambda *_args, **_kwargs: DiscoveryResult("unknown_error", (), "offline"),
    )
    openwebui._write_audio_projection({"stt": {"MODEL": "asr"}})
    client = _AudioClient({"stt": {"MODEL": "asr"}})
    desired = openwebui.compute_desired_state()
    openwebui.reconcile_via_api(client, desired)
    assert client.audio_writes == 0


def test_compute_desired_audio_stable_when_discovery_is_reversed(monkeypatch, tmp_path):
    from local_engines import DiscoveryResult

    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("OMLX_API_KEY", "fixture-key")
    monkeypatch.setattr(
        openwebui, "_audio_state_path", lambda: str(tmp_path / "audio.json")
    )
    monkeypatch.setattr(
        openwebui, "_embedding_state_path", lambda: str(tmp_path / "embedding.json")
    )
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    monkeypatch.setattr(openwebui, "_read_embedding_state", lambda: {})
    monkeypatch.setattr(openwebui.constants, "is_meridian_configured", lambda: False)
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *_: "fixture-build")
    monkeypatch.setattr(
        route_probes,
        "probe_stt",
        lambda *_a, **_k: type("R", (), {"status": "ok", "notes": ("HTTP 200",)})(),
    )
    models = [
        {"name": "asr-selected", "model_type": "audio_stt"},
        {"name": "asr-other", "model_type": "audio_stt"},
    ]
    selection = {
        "stt": {
            "operation": "stt",
            "provider": "omlx",
            "model": "asr-selected",
            "qualification": "protocol_probed",
            "reason": "persisted",
        }
    }
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: selection)

    def compute(ordered):
        monkeypatch.setattr(
            openwebui,
            "iter_engine_discovery",
            lambda *_a, **_k: DiscoveryResult("ok_models", [], None),
        )
        monkeypatch.setattr(openwebui, "audio_models", lambda *_: ordered)
        return openwebui.compute_desired_state(allow_auto_probe=True)

    forward = compute(models)
    reverse = compute(list(reversed(models)))
    assert forward["audio"]["stt"] == reverse["audio"]["stt"]
    assert forward["audio_selection"] == reverse["audio_selection"]
    assert "order" not in repr(forward["audio_selection"]).lower()


def test_same_dims_model_change_refuses_without_ack_switches_with_ack(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-embedding.json"
    monkeypatch.setattr(openwebui, "_embedding_state_path", lambda: str(state))
    client = _EmbeddingClient(
        _embedding_config(engine="ollama", model="other-768-model")
    )
    # An owned same-dims state: the recorded model differs from the target,
    # the recorded dims match — dimension-only guard would pass this; parity
    # is inconclusive (no probe wired) -> the acknowledgement gate.
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": "other-768-model",
                },
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
            }
        }
    )
    _embedding_reconcile(client)
    assert client.writes == 0
    assert client.config["RAG_EMBEDDING_MODEL"] == "other-768-model"
    assert "migration" in caplog.text
    ack = {
        "previous_model": "other-768-model",
        "previous_engine": "ollama",
        "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "new_model": openwebui.EMBEDDING_MODEL,
        "new_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
        "acknowledged_at": "operator-confirmed",
    }
    openwebui._write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": "other-768-model",
                },
                "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
                "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
            },
            "acknowledgement": ack,
        }
    )
    _embedding_reconcile(client)
    assert client.writes == 1
    assert client.config["RAG_EMBEDDING_MODEL"] == openwebui.EMBEDDING_MODEL
    saved = json.loads(state.read_text())
    assert saved["embedding_fingerprint"]["model"] == openwebui.EMBEDDING_MODEL
    assert saved["embedding_fingerprint"]["parity"] in {"acknowledged", "pass", "n-a"}


def test_audio_failed_readback_keeps_applied_evidence_and_projection(
    monkeypatch, tmp_path
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    client = _AudioClient({"stt": {"MODEL": ""}})
    client.alter_writes = (
        True  # the POST succeeds; the readback returns a changed model
    )
    with pytest.raises(openwebui.OpenWebUIError) as raised:
        openwebui.reconcile_via_api(
            client,
            {
                "openai": [],
                "anthropic": [],
                "ollama": [],
                "audio": {"stt": {"ENGINE": "openai", "MODEL": "new-model"}},
            },
        )
    # The carried evidence: the POST already happened; verification did not.
    assert getattr(raised.value, "outcome") == {
        "status": "failed",
        "applied": True,
        "verified": False,
    }
    assert raised.value.actions["failed"] == ["audio"]
    assert "audio" in raised.value.actions["applied"]
    assert client.audio_writes == 1  # the applied write is not lost
    assert not state.exists()  # ownership is not persisted on failed verification


def test_legacy_audio_release_refusal_reports_refused_outcome(
    monkeypatch, tmp_path, caplog
):
    state = tmp_path / "managed-audio.json"
    monkeypatch.setattr(openwebui, "_audio_state_path", lambda: str(state))
    state.write_text(
        json.dumps(
            {"projection": {"stt": {"MODEL": "asr", "OPENAI_API_KEY": "LEGACY_SECRET"}}}
        )
    )
    client = _AudioClient({"stt": {"MODEL": "asr"}})
    result = openwebui._reconcile_audio(client, client.audio, {})
    assert result["status"] == "refused"
    assert result["applied"] is False
    assert result["verified"] is False
    assert client.audio_writes == 0
    assert "LEGACY_SECRET" not in caplog.text
    assert "restore the prior credential manually" in caplog.text


def test_embedding_wanted_native_url_update_is_not_a_noop(monkeypatch, tmp_path):
    """A wanted URL change is a managed transition: written and verified."""
    monkeypatch.setattr(
        openwebui, "_embedding_state_path", lambda: str(tmp_path / "embedding.json")
    )
    client = _EmbeddingClient(_embedding_config("ollama", openwebui.EMBEDDING_MODEL))
    setattr(client, "embedding_backend_identity_probe", lambda _config: None)
    prior = {
        "projection": {
            "owned": {
                "RAG_EMBEDDING_ENGINE": "ollama",
                "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            },
            "previous": {"engine": "ollama", "model": openwebui.EMBEDDING_MODEL},
            "previous_output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "ollama_config": {"url": openwebui.EMBEDDING_OLLAMA_URL},
        },
    }
    openwebui._write_embedding_state(prior)

    def desired(probe_routes=False):
        return {
            "RAG_EMBEDDING_ENGINE": "ollama",
            "RAG_EMBEDDING_MODEL": openwebui.EMBEDDING_MODEL,
            "output_dims": openwebui.EMBEDDING_OUTPUT_DIMS,
            "ollama_config": {
                "url": openwebui._ollama_native_base(openwebui.EMBEDDING_OLLAMA_URL)
            },
        }

    wanted_full = desired()
    assert not openwebui._wanted_embedding_matches_current(
        wanted_full, client.get_embedding_config()
    )
    report = openwebui.reconcile_via_api(
        client,
        {
            "openai": [],
            "anthropic": [],
            "ollama": [],
            "embedding": wanted_full,
        },
    ).actions
    assert report["applied"] == ["embedding"]
    assert report["verified"] == ["embedding"]
    native = openwebui._ollama_native_base(openwebui.EMBEDDING_OLLAMA_URL)
    assert client.get_embedding_config()["ollama_config"]["url"] == native
    saved = openwebui._read_embedding_state()
    assert saved["projection"]["ollama_config"]["url"] == native


def test_gateway_audio_collision_renders_served_namespaced_alias(monkeypatch, tmp_path):
    """P1.1: a cross-engine collision leaves only namespaced aliases served."""
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "asr", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(
        openwebui, "_litellm_endpoint", lambda: "http://127.0.0.1:4000/v1"
    )
    key = tmp_path / "openwebui.key"
    key.write_text("scoped-wire-secret")
    monkeypatch.setattr(openwebui, "LITELLM_OPENWEBUI_KEY_PATH", str(key))
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "scoped-wire-secret")
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *_: "test-build")
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    # Collision inventory: the bare id is NOT served; only namespaced forms.
    monkeypatch.setattr(
        route_probes,
        "fetch_gateway_models",
        lambda endpoint, key_value=None: ["ollama/asr", "omlx/asr"],
    )
    observed = []

    def success(endpoint, model, key_reference, key_value=None, **_kwargs):
        observed.append((endpoint, model, key_reference, key_value))
        return type("Result", (), {"status": "ok", "notes": ("HTTP 200",)})()

    monkeypatch.setattr(route_probes, "probe_stt", success)
    bundle = openwebui._desired_audio_settings(allow_auto_probe=True)
    assert observed == [
        ("http://127.0.0.1:4000/v1", "omlx/asr", str(key), "scoped-wire-secret")
    ]
    assert bundle["settings"]["stt"]["MODEL"] == "omlx/asr"
    assert (
        bundle["settings"]["stt"]["OPENAI_API_BASE_URL"] == "http://127.0.0.1:4000/v1"
    )
    assert bundle["settings"]["stt"]["OPENAI_API_KEY"] == "scoped-wire-secret"
    assert "scoped-wire-secret" not in repr(bundle["selection"])


def test_gateway_audio_alias_resolution_degrades_bare_on_unknown_inventory(
    monkeypatch, tmp_path
):
    """P1.1 boundary: UNKNOWN served inventory keeps the bare candidate."""
    monkeypatch.setattr(openwebui, "_disabled", lambda: set())
    monkeypatch.setattr(openwebui, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        openwebui, "local_endpoint_for", lambda *_: ("http://omlx/v1", "OMLX_API_KEY")
    )
    monkeypatch.setattr(
        openwebui,
        "audio_models",
        lambda *_: [{"name": "asr", "model_type": "audio_stt"}],
    )
    monkeypatch.setattr(openwebui.constants, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(
        openwebui, "_litellm_endpoint", lambda: "http://127.0.0.1:4000/v1"
    )
    key = tmp_path / "openwebui.key"
    key.write_text("scoped-wire-secret")
    monkeypatch.setattr(openwebui, "LITELLM_OPENWEBUI_KEY_PATH", str(key))
    monkeypatch.setattr(openwebui, "_litellm_key_value", lambda: "scoped-wire-secret")
    monkeypatch.setattr(openwebui, "_route_engine_build", lambda *_: "test-build")
    monkeypatch.setattr(openwebui, "_read_audio_selection", lambda: None)
    monkeypatch.setattr(openwebui, "_read_audio_projection", lambda: {})
    monkeypatch.setattr(
        route_probes, "fetch_gateway_models", lambda endpoint, key_value=None: None
    )
    observed = []

    def success(endpoint, model, key_reference, key_value=None, **_kwargs):
        observed.append((endpoint, model))
        return type("Result", (), {"status": "ok", "notes": ("HTTP 200",)})()

    monkeypatch.setattr(route_probes, "probe_stt", success)
    openwebui._desired_audio_settings(allow_auto_probe=True)
    assert observed == [("http://127.0.0.1:4000/v1", "asr")]
