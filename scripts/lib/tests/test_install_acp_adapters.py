import importlib.util
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "install_acp_adapters", ROOT / "scripts" / "install-acp-adapters.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def test_gate_off_skips_without_subprocess(monkeypatch):
    monkeypatch.delenv("DOTFILES_RUN_OPENCODE_TOOLS_SETUP", raising=False)
    with mock.patch.object(module.subprocess, "run") as run:
        assert module.install_opencode_adapters() == 0
        run.assert_not_called()


def test_dry_run_is_network_free_and_preserves_pins(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_TOOLS_SETUP", "1")
    with (
        mock.patch.object(module.shutil, "which", return_value=None),
        mock.patch.object(module.subprocess, "run") as run,
    ):
        run.return_value.returncode = 1
        assert module.install_opencode_adapters(dry_run=True) == 0
        assert module.CLAUDE_ACP_VERSION == "latest"
        assert module.CODEX_ACP_VERSION == "latest"
        assert module.PI_ACP_VERSION == "latest"
        assert run.call_count == 3
        assert not any("install" in call.args[0] for call in run.call_args_list)


def test_npm_adapter_skips_when_installed():
    with mock.patch.object(module, "_run", return_value=True) as run:
        assert module.install_npm_adapter("pi-acp", module.PI_ACP_VERSION) == 0
        run.assert_called_once_with(["npm", "list", "-g", "pi-acp"], quiet=True)


def test_antigravity_gate_off(monkeypatch):
    monkeypatch.delenv("DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP", raising=False)
    with mock.patch.object(module.subprocess, "run") as run:
        assert module.install_antigravity_acp() == 0
        run.assert_not_called()
