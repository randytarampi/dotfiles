---
name: pull-intake
description: Review an upstream dotfiles pull as an operator before deploying. Use when the user pulls a commit range and asks what changed, which ~/.env keys are affected, what to opt into, what NOT to opt into, and what fixes landed. Triggers on "pulled", "what's going on in there", "env changes", "opt into", "what do we need to do after pulling".
---

# Pull intake

Standard pre-deploy review for a newly pulled commit range.

## Procedure

1. **Range inventory**: `git log --oneline <old>..<new>` and `git diff --stat`.
   Group commits by theme (features, config retargets, docs, deps) before diving
   into details; the themes shape the report.
2. **Env surface**: diff `dot_dotfiles/shell/.env.example` and
   `scripts/migrate-env-gates.py` — every new/renamed/deprecated key is an
   operator decision. Check which are active, commented, or absent in the
   real `~/.env` (names only). Required-vs-optional is defined by gates the
   user already runs.
3. **Migration**: note what `make migrate` does for this range (renames, OR
   migrations, appended keys) and that project `.opencode/.env` files are
   deliberately excluded.
4. **Opt-in ledger**: classify opt-ins with the repo's own risk language and
   cite the doc that defines the contract (e.g. `docs/LITELLM.md`,
   `docs/TIERS.md`). Flag contradictions the doctor enforces (for example:
   exposing the Admin UI requires the service to actually serve it) before
   they become verify failures.
5. **Machine-state cross-check**: verify the pulled gates against this
   machine — service status (`launchctl list`), credentials present (names
   only, never values), oMLX/port overrides, and tier servability under
   gateway-only mode (`scripts/detect-tier-coverage.py`).
6. **Model retargets**: when model ids changed, consult
   `docs/MODEL_UPDATES.md` history and check live provider catalogues before
   declaring anything "fixed"; a listed id is not proof of inference access.
7. **Report shape**: What's going on · ~/.env changes (required / optional /
   deprecated-no-op) · how to opt in · what NOT to opt into · fixes worth
   knowing. Offer, but never run, `make migrate` / `make deploy` without the
   user's go-ahead.

## Secret hygiene

Never print `~/.env` wholesale. Grep key names; redact values with sed before
displaying anything; back the file up to the session temp dir before editing;
edit line-scoped (sed) rather than reading secrets into context.