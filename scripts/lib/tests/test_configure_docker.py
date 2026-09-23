import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "configure-docker.py"


def run(home, path="", **values):
    env = {"HOME": str(home), "PATH": path, "PYTHONPATH": str(ROOT / "scripts" / "lib")}
    env.update({k: str(v) for k, v in values.items()})
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=ROOT, env=env, capture_output=True, text=True
    )


def test_gate_off(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0
    assert not (tmp_path / ".docker/config.json").exists()


def test_creates_baseline(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for executable in ("docker", "docker-credential-desktop"):
        path = bin_dir / executable
        path.touch()
        path.chmod(0o755)
    result = run(tmp_path, str(bin_dir), DOTFILES_RUN_DOCKER_CONFIG_SETUP=1)
    assert result.returncode == 0
    assert json.loads((tmp_path / ".docker/config.json").read_text()) == {
        "auths": {},
        "credsStore": "desktop",
    }


def test_existing_untouched(tmp_path):
    path = tmp_path / ".docker/config.json"
    path.parent.mkdir()
    path.write_text('{"auths": {"example": {"auth": "redacted"}}}\n')
    result = run(tmp_path, DOTFILES_RUN_DOCKER_CONFIG_SETUP=1)
    assert result.returncode == 0
    assert path.read_text() == '{"auths": {"example": {"auth": "redacted"}}}\n'


def test_malformed_reported(tmp_path):
    path = tmp_path / ".docker/config.json"
    path.parent.mkdir()
    path.write_text("not json\n")
    result = run(tmp_path, DOTFILES_RUN_DOCKER_CONFIG_SETUP=1)
    assert result.returncode == 1
    assert path.read_text() == "not json\n"


def test_omits_creds_store_without_helper(tmp_path):
    result = run(tmp_path, DOTFILES_RUN_DOCKER_CONFIG_SETUP=1)
    assert result.returncode == 0
    assert json.loads((tmp_path / ".docker/config.json").read_text()) == {
        "auths": {},
    }
