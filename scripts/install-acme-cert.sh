#!/usr/bin/env bash
set -euo pipefail

_SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck disable=SC1091
source "$LIB_DIR/common.sh"
source "$LIB_DIR/common_args.sh"
export COMMON_USAGE="$0"
export COMMON_HELP_TEXT="Issue and install configured ACME certificates."
parse_common_args "$@"
set -- ${COMMON_ARGS_REMAINING[@]+"${COMMON_ARGS_REMAINING[@]}"}
# shellcheck disable=SC1091
source "$LIB_DIR/env.sh"

load_env || warn "$HOME/.env not found, skipping env load"

if [[ "${DOTFILES_RUN_ACME_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}" != "1" ]]; then
  info "DOTFILES_RUN_ACME_SETUP='${DOTFILES_RUN_ACME_SETUP:-${DOTFILES_RUN_CADDY_SETUP:-0}}' — skipping acme.sh setup"
  exit 0
fi

DOMAINS="$(
  python3 -c "
import os
import sys

sys.path.insert(0, os.path.join('$SCRIPT_DIR', 'lib'))
from caddy_domains import load_domains
from pathlib import Path

zones_path = Path(os.path.expanduser('~/.config/caddy/ddns-zones.json'))
print(' '.join(load_domains(zones_path)))
"
)"

if [[ -z "$DOMAINS" ]]; then
  info "No domains configured — skipping acme.sh cert issue"
  exit 0
fi

if ! command -v brew &>/dev/null; then
  die "Homebrew not found; install Homebrew first"
fi

ACME_EMAIL="${ACME_EMAIL:?ACME_EMAIL is required}"
AWS_ACCESS_KEY_ID="${ROUTE53_AWS_ACCESS_KEY_ID:-}"
AWS_SECRET_ACCESS_KEY="${ROUTE53_AWS_SECRET_ACCESS_KEY:-}"

if [[ -z "$AWS_ACCESS_KEY_ID" || -z "$AWS_SECRET_ACCESS_KEY" ]]; then
  die "ROUTE53_AWS_ACCESS_KEY_ID and ROUTE53_AWS_SECRET_ACCESS_KEY are required"
fi

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

BREW_PREFIX="$(brew --prefix)"
CADDY_CERT_DIR="${BREW_PREFIX}/etc/caddy/certs"
CADDYFILE_PATH="${BREW_PREFIX}/etc/caddy/Caddyfile"
ACME_HOME="$HOME/.acme.sh"
ACME_BIN="${ACME_HOME}/acme.sh"

if [[ ! -d "$ACME_HOME" ]]; then
  info "Installing acme.sh..."
  curl https://get.acme.sh | sh -s email="${ACME_EMAIL}" || die "acme.sh install failed"
else
  ok "acme.sh already installed at ${ACME_HOME}"
fi

# Use Let's Encrypt as default CA (ZeroSSL has issues with wildcard issuance)
"$ACME_BIN" --set-default-ca --server letsencrypt 2>/dev/null || warn "Failed to set Let's Encrypt as default CA"

if [[ ! -x "$ACME_BIN" ]]; then
  die "acme.sh binary not found at ${ACME_BIN}"
fi

read -r -a DOMAIN_ARRAY <<<"$DOMAINS"
PRIMARY_DOMAIN="${DOMAIN_ARRAY[0]}"

CERT_FILE="${BREW_PREFIX}/etc/caddy/certs/fullchain.pem"
ISSUE_ARGS=(--issue --dns dns_aws)
for domain in "${DOMAIN_ARRAY[@]}"; do
  ISSUE_ARGS+=(-d "$domain" -d "*.${domain}")
done

if [[ -f "$CERT_FILE" ]]; then
  EXISTING_SANS="$({
    openssl x509 -in "$CERT_FILE" -noout -ext subjectAltName |
      grep -o 'DNS:[^,]*' |
      sed 's/DNS://g' |
      sort -u
  } 2>/dev/null || true)"
  EXPECTED_SANS="$(
    for domain in "${DOMAIN_ARRAY[@]}"; do
      printf '%s\n' "$domain" "*.${domain}"
    done | sort -u
  )"
  if [[ "$EXISTING_SANS" == "$EXPECTED_SANS" ]]; then
    info "Cert SANs unchanged — skipping --force (avoids rate limits)"
  else
    info "Cert SANs changed — using --force to re-issue"
    ISSUE_ARGS+=(--force)
  fi
else
  info "No existing cert — issuing fresh"
fi

# Build domain list: each domain + its wildcard (DNS-01 supports wildcards natively).
# One cert covers {domain} and *.{domain} (e.g. opencode.{domain}).
info "Issuing certificate for ${DOMAINS} (with wildcards)..."

# acme.sh returns non-zero when cert already exists and isn't due for renewal — treat as success.
# Only add --force when the current cert SANs differ from the requested domain list.
"$ACME_BIN" "${ISSUE_ARGS[@]}" || {
  exit_code=$?
  if [[ "$exit_code" -eq 2 ]]; then
    ok "Certificate already issued and not due for renewal — skipping issue."
  else
    die "acme.sh issue failed (exit code ${exit_code})"
  fi
}

info "Installing certificate files..."
mkdir -p "$CADDY_CERT_DIR"
# reloadcmd must tolerate Caddy not being installed/running yet (script 23 runs before script 24).
# On first run the Caddyfile won't exist; on renewals it will and Caddy should be running.
RELOAD_CMD="rm -f '${HOME}/Library/Application Support/Caddy/autosave.json' 2>/dev/null || true; if [ -f '${CADDYFILE_PATH}' ] && command -v caddy >/dev/null 2>&1; then sudo caddy reload --force --config '${CADDYFILE_PATH}' || echo 'caddy reload failed (will succeed once Caddy is running)'; else echo 'Caddy not yet installed — reload skipped'; fi"
"$ACME_BIN" --install-cert -d "${PRIMARY_DOMAIN}" \
  --fullchain-file "${CADDY_CERT_DIR}/fullchain.pem" \
  --key-file "${CADDY_CERT_DIR}/key.pem" \
  --reloadcmd "$RELOAD_CMD" || die "acme.sh install-cert failed"

info "Installing acme.sh cronjob..."
"$ACME_BIN" --install-cronjob || warn "acme.sh cronjob installation failed"

ok "acme.sh certificate setup complete.

Domains: ${DOMAINS}
Primary domain: ${PRIMARY_DOMAIN}
Cert dir: ${CADDY_CERT_DIR}
Caddyfile: ${CADDYFILE_PATH} (reload is deferred to script 24 on first run)"
