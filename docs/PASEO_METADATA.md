# Strict local Pi metadata for Paseo

Paseo now documents **Manual** metadata selection using a non-empty
`agents.metadataGeneration.providers` list. Only those entries are tried,
and failure produces placeholders rather than a cloud/default-agent fallback.
This is the contract in current upstream documentation; owner must verify
the installed Paseo version behaves accordingly before deployment.

`configure-paseo-metadata.py --preset local --dry-run` derives the effective
`orchestrator` through the *same* `tier_registry.classify_models_for_preset`
and `materialize_role_models` pipeline as `configure-opencode-tier.py`.
It refuses unresolved placeholders, cloud providers, absent local models,
a non-loopback Pi endpoint, models not advertised in Pi's private
`~/.pi/agent/models.json`, and a Pi current default that disagrees with
the derived model. These checks prevent silently selecting a different
cloud or gateway route.

A private, existing Paseo home is required. Existing user-authored metadata
provider arrays may not be replaced. Owner may stage/apply with
`DOTFILES_RUN_PASEO_METADATA_SETUP=1 python3
scripts/configure-paseo-metadata.py --preset local --apply`, only while all
Paseo UI/daemon settings writers are quiescent. The native mutation is
rehearsed in a private copy and verified against the full expected JSON,
but it **does not repair issue #173**.

Owner-only acceptance: prove real title, branch, commit and PR draft
generation through Pi with the exact local model and no cloud egress;
deliberately fail the local route and confirm metadata failure/placeholders
instead of paid fallback. Never use generation logs as a substitute for
network receipts. Roll back only an exact managed metadata-provider value,
never all user settings.

Owner: Randy Tarampi. Implementation: ChatGPT (GPT-6).
Installed-local Pi/Paseo consumer acceptance: NOT RUN.
