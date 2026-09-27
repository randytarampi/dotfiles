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
