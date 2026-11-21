# Private local model-evidence mapping onboarding

**Status: research and offline shadow only.** This facility does not authorize evidence-driven model selection, publish benchmark data, or write local mappings. Each candidate remains a human-reviewed hypothesis about a checkpoint and quantisation.

## Offline workflow

1. Obtain any source snapshot separately using the dedicated provider refresh command and your own credentials/terms. AA v3 private cache includes UUID, slug, display name, creator identity and Intelligence index; the old v2 has no join names. BenchLM Free may expose only the first 100 ranks from 5 December 2026. whatmodel is a 40-row incomplete public reviewed subset and the declared HTTP client may receive HTML, not JSON.
2. Run `python3 scripts/suggest-model-evidence-mappings.py` on the **development host** with working local Ollama/oMLX discovery. This probes local model metadata but never calls benchmark-provider APIs, writes a map or approves a suggested mapping.
3. For offline fixtures or a private locally captured inventory, supply `--models-file /private/fleet.json`. Optional arguments are `--source-dir`, `--aa-cache-dir`, `--map-file` and `--profile quiet|loud|solo`.
4. The JSON output groups distinct eligible local routes by qualified reasoning, coding, lightweight and solo categories. It suggests exact `model_id`, `creator_id` and scoring `scale` triples only when checkpoint names resemble the local model name after stripping common local quantisation markers. Every suggestion has `owner_verified: false` and includes a source-coverage caveat; no scores or tokens are printed.
5. Verify **checkpoint revision**, upstream creator, fine-tune, parameter count, dense/MoE architecture, active experts, context, local runtime, quantisation and source methodology **manually**. A matching name or size is NOT proof of benchmark equivalence. Identical local model routes appearing twice are not two independent candidates.
6. Only after that review, manually edit the private path `~/.config/dotfiles/model-evidence-map.json` or the value of `DOTFILES_MODEL_EVIDENCE_MAP`. Never commit the map or restricted source rows. Use owner-only file permissions.
7. Run `python3 scripts/configure-opencode-tier.py --preset local --dry-run --model-evidence off` followed by the same command with `--model-evidence shadow`. The shadow diagnostics must show **two distinct mapped, supported, comparable** candidates in the same category before a rank comparison is meaningful.

## Private mapping format (template, not approved)

```json
{ "schema_version": 1, "mappings": {
  "omlx/LOCAL-MODEL-NAME": {
    "benchlm-coding": {
      "model_id": "EXACT-REVIEWED-UPSTREAM-ID",
      "creator_id": "EXACT-REVIEWED-CREATOR",
      "scale": "EXACT-OBSERVED-SOURCE-SCALE"
    }
  }
} }
```

Suggested identities are **proposals**, not executable mappings. The tool deliberately has no `--write`, `--approve` or automatic matching feature.

## Decision and rollback

If no comparable candidate pair exists, report **unmappable / not testable**, not benchmark agreement. If two eligible models exist, compare actual coding/reasoning/tool-call quality, context, TTFT, throughput and memory on the same host against curated `DOTFILES_CATEGORY_MODELS` / `DOTFILES_ROLE_MODELS` preferences. Keep `--model-evidence off` as the production default pending a demonstrated measured win. The executable currently accepts only off/shadow; `apply` is deliberately rejected until a separately authorised change is reviewed. Scores never override capability gates and no provider outage blocks local selection.

**Known limitation:** BenchLM four-file publication is atomic per file, not a transaction. Interrupted replacement can make all evidence unusable until a new complete generation is published. The one-read schema probes cannot verify the full dataset.

**Provenance:** 21 November 2026; implementation and coordination by ChatGPT (GPT-6); live API and hardware findings supplied by the implementation colleague. No paid source subscription, host benchmarks, secret operations or human approvals are claimed.
