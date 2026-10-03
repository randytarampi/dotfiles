import os
import json
import shlex
import subprocess
import urllib.error
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

import litellm_config

CONFIGURE_SPEC = spec_from_file_location(
    "configure_litellm", Path(__file__).resolve().parents[2] / "configure-litellm.py"
)
CONFIGURE = module_from_spec(CONFIGURE_SPEC)
CONFIGURE_SPEC.loader.exec_module(CONFIGURE)
PI_SPEC = spec_from_file_location(
    "configure_pi", Path(__file__).resolve().parents[2] / "configure-pi.py"
)
PI = module_from_spec(PI_SPEC)
PI_SPEC.loader.exec_module(PI)


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
                "OPENCODE_",
                "OLLAMA_",
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
    assert (
        aliases["model-a"]["litellm_params"]
        == aliases["omlx/model-a"]["litellm_params"]
    )
    assert (
        aliases["model-b"]["litellm_params"]
        == aliases["ollama/model-b"]["litellm_params"]
    )


def test_ollama_cloud_stubs_are_routed_through_local_daemon_without_duplicates(
    monkeypatch,
):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["ollama"])
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: [
            {"name": "glm-5.3:cloud"},
            {"name": "qwen3-cloud"},
            {"name": "local-model"},
        ],
    )
    monkeypatch.setattr(
        litellm_config,
        "list_cloud_ollama_models",
        lambda: [{"name": "glm-5.3:cloud"}, {"name": "deepseek-cloud"}],
    )
    monkeypatch.setattr(
        litellm_config, "get_ollama_local_base_url", lambda: "http://127.0.0.1:11434"
    )

    entries = litellm_config.compute_model_list()
    aliases = [entry["model_name"] for entry in entries]
    by_alias = {entry["model_name"]: entry for entry in entries}

    assert aliases.count("ollama/glm-5.3:cloud") == 1
    assert aliases.count("glm-5.3:cloud") == 1
    assert {
        "ollama/qwen3-cloud",
        "qwen3-cloud",
        "ollama/deepseek-cloud",
        "deepseek-cloud",
    } <= set(aliases)
    assert {"ollama/local-model", "local-model"} <= set(aliases)
    assert by_alias["ollama/glm-5.3:cloud"]["litellm_params"] == {
        "model": "ollama/glm-5.3:cloud",
        "api_base": "http://127.0.0.1:11434",
    }
    assert (
        by_alias["glm-5.3:cloud"]["litellm_params"]
        == by_alias["ollama/glm-5.3:cloud"]["litellm_params"]
    )


def test_model_list_and_config_are_stable_across_discovery_order(tmp_path, monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx", "ollama"])
    discovered = {
        "omlx": ["shared", "omlx-zeta", "omlx-alpha", "omlx-alpha"],
        "ollama": ["shared-cloud", "local-zeta", "shared", "shared-cloud"],
    }
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: [{"name": name} for name in discovered[provider]],
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda provider, protocol: (
            ("http://127.0.0.1:8000/v1", "OMLX_API_KEY") if provider == "omlx" else None
        ),
    )
    cloud_models = [{"name": name} for name in ("shared-cloud", "cloud-alpha")]
    monkeypatch.setattr(
        litellm_config, "list_cloud_ollama_models", lambda: cloud_models
    )
    monkeypatch.setattr(
        litellm_config,
        "get_ollama_local_base_url",
        lambda: "http://127.0.0.1:11434/v1",
    )
    catalogue = ["router-alpha", "router-zeta"]
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: catalogue)
    environ = {
        "LITELLM_PORT": "4400",
        "OMLX_API_KEY": "test-omlx-key",
        "OPENROUTER_API_KEY": "test-openrouter-key",
    }

    first_entries = litellm_config.compute_model_list(environ)
    first_config = litellm_config.render_config(environ, first_entries)
    config_path = tmp_path / "config.yaml"
    assert litellm_config.write_config(config_path, environ, first_entries) is True
    first_bytes = config_path.read_bytes()

    discovered["omlx"].reverse()
    discovered["ollama"].reverse()
    cloud_models.reverse()
    second_entries = litellm_config.compute_model_list(environ)
    second_config = litellm_config.render_config(environ, second_entries)
    assert second_entries == first_entries
    assert second_config == first_config
    assert litellm_config.write_config(config_path, environ, second_entries) is False
    assert config_path.read_bytes() == first_bytes

    aliases = [entry["model_name"] for entry in second_entries]
    assert aliases[:8] == [
        "omlx/omlx-alpha",
        "omlx-alpha",
        "omlx/omlx-alpha",
        "omlx-alpha",
        "omlx/omlx-zeta",
        "omlx-zeta",
        "omlx/shared",
        "shared",
    ]
    assert aliases[8:18] == [
        "ollama/cloud-alpha",
        "cloud-alpha",
        "ollama/local-zeta",
        "local-zeta",
        "ollama/shared",
        "shared",
        "ollama/shared-cloud",
        "shared-cloud",
        "openrouter/router-alpha",
        "openrouter/router-zeta",
    ]
    assert aliases.count("omlx/omlx-alpha") == 2
    assert aliases.count("omlx-alpha") == 2
    by_alias = {entry["model_name"]: entry for entry in second_entries}
    assert by_alias["omlx/shared"]["litellm_params"] == {
        "model": "openai/shared",
        "api_base": "http://127.0.0.1:8000/v1",
        "api_key": "os.environ/OMLX_API_KEY",
    }
    assert by_alias["ollama/shared-cloud"]["litellm_params"] == {
        "model": "ollama/shared-cloud",
        "api_base": "http://127.0.0.1:11434",
    }
    assert aliases.index("shared") == 7
    assert aliases.index("shared", 8) == 13


