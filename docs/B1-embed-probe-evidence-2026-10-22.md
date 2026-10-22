# B1 embedding-probe evidence — 22 Oct 2026

## State
- LiteLLM liveliness: HTTP 200; scoped key file present, mode 600.
- WebUI reconciliation: clean; keep=1; collisions=0. Preserve-on-unavailable was honoured.

## Warm recurrence
- Applies at 23:06:14 and 23:06:41, with no restart between, repeated:
  - `openwebui.py:1177 Open WebUI embedding gateway route unavailable: connection unavailable or timed out`
  - `openwebui.py:3718 Open WebUI embedding discovery unavailable; preserving managed settings`
- Audio-credential warning count was zero on both applies (B2 live proof).

## Endpoint discrimination
| Request | Result |
| --- | --- |
| `GET /v1/models` with scoped key | HTTP 200, 0.024 s |
| `POST /v1/embeddings`, model `ollama/embeddinggemma-2:740m-mxfp8` | HTTP 500, 4.9–5.25 s; `litellm.InternalServerError: OpenAIException - Missing credentials. Please pass an api_key` |
| Ollama direct `POST /api/embed`, same model | HTTP 200, 0.125 s |

## Hypothesis outcomes
- (a) Boot window: eliminated; liveliness was 200.
- (b) Cold first-load timeout: eliminated; gateway answered with HTTP 500.
- (c) Missing model: eliminated; direct Ollama embedding succeeded.
- (d) Missing/drifted scoped key: eliminated; file was present and models request succeeded.
- (e) Proxy-config credential gap: confirmed; generated LiteLLM entry for the Ollama embedding lane lacks a required `api_key`.
- (f) Endpoint/port discrepancy: eliminated; models request was fast and successful.

## Findings and disposition
- Root cause is the generated LiteLLM entry; corrective work belongs to C-slice 2/H, not B1.
- B1 fixes diagnostic fidelity: observed HTTP errors include their status instead of being described as connection/timeout failures.
- UNKNOWN/unavailable is preserved: reconciliation continues preserving managed settings until the corrective configuration lands.

## Corrective outcome (22 Oct 2026, same day)
- Generator fix `litellm_config._entry` (commit `6747ed2`): every `api_base` lane without a `key_env` now carries a non-empty `api_key: "none"` placeholder; credentialled lanes keep `os.environ/` references. Mutation-first test: `test_base_url_lanes_without_key_env_carry_a_placeholder_credential` (red on the two ollama lanes), plus stale-expectation updates for the cloud-stub and stability tests.
- Live proof after `configure-litellm.py` apply + `make litellm-restart`: liveliness 200 at ~15 s; `POST /v1/embeddings` model `ollama/embeddinggemma-2:740m-mxfp8` → **HTTP 200, 0.075 s** (was HTTP 500 at 4.9–5.25 s). Second apply converged clean; classification-only registry rows logged honest INFO skips (gemini/hermes/mozart) during provisioning.
- The former `embedding gateway route unavailable` warnings no longer occur (0 occurrences on the post-fix Open WebUI apply). The reconciliation now **refuses the embedding engine switch to the gateway route without the required acknowledgement** — the designed consent gate (Decision 5 / PR H): the refusal is honest, makes no writes, and preserves managed settings. The engine switch itself is an owner decision executed in PR H, not B1.
