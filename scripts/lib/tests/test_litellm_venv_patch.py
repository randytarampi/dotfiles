import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / "configure-litellm-venv.py"
SPEC = importlib.util.spec_from_file_location("litellm_venv_patch", SCRIPT)
PATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHER)


def test_patch_is_idempotent_and_preserves_first_backup(tmp_path):
    target = tmp_path / "utils.py"
    original = (
        "def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:\n"
        "    try:\n        return get_model_info(model)\n    except Exception:\n        return None\n"
    )
    target.write_text(original)
    assert PATCHER.configure(target) == 0
    backup = target.with_name("utils.py.orig-dotfiles")
    assert backup.read_text() == original
    patched = target.read_text()
    assert PATCHER.configure(target) == 0
    assert target.read_text() == patched
    assert backup.read_text() == original


def test_patch_rejects_unexpected_signature(tmp_path):
    target = tmp_path / "utils.py"
    target.write_text("def _safe_get_model_info(info):\n    return {}\n")
    try:
        PATCHER.configure(target)
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected signature accepted")
    assert not target.with_name("utils.py.orig-dotfiles").exists()


def test_patch_dry_run_does_not_write(tmp_path):
    target = tmp_path / "utils.py"
    target.write_text(
        "def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:\n    return None\n"
    )
    assert PATCHER.configure(target, dry_run=True) == 0
    assert "dotfiles listing-enrichment" not in target.read_text()
    assert not target.with_name("utils.py.orig-dotfiles").exists()


def test_patch_compile_failure_restores_invocation_source_with_no_backup(
    tmp_path, monkeypatch
):
    target = tmp_path / "utils.py"
    original = "def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:\n    return None\n"
    target.write_text(original)
    monkeypatch.setattr(
        PATCHER.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    assert PATCHER.configure(target, no_backup=True) == 1
    assert target.read_text() == original


def test_patch_compile_failure_ignores_stale_backup(tmp_path, monkeypatch):
    target = tmp_path / "utils.py"
    original = "def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:\n    return None\n"
    target.write_text(original)
    target.with_name("utils.py.orig-dotfiles").write_text("stale source")
    monkeypatch.setattr(
        PATCHER.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    assert PATCHER.configure(target) == 1
    assert target.read_text() == original


def test_patch_compiler_launch_error_restores_source(tmp_path, monkeypatch):
    target = tmp_path / "utils.py"
    original = "def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:\n    return None\n"
    target.write_text(original)

    def fail(*args, **kwargs):
        raise OSError("launch failed")

    monkeypatch.setattr(PATCHER.subprocess, "run", fail)
    assert PATCHER.configure(target, no_backup=True) == 1
    assert target.read_text() == original


def test_patch_rollback_preserves_crlf_bytes_and_mode(tmp_path, monkeypatch):
    target = tmp_path / "utils.py"
    original = (
        b"def _safe_get_model_info(model: str, get_model_info: Callable[[str], ModelInfo]) -> ModelInfo | None:\r\n"
        b"    return None\r\n"
    )
    target.write_bytes(original)
    target.chmod(0o640)
    monkeypatch.setattr(
        PATCHER.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    assert PATCHER.configure(target, no_backup=True) == 1
    assert target.read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o640


CLAUDE_SNAPSHOT = {
    "claude-sonnet-5-5": {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 1e-05,
        "mode": "chat",
    }
}


def _cost_map_fixture(tmp_path, entries):
    target = tmp_path / "model_prices_and_context_window_backup.json"
    target.write_text(
        '{"gpt-5.5": {"input_cost_per_token": 0.0000015}}\n'.replace(
            '{"gpt-5.5": {"input_cost_per_token": 0.0000015}}',
            str(entries).replace("'", '"'),
        )
    )
    return target


def test_cost_map_merge_applies_snapshot_idempotently(tmp_path, monkeypatch):
    target = _cost_map_fixture(
        tmp_path, {"gpt-5.5": {"input_cost_per_token": 0.0000015}}
    )
    monkeypatch.setattr(PATCHER, "snapshot_rates", lambda: dict(CLAUDE_SNAPSHOT))
    monkeypatch.setattr(PATCHER, "cost_map_file", lambda: target)
    assert PATCHER.merge_cost_rates(target) == 0
    merged = json.loads(target.read_text())
    assert merged["claude-sonnet-5-5"] == CLAUDE_SNAPSHOT["claude-sonnet-5-5"]
    assert merged["gpt-5.5"] == {"input_cost_per_token": 0.0000015}
    backup = target.with_name(
        "model_prices_and_context_window_backup.json.orig-dotfiles"
    )
    assert backup.exists()
    first_bytes = target.read_bytes()
    assert PATCHER.merge_cost_rates(target) == 0
    assert target.read_bytes() == first_bytes
    assert not target.with_name(
        "model_prices_and_context_window_backup.json.rollback"
    ).exists()


def test_cost_map_merge_dry_run_writes_nothing(tmp_path, monkeypatch, caplog):
    target = _cost_map_fixture(tmp_path, {})
    monkeypatch.setattr(PATCHER, "snapshot_rates", lambda: dict(CLAUDE_SNAPSHOT))
    assert PATCHER.merge_cost_rates(target, dry_run=True) == 0
    assert json.loads(target.read_text()) == {}
    assert "claude-sonnet-5-5" in caplog.text


def test_cost_map_merge_restores_original_on_write_failure(tmp_path, monkeypatch):
    target = _cost_map_fixture(tmp_path, {})
    original_bytes = target.read_bytes()
    monkeypatch.setattr(PATCHER, "snapshot_rates", lambda: dict(CLAUDE_SNAPSHOT))
    real_replace = PATCHER.os.replace
    calls = {"n": 0}

    def fail_first_replace(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(PATCHER.os, "replace", fail_first_replace)
    assert PATCHER.merge_cost_rates(target, no_backup=True) == 1
    assert target.read_bytes() == original_bytes
    assert not target.with_name(
        "model_prices_and_context_window_backup.json.rollback"
    ).exists()


def test_cost_map_merge_detects_upstream_drift(tmp_path, monkeypatch):
    target = _cost_map_fixture(
        tmp_path,
        {
            "gpt-5.5": {"input_cost_per_token": 0.0000015},
            "claude-sonnet-5-5": {"input_cost_per_token": 9e-9},
        },
    )
    monkeypatch.setattr(PATCHER, "snapshot_rates", lambda: dict(CLAUDE_SNAPSHOT))
    assert PATCHER.cost_map_rate_drift(
        json.loads(target.read_text()), CLAUDE_SNAPSHOT
    ) == ["claude-sonnet-5-5"]
    assert PATCHER.merge_cost_rates(target) == 0
    assert (
        json.loads(target.read_text())["claude-sonnet-5-5"]
        == CLAUDE_SNAPSHOT["claude-sonnet-5-5"]
    )


def test_snapshot_rates_rejects_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(PATCHER, "SCRIPT_DIR", tmp_path)
    try:
        PATCHER.snapshot_rates()
    except ValueError:
        pass
    else:
        raise AssertionError("missing snapshot accepted")
