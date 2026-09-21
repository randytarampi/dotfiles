import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "configure-aws.py"


def run(home, **values):
    env = {k: os.environ[k] for k in ("PATH", "PYTHONPATH") if k in os.environ}
    env.update({k: str(v) for k, v in values.items()})
    env["HOME"] = str(home)
    return subprocess.run(
        ["python3", str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True
    )


def test_gate_off(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0
    assert not (tmp_path / ".aws/config").exists()


def test_creates_region(tmp_path):
    result = run(
        tmp_path, DOTFILES_RUN_AWS_CONFIG_SETUP=1, DOTFILES_AWS_REGION="ca-central-1"
    )
    assert result.returncode == 0
    assert (
        tmp_path / ".aws/config"
    ).read_text() == "[default]\nregion = ca-central-1\noutput = json\n"


def test_existing_untouched(tmp_path):
    path = tmp_path / ".aws/config"
    path.parent.mkdir()
    path.write_text("[profile user]\nregion = eu-west-1\n")
    result = run(tmp_path, DOTFILES_RUN_AWS_CONFIG_SETUP=1)
    assert result.returncode == 0
    assert path.read_text() == "[profile user]\nregion = eu-west-1\n"


def test_malformed_reported(tmp_path):
    path = tmp_path / ".aws/config"
    path.parent.mkdir()
    path.write_text("not ini\n")
    result = run(tmp_path, DOTFILES_RUN_AWS_CONFIG_SETUP=1)
    assert result.returncode == 1
    assert path.read_text() == "not ini\n"
