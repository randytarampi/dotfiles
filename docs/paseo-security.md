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

## Owner-only native settings maintenance checklist (#213)

The owner accepted quiescent **manual** native writes indefinitely. This is an operational compromise, not a cross-process lock; the Paseo UI, daemon and CLI can still overwrite one another. A successful `check-paseo-security.py` result proves only stored configuration constraints. It **cannot** prove the live password reached the daemon, that a network endpoint is reachable, or that another native writer has stopped.

1. **Prepare, without changing anything.** Identify the actual installed Paseo version, native `PASEO_HOME`, daemon/UI process owners and active sessions. Read the current configuration through a private, redacted file inspection; never dump credentials into a PR, console transcript or chat. Record the intended specific key/value changes.
2. **Establish an exclusive maintenance window.** Stop or quiesce *every* Paseo UI, daemon and competing CLI settings writer using the installed version's supported controls, and confirm with process inspection. If other writers or their state cannot be established, **refuse to apply**. A single `flock` in the dotfiles wrapper cannot protect against native writers.
3. **Back up before applying.** Save a private, mode-0600 copy of the entire `config.json` at an operator-chosen path outside public, version-controlled or synchronised directories. Keep it until post-restart and post-use readback confirms settings. Reject symlinks, unexpected ownership, unsafe parent modes or an existing backup path rather than replacing it.
4. **Rehearse and inspect.** Run the relevant `configure-paseo-*.py --dry-run` against the explicit home, inspect the planned scope, then use the existing *owner-only* `--apply` in the quiescent maintenance window. Normal `make deploy` remains inspection-only. Do not run multiple native setting writers concurrently.
5. **Verify the whole state.** Immediately compare the entire post-apply JSON to the desired configuration and original unrelated keys, not just the key being set. Restart only through the supported native lifecycle, then check the intended configuration again. Treat reload failure, unexpectedly changed keys or unavailable daemon as **partial/UNKNOWN**; do **not** restore an old snapshot over a newer unrelated write.
6. **Test security separately.** From a disposable host/config, test right/wrong password, required hostname rejection, relay declaration and the intended Caddy access mode; check actual TLS/WebSocket and listener exposure under an owner-approved topology. Confirm Manual metadata has a non-empty Pi-only provider list and an unavailable local model yields placeholders without cloud fallback on the **installed** Paseo version. A source-level mock is not this host evidence.
7. **Recover and record.** If the live config is still exclusively controlled and its original owner-approved snapshot is current, restore with the same private/safe procedure. Otherwise stop and request owner reconciliation; never blindly roll back over another writer. Record exact version, redacted verification receipt and each PASS/FAIL/UNKNOWN in [#213](https://github.com/randytarampi/dotfiles/issues/213). No runtime authentication tests have been performed by this documentation PR.

The bounded source tests in `scripts/lib/tests/test_paseo_operator_contract.py` pin read-only audit uncertainty, refusal of public native listeners and plaintext password, preservation of user-authored metadata provider lists, explicit Pi-only selection and inspection-only deploy wiring. They do not claim to prove cross-process quiescence, daemon key propagation or the network protocol. #173 remains closed *not planned* under the owner's accepted operator procedure.
