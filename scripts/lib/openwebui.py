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
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import constants
from selected_service import (
    DirectRoute,
    Operation,
    Qualification,
    ServiceLocator,
    Transport,
    assert_renders_from,
    resolve_transport,
    select,
)
from ai_models import is_known_unqualified_tts
from local_engines import (
    LOCAL_ENGINES,
    active_engines,
    audio_models,
    iter_engine_discovery,
    local_endpoint_for,
)
from provider_endpoints import PROVIDER_ENDPOINTS

MANAGED_PREFIX_NAMESPACE = "dw-"
MANAGED_MARKER_KEY = "managed_by"
MANAGED_MARKER_VALUE = "dotfiles"
DISABLED_ENGINES_ENV = "DOTFILES_OPENWEBUI_DISABLED_ENGINES"
STOCK_OPENAI_URL = "https://api.openai.com/v1"
STOCK_OPENAI_PREFIXES = ("", "openai")
AUDIO_OWNED_FIELDS = {
    "stt": (
        "ENGINE",
        "OPENAI_API_REQUEST_FORMAT",
        "OPENAI_API_BASE_URL",
        "MODEL",
        "OPENAI_API_KEY",
    ),
    "tts": ("ENGINE", "OPENAI_API_BASE_URL", "MODEL", "VOICE", "OPENAI_API_KEY"),
}
AUDIO_STATE_PATH = "~/.local/share/openwebui/data/managed-audio.json"
EMBEDDING_STATE_PATH = "~/.local/share/openwebui/data/managed-embedding.json"
EMBEDDING_MODEL = "embeddinggemma-2:740m-mxfp8"
EMBEDDING_OUTPUT_DIMS = 768
EMBEDDING_OLLAMA_URL = constants.get_ollama_local_base_url()
LITELLM_OPENWEBUI_KEY_PATH = "~/.local/share/litellm/clients/openwebui.key"


def _litellm_key_value():
    """Read the scoped WebUI proxy key only for a runtime probe/render."""
    import stat
    from pathlib import Path

    path = Path(LITELLM_OPENWEBUI_KEY_PATH).expanduser()
    try:
        if any(
            candidate.is_symlink()
            for candidate in (path.parent.parent, path.parent, path)
        ):
            return None
        metadata = path.stat()
        directory = path.parent.stat()
        if (
            not path.is_file()
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) & 0o077
        ):
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _litellm_endpoint():
    from model_catalogues import _configured_litellm_port

    return f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"


def _gateway_embedding_alias(fetch=None, endpoint=None, key_value=None):
    """Resolve the served namespaced alias for the managed embedding model.

    Reads the REAL served gateway inventory once per render; the alias must
    exist as ``ollama/<EMBEDDING_MODEL>`` (the canonical namespaced alias every
    modality emits). A guessed alias cannot establish qualification, so an
    unresolvable inventory or a missing alias returns None and the caller
    degrades to the honest unavailable/preserve record. Values are never
    persisted; the key value is a wire-time read only.
    """
    from route_probes import fetch_gateway_models

    resolved_endpoint = endpoint if endpoint is not None else _litellm_endpoint()
    resolved_key = key_value if key_value is not None else _litellm_key_value()
    fetch = fetch or fetch_gateway_models
    inventory = fetch(resolved_endpoint, resolved_key)
    if not inventory:
        return None
    candidate = f"ollama/{EMBEDDING_MODEL}"
    if candidate in inventory:
        return candidate
    return None


def _ollama_native_base(openai_url):
    """Derive WebUI's native Ollama base from an OpenAI-compatible URL.

    WebUI's native ``ollama`` embedding engine appends ``/api/embed`` to the
    configured URL, so the value written into ``ollama_config.url`` must be
    the bare origin; the OpenAI-compatible ``/v1`` endpoint stays the probe
    and route fact. Deriving (never free-constructing) keeps the render
    invariant.
    """
    return openai_url[:-3] if openai_url.endswith("/v1") else openai_url


EMBEDDING_DEFAULTS = {
    "RAG_EMBEDDING_ENGINE": "",
    "RAG_EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
    "ollama_config": {"url": EMBEDDING_OLLAMA_URL},
}
SELECTION_PROVENANCE_KEY = "selection"
AUDIO_DEFAULTS = {
    "stt": {
        "ENGINE": "",
        "OPENAI_API_REQUEST_FORMAT": "multipart",
        "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
        "MODEL": "",
        "OPENAI_API_KEY": "",
    },
    "tts": {
        "ENGINE": "",
        "OPENAI_API_BASE_URL": "https://api.openai.com/v1",
        "MODEL": "",
        "VOICE": "",
        "OPENAI_API_KEY": "",
    },
}


def _litellm_client_key(env_name):
    """Read the Open WebUI key file; raise RuntimeError if missing or empty."""
    path = os.path.expanduser("~/.local/share/litellm/clients/openwebui.key")
    try:
        value = open(path, encoding="utf-8").read().strip()
    except OSError:
        value = ""
    if not value:
        raise RuntimeError(f"Open WebUI LiteLLM key file is unavailable: {path}")
    return value


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


def _canonicalize_host(url):
    """Use the stable loopback address for locally generated endpoints."""
    parts = urlsplit(url)
    if parts.hostname != "localhost":
        return url
    host = "127.0.0.1"
    if parts.username or parts.password:
        auth = parts.username or ""
        if parts.password is not None:
            auth += f":{parts.password}"
        host = f"{auth}@{host}"
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


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
            MANAGED_MARKER_KEY: MANAGED_MARKER_VALUE,
        },
        "collection": collection,
    }


