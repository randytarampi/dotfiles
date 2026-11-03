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
    # Normalize only layout aliases; all other fields must match to avoid
    # overwriting user-owned tool filters, timeouts, cwd, or transport settings.
    normalized = dict(entry)
    command = normalized.pop("command", None)
    args = list(normalized.pop("args", None) or [])
    if isinstance(command, list):
        args = command[1:] if len(command) > 1 else args
        command = command[0] if command else None
    environment = normalized.pop("environment", None)
    env = normalized.pop("env", None)
    if environment is not None and env is not None and environment != env:
        normalized["environment"] = environment
    enabled = normalized.pop("enabled", None)
    disabled = normalized.pop("disabled", None)
    if enabled is None:
        enabled = not bool(disabled)
    elif disabled is not None and bool(enabled) == bool(disabled):
        normalized["disabled"] = disabled
    return {
        **normalized,
        "command": command,
        "args": args,
        "env": dict(environment if environment is not None else env or {}),
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
                "template; refusing to overwrite user-owned vault/arguments/env/settings"
            )
