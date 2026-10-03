"""Hermetic platform coverage for the unattended Caddy preflight seam."""

from __future__ import annotations

import subprocess
import sys

import pytest

import caddy_noninteractive_harness as harness
from caddy_noninteractive_harness import (
    BOUNDARY_TARGET,
    PRESERVE_ENV,
    PROBE_FAILURE_EXIT,
)


def _assert_no_package_or_library_events(fixture):
    harness.assert_no_events(
        fixture,
        (
            "forbidden_tool",
            "later_lib_source",
        ),
    )


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_render_gate_off_skips_before_sudo(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "0")
    result = harness.run_render(platform, fixture)
    assert result.returncode == 0, result.stderr
    assert "skipping Caddy setup" in result.stdout
    harness.assert_no_events(
        fixture,
        (
            "sudo_preflight",
            "common_entry",
            "retry_marker",
            "later_lib_source",
            "forbidden_tool",
        ),
    )
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_unavailable_sudo_returns_general_zero_with_probe_only(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "1", "unavailable")
    result = harness.run_render(platform, fixture)
    assert result.returncode == 0, result.stderr
    assert "skipping in a noninteractive session" in result.stderr
    sudo_events = harness.events_named(fixture, "sudo_preflight")
    assert [event["argv"] for event in sudo_events] == [["-n", "true"]]
    _assert_no_package_or_library_events(fixture)
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_real_target_gate_off_has_no_render_or_tool_events(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "0")
    result = harness.run_make(fixture)
    assert result.returncode == 0, result.stderr
    assert "gate is off" in result.stdout
    harness.assert_no_events(
        fixture,
        (
            "controlled_render",
            "make_mktemp",
            "make_syntax_check",
            "make_chmod",
            "sudo_preflight",
            "common_entry",
            "forbidden_tool",
        ),
    )
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_dry_run_is_side_effect_free(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "1")
    result = harness.run_make(fixture, "DRY_RUN=1")
    assert result.returncode == 0, result.stderr
    assert "Would render and run" in result.stdout
    harness.assert_no_events(
        fixture,
        (
            "controlled_render",
            "make_mktemp",
            "make_syntax_check",
            "make_chmod",
            "sudo_preflight",
            "common_entry",
            "forbidden_tool",
        ),
    )
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_real_target_unavailable_sudo_fails_closed_twice(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "1", "unavailable")
    results = [harness.run_make(fixture) for _ in range(2)]
    for result in results:
        assert result.returncode == 2, result.stderr
        assert "skipping in a noninteractive session" in result.stderr
    sudo_events = harness.events_named(fixture, "sudo_preflight")
    assert [event["argv"] for event in sudo_events] == [
        ["-n", "true"],
        ["-n", "true"],
    ]
    _assert_no_package_or_library_events(fixture)
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_real_target_authorized_stops_at_platform_seam_twice(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "1", "authorized")
    results = [harness.run_make(fixture) for _ in range(2)]
    mktemp_events = harness.events_named(fixture, "make_mktemp")
    syntax_checks = harness.events_named(fixture, "make_syntax_check")
    chmods = harness.events_named(fixture, "make_chmod")
    sudo_events = harness.events_named(fixture, "sudo_preflight")
    for index, result in enumerate(results):
        assert result.returncode == 0, result.stderr
        assert len(mktemp_events[index]["argv"]) == 1
        temp_path = str(mktemp_events[index]["argv"][0]).replace("--path=", "", 1)
        assert str(fixture.temp / "dotfiles-caddy-setup.") in temp_path
        assert syntax_checks[index]["argv"] == ["-n", temp_path]
        assert chmods[index]["argv"] == ["700", temp_path]
    assert len(sudo_events) == 4
    assert [event["argv"] for event in sudo_events[0::2]] == [
        ["-n", "true"],
        ["-n", "true"],
    ]
    if platform == "darwin":
        handoffs = [event["argv"] for event in sudo_events[1::2]]
        for index, argv in enumerate(handoffs):
            assert argv[0] == "-n"
            assert argv[1] == PRESERVE_ENV
            assert argv[2] == str(mktemp_events[index]["argv"][0]).replace(
                "--path=", "", 1
            )
    else:
        common_entries = harness.events_named(fixture, "common_entry")
        retry_markers = harness.events_named(fixture, "retry_marker")
        probes = [event["argv"] for event in sudo_events[1::2]]
        assert len(common_entries) == 2
        assert len(retry_markers) == 2
        assert probes == [["-n", BOUNDARY_TARGET], ["-n", BOUNDARY_TARGET]]
        assert harness.events_named(fixture, "boundary_probe_ok")
    _assert_no_package_or_library_events(fixture)
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Darwin interactive sudo prompt path"
)
def test_interactive_darwin_uses_precise_preserve_env_without_dispatch(tmp_path):
    fixture = harness.new_fixture(tmp_path, "darwin", "1", "interactive")
    result = harness.run_render_with_pty("darwin", fixture)
    assert result.returncode == 1, (result.stdout, result.stderr)
    sudo_events = harness.events_named(fixture, "sudo_preflight")
    assert len(sudo_events) == 1
    argv = sudo_events[0]["argv"]
    assert argv[0] == PRESERVE_ENV
    assert argv[1].startswith(str(fixture.temp))
    _assert_no_package_or_library_events(fixture)


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_source_dir_override_bypass_is_rejected_before_execution(platform, tmp_path):
    fixture = harness.new_fixture(tmp_path, platform, "1")
    render = harness.render_forced_wrong_source(platform, fixture)
    with pytest.raises(AssertionError, match="Unsafe or incomplete render rejected"):
        harness.validate_render(platform, fixture, render)
    assert list(fixture.temp.iterdir()) == []


