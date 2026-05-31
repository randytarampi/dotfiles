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
