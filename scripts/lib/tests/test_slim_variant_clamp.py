import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "configure_opencode_clamp", ROOT / "scripts/configure-opencode.py"
)
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


def test_variant_clamps_to_nearest_effort_and_drops_unsupported():
    slim = {
        "presets": {
            "pro": {
                "designer": {"model": "ollama-cloud/glm", "variant": "medium"},
                "worker": {"model": "ollama-cloud/plain", "variant": "low"},
            }
        }
    }
    providers = {
        "ollama-cloud": {
            "models": {
                "glm": {
                    "reasoning_options": [
                        {"type": "effort", "values": ["low", "high", "max"]}
                    ]
                },
                "plain": {"reasoning_options": [{"type": "toggle"}]},
            }
        }
    }
    configure.clamp_slim_variants(slim, providers)
    assert slim["presets"]["pro"]["designer"]["variant"] == "high"
    assert "variant" not in slim["presets"]["pro"]["worker"]


def test_marketplace_passthrough_is_not_a_role_for_variant_clamp():
    slim = {
        "presets": {
            "pro": {
                "marketplace": {"agents": ["owner/pkg"]},
                "designer": {"model": "ollama-cloud/glm", "variant": "medium"},
            }
        }
    }
    _, generated = configure.prepare_config_candidates(
        {"providers": {}}, slim, preserve_dynamic=True
    )
    generated_slim = __import__("json").loads(generated)
    assert generated_slim["presets"]["pro"]["marketplace"] == {"agents": ["owner/pkg"]}


def test_marketplace_packages_are_not_provider_references(tmp_path):
    import json
    import sys

    sys.path.insert(0, str(ROOT / "scripts/lib"))
    from opencode_config import get_preset_providers

    config = tmp_path / "slim.json"
    config.write_text(
        json.dumps(
            {
                "presets": {
                    "pro": {
                        "designer": {"model": "openai/gpt-6"},
                        "marketplace": {
                            "agents": ["alvin/debugger@1.0.0", "alvin/validator@1.0.1"]
                        },
                    }
                }
            }
        )
    )
    assert get_preset_providers("pro", config) == {"openai"}


def test_marketplace_activation_in_source_every_preset_only():
    import json

    source = json.loads(
        (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    expected = [
        "alvin/debugger@1.0.0",
        "alvin/documenter@1.0.0",
        "alvin/validator@1.0.1",
    ]
    assert source["presets"]
    assert all(
        preset["marketplace"]["agents"] == expected
        for preset in source["presets"].values()
    )
    assert all("marketplace" not in tier for tier in source["_tiers"].values())


def test_marketplace_reconcile_cache_cli_and_pin_mismatch(
    tmp_path, monkeypatch, caplog
):
    from types import SimpleNamespace

    monkeypatch.setattr(configure.Path, "home", lambda: tmp_path)
    cached_cli = (
        tmp_path
        / ".cache/opencode/npm/oh-my-opencode-slim@latest/123/node_modules/oh-my-opencode-slim/dist/cli/index.js"
    )
    cached_cli.parent.mkdir(parents=True)
    cached_cli.touch()
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if "list" in args:
            return SimpleNamespace(
                returncode=0,
                stdout='[{"id":"alvin/debugger","version":"0.9.0"}]',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    configure.reconcile_marketplace(["alvin/debugger@1.0.0"], runner=runner)
    assert len(calls) == 1
    assert calls[0][0:2] == ["node", str(cached_cli)]
    assert "--host-flavor" in calls[0] and "v2" in calls[0]
    assert "Marketplace package alvin/debugger is installed at 0.9.0" in caplog.text


def test_marketplace_reconcile_uses_bunx_without_cached_cli(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(configure.Path, "home", lambda: tmp_path)
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    configure.reconcile_marketplace([], runner=runner)
    configure.reconcile_marketplace(["alvin/new@1.0.0"], runner=runner)
    assert calls[0][:2] == ["bunx", "oh-my-opencode-slim@latest"]
