"""Launch and verify a process-bound LiteLLM configuration generation.

The managed gateway venv supplies PyYAML and psutil. No network request,
credential lookup, parsed-router assertion, or inference happens here.
"""

import hashlib
import json
import os
import stat
import sys
import tempfile
from collections import Counter
from pathlib import Path

UNKNOWN = "refused-no-loaded-generation-receipt"
STALE = "refused-stale-loaded-generation"
FOREIGN = "refused-loaded-generation-port-mismatch"
MISMATCH = "refused-loaded-generation-projection-mismatch"
PROCESS = "refused-loaded-generation-process-mismatch"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _owned_file(path, *, private=False, missing=False):
    """Reject linked ancestors/leaves and foreign or special files."""
    path = Path(os.path.abspath(path))
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("unsafe generation directory")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or (private and info.st_mode & 0o077)
    ):
        raise ValueError("unsafe generation file")
    return info


def _read(path, *, private=False):
    _owned_file(path, private=private)
    return path.read_bytes()


def _publish(path, data, mode):
    _owned_file(path, missing=True)
    fd, temporary = tempfile.mkstemp(prefix=".generation-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), mode)
            handle.flush()
            os.fsync(handle.fileno())
        _owned_file(path, missing=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _snapshot(home, digest):
    return home / f".loaded-config-{digest}.yaml"


def _command(home, snapshot, port):
    return [
        str(home / "venv/bin/litellm"),
        "--config",
        str(snapshot),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--num_workers",
        "1",
    ]


def _port(value):
    port = int(value)
    if not 1 <= port <= 65535 or str(port) != str(value):
        raise ValueError("invalid gateway port")
    return port


def _process_matches(home, snapshot, port, receipt):
    import psutil

    process = psutil.Process(receipt["pid"])
    if process.create_time() != receipt["created"]:
        return False
    command = _command(home, snapshot, port)
    if process.cmdline()[-len(command) :] != command:
        return False
    return (
        any(
            connection.status == psutil.CONN_LISTEN
            and connection.laddr.ip == "127.0.0.1"
            and connection.laddr.port == port
            for connection in process.net_connections(kind="tcp")
        )
        and process.is_running()
    )


def _route_signature(rows):
    if not isinstance(rows, list):
        raise ValueError("missing route list")
    signature = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid route")
        alias = row.get("model_name")
        params = row.get("litellm_params")
        if not isinstance(params, dict):
            raise ValueError("invalid route parameters")
        wire, base = params.get("model"), params.get("api_base")
        if not isinstance(alias, str) or not alias:
            raise ValueError("invalid route alias")
        if not isinstance(wire, str) or not wire:
            raise ValueError("invalid wire model")
        if base is not None and not isinstance(base, str):
            raise ValueError("invalid API base")
        signature[alias, wire, base] += 1
    return signature


def _yaml_routes(data):
    import yaml

    class UniqueKeysLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in result:
                    raise ValueError("duplicate YAML key")
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    document = yaml.load(data, Loader=UniqueKeysLoader)  # nosec B506
    if not isinstance(document, dict):
        raise ValueError("invalid gateway document")
    return document.get("model_list")


def launch(home, port):
    """Exec the exact snapshot whose digest and process identity we publish."""
    import psutil

    port = _port(port)
    config = _read(home / "config.yaml")
    digest = _digest(config)
    snapshot = _snapshot(home, digest)
    if _owned_file(snapshot, private=True, missing=True) is None:
        _publish(snapshot, config, 0o400)
    if _read(snapshot, private=True) != config:
        raise ValueError("generation snapshot differs")
    receipt = {
        "schema": 1,
        "sha256": digest,
        "pid": os.getpid(),
        "created": psutil.Process().create_time(),
        "port": port,
    }
    _publish(
        home / ".loaded-generation",
        (json.dumps(receipt, sort_keys=True) + "\n").encode(),
        0o600,
    )
    # exec retains the process identity; a failed exec cannot attest a listener.
    command = _command(home, snapshot, port)
    os.execv(command[0], command)  # nosec B606 - fixed managed gateway argv.


def check(home, port, projection_digest):
    """Check bytes, route semantics, and the process actually listening."""
    port = _port(port)
    config = _read(home / "config.yaml")
    digest = _digest(config)
    receipt = json.loads(_read(home / ".loaded-generation", private=True))
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != 1
        or type(receipt.get("pid")) is not int
        or receipt["pid"] <= 0
        or type(receipt.get("created")) not in (int, float)
    ):
        return UNKNOWN
    if receipt.get("port") != port:
        return FOREIGN
    if receipt.get("sha256") != digest:
        return STALE
    snapshot = _snapshot(home, digest)
    if _read(snapshot, private=True) != config:
        return STALE
    projection = _read(home / "free-route-config.json")
    if _digest(projection) != projection_digest:
        return MISMATCH
    document = json.loads(projection)
    if not isinstance(document, dict):
        return MISMATCH
    if _route_signature(_yaml_routes(config)) != _route_signature(
        document.get("routes")
    ):
        return MISMATCH
    if not _process_matches(home, snapshot, port, receipt):
        return PROCESS
    # Reject a generation/plan replacement during the check.
    if (
        _read(home / "config.yaml") != config
        or _read(home / "free-route-config.json") != projection
        or json.loads(_read(home / ".loaded-generation", private=True)) != receipt
    ):
        return STALE
    return None


def main(argv):
    try:
        if len(argv) == 2 and argv[0] == "install":
            _publish(
                Path(argv[1]) / "launch-generation.py",
                Path(__file__).read_bytes(),
                0o700,
            )
            return 0
        if len(argv) == 3 and argv[0] == "launch":
            launch(Path(argv[1]), argv[2])
            return 1
        if len(argv) == 4 and argv[0] == "check":
            reason = check(Path(argv[1]), argv[2], argv[3])
            if reason is None:
                return 0
            print(reason)
            return 1
    except Exception:
        # Do not disclose YAML, credentials, process arguments or exception text.
        print(UNKNOWN)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
