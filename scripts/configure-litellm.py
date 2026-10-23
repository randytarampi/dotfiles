#!/usr/bin/env python3
"""Generate the gated, loopback-only LiteLLM proxy configuration."""

import argparse
import glob
import hashlib
import os
import json
import shutil
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 (sys.path must be set up first)
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
from litellm_config import (  # noqa: E402
    APP_KEYS,
    CLIENT_REGISTRY,
    LiveCatalogueError,
    compute_model_list,
    write_config,
)
import litellm_config  # noqa: E402
from litellm_client_auth import resolve_client_auth  # noqa: E402

GATE_ENV = "DOTFILES_RUN_LITELLM_SETUP"

_CLIENT_ENABLE_GATES = {
    "opencode": ("DOTFILES_RUN_OPENCODE_SETUP",),
    "pi": ("DOTFILES_RUN_PI_SETUP", "DOTFILES_USE_LITELLM_PROXY"),
    "openwebui": ("DOTFILES_RUN_OPENWEBUI_SETUP",),
    "junie": ("DOTFILES_RUN_JUNIE_CLI_SETUP",),
}
_LEGACY_CLIENT_GATES = (
    "DOTFILES_RUN_OPENCODE_SETUP",
    "DOTFILES_RUN_PI_SETUP",
    "DOTFILES_RUN_OPENWEBUI_SETUP",
    "DOTFILES_RUN_JUNIE_CLI_SETUP",
    "DOTFILES_USE_LITELLM_PROXY",
)
_CLI_INSTALLS = {"opencode": "opencode", "pi": "pi"}


def _client_provisioning_state(row, enforce_registry_gates=True):
    """Return explicit eligibility and an honest reason for a registry row."""
    if (
        not row["credential_policy"].startswith("provision")
        or row["classification"] != "eligible"
    ):
        return False, "classification-only/no credential"
    if enforce_registry_gates and not all(
        os.environ.get(gate.split("=", 1)[0], "0") == gate.split("=", 1)[1]
        for gate in row["setup_gates"]
    ):
        return False, "setup gate disabled"
    if enforce_registry_gates and not all(
        os.environ.get(gate, "0") == "1" for gate in _CLIENT_ENABLE_GATES[row["client"]]
    ):
        return False, "user enablement gate disabled"
    binary = _CLI_INSTALLS.get(row["client"])
    # Fleet-managed clients (openwebui/junie) prove installation through their
    # own DOTFILES_RUN_* enablement gates — their configure scripts own the
    # install in the same deploy. CLI detection applies only where a binary
    # exists; no invented GUI heuristics here.
    if binary is not None and shutil.which(binary) is None:
        return False, "not installed"
    if "OpenAI-compatible" not in row["transports"]:
        return False, "usable transport UNKNOWN"
    return True, "eligible"


