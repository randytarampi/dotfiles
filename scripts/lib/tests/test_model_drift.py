import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from model_catalogues import load_allowlists

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "check-model-drift.py"
SPEC = importlib.util.spec_from_file_location("check_model_drift", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
DRIFT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIFT)


def test_live_drift_allowlist_uses_ids_not_display_names(tmp_path):
    path = tmp_path / "openai.json"
    path.write_text('{"models": {"real-id": {"name": "Pretty name"}}}')
    assert load_allowlists({"openai": path}) == {"openai": {"real-id"}}


def test_catalogue_cache_fetches_same_url_and_key_once(monkeypatch):
    setattr(DRIFT, "_CATALOGUE_CACHE", {})
    calls = []
    payload = {"data": []}
    monkeypatch.setattr(
        DRIFT, "get_catalogue", lambda url, key: calls.append((url, key)) or payload
    )
    assert (
        DRIFT._cached_catalogue("https://example.test/v1/models", "secret") is payload
    )
    assert (
        DRIFT._cached_catalogue("https://example.test/v1/models", "secret") is payload
    )
    assert calls == [("https://example.test/v1/models", "secret")]


def test_slim_litellm_prefix_normalizes_before_allowlist_check(monkeypatch):
    monkeypatch.setattr(DRIFT, "load_allowlists", lambda _: {"openai": {"model"}})
    monkeypatch.setattr(DRIFT, "active_engines", lambda: [])
    data = {"model": "litellm/openai/model"}
    assert DRIFT.check_slim(data, proxy_mode=True) == []
    assert "direct mode" in DRIFT.check_slim(data, proxy_mode=False)[0]


def test_slim_litellm_unknown_normalized_identity_is_error(monkeypatch):
    monkeypatch.setattr(DRIFT, "load_allowlists", lambda _: {"openai": set()})
    monkeypatch.setattr(DRIFT, "active_engines", lambda: [])
    assert DRIFT.check_slim({"model": "litellm/openai/missing"}, proxy_mode=True)


def test_local_engine_drift_is_skipped_when_gate_is_off(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    assert DRIFT.check_local_engine_models() == []


def test_local_engine_drift_skips_when_engine_is_unreachable(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_engine_references", return_value={"missing"}),
        patch.object(DRIFT, "active_engines", return_value=["omlx"]),
        patch.object(DRIFT, "iter_engine_models_strict", return_value=None),
    ):
        assert DRIFT.check_local_engine_models() == []


def test_local_engine_drift_skips_catalogue_error_without_false_missing(monkeypatch):
    """Swallowed catalogue errors read as engine-unavailable, not all-missing.

    Exercises the real chain: the oMLX lister raises under strict mode →
    iter_engine_models_strict converts the exception to None → drift skips
    the engine instead of reporting every deployed model missing.
    """
    import local_engines
    import omlx

    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setitem(
        local_engines.LOCAL_ENGINES["omlx"], "health_check", lambda: (True, "")
    )
    with (
        patch.object(DRIFT, "deployed_engine_references", return_value={"missing"}),
        patch.object(DRIFT, "active_engines", return_value=["omlx"]),
        patch.object(omlx, "list_omlx_models", side_effect=RuntimeError("refused")),
    ):
        assert DRIFT.check_local_engine_models() == []


def test_omlx_lister_non_strict_returns_empty_on_error(monkeypatch):
    """Default listing stays byte-identical: errors collapse to []."""
    import local_engines
    import omlx

    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setitem(
        local_engines.LOCAL_ENGINES["omlx"], "health_check", lambda: (True, "")
    )
    with patch.object(omlx, "list_omlx_models", side_effect=RuntimeError("refused")):
        assert local_engines.iter_engine_models("omlx") == []


def test_local_engine_drift_reports_missing_deployed_model(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    with (
        patch.object(DRIFT, "deployed_engine_references", return_value={"missing"}),
        patch.object(DRIFT, "active_engines", return_value=["omlx"]),
        patch.object(
            DRIFT, "iter_engine_models_strict", return_value=[{"name": "present"}]
        ),
    ):
        assert DRIFT.check_local_engine_models() == [
            "omlx model missing is not present in the live catalogue"
        ]


def test_junie_profile_scan_skips_manifest_and_non_object_json(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / ".dotfiles-generated-profiles.json").write_text(
        json.dumps(["generated-profile"]), encoding="utf-8"
    )
    (models_dir / "invalid-shape.json").write_text("[]", encoding="utf-8")
    profile_path = models_dir / "actual-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "baseUrl": "http://127.0.0.1:4000/v1/chat/completions",
                "primaryModel": {"id": "legitimate-model-reference"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JUNIE_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("JUNIE_LOCAL_GROUPS", str(tmp_path / "missing-groups.json"))
    catalogue_requests = []

    def empty_catalogue(url, api_key=""):
        catalogue_requests.append(url)
        return {"data": []}

    monkeypatch.setattr(DRIFT, "get_catalogue", empty_catalogue)

    violations = DRIFT.check_junie_profiles()

    assert len(catalogue_requests) == 1
    assert violations == [
        f"{profile_path} points to missing model legitimate-model-reference at "
        "http://127.0.0.1:4000/v1/chat/completions"
    ]
    assert DRIFT.profile_models(models_dir / "invalid-shape.json") is None
