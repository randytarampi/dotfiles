"""Pure transport mapping for canonical model identities."""

from pathlib import Path


def resolve_alias(
    canonical_identity,
    proxy_mode,
    *,
    client_key=None,
    gateway_url=None,
):
    """Resolve a canonical provider/model ID for one client transport.

    Returns a mapping containing ``model``, ``base_url`` and ``api_key``.
    The function has no environment or filesystem reads; callers supply mode
    and credential indirection explicitly.
    """
    if not isinstance(canonical_identity, str) or "/" not in canonical_identity:
        raise ValueError("canonical identity must be provider/model")
    forward_parts = [part for part in canonical_identity.split("/") if part != ""]
    if (
        len(forward_parts) != len(canonical_identity.split("/"))
        or len(forward_parts) < 2
    ):
        raise ValueError(
            f"canonical identity has empty segments or no model part: {canonical_identity!r}"
        )
    if not proxy_mode:
        return {"model": canonical_identity, "base_url": None, "api_key": None}
    if not gateway_url:
        raise ValueError("gateway_url is required in LiteLLM proxy mode")
    if client_key is None:
        raise ValueError("client key indirection is required in LiteLLM proxy mode")
    if isinstance(client_key, Path):
        key_ref = "{file:" + str(client_key) + "}"
    elif isinstance(client_key, dict) and isinstance(client_key.get("file"), str):
        key_ref = "{file:" + client_key["file"] + "}"
    elif isinstance(client_key, str) and client_key.startswith(
        ("{file:", "os.environ/", "$", "!cat ")
    ):
        key_ref = client_key
    else:
        raise ValueError("client key must be a file or environment-variable reference")
    return {
        "model": f"litellm/{canonical_identity}",
        "base_url": gateway_url.rstrip("/"),
        "api_key": key_ref,
    }


def resolve_canonical_identity(client_reference, proxy_mode):
    """Invert a client reference into its canonical identity, if recognized."""
    if not isinstance(client_reference, str):
        return None
    candidate = (
        client_reference[len("litellm/") :]
        if client_reference.startswith("litellm/")
        else client_reference
    )
    if not proxy_mode and candidate != client_reference:
        return None
    # Structural validation: exactly one transport hop, no empty segments.
    if candidate.startswith("litellm/"):
        return None  # nested transport (litellm/litellm/...) is never valid
    parts = [part for part in candidate.split("/") if part != ""]
    if len(parts) != len(candidate.split("/")) or len(parts) < 2:
        return None  # empty segment (leading/trailing/double slash) or no separator
    return candidate


def canonical_allowlist_key(model_id):
    """Strip provider-internal path segments for allowlist lookup.

    Google aliases use ``google/models/<id>`` while the checked-in google
    allowlist keys bare IDs, so ``models/<id>`` collapses to ``<id>``.
    """
    if isinstance(model_id, str) and model_id.startswith("models/"):
        return model_id[len("models/") :]
    return model_id
