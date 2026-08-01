import os
import logging
from pathlib import Path


def ensure_dir(directory):
    """
    Ensure a directory exists, creating parent dirs as needed.
    """
    Path(directory).mkdir(parents=True, exist_ok=True)


def ensure_symlink(target, link):
    """
    Create or update a symlink. Replaces existing symlink, warns if target is a real directory.
    """
    link_path = Path(link)

    # Check if link is a symlink
    if link_path.is_symlink():
        current_target = os.readlink(str(link_path))
        if current_target == str(target):
            logging.info(f"Symlink already exists: {link_path.name} -> {target}")
        else:
            logging.warning(
                f"Symlink {link_path.name} points to '{current_target}' (expected {target}) — replacing"
            )
            link_path.unlink()
            os.symlink(target, str(link_path))
            logging.info(f"Symlink updated: {link_path.name} -> {target}")
    elif link_path.is_dir() and not link_path.is_symlink():
        logging.warning(
            f"{link_path.name} is a directory (not a symlink) at {link_path} — skipping"
        )
        logging.info(f"  If you want a symlink, remove {link_path}/ and re-run")
    else:
        # Create a new symlink. Ensure target's parent dir exists if needed.
        if link_path.exists():
            link_path.unlink()
        os.symlink(target, str(link_path))
        logging.info(f"Symlink created: {link_path.name} -> {target}")


def ensure_ai_dirs(project_root):
    """
    Ensure the standard JetBrains AI directory structure under a base dir.
    Creates: .ai/mcp, .ai/plans, .ai/review, .ai/rules, .ai/agents
    Then creates symlinks: .junie -> .ai, .aiassistant/rules -> .ai/rules

    Note: .ai/memory/ is NOT created here — it is managed by JetBrains AI
    Assistant tooling at runtime. It is gitignored and local-only. It may
    contain: tasks.md, errors.md, feedback.md (agent memory placeholders,
    typically empty), language.json and memory.version (JetBrains artifacts).
    Keep the directory; do not commit its contents.
    """
    project_path = Path(project_root)
    ai_dir = project_path / ".ai"

    # Create .ai subdirectories
    for subdir in ["mcp", "plans", "review", "rules", "agents"]:
        ensure_dir(ai_dir / subdir)

    # .junie -> .ai symlink
    ensure_symlink(".ai", project_path / ".junie")

    # .aiassistant/rules -> .ai/rules symlink
    aiassistant_dir = project_path / ".aiassistant"
    ensure_dir(aiassistant_dir)
    ensure_symlink("../.ai/rules", aiassistant_dir / "rules")
