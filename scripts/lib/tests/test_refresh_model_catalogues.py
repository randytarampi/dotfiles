import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "refresh_model_catalogues", ROOT / "scripts/refresh-model-catalogues.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
import model_catalogues as CATALOGUES  # noqa: E402  # loader bootstraps scripts/lib.


def test_zero_price_filter_requires_numeric_zero_entries():
    assert MODULE.is_zero_priced({"prompt": "0", "completion": 0})
    assert not MODULE.is_zero_priced({"prompt": "0.01", "completion": "0"})
    assert not MODULE.is_zero_priced({"prompt": "0", "completion": "unknown"})
    assert not MODULE.is_zero_priced({"prompt": 0, "completion": None})
    assert not MODULE.is_zero_priced({"prompt": 0, "completion": False})
    assert MODULE.is_zero_priced({"prompt": 0, "completion": "0"})
    assert not MODULE.is_zero_priced(None)


def test_unavailable_refresh_writes_artefact_without_allowlist_changes(
    tmp_path, monkeypatch
):
    output = tmp_path / "artifacts/model-catalogues/opencode-zen-free.json"
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    assert MODULE.refresh(output) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert "fetched_at" in payload


def test_successful_refresh_writes_free_models(tmp_path, monkeypatch):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE,
        "get_catalogue",
        lambda url, api_key, **kwargs: {
            "data": [
                {"id": "free-model", "pricing": {"prompt": 0, "completion": "0"}},
                {"id": "paid-model", "pricing": {"prompt": 1, "completion": 0}},
            ]
        },
    )
    assert MODULE.refresh(output) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "available"
    assert [model["id"] for model in payload["models"]] == ["free-model"]


def test_unchanged_catalogue_is_marked_without_publication_churn(tmp_path, monkeypatch):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE,
        "get_catalogue",
        lambda url, api_key, **kwargs: {
            "data": [
                {"id": "free-model", "pricing": {"prompt": 0, "completion": 0}},
                {"id": "another-free-model", "pricing": {"prompt": 0, "completion": 0}},
            ]
        },
    )
    assert MODULE.refresh(output) == 0
    monkeypatch.setattr(
        MODULE,
        "get_catalogue",
        lambda url, api_key, **kwargs: {
            "data": [
                {"id": "another-free-model", "pricing": {"prompt": 0, "completion": 0}},
                {"id": "free-model", "pricing": {"prompt": 0, "completion": 0}},
            ]
        },
    )
    assert MODULE.refresh(output) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "unchanged"


def test_dry_run_fetches_but_does_not_write(tmp_path, monkeypatch):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE, "get_catalogue", lambda url, api_key, **kwargs: {"data": []}
    )
    assert MODULE.refresh(output, dry_run=True) == 0
    assert not output.exists()


def test_configured_omlx_loopback_port_is_allowed(monkeypatch):
    monkeypatch.setenv("OMLX_PORT", "8100")
    CATALOGUES._validate_catalogue_url("http://127.0.0.1:8100/v1/models")


def test_full_url_omlx_loopback_override_is_allowed(monkeypatch):
    monkeypatch.delenv("OMLX_PORT", raising=False)
    monkeypatch.setenv("OMLX_BASE_URL", "http://127.0.0.1:8100")
    CATALOGUES._validate_catalogue_url("http://127.0.0.1:8100/v1/models")


def test_trusted_provider_override_host_is_allowed():
    CATALOGUES._validate_catalogue_url("https://api.openai.com/v1/models")


def test_full_url_https_provider_override_is_allowed(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider-proxy.invalid/v1")
    CATALOGUES._validate_catalogue_url("https://provider-proxy.invalid/v1/models")


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/models.json",
        "https://user:" + "pass" + "@api.openai.com/v1/models",
        "http://api.openai.com/v1/models",
    ],
)
def test_untrusted_scheme_or_userinfo_is_rejected(url):
    with pytest.raises(ValueError):
        CATALOGUES._validate_catalogue_url(url)


def test_non_configured_loopback_port_is_rejected(monkeypatch):
    monkeypatch.setenv("OMLX_PORT", "8100")
    with pytest.raises(ValueError):
        CATALOGUES._validate_catalogue_url("http://127.0.0.1:8101/v1/models")


def test_unsafe_full_url_override_is_rejected(monkeypatch):
    for value in (
        "file:///tmp/models.json",
        "http://provider-proxy.invalid/v1",
        "https://user:" + "pass" + "@api.openai.com/v1",
    ):
        monkeypatch.setenv("OPENAI_BASE_URL", value)
        with pytest.raises(ValueError):
            CATALOGUES._validate_catalogue_url(value)
