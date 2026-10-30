import json
import os
import tempfile
import unittest
from unittest.mock import patch

import models_dev


class FreeCatalogueSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "cache.json")
        self.raw = {
            "sample": {
                "name": "Sample",
                "models": {
                    "m": {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"]},
                    }
                },
            }
        }

    def test_stale_cache_is_not_fallback(self):
        with open(self.path, "w") as f:
            json.dump(self.raw, f)
        os.utime(self.path, (0, 0))
        with self.assertRaises(models_dev.ModelsDevEvidenceError):
            models_dev.fetch_models_dev_snapshot(
                clock=lambda: 1000000,
                cache_path=self.path,
                fetch=lambda: (_ for _ in ()).throw(OSError()),
            )

    def test_future_cache_rejected_and_fetch_used(self):
        with open(self.path, "w") as f:
            json.dump(self.raw, f)
        os.utime(self.path, (2000, 2000))
        result = models_dev.fetch_models_dev_snapshot(
            clock=lambda: 1000, cache_path=self.path, fetch=lambda: self.raw
        )
        self.assertEqual(result["models"], self.raw)

    def test_fresh_cache_envelope_and_raw_fields(self):
        with open(self.path, "w") as f:
            json.dump(self.raw, f)
        os.utime(self.path, (1000, 1000))
        result = models_dev.fetch_models_dev_snapshot(
            clock=lambda: 1100, cache_path=self.path
        )
        self.assertEqual(result["models"], self.raw)
        self.assertEqual(result["source"], "models.dev")
        self.assertEqual(result["observed_at"], "1970-01-01T00:16:40+00:00")
        self.assertEqual(result["models"]["sample"]["models"]["m"]["reasoning"], True)
        self.assertIn("expires_at", result)

    def test_successful_fetch_is_observed_after_parse(self):
        ticks = iter((1000, 1200))
        result = models_dev.fetch_models_dev_snapshot(
            clock=lambda: next(ticks), cache_path=self.path, fetch=lambda: self.raw
        )
        self.assertEqual(result["observed_at"], "1970-01-01T00:20:00+00:00")
        self.assertFalse(os.path.exists(self.path))

    def test_malformed_fetch_and_failure_do_not_write(self):
        with self.assertRaises(models_dev.ModelsDevEvidenceError):
            models_dev.fetch_models_dev_snapshot(
                clock=lambda: 100, cache_path=self.path, fetch=lambda: {"bad": []}
            )
        self.assertFalse(os.path.exists(self.path))
        with patch("models_dev.urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(models_dev.ModelsDevEvidenceError):
                models_dev.fetch_models_dev_snapshot(
                    clock=lambda: 100, cache_path=self.path
                )
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
