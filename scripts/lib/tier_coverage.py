"""Resolve Slim registry model selections against a LiteLLM gateway inventory.

Shared by scripts/detect-tier-coverage.py (CLI) and the doctor's tier-coverage
row in scripts/verify-config.py. The resolution deliberately mirrors
``apply_litellm_slim_gate`` in configure-opencode.py so that a doctor row and a
deploy-time gateway-only failure can never disagree: both apply the same
canonical comparison and the same direct-exception handling.

Inventory sources are always an explicit deploy artifact (the generated
opencode.json ``provider.litellm`` model ids, or a future equivalent). When the
inventory is unavailable the result is UNKNOWN and non-blocking, matching the
repository's UNKNOWN discipline (e.g. litellm_cloud_aliases).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from litellm_aliases import canonical_allowlist_key

# Keep aligned with apply_litellm_slim_gate's direct_exceptions and local_owners.
# Local endpoints are deliberate alternatives, not LiteLLM catalogue entries.
DIRECT_EXCEPTIONS = {"meridian", "ollama", "omlx", "llamacpp"}
MODEL_FIELDS = {"model", "primaryModel"}
FALLBACK_FIELD = "fallback"


class RegistryRef:
    """A model reference with the dotted path where it was declared."""

    __slots__ = ("location", "ref")

    def __init__(self, location: str, ref: str) -> None:
        """Store a model reference and its dotted location in the registry."""
        self.location = location
        self.ref = ref

    def __eq__(self, other: object) -> bool:
        """Compare registry references by both location and model reference."""
        return isinstance(other, RegistryRef) and (
            self.location,
            self.ref,
        ) == (other.location, other.ref)

    def __hash__(self) -> int:
        """Hash the location and model reference used for equality."""
        return hash((self.location, self.ref))

    def __repr__(self) -> str:
        """Return a constructor-style representation of the registry reference."""
        return f"RegistryRef({self.location!r}, {self.ref!r})"


def collect_registry_refs(slim: dict) -> list[RegistryRef]:
    """Collect every resolvable model selection in a Slim registry document.

    Walks presets, council presets, the per-tier ``_tiers`` mirror, and
    fallback chains — exactly the shapes apply_litellm_slim_gate visits.
    Routed ``litellm/`` aliases are checked against inventory IDs exactly.
    """
    refs: list[RegistryRef] = []

    def add_ref(location: str, value: str) -> None:
        """Collect routed aliases and provider-qualified model selections."""
        if isinstance(value, str) and (
            value.startswith("litellm/")
            and bool(value.removeprefix("litellm/"))
            or "/" in value
            and not value.startswith("litellm/")
        ):
            refs.append(RegistryRef(location, value))

    def visit(node, location: str) -> None:
        """Walk nested registry nodes, collecting model fields and fallbacks."""
        if isinstance(node, dict):
            for key, child in node.items():
                child_location = f"{location}.{key}" if location else str(key)
                if key in MODEL_FIELDS:
                    add_ref(child_location, child)
                elif key == FALLBACK_FIELD and isinstance(child, dict):
                    for role, chain in child.items():
                        chain_location = f"{child_location}.{role}"
                        if isinstance(chain, list):
                            for index, entry in enumerate(chain):
                                add_ref(f"{chain_location}[{index}]", entry)
                        else:
                            add_ref(chain_location, chain)
                else:
                    visit(child, child_location)
        elif isinstance(node, list):
            for index, entry in enumerate(node):
                visit(entry, f"{location}[{index}]")

    visit(slim, "")
    return refs


def _matches_inventory(ref: str, inventory: set[str]) -> bool:
    """Apply apply_litellm_slim_gate's owner-fallback and canonical matching."""
    if ref.startswith("litellm/"):
        return ref.removeprefix("litellm/") in inventory
    owner, model = ref.split("/", 1)
    if owner in DIRECT_EXCEPTIONS:
        return True
    owner_for_lookup = {
        "anthropic": "meridian",
        "cohere": "cohere_chat",
    }.get(owner, owner)
    canonical = canonical_allowlist_key(model)
    matches = [
        item
        for item in inventory
        if "/" in item
        and item.split("/", 1)[0] == owner_for_lookup
        and canonical_allowlist_key(item.split("/", 1)[1]) == canonical
    ]
    if not matches and owner == "anthropic":
        matches = [
            item
            for item in inventory
            if "/" in item
            and item.split("/", 1)[0] == "anthropic"
            and canonical_allowlist_key(item.split("/", 1)[1]) == canonical
        ]
    if not matches and owner == "cohere":
        matches = [
            item
            for item in inventory
            if item.split("/", 1)[0] == "cohere"
            and canonical_allowlist_key(item.split("/", 1)[1]) == canonical
        ]
    return bool(matches)


def unresolved_refs(slim: dict, inventory: Iterable[str]) -> list[RegistryRef]:
    """Return registry selections the given gateway inventory cannot serve."""
    available = {item for item in inventory if isinstance(item, str) and item}
    return [
        entry
        for entry in collect_registry_refs(slim)
        if not _matches_inventory(entry.ref, available)
    ]


def load_inventory_from_opencode_json(path: Path) -> list[str] | None:
    """Return gateway model ids from a generated opencode.json, or None.

    None means the inventory is UNKNOWN (file missing, unparseable, or the
    ``provider.litellm`` block absent — e.g. direct mode), and callers must
    treat coverage as non-blocking rather than unserved.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    provider = document.get("providers", document.get("provider"))
    if not isinstance(provider, dict):
        return None
    litellm = provider.get("litellm")
    if not isinstance(litellm, dict):
        return None
    models = litellm.get("models")
    if not isinstance(models, dict):
        return None
    ids: list[str] = []
    for entry in models.values():
        if isinstance(entry, dict):
            entry_id = entry.get("id")
            if isinstance(entry_id, str):
                ids.append(entry_id)
    return ids
