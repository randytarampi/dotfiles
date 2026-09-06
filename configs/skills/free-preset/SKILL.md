---
name: free-preset
description: "Refresh and verify the free cross-provider tier preset. Use when working on the free preset, model refresh, preset rebuild, or model allowlist changes. Triggers on: 'free preset', 'model refresh', 'preset rebuild', 'allowlist'."
---

# Free Preset

Use this repeatable procedure to refresh the preset with high confidence.

## Procedure

1. Verify that current model IDs are live before changing configuration:
   - OpenCode Zen: <https://opencode.ai/zen/v1/models>
   - OpenRouter: <https://openrouter.ai/models>. A `:free` suffix identifies
     the free tier. OpenRouter caps usage at 50 requests/day with less than
     $10 in credits or 1,000 requests/day with at least $10 in credits, and
     permits 20 RPM via `GET /api/v1/key`.
   - Gemini: <https://ai.google.dev/gemini-api/docs/models> and
     <https://aistudio.google.com/rate-limit?timeRange=last-28-days>. Gemini
     has no universal RPM/RPD table: limits are per project and RPD resets at
     midnight PT.
2. Update `configs/opencode/google-models.json` and
   `configs/opencode/openrouter-models.json` allowlists first.
3. Update `configs/opencode/oh-my-opencode-slim.json` in all three places:
   `presets`, `council.presets`, and `_tiers`. The names must stay synchronized.
4. Check fallback rules: providers must be deduplicated within each array,
   no fallback may duplicate its role primary, and the council fallback must
   remain empty.
5. Run the fast invariant check and the full repository verification:

   ```sh
   python3 scripts/verify-slim-invariants.py
   make verify
   ```

6. If a model retires, replace it in the role, fallback, and allowlist in the
   same change.
7. Verify each candidate model with a real chat-completions probe, not just
   the catalog listing — a model can be listed (HTTP 200 on `/models`) yet
   fail on every request (HTTP 500). Note that OpenCode Zen returns a
   misleading `CreditsError: No payment method` for some contributor-free
   models; the free tier itself is free (`"cost": "0"` in live responses),
   so a payment-method error on a `*-free` model means the model is broken
   upstream, not that billing is needed.

OpenCode Zen contributor models may have privacy considerations; review the
current Zen terms before relying on them.
