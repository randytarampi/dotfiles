# Optional inference providers

This opt-in integration covers GroqCloud, Mistral Studio, Cloudflare **Workers AI**
(not Cloudflare AI Gateway), and NVIDIA-hosted NIM. It does not guarantee zero-cost
entitlement, tool support, latency or production rights. Model entries are **candidate
identities**, not measured inference success.

## Configure accounts and credentials

Place the selected credentials in private environment configuration (never commit
values or paste them into review comments). Names are documented in
`dot_dotfiles/shell/.env.example`. Use:

| Provider | Secret names | Model examples | Direct API |
|---|---|---|---|
| Groq | `GROQ_API_KEY` | `openai/gpt-oss-120b`, `openai/gpt-oss-20b` | `https://api.groq.com/openai/v1` |
| Mistral | `MISTRAL_API_KEY` | `mistral-small-latest`, `codestral-latest` | `https://api.mistral.ai/v1` |
| Cloudflare | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | `@cf/meta/llama-3.1-8b-instruct-fp8` | `https://api.cloudflare.com/client/v4/accounts/<account-id>/ai/v1` |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | `openai/gpt-oss-20b`, `deepseek-ai/deepseek-v4-flash` | `https://integrate.api.nvidia.com/v1` |

The Cloudflare account ID must be exactly 32 hexadecimal digits. The endpoint
host is constant; arbitrary URL overrides are not accepted. For NVIDIA Build, the
free hosted API is intended for permitted development/testing. The unattended
review workflow does not enable it unless `allow_nvidia_review: true` is explicitly
set and its permitted-use terms are independently accepted.

Set a comma-separated list of provider *names*:

```dotenv
DOTFILES_OPTIONAL_INFERENCE_PROVIDERS='groq,mistral,cloudflare,nvidia-nim'
```

Omitting the setting (the default) enables none. An unrecognised name or
duplicate is an error; a missing provider key omits only that provider.
For example, select `groq,mistral` when Cloudflare and NVIDIA have not been
set up. Never put real values in `.env.example`.

## Direct and LiteLLM modes

- **Direct** (`DOTFILES_USE_LITELLM_PROXY=0`): the selected, credentialed
  models are available in generated OpenCode and Pi providers. Junie model
  groups use their own supported OpenAI-completion endpoints.
- **Gateway** (`DOTFILES_USE_LITELLM_PROXY=1`): consumers use their separate
  LiteLLM client keys. The four provider alias families are published only
  when `DOTFILES_LITELLM_CATALOGUE_SCOPE=full` and the provider is selected
  and credentialed; they are never inferred to be free merely from the key.
  In the default `free` catalogue scope they remain withheld, since account
  entitlement and zero-charge status have not been verified.
- An unavailable gateway must **not** silently turn into direct provider calls.
  Gateway aliases have the form `litellm/groq/openai/gpt-oss-120b`,
  `litellm/mistral/mistral-small-latest`,
  `litellm/cloudflare/@cf/meta/llama-3.1-8b-instruct-fp8` or
  `litellm/nvidia-nim/openai/gpt-oss-20b`.
- New providers are opt-in **full-scope/evaluation candidates**, not
  automatically admitted to the strict-free `free` or Zen-only presets.
  Promotion to those fallback chains requires source-bound recurring
  zero-charge entitlement, verified tool/image calls and evidence expiry.
  Preserve the existing fail-closed dynamic-free policy.

## OpenCode GitHub review action

