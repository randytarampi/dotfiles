"""JetBrains profile generation must refuse an unauthorised gateway generation.

``generate-jetbrains-profiles.py`` writes gateway-only profiles that embed the
Junie client key, and both `configure-jetbrains-ai.py` and the generator can be
run directly. Both must refuse a stale, revoked or unattestable subscription
generation before any profile byte changes, while the gateway lane being off
must leave generation untouched.

Nothing here reaches a service, a real credential or a real home directory.
"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

import litellm_route_preflight as preflight
from gateway_authority import install_gateway_authority

SCRIPT = Path(__file__).resolve().parents[2] / "generate-jetbrains-profiles.py"
SPEC = importlib.util.spec_from_file_location("jetbrains_gateway_auth_test", SCRIPT)
PROFILES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILES)

# A direct-provider group that LiteLLM routes, so the plan publishes a
# gateway-only profile exactly as production does.
ROUTED_MODEL = "gpt-6-luna"
ROUTED_ALIAS = f"openai/{ROUTED_MODEL}"

REFUSALS = [
    pytest.param({"projection": "missing"}, id="missing-projection"),
    pytest.param({"projection": "unreadable"}, id="unreadable-projection"),
    pytest.param({"projection": "forged"}, id="forged-projection"),
    pytest.param({"receipt": preflight.REFUSED_NO_RECEIPT}, id="missing-receipt"),
    pytest.param({"receipt": preflight.REFUSED_STALE_GENERATION}, id="stale-receipt"),
    pytest.param({"report_ids": []}, id="revoked-report"),
    pytest.param({"report_state": "missing"}, id="missing-report"),
    pytest.param({"served": set()}, id="missing-advertised-alias"),
    pytest.param({"wire": "openai"}, id="wrong-wire-same-alias"),
    pytest.param({"oauth_expiry": None}, id="expired-oauth-cache"),
]


def _harness(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ai_dir = home / ".ai"
    ai_dir.mkdir(parents=True)
    junie_dir = home / ".junie"
    junie_dir.symlink_to(ai_dir, target_is_directory=True)
    target = junie_dir / "models"
    target.mkdir(mode=0o700)
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True, mode=0o700)
    clients.chmod(0o700)
    key_file = clients / "junie.key"
    key_file.write_text("dummy-junie-key-one", encoding="utf-8")
    key_file.chmod(0o600)

    groups_path = tmp_path / "groups.json"
    groups_path.write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "baseUrl": "https://api.example/v1",
                        "apiType": "OpenAICompletion",
                        "apiKeyEnv": "OPENAI_API_KEY",
                    },
                    "litellm": {
                        "baseUrl": "http://127.0.0.1:4000/v1",
                        "apiType": "OpenAICompletion",
                        "apiKeyEnv": "LITELLM_JUNIE_KEY",
                    },
                },
                "groups": {},
            }
        ),
        encoding="utf-8",
    )

    registry = {
        "presets": {
            "pro": {
                "orchestrator": {"model": f"openai/{ROUTED_MODEL}"},
                "librarian": {"model": f"openai/{ROUTED_MODEL}"},
            }
        },
        "_tiers": {},
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_JUNIE_KEY", "stale-env-dummy")
    monkeypatch.setattr(PROFILES, "load_env", lambda: True)
    monkeypatch.setattr(PROFILES.tier_registry, "load_registry", lambda: registry)
    monkeypatch.setattr(PROFILES, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(PROFILES, "append_pool_profile_specs", lambda specs: None)

    import model_stamp

    monkeypatch.setattr(
        model_stamp, "STAMP_PATH", home / ".local/share/dotfiles/model-sync-stamp"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--groups-json",
            str(groups_path),
            "--target-dir",
            str(target),
            "--local-models",
            "[]",
        ],
    )
    return home, target


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("authority", REFUSALS)
def test_gateway_profiles_refuse_unauthorised_generation(
    tmp_path, monkeypatch, authority
):
    home, target = _harness(tmp_path, monkeypatch)
    advertised = install_gateway_authority(
        home, monkeypatch, planned={ROUTED_ALIAS}, **authority
    )
    monkeypatch.setattr(PROFILES, "litellm_catalogue_models", lambda *_args: advertised)
    before = _snapshot(target)

    with pytest.raises(SystemExit) as error:
        PROFILES.main()

    assert error.value.code == 1
    assert _snapshot(target) == before


def test_gateway_profiles_refuse_when_the_catalogue_is_unknown(tmp_path, monkeypatch):
    """An unreachable catalogue can never authorise a gateway-only write."""
    import model_catalogues

    home, target = _harness(tmp_path, monkeypatch)
    install_gateway_authority(home, monkeypatch, planned={ROUTED_ALIAS})
    monkeypatch.setattr(PROFILES, "litellm_catalogue_models", lambda *_args: None)
    monkeypatch.setattr(model_catalogues, "get_models", lambda *args, **kwargs: None)
    before = _snapshot(target)

    with pytest.raises(SystemExit) as error:
        PROFILES.main()

    assert error.value.code == 1
    assert _snapshot(target) == before


def test_gateway_profiles_are_written_for_a_valid_generation(tmp_path, monkeypatch):
    home, target = _harness(tmp_path, monkeypatch)
    advertised = install_gateway_authority(home, monkeypatch, planned={ROUTED_ALIAS})
    monkeypatch.setattr(PROFILES, "litellm_catalogue_models", lambda *_args: advertised)

    PROFILES.main()

    generated = sorted(path.name for path in target.glob("*.json"))
    assert generated == ["pro.json"]
    assert ROUTED_ALIAS in (target / "pro.json").read_text(encoding="utf-8")


def test_gateway_lane_off_is_never_guarded(tmp_path, monkeypatch):
    """With the subscription lane off there is no generation to attest."""
    _home, target = _harness(tmp_path, monkeypatch)
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(PROFILES, "litellm_catalogue_models", lambda *_args: set())

    PROFILES.main()

    generated = sorted(path.name for path in target.glob("*.json"))
    assert generated == ["pro.json"]
    assert "gpt-6-luna" in (target / "pro.json").read_text(encoding="utf-8")
