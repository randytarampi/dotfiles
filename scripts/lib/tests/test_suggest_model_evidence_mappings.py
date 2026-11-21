import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "suggest-model-evidence-mappings.py"
SPEC = importlib.util.spec_from_file_location("suggest_evidence", SCRIPT)
SUGGEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUGGEST)


class Discovery:
    def __init__(self, status, records=()):
        self.status = status
        self.records = tuple(records)


def install(monkeypatch, discovery, categories=None, calls=None):
    seen = calls if calls is not None else {}

    def probe(provider):
        seen[provider] = seen.get(provider, 0) + 1
        return discovery[provider]

    def resolve(
        records, profile=None, min_reasoning_embedding=0, category_observer=None
    ):
        seen["records"] = records
        seen["profile"] = profile
        seen["floor"] = min_reasoning_embedding
        if category_observer is not None:
            category_observer(
                categories
                if categories is not None
                else {
                    "reasoning": [],
                    "code-gen": list(records),
                    "lightweight": [],
                    "solo": [],
                }
            )

    monkeypatch.setattr(SUGGEST, "engine_gate_active", lambda provider: True)
    monkeypatch.setattr(SUGGEST, "iter_engine_discovery", probe)
    monkeypatch.setattr(SUGGEST, "resolve_roles_from_list", resolve)
    monkeypatch.setattr(SUGGEST, "load_snapshot", lambda path: None)
    monkeypatch.setattr(SUGGEST, "load_catalogues", lambda path, aa_evidence=None: [])
    return seen


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["suggest-model-evidence-mappings", *argv])
    return SUGGEST.main()


def test_live_capture_runs_each_engine_once_and_merges_exact_name(
    monkeypatch, capsys, tmp_path
):
    calls = install(
        monkeypatch,
        {
            "ollama": Discovery("ok_models", [{"name": "llama3:8b", "size_gb": 5}]),
            "omlx": Discovery(
                "ok_models",
                [{"name": "llama3:8b", "size_gb": 5, "quantization": "4bit"}],
            ),
        },
    )
    assert run(monkeypatch, "--map", str(tmp_path / "map.json")) == 0
    assert calls["ollama"] == 1 and calls["omlx"] == 1
    merged = calls["records"]
    assert [(m["provider"], m["name"]) for m in merged] == [("omlx", "llama3:8b")]
    assert merged[0]["equivalent_ollama_names"] == ["llama3:8b"]
    out = capsys.readouterr().out
    assert "omlx/llama3:8b" in out
    assert "ollama/llama3:8b" not in out
    assert "4bit" in out


def test_normalized_equivalent_keeps_omlx_and_unions_legacy_equivalents(
    monkeypatch, tmp_path
):
    calls = install(
        monkeypatch,
        {
            "ollama": Discovery("ok_models", [{"name": "qwen3.8:27b", "size_gb": 17}]),
            "omlx": Discovery(
                "ok_models",
                [
                    {
                        "name": "Qwen3.8-27B-MLX-4bit",
                        "size_gb": 16,
                        "equivalent_ollama_names": ["legacy-qwen"],
                    }
                ],
            ),
        },
    )
    assert run(monkeypatch, "--map", str(tmp_path / "map.json")) == 0
    merged = calls["records"]
    assert [m["name"] for m in merged] == ["Qwen3.8-27B-MLX-4bit"]
    assert merged[0]["equivalent_ollama_names"] == ["legacy-qwen", "qwen3.8:27b"]


def test_incomplete_discovery_refuses_without_suggestions(
    monkeypatch, capsys, tmp_path
):
    install(
        monkeypatch,
        {
            "ollama": Discovery("unknown_error"),
            "omlx": Discovery("ok_empty"),
        },
    )
    assert run(monkeypatch, "--map", str(tmp_path / "map.json")) == 1
    assert "local discovery incomplete" in capsys.readouterr().err
    assert not (tmp_path / "map.json").exists()


def test_empty_admitted_result_refuses_unfiltered_suggestions(
    monkeypatch, capsys, tmp_path
):
    install(
        monkeypatch,
        {
            "ollama": Discovery("ok_models", [{"name": "llama3:8b", "size_gb": 5}]),
            "omlx": Discovery("ok_empty"),
        },
        categories={"reasoning": [], "code-gen": [], "lightweight": [], "solo": []},
    )
    assert run(monkeypatch, "--map", str(tmp_path / "map.json")) == 1
    assert "no post-qualification local candidates" in capsys.readouterr().err


def test_profile_and_reasoning_floor_reach_the_resolver(monkeypatch, tmp_path):
    calls = install(
        monkeypatch,
        {
            "ollama": Discovery("ok_models", [{"name": "llama3:8b", "size_gb": 5}]),
            "omlx": Discovery("ok_empty"),
        },
    )
    assert (
        run(
            monkeypatch,
            "--map",
            str(tmp_path / "map.json"),
            "--profile",
            "quiet",
            "--min-reasoning-embedding",
            "8192",
        )
        == 0
    )
    assert calls["profile"] == "quiet"
    assert calls["floor"] == 8192


def test_offline_fixture_never_probes_services(monkeypatch, capsys, tmp_path):
    fixture = tmp_path / "models.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "name": "llama3:8b",
                    "provider": "ollama",
                    "capabilities": ["completion", "tools"],
                    "quantization": "Q4_K_M",
                }
            ]
        )
    )

    def fail(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("offline fixture probed a service")

    monkeypatch.setattr(SUGGEST, "iter_engine_discovery", fail)
    monkeypatch.setattr(SUGGEST, "resolve_roles_from_list", fail)
    monkeypatch.setattr(SUGGEST, "load_snapshot", lambda path: None)
    monkeypatch.setattr(SUGGEST, "load_catalogues", lambda path, aa_evidence=None: [])
    assert (
        run(
            monkeypatch,
            "--models-file",
            str(fixture),
            "--map",
            str(tmp_path / "map.json"),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "ollama/llama3:8b" in out
    assert "UNOBSERVED" in out
