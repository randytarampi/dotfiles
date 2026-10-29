#!/usr/bin/env python3
"""Opt in to a Hermes LiteLLM custom-provider configuration scaffold."""

import argparse
import os
import re
import sys
import tempfile
import stat
import hashlib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import logger  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

GATE = "DOTFILES_RUN_HERMES_SETUP"
PROVIDER = "dotfiles-litellm"
KEY_ENV = "LITELLM_HERMES_KEY"
MODEL = re.compile(r"^[^\s\x00-\x1f\x7f\"'\\]+$")


def hermes_home():
    """Match Hermes' explicit home override before its platform default."""
    override = os.environ.get("HERMES_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".hermes"


def validate_endpoint(value):
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})/v1", value)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise ValueError("endpoint must be http://127.0.0.1:<port>/v1")
    return value


def _parse_config(source):
    try:
        events = list(yaml.parse(source)) if source.strip() else []
        if any(isinstance(event, yaml.AliasEvent) for event in events):
            raise ValueError("refusing YAML aliases")
        parsed = yaml.compose(source) if source.strip() else None
        data = yaml.safe_load(source) if source.strip() else {}
    except yaml.YAMLError:
        raise ValueError("refusing malformed existing Hermes YAML") from None
    if parsed is not None:

        def check(node):
            if isinstance(node, yaml.MappingNode):
                keys = [
                    key.value
                    for key, _ in node.value
                    if isinstance(key, yaml.ScalarNode)
                ]
                if len(keys) != len(set(keys)):
                    raise ValueError("refusing duplicate keys in Hermes YAML")
                for key, value in node.value:
                    if isinstance(key, yaml.ScalarNode) and key.value == "<<":
                        raise ValueError("refusing YAML merge keys")
                    check(key)
                    if (
                        node is parsed
                        and isinstance(key, yaml.ScalarNode)
                        and key.value == "providers"
                    ):
                        if isinstance(value, yaml.MappingNode) and value.flow_style:
                            raise ValueError("refusing flow-style Hermes providers")
                        if isinstance(value, yaml.MappingNode):
                            for provider_key, provider_value in value.value:
                                if (
                                    isinstance(
                                        provider_value,
                                        (yaml.MappingNode, yaml.SequenceNode),
                                    )
                                    and provider_value.flow_style
                                ):
                                    raise ValueError(
                                        "refusing flow-style Hermes provider entry"
                                    )
                                check(provider_key)
                                check(provider_value)
                    else:
                        check(value)
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    check(child)

        check(parsed)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("refusing malformed Hermes config")
    return data, parsed


def reconcile(source, model, endpoint):
    if not model or not MODEL.fullmatch(model):
        raise ValueError(
            "--model must be a non-empty exact model alias without whitespace/control characters"
        )
    endpoint = validate_endpoint(endpoint)
    parsed, root = _parse_config(source)
    providers_node = (
        next(
            (
                value
                for key, value in root.value
                if isinstance(key, yaml.ScalarNode) and key.value == "providers"
            ),
            None,
        )
        if root
        else None
    )
    providers = parsed.get("providers", {})
    if providers is not None and not isinstance(providers, dict):
        raise ValueError("refusing malformed Hermes providers mapping")
    if "providers" in parsed and (
        not isinstance(providers_node, yaml.MappingNode) or not providers_node.value
    ):
        raise ValueError("refusing non-block or empty providers mapping")
    existing = (providers or {}).get(PROVIDER)
    desired = {
        "name": PROVIDER,
        "api": endpoint,
        "transport": "chat_completions",
        "key_env": KEY_ENV,
        "default_model": model,
    }
    if PROVIDER in (providers or {}):
        if existing == desired:
            return source
        raise ValueError(f"refusing existing providers.{PROVIDER} entry")
    if providers_node is None:
        prefix = source + ("\n" if source and not source.endswith("\n") else "")
        insertion = "providers:\n" + "".join(
            "  " + line
            for line in yaml.safe_dump({PROVIDER: desired}, sort_keys=False).splitlines(
                keepends=True
            )
        )
        result = prefix + insertion
    else:
        # Insert before the first provider, at its existing indentation. End
        # marks can already point at the next root key or include its comments.
        first_key = providers_node.value[0][0]
        insertion = source.rfind("\n", 0, first_key.start_mark.index) + 1
        indent = " " * first_key.start_mark.column
        nested = yaml.safe_dump(desired, default_flow_style=False, sort_keys=False)
        addition = (
            indent
            + f"{PROVIDER}:\n"
            + "".join(indent + "  " + line for line in nested.splitlines(keepends=True))
        )
        result = source[:insertion] + addition + source[insertion:]
    result_data, _ = _parse_config(result)
    expected = dict(parsed)
    expected_providers = dict(providers or {})
    expected_providers[PROVIDER] = desired
    expected["providers"] = expected_providers
    if result_data != expected:
        raise ValueError("refusing generated YAML that changes existing configuration")
    return result


