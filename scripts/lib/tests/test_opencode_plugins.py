import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_checker():
    """Import the plugin consistency checker from its script path."""
    spec = importlib.util.spec_from_file_location(
        "check_plugin_consistency", ROOT / "scripts/check-plugin-consistency.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_pinned_spec_loader():
    """Import the shared plugin manifest loader for package-spec validation."""
    spec = importlib.util.spec_from_file_location(
        "opencode_plugins_loader", ROOT / "scripts/lib/opencode_plugins.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_matches_config_and_install_template():
    """Keep plugin specs and ordering consistent across manifest and consumers."""
    checker = load_checker()
    full_manifest = checker.load_manifest()["plugins"]
    assert "opencode-mem@latest" in full_manifest
    loader = load_pinned_spec_loader()
    assert all(loader.is_pinned_spec(spec) for spec in full_manifest)
    assert full_manifest[1] == "@tarquinen/opencode-dcp@latest"
    manifest = checker.active_plugin_specs()
    expected = [manifest[0], *manifest[1:]]
    assert checker.parse_install_plugins(checker.INSTALL_SCRIPT) == expected


def test_gated_plugins_are_excluded_until_explicitly_enabled(monkeypatch):
    checker = load_checker()
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_MEMORY_SETUP", "0")
    inactive = checker.active_plugin_specs()
    assert "opencode-mem@latest" not in inactive
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_MEMORY_SETUP", "1")
    active = checker.active_plugin_specs()
    assert "opencode-mem@latest" in active


@pytest.mark.parametrize("field", ["install", "config"])
def test_missing_extra_and_mismatch_failures(field):
    checker = load_checker()
    manifest = checker.active_plugin_specs()
    expected = manifest
    # trufflehog:ignore - synthetic package spec used only to test mismatch handling.
    actual = expected[:-1] + ["unexpected@9.9.9"]
    kwargs = {"install_plugins": expected, "config_plugins": expected}
    kwargs[f"{field}_plugins"] = actual
    assert checker.check_consistency(expected, **kwargs) == 1


@pytest.mark.parametrize(
    "filename, parser, replacement",
    [
        # The manifest drives DCP placement; the emitter's only hard-coupled
        # spec consumer is the plannotator object entry — drift there IS detected.
        (
            "scripts/configure-opencode.py",
            "config",
            '"package": "@plannotator/opencode-drift",',
        ),
    ],
)
def test_real_consumer_drift_is_detected(tmp_path, filename, parser, replacement):
    checker = load_checker()
    source = ROOT / filename
    mutated = tmp_path / source.name
    text = source.read_text(encoding="utf-8")
    mutated.write_text(
        text.replace('"package": plannotator,', replacement, 1),
        encoding="utf-8",
    )
    assert mutated.read_text(encoding="utf-8") != text  # mutation must apply
    manifest = checker.active_plugin_specs()
    expected = manifest
    actual = checker.parse_config_plugins(mutated)
    assert actual != manifest


def test_install_exclusion_condition_mutation_fails(tmp_path):
    checker = load_checker()
    source = ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    mutated = tmp_path / source.name
    text = source.read_text(encoding="utf-8").replace(
        '[[ "$plugin" == "$OH_MY_PLUGIN" ]] || PLUGINS+=("$plugin")',
        '[[ "$plugin" == "$OH_MY_PLUGIN" || "$plugin" == "opencode-mem@latest" ]] || PLUGINS+=("$plugin")',  # trufflehog:ignore
    )
    mutated.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="exclusion condition changed"):
        checker.parse_install_plugins(mutated)


def test_consumer_order_mutation_fails(tmp_path):
    checker = load_checker()
    source = ROOT / "scripts/configure-opencode.py"
    mutated = tmp_path / source.name
    text = source.read_text(encoding="utf-8").replace(
        "configured_plugins[0],\n                configured_plugins[1],",
        "configured_plugins[1],\n                configured_plugins[0],",
        1,
    )
    assert text != source.read_text(encoding="utf-8")  # mutation must apply
    mutated.write_text(text, encoding="utf-8")
    expected = checker.active_plugin_specs()
    actual = checker.parse_config_plugins(mutated)
    assert actual[0] != expected[0]  # parsed output carries the order violation
    assert checker.check_consistency(expected, expected, actual) == 1


def test_commented_manifest_import_fails_ast_consumer_check(tmp_path):
    checker = load_checker()
    mutated = tmp_path / "configure-opencode.py"
    mutated.write_text(
        "# from opencode_plugins import active_plugin_specs\n"
        "configured_plugins = active_plugin_specs()\n",
        encoding="utf-8",
    )
    assert not checker._config_consumes_manifest(mutated)


def test_presence_is_opt_in(tmp_path):
    checker = load_checker()
    manifest_specs = checker.active_plugin_specs()
    specs = manifest_specs
    assert checker.check_consistency(manifest_specs, specs, specs) == 0
    assert (
        checker.check_consistency(
            manifest_specs, specs, specs, {spec: False for spec in manifest_specs}
        )
        == 1
    )


def test_presence_check_is_read_only_and_mockable(tmp_path):
    checker = load_checker()
    cache = tmp_path / "opencode" / "npm" / "example-plugin@1.2.3"
    cache.mkdir(parents=True)
    assert checker.is_plugin_installed("example-plugin@1.2.3", tmp_path / "opencode")
    assert not checker.is_plugin_installed(
        "example-plugin@9.9.9", tmp_path / "opencode"
    )


def test_plan_agent_is_disabled_but_not_plannotator_planning_agent():
    source = (ROOT / "scripts/configure-opencode.py").read_text(encoding="utf-8")
    assert '"plan": {"disabled": True}' in source
    planning_block = source.split('"planningAgents":', 1)[1].split("]", 1)[0]
    assert '"plan"' not in planning_block


def test_generated_global_config_disables_native_auto_compaction():
    source = (ROOT / "scripts/configure-opencode.py").read_text(encoding="utf-8")
    assert '"compaction": {"auto": False}' in source


def test_native_config_shape_contract():
    source = (ROOT / "scripts/configure-opencode.py").read_text(encoding="utf-8")
    assert 'config["permissions"] = _native_permissions(' in source
    assert (
        '{"action": "external_directory", "resource": "*", "effect": "ask"}' in source
    )
    assert '"mcp.servers"' not in source  # runtime wrapper, not a static literal
    assert "def _native_mcp_config" in source
    assert '"plugins": [' in source
    assert '"package": plannotator' in source  # v2 {package, options} object form
    assert '"agents": {' in source  # native v2 agents key
    assert '"providers": {}' in source  # native v2 providers key
    assert '"lsp"' not in source
    assert 'config.pop("server", None)' in source
    assert 'config.pop("logLevel", None)' in source
    assert '"policies"' not in source  # v1 provider filters normalize silently
    assert '"disabled_providers": []' in source


# trufflehog:ignore - package specs and semver strings, not credentials.
def test_pinned_spec_accepts_floating_latest_alias():
    spec = importlib.util.spec_from_file_location(
        "opencode_plugins_loader", ROOT / "scripts/lib/opencode_plugins.py"
    )
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    assert loader.is_pinned_spec("oh-my-opencode-slim@latest")
    assert loader.is_pinned_spec("@plannotator/opencode@latest")
    assert loader.is_pinned_spec("example-plugin@1.2.3")
    assert not loader.is_pinned_spec("example-plugin")
    assert not loader.is_pinned_spec("example-plugin@not-a-version")
    assert not loader.is_pinned_spec("example-plugin@^1.0.0")


def test_latest_alias_dir_check_skips_package_json_fallback(tmp_path):
    checker = load_checker()
    cache = tmp_path / "opencode" / "npm" / "oh-my-opencode-slim@latest"
    cache.mkdir(parents=True)
    assert checker.is_plugin_installed(
        "oh-my-opencode-slim@latest", tmp_path / "opencode"
    )
    # Directory missing: the fallback compares literal versions and can never
    # match "latest", so it must report not-installed rather than scan.
    assert not checker.is_plugin_installed(
        "oh-my-opencode-slim@latest", tmp_path / "absent"
    )


def test_cli_plugins_manifest_and_installer_agree():
    checker = load_checker()
    assert checker.check_cli_plugin_consistency() == 0


def test_git_spec_validation():
    loader = load_pinned_spec_loader()
    assert loader.is_pinned_spec(
        "github:AxeForce/opencode-vibeguard-v2#934823e5500f6af8270a45ba2d2f245bc6e4faa4"
    )
    assert not loader.is_pinned_spec("github:AxeForce/repo#bad-ref!")
    assert not loader.is_pinned_spec("github:owner/repo/sub#abc123")


def test_replacement_plugins_are_present():
    plugins = load_checker().load_manifest()["plugins"]
    assert "@cheesecodestudio/opencode-tokenscope-v2@latest" in plugins
    assert any(
        item.startswith("github:AxeForce/opencode-vibeguard-v2#") for item in plugins
    )


def test_original_tokenscope_and_vibeguard_are_absent():
    manifest_text = (ROOT / "configs/opencode/opencode-plugins.json").read_text(
        encoding="utf-8"
    )
    assert (
        "@ramtinj95/opencode-tokenscope@" not in manifest_text
    ), "forbidden legacy TokenScope plugin spec found"
    assert (
        "opencode-vibeguard@" not in manifest_text
    ), "forbidden legacy VibeGuard plugin spec found"
