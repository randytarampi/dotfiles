#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Configure Caddy: generate a multi-domain Caddyfile from env + template."""

from __future__ import annotations

import argparse
import os
import string
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.realpath(__file__)))
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger
from env import load_env
from file_utils import write_text_file
from caddy_domains import load_domains
from cli_helpers import add_common_args

DEFAULT_ZONES_CONFIG = "~/.config/caddy/ddns-zones.json"
DEFAULT_AUTH_CONF = "~/.config/caddy/caddy-auth.conf"
ALLOWED_ACCESS = {"localhost", "lan", "public"}


def get_brew_prefix() -> str:
    import platform

    if platform.system() != "Darwin":
        # Linux: Caddy installed via package manager, config in /etc/caddy
        return "/usr"
    try:
        result = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, check=True
        )
    except Exception as exc:
        logger.critical(f"Failed to resolve Homebrew prefix: {exc}")
        raise SystemExit(1)

    prefix = result.stdout.strip()
    if not prefix:
        logger.critical("Homebrew prefix is empty")
        raise SystemExit(1)
    return prefix


def get_bind_ip() -> str:
    import platform

    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["ipconfig", "getifaddr", "en0"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        else:
            # Linux: hostname -I returns space-separated IPs
            result = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ip = result.stdout.strip().split()[0] if result.stdout.strip() else ""
            return ip or "0.0.0.0"
        ip = result.stdout.strip()
        return ip or "0.0.0.0"
    except Exception:
        return "0.0.0.0"


def resolve_tls_for_local_domains(brew_prefix: str) -> str:
    """Use real acme.sh cert if available, else tls internal."""
    cert_fullchain = str(
        Path(brew_prefix) / "etc" / "caddy" / "certs" / "fullchain.pem"
    )
    cert_key = str(Path(brew_prefix) / "etc" / "caddy" / "certs" / "key.pem")
    if Path(cert_fullchain).exists() and Path(cert_key).exists():
        return f"tls {cert_fullchain} {cert_key}"
    return "tls internal"


def expand_path(path_value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path_value)))


def parse_auth_conf(auth_path: Path) -> list[tuple[str, str]]:
    users: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    try:
        lines = auth_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.critical(f"Failed to read auth config {auth_path}: {exc}")
        raise SystemExit(1)

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            logger.warning(f"Skipping invalid auth line in {auth_path}")
            continue

        user, password_hash = line.split(":", 1)
        user = user.strip()
        password_hash = password_hash.strip()
        if not user or not password_hash:
            logger.warning(f"Skipping incomplete auth line in {auth_path}")
            continue

        entry = (user, password_hash)
        if entry in seen:
            continue
        seen.add(entry)
        users.append(entry)

    return users


def resolve_auth() -> tuple[list[tuple[str, str]], str, Path | None]:
    auth_conf_path = expand_path(DEFAULT_AUTH_CONF)
    if not auth_conf_path.exists():
        logger.critical(
            "caddy-auth.conf is required at ~/.config/caddy/caddy-auth.conf. Copy from configs/caddy/caddy-auth.example.conf and add users with `caddy hash-password`."
        )
        raise SystemExit(1)

    users = parse_auth_conf(auth_conf_path)
    if users:
        return users, "file", auth_conf_path

    logger.critical(
        "No valid user:hash entries found in caddy-auth.conf. Add entries with `caddy hash-password`."
    )
    raise SystemExit(1)


def build_auth_block(users: list[tuple[str, str]]) -> str:
    lines = ["  basic_auth {"]
    for user, password_hash in users:
        lines.append(f"    {user} {password_hash}")
    lines.append("  }")
    return "\n".join(lines)


def build_route_block(plannotator_portal_dir: str) -> str:
    return "\n".join(
        [
            "  # Ollama inference — READ-ONLY (write endpoints blocked per oracle warning 1)",
            "  handle_path /ollama/* {",
            "    @blocked path /api/pull /api/delete /api/create /api/push /api/copy",
            "    respond @blocked 403",
            "",
            "    @ollama_read path /api/generate /api/chat /api/tags /api/show /api/version /v1/*",
            "    reverse_proxy @ollama_read 127.0.0.1:11434 {",
            "      flush_interval -1",
            "    }",
            "",
            "    respond 404",
            "  }",
            "",
            "  # Meridian /v1 (OpenAI-compatible inference)",
            "  handle_path /meridian/v1/* {",
            "    reverse_proxy 127.0.0.1:3456 {",
            "      flush_interval -1",
            "    }",
            "  }",
            "",
            "  # Plannotator paste service (encrypted payloads)",
            "  handle_path /plannotator/* {",
            "    reverse_proxy 127.0.0.1:19433 {",
            "      flush_interval -1",
            "    }",
            "  }",
            "",
            "  # Plannotator portal (static SPA)",
            "  handle {",
            f"    root * {plannotator_portal_dir}",
            "    try_files {path} /index.html",
            "    file_server",
            "  }",
        ]
    )


