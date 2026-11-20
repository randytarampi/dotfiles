"""Gate-owned packages must not be adopted or reinstalled by setup gates."""

import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def _load_sync_module():
    path = ROOT / "scripts/sync-brewfiles.py"
    spec = importlib.util.spec_from_file_location("sync_brewfiles", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_context_mode_is_not_in_package_manifests():
    manifests = [
        *ROOT.glob("Brewfile*"),
        *ROOT.glob("wingetfile*"),
    ]
    assert manifests
    assert all(
        "context-mode" not in path.read_text(encoding="utf-8") for path in manifests
    )


def test_sync_skips_context_mode_but_adopts_normal_npm(tmp_path):
    sync = _load_sync_module()
    context = sync.BrewEntry("npm", "context-mode", None, 'npm "context-mode"')
    regular = sync.BrewEntry("npm", "ordinary-tool", None, 'npm "ordinary-tool"')
    assert sync.classify_entry(context) is None
    assert sync.classify_entry(regular) == "dev"

    category_files = sync.load_category_files(ROOT)
    with patch.object(
        sync,
        "run_brew_bundle_dump",
        return_value='npm "context-mode"\nnpm "ordinary-tool"\n',
    ):
        # Exercise normal sync loop without writing source manifests.
        with patch.object(sync, "load_category_files", return_value=category_files):
            result = sync.sync_brewfiles(
                sync.argparse.Namespace(diff=False, auto=True, dry_run=True)
            )
    assert result == 0
    assert all(
        entry.name != "context-mode"
        for item in category_files.values()
        for entry in item.additions
    )
    assert any(
        entry.name == "ordinary-tool"
        for item in category_files.values()
        for entry in item.additions
    )


def test_gate_scripts_verify_manifest_owned_packages():
    voice = (
        ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    ).read_text()
    acp = (ROOT / "scripts/install-acp-adapters.py").read_text()
    for forbidden in (
        "brew install whisper.cpp",
        "brew install sox",
        "uv tool install piper-tts",
        "pip install piper-tts",
    ):
        assert forbidden not in voice
    assert "brew install copilot-cli" not in acp
    for package, declaration in (
        ("whisper.cpp", 'brew "whisper.cpp"'),
        ("sox", 'brew "sox"'),
        ("piper-tts", 'uv "piper-tts"'),
        ("copilot-cli", 'cask "copilot-cli"'),
    ):
        declarations = sum(
            path.read_text(encoding="utf-8").count(declaration)
            for path in ROOT.glob("Brewfile*")
        )
        assert declarations == 1, (package, declarations)
    assert "Brewfile.dev" in voice
    assert "Brewfile.dev" in acp
