import json
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


if __name__ == "__main__":
    unittest.main()
