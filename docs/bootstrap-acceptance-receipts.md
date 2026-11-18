# Bootstrap Acceptance Receipts

`scripts/collect-bootstrap-acceptance.py` produces a comparable, privacy-bounded record of bootstrap evidence across Linux, macOS and Windows. Its default inspect mode is read-only: it never invokes an installer, service manager, sudo, `make deploy` or a hypervisor.

Use `--record PATH` to merge structured outcomes supplied by an authorized external driver. The collector does not perform those operations. Outcome inputs should use `{ "stages": { "preflight": {"outcome":"pass", "exit_code":0} } }`; a stage may be an array when recording attempts. A failure remains a failure even if a later attempt passes, so retries cannot masquerade as first-and-second-install success.

The allowlisted receipt schema records host facts, coarse tool path classes, selected-manifest SHA-256 values, five stage outcomes, conservative qualification, privacy declarations and provenance. It cannot prove that a driver performed the claimed work, that a service is healthy, that credentials work, or that host facts have not been spoofed. `--validate` checks schema shape and can compare `--source-sha` to reject stale evidence.

Receipts never contain raw paths, usernames, email addresses, secrets or captured command output. Secret-like keys in recorded JSON are rejected. Use `--out PATH` for an atomic write; existing files require `--force`, and symlinks are refused.
