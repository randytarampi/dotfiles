# Lightweight local utility and decision models

> P1 [#223](https://github.com/randytarampi/dotfiles/issues/223) · experiment stage at `main@ab5077e30` · parent [#221](https://github.com/randytarampi/dotfiles/issues/221). This is **not a live tier/model promotion**.

## Consumer-first qualification

- **Existing real consumer:** Paseo metadata generation via the selected Pi-local orchestrator; verify **Manual** mode uses only configured candidates and returns a placeholder if no local model works, on the exact installed Paseo release. Do not accidentally change working sessions or fall back to cloud.
- **Potential existing consumer:** Hermes auxiliary summary/model calls; inspect native `~/.hermes/config.yaml` active model and independent auxiliary routes, including key selection and cloud egress, before changing them (#217 is separate P0).
- **Possible consumers requiring proof:** small classification, intent routing, file-summary helpers. Don't add a model role, service or decision API endpoint with no concrete invoking client.
- **Existing system:** `scripts/lib/local_engines.py` discovers Ollama/oMLX. `docs/tiers.md` owns selected local models. Reuse the service-readiness audit [local-service-readiness.md](local-service-readiness.md) rather than another registry.

## Benchmarks and input

`python3 scripts/summarize-local-utility-benchmarks.py --results /path/to/redacted-results.json` calculates per-device/model/task median measured latency, peak measured resident GB, success and network-egress flags **separately for owner-reported, synthetic and unverified samples**; these provenance fields are supplied by the operator and not independently certified by the summarizer (they must never distort each other's measurements). It reads at most **1 MiB** from a deliberate local nonsymlink JSON file, rejects multiline/oversized labels, sends **no model requests**, stores **no files**, does not compare cost claims and marks **every** candidate `NOT_AUTO_PROMOTED`. Rejects unexpected fields to avoid accidentally capturing prompts, personal data, API keys or paths.

Minimum measurement record shape (illustrative *synthetic* data, not a host benchmark):

```json
[
  {
    "device": "test-host",
    "model": "local/fixture",
    "task": "metadata-title",
    "latency_ms": 100,
    "peak_gb": 1.5,
    "passed": true,
    "network_egress": false,
    "provenance": "synthetic"
  }
]
```

**Separate operator-run scenarios:** 4K/8K/16K input; short 64/256-token metadata outputs; a tool-schema dispatch and structured JSON task; cold versus warm start; cache retention; 1 and 2 concurrent clients; disabled thinking versus actual reasoning token output; and failure on missing/expired local model. Record measured p50/p95 completion latency, memory and correctness **outside** this aggregator until source data is safe to share. Target machines and installed versions must be recorded. Owner's previous Mellum2.1/Qwen benchmarks are candidates only; they are not new acceptance evidence.

## Decision protocol

1. **Read-only version check:** verify installed LiteLLM version plus any actual `/v1/decisions`/`/v1/systemone` semantics *only if a current client invokes this API*. These are typed **decision-model** endpoints and not synonyms for Chat Completions or a universal local router.
2. **Compare transport:** Pi direct local provider versus equivalent authenticated LiteLLM alias, if and only if both are served and authorized. Mark nonapplicable where no supported route exists.
3. **Prove egress and failure policy:** a local-only workload must never silently fall through to cloud on local 429, unavailable model, timeout or daemon restart; a placeholder or explicit unavailable result is acceptable.
4. **Select only after evidence:** prefer a single already-resident model with fast and agent workload profiles when quality and memory headroom support it. Require strict current-account evidence before any model is labelled zero-charge in a **cloud** path.
5. **Isolated implementation PR:** if beneficial, change one real consumer model setting with backup/readback and rollback. Otherwise document evidence-based no change and close #223.

## Gates and rollback

No automatic model downloads, endpoint publication, tier changes, default profile switches, background service or cost policy edits in this PR. Rollback removes only this offline report utility and guidance. Real host results are **not run** here.