The reusable `.github/workflows/agentic-review.yml` accepts the trusted
`workflow_call` input `inference_provider`: `free` (default), `opencode`,
`groq`, `mistral`, `cloudflare`, or `nvidia-nim`. Maintainer-authorized `/opencode --pool=mistral`, `/opencode --pool=groq`,
`/opencode --pool=cloudflare`, `/opencode --pool=nim` or
`/opencode --pool=opencode` select an OpenCode inference pool without
notifying GitHub user accounts. `/opencode` uses the `free` pool, whose
configuration-time preference is Mistral, Cloudflare, Groq, then the existing
OpenCode Big Pickle when none of those credentials are present. This is
**not** runtime 429 failover or positive proof of zero-charge entitlement.
NVIDIA is excluded from the automatic pool. Multiple conflicting selectors
are rejected. The trusted-ref must be refreshed before new commands go live.
The GitHub repository or
organisation holding the caller workflow must provide the corresponding
optional secrets in addition to the existing review secrets. The Cloudflare
account identifier belongs in Actions **variable** `CLOUDFLARE_ACCOUNT_ID`,
not a secret. NVIDIA's `--pool=nim` option also requires explicit trusted
`allow_nvidia_review: true` approval and is not enabled by the dispatcher.
Do not inject
them into untrusted PR files.

The official OpenCode GitHub App still publishes with OIDC, using its
read-only `plan` agent, trusted-ref assets, and no-push guard. A missing
selected credential uses the previous `opencode/big-pickle` model. Invalid
trusted input or a malformed Cloudflare account ID fails closed.

**Hosted GitHub runners cannot reach the workstation's loopback LiteLLM**
without separately approved network/service deployment. The review job uses
direct providers only; it does **not** automatically load your local Slim
preset. Its optional model selection is **not an automatic 429 fallback chain**.

## Fallback and 429 boundaries

The upstream Slim plugin implements `fallback.enabled`, host retry-hook
model switching on v2, and separate foreground/background behaviour.
LiteLLM also has its own retry/fallback capabilities. This change adds **no
new 429 retry implementation**. Confirm the chosen actual consumer has a
working error-to-next-model path; merely listing several models in a provider
catalogue does not create it. A default GitHub review still runs one model
per review request; an exhausted 429 can fail that job.

Do not deliberately create a paid fallback from `free`. For the existing
Zen-only preset, do not add third-party provider routes.

## Validation, recovery, and owner checks

Run targeted provider/alias tests, `make check-slim-invariants`,
`make check-env-coverage`, `make check-hashes`, `make check-ci-assets`,
`make check-actionlint`, then `make verify`. Run configuration generation
twice for credential-present and credential-absent cases; confirm user-owned
files remain intact.

After provisioning credentials, verify an actual chat + tool call per provider
in direct mode and through a scoped LiteLLM virtual key, plus Junie/Pi/OpenCode
consumption. Verify modality before assigning an image-observer role, and
inspect account rate-limit/billing dashboards. Confirm a genuine GitHub review
publication from the official App before switching away from Big Pickle by
default.

For rollback, unset `DOTFILES_OPTIONAL_INFERENCE_PROVIDERS` (or remove one
name), regenerate with normal setup/deploy gates, and restore the trusted
review `inference_provider` value to `opencode`. Do not delete or overwrite
credential stores, existing client virtual keys or user-owned profiles.

## References

- [GroqCloud](https://console.groq.com/docs/overview)
- [Mistral Studio](https://docs.mistral.ai/)
- [Workers AI OpenAI compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)
- [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [NVIDIA hosted NIM](https://build.nvidia.com/explore)
- [Slim fallback configuration](https://github.com/alvinunreal/oh-my-opencode-slim/blob/master/docs/configuration.md)

## Model-pool strategy (deliberately deferred)

A fully verified default `free` pool remains an extension after credentialed tool-call,
licensing, cost and independent-rate-limit evidence for each account/model.
The action currently has one primary `with.model`, not the local Slim
orchestrator/fallback engine. A catalogue of names does not provide an ordered
runtime fallback chain for GitHub reviews. Future work should re-use the existing
evidence-qualified dynamic-free registry or explicitly configure the host's
supported fallback plugin, without silently enabling paid/development-only
capacity. The fallback when no preferred provider is configured remains `opencode/big-pickle`.
