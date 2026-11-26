"""Opt-in Linux package cohort: gate off, explicit positive, fail-closed selection.

chezmoi renders for the host OS only, so the template is rendered on the host
and the Linux/Darwin branch is chosen through a stubbed ``uname``. Homebrew,
npm, node, corepack and curl are stubbed as well; no real package manager,
network request or sudo call is made.
"""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_04-install-packages.sh.tmpl"
ENV_EXAMPLE = ROOT / "dot_dotfiles/shell/.env.example"
COHORT_LINES = ['brew "chezmoi"', 'brew "jq"', 'brew "ripgrep"']
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

BREW_STUB = """#!/usr/bin/env bash
echo "brew $*" >> "$CALLS"
case "$1" in
  shellenv)
    [[ "${FAKE_BREW_SHELLENV_EXIT:-0}" == "0" ]] || exit "${FAKE_BREW_SHELLENV_EXIT}"
    printf 'export PATH=%q\\n' "$PATH"
    ;;
  --prefix)
    echo "$FAKE_PREFIX"
    ;;
  trust)
    shift
    [[ "${1:-}" == "--help" ]] && exit 0
    ;;
  bundle)
    for arg in "$@"; do
      case "$arg" in --file=*) file="${arg#--file=}" ;; esac
    done
    [[ "${FAKE_BREW_BUNDLE_EXIT:-0}" == "0" ]] || exit "${FAKE_BREW_BUNDLE_EXIT}"
    cp "$file" "$OUT/$(basename "$file").$RANDOM"
    ;;
esac
exit 0
"""

CURL_STUB = """#!/usr/bin/env bash
echo "curl $*" >> "$CALLS"
case "${FAKE_CURL_MODE:-ok}" in
  fail) exit 22 ;;
  installer-fails) printf 'exit 7\\n' ; exit 0 ;;
  *) exit 0 ;;
esac
"""


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


def _sandbox(tmp_path, categories, *, linux=True, extra_env=None):
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls.txt"
    out = tmp_path / "bundles"
    out.mkdir()
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (fake / "brew").write_text(BREW_STUB, encoding="utf-8")
    (fake / "curl").write_text(CURL_STUB, encoding="utf-8")
    (fake / "npm").write_text(
        '#!/usr/bin/env bash\necho "npm $*" >> "$CALLS"\nexit 0\n',
        encoding="utf-8",
    )
    (fake / "node").write_text("#!/usr/bin/env bash\necho v0.0.0\n", encoding="utf-8")
    (fake / "corepack").write_text(
        '#!/usr/bin/env bash\necho "corepack $*" >> "$CALLS"\nexit 0\n',
        encoding="utf-8",
    )
    (fake / "id").write_text("#!/usr/bin/env bash\necho 0\n", encoding="utf-8")
    (fake / "uname").write_text(
        "#!/usr/bin/env bash\necho %s\n" % ("Linux" if linux else "Darwin"),
        encoding="utf-8",
    )
    for path in fake.iterdir():
        path.chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text(_render(tmp_path, categories), encoding="utf-8")
    (tmp_path / "home").mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": f"{fake}:{os.environ.get('PATH', '')}",
        "CALLS": str(calls),
        "OUT": str(out),
        "FAKE_PREFIX": str(prefix),
        "DOTFILES_RUN_PACKAGES_SETUP": "1",
    }
    env.pop("DOTFILES_RUN_LINUX_PACKAGES_SETUP", None)
    env.update(extra_env or {})
    return script, calls, out, env


def _run(script, env):
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=120, env=env
    )


def _brew_calls(calls):
    return calls.read_text().splitlines() if calls.exists() else []


def _bundled_lines(out):
    bundles = sorted(out.iterdir())
    assert bundles, "no bundle was recorded"
    return [
        line
        for bundle in bundles
        for line in bundle.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _force_missing_brew(script):
    """Isolate Homebrew discovery so a host install is never probed."""
    source = script.read_text(encoding="utf-8")
    marker = 'source "${LIB_DIR}/homebrew_bootstrap.sh"'
    assert marker in source
    script.write_text(
        source.replace(
            marker,
            marker + "\n_dotfiles_brew_binary() { return 1; }",
            1,
        ),
        encoding="utf-8",
    )


def test_gate_off_performs_zero_homebrew_operations(tmp_path):
    absent = tmp_path / "absent"
    absent.mkdir()
    script, calls, out, env = _sandbox(absent, {})
    result = _run(script, env)
    assert result.returncode == 0, result.stderr
    assert "skipping Linux package cohort" in result.stdout
    assert _brew_calls(calls) == []
    assert not any(out.iterdir())

    # An explicit zero is as inert as an absent value.
    explicit = tmp_path / "explicit-zero"
    explicit.mkdir()
    script, calls, out, env = _sandbox(
        explicit, {}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "0"}
    )
    result = _run(script, env)
    assert result.returncode == 0, result.stderr
    assert _brew_calls(calls) == []


