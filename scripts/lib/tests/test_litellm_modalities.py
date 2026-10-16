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
    assert (
        not {"chatterbox-multilingual-v3", "S3TokenizerV2", "MarkItDown"}
        & by_name.keys()
    )
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
