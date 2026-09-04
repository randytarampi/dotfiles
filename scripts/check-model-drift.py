#!/usr/bin/env python3
"""Check model assignments against checked-in and live model catalogs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import logger
from model_stamp import is_stale

REPO_ROOT = SCRIPT_DIR.parent
SLIM_PATH = REPO_ROOT / "configs" / "opencode" / "oh-my-opencode-slim.json"
# Google/OpenRouter entries are checked for internal allowlist membership only;
# they are not queried against live catalogs. Refresh via free-preset skill.
ALLOWLISTS = {
    provider: REPO_ROOT / "configs" / "opencode" / f"{provider}-models.json"
    for provider in (
        "openai",
        "anthropic",
        "ollama-cloud",
        "github-copilot",
        "opencode",
        "google",
        "openrouter",
    )
}


def iter_models(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "model" and isinstance(child, str):
                yield child
            else:
                yield from iter_models(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, str):
                yield child
            else:
                yield from iter_models(child)


def get_models(url: str, api_key: str = "") -> set[str] | None:
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {str(item.get("id")) for item in data.get("data", []) if item.get("id")}
    except Exception as exc:
        logger.warning("Could not reach %s — skipping (%s)", url, exc)
        return None


def endpoint_models_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return base_url + "/models"


def load_allowlists() -> dict[str, set[str]]:
    result = {}
    for provider, path in ALLOWLISTS.items():
        if not path.exists():
            logger.warning("Missing %s model allowlist — skipping", path)
            continue
        try:
            models = json.loads(path.read_text(encoding="utf-8")).get("models", {})
            values = set(models) if isinstance(models, dict) else set()
            if isinstance(models, dict):
                values.update(
                    item.get("name")
                    for item in models.values()
                    if isinstance(item, dict) and item.get("name")
                )
            result[provider] = values
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s — skipping (%s)", path, exc)
    return result


def check_slim(data: dict) -> list[str]:
    violations = []
    allowlists = load_allowlists()
    for model in set(iter_models(data)):
        if model.startswith("_local:") or model.startswith("ollama/"):
            continue
        if "/" not in model:
            continue
        provider, model_id = model.split("/", 1)
        if provider not in allowlists:
            violations.append(f"{model} uses an unavailable {provider} model allowlist")
        elif model_id not in allowlists[provider]:
            violations.append(f"{model} is not in the {provider} model allowlist")
    return violations


def profile_models(path: Path) -> list[tuple[str, set[str], str]] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("providers"), dict) and isinstance(
            data.get("groups"), dict
        ):
            entries = []
            for group in data["groups"].values():
                if not isinstance(group, dict):
                    continue
                provider = data["providers"].get(group.get("provider", ""), {})
                ids = {
                    str(group[key]).split("/", 1)[-1]
                    for key in ("primaryModel", "fasterModel")
                    if isinstance(group.get(key), str)
                }
                if isinstance(provider, dict):
                    api_key = os.environ.get(provider.get("apiKeyEnv", ""), "")
                    entries.append((provider.get("baseUrl", ""), ids, api_key))
            return entries
        models = set()
        primary = data.get("primaryModel")
        if isinstance(primary, dict) and primary.get("id"):
            models.add(primary["id"])
        entries = [(data.get("baseUrl", ""), models, data.get("apiKey", ""))]
        faster = data.get("fasterModel")
        if isinstance(faster, dict) and faster.get("id"):
            faster_base = faster.get("baseUrl", data.get("baseUrl", ""))
            if faster_base == data.get("baseUrl", ""):
                models.add(faster["id"])
            else:
                entries.append((faster_base, {faster["id"]}, faster.get("apiKey", "")))
        return entries
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read Junie profile %s — skipping (%s)", path, exc)
        return None


def check_junie_profiles() -> list[str]:
    models_dir = Path(
        os.environ.get("JUNIE_MODELS_DIR", "~/.junie/models")
    ).expanduser()
    paths = list(models_dir.glob("*.json")) if models_dir.is_dir() else []
    local_config = Path(
        os.environ.get("JUNIE_LOCAL_GROUPS", "~/.junie-local/model-groups.json")
    ).expanduser()
    if local_config.exists():
        paths.append(local_config)
    grouped = {}
    for path in paths:
        result = profile_models(path)
        if result:
            for base_url, models, api_key in result:
                grouped.setdefault(base_url, []).append((path, models, api_key))
    violations = []
    for base_url, profiles in grouped.items():
        if not base_url:
            logger.warning("Junie profile group has no baseUrl — skipping")
            continue
        api_key = next((entry[2] for entry in profiles if entry[2]), "")
        models = get_models(endpoint_models_url(base_url), api_key)
        if models is None:
            continue
        # Catalog ID shapes vary by provider (Meridian serves bare ids, Google's
        # OpenAI-compat catalog prefixes with "models/", profiles may carry
        # provider prefixes like "anthropic/..."). Compare on the last segment.
        catalog_ids = {m.rsplit("/", 1)[-1] for m in models}
        for path, profile_ids, _ in profiles:
            normalized = {m.rsplit("/", 1)[-1] for m in profile_ids}
            for model in sorted(normalized - catalog_ids):
                violations.append(
                    f"{path} points to missing model {model} at {base_url}"
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check model assignments for catalog drift."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable results"
    )
    args = parser.parse_args()
    results = {"violations": [], "warnings": []}
    try:
        data = json.loads(SLIM_PATH.read_text(encoding="utf-8"))
        violations = check_slim(data)
        results["violations"].extend(violations)
        # Local placeholders are intentionally not self-validated against the
        # same live set; deployed Junie profile checks validate concrete IDs.
    except (OSError, json.JSONDecodeError) as exc:
        results["violations"].append(f"Could not read slim config: {exc}")
    results["violations"].extend(check_junie_profiles())
    stale, age = is_stale()
    if stale:
        age_text = "never" if age is None else f"{age:.0f}"
        results["warnings"].append(
            "Model assignments last synced "
            f"{age_text} days ago — re-run `make deploy` (or scripts/configure-jetbrains-ai.py + "
            "scripts/configure-opencode.py) after reviewing docs/MODEL_UPDATES.md"
        )
        logger.warning(results["warnings"][-1])
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for violation in results["violations"]:
            logger.error("Model drift: %s", violation)
        logger.info(
            "Model drift check complete: %d violation(s)", len(results["violations"])
        )
    return 1 if results["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
