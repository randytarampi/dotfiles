# Junie Model Groups ↔ Oh My OpenCode Sync


Groq, Mistral, Cloudflare Workers AI and NVIDIA NIM have opt-in direct
model groups and LiteLLM twins. Groups without credentials or advertised
proxy aliases are not guaranteed usable. See
[OPTIONAL_INFERENCE.md](OPTIONAL_INFERENCE.md) for configuration and tests.
## Redaction hooks

Junie currently provides guidance through custom instructions only. No request-termination hook is verified; outbound LLM request redaction is not a claimed capability (**UNKNOWN-verified-none**). See the [migration plan](OPENCODE-V2-MIGRATION-PLAN.md#L67).
d