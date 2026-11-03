import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_layout(tmp_path, configs=(), templates=()):
    mcp_dir = tmp_path / "mcp"
    templates_dir = mcp_dir / "templates"
    templates_dir.mkdir(parents=True)
    for name in configs:
        (mcp_dir / f"{name}.json").write_text("{}")
    for name in templates:
        (templates_dir / f"{name}.json").write_text("{}")
    registry = mcp_dir / "global-mcps.json"
    registry.write_text(json.dumps({"project_mcp_templates": list(templates)}))
    return registry, mcp_dir, templates_dir


def run_check(module, registry, mcp_dir, templates_dir, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-mcp-parity.py",
            "--registry",
            str(registry),
            "--mcp-dir",
            str(mcp_dir),
            "--templates-dir",
            str(templates_dir),
        ],
    )
    return module.main()


def test_mcp_parity_holds(tmp_path, monkeypatch):
    module = load_script("check-mcp-parity")
    registry, mcp_dir, templates_dir = make_layout(
        tmp_path, configs=("alpha",), templates=("alpha",)
    )
    assert run_check(module, registry, mcp_dir, templates_dir, monkeypatch) == 0


def test_mcp_template_without_config_is_flagged(tmp_path, monkeypatch):
    module = load_script("check-mcp-parity")
    registry, mcp_dir, templates_dir = make_layout(
        tmp_path, configs=(), templates=("missing",)
    )
    assert run_check(module, registry, mcp_dir, templates_dir, monkeypatch) == 1


def test_mcp_config_without_template_is_flagged(tmp_path, monkeypatch):
    module = load_script("check-mcp-parity")
    registry, mcp_dir, templates_dir = make_layout(
        tmp_path, configs=("stray",), templates=()
    )
    assert run_check(module, registry, mcp_dir, templates_dir, monkeypatch) == 1


def test_malformed_registry_reports_controlled_error(tmp_path, monkeypatch):
    module = load_script("check-mcp-parity")
    registry, mcp_dir, templates_dir = make_layout(tmp_path)
    registry.write_text("[]")
    assert run_check(module, registry, mcp_dir, templates_dir, monkeypatch) == 1


def test_shared_basic_memory_is_registered_for_every_managed_global_client():
    """Only one registry manages Basic Memory and retains per-tool MCP peers."""
    root = Path(__file__).resolve().parents[3]
    registry = json.loads((root / "configs/mcp/global-mcps.json").read_text())
    template = json.loads((root / "configs/mcp/basic-memory.json").read_text())
    mirrored = json.loads(
        (root / "configs/mcp/templates/basic-memory.json").read_text()
    )
    assert template == mirrored
    assert template["name"] == "basic-memory"
    assert template["args"] == [
        "--prerelease=allow",
        "--from",
        "basic-memory==1.0.0",
        "basic-memory",
        "mcp",
    ]
    env = template["env"]
    assert env["BASIC_MEMORY_NO_PROMOS"] == "1"
    assert env["BASIC_MEMORY_LOGFIRE_ENABLED"] == "false"
    assert env["BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE"] == "false"
    assert env["BASIC_MEMORY_RERANKER_ENABLED"] == "true"
    assert env["BASIC_MEMORY_RERANKER_PROVIDER"] == "fastembed"
    assert env["BASIC_MEMORY_FORCE_LOCAL"] == "true"
    assert env["BASIC_MEMORY_AUTO_UPDATE"] == "false"
    assert env["OTEL_SDK_DISABLED"] == "true"
    assert env["BASIC_MEMORY_RERANKER_MODEL"] == "jinaai/jina-reranker-v1-tiny-en"
    assert env["BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER"] == "fastembed"
    assert registry["tools"]
    for name, client in registry["tools"].items():
        names = [entry["template"] for entry in client["mcp_servers"]]
        assert names.count("basic-memory") == 1, name
        assert "codegraph" in names, name
