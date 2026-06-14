#!/usr/bin/env python3
"""
Configure MCP Tool Helper.
Consolidates all registry parses, template resolutions, format conversions, and file merges.
"""

import sys
import json
import argparse
import os
import re
import fnmatch

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = SCRIPT_DIR if SCRIPT_DIR.endswith("lib") else os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from env import load_env


def get_tool_config(registry_file: str, tool: str) -> dict:
    if not os.path.exists(registry_file):
        logger.critical(f"Registry file not found: {registry_file}")
        sys.exit(1)
    with open(registry_file, "r", encoding="utf-8") as f:
        reg = json.load(f)
    tools = reg.get("tools", {})
    if tool not in tools:
        available = ", ".join(tools.keys())
        logger.critical(f"Unknown tool '{tool}'. Available: {available}")
        sys.exit(1)
    return tools[tool]


def resolve_env_vars(obj):
    if isinstance(obj, str):

        def sub(m):
            var = m.group(1)
            val = os.environ.get(var)
            if val is not None:
                return val
            # Preserve unknown variable references like ${workspaceFolder}
            # (tool-specific runtime variables, not env vars)
            return m.group(0)

        return re.sub(r"\$\{(\w+)\}", sub, obj)
    elif isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_env_vars(i) for i in obj]
    return obj


def resolve_env_files(env_file, mode, project_dir):
    files = []
    if env_file and os.path.isfile(env_file):
        files.append(env_file)

    global_env = os.path.expanduser("~/.config/opencode/.env")
    if os.path.isfile(global_env):
        files.append(global_env)

    if mode == "project" and project_dir:
        for candidate in [
            os.path.join(project_dir, ".opencode", ".env"),
            os.path.join(project_dir, ".ai", ".env"),
            os.path.join(project_dir, ".env"),
        ]:
            if os.path.isfile(candidate):
                files.append(candidate)
    return files


def filter_mcp_templates(
    template_list: list, include_patterns: str, exclude_patterns: str
) -> list:
    result = []
    inc_pats = (
        [pat.strip() for pat in include_patterns.split(",") if pat.strip()]
        if include_patterns
        else []
    )
    exc_pats = (
        [pat.strip() for pat in exclude_patterns.split(",") if pat.strip()]
        if exclude_patterns
        else []
    )

    for tpl in template_list:
        included = True
        if inc_pats:
            included = False
            for pat in inc_pats:
                if fnmatch.fnmatch(tpl, pat):
                    included = True
                    break

        excluded = False
        if exc_pats:
            for pat in exc_pats:
                if fnmatch.fnmatch(tpl, pat):
                    excluded = True
                    break

        if included and not excluded:
            result.append(tpl)

    return result


def resolve_defs_list(templates_dir, template_list, tool_config, mode):
    server_overrides = {}
    if mode == "global":
        for s in tool_config.get("mcp_servers", []):
            name_override = s.get("name_override")
            extra_fields = s.get("extra_fields", {})
            if name_override or extra_fields:
                server_overrides[s["template"]] = {
                    "name_override": name_override,
                    "extra_fields": extra_fields,
                }

    results = []
    for tpl_name in template_list:
        tpl_path = os.path.join(templates_dir, tpl_name + ".json")
        if not os.path.exists(tpl_path):
            tpl_path = os.path.join(templates_dir, tpl_name)
        if not os.path.exists(tpl_path):
            logger.warning(f"Template {tpl_name} not found in {templates_dir}")
            continue

        with open(tpl_path, "r", encoding="utf-8") as f:
            tpl = json.load(f)

        override = server_overrides.get(tpl_name, {})
        if override.get("name_override"):
            tpl["name"] = override["name_override"]
        if override.get("extra_fields"):
            for k, v in override["extra_fields"].items():
                tpl[k] = v

        tpl = resolve_env_vars(tpl)
        results.append(tpl)
    return results


