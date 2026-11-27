"""The early Junie hook must not demand a receipt the later producer creates.

Script 06 runs before script 15 in chezmoi's numeric order. Script 15 installs
the managed launcher and writes the launch generation; only after that can the
route preflight attest a generation. In gateway mode the early hook therefore
emits directories and defers model output to the post-producer stage, and the
later stage (`configure-all.sh`) is what completes or fails.

These tests render the real template against a fake source tree and run it with
a fake HOME and a stubbed generator, so the ordering is exercised rather than
asserted from a regex.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts" / "run_onchange_06-install-junie-cli.sh.tmpl"


def _render(tmp_path, *, generator_exit=0):
    stamp = tmp_path / "configure-calls.log"
    generator = tmp_path / "configure-jetbrains-ai.stub"
    generator.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{stamp}"\n'
        f"exit {generator_exit}\n",
        encoding="utf-8",
    )
    generator.chmod(0o755)

    chezmoi = shutil.which("chezmoi")
    assert chezmoi, "chezmoi is required to render the Junie template"
    result = subprocess.run(
        [chezmoi, "execute-template", "--source", str(ROOT)],
        stdin=TEMPLATE.open(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # Keep the real includes and library; only the generator is substituted, so
    # the assertions are about orchestration (which arguments, in which mode),
    # not about profile generation itself.
    rendered_text = result.stdout.replace(
        str(ROOT / "scripts" / "configure-jetbrains-ai.py"), str(generator)
    )
    assert str(generator) in rendered_text
    rendered = tmp_path / "06-install-junie-cli.sh"
    rendered.write_text(rendered_text, encoding="utf-8")
    rendered.chmod(0o755)
    return rendered, stamp


def _run(rendered, tmp_path, *, junie="1", proxy="1"):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    junie_stub = bindir / "junie"
    junie_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    junie_stub.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "DOTFILES_RUN_JUNIE_CLI_SETUP": junie,
        "DOTFILES_USE_LITELLM_PROXY": proxy,
    }
    return subprocess.run(
        ["bash", str(rendered)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )


def _calls(stamp):
    if not stamp.exists():
        return []
    return [line for line in stamp.read_text(encoding="utf-8").splitlines()]


def test_gateway_mode_defers_model_emission_to_the_post_producer_stage(tmp_path):
    rendered, stamp = _render(tmp_path)

    result = _run(rendered, tmp_path, proxy="1")

    assert result.returncode == 0, result.stderr
    assert _calls(stamp) == ["--skip models"]
    assert "deferred to the post-launcher stage" in result.stdout + result.stderr


def test_direct_mode_still_emits_models_in_the_early_hook(tmp_path):
    rendered, stamp = _render(tmp_path)

    result = _run(rendered, tmp_path, proxy="0")

    assert result.returncode == 0, result.stderr
    assert _calls(stamp) == [""]
    assert "deferred" not in result.stdout + result.stderr


def test_gate_off_does_no_work(tmp_path):
    rendered, stamp = _render(tmp_path)

    result = _run(rendered, tmp_path, junie="0", proxy="0")

    assert result.returncode == 0, result.stderr
    assert _calls(stamp) == []


@pytest.mark.parametrize("value", ["true", "yes", "on", "TRUE"])
def test_gate_spellings_match_the_python_consumers(tmp_path, value):
    """A shell `== "1"` check would silently disagree with the Python gate."""
    rendered, stamp = _render(tmp_path)

    result = _run(rendered, tmp_path, junie=value, proxy="0")

    assert result.returncode == 0, result.stderr
    assert _calls(stamp) == [""]


@pytest.mark.parametrize("value", ["true", "yes", "on"])
def test_proxy_spellings_defer_like_the_python_gate(tmp_path, value):
    rendered, stamp = _render(tmp_path)

    result = _run(rendered, tmp_path, proxy=value)

    assert result.returncode == 0, result.stderr
    assert _calls(stamp) == ["--skip models"]


def test_directory_failure_is_fatal_not_a_passing_deploy(tmp_path):
    """Deferral is scheduling only: a failed directory stage must exit non-zero."""
    rendered, stamp = _render(tmp_path, generator_exit=3)

    result = _run(rendered, tmp_path, proxy="1")

    assert result.returncode != 0
    assert _calls(stamp) == ["--skip models"]
