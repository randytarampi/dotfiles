# E2 Provider Spike Findings — 2026-10-22

**Snapshot captured at:** 2026-10-23 CEST (repository/configuration snapshot; see [the model catalogue snapshot](../configs/litellm/model-snapshot-2026-10-22.md)). **Live probes performed at:** 2026-10-23 CEST. Snapshot capture time and live-probe time are separate provenance fields; the rows below date each live observation. Credentials were checked for non-emptiness; no key material or derived identifiers were recorded. Probes used 15-second timeouts and recorded only selected rate-limit headers and response structure.

## Cohere

- **Verified 2026-10-23 (live probe) — Catalogue:** `GET https://api.cohere.com/v1/models` returned HTTP 200 in 558 ms. Catalogue returned 20 models. Command-family highlights included `command-a-03-2025`, `command-a-plus-05-2026`, `command-a-reasoning-08-2025`, `command-r-08-2024`, `command-r-plus-08-2024` and `command-r7b-12-2024`; these establish catalogue visibility, not suitability or performance.
- **Verified 2026-10-23 (live probe) — Tiny chat:** one OpenAI-compatible request to `https://api.cohere.com/compatibility/v1/chat/completions`, using listed model `command-a-03-2025`, returned HTTP 200 in 13,748 ms with one choice. Response shape: `choices`, `created`, `id`, `model`, `object`, `usage`; message keys: `content`, `role`.
- **Observed 2026-10-23 (live probes) — Entitlement/rate signals:** catalogue response headers included `x-endpoint-monthly-call-limit: 1000` and `x-trial-endpoint-call-limit: 100`; chat response headers included `x-endpoint-monthly-call-limit: 1000` and `x-trial-endpoint-call-limit: 20`. These are **observed headers, not proof of entitlement**: they do not establish that this account or key holds a trial allocation, that any model is permanently free, or any pricing — production keys can also receive restricted-scope headers. Entitlement, account scope and cost status remain **UNKNOWN** pending provider confirmation; the strict-free policy must not rely on these headers as evidence.
- **Open item:** tool-calling support was not tested. Native LiteLLM provider tool-calling still requires verification against the installed LiteLLM version in the E2 wire-up PR.
- **References:** [Cohere rate limits](https://docs.cohere.com/docs/rate-limits); [Compatibility API](https://docs.cohere.com/docs/compatibility-api).

## Poolside

- **Verified 2026-10-23 (live probe) — Catalogue:** `GET https://inference.poolside.ai/v1/models` returned HTTP 200 in 676 ms and listed `poolside/laguna-xs-2.1` and `poolside/laguna-s-2.1` (two models returned).
- **Verified 2026-10-23 (live probe) — Tiny chat:** one OpenAI-compatible completion to `https://inference.poolside.ai/v1/chat/completions` using `poolside/laguna-xs-2.1` returned HTTP 200 in 562 ms with one choice. Response keys were `choices`, `created`, `id`, `model`, `object`, `usage`; message keys were `content`, `reasoning_content`, `role`.
- **Observed 2026-10-23 (live probes) — API shape/entitlement signals:** the `/v1/models` and `/v1/chat/completions` paths behaved as expected for a generic OpenAI-compatible adapter. Chat headers included `x-ratelimit-limit-requests: 60` and `x-ratelimit-remaining-requests: 30`. No explicit entitlement tier or pricing signal was observed in the selected response headers/body; commercial entitlement and pricing remain unknown.
- **Observed 2026-10-23 (live probe) — Request identifier handling:** the response also included a provider request identifier header; it is intentionally omitted here to avoid recording a correlable identifier.