def format_configs_to_str(fmt, defs):
    if fmt == "json-mcpServers":
        result = {"mcpServers": {}}
        for d in defs:
            name = d["name"]
            entry = {}
            if d.get("type") == "url":
                entry["type"] = "streamable-http"
                entry["url"] = d["url"]
            else:
                entry["command"] = d["command"]
                if d.get("args"):
                    entry["args"] = d["args"]
            if d.get("env") and any(v for v in d["env"].values()):
                entry["env"] = {k: v for k, v in d["env"].items() if v}
            if d.get("headers"):
                entry["headers"] = d["headers"]
            if "enabled" in d:
                entry["enabled"] = d["enabled"]
            result["mcpServers"][name] = entry
        return json.dumps(result, indent=4)

    elif fmt == "toml-mcpServers":
        lines = []
        for d in defs:
            name = d["name"]
            lines.append(f"[mcp_servers.{name}]")
            if d.get("type") == "url":
                lines.append('type = "streamable-http"')
                lines.append(f'url = "{d["url"]}"')
                if d.get("headers"):
                    header_str = ", ".join(
                        f'"{k}" = "{v}"' for k, v in d["headers"].items()
                    )
                    lines.append(f"http_headers = {{ {header_str} }}")
            else:
                lines.append(f'command = "{d["command"]}"')
                if d.get("args"):
                    args_str = ", ".join(f'"{a}"' for a in d["args"])
                    lines.append(f"args = [{args_str}]")
                if d.get("env") and any(v for v in d["env"].values()):
                    lines.append(f"[mcp_servers.{name}.env]")
                    for k, v in d["env"].items():
                        if v:
                            lines.append(f'{k} = "{v}"')
            if "enabled" in d:
                lines.append(f'enabled = {str(d["enabled"]).lower()}')
            if d.get("extra_fields"):
                for k, v in d["extra_fields"].items():
                    if isinstance(v, bool):
                        lines.append(f"{k} = {str(v).lower()}")
                    elif isinstance(v, str):
                        lines.append(f'{k} = "{v}"')
            lines.append("")
        return "\n".join(lines)

    elif fmt == "opencode-internal":
        result = {}
        for d in defs:
            name = d["name"]
            if d.get("type") == "url":
                entry = {"type": "remote", "url": d["url"]}
            else:
                entry = {"type": "local"}
                cmd_array = [d["command"]] + d.get("args", [])
                entry["command"] = cmd_array
            if d.get("env") and any(v for v in d["env"].values()):
                entry["environment"] = {k: v for k, v in d["env"].items() if v}
            if d.get("headers"):
                entry["headers"] = d["headers"]
            if "enabled" in d:
                entry["enabled"] = d["enabled"]
            result[name] = entry
        return json.dumps({"mcp": result}, indent=4)

    elif fmt == "json-settings-merge":
        mcp_servers = {}
        for d in defs:
            name = d["name"]
            entry = {}
            if d.get("type") == "url":
                entry["type"] = "streamable-http"
                entry["url"] = d["url"]
            else:
                entry["command"] = d["command"]
                if d.get("args"):
                    entry["args"] = d["args"]
            if d.get("env") and any(v for v in d["env"].values()):
                entry["env"] = {k: v for k, v in d["env"].items() if v}
            if d.get("headers"):
                entry["headers"] = d["headers"]
            mcp_servers[name] = entry
        return json.dumps({"mcpServers": mcp_servers}, indent=4)
    else:
        logger.critical(f"Unsupported format: {fmt}")
        sys.exit(1)


def redact_secrets(obj):
    secret_key_re = re.compile(
        r"(TOKEN|SECRET|PASSWORD|KEY|AUTH|CREDENTIAL)", re.IGNORECASE
    )
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            if secret_key_re.search(str(key)) and value:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_secrets(value)
        return redacted
    if isinstance(obj, list):
        return [redact_secrets(value) for value in obj]
    if isinstance(obj, str) and obj:
        redacted = re.sub(r"(Bearer\s+)[^\s\"']+", r"\1<redacted>", obj)
        redacted = re.sub(
            r"((?:token|secret|password|api[_-]?key)=)[^&\s\"']+",
            r"\1<redacted>",
            redacted,
            flags=re.IGNORECASE,
        )
        return redacted
    return obj


def redact_output_content(format_type, output_content):
    if format_type in ["json-mcpServers", "opencode-internal", "json-settings-merge"]:
        try:
            return json.dumps(redact_secrets(json.loads(output_content)), indent=4)
        except json.JSONDecodeError:
            return output_content
    return re.sub(
        r'(^\s*[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|KEY|AUTH|CREDENTIAL)[A-Za-z_]*\s*=\s*").*?(")',
        r"\1<redacted>\2",
        output_content,
        flags=re.IGNORECASE | re.MULTILINE,
    )


