"""Direct client writers must refuse an unauthorised gateway generation (#361-B1).

``configure-all.sh`` preflights ``scripts/reconcile-litellm-routes.py`` before
the fleet writes gateway-only clients, but ``configure-opencode.py`` and
``configure-pi.py`` can also be run directly. Each refused case below asserts
that the writer exits non-zero and leaves its target tree byte-identical, with
no child process started. Nothing here reaches a service, a real credential or
a real home directory.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import litellm_route_preflight as preflight
from gateway_authority import (
    FIXTURE_ALIAS,
    install_client_key,
    install_gateway_authority,
)

ROOT = Path(__file__).resolve().parents[3]
OPENCODE = ROOT / "scripts" / "configure-opencode.py"
PI = ROOT / "scripts" / "configure-pi.py"

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


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- OpenCode -------------------------------------------------------------


class _ReachedPostGuard(BaseException):
    """Sentinel raised where OpenCode starts building the payload."""


def _opencode(monkeypatch, tmp_path, authority, *, dry_run=False):
    script = _load_module("configure_opencode_authorization", OPENCODE)
    home = tmp_path / "home"
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    output = config_dir / "opencode.json"
    output.write_text('{"mcp": {}, "sentinel": true}\n', encoding="utf-8")
    (config_dir / "oh-my-opencode-slim.json").write_text(
        '{"preset": "local"}\n', encoding="utf-8"
    )

    monkeypatch.setenv("HOME", str(home))
    # Ambient credentials must not shadow the fixture's own key file.
    monkeypatch.delenv("LITELLM_OPENCODE_KEY", raising=False)
    monkeypatch.delenv("LITELLM_PI_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    monkeypatch.delenv("DOTFILES_OPENCODE_TIER", raising=False)
    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_PRESET", raising=False)
    argv = ["configure-opencode.py", "--preset", "local", "--mode", "global"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(script, "load_env", lambda *a, **k: False)
    monkeypatch.setattr(script, "list_local_ollama_models", lambda: [])

    started = []

    def child(args, **kwargs):
        name = Path(args[1]).name if len(args) > 1 else ""
        started.append(name)
        if name != "configure-mcp-tool.py":
            pytest.fail(f"unexpected child process {name!r}")
        return subprocess.CompletedProcess(args, 0, '{"mcp": {}}', "")

    monkeypatch.setattr(script.subprocess, "run", child)
    install_gateway_authority(home, monkeypatch, **authority)
    install_client_key(home, "opencode.key")
    return script, home, config_dir, output, started


@pytest.mark.parametrize("authority", REFUSALS)
def test_opencode_refuses_unauthorised_gateway_generation(
    tmp_path, monkeypatch, authority
):
    script, _home, config_dir, output, started = _opencode(
        monkeypatch, tmp_path, authority
    )
    before = _snapshot(config_dir)

    with pytest.raises(SystemExit) as error:
        script.main.__wrapped__()

    assert error.value.code == 1
    assert _snapshot(config_dir) == before
    assert output.read_bytes() == before["opencode.json"]
    assert started == []


def test_opencode_refuses_when_the_client_key_is_absent(tmp_path, monkeypatch):
    script, home, config_dir, output, started = _opencode(monkeypatch, tmp_path, {})
    (home / ".local/share/litellm/clients/opencode.key").unlink()
    before = _snapshot(config_dir)

    with pytest.raises(SystemExit) as error:
        script.main.__wrapped__()

    assert error.value.code == 1
    assert _snapshot(config_dir) == before
    assert started == []


def test_opencode_authorised_dry_run_writes_nothing(tmp_path, monkeypatch):
    script, _home, config_dir, output, started = _opencode(
        monkeypatch, tmp_path, {}, dry_run=True
    )
    monkeypatch.setattr(
        script,
        "fetch_models_dev",
        lambda *a, **k: (_ for _ in ()).throw(_ReachedPostGuard()),
    )
    before = _snapshot(config_dir)

    with pytest.raises(_ReachedPostGuard):
        script.main.__wrapped__()

    assert _snapshot(config_dir) == before
    assert output.read_bytes() == before["opencode.json"]
    assert started == ["configure-mcp-tool.py"]


def test_opencode_gate_off_never_consults_the_authority(tmp_path, monkeypatch):
    script, _home, config_dir, _output, _started = _opencode(
        monkeypatch, tmp_path, {"projection": "missing"}
    )
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        preflight,
        "authorize_gateway_client",
        lambda *a, **k: pytest.fail("direct mode must not authorise a gateway"),
    )
    monkeypatch.setattr(
        script,
        "fetch_models_dev",
        lambda *a, **k: (_ for _ in ()).throw(_ReachedPostGuard()),
    )
    before = _snapshot(config_dir)

    with pytest.raises(_ReachedPostGuard):
        script.main.__wrapped__()

    assert _snapshot(config_dir) == before


def test_opencode_gate_off_reaches_the_payload_without_refusing(tmp_path, monkeypatch):
    """With the OAuth gate off the lane is unused, so nothing can be stale."""
    script, _home, _config_dir, _output, _started = _opencode(
        monkeypatch, tmp_path, {"gate": "0"}
    )
    monkeypatch.setattr(
        script,
        "fetch_models_dev",
        lambda *a, **k: (_ for _ in ()).throw(_ReachedPostGuard()),
    )

    with pytest.raises(_ReachedPostGuard):
        script.main.__wrapped__()


def test_opencode_unreadable_config_is_quarantined_before_the_guard(
    tmp_path, monkeypatch
):
    script, _home, config_dir, output, _started = _opencode(
        monkeypatch, tmp_path, {"receipt": preflight.REFUSED_STALE_GENERATION}
    )
    output.write_text("{not json", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        script.main.__wrapped__()

    assert error.value.code == 1
    assert output.read_text(encoding="utf-8") == "{not json"
    quarantined = sorted(config_dir.glob("opencode.json.unreadable-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"{not json"
    assert oct(quarantined[0].stat().st_mode & 0o777) == "0o600"


# --- Pi -------------------------------------------------------------------


_SENTINELS = {
    "settings.json": b'{"sentinel": true}\n',
    "models.json": b'{"providers": {}}\n',
    "auth.json": b'{"api_keys": {}}\n',
}


def _pi(monkeypatch, tmp_path, authority):
    script = _load_module("configure_pi_authorization", PI)
    home = tmp_path / "home"
    agent = home / ".pi/agent"
    agent.mkdir(parents=True)
    for name, content in _SENTINELS.items():
        (agent / name).write_bytes(content)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.delenv("DOTFILES_PI_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["configure-pi.py", "--preset", "pro-plus", "--no-local-fallbacks"]
    )
    install_client_key(home, "pi.key")
    served = install_gateway_authority(home, monkeypatch, **authority)

    # Reuse the static free-preset role shape so the transport path is exercised
    # without the dynamic evidence selector, which has its own suite.
    registry = script.tier_registry.load_registry(script.SLIM)
    registry["presets"]["pro-plus"] = registry["presets"]["free"]
    monkeypatch.setattr(script.tier_registry, "load_registry", lambda *a: registry)
    needed = {FIXTURE_ALIAS}
    for entry in registry["presets"]["pro-plus"].values():
        model = entry.get("model") if isinstance(entry, dict) else entry
        if isinstance(model, str) and "/" in model:
            needed.add(model)
    # Every advertised id reaches Pi's model list; only the openai/ subset is
    # attested by the projection, which is the set the guard compares. An
    # explicit ``served`` override keeps the exact set so the negative case can
    # withhold the alias.
    advertised = needed if "served" not in authority else set(served)
    monkeypatch.setattr(
        script,
        "get_catalogue",
        lambda *a, **k: {"data": [{"id": alias} for alias in sorted(advertised)]},
    )
    monkeypatch.setattr(script, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(script, "_ensure_packages", lambda *a, **k: None)
    monkeypatch.setattr(script, "seed_plugin_configs", lambda *a, **k: None)
    return script, agent, advertised


@pytest.mark.parametrize("authority", REFUSALS)
def test_pi_refuses_unauthorised_gateway_generation(tmp_path, monkeypatch, authority):
    script, agent, _advertised = _pi(monkeypatch, tmp_path, authority)
    before = _snapshot(agent)

    assert script.main() == 1

    assert _snapshot(agent) == before


def test_pi_authorised_gateway_generation_writes_its_own_routes(tmp_path, monkeypatch):
    script, agent, _advertised = _pi(monkeypatch, tmp_path, {})

    assert script.main() is None

    settings = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    models = json.loads((agent / "models.json").read_text(encoding="utf-8"))
    assert settings["defaultProvider"] == "litellm"
    for name in ("settings.json", "models.json", "auth.json"):
        assert (agent / name).read_bytes() != _SENTINELS[name]
    proxy = models["providers"]["litellm"]
    assert proxy["baseUrl"] == "http://127.0.0.1:4000/v1"
    assert proxy["apiKey"].startswith("!cat ")
    assert FIXTURE_ALIAS in {entry["id"] for entry in proxy["models"]}


def test_pi_direct_mode_ignores_the_gateway_authority(tmp_path, monkeypatch):
    script, agent, _advertised = _pi(monkeypatch, tmp_path, {"projection": "missing"})
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        preflight,
        "authorize_gateway_client",
        lambda *a, **k: pytest.fail("direct mode must not authorise a gateway"),
    )

    assert script.main() is None

    settings = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    assert settings["defaultProvider"] != "litellm"


def test_pi_gateway_dry_run_leaves_output_unchanged(tmp_path, monkeypatch):
    script, agent, _advertised = _pi(monkeypatch, tmp_path, {})
    monkeypatch.setattr(
        sys, "argv", ["configure-pi.py", "--preset", "pro-plus", "--dry-run"]
    )
    before = _snapshot(agent)

    assert script.main() is None

    assert _snapshot(agent) == before


def test_pi_profiles_only_needs_no_gateway_authority(tmp_path, monkeypatch):
    script = _load_module("configure_pi_profiles_authorization", PI)
    home = tmp_path / "home"
    agent = home / ".pi/agent"
    agent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        sys, "argv", ["configure-pi.py", "--preset", "anthropic", "--profiles-only"]
    )
    monkeypatch.setattr(
        preflight,
        "authorize_gateway_client",
        lambda *a, **k: pytest.fail("--profiles-only must stay detached"),
    )
    monkeypatch.setattr(script, "load_env", lambda *a, **k: pytest.fail("env load"))

    assert script.main() == 0

    profile = agent / "profiles/pi-subagents/anthropic.json"
    assert profile.is_file()
    assert not (agent / "settings.json").exists()