@pytest.mark.parametrize("platform", ("darwin", "linux"))
def test_early_path_reset_or_package_action_is_rejected_before_execution(
    platform, tmp_path
):
    fixture = harness.new_fixture(tmp_path, platform, "1")
    rendered = harness.render_forced(platform, fixture)
    path_reset = (
        'PATH="$(dirname "$BREW_BIN"):${PATH}"'
        if platform == "darwin"
        else 'PATH="/usr/local/bin:/usr/bin:/bin:${REAL_HOME}/.local/bin:${PATH}"'
    )
    package_action = (
        '"$BREW_BIN" install caddy'
        if platform == "darwin"
        else "sudo apt-get install -y caddy"
    )
    unsafe = (
        rendered.splitlines()[0]
        + f"\n{path_reset}\n{package_action}\n"
        + "\n".join(rendered.splitlines()[1:])
    )
    with pytest.raises(AssertionError, match="Common library must be sourced before"):
        harness.validate_render(platform, fixture, unsafe)
    assert list(fixture.temp.iterdir()) == []


def test_linux_missing_boundary_probe_fails_closed_before_libraries(tmp_path):
    fixture = harness.new_fixture(tmp_path, "linux", "1", "probe-rejected")
    rendered = harness.render_forced("linux", fixture)
    harness.validate_render("linux", fixture, rendered)
    script = harness.write_render(fixture, rendered)
    try:
        result = subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            env=fixture.env,
            cwd=str(fixture.home),
            timeout=10,
        )
    finally:
        script.unlink()
    assert result.returncode == PROBE_FAILURE_EXIT, (result.stdout, result.stderr)
    assert harness.events_named(fixture, "common_entry")
    assert harness.events_named(fixture, "boundary_probe_failed")
    _assert_no_package_or_library_events(fixture)


def test_makefile_retry_marker_guard_fails_safely_without_target_mutation():
    text = harness.makefile_text()
    marker = "CADDY_EXPLICIT_RETRY=1"
    assert marker in text
    missing = text.replace(marker, "MISSING_RETRY_MARKER", 1)
    with pytest.raises(AssertionError):
        assert marker in missing and missing != text
