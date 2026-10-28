from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("mode, expected", [(0o600, 0), (0o644, 1)])
def test_noisy_gnu_stat_fallback_preserves_key_policy(tmp_path, mode, expected):
    home = tmp_path / "home"
    key = home / ".local/share/litellm/clients/copilot-cli.key"
    key.parent.mkdir(parents=True)
    key.write_text("fixture-key")
    key.chmod(mode)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    stub = binaries / "stat"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import os, stat, sys\n"
        "if sys.argv[1] == '-f':\n"
        "    print('GNU filesystem output before failure')\n"
        "    sys.exit(1)\n"
        "print(format(stat.S_IMODE(os.stat(sys.argv[-1]).st_mode), 'o'))\n"
    )
    stub.chmod(0o700)
    child = binaries / "copilot"
    child.write_text('#!/bin/sh\nprintf "child-ran\\n"\n')
    child.chmod(0o700)
    env = {
        "HOME": str(home),
        "PATH": f"{binaries}:/usr/bin:/bin",
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": "fixture/model",
    }
    script = Path(__file__).resolve().parents[2] / "copilot-litellm"
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    assert ("child-ran" in result.stdout) is (expected == 0)
    if expected:
        assert "mode 0600" in result.stderr
