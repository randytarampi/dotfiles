# Bootstrap Acceptance Receipts

`scripts/collect-bootstrap-acceptance.py` produces a comparable, privacy-bounded record of bootstrap evidence across Linux, macOS and Windows. Its default inspect mode is read-only: it never invokes an installer, service manager, sudo, `make deploy` or a hypervisor.

Use `--record PATH` to merge structured outcomes supplied by an authorized external driver. The collector does not perform those operations. Outcome inputs should use `{ "stages": { "preflight": {"outcome":"pass", "exit_code":0} } }`; a stage may be an array when recording attempts. A failure remains a failure even if a later attempt passes, so retries cannot masquerade as first-and-second-install success.

The allowlisted receipt schema records host facts, coarse tool path classes, each tool's first `--version` line (truncated to 120 characters) and its container type (`script`, `elf`, `macho`, `pe` or `unknown`), selected-manifest SHA-256 values, five stage outcomes, conservative qualification, privacy declarations and provenance. It cannot prove that a driver performed the claimed work, that a service is healthy, that credentials work, or that host facts have not been spoofed. `--validate` checks schema shape and can compare `--source-sha` to reject stale evidence.

Receipts never contain raw paths, usernames, email addresses, secrets or captured command output. Secret-like keys in recorded JSON are rejected. Use `--out PATH` for an atomic write; existing files require `--force`, and symlinks are refused.

## Execution provenance

A schema-valid receipt proves shape, not that anything ran. `--record` merges
stage outcomes supplied by an external driver, so an all-pass receipt can be
authored by hand.

Use `--require-driver` (or `make receipt-validate RECEIPT=… SOURCE_SHA=…`) to
reject a receipt whose stages claim `pass` without a reference to the run that
produced it. One of these `provenance` fields satisfies the requirement:

| Field | Use |
|---|---|
| `run_url` | Immutable CI run URL — preferred, because it is independently fetchable |
| `run_id` | Run identifier when a URL is not available |
| `artifact_id` | Uploaded artifact identifier |
| `driver` | Named driver for an owner-host run with no CI record |

A receipt with no `pass` claims (everything `not_run` or `not_applicable`) needs
no provenance — it is an inventory, not an acceptance claim.

Citing a receipt in an issue is not the same as closing it. A schema-valid
all-pass record with no run reference must not close an issue by itself, and
`make receipt-validate` is deliberately **not** part of `make verify`: a clean
CI checkout cannot produce a host receipt, so requiring one there would either
fail everywhere or be permanently not-applicable.