def validate_service_lane_runtime():
    """Validate service-tier filtering and metadata in the managed gateway venv."""
    python = Path("~/.local/share/litellm/venv/bin/python").expanduser()
    probe = """
import json
from importlib.metadata import version
import litellm
from litellm.utils import get_optional_params

params = get_optional_params(
    model="openai/gpt-6.1-sol",
    custom_llm_provider="openai",
    drop_params=True,
    service_tier="flex",
)
if params.get("service_tier") != "flex":
    raise RuntimeError("service_tier was dropped with drop_params=True")
info = litellm.get_model_info("gpt-6.1-sol")
print(json.dumps({
    "version": version("litellm"),
    "metadata_path": "top-level model_info" if isinstance(info, dict) else "unknown",
}))
"""
    try:
        result = subprocess.run(  # nosec B603 - fixed probe in the managed gateway interpreter.
            [str(python), "-I", "-c", probe],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        evidence = json.loads(result.stdout)
        logger.info(
            "LiteLLM service-lane runtime validation VERIFIED: version=%s; "
            "service_tier survives drop_params; pricing lookup uses %s",
            evidence["version"],
            evidence["metadata_path"],
        )
        return True
    except Exception as error:
        logger.warning(
            "LiteLLM service-lane runtime validation UNVERIFIED: %s",
            getattr(error, "stderr", None) or error,
        )
        return False


GENERATION_NOTES_PATH = Path(
    "~/.local/share/litellm/last_generation_notes.json"
).expanduser()


def _persist_generation_notes(notes, path=None):
    """Atomically persist coverage diagnostics for the separately-run doctor."""
    target = Path(path or GENERATION_NOTES_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=".litellm-notes.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(list(notes), handle)
            handle.write("\n")
        os.replace(temp_path, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _refresh_rates_snapshot(dry_run=False):
    """Refresh the runtime rates snapshot, degrading honestly on failure.

    The rates document is auxiliary pricing metadata: a fetch timeout or any
    refresh failure keeps the existing snapshot and never fails the deploy
    (the next successful run refreshes it). Returns the state string for the
    generation summary.
    """
    try:
        return _sync_runtime_rates_snapshot(dry_run=dry_run)
    except Exception as rates_error:  # noqa: BLE001 - degrade-by-design
        logger.warning(
            "LiteLLM rates snapshot refresh failed (%s); keeping the existing snapshot",
            rates_error,
        )
        return "unchanged"


def _sync_runtime_rates_snapshot(dry_run=False, runtime_path=None) -> str:
    """Refresh the runtime rates document from models.dev-derived rates.

    The committed repo snapshot stays the offline fallback; the runtime file
    (~/.local/share/litellm/model-rates.json) is what the venv patcher merges
    into the installed cost map, refreshed here at every generation. Diff-only
    write keeps repeated deploys byte-stable.
    """
    runtime = Path(
        runtime_path or "~/.local/share/litellm/model-rates.json"
    ).expanduser()
    generated = litellm_config.build_rates_snapshot()
    payload = json.dumps(generated, indent=2, sort_keys=True) + "\n"
    try:
        current = runtime.read_text(encoding="utf-8") if runtime.is_file() else ""
    except OSError:
        current = ""
    if current == payload:
        return "unchanged"
    if dry_run:
        logger.info("LiteLLM runtime rates would be updated: %s", runtime)
        return "updated"
    try:
        runtime.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=str(runtime.parent), prefix=".model-rates."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temp_path, runtime)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        return "updated"
    except OSError as error:
        logger.warning("LiteLLM runtime rates write failed: %s", error)
        return "failed"


def _service_env_value(name, path):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                values = shlex.split(line.split("=", 1)[1])
                return values[0] if values else ""
    except (OSError, ValueError):
        pass
    return ""


def _database_url_configured():
    return bool(
        os.environ.get("LITELLM_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )


def _request_json(url, method, master_key, payload=None, timeout=30):
    parsed_url = urllib.parse.urlsplit(url)
    if parsed_url.scheme != "http" or parsed_url.hostname != "127.0.0.1":
        raise ValueError(
            "LiteLLM provisioning requests are restricted to loopback HTTP"
        )
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    # B310: URL scheme and host are constrained above to loopback HTTP.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _persist_app_keys(path: Path, keys: dict[str, str]) -> None:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                values[name] = value
    values.update({name: shlex.quote(value) for name, value in keys.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.writelines(f"{name}={value}\n" for name, value in values.items())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _key_file_materialized(client_key_path: Path, env_name, service_env_path) -> bool:
    """A client key is materialized only with non-empty usable key material.

    An empty key file alone would strand the client: the recovery path below
    has no value to repair it with, so treat emptiness as unmaterialized.
    """
    if client_key_path.is_symlink():
        return False
    if client_key_path.is_file():
        try:
            if client_key_path.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            pass
    return bool(_service_env_value(env_name, service_env_path))


def _write_client_key(service_env_path: Path, client, env_name, dry_run=False) -> None:
    """Materialize a per-client app key without sourcing service.env."""
    key = _service_env_value(env_name, service_env_path)
    if not key:
        return
    directory = service_env_path.parent
    target_dir = directory / "clients"
    target = target_dir / f"{client}.key"
    try:
        if (
            service_env_path.is_symlink()
            or directory.is_symlink()
            or target_dir.is_symlink()
            or target.is_symlink()
        ):
            raise OSError("unsafe symlink in LiteLLM client-key path")
        if dry_run:
            return
        directory.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(target_dir, 0o700)
        if target.exists() and target.read_text(encoding="utf-8") == key:
            os.chmod(target, 0o600)
            return
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=f".{client}.key.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(key)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    except OSError as error:
        logger.warning("Could not safely write %s LiteLLM key file: %s", client, error)


def _write_opencode_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the OpenCode app key without sourcing service.env."""
    _write_client_key(service_env_path, "opencode", "LITELLM_OPENCODE_KEY", dry_run)


def _write_pi_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the Pi app key without sourcing service.env."""
    _write_client_key(service_env_path, "pi", "LITELLM_PI_KEY", dry_run)


def _write_junie_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the Junie app key without sourcing service.env."""
    _write_client_key(service_env_path, "junie", "LITELLM_JUNIE_KEY", dry_run)


def _write_openwebui_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the Open WebUI app key without sourcing service.env."""
    _write_client_key(service_env_path, "openwebui", "LITELLM_OPENWEBUI_KEY", dry_run)


def _materialized_client_key_value(
    client_key_path: Path, env_name, service_env_path
) -> str:
    """Return the resolver's canonical credential for one client.

    Both sides are stripped to the credential the clients actually send, so
    a whitespace-padded file hashes the same bare key it will authenticate
    with; a whitespace-only file is unmaterialized (same semantics as
    _key_file_materialized) and the service.env fallback applies.
    """
    # Canonical client file is primary; env is accepted only when consistent,
    # and is the fallback only when that file is absent.
    try:
        if client_key_path.is_file() and not client_key_path.is_symlink():
            if client_key_path.read_text(encoding="utf-8").strip():
                auth = resolve_client_auth(env_name, client_key_path)
                return auth.value if auth.state == "accepted" and auth.value else ""
    except OSError:
        pass
    service_value = _service_env_value(env_name, service_env_path)
    if not service_value:
        return ""
    previous = os.environ.get(env_name)
    os.environ[env_name] = service_value
    try:
        auth = resolve_client_auth(env_name, client_key_path)
    finally:
        if previous is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = previous
    return auth.value if auth.state == "accepted" and auth.value else ""


def _align_client_token_hash(
    alias, token_hash, previous_hash, service_env_path: Path
) -> None:
    """Align one app-key row's registered hash with the local key material.

    Upstream exposes no API to set a token value, and the local material is
    canonical once a key is materialized, so the alignment runs through the
    same managed-venv prisma path the spend backfill uses.
    """
    import subprocess  # nosec B404 -- managed-venv runner with internal code strings

    database_url = _service_env_value("DATABASE_URL", service_env_path)
    if not database_url:
        raise RuntimeError("DATABASE_URL missing from service.env")
    candidates = sorted(
        glob.glob(str(service_env_path.parent / "venv" / "bin" / "python3*"))
    )
    if not candidates:
        raise RuntimeError("LiteLLM venv interpreter not found")
    code = (
        "import json, sys\n"
        "from prisma import Prisma\n"
        "import asyncio\n"
        "async def _main():\n"
        "    client = Prisma()\n"
        "    await client.connect()\n"
        "    rows = await client.query_raw(\n"
        '        \'UPDATE "LiteLLM_VerificationToken" SET token = $1 '
        "WHERE key_alias = $2 AND token = $3 RETURNING key_alias, token',\n"
        "        sys.argv[1], sys.argv[2],\n        sys.argv[3],\n"
        "    )\n"
        "    await client.disconnect()\n"
        "    print(json.dumps(rows, default=str))\n"
        "asyncio.run(_main())\n"
    )
    try:
        proc = subprocess.run(  # nosec B603 -- controlled: internal code + managed interpreter
            [candidates[-1], "-c", code, token_hash, alias, previous_hash],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env={**os.environ, "DATABASE_URL": database_url},
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("app-key hash alignment timed out") from error
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
        raise RuntimeError(f"app-key hash alignment failed: {tail[:200]}")
    # The constrained UPDATE must return exactly the row it aligned; zero or
    # multiple returned rows mean the registry moved underneath us and the
    # repair did NOT land — never report that as success.
    try:
        aligned = json.loads(
            [line for line in proc.stdout.splitlines() if line.strip()][-1]
        )
    except (json.JSONDecodeError, IndexError) as error:
        raise RuntimeError("app-key hash alignment returned no payload") from error
    if not isinstance(aligned, list) or len(aligned) != 1:
        raise RuntimeError(
            f"unexpected alignment result: {len(aligned) if isinstance(aligned, list) else 'no rows'} row(s) matched"
        )


def _verify_app_key_hash(
    alias,
    client_key_path: Path,
    env_name,
    service_env_path: Path,
    record_token,
    dry_run=False,
) -> None:
    """Repair app-key rows whose registered hash drifted from local material.

    The deployed key material (service.env + the client key file) is the
    canonical value once a key is materialized; a generation race can leave
    the gateway row hashing a value that no longer exists on disk, which
    makes every gateway request with that key 401 — observed live as Open
    WebUI rendering no models at all.
    """
    if not isinstance(record_token, str) or not record_token:
        return
    value = _materialized_client_key_value(client_key_path, env_name, service_env_path)
    if not value:
        return
    expected = hashlib.sha256(value.encode("utf-8")).hexdigest()
    if expected == record_token:
        return
    if dry_run:
        logger.warning(
            "LiteLLM app key %s drifted: registered hash %s… != local %s… (dry-run: not repaired)",
            alias,
            str(record_token)[:12],
            expected[:12],
        )
        return
    logger.warning(
        "LiteLLM app key %s drifted: registered hash %s… != local %s… — repairing the row",
        alias,
        str(record_token)[:12],
        expected[:12],
    )
    try:
        _align_client_token_hash(alias, expected, record_token, service_env_path)
    except (OSError, RuntimeError) as error:
        logger.warning("Could not repair drifted LiteLLM app key %s: %s", alias, error)


def provision_app_keys(
    master_key,
    service_env_path=None,
    api_base=None,
    dry_run=False,
    strict=False,
    *,
    enforce_registry_gates=True,
):
    """Idempotently provision per-client virtual keys after LiteLLM is healthy."""
    return _provision_app_keys(
        master_key,
        service_env_path,
        api_base,
        dry_run,
        strict,
        enforce_registry_gates=enforce_registry_gates,
    )


def _provision_app_keys(
    master_key,
    service_env_path=None,
    api_base=None,
    dry_run=False,
    strict=False,
    *,
    enforce_registry_gates=True,
):
    if not master_key:
        logger.warning("LiteLLM master key unavailable; skipping app-key provisioning")
        return not strict
    api_base = (
        api_base or f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}"
    ).rstrip("/")
    service_env_path = Path(
        service_env_path or "~/.local/share/litellm/service.env"
    ).expanduser()
    try:
        _request_json(
            (
                f"{api_base}/health/readiness"
                if strict
                else f"{api_base}/health/liveliness"
            ),
            "GET",
            master_key,
        )
        # Default /key/list responses carry opaque hash strings; only
        # return_full_object=true exposes key_alias for idempotency.
        listing = _request_json(
            f"{api_base}/key/list?return_full_object=true", "GET", master_key
        )
        records = listing.get("keys", []) if isinstance(listing, dict) else []
        aliases = {
            item.get("key_alias")
            for item in records
            if isinstance(item, dict) and item.get("key_alias")
        }
        found = {}
        provisioning_rows = []
        for row in CLIENT_REGISTRY:
            eligible, reason = _client_provisioning_state(row, enforce_registry_gates)
            if eligible:
                provisioning_rows.append(row)
            elif (
                row["classification"] == "classification-only"
                or row["client"] == "junie"
            ):
                logger.info("Skipping LiteLLM client %s: %s", row["client"], reason)
            else:
                logger.debug("Skipping LiteLLM client %s: %s", row["client"], reason)
        preflight = []
        refusal = None
        for row in provisioning_rows:
            alias, env_name = row["client"], row["env_name"]
            client_key_path = service_env_path.parent / "clients" / f"{alias}.key"
            auth = resolve_client_auth(env_name, client_key_path)
            remote = next(
                (
                    item
                    for item in records
                    if isinstance(item, dict) and item.get("key_alias") == alias
                ),
                None,
            )
            if auth.state == "conflict":
                logger.error(
                    "LiteLLM client %s credential conflict: %s", alias, auth.detail
                )
                refusal = "credential conflict"
            if auth.state == "accepted" and remote is None:
                refusal = "invalid state: local key exists without a gateway record"
            elif not strict and auth.state == "absent":
                logger.info(
                    "LiteLLM client %s not authorized: credential absent", alias
                )
            preflight.append((row, auth, remote))
        if refusal:
            logger.error("LiteLLM provisioning preflight refused: %s", refusal)
            return False

        for row, auth, remote in preflight:
            alias, env_name = row["client"], row["env_name"]
            client_key_path = service_env_path.parent / "clients" / f"{alias}.key"
            stranded_record = remote
            if auth.state == "accepted" and auth.value:
                found[env_name] = auth.value
            if stranded_record is not None:
                # The key-list endpoint intentionally does not reveal existing
                # key material; preserve any locally stored key on disk.
                materialized = _key_file_materialized(
                    client_key_path, env_name, service_env_path
                )
                if client_key_path.is_symlink():
                    continue
                if materialized:
                    _verify_app_key_hash(
                        alias,
                        client_key_path,
                        env_name,
                        service_env_path,
                        stranded_record.get("token") if stranded_record else None,
                        dry_run=dry_run,
                    )
                    continue
                if stranded_record:
                    key = _recover_stranded_app_key(
                        api_base, master_key, alias, stranded_record
                    )
                    if key:
                        found[env_name] = key
                    continue
            if auth.state == "accepted":
                # Do not mint a different credential over a locally accepted
                # key; rotate explicitly, then verify before retrying.
                continue
            try:
                generated = _request_json(
                    f"{api_base}/key/generate",
                    "POST",
                    master_key,
                    # key_alias is attribution only; it does not scope
                    # model access, team membership, or budget.
                    {"key_alias": alias, "duration": None},
                )
            except OSError as error:
                # Some proxy versions reject duplicate aliases with 400 even
                # when the list response hides key_alias — treat those as
                # already-provisioned and keep the loop idempotent.
                status = getattr(error, "code", None)
                if status == 400:
                    listing = _request_json(
                        f"{api_base}/key/list?return_full_object=true",
                        "GET",
                        master_key,
                    )
                    retry_records = (
                        listing.get("keys", []) if isinstance(listing, dict) else []
                    )
                    record = next(
                        (
                            item
                            for item in retry_records
                            if isinstance(item, dict) and item.get("key_alias") == alias
                        ),
                        None,
                    )
                    materialized = _key_file_materialized(
                        client_key_path, env_name, service_env_path
                    )
                    if record and not materialized and not client_key_path.is_symlink():
                        key = _recover_stranded_app_key(
                            api_base, master_key, alias, record
                        )
                        if key:
                            found[env_name] = key
                        continue
                    logger.info(
                        "LiteLLM key alias %s already exists; preserving local key state",
                        alias,
                    )
                    continue
                raise
            key = generated.get("key") if isinstance(generated, dict) else None
            if key:
                materialized = _materialized_client_key_value(
                    client_key_path, env_name, service_env_path
                )
                if materialized and materialized != key:
                    logger.error(
                        "LiteLLM client %s invalid state: generated and canonical credentials disagree",
                        alias,
                    )
                    return False
                found[env_name] = key
                aliases.add(alias)
            else:
                logger.warning("LiteLLM did not return a key for alias %s", alias)
        generated_keys = {}
        for name, value in found.items():
            row = next(row for row in provisioning_rows if row["env_name"] == name)
            canonical = resolve_client_auth(
                name, service_env_path.parent / "clients" / f"{row['client']}.key"
            )
            if canonical.state == "absent":
                generated_keys[name] = value
        if generated_keys:
            _persist_app_keys(service_env_path, generated_keys)
            logger.info("Provisioned %d LiteLLM app key(s)", len(generated_keys))
        for row in provisioning_rows:
            writer = {
                "opencode": _write_opencode_key,
                "pi": _write_pi_key,
                "junie": _write_junie_key,
                "openwebui": _write_openwebui_key,
            }.get(row["client"])
            if writer:
                writer(service_env_path, dry_run=dry_run)
        if strict:
            for row in provisioning_rows:
                alias, env_name = row["client"], row["env_name"]
                key_path = service_env_path.parent / "clients" / f"{alias}.key"
                auth = resolve_client_auth(env_name, key_path)
                if auth.state != "accepted":
                    logger.error(
                        "LiteLLM client %s is not authorized: %s", alias, auth.detail
                    )
                    return False
                key = auth.value
                if not key:
                    logger.error("LiteLLM client key is not provisioned: %s", alias)
                    return False
                try:
                    response = _request_json(f"{api_base}/v1/models", "GET", key)
                except (OSError, ValueError, TypeError):
                    logger.error("LiteLLM client key authentication failed: %s", alias)
                    return False
                if not isinstance(response, dict) or not isinstance(
                    response.get("data"), list
                ):
                    logger.error("LiteLLM client key model access failed: %s", alias)
                    return False
        return True
    except (OSError, ValueError) as error:
        logger.warning("LiteLLM app-key provisioning deferred: %s", error)
        return not strict


def _recover_stranded_app_key(api_base, master_key, alias, record):
    """Delete and regenerate only an alias whose local key material is absent."""
    token_hash = record.get("token") or record.get("token_id")
    if not isinstance(token_hash, str) or not token_hash:
        logger.warning(
            "Cannot recover stranded LiteLLM alias %s: token hash unavailable", alias
        )
        return None
    _request_json(f"{api_base}/key/delete", "POST", master_key, {"keys": [token_hash]})
    generated = _request_json(
        f"{api_base}/key/generate",
        "POST",
        master_key,
        {"key_alias": alias, "duration": None},
    )
    key = generated.get("key") if isinstance(generated, dict) else None
    if key:
        logger.info("recovered stranded alias %s: deleted + regenerated", alias)
    return key


def main():
    parser = argparse.ArgumentParser(
        description="Configure the loopback LiteLLM gateway.", allow_abbrev=False
    )
    add_common_args(parser)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Require enabled client keys to authenticate against the ready local gateway",
    )
    args = parser.parse_args()
    if not load_env():
        logger.warning("~/.env not found")
    if os.environ.get(GATE_ENV, "0") != "1":
        logger.info("%s is not enabled; skipping LiteLLM setup", GATE_ENV)
        return 0
    if not _database_url_configured():
        logger.error("LITELLM_DATABASE_URL is required when LiteLLM is enabled")
        return 1
    if args.require_ready and args.dry_run:
        parser.error("--require-ready cannot be combined with --dry-run")
    if args.require_ready:
        service_env_path = Path("~/.local/share/litellm/service.env").expanduser()
        master_key = os.environ.get("LITELLM_MASTER_KEY", "").strip()
        if not master_key:
            master_key = _service_env_value("LITELLM_MASTER_KEY", service_env_path)
        return 0 if provision_app_keys(master_key, service_env_path, strict=True) else 1
    config_path = os.path.expanduser("~/.local/share/litellm/config.yaml")
    try:
        service_lane_runtime_valid = validate_service_lane_runtime()
        # Compute the snapshot once: write_config reuses it instead of
        # re-enumerating provider catalogues a second time per deploy.
        previous_entries = []
        try:
            previous_entries = litellm_config.read_previous_entries(config_path)
        except (OSError, ValueError, TypeError):
            pass
        entries = compute_model_list(previous_entries=previous_entries)
        if (
            os.environ.get(litellm_config.OPENAI_SERVICE_LANES_ENV, "0") == "1"
            and any(
                entry["litellm_params"].get("service_tier") in ("flex", "fast")
                for entry in entries
            )
            and not service_lane_runtime_valid
        ):
            logger.error(
                "LiteLLM service-lane runtime validation failed; keeping existing config"
            )
            return 1
        for note in litellm_config.last_generation_notes:
            logger.warning("LiteLLM coverage: %s", note)
        if args.dry_run:
            master_key_set = bool(os.environ.get("LITELLM_MASTER_KEY", "").strip())
            rates_state = _refresh_rates_snapshot(dry_run=True)
            logger.info(
                "LiteLLM dry-run: models=%d master_key_set=%s config=%s rates=%s",
                len(entries),
                master_key_set,
                config_path,
                rates_state,
            )
            return 0
        changed = write_config(config_path, entries=entries)
        litellm_config.write_previous_entries(config_path, entries)
        _persist_generation_notes(litellm_config.last_generation_notes)
        logger.info(
            "LiteLLM config %s (%d model entries)",
            "updated" if changed else "unchanged",
            len(entries),
        )
        rates_state = _refresh_rates_snapshot(args.dry_run)
        if rates_state == "updated":
            logger.info("LiteLLM rates snapshot refreshed (models.dev)")
        service_env_path = Path("~/.local/share/litellm/service.env").expanduser()
        master_key = os.environ.get("LITELLM_MASTER_KEY", "").strip()
        if not master_key:
            master_key = _service_env_value("LITELLM_MASTER_KEY", service_env_path)
        provision_app_keys(master_key, service_env_path, dry_run=args.dry_run)
        return 0
    except LiveCatalogueError as error:
        logger.error(
            "LiteLLM live catalogue enumeration failed; keeping existing config: %s",
            error,
        )
        return 1
    except Exception as error:
        logger.error("LiteLLM configuration failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
