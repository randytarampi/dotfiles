import subprocess

import discover_models
import local_engines
import omlx


def test_ollama_successful_empty_is_distinct_from_failure(monkeypatch):
    monkeypatch.setattr(discover_models, "find_ollama", lambda: "/bin/ollama")
    monkeypatch.setattr(
        discover_models.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "NAME ID SIZE\n", ""
        ),
    )
    result = discover_models._list_local_ollama_discovery()
    assert result.status == "ok_empty"
    assert result.records == ()


def test_ollama_models_return_records(monkeypatch):
    monkeypatch.setattr(discover_models, "find_ollama", lambda: "/bin/ollama")
    monkeypatch.setattr(
        discover_models.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "NAME ID SIZE\nmodel:latest abc 2 GB\n", ""
        ),
    )
    result = discover_models._list_local_ollama_discovery()
    assert result.status == "ok_models"
    assert result.records[0]["name"] == "model:latest"


def test_ollama_binary_missing_and_subprocess_failures_are_unknown(monkeypatch):
    monkeypatch.setattr(discover_models, "find_ollama", lambda: "")
    missing = discover_models._list_local_ollama_discovery()
    assert missing.status == "unknown_error"
    assert missing.records == ()

    monkeypatch.setattr(discover_models, "find_ollama", lambda: "/bin/ollama")
    for error in (subprocess.TimeoutExpired("ollama", 5), OSError("unavailable")):
        monkeypatch.setattr(
            discover_models.subprocess,
            "run",
            lambda *a, _e=error, **k: (_ for _ in ()).throw(_e),
        )
        result = discover_models._list_local_ollama_discovery()
        assert result.status == "unknown_error"
        assert result.records == ()


def test_omlx_http_auth_failure_is_unknown(monkeypatch):
    import urllib.error

    monkeypatch.setattr(
        omlx,
        "_get_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError("http://localhost", 403, "Forbidden", None, None)
        ),
    )
    result = omlx.list_omlx_discovery()
    assert result.status == "unknown_error"
    assert result.records == ()
    assert "HTTPError" in result.reason


def test_registry_health_failure_is_unknown(monkeypatch):
    monkeypatch.setitem(
        local_engines.LOCAL_ENGINES["omlx"],
        "health_check",
        lambda: (False, "unreachable"),
    )
    result = local_engines.iter_engine_discovery("omlx")
    assert result.status == "unknown_error"
    assert result.records == ()


def test_legacy_ollama_accessor_keeps_empty_list_contract(monkeypatch):
    monkeypatch.setattr(discover_models, "find_ollama", lambda: "")
    assert discover_models._list_local_ollama_models() == []