def checked_path(path):
    path = path.absolute()
    # macOS exposes its system temporary directory through this fixed alias.
    if sys.platform == "darwin" and path.parts[:2] == ("/", "var"):
        path = Path("/private/var").joinpath(*path.parts[2:])
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError("refusing symlink Hermes config path or ancestor")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser, no_backup=True)
    parser.add_argument(
        "--model", required=True, help="Exact LiteLLM served model alias"
    )
    parser.add_argument(
        "--endpoint",
        default=f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
        help="Loopback LiteLLM OpenAI-compatible endpoint",
    )
    args = parser.parse_args(argv)
    if (
        os.environ.get(GATE, "0") != "1"
        or os.environ.get("DOTFILES_USE_LITELLM_PROXY", "0") != "1"
    ):
        logger.info(
            "Hermes config scaffold disabled; set %s=1 and DOTFILES_USE_LITELLM_PROXY=1",
            GATE,
        )
        return 0
    try:
        if yaml is None:
            raise ValueError(
                "PyYAML is required; run this generator with poetry run python"
            )
        if not args.model or not MODEL.fullmatch(args.model):
            raise ValueError(
                "--model must be a non-empty exact model alias without whitespace/control characters"
            )
        endpoint = validate_endpoint(args.endpoint)
        path = checked_path(hermes_home() / "config.yaml")
        original_stat = path.lstat() if path.exists() else None
        if original_stat and (
            not stat.S_ISREG(original_stat.st_mode)
            or original_stat.st_uid != os.getuid()
            or original_stat.st_nlink != 1
        ):
            raise ValueError("refusing non-owned, linked or non-regular Hermes config")
        original_bytes = path.read_bytes() if path.exists() else b""
        if b"\r\n" in original_bytes and b"\n" in original_bytes.replace(b"\r\n", b""):
            raise ValueError("refusing mixed newline conventions in Hermes config")
        try:
            source = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("refusing non-UTF-8 Hermes config") from None
        updated = reconcile(source, args.model, endpoint)
        if updated == source:
            logger.info("Hermes LiteLLM provider already configured: %s", path)
            return 0
        if args.dry_run:
            logger.info(
                "Would update Hermes provider config: %s (credential delivery is not qualified)",
                path,
            )
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = path.with_name(path.name + ".bak")
        original_mode = original_stat.st_mode & 0o777 if original_stat else 0o600
        original_hash = hashlib.sha256(original_bytes).hexdigest()
        if path.exists() and not args.no_backup:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(original_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        temporary = None
        try:
            newline = "\r\n" if b"\r\n" in original_bytes else "\n"
            updated_bytes = (
                updated.replace("\r\n", "\n").replace("\n", newline).encode("utf-8")
            )
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(updated_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, original_mode)
            if path.exists():
                current = path.lstat()
                if original_stat is None or (
                    current.st_dev,
                    current.st_ino,
                    current.st_mtime_ns,
                    current.st_size,
                    current.st_mode,
                    current.st_nlink,
                ) != (
                    original_stat.st_dev,
                    original_stat.st_ino,
                    original_stat.st_mtime_ns,
                    original_stat.st_size,
                    original_stat.st_mode,
                    original_stat.st_nlink,
                ):
                    raise ValueError("Hermes config changed concurrently")
                if hashlib.sha256(path.read_bytes()).hexdigest() != original_hash:
                    raise ValueError(
                        "Hermes config changed concurrently; refusing replacement"
                    )
            elif original_stat is not None:
                raise ValueError("Hermes config disappeared concurrently")
            checked_path(path)
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        logger.info("Hermes LiteLLM provider scaffold written: %s", path)
        logger.warning(
            "Hermes credential-pool precedence may supersede key_env; delivery is unqualified"
        )
        return 0
    except (OSError, ValueError) as error:
        logger.error("Hermes configuration refused: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
