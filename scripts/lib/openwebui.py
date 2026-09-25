"""Pure desired-state and collision-safe Open WebUI reconciliation.

The contract in ``docs/CHAT_FRONTEND.md`` principle 1 makes the database
runtime authority: environment values and ``LOCAL_ENGINES`` are inputs, while
deployment reconciliation reads and merges managed entries into that database.
Managed ownership is registry-derived and stable even when a provider is
disabled or temporarily keyless.  Equality is separate from ownership so key
rotation and admin enable flips converge safely.  Unmanaged entries are
preserved verbatim, and ownership collisions fail closed.  Ollama remains on
the native collection and is not duplicated in OpenAI connections.
"""

import copy
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import constants
from local_engines import LOCAL_ENGINES, active_engines, local_endpoint_for
from provider_endpoints import PROVIDER_ENDPOINTS

MANAGED_PREFIX_NAMESPACE = "dw-"
DISABLED_ENGINES_ENV = "DOTFILES_OPENWEBUI_DISABLED_ENGINES"


def mask_secret(value):
    """Return an opaque rendering that distinguishes unset from set secrets."""
    if value is None or value == "":
        return "<unset>"
    value = str(value)
    if len(value) <= 6:
        return "<set>"
    return value[:2] + "…" + value[-4:]


def _disabled():
    return {
        item.strip().lower()
        for item in os.environ.get(DISABLED_ENGINES_ENV, "").split(",")
        if item.strip()
    }


def _root(url):
    return url.rstrip("/")[:-3] if url.rstrip("/").endswith("/v1") else url.rstrip("/")


def _anthropic_url(url):
    return _root(url)


def _registry_endpoint(provider, protocol):
    """Resolve an engine endpoint without applying its active gate."""
    engine = LOCAL_ENGINES[provider]
    if protocol not in engine.get("api", ""):
        return None
    if protocol == "anthropic" and not engine.get("anthropic_support", False):
        return None
    if protocol == "openai" and not engine.get("openai_support", True):
        return None
    url = engine["base_url"]().rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url, engine.get("api_key_env") or None


def _connection(prefix, url, key, connection_type, collection):
    return {
        "url": url,
        "key": key or "",
        "config": {
            "prefix_id": prefix,
            "connection_type": connection_type,
            "enable": True,
        },
        "collection": collection,
    }


def ownership_catalogue(ollama_cloud_proxy_url=None):
    """Return stable prefix → expected endpoint/type ownership identities."""
    catalogue = {}
    for provider in LOCAL_ENGINES:
        if provider == "ollama":
            catalogue[f"{MANAGED_PREFIX_NAMESPACE}ollama"] = {
                "url": _root(LOCAL_ENGINES[provider]["base_url"]()),
                "connection_type": "ollama",
                "collection": "ollama",
            }
            continue
        for protocol in ("openai", "anthropic"):
            endpoint = _registry_endpoint(provider, protocol)
            if endpoint:
                catalogue[f"{MANAGED_PREFIX_NAMESPACE}{provider}-{protocol}"] = {
                    "url": endpoint[0],
                    "connection_type": protocol,
                    "collection": "openai",
                }
    clouds = {
        "openai": (constants.get_provider_base_url("openai"), "openai"),
        "anthropic": (
            _anthropic_url(constants.get_provider_base_url("anthropic")),
            "anthropic",
        ),
        "google": (PROVIDER_ENDPOINTS["google"]["baseUrl"], "openai"),
        "openrouter": (PROVIDER_ENDPOINTS["openrouter"]["baseUrl"], "openai"),
        "opencode": (PROVIDER_ENDPOINTS["opencode"]["baseUrl"], "openai"),
        "ollama-cloud": (constants.BASE_URLS["ollama-cloud"], "openai"),
        "meridian": (constants.get_meridian_base_url(), "anthropic"),
    }
    for provider, (url, connection_type) in clouds.items():
        catalogue[f"{MANAGED_PREFIX_NAMESPACE}{provider}"] = {
            "url": url,
            "urls": {url},
            "connection_type": connection_type,
            "collection": "openai",
        }
    if ollama_cloud_proxy_url:
        catalogue["dw-ollama-cloud"]["urls"].add(ollama_cloud_proxy_url)
    catalogue["dw-ollama-cloud"]["urls"].add(constants.get_ollama_local_base_url())
    return catalogue


def _ollama_cloud_url():
    if not os.environ.get("OLLAMA_API_KEY", "").strip():
        return constants.BASE_URLS["ollama-cloud"]
    if constants.should_use_ollama_cloud_proxy():
        _, can_proxy = constants.check_ollama_daemon()
        if can_proxy:
            return constants.get_ollama_local_base_url()
    return constants.BASE_URLS["ollama-cloud"]


