# Account-specific strict-free admission (E1.0)

**Status: policy/reference implementation only, 2026-11-07.** This is a pure, non-networked decision layer. It does not intercept OpenCode, OMO-Slim, LiteLLM or any client request. The dependent runtime work is [issue #211](https://github.com/randytarampi/dotfiles/issues/211). It must not be called an effective no-spend guarantee until that integration is independently verified.

## Decision and trust boundary

A strictly free request requires *current, account-specific* evidence that **this exact request** cannot incur a monetary charge. Public zero-price catalogues, account-unbound screenshots, promotional credits and generic advertised quotas do not satisfy this standard. The authenticated evidence collector must bind account, credential fingerprint (no raw token), provider, endpoint, exact model, every LiteLLM deployment/backend, privacy policy, remaining token/request quota and a bounded maximum billable USD amount. Source evidence expires after at most five minutes. A potentially paid overage route fails closed.

The module at scripts/lib/strict_free_admission.py accepts Evidence only as an internal value **after** some independently authorised collector has authenticated it. The provenance label and reference are *not signatures* and can be forged by an untrusted caller. The runtime must prevent request payloads, model output and arbitrary files from constructing trusted evidence. If no authenticated collector exists, **all routes remain UNKNOWN/DENIED**. An allowed pure result without a fully guarded request boundary does not grant permission to transmit a request.

## Contract and negative cases

The pure admit function checks one exact backend. The admit_chain function checks every possible primary/fallback backend; it rejects an unknown or unproven branch before a caller can choose it. The same check must be applied separately at every root, delegated/background child, council, metadata and retry invocation by the future runtime integration. A changed key, alias or backend invalidates the evidence.

Negative cases: absent/stale/future evidence, public price list only, nonzero/NaN cost, trial, account revoked, account and credential mismatch, wrong endpoint/deployment/model, quota exhausted/unknown, positive billed overage, privacy mismatch, missing fallback and malformed request budget.

## Current candidate eligibility

The repository's existing catalogue and generation-time resolver may list the following candidates, but **none is account-admitted by this new policy without current account/credential-bound evidence and a trusted request-time guard**.

| Candidate family | Account-specific zero-charge status in this PR | Missing acceptance |
|---|---|---|
| OpenCode Console / Zen free models | UNKNOWN | Effective account entitlement, credential scope, quota, privacy and no paid fallback |
| Direct OpenRouter / Poolside / Cohere | UNKNOWN | Authoritative account billing, exact endpoint and model/backend, hard free capacity |
| Optional Groq / Mistral / Cloudflare / NVIDIA NIM | UNKNOWN | Explicit provider/account quota and zero-overage enforcement; ordinary optional routes remain full-scope |
| Local Ollama / oMLX | UNKNOWN as a *strict-free runtime route* | Proven local-only target, identity, privacy and no hidden cloud/fallback billing |
| LiteLLM alias or deployment group | UNKNOWN | Complete active backend inventory; every child deployment individually admitted |

Generation-time `free` labels and provider-local models remain untouched. A local model is not automatically evidence that every subsequent auxiliary, cloud or fallback call is free.

## Entry points, tests and rollout

- Source: scripts/lib/strict_free_admission.py
- Pure tests: scripts/lib/tests/test_strict_free_admission.py (PYTHONPATH=scripts/lib pytest -q scripts/lib/tests/test_strict_free_admission.py).
- Full gate: make verify on the exact branch; then current-head CI and independent review.
- #87 may reach policy completion once the evidence fields and provenance trust assumptions are accepted. Runtime enforcement, owner billing API proof, provider limits and OMO-Slim fallback are independently blocked in #211 until verified.
- No model presets, routing, virtual keys, host files, existing free classifications or billing records are modified by this PR. Rollback is removing the spec/library and tests.
- Authorship: ChatGPT (GPT-6) authored the reference implementation, tests and plan; repository owner established the zero-charge policy. No live account verification, installed-client tests or independent approval were performed.
