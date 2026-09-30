import os
import shlex
import subprocess
from pathlib import Path

import pytest

import litellm_config


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(
            (
                "DOTFILES_",
                "OPENAI_",
                "ANTHROPIC_",
                "GEMINI_",
                "OPENROUTER_",
                "MERIDIAN_",
                "OMLX_",
            )
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(litellm_config, "active_engines", lambda: [])
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: [])


def test_cloud_keys_and_meridian_are_conditional(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("MERIDIAN_API_KEY", "meridian")
    entries = litellm_config.compute_model_list()
    aliases = {entry["model_name"] for entry in entries}
    assert "openai/default" in aliases
    assert any(
        entry["litellm_params"].get("model") == "openai/gpt-6-luna" for entry in entries
    )
    assert "anthropic/default" not in aliases
    monkeypatch.setattr(litellm_config, "is_meridian_configured", lambda: True)
    assert "meridian/claude-sonnet-5-5" in {
        entry["model_name"] for entry in litellm_config.compute_model_list()
    }


@pytest.mark.parametrize("catalogue", [["m1", "m2"], []])
def test_live_catalogue_entries_and_fallback(monkeypatch, catalogue):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "or-" + "stub")
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: catalogue)

    entries = litellm_config.compute_model_list()
    aliases = {entry["model_name"]: entry for entry in entries}

    if catalogue:
        assert {"openrouter/m1", "openrouter/m2"} <= aliases.keys()
        assert aliases["openrouter/m1"]["litellm_params"]["api_key"] == (
            "os.environ/OPENROUTER_API_KEY"
        )
        assert "api_base" in aliases["openrouter/m1"]["litellm_params"]
    else:
        assert "openrouter/default" in aliases


def test_live_catalogue_failure_aborts_model_list(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "or-" + "stub")
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: None)
    with pytest.raises(litellm_config.LiveCatalogueError):
        litellm_config.compute_model_list()


def test_local_registry_models_use_protocol_specific_entries(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx", "ollama"])
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: [{"name": "model-a" if provider == "omlx" else "model-b"}],
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda provider, protocol: (
            ("http://127.0.0.1:8000/v1", None) if provider == "omlx" else None
        ),
    )
    aliases = {
        entry["model_name"]: entry for entry in litellm_config.compute_model_list()
    }
    assert aliases["omlx/model-a"]["litellm_params"]["model"] == "openai/model-a"
    assert aliases["ollama/model-b"]["litellm_params"]["model"] == "ollama/model-b"


def test_render_uses_environment_references_and_no_inline_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-render")
    rendered = litellm_config.render_config()
    assert "do-not-render" not in rendered
    assert "api_key: os.environ/OPENAI_API_KEY" in rendered
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in rendered


def test_render_is_idempotent(tmp_path):
    path = tmp_path / "config.yaml"
    assert litellm_config.write_config(path, {}) is True
    assert litellm_config.write_config(path, {}) is False


def test_litellm_service_missing_plist_fails_hermetically(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; litellm_service_start"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == 1


def test_litellm_env_sync_in_sparse_environment(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
    )
    assert result.returncode == 0
    assert env_path.is_file()
    assert env_path.stat().st_mode & 0o777 == 0o600
    env_text = env_path.read_text(encoding="utf-8")
    names = {line.split("=", 1)[0] for line in env_text.splitlines() if "=" in line}
    assert "DISABLE_ADMIN_UI" in names


def test_litellm_env_sync_disable_admin_ui_override(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_path.parent.mkdir(parents=True)

    def sync(extra_env, target):
        script = (
            f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
            f"source {shlex.quote(str(helper))}; "
            f"litellm_service_env_sync {shlex.quote(str(env_path))}"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                "HOME": str(tmp_path),
                "PATH": os.environ.get("PATH", ""),
                **extra_env,
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        return dict(
            line.split("=", 1)
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )

    assert sync({}, None)["DISABLE_ADMIN_UI"] == "True"
    assert (
        sync({"LITELLM_DISABLE_ADMIN_UI": "False"}, None)["DISABLE_ADMIN_UI"] == "False"
    )
    assert sync({}, None)["DISABLE_ADMIN_UI"] == "True"


@pytest.mark.parametrize("openssl_body", ["return 1", ":"])
def test_litellm_master_key_generation_rejects_failure_or_empty(tmp_path, openssl_body):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "service.env"
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"openssl() {{ {openssl_body}; }}; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == 1
    assert not env_path.exists()


def test_litellm_keyed_local_and_meridian_shapes(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        litellm_config, "iter_engine_models", lambda _: [{"name": "local"}]
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *_: ("http://local/v1", "OMLX_API_KEY"),
    )
    monkeypatch.setattr(litellm_config, "is_meridian_configured", lambda: True)
    monkeypatch.setenv("MERIDIAN_API_KEY", "meridian")
    entries = litellm_config.compute_model_list()
    assert (
        any(
            item["litellm_params"].get("api_key") == "os.environ/OMLX_API_KEY"
            for item in entries
        )
        is False
    )
    monkeypatch.setenv("OMLX_API_KEY", "omlx")
    keyed = litellm_config.compute_model_list()
    assert any(
        item["litellm_params"].get("api_key") == "os.environ/OMLX_API_KEY"
        for item in keyed
    )
    assert any(item["model_name"] == "meridian/claude-sonnet-5-5" for item in keyed)


def test_google_uses_native_adapter(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    entries = litellm_config.compute_model_list()
    google = next(item for item in entries if item["model_name"] == "google/default")
    assert google["litellm_params"]["model"].startswith("gemini/")
    assert "api_base" not in google["litellm_params"]
