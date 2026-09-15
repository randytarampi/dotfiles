#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Configure the macOS Metal wired-limit for oMLX (Apple Silicon only).

Apple's default Metal cap is ~75% of RAM (iogpu.wired_limit_mb unset = 0).
Models whose runtime footprint (weights + KV cache + buffers) exceeds that
cap fail to load with HTTP 507 ("does not fit under the metal_cap memory
ceiling"). This script applies the kernel knob immediately via sudo sysctl
and installs a root LaunchDaemon (com.dotfiles.omlx-wired-limit) that
re-applies it at every boot for persistence.

Env inputs:
  DOTFILES_RUN_OMLX_SETUP  Gate (default 0; script is a no-op unless 1).
  OMLX_WIRED_LIMIT_MB      Desired wired limit in MB (default 40960).
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.realpath(__file__)))
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger
from cli_helpers import add_common_args
from env import load_env
from file_utils import write_text_file

LAUNCHD_LABEL = "com.dotfiles.omlx-wired-limit"
LAUNCHD_PLIST = f"/Library/LaunchDaemons/{LAUNCHD_LABEL}.plist"
SYSCTL_KEY = "iogpu.wired_limit_mb"
DEFAULT_WIRED_LIMIT_MB = 40960
# Refuse to set more than 90% of physical RAM for Metal — the remainder must
# cover the kernel and all other processes.
MAX_WIRED_FRACTION = 0.9


