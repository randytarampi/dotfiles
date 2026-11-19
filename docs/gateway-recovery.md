# Recovering the local AI stack from a stock terminal

**Rehearsal status: NOT RUN.** This is a procedure derived from the code paths,
not from an exercised recovery. Do not treat it as verified until the rehearsal
in the last section has been performed and its receipt recorded.

## The failure this is for

Every client that routes through LiteLLM depends on one process. The plausible
sequence that takes them all down together:

1. `make deploy` applies chezmoi state **before** runtime reconciliation.
2. On gateway mode, `configure-litellm-venv.py` patches an exact installed
   source layout. An upstream release that changes that layout breaks the patch.
3. The gateway fails readiness, so `configure-all.sh` halts **before** the
   gateway-only client rewrite.
4. Clients already pointed at the gateway have nothing to talk to.

Clean L0 CI cannot catch this: it has no credentials and no providers, so the
gateway path never runs there. A green `ci/required` is compatible with a broken
runtime.

The secondary risk is that you reach for an AI assistant to diagnose it, and the
assistant is behind the same broken gateway.

## Preconditions to have before you need this

- A private, access-controlled, **verified restore set** for the last-known-good `~/.local/share/litellm` (configuration, `service.env`, rates **and the qualified virtual environment or its exact offline install artifacts**). A configuration-only backup cannot undo an incompatible Python package upgrade. Do not place this backup in Git or public CI artifacts.
- The last-known-good LiteLLM and dependency versions, including Prisma and Pillow, plus a local copy of the install artifacts or a tested restore procedure. A version string alone cannot recover a machine without network access.
- Working `git`, a shell, and your normal user account. No agents required.

## Procedure

Work from a plain terminal. Do not run `make deploy` until one client works
again — it applies the whole tree and can deepen the hole.

1. **Establish scope without the gateway.** Which clients are down? Do local
   engines still answer directly?
   ```sh
   curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:11434/api/tags   # Ollama
   curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/v1/models    # oMLX
   ```
   A reachable local engine means you can still work while the gateway is down.

2. **Confirm the gateway is the fault, not the network.**
   ```sh
   make litellm-diagnose
   launchctl print gui/$(id -u)/com.litellm.proxy >/dev/null && echo loaded
   ```
   `GATE-OFF` means the gate is off, not that it broke.

3. **Restore the executable runtime as well as its configuration.** First inspect the installed version and the configured service gate without changing them. If the outage followed a LiteLLM upgrade or failed patch, restoring only `service.env`, `config.yaml` and rates **will not restore** the prior Python package or repair its in-venv patch. On an isolated recovery host, use the verified private backup of the *previously working* virtual environment, or rebuild a replacement virtual environment from the qualified, locally cached wheels and recorded dependencies, then atomically activate that restored runtime. Do not overwrite an in-use virtual environment in place or guess an older version from the current source default. Confirm ownership and file modes and retain the failed runtime for diagnosis; stop if neither a known-good runtime nor reproducible wheels exist.

   Restore the backed-up configuration and service environment together with that runtime, then restart **only** LiteLLM:
   ```sh
   make litellm-restart
   ```
   The `DOTFILES_RUN_LITELLM_SETUP` gate must be enabled and the expected service unit present. This Make target is a service restart, **not** a package downgrade, virtual-environment repair or roll-forward deployment. Restarting all services restarts the thing you are trying to fix, plus everything else.

4. **Wait, then probe.** The proxy needs roughly 20–40 seconds before it listens.
   An immediate `curl` returning `000` in ~0.001 s is "not yet listening", not a
   failure. Re-probe `/health/liveliness` before concluding anything.

5. **Verify one client, not all.** Pick the one you need. A single successful
   authenticated `/v1/models` call with that client's key is the minimum proof;
   a real task is better.

6. **Only then** consider a wider deploy, and re-run `make litellm-diagnose`
   afterwards.

## What "recovered" means

The previously working route works again. It does **not** mean every client is
healthy, that the failed upgrade is understood, or that the underlying
version-drift risk is gone.

## Rehearsal (NOT RUN)

To convert this from a plausible procedure into evidence, on a **disposable VM
or isolated host** — never the primary gateway:

1. Capture baseline: config file list, pinned version, key file **names only**.
2. Simulate the fault: corrupt the in-venv patch target or point the gateway at
   an incompatible layout.
3. Perform the procedure above using the private backup, local package artifacts and Git; prove that the previously working *runtime* was restored, not just its configuration.
4. Record: elapsed operator time, every manual step, and what was preserved.
5. Success criterion: a working authenticated model listing **and one previously working client request** within a *provisional* **15-minute target**; measure and revise the target from the rehearsal. No AI agent, bot credential or full redeploy. Record recovery failures honestly.

Record the result as a receipt per
[bootstrap-acceptance-receipts.md](bootstrap-acceptance-receipts.md) and link it
from the roadmap. Until then this document is a hypothesis with steps attached.