def compute_desired_state():
    """Build desired entries; inactive/keyless providers remain ownership-known."""
    desired = {"openai": [], "anthropic": [], "ollama": []}
    disabled = _disabled()
    for provider in active_engines():
        if provider.lower() in disabled:
            continue
        if provider == "ollama":
            desired["ollama"].append(
                _connection(
                    "dw-ollama",
                    _root(constants.get_ollama_local_base_url()),
                    "",
                    "ollama",
                    "ollama",
                )
            )
            continue
        for protocol in ("openai", "anthropic"):
            endpoint = local_endpoint_for(provider, protocol)
            if endpoint:
                url, key_env = endpoint
                desired[protocol].append(
                    _connection(
                        f"dw-{provider}-{protocol}",
                        url,
                        os.environ.get(key_env, "") if key_env else "",
                        protocol,
                        "openai",
                    )
                )
    if constants.is_meridian_configured():
        desired["anthropic"].append(
            _connection(
                "dw-meridian",
                constants.get_meridian_base_url(),
                os.environ.get("MERIDIAN_API_KEY", ""),
                "anthropic",
                "openai",
            )
        )
    cloud_keys = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": PROVIDER_ENDPOINTS["google"]["apiKeyEnv"],
        "openrouter": PROVIDER_ENDPOINTS["openrouter"]["apiKeyEnv"],
        "opencode": PROVIDER_ENDPOINTS["opencode"]["apiKeyEnv"],
        "ollama-cloud": "OLLAMA_API_KEY",
    }
    for provider, key_env in cloud_keys.items():
        key = os.environ.get(key_env, "").strip()
        if key:
            if provider == "openai":
                url = constants.get_provider_base_url("openai")
            elif provider == "anthropic":
                url = _anthropic_url(constants.get_provider_base_url("anthropic"))
            elif provider == "ollama-cloud":
                url = _ollama_cloud_url()
            else:
                url = PROVIDER_ENDPOINTS[provider]["baseUrl"]
            connection_type = "anthropic" if provider == "anthropic" else "openai"
            desired["anthropic" if connection_type == "anthropic" else "openai"].append(
                _connection(
                    f"dw-{provider}",
                    url,
                    key,
                    connection_type,
                    "openai",
                )
            )
    return desired


def emit_env(desired=None, masked=False):
    desired = desired or compute_desired_state()
    entries = desired.get("openai", []) + desired.get("anthropic", [])
    keys = [mask_secret(item["key"]) if masked else item["key"] for item in entries]
    return "\n".join(
        [
            f"OPENAI_API_BASE_URLS={json.dumps([item['url'] for item in entries])}",
            f"OPENAI_API_KEYS={json.dumps(keys, ensure_ascii=False)}",
            f"OPENAI_API_CONFIGS={json.dumps([item['config'] for item in entries], sort_keys=True)}",
            f"OLLAMA_BASE_URLS={';'.join(item['url'] for item in desired.get('ollama', []))}",
            f"OLLAMA_API_CONFIGS={json.dumps([item['config'] for item in desired.get('ollama', [])], sort_keys=True)}",
        ]
    )


class OpenWebUIError(RuntimeError):
    pass


class ReachableError(OpenWebUIError):
    pass


class AuthError(OpenWebUIError):
    pass


