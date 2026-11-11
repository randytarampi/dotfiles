"""Keep AI CLI installation in the existing package manifests, not the Ollama stub task."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AI_TOOLS = ROOT / ".chezmoiscripts/run_onchange_08-install-ai-cli-tools.sh.tmpl"


def test_codegraph_and_macos_lazyskills_have_one_package_owner():
    manifest = (ROOT / "Brewfile.dev").read_text(encoding="utf-8")
    task = AI_TOOLS.read_text(encoding="utf-8")

    assert manifest.count('npm "@colbymchenry/codegraph"') == 1
    assert manifest.count('cask "alvinunreal/tap/lazyskills"') == 1
    assert "npm install -g @colbymchenry/codegraph" not in task
    assert "brew install --cask lazyskills" not in task

    # Preserve Linux's separate installer until a qualified package exists.
    assert "curl -fsSL https://lazyskills.sh/install | sh" in task
    # This gate must still refresh OpenCode's Ollama Cloud stubs.
    assert 'ollama pull "${model}:cloud"' in task
