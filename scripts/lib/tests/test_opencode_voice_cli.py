import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_writer_cli_registration_matches_manifest():
    source = (ROOT / "configure-opencode-voice.py").read_text()
    tree = ast.parse(source)
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    source_text = ast.get_source_segment(source, main) or ""
    assert '"package": voice_plugin_spec' in source_text
    assert '"options": entry_options' in source_text
    manifest = _load("opencode_plugins_for_voice", ROOT / "lib" / "opencode_plugins.py")
    specs = manifest.active_cli_plugin_specs()
    assert specs == ["@bojackduy/opencode-voice@latest"]
    assert "active_cli_plugin_specs()" in source
    assert 'os.path.join(config_dir_path, "cli.json")' in source


def test_voice_matches_forms_and_preserves_cli_directives():
    module = _load("voice_cli", ROOT / "configure-opencode-voice.py")
    assert module._voice_entry_matches("@bojackduy/opencode-voice@latest")
    assert module._voice_entry_matches({"package": "-@bojackduy/opencode-voice@latest"})
    assert not module._voice_entry_matches("*")
    assert not module._voice_entry_matches("-team.*")


def test_cli_reconcile_preserves_object_root_form(tmp_path, monkeypatch):
    """The {plugins} object root (probe-verified upstream v2 form) is merged,
    never converted, and its non-array shape survives the voice entry write."""
    module = _load("voice_cli_object_root", ROOT / "configure-opencode-voice.py")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "false")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(module, "load_env", lambda *_: False)
    monkeypatch.setattr(module, "get_available_tiers", lambda: ["local"])
    monkeypatch.setattr(
        module,
        "get_voice_config",
        lambda *_args, **_kwargs: {"endpoint": "https://v.example", "model": "m"},
    )
    monkeypatch.setattr(module, "_voice_resolution_options", lambda *_: {})
    monkeypatch.setattr(
        module.sys, "argv", ["voice", "--preset", "local", "--no-backup"]
    )
    seed = {
        "$schema": "https://opencode.ai/cli.json",
        "plugins": ["first", ["foreign", {}]],
    }
    (tmp_path / "cli.json").write_text(json.dumps(seed))
    assert module.main() is None
    result = json.loads((tmp_path / "cli.json").read_text())
    assert set(result) == {"$schema", "plugins"}
    assert result["plugins"][:-1] == ["first", ["foreign", {}]]
    assert result["plugins"][-1] == {
        "package": "@bojackduy/opencode-voice@latest",
        "options": {"endpoint": "https://v.example", "model": "m"},
    }


def test_cli_reconcile_preserves_entries_and_removes_old_tui(tmp_path, monkeypatch):
    module = _load("voice_cli_reconcile", ROOT / "configure-opencode-voice.py")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "false")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(module, "load_env", lambda *_: False)
    monkeypatch.setattr(module, "get_available_tiers", lambda: ["local"])
    monkeypatch.setattr(
        module,
        "get_voice_config",
        lambda *_args, **_kwargs: {
            "endpoint": "https://voice.example/v1",
            "model": "voice-model",
            "apiKeyEnv": "VOICE_KEY",
            "sttEndpoint": "https://stt.example/v1",
            "sttModel": "stt-model",
            "sttApiKeyEnv": "STT_KEY",
        },
    )
    monkeypatch.setattr(module, "_voice_resolution_options", lambda *_: {})
    monkeypatch.setattr(
        module.sys, "argv", ["voice", "--preset", "local", "--no-backup"]
    )
    cli = ["first", "-team.*", "*", "last"]
    (tmp_path / "cli.json").write_text(json.dumps(cli))
    (tmp_path / "tui.json").write_text(
        json.dumps(
            {
                "plugin": [
                    ["@renjfk/opencode-voice", {"model": "legacy"}],
                    ["@bojackduy/opencode-voice@latest", {}],
                    "unrelated",
                ],
                "user": True,
            }
        )
    )
    assert module.main() is None
    result = json.loads((tmp_path / "cli.json").read_text())
    assert result[:-1] == cli
    assert result[-1] == {
        "package": "@bojackduy/opencode-voice@latest",
        "options": {
            "endpoint": "https://voice.example/v1",
            "model": "voice-model",
            "apiKeyEnv": "VOICE_KEY",
            "sttEndpoint": "https://stt.example/v1",
            "sttModel": "stt-model",
            "sttApiKeyEnv": "STT_KEY",
        },
    }
    assert (tmp_path / "cli.json").stat().st_mode & 0o777 == 0o600
    tui = json.loads((tmp_path / "tui.json").read_text())
    assert tui == {"plugin": ["unrelated"], "user": True}