def ownership_catalogue(proxy_mode=None):
    """Return stable prefix → expected endpoint/type ownership identities."""
    catalogue = {}
    for provider in LOCAL_ENGINES:
        if provider == "ollama":
            catalogue["ollama"] = {
                "url": _canonicalize_host(_root(LOCAL_ENGINES[provider]["base_url"]())),
                "connection_type": "ollama",
                "collection": "ollama",
            }
            continue
        for protocol in ("openai",):
            endpoint = _registry_endpoint(provider, protocol)
            if endpoint:
                catalogue[provider] = {
                    "url": _canonicalize_host(endpoint[0]),
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
        url = _canonicalize_host(url)
        catalogue[provider] = {
            "url": url,
            "urls": {url},
            "connection_type": connection_type,
            "collection": "openai",
        }
    enforce_proxy_prerequisites = proxy_mode is None
    proxy_mode = (
        constants.get_litellm_proxy_mode() if proxy_mode is None else proxy_mode
    )
    if proxy_mode:
        if (
            enforce_proxy_prerequisites
            and os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") != "1"
        ):
            raise RuntimeError(
                "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
            )
        from model_catalogues import _configured_litellm_port

        endpoint = f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
        # The single gateway connection is itself a catalogue-owned identity so
        # the reconciler keeps/updates/prunes it like every other managed entry.
        catalogue["litellm"] = {
            "url": endpoint,
            "urls": {endpoint},
            "connection_type": "openai",
            "collection": "openai",
        }
        for identity in catalogue.values():
            identity["url"] = endpoint
            identity["urls"] = {endpoint}
            identity["connection_type"] = "openai"
            identity["collection"] = "openai"
    return catalogue


def compute_desired_state(probe_routes=False, allow_auto_probe=False, read_only=False):
    """Build desired entries; inactive/keyless providers remain ownership-known.

    ``read_only`` marks the diagnostic (--check/--dry-run) context: gateway
    sections render their honest unresolved projection instead of resolving
    credentials, and the apply path keeps the fail-closed RuntimeError.
    """
    desired: dict[str, Any] = {"openai": [], "anthropic": [], "ollama": []}
    discovery = iter_engine_discovery("omlx")
    disabled = _disabled()
    for provider in active_engines():
        if provider.lower() in disabled:
            continue
        if provider == "ollama":
            desired["ollama"].append(
                _connection(
                    "ollama",
                    _canonicalize_host(_root(constants.get_ollama_local_base_url())),
                    "",
                    "ollama",
                    "ollama",
                )
            )
            continue
        for protocol in ("openai",):
            endpoint = local_endpoint_for(provider, protocol)
            if endpoint:
                url, key_env = endpoint
                desired[protocol].append(
                    _connection(
                        provider,
                        _canonicalize_host(url),
                        os.environ.get(key_env, "") if key_env else "",
                        protocol,
                        "openai",
                    )
                )
    if constants.is_meridian_configured():
        desired["anthropic"].append(
            _connection(
                "meridian",
                _canonicalize_host(constants.get_meridian_base_url()),
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
                url = constants.BASE_URLS["ollama-cloud"]
            else:
                url = PROVIDER_ENDPOINTS[provider]["baseUrl"]
            connection_type = "anthropic" if provider == "anthropic" else "openai"
            desired["anthropic" if connection_type == "anthropic" else "openai"].append(
                _connection(
                    provider,
                    _canonicalize_host(url),
                    key,
                    connection_type,
                    "openai",
                )
            )
    if constants.get_litellm_proxy_mode():
        if os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") != "1":
            raise RuntimeError(
                "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
            )
        from model_catalogues import _configured_litellm_port

        if read_only:
            # Diagnostic modes never resolve credentials (P1.2): the managed
            # gateway connection is not rendered rather than guessing a key;
            # the apply path keeps the fail-closed RuntimeError below.
            import logger

            logger.warning(
                "Gateway mode in a read-only diagnostic: the proxy client key "
                "is unresolved, so the managed connection state is not rendered"
            )
        else:
            # Fail-closed: gateway compute requires the materialised client key
            # (never the LiteLLM master key; RuntimeError on missing/empty —
            # guard test test_litellm_proxy_requires_openwebui_key_file...).
            # Test fixtures patch _litellm_client_key for keyless runners.
            client_key = _litellm_client_key("LITELLM_OPENWEBUI_KEY")
            # Gateway-only mode: ONE managed connection covers every proxy model.
            # Per-provider connections would each re-export the entire LiteLLM
            # catalogue under its own prefix (the 540-models listing problem), so
            # the desired state collapses to a single owned "litellm" identity.
            endpoint = f"http://127.0.0.1:{_configured_litellm_port() or 4000}/v1"
            desired = {
                "openai": [
                    _connection(
                        "litellm",
                        endpoint,
                        client_key,
                        "openai",
                        "openai",
                    )
                ],
                "anthropic": [],
                "ollama": [],
            }
    audio_selection = {}
    audio = _desired_audio_settings(
        probe_routes=probe_routes,
        allow_auto_probe=allow_auto_probe,
        selection_records=audio_selection,
    )
    explicitly_disabled = (
        os.environ.get("DOTFILES_RUN_OMLX_SETUP") == "0" or "omlx" in disabled
    )
    if explicitly_disabled:
        audio = None
        desired["audio_status"] = "disabled"
    elif discovery.status == "unknown_error":
        import logger

        logger.warning(
            "oMLX discovery is unknown; preserving managed Open WebUI settings"
        )
        audio = None
        # The reconciler cannot distinguish an unknown backend from an
        # explicitly disabled engine by the mere absence of the audio bundle;
        # carry the reason so unknown/empty preserve while disabled releases.
        desired["audio_status"] = "unknown"
    elif discovery.status == "ok_empty" and _read_audio_projection() is not None:
        import logger

        logger.warning(
            "oMLX discovery is empty; preserving managed Open WebUI settings"
        )
        audio = None
        desired["audio_status"] = "empty"
    if audio is not None:
        desired["audio"] = audio["settings"]
        desired["audio_selection"] = audio["selection"]
    if audio_selection:
        if "_unavailable" in audio_selection:
            unavailable = audio_selection["_unavailable"]
            unavailable.update(audio_selection.get("_evidence", {}))
            desired["audio_selection"] = unavailable
        else:
            desired["audio_selection"] = audio_selection
    if audio is None and not explicitly_disabled and discovery.status == "ok_models":
        desired["audio_status"] = "unavailable"
    embedding_selection = {}
    embedding = _desired_embedding_settings(
        probe_routes=probe_routes,
        allow_auto_probe=allow_auto_probe,
        selection_records=embedding_selection,
    )
    if embedding is not None:
        selection = embedding.pop(SELECTION_PROVENANCE_KEY)
        desired["embedding"] = embedding
        desired["embedding_selection"] = selection
        embedding_selection = selection
    elif embedding_selection:
        unavailable = embedding_selection.get("_unavailable", embedding_selection)
        unavailable.update(embedding_selection.get("_evidence", {}))
        desired["embedding_selection"] = unavailable
    if embedding is None:
        desired["embedding_status"] = "unavailable"
    return desired


_ROUTE_BUILD_CACHE = {}


def _route_engine_build(provider, base_url=None):
    """Return an authoritative running/local engine version, cached per run.

    The build is a property of the daemon/install for the whole provider, so
    provider-only caching is deliberate (endpoint independence); endpoint is
    bound separately in the route-evidence record.

    oMLX has no stable version endpoint. Its installed package version is the
    available build identity; an in-place upgrade before restart may differ
    from the still-running process, so endpoint changes remain independently
    bound.
    """
    if provider in _ROUTE_BUILD_CACHE:
        return _ROUTE_BUILD_CACHE[provider]
    build = None
    if provider == "ollama":
        try:
            version_url = (base_url or constants.get_ollama_local_base_url()).rstrip(
                "/"
            )
            root = version_url[:-3] if version_url.endswith("/v1") else version_url
            if not root.startswith(("http://", "https://")):
                build = None
            else:
                from urllib.request import Request, urlopen

                request = Request(f"{root}/api/version", method="GET")
                with urlopen(
                    request, timeout=3
                ) as response:  # nosec B310 - scheme-checked above
                    payload = json.loads(response.read().decode("utf-8"))
                build = payload.get("version") if isinstance(payload, dict) else None
        except Exception:
            build = None
    elif provider == "omlx":
        import subprocess  # nosec B404 - oMLX build identity via brew metadata only

        try:
            completed = (
                subprocess.run(  # nosec B603 B607 - brew metadata query; constant args
                    ["brew", "list", "--versions", "omlx"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
            )
            fields = completed.stdout.strip().split()
            build = fields[-1] if len(fields) >= 2 else None
        except (OSError, subprocess.SubprocessError):
            build = None
    _ROUTE_BUILD_CACHE[provider] = build
    return build


def _route_binding(
    provider,
    model,
    operation,
    endpoint,
    key_reference,
    engine_build,
    credential_digest=None,
):
    return {
        "provider": provider,
        "model": model,
        "operation": operation.value,
        "url": endpoint,
        "credential_reference": key_reference,
        "credential_digest": credential_digest,
        "engine_build": engine_build,
    }


# Persisted route evidence older than this is stale: reuse never authorizes a
# qualification without a fresh, build-known, credential-bound binding.
_SELECTION_REUSE_MAX_AGE = timedelta(hours=24)


def _credential_digest(key_value):
    """Non-secret credential-generation fingerprint (16-hex prefix, or None)."""
    if not key_value:
        return None
    return hashlib.sha256(key_value.encode("utf-8")).hexdigest()[:16]


def _selection_record_fresh(record):
    """True when the record was probed recently enough to reuse.

    Missing or malformed timestamps refuse reuse (conservative legacy
    behavior); timestamps in the future beyond a small clock-skew allowance
    also refuse.
    """
    recorded = record.get("recorded_at")
    if not recorded:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            str(recorded).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return False
    return age >= timedelta(0) and age <= _SELECTION_REUSE_MAX_AGE


def _qualify_candidates(
    candidates,
    operation,
    endpoint,
    key_reference,
    probe,
    *,
    full_probe=False,
    probe_on_missing=False,
    key_value=None,
    **probe_options,
):
    """Reuse exact route-bound evidence; probe selected stale/missing routes.

    ``full_probe`` is the explicit --probe-routes opt-in (probe every
    candidate). ``probe_on_missing`` is the healthy-apply auto-probe: probe a
    candidate only when it lacks valid persisted route evidence. With both
    false (read-only modes), a missing/stale binding keeps the candidate's
    discovered qualification and records that it was not probed in this mode.
    """
    from route_probes import qualification_from_probe

    saved = _read_audio_selection() or {}
    active = _read_embedding_state().get("selection") or {}
    previous_by_identity = {}
    if isinstance(saved, dict):
        previous_by_identity.update(
            {
                (entry.get("provider"), entry.get("model")): entry
                for entry in saved.values()
                if isinstance(entry, dict)
            }
        )
    if isinstance(active, dict) and active.get("operation"):
        previous_by_identity[(active.get("provider"), active.get("model"))] = active

    qualified, reasons = [], {}
    for provider, model, discovered in candidates:
        engine_endpoint = endpoint
        should_probe = full_probe or probe_on_missing
        build = _route_engine_build(provider, engine_endpoint) if should_probe else None
        resolved_key_value = key_value
        if should_probe and resolved_key_value is None and key_reference:
            # Direct-mode probes resolve the referenced environment value;
            # empty strings count as absent for credential probing.
            resolved = os.environ.get(key_reference)
            resolved_key_value = resolved if resolved else None
        # Non-secret credential-generation proof: reuse must survive
        # rotations only when the digest (and everything else in the
        # binding) is byte-identical; the value itself is never stored.
        binding = _route_binding(
            provider,
            model,
            operation,
            engine_endpoint,
            key_reference,
            build,
            credential_digest=(
                _credential_digest(resolved_key_value) if should_probe else None
            ),
        )
        prior = previous_by_identity.get((provider, model), {})
        evidence = prior.get("route_evidence")
        if (
            not full_probe
            and _selection_record_fresh(prior)
            and (build is not None or not should_probe)
            and evidence == binding
            and prior.get("qualification")
            in {
                Qualification.PROTOCOL_PROBED.value,
                Qualification.CONSUMER_VERIFIED.value,
            }
        ):
            qualification = Qualification(prior["qualification"])
            reason = prior.get("reason", "persisted route evidence reused")
            # Reused evidence keeps its record minus the reason override: the
            # locator's select-reason stays authoritative for the consumer, and
            # the measured width + fresh binding ride through for enforcement.
            reasons[(provider, model)] = {
                "route_evidence": evidence,
                "recorded_at": prior.get("recorded_at"),
                "output_dims": prior.get("output_dims"),
            }
        elif full_probe or probe_on_missing:
            result = probe(
                engine_endpoint,
                model,
                key_reference=key_reference,
                key_value=resolved_key_value,
                **probe_options,
            )
            qualification = qualification_from_probe(result)
            reason = "; ".join(result.notes) or result.status
            http_status = getattr(result, "http_status", None)
            if operation == Operation.EMBEDDING and http_status is not None:
                if http_status in (401, 403):
                    classification = "authentication rejected"
                elif http_status == 404:
                    classification = "model not found"
                elif http_status == 429:
                    classification = "rate limited"
                elif 500 <= http_status <= 599:
                    classification = "server-side error"
                else:
                    classification = "request rejected"
                reason = (
                    f"gateway returned HTTP {http_status} ({classification}); "
                    f"{reason}"
                )
            reason_record = {"reason": reason}
            if qualification in {
                Qualification.PROTOCOL_PROBED,
                Qualification.CONSUMER_VERIFIED,
            }:
                reason_record["route_evidence"] = binding
                # Freshness proof recorded with the evidence: reuse decisions
                # are bounded by _selection_record_fresh.
                reason_record["recorded_at"] = datetime.now(timezone.utc).isoformat()
                if getattr(result, "dimension", None):
                    # Embedding probes carry the served representation width;
                    # gateway transitions need it for the dimension gates.
                    reason_record["output_dims"] = result.dimension
            reasons[(provider, model)] = reason_record
        else:
            qualification = discovered
            reason = "route evidence missing or stale; not probed in read-only mode"
        qualified.append((provider, model, qualification))
    return qualified, reasons


def _served_wire_alias(provider, model, endpoint, key_value):
    """Resolve the exact served gateway alias for one audio candidate (P1.1).

    Cross-engine id collisions leave ONLY the namespaced aliases served;
    probing or rendering an unserved bare id would fail. The served
    inventory is the real source: the bare id stays when it is actually
    served, the canonical ``provider/<id>`` alias is used when only that is
    served, and an unserved candidate resolves to None so it is dropped with
    an honest unavailable outcome rather than a doomed probe. The key value
    is a wire-time read only and is never logged or persisted.
    """
    from route_probes import fetch_gateway_models

    inventory = fetch_gateway_models(endpoint, key_value)
    if inventory is None:
        # Served-alias UNKNOWN (unreachable/unauthorized/malformed): keeping
        # the bare candidate is the honest boundary — inventory evidence is
        # UNKNOWN, not a removal decision. The probe/route layer reports
        # reality from here.
        return model
    if model in inventory:
        return model
    namespaced = f"{provider}/{model}"
    if namespaced in inventory:
        return namespaced
    return None


def _desired_audio_settings(
    probe_routes=False, allow_auto_probe=False, selection_records=None
):
    """Return the owned speech settings, or None when oMLX is unavailable."""
    if "omlx" in _disabled() or "omlx" not in active_engines():
        return None
    endpoint = local_endpoint_for("omlx", "openai")
    models = audio_models("omlx")
    if not endpoint:
        return None
    selection_records = selection_records if selection_records is not None else {}
    stt_candidates = [
        ("omlx", item["name"], Qualification.DISCOVERED)
        for item in models
        if item.get("name") and item.get("model_type") == "audio_stt"
    ]
    gateway = constants.get_litellm_proxy_mode()
    gateway_endpoint = _litellm_endpoint() if gateway else None
    gateway_key = (
        _litellm_key_value() if gateway and (probe_routes or allow_auto_probe) else None
    )
    key_reference = LITELLM_OPENWEBUI_KEY_PATH if gateway else endpoint[1]
    if gateway and gateway_key:
        # P1.1 canonical-alias resolution: cross-engine id collisions leave
        # ONLY namespaced aliases served, and probing/rendering an unserved
        # bare id would fail. Resolve every candidate against the SERVED
        # inventory; unserved candidates are dropped with an honest note.
        # Read-only modes (no key) keep the bare render and their honest
        # not-probed/unresolved notes.
        stt_candidates = [
            (provider, resolved, qual)
            for provider, resolved, qual in (
                (
                    candidate[0],
                    _served_wire_alias(
                        candidate[0],
                        candidate[1],
                        gateway_endpoint,
                        gateway_key,
                    ),
                    candidate[2],
                )
                for candidate in stt_candidates
            )
            if resolved is not None
        ]
    from route_probes import probe_stt

    stt_candidates, audio_probe_reasons = _qualify_candidates(
        stt_candidates,
        Operation.STT,
        gateway_endpoint or endpoint[0],
        key_reference,
        probe_stt,
        full_probe=probe_routes,
        probe_on_missing=probe_routes or allow_auto_probe,
        key_value=gateway_key if gateway else None,
    )
    locator_options = (
        {"gateway_route": (gateway_endpoint, key_reference, gateway_key)}
        if gateway and (probe_routes or allow_auto_probe)
        else {}
    )
    stt = _audio_locator(
        Operation.STT, stt_candidates, endpoint, selection_records, **locator_options
    )
    if stt is None:
        if selection_records and "_unavailable" in selection_records:
            selection_records.update(selection_records["_unavailable"])
        import logger

        logger.warning("No qualified oMLX STT model; omitting audio settings")
        return None
    settings = {
        "stt": {
            "ENGINE": "openai",
            "OPENAI_API_BASE_URL": stt.route.endpoint,
            "OPENAI_API_REQUEST_FORMAT": "multipart",
            "MODEL": stt.route.wire_model,
            "OPENAI_API_KEY": (
                gateway_key if gateway else os.environ.get("OMLX_API_KEY")
            )
            or None,
        }
    }
    selections = {"stt": _locator_provenance(stt)}
    stt_evidence = audio_probe_reasons.get(
        (stt.selection.provider, stt.selection.model), {}
    )
    if stt_evidence:
        selections["stt"].update(stt_evidence)
    tts_candidates = [
        ("omlx", item["name"], Qualification.DISCOVERED)
        for item in models
        if item.get("name")
        and item.get("model_type") == "audio_tts"
        and not is_known_unqualified_tts(item.get("name"))
    ]
    if gateway and gateway_key:
        # Same P1.1 canonical-alias resolution as the STT family.
        tts_candidates = [
            (provider, resolved, qual)
            for provider, resolved, qual in (
                (
                    candidate[0],
                    _served_wire_alias(
                        candidate[0],
                        candidate[1],
                        gateway_endpoint,
                        gateway_key,
                    ),
                    candidate[2],
                )
                for candidate in tts_candidates
            )
            if resolved is not None
        ]
    from route_probes import probe_tts

    tts_candidates, tts_reasons = _qualify_candidates(
        tts_candidates,
        Operation.TTS,
        gateway_endpoint or endpoint[0],
        key_reference,
        probe_tts,
        full_probe=probe_routes,
        probe_on_missing=probe_routes or allow_auto_probe,
        key_value=gateway_key if gateway else None,
        voice=os.environ.get("DOTFILES_VOICE_TTS_VOICE", "serena"),
    )
    audio_probe_reasons.update(tts_reasons)
    tts = _audio_locator(
        Operation.TTS, tts_candidates, endpoint, selection_records, **locator_options
    )
    if tts is not None:
        tts_model = tts.route.wire_model
        tts_settings = {
            "ENGINE": "openai",
            "OPENAI_API_BASE_URL": tts.route.endpoint,
            "MODEL": tts_model,
            "OPENAI_API_KEY": (
                gateway_key if gateway else os.environ.get("OMLX_API_KEY")
            )
            or None,
        }
        voice = os.environ.get("DOTFILES_VOICE_TTS_VOICE", "serena")
        if tts_model and tts_model.lower().split("/", 1)[-1].startswith("qwen3-tts"):
            tts_settings["VOICE"] = voice
        settings["tts"] = tts_settings
        selections["tts"] = _locator_provenance(tts)
        tts_evidence = audio_probe_reasons.get(
            (tts.selection.provider, tts.selection.model), {}
        )
        if tts_evidence:
            selections["tts"].update(tts_evidence)
    else:
        import logger

        logger.warning(
            "No qualified oMLX TTS model found; omitting Open WebUI TTS settings"
        )
    return {"settings": settings, "selection": selections}


def _audio_locator(
    operation, candidates, endpoint, selection_records=None, gateway_route=None
):
    preference = []
    previous = _read_audio_selection()
    previous = previous.get(operation.value) if isinstance(previous, dict) else None
    previous_tuple = None
    if (
        isinstance(previous, dict)
        and previous.get("provider")
        and previous.get("model")
    ):
        previous_tuple = (
            str(previous.get("provider")),
            str(previous.get("model")),
            Qualification(previous.get("qualification", "unknown")),
        )
    if previous_tuple is None:
        projection = _read_audio_projection() or {}
        owned = projection.get("stt" if operation == Operation.STT else "tts", {})
        prior_model = owned.get("MODEL")
        if prior_model and candidates:
            previous_tuple = (candidates[0][0], prior_model, Qualification.UNKNOWN)
    selection_records = selection_records if selection_records is not None else {}
    selected = select(
        operation, candidates, preference, previously_selected=previous_tuple
    )
    gateway = constants.get_litellm_proxy_mode()
    if gateway and gateway_route is None:
        selection = select(
            operation,
            candidates,
            preference=preference,
            previously_selected=previous_tuple,
        )
        selection_record = {
            operation.value: {
                "operation": operation.value,
                "provider": selection.provider,
                "model": selection.model,
                "qualification": Qualification.UNAVAILABLE.value,
                "reason": selection.reason,
                "candidate_qualification": selection.qualification.value,
                "transport": Transport.UNAVAILABLE.value,
                "endpoint": None,
                "wire_model": None,
                "key_reference": endpoint[1],
            }
        }
        if selection_records is not None:
            selection_records["_unavailable"] = selection_record
        import logger

        logger.warning(
            "Open WebUI %s gateway route unavailable: no verified proxy model mapping",
            operation.value,
        )
        return None
    if gateway:
        if gateway_route is None:
            return None
        gateway_endpoint, key_reference, key_value = gateway_route
        route = resolve_transport(
            "gateway",
            selected.qualification,
            endpoint=gateway_endpoint,
            wire_model=selected.model,
            key_reference=key_reference,
            credential_present=bool(key_value),
            credential_required=True,
            operation=operation,
        )
        locator = ServiceLocator(selected, route)
        if selected.provider is None or route.transport == Transport.UNAVAILABLE:
            return None
        return locator
    route = resolve_transport(
        "direct",
        selected.qualification,
        endpoint=endpoint[0],
        wire_model=selected.model,
        key_reference=endpoint[1],
        credential_present=not endpoint[1] or bool(os.environ.get(endpoint[1])),
        credential_required=False,
        direct=DirectRoute(
            endpoint=endpoint[0],
            wire_model=selected.model,
            key_reference=endpoint[1],
            credential_required=False,
        ),
        operation=operation,
    )
    locator = ServiceLocator(selected, route)
    if selected.provider is None or route.transport == Transport.UNAVAILABLE:
        selection_record = {
            operation.value: {
                "operation": operation.value,
                "provider": selected.provider,
                "model": selected.model,
                "qualification": (
                    selected.qualification.value
                    if any(
                        candidate[2] != Qualification.DISCOVERED
                        for candidate in candidates
                    )
                    else Qualification.UNAVAILABLE.value
                ),
                "reason": selected.reason,
                "candidate_qualification": selected.qualification.value,
                "transport": route.transport.value,
                "endpoint": route.endpoint,
                "wire_model": route.wire_model,
                "key_reference": route.key_reference or endpoint[1],
            }
        }
        if selection_records is not None:
            selection_records["_unavailable"] = selection_record
        import logger

        logger.warning(
            "Open WebUI %s unavailable: %s",
            operation.value,
            route.reason or selected.reason,
        )
        return None
    assert_renders_from(
        locator,
        {
            "provider": selected.provider,
            "model": selected.model,
            "transport": route.transport,
            "endpoint": route.endpoint,
            "wire_model": route.wire_model,
        },
    )
    if selection_records is not None:
        provenance = _locator_provenance(locator)
        selection_records[operation.value] = provenance
    return locator


def _locator_provenance(locator):
    selection, route = locator.selection, locator.route
    return {
        "operation": selection.operation.value,
        "provider": selection.provider,
        "model": selection.model,
        "qualification": selection.qualification.value,
        "reason": selection.reason,
        "transport": route.transport.value,
        "endpoint": route.endpoint,
        "wire_model": route.wire_model,
        "key_reference": route.key_reference,
    }


def _persist_audio_selection(selection):
    _write_audio_projection(_read_audio_projection() or {}, selection=selection)


def _persist_embedding_selection(selection):
    state = _read_embedding_state()
    state["selection"] = selection
    _write_embedding_state(state)


def _desired_embedding_settings(
    probe_routes=False, allow_auto_probe=False, selection_records=None
):
    selection_records = selection_records if selection_records is not None else {}
    if constants.get_litellm_proxy_mode():
        if not (probe_routes or allow_auto_probe):
            selection_record = {
                "operation": "embedding",
                "provider": None,
                "model": None,
                "qualification": "unknown",
                "reason": "gateway inventory not resolved in read-only mode",
                "transport": "unavailable",
                "endpoint": None,
                "wire_model": None,
                "key_reference": None,
            }
            if selection_records is not None:
                selection_records["_unavailable"] = selection_record
            import logger

            logger.warning(
                "Open WebUI embedding gateway route not resolved in read-only mode"
            )
            return None
        alias = _gateway_embedding_alias()
        if not alias:
            selection_record = {
                "operation": "embedding",
                "provider": None,
                "model": None,
                "qualification": "unavailable",
                "reason": "no verified proxy model mapping",
                "transport": "unavailable",
                "endpoint": None,
                "wire_model": None,
                "key_reference": LITELLM_OPENWEBUI_KEY_PATH,
            }
            if selection_records is not None:
                selection_records["_unavailable"] = selection_record
            import logger

            logger.warning(
                "Open WebUI embedding gateway route unavailable: "
                "no verified proxy model mapping"
            )
            return None
        gateway_endpoint = _litellm_endpoint()
        gateway_key = _litellm_key_value()
        from route_probes import probe_embeddings

        candidates, qualification_reasons = _qualify_candidates(
            [("gateway", alias, Qualification.DISCOVERED)],
            Operation.EMBEDDING,
            gateway_endpoint,
            LITELLM_OPENWEBUI_KEY_PATH,
            probe_embeddings,
            full_probe=probe_routes,
            probe_on_missing=probe_routes or allow_auto_probe,
            key_value=gateway_key,
        )
        qualification = candidates[0][2]
        evidence_record = qualification_reasons.get(("gateway", alias), {})
        probe_reason = evidence_record.get("reason")
        selection = select(
            Operation.EMBEDDING,
            [("gateway", alias, qualification)],
            preference=[("gateway", alias)],
        )
        route = resolve_transport(
            "gateway",
            selection.qualification,
            endpoint=gateway_endpoint,
            wire_model=selection.model,
            key_reference=LITELLM_OPENWEBUI_KEY_PATH,
            credential_present=bool(gateway_key),
            credential_required=True,
            operation=Operation.EMBEDDING,
        )
        if selection.provider is None or route.transport == Transport.UNAVAILABLE:
            selection_record = {
                "operation": "embedding",
                "provider": selection.provider,
                "model": selection.model,
                "qualification": (
                    Qualification.UNAVAILABLE.value
                    if qualification == Qualification.UNAVAILABLE
                    else Qualification.UNKNOWN.value
                ),
                "reason": probe_reason or selection.reason,
                "transport": route.transport.value,
                "endpoint": route.endpoint,
                "wire_model": route.wire_model,
                "key_reference": LITELLM_OPENWEBUI_KEY_PATH,
            }
            if selection_records is not None:
                selection_records["_unavailable"] = selection_record
            import logger

            logger.warning(
                "Open WebUI embedding gateway route unavailable: %s",
                probe_reason or selection.reason,
            )
            return None
        rendered = {
            "provider": selection.provider,
            "model": selection.model,
            "transport": route.transport,
            "endpoint": route.endpoint,
            "wire_model": route.wire_model,
        }
        locator = ServiceLocator(selection, route)
        assert_renders_from(locator, rendered)
        provenance = {
            **_locator_provenance(locator),
            "reason": probe_reason or selection.reason,
        }
        if evidence_record.get("route_evidence"):
            provenance["route_evidence"] = evidence_record["route_evidence"]
        if evidence_record.get("output_dims"):
            provenance["output_dims"] = evidence_record["output_dims"]
        return {
            "RAG_EMBEDDING_ENGINE": "openai",
            "RAG_EMBEDDING_MODEL": rendered["wire_model"],
            "openai_config": {"url": rendered["endpoint"]},
            # MEASURED served width (planning evidence, never an assumed
            # constant); the reconciler enforces it and the WebUI payload
            # construction strips it.
            "_served_dims": evidence_record.get("output_dims"),
            SELECTION_PROVENANCE_KEY: provenance,
        }
    previous_state = _read_embedding_state()
    projection = previous_state.get("projection", {})
    owned = projection.get("owned", {}) if isinstance(projection, dict) else {}
    previous_model = owned.get("RAG_EMBEDDING_MODEL")
    previous = (
        ("ollama", previous_model, Qualification.UNKNOWN) if previous_model else None
    )
    from route_probes import probe_embeddings

    key_reference = "OLLAMA_API_KEY" if constants.get_litellm_proxy_mode() else None
    candidates, qualification_reasons = _qualify_candidates(
        [("ollama", EMBEDDING_MODEL, Qualification.DISCOVERED)],
        Operation.EMBEDDING,
        EMBEDDING_OLLAMA_URL,
        key_reference,
        probe_embeddings,
        full_probe=probe_routes,
        probe_on_missing=probe_routes or allow_auto_probe,
        key_value=None,
    )
    if selection_records is not None:
        selection_records["_evidence"] = qualification_reasons.get(
            ("ollama", EMBEDDING_MODEL), {}
        )
    qualification = candidates[0][2]
    probe_reason = qualification_reasons.get(("ollama", EMBEDDING_MODEL), {}).get(
        "reason"
    )
    selection = select(
        Operation.EMBEDDING,
        [("ollama", EMBEDDING_MODEL, qualification)],
        preference=[("ollama", EMBEDDING_MODEL)],
        previously_selected=previous,
    )
    route = resolve_transport(
        "direct",
        selection.qualification,
        endpoint=EMBEDDING_OLLAMA_URL,
        wire_model=selection.model,
        direct=DirectRoute(endpoint=EMBEDDING_OLLAMA_URL, wire_model=selection.model),
        credential_required=False,
        operation=Operation.EMBEDDING,
    )
    locator = ServiceLocator(selection, route)
    if selection.provider is None or route.transport == Transport.UNAVAILABLE:
        selection_record = {
            "operation": "embedding",
            "provider": selection.provider,
            "model": selection.model,
            "qualification": Qualification.UNAVAILABLE.value,
            "reason": probe_reason or selection.reason,
            "transport": route.transport.value,
            "endpoint": route.endpoint,
            "wire_model": route.wire_model,
            "key_reference": route.key_reference,
        }
        if selection_records is not None:
            selection_records["_unavailable"] = selection_record
        import logger

        logger.warning(
            "Open WebUI embedding unavailable: %s", route.reason or selection.reason
        )
        return None
    rendered = {
        "provider": selection.provider,
        "model": selection.model,
        "transport": route.transport,
        "endpoint": route.endpoint,
        "wire_model": route.wire_model,
    }
    assert_renders_from(locator, rendered)
    provenance = {
        **_locator_provenance(locator),
        "reason": probe_reason or selection.reason,
    }
    if qualification_reasons:
        provenance["route_evidence"] = qualification_reasons[
            ("ollama", EMBEDDING_MODEL)
        ]["route_evidence"]
    return {
        "RAG_EMBEDDING_ENGINE": rendered["provider"],
        "RAG_EMBEDDING_MODEL": rendered["wire_model"],
        "output_dims": EMBEDDING_OUTPUT_DIMS,
        "ollama_config": {"url": _ollama_native_base(rendered["endpoint"])},
        SELECTION_PROVENANCE_KEY: provenance,
    }


def _merge_owned_audio(current, wanted):
    """Merge only bundle-owned fields, preserving all operator settings."""
    result = copy.deepcopy(current)
    changed = False
    for section, values in wanted.items():
        if section not in result or not isinstance(result[section], dict):
            result[section] = {}
        for key, value in values.items():
            if value is None:
                value = result[section].get(key)
                if value is None:
                    continue
            if result[section].get(key) != value:
                result[section][key] = value
                changed = True
    return result, changed


def _audio_projection(config):
    return {
        section: {
            key: config.get(section, {}).get(key, AUDIO_DEFAULTS[section].get(key, ""))
            for key in keys
            if key != "OPENAI_API_KEY"
        }
        for section, keys in AUDIO_OWNED_FIELDS.items()
    }


def _audio_credential_transition_path():
    return f"{_audio_state_path()}.credentials"


def _read_audio_credential_transitions():
    try:
        with open(_audio_credential_transition_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _write_audio_credential_transitions(transitions):
    path = _audio_credential_transition_path()
    if not transitions:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    _guarantee_no_secrets(transitions)
    temp = f"{path}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(transitions, handle, indent=2)
        handle.write("\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _audio_state_path():
    return os.path.expanduser(AUDIO_STATE_PATH)


def _read_audio_projection():
    try:
        with open(_audio_state_path(), encoding="utf-8") as handle:
            return json.load(handle).get("projection")
    except (OSError, ValueError):
        return None


def _read_audio_selection():
    try:
        with open(_audio_state_path(), encoding="utf-8") as handle:
            return json.load(handle).get("selection")
    except (OSError, ValueError):
        return None


def _write_audio_projection(projection, selection=None):
    path = _audio_state_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if projection is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    if selection is None:
        selection = _read_audio_selection()
    state = {"projection": projection}
    if selection is not None:
        state["selection"] = selection
    _guarantee_no_secrets(state, allow_key_reference=True)
    temp = f"{path}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _audio_owned_projection(config):
    return _audio_projection(config)


def _audio_refusal(current, projection, allowed_fields=None):
    observed = _audio_projection(current)
    if projection is None:
        fields = (
            allowed_fields
            if allowed_fields is not None
            else {
                (section, key)
                for section, keys in AUDIO_OWNED_FIELDS.items()
                for key in keys
            }
        )
        return [
            (section, key)
            for section, key in fields
            if observed.get(section, {}).get(key, "")
            != AUDIO_DEFAULTS.get(section, {}).get(key, "")
        ]
    return [
        (section, key)
        for section, values in projection.items()
        for key, value in values.items()
        if observed.get(section, {}).get(key, "") != value
    ]


def _audio_gate_active():
    return "omlx" not in _disabled() and "omlx" in active_engines()


def _audio_projection_subset(config, fields):
    """Project only the explicitly owned (section, key) subset."""
    grouped = {}
    for section, key in fields:
        if key == "OPENAI_API_KEY":
            continue
        grouped.setdefault(section, []).append(key)
    return {
        section: {
            key: config.get(section, {}).get(
                key, AUDIO_DEFAULTS.get(section, {}).get(key, "")
            )
            for key in keys
        }
        for section, keys in grouped.items()
    }


def _recorded_fields(projection):
    return {
        (section, key)
        for section, values in (projection or {}).items()
        for key in values
        if key != "OPENAI_API_KEY"
    }


def _credential_fields(projection):
    return {
        (section, key)
        for section, values in (projection or {}).items()
        for key in values
        if key == "OPENAI_API_KEY"
    }


def _verify_audio_projection(client, expected):
    import logger

    readback = client.get_audio_config()
    observed = _audio_projection(readback) if isinstance(readback, dict) else None
    matches = observed is not None and all(
        observed.get(section, {}).get(key, "") == value
        for section, values in expected.items()
        for key, value in values.items()
    )
    if not matches:
        logger.error("Open WebUI audio readback differs from expected owned projection")
        raise OpenWebUIError("Open WebUI audio configuration write verification failed")


def _audio_credential_expectations(transitions):
    expected = {}
    for section, marker in transitions.items():
        restoration = marker.get("restoration", "unprovable")
        if restoration == "empty":
            expected[(section, "OPENAI_API_KEY")] = ("empty", "")
        elif restoration.startswith("env:"):
            current = os.environ.get(restoration[4:])
            proof = marker.get("baseline_sha256")
            if (
                not current
                or not proof
                or hashlib.sha256(current.encode()).hexdigest() != proof
            ):
                expected[(section, "OPENAI_API_KEY")] = ("unprovable", None)
            else:
                expected[(section, "OPENAI_API_KEY")] = ("env", current)
        else:
            expected[(section, "OPENAI_API_KEY")] = ("unprovable", None)
    return expected


def _audio_credential_readback_matches(client, expectations):
    if not expectations:
        return True
    readback = client.get_audio_config()
    if not isinstance(readback, dict):
        return False
    for (section, key), (kind, value) in expectations.items():
        observed = readback.get(section, {}).get(key)
        if kind == "unprovable" or value is None or observed != value:
            return False
    return True


def _audio_reconcile_drift(current, projection, wanted_fields, recorded_fields, logger):
    if projection is not None:
        refusal = _audio_refusal(current, projection, recorded_fields)
        message = "Refusing Open WebUI audio reconciliation; owned fields changed: %s"
    else:
        refusal = _audio_refusal(current, None, wanted_fields)
        message = "Refusing adoption of customized Open WebUI audio fields: %s"
    if refusal:
        logger.error(message, refusal)
        return True
    return False


def _reconcile_audio_adoption(
    client, current, wanted, projection, wanted_fields, recorded_fields, logger
):
    new_fields = wanted_fields - recorded_fields
    if new_fields:
        new_refusal = _audio_refusal(current, None, new_fields)
        if new_refusal:
            logger.error(
                "Refusing adoption of customized Open WebUI audio fields: %s",
                new_refusal,
            )
            return {"status": "refused", "applied": False, "verified": False}
    owned_fields = recorded_fields | wanted_fields
    desired_now = _audio_projection_subset(
        _merge_owned_audio(current, wanted)[0], owned_fields
    )
    credential_change = any(
        current.get(section, {}).get(key) != value
        for section, values in wanted.items()
        for key, value in values.items()
        if key == "OPENAI_API_KEY" and value
    )
    if (
        _audio_projection_subset(current, owned_fields) == desired_now
        and not credential_change
    ):
        return {"status": "noop", "applied": False, "verified": False}
    prewrite = client.get_audio_config()
    if projection is not None:
        # Recheck recorded-field drift and newly claimed fields before writing.
        prewrite_refusal = _audio_refusal(prewrite, projection, recorded_fields)
        if new_fields:
            prewrite_refusal += _audio_refusal(prewrite, None, new_fields)
    else:
        prewrite_refusal = _audio_refusal(prewrite, None, wanted_fields)
    if prewrite_refusal:
        logger.error("Refusing Open WebUI audio write after concurrent edit")
        return {"status": "refused", "applied": False, "verified": False}
    candidate = _merge_owned_audio(prewrite, wanted)[0]
    desired = _audio_projection_subset(candidate, owned_fields)
    client.update_audio_config(candidate)
    try:
        _verify_audio_projection(client, desired)
    except OpenWebUIError as failure:
        failure.outcome = {"status": "failed", "applied": True, "verified": False}
        raise
    _write_audio_projection(desired)


def _reconcile_audio_gate_off(client, projection, recorded_fields, logger):
    prewrite = client.get_audio_config()
    if _audio_refusal(prewrite, projection, recorded_fields):
        logger.error("Refusing Open WebUI audio disable after concurrent edit")
        return {"status": "refused", "applied": False, "verified": False}
    restore = {}
    for section, key in recorded_fields:
        if key == "OPENAI_API_KEY":
            logger.error(
                "Cannot safely restore released audio credential; restore the prior credential manually and remove managed-audio.json"
            )
            return {"status": "refused", "applied": False, "verified": False}
        restore.setdefault(section, {})[key] = AUDIO_DEFAULTS.get(section, {}).get(
            key, ""
        )
    merged, _ = _merge_owned_audio(prewrite, restore)
    changed = merged != prewrite
    if changed:
        client.update_audio_config(merged)
    try:
        _verify_audio_projection(
            client, _audio_projection_subset(merged, recorded_fields)
        )
    except OpenWebUIError as failure:
        failure.outcome = {"status": "failed", "applied": changed, "verified": False}
        raise
    _write_audio_projection(None)


def _reconcile_audio(client, current, wanted):
    import logger

    if not isinstance(current, dict):
        raise OpenWebUIError("Open WebUI audio configuration has an invalid shape")
    projection = _read_audio_projection()
    wanted = {
        section: {key: value for key, value in values.items() if value is not None}
        for section, values in (wanted or {}).items()
    }
    wanted = {section: values for section, values in wanted.items() if values}
    wanted_fields = {
        (section, key)
        for section, values in wanted.items()
        for key in values
        if key != "OPENAI_API_KEY"
    }
    credential_transitions = _read_audio_credential_transitions()
    persisted_credential_transitions = copy.deepcopy(credential_transitions)
    credential_transition_changed = False
    recorded_fields = _recorded_fields(projection)
    legacy_credentials = _credential_fields(projection)
    if legacy_credentials:
        sanitized = {
            section: {
                key: value for key, value in values.items() if key != "OPENAI_API_KEY"
            }
            for section, values in (projection or {}).items()
        }
        sanitized = {section: values for section, values in sanitized.items() if values}
        _write_audio_projection(sanitized or None)
        if not any(
            value
            for values in wanted.values()
            for key, value in values.items()
            if key == "OPENAI_API_KEY"
        ):
            logger.error(
                "Cannot safely restore released audio credential; restore the prior credential manually and remove managed-audio.json"
            )
            return {"status": "refused", "applied": False, "verified": False}
    if credential_transitions and not wanted and projection is None:
        projection = credential_transitions.get("projection")
        credential_transitions = credential_transitions.get(
            "credentials", credential_transitions
        )
    effective_projection = (
        _read_audio_projection() if legacy_credentials else projection
    )
    if _audio_reconcile_drift(
        current, effective_projection, wanted_fields, recorded_fields, logger
    ):
        return {"status": "refused"}
    if credential_transitions and not wanted and projection is not None:
        for section, transition in credential_transitions.items():
            expected_owned = projection.get(section, {})
            actual = current.get(section, {})
            if any(
                actual.get(key, "") != value for key, value in expected_owned.items()
            ):
                logger.error(
                    "Refusing Open WebUI audio release after owned-field drift"
                )
                return {"status": "refused", "released": ["audio-release"]}
    released_fields = recorded_fields - wanted_fields
    released_credentials = set()
    if projection is not None:
        released_credentials = {
            (section, "OPENAI_API_KEY")
            for section in credential_transitions
            if section not in wanted or "OPENAI_API_KEY" not in wanted[section]
        }
    credential_expectations = _audio_credential_expectations(
        {
            section: credential_transitions.get(section, {"restoration": "unprovable"})
            for section, _ in released_credentials
        }
    )
    if released_credentials and any(
        marker.get("restoration", "unprovable").startswith("env:")
        and _audio_credential_expectations({section: marker}).get(
            (section, "OPENAI_API_KEY"), ("unprovable", None)
        )[0]
        == "unprovable"
        for section, marker in credential_transitions.items()
        if (section, "OPENAI_API_KEY") in released_credentials
    ):
        logger.error(
            "Cannot safely restore released audio credential: environment baseline changed; restore the previous credential manually."
        )
        return {
            "status": "refused",
            "applied": False,
            "verified": False,
            "failed": ["audio-credential-restore"],
            "released": [],
        }
    if released_credentials and any(
        kind == "unprovable" or value is None
        for kind, value in credential_expectations.values()
    ):
        logger.error(
            "Cannot safely restore released audio credential: baseline reference changed or is unverifiable. Restore the prior credential manually; managed ownership is retained."
        )
        return {
            "status": "refused",
            "applied": False,
            "verified": False,
            "failed": ["audio-credential-restore"],
            "released": [],
        }
    if released_credentials and projection is None:
        logger.error(
            "Cannot safely restore released audio credential; restore the prior credential manually, then remove managed-audio.json and its credentials marker"
        )
        return {
            "status": "refused",
            "applied": False,
            "verified": False,
            "failed": ["audio-credential-restore"],
            "released": [],
        }
    wanted_credentials = {
        (section, key): value
        for section, values in wanted.items()
        for key, value in values.items()
        if key == "OPENAI_API_KEY" and value
    }
    new_fields = wanted_fields - recorded_fields
    if new_fields:
        new_refusal = _audio_refusal(current, None, new_fields)
        if new_refusal:
            logger.error(
                "Refusing adoption of customized Open WebUI audio fields: %s",
                new_refusal,
            )
            return {"status": "refused", "applied": False, "verified": False}
    prewrite = client.get_audio_config()
    prewrite_refusal = _audio_refusal(prewrite, effective_projection, recorded_fields)
    prewrite_refusal += _audio_refusal(prewrite, None, new_fields)
    if prewrite_refusal:
        logger.error("Refusing Open WebUI audio write after concurrent edit")
        return {"status": "refused"}
    candidate = _merge_owned_audio(prewrite, wanted)[0]
    credential_value_changed = any(
        prewrite.get(section, {}).get(key) != value
        for (section, key), value in wanted_credentials.items()
    )
    for (section, key), value in wanted_credentials.items():
        candidate.setdefault(section, {})[key] = value
    for section, key in wanted_credentials:
        if section in credential_transitions:
            continue
        prior = prewrite.get(section, {}).get(key, "")
        if not credential_value_changed:
            continue
        referenced_env = next(
            (
                name
                for name in ("OMLX_API_KEY", "LITELLM_OPENWEBUI_KEY")
                if os.environ.get(name) and os.environ.get(name) == prior
            ),
            None,
        )
        restoration = (
            "empty"
            if not prior
            else f"env:{referenced_env}" if referenced_env else "unprovable"
        )
        credential_transitions[section] = {
            "field": key,
            "restoration": restoration,
            **(
                {"baseline_sha256": hashlib.sha256(prior.encode()).hexdigest()}
                if referenced_env and prior
                else {}
            ),
        }
    credential_transition_changed = (
        credential_transitions != persisted_credential_transitions
    )
    if credential_transition_changed:
        _write_audio_credential_transitions(credential_transitions)
    if credential_transition_changed or credential_value_changed:
        logger.warning(
            "Open WebUI audio credential transition recorded without persisting credential values"
        )
    if projection is not None:
        projection.setdefault("credentials", {}).update(credential_transitions)
    if (
        released_fields
        and projection is not None
        and any(key == "OPENAI_API_KEY" for _, key in released_fields)
        and not released_credentials
    ):
        logger.error(
            "Cannot safely restore released audio credential; restore the prior credential manually and remove managed-audio.json"
        )
        return {"status": "refused"}
    if released_credentials:
        restore = {
            section: {"OPENAI_API_KEY": value}
            for (section, _), (_, value) in credential_expectations.items()
        }
        candidate = _merge_owned_audio(candidate, restore)[0]
    if released_fields and projection is not None:
        restore = {}
        for section, key in released_fields:
            restore.setdefault(section, {})[key] = AUDIO_DEFAULTS.get(section, {}).get(
                key, ""
            )
        candidate = _merge_owned_audio(candidate, restore)[0]
        retained = recorded_fields & wanted_fields
        # Verify the FULL owned set: released fields must read back at their
        # baseline values and retained/wanted fields at their new values. An
        # empty retained∪wanted must still verify the released restoration —
        # otherwise an ignored write releases ownership unverified.
        expected = None
        expected_fields = retained | wanted_fields | released_fields
        expected_fields |= released_credentials
        applied = False
        verified = False
        if expected_fields:
            expected = _audio_projection_subset(candidate, expected_fields)
            applied = candidate != prewrite
            if candidate != prewrite:
                client.update_audio_config(candidate)
            try:
                _verify_audio_projection(client, expected)
                if released_credentials and not _audio_credential_readback_matches(
                    client, credential_expectations
                ):
                    raise OpenWebUIError(
                        "Open WebUI audio credential restore could not be proven"
                    )
            except OpenWebUIError as failure:
                failure.outcome = {
                    "status": "failed",
                    "applied": applied,
                    "verified": False,
                    "released": sorted(released_fields),
                }
                raise
            verified = True
        # Ownership persists only over CLAIMED fields; released fields are
        # verified-restored above but not owned afterwards.
        if released_credentials and not (retained | wanted_fields):
            try:
                if not _audio_credential_readback_matches(
                    client, credential_expectations
                ):
                    raise OpenWebUIError(
                        "Open WebUI audio credential restore could not be proven"
                    )
            except OpenWebUIError as failure:
                failure.outcome = {
                    "status": "failed",
                    "applied": applied,
                    "verified": False,
                    "released": [],
                }
                raise
        _write_audio_projection(
            _audio_projection_subset(candidate, retained | wanted_fields)
            if (retained | wanted_fields)
            else None
        )
        if released_credentials and not (retained | wanted_fields):
            _write_audio_credential_transitions({})
        remaining_credentials = {
            section: marker
            for section, marker in credential_transitions.items()
            if (section, "OPENAI_API_KEY") not in released_credentials
        }
        if released_credentials:
            _write_audio_credential_transitions(remaining_credentials)
            logger.warning(
                "Open WebUI audio credential ownership released after verified restoration"
            )
        return {
            "status": "verified" if expected_fields else "noop",
            "applied": applied,
            "verified": verified,
            "released": sorted(released_fields),
        }
    if wanted:
        expected = _audio_projection_subset(candidate, recorded_fields | wanted_fields)
        if candidate != prewrite or wanted_credentials:
            client.update_audio_config(candidate)
            try:
                _verify_audio_projection(client, expected)
            except OpenWebUIError as failure:
                failure.outcome = {
                    "status": "failed",
                    "applied": True,
                    "verified": False,
                }
                raise
            _write_audio_projection(expected)
            return {"status": "verified", "applied": True, "verified": True}
        return {"status": "noop", "applied": False, "verified": False}
    elif projection is not None or released_credentials:
        if released_credentials:
            client.update_audio_config(candidate)
            try:
                if not _audio_credential_readback_matches(
                    client, credential_expectations
                ):
                    raise OpenWebUIError(
                        "Open WebUI audio credential restore could not be proven"
                    )
            except OpenWebUIError as failure:
                failure.outcome = {
                    "status": "failed",
                    "applied": True,
                    "verified": False,
                }
                raise
            _write_audio_projection(None)
            _write_audio_credential_transitions({})
            logger.warning(
                "Open WebUI audio credential ownership released after verified restoration"
            )
            return {
                "status": "verified",
                "applied": True,
                "verified": True,
                "released": sorted(released_credentials),
            }
        restore = {
            section: {
                key: AUDIO_DEFAULTS.get(section, {}).get(key, "")
                for section, key in recorded_fields
            }
            for section in {s for s, _ in recorded_fields}
        }
        candidate = _merge_owned_audio(prewrite, restore)[0]
        applied = candidate != prewrite
        if applied:
            client.update_audio_config(candidate)
        try:
            _verify_audio_projection(
                client, _audio_projection_subset(candidate, recorded_fields)
            )
        except OpenWebUIError as failure:
            failure.outcome = {
                "status": "failed",
                "applied": applied,
                "verified": False,
                "released": sorted(recorded_fields),
            }
            raise
        _write_audio_projection(None)
        return {
            "status": "verified",
            "applied": candidate != prewrite,
            "verified": True,
            "released": sorted(recorded_fields),
        }
    if wanted_credentials and not credential_transition_changed:
        logger.debug(
            "Open WebUI audio credential transition remains unchanged; credential values are not persisted"
        )
    return {"status": "noop", "applied": False, "verified": False}


def _embedding_state_path():
    return os.path.expanduser(EMBEDDING_STATE_PATH)


def _read_embedding_state():
    try:
        with open(_embedding_state_path(), encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _embedding_ledger_read():
    """Distinguish absent, usable legacy, and unusable ownership evidence."""
    from pathlib import Path

    path = Path(_embedding_state_path())
    if any(part.is_symlink() for part in (path, *path.parents)):
        return "invalid", None
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "unavailable", None
    except ValueError:
        return "invalid", None
    if not isinstance(state, dict):
        return "invalid", None
    projection = state.get("projection")
    owned = projection.get("owned") if isinstance(projection, dict) else None
    projection_valid = isinstance(owned, dict) and {
        "RAG_EMBEDDING_ENGINE",
        "RAG_EMBEDDING_MODEL",
    }.issubset(owned)
    acknowledgement = state.get("acknowledgement")
    acknowledgement_valid = isinstance(acknowledgement, dict) and {
        "previous_model",
        "previous_engine",
        "new_model",
        "new_output_dims",
        "acknowledged_at",
    }.issubset(acknowledgement)
    # An explicit pending acknowledgement is a supported legacy ledger even
    # before its first managed projection has been recorded.
    if ("projection" in state and not projection_valid) or (
        not projection_valid and not acknowledgement_valid
    ):
        return "invalid", None
    return "valid-legacy", state


def _write_embedding_state(state):
    path = _embedding_state_path()
    from pathlib import Path

    target = Path(path)
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise OpenWebUIError("Refusing unsafe Open WebUI embedding ledger path")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if state is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    state = dict(state)
    if "selection" not in state:
        previous = _read_embedding_state().get("selection")
        if previous is not None:
            state["selection"] = previous
    _guarantee_no_secrets(state, allow_key_reference=True)
    descriptor, temp = tempfile.mkstemp(
        prefix=".managed-embedding.", dir=os.path.dirname(path)
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def _embedding_identity(config):
    return {
        "engine": config.get("RAG_EMBEDDING_ENGINE", ""),
        "model": config.get("RAG_EMBEDDING_MODEL", ""),
    }


_EMBEDDING_IDENTITY_UNSET = object()


def _embedding_refusal(
    config,
    projection,
    client=None,
    state=None,
    backend_identity=_EMBEDDING_IDENTITY_UNSET,
):
    identity = _embedding_identity(config)
    fingerprint = (state or _read_embedding_state()).get("embedding_fingerprint", {})
    if backend_identity is _EMBEDDING_IDENTITY_UNSET:
        backend_identity = _embedding_backend_identity(client, config)
    if (
        # Gateway-owned fingerprints (openai engine) have no ollama daemon
        # identity to probe; their route binding lives in the route record.
        fingerprint.get("engine") != "openai"
        and _embedding_backend_changed(fingerprint, backend_identity)
    ):
        return "Refusing Open WebUI embedding reconciliation; backend identity changed or is unavailable"
    if projection is None:
        default = _embedding_identity(EMBEDDING_DEFAULTS)
        if identity != default:
            return (
                "Refusing adoption of customized Open WebUI embedding identity "
                f"{identity!r}; expected built-in default {default!r}"
            )
    else:
        expected = projection.get("owned", {})
        if identity != {
            "engine": expected.get("RAG_EMBEDDING_ENGINE"),
            "model": expected.get("RAG_EMBEDDING_MODEL"),
        }:
            return "Refusing Open WebUI embedding reconciliation; recorded model identity changed"
        if (
            config.get("RAG_EMBEDDING_ENGINE") == "ollama"
            or "ollama_config" in projection
        ) and config.get("ollama_config", {}).get("url") != projection.get(
            "ollama_config", {}
        ).get(
            "url"
        ):
            return "Refusing Open WebUI embedding reconciliation; recorded Ollama URL changed"
    return None


def _embedding_backend_changed(fingerprint, backend_identity):
    prior = fingerprint.get("backend_identity")
    return prior is not None and (backend_identity is None or prior != backend_identity)


def _embedding_refusal_outcome(logger, message):
    logger.error("%s", message)
    failure = OpenWebUIError(message)
    failure.outcome = {"status": "failed", "applied": False, "verified": False}
    return failure


def _wanted_is_route_only(wanted, current):
    """True when the wanted state changes only the Ollama routing URL.

    A routing-URL update is a transport change: the resolved backend identity
    and the vector representation are untouched, so no re-embedding
    acknowledgement is required.
    """
    if wanted is None:
        return False
    for key in ("RAG_EMBEDDING_ENGINE", "RAG_EMBEDDING_MODEL"):
        if wanted.get(key) != current.get(key):
            return False
    wanted_url = (wanted.get("ollama_config") or {}).get("url")
    current_url = current.get("ollama_config", {}).get("url")
    if not wanted_url or not current_url or wanted_url == current_url:
        return False
    return _ollama_native_base(wanted_url) == _ollama_native_base(current_url)


def _wanted_embedding_matches_current(wanted, current):
    """True only when the wanted state introduces no managed transition."""
    if wanted is None:
        return True
    for key in ("RAG_EMBEDDING_ENGINE", "RAG_EMBEDDING_MODEL"):
        if key in wanted and wanted[key] != current.get(key):
            return False
    wanted_url = (wanted.get("ollama_config") or {}).get("url")
    if wanted_url and wanted_url != current.get("ollama_config", {}).get("url"):
        return False
    return True


def _embedding_candidate(prewrite, target_url=None):
    candidate = {
        key: prewrite[key]
        for key in (
            "RAG_EMBEDDING_BATCH_SIZE",
            "ENABLE_ASYNC_EMBEDDING",
            "RAG_EMBEDDING_CONCURRENT_REQUESTS",
        )
        if key in prewrite
    }
    candidate["ollama_config"] = copy.deepcopy(
        prewrite.get("ollama_config")
        if isinstance(prewrite.get("ollama_config"), dict)
        else {}
    )
    candidate["ollama_config"]["url"] = _ollama_native_base(
        target_url or EMBEDDING_OLLAMA_URL
    )
    for section in ("openai_config", "azure_openai_config"):
        if section in prewrite:
            candidate[section] = copy.deepcopy(prewrite[section])
    candidate.update(
        {"RAG_EMBEDDING_ENGINE": "ollama", "RAG_EMBEDDING_MODEL": EMBEDDING_MODEL}
    )
    return candidate


def _verify_embedding_readback(client, expected, restore=False):
    readback = client.get_embedding_config()
    if expected.get("RAG_EMBEDDING_ENGINE") == "openai":
        # Active-engine readback for the gateway route: engine/model and the
        # OpenAI URL (plus the written key, compared but never persisted).
        expected_openai = expected.get("openai_config") or {}
        readback_openai = readback.get("openai_config") or {}
        if (
            _embedding_identity(readback) != _embedding_identity(expected)
            or (
                expected_openai.get("url")
                and readback_openai.get("url") != expected_openai.get("url")
            )
            or (
                "key" in expected_openai
                and readback_openai.get("key") != expected_openai.get("key")
            )
        ):
            message = (
                "Open WebUI embedding restore verification failed"
                if restore
                else "Open WebUI embedding configuration write verification failed"
            )
            raise OpenWebUIError(message)
        return
    expected_url = expected.get("ollama_config", {}).get("url")
    if (
        _embedding_identity(readback) != _embedding_identity(expected)
        or readback.get("ollama_config", {}).get("url") != expected_url
    ):
        message = (
            "Open WebUI embedding restore verification failed"
            if restore
            else "Open WebUI embedding configuration write verification failed"
        )
        raise OpenWebUIError(message)


def _reconcile_embedding_adoption(
    client,
    state,
    projection,
    current,
    logger,
    wanted=None,
    planning_backend_identity=_EMBEDDING_IDENTITY_UNSET,
    planning_backend_changed=_EMBEDDING_IDENTITY_UNSET,
):
    saved_fingerprint = state.get("embedding_fingerprint", {})
    if (
        planning_backend_identity is not _EMBEDDING_IDENTITY_UNSET
        and planning_backend_changed is not _EMBEDDING_IDENTITY_UNSET
    ):
        # The planning checkpoint owns the backend-identity decision: the
        # authorized identity and its changed flag are threaded down so no
        # later stage can re-probe and silently re-normalize the baseline.
        backend_identity = planning_backend_identity
        backend_changed = planning_backend_changed
    else:
        backend_identity = _embedding_backend_identity(client, current)
        backend_changed = _embedding_backend_changed(
            saved_fingerprint, backend_identity
        )
    if backend_changed and not _embedding_acknowledges(
        state.get("acknowledgement"),
        _embedding_identity(current),
        (projection or {}).get("output_dims"),
        {"engine": "ollama", "model": EMBEDDING_MODEL},
        EMBEDDING_OUTPUT_DIMS,
    ):
        _log_embedding_migration_refusal(
            logger,
            _embedding_identity(current),
            (projection or {}).get("output_dims"),
            {"engine": "ollama", "model": EMBEDDING_MODEL},
            EMBEDDING_OUTPUT_DIMS,
        )
        return {"status": "refused", "applied": False, "verified": False}
    acknowledged_backend_change = backend_changed
    if acknowledged_backend_change and backend_identity is None:
        return {
            "status": "refused",
            "applied": False,
            "verified": False,
        }
    if (
        projection is not None
        and not backend_changed
        and _embedding_identity(current)
        == {"engine": "ollama", "model": EMBEDDING_MODEL}
        and current.get("ollama_config", {}).get("url")
        == projection.get("ollama_config", {}).get("url")
        # A no-op requires the resolved wanted state to agree too: a wanted
        # URL/identity update is a managed transition, never drifted state.
        and _wanted_embedding_matches_current(wanted, current)
    ):
        return {"status": "noop", "applied": False, "verified": False}
    route_only = _wanted_is_route_only(wanted, current)
    previous = _embedding_identity(current)
    old_dims = 384 if previous == _embedding_identity(EMBEDDING_DEFAULTS) else None
    recorded_dims = (projection or {}).get("output_dims")
    if (
        previous == {"engine": "ollama", "model": EMBEDDING_MODEL}
        and recorded_dims is not None
    ):
        # The store is already managed under the recorded model identity: the
        # recorded output_dims are its acknowledged baseline dimensions, so a
        # same-model route change is never a dimension migration.
        old_dims = recorded_dims
    parity = None
    same_dims_gate_applied = route_only
    same_dims_other_model = (
        not route_only
        and previous != {"engine": "ollama", "model": EMBEDDING_MODEL}
        and recorded_dims == EMBEDDING_OUTPUT_DIMS
    )
    if same_dims_other_model:
        # Dimensions never establish representation compatibility: a
        # same-dimension model change requires probe parity; probes are
        # injectable and none is wired for this path, so parity is
        # inconclusive here and the acknowledgement gate stays. The recorded
        # output_dims are the acknowledged baseline dimensions.
        previous_probe = getattr(client, "previous_embedding_probe", None)
        target_probe = getattr(client, "target_embedding_probe", None)
        parity = _embedding_vector_parity(previous_probe, target_probe)
        if not parity:
            acknowledgement = state.get("acknowledgement")
            if not _embedding_acknowledges(
                acknowledgement,
                previous,
                recorded_dims,
                {"engine": "ollama", "model": EMBEDDING_MODEL},
                EMBEDDING_OUTPUT_DIMS,
            ):
                _log_embedding_migration_refusal(
                    logger,
                    previous,
                    recorded_dims,
                    {"engine": "ollama", "model": EMBEDDING_MODEL},
                    EMBEDDING_OUTPUT_DIMS,
                )
                return {"status": "refused", "applied": False, "verified": False}
        same_dims_gate_applied = True
    if (
        not same_dims_gate_applied
        and not acknowledged_backend_change
        and old_dims != EMBEDDING_OUTPUT_DIMS
    ):
        acknowledgement = state.get("acknowledgement")
        if not _embedding_acknowledges(
            acknowledgement,
            previous,
            old_dims,
            {"engine": "ollama", "model": EMBEDDING_MODEL},
            EMBEDDING_OUTPUT_DIMS,
        ):
            _log_embedding_migration_refusal(
                logger,
                previous,
                old_dims,
                {"engine": "ollama", "model": EMBEDDING_MODEL},
                EMBEDDING_OUTPUT_DIMS,
            )
            return {"status": "refused", "applied": False, "verified": False}
    route_change = (
        projection is not None
        and wanted is not None
        and not route_only
        and _embedding_identity(wanted) == _embedding_identity(current)
        and (wanted.get("ollama_config") or {}).get("url")
        != current.get("ollama_config", {}).get("url")
    )
    authorized_target_url = None
    if route_change:
        # Same model, but the routing target leaves the proven same-origin
        # normalization: the frozen contract requires backend-identity plus
        # parity evidence (or the explicit acknowledgement) before any write.
        # Whatever authorizes this transition, the parity authorization, the
        # POST, the readback and the persisted projection must all bind to the
        # SAME resolved wanted destination.
        authorized_target_url = _ollama_native_base(
            (wanted.get("ollama_config") or {}).get("url")
        )
        parity_evidence = _embedding_parity_evidence(client, wanted)
        saved_backend = (state.get("embedding_fingerprint", {}) or {}).get(
            "backend_identity"
        )
        evidence_valid = _embedding_route_parity_conclusive(
            parity_evidence, saved_backend, client, wanted
        )
        if not evidence_valid:
            acknowledgement = state.get("acknowledgement")
            if not _embedding_acknowledges(
                acknowledgement,
                previous,
                recorded_dims,
                {"engine": "ollama", "model": EMBEDDING_MODEL},
                EMBEDDING_OUTPUT_DIMS,
            ):
                logger.error(
                    "Refusing Open WebUI embedding route change without parity "
                    "evidence or acknowledgement"
                )
                return {"status": "refused", "applied": False, "verified": False}
    prewrite = client.get_embedding_config()
    prewrite_backend_identity = _embedding_backend_identity(client, prewrite)
    if acknowledged_backend_change and (
        prewrite_backend_identity is None
        or prewrite_backend_identity != backend_identity
    ):
        raise _embedding_refusal_outcome(
            logger,
            "Refusing Open WebUI embedding write after concurrent backend change",
        )
    prewrite_state = dict(state)
    if acknowledged_backend_change:
        saved = dict(state.get("embedding_fingerprint", {}))
        prewrite_fingerprint = dict(saved)
        prewrite_fingerprint["backend_identity"] = prewrite_backend_identity
        prewrite_state["embedding_fingerprint"] = prewrite_fingerprint
        prewrite_state["acknowledgement"] = state.get("acknowledgement")
    prewrite_refusal = _embedding_refusal(
        prewrite,
        projection,
        None,
        prewrite_state,
        backend_identity=prewrite_backend_identity,
    )
    if prewrite_refusal:
        logger.error("Refusing Open WebUI embedding write after concurrent edit")
        return {"status": "refused", "applied": False, "verified": False}
    if acknowledged_backend_change and (
        _embedding_identity(prewrite) != _embedding_identity(current)
        or prewrite.get("ollama_config", {}).get("url")
        != current.get("ollama_config", {}).get("url")
    ):
        logger.error("Refusing Open WebUI embedding write after concurrent edit")
        return {"status": "refused", "applied": False, "verified": False}
    candidate = _embedding_candidate(prewrite, target_url=authorized_target_url)
    client.update_embedding_config(candidate)
    try:
        _verify_embedding_readback(client, candidate)
    except OpenWebUIError as failure:
        failure.outcome = {"status": "failed", "applied": True, "verified": False}
        raise
    backend_identity = _embedding_backend_identity(client, candidate)
    parity_evidence = _embedding_parity_evidence(client, candidate)
    if acknowledged_backend_change and backend_identity != prewrite_backend_identity:
        failure = OpenWebUIError(
            "Open WebUI embedding backend identity changed after write",
            actions={
                "planned": ["embedding"],
                "applied": ["embedding"],
                "verified": [],
                "failed": ["embedding"],
            },
        )
        failure.outcome = {"status": "failed", "applied": True, "verified": False}
        raise failure
    _write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "ollama",
                    "RAG_EMBEDDING_MODEL": EMBEDDING_MODEL,
                },
                "previous": previous,
                "previous_output_dims": old_dims,
                "output_dims": EMBEDDING_OUTPUT_DIMS,
                "previous_ollama_url": prewrite.get("ollama_config", {}).get("url"),
                "ollama_config": {
                    "url": authorized_target_url
                    or _ollama_native_base(EMBEDDING_OLLAMA_URL)
                },
            },
            "embedding_fingerprint": {
                "engine": "ollama",
                "model": EMBEDDING_MODEL,
                "output_dims": EMBEDDING_OUTPUT_DIMS,
                "parity": (
                    "n-a"
                    if not same_dims_other_model
                    else ("pass" if parity else "acknowledged")
                ),
                "backend_identity": backend_identity,
                "version_evidence": (
                    backend_identity.get("version")
                    if isinstance(backend_identity, dict)
                    else None
                ),
                "parity_evidence": parity_evidence,
            },
            "acknowledgement": state.get("acknowledgement"),
        }
    )
    return {"status": "verified", "applied": True, "verified": True}


def _embedding_backend_identity(client, config):
    probe = getattr(client, "embedding_backend_identity_probe", None)
    return probe(config) if callable(probe) else None


def _embedding_parity_evidence(client, config):
    probe = getattr(client, "embedding_parity_probe", None)
    return probe(config) if callable(probe) else "inconclusive"


def _embedding_route_parity_conclusive(evidence, source_identity, client, wanted):
    """True when route-change parity evidence affirms the transition.

    Conclusive evidence is an affirmative record bound to this transition: it
    must report parity success (affirmative ``parity`` is True), carry the
    verified source backend identity equal to the persisted fingerprint, and
    carry the target backend identity confirmed by a live probe at the wanted
    configuration. Failed, unknown, incomplete, stale or unrelated evidence —
    including any non-dict, failed-status or identity-mismatched payload —
    keeps the acknowledgement/refusal boundary.
    """
    if not isinstance(evidence, dict):
        return False
    if evidence.get("parity") is not True:
        return False
    recorded_source = evidence.get("source_backend_identity")
    recorded_target = evidence.get("target_backend_identity")
    if not recorded_source or not recorded_target:
        return False
    if source_identity is None or recorded_source != source_identity:
        return False
    target_identity = _embedding_backend_identity(client, wanted)
    if target_identity is None or recorded_target != target_identity:
        return False
    return True


def _reconcile_embedding_gateway(client, state, current, wanted, logger):
    """Reconcile the managed embedding model through a gateway route (R6.7).

    The alias and route URL arrive rendered against the SERVED gateway
    inventory; only the canonical namespaced alias of the managed model is
    reconciled here — any other alias is refused. The frozen acknowledgement
    machinery applies to the engine switch (the extended schema carries the
    new engine identity); nothing is written without it. OpenAI settings are
    never owned: the key is resolved from the scoped reference at write time,
    compared at readback without persisting, and only this explicit
    engine-openai payload is written. The preserved Ollama route stays in the
    projection for gate-off restoration, and parity is recorded honestly —
    affirmative only when the injectable machinery proves it.
    """
    alias = wanted.get("RAG_EMBEDDING_MODEL")
    route_url = (wanted.get("openai_config") or {}).get("url")
    canonical = f"ollama/{EMBEDDING_MODEL}"
    if alias != canonical or not route_url:
        logger.error(
            "Refusing Open WebUI embedding gateway alias %r; only the managed "
            "model alias %s is reconciled through the gateway route",
            alias,
            canonical,
        )
        return {"status": "refused", "applied": False, "verified": False}
    projection = state.get("projection")
    recorded = (
        (projection or {}).get("owned", {}) if isinstance(projection, dict) else {}
    )
    recorded_engine = recorded.get("RAG_EMBEDDING_ENGINE")
    recorded_dims = (
        (projection or {}).get("output_dims") if isinstance(projection, dict) else None
    )
    prewrite = client.get_embedding_config()
    identity = _embedding_identity(prewrite)
    if _embedding_identity(current) != identity:
        logger.error("Refusing Open WebUI embedding write after concurrent edit")
        return {"status": "refused", "applied": False, "verified": False}
    if recorded_engine == "openai":
        route_record = (projection or {}).get("route") or {}
        if (
            recorded.get("RAG_EMBEDDING_MODEL") != alias
            or route_record.get("url") != route_url
            or identity != {"engine": "openai", "model": alias}
            or (prewrite.get("openai_config") or {}).get("url") != route_url
        ):
            logger.error("Refusing Open WebUI embedding write after concurrent edit")
            return {"status": "refused", "applied": False, "verified": False}
    else:
        old_dims = (
            recorded_dims
            if recorded_dims is not None
            else (384 if _embedding_identity(EMBEDDING_DEFAULTS) == identity else None)
        )
        if not _embedding_acknowledges(
            state.get("acknowledgement"),
            identity,
            old_dims,
            {"engine": "openai", "model": alias},
            EMBEDDING_OUTPUT_DIMS,
        ):
            logger.error(
                "Refusing Open WebUI embedding engine switch to the gateway "
                "route without the required acknowledgement; edit %s with the "
                "previous_engine/previous_model/previous_output_dims, "
                "new_engine=new_model and acknowledged_at fields matching this "
                "transition, after resetting/re-embedding the vector store if "
                "its representation changes",
                _embedding_state_path(),
            )
            return {"status": "refused", "applied": False, "verified": False}
    key_value = _litellm_key_value()
    if not key_value:
        logger.error(
            "Refusing Open WebUI gateway embedding; the scoped key reference "
            "is unresolved"
        )
        return {"status": "refused", "applied": False, "verified": False}
    served_dims = wanted.get("_served_dims")
    if served_dims != EMBEDDING_OUTPUT_DIMS:
        # Alias membership does not establish the vector representation: the
        # MEASURED served width from the planning probe is what binds.
        logger.error(
            "Refusing Open WebUI gateway embedding; served width %s does not "
            "match the managed representation width %s",
            served_dims,
            EMBEDDING_OUTPUT_DIMS,
        )
        return {"status": "refused", "applied": False, "verified": False}
    if (
        recorded_engine == "openai"
        and (prewrite.get("openai_config") or {}).get("key") == key_value
    ):
        # Already-managed gateway route with a matching effective
        # configuration: no-op. Repeated applies and key refreshes must never
        # rewrite the original restoration baseline.
        return {"status": "noop", "applied": False, "verified": False}
    candidate = {
        key: prewrite[key]
        for key in (
            "RAG_EMBEDDING_BATCH_SIZE",
            "ENABLE_ASYNC_EMBEDDING",
            "RAG_EMBEDDING_CONCURRENT_REQUESTS",
        )
        if key in prewrite
    }
    candidate.update(
        {
            "RAG_EMBEDDING_ENGINE": "openai",
            "RAG_EMBEDDING_MODEL": alias,
            "openai_config": {"url": route_url, "key": key_value},
        }
    )
    if isinstance(prewrite.get("ollama_config"), dict):
        # Explicitly preserve the native restoration data: the Ollama section
        # passes through so replacement-style server stores keep it and the
        # reverse transition's prewrite checks stay truthful.
        candidate["ollama_config"] = copy.deepcopy(prewrite["ollama_config"])
    client.update_embedding_config(candidate)
    try:
        _verify_embedding_readback(client, candidate)
    except OpenWebUIError as failure:
        failure.outcome = {"status": "failed", "applied": True, "verified": False}
        raise
    previous_probe = getattr(client, "previous_embedding_probe", None)
    target_probe = getattr(client, "target_embedding_probe", None)
    parity = (
        _embedding_vector_parity(previous_probe, target_probe)
        if callable(previous_probe) and callable(target_probe)
        else None
    )
    parity_evidence = _embedding_parity_evidence(client, candidate)
    if recorded_engine == "openai":
        # Rotation/refresh re-apply: carry the ORIGINAL restoration baseline
        # verbatim — the pre-switch identity, its width and the preserved
        # Ollama route are never rewritten by repeated gateway applies.
        carry = projection or {}
        baseline_identity = carry.get("previous")
        baseline_dims = carry.get("previous_output_dims")
        preserved_ollama_url = (carry.get("ollama_config") or {}).get("url")
    else:
        baseline_identity = identity
        baseline_dims = (
            recorded_dims
            if recorded_dims is not None
            else (384 if _embedding_identity(EMBEDDING_DEFAULTS) == identity else None)
        )
        preserved_ollama_url = (prewrite.get("ollama_config") or {}).get("url")
    _write_embedding_state(
        {
            "projection": {
                "owned": {
                    "RAG_EMBEDDING_ENGINE": "openai",
                    "RAG_EMBEDDING_MODEL": alias,
                },
                "previous": baseline_identity,
                "previous_output_dims": baseline_dims,
                "output_dims": EMBEDDING_OUTPUT_DIMS,
                "previous_ollama_url": preserved_ollama_url,
                "ollama_config": {"url": preserved_ollama_url},
                "route": {
                    "engine": "openai",
                    "url": route_url,
                    "key_reference": LITELLM_OPENWEBUI_KEY_PATH,
                },
            },
            "embedding_fingerprint": {
                "engine": "openai",
                "model": alias,
                "output_dims": EMBEDDING_OUTPUT_DIMS,
                "parity": "pass" if parity else "inconclusive",
                "backend_identity": None,
                "version_evidence": None,
                "parity_evidence": parity_evidence,
            },
            "acknowledgement": state.get("acknowledgement"),
        }
    )
    return {"status": "verified", "applied": True, "verified": True}


def _embedding_vector_parity(previous_probe, target_probe, tolerance=1e-6):
    """Deterministic embedding representation-parity check (contract-frozen).

    Both probes are injectable callables returning {text -> vector}; the check
    verifies identical dimensions per text and near-identical vectors
    (normalized cosine/norm within `tolerance`). Parity is used ONLY for
    transport-only changes of the SAME qualified backend/model. Any
    model-identity change without probe evidence keeps the acknowledgement
    gate; a live unavailable probe = parity inconclusive => the gate stays.
    """
    if not callable(previous_probe) or not callable(target_probe):
        return False
    try:
        prev_vectors = previous_probe()
        target_vectors = target_probe()
    except Exception:
        return False
    if not isinstance(prev_vectors, dict) or not isinstance(target_vectors, dict):
        return False
    if set(prev_vectors) != set(target_vectors) or not prev_vectors:
        return False
    for text in prev_vectors:
        a, b = prev_vectors[text], target_vectors[text]
        if len(a) != len(b) or not a:
            return False
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return False
        if abs(norm_a - norm_b) / max(norm_a, 1.0) > tolerance:
            return False
        cosine = sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)
        if cosine < 1.0 - tolerance:
            return False
    return True


PARITY_PROBE_STRINGS = (
    "short probe",
    "unicode probe \u2734 \u2744",
    "a longer context probe string for embedding parity",
)


def _guarantee_no_secrets(payload, allow_key_reference=False):
    """State-writers refuse credential-bearing payloads outright (contract-frozen).

    Any dict field whose name matches credential patterns must carry an empty
    value; otherwise reconciliation refuses to persist the state. mode 0600 is
    not permission for cleartext.
    """

    secret_words = ("KEY", "TOKEN", "SECRET", "PASSWORD")

    def _scan(node):
        if isinstance(node, dict):
            for name, value in node.items():
                if isinstance(value, (dict, list)):
                    _scan(value)
                elif (
                    any(word in str(name).upper() for word in secret_words)
                    and isinstance(value, str)
                    and value
                    and not (allow_key_reference and str(name) == "key_reference")
                ):
                    raise OpenWebUIError(
                        f"Refusing to persist secret-looking managed state: {name}"
                    )
        elif isinstance(node, list):
            for item in node:
                _scan(item)

    _scan(payload)


def _embedding_restore(prewrite, projection):
    restored = {
        key: prewrite[key]
        for key in (
            "RAG_EMBEDDING_BATCH_SIZE",
            "ENABLE_ASYNC_EMBEDDING",
            "RAG_EMBEDDING_CONCURRENT_REQUESTS",
        )
        if key in prewrite
    }
    restored["ollama_config"] = copy.deepcopy(
        prewrite.get("ollama_config")
        if isinstance(prewrite.get("ollama_config"), dict)
        else {}
    )
    for section in ("openai_config", "azure_openai_config"):
        if section in prewrite:
            restored[section] = copy.deepcopy(prewrite[section])
    restored.update(
        {
            "RAG_EMBEDDING_ENGINE": projection.get("previous", {}).get("engine", ""),
            "RAG_EMBEDDING_MODEL": projection.get("previous", {}).get("model", ""),
        }
    )
    if projection.get("previous_ollama_url") is not None:
        restored["ollama_config"]["url"] = projection["previous_ollama_url"]
    return restored


def _reconcile_embedding_gate_off(client, state, projection, logger):
    prewrite = client.get_embedding_config()
    if _embedding_refusal(prewrite, projection, client, state):
        logger.error("Refusing Open WebUI embedding disable after concurrent edit")
        return {"status": "refused", "applied": False, "verified": False}
    previous = projection.get("previous", {})
    previous_dims = projection.get("previous_output_dims")
    current_dims = projection.get("output_dims")
    if previous != _embedding_identity(prewrite):
        acknowledgement = state.get("acknowledgement")
        if not _embedding_acknowledges(
            acknowledgement,
            _embedding_identity(prewrite),
            current_dims,
            previous,
            previous_dims,
        ):
            _log_embedding_migration_refusal(
                logger,
                _embedding_identity(prewrite),
                current_dims,
                previous,
                previous_dims,
            )
            return {"status": "refused", "applied": False, "verified": False}
    restored = _embedding_restore(prewrite, projection)
    client.update_embedding_config(restored)
    try:
        _verify_embedding_readback(client, restored, restore=True)
    except OpenWebUIError as failure:
        failure.outcome = {"status": "failed", "applied": True, "verified": False}
        raise
    _write_embedding_state(None)
    return {
        "status": "verified",
        "applied": True,
        "verified": True,
        "released": ["embedding"],
    }


def _reconcile_embedding(client, current, wanted):
    import logger

    if not isinstance(current, dict) or (
        # Replacement-style gateway snapshots may omit the native Ollama
        # section entirely once the openai engine is active; the guard accepts
        # either engine's section and the engine-specific paths validate the
        # rest.
        not isinstance(current.get("ollama_config"), dict)
        and not isinstance(current.get("openai_config"), dict)
    ):
        raise OpenWebUIError("Open WebUI embedding configuration has an invalid shape")
    ledger_status, ledger_state = _embedding_ledger_read()
    if ledger_status != "valid-legacy":
        logger.error(
            "Refusing Open WebUI embedding reconciliation; ownership ledger is %s",
            ledger_status,
        )
        return {"status": "refused", "applied": False, "verified": False}
    state = ledger_state
    projection = state.get("projection")
    fingerprint = state.get("embedding_fingerprint", {})
    if wanted and wanted.get("RAG_EMBEDDING_ENGINE") == "openai":
        return _reconcile_embedding_gateway(client, state, current, wanted, logger)
    backend_identity = (
        None
        if fingerprint.get("engine") == "openai"
        else _embedding_backend_identity(client, current)
    )
    refusal = _embedding_refusal(
        current, projection, client, state, backend_identity=backend_identity
    )
    if refusal and wanted:
        identity = _embedding_identity(current)
        changed = _embedding_backend_changed(fingerprint, backend_identity)
        if (
            changed
            and backend_identity is not None
            and _embedding_acknowledges(
                state.get("acknowledgement"),
                identity,
                (projection or {}).get("output_dims"),
                {"engine": "ollama", "model": EMBEDDING_MODEL},
                EMBEDDING_OUTPUT_DIMS,
            )
        ):
            # Acknowledgement authorizes exactly THIS probed identity; the
            # planning values thread down so later stages enforce the same
            # identity and never re-probe a new baseline.
            refusal = None
    if refusal:
        logger.error("%s", refusal)
        return {"status": "refused", "applied": False, "verified": False}
    if wanted:
        return _reconcile_embedding_adoption(
            client,
            state,
            projection,
            current,
            logger,
            wanted=wanted or None,
            planning_backend_identity=backend_identity,
            planning_backend_changed=_embedding_backend_changed(
                fingerprint, backend_identity
            ),
        )
    elif projection is not None:
        return _reconcile_embedding_gate_off(client, state, projection, logger)
    return {"status": "noop", "applied": False, "verified": False}


def _embedding_acknowledges(acknowledgement, old, old_dims, new, new_dims):
    if not isinstance(acknowledgement, dict):
        return False
    expected = {
        "previous_model": old["model"],
        "previous_engine": old["engine"],
        "previous_output_dims": old_dims,
        "new_model": new["model"],
        "new_output_dims": new_dims,
    }
    new_engine = new.get("engine", "ollama")
    if "new_engine" in acknowledgement:
        # Engine-aware transitions (e.g. the R6.7 gateway route) carry the new
        # engine identity inside the acknowledgement itself.
        expected["new_engine"] = new_engine
    elif new_engine == "openai":
        # A legacy acknowledgement (omitting new_engine) cannot authorize a
        # gateway switch; it predates the engine-aware schema.
        return False
    return all(
        acknowledgement.get(key) == value for key, value in expected.items()
    ) and bool(acknowledgement.get("acknowledged_at"))


def _log_embedding_migration_refusal(logger, old, old_dims, new, new_dims):
    logger.error(
        "Refusing dimension-changing Open WebUI embedding migration: "
        "%s (%s-dim) -> %s (%s-dim). Reset/re-embed the vector store "
        "using Open WebUI's own mechanisms, then edit %s with the "
        "required acknowledgement object before retrying.",
        old["model"],
        old_dims or "unknown",
        new["model"],
        new_dims or "unknown",
        _embedding_state_path(),
    )


class OpenWebUIError(RuntimeError):
    def __init__(self, message, actions=None):
        super().__init__(message)
        self.actions = actions or {
            "planned": [],
            "applied": [],
            "verified": [],
            "failed": [],
        }


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
    TERMINAL_CONFIG_PATH = "/api/v1/configs/terminal_servers"
    TOOL_SERVERS_CONFIG_PATH = "/api/v1/configs/tool_servers"
    MODELS_CONFIG_PATH = "/api/v1/configs/models"

    def __init__(self, base_url, api_key, timeout=10, admin_credentials=None):
        self.base_url, self.api_key, self.timeout = (
            base_url.rstrip("/"),
            api_key,
            timeout,
        )
        self.admin_credentials = admin_credentials or {}
        self._session_token = None

    def _request_bytes(self, path, data, content_type, method="POST", retry_auth=True):
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:  # nosec B310
                return response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                if retry_auth and self._try_signin():
                    return self._request_bytes(path, data, content_type, method, False)
                raise AuthError(f"Open WebUI API HTTP {error.code}") from error
            raise ReachableError(f"Open WebUI API HTTP {error.code}") from error
        except (urllib.error.URLError, OSError) as error:
            raise ReachableError(f"Open WebUI API unavailable: {error}") from error

    @staticmethod
    def _multipart(fields, filename, content, content_type):
        boundary = "----OpenWebUIClientBoundary7MA4YWxkTrZu0gW"
        chunks = []
        for name, value in fields.items():
            chunks.extend(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(),
                )
            )
        chunks.extend(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode(),
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
        return f"multipart/form-data; boundary={boundary}", b"".join(chunks)

    def transcribe_audio(self, wav_bytes, filename):
        """Submit audio to the configured consumer transcription route."""
        content_type, body = self._multipart({}, filename, wav_bytes, "audio/wav")
        _, response = self._request_bytes(
            "/api/v1/audio/transcriptions", body, content_type
        )
        return json.loads(response.decode())

    def speech(self, text, model=None, voice=None):
        """Return the configured consumer speech route's content type and audio bytes."""
        payload = {"input": text}
        if model is not None:
            payload["model"] = model
        if voice is not None:
            payload["voice"] = voice
        content_type, response = self._request_bytes(
            "/api/v1/audio/speech", json.dumps(payload).encode(), "application/json"
        )
        return content_type, response

    def upload_file(self, content, filename, content_type):
        """Upload a file through Open WebUI's file API."""
        multipart_type, body = self._multipart({}, filename, content, content_type)
        _, response = self._request_bytes("/api/v1/files/", body, multipart_type)
        return json.loads(response.decode())

    def process_file(self, file_id):
        """ProcessFileForm requires JSON file_id; content and collection_name are optional."""
        return self._request(
            "/api/v1/retrieval/process/file", "POST", {"file_id": file_id}
        )

    def retrieval_query(self, query, file_ids=None, collection_id=None, k=2):
        """QueryDocForm uses collection_name; QueryCollectionsForm uses collection_names."""
        if collection_id is not None:
            payload = {"collection_name": collection_id, "query": query, "k": k}
            return self._request("/api/v1/retrieval/query/doc", "POST", payload)
        payload = {"collection_names": file_ids or [], "query": query, "k": k}
        return self._request("/api/v1/retrieval/query/collection", "POST", payload)

    def _request(self, path, method="GET", payload=None, retry_auth=True):
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
            # Loopback-only URLs by construction (base_url is 127.0.0.1 or a
            # user-configured LAN endpoint) — B310 does not apply.
            with urllib.request.urlopen(  # nosec B310
                request, timeout=self.timeout
            ) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                if retry_auth and self._try_signin():
                    return self._request(path, method, payload, retry_auth=False)
                raise AuthError(f"Open WebUI API HTTP {error.code}") from error
            raise ReachableError(f"Open WebUI API HTTP {error.code}") from error
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
            raise ReachableError(f"Open WebUI API unavailable: {error}") from error

    def _try_signin(self):
        """Fall back to admin-credential auth when the API key is rejected.

        Some Open WebUI deployments disable API-key authentication for admin
        config endpoints; a signin-derived session token still authorizes
        them. Never logs credential material.
        """
        email = self.admin_credentials.get("email")
        password = self.admin_credentials.get("password")
        if not email or not password or self._session_token:
            return False
        request = urllib.request.Request(
            self.base_url + "/api/v1/auths/signin",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            # Loopback-only signin endpoint by construction — B310 does not apply.
            with urllib.request.urlopen(  # nosec B310
                request, timeout=self.timeout
            ) as response:
                token = json.loads(response.read().decode() or "{}").get("token")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return False
        if not token:
            return False
        self._session_token = token
        self.api_key = token
        return True

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

    def get_terminal_servers_config(self):
        return self._request(self.TERMINAL_CONFIG_PATH)

    def update_terminal_servers_config(self, config):
        return self._request(self.TERMINAL_CONFIG_PATH, "POST", config)

    def get_tool_servers_config(self):
        return self._request(self.TOOL_SERVERS_CONFIG_PATH)

    def update_tool_servers_config(self, config):
        return self._request(self.TOOL_SERVERS_CONFIG_PATH, "POST", config)

    MODELS_LIST_PATH = "/api/models"

    def get_models(self):
        return self._request(self.MODELS_LIST_PATH)

    def get_models_config(self):
        return self._request(self.MODELS_CONFIG_PATH)

    def update_models_config(self, config):
        return self._request(self.MODELS_CONFIG_PATH, "POST", config)

    AUDIO_CONFIG_PATH = "/api/v1/audio/config"
    AUDIO_CONFIG_UPDATE_PATH = "/api/v1/audio/config/update"

    def get_audio_config(self):
        return self._request(self.AUDIO_CONFIG_PATH)

    def update_audio_config(self, config):
        return self._request(self.AUDIO_CONFIG_UPDATE_PATH, "POST", config)

    EMBEDDING_CONFIG_PATH = "/api/v1/retrieval/embedding"
    EMBEDDING_CONFIG_UPDATE_PATH = "/api/v1/retrieval/embedding/update"

    def get_embedding_config(self):
        return self._request(self.EMBEDDING_CONFIG_PATH)

    def update_embedding_config(self, config):
        return self._request(self.EMBEDDING_CONFIG_UPDATE_PATH, "POST", config)

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


def _managed(entry):
    """Return whether the connection carries the dotfiles ownership marker."""
    return entry.get("config", {}).get(MANAGED_MARKER_KEY) == MANAGED_MARKER_VALUE


def _is_stock_openai_default(entry):
    """True for upstream's no-key https://api.openai.com/v1 default entry."""
    if entry.get("key"):
        return False
    if _canonicalize_host(entry.get("url", "")) != _canonicalize_host(STOCK_OPENAI_URL):
        return False
    if _identity(entry) not in STOCK_OPENAI_PREFIXES:
        return False
    config = entry.get("config", {})
    # open-webui 0.12.0's config migration drops 'enable' from migrated
    # entries; a missing flag is still the stock seed, while an explicit
    # non-true value means the entry was customised and must survive.
    if config.get("enable", True) is not True:
        return False
    if config.keys() - {"connection_type", "enable", "prefix_id"}:
        return False
    # Upstream seed is OpenAI-protocol (or omits connection_type). A customised
    # non-OpenAI value must survive proxy-mode pruning.
    connection_type = config.get("connection_type")
    return connection_type is None or connection_type == "openai"


def _ownership_identity(url, connection_type, collection):
    """Return a canonical endpoint, protocol and collection ownership tuple."""
    return (_root(_canonicalize_host(url)), connection_type, collection)


def _catalogue_identity(owner):
    return _ownership_identity(
        owner["url"], owner["connection_type"], owner["collection"]
    )


def reconcile(
    current_openai, current_ollama, desired, *, namespace=MANAGED_PREFIX_NAMESPACE
):
    """Plan connection changes and report ownership collisions without writing.

    Compare current and previous routing catalogues to migrate managed entries
    between direct and proxy modes. Preserve unrelated user connections and
    retain collection snapshots in the returned ReconcileResult.
    """
    catalogue = ownership_catalogue()
    previous_catalogue = ownership_catalogue(not constants.get_litellm_proxy_mode())
    proxy_enabled = constants.get_litellm_proxy_mode()
    desired_entries = (
        desired.get("openai", [])
        + desired.get("anthropic", [])
        + desired.get("ollama", [])
    )
    desired_by_prefix = {_identity(item): item for item in desired_entries}
    catalogue_by_identity = {
        _catalogue_identity(owner): prefix for prefix, owner in catalogue.items()
    }
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
    # Upstream seeds a default connection (plain enable-only config, no
    # prefix_id) on first run; next to a managed twin with the same URL it is
    # a duplicate. Only exact-URL matches of managed desired entries in the
    # SAME collection are pruned; other unmanaged entries stay untouched.
    managed_urls = {
        collection: {
            item["url"] for item in desired_entries if item["collection"] == collection
        }
        for collection in ("openai", "ollama")
    }
    for collection, existing in current:
        config = existing.get("config", {})
        prefix = _identity(existing)
        wanted = desired_by_prefix.get(prefix)
        owner = catalogue.get(prefix)
        current_identity = _ownership_identity(
            existing.get("url", ""), config.get("connection_type"), collection
        )
        identity_owner = catalogue_by_identity.get(current_identity)
        expected_identity = owner and current_identity == _catalogue_identity(owner)
        if _managed(existing):
            previous_owner = previous_catalogue.get(prefix)
            previously_managed = (
                previous_owner
                and current_identity == _catalogue_identity(previous_owner)
            )
            if previously_managed and wanted is None:
                plan.entries.append(
                    {
                        "action": "delete",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
                continue
            if previously_managed and wanted is not None:
                seen.add(prefix)
                if collection != wanted["collection"]:
                    plan.entries.extend(
                        (
                            {
                                "action": "delete",
                                "collection": collection,
                                "entry": copy.deepcopy(existing),
                            },
                            {
                                "action": "add",
                                "collection": wanted["collection"],
                                "entry": copy.deepcopy(wanted),
                            },
                        )
                    )
                else:
                    plan.entries.append(
                        {
                            "action": "update",
                            "collection": collection,
                            "entry": copy.deepcopy(wanted),
                            "before": copy.deepcopy(existing),
                        }
                    )
                continue
            # Degenerate proxy-collapse case: stale managed entries migrated
            # from the previous per-provider proxy topology sit at the
            # gateway endpoint with a connection_type the rewritten proxy
            # catalogue no longer records (every proxy-mode owner is
            # openai/openai). Their desired state is gone, so they are
            # deletable even on identity mismatch if their prefix is a known
            # former owner; other entries keep the fail-closed collision path.
            gateway_url = catalogue.get("litellm", {}).get("url", "")
            stale_known_owner = (
                proxy_enabled and wanted is None and previous_owner is not None
            )
            at_gateway = bool(gateway_url) and _canonicalize_host(
                existing.get("url", "")
            ) == _canonicalize_host(gateway_url)
            if stale_known_owner and at_gateway:
                plan.entries.append(
                    {
                        "action": "delete",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
                continue
            # owner == prefix and the entry's identity matches its catalogue
            # owner: authoritative match. The reverse map is unreliable when
            # multiple owners share one URL (LiteLLM canary collapses every
            # provider onto the same endpoint), so only use it when the
            # owner's own catalogue identity disagrees.
            if (
                not owner
                or not expected_identity
                or (
                    identity_owner
                    and identity_owner != prefix
                    and not expected_identity
                )
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
        elif prefix.startswith(namespace):
            # Legacy dw-* entries migrate only when their endpoint/type still
            # belongs to the current catalogue.  A stale legacy entry is
            # removed rather than adopted as an unrelated user connection.
            target = identity_owner
            migrated = desired_by_prefix.get(target) if target else None
            if target and migrated is not None:
                if target in seen:
                    plan.entries.append(
                        {
                            "action": "delete",
                            "collection": collection,
                            "entry": copy.deepcopy(existing),
                        }
                    )
                else:
                    seen.add(target)
                    plan.entries.append(
                        {
                            "action": "update",
                            "collection": collection,
                            "entry": copy.deepcopy(migrated),
                            "before": copy.deepcopy(existing),
                        }
                    )
            else:
                plan.entries.append(
                    {
                        "action": "delete",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
        elif identity_owner:
            # A new-style prefix without the marker is adoptable on sight only
            # when both its endpoint/type and prefix agree with the catalogue.
            if identity_owner != prefix:
                if prefix in desired_by_prefix:
                    collisions.append(copy.deepcopy(existing))
                plan.entries.append(
                    {
                        "action": "keep",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
            elif wanted is None or prefix in seen:
                plan.entries.append(
                    {
                        "action": "keep" if wanted is None else "delete",
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
        else:
            if prefix in desired_by_prefix:
                collisions.append(copy.deepcopy(existing))
                plan.entries.append(
                    {
                        "action": "keep",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
                continue
            if (
                collection == "openai"
                and proxy_enabled
                and _is_stock_openai_default(existing)
            ):
                # Upstream ships a no-key api.openai.com default entry: plain
                # enable-only config and no prefix_id, so on a managed gateway
                # deployment it is a duplicate seed. Prune exactly that entry in
                # proxy mode; key-bearing or customised entries stay untouched.
                plan.entries.append(
                    {
                        "action": "delete",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
                continue
            seed_config = {
                key: value
                for key, value in config.items()
                if key not in {"connection_type", "enable", "prefix_id"}
            }
            is_upstream_seed = (
                not prefix
                and not existing.get("key")
                and config.get("enable") is True
                and not seed_config
            )
            if (
                _canonicalize_host(existing.get("url", "")) in managed_urls[collection]
                and is_upstream_seed
            ):
                plan.entries.append(
                    {
                        "action": "delete",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
                    }
                )
            else:
                plan.entries.append(
                    {
                        "action": "keep",
                        "collection": collection,
                        "entry": copy.deepcopy(existing),
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
    audio_wanted = desired.get("audio")
    audio_status = desired.get("audio_status")
    actions = {
        "planned": [item["action"] for item in result.plan.entries],
        "applied": [],
        "verified": [],
        "failed": [],
    }
    if audio_wanted or _read_audio_projection() is not None:
        actions["planned"].append("audio")
    embedding_planned = bool(desired.get("embedding"))
    if embedding_planned:
        actions["planned"].append("embedding")
    try:
        audio_result = None
        if hasattr(client, "get_audio_config"):
            audio_selection = desired.get("audio_selection")
            if audio_wanted is None and audio_status in {
                "unknown",
                "empty",
                "unavailable",
            }:
                # Explicitly non-released absence: preserve managed settings.
                import logger

                logger.warning(
                    "Open WebUI audio discovery %s; preserving managed settings",
                    audio_status,
                )
            else:
                audio_current = client.get_audio_config()
                before_openai, before_ollama = (
                    client.get_openai_config(),
                    client.get_ollama_config(),
                )
                if before_openai != initial_openai or before_ollama != initial_ollama:
                    raise OpenWebUIError(
                        "Open WebUI snapshot changed before write; aborting"
                    )
                audio_result = _reconcile_audio(client, audio_current, audio_wanted)
                if (
                    audio_result.get("released")
                    and audio_result.get("status") == "refused"
                ):
                    actions["failed"].append("audio-release")
                if audio_result.get("status") == "refused":
                    actions["failed"].append("audio")
                if audio_result.get("applied"):
                    actions["applied"].append("audio")
                if audio_result.get("verified"):
                    actions["verified"].append("audio")
                if audio_result.get("released"):
                    actions["applied"].append("audio-release")
                    actions["verified"].append("audio-release")
            if audio_selection:
                record = audio_selection.get("_unavailable", audio_selection)
                if (
                    "_unavailable" in audio_selection
                    or record.get("qualification") in {"unavailable", "unknown"}
                    or (audio_result is not None and audio_result.get("applied"))
                ):
                    # Unavailable/unknown markers and verified applies persist;
                    # refused/unresolved qualified decisions never replace
                    # reusable successful evidence.
                    _persist_audio_selection(record)
    except OpenWebUIError as error:
        actions["failed"].append("audio")
        outcome = getattr(error, "outcome", None)
        if outcome:
            if outcome.get("applied"):
                actions["applied"].append("audio")
            if outcome.get("verified"):
                actions["verified"].append("audio")
        error.actions = actions
        raise
    try:
        if hasattr(client, "get_embedding_config"):
            embedding_selection = desired.get("embedding_selection")
            if desired.get("embedding_status") in {"unknown", "unavailable"}:
                import logger

                logger.warning(
                    "Open WebUI embedding discovery %s; preserving managed settings",
                    desired["embedding_status"],
                )
                embedding_result = {
                    "status": "unresolved",
                    "applied": False,
                    "verified": False,
                }
            else:
                embedding_current = client.get_embedding_config()
                embedding_result = _reconcile_embedding(
                    client, embedding_current, desired.get("embedding")
                )
            if embedding_result.get("status") == "refused":
                actions["failed"].append("embedding")
            if desired.get("embedding"):
                if embedding_result.get("applied"):
                    actions["applied"].append("embedding")
                if embedding_result.get("verified"):
                    actions["verified"].append("embedding")
            if embedding_selection:
                record = embedding_selection.get("_unavailable", embedding_selection)
                if (
                    "_unavailable" in embedding_selection
                    or record.get("qualification") in {"unavailable", "unknown"}
                    or embedding_result.get("applied")
                ):
                    # Unavailable/unknown markers and verified applies persist;
                    # refused/unresolved qualified decisions never replace
                    # reusable successful evidence.
                    _persist_embedding_selection(record)
            if embedding_result.get("released"):
                actions["applied"].append("embedding-release")
                actions["verified"].append("embedding-release")
    except OpenWebUIError as error:
        actions["failed"].append("embedding")
        outcome = getattr(error, "outcome", None)
        if outcome:
            if outcome.get("applied"):
                actions["applied"].append("embedding")
            if outcome.get("verified"):
                actions["verified"].append("embedding")
        error.actions = actions
        raise
    try:
        before_openai, before_ollama = (
            client.get_openai_config(),
            client.get_ollama_config(),
        )
        if before_openai != initial_openai or before_ollama != initial_ollama:
            raise OpenWebUIError("Open WebUI snapshot changed before write; aborting")
    except OpenWebUIError as error:
        actions["failed"].append("connections-snapshot")
        error.actions = actions
        raise
    except Exception as error:
        actions["failed"].append("connections-snapshot")
        raise OpenWebUIError(
            "Open WebUI connection snapshot read failed", actions=actions
        ) from error
    changed = tuple(
        collection
        for collection in ("openai", "anthropic", "ollama")
        if any(
            item["collection"] == collection
            and item["action"] in {"add", "update", "delete"}
            for item in result.plan.entries
        )
    )
    actions["planned"].extend(sorted(changed))
    try:
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
            payload = OpenWebUIClient.payload(
                result.snapshots[collection]["envelope"], entries
            )
            if collection == "openai":
                client.update_openai_config(payload)
            else:
                client.update_ollama_config(payload)
            actions["applied"].append(collection)
    except Exception as error:
        actions["failed"].append(locals().get("collection", "connections"))
        if isinstance(error, OpenWebUIError):
            error.actions = actions
            raise
        raise OpenWebUIError(
            "Open WebUI connection write failed", actions=actions
        ) from error
    if changed:
        # Unmanaged entries the plan prunes are known upstream seed duplicates;
        # anything else moving must fail the verification. Prunes are tracked
        # by full serialized identity (collection + url + config) so a deleted
        # seed never masks a different unmanaged entry sharing its URL.
        pruned_entries = {
            (
                item["collection"],
                item["entry"].get("url", ""),
                json.dumps(
                    {
                        "url": item["entry"].get("url", ""),
                        "key": item["entry"].get("key", ""),
                        "config": item["entry"].get("config", {}),
                    },
                    sort_keys=True,
                ),
            )
            for item in result.plan.entries
            if item["action"] == "delete" and not _managed(item["entry"])
        }
        catalogue = ownership_catalogue()

        def _owned_or_migrating(item, collection):
            if _managed(item):
                return True
            config = item.get("config", {})
            identity = _ownership_identity(
                item.get("url", ""),
                config.get("connection_type"),
                collection,
            )
            prefix = _identity(item)
            owner = catalogue.get(prefix)
            if owner is not None and identity == _catalogue_identity(owner):
                return True
            if prefix.startswith(MANAGED_PREFIX_NAMESPACE):
                by_identity = {
                    _catalogue_identity(owner): catalogue_prefix
                    for catalogue_prefix, owner in catalogue.items()
                }
                return by_identity.get(identity) is not None
            return False

        def _prune_fingerprint(item):
            # Full serialized identity (url + key + config) so a deleted seed
            # never masks a different unmanaged entry sharing its URL.
            return json.dumps(
                {
                    "url": item.get("url", ""),
                    "key": item.get("key", ""),
                    "config": item.get("config", {}),
                },
                sort_keys=True,
            )

        def _unmanaged(entries, collection):
            return [
                copy.deepcopy(item)
                for item in entries
                if not _owned_or_migrating(item, collection)
                and (
                    collection,
                    item.get("url", ""),
                    _prune_fingerprint(item),
                )
                not in pruned_entries
            ]

        before_unmanaged = {
            collection: _unmanaged(result.snapshots[collection]["entries"], collection)
            for collection in result.snapshots
        }
        try:
            verified = reconcile(
                client.get_openai_config(), client.get_ollama_config(), desired
            )
        except OpenWebUIError as error:
            actions["failed"].append("connections-verification")
            error.actions = actions
            raise
        except Exception as error:
            actions["failed"].append("connections-verification")
            raise OpenWebUIError(
                "Open WebUI connection verification read failed", actions=actions
            ) from error
        after_unmanaged = {
            collection: _unmanaged(
                verified.snapshots[collection]["entries"], collection
            )
            for collection in verified.snapshots
        }
        if (
            verified.status != "clean"
            or any(item["action"] != "keep" for item in verified.plan.entries)
            or before_unmanaged != after_unmanaged
        ):
            actions["failed"].append("connections")
            raise OpenWebUIError(
                "Open WebUI write verification failed or unmanaged state changed",
                actions=actions,
            )
    actions["verified"].extend(sorted(changed))
    result.actions = actions
    return result
