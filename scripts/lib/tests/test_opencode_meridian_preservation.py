"""Meridian registration persistence across generator writes.

`meridian setup --v2` owns its OpenCode registration outside the plugin
manifest; configure-opencode.py's wholesale plugins rebuild must preserve
the on-disk entry (string path or {package} object) instead of erasing it,
while never synthesizing an entry the owner removed or disabled.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/configure-opencode.py"
SPEC = importlib.util.spec_from_file_location(
    "configure_opencode_preserve", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
configure_opencode = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = configure_opencode
SPEC.loader.exec_module(configure_opencode)

MERIDIAN_PATH_STRING = "/n/store/@rynfar/meridian/plugin/meridian.ts"


def test_preserves_string_meridian_entry(tmp_path):
    entries = ["/elsewhere/stuff", MERIDIAN_PATH_STRING]
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": entries}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == [
        MERIDIAN_PATH_STRING
    ]


def test_preserves_package_object_meridian_entry(tmp_path):
    entry = {"package": MERIDIAN_PATH_STRING, "options": {"x": 1}}
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": [entry]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == [entry]


def test_absent_meridian_is_never_synthesized(tmp_path):
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": ["unrelated"]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []


def test_disable_directive_is_never_synthesized(tmp_path):
    directive = "-@rynfar/meridian"
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": [directive]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == [
        "-@rynfar/meridian"
    ]


def test_unreadable_config_is_fail_open(tmp_path):
    (tmp_path / "opencode.json").write_text("{ not json")
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []
    missing = tmp_path / "sub"
    missing.mkdir()
    assert configure_opencode.meridian_preserved_entries(str(missing)) == []


def test_malformed_plugins_value_is_fail_open(tmp_path):
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": "not-a-list"}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []


def test_precise_matching_rejects_substring_lookalikes(tmp_path):
    lookalike = "@other/notes-about-@rynfar/meridian-features"
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": [lookalike]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []
    partial = "/store/@other/@rynfar/meridian-extra/plugin/meridian.ts"
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": [partial]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []


def test_directive_and_package_forms_match(tmp_path):
    forms = [
        "@rynfar/meridian",
        "@rynfar/meridian@1.87.0",
        "-@rynfar/meridian",
        "/n/store/@rynfar/meridian/plugin/meridian.ts",
        {"package": "@rynfar/meridian", "options": {}},
    ]
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": forms}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == forms


def _seed_registry(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    configs = tmp_path / "configs"
    opencode_configs = configs / "opencode"
    opencode_configs.mkdir(parents=True)
    registry = json.loads(
        (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    (opencode_configs / "oh-my-opencode-slim.json").write_text(json.dumps(registry))
    for filename in (
        "openai-models.json",
        "ollama-cloud-models.json",
        "anthropic-models.json",
        "opencode-models.json",
        "github-copilot-models.json",
        "google-models.json",
        "openrouter-models.json",
    ):
        (opencode_configs / filename).write_text('{"models": {}}')
    monkeypatch.setattr(configure_opencode, "SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setattr(configure_opencode, "CONFIGS_DIR", str(configs), raising=False)
    monkeypatch.setattr(configure_opencode, "load_env", lambda *args: False)
    monkeypatch.setattr(configure_opencode, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(configure_opencode, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        configure_opencode, "build_opencode_server_config", lambda: None
    )
    monkeypatch.setattr(configure_opencode, "get_preset_providers", lambda *a: set())
    monkeypatch.setattr(configure_opencode, "get_litellm_proxy_mode", lambda: False)
    monkeypatch.setattr(
        configure_opencode,
        "get_slim_config_path",
        lambda: str(opencode_configs / "oh-my-opencode-slim.json"),
    )
    monkeypatch.setattr(
        configure_opencode, "build_model_entry", lambda name, *args: {"name": name}
    )

    def fake_child(args, **kwargs):
        return type("Result", (), {"stdout": "{}", "returncode": 0, "stderr": ""})()

    monkeypatch.setattr(configure_opencode.subprocess, "run", fake_child)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    monkeypatch.delenv("DOTFILES_RUN_OPENCODE_MEMORY_SETUP", raising=False)
    return config_dir / "opencode.json"


def test_registered_meridian_survives_two_full_generator_runs(tmp_path, monkeypatch):
    """End-to-end: seed an upstream-registered Meridian plugin, then run the
    whole global generator twice with the Meridian gate OFF — the entry must
    keep its exact position and form, and the manifest-owned surfaces must
    stay byte-stable around it."""
    config_path = _seed_registry(tmp_path, monkeypatch)
    meridian_entry = "/n/store/@rynfar/meridian/plugin/meridian.ts"
    config_path.write_text(
        json.dumps({"plugins": [meridian_entry], "mcp": {"x": {"s": 1}}})
    )
    outputs = []
    for _run_index in range(2):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "configure-opencode.py",
                "--mode",
                "global",
                "--preset",
                "local",
                "--skip",
                "mcps,acp-agents,voice,dcp",
            ],
        )
        configure_opencode.main.__wrapped__()
        outputs.append(json.loads(config_path.read_text()))
    plugins_first, plugins_second = outputs[0]["plugins"], outputs[1]["plugins"]
    assert plugins_first == plugins_second
    assert plugins_first[-1] == meridian_entry
    # Manifest-owned surfaces stay stable between runs.
    for key in ("compaction", "providers", "agents", "disabled_providers"):
        assert outputs[0][key] == outputs[1][key], key
