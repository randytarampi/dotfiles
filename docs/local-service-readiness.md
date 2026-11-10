# Local-service dependency and readiness audit

> P1 [#221](https://github.com/randytarampi/dotfiles/issues/221) · started 2026-11-07 at main@ab5077e30 · owner-reported successful Codex, Junie/IntelliJ, Xcode, Paseo, OpenCode and Pi LiteLLM use. **This report does not independently verify those host sessions.**

## Bounded diagnostic

Run `python3 scripts/verify-local-service-readiness.py` for a JSON snapshot derived from the repository's existing `LOCAL_ENGINES` registry and current gateway gates. This reads **no credentials** and makes **no HTTP requests** by default. Use `--probe` only for a deliberate anonymous GET to **loopback** Ollama `/api/tags`, oMLX `/health` and LiteLLM `/v1/models`; a 401 is classified `REACHABLE_AUTH_REQUIRED`, never qualified. `--strict` exits nonzero for any configured route that is not anonymously reachable. No process starts, listening-port changes, persistent files, model downloads or environment writes.

The proof ladder is **configured → process/service reachable → authenticated model served → modality/operation probe-qualified → actual consumer-verified**. Only the first two stages can be observed here. A healthy `/api/tags` lists installed models but does not prove a model is loaded or usable; oMLX `/health` does not establish a typed speech route; a keyless LiteLLM `/v1/models` may report 401 even while a scoped virtual key works.

| Producer | Wire/probe | Existing consumers | Required decisive evidence |
|---|---|---|---|
| Ollama | local `/api/tags` and OpenAI-compatible `/v1` | OpenCode local models, Pi, Open WebUI; optional Copilot CLI | Actual installed model, prompt/tool task, no Ollama Cloud fallback |
| oMLX | `/health`, OpenAI-compatible `/v1/models`, typed `/v1/audio/*` | Local tier tools, WebUI, Paseo, OpenCode Voice where supported | Actual typed model, key/voice/format, STT/TTS probes, model in memory |
| LiteLLM | authenticated model catalogue and scoped key | Codex, Junie, IntelliJ, Xcode, Paseo, OpenCode, Pi (owner-reported) and opt-in new clients | Served *exact alias*, attributed request, failure/timeout/401/429 behavior |
| Basic Memory | native local MCP via `uvx` | Pi, OpenCode, Claude and other registered MCP clients | Actual new-session recall, local embedding/reranking and access denial |
| Pi local orchestrator | Pi-assigned local provider and model | Paseo Manual metadata generation | Actual metadata task and placeholder-only failure, no hosted cloud fallback |
| OpenCode Voice | `@renjfk/opencode-voice` TUI | Whisper-cli or oMLX STT; **Piper** TTS | Plugin's real native API and host output/insertion; HTTP service does not replace Piper |
| Pi Voice | `@juicesharp/rpiv-voice` | sherpa-onnx STT | Native plugin completion; no invented generic HTTP synthesis |

## Follow-up and failure matrix

- **Gate off:** no implicit service launch; preserve selected direct routes and user preferences.
- **Service down/restarting, wrong port, missing model, 401/403, quota/429:** record specific reason and refuse unapproved paid/privacy-changing fallback; no automatic daemon startup merely because a tool was installed.
- **Exact key/alias test:** separate owner-authorized `/v1/models` and a small task; redact credentials, prompts and user data from any stored receipt.
- **Service ownership:** inspect macOS launchd versus shell/manual process, Linux daemon and Windows host conventions from [orchestration.md](orchestration.md); no second process supervisor or new registry.
- **State preservation and recovery:** never alter persistent WebUI embeddings, Basic Memory data, user-home provider selection or public/tailnet Caddy routes while testing.

**Dated implementation context:** PR #234 delivered the diagnostic without executing host probes. The subsequent bounded host audit recorded below closed #221 on 2026-11-09. Its loopback observations are not model inference or installed-consumer qualification; those remain owned by the applicable client-specific issues.

## 2026-11-09 audit matrix capture (issue #221 closure evidence)

**Owner-host audit (2026-11-09):** No-HTTP baseline: Ollama (`http://localhost:11434`), oMLX (`http://127.0.0.1:11427`) and LiteLLM (`http://127.0.0.1:4000`) were configured but `NOT_PROBED`. The separately authorised anonymous loopback probe found Ollama/oMLX `REACHABLE_NOT_INFERENCE_QUALIFIED`, and LiteLLM `REACHABLE_AUTH_REQUIRED` (401 is the expected result for a protected model-list endpoint, **not** proof of an authenticated inference task). Codex CLI, Pi, OpenCode, Open WebUI and Basic Memory use was **OWNER-REPORTED**; consumer-level proof must be cited from the specific issue when it exists. Paseo's relay/WebUI posture was **configuration-audited**, not independently TLS/WebSocket-qualified. Paseo speech initially had successful LiteLLM gateway STT/TTS probes but was subsequently moved by owner direction to **direct oMLX** (`http://127.0.0.1:11427/v1`), with successful HTTP TTS and no completed in-app voice task (#222). The registered scoped gateway key may remain available without being the active speech route. Source #252 verifies LiteLLM-last restart ordering, not actual owner-host restart completion. The finite producer audit #221 is closed; active client blockers are independently tracked in #209/#210/#216/#220/#222 and #85/#211.
