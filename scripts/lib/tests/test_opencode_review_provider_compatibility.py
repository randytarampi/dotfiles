"""Run the actual CI reviewer model builder with fake credentials and no network."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _model_builder():
    data = yaml.safe_load((ROOT / ".github/workflows/agentic-review.yml").read_text())
    step = next(
        item
        for item in data["jobs"]["opencode"]["steps"]
        if item.get("name") == "Configure read-only OpenCode v2 review agent"
    )
    return step["run"].split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _generate(tmp_path, requested, **keys):
    source = tmp_path / "trusted/configs/opencode/ci/opencode.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        (ROOT / "configs/opencode/ci/opencode.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    output = tmp_path / "outputs"
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "TRUSTED_DOTFILES": str(tmp_path / "trusted"),
        "GITHUB_ENV": str(tmp_path / "environment"),
        "GITHUB_OUTPUT": str(output),
        "REVIEW_INFERENCE_PROVIDER": requested,
        "REVIEW_ALLOW_NVIDIA": "false",
        "HAS_GROQ_KEY": "false",
        "HAS_MISTRAL_KEY": "false",
        "HAS_CLOUDFLARE_KEY": "false",
        "HAS_NVIDIA_NIM_KEY": "false",
        "HAS_OPENCODE_KEY": "false",
        "CLOUDFLARE_ACCOUNT_ID": "",
    }
    env.update(keys)
    result = subprocess.run(
        ["python3", "-c", _model_builder()],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return result, None, {}, None
    config = json.loads((tmp_path / "opencode-review-config.json").read_text())
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    proxy = tmp_path / "review-free-litellm.json"
    return (
        result,
        config,
        outputs,
        json.loads(proxy.read_text()) if proxy.exists() else None,
    )


def _assert_registered(config, provider, model, base_url, credential):
    assert "provider" not in config
    modern = config["providers"][provider]
    assert modern["env"] == [credential]
    assert modern["package"] == "aisdk:@ai-sdk/openai-compatible"
    assert modern["settings"] == {
        "baseURL": base_url,
        "apiKey": "{env:" + credential + "}",
    }
    assert model in modern["models"]


def test_default_free_pool_registers_only_v2_provider(tmp_path):
    result, config, outputs, proxy = _generate(
        tmp_path, "free", HAS_MISTRAL_KEY="true", HAS_OPENCODE_KEY="true"
    )
    assert result.returncode == 0, result.stderr
    assert outputs["selected_provider"] == "review-free"
    assert outputs["model"] == "review-free/review-free"
    _assert_registered(
        config,
        "review-free",
        "review-free",
        "http://127.0.0.1:4907/v1",
        "REVIEW_LITELLM_KEY",
    )
    assert [item["litellm_params"]["order"] for item in proxy["model_list"]] == [1, 2]
    assert {item["model_name"] for item in proxy["model_list"]} == {"review-free"}
    assert all(
        item["litellm_params"]["api_key"].startswith("os.environ/")
        for item in proxy["model_list"]
    )
    assert config["mcp"] == {}
    assert config["plugins"] == []
    assert config["default_agent"] == "review"
    assert config["agents"]["review"]["mode"] == "primary"
    assert config["permissions"] == [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "glob", "resource": "*", "effect": "allow"},
        {"action": "grep", "resource": "*", "effect": "allow"},
    ]
    assert config["agents"]["review"]["permissions"] == config["permissions"]


@pytest.mark.parametrize(
    ("provider", "credential", "model", "base_url"),
    [
        (
            "mistral",
            "MISTRAL_API_KEY",
            "mistral-small-latest",
            "https://api.mistral.ai/v1",
        ),
        (
            "groq",
            "GROQ_API_KEY",
            "openai/gpt-oss-120b",
            "https://api.groq.com/openai/v1",
        ),
    ],
)
def test_direct_provider_requests_are_v2_only(
    tmp_path, provider, credential, model, base_url
):
    result, config, outputs, proxy = _generate(
        tmp_path, provider, **{"HAS_" + provider.upper() + "_KEY": "true"}
    )
    assert result.returncode == 0, result.stderr
    assert outputs["model"] == provider + "/" + model
    _assert_registered(config, provider, model, base_url, credential)
    assert proxy is None


def test_only_one_free_provider_uses_direct_route(tmp_path):
    result, config, outputs, proxy = _generate(tmp_path, "free", HAS_GROQ_KEY="true")
    assert result.returncode == 0, result.stderr
    assert outputs["model"] == "groq/openai/gpt-oss-120b"
    _assert_registered(
        config,
        "groq",
        "openai/gpt-oss-120b",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
    )
    assert proxy is None


def test_no_keys_preserves_builtin_opencode_route(tmp_path):
    result, config, outputs, proxy = _generate(tmp_path, "free")
    assert result.returncode == 0, result.stderr
    assert outputs["model"] == "opencode/big-pickle"
    assert "review-free" not in config.get("providers", {})
    assert proxy is None


def test_nvidia_requires_independent_approval(tmp_path):
    result, config, _, proxy = _generate(
        tmp_path, "nvidia-nim", HAS_NVIDIA_NIM_KEY="true"
    )
    assert result.returncode != 0
    assert "requires explicit development-use approval" in result.stderr
    assert config is None and proxy is None
