# Local speech: bounded cross-client acceptance

> P1 [#222](https://github.com/randytarampi/dotfiles/issues/222) · first source/protocol inventory at `main@ab5077e30` · parent dependency [#221](https://github.com/randytarampi/dotfiles/issues/221) read-only service readiness. **No installed-host speech was executed by this author.**

## Decision and non-goal

Sharing a typed oMLX model or authenticated LiteLLM `/v1/audio/*` route is valuable **only if the actual client/plugin can consume the same transport and audio format**. Avoid a second audio provider registry, a universal speech daemon, implicit downloads and an automatic replacement of native STT/TTS plugins. The canonical selection path is `scripts/lib/selected_service.py` and the existing per-consumer generators. Route preference remains explicit choice → still-qualified prior → positive evidence → pinned → favourite → discovery order; `loaded` and `pinned` alone never prove successful inference.

## Producer / consumer contract

| Consumer | STT producer and required wire | TTS producer and required wire | Credential/owner boundary | Current status |
|---|---|---|---|---|
| OpenCode v2 TUI `@renjfk/opencode-voice` | whisper-cli local subprocess, or typed oMLX STT if supported by current plugin/options | Piper-native synthesis, **not generic HTTP TTS** | `configure-opencode-voice.py` preserves unmanaged `cli.json` plugin keys | Config delivered; pinned actual installed version and speech output UNKNOWN |
| Pi `@juicesharp/rpiv-voice` | sherpa-onnx in-process and installed native model; no qualified HTTP replacement | No generic HTTP TTS currently proven | Pi extension/package owner; separate Pi model roles | Native path documented; HTTP substitution unsupported until upstream contract changes |
| Open WebUI | OpenAI-style transcriptions, local oMLX or gateway, shared selection/previously qualified route | TTS model `audio_tts` and voice-ID support via existing WebUI bundle | Owned WebUI projection ledger; embedding/index representation must not be changed | Earlier consumer outcomes recorded in [chat-frontend.md](chat-frontend.md); no new live test |
| Paseo | oMLX typed STT or scoped LiteLLM audio route from `configure-paseo-voice.py` | Typed oMLX or gateway speech via explicit speaker name | Private `paseo.key`, native daemon env loader and owner-only writes | Source delivered; actual daemon secret consumption, negative paths still #213 |

**Transport is not identical to model identity.** Whisper GGML files, Piper voice models, sherpa-onnx bundles, MLX models, and LiteLLM wire aliases are different on-disk/runtime representations even if they implement similar speech tasks.

## Executable verification phases

**A — inventory first:** Inspect actual installed OpenCode and Pi voice plugin versions/options, oMLX daemon build, WebUI versions, Paseo daemon version; record exact model file/digest, voice ID, sample rate/codec, input length limit, client URL and credential source. Use [local-service-readiness.md](local-service-readiness.md) for anonymous service reachability only. Use existing `local_engines.audio_models`, `selected_service.select` and `route_probes` rather than duplicating discovery.

**B — source/unit tests:** Run `PYTHONPATH=scripts/lib pytest -q scripts/lib/tests/test_opencode_voice_gateway_normalization.py scripts/lib/tests/test_voice_auth_source.py scripts/lib/tests/test_openwebui_audio_transitions.py scripts/lib/tests/test_configure_paseo_voice.py scripts/lib/tests/test_paseo_audio.py scripts/lib/tests/test_selected_service.py`, plus `make verify`. Build fixture tests for explicit model override, known-qualified prior, pinned and favourite ranking, absent/ambiguous voices, duplicate aliases, missing private key, and gate-off preservation. No real recordings or third-party calls in CI.

**C — controlled host tasks:** For **each supported STT and TTS consumer separately**, record one real success with a disposable audio fixture and redacted receipt; force 401, 404, 429, 500, wrong audio format, missing voice, missing model, gateway-down, timeout and no unintended paid/direct fallback. An installed voice plugin may legitimately require native Piper or sherpa-onnx; mark an HTTP replacement **not applicable** rather than pretending unified HTTP routing is functional.

**D — implementation decision:** If an upstream-supported OpenCode Voice TTS HTTP option becomes available, isolate it behind explicit consent with exact schema, streaming/voice tests and rollback in a separate PR. If Pi accepts a qualified oMLX STT route, qualify independently. Otherwise preserve native formats and close this issue once the protocol/acceptance matrix is documented as supported, unsupported or deliberately native.

## Safety/rollback

No changes in this first tranche to `~/.config/opencode/cli.json`, `~/.pi`, `~/.local/share/openwebui`, `~/.paseo`, private model stores or existing service listeners. Do not open local engines to LAN/public networks. Testing must not record private microphone audio in CI, retain personal audio in logs or trigger unapproved provider charges. If later adapters fail, revert only their owned route without erasing user voice/model preference.

## Follow-up ledger

- Plugin protocol and installed model/voice evidence: **UNKNOWN**, owner-host.
- Shared local discovery and credential delivery: existing code; source tests to be rechecked on exact PR head.
- New wire adapters: **deferred pending actual supported client API**, not silently implemented here.
- Owner-reported successful routine Paseo/OpenCode/Pi use remains acknowledged and need not be rerun absent a specific regression.
