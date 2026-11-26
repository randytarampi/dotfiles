"""One selected-manifest contract for preflight, tap trust and bundles.

The defect class: the Linux preflight, the tap-trust step and the bundle calls
each derived "which manifests are selected" independently, so a category could
be audited but not bundled, or bundled but never audited.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_04-install-packages.sh.tmpl"
CATEGORY_KEYS = (
    "dev",
    "dev_ops",
    "local_ai_headless",
    "dev_security",
    "utilities",
    "legacy",
    "desktop_browsers",
    "desktop_fonts",
    "desktop_dev",
)


def _render(tmp_path, categories):
    chezmoi = shutil.which("chezmoi")
    assert chezmoi, "chezmoi is required to render the package template"
    config = tmp_path / "chezmoi.toml"
    lines = ["[data.categories]"]
    for key in CATEGORY_KEYS:
        lines.append(f"{key} = {str(categories.get(key, False)).lower()}")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [chezmoi, "execute-template", "--source", str(ROOT), "--config", str(config)],
        stdin=TEMPLATE.open(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _names(rendered, variable):
    """Ordered manifest basenames assigned to one selection array."""
    names = []
    for line in rendered.splitlines():
        stripped = line.strip()
        # Match declarations/appends only; the Linux cohort mutates the single
        # array in place (``selected_brewfiles[0]=...``) and is not a second
        # selection.
        if not (
            stripped.startswith(variable + "=(")
            or stripped.startswith(variable + "+=(")
        ):
            continue
        match = re.search(r'"([^"]+)"', stripped)
        if match:
            names.append(Path(match.group(1)).name)
    return names


def test_default_selection_is_base_plus_dev_in_order(tmp_path):
    rendered = _render(tmp_path, {"dev": True})
    assert _names(rendered, "selected_brewfiles") == ["Brewfile", "Brewfile.dev"]


def test_every_category_off_selects_only_the_base_manifest(tmp_path):
    rendered = _render(tmp_path, {})
    assert _names(rendered, "selected_brewfiles") == ["Brewfile"]


def test_optional_categories_are_appended_after_the_base_manifests(tmp_path):
    rendered = _render(
        tmp_path,
        {"dev": True, "dev_ops": True, "legacy": True, "utilities": True},
    )
    selection = _names(rendered, "selected_brewfiles")
    assert selection[0] == "Brewfile"
    assert selection[1] == "Brewfile.dev"
    assert "Brewfile.utilities" in selection
    assert "Brewfile.dev.ops" in selection
    assert "Brewfile.legacy" in selection
    assert selection.index("Brewfile.dev.ops") > selection.index("Brewfile.dev")


def test_headless_ollama_keeps_its_formula_but_gains_no_npm_lane(tmp_path):
    rendered = _render(tmp_path, {"local_ai_headless": True})
    assert "Brewfile.local.ai.headless" in _names(rendered, "selected_brewfiles")
    assert "Brewfile.local.ai.headless" not in _names(
        rendered, "selected_npm_manifests"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="darwin-only manifests")
def test_desktop_manifests_are_macos_only(tmp_path):
    rendered = _render(tmp_path, {"desktop_browsers": True, "desktop_dev": True})
    selection = _names(rendered, "selected_brewfiles")
    assert "Brewfile.desktop.browsers" in selection
    assert "Brewfile.desktop.dev" in selection


def test_linux_never_selects_darwin_only_manifests(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("the template renders the host OS only")
    rendered = _render(tmp_path, {"desktop_browsers": True, "desktop_dev": True})
    selection = _names(rendered, "selected_brewfiles")
    assert "Brewfile.desktop.browsers" not in selection
    assert "Brewfile.desktop.dev" not in selection


def test_one_selection_array_feeds_audit_trust_and_bundles():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.count("selected_brewfiles=(") == 1
    # Audit arguments are built from the array, never from a second list.
    assert 'for selected_manifest in "${selected_brewfiles[@]}"' in source
    assert 'linux_audit_args+=(--manifest "$selected_manifest")' in source
    # Bundles iterate the same array.
    assert (
        'for selected_manifest in "${selected_brewfiles[@]}"; do\n    brew_bundle_filtered'
        in source
    )
    # No glob over unselected manifests, and every bundle argument is quoted.
    assert '"/Brewfile*"' not in source
    assert "brew_bundle_filtered {{" not in source
    assert source.count('brew_bundle_filtered "$selected_manifest"') == 1


def test_audit_precedes_homebrew_and_tap_trust():
    source = TEMPLATE.read_text(encoding="utf-8")
    audit = source.index('check-linux-brewfile-eligibility.py" --enforce')
    homebrew = source.index("dotfiles_homebrew_ready ||")
    trust = source.index("brew trust --help")
    assert audit < homebrew < trust


def test_tap_trust_is_scoped_and_failures_are_not_hidden():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert 'brew trust "$tap_name" 2>/dev/null || true' not in source
    assert 'if [[ -n "$tap_name" ]] && ! brew trust "$tap_name"; then' in source
    assert "brew trust --help >/dev/null 2>&1" in source
    assert "refusing to bundle untrusted taps" in source
    assert "brew bundle cleanup" not in source


def test_eula_default_is_retained_only_for_macos():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert (
        'if [[ "$(uname -s)" == "Darwin" ]]; then\n    export HOMEBREW_ACCEPT_EULA='
        in source
    )
    assert source.count("HOMEBREW_ACCEPT_EULA") == 2  # the guard and the export


def test_bundle_filter_delegates_to_the_exact_name_filter():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert 'scripts/filter-brewfile.py" \\' in source
    assert '--ignore-list "${DOTFILES_BREWFILE_IGNORE}"' in source
    assert "tr ',' '|'" not in source
    assert "grep -vE" not in source


def _sandbox(tmp_path, categories):
    """Render the template and prepare fake brew/npm/id/uname for a Linux run."""
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls.txt"
    out = tmp_path / "bundles"
    out.mkdir()
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (fake / "brew").write_text(
        "#!/usr/bin/env bash\n"
        'echo "brew $*" >> "$CALLS"\n'
        'case "$1" in\n'
        "  shellenv) printf 'export PATH=%q\\n' \"$PATH\" ;;\n"
        '  --prefix) echo "$FAKE_PREFIX" ;;\n'
        '  trust) shift; [[ "${1:-}" == "--help" ]] && exit 0 ;;\n'
        '  bundle) for a in "$@"; do case "$a" in --file=*) file="${a#--file=}";; esac; done;\n'
        '          cp "$file" "$OUT/$(basename "$file").$RANDOM" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake / "npm").write_text(
        '#!/usr/bin/env bash\necho "npm $*" >> "$CALLS"\nexit 0\n', encoding="utf-8"
    )
    (fake / "id").write_text("#!/usr/bin/env bash\necho 0\n", encoding="utf-8")
    (fake / "uname").write_text("#!/usr/bin/env bash\necho Linux\n", encoding="utf-8")
    for path in fake.iterdir():
        path.chmod(0o755)
    rendered = _render(tmp_path, categories)
    script = tmp_path / "run.sh"
    script.write_text(rendered, encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": f"{fake}:{os.environ.get('PATH', '')}",
        "CALLS": str(calls),
        "OUT": str(out),
        "FAKE_PREFIX": str(prefix),
        "DOTFILES_RUN_PACKAGES_SETUP": "1",
    }
    (tmp_path / "home").mkdir()
    return script, calls, out, env


def test_linux_refusal_and_gate_off_make_no_package_calls(tmp_path):
    script, calls, out, env = _sandbox(tmp_path, {"dev": True})
    # Linux Homebrew work now requires the Linux cohort gate; enable it so the
    # audit (not the gate) is what refuses the dev selection.
    env["DOTFILES_RUN_LINUX_PACKAGES_SETUP"] = "1"
    refused = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=120, env=env
    )
    assert refused.returncode == 1
    assert "blockers" in refused.stdout
    assert not calls.exists(), calls.read_text()
    assert not any(out.iterdir())

    off = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**env, "DOTFILES_RUN_PACKAGES_SETUP": "0"},
    )
    assert off.returncode == 0
    assert not calls.exists()


def test_linux_cohort_bundles_once_without_eula_or_ignored_names(tmp_path):
    script, calls, out, env = _sandbox(tmp_path, {})
    env["DOTFILES_RUN_LINUX_PACKAGES_SETUP"] = "1"
    env["DOTFILES_BREWFILE_IGNORE"] = "jq,ripgrep"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=120, env=env
    )
    assert result.returncode == 0, result.stderr
    invocations = calls.read_text().splitlines()
    assert len([line for line in invocations if line.startswith("brew bundle")]) == 1
    bundles = list(out.iterdir())
    assert len(bundles) == 1
    bundled = bundles[0].read_text(encoding="utf-8")
    # The cohort is the base manifest narrowed to chezmoi/jq/ripgrep only;
    # operator ignores still remove names from that view.
    assert 'brew "chezmoi"' in bundled
    assert 'brew "jq"' not in bundled
    assert 'brew "ripgrep"' not in bundled
    assert 'brew "git"' not in bundled
    assert 'tap "' not in bundled
    # Linux must not accept a licence the operator did not supply.
    assert "HOMEBREW_ACCEPT_EULA" not in env
    # The npm companion lane is macOS-only; the cohort has no npm members.
    assert "npm " not in "\n".join(invocations)
