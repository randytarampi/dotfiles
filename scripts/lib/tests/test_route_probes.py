import io
import json
import math
import sys
import unittest
from email.message import Message
from unittest.mock import patch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

import route_probes
from selected_service import Qualification


class FakeResponse:
    def __init__(self, status=200, body=b"", content_type="application/json"):
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class RouteProbeTests(unittest.TestCase):
    def call(self, response, function, *args, **kwargs):
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=response
        ) as opened:
            result = function("http://engine/v1", "model", *args, **kwargs)
        return result, opened.call_args.args[0]

    def test_stt_ok_and_endpoint(self):
        result, request = self.call(
            FakeResponse(body=b'{"text":"hello"}'), route_probes.probe_stt, "KEY_REF"
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(request.full_url, "http://engine/v1/audio/transcriptions")
        self.assertIn(b"probe.wav", request.data)

    def test_tts_binary(self):
        result, _ = self.call(
            FakeResponse(body=b"audio", content_type="audio/wav"),
            route_probes.probe_tts,
            key_reference="KEY_REF",
        )
        self.assertEqual(result.status, "ok")

    def test_tts_engine_dispatch_keeps_key_reference_out_of_voice(self):
        import local_engines

        with (
            patch.object(
                local_engines,
                "local_endpoint_for",
                return_value=("http://engine/v1", "TTS_KEY"),
            ),
            patch.object(
                route_probes.urllib.request,
                "urlopen",
                return_value=FakeResponse(body=b"audio", content_type="audio/wav"),
            ) as opened,
        ):
            result = local_engines.probe_engine_model(
                "omlx", "model", "tts", key_value="synthetic-key", timeout=9
            )
        self.assertEqual(result.status, "ok")
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data)["voice"], "alloy")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-key")
        self.assertEqual(opened.call_args.kwargs["timeout"], 9)

    def test_tts_positional_credentials_and_keyword_voice(self):
        result, request = self.call(
            FakeResponse(body=b"audio", content_type="audio/wav"),
            route_probes.probe_tts,
            "TTS_KEY",
            "synthetic-key",
            9,
            voice="serena",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(json.loads(request.data)["voice"], "serena")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-key")

    def test_stt_empty_text_is_valid_shape(self):
        result, _ = self.call(
            FakeResponse(body=b'{"text":"","language":"en","segments":[]}'),
            route_probes.probe_stt,
            "KEY_REF",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            route_probes.qualification_from_probe(result),
            Qualification.PROTOCOL_PROBED,
        )

    def test_stt_missing_text_field_is_shape_error(self):
        result, _ = self.call(
            FakeResponse(body=b'{"language":"en"}'), route_probes.probe_stt, "KEY_REF"
        )
        self.assertEqual(result.status, "response_shape_error")

    def test_tts_voice_retry_from_server_error(self):
        error = FakeResponse(
            status=500,
            body=(
                b'{"error":{"message":"Speaker \'alloy\' not supported. '
                b"Available: ['serena', 'vivian']\","
                b'"type":"server_error","param":null,"code":null}}'
            ),
            content_type="application/json",
        )
        audio = FakeResponse(body=b"audio", content_type="audio/wav")
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=[error, audio],
        ) as opened:
            result = route_probes.probe_tts(
                "http://engine/v1", "model", key_reference="KEY_REF"
            )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.notes, ("server-recommended voice discovered: serena",))
        second = json.loads(opened.call_args_list[1].args[0].data)
        self.assertEqual(second["voice"], "serena")

    def test_tts_non_voice_error_not_retried(self):
        error = FakeResponse(
            status=500,
            body=b'{"error":{"message":"No conditionals available."}}',
            content_type="application/json",
        )
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=[error],
        ):
            result = route_probes.probe_tts(
                "http://engine/v1", "chatterbox", key_reference="KEY_REF"
            )
        self.assertEqual(result.status, "unavailable")

    def test_embedding_vector_dimension(self):
        result, request = self.call(
            FakeResponse(body=b'{"data":[{"embedding":[0.1,0.2]}]}'),
            route_probes.probe_embeddings,
            "KEY_REF",
        )
        self.assertEqual(result.dimension, 2)
        self.assertIn(b"deterministic seed", request.data)

    def test_auth_errors_unknown_and_not_shape_failure(self):
        for code, auth in ((401, "unauthorized"), (403, "forbidden")):
            result, _ = self.call(
                FakeResponse(status=code),
                route_probes.probe_embeddings,
                "KEY_REF",
                key_value="SENTINEL_SECRET",
            )
            self.assertEqual(result.status, "unknown")
            self.assertEqual(result.auth_failure, auth)
            self.assertNotEqual(
                route_probes.qualification_from_probe(result),
                Qualification.PROTOCOL_PROBED,
            )

    def test_status_result_preserves_auth_http_codes(self):
        for code, auth in ((401, "unauthorized"), (403, "forbidden")):
            result = route_probes._status_result(code, "model", "http://engine")
            self.assertEqual(result.auth_failure, auth)
            self.assertEqual(result.http_status, code)

    def test_bad_shape_and_unreachable_mapping(self):
        result, _ = self.call(
            FakeResponse(body=b'{"data":[]}'), route_probes.probe_embeddings, "KEY_REF"
        )
        self.assertEqual(
            route_probes.qualification_from_probe(result), Qualification.UNAVAILABLE
        )
        with patch.object(
            route_probes.urllib.request, "urlopen", side_effect=TimeoutError
        ):
            result = route_probes.probe_embeddings(
                "http://engine/v1", "model", "KEY_REF"
            )
        self.assertEqual(
            route_probes.qualification_from_probe(result), Qualification.UNKNOWN
        )

    def test_no_key_leak_and_resolution_helpers(self):
        sentinel = "SENTINEL_SECRET"
        result, request = self.call(
            FakeResponse(status=401),
            route_probes.probe_stt,
            "KEY_REF",
            key_value=sentinel,
        )
        self.assertNotIn(sentinel, repr(result))
        self.assertEqual(request.get_header("Authorization"), "Bearer " + sentinel)
        self.assertEqual(
            route_probes.litellm_proxy_endpoint_for(), "http://127.0.0.1:4000/v1"
        )
        self.assertIsNone(route_probes.litellm_proxy_endpoint_for(False))
        self.assertEqual(
            route_probes.qualification_from_probe(
                route_probes.ProbeResult("ok", "m", "m", "e")
            ),
            Qualification.PROTOCOL_PROBED,
        )

    def test_fetch_gateway_models_inventory_and_degradation(self):
        ok = FakeResponse(
            body=json.dumps({"data": [{"id": "b"}, {"id": "a"}]}).encode()
        )
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=ok
        ) as opened:
            ids = route_probes.fetch_gateway_models(
                "http://gateway:4000/v1", "wire-key"
            )
        self.assertEqual(ids, ["a", "b"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://gateway:4000/v1/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + "wire-key")
        cases = [
            FakeResponse(body=b"<html>"),
            FakeResponse(body=b'{"data": "not-a-list"}'),
        ]
        for response in cases:
            with patch.object(
                route_probes.urllib.request, "urlopen", return_value=response
            ):
                self.assertIsNone(
                    route_probes.fetch_gateway_models("http://gateway:4000/v1", "k")
                )
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=route_probes.urllib.error.URLError("no route"),
        ):
            self.assertIsNone(
                route_probes.fetch_gateway_models("http://gateway:4000/v1", "k")
            )
        self.assertIsNone(route_probes.fetch_gateway_models("ftp://gateway/v1", "k"))


class H0EmbeddingCandidateTests(unittest.TestCase):
    """Read-only tests for the H0 embedding candidate resolver (issue #85)."""

    def test_validate_embedding_vector_finite_nonzero(self):
        vec = [0.1, -0.3, 0.5, 0.2]
        finite, nonzero, norm = route_probes._validate_embedding_vector(vec)
        self.assertTrue(finite)
        self.assertTrue(nonzero)
        self.assertAlmostEqual(
            norm, math.sqrt(0.1**2 + 0.3**2 + 0.5**2 + 0.2**2), delta=1e-6
        )

    def test_validate_embedding_vector_nan(self):
        vec = [0.1, float("nan"), 0.5]
        finite, _, norm = route_probes._validate_embedding_vector(vec)
        self.assertFalse(finite)
        self.assertIsNone(norm)

    def test_validate_embedding_vector_inf(self):
        vec = [0.1, float("inf"), 0.5]
        finite, _, norm = route_probes._validate_embedding_vector(vec)
        self.assertFalse(finite)
        self.assertIsNone(norm)

    def test_validate_embedding_vector_all_zeros(self):
        vec = [0.0, 0.0, 0.0]
        finite, nonzero, norm = route_probes._validate_embedding_vector(vec)
        self.assertTrue(finite)
        self.assertFalse(nonzero)
        self.assertAlmostEqual(norm, 0.0)

    def test_validate_embedding_vector_rejects_bad_values_without_crashing(self):
        for vector in (
            [True],
            [None],
            ["1"],
            [float("nan")],
            [float("inf")],
            [1.7e308, 1.7e308],
        ):
            with self.subTest(vector=vector):
                finite, nonzero, norm = route_probes._validate_embedding_vector(vector)
                self.assertFalse(finite)
                self.assertIsNone(norm)

    def test_native_ollama_embed_uses_normalized_api_root_and_wire_shape(self):
        response = FakeResponse(body=b'{"model":"m","embeddings":[[0.5,0.25]]}')
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=response
        ) as opened:
            obs = route_probes.probe_embedding_vector(
                "http://localhost:11434/v1", "m", native_ollama=True
            )
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/embed")
        self.assertEqual(
            json.loads(request.data),
            {"model": "m", "input": "route probe deterministic seed"},
        )
        self.assertEqual(obs.outcome, "qualified")

    def test_native_show_uses_real_capabilities_and_tags_digest(self):
        tags = FakeResponse(body=b'{"models":[{"name":"m","digest":"sha256:abc"}]}')
        show = FakeResponse(
            body=b'{"capabilities":["completion","embedding"],"model_info":{"general.architecture":"x"}}'
        )
        embed = FakeResponse(body=b'{"model":"m","embeddings":[[1.0]]}')
        with patch.object(
            route_probes.urllib.request, "urlopen", side_effect=[tags, show, embed]
        ) as opened:
            obs = route_probes.discover_embedding_candidates(
                ollama_url="http://localhost:11434/api"
            )
        self.assertEqual(
            [call.args[0].full_url for call in opened.call_args_list],
            [
                "http://localhost:11434/api/tags",
                "http://localhost:11434/api/show",
                "http://localhost:11434/api/embed",
            ],
        )
        self.assertEqual(
            json.loads(opened.call_args_list[1].args[0].data), {"model": "m"}
        )
        self.assertEqual(obs[0].digest, "sha256:abc")

    def test_ambiguous_gateway_route_does_not_post_embeddings_by_default(self):
        inventory = FakeResponse(body=b'{"data":[{"id":"ollama/m"}]}')
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=inventory
        ) as opened:
            obs = route_probes.discover_embedding_candidates(
                gateway_url="https://gateway.example/v1"
            )
        self.assertEqual(len(opened.call_args_list), 1)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].outcome, "unknown")

    def test_gateway_inference_requires_explicit_permission(self):
        inventory = FakeResponse(body=b'{"data":[{"id":"ollama/m"}]}')
        embedding = FakeResponse(body=b'{"data":[{"embedding":[1.0]}]}')
        with patch.object(
            route_probes.urllib.request, "urlopen", side_effect=[inventory, embedding]
        ) as opened:
            obs = route_probes.discover_embedding_candidates(
                gateway_url="http://127.0.0.1:4000/v1",
                allow_gateway_inference=True,
                gateway_embedding_aliases=["ollama/m"],
            )
        self.assertEqual(
            opened.call_args_list[1].args[0].full_url,
            "http://127.0.0.1:4000/v1/embeddings",
        )
        self.assertEqual(obs[0].outcome, "qualified")

    def test_loopback_gateway_without_permission_is_unknown_without_post(self):
        inventory = FakeResponse(body=b'{"data":[{"id":"ollama/m"}]}')
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=inventory
        ) as opened:
            obs = route_probes.discover_embedding_candidates(
                gateway_url="http://127.0.0.1:4000/v1"
            )
        self.assertEqual(len(opened.call_args_list), 1)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].outcome, "unknown")

    def test_gateway_failure_is_unknown_but_empty_is_empty(self):
        with patch.object(
            route_probes.urllib.request, "urlopen", side_effect=TimeoutError
        ):
            failed = route_probes.discover_embedding_candidates(
                gateway_url="http://gateway.example/v1"
            )
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].outcome, "unknown")
        empty = FakeResponse(body=b'{"data":[]}')
        with patch.object(route_probes.urllib.request, "urlopen", return_value=empty):
            self.assertEqual(
                route_probes.discover_embedding_candidates(
                    gateway_url="http://gateway.example/v1"
                ),
                [],
            )

    def test_gateway_model_label_is_not_a_digest(self):
        response = FakeResponse(
            body=b'{"data":[{"embedding":[1.0]}],"model":"ollama/m"}'
        )
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=response
        ):
            obs = route_probes.probe_embedding_vector(
                "https://gateway.example/v1", "ollama/m", native_ollama=False
            )
        self.assertIsNone(obs.digest)

    def test_native_protocol_requires_explicit_selection_and_gateway_root_is_normalized(
        self,
    ):
        response = FakeResponse(body=b'{"data":[{"embedding":[1.0]}]}')
        with patch.object(
            route_probes.urllib.request, "urlopen", return_value=response
        ) as opened:
            route_probes.probe_embedding_vector("http://localhost:11434/v1", "m")
        self.assertEqual(
            opened.call_args.args[0].full_url, "http://localhost:11434/v1/embeddings"
        )
        self.assertEqual(
            route_probes._openai_root("http://gateway/api"), "http://gateway/v1"
        )

    def test_vector_huge_finite_values_are_supported_and_overflow_is_unknown(self):
        self.assertTrue(route_probes._validate_embedding_vector([1e200])[0])
        self.assertFalse(route_probes._validate_embedding_vector([1.7e308, 1.7e308])[0])
        self.assertFalse(route_probes._validate_embedding_vector([10**10000])[0])

    def test_gateway_empty_inventory_is_distinct_from_failed_inventory(self):
        empty = FakeResponse(body=b'{"data":[]}')
        with patch.object(route_probes.urllib.request, "urlopen", return_value=empty):
            self.assertEqual(route_probes.fetch_gateway_models("http://gateway/v1"), [])
        with patch.object(
            route_probes.urllib.request, "urlopen", side_effect=TimeoutError
        ):
            self.assertIsNone(route_probes.fetch_gateway_models("http://gateway/v1"))

    def test_probe_embedding_vector_success(self):
        body = json.dumps(
            {"data": [{"embedding": [0.1, 0.2, 0.3]}], "model": "test-model"}
        ).encode()
        ok = FakeResponse(status=200, body=body)
        with patch.object(route_probes.urllib.request, "urlopen", return_value=ok):
            obs = route_probes.probe_embedding_vector(
                "http://ollama:11434/v1", "test-model", None, timeout=5
            )
        self.assertEqual(obs.outcome, "qualified")
        self.assertEqual(obs.qualification, Qualification.PROTOCOL_PROBED.value)
        self.assertEqual(obs.dimension, 3)
        self.assertTrue(obs.vector_finite)
        self.assertTrue(obs.vector_nonzero)
        self.assertIsNotNone(obs.vector_norm)
        self.assertIsNone(obs.digest)

    def test_probe_embedding_vector_unavailable_http(self):
        err = route_probes.urllib.error.HTTPError(
            "http://x/v1/embeddings", 503, "unavail", {}, io.BytesIO(b"")
        )
        with patch.object(route_probes.urllib.request, "urlopen", side_effect=err):
            obs = route_probes.probe_embedding_vector(
                "http://ollama:11434/v1", "test-model", None, timeout=5
            )
        self.assertEqual(obs.outcome, "unavailable")
        self.assertEqual(obs.qualification, Qualification.UNAVAILABLE.value)
        self.assertIsNone(obs.dimension)

    def test_probe_embedding_vector_connection_failure(self):
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            obs = route_probes.probe_embedding_vector(
                "http://ollama:11434/v1", "test-model", None, timeout=5
            )
        self.assertEqual(obs.outcome, "unavailable")
        self.assertEqual(obs.qualification, Qualification.UNAVAILABLE.value)

    def test_probe_embedding_vector_shape_error(self):
        body = json.dumps({"data": [{"embedding": "not-a-list"}]}).encode()
        ok = FakeResponse(status=200, body=body)
        with patch.object(route_probes.urllib.request, "urlopen", return_value=ok):
            obs = route_probes.probe_embedding_vector(
                "http://ollama:11434/v1", "test-model", None, timeout=5
            )
        self.assertEqual(obs.outcome, "unknown")
        self.assertEqual(obs.qualification, Qualification.UNKNOWN.value)

    def test_fetch_ollama_tags_returns_names(self):
        body = json.dumps(
            {"models": [{"name": "b-model"}, {"name": "a-model"}]}
        ).encode()
        ok = FakeResponse(status=200, body=body)
        with patch.object(route_probes.urllib.request, "urlopen", return_value=ok):
            tags = route_probes._fetch_ollama_tags(
                "http://localhost:11434/api", None, timeout=5
            )
        self.assertEqual(tags, ["a-model", "b-model"])

    def test_fetch_ollama_tags_url_failure_returns_none(self):
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=TimeoutError("no route"),
        ):
            self.assertIsNone(
                route_probes._fetch_ollama_tags(
                    "http://localhost:11434/api", None, timeout=5
                )
            )

    def test_fetch_ollama_show_returns_details(self):
        body = json.dumps(
            {
                "modelinfo": {
                    "embedding": True,
                    "name": "embeddinggemma-2:740m-mxfp8",
                }
            }
        ).encode()
        ok = FakeResponse(status=200, body=body)
        with patch.object(route_probes.urllib.request, "urlopen", return_value=ok):
            details = route_probes._fetch_ollama_show(
                "http://localhost:11434/api", "embeddinggemma", None, timeout=5
            )
        self.assertIsNotNone(details)
        assert details is not None  # for LSP narrowing
        self.assertTrue(details["modelinfo"]["embedding"])

    def test_discover_embedding_candidates_ollama(self):
        """Ollama discovery: non-embedding models are unavailable, embedding models are probed."""
        tags_body = json.dumps(
            {"models": [{"name": "text-embed"}, {"name": "chat-model"}]}
        ).encode()
        # _fetch_ollama_tags returns sorted names, so "chat-model" is processed first
        show_chat = json.dumps({"capabilities": ["completion"]}).encode()
        show_embed = json.dumps({"capabilities": ["completion", "embedding"]}).encode()
        embed_probe_body = json.dumps(
            {"model": "text-embed", "embeddings": [[0.1, 0.2, 0.3]]}
        ).encode()

        responses = iter(
            [
                FakeResponse(200, tags_body),  # /api/tags
                FakeResponse(200, show_chat),  # /api/show chat-model (first after sort)
                FakeResponse(200, show_embed),  # /api/show text-embed
                FakeResponse(200, embed_probe_body),  # /api/embed text-embed
            ]
        )
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=lambda *a, **k: next(responses),
        ):
            obs = route_probes.discover_embedding_candidates(
                ollama_url="http://localhost:11434/v1"
            )

        self.assertEqual(len(obs), 2)
        by_alias = {o.wire_alias: o for o in obs}
        self.assertIn("text-embed", by_alias)
        self.assertEqual(by_alias["text-embed"].outcome, "qualified")
        self.assertEqual(by_alias["text-embed"].provider, "ollama")
        self.assertEqual(by_alias["text-embed"].dimension, 3)
        self.assertIn("chat-model", by_alias)
        self.assertEqual(by_alias["chat-model"].outcome, "unavailable")

    def test_discover_embedding_candidates_no_urls_returns_empty(self):
        self.assertEqual(
            route_probes.discover_embedding_candidates(),
            [],
        )

    def test_discover_embedding_candidates_gateway(self):
        """Gateway discovery: gateway models are probed for embedding support."""
        models_body = json.dumps(
            {"data": [{"id": "ollama/embeddinggemma"}, {"id": "chat-model"}]}
        ).encode()
        probe_body = json.dumps(
            {"data": [{"embedding": [0.5, 0.1, 0.2]}], "model": "ollama/embeddinggemma"}
        ).encode()

        responses = iter(
            [
                FakeResponse(200, models_body),  # /v1/models
                FakeResponse(200, probe_body),  # /embeddings for ollama/embeddinggemma
            ]
        )
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            side_effect=lambda *a, **k: next(responses),
        ):
            obs = route_probes.discover_embedding_candidates(
                gateway_url="http://127.0.0.1:4000/v1",
                allow_gateway_inference=True,
                gateway_embedding_aliases=["ollama/embeddinggemma"],
            )

        # Only the explicitly selected alias is probed.
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].wire_alias, "ollama/embeddinggemma")
        self.assertEqual(obs[0].outcome, "qualified")
        self.assertEqual(obs[0].provider, "gateway")
        self.assertEqual(obs[0].dimension, 3)

    def test_malformed_inventory_is_unknown_not_empty(self):
        for body in (b'{"data":[{}]}', b'{"data":[{"id":42}]}'):
            with patch.object(
                route_probes.urllib.request,
                "urlopen",
                return_value=FakeResponse(body=body),
            ):
                self.assertIsNone(route_probes.fetch_gateway_models("http://gateway"))
        with patch.object(
            route_probes.urllib.request,
            "urlopen",
            return_value=FakeResponse(body=b'{"models":[{}]}'),
        ):
            self.assertIsNone(route_probes._fetch_ollama_tag_records("http://ollama"))

    def test_missing_native_capabilities_is_unknown(self):
        with (
            patch.object(
                route_probes, "_fetch_ollama_tag_records", return_value=[("m", None)]
            ),
            patch.object(
                route_probes, "_fetch_ollama_show", return_value={"model_info": {}}
            ),
        ):
            observations = route_probes.discover_embedding_candidates(
                ollama_url="http://ollama"
            )
        self.assertEqual(observations[0].outcome, "unknown")

    def test_arbitrary_exact_gateway_embedding_alias(self):
        with (
            patch.object(
                route_probes,
                "fetch_gateway_models",
                return_value=["custom/embed", "ollama/chat"],
            ),
            patch.object(
                route_probes,
                "probe_embedding_vector",
                return_value=route_probes.EmbeddingObservation(
                    provider="unknown",
                    wire_alias="custom/embed",
                    backend_endpoint="http://gateway/v1/embeddings",
                    outcome="qualified",
                ),
            ) as probe,
        ):
            observations = route_probes.discover_embedding_candidates(
                gateway_url="http://gateway",
                allow_gateway_inference=True,
                gateway_embedding_aliases=["custom/embed"],
            )
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.args[1], "custom/embed")
        self.assertEqual(observations[0].wire_alias, "custom/embed")


if __name__ == "__main__":
    unittest.main()
