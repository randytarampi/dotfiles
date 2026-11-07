# Paseo password, Caddy and concurrency acceptance

## Password source of truth

Paseo supports a `PASEO_PASSWORD` environment variable for foreground daemon startup, but current upstream managed/Desktop daemon startup does not necessarily inherit caller environment overrides. In particular, placing `PASEO_PASSWORD` in `~/.env` is **not** proof that the managed daemon uses it. The native `paseo daemon set-password` is interactive; do not pipe a password to its stdin, put it on the command line, log it, write it into a public launchd plist, or rotate persisted bcrypt on every deploy.

The operator must establish the actual daemon process owner, supported secret delivery and readback privately. This repository makes no automatic password mutations. If secure managed-daemon injection is unavailable, continue using Paseo's native interactive setup, without exposing a listener.

## Read-only security check

`python3 scripts/check-paseo-security.py --access localhost --json` first loads the canonical `~/.env` (refusing when unavailable), then reads only an existing private native Paseo configuration and checks the Caddy loopback listener, declared/native relay posture, enabled Web UI, required localhost host, and owner-selected exposure mode. The checker uses the same configured domain calculation as Caddy, including `paseo.local.*` aliases in localhost mode. LAN/public **and any declared hosted relay** require stored bcrypt. Merged #187 establishes `CADDY_ACCESS` as the Caddy exposure selector and retires `DOTFILES_PASEO_PUBLIC`; there is no second public opt-in gate. Merged #189 originally forbade any `DOTFILES_PASEO_RELAY=1` intent on a Caddy route; the revised contract (owner decision, November 2026) allows a **declared** hosted-relay posture to coexist with the Caddy Web UI route: the audit reports `relay: "enabled (declared)"` instead of refusing, and still fails closed on invalid relay values, an undeclared enabled native relay, loopback or `PASEO_LISTEN`/`PASEO_RELAY_ENABLED` conflicts, and missing bcrypt for LAN/public or declared hosted relay (including localhost Caddy mode). Hosted-relay reachability is owner-accepted, not verified here; the audit reports **daemon runtime, Caddy TLS and WebSockets as UNKNOWN**, because configuration is not a network acceptance test.

Owner-only acceptance must prove correct and incorrect password, missing allowlist host, alternate Host header, TLS/DNS, WebSocket upgrade, Caddy reconfiguration, and private tailnet vs public reachability using the actual installed service. Keep direct tailnet binding separate from loopback Caddy topology and do not expand the attack surface to LiteLLM, oMLX, Pi, Xcode MCP or arbitrary agent tools.

## Pre-apply settings preview

`configure-paseo-web.py --dry-run` currently reports the *number* of proposed changes, not their keys and values. Before native `--apply`, in the exact same owner-controlled environment as the eventual apply, inspect the **read-only** proposed keys `features.webUi.enabled`, `daemon.relay.enabled` and `daemon.hostnames` with the repository's own policy functions:

```sh
python3 - <<'PY'
import importlib.util
import os
from pathlib import Path

script = Path('scripts/configure-paseo-web.py').resolve()
spec = importlib.util.spec_from_file_location('paseo_web_preview', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
home = Path(os.environ.get('PASEO_HOME', str(Path.home() / '.paseo'))).expanduser()
_, original = module.read_config(home)
access = os.environ.get('CADDY_ACCESS', 'localhost').strip().lower() or 'localhost'
proposed = module.desired_config(original, access)
for key, value in module.planned_mutations(original, proposed):
    print(f'{key}: {value!r}')
PY
```

This only reads the existing private configuration and computes the precise prospective values using the same code as apply; it does not run the native setter or print the complete configuration (which may contain credentials). If the output differs from the operator-approved policy, **do not apply**. A separate private backup and quiescence are mandatory; a preview cannot fix concurrent lost updates.

## Concurrency remains unresolved

**Owner decision (2026-11-07):** [#173](https://github.com/randytarampi/dotfiles/issues/173) was closed **not planned**, because the owner accepts manual/quiescent native configuration indefinitely instead of implementing a shared upstream lock/CAS. **The cross-process lost-update race remains real and unfixed.** Staged CLI rehearsal and a preflight readback do **not** establish cross-writer transactions. Do not enable unattended native `--apply` or claim race freedom. Before owner-invoked apply, identify and quiesce competing UI/daemon/CLI writers, back up private settings, preview, perform the native write and read back the *whole* configuration. If exclusivity cannot be demonstrated, refuse the write. Never automatically roll back a newer unrelated writer. Rehearsal, rollback and security denial acceptance are tracked by [#213](https://github.com/randytarampi/dotfiles/issues/213).

This change only adds a read-only checker and acceptance runbook. Author: ChatGPT (GPT-6); requirements/host qualification: Randy Tarampi. Live TLS/auth/session negative tests: NOT RUN here. Native cross-process race resolution: deliberately not pursued under the owner's manual-operation decision.
