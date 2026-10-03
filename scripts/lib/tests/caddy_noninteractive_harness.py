"""Fixture helper for hermetic Caddy noninteractive preflight tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / ".chezmoiscripts/run_onchange_24-install-caddy.sh.tmpl"
MAKEFILE = ROOT / "Makefile"
PYTHON = Path(sys.executable).resolve()
CHEZMOI = Path(shutil.which("chezmoi") or "chezmoi").resolve()
BASH = Path(shutil.which("bash") or "/bin/bash").resolve()
MAKE = Path(shutil.which("make") or "/usr/bin/make").resolve()
PRESERVE_ENV = (
    "--preserve-env=BREW_BIN,DOTFILES_RUN_CADDY_SETUP,"
    "DOTFILES_LITELLM_UI_EXPOSED,CADDY_EXPLICIT_RETRY"
)
BOUNDARY_TARGET = "__caddy_preflight_boundary_probe__"
PROBE_FAILURE_EXIT = 10
LATER_LIB_EXIT = 99
FORBIDDEN_TOOLS = (
    "apt",
    "apt-get",
    "brew",
    "curl",
    "wget",
    "install",
    "caddy",
    "launchctl",
    "systemctl",
    "pkill",
)


def makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


@dataclass(frozen=True)
class CaddyFixture:
    root: Path
    source: Path
    home: Path
    bin_dir: Path
    temp: Path
    config: Path
    events: Path
    registry: Path
    env: dict[str, str]


def _event_tool_source(events_file: Path) -> str:
    lines = [
        f"#!{PYTHON}",
        "import fcntl",
        "import json",
        "import sys",
        "from pathlib import Path",
        f"destination = Path({json.dumps(str(events_file))})",
        "def main():",
        "    if len(sys.argv) < 2:",
        "        raise SystemExit(2)",
        "    event = sys.argv[1]",
        "    allowed = {",
        '        "common_entry",',
        '        "retry_marker",',
        '        "boundary_probe_ok",',
        '        "boundary_probe_failed",',
        '        "later_lib_source",',
        '        "forbidden_tool",',
        '        "sudo_preflight",',
        '        "make_syntax_check",',
        '        "make_execute",',
        '        "make_chmod",',
        '        "make_mktemp",',
        '        "make_rm",',
        '        "controlled_render",',
        "    }",
        "    if event not in allowed:",
        '        event = "unexpected_event"',
        '    record = {"event": event, "argv": sys.argv[2:]}',
        "    with destination.open('a', encoding='utf-8') as stream:",
        "        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)",
        "        stream.write(json.dumps(record, sort_keys=True) + '\\n')",
        "if __name__ == '__main__':",
        "    main()",
    ]
    return "\n".join(lines) + "\n"


def _common_fixture_source(python: Path, event_tool: Path) -> str:
    lines = [
        "#!/usr/bin/env bash",
        f'"{python}" "{event_tool}" common_entry',
        'if [[ "${_CADDY_EXPLICIT_RETRY:-0}" != "0" ]]; then',
        f'  "{python}" "{event_tool}" retry_marker --retry=1',
        "fi",
        f"if sudo {BOUNDARY_TARGET}; then",
        f'  "{python}" "{event_tool}" boundary_probe_ok 0',
        "else",
        "  probe_status=$?",
        f'  "{python}" "{event_tool}" boundary_probe_failed "$probe_status"',
        f"  exit {PROBE_FAILURE_EXIT}",
        "fi",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def _utility_source(action: str, fixture_root: Path) -> str:
    return (
        f"#!{PYTHON}\n"
        "import sys\n"
        f"sys.path.insert(0, {json.dumps(str(Path(__file__).resolve().parent))})\n"
        "import caddy_noninteractive_harness as harness\n"
        f"fixture = harness.load_fixture(Path({json.dumps(str(fixture_root / 'fixture.json'))}))\n"
        "harness.BASH = Path(fixture.env['CADDY_REAL_BASH'])\n"
        f"raise SystemExit(harness.run_fixture_utility({action!r}, sys.argv[1:], fixture))\n"
    ).replace("import sys\n", "import sys\nfrom pathlib import Path\n", 1)


def _register(path: Path, fixture: CaddyFixture) -> None:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or resolved.parent != fixture.temp.resolve(strict=True):
        raise AssertionError(f"Refusing untrusted fixture temp path: {path}")
    paths = json.loads(fixture.registry.read_text(encoding="utf-8"))
    if str(resolved) not in paths:
        paths.append(str(resolved))
        fixture.registry.write_text(json.dumps(paths), encoding="utf-8")


def _registered_file(raw_path: str, fixture: CaddyFixture) -> Path:
    path = Path(raw_path)
    resolved = path.resolve(strict=True)
    paths = json.loads(fixture.registry.read_text(encoding="utf-8"))
    if (
        path.is_symlink()
        or not resolved.is_file()
        or resolved.parent != fixture.temp.resolve(strict=True)
        or str(resolved) not in paths
    ):
        raise AssertionError(f"Refusing unregistered fixture temp: {raw_path}")
    return resolved


def load_fixture(manifest: Path) -> CaddyFixture:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return CaddyFixture(
        root=Path(data["root"]),
        source=Path(data["source"]),
        home=Path(data["home"]),
        bin_dir=Path(data["bin_dir"]),
        temp=Path(data["temp"]),
        config=Path(data["config"]),
        events=Path(data["events"]),
        registry=Path(data["registry"]),
        env=data["env"],
    )


def run_fixture_utility(action: str, args: list[str], fixture: CaddyFixture) -> int:
    if action == "mktemp":
        expected = str(fixture.temp / "dotfiles-caddy-setup.XXXXXX")
        if args != [expected]:
            return 2
        descriptor, raw = tempfile.mkstemp(
            prefix="dotfiles-caddy-setup.", dir=str(fixture.temp)
        )
        os.close(descriptor)
        _register(Path(raw), fixture)
        record_event(fixture, "make_mktemp", [f"--path={raw}"])
        print(raw)
        return 0
    if action == "bash":
        syntax = len(args) == 2 and args[0] == "-n"
        if not syntax and len(args) != 1:
            return 2
        if syntax and args[0] != "-n":
            return 2
        path = _registered_file(args[-1], fixture)
        validate_render(fixture.env["CADDY_TEST_PLATFORM"], fixture, path.read_text())
        if syntax:
            record_event(fixture, "make_syntax_check", args)
            command = [str(BASH), "-n", str(path)]
            env = fixture.env
        else:
            record_event(fixture, "make_execute", args)
            command = [str(BASH), str(path)]
            env = fixture.env.copy()
            env["CADDY_EXPLICIT_RETRY"] = os.environ.get("CADDY_EXPLICIT_RETRY", "0")
        return subprocess.run(command, env=env, timeout=10, check=False).returncode
    if action == "chmod":
        if len(args) != 2 or args[0] != "700":
            return 2
        path = _registered_file(args[1], fixture)
        path.chmod(0o700)
        record_event(fixture, "make_chmod", args)
        return 0
    if action == "rm":
        if len(args) != 2 or args[0] != "-f":
            return 2
        path = _registered_file(args[1], fixture)
        paths = json.loads(fixture.registry.read_text(encoding="utf-8"))
        path.unlink()
        fixture.registry.write_text(
            json.dumps([entry for entry in paths if entry != str(path)]),
            encoding="utf-8",
        )
        record_event(fixture, "make_rm", args)
        return 0
    return 2


def record_event(fixture: CaddyFixture, event: str, argv: list[str]) -> None:
    with fixture.events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, "argv": argv}) + "\n")


def _wrapper_source(
    platform: str,
    fixture: CaddyFixture,
) -> str:
    return (
        "\n".join(
            [
                f"#!{PYTHON}",
                "import json",
                "from pathlib import Path",
                "import subprocess",
                "import sys",
                f"BINARY = {json.dumps(str(CHEZMOI))}",
                f"EXPECTED_SOURCE = {json.dumps(str(ROOT))}",
                f"PLATFORM = {json.dumps(platform)}",
                f"SOURCE_DIR = {json.dumps(str(fixture.source))}",
                f"CONFIG_PATH = {json.dumps(str(fixture.config))}",
                f"HOME_PATH = {json.dumps(str(fixture.home))}",
                f"BIN_PATH = {json.dumps(str(fixture.bin_dir))}",
                f"TEMP_PATH = {json.dumps(str(fixture.temp))}",
                f"EVENT_PATH = {json.dumps(str(fixture.events))}",
                f"TEMPLATE_PATH = {json.dumps(str(TEMPLATE))}",
                "def main():",
                "    if len(sys.argv) != 4 or sys.argv[1] != '--source' or sys.argv[2] != EXPECTED_SOURCE or sys.argv[3] != 'execute-template':",
                "        return 2",
                "    env = {",
                "        'HOME': HOME_PATH,",
                "        'PATH': BIN_PATH,",
                "        'TMPDIR': TEMP_PATH,",
                "        'LC_ALL': 'C',",
                "        'LANG': 'C',",
                f"        'DOTFILES_RUN_CADDY_SETUP': {json.dumps(fixture.env['DOTFILES_RUN_CADDY_SETUP'])},",
                "        'DOTFILES_LITELLM_UI_EXPOSED': '0',",
                "        'CADDY_EXPLICIT_RETRY': '0',",
                f"        'SUDO_MODE': {json.dumps(fixture.env['SUDO_MODE'])},",
                "    }",
                "    override = json.dumps({'chezmoi': {'os': PLATFORM, 'sourceDir': SOURCE_DIR}})",
                "    rendered = subprocess.run(",
                "        [BINARY, '--config', CONFIG_PATH, '--source', EXPECTED_SOURCE, '--override-data', override, 'execute-template'],",
                f"        input=Path({json.dumps(str(TEMPLATE))}).read_text(encoding='utf-8'),",
                "        text=True,",
                "        capture_output=True,",
                "        env=env,",
                "        cwd=EXPECTED_SOURCE,",
                "        timeout=10,",
                "        check=True,",
                "    ).stdout",
                f"    sys.path.insert(0, {json.dumps(str(Path(__file__).resolve().parent))})",
                "    import caddy_noninteractive_harness as harness",
                f"    fixture = harness.load_fixture(Path({json.dumps(str(fixture.root / 'fixture.json'))}))",
                "    harness.validate_render(PLATFORM, fixture, rendered)",
                "    record = {'event': 'controlled_render', 'platform': PLATFORM}",
                "    with open(EVENT_PATH, 'a', encoding='utf-8') as stream:",
                "        stream.write(json.dumps(record, sort_keys=True) + chr(10))",
                "    sys.stdout.write(rendered)",
                "    return 0",
                "if __name__ == '__main__':",
                "    raise SystemExit(main())",
            ]
        )
        + "\n"
    )


def _stub_tools(
    bin_dir: Path,
    fixture_root: Path,
) -> None:
    actions = {"bash": "bash", "mktemp": "mktemp", "chmod": "chmod", "rm": "rm"}
    for name, action in actions.items():
        executable = bin_dir / name
        executable.write_text(_utility_source(action, fixture_root), encoding="utf-8")
        executable.chmod(0o755)


def new_fixture(
    tmp_path: Path,
    platform: str,
    gate: str,
    sudo_mode: str = "unavailable",
) -> CaddyFixture:
    assert platform in {"darwin", "linux"}
    assert gate in {"0", "1"}
    assert sudo_mode in {"unavailable", "authorized", "interactive", "probe-rejected"}
    root = tmp_path / "caddy-fixture"
    home = root / "home"
    bin_dir = root / "bin"
    source = root / "source"
    source_lib = source / "scripts" / "lib"
    temp_dir = root / "temp"
    config_dir = root / "config"
    cache_dir = root / "cache"
    for directory in (home, bin_dir, source_lib, temp_dir, config_dir, cache_dir):
        directory.mkdir(parents=True)
    events = root / "events.jsonl"
    registry = root / "registered-temps.json"
    registry.write_text("[]", encoding="utf-8")
    event_tool = bin_dir / "record-event.py"
    event_tool.write_text(_event_tool_source(events), encoding="utf-8")
    config = config_dir / "chezmoi.json"
    config.write_text("{}\n", encoding="utf-8")
    (home / ".env").write_text(
        f"DOTFILES_RUN_CADDY_SETUP='{gate}'\n"
        "CADDY_EXPLICIT_RETRY='0'\n"
        "DOTFILES_LITELLM_UI_EXPOSED='0'\n",
        encoding="utf-8",
    )
    fake_id = bin_dir / "id"
    fake_id.write_text(
        '#!/bin/sh\n[ "$#" -eq 1 ] && [ "$1" = "-u" ] || exit 2\nprintf "501\\n"\n',
        encoding="utf-8",
    )
    fake_id.chmod(0o755)
    (bin_dir / "sudo").write_text(
        _sudo_stub_source(PYTHON, event_tool, PRESERVE_ENV, temp_dir),
        encoding="utf-8",
    )
    (bin_dir / "sudo").chmod(0o755)
    (source_lib / "common.sh").write_text(
        _common_fixture_source(PYTHON, event_tool), encoding="utf-8"
    )
    for name in ("env.sh", "caddy_helpers.sh"):
        stub = source_lib / name
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'"{PYTHON}" "{event_tool}" later_lib_source --file={name}\n'
            f"exit {LATER_LIB_EXIT}\n",
            encoding="utf-8",
        )
    for name in FORBIDDEN_TOOLS:
        stub = bin_dir / name
        stub.write_text(
            "#!/bin/sh\n"
            f'"{PYTHON}" "{event_tool}" forbidden_tool --tool={name}\n'
            "exit 1\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": str(bin_dir),
        "TMPDIR": str(temp_dir),
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CADDY_EXPLICIT_RETRY": "0",
        "SUDO_MODE": sudo_mode,
        "DOTFILES_RUN_CADDY_SETUP": gate,
        "DOTFILES_LITELLM_UI_EXPOSED": "0",
        "CADDY_EVENT_FILE": str(events),
        "EVENT_TOOL": str(event_tool),
        "CADDY_TEST_PLATFORM": platform,
        "CADDY_REAL_BASH": str(BASH),
    }
    if platform == "darwin":
        env["BREW_BIN"] = str(bin_dir / "brew")
    env["XDG_CACHE_HOME"] = str(cache_dir)
    env["XDG_CONFIG_HOME"] = str(config_dir)
    for forbidden in (
        "BASH_ENV",
        "ENV",
        "MAKEFLAGS",
        "SHELLOPTS",
        "COVERAGE_PROCESS_START",
        "COVERAGE_FILE",
        "COVERAGE_RCFILE",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        env.pop(forbidden, None)
    fixture = CaddyFixture(
        root=root,
        source=source,
        home=home,
        bin_dir=bin_dir,
        temp=temp_dir,
        config=config,
        events=events,
        registry=registry,
        env=env,
    )
    manifest = {
        "root": str(root),
        "source": str(source),
        "home": str(home),
        "bin_dir": str(bin_dir),
        "temp": str(temp_dir),
        "config": str(config),
        "events": str(events),
        "registry": str(registry),
        "env": env,
    }
    (root / "fixture.json").write_text(json.dumps(manifest), encoding="utf-8")
    _stub_tools(bin_dir, root)
    wrapper = bin_dir / "controlled-chezmoi-wrapper.py"
    wrapper.write_text(_wrapper_source(platform, fixture), encoding="utf-8")
    wrapper.chmod(0o755)
    return fixture


def _sudo_stub_source(
    python: Path,
    event_tool: Path,
    preserve_env: str,
    temp_dir: Path,
) -> str:
    render_prefix = f"{temp_dir}/caddy-direct-render-"
    make_prefix = f"{temp_dir}/dotfiles-caddy-setup."
    lines = [
        "#!/bin/sh",
        "set -u",
        f'"{python}" "{event_tool}" sudo_preflight "$@"',
        'if [ "$#" -eq 2 ] && [ "${1:-}" = "-n" ] && [ "${2:-}" = "true" ]; then',
        '  if [ "${SUDO_MODE:-}" = "unavailable" ]; then exit 1; fi',
        '  if [ "${SUDO_MODE:-}" = "probe-rejected" ]; then exit 0; fi',
        '  if [ "${SUDO_MODE:-}" = "authorized" ]; then exit 0; fi',
        "  exit 0",
        "fi",
        'if [ "$#" -eq 2 ] && [ "${1:-}" = "-n" ] && '
        + f'[ "${{2:-}}" = "{BOUNDARY_TARGET}" ]; then',
        '  if [ "${SUDO_MODE:-}" = "probe-rejected" ]; then exit 1; fi',
        '  if [ "${SUDO_MODE:-}" = "authorized" ]; then exit 0; fi',
        "  exit 1",
        "fi",
        'if [ "$#" -eq 3 ] && [ "${1:-}" = "-n" ] && '
        + f'[ "${{2:-}}" = "{preserve_env}" ]; then',
        '  case "${3:-}" in',
        f'    {render_prefix}*) if [ "${{SUDO_MODE:-}}" = "authorized" ]; then exit 0; fi ;;',
        f'    {make_prefix}*) if [ "${{SUDO_MODE:-}}" = "authorized" ]; then exit 0; fi ;;',
        "    *) exit 2 ;;",
        "  esac",
        "  exit 2",
        "fi",
        'if [ "${#}" -eq 3 ] && [ "${1:-}" = "--preserve-env=BREW_BIN" ]; then exit 2; fi',
        'if [ "${#}" -eq 2 ] && [ "${1:-}" = "'
        + preserve_env
        + '" ] && [ "${SUDO_MODE:-}" = "interactive" ]; then',
        "  exit 1",
        "fi",
        "exit 2",
    ]
    return "\n".join(lines) + "\n"


def render_forced(platform: str, fixture: CaddyFixture) -> str:
    override = json.dumps(
        {"chezmoi": {"os": platform, "sourceDir": str(fixture.source)}}
    )
    return subprocess.run(
        [
            str(CHEZMOI),
            "--config",
            str(fixture.config),
            "--source",
            str(ROOT),
            "--override-data",
            override,
            "execute-template",
        ],
        input=TEMPLATE.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
        env=fixture.env,
        cwd=str(ROOT),
        check=True,
        timeout=30,
    ).stdout


def render_forced_wrong_source(platform: str, fixture: CaddyFixture) -> str:
    override = json.dumps(
        {"not-chezmoi": {"os": platform, "sourceDir": str(fixture.source)}}
    )
    return subprocess.run(
        [
            str(CHEZMOI),
            "--config",
            str(fixture.config),
            "--source",
            str(ROOT),
            "--override-data",
            override,
            "execute-template",
        ],
        input=TEMPLATE.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
        env=fixture.env,
        cwd=str(ROOT),
        check=True,
        timeout=30,
    ).stdout


def validate_render(platform: str, fixture: CaddyFixture, rendered: str) -> None:
    lib_line = f'LIB_DIR="{fixture.source / "scripts/lib"}"'
    common_source = 'source "$LIB_DIR/common.sh"'
    tail = (
        'ok "Caddy setup complete."'
        if platform == "darwin"
        else 'ok "Caddy systemd setup complete."'
    )
    package = (
        '"$BREW_BIN" install caddy'
        if platform == "darwin"
        else "sudo apt-get install -y caddy"
    )
    path_reset = (
        'PATH="$(dirname "$BREW_BIN"):${PATH}"'
        if platform == "darwin"
        else 'PATH="/usr/local/bin:/usr/bin:/bin:${REAL_HOME}/.local/bin:${PATH}"'
    )
    prefix = (
        'if [[ "$(id -u)" -ne 0 ]]; then'
        if platform == "darwin"
        else 'if [[ "$(id -u)" -ne 0 && ! -t 0 && ! -t 2 ]]; then'
    )
    required = (
        rendered.lstrip().startswith("#!/usr/bin/env bash"),
        "set -euo pipefail" in rendered,
        prefix in rendered,
        lib_line in rendered,
        common_source in rendered,
        package in rendered,
        path_reset in rendered,
        tail in rendered,
        "--dry-run" not in rendered and "--force" not in rendered,
    )
    if not all(required):
        raise AssertionError(
            "Unsafe or incomplete render rejected\n"
            f"platform={platform}\nlib_line={lib_line!r}\nprefix={rendered[:200]!r}"
        )
    common_index = rendered.index(common_source)
    package_index = rendered.index(package)
    path_index = rendered.index(path_reset)
    if rendered.count(lib_line) != 1 or rendered.count(common_source) != 1:
        raise AssertionError("Fixture source seam is not unique")
    if common_index > package_index or common_index > path_index:
        raise AssertionError(
            "Common library must be sourced before any PATH reset or package action"
        )
    expected_preflight = (
        f'exec sudo -n {PRESERVE_ENV} "$0" "$@"' in rendered
        if platform == "darwin"
        else 'sudo() { command sudo -n "$@"; }' in rendered
    )
    if not expected_preflight:
        raise AssertionError(
            f"Missing expected platform preflight in render for {platform}"
        )


def write_render(fixture: CaddyFixture, rendered: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix="caddy-direct-render-", suffix=".sh", dir=str(fixture.temp)
    )
    os.close(descriptor)
    path = Path(raw_path)
    path.write_text(rendered, encoding="utf-8")
    path.chmod(0o700)
    _register(path, fixture)
    return path


def run_render(
    platform: str, fixture: CaddyFixture
) -> subprocess.CompletedProcess[str]:
    rendered = render_forced(platform, fixture)
    validate_render(platform, fixture, rendered)
    script = write_render(fixture, rendered)
    try:
        return subprocess.run(
            [str(script)],
            capture_output=True,
            text=True,
            env=fixture.env,
            cwd=str(fixture.home),
            timeout=10,
        )
    finally:
        script.unlink()


def run_render_with_pty(
    platform: str, fixture: CaddyFixture
) -> subprocess.CompletedProcess[str]:
    import pty

    rendered = render_forced(platform, fixture)
    validate_render(platform, fixture, rendered)
    script = write_render(fixture, rendered)
    master, slave = pty.openpty()
    try:
        return subprocess.run(
            [str(script)],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=slave,
            text=True,
            env=fixture.env,
            cwd=str(fixture.home),
            timeout=10,
        )
    finally:
        os.close(slave)
        os.close(master)
        script.unlink()


def run_make(
    fixture: CaddyFixture, *arguments: str
) -> subprocess.CompletedProcess[str]:
    wrapper = fixture.bin_dir / "controlled-chezmoi-wrapper.py"
    command = [
        str(MAKE),
        "-C",
        str(ROOT),
        "caddy-setup",
        f"CHEZMOI={wrapper}",
        f"CHEZMOI_SOURCE={ROOT}",
        f"ENV_FILE={fixture.home / '.env'}",
        f"SHELL={BASH}",
        "SHFMT=",
        *arguments,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=fixture.env,
        cwd=str(ROOT),
        timeout=30,
    )


def read_events(fixture: CaddyFixture) -> list[dict[str, Any]]:
    if not fixture.events.exists():
        return []
    lines = fixture.events.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def events_named(fixture: CaddyFixture, event: str) -> list[dict[str, Any]]:
    return [record for record in read_events(fixture) if record.get("event") == event]


def assert_no_events(fixture: CaddyFixture, names: tuple[str, ...]) -> None:
    found = [
        record["event"]
        for record in read_events(fixture)
        if record.get("event") in set(names)
    ]
    assert not found, f"unexpected events: {found!r}; all={read_events(fixture)!r}"
