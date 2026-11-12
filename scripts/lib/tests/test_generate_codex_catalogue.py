"""No live credentials, network or host Codex configuration in these tests."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "generate-codex-catalogue.py"
SPEC = importlib.util.spec_from_file_location("codex_served_catalogue", SCRIPT)
catalogue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalogue)

SERVED = {
    "data": [
        {"id": "ollama/glm-5.3-flash:cloud", "object": "model"},
        {"id": "ollama/kimi-k3:cloud", "object": "model"},
        {"id": "Qwen3.8-27B-4bit", "object": "model"},
        {"id": "gemma4:12b-mxfp8", "object": "model"},
    ]
}


def test_slugs_match_registered_gateway_ids_exactly_not_ollama_shorthand():
    result = catalogue.generate_catalogue(
        SERVED,
        ["ollama/glm-5.3-flash:cloud", "Qwen3.8-27B-4bit"],
        65536,
    )
    assert result["_dotfiles_managed"] == catalogue.MANAGED
    assert [row["slug"] for row in result["models"]] == [
        "ollama/glm-5.3-flash:cloud",
        "Qwen3.8-27B-4bit",
    ]
    assert all(row["context_window"] == 65536 for row in result["models"])
    assert all(row["supports_parallel_tool_calls"] is False for row in result["models"])
    assert all(row["supported_reasoning_levels"] == [] for row in result["models"])


@pytest.mark.parametrize(
    "selected",
    [
        ["glm-5.3-flash:cloud"],
        ["ollama/kimi-k3:cloud", "kimi-k3:cloud"],
        ["gpt-6-astra"],
        ["Qwen3.8-27B-4bit", "Qwen3.8-27B-4bit"],
        ["model with spaces"],
        ["../danger"],
        [],
    ],
)
def test_unserved_ambiguous_or_unsafe_models_fail_closed(selected):
    with pytest.raises(ValueError):
        catalogue.generate_catalogue(SERVED, selected, 65536)


@pytest.mark.parametrize("payload", [{}, [], {"data": []}, {"data": [123]}])
def test_missing_or_malformed_inventory_is_not_success(payload):
    with pytest.raises(ValueError):
        catalogue.generate_catalogue(payload, ["gemma4:12b-mxfp8"], 65536)


@pytest.mark.parametrize("budget", [None, True, 0, 1024, 1048577])
def test_unqualified_context_budget_refused(budget):
    with pytest.raises(ValueError):
        catalogue.generate_catalogue(SERVED, ["gemma4:12b-mxfp8"], budget)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1/models",
        "http://127.0.0.1.example.com:4000/v1/models",
        "http://user:pass@127.0.0.1:4000/v1/models",
        "http://127.0.0.1:4000/v1/models?token=secret",
        "http://127.0.0.1:4000/v1/chat/completions",
        "http://127.0.0.1/v1/models",
        "http://127.0.0.1:99999/v1/models",
    ],
)
def test_no_nonlocal_or_wrong_wire_probe(url):
    with pytest.raises(ValueError):
        catalogue.validate_url(url)
    assert catalogue.validate_url("http://127.0.0.1:4000/v1/models")


def test_private_scoped_key_only_and_never_printed(tmp_path):
    path = tmp_path / "codex.key"
    path.write_text("synthetic-only-key\n", encoding="utf-8")
    path.chmod(0o600)
    assert catalogue.read_key(path) == "synthetic-only-key"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        catalogue.read_key(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink|unsafe"):
        catalogue.read_key(link)


def test_generated_file_is_private_idempotent_and_replacement_requires_intent(tmp_path):
    path = tmp_path / "codex" / "dotfiles-litellm-models.json"
    one = json.dumps(catalogue.generate_catalogue(SERVED, ["gemma4:12b-mxfp8"], 65536))
    two = json.dumps(
        catalogue.generate_catalogue(SERVED, ["ollama/kimi-k3:cloud"], 65536)
    )
    assert catalogue.save_catalogue(path, one, replace=False)
    assert path.stat().st_mode & 0o777 == 0o600
    assert catalogue.save_catalogue(path, one, replace=False) is False
    with pytest.raises(ValueError, match="differs"):
        catalogue.save_catalogue(path, two, replace=False)
    assert json.loads(path.read_text())["models"][0]["slug"] == "gemma4:12b-mxfp8"
    assert catalogue.save_catalogue(path, two, replace=True)
    assert json.loads(path.read_text())["models"][0]["slug"] == "ollama/kimi-k3:cloud"
    backups = list(path.parent.glob("dotfiles-litellm-models.json.*.bak"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(backups[0].read_text())["models"][0]["slug"] == "gemma4:12b-mxfp8"


def test_unowned_existing_catalogue_is_never_replaced(tmp_path):
    path = tmp_path / "codex" / "dotfiles-litellm-models.json"
    path.parent.mkdir(mode=0o700)
    path.write_text('{"models":[{"slug":"native-hosted"}]}', encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="differs"):
        catalogue.save_catalogue(path, '{"_dotfiles_managed":"new"}', replace=True)
    assert json.loads(path.read_text())["models"][0]["slug"] == "native-hosted"


def test_symlinked_catalogue_never_writes_outside_its_directory(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text("original", encoding="utf-8")
    (owned / "dotfiles-litellm-models.json").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        catalogue.save_catalogue(
            owned / "dotfiles-litellm-models.json", "new", replace=True
        )
    assert target.read_text(encoding="utf-8") == "original"


def test_gateway_model_fetch_uses_only_literal_loopback_and_never_follows_redirects(
    monkeypatch,
):
    calls = []
    status = 200

    class FakeConnection:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def request(self, method, target, headers):
            calls.append(("request", method, target, headers["Authorization"]))

        def getresponse(self):
            class Response:
                def read(self, limit):
                    assert limit == 1048577
                    return json.dumps(SERVED).encode("utf-8")

            response = Response()
            response.status = status
            return response

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(catalogue.http.client, "HTTPConnection", FakeConnection)
    assert (
        catalogue.fetch_served("http://localhost:4000/v1/models", "dummy-key") == SERVED
    )
    assert calls == [
        ("connect", "127.0.0.1", 4000, 8),
        ("request", "GET", "/v1/models", "Bearer dummy-key"),
        ("close",),
    ]

    # A gateway sending Location: https://host.example/ must never cause a
    # second connection or leak the scoped Authorization value.
    status = 302
    calls.clear()
    with pytest.raises(ValueError, match="unavailable or key denied"):
        catalogue.fetch_served("http://127.0.0.1:4000/v1/models", "dummy-key")
    assert calls == [
        ("connect", "127.0.0.1", 4000, 8),
        ("request", "GET", "/v1/models", "Bearer dummy-key"),
        ("close",),
    ]


def test_catalogue_http_failure_closes_connection_and_hides_low_level_details(
    monkeypatch,
):
    closed = []

    class FailingConnection:
        def __init__(self, _host, _port, timeout):
            assert timeout == 8

        def request(self, _method, _target, headers):
            assert headers["Authorization"] == "Bearer synthetic-key"
            raise OSError("SYNTHETIC_REQUEST_HEADER_SHOULD_NOT_LEAK")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(catalogue.http.client, "HTTPConnection", FailingConnection)
    with pytest.raises(ValueError, match="unavailable or key denied") as error:
        catalogue.fetch_served("http://127.0.0.1:4000/v1/models", "synthetic-key")
    assert "SYNTHETIC_REQUEST" not in str(error.value)
    assert closed == [True]


def test_new_catalogue_write_failure_is_atomic_and_leaves_no_partial_target(
    tmp_path, monkeypatch
):
    target = tmp_path / "dotfiles-litellm-models.json"
    content = json.dumps(
        catalogue.generate_catalogue(SERVED, ["gemma4:12b-mxfp8"], 65536)
    )

    def fail_link(_source, _destination):
        raise OSError("synthetic disk or filesystem failure")

    monkeypatch.setattr(catalogue.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic"):
        catalogue.save_catalogue(target, content, replace=False)
    assert not target.exists()
    assert list(tmp_path.glob(".dotfiles-codex-*")) == []
