"""The scheduled real-gate matrix must prepare the canonical deploy runtime.

Regression: run 42487586224 failed all three gates before their postconditions
because the nightly runner lacked Poetry, required by configure-all.sh Codex.
The scheduled lane deliberately remains allow-failure until stabilization.
"""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[3]
NIGHTLY = ROOT / ".github/workflows/nightly-integration.yml"
CI = ROOT / ".github/workflows/ci.yml"


def test_nightly_real_gates_have_locked_poetry_before_switching_home():
    workflow = yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))
    gate = workflow["jobs"]["real-gate"]
    assert gate["env"]["POETRY_VIRTUALENVS_IN_PROJECT"] == "true"

    steps = gate["steps"]
    named = {step["name"]: index for index, step in enumerate(steps) if "name" in step}
    poetry_index = named["Install Poetry (before setup-python)"]
    deps_index = named["Install locked production Python dependencies"]
    home_index = named["Prepare fresh home and single-gate environment"]
    deploy_index = named["Run canonical deploy path"]
    assert poetry_index < deps_index < home_index < deploy_index

    required_ci = CI.read_text(encoding="utf-8")
    version = re.search(r"poetry==[0-9]+[.][0-9]+[.][0-9]+", required_ci)
    assert version is not None
    assert version.group() in steps[poetry_index]["run"]
    assert steps[deps_index]["run"].strip() == "poetry install --only main --no-root"

    # Never accidentally promote a stabilizing scheduled matrix to a hard PR
    # context when repairing its missing runtime prerequisite.
    assert gate["continue-on-error"] is True
    assert {item["gate"] for item in gate["strategy"]["matrix"]["include"]} == {
        "DOTFILES_RUN_OPENCODE_SETUP",
        "DOTFILES_RUN_MCP_SETUP",
        "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
    }
