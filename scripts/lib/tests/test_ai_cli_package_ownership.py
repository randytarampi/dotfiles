"""Keep AI CLI installation in the existing package manifests, not the Ollama stub task.

LazySkills was retired from repo management on 2026-11-19. These tests pin the
retirement so a well-meaning re-add has to be deliberate: the cask, the
`curl | sh` installer and the unattended bootstrap block must stay gone, while
the retained `skills` CLI and the Ollama Cloud stub refresh must survive.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AI_TOOLS = ROOT / ".chezmoiscripts/run_onchange_08-install-ai-cli-tools.sh.tmpl"
BREWFILE = ROOT / "Brewfile.dev"
INSTALL_SKILLS = ROOT / "scripts/install-skills.sh"


def test_codegraph_has_one_package_owner():
    manifest = BREWFILE.read_text(encoding="utf-8")
    task = AI_TOOLS.read_text(encoding="utf-8")

    assert manifest.count('npm "@colbymchenry/codegraph"') == 1
    assert "npm install -g @colbymchenry/codegraph" not in task

    # This gate must still refresh OpenCode's Ollama Cloud stubs.
    assert 'ollama pull "${model}:cloud"' in task


def test_lazyskills_is_not_managed():
    manifest = BREWFILE.read_text(encoding="utf-8")
    task = AI_TOOLS.read_text(encoding="utf-8")
    installer = INSTALL_SKILLS.read_text(encoding="utf-8")

    assert "lazyskills" not in manifest, "LazySkills cask reintroduced"
    assert "lazyskills" not in task, "LazySkills bootstrap reintroduced"
    assert (
        "lazyskills.sh/install" not in installer
    ), "LazySkills network installer reintroduced"
    assert "brew install --cask lazyskills" not in installer


def test_skills_cli_installer_is_retained():
    installer = INSTALL_SKILLS.read_text(encoding="utf-8")
    assert "brew install skills" in installer
    assert "npm install -g skills" in installer
    assert 'ok "Skills CLI tools install complete!"' in installer
