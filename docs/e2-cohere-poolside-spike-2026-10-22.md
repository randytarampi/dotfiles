# E2 Provider Spike Findings — 2026-10-22

Initial snapshot created 2026-10-23 CEST; live probes verified 2026-10-23 CEST. Credentials were checked for non-emptiness; no key material or derived identifiers were recorded. Probes used 15-second timeouts and recorded only selected rate-limit headers and response structure.

## Cohere

- **2026-10-23 — Catalogue (verified):** `GET https://api.cohere.com/v1/models` returned HTTP 200 in 558 ms. Catalogue returned 20 models. Command-family highlights included `command-a-03-2025`, `command-a-plus-05-2026`, `command-a-reasoning-08-2025`, `command-r-08-2024`, `command-r-plus-08-2024` and `command-r7b-12-2024`; these establish catalogue visibility, not suitability or performance.
- **2026-10-23 — Tiny chat (verified):** one OpenAI-compatible request to `https://api.cohere.com/compatibility/v1/chat/completions`, using listed model `command-a-03-2025`, returned HTTP 200 in 13,748 ms with one choice. Response shape: `choices`, `created`, `id`, `model`, `object`, `usage`; message keys: `content`, `role`.
- **2026-10-23 — Entitlement/rate signals:** catalogue response headers included `x-endpoint-monthly-call-limit: 1000` and `x-trial-endpoint-call-limit: 100`; chat response headers included `x-endpoint-monthly-call-limit: 1000` and `x-trial-endpoint-call-limit: 20`. The monthly 1,000-call signal is present. Trial call limits are also present, so these observations indicate trial-limited endpoint use; exact account-wide entitlement interpretation remains unconfirmed by these headers alone.
- **Open item:** tool-calling support was not tested. Native LiteLLM provider tool-calling still requires verification against the installed LiteLLM version in the E2 wire-up PR.
- **References:** [Cohere rate limits](https://docs.cohere.com/docs/rate-limits); [Compatibility API](https://docs.cohere.com/docs/compatibility-api).

## Poolside

- **2026-10-23 — Catalogue (verified):** `GET https://inference.poolside.ai/v1/models` returned HTTP 200 in 676 ms and listed `poolside/laguna-xs-2.1` and `poolside/laguna-s-2.1` (two models returned).
- **2026-10-23 — Tiny chat (verified):** one OpenAI-compatible completion to `https://inference.poolside.ai/v1/chat/completions` using `poolside/laguna-xs-2.1` returned HTTP 200 in 562 ms with one choice. Response keys were `choices`, `created`, `id`, `model`, `object`, `usage`; message keys were `content`, `reasoning_content`, `role`.
- **2026-10-23 — API shape/entitlement signals:** the `/v1/models` and `/v1/chat/completions` paths behaved as expected for a generic OpenAI-compatible adapter. Chat headers included `x-ratelimit-limit-requests: 60` and `x-ratelimit-remaining-requests: 30`. No explicit entitlement tier or pricing signal was observed in the selected response headers/body; commercial entitlement and pricing remain unknown.
- **2026-10-23 — Request identifier handling:** the response also included a provider request identifier header; it is intentionally omitted here to avoid recording a correlable identifier.
