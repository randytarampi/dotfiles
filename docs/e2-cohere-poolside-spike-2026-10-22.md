# E2 Provider Spike Findings — 2026-10-22

Captured 2026-10-23 CEST for PR E review. Credentials were checked for non-emptiness only; no key material or derived identifiers were recorded. No live probes were made because the credentials were absent or empty.

## Cohere

- **2026-10-23 — Key:** `COHERE_API_KEY` was absent or empty in `~/.env`. Finding: no authenticated catalogue or chat request was attempted; entitlement and inference availability are unknown. Spike closed under the absent-credential stop rule.
- **2026-10-23 — Catalogue:** live catalogue response, HTTP status, latency and headers are unknown; no request was made.
- **2026-10-23 — Chat and trial status:** no completion was attempted. Response status, latency, rate-limit headers and whether this key would be trial-limited to 1,000 calls/month are unknown.
- **References:** [Cohere rate limits](https://docs.cohere.com/docs/rate-limits); [Compatibility API](https://docs.cohere.com/docs/compatibility-api). These documentation URLs were not live-probed as part of the authenticated spike.

## Poolside

- **2026-10-23 — Key:** `POOLSIDE_API_KEY` was absent or empty in `~/.env`. Finding: key absent — entitlement unknown. Spike closed under the absent-credential stop rule.
- **2026-10-23 — Catalogue and completion:** no requests were made; response statuses, latency and response shape are unknown.
- **2026-10-23 — Unverified claims:** `https://inference.poolside.ai/v1/chat/completions` and model IDs `poolside/laguna-xs-2.1` and `poolside/laguna-s-2.1` remain unverified claims, not established endpoint or model availability.