def build_site_block(
    site_label: str,
    bind_ip: str,
    tls_line: str,
    auth_block: str,
    route_block: str,
    opencode_redirect: str | None = None,
    lan_only: bool = False,
) -> str:
    lines = [f"{site_label} {{", f"  bind {bind_ip}", f"  {tls_line}", ""]
    if opencode_redirect:
        lines.extend(
            [
                "  handle /opencode {",
                f"    redir {opencode_redirect} 308",
                "  }",
                "",
            ]
        )
    if lan_only:
        lines.extend(
            ["  @not_lan not remote_ip private_ranges", "  abort @not_lan", ""]
        )
    lines.extend([auth_block, "", route_block, "}"])
    return "\n".join(lines)


def build_site_label(hostname: str, https_port: str) -> str:
    if https_port == "443":
        return f"https://{hostname}"
    return f"https://{hostname}:{https_port}"


def build_opencode_site_block(
    site_label: str,
    bind_ip: str,
    tls_line: str,
    auth_block: str,
    opencode_port: str,
    lan_only: bool = False,
    include_forward_headers: bool = False,
) -> str:
    lines = [f"{site_label} {{", f"  bind {bind_ip}", f"  {tls_line}", ""]
    if lan_only:
        lines.extend(
            ["  @not_lan not remote_ip private_ranges", "  abort @not_lan", ""]
        )
    if auth_block:
        lines.extend([auth_block, ""])
    lines.extend(
        [
            f"  reverse_proxy 127.0.0.1:{opencode_port} {{",
            "    flush_interval -1",
        ]
    )
    if include_forward_headers:
        lines.extend(
            [
                "    header_up X-Forwarded-Proto {scheme}",
                "    header_up X-Forwarded-Host {host}",
            ]
        )
    lines.extend(["  }", "}"])
    return "\n".join(lines)


def build_opencode_site_blocks(
    domains: list[str],
    access_mode: str,
    auth_block: str,
    bind_ip: str,
    cert_fullchain: str,
    cert_key: str,
    local_domain_tls_line: str,
    opencode_port: str,
    https_port: str,
) -> list[str]:
    blocks: list[str] = []
    if access_mode == "localhost":
        blocks.append(
            build_opencode_site_block(
                build_site_label("opencode.localhost", https_port),
                "127.0.0.1",
                "tls internal",
                "",
                opencode_port,
            )
        )
        local_domains = [domain for domain in domains if domain.startswith("local.")]
        for local_domain in local_domains:
            blocks.append(
                build_opencode_site_block(
                    build_site_label(f"opencode.{local_domain}", https_port),
                    "127.0.0.1",
                    local_domain_tls_line,
                    "",
                    opencode_port,
                )
            )
        return blocks

    for domain in domains:
        blocks.append(
            build_opencode_site_block(
                build_site_label(f"opencode.{domain}", https_port),
                bind_ip,
                f"tls {cert_fullchain} {cert_key}",
                auth_block,
                opencode_port,
                lan_only=access_mode == "lan",
                include_forward_headers=True,
            )
        )
    return blocks


