import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_plugin_consistency", ROOT / "scripts/check-plugin-consistency.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_matches_config_and_install_template():
    checker = load_checker()
    full_manifest = checker.load_manifest()["plugins"]
    assert "opencode-mem@2.26.0" in full_manifest
    manifest = checker.active_plugin_specs()
    expected = [manifest[0], "@tarquinen/opencode-dcp@latest", *manifest[1:]]
    assert checker.parse_config_plugins(checker.CONFIG_SCRIPT) == expected
    assert checker.parse_install_plugins(checker.INSTALL_SCRIPT) == expected


def test_gated_plugins_are_excluded_until_explicitly_enabled(monkeypatch):
    checker = load_checker()
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_MEMORY_SETUP", "0")
    inactive = checker.active_plugin_specs()
    assert "opencode-mem@2.26.0" not in inactive
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_MEMORY_SETUP", "1")
    active = checker.active_plugin_specs()
    assert "opencode-mem@2.26.0" in active


@pytest.mark.parametrize("field", ["install", "config"])
def test_missing_extra_and_mismatch_failures(field):
    checker = load_checker()
    manifest = checker.active_plugin_specs()
    expected = [manifest[0], "@tarquinen/opencode-dcp@latest", *manifest[1:]]
    # trufflehog:ignore - synthetic package spec used only to test mismatch handling.
    actual = expected[:-1] + ["unexpected@9.9.9"]
    kwargs = {"install_plugins": expected, "config_plugins": expected}
    kwargs[f"{field}_plugins"] = actual
    assert checker.check_consistency(expected, **kwargs) == 1


@pytest.mark.parametrize(
    "filename, parser, replacement",
    [
        (
            ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl",
            "install",
            '"@tarquinen/opencode-dcp@drift"',
        ),
        ("scripts/configure-opencode.py", "config", '"@tarquinen/opencode-dcp@drift"'),
    ],
)
def test_real_consumer_drift_is_detected(tmp_path, filename, parser, replacement):
    checker = load_checker()
    source = ROOT / filename
    mutated = tmp_path / source.name
    mutated.write_text(
        source.read_text(encoding="utf-8").replace(
            '"@tarquinen/opencode-dcp@latest"', replacement, 1
        ),
        encoding="utf-8",
    )
    manifest = checker.active_plugin_specs()
    expected = [manifest[0], "@tarquinen/opencode-dcp@latest", *manifest[1:]]
    actual = (
        checker.parse_install_plugins(mutated)
        if parser == "install"
        else checker.parse_config_plugins(mutated)
    )
    assert (
        checker.check_consistency(
            checker.active_plugin_specs(),
            actual if parser == "install" else expected,
            actual if parser == "config" else expected,
        )
        == 1
    )


def test_install_exclusion_condition_mutation_fails(tmp_path):
    checker = load_checker()
    source = ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    mutated = tmp_path / source.name
    text = source.read_text(encoding="utf-8").replace(
        '[[ "$plugin" == "$OH_MY_PLUGIN" ]] || PLUGINS+=("$plugin")',
        '[[ "$plugin" == "$OH_MY_PLUGIN" || "$plugin" == "opencode-mem@2.26.0" ]] || PLUGINS+=("$plugin")',
    )
    mutated.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="exclusion condition changed"):
        checker.parse_install_plugins(mutated)


def test_consumer_order_mutation_fails(tmp_path):
    checker = load_checker()
    source = ROOT / "scripts/configure-opencode.py"
    mutated = tmp_path / source.name
    text = source.read_text(encoding="utf-8").replace(
        'configured_plugins[0],\n                "@tarquinen/opencode-dcp@latest",',
        '"@tarquinen/opencode-dcp@latest",\n                configured_plugins[0],',
        1,
    )
    mutated.write_text(text, encoding="utf-8")
    expected = checker.active_plugin_specs()
    actual = checker.parse_config_plugins(mutated)
    assert (
        checker.check_consistency(
            expected, checker.parse_install_plugins(checker.INSTALL_SCRIPT), actual
        )
        == 1
    )


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
    specs = [manifest_specs[0], "@tarquinen/opencode-dcp@latest", *manifest_specs[1:]]
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
    assert '"plan": {"disable": True}' in source
    planning_block = source.split('"planningAgents":', 1)[1].split("]", 1)[0]
    assert '"plan"' not in planning_block


def test_generated_global_config_disables_native_auto_compaction():
    source = (ROOT / "scripts/configure-opencode.py").read_text(encoding="utf-8")
    assert '"compaction": {"auto": False}' in source
