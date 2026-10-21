import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / "verify-local-ai-smoke.py"
SPEC = importlib.util.spec_from_file_location("local_ai_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def _inputs(tmp_path, refs=("existing/model",)):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_list:\n" + "".join(f'- model_name: "{name}"\n' for name in refs)
    )
    ledger = tmp_path / "config.models.json"
    ledger.write_text(json.dumps([{"model_name": name} for name in refs]))
    return [config, ledger]


def _runner(command, **kwargs):
    home = Path(kwargs["env"]["HOME"]) / ".local/share/litellm"
    names = ["ollama-local/llama3.2:3b", "existing/model"]
    (home / "config.yaml").write_text(
        "model_list:\n" + "".join(f'- model_name: "{n}"\n' for n in names)
    )
    (home / "config.models.json").write_text(
        json.dumps([{"model_name": n} for n in names])
    )
    (home / "last_generation_notes.json").write_text(
        json.dumps(["UNKNOWN: catalogue absent/expired"])
    )
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_retention_run_smoke_passes_with_fixture_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.Path, "home", lambda: tmp_path)
    root = tmp_path / ".local/share/litellm"
    root.mkdir(parents=True)
    _inputs(root)
    report = smoke.run_smoke(mode="retention", runner=_runner)
    assert report["passed"]
    assert report["mode"] == "retention"


def test_retention_missing_baseline_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.Path, "home", lambda: tmp_path)
    report = smoke.run_smoke(mode="retention")
    assert report == {
        "skipped": True,
        "reason": "deployed LiteLLM config.yaml or config.models.json baseline is missing",
    }


def test_acceptance_run_smoke_with_inventory_and_hook(tmp_path):
    baseline = _inputs(tmp_path)
    target = Path(tmp_path) / "new-home"
    calls = []

    def hook(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("non-loopback network blocked: example.com")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(smoke.urllib.request, "urlopen", hook)
    report = smoke.run_smoke(
        target,
        mode="acceptance",
        baseline=baseline,
        inventory={"ollama-local/llama3.2:3b", "existing/model"},
        runner=_runner,
        network_hook=hook,
    )
    assert report["passed"]
    assert "OPENAI_API_KEY" not in report.get("environment", {})
    with pytest.raises(RuntimeError, match="blocked"):
        smoke.urllib.request.urlopen("https://example.com")
    assert calls == [("https://example.com",), ("https://example.com",)]
    monkeypatch.undo()


def test_acceptance_requires_complete_inventory_and_new_home(tmp_path):
    baseline = _inputs(tmp_path)
    with pytest.raises(ValueError, match="complete baseline"):
        smoke.run_smoke(tmp_path / "new", mode="acceptance", baseline=baseline)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="must not exist"):
        smoke.run_smoke(
            existing,
            mode="acceptance",
            baseline=baseline,
            inventory={smoke.TARGET_ALIAS},
        )


def test_acceptance_dry_run_does_not_create_home(tmp_path):
    baseline = _inputs(tmp_path)
    home = tmp_path / "new-home"
    before = sorted(
        (path.relative_to(tmp_path), path.read_bytes()) for path in tmp_path.rglob("*")
    )
    report = smoke.run_smoke(
        home,
        mode="acceptance",
        baseline=baseline,
        inventory={smoke.TARGET_ALIAS},
        dry_run=True,
    )
    assert report["dry_run"]
    after = sorted(
        (path.relative_to(tmp_path), path.read_bytes()) for path in tmp_path.rglob("*")
    )
    assert before == after
    assert not home.exists()


def test_served_names_must_be_inventory_and_include_baseline_refs(tmp_path):
    report = smoke.validate_snapshot(
        None,
        None,
        None,
        config_text='- model_name: "ollama-local/llama3.2:3b"\n',
        ledger_text='[{"model_name":"ollama-local/llama3.2:3b"}]',
        notes_text="[]",
        inventory_names={smoke.TARGET_ALIAS},
        required_baseline_refs={"required/model"},
    )
    assert report["served_within_inventory"]
    assert not report["required_baseline_refs_served"]


def test_help_exits_zero():
    with pytest.raises(SystemExit) as result:
        smoke.main(["--help"])
    assert result.value.code == 0