def test_app_key_provisioning_is_alias_idempotent_and_mode_600(tmp_path, monkeypatch):
    calls = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        calls.append((url, method, payload))
        if "/key/list" in url:
            return {"keys": [{"key_alias": "opencode"}]}
        if url.endswith("/key/generate"):
            return {"key": f"sk-generated-{payload['key_alias']}"}
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    path = tmp_path / "service.env"
    path.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")

    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")

    assert [c[2]["key_alias"] for c in calls if c[1] == "POST"] == [
        "pi",
        "openwebui",
        "junie",
    ]
    content = path.read_text(encoding="utf-8")
    assert "LITELLM_PI_KEY=sk-generated-pi" in content
    assert "LITELLM_OPENCODE_KEY" not in content
    assert path.stat().st_mode & 0o777 == 0o600


def test_opencode_key_file_is_private_atomic_idempotent_and_rotatable(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_OPENCODE_KEY='dummy-one'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    target = tmp_path / "clients" / "opencode.key"
    assert target.read_text(encoding="utf-8") == "dummy-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_opencode_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_OPENCODE_KEY='dummy-two'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-two"
    assert target.stat().st_ino != inode


def test_opencode_key_file_missing_key_and_symlink_fail_closed(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_PI_KEY='dummy'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    assert not (tmp_path / "clients").exists()
    target_dir = tmp_path / "clients"
    target_dir.mkdir()
    target = target_dir / "opencode.key"
    target.symlink_to(tmp_path / "outside")
    service.write_text("LITELLM_OPENCODE_KEY='dummy'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    assert target.is_symlink()
    assert not (tmp_path / "outside").exists()


def test_junie_key_file_is_idempotent_rotatable_and_symlink_safe(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_JUNIE_KEY='dummy-junie-one'\n", encoding="utf-8")
    CONFIGURE._write_junie_key(service)
    target = tmp_path / "clients/junie.key"
    assert target.read_text(encoding="utf-8") == "dummy-junie-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_junie_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_JUNIE_KEY='dummy-junie-two'\n", encoding="utf-8")
    CONFIGURE._write_junie_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-junie-two"
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    CONFIGURE._write_junie_key(service)
    assert target.is_symlink()
    assert not (tmp_path / "outside").exists()


def test_pi_key_file_is_private_idempotent_and_rotatable(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_PI_KEY='dummy-one'\n", encoding="utf-8")
    CONFIGURE._write_pi_key(service)
    target = tmp_path / "clients" / "pi.key"
    assert target.read_text(encoding="utf-8") == "dummy-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_pi_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_PI_KEY='dummy-two'\n", encoding="utf-8")
    CONFIGURE._write_pi_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-two"
    assert target.stat().st_ino != inode


def test_pi_provider_override_requires_private_file_and_uses_command_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_PI_USE_LITELLM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    providers = {
        name: {"baseUrl": f"https://{name}.example/v1", "apiKey": "direct"}
        for name in ("openai", "ollama", "omlx")
    }
    with pytest.raises(RuntimeError):
        PI.apply_litellm_provider_overrides(providers)
    assert all(
        provider["baseUrl"].startswith("https://") for provider in providers.values()
    )
    target = tmp_path / ".local/share/litellm/clients/pi.key"
    target.parent.mkdir(parents=True)
    target.write_text("dummy", encoding="utf-8")
    target.parent.chmod(0o700)
    target.chmod(0o600)
    PI.apply_litellm_provider_overrides(providers)
    assert all(p["baseUrl"] == "http://127.0.0.1:4000/v1" for p in providers.values())
    assert all(
        p["apiKey"] == f"!cat {shlex.quote(str(target))}" for p in providers.values()
    )


def test_pi_provider_override_rejects_symlinked_key_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_PI_USE_LITELLM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    directory = tmp_path / ".local/share/litellm/clients"
    directory.mkdir(parents=True)
    (directory / "pi.key").symlink_to(tmp_path / "outside")
    providers = {"openai": {"baseUrl": "https://direct/v1", "apiKey": "direct"}}
    with pytest.raises(RuntimeError):
        PI.apply_litellm_provider_overrides(providers)


def test_existing_alias_recreates_file_from_service_env_without_generation(
    tmp_path, monkeypatch
):
    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {"keys": [{"key_alias": alias} for alias in CONFIGURE.APP_KEYS]}
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    service = tmp_path / "service.env"
    service.write_text(
        "LITELLM_OPENCODE_KEY='dummy-existing'\n"
        "LITELLM_JUNIE_KEY='dummy-junie-existing'\n",
        encoding="utf-8",
    )
    CONFIGURE.provision_app_keys("dummy-master", service, "http://127.0.0.1:4000")
    assert (tmp_path / "clients/opencode.key").read_text(encoding="utf-8") == (
        "dummy-existing"
    )
    junie_key = tmp_path / "clients/junie.key"
    assert junie_key.read_text(encoding="utf-8") == "dummy-junie-existing"
    assert junie_key.parent.stat().st_mode & 0o777 == 0o700
    assert junie_key.stat().st_mode & 0o777 == 0o600


def test_app_key_provisioning_treats_400_generate_as_alias_exists(
    tmp_path, monkeypatch
):
    """Proxy versions where /key/list hides key_alias: a 400 on generate
    means the alias already exists — provisioning stays idempotent."""
    generate_attempts = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {"keys": []}
        if url.endswith("/key/generate"):
            alias = payload["key_alias"]
            generate_attempts.append(alias)
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    path = tmp_path / "service.env"
    path.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")

    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")

    assert len(generate_attempts) == len(CONFIGURE.APP_KEYS)
    # A 400 is not persisted as a key; the service env keeps its prior state.
    assert not any(
        name in path.read_text(encoding="utf-8") for name in CONFIGURE.APP_KEYS.values()
    )


def test_app_key_provisioning_defers_on_unexpected_generate_errors(
    tmp_path, monkeypatch
):
    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {"keys": []}
        if url.endswith("/key/generate"):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    path = tmp_path / "service.env"
    path.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")

    # Returns cleanly (the outer handler warns and defers), does not raise.
    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")
    assert not any(
        name in path.read_text(encoding="utf-8") for name in CONFIGURE.APP_KEYS.values()
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/unexpected",
        "https://127.0.0.1:4000/key/list",
        "http://example.test:4000/key/list",
    ],
)
def test_litellm_provisioning_rejects_non_loopback_or_non_http_urls(url, monkeypatch):
    monkeypatch.setattr(
        CONFIGURE.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("unsafe URL must not be opened"),
    )
    with pytest.raises(ValueError, match="restricted to loopback HTTP"):
        CONFIGURE._request_json(url, "GET", "master")


def test_render_uses_environment_references_and_no_inline_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-render")
    rendered = litellm_config.render_config()
    assert "do-not-render" not in rendered
    assert "api_key: os.environ/OPENAI_API_KEY" in rendered
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in rendered
    assert "database_url: os.environ/DATABASE_URL" in rendered


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


def test_litellm_env_sync_preserves_service_managed_app_keys(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "LITELLM_MASTER_KEY='sk-test-master-key'\n"
        "LITELLM_OPENCODE_KEY='sk-open-code'\n"
        "LITELLM_PI_KEY='sk-pi'\n"
        "LITELLM_OPENWEBUI_KEY='sk-webui'\n"
        "LITELLM_JUNIE_KEY='sk-junie'\n",
        encoding="utf-8",
    )
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0
    values = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert values["LITELLM_OPENCODE_KEY"] == "sk-open-code"
    assert values["LITELLM_PI_KEY"] == "sk-pi"
    assert values["LITELLM_OPENWEBUI_KEY"] == "sk-webui"
    assert values["LITELLM_JUNIE_KEY"] == "sk-junie"
    assert env_path.stat().st_mode & 0o777 == 0o600


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
    keyed_by_name = {item["model_name"]: item for item in keyed}
    assert keyed_by_name["omlx/local"]["litellm_params"]["api_key"] == (
        "os.environ/OMLX_API_KEY"
    )
    assert (
        keyed_by_name["local"]["litellm_params"]
        == keyed_by_name["omlx/local"]["litellm_params"]
    )
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


@pytest.mark.parametrize(
    "env_name, provider, model, api_base",
    [
        ("OPENAI_API_KEY", "openai", "openai/gpt-6-luna", "https://api.openai.com/v1"),
        (
            "ANTHROPIC_API_KEY",
            "anthropic",
            "anthropic/claude-sonnet-5-5",
            "https://api.anthropic.com",
        ),
        (
            "OPENROUTER_API_KEY",
            "openrouter",
            "openrouter/openai/gpt-4o",
            "https://openrouter.ai/api/v1",
        ),
        (
            "OPENCODE_API_KEY",
            "opencode",
            "openai/gpt-6-luna",
            "https://opencode.ai/zen/v1",
        ),
        (
            "OLLAMA_API_KEY",
            "ollama-cloud",
            "openai/gpt-oss:120b",
            "https://ollama.com/v1",
        ),
    ],
)
def test_cloud_provider_routes_use_recorded_upstreams(
    monkeypatch, env_name, provider, model, api_base
):
    # Split-concat keeps the literal out of trufflehog Lob's key-shaped string
    # matching (precedent: test_litellm.py's sk- stub fixtures).
    monkeypatch.setenv(env_name, "test-" + "key")
    entry = next(
        item
        for item in litellm_config.compute_model_list()
        if item["model_name"] == f"{provider}/default"
    )
    params = entry["litellm_params"]
    assert params["model"] == model
    assert params["api_base"] == api_base
    assert params["api_key"] == f"os.environ/{env_name}"


@pytest.mark.parametrize(
    "entry",
    [
        {
            "model_name": "bad",
            "litellm_params": {"api_base": "http://127.0.0.1:4000/v1"},
        },
        {"model_name": "bad", "litellm_params": {"api_base": "~/.mozart/mozart.json"}},
        {"model_name": "bad", "litellm_params": {"model": "mozart-router/default"}},
    ],
)
def test_render_rejects_mozart_and_self_routing(entry):
    with pytest.raises(litellm_config.RoutingInvariantError):
        litellm_config.render_config(entries=[entry])


@pytest.mark.parametrize(
    "entry",
    [
        {
            "model_name": "openai/wolf.mozart-v1",
            "litellm_params": {"model": "openai/wolf.mozart-v1"},
        },
        {
            "model_name": "alias-mozart-router-v1",
            "litellm_params": {"model": "openai/ordinary-model"},
        },
    ],
)
def test_render_allows_legitimate_substrings(entry):
    assert "model_list:" in litellm_config.render_config(entries=[entry])


def test_generator_uses_litellm_port_override():
    entry = {
        "model_name": "bad",
        "litellm_params": {"api_base": "http://127.0.0.1:4100/v1"},
    }
    with pytest.raises(litellm_config.RoutingInvariantError):
        litellm_config.render_config(environ={"LITELLM_PORT": "4100"}, entries=[entry])


def test_mozart_template_has_no_litellm_gateway():
    template = Path(__file__).resolve().parents[3] / "configs/mozart-router/mozart.json"
    config = json.loads(template.read_text(encoding="utf-8"))
    assert all(
        "127.0.0.1:4000" not in json.dumps(gateway)
        for gateway in config.get("gateways", {}).values()
    )


def _fake_prisma_venv(tmp_path):
    venv = tmp_path / "venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    schema = venv / "lib/python3.12/site-packages/litellm_proxy_extras/schema.prisma"
    schema.parent.mkdir(parents=True)
    schema.write_text("// fixture schema\n", encoding="utf-8")
    ready = tmp_path / "prisma-client-ready"
    calls = tmp_path / "prisma-generate-calls"
    python = bin_dir / "python"
    python.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'if [[ "$*" == *"site.getsitepackages"* ]]; then\n'
        '  printf "%s\\n" "$FAKE_PRISMA_SCHEMA"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "$*" == *"from prisma import Prisma"* ]]; then\n'
        '  [[ -f "$FAKE_PRISMA_READY" ]] && exit 0\n'
        "  exit 1\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    prisma = bin_dir / "prisma"
    prisma.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'case ":$PATH:" in *":$FAKE_PRISMA_BIN:"*) ;; *) exit 3 ;; esac\n'
        'if [[ "$1" == "format" && "$2" == "--schema" && -f "$3" ]]; then\n'
        "  exit 0\n"
        "fi\n"
        '[[ "$1" == "generate" && "$2" == "--schema" && "$3" == "$FAKE_PRISMA_SCHEMA" ]] || exit 2\n'
        'printf "%s\\n" called >> "$FAKE_PRISMA_CALLS"\n'
        'if [[ "${FAKE_PRISMA_MODE:-success}" == "success" ]]; then\n'
        '  touch "$FAKE_PRISMA_READY"\n'
        '  mkdir -p "$(dirname "$FAKE_PRISMA_PACKAGED")"\n'
        '  cp "$FAKE_PRISMA_SCHEMA" "$FAKE_PRISMA_PACKAGED"\n'
        "fi\n"
        '[[ "${FAKE_PRISMA_MODE:-success}" != "fail" ]]\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    prisma.chmod(0o755)
    return venv, schema, ready, calls


def _run_prisma_guard(venv, schema, ready, calls, **overrides):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "FAKE_PRISMA_SCHEMA": str(schema),
        "FAKE_PRISMA_READY": str(ready),
        "FAKE_PRISMA_CALLS": str(calls),
        "FAKE_PRISMA_BIN": str(venv / "bin"),
        "FAKE_PRISMA_PACKAGED": str(schema.parent.parent / "prisma/schema.prisma"),
        **overrides,
    }
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; litellm_ensure_prisma_client "$2"',
            "prisma-test",
            str(helper),
            str(venv),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_litellm_prisma_guard_generates_missing_client_once(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    first = _run_prisma_guard(venv, schema, ready, calls)
    second = _run_prisma_guard(venv, schema, ready, calls)

    assert first.returncode == 0
    assert second.returncode == 0
    assert ready.is_file()
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]


def test_litellm_prisma_guard_regenerates_stale_importable_client(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    packaged = schema.parent.parent / "prisma/schema.prisma"
    packaged.parent.mkdir()
    packaged.write_text("// older generated schema\n", encoding="utf-8")
    ready.touch()

    result = _run_prisma_guard(venv, schema, ready, calls)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert packaged.read_text(encoding="utf-8") == schema.read_text(encoding="utf-8")


def test_litellm_prisma_guard_fails_if_generation_does_not_create_client(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    result = _run_prisma_guard(
        venv, schema, ready, calls, FAKE_PRISMA_MODE="incomplete"
    )

    assert result.returncode != 0
    assert "client remains unavailable" in result.stderr
    assert not ready.exists()


def test_litellm_prisma_guard_fails_if_schema_is_missing(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    schema.unlink()

    result = _run_prisma_guard(venv, schema, ready, calls)

    assert result.returncode != 0
    assert "schema is unavailable" in result.stderr
    assert not calls.exists()


def test_litellm_template_prepares_prisma_before_service_start():
    template = (
        Path(__file__).resolve().parents[3]
        / ".chezmoiscripts/run_onchange_31-litellm.sh.tmpl"
    ).read_text(encoding="utf-8")
    prepare = template.index('litellm_ensure_prisma_client "$VENV"')
    assert prepare < template.index("systemctl --user enable --now litellm.service")
    assert prepare < template.index("litellm_service_start")
