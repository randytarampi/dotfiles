"""CLI regression checks for invalid Slim registry document shapes."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "detect-tier-coverage.py"


@pytest.mark.parametrize("slim_json", ["true", "1"])
def test_non_object_slim_document_returns_registry_error_without_traceback(
    tmp_path, slim_json
):
    slim = tmp_path / "slim.json"
    slim.write_text(slim_json)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slim", str(slim)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "Slim registry is not a JSON object" in result.stderr
