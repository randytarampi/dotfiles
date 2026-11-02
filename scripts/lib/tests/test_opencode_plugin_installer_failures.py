from pathlib import Path
import os
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
TEMPLATE = (
    REPO_ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
)


def run_installer(tmp_path, *, opencode_script, npx_exit="0", update_gate="0"):
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    opencode = bin_dir / "opencode"
    opencode.write_text(
        "#!/bin/bash\nif [[ $1 == --version ]]; then echo 'opencode v2.2.5'; exit 0; fi\n"
        + opencode_script
    )
    opencode.chmod(0o755)
    npx = bin_dir / "npx"
    npx.write_text(f"#!/bin/bash\nexit {npx_exit}\n")
    npx.chmod(0o755)
    rendered = subprocess.run(
        ["chezmoi", "execute-template", "--source", str(REPO_ROOT)],
        input=TEMPLATE.read_text(),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    script = tmp_path / "installer.sh"
    script.write_text(rendered)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{Path(__import__('sys').executable).parent}:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "scripts/lib"),
        "DOTFILES_RUN_OPENCODE_TOOLS_SETUP": "1",
        "DOTFILES_RUN_OPENCODE_PLUGIN_UPDATE": update_gate,
        "DOTFILES_RUN_OPENCODE_MEMORY_SETUP": "0",
        "DOTFILES_RUN_VOICE_SETUP": "0",
    }
    return subprocess.run(
        ["bash", str(script)], env=env, text=True, capture_output=True
    )


@pytest.mark.parametrize(
    ("opencode_script", "npx_exit", "update_gate", "should_pass"),
    [
        (
            'if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\nif [[ $1 == plugin && $2 == check ]]; then case "$3" in github:*) echo \'opencode-vibeguard-v2 3.2.0 (current)\' ;; *) echo "${3%@*} 3.2.0 (current)" ;; esac; exit 0; fi\n',
            "0",
            "0",
            True,
        ),
        ("if [[ $1 == plugin && $2 == add ]]; then exit 1; fi\n", "0", "0", False),
        (
            "if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\nif [[ $1 == plugin && $2 == check ]]; then echo '@tarquinen/opencode-dcp (check failed)'; exit 1; fi\n",
            "0",
            "0",
            False,
        ),
        (
            "if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\nif [[ $1 == plugin && $2 == check ]]; then echo 'no usable version'; exit 0; fi\n",
            "0",
            "0",
            False,
        ),
        (
            'if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\nif [[ $1 == plugin && $2 == check ]]; then case "$3" in github:*) echo \'opencode-vibeguard-v2 3.2.0 (current)\' ;; *) echo "${3%@*} 3.2.0 (latest)" ;; esac; exit 0; fi\n',
            "0",
            "0",
            True,
        ),
        (
            "if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\nif [[ $1 == plugin && $2 == check ]]; then echo '@tarquinen/opencode-dcp 3.2.0 (current)'; exit 0; fi\nif [[ $1 == plugin && $2 == update ]]; then exit 1; fi\n",
            "0",
            "1",
            False,
        ),
        (
            "if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\nif [[ $1 == plugin && $2 == check ]]; then echo '@tarquinen/opencode-dcp 3.2.0 (not-current)'; exit 0; fi\nif [[ $1 == plugin && $2 == update ]]; then exit 0; fi\n",
            "0",
            "1",
            False,
        ),
    ],
)
def test_rendered_installer_failure_matrix(
    tmp_path, opencode_script, npx_exit, update_gate, should_pass
):
    result = run_installer(
        tmp_path,
        opencode_script=opencode_script,
        npx_exit=npx_exit,
        update_gate=update_gate,
    )
    assert (result.returncode == 0) is should_pass, result.stdout + result.stderr


def test_installer_keeps_omo_failure_bit():
    source = (
        ROOT.parent / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    ).read_text()
    assert source.count("PLUGIN_INSTALL_FAILED=0") == 1


def test_plugin_check_failure_is_fail_closed():
    source = (
        ROOT.parent / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    ).read_text()
    check_block = source.split('if output="$("${OC_MANAGED}" plugin check', 1)[1].split(
        "done", 1
    )[0]
    assert "PLUGIN_INSTALL_FAILED=1" in check_block
    assert "check failed" not in check_block.split("else", 1)[0]
    assert '"${OC_MANAGED}" plugin update "${plugin}"' in source
    assert 'if [[ "${PLUGIN_UPDATE_FAILED}" -ne 0 ]]; then' in source
    # Every plugin subcommand runs through the managed-binary resolver.
    assert 'opencode plugin add "${plugin}"' not in source
    assert "opencode_managed_bin" in source


def test_plugin_parser_accepts_concrete_version_only():
    import re

    parser = re.compile(
        r"(?:^|\s)([^\s()]+)\s+(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\s+\((?:current|latest)\)(?=$|\s)",
        re.M,
    )
    assert parser.findall("opencode-dcp 3.2.1 (current)") == [("opencode-dcp", "3.2.1")]
    assert parser.findall("opencode-dcp (check failed)") == []
    assert parser.findall("opencode-dcp (check") == []
    assert parser.findall("opencode-dcp 3.2.1 (latest)") == [("opencode-dcp", "3.2.1")]


def test_successful_update_recheck_uses_same_current_line_parser(tmp_path):
    script = """if [[ $1 == plugin && $2 == add ]]; then exit 0; fi
if [[ $1 == plugin && $2 == check ]]; then case "$3" in github:*) echo 'opencode-vibeguard-v2 3.2.0 (current)' ;; *) echo "${3%@*} 3.2.0 (current)" ;; esac; exit 0; fi
if [[ $1 == plugin && $2 == update ]]; then exit 0; fi
"""
    result = run_installer(tmp_path, opencode_script=script, update_gate="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "did not converge" not in result.stdout + result.stderr


def test_parser_failure_is_reported_intentionally_not_by_pipefail(tmp_path):
    script = """if [[ $1 == plugin && $2 == add ]]; then exit 0; fi
if [[ $1 == plugin && $2 == check ]]; then echo "${3%@*} garbled response"; exit 0; fi
"""
    result = run_installer(tmp_path, opencode_script=script)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "parser failed" in combined
    assert "pipefail" not in combined
    assert "unbound variable" not in combined


def test_git_cache_probe_layout_detectable(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location

    path = ROOT / "check-plugin-consistency.py"
    spec = spec_from_file_location("plugin_consistency_probe", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    commit = "934823e5500f6af8270a45ba2d2f245bc6e4faa4"
    package_dir = tmp_path / "npm/git-opencode-vibeguard-v2-6bc7939b2f86/1793529876908"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"name":"opencode-vibeguard-v2","resolved":"git+https://github.com/AxeForce/opencode-vibeguard-v2#'
        + commit
        + '"}'
    )
    assert module.is_plugin_installed(
        "github:AxeForce/opencode-vibeguard-v2#" + commit, tmp_path
    )


def test_git_cache_checker_fails_closed_for_missing_or_corrupt_metadata(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location

    path = ROOT / "check-plugin-consistency.py"
    spec = spec_from_file_location("plugin_consistency_corrupt", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    commit = "934823e5500f6af8270a45ba2d2f245bc6e4faa4"
    spec_text = "github:AxeForce/opencode-vibeguard-v2#" + commit
    assert not module.is_plugin_installed(spec_text, tmp_path)
    corrupt = tmp_path / "npm/git-opencode-vibeguard-v2-hash/1/package.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{")
    assert not module.is_plugin_installed(spec_text, tmp_path)