def merge_configs_to_file(fmt, mcp_path, output):
    if not os.path.exists(mcp_path):
        with open(mcp_path, "w", encoding="utf-8") as f:
            f.write(output)
        logger.info(f"Created file with configuration: {mcp_path}")
        return

    if fmt == "json-settings-merge":
        new_data = json.loads(output)
        with open(mcp_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing.update(new_data)
        with open(mcp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=4)
            f.write("\n")
        logger.info(f"Merged config into: {mcp_path}")

    elif fmt == "opencode-internal":
        new_data = json.loads(output)
        with open(mcp_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if "mcp" in new_data:
            if "mcp" not in existing:
                existing["mcp"] = {}
            existing["mcp"].update(new_data["mcp"])
        with open(mcp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=4)
            f.write("\n")
        logger.info(f"Merged mcp configs into: {mcp_path}")

    elif fmt == "toml-mcpServers":
        with open(mcp_path, "r", encoding="utf-8") as f:
            content = f.read()

        content = re.sub(
            r"\n*\[mcp_servers\.[^\]]+\].*?(?=\n\[|$)", "", content, flags=re.DOTALL
        )
        with open(mcp_path, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n\n" + output.strip() + "\n")
        logger.info(f"Updated TOML mcp servers in: {mcp_path}")
    else:
        with open(mcp_path, "w", encoding="utf-8") as f:
            f.write(output)
        logger.info(f"Overwrote file with configuration: {mcp_path}")


def cmd_get_tool_config(args):
    cfg = get_tool_config(args.registry_file, args.tool)
    print(json.dumps(cfg))


def cmd_get_info(args):
    cfg = get_tool_config(args.registry_file, args.tool)
    home = os.path.expanduser("~")
    mcp_path = cfg.get("mcp_path", "").replace("~", home)
    project_mcp_path = cfg.get("project_mcp_path", cfg.get("mcp_path", ""))

    resolved_path = mcp_path
    if args.mode == "project" and args.project_dir:
        resolved_path = os.path.join(args.project_dir, project_mcp_path)

    info = {
        "mcp_path": mcp_path,
        "format": cfg.get("format", ""),
        "project_mcp_path": project_mcp_path,
        "resolved_mcp_path": resolved_path,
    }
    print(json.dumps(info))


def cmd_get_templates(args):
    if args.mode == "project":
        if args.project_mcps:
            print(" ".join(args.project_mcps.split(",")))
            return
        if not os.path.exists(args.registry_file):
            logger.critical(f"Registry file not found: {args.registry_file}")
            sys.exit(1)
        with open(args.registry_file, "r", encoding="utf-8") as f:
            reg = json.load(f)
        print(" ".join(reg.get("project_mcp_templates", [])))
    else:
        cfg = get_tool_config(args.registry_file, args.tool)
        servers = cfg.get("mcp_servers", [])
        templates = [s["template"] for s in servers if "template" in s]
        print(" ".join(templates))


def cmd_resolve_defs(args):
    tool_config = json.loads(args.tool_config)
    results = resolve_defs_list(
        args.templates_dir, args.template_list.split(), tool_config, args.mode
    )
    print(json.dumps(results))


def cmd_format_configs(args):
    defs = json.loads(args.defs_json)
    out = format_configs_to_str(args.format, defs)
    print(out)


def cmd_merge_configs(args):
    merge_configs_to_file(args.format, args.mcp_path, args.output_content)


def orchestrate_mcp_config(args):
    dotfiles_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")
    )
    templates_dir = os.path.join(dotfiles_dir, "configs", "mcp", "templates")
    registry_file = os.path.join(dotfiles_dir, "configs", "mcp", "global-mcps.json")

    if not os.path.exists(registry_file):
        logger.critical(f"Registry not found at {registry_file}")
        sys.exit(1)

    project_dir = args.project_dir if args.project_dir else os.getcwd()

    env_files = resolve_env_files(args.env_file, args.mode, project_dir)
    for f in env_files:
        load_env(f)

    tool_config = get_tool_config(registry_file, args.tool)

    home = os.path.expanduser("~")
    mcp_path = tool_config.get("mcp_path", "").replace("~", home)
    project_mcp_path = tool_config.get(
        "project_mcp_path", tool_config.get("mcp_path", "")
    )

    resolved_mcp_path = mcp_path
    if args.mode == "project" and project_dir:
        resolved_mcp_path = os.path.join(project_dir, project_mcp_path)

    format_type = tool_config.get("format", "")

    if args.mode == "project":
        if args.project_mcps:
            template_list = args.project_mcps.split(",")
        else:
            with open(registry_file, "r", encoding="utf-8") as f:
                reg = json.load(f)
            template_list = reg.get("project_mcp_templates", [])
    else:
        servers = tool_config.get("mcp_servers", [])
        template_list = [s["template"] for s in servers if "template" in s]

    if args.include or args.exclude:
        template_list = filter_mcp_templates(template_list, args.include, args.exclude)
        if not template_list:
            logger.warning(
                "No MCP templates remaining after --include/--exclude filtering"
            )
            return

    mcp_defs = resolve_defs_list(templates_dir, template_list, tool_config, args.mode)

    if not mcp_defs:
        logger.warning(
            f"No MCP definitions to generate for {args.tool} ({args.mode} mode)"
        )
        return

    output_content = format_configs_to_str(format_type, mcp_defs)

    if args.dry_run:
        print(f"# --- {args.tool} ({args.mode}) → {resolved_mcp_path} ---")
        if args.show_secrets:
            print(output_content)
        else:
            print("# Secrets redacted; pass --show-secrets to print resolved values.")
            print(redact_output_content(format_type, output_content))
        return

    parent_dir = os.path.dirname(resolved_mcp_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    if args.backup and os.path.exists(resolved_mcp_path):
        from file_utils import backup_file

        backup_file(resolved_mcp_path, enabled=True)

    merge_configs_to_file(format_type, resolved_mcp_path, output_content)

    logger.info(f"{args.tool} MCP config written to {resolved_mcp_path}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in [
        "get-tool-config",
        "get-info",
        "get-templates",
        "resolve-defs",
        "format-configs",
        "merge-configs",
    ]:
        parser = argparse.ArgumentParser(
            description="Configure MCP Tool CLI Helper Subcommands"
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        p_tc = subparsers.add_parser("get-tool-config")
        p_tc.add_argument("--registry-file", required=True)
        p_tc.add_argument("--tool", required=True)

        p_inf = subparsers.add_parser("get-info")
        p_inf.add_argument("--registry-file", required=True)
        p_inf.add_argument("--tool", required=True)
        p_inf.add_argument("--mode", default="global")
        p_inf.add_argument("--project-dir", default="")

        p_tpl = subparsers.add_parser("get-templates")
        p_tpl.add_argument("--registry-file", required=True)
        p_tpl.add_argument("--tool", required=True)
        p_tpl.add_argument("--mode", default="global")
        p_tpl.add_argument("--project-mcps", default="")

        p_res = subparsers.add_parser("resolve-defs")
        p_res.add_argument("--templates-dir", required=True)
        p_res.add_argument("--template-list", required=True)
        p_res.add_argument("--tool-config", required=True)
        p_res.add_argument("--mode", default="global")

        p_fmt = subparsers.add_parser("format-configs")
        p_fmt.add_argument("--format", required=True)
        p_fmt.add_argument("--defs-json", required=True)

        p_mrg = subparsers.add_parser("merge-configs")
        p_mrg.add_argument("--format", required=True)
        p_mrg.add_argument("--mcp-path", required=True)
        p_mrg.add_argument("--output-content", required=True)

        args = parser.parse_args()

        if args.command == "get-tool-config":
            cmd_get_tool_config(args)
        elif args.command == "get-info":
            cmd_get_info(args)
        elif args.command == "get-templates":
            cmd_get_templates(args)
        elif args.command == "resolve-defs":
            cmd_resolve_defs(args)
        elif args.command == "format-configs":
            cmd_format_configs(args)
        elif args.command == "merge-configs":
            cmd_merge_configs(args)
    else:
        parser = argparse.ArgumentParser(
            description="Generate MCP configuration for a single AI tool from centralized templates."
        )
        parser.add_argument(
            "--mode",
            default="global",
            choices=["global", "project"],
            help="Config mode",
        )
        parser.add_argument(
            "--project-dir", default="", help="Project directory for project-mode"
        )
        parser.add_argument(
            "--project-mcps",
            default="",
            help="Comma-separated list of project MCP template names",
        )
        parser.add_argument(
            "--include",
            default="",
            help="Only include MCP templates matching these comma-separated globs",
        )
        parser.add_argument(
            "--exclude",
            default="",
            help="Exclude MCP templates matching these comma-separated globs",
        )
        parser.add_argument(
            "--env-file", default="", help="Path to .env file with secrets"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print generated config to stdout instead of writing",
        )
        parser.add_argument(
            "--show-secrets",
            action="store_true",
            help="With --dry-run, print resolved secret values instead of redacting them",
        )
        parser.add_argument(
            "--backup",
            action="store_true",
            default=True,
            help="Create .bak of existing config",
        )
        parser.add_argument(
            "--no-backup", dest="backup", action="store_false", help="Skip backup"
        )
        parser.add_argument(
            "tool",
            help="AI tool to configure: ai, air, cursor, codex, opencode, gemini, junie",
        )
        args = parser.parse_args()

        orchestrate_mcp_config(args)


if __name__ == "__main__":
    main()
