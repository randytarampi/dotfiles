#!/usr/bin/env python3
"""Shared file utility helpers for AI configuration scripts.

Provides backup and write utilities used across configure scripts.
"""

import os
import shutil

import logger


def backup_file(path, enabled=True):
    """Create a .bak backup of a file.

    Args:
      path: File path to back up.
      enabled: If False, skip backup entirely.

    Returns:
      Backup path if created, None otherwise.
    """
    if not enabled or not os.path.exists(path):
        return None
    backup_path = f"{path}.bak"
    try:
        shutil.copy2(path, backup_path)
        return backup_path
    except Exception as exc:
        logger.warning(f"Failed to backup {path}: {exc}")
        return None


def write_text_file(path, content, backup=True):
    """Write text content to a file, optionally backing up existing file first.

    Args:
      path: Target file path.
      content: Text content to write.
      backup: If True, create .bak of existing file before writing.
    """
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    if backup:
        backup_file(path, enabled=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
