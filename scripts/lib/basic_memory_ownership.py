"""Non-destructive ownership guard for the shared Basic Memory MCP name.

The dotfiles generator may install a missing server or re-render an identical
entry, but must never silently replace a user-selected vault, command or env.
A conflict fails before writes. The owner can remove the old entry to adopt
the managed defaults, or keep their existing MCP configuration untouched.
"""


def _comparison_shape(entry):
    """Reduce the known MCP entry layouts to their substantive fields.

    Handled layouts: template/JSON ``{command, args, env}``, v2-native
    ``{command: [bin, ...args], environment}``, and any variant carrying
    ``disabled``/``enabled`` policy keys. Fields that only express the same
    intent in a different layout (``environment`` vs ``env``, a bundled
    argument array) must not read as a user-owned divergence.
    """
    if not isinstance(entry, dict):
        return entry
    command = entry.get("command")
    args = list(entry.get("args") or [])
    if isinstance(command, list):
        args = command[1:] if len(command) > 1 else args
        command = command[0] if command else None
    env = entry.get("environment") or entry.get("env") or {}
    enabled = entry.get("enabled")
    if enabled is None:
        enabled = not bool(entry.get("disabled", False))
    return {
        "command": command,
        "args": args,
        "env": dict(env),
        "enabled": bool(enabled),
    }


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
    if previous is not None:
        before_shape = _comparison_shape(previous)
        after_shape = _comparison_shape(after.get("basic-memory"))
        if before_shape != after_shape:
            raise ValueError(
                "existing basic-memory MCP configuration differs from the managed "
                "template; refusing to overwrite user-owned vault/arguments/env"
            )
