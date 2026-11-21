import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "refresh-artificial-analysis.py"
SPEC = importlib.util.spec_from_file_location("refresh_aa", SCRIPT)
REFRESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)


def test_refresh_refuses_before_transport_on_a_shared_cache(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    called = []
    monkeypatch.setattr(REFRESH, "fetch_free", lambda *a, **k: called.append(1))
    monkeypatch.setattr(
        sys,
        "argv",
        ["refresh-artificial-analysis", "--refresh", "--cache-dir", str(shared)],
    )
    assert REFRESH.main() == 1
    assert called == []
    assert (shared.stat().st_mode & 0o777) == 0o755
