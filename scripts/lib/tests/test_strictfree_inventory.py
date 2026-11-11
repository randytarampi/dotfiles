import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
DOC = ROOT / "docs" / "strict-free-executor-inventory.md"
EXPECTED_SURFACES = {
    "opencode-foreground",
    "opencode-delegated",
    "opencode-council",
    "opencode-auxiliary",
    "codex-panel",
    "junie-claude-cursor",
    "hermes-direct",
    "litellm-gateway",
}
VERDICTS = {"HOOKABLE-CONSTRAINED", "NOT-INTERCEPTABLE", "BYPASSABLE"}


def test_versioned_inventory_matrix_has_required_surface_coverage():
    text = DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    assert len(blocks) == 1
    inventory = json.loads(blocks[0])

    assert re.fullmatch(r"\d+\.\d+\.\d+", inventory["version"])
    rows = inventory["rows"]
    assert {row["id"] for row in rows} == EXPECTED_SURFACES
    for row in rows:
        assert row["surface"]
        assert row["producer"]
        assert isinstance(row["pre_send_hook_today"], bool)
        assert row["bypass"]
        assert row["verdict"] in VERDICTS
