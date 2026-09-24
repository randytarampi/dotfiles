import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "migrate-env-gates.py"
spec = importlib.util.spec_from_file_location("migrate_env_gates", SCRIPT)
migrate_env_gates = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(migrate_env_gates)


def test_live_omlx_gate_is_not_removed_but_voice_vars_are():
    assert "DOTFILES_USE_LOCAL_OMLX" not in migrate_env_gates.REMOVED_ENV_VARS
    assert migrate_env_gates.REMOVED_ENV_VARS >= {
        "DOTFILES_RUN_VOICE_SETUP",
        "DOTFILES_WHISPER_MODEL",
        "DOTFILES_PIPER_VOICE",
    }
