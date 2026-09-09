#!/usr/bin/env python3
"""Tests for Ollama capability/metadata parsing in models_dev.py.

Covers get_ollama_show_info (context length + capabilities parsing) and
get_ollama_modalities (OpenCode modalities resolution for local and
:cloud-stub models). Regression context: OpenCode rejects image
attachments client-side for provider entries without declared
`modalities`, even when the backend accepts images.
"""

import unittest
from unittest.mock import patch

import models_dev

SHOW_OUTPUT_FLASH = """\
Model
  architecture     glm4_moe
  parameters       320B
  quantization      Q4_K_M
Capabilities
  completion
  thinking
  tools
  vision
context length      1048576
"""

SHOW_OUTPUT_TEXT_ONLY = """\
Model
  architecture     glm4_moe
Capabilities
  completion
  thinking
  tools
context length      1000000
"""

SHOW_OUTPUT_VISION_AUDIO = """\
Model
  architecture     gemma3
Capabilities
  completion
  vision
  audio
  tools
context length      262144
"""


class GetOllamaShowInfoTest(unittest.TestCase):
    def _run_with(self, stdout):
        def fake_run(cmd, capture_output, text, timeout):
            return type("R", (), {"returncode": 0, "stdout": stdout})()

        return fake_run

    def test_parses_context_length_and_capabilities(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=self._run_with(SHOW_OUTPUT_FLASH),
        ):
            info = models_dev.get_ollama_show_info("glm-5.3-flash:cloud")
        self.assertEqual(info["context_length"], 1048576)
        self.assertEqual(
            info["capabilities"], {"completion", "thinking", "tools", "vision"}
        )

    def test_parses_text_only_model(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=self._run_with(SHOW_OUTPUT_TEXT_ONLY),
        ):
            info = models_dev.get_ollama_show_info("glm-5.3:cloud")
        self.assertEqual(info["context_length"], 1000000)
        self.assertNotIn("vision", info["capabilities"])

    def test_capability_block_ends_at_non_capability_line(self):
        # A capability token appearing after the Capabilities block ended
        # (blank line, section header, context length) must not be picked up.
        # Base here has tools REMOVED from the real block; a stray "tools"
        # line trails the context-length line with no active block.
        output = SHOW_OUTPUT_TEXT_ONLY.replace("  tools\n", "").replace(
            "context length      1000000",
            "context length      1000000\ntools",
        )
        with patch.object(
            models_dev_subprocess, "run", side_effect=self._run_with(output)
        ):
            info = models_dev.get_ollama_show_info("m:cloud")
        self.assertNotIn("tools", info["capabilities"])
        self.assertEqual(info["capabilities"], {"completion", "thinking"})

    def test_failure_returns_empty(self):
        def failing_run(cmd, capture_output, text, timeout):
            return type("R", (), {"returncode": 1, "stdout": ""})()

        with patch.object(models_dev_subprocess, "run", side_effect=failing_run):
            info = models_dev.get_ollama_show_info("nope")
        self.assertIsNone(info["context_length"])
        self.assertEqual(info["capabilities"], set())


class GetOllamaModalitiesTest(unittest.TestCase):
    def test_cloud_stub_uses_models_dev_catalog(self):
        catalog = {
            "ollama-cloud": {
                "models": {
                    "glm-5.3-flash": {
                        "modalities": {"input": ["text", "image"], "output": ["text"]}
                    }
                }
            }
        }
        # Even if `ollama show` omits vision (under-reporting for cloud stubs),
        # the catalog lookup wins for :cloud-suffixed IDs.
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=self._empty_run(),
        ):
            result = models_dev.get_ollama_modalities("glm-5.3-flash:cloud", catalog)
        self.assertEqual(result, {"input": ["text", "image"], "output": ["text"]})

    def test_local_vision_model_from_ollama_show(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=self._empty_run(SHOW_OUTPUT_VISION_AUDIO),
        ):
            result = models_dev.get_ollama_modalities("gemma4:12b", {})
        self.assertEqual(
            result, {"input": ["text", "image", "audio"], "output": ["text"]}
        )

    def test_text_only_model_returns_none(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=self._empty_run(SHOW_OUTPUT_TEXT_ONLY),
        ):
            self.assertIsNone(models_dev.get_ollama_modalities("m:cloud", {}))

    def test_cloud_stub_without_catalog_falls_back_to_show(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=self._empty_run(SHOW_OUTPUT_FLASH),
        ):
            result = models_dev.get_ollama_modalities("unknown:cloud", {})
        self.assertEqual(result, {"input": ["text", "image"], "output": ["text"]})

    def _empty_run(self, stdout=""):
        def fake_run(cmd, capture_output, text, timeout):
            return type("R", (), {"returncode": 0, "stdout": stdout})()

        return fake_run


import subprocess as models_dev_subprocess  # noqa: E402

import models_dev  # noqa: E402

if __name__ == "__main__":
    unittest.main()
