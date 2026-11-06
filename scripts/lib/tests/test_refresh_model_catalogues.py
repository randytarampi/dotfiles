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


def test_zero_price_filter_requires_complete_valid_cost_evidence():
    assert MODULE.is_zero_priced({"input": "0", "output": 0})
    assert MODULE.is_zero_priced(
        {"input": 0, "output": 0, "cache_read": 0, "context_over_200k": {"input": 0}}
    )
    for cost in (
        {"input": 0.01, "output": 0},
        {"input": 0, "output": 0, "cache_read": 0.001},
        {"input": 0, "output": "unknown"},
        {"input": 0, "output": None},
        {"input": 0, "output": False},
        {"input": 0, "output": 0, "cache_write": -1},
        {"input": {"nested": 0}, "output": 0},
        {"input": 0, "output": {"nested": 0}},
        {"input": 0, "output": float("nan")},
        {"input": 0},
        {"prompt": 0, "completion": 0},
        None,
    ):
        assert not MODULE.is_zero_priced(cost)
    assert MODULE.valid_pricing({"input": 1, "output": 0})
    assert not MODULE.valid_pricing({"input": 0, "output": None})


def metadata_snapshot(models, *, observed="2026-11-06T18:58:00+00:00"):
    return {
        "source": "models.dev",
        "observed_at": observed,
        "expires_at": "2026-11-07T18:58:00+00:00",
        "models": {"opencode": {"models": models}},
    }


def test_unavailable_refresh_writes_artefact_without_allowlist_changes(
    tmp_path, monkeypatch
):
    output = tmp_path / "artifacts/model-catalogues/opencode-zen-free.json"
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    assert MODULE.refresh(output) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert "fetched_at" in payload


def test_console_listing_uses_documented_url_and_strict_auth(
    tmp_path, monkeypatch
):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic-console-key")
    observed = []

    def fetch(url, api_key, *, strict):
        observed.append((url, api_key, strict))
        return {"object": "list", "data": [{"id": "candidate", "object": "model"}]}

    monkeypatch.setattr(MODULE, "get_catalogue", fetch)
    monkeypatch.setattr(
        MODULE,
        "fetch_models_dev_snapshot",
        lambda: metadata_snapshot(
            {"candidate": {"cost": {"input": 0, "output": 0}}}
        ),
    )
    assert MODULE.refresh(output) == 0
    assert observed == [
        ("https://opencode.ai/inference/v1/models", "synthetic-console-key", True)
    ]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source"] == "https://opencode.ai/inference/v1/models"
    assert [m["id"] for m in payload["models"]] == ["candidate"]


@pytest.mark.parametrize("status_code", [401, 403])
def test_console_auth_failure_fails_closed(status_code, tmp_path, monkeypatch):
    import urllib.error

    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic-console-key")

    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://opencode.ai/inference/v1/models",
            status_code,
            "Unauthorized",
            None,
            None,
        )

    monkeypatch.setattr(MODULE, "get_catalogue", unavailable)
    assert MODULE.refresh(output) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert payload["source"] == "https://opencode.ai/inference/v1/models"
    assert "synthetic-console-key" not in json.dumps(payload)


def test_successful_refresh_joins_realistic_console_ids_to_exact_metadata(
    tmp_path, monkeypatch
):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    # The current Console model response supplies IDs, not prices.
    monkeypatch.setattr(
        MODULE,
        "get_catalogue",
        lambda url, api_key, **kwargs: {
            "object": "list",
            "data": [
                {"id": "paid-model", "object": "model", "owned_by": "opencode"},
                {"id": "big-pickle", "object": "model", "owned_by": "opencode"},
                {"id": "not-a-free-suffix", "object": "model"},
            ],
        },
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_models_dev_snapshot",
        lambda: metadata_snapshot(
            {
                "big-pickle": {"cost": {"input": 0, "output": 0, "cache_read": 0}},
                "paid-model": {"cost": {"input": 1, "output": 2}},
                "not-a-free-suffix": {"cost": {"input": 0, "output": 0}},
                "not-serving": {"cost": {"input": 0, "output": 0}},
            }
        ),
    )
    assert MODULE.refresh(output) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "available"
    assert payload["pricing_source"] == MODULE.MODELS_DEV_URL
    assert payload["counts"] == {
        "catalogue": 3,
        "classified": 3,
        "free": 2,
        "unclassified": 0,
    }
    assert [model["id"] for model in payload["models"]] == [
        "big-pickle",
        "not-a-free-suffix",
    ]
    assert payload["models"][0]["pricing"]["cache_read"] == 0
    assert "not-serving" not in [m["id"] for m in payload["models"]]


