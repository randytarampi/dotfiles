# Paseo speech-specific LiteLLM client key

Paseo must not borrow the Pi, OpenCode, Xcode or Open WebUI virtual key. The existing LiteLLM registry now owns a separate `paseo` client identity, `LITELLM_PASEO_KEY`, and canonical private key file `~/.local/share/litellm/clients/paseo.key`.

The key is **eligible for provisioning only when all gates are enabled**: `DOTFILES_RUN_LITELLM_SETUP=1`, `DOTFILES_RUN_PASEO_VOICE_SETUP=1` and `DOTFILES_USE_LITELLM_PROXY=1`, with an installed Paseo CLI. Enabling these gates may cause the existing LiteLLM provisioning flow to create a virtual key; review current remote key records and store private backups first. A generated virtual key alone does not prove that the gateway accepts `/v1/audio/transcriptions` or `/v1/audio/speech`.

This PR establishes identity/persistence only. The separately reviewed Paseo speech adapter must qualify exact STT/TTS aliases, avoid fallback across gateway/direct boundaries and provide credentials securely to the running daemon, whose environment may not inherit shell `~/.env`.

Owner: Randy Tarampi; implementation: ChatGPT (GPT-6). Live gateway provisioning and speech requests: NOT RUN.
