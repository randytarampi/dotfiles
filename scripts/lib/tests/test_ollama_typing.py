import local_engines
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_ollama_show_for_unrelated_tests(monkeypatch, request):
    if request.module.__name__ == __name__:
        return
    if request.module.__name__.endswith("test_omlx_lane_b"):
        monkeypatch.setitem(
            local_engines.LOCAL_ENGINES["ollama"], "metadata_lookup", None
        )
        return
    monkeypatch.setitem(
        local_engines.LOCAL_ENGINES["ollama"],
        "metadata_lookup",
        lambda name: {"capabilities": ["completion"], "model_type": "llm"},
    )


def test_ollama_show_capabilities_type_embedding(monkeypatch):
    metadata = {
        "capabilities": ["completion", "embedding", "vision", "audio"],
        "model_type": "llm",
    }
    assert local_engines.LOCAL_ENGINES["ollama"]["metadata_capabilities"](metadata) == {
        "model_type": "embedding",
        "primary_category": "embedding",
    }


def test_ollama_metadata_lookup_uses_show_endpoint(monkeypatch):
    import json
    from io import BytesIO
    from unittest.mock import patch

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    response = Response(json.dumps({"capabilities": ["embedding"]}).encode())
    monkeypatch.setattr(
        local_engines, "get_ollama_local_base_url", lambda: "http://ollama"
    )
    local_engines._OLLAMA_SHOW_CACHE.clear()
    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        metadata = local_engines._ollama_metadata("embeddinggemma:tag")
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/api/show")
    assert json.loads(request.data) == {"model": "embeddinggemma:tag"}
    assert local_engines._ollama_capabilities(metadata)["model_type"] == "embedding"


def test_ollama_plain_llm_and_unknown_remain_untyped():
    assert local_engines._ollama_capabilities({"capabilities": ["completion"]}) == {}
    assert local_engines._ollama_capabilities({"capabilities": []}) == {}


def test_ollama_show_404_fails_closed(monkeypatch, caplog):
    import urllib.error

    monkeypatch.setattr(
        local_engines, "get_ollama_local_base_url", lambda: "http://ollama"
    )
    local_engines._OLLAMA_SHOW_CACHE.clear()
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://ollama/api/show", 404, "Not Found", None, None
            ),
        ),
        caplog.at_level("WARNING"),
    ):
        metadata = local_engines._ollama_metadata("missing:latest")
    assert metadata == {}
    assert "WARNING" not in caplog.text


def test_ollama_reranker_vocabulary():
    assert local_engines._ollama_capabilities({"capabilities": ["reranking"]}) == {
        "model_type": "reranker",
        "primary_category": "reranker",
    }


def test_typing_hook_is_registered_only_for_ollama():
    assert (
        local_engines.LOCAL_ENGINES["ollama"]["metadata_capabilities"]
        is local_engines._ollama_capabilities
    )


def test_details_chain_types_ollama_embedding_through_hook(monkeypatch):
    import json
    from io import BytesIO
    from unittest.mock import patch

    import tier_resolve

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    payload = json.dumps(
        {
            "capabilities": ["embedding"],
            "details": {
                "parameter_size": "744.37M",
                "quantization_level": "mxfp8",
            },
            "model_info": {
                "general.architecture": "embedding_gemma2",
                "embedding_gemma2.embedding_length": 512,
                "embedding_gemma2.context_length": 262144,
            },
        }
    ).encode()
    monkeypatch.setattr(
        local_engines, "get_ollama_local_base_url", lambda: "http://ollama/v1"
    )
    local_engines._OLLAMA_SHOW_CACHE.clear()
    with patch("urllib.request.urlopen", return_value=Response(payload)):
        details = tier_resolve.get_model_details(
            "typing-e2e:740m-mxfp8", provider="ollama"
        )
    assert details["model_type"] == "embedding"
    assert "embedding" in details["capabilities"]
    assert details["architecture"] == "embedding_gemma2"
    assert details["embedding_length"] == 512
    assert details["context_length"] == 262144
    assert details["quantization"] == "mxfp8"
    assert details["is_moe"] is False
    # Repeated lookup must be served from the non-empty-only cache (no fetch).
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("fetch happened again")
    ):
        cached = tier_resolve.get_model_details(
            "typing-e2e:740m-mxfp8", provider="ollama"
        )
    assert cached["model_type"] == "embedding"


def test_details_chain_disabled_hook_stays_untyped(monkeypatch):
    import tier_resolve
    from unittest.mock import patch

    monkeypatch.setattr(
        local_engines, "get_ollama_local_base_url", lambda: "http://ollama/v1"
    )
    local_engines._OLLAMA_SHOW_CACHE.clear()
    with patch("urllib.request.urlopen", side_effect=OSError("unavailable")):
        details = tier_resolve.get_model_details(
            "typing-e2e:740m-mxfp8", provider="ollama"
        )
    assert details["model_type"] is None
    assert details["capabilities"] == []


def test_dense_moe_details_preserved_through_production_hook(monkeypatch):
    import json
    from io import BytesIO
    import tier_resolve

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    # Real Ollama /api/show schema: numeric parameter count in model_info,
    # size/quantization strings under details (verified upstream
    # ShowResponse/ModelDetails).
    payload = json.dumps(
        {
            "capabilities": ["completion", "tools"],
            "details": {
                "parameter_size": "27.0B",
                "quantization_level": "Q4_K_M",
            },
            "model_info": {
                "general.architecture": "qwen_moe",
                "general.parameter_count": 27000000000,
                "qwen_moe.context_length": 32768,
                "qwen_moe.embedding_length": 2048,
            },
        }
    ).encode()
    monkeypatch.setattr(
        local_engines, "get_ollama_local_base_url", lambda: "http://ollama"
    )
    local_engines._OLLAMA_SHOW_CACHE.clear()
    with patch("urllib.request.urlopen", return_value=Response(payload)):
        details = tier_resolve.get_model_details("dense-chat", provider="ollama")
    assert details["architecture"] == "qwen_moe"
    assert details["is_moe"] is True
    assert details["param_count"] == 27
    assert details["context_length"] == 32768
    assert details["embedding_length"] == 2048
    assert details["quantization"] == "Q4_K_M"
    assert details["capabilities"] == ["completion", "tools"]
