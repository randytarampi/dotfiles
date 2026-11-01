"""Host-gated OpenCode plugin installer regressions."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"


def render_template(tmp_path):
    """Strip chezmoi-only fingerprint comments; the executable body is Bash."""
    rendered = (
        "\n".join(
            line
            for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
            if not (line.startswith("# ") and "{{" in line)
        )
        + "\n"
    )
    rendered = rendered.replace("{{ .chezmoi.sourceDir }}", str(ROOT))
    path = tmp_path / "install.sh"
    path.write_text(rendered, encoding="utf-8")
    return path


def run_installer(tmp_path, version=None, *, missing=False, status=0, twice=False):
    home = tmp_path / "home"
    stubs = home / ".scenario-stubs"
    stubs.mkdir(parents=True)
    recorder = home / "recorder"
    opencode = stubs / "opencode"
    if not missing:
        opencode.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then printf '%s\\n' "
            + repr(version or "")
            + f"; exit {status}; fi\n"
            'if [ "$1" = plugin ] && [ "$2" = check ]; then echo "${3#*@} 3.2.0 (current)"; exit 0; fi\n'
            'printf \'%s\\n\' "$*" >> "$HOME/recorder"\n',
            encoding="utf-8",
        )
        opencode.chmod(0o755)
    for name in ("bunx", "npx"):
        stub = stubs / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    script = render_template(tmp_path)
    env = {
        "HOME": str(home),
        "PATH": f"{stubs}:{Path(__import__('sys').executable).parent}:{Path('/usr/bin')}:{Path('/bin')}",
        "DOTFILES_RUN_OPENCODE_TOOLS_SETUP": "1",
        "DOTFILES_RUN_OPENCODE_MEMORY_SETUP": "0",
        "DOTFILES_RUN_VOICE_SETUP": "0",
        "PYTHONPATH": str(ROOT / "scripts/lib"),
    }
    results = []
    for _ in range(2 if twice else 1):
        results.append(
            subprocess.run(
                ["/bin/bash", str(script)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
        )
    calls = recorder.read_text().splitlines() if recorder.exists() else []
    return results, calls


def test_v1_skips_native_plugin_commands(tmp_path):
    results, calls = run_installer(tmp_path, "1.19.5")
    assert results[0].returncode == 0
    assert "host is known (major 1)" in results[0].stderr
    assert not any(call.startswith("plugin add ") for call in calls)


def test_v2_installs_manifest_plugins_and_is_idempotent(tmp_path):
    results, calls = run_installer(tmp_path, "opencode v2.2.5", twice=True)
    assert [result.returncode for result in results] == [0, 0]
    assert calls
    assert all(call.startswith("plugin add ") for call in calls)
    import sys

    sys.path.insert(0, str(ROOT / "scripts/lib"))
    from opencode_plugins import active_plugin_specs

    expected = [
        f"plugin add {spec}"
        for spec in active_plugin_specs(
            ROOT / "configs/opencode/opencode-plugins.json"
        )[1:]
        if spec != "opencode-mem@latest"
    ] + ["plugin add @bojackduy/opencode-voice@latest"]
    assert calls == expected * 2


def test_missing_binary_skips_with_warning(tmp_path):
    results, calls = run_installer(tmp_path, missing=True)
    assert results[0].returncode == 0
    assert "host is absent" in results[0].stderr
    assert not calls


def test_unprobeable_binary_skips_without_mutation(tmp_path):
    results, calls = run_installer(tmp_path, "garbage", status=2)
    assert results[0].returncode == 0
    assert "host is unknown" in results[0].stderr
    assert not calls
