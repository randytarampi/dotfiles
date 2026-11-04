from pathlib import Path
import os
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
TEMPLATE = (
    REPO_ROOT / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
)


def run_installer(
    tmp_path, *, opencode_script, npx_exit="0", update_gate="0", npx_script=None
):
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
    npx.write_text("#!/bin/bash\n" + (npx_script or f"exit {npx_exit}\n"))
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


def test_omo_probe_drops_inherited_exporter_settings_only(tmp_path, monkeypatch):
    for key in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_METRIC_EXPORT_INTERVAL",
    ):
        monkeypatch.setenv(key, "inherited")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    result = run_installer(
        tmp_path,
        opencode_script=(
            "if [[ $1 == plugin && $2 == check ]]; then "
            'case "$3" in github:*) echo "opencode-vibeguard-v2 3.2.0 (current)" ;; '
            '*) echo "${3%@*} 3.2.0 (current)" ;; esac; fi\n'
        ),
        npx_script=(
            "[[ -z ${OTEL_EXPORTER_OTLP_ENDPOINT+x} && "
            "-z ${OTEL_EXPORTER_OTLP_PROTOCOL+x} && "
            "-z ${OTEL_METRIC_EXPORT_INTERVAL+x} && "
            "${OTEL_SDK_DISABLED:-} == true ]]\n"
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "oh-my-opencode-slim installed" in result.stdout
    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "inherited"


def test_installer_keeps_omo_failure_bit():
    source = (
        ROOT.parent / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    ).read_text()
    assert source.count("PLUGIN_INSTALL_FAILED=0") == 1


def test_plugin_check_failure_is_fail_closed():
    source = (
        ROOT.parent / ".chezmoiscripts/run_onchange_07-install-opencode-plugins.sh.tmpl"
    ).read_text()
    check_block = source.split("opencode_plugin_check_retried() {", 1)[1].split(
        "PLUGIN_RESOLVED_VERSIONS=()", 1
    )[0]
    assert "PLUGIN_INSTALL_FAILED=1" in source
    # The check exit code reaches the parser; even (current) output fails
    # when the plugin-check command exits nonzero.
    assert (
        '"${OC_MANAGED}" plugin check "${spec}" 2>&1)" || check_exit=$?' in check_block
    )
    assert "parse_plugin_check" in check_block
    # The recorded context transient gets one bounded retry, not a silent pass.
    assert '*"Plugin is not configured"*' in check_block
    assert '"${attempt}" -lt 2' in check_block
    assert "parser failed" in check_block
    assert 'cd "$HOME"' in source
    assert 'die "One or more OpenCode plugin version checks failed"' in source
    assert '"${OC_MANAGED}" plugin update "${plugin}"' in source
    assert 'if [[ "${PLUGIN_UPDATE_FAILED}" -ne 0 ]]; then' in source
    # The recheck fails on nonzero exits and non-(current) status.
    assert 'opencode_plugin_check_retried "${plugin}" "" 1' in source
    # Every plugin subcommand runs through the managed-binary resolver.
    assert 'opencode plugin add "${plugin}"' not in source
    assert "opencode_managed_bin" in source


@pytest.mark.parametrize(
    ("failed_spec", "check_line", "should_pass"),
    [
        (None, None, True),
        ("@tarquinen/opencode-dcp@latest", "opencode-dcp 3.2.0 (current)", False),
        ("@tarquinen/opencode-dcp@latest", "opencode-dcp 3.2.0 (check failed)", False),
    ],
)
def test_nonzero_check_exit_fails_closed_on_check_failures(
    tmp_path, failed_spec, check_line, should_pass
):
    script = """if [[ $1 == plugin && $2 == add ]]; then exit 0; fi
if [[ $1 == plugin && $2 == check ]]; then
  case "$3" in
    github:*) name=opencode-vibeguard-v2 ;;
    *) name="${3%@*}" ;;
  esac
  if [[ "$3" == "__FAILED_SPEC__" ]]; then
    echo "__CHECK_LINE__"
    exit 1
  fi
  echo "$name 3.2.0 (current)"
  exit 0
fi
"""
    script = script.replace("__FAILED_SPEC__", failed_spec or "never-match")
    script = script.replace("__CHECK_LINE__", check_line or "none")
    result = run_installer(tmp_path, opencode_script=script)
    combined = result.stdout + result.stderr
    assert (result.returncode == 0) is should_pass, combined
    if failed_spec:
        assert "parser failed" in combined


@pytest.mark.parametrize(
    ("sha_spec", "should_pass"),
    [
        ("github:AxeForce/opencode-vibeguard-v2#main", True),
        ("@tarquinen/opencode-dcp@latest", False),
    ],
)
def test_git_sha_version_is_only_valid_for_github_plugins(
    tmp_path, sha_spec, should_pass
):
    script = """if [[ $1 == plugin && $2 == add ]]; then exit 0; fi
if [[ $1 == plugin && $2 == check ]]; then
  if [[ "$3" == "__SHA_SPEC__" ]]; then
    case "$3" in
      github:*) name=opencode-vibeguard-v2 ;;
      *) name="${3%@*}" ;;
    esac
    echo "$name 934823e (current)"
    exit 0
  fi
  case "$3" in
    github:*) name=opencode-vibeguard-v2 ;;
    *) name="${3%@*}" ;;
  esac
  echo "$name 3.2.0 (current)"
  exit 0
fi
""".replace("__SHA_SPEC__", sha_spec)
    result = run_installer(tmp_path, opencode_script=script)
    assert (result.returncode == 0) is should_pass, result.stdout + result.stderr


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


_GATE_ADD_LINE = "if [[ $1 == plugin && $2 == add ]]; then exit 0; fi\n"
_GATE_CHECK_IF = "if [[ $1 == plugin && $2 == check ]]; then\n"
_GATE_PROLOGUE = _GATE_ADD_LINE + _GATE_CHECK_IF
_GATE_EPILOGUE = "fi\n"
# The resolved-name branch mirrors the live 2.2.5 check output: Git-source
# plugins print the bare repo name; scoped packages print the bare scope.
_GATE_RESOLVE_OK = (
    '    case "$3" in\n'
    "        github:*) name=opencode-vibeguard-v2 ;;\n"
    '        *) name="${3%@*}" ;;\n'
    "    esac\n"
)
_GATE_OK_LINE = '    echo "$name 3.2.0 (current)"\n    exit 0\n'


def _gate_stub(check_body):
    """Compose a plugin-check stub: prologue + caller body + resolved echo + epilogue."""
    return (
        _GATE_PROLOGUE + check_body + _GATE_RESOLVE_OK + _GATE_OK_LINE + _GATE_EPILOGUE
    )


@pytest.mark.parametrize(
    ("target_spec", "check_lines", "should_pass", "expect_note"),
    [
        # The TUI section echoes the configured spec (alias included) even
        # without a Server section — recorded from the 2026-11-03 TMPDIR run.
        (
            "@tarquinen/opencode-dcp@latest",
            ["@tarquinen/opencode-dcp@latest 3.2.0 (current)"],
            True,
            False,
        ),
        # Server + TUI sections printing one agreeing (version, status) pair.
        (
            "@tarquinen/opencode-dcp@latest",
            [
                "Server",
                "  opencode-dcp 3.2.0 (current)",
                "TUI",
                "  @tarquinen/opencode-dcp@latest 3.2.0 (current)",
            ],
            True,
            False,
        ),
        # Disagreeing sections are a real divergence and fail closed.
        (
            "@tarquinen/opencode-dcp@latest",
            [
                "Server",
                "  opencode-dcp 3.2.0 (current)",
                "TUI",
                "  @tarquinen/opencode-dcp@latest 3.3.0 (latest)",
            ],
            False,
            False,
        ),
        # Recorded 2026-11-03: @plannotator prints (update available) while
        # configured with @latest; ordinary deploys pass with a note.
        (
            "@plannotator/opencode@latest",
            ["plannotator 0.28.45 (update available)"],
            True,
            True,
        ),
    ],
)
def test_live_v2_check_output_shapes(
    tmp_path, target_spec, check_lines, should_pass, expect_note
):
    body = "\n".join("        echo " + repr(line) for line in check_lines)
    script = (
        _GATE_PROLOGUE
        + f'    case "$3" in\n        {target_spec})\n'
        + body
        + "\n        exit 0\n        ;;\n"
        + _GATE_RESOLVE_OK.splitlines()[1]
        + "\n"
        + _GATE_RESOLVE_OK.splitlines()[2]
        + "\n"
        + "    esac\n"
        + '    echo "'
        + (
            "opencode-vibeguard-v2 3.2.0 (current)"
            if target_spec.startswith("github")
            else "${3%@*} 3.2.0 (current)"
        )
        + '"\n    exit 0\n'
        + _GATE_EPILOGUE
    )
    result = run_installer(tmp_path, opencode_script=script)
    combined = result.stdout + result.stderr
    assert (result.returncode == 0) is should_pass, combined
    if expect_note and should_pass:
        assert "behind upstream" in combined
    if not should_pass:
        assert "ambiguous version/status lines" in combined


def test_update_lane_refreshes_update_available_and_rechecks_current(tmp_path):
    script = (
        _GATE_ADD_LINE
        + '    if [[ $1 == plugin && $2 == update ]]; then touch "$HOME/plugin-updated"; exit 0; fi\n'
        + _GATE_CHECK_IF
        + _GATE_RESOLVE_OK
        + (
            '    if [[ "$3" == "@tarquinen/opencode-dcp@latest" ]]; then\n'
            '        if [[ -f "$HOME/plugin-updated" ]]; then\n'
            '            echo "$name 3.2.1 (current)"\n'
            "            exit 0\n"
            "        fi\n"
            '        echo "$name 3.2.0 (update available)"\n'
            "        exit 0\n"
            "    fi\n"
            '    echo "$name 3.2.0 (current)"\n'
            "    exit 0\n"
        )
        + _GATE_EPILOGUE
    )
    result = run_installer(tmp_path, opencode_script=script, update_gate="1")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "OpenCode plugin update @tarquinen/opencode-dcp@latest" in combined
    assert "resolved=3.2.1" in combined
    assert "behind upstream" in combined


def test_update_lane_recheck_still_update_available_fails_closed(tmp_path):
    script = (
        _GATE_PROLOGUE
        + _GATE_RESOLVE_OK
        + (
            '    if [[ "$3" == "@tarquinen/opencode-dcp@latest" ]]; then\n'
            '        echo "$name 3.2.0 (update available)"\n'
            "        exit 0\n"
            "    fi\n"
            '    echo "$name 3.2.0 (current)"\n'
            "    exit 0\n"
        )
        + _GATE_EPILOGUE
    )
    result = run_installer(tmp_path, opencode_script=script, update_gate="1")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "recheck parser failed" in result.stdout + result.stderr


def test_recorded_context_transient_gets_one_retry(tmp_path):
    script = (
        _GATE_PROLOGUE
        + (
            '    n=$(cat "$HOME/check-count" 2>/dev/null || echo 0)\n'
            "    n=$((n + 1))\n"
            '    echo "$n" > "$HOME/check-count"\n'
        )
        + _GATE_RESOLVE_OK
        + (
            '    if [[ "$n" -eq 1 ]]; then\n'
            '        echo "Error: Plugin is not configured: $3"\n'
            "        exit 1\n"
            "    fi\n"
            '    echo "$name 3.2.0 (current)"\n'
            "    exit 0\n"
        )
        + _GATE_EPILOGUE
    )
    result = run_installer(tmp_path, opencode_script=script)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "retrying once" in combined


def test_persistent_context_error_fails_closed(tmp_path):
    script = (
        _GATE_PROLOGUE
        + '    echo "Error: Plugin is not configured: $3"\n    exit 1\n'
        + _GATE_EPILOGUE
    )
    result = run_installer(tmp_path, opencode_script=script)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined


def test_successful_update_recheck_uses_same_current_line_parser(tmp_path):
    script = """if [[ $1 == plugin && $2 == add ]]; then exit 0; fi
if [[ $1 == plugin && $2 == check ]]; then case "$3" in github:*) echo 'opencode-vibeguard-v2 3.2.0 (current)' ;; *) echo "${3%@*} 3.2.0 (current)" ;; esac; exit 0; fi
if [[ $1 == plugin && $2 == update ]]; then exit 0; fi
"""
    result = run_installer(tmp_path, opencode_script=script, update_gate="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "did not converge" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("post_status", "should_pass"), [("current", True), ("latest", False)]
)
def test_plugin_update_accepts_new_concrete_version_only_if_current(
    tmp_path, post_status, should_pass
):
    # A successful update is expected to change the resolved version.
    # The pre-update 3.2.0 is not an equality constraint for the 3.2.1 recheck.
    script = """if [[ $1 == plugin && $2 == add ]]; then exit 0; fi
if [[ $1 == plugin && $2 == update ]]; then touch "$HOME/plugin-updated"; exit 0; fi
if [[ $1 == plugin && $2 == check ]]; then
  version=3.2.0; status=current
  if [[ -f "$HOME/plugin-updated" ]]; then
    version=3.2.1; status=__POST_STATUS__
  fi
  case "$3" in
    github:*) name=opencode-vibeguard-v2 ;;
    *) name="${3%@*}" ;;
  esac
  echo "$name $version ($status)"
  exit 0
fi
""".replace("__POST_STATUS__", post_status)
    result = run_installer(tmp_path, opencode_script=script, update_gate="1")
    combined = result.stdout + result.stderr
    assert (result.returncode == 0) is should_pass, combined
    if should_pass:
        assert "previous=3.2.0; resolved=3.2.1" in combined
    else:
        assert "recheck parser failed" in combined


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


def test_plugin_update_available_is_rejected_for_pinned_specs():
    source = TEMPLATE.read_text()
    parser = (
        "parse_plugin_check() {"
        + source.split("parse_plugin_check() {", 1)[1].split("\nHOST_STATE=", 1)[0]
    )
    for spec, status, should_pass in (
        ("@tarquinen/opencode-dcp@3.2.0", "update available", False),
        ("@tarquinen/opencode-dcp@latest", "update available", True),
        ("@tarquinen/opencode-dcp@3.2.0", "current", True),
    ):
        result = subprocess.run(
            [
                "bash",
                "-c",
                parser + '\nparse_plugin_check "$1" "$2" "" 0 0',
                "bash",
                spec,
                f"opencode-dcp 3.2.0 ({status})",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert (result.returncode == 0) is should_pass, (
            spec + result.stdout + result.stderr
        )
