# Paseo password, Caddy and concurrency acceptance

## Password source of truth

Paseo supports a `PASEO_PASSWORD` environment variable for foreground
daemon startup, but current upstream managed/Desktop daemon startup does not
necessarily inherit caller environment overrides. In particular, placing
`PASEO_PASSWORD` in `~/.env` is **not** proof that the managed daemon uses
it. The native `paseo daemon set-password` is interactive; do not pipe a
password to its stdin, put it on the command line, log it, write it into a
public launchd plist, or rotate persisted bcrypt on every deploy.

The operator must establish the actual daemon process owner, supported
secret delivery and readback privately. This repository makes no automatic
password mutations. If secure managed-daemon injection is unavailable,
continue using Paseo's native interactive setup, without exposing a listener.

## Read-only security check

`python3 scripts/check-paseo-security.py --access localhost --json` first loads
the canonical `~/.env` (refusing when unavailable), then reads only an existing
private native Paseo configuration and checks the Caddy
loopback listener, declared/native relay posture, enabled Web UI, required
localhost host, and owner-selected exposure mode. The checker uses the same configured domain
calculation as Caddy, including `paseo.local.*` aliases in localhost mode.
LAN/public **and any declared hosted relay** require stored bcrypt. Merged #187 establishes `CADDY_ACCESS`
as the Caddy exposure selector and retires `DOTFILES_PASEO_PUBLIC`; there
is no second public opt-in gate. Merged #189 originally forbade any
`DOTFILES_PASEO_RELAY=1` intent on a Caddy route; the revised contract
(owner decision, November 2026) allows a **declared** hosted-relay posture
to coexist with the Caddy Web UI route: the audit reports
`relay: "enabled (declared)"` instead of refusing, and still fails closed
on invalid relay values, an undeclared enabled native relay, loopback or
`PASEO_LISTEN`/`PASEO_RELAY_ENABLED` conflicts, and missing bcrypt for
LAN/public or declared hosted relay (including localhost Caddy mode). Hosted-relay reachability is owner-accepted, not verified
here; the audit reports **daemon runtime, Caddy TLS and WebSockets as
UNKNOWN**, because configuration is not a network acceptance test.

Owner-only acceptance must prove correct and incorrect password, missing
allowlist host, alternate Host header, TLS/DNS, WebSocket upgrade, Caddy
reconfiguration, and private tailnet vs public reachability using the
actual installed service. Keep direct tailnet binding separate from
loopback Caddy topology and do not expand the attack surface to LiteLLM,
oMLX, Pi, Xcode MCP or arbitrary agent tools.

## Concurrency remains unresolved

Issue #173 tracks lost disjoint-key writes from native CLI/UI/daemon
configuration writers. Staged CLI rehearsal and a preflight readback do
**not** establish shared cross-process locking. To close #173, require an
upstream lock/CAS that serializes all native writers, with two-writer
disjoint-key preservation, stale revision refusal, crash/interrupt and
repeat-write tests. Until then, every native Paseo config apply is
owner-invoked while all other writers are quiescent; never automatically
roll back a newer config snapshot or mark #173 complete.

This change only adds a read-only checker and acceptance runbook.
Author: ChatGPT (GPT-6); requirements/host qualification: Randy Tarampi.
Live TLS/auth/session tests and cross-process race resolution: NOT RUN.
