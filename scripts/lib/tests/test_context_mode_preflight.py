"""Context Mode's install-before-config authorization preflight."""

import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "context_mode_preflight.py"
spec = importlib.util.spec_from_file_location("context_mode_preflight", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def config_with_rules():
    return {
        "plugins": ["context-mode@2.2.1"],
        "permissions": [
            {"action": action, "resource": "*", "effect": effect}
            for action, effect in module.REQUIRED_EFFECTS.items()
        ],
    }


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"plugins": ["-context-mode"]}, True),
        ({"plugins": ["-context-mode", "-context-mode"]}, False),
        ({"plugins": []}, False),
        ({"plugins": ["context-mode@latest"]}, False),
        ({"plugins": ["context-mode@2.2.1"]}, False),
        ({"plugins": ["-context-mode", "context-mode@2.2.1"]}, False),
        (config_with_rules(), True),
        (
            {
                **config_with_rules(),
                "permissions": [
                    *config_with_rules()["permissions"],
                    {"action": "ctx_execute", "resource": "*", "effect": "allow"},
                ],
            },
            False,
        ),
        (
            {
                **config_with_rules(),
                "permissions": [
                    *config_with_rules()["permissions"],
                    {"action": "*", "resource": "*", "effect": "allow"},
                ],
            },
            False,
        ),
        ({"plugins": ["context-mode@2.2.1"], "permissions": []}, False),
        ({"plugins": None}, False),
    ],
)
def test_staged_config_authorization(tmp_path, config, expected):
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert module.safely_staged(path) is expected


def test_missing_invalid_or_symlinked_config_refused(tmp_path):
    path = tmp_path / "opencode.json"
    assert not module.safely_staged(path)
    path.write_text("{ not-json", encoding="utf-8")
    assert not module.safely_staged(path)
    path.unlink()
    target = tmp_path / "user-config.json"
    target.write_text('{"plugins":["-context-mode"]}', encoding="utf-8")
    path.symlink_to(target)
    assert not module.safely_staged(path)
