#!/usr/bin/env python3
"""Tests for Ollama capability/metadata parsing in models_dev.py.

Covers get_ollama_show_info (context length + capabilities parsing),
get_ollama_modalities (OpenCode modalities resolution), and model entry
modality passthrough.

OpenCode rejects image attachments client-side for provider entries without
declared `modalities`, so catalog-advertised cloud modalities are preserved.
"""

import subprocess as models_dev_subprocess
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


def _run_with(stdout):
    def fake_run(cmd, capture_output, text, timeout):
        return type("R", (), {"returncode": 0, "stdout": stdout})()

    return fake_run


class GetOllamaShowInfoTest(unittest.TestCase):
    def test_parses_context_length_and_capabilities(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=_run_with(SHOW_OUTPUT_FLASH),
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
            side_effect=_run_with(SHOW_OUTPUT_TEXT_ONLY),
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
        with patch.object(models_dev_subprocess, "run", side_effect=_run_with(output)):
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
    def test_cloud_id_uses_models_dev_catalog(self):
        catalog = {
            "ollama-cloud": {
                "models": {
                    "glm-5.3-flash": {
                        "modalities": {"input": ["text", "image"], "output": ["text"]}
                    }
                }
            }
        }
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=_run_with(SHOW_OUTPUT_FLASH),
        ):
            result = models_dev.get_ollama_modalities("glm-5.3-flash:cloud", catalog)
        self.assertEqual(result, {"input": ["text", "image"], "output": ["text"]})

    def test_local_vision_model_from_ollama_show(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=_run_with(SHOW_OUTPUT_VISION_AUDIO),
        ):
            result = models_dev.get_ollama_modalities("gemma4:12b", {})
        self.assertEqual(
            result, {"input": ["text", "image", "audio"], "output": ["text"]}
        )

    def test_text_only_model_returns_none(self):
        with patch.object(
            models_dev_subprocess,
            "run",
            side_effect=_run_with(SHOW_OUTPUT_TEXT_ONLY),
        ):
            self.assertIsNone(models_dev.get_ollama_modalities("qwen3.5:9b", {}))


class BuildModelEntryModalityTest(unittest.TestCase):
    CATALOG = {
        "ollama-cloud": {
            "models": {
                "glm-5.3-flash": {
                    "modalities": {"input": ["text", "image"], "output": ["text"]}
                }
            }
        },
        "openai": {
            "models": {
                "gpt-test": {
                    "modalities": {"input": ["text", "image"], "output": ["text"]}
                }
            }
        },
    }

    def test_ollama_cloud_provider_keeps_catalog_modalities(self):
        entry = models_dev.build_model_entry(
            "glm-5.3-flash", self.CATALOG, "ollama-cloud"
        )
        self.assertEqual(
            entry["modalities"], {"input": ["text", "image"], "output": ["text"]}
        )

    def test_other_providers_keep_catalog_modalities(self):
        entry = models_dev.build_model_entry("gpt-test", self.CATALOG, "openai")
        # Non-ollama-cloud provider keys pass catalog modalities through.
        self.assertEqual(
            entry["modalities"], {"input": ["text", "image"], "output": ["text"]}
        )


if __name__ == "__main__":
    unittest.main()
