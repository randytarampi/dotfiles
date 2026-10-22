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
