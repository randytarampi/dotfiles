## Description

<!-- Brief description of what this PR changes and why -->

## Testing

<!-- How did you verify these changes? -->
- [ ] `make verify` passes (lint + drift + doctor + check-hashes + dry-run)
- [ ] `make deploy` succeeds on a clean run
- [ ] Configs are valid JSON/YAML

## Checklist

- [ ] I've read `AGENTS.md` for repo conventions
- [ ] Hash triggers updated for any new config inputs (`make check-hashes`)
- [ ] Scripts are idempotent (safe to re-run via `make deploy`)
- [ ] New scripts wired into both `run_onchange_*` AND `configure-all.sh`
