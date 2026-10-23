import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / "verify-local-ai-smoke.py"
SPEC = importlib.util.spec_from_file_location("local_ai_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)

TARGET_ALIAS = "ollama/llama3.2:3b"


@pytest.fixture(autouse=True)
def _local_model_alias(monkeypatch):
    monkeypatch.setattr(smoke, "target_alias", lambda: TARGET_ALIAS)


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
    names = [TARGET_ALIAS, "existing/model"]
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


def test_acceptance_report_marks_omlx_only_not_applicable_and_required_ollama_failure(
    tmp_path,
):
    baseline = _inputs(tmp_path)
    report = smoke.run_smoke(
        tmp_path / "omlx-host",
        mode="acceptance",
        baseline=baseline,
        inventory={TARGET_ALIAS, "existing/model"},
        advertised_engines=["omlx"],
        runner=_runner,
    )
    assert report["ollama_requirement"] == "NOT_APPLICABLE"

    required = smoke.run_smoke(
        tmp_path / "ollama-required-host",
        mode="acceptance",
        baseline=baseline,
        inventory={TARGET_ALIAS, "existing/model"},
        advertised_engines=["ollama"],
        ollama_required=True,
        runner=_runner,
    )
    assert required["ollama_requirement"] == "FAILURE"


def test_retention_missing_baseline_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.Path, "home", lambda: tmp_path)
    with pytest.raises(
        ValueError,
        match="deployed LiteLLM config.yaml or config.models.json baseline is missing",
    ):
        smoke.run_smoke(mode="retention")


def test_acceptance_run_smoke_with_inventory_and_hook(tmp_path, monkeypatch):
    baseline = _inputs(tmp_path)
    target = Path(tmp_path) / "new-home"
    calls = []

    def hook(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("non-loopback network blocked: example.com")

    monkeypatch.setattr(smoke, "_deny_non_loopback", hook)
    report = smoke.run_smoke(
        target,
        mode="acceptance",
        baseline=baseline,
        inventory={TARGET_ALIAS, "existing/model"},
        runner=_runner,
        network_hook=hook,
    )
    assert report["passed"]
    assert "OPENAI_API_KEY" not in report.get("environment", {})
    with pytest.raises(RuntimeError, match="blocked"):
        smoke._deny_non_loopback("https://example.com")
    assert calls == [("https://example.com",), ("https://example.com",)]


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
            inventory={TARGET_ALIAS},
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
        inventory={TARGET_ALIAS},
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
        config_text=f'- model_name: "{TARGET_ALIAS}"\n',
        ledger_text=json.dumps([{"model_name": TARGET_ALIAS}]),
        notes_text="[]",
        inventory_names={TARGET_ALIAS},
        required_baseline_refs={"required/model"},
    )
    assert report["served_within_inventory"]
    assert not report["required_baseline_refs_served"]


def test_help_exits_zero():
    with pytest.raises(SystemExit) as result:
        smoke.main(["--help"])
    assert result.value.code == 0


def test_smoke_destination_rejections(tmp_path, monkeypatch):
    """P0.4: both modes reject the source HOME and pre-populated destinations."""
    monkeypatch.setattr(smoke.Path, "home", lambda: tmp_path)
    populated = tmp_path / "dest"
    populated.mkdir()
    sentinel = populated / "keep.txt"
    sentinel.write_text("sentinel")

    def _never_writes(*_args, **_kwargs):
        raise AssertionError("no child must run after a destination rejection")

    with pytest.raises(ValueError, match="empty directory"):
        smoke.run_smoke(home=str(populated), mode="retention", runner=_never_writes)
    with pytest.raises(ValueError, match="must not be the real source HOME"):
        smoke.run_smoke(
            home=str(tmp_path),
            mode="acceptance",
            baseline=_inputs(tmp_path),
            inventory={"existing/model", TARGET_ALIAS},
            runner=_never_writes,
        )
    assert sentinel.read_text() == "sentinel"


def test_acceptance_cli_plumbing_main(tmp_path):
    """P0.4 CLI plumbing: real main() with acceptance inputs and dry-run safety."""
    baseline = _inputs(tmp_path, refs=("existing/model", TARGET_ALIAS))
    config, ledger = baseline
    newdir = tmp_path / "fresh-home"
    argv = [
        "--mode",
        "acceptance",
        "--home",
        str(newdir),
        "--baseline-config",
        str(config),
        "--baseline-inventory",
        str(ledger),
        "--dry-run",
    ]
    assert smoke.main(argv) == 0
    assert not newdir.exists()  # dry-run created nothing
    missing = [
        "--mode",
        "acceptance",
        "--home",
        str(tmp_path / "other"),
    ]
    assert smoke.main(missing) == 1  # mandatory inputs fail closed
