import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "refresh_model_catalogues", ROOT / "scripts/refresh-model-catalogues.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_zero_price_filter_requires_numeric_zero_entries():
    assert MODULE.is_zero_priced({"prompt": "0", "completion": 0})
    assert not MODULE.is_zero_priced({"prompt": "0.01", "completion": "0"})
    assert not MODULE.is_zero_priced({"prompt": "0", "completion": "unknown"})
    assert not MODULE.is_zero_priced({"prompt": 0, "completion": None})
    assert not MODULE.is_zero_priced({"prompt": 0, "completion": False})
    assert MODULE.is_zero_priced({"prompt": 0, "completion": "0"})
    assert not MODULE.is_zero_priced(None)


def test_unavailable_refresh_writes_artefact_without_allowlist_changes(
    tmp_path, monkeypatch
):
    output = tmp_path / "artifacts/model-catalogues/opencode-zen-free.json"
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    assert MODULE.refresh(output) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert "fetched_at" in payload
