"""Paseo has its own LiteLLM client identity and no borrowed agent key."""

from pathlib import Path

from litellm_config import APP_KEYS, CLIENT_REGISTRY


def test_paseo_key_registry_is_distinct_from_other_clients():
    assert APP_KEYS["paseo"] == "LITELLM_PASEO_KEY"
    assert APP_KEYS["paseo"] not in (APP_KEYS["pi"], APP_KEYS["openwebui"])
    paseo = next(row for row in CLIENT_REGISTRY if row["client"] == "paseo")
    assert paseo["key_file"].endswith("clients/paseo.key")
    assert "DOTFILES_RUN_PASEO_VOICE_SETUP=1" in paseo["setup_gates"]
    assert "DOTFILES_USE_LITELLM_PROXY=1" in paseo["setup_gates"]
    assert paseo["classification"] == "eligible"
    assert paseo["credential_policy"] == "provision"


def test_service_env_sync_keeps_paseo_key_and_provisioner_registers_writer():
    root = Path(__file__).resolve().parents[3]
    shell = (root / "scripts/lib/litellm_service.sh").read_text()
    script = (root / "scripts/configure-litellm.py").read_text()
    assert "LITELLM_PASEO_KEY" in shell
    assert '"paseo": _write_paseo_key' in script
    assert '"paseo": "paseo"' in script
    assert "LITELLM_PASEO_KEY" in (root / "dot_dotfiles/shell/.env.example").read_text()


def test_absent_paseo_alias_preview_has_no_post_and_no_key_file(monkeypatch, tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "configure-litellm.py"
    spec = importlib.util.spec_from_file_location("paseo_key_preview_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def request(url, method, *_args, **_kwargs):
        calls.append(method)
        if "key/list" in url:
            return {"keys": []}
        return {"status": "ok"}

    monkeypatch.setattr(module, "_request_json", request)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/bin/paseo")
    # The preview must not read this host's real client credentials.
    # Owner shells can export sibling LITELLM_* keys (for example
    # LITELLM_OPENCODE_KEY), which would strand them against the
    # stubbed gateway and refuse the whole preflight.
    for env_name in APP_KEYS.values():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_PASEO_VOICE_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    service = tmp_path / "service.env"
    assert module.provision_app_keys("master", service, dry_run=True)
    assert calls and set(calls) == {"GET"}
    assert not service.exists()
    assert not (tmp_path / "clients/paseo.key").exists()
