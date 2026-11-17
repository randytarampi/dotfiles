"""Linux Brewfile safety guard rejects certain partial installs before brew."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check-linux-brewfile-eligibility.py"
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_04-install-packages.sh.tmpl"


def _module():
    spec = importlib.util.spec_from_file_location("linux_brew_audit", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_repo_default_dev_manifest_fails_before_partial_linux_bundle():
    report = _module().assess([ROOT / "Brewfile", ROOT / "Brewfile.dev"])
    assert report["blocked_entries"] >= 1
    assert report["packages_l1"] == "not_qualified"
    assert report["operational_verified"] is False
    assert any(
        item["status"] == "blocked_linux_cask"
        for doc in report["manifests"]
        for item in doc["entries"]
    )
    result = _run("--enforce", "--manifest", "Brewfile", "--manifest", "Brewfile.dev")
    assert result.returncode == 1


def test_unknown_formula_and_tap_never_claim_linux_support(tmp_path):
    manifest = tmp_path / "Linux candidate Brewfile"
    manifest.write_text('tap "test/tap"\nbrew "chezmoi"\nnpm "@x/agent"\n')
    result = _run("--json", "--enforce", "--manifest", manifest)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["blocked_entries"] == 0
    assert report["packages_l1"] == "not_qualified"
    assert report["operational_verified"] is False
    assert [item["status"] for item in report["manifests"][0]["entries"]] == [
        "needs_linux_formula_or_tap_evidence",
        "needs_linux_formula_or_tap_evidence",
        "external_runtime_not_qualified",
    ]


def test_cask_and_invalid_entry_are_blocked_without_execution(tmp_path):
    manifest = tmp_path / "danger"
    marker = tmp_path / "never-executed"
    manifest.write_text(
        'cask "antigravity-cli"\n' + '$(touch "' + str(marker) + '")\n' + 'brew "git"\n'
    )
    result = _run("--json", "--enforce", "--manifest", manifest)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["blocked_entries"] == 2
    assert not marker.exists()
    assert "touch" not in result.stdout  # Invalid source line never echoed.


def test_operator_ignored_cask_is_reported_but_not_blocked(tmp_path):
    manifest = tmp_path / "dev"
    manifest.write_text('cask "copilot-cli"\nbrew "git"\n')
    result = _run(
        "--json", "--enforce", "--manifest", manifest, "--ignore", "copilot-cli"
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["blocked_entries"] == 0
    assert report["ignored_entries"] == 1
    assert report["packages_l1"] == "not_qualified"
    assert [item["status"] for item in report["manifests"][0]["entries"]] == [
        "ignored_by_operator",
        "needs_linux_formula_or_tap_evidence",
    ]


def test_ignore_never_suppresses_malformed_syntax(tmp_path):
    manifest = tmp_path / "danger"
    manifest.write_text('$(touch x)\ncask "copilot-cli"\n')
    result = _run(
        "--json",
        "--enforce",
        "--manifest",
        manifest,
        "--ignore",
        "copilot-cli",
        "--ignore",
        "anything",
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["blocked_entries"] == 1
    assert report["ignored_entries"] == 1


def test_repo_legacy_manifest_casks_are_blocked_on_linux(tmp_path):
    """`legacy` is not macOS-gated, so its casks must fail before brew runs."""
    report = _module().assess([ROOT / "Brewfile.legacy"])
    assert report["blocked_entries"] == 3
    assert [item["status"] for item in report["manifests"][0]["entries"]] == [
        "blocked_linux_cask",
        "blocked_linux_cask",
        "blocked_linux_cask",
        "needs_linux_formula_or_tap_evidence",
    ]


def test_missing_or_symlinked_manifest_fails_closed(tmp_path):
    missing = tmp_path / "absent"
    assert _run("--manifest", missing).returncode == 1
    target = tmp_path / "target"
    target.write_text('brew "git"\n')
    link = tmp_path / "link"
    link.symlink_to(target)
    assert _run("--manifest", link).returncode == 1


def test_package_template_guards_linux_before_homebrew_download():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.index(
        'scripts/check-linux-brewfile-eligibility.py" --enforce'
    ) < source.index("dotfiles_homebrew_ready ||")
    assert 'linux_audit_args=(--manifest "{{ .chezmoi.sourceDir }}/Brewfile")' in source
    # Every other Linux-reachable brew_bundle_filtered manifest must be audited.
    for manifest in (
        "Brewfile.dev",
        "Brewfile.local.ai.headless",
        "Brewfile.dev.security",
        "Brewfile.utilities",
        "Brewfile.dev.ops",
        "Brewfile.legacy",
    ):
        expected = (
            'linux_audit_args+=(--manifest "{{ .chezmoi.sourceDir }}/' + manifest + '")'
        )
        assert expected in source, manifest
    for name in (
        "dev",
        "local_ai_headless",
        "dev_security",
        "utilities",
        "dev_ops",
        "legacy",
    ):
        assert "if .categories." + name in source
    assert "DOTFILES_BREWFILE_IGNORE" in source
    assert 'linux_audit_args+=(--ignore "$linux_ignored_name")' in source
    assert 'if [[ "${DOTFILES_RUN_PACKAGES_SETUP:-0}" != "1" ]]' in source
    assert 'include "scripts/check-linux-brewfile-eligibility.py" | sha256sum' in source