def build_caddyfile(
    domains: list[str],
    access_mode: str,
    auth_block: str,
    bind_ip: str,
    cert_fullchain: str,
    cert_key: str,
    local_domain_tls_line: str,
    opencode_port: str,
    plannotator_portal_dir: str,
    https_port: str,
) -> str:
    route_block = build_route_block(plannotator_portal_dir)

    domain_blocks: list[str] = []
    if access_mode in {"lan", "public"}:
        for domain in domains:
            domain_blocks.append(
                build_site_block(
                    build_site_label(domain, https_port),
                    bind_ip,
                    f"tls {cert_fullchain} {cert_key}",
                    auth_block,
                    route_block,
                    opencode_redirect=f"{build_site_label(f'opencode.{domain}', https_port)}/",
                    lan_only=access_mode == "lan",
                )
            )

    localhost_blocks = [
        # Localhost is bound to 127.0.0.1 — only local processes can reach it.
        # Skip basic_auth on localhost (no security value, just friction).
        build_site_block(
            build_site_label("localhost", https_port),
            "127.0.0.1",
            "tls internal",
            "",
            route_block,
            opencode_redirect=f"{build_site_label('opencode.localhost', https_port)}/",
        )
    ]

    if access_mode == "localhost":
        local_domains = [domain for domain in domains if domain.startswith("local.")]
        for local_domain in local_domains:
            localhost_blocks.append(
                build_site_block(
                    build_site_label(local_domain, https_port),
                    "127.0.0.1",
                    local_domain_tls_line,
                    "",
                    route_block,
                    opencode_redirect=f"{build_site_label(f'opencode.{local_domain}', https_port)}/",
                )
            )

    opencode_sites = build_opencode_site_blocks(
        domains=domains,
        access_mode=access_mode,
        auth_block=auth_block,
        bind_ip=bind_ip,
        cert_fullchain=cert_fullchain,
        cert_key=cert_key,
        local_domain_tls_line=local_domain_tls_line,
        opencode_port=opencode_port,
        https_port=https_port,
    )

    template_path = (
        Path(__file__).resolve().parent.parent / "configs" / "caddy" / "Caddyfile.tmpl"
    )
    if not template_path.exists():
        logger.critical(f"Missing Caddy template: {template_path}")
        raise SystemExit(1)

    template_text = template_path.read_text(encoding="utf-8")
    return string.Template(template_text).substitute(
        domain_sites="\n\n".join(domain_blocks),
        opencode_sites="\n\n".join(opencode_sites),
        localhost_block="\n\n".join(localhost_blocks),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Caddyfile.")
    add_common_args(parser)
    args = parser.parse_args()

    if not load_env():
        logger.warning("~/.env not found")

    if os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0") != "1":
        logger.info(
            f"DOTFILES_RUN_CADDY_SETUP='{os.environ.get('DOTFILES_RUN_CADDY_SETUP', '0')}' — skipping Caddy configuration"
        )
        return

    access_mode = (
        os.environ.get("CADDY_ACCESS", "localhost").strip().lower() or "localhost"
    )
    if access_mode not in ALLOWED_ACCESS:
        logger.critical(
            f"Invalid CADDY_ACCESS='{access_mode}' — expected one of: {', '.join(sorted(ALLOWED_ACCESS))}"
        )
        raise SystemExit(1)

    zones_config_value = os.environ.get(
        "CADDY_ZONES_CONFIG", DEFAULT_ZONES_CONFIG
    ).strip()
    zones_path = expand_path(zones_config_value or DEFAULT_ZONES_CONFIG)
    domains = load_domains(zones_path)
    if access_mode in {"lan", "public"} and not domains:
        logger.critical(
            f"No domains found in {zones_path} — CADDY_ACCESS={access_mode} requires at least one domain"
        )
        raise SystemExit(1)

    auth_users, auth_source, auth_conf_path = resolve_auth()
    auth_block = build_auth_block(auth_users)

    brew_prefix = get_brew_prefix()
    output_path = Path(brew_prefix) / "etc" / "caddy" / "Caddyfile"

    bind_ip = os.environ.get("CADDY_BIND_IP", "").strip() or get_bind_ip()
    opencode_port = os.environ.get("OPENCODE_SERVER_PORT", "4096").strip() or "4096"
    https_port = os.environ.get("CADDY_HTTPS_PORT", "443").strip() or "443"
    plannotator_portal_dir = str(
        expand_path(
            os.environ.get("PLANNOTATOR_PORTAL_DIR", "~/.plannotator/portal")
            or "~/.plannotator/portal"
        )
    )
    cert_fullchain = str(
        Path(brew_prefix) / "etc" / "caddy" / "certs" / "fullchain.pem"
    )
    cert_key = str(Path(brew_prefix) / "etc" / "caddy" / "certs" / "key.pem")
    local_domain_tls_line = resolve_tls_for_local_domains(brew_prefix)

    try:
        caddyfile = build_caddyfile(
            domains=domains,
            access_mode=access_mode,
            auth_block=auth_block,
            bind_ip=bind_ip,
            cert_fullchain=cert_fullchain,
            cert_key=cert_key,
            local_domain_tls_line=local_domain_tls_line,
            opencode_port=opencode_port,
            plannotator_portal_dir=plannotator_portal_dir,
            https_port=https_port,
        )
    except Exception as exc:
        logger.critical(f"Failed to build Caddyfile: {exc}")
        raise SystemExit(1)

    if args.dry_run:
        logger.info(f"Would write Caddyfile to {output_path}")
    else:
        write_text_file(str(output_path), caddyfile, backup=not args.no_backup)

    auth_user_list = ", ".join(user for user, _ in auth_users)
    domain_summary = ", ".join(domains) if domains else "(none)"
    summary_lines = [
        "Caddy configured!",
        "",
        f"Access:    {access_mode}",
        f"Domains:   {domain_summary}",
        f"Auth:      {auth_user_list} ({auth_source})",
        f"Caddyfile: {output_path}",
        f"Bind IP:   {bind_ip}",
        f"HTTPS port: {https_port}",
        f"Zones:     {zones_path}",
        f"Auth conf: {auth_conf_path}",
        f"Portal:    {plannotator_portal_dir}",
    ]
    logger.info("\n".join(summary_lines))


if __name__ == "__main__":
    main()
