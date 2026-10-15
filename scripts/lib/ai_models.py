def resolve_model(pattern, available_models):
    """
    Given a prefix pattern and a list of available model IDs, find the best match.
    - pattern: str pattern to search (e.g. 'gpt-4')
    - available_models: list of available model strings
    Returns: best matching model ID.
    """
    # Exact match
    if pattern in available_models:
        return pattern

    # Prefix match - prefer the match with the longest name
    best = ""
    for m in available_models:
        if m.startswith(pattern):
            if not best or len(m) > len(best):
                best = m

    if best:
        return best

    # Fallback: return the pattern as-is
    return pattern


def strip_provider_prefix(model_name):
    """Strip provider prefixes like 'omlx/' and 'ollama/' from model names."""
    if "/" in model_name:
        return model_name.split("/", 1)[1]
    return model_name


# Known locally-served specialized model families (decision, STT/TTS/STS,
# embedding, rerank, conversion). Matched as lowercase name prefixes so the
# guard works on untyped discovery records: Ollama discovery emits only
# name/size/provider and the healthy `ollama show` lookup never supplies a
# model_type, so typed metadata alone cannot distinguish a specialized model
# from a legacy chat model. Names outside this list stay eligible (typed
# metadata still governs them); add an entry when a new specialized family is
# deployed. The chat gateway catalogue and Junie pool filters have no
# dedicated modality seats, so these names are excluded there unconditionally.
SPECIALIZED_MODEL_NAME_PREFIXES = (
    "strands-decider",
    "whisper",
    "qwen3-asr",
    "qwen3-tts",
    "chatterbox",
    "gemma4audio",
    "nomic-embed",
    "mxbai-embed",
    "snowflake-arctic-embed",
    "voyage-code",
    "bge-m3",
    "bge-reranker",
    "markitdown",
)


def is_specialized_model_name(model_name):
    """True when the model name is a known specialized (non-chat) model family."""
    name = (model_name or "").lower()
    return name.startswith(SPECIALIZED_MODEL_NAME_PREFIXES)


def is_ollama_decision_model_name(model_name):
    """True when the model name is the known decision (routing) model family."""
    return (model_name or "").lower().startswith("strands-decider")
