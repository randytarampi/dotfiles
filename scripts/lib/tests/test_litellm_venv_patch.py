import importlib.util
from pathlib import Path

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
