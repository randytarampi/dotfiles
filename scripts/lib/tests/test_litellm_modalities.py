from unittest.mock import patch

import litellm_config


def test_discovered_modalities_are_exact_id_and_contained_from_chat():
    discovered = [
        {"name": "Qwen3-ASR-1.7B-8bit", "model_type": "audio_stt"},
        {"name": "whisper-large-v3-turbo-asr-fp16", "model_type": "audio_stt"},
        {"name": "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit", "model_type": "audio_tts"},
        {"name": "chatterbox-multilingual-v3", "model_type": "audio_tts"},
        {"name": "S3TokenizerV2", "model_type": "tokenizer"},
        {"name": "MarkItDown", "model_type": "conversion"},
        {"name": "vectors", "model_type": "embedding"},
        {"name": "ranker", "model_type": "rerank"},
        {"name": "chat-model", "model_type": "llm"},
    ]
    with (
        patch.object(litellm_config, "active_engines", return_value=["omlx"]),
        patch.object(litellm_config, "iter_engine_models", return_value=discovered),
        patch.object(
            litellm_config,
            "local_endpoint_for",
            return_value=("http://127.0.0.1:11427/v1", "OMLX_API_KEY"),
        ),
        patch.object(litellm_config, "list_cloud_ollama_models", return_value=[]),
    ):
        generated = litellm_config.compute_model_list({"OMLX_API_KEY": "key"})
        chat_fixture = litellm_config.compute_model_list({"OMLX_API_KEY": "key"})

    by_name = {entry["model_name"]: entry for entry in generated}
    modalities = {
        "Qwen3-ASR-1.7B-8bit",
        "whisper-large-v3-turbo-asr-fp16",
        "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
        "vectors",
        "ranker",
    }
    assert modalities <= by_name.keys()
    assert not {"chatterbox-multilingual-v3", "S3TokenizerV2"} & by_name.keys()
    assert by_name["MarkItDown"]["litellm_params"] == {
        "model": "openai/MarkItDown",
        "api_base": "http://127.0.0.1:11427/v1",
        "api_key": "os.environ/OMLX_API_KEY",
    }
    for name in modalities:
        assert by_name[name]["litellm_params"] == {
            "model": f"openai/{name}",
            "api_base": "http://127.0.0.1:11427/v1",
            "api_key": "os.environ/OMLX_API_KEY",
        }
    assert "chat-model" in {entry["model_name"] for entry in chat_fixture}
    # Typed modality records do not acquire qualified chat aliases.
    assert not {f"omlx/{name}" for name in modalities} & by_name.keys()
    names = [entry["model_name"] for entry in generated]
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_reranker_type_routes_through_omlx_normalization():
    import omlx

    models_payload = {"data": [{"id": "rank", "max_model_len": 4096}]}
    status_payload = {"models": [{"id": "rank", "model_type": "reranker"}]}
    omlx._STATUS_CACHE.clear()
    with (
        patch.object(omlx, "_get_json", side_effect=[models_payload, status_payload]),
        patch.object(litellm_config, "active_engines", return_value=["omlx"]),
        patch.object(
            litellm_config,
            "iter_engine_models",
            side_effect=lambda provider, include_cloud=False: omlx.list_omlx_models(),
        ),
        patch.object(
            litellm_config,
            "local_endpoint_for",
            return_value=("http://127.0.0.1:11427/v1", "OMLX_API_KEY"),
        ),
    ):
        generated = litellm_config._modality_entries({"OMLX_API_KEY": "key"})
    names = {entry["model_name"] for entry in generated}
    assert "rank" in names
    entry = next(e for e in generated if e["model_name"] == "rank")
    assert entry["litellm_params"] == {
        "model": "openai/rank",
        "api_base": "http://127.0.0.1:11427/v1",
        "api_key": "os.environ/OMLX_API_KEY",
    }


def test_markitdown_word_and_s3tokenizer_guard(monkeypatch):
    import omlx
    from local_engines import _ollama_metadata  # noqa: F401  (import sanity)

    models_payload = {
        "data": [
            {"id": "MarkItDown", "max_model_len": None},
            {"id": "mlx-community--S3TokenizerV2", "max_model_len": 32768},
        ]
    }
    # The REAL status payload types MarkItDown ("markitdown" — the producer's
    # established word) and MIS-types the S3 tokenizer as "llm" (engine_type
    # "batched", unloaded — upstream oMLX mapping). Chat admission for the
    # mis-typed record is blocked by the name-level guard only.)
    omlx._STATUS_CACHE.clear()
    with (
        # The status index is cached once; subsequent discovery passes fetch
        # only /v1/models.
        patch.object(
            omlx,
            "_get_json",
            side_effect=[
                models_payload,
                {
                    "models": [
                        {"id": "MarkItDown", "model_type": "markitdown"},
                        {
                            "id": "mlx-community--S3TokenizerV2",
                            "model_type": "llm",
                            "engine_type": "batched",
                        },
                    ]
                },
                models_payload,
                models_payload,
                models_payload,
                models_payload,
            ],
        ),
        patch.object(litellm_config, "active_engines", return_value=["omlx"]),
        patch.object(
            litellm_config,
            "iter_engine_models",
            side_effect=lambda provider, include_cloud=False: omlx.list_omlx_models(),
        ),
        patch.object(
            litellm_config,
            "local_endpoint_for",
            return_value=("http://127.0.0.1:11427/v1", "OMLX_API_KEY"),
        ),
    ):
        generated = litellm_config.compute_model_list({"OMLX_API_KEY": "key"})
    names = {entry["model_name"] for entry in generated}
    assert "MarkItDown" in names  # exact-id conversion alias via the producer word
    assert not any("S3TokenizerV2" in name for name in names)
