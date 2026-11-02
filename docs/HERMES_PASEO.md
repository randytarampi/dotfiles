# Hermes through Paseo ACP (no messaging gateway)

**Scope:** register Hermes as a coding-capable ACP provider in Paseo. No Telegram, WhatsApp, cron/notifications, gateway services, webhook servers or Hermes-to-Paseo command broker are installed by this change.

## Existing state and ownership

The repository's `scripts/configure-hermes.py` is a separate LiteLLM provider scaffold gated by `DOTFILES_RUN_HERMES_SETUP` and `DOTFILES_USE_LITELLM_PROXY`. It is not a Hermes install/ACP/gateway manager. It has an explicit unresolved credential-delivery warning. Do not broaden that gate or claim functional LiteLLM routing without an authenticated model request.

Hermes reads its own provider credentials from user-owned `~/.hermes` state. Paseo's `~/.paseo/config.json` is also user-owned. This integration registers only the missing `agents.providers.hermes` entry through Paseo's **native** `daemon config set` command, and refuses a divergent existing Hermes entry.

## Owner-run setup

1. Install and authenticate Hermes separately using its [official installer](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart); verify `hermes --version` and `hermes status`. The dotfiles `Brewfile.dev` may include Hermes as a package but a package being present does not prove correct credentials or ACP support.
2. Add/verify Hermes ACP extra using the current upstream documentation. On current full-extras installations it is available already; otherwise from the Hermes checkout run `python -c "import pm; pm.sync_venv(['acp'], explicit=True)"` and test `hermes acp --check`. Do **not** run the interactive browser bootstrap by default.
3. Ensure Paseo is installed and its config exists. Check with `python3 scripts/configure-paseo-hermes.py` (no write). Apply explicitly using `DOTFILES_RUN_PASEO_HERMES_ACP_SETUP=1 python3 scripts/configure-paseo-hermes.py --apply`. A successful CLI config edit asks Paseo to reload available providers.
4. Inspect the saved `agents.providers.hermes.command`: the configurator uses the locally resolved **absolute Hermes executable path** rather than relying on a potentially different `PATH` in Paseo's daemon. If the Hermes binary moves, stop and reconcile the user-owned provider explicitly; do not silently rewrite it. From Paseo, launch a disposable Hermes ACP session. Validate model auth, actual streamed response, tool approvals **including denial of terminal/code-execution requests**, project working directory, cancellation and reconnection. Presence of the provider row alone is insufficient evidence. Hermes ACP can offer terminal/code-execution tools; before allowing untrusted sessions, prove Paseo surfaces approval prompts and does not silently auto-approve. If that cannot be proven, restrict the Hermes ACP toolset or block launch. Unlike Telegram/WhatsApp, no unauthenticated chat gateway is added.

The intended provider entry uses the absolute binary observed on the host (example path only):

```json
{"extends":"acp","label":"Hermes","command":["/home/owner/.local/bin/hermes","acp"]}
```

Other native and ACP providers remain user-managed. In particular, this feature neither launches Hermes as a messaging service nor gives Hermes authority to call `paseo run` or remote shell commands outside an explicitly approved local session.

## Negative tests, rollback and deferred work

Reject missing ACP extra, malformed Paseo JSON, user-authored Hermes entry, absent local CLI, and unapproved opt-in. The normal check must not change files. If the registration fails midway, inspect `~/.paseo/config.json` and preserve all user entries; ask the owner before restoring a backup. Roll back **only** the dotfiles-added `agents.providers.hermes` entry using Paseo's native `daemon config unset agents.providers.hermes` after confirming it is still identical to the managed entry.

Messaging, cron delivery, WhatsApp/Telegram, generic remote computer control, and a Hermes-to-Paseo broker remain deliberately out of scope.

References: [Hermes ACP](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp), [Paseo custom providers](https://paseo.sh/docs/custom-providers), [Paseo configuration](https://paseo.sh/docs/configuration), [Paseo remote access](PASEO_REMOTE.md).