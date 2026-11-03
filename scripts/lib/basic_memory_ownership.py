"""Non-destructive ownership guard for the shared Basic Memory MCP name.

The dotfiles generator may install a missing server or re-render an identical
entry, but must never silently replace a user-selected vault, command or env.
A conflict fails before writes. The owner can remove the old entry to adopt
the managed defaults, or keep their existing MCP configuration untouched.
"""


def ensure_basic_memory_ownership(existing, generated, namespace):
    """Reject collisions with a differently configured existing memory server."""
    if not isinstance(existing, dict) or not isinstance(generated, dict):
        raise ValueError("MCP configuration must be an object")
    before = existing.get(namespace, {})
    after = generated.get(namespace, {})
    if namespace == "mcp":
        before = before.get("servers", before) if isinstance(before, dict) else before
        after = after.get("servers", after) if isinstance(after, dict) else after
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("MCP server map must be an object")
    if "basic-memory" not in after:
        return
    previous = before.get("basic-memory")
    if previous is not None and previous != after.get("basic-memory"):
        raise ValueError(
            "existing basic-memory MCP configuration differs from the managed "
            "template; refusing to overwrite user-owned vault/arguments/env"
        )