class OpenWebUIClient:
    """Thin adapter for the verified Open WebUI 0.11.4 configuration API."""

    OPENAI_CONFIG_PATH = "/openai/config"
    OLLAMA_CONFIG_PATH = "/ollama/config"
    OPENAI_UPDATE_PATH = "/openai/config/update"
    OLLAMA_UPDATE_PATH = "/ollama/config/update"

    def __init__(self, base_url, api_key, timeout=10):
        self.base_url, self.api_key, self.timeout = (
            base_url.rstrip("/"),
            api_key,
            timeout,
        )

    def _request(self, path, method="GET", payload=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as error:
            error_type = AuthError if error.code in {401, 403} else ReachableError
            raise error_type(f"Open WebUI API HTTP {error.code}") from error
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
            raise ReachableError(f"Open WebUI API unavailable: {error}") from error

    def health_check(self):
        try:
            self._request("/api/version")
            return True
        except OpenWebUIError:
            return False

    def get_openai_config(self):
        return self._request(self.OPENAI_CONFIG_PATH)

    def get_ollama_config(self):
        return self._request(self.OLLAMA_CONFIG_PATH)

    def update_openai_config(self, config):
        return self._request(self.OPENAI_UPDATE_PATH, "POST", config)

    def update_ollama_config(self, config):
        return self._request(self.OLLAMA_UPDATE_PATH, "POST", config)

    @staticmethod
    def normalize(response, collection):
        if isinstance(response, list):
            if not all(isinstance(item, dict) for item in response):
                raise OpenWebUIError("Open WebUI returned a non-object connection")
            return response, response
        if not isinstance(response, dict):
            raise OpenWebUIError(
                "Open WebUI returned an unexpected configuration shape"
            )
        for key in ("connections", "data", "items"):
            if isinstance(response.get(key), list):
                if not all(isinstance(item, dict) for item in response[key]):
                    raise OpenWebUIError("Open WebUI returned a non-object connection")
                return response[key], copy.deepcopy(response)
        if (
            collection == "openai"
            and {
                "OPENAI_API_BASE_URLS",
                "OPENAI_API_KEYS",
                "OPENAI_API_CONFIGS",
            }
            <= response.keys()
        ):
            urls = response["OPENAI_API_BASE_URLS"]
            keys = response["OPENAI_API_KEYS"]
            configs = response["OPENAI_API_CONFIGS"]
            if (
                not isinstance(urls, list)
                or not isinstance(keys, list)
                or not isinstance(configs, dict)
            ):
                raise OpenWebUIError(
                    "Open WebUI OpenAI configuration has invalid field types"
                )
            return [
                {
                    "url": url,
                    "key": keys[index] if index < len(keys) else "",
                    "config": copy.deepcopy(
                        configs.get(str(index), configs.get(url, {}))
                    ),
                }
                for index, url in enumerate(urls)
            ], copy.deepcopy(response)
        if (
            collection == "ollama"
            and {
                "OLLAMA_BASE_URLS",
                "OLLAMA_API_CONFIGS",
            }
            <= response.keys()
        ):
            urls = response["OLLAMA_BASE_URLS"]
            configs = response["OLLAMA_API_CONFIGS"]
            if not isinstance(urls, list) or not isinstance(configs, dict):
                raise OpenWebUIError(
                    "Open WebUI Ollama configuration has invalid field types"
                )
            entries = []
            for index, url in enumerate(urls):
                config = copy.deepcopy(configs.get(str(index), configs.get(url, {})))
                entries.append(
                    {"url": url, "key": config.pop("key", ""), "config": config}
                )
            return entries, copy.deepcopy(response)
        if not response:
            return [], copy.deepcopy(response)
        raise OpenWebUIError("Open WebUI response is missing a recognized envelope")

    @staticmethod
    def payload(envelope, entries):
        if isinstance(envelope, dict):
            if "OPENAI_API_BASE_URLS" in envelope:
                result = copy.deepcopy(envelope)
                result["OPENAI_API_BASE_URLS"] = [entry["url"] for entry in entries]
                result["OPENAI_API_KEYS"] = [entry.get("key", "") for entry in entries]
                result["OPENAI_API_CONFIGS"] = {
                    str(index): copy.deepcopy(entry.get("config", {}))
                    for index, entry in enumerate(entries)
                }
                return result
            if "OLLAMA_BASE_URLS" in envelope:
                result = copy.deepcopy(envelope)
                result["OLLAMA_BASE_URLS"] = [entry["url"] for entry in entries]
                result["OLLAMA_API_CONFIGS"] = {}
                for index, entry in enumerate(entries):
                    config = copy.deepcopy(entry.get("config", {}))
                    if entry.get("key"):
                        config["key"] = entry["key"]
                    result["OLLAMA_API_CONFIGS"][str(index)] = config
                return result
            result = copy.deepcopy(envelope)
            key = next(
                (key for key in ("connections", "data", "items") if key in result),
                "connections",
            )
            result[key] = entries
            return result
        return entries


@dataclass
class ReconcilePlan:
    entries: list = field(default_factory=list)


@dataclass
class ReconcileResult:
    plan: ReconcilePlan
    collisions: list = field(default_factory=list)
    status: str = "clean"
    snapshots: dict = field(default_factory=dict)

    def summary(self):
        counts = {}
        for item in self.plan.entries:
            counts[item["action"]] = counts.get(item["action"], 0) + 1
        detail = (
            ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            or "no-op"
        )
        return f"Open WebUI reconciliation: {self.status}; {detail}; collisions={len(self.collisions)}"


def _normal(current, collection):
    return OpenWebUIClient.normalize(current, collection)


def _equal(current, wanted):
    return (
        current.get("url") == wanted.get("url")
        and current.get("key", "") == wanted.get("key", "")
        and current.get("config", {}) == wanted.get("config", {})
    )


def reconcile(
    current_openai, current_ollama, desired, *, namespace=MANAGED_PREFIX_NAMESPACE
):
    proxy_url = next(
        (
            item["url"]
            for item in desired.get("openai", [])
            if item["config"].get("prefix_id") == "dw-ollama-cloud"
        ),
        None,
    )
    catalogue = ownership_catalogue(proxy_url)
    desired_entries = (
        desired.get("openai", [])
        + desired.get("anthropic", [])
        + desired.get("ollama", [])
    )
    desired_by_prefix = {_identity(item): item for item in desired_entries}
    current = []
    snapshots = {}
    for collection, source in (("openai", current_openai), ("ollama", current_ollama)):
        entries, envelope = _normal(source, collection)
        snapshots[collection] = {
            "entries": copy.deepcopy(entries),
            "envelope": envelope,
        }
        current.extend((collection, item) for item in entries)
    plan = ReconcilePlan()
    collisions = []
    seen = set()
    for collection, existing in current:
        config = existing.get("config", {})
        prefix = _identity(existing)
        wanted = desired_by_prefix.get(prefix)
        owner = catalogue.get(prefix)
        expected_type = owner and owner["connection_type"] == config.get(
            "connection_type"
        )
        expected_url = owner and existing.get("url") in owner.get(
            "urls", {owner["url"]}
        )
        expected_collection = owner and owner["collection"] == collection
        if not prefix.startswith(namespace):
            plan.entries.append(
                {
                    "action": "keep",
                    "collection": collection,
                    "entry": copy.deepcopy(existing),
                }
            )
        elif (
            not owner
            or not expected_type
            or not expected_url
            or not expected_collection
        ):
            collisions.append(copy.deepcopy(existing))
            plan.entries.append(
                {
                    "action": "skip",
                    "collection": collection,
                    "entry": copy.deepcopy(existing),
                }
            )
        elif wanted is None:
            plan.entries.append(
                {
                    "action": "delete",
                    "collection": collection,
                    "entry": copy.deepcopy(existing),
                }
            )
        elif _equal(existing, wanted):
            seen.add(prefix)
            plan.entries.append(
                {
                    "action": "keep",
                    "collection": collection,
                    "entry": copy.deepcopy(existing),
                }
            )
        else:
            seen.add(prefix)
            plan.entries.append(
                {
                    "action": "update",
                    "collection": collection,
                    "entry": copy.deepcopy(wanted),
                    "before": copy.deepcopy(existing),
                }
            )
    for prefix, wanted in desired_by_prefix.items():
        if prefix not in seen and not any(
            _identity(item) == prefix for _, item in current
        ):
            plan.entries.append(
                {
                    "action": "add",
                    "collection": wanted["collection"],
                    "entry": copy.deepcopy(wanted),
                }
            )
    return ReconcileResult(
        plan, collisions, "collision" if collisions else "clean", snapshots
    )


def _identity(entry):
    config = entry.get("config", {}) if isinstance(entry, dict) else {}
    return config.get("prefix_id", "") or ""


def _serialized(entry):
    result = copy.deepcopy(entry)
    result.pop("collection", None)
    return result


def reconcile_via_api(client, desired):
    initial_openai, initial_ollama = (
        client.get_openai_config(),
        client.get_ollama_config(),
    )
    result = reconcile(initial_openai, initial_ollama, desired)
    if result.status == "collision":
        return result
    before_openai, before_ollama = (
        client.get_openai_config(),
        client.get_ollama_config(),
    )
    if before_openai != initial_openai or before_ollama != initial_ollama:
        raise OpenWebUIError("Open WebUI snapshot changed before write; aborting")
    changed = {
        item["collection"]
        for item in result.plan.entries
        if item["action"] in {"add", "update", "delete"}
    }
    for collection in changed:
        entries = [
            (
                _serialized(item["entry"])
                if item["action"] in {"add", "update"}
                else copy.deepcopy(item["entry"])
            )
            for item in result.plan.entries
            if item["collection"] == collection and item["action"] != "delete"
        ]
        envelope = result.snapshots[collection]["envelope"]
        payload = OpenWebUIClient.payload(envelope, entries)
        if collection == "openai":
            client.update_openai_config(payload)
        else:
            client.update_ollama_config(payload)
    if changed:
        before_unmanaged = {
            collection: [
                copy.deepcopy(item)
                for item in result.snapshots[collection]["entries"]
                if not _identity(item).startswith(MANAGED_PREFIX_NAMESPACE)
            ]
            for collection in result.snapshots
        }
        verified = reconcile(
            client.get_openai_config(), client.get_ollama_config(), desired
        )
        after_unmanaged = {
            collection: [
                copy.deepcopy(item)
                for item in verified.snapshots[collection]["entries"]
                if not _identity(item).startswith(MANAGED_PREFIX_NAMESPACE)
            ]
            for collection in verified.snapshots
        }
        if (
            verified.status != "clean"
            or any(item["action"] != "keep" for item in verified.plan.entries)
            or before_unmanaged != after_unmanaged
        ):
            raise OpenWebUIError(
                "Open WebUI write verification failed or unmanaged state changed"
            )
    return result
