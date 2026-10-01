import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "verify_tier_docs", ROOT / "scripts/verify-tier-docs.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _write_fixtures(tmp_path):
    slim = tmp_path / "slim.json"
    docs = tmp_path / "TIERS.md"
    slim.write_text(
        json.dumps({"presets": {"test": {"orchestrator": {"model": "openai/alpha"}}}}),
        encoding="utf-8",
    )
    docs.write_text(
        "### Test Tier (`test`)\n\n"
        "| Role | Model | Variant |\n|------|-------|---------|\n"
        "| orchestrator | `alpha` | high |\n",
        encoding="utf-8",
    )
    return slim, docs


def test_checker_reports_bogus_documentation_model(tmp_path, capsys):
    slim, docs = _write_fixtures(tmp_path)
    docs.write_text(docs.read_text(encoding="utf-8").replace("`alpha`", "`bogus`"))

    assert MODULE.check(slim, docs) == 2
    output = capsys.readouterr().err
    assert "Missing from docs/TIERS.md tier tables: alpha" in output
    assert "Extra in docs/TIERS.md tier tables: bogus" in output


def test_checker_reports_bogus_registry_model(tmp_path, capsys):
    slim, docs = _write_fixtures(tmp_path)
    data = json.loads(slim.read_text(encoding="utf-8"))
    data["presets"]["test"]["oracle"] = {"model": "openai/bogus"}
    slim.write_text(json.dumps(data), encoding="utf-8")

    assert MODULE.check(slim, docs) == 2
    output = capsys.readouterr().err
    assert "Missing from docs/TIERS.md tier tables: bogus" in output