def test_missing_or_invalid_price_is_incomplete_and_never_available(
    tmp_path, monkeypatch
):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE,
        "get_catalogue",
        lambda *args, **kwargs: {
            "data": [{"id": "free"}, {"id": "unknown"}, {"id": "invalid"}]
        },
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_models_dev_snapshot",
        lambda: metadata_snapshot(
            {
                "free": {"cost": {"input": 0, "output": 0}},
                "invalid": {"cost": {"input": 0, "output": None}},
            }
        ),
    )
    assert MODULE.refresh(output) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "incomplete"
    assert payload["counts"] == {
        "catalogue": 3,
        "classified": 1,
        "free": 1,
        "unclassified": 2,
    }
    assert payload["unclassified_model_ids"] == ["invalid", "unknown"]
    assert [m["id"] for m in payload["models"]] == ["free"]


def test_missing_pricing_snapshot_is_unavailable(tmp_path, monkeypatch):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE,
        "get_catalogue",
        lambda *args, **kwargs: {"data": [{"id": "big-pickle"}]},
    )

    def cannot_fetch():
        raise RuntimeError("credential-like text should not leak")

    monkeypatch.setattr(MODULE, "fetch_models_dev_snapshot", cannot_fetch)
    assert MODULE.refresh(output) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert payload["counts"]["unclassified"] == 1
    assert "credential-like" not in json.dumps(payload)


@pytest.mark.parametrize("data", [[], {}, [{"id": "x"}, {"id": "x"}], [42]])
def test_empty_malformed_or_duplicate_console_catalogue_is_unavailable(
    data, tmp_path, monkeypatch
):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(MODULE, "get_catalogue", lambda *args, **kwargs: {"data": data})
    assert MODULE.refresh(output) == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "unavailable"


def test_unchanged_catalogue_ignores_price_snapshot_observation_time(
    tmp_path, monkeypatch
):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE, "get_catalogue", lambda *args, **kwargs: {"data": [{"id": "a"}]}
    )
    observed = ["2026-11-06T18:58:00+00:00"]

    def snapshot():
        return metadata_snapshot(
            {"a": {"cost": {"input": 0, "output": 0}}}, observed=observed[0]
        )

    monkeypatch.setattr(MODULE, "fetch_models_dev_snapshot", snapshot)
    assert MODULE.refresh(output) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "available"
    observed[0] = "2026-11-06T19:00:00+00:00"
    assert MODULE.refresh(output) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "unchanged"


def test_dry_run_fetches_but_does_not_write(tmp_path, monkeypatch):
    output = tmp_path / "catalogue.json"
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(
        MODULE, "get_catalogue", lambda *args, **kwargs: {"data": [{"id": "x"}]}
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_models_dev_snapshot",
        lambda: metadata_snapshot({"x": {"cost": {"input": 0, "output": 0}}}),
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


def test_litellm_default_loopback_port_is_static_when_setup_is_off(monkeypatch):
    monkeypatch.delenv("LITELLM_PORT", raising=False)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    CATALOGUES._validate_catalogue_url("http://127.0.0.1:4000/v1/models")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "0")
    CATALOGUES._validate_catalogue_url("http://127.0.0.1:4000/v1/models")


def test_litellm_custom_port_requires_valid_gate_and_loopback(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_PORT", "4317")
    CATALOGUES._validate_catalogue_url("http://localhost:4317/v1/models")
    for value in ("0", "65536", "not-a-port", "-1"):
        monkeypatch.setenv("LITELLM_PORT", value)
        with pytest.raises(ValueError):
            CATALOGUES._validate_catalogue_url("http://127.0.0.1:4000/v1/models")
    monkeypatch.setenv("LITELLM_PORT", "4317")
    for url in (
        "http://proxy.example:4317/v1/models",
        "http://user:pass@127.0.0.1:4317/v1/models",
    ):
        with pytest.raises(ValueError):
            CATALOGUES._validate_catalogue_url(url)


def test_unsafe_full_url_override_is_rejected(monkeypatch):
    for value in (
        "file:///tmp/models.json",
        "http://provider-proxy.invalid/v1",
        "https://user:" + "pass" + "@api.openai.com/v1",
    ):
        monkeypatch.setenv("OPENAI_BASE_URL", value)
        with pytest.raises(ValueError):
            CATALOGUES._validate_catalogue_url(value)
