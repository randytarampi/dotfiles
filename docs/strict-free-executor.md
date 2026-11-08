# Strict-free runtime execution handoff (E1.1/E1.2)

**Status: staged/test-only dispatch seam, 2026-11-07. NOT connected to production.** This PR is stacked on the account-policy PR for [#87](https://github.com/randytarampi/dotfiles/issues/87). Nothing prevents an installed OpenCode/OMO-Slim or LiteLLM client from issuing an unguarded paid request today. Do not describe strict-free as enforced by this PR.

## Implementation entry point

scripts/lib/strict_free_executor.py wraps an injected send function with the pure account-qualified admission decision. The guarded choice seam rejects if even an unselected potential fallback is unqualified, and selects only an explicit index. The test spy in scripts/lib/tests/test_strict_free_executor.py proves a denied route never reaches the injected sender, and proves a 429 propagates without automatically visiting a fallback with stale evidence. It is **not an upstream fallback implementation** and the synthetic RateLimitError does not establish any host error-to-retry semantics.

## Mandatory next engineering phases

1. Inspect the exact installed OpenCode v2, OMO-Slim and LiteLLM versions. Check which pre-request hooks exist in foreground, delegated/background child, council, metadata, retries and model-switch state. Some OMO-Slim versions treat v2 foreground fallback differently; do not assume JSON arrays or the retry hook always replay a request.
2. Identify **one enforceable boundary** for each actual outbound request. A start-of-session check is insufficient: keys, aliases, deployment lists, quota and provider policy can change before fallback. If a privileged direct bypass exists, either guard it or remove the strict-free claim for that path.
3. Supply account-authorised attested evidence before *every send*. The supplied dataclass/fixtures cannot verify provider signatures, authenticate billing state, reserve quota atomically or guarantee the token budget is enforced. A stateless re-invocation with the same pre-429 evidence is NOT safe: a real integration must invalidate/refresh it or stop. Deny UNKNOWN. Never silently resume on a paid alternate after a gateway 401/429.
4. Add deterministic host-equivalent fault injection: paid-route spy, missing key, 401/403, 429, exhausted fallback, mid-session model switch, background delegate, council and privacy mismatch. Assert zero paid-route sends, not simply successful config generation.
5. Only then perform an owner-approved installed consumer test, with account-bound receipt, actual backend identity, attribution, no bill and rollback. Stay draft until required CI and review are green on the integrated stack.

**Deployment isolation:** the dispatch seam is classified non-deployed in `scripts/check-hashes.py`; it is intentionally absent from chezmoi run-on-change triggers. Merging a test seam must not regenerate OpenCode config or change user settings. The actual production guard, once implemented and reviewed, will need new deployment hashes tied to the real caller.

## Constraints and rollback

Do not rewrite the external plugin, create an independent router, force every tool through LiteLLM, change existing live presets or claim no spending from displayed prices. Unknown account quota and stale entitlements are hard denials. This PR changes only a pure library, synthetic tests and documentation; revert these files to roll back. For a future runtime integration, rollback must preserve the prior owner-approved configuration without silently relaxing a promised strict-free mode.

## Tests and evidence

- Focused: PYTHONPATH=scripts/lib pytest -q scripts/lib/tests/test_strict_free_admission.py scripts/lib/tests/test_strict_free_executor.py.
- Required: make verify on the integrated PR tip, current-head CI and independent review.
- Owner-host: NOT RUN. No credentialed inference, account billing read or upstream hook probe performed by this author.
- Attribution: ChatGPT (GPT-6) prepared the test-only adapter and handoff. Repository owner approved the zero-charge requirement.