def test_explicit_positive_installs_only_the_cohort(tmp_path):
    script, calls, out, env = _sandbox(
        tmp_path, {}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}
    )
    result = _run(script, env)
    assert result.returncode == 0, result.stderr
    invocations = _brew_calls(calls)
    bundle_calls = [line for line in invocations if line.startswith("brew bundle")]
    assert len(bundle_calls) == 1, invocations
    assert _bundled_lines(out) == COHORT_LINES
    assert not [line for line in invocations if line.startswith("brew trust")]
    assert not [line for line in invocations if line.startswith("npm ")]
    assert "HOMEBREW_ACCEPT_EULA" not in env


def test_dev_selection_fails_before_any_homebrew_operation(tmp_path):
    script, calls, out, env = _sandbox(
        tmp_path, {"dev": True}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}
    )
    result = _run(script, env)
    assert result.returncode == 1
    assert "blockers" in result.stdout
    assert _brew_calls(calls) == []
    assert not any(out.iterdir())


def test_audit_clean_broader_selection_is_refused_not_narrowed(tmp_path):
    """`utilities` has no casks, so only the cohort compatibility check can stop it."""
    script, calls, out, env = _sandbox(
        tmp_path,
        {"utilities": True},
        extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"},
    )
    result = _run(script, env)
    assert result.returncode == 1
    assert "accepts only the base Brewfile" in result.stderr
    assert _brew_calls(calls) == []
    assert not any(out.iterdir())


def test_missing_brew_download_failure_aborts_before_bundle(tmp_path):
    script, calls, out, env = _sandbox(
        tmp_path, {}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}
    )
    _force_missing_brew(script)
    result = _run(script, dict(env, FAKE_CURL_MODE="fail"))
    assert result.returncode == 1
    assert "Homebrew prerequisite unavailable" in result.stderr
    assert _brew_calls(calls) == [
        "curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
    ]
    assert not any(out.iterdir())


def test_failed_first_install_aborts_before_bundle(tmp_path):
    script, calls, out, env = _sandbox(
        tmp_path, {}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}
    )
    _force_missing_brew(script)
    result = _run(script, dict(env, FAKE_CURL_MODE="installer-fails"))
    assert result.returncode == 1
    assert "Homebrew prerequisite unavailable" in result.stderr
    assert not any(line.startswith("brew bundle") for line in _brew_calls(calls))
    assert not any(out.iterdir())


def test_failed_first_bundle_is_not_retried(tmp_path):
    script, calls, out, env = _sandbox(
        tmp_path, {}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}
    )
    result = _run(script, dict(env, FAKE_BREW_BUNDLE_EXIT="4"))
    assert result.returncode != 0
    invocations = _brew_calls(calls)
    assert len([line for line in invocations if line.startswith("brew bundle")]) == 1
    assert not [line for line in invocations if line.startswith("npm ")]


def test_repeat_run_has_no_drift(tmp_path):
    script, calls, out, env = _sandbox(
        tmp_path, {}, extra_env={"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}
    )
    first = _run(script, env)
    assert first.returncode == 0, first.stderr
    first_lines = _bundled_lines(out)

    for bundle in out.iterdir():
        bundle.unlink()
    second = _run(script, env)
    assert second.returncode == 0, second.stderr
    assert _bundled_lines(out) == first_lines == COHORT_LINES
    invocations = _brew_calls(calls)
    assert len([line for line in invocations if line.startswith("brew bundle")]) == 2


def test_macos_parity_is_unaffected_by_the_linux_gate(tmp_path):
    markers = []
    for name, extra in (
        ("plain", {}),
        ("linux-gate-on", {"DOTFILES_RUN_LINUX_PACKAGES_SETUP": "1"}),
    ):
        case = tmp_path / name
        case.mkdir()
        script, calls, out, env = _sandbox(
            case, {"dev": True}, linux=False, extra_env=extra
        )
        result = _run(script, env)
        assert result.returncode == 0, result.stderr
        ordered = []
        for line in _brew_calls(calls):
            if line.startswith("brew bundle"):
                ordered.append("bundle:" + Path(line.split("--file=")[1]).name)
            elif line.startswith("npm "):
                ordered.append("npm")
        markers.append(ordered)
    # Per-manifest npm order is preserved: each bundle is followed by its own
    # npm reconciliation before the next bundle starts.
    assert [item for item in markers[0] if item.startswith("bundle:")] == [
        "bundle:Brewfile",
        "bundle:Brewfile.dev",
    ]
    first_bundle = markers[0].index("bundle:Brewfile")
    second_bundle = markers[0].index("bundle:Brewfile.dev")
    assert "npm" in markers[0][first_bundle:second_bundle]
    assert "npm" in markers[0][second_bundle:]
    # Enabling the Linux-only gate must not change macOS behaviour.
    assert markers[0] == markers[1]


def test_gate_is_wired_documented_and_keeps_one_selection():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert source.count("selected_brewfiles=(") == 1
    assert source.count("linux_cohort_names=") == 1
    assert "chezmoi jq ripgrep" in source
    assert '{{ env "DOTFILES_RUN_LINUX_PACKAGES_SETUP" | sha256sum }}' in source
    assert "DOTFILES_RUN_LINUX_PACKAGES_SETUP:-0" in source
    documented = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "DOTFILES_RUN_LINUX_PACKAGES_SETUP='0'" in documented
