"""No-network tests for the isolated Gemini LiteLLM launcher."""

import importlib.machinery
import importlib.util
from pathlib import Path
import stat

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "gemini-litellm"
LOADER = importlib.machinery.SourceFileLoader("gemini_litellm_launcher", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
MODULE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MODULE)


def good():
    return {
        "DOTFILES_RUN_GEMINI_SETUP": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_GEMINI_LITELLM_MODEL": "gemini-3.8-flash",
        "LITELLM_PORT": "4000",
    }


def test_prepare_is_explicit_scoped_and_non_mutating():
    env = good()
    model, endpoint, child = MODULE.prepare(env, key="secret-only-for-test")
    assert model == "gemini-3.8-flash"
    assert endpoint == "http://127.0.0.1:4000"
    assert child["GEMINI_API_KEY"] == "secret-only-for-test"
    assert child["GOOGLE_GEMINI_BASE_URL"] == endpoint
    assert "GEMINI_API_KEY" not in env


@pytest.mark.parametrize(
    "invalid",
    [
        "https://example.com",
        "http://169.254.169.254:4000",
        "http://localhost:4000/v1",
        "http://user:pass@localhost:4000",
        "http://127.0.0.1:4000?redirect=evil",
        "http://localhost:99999",
    ],
)
def test_endpoint_refuses_nonlocal_or_invalid(invalid):
    with pytest.raises(ValueError):
        MODULE.validate_endpoint(invalid)


def test_conflicts_and_gate_fail_closed():
    for name in (
        "DOTFILES_RUN_GEMINI_SETUP",
        "DOTFILES_RUN_LITELLM_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
    ):
        env = good()
        env[name] = "0"
        with pytest.raises(ValueError):
            MODULE.prepare(env)
    env = good()
    env["GEMINI_API_KEY"] = "actual-elsewhere"
    with pytest.raises(ValueError):
        MODULE.prepare(env, key="dedicated-scoped")
    env = good()
    env["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    with pytest.raises(ValueError):
        MODULE.prepare(env)


def test_key_read_requires_private_regular_file(tmp_path):
    path = tmp_path / "gemini.key"
    path.write_text("dummy-private-key", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert MODULE.read_private_key(path) == "dummy-private-key"
    path.chmod(0o644)
    with pytest.raises(ValueError):
        MODULE.read_private_key(path)
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        MODULE.read_private_key(path)


def test_dry_run_never_reads_credentials(monkeypatch, capsys):
    for name, value in good().items():
        monkeypatch.setenv(name, value)
    assert MODULE.main(["--dry-run"]) == 0
    assert "UNKNOWN" in capsys.readouterr().out


def test_native_login_must_explicitly_select_api_key(tmp_path):
    import json

    home = tmp_path / "home"
    project = tmp_path / "repo"
    project.mkdir()
    settings = home / ".gemini/settings.json"
    settings.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="select Gemini API-key"):
        MODULE.require_api_key_auth(home, project)
    settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}})
    )
    with pytest.raises(ValueError, match="gemini-api-key"):
        MODULE.require_api_key_auth(home, project)
    settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}})
    )
    assert MODULE.require_api_key_auth(home, project) is True
    project_settings = project / ".gemini/settings.json"
    project_settings.parent.mkdir()
    project_settings.write_text(
        json.dumps({"security": {"auth": {"selectedType": "vertex-ai"}}})
    )
    with pytest.raises(ValueError, match="workspace"):
        MODULE.require_api_key_auth(home, project)
    project_settings.unlink()
    (project / ".gemini/.env").write_text("GEMINI_API_KEY=another-provider")
    with pytest.raises(ValueError, match="workspace Gemini .env"):
        MODULE.require_api_key_auth(home, project)


def test_dry_run_refuses_forwarded_unknown_flags(monkeypatch):
    for name, value in good().items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exc:
        MODULE.main(["--dry-run", "--surprise"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        MODULE.main(["--dry-run", "--model=direct-google"])
    assert exc.value.code == 2
