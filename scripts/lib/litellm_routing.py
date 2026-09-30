"""Shared, field-aware LiteLLM routing loop validation."""

import re
import json
from urllib.parse import urlsplit

RESERVED_ROUTING_COMPONENTS = {"mozart", ".mozart", "mozart-router"}
ROUTING_FIELDS = ("model", "api_base", "api_key", "gateway", "model_provider")
ENDPOINT_FIELDS = {"api_base", "gateway"}


def _components(value):
    return set(re.findall(r"[A-Za-z0-9_.-]+", value.lower()))


def _endpoint(value):
    parsed = urlsplit(value if "://" in value else f"//{value}")
    try:
        port = parsed.port
    except ValueError:
        return None, None
    return parsed.hostname, port


def is_litellm_loop_target(value, *, field, port=4000):
    """Return whether *value* is a Mozart or self-routing target.

    Reserved names are matched as complete routing components, never as
    arbitrary substrings. Host/port matching is limited to endpoint fields.
    """
    if not isinstance(value, str):
        return False
    if field != "api_key" and _components(value) & RESERVED_ROUTING_COMPONENTS:
        return True
    if field not in ENDPOINT_FIELDS:
        return False
    hostname, target_port = _endpoint(value)
    return hostname in {"127.0.0.1", "localhost", "::1"} and target_port == port


def routing_entries_are_safe(entries, *, port=4000):
    for entry in entries:
        params = entry.get("litellm_params", {})
        if not isinstance(params, dict):
            continue
        for field in ROUTING_FIELDS:
            if is_litellm_loop_target(params.get(field), field=field, port=port):
                return False
    return True


def routing_config_text_is_safe(config_text, *, port=4000):
    """Parse only model_list routing fields and validate those fields."""
    try:
        import yaml
    except ModuleNotFoundError:
        # The deployed doctor is also runnable with the macOS system Python,
        # which does not include PyYAML. Generated configs use this small,
        # deliberately strict YAML subset; parse it structurally rather than
        # falling back to a raw-text search.
        config = _parse_generated_model_list(config_text)
    else:
        try:
            config = yaml.safe_load(config_text)
        except yaml.YAMLError:
            return False
    if not isinstance(config, dict):
        return True
    model_list = config.get("model_list", [])
    if not isinstance(model_list, list):
        return False
    entries = [entry for entry in model_list if isinstance(entry, dict)]
    return routing_entries_are_safe(entries, port=port)


def _parse_generated_model_list(config_text):
    entries = []
    current = None
    in_params = False
    for raw_line in config_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "model_list: []":
            return {"model_list": []}
        if stripped == "model_list:":
            continue
        if stripped == "litellm_settings:" or stripped == "general_settings:":
            break
        if line.startswith("  - "):
            current = {"litellm_params": {}}
            entries.append(current)
            in_params = False
            continue
        if current is None:
            continue
        if stripped == "litellm_params:":
            in_params = True
            continue
        if ":" not in stripped or not in_params:
            continue
        key, value = stripped.split(":", 1)
        if key not in ROUTING_FIELDS:
            continue
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        current["litellm_params"][key] = value
    return {"model_list": entries}