def _read_wired_limit() -> int:
    """Return the current iogpu.wired_limit_mb value (0 means Apple default)."""
    result = subprocess.run(
        ["/usr/sbin/sysctl", "-n", SYSCTL_KEY],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def _physical_ram_mb() -> int:
    try:
        pages = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
        return pages // (1024 * 1024)
    except (ValueError, OSError):
        return 0


def build_plist(limit_mb: int) -> str:
    """Render the LaunchDaemon plist XML for the wired-limit boot hook."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>Comment</key>
    <string>dotfiles: raise Metal wired limit for oMLX models (boot hook)</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/sbin/sysctl</string>
        <string>{SYSCTL_KEY}={limit_mb}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""


def install_plist(
    plist_path: Path, limit_mb: int, *, dry_run: bool, backup: bool
) -> None:
    """Write (or converge) the root LaunchDaemon plist and load it."""
    rendered = build_plist(limit_mb)
    existing = None
    if plist_path.exists():
        probe_read = subprocess.run(
            ["/usr/bin/sudo", "-n", "/bin/cat", str(plist_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        existing = (
            probe_read.stdout
            if probe_read.returncode == 0
            else plist_path.read_text(encoding="utf-8")
        )
    if existing == rendered:
        logger.info(f"{LAUNCHD_LABEL} plist already up to date at {plist_path}")
    elif dry_run:
        logger.info(f"Would write LaunchDaemon plist to {plist_path}")
    else:
        _write_root_file(plist_path, rendered, backup=backup)

    if dry_run:
        logger.info("Would bootstrap and kickstart the LaunchDaemon (sudo required)")
        return

    loaded = subprocess.run(
        ["/bin/launchctl", "print", f"system/{LAUNCHD_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if loaded.returncode == 0:
        # The sysctl is idempotent and the plist only runs at boot (RunAtLoad,
        # KeepAlive=false): a loaded daemon whose plist already converged needs
        # no kickstart. Skip the sudo prompt when nothing changed on disk.
        if existing == rendered:
            logger.info(f"{LAUNCHD_LABEL} loaded — no reapply needed")
            return
        kickstart = subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                "/bin/launchctl",
                "kickstart",
                "-k",
                f"system/{LAUNCHD_LABEL}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if kickstart.returncode == 0:
            logger.info(f"{LAUNCHD_LABEL} re-applied via kickstart")
        else:
            logger.warning(
                f"kickstart {LAUNCHD_LABEL} failed — run 'sudo launchctl kickstart -k system/{LAUNCHD_LABEL}' manually"
            )
    else:
        # Not loaded: needs a bootstrap. Escalate via osascript when sudo -n
        # fails so the flow still completes from make deploy or a terminal.
        subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                "/bin/launchctl",
                "bootout",
                f"system/{LAUNCHD_LABEL}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        bootstrap = subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                "/bin/launchctl",
                "bootstrap",
                "system",
                str(plist_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if bootstrap.returncode != 0:
            prompt = "dotfiles: load oMLX wired-limit LaunchDaemon (boot persistence)"
            applescript = (
                "do shell script "
                f'"/bin/launchctl bootstrap system {plist_path}" '
                f'with administrator privileges with prompt "{prompt}"'
            )
            escalated = subprocess.run(
                ["/usr/bin/osascript", "-e", applescript],
                capture_output=True,
                text=True,
                check=False,
            )
            bootstrap = escalated
        if bootstrap.returncode == 0:
            logger.info(f"{LAUNCHD_LABEL} bootstrapped and applied")
        else:
            logger.warning(
                f"bootstrap {LAUNCHD_LABEL} failed — run 'sudo launchctl bootstrap system {plist_path}' manually"
            )


def _write_root_file(path: Path, content: str, *, backup: bool) -> None:
    """Write content to a root-owned path, escalating via osascript when needed."""
    if os.geteuid() == 0:
        write_text_file(str(path), content, backup=backup)
        os.chown(path, 0, 0)
        os.chmod(path, 0o644)
        logger.info(f"LaunchDaemon written to {path}")
        return
    tmp = Path("/tmp") / f"{LAUNCHD_LABEL}.plist.tmp"
    tmp.write_text(content, encoding="utf-8")
    if backup and path.exists():
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/bin/cp", str(path), f"{path}.bak"],
            capture_output=True,
            text=True,
            check=False,
        )
    prompt = (
        "dotfiles: install oMLX wired-limit LaunchDaemon "
        f"({path.name}, boot persistence)"
    )
    applescript = (
        f'do shell script "install -m 644 {tmp} {path} && chown root:wheel {path}" '
        f'with administrator privileges with prompt "{prompt}"'
    )
    escalated = subprocess.run(
        ["/usr/bin/osascript", "-e", applescript],
        capture_output=True,
        text=True,
        check=False,
    )
    if escalated.returncode != 0:
        logger.warning(
            f"Administrator prompt failed or was cancelled: {escalated.stderr.strip()}\n"
            f"Install manually: sudo install -m 644 {tmp} {path}"
        )
        return
    logger.info(f"LaunchDaemon written to {path}")


def apply_sysctl(limit_mb: int, *, dry_run: bool) -> bool:
    """Apply the kernel knob now; return True when a (re)apply happened or is needed."""
    current = _read_wired_limit()
    if current == limit_mb:
        logger.info(f"{SYSCTL_KEY} already {limit_mb} — no change")
        return False
    if dry_run:
        logger.info(
            f"Would run: sudo sysctl {SYSCTL_KEY}={limit_mb} (current: {current})"
        )
        return True
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/sbin/sysctl", f"{SYSCTL_KEY}={limit_mb}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            f"sudo sysctl needs a password (sudo -n failed). Run manually:\n"
            f"  sudo sysctl {SYSCTL_KEY}={limit_mb}\n"
            f"or approve the administrator prompt this script raises next."
        )
        # Fall back to an interactive administrator prompt via osascript so the
        # script works both from a terminal and from make deploy.
        prompt = (
            "dotfiles: raise Metal wired limit to "
            f"{limit_mb} MB (fixes oMLX 507 model-load errors)"
        )
        applescript = (
            f'do shell script "sysctl {SYSCTL_KEY}={limit_mb}" '
            f'with administrator privileges with prompt "{prompt}"'
        )
        escalated = subprocess.run(
            ["/usr/bin/osascript", "-e", applescript],
            capture_output=True,
            text=True,
            check=False,
        )
        if escalated.returncode != 0:
            logger.warning(
                f"Administrator prompt failed or was cancelled: {escalated.stderr.strip()}"
            )
            return False
    applied = _read_wired_limit()
    if applied == limit_mb:
        logger.info(f"{SYSCTL_KEY} set to {limit_mb}")
        return True
    logger.warning(f"{SYSCTL_KEY} is {applied}, expected {limit_mb}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply and persist the macOS Metal wired limit for oMLX."
    )
    add_common_args(parser, no_backup=True)
    args = parser.parse_args()

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        logger.info("oMLX wired-limit tuning is Apple-Silicon/macOS only — skipping")
        return

    if not load_env():
        logger.warning("~/.env not found")

    if os.environ.get("DOTFILES_RUN_OMLX_SETUP", "0") != "1":
        logger.info(
            f"DOTFILES_RUN_OMLX_SETUP='{os.environ.get('DOTFILES_RUN_OMLX_SETUP', '0')}' — skipping oMLX wired-limit configuration"
        )
        return

    raw_limit = os.environ.get("OMLX_WIRED_LIMIT_MB", "").strip()
    try:
        limit_mb = int(raw_limit) if raw_limit else DEFAULT_WIRED_LIMIT_MB
    except ValueError:
        logger.critical(
            f"Invalid OMLX_WIRED_LIMIT_MB='{raw_limit}' — expected an integer"
        )
        raise SystemExit(1)

    ram_mb = _physical_ram_mb()
    if ram_mb and limit_mb > MAX_WIRED_FRACTION * ram_mb:
        logger.critical(
            f"Refusing OMLX_WIRED_LIMIT_MB={limit_mb}: exceeds {MAX_WIRED_FRACTION:.0%} of physical RAM ({ram_mb} MB)"
        )
        raise SystemExit(1)

    apply_sysctl(limit_mb, dry_run=args.dry_run)
    install_plist(
        Path(LAUNCHD_PLIST),
        limit_mb,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )

    summary_lines = [
        "oMLX wired-limit configured!",
        "",
        f"  • Kernel: {SYSCTL_KEY}={limit_mb}",
        f"  • Boot persistence: {LAUNCHD_PLIST} ({LAUNCHD_LABEL})",
        "  • Reboot-safe; change via OMLX_WIRED_LIMIT_MB in ~/.env",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