def test_dcp_removes_stale_forms_without_addition():
    module = _load("dcp_cli", ROOT / "configure-opencode-dcp.py")
    entries = [
        "@tarquinen/opencode-dcp@latest",
        ["@tarquinen/opencode-dcp", {}],
        {"package": "@tarquinen/opencode-dcp@latest"},
        "other",
    ]
    assert [entry for entry in entries if not module.is_dcp_entry(entry)] == ["other"]


def test_dcp_main_only_removes_stale_registration(tmp_path, monkeypatch):
    module = _load("dcp_reconcile", ROOT / "configure-opencode-dcp.py")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "load_env", lambda *_: False)
    monkeypatch.setattr(module.sys, "argv", ["dcp", "--no-backup"])
    source = {"plugin": ["@tarquinen/opencode-dcp@latest", "other"], "user": 1}
    (tmp_path / "tui.json").write_text(json.dumps(source))
    assert module.main() is None
    assert json.loads((tmp_path / "tui.json").read_text()) == {
        "plugin": ["other"],
        "user": 1,
    }
    assert not (tmp_path / "cli.json").exists()


def test_verify_cli_json_parseable_or_absent(tmp_path, monkeypatch, capsys):
    module = _load("verify_for_cli", ROOT / "verify-config.py")
    monkeypatch.setattr(module, "HOME", tmp_path)
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")
    config = tmp_path / ".config/opencode"
    config.mkdir(parents=True)
    (config / "dcp.jsonc").write_text('{"compress":{}}')
    (config / "opencode.json").write_text('{"compaction":{"auto":false}}')
    (config / "cli.json").write_text("not json")
    assert module.check_opencode_orphan_files() == 1
    assert "✗ OpenCode cli.json" in capsys.readouterr().out
    # The object {plugins} root is probe-verified as a valid v2 form.
    (config / "cli.json").write_text('{"plugins": [{"package": "x@1"}]}')
    capsys.readouterr()
    assert module.check_opencode_orphan_files() == 0
    (config / "cli.json").write_text('{"plugins": ["@slkiser/opencode-quota@4.10.2"]}')
    capsys.readouterr()
    assert module.check_opencode_orphan_files() == 0
    assert "retired quota plugin" in capsys.readouterr().out
    (config / "cli.json").unlink()
    assert module.check_opencode_orphan_files() == 0


def test_voice_cli_entry_options_shape_is_object(tmp_path, monkeypatch):
    module = _load("voice_options_shape", ROOT / "configure-opencode-voice.py")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "false")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(module, "load_env", lambda *_: False)
    monkeypatch.setattr(module, "get_available_tiers", lambda: ["local"])
    monkeypatch.setattr(
        module,
        "get_voice_config",
        lambda *_args, **_kwargs: {
            "endpoint": "https://voice.example/v1",
            "model": "voice-model",
        },
    )
    monkeypatch.setattr(module, "_voice_resolution_options", lambda *_: {})
    monkeypatch.setattr(
        module.sys, "argv", ["voice", "--preset", "local", "--no-backup"]
    )
    assert module.main() is None
    entry = json.loads((tmp_path / "cli.json").read_text())[0]
    assert entry["package"] == "@bojackduy/opencode-voice@latest"
    assert entry["options"] == {
        "endpoint": "https://voice.example/v1",
        "model": "voice-model",
    }
