#!/usr/bin/env python3
"""
_configure-jetbrains-workspace-project.py — Configures JetBrains workspace modules.
Writes .ai/mcp/mcp.json and creates .junie → .ai symlink.
"""

# Manual-only: not wired into configure-all.sh because it operates on a specific
# JetBrains workspace project and requires explicit --workspace-root/--project-dir.
# Invoke directly when configuring AI dirs for a JB workspace module.

import sys
import os
import argparse
import shutil
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger


def is_forbidden_root(d, workspace_root):
    forbidden = {
        os.path.join(workspace_root, ".idea"),
        os.path.join(workspace_root, ".fleet"),
        os.path.join(workspace_root, ".air"),
        os.path.join(workspace_root, ".claude"),
        os.path.join(workspace_root, ".opencode"),
    }
    return d in forbidden


def resolve_iml_content_root(iml_path, workspace_root):
    if not os.path.isfile(iml_path):
        return None

    raw_url = ""
    try:
        tree = ET.parse(iml_path)
        root = tree.getroot()
        for content in root.iter("content"):
            raw_url = content.attrib.get("url", "")
            if raw_url:
                break
    except Exception:
        pass

    if not raw_url:
        return None

    iml_dir = os.path.dirname(iml_path)
    expanded = raw_url.replace("$MODULE_DIR$", iml_dir)

    pathpart = expanded
    if pathpart.startswith("file://"):
        pathpart = pathpart[7:]
        if pathpart.startswith("localhost"):
            pathpart = pathpart[9:]

    resolved = ""
    if pathpart.startswith("/"):
        if os.path.isdir(pathpart):
            resolved = os.path.abspath(pathpart)
    else:
        try_dir = os.path.abspath(os.path.join(iml_dir, pathpart))
        if os.path.isdir(try_dir):
            resolved = try_dir

    if resolved and os.path.isdir(resolved):
        return resolved

    # Many .idea/*.iml files use file://$MODULE_DIR$/subdir while the real tree is $PROJECT_DIR$/subdir
    if "$MODULE_DIR$/" in raw_url:
        parts = raw_url.split("$MODULE_DIR$/", 1)
        if len(parts) > 1:
            suffix = parts[1]
            if "/" not in suffix and ".." not in suffix:
                try_path = os.path.join(workspace_root, suffix)
                if os.path.isdir(try_path):
                    return os.path.abspath(try_path)

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Configure JetBrains workspace modules with symlinks to AI configs."
    )
    parser.add_argument(
        "--workspace-root",
        "--project-dir",
        dest="workspace_root",
        help="Workspace or project root directory",
    )

    args = parser.parse_args()

    workspace_root = args.workspace_root
    if not workspace_root:
        workspace_root = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
    else:
        workspace_root = os.path.abspath(os.path.expanduser(workspace_root))

    os.chdir(workspace_root)

    ide_config_dir = ""
    for c_dir in [".idea", ".fleet"]:
        if os.path.isdir(c_dir) and os.path.isfile(os.path.join(c_dir, "modules.xml")):
            ide_config_dir = c_dir
            break

    if not ide_config_dir:
        logger.warning("No JetBrains IDE configuration directory found")
        sys.exit(0)

    modules_xml_path = os.path.join(ide_config_dir, "modules.xml")

    # Parse module paths from modules.xml
    rel_paths = []
    try:
        tree = ET.parse(modules_xml_path)
        root = tree.getroot()
        for module in root.iter("module"):
            filepath = module.attrib.get("filepath", "")
            if filepath:
                # Strip $PROJECT_DIR$/ prefix
                if filepath.startswith("$PROJECT_DIR$/"):
                    rel_paths.append(filepath.replace("$PROJECT_DIR$/", ""))
                else:
                    rel_paths.append(filepath)
    except Exception as e:
        logger.critical(f"Failed to parse {modules_xml_path}: {e}")
        sys.exit(1)

    targets = set()

    def append_target(d):
        if not d or not os.path.isdir(d):
            return
        if is_forbidden_root(d, workspace_root):
            return
        targets.add(os.path.abspath(d))

    for rel_path in rel_paths:
        if not rel_path:
            continue

        # Synthetic modules: .idea/foo.iml — dirname would wrongly be .idea; use .iml content root
        if rel_path.startswith(".idea/") and rel_path.endswith(".iml"):
            iml_abs = os.path.join(workspace_root, rel_path)
            content_root = resolve_iml_content_root(iml_abs, workspace_root)
            if content_root:
                append_target(content_root)
            continue

        # Directory containing the .iml
        parent_rel = os.path.dirname(rel_path)
        if not parent_rel or parent_rel == ".":
            append_target(workspace_root)
        else:
            abs_parent = os.path.abspath(os.path.join(workspace_root, parent_rel))
            if os.path.isdir(abs_parent):
                append_target(abs_parent)

        # Repo / package root: first path segment (e.g. marketplace/api/... -> marketplace/)
        first = rel_path.split("/", 1)[0] if "/" in rel_path else rel_path
        if first and first != ".idea" and first != ".fleet" and first != rel_path:
            append_target(os.path.join(workspace_root, first))

    for abs_project_dir in sorted(targets):
        if not abs_project_dir:
            continue

        # Standard AI directories and files
        for ai_dir in [
            ".junie",
            ".ai",
            ".aiassistant",
            ".codex",
            ".cursor",
            "opencode.json",
            ".mcp.json",
        ]:
            source_path = os.path.join(workspace_root, ai_dir)
            target_path = os.path.join(abs_project_dir, ai_dir)

            if target_path == source_path or target_path.startswith(source_path + "/"):
                continue

            if os.path.exists(source_path):
                logger.info(f"Linking {source_path} to {target_path}...")
                try:
                    if os.path.islink(target_path) or os.path.exists(target_path):
                        if os.path.islink(target_path):
                            os.unlink(target_path)
                        elif os.path.isdir(target_path):
                            shutil.rmtree(target_path)
                        else:
                            os.remove(target_path)
                    os.symlink(source_path, target_path)
                except Exception as e:
                    logger.warning(
                        f"Failed to link {source_path} to {target_path}: {e}"
                    )

        # .opencode: real directory per project + specific files/dirs symlinked
        opencode_dir_src = os.path.join(workspace_root, ".opencode")
        opencode_dir_dst = os.path.join(abs_project_dir, ".opencode")

        if os.path.isdir(opencode_dir_src):
            if opencode_dir_dst != opencode_dir_src:
                try:
                    if os.path.islink(opencode_dir_dst):
                        os.unlink(opencode_dir_dst)
                    os.makedirs(opencode_dir_dst, exist_ok=True)

                    for item in ["agents", "oh-my-opencode-slim.json"]:
                        item_src = os.path.join(opencode_dir_src, item)
                        item_dst = os.path.join(opencode_dir_dst, item)

                        if os.path.exists(item_src):
                            logger.info(f"Linking {item_src} to {item_dst}...")
                            if os.path.islink(item_dst):
                                os.unlink(item_dst)
                            elif os.path.exists(item_dst):
                                if os.path.isdir(item_dst):
                                    shutil.rmtree(item_dst)
                                else:
                                    os.remove(item_dst)
                            os.symlink(item_src, item_dst)
                except Exception as e:
                    logger.warning(
                        f"Failed to configure .opencode under {abs_project_dir}: {e}"
                    )

        # .air: real directory per project + specific files/dirs symlinked (not the whole .air tree)
        # NOTE: .air/mcp.json now points to .ai/mcp/mcp.json (Air shares the same MCP config as JetBrains AI)
        air_dir_src = os.path.join(workspace_root, ".air")
        air_dir_dst = os.path.join(abs_project_dir, ".air")

        if os.path.isdir(air_dir_src):
            if air_dir_dst != air_dir_src:
                try:
                    if os.path.islink(air_dir_dst):
                        os.unlink(air_dir_dst)
                    os.makedirs(air_dir_dst, exist_ok=True)

                    # .air/mcp.json → ../.ai/mcp/mcp.json (shared with JetBrains AI)
                    ai_mcp_src = os.path.join(abs_project_dir, ".ai", "mcp", "mcp.json")
                    air_mcp_dst = os.path.join(air_dir_dst, "mcp.json")

                    if os.path.exists(ai_mcp_src):
                        logger.info(f"Linking {air_mcp_dst} -> ../.ai/mcp/mcp.json...")
                        if os.path.islink(air_mcp_dst):
                            os.unlink(air_mcp_dst)
                        elif os.path.exists(air_mcp_dst):
                            os.remove(air_mcp_dst)
                        os.symlink("../.ai/mcp/mcp.json", air_mcp_dst)

                    for item in [
                        "docker.json",
                        "worktree.json",
                        "review",
                        "plans",
                    ]:
                        item_src = os.path.join(air_dir_src, item)
                        item_dst = os.path.join(air_dir_dst, item)

                        if os.path.exists(item_src):
                            logger.info(f"Linking {item_src} to {item_dst}...")
                            if os.path.islink(item_dst):
                                os.unlink(item_dst)
                            elif os.path.exists(item_dst):
                                if os.path.isdir(item_dst):
                                    shutil.rmtree(item_dst)
                                else:
                                    os.remove(item_dst)
                            os.symlink(item_src, item_dst)
                except Exception as e:
                    logger.warning(
                        f"Failed to configure .air under {abs_project_dir}: {e}"
                    )

        # .claude: real directory per project + specific files/dirs symlinked
        claude_dir_src = os.path.join(workspace_root, ".claude")
        claude_dir_dst = os.path.join(abs_project_dir, ".claude")

        if os.path.isdir(claude_dir_src):
            if claude_dir_dst != claude_dir_src:
                try:
                    if os.path.islink(claude_dir_dst):
                        os.unlink(claude_dir_dst)
                    os.makedirs(claude_dir_dst, exist_ok=True)

                    item_src = os.path.join(claude_dir_src, "settings.local.json")
                    item_dst = os.path.join(claude_dir_dst, "settings.local.json")

                    if os.path.exists(item_src):
                        logger.info(f"Linking {item_src} to {item_dst}...")
                        if os.path.islink(item_dst):
                            os.unlink(item_dst)
                        elif os.path.exists(item_dst):
                            os.remove(item_dst)
                        os.symlink(item_src, item_dst)
                except Exception as e:
                    logger.warning(
                        f"Failed to configure .claude under {abs_project_dir}: {e}"
                    )


if __name__ == "__main__":
    main()
