#!/usr/bin/env python3
"""Install ACP adapters used by OpenCode."""

import argparse, os, platform, shutil, subprocess, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
import logger
from cli_helpers import add_common_args

CLAUDE_ACP_VERSION = "latest"
CODEX_ACP_VERSION = "latest"
PI_ACP_VERSION = "latest"


def _run(command, *, cwd=None, quiet=False):
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("Command failed (%s): %s", " ".join(command), error)
        return False
    return result.returncode == 0


def _gate(name):
    value = os.environ.get(name, "0")
    if value != "1":
        logger.info("%s='%s' — skipping ACP adapter installation", name, value)
        return False
    return True


def install_antigravity_acp(dry_run=False):
    install_dir = Path.home() / ".local/share/antigravity-acp"
    link_path = Path.home() / ".local/bin/agy-acp"
    if os.environ.get("DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP", "0") != "1":
        logger.info(
            "DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP='%s' — skipping antigravity-acp install",
            os.environ.get("DOTFILES_RUN_ANTIGRAVITY_ACP_SETUP", "0"),
        )
        return 0
    logger.info("Checking Antigravity ACP prerequisites...")
    if shutil.which("bun") is None:
        logger.warning(
            "'bun' not found — skipping antigravity-acp install (build-time dependency)"
        )
        logger.warning("Install Bun first: curl -fsSL https://bun.sh | bash")
        return 0
    if shutil.which("agy") is None:
        logger.warning(
            "'agy' not found on PATH — the bridge will auto-download it, but this is unexpected"
        )
        logger.warning("Install agy first: brew install --cask antigravity-cli")
    if sys.platform != "darwin" or platform.machine() != "arm64":
        logger.warning(
            "Unsupported platform: %s — skipping antigravity-acp install", sys.platform
        )
        logger.warning("Only macOS arm64 is currently supported")
        return 0
    binary = install_dir / "dist/agy-acp-darwin-arm64"
    if dry_run:
        logger.info(
            "[DRY RUN] Would clone/update %s, build agy-acp-darwin-arm64, and symlink %s",
            install_dir,
            link_path,
        )
        return 0
    if (install_dir / ".git").is_dir():
        logger.info("Updating antigravity-acp at %s...", install_dir)
        if not _run(["git", "-C", str(install_dir), "pull", "--ff-only"]):
            logger.warning(
                "Failed to update antigravity-acp repository — continuing with existing checkout"
            )
    else:
        logger.info("Cloning antigravity-acp to %s...", install_dir)
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        if not _run(
            [
                "git",
                "clone",
                "https://github.com/shubzkothekar/antigravity-acp.git",
                str(install_dir),
            ]
        ):
            logger.error("Failed to clone antigravity-acp repository")
            return 1
    if not _run(["bun", "install"], cwd=install_dir):
        logger.error("bun install failed in antigravity-acp repository")
        return 1
    if not _run(["bun", "run", "build:mac-arm64"], cwd=install_dir):
        logger.error("Failed to build antigravity-acp prebuilt binary")
        return 1
    if not binary.is_file() or not os.access(binary, os.X_OK):
        logger.error("Expected built binary not found or not executable: %s", binary)
        return 1
    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.unlink(missing_ok=True)
    link_path.symlink_to(binary)
    logger.info("Symlinked %s → %s", binary, link_path)
    return 0


def install_npm_adapter(package, version, dry_run=False):
    if _run(["npm", "list", "-g", package], quiet=True):
        logger.info("%s already installed", package)
    elif dry_run:
        logger.info("[DRY RUN] Would install %s@%s globally", package, version)
    elif _run(["npm", "install", "-g", f"{package}@{version}"], quiet=True):
        logger.info("%s installed", package)
    else:
        logger.warning("Failed to install %s", package)
    return 0


def install_opencode_adapters(dry_run=False):
    if not _gate("DOTFILES_RUN_OPENCODE_TOOLS_SETUP"):
        return 0
    if shutil.which("copilot") is not None:
        logger.info("GitHub Copilot CLI already installed")
    elif dry_run:
        logger.info(
            "[DRY RUN] Would install GitHub Copilot CLI with brew install copilot-cli"
        )
    elif _run(["brew", "install", "copilot-cli"], quiet=True):
        logger.info("GitHub Copilot CLI installed")
    else:
        logger.warning(
            "Failed to install GitHub Copilot CLI (public preview — may require manual install)"
        )
    install_npm_adapter(
        "@agentclientprotocol/claude-agent-acp", CLAUDE_ACP_VERSION, dry_run
    )
    install_npm_adapter("@agentclientprotocol/codex-acp", CODEX_ACP_VERSION, dry_run)
    install_npm_adapter("pi-acp", PI_ACP_VERSION, dry_run)
    return 0


def install_cursor_agent(dry_run=False):
    if not _gate("DOTFILES_RUN_OPENCODE_TOOLS_SETUP"):
        return 0
    if shutil.which("cursor-agent") is not None:
        logger.info("Cursor agent CLI already installed")
        logger.info("Next step: cursor-agent login (browser auth via Cursor account)")
        return 0
    if shutil.which("cursor") is None:
        logger.warning(
            "'cursor' not found — skipping cursor-agent install (Cursor IDE required)"
        )
        return 0
    if dry_run:
        logger.info(
            "[DRY RUN] Would trigger cursor-agent auto-install via 'cursor agent --version'"
        )
        return 0
    _run(["cursor", "agent", "--version"], quiet=True)
    if shutil.which("cursor-agent") is None:
        logger.warning(
            "cursor-agent not found on PATH after auto-install — ensure ~/.local/bin is in your PATH"
        )
    else:
        logger.info("Cursor agent CLI installed")
    logger.info("Next step: cursor-agent login (browser auth via Cursor account)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install ACP (Agent Client Protocol) adapters for OpenCode."
    )
    add_common_args(parser)
    args = parser.parse_args(argv)
    for installer in (install_antigravity_acp, install_cursor_agent):
        result = installer(args.dry_run)
        if result:
            return result
    return install_opencode_adapters(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
