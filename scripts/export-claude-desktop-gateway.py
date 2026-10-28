#!/usr/bin/env python3
"""Print an explicit, secret-free Claude Desktop gateway mobileconfig."""

import argparse
import json
import plistlib
import re
import sys
import uuid
from urllib.parse import urlsplit

PAYLOAD_DOMAIN = "com.anthropic.claudefordesktop"
PROFILE_ID = "com.dotfiles.claude-desktop.gateway"


def _plain_text(value, label):
    if (
        not value
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise argparse.ArgumentTypeError(f"{label} must be non-empty plain text")
    return value


def _endpoint(value):
    _plain_text(value, "endpoint")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("endpoint must include a valid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or port is None
        or not 1 <= port <= 65535
    ):
        raise argparse.ArgumentTypeError(
            "endpoint must be http://127.0.0.1:<valid-port> without a path"
        )
    return f"http://127.0.0.1:{port}"


def _helper(value):
    _plain_text(value, "helper executable")
    if not value.startswith("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        raise argparse.ArgumentTypeError("helper executable must be an absolute path")
    return value


def build_payload(endpoint, models, helper):
    return {
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": endpoint,
        "inferenceModels": json.dumps(
            [{"name": model} for model in models], separators=(",", ":")
        ),
        "inferenceCredentialKind": "helper-script",
        "inferenceCredentialHelper": helper,
        "inferenceGatewayAuthScheme": "bearer",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--endpoint", required=True, type=_endpoint)
    parser.add_argument("--model", required=True, action="append", metavar="EXACT_ID")
    parser.add_argument(
        "--helper", required=True, type=_helper, metavar="ABSOLUTE_PATH"
    )
    args = parser.parse_args(argv)
    try:
        try:
            models = [_plain_text(model, "model id") for model in args.model]
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
        if any(any(char.isspace() for char in model) for model in models):
            parser.error("model ids must not contain whitespace")
        output_payload = {
            "PayloadType": PAYLOAD_DOMAIN,
            "PayloadVersion": 1,
            "PayloadIdentifier": PROFILE_ID + ".payload",
            "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, PROFILE_ID + ".payload")),
            "PayloadDisplayName": "Claude Desktop gateway (opt-in)",
            "PayloadEnabled": True,
            "PayloadDescription": (
                "Export only. Review and install separately after consent. "
                "The helper path is a reference, not verified credential delivery."
            ),
        }
        output_payload.update(build_payload(args.endpoint, models, args.helper))
        output = plistlib.dumps(
            {
                "PayloadType": "Configuration",
                "PayloadVersion": 1,
                "PayloadIdentifier": PROFILE_ID,
                "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, PROFILE_ID)),
                "PayloadDisplayName": "Claude Desktop gateway (opt-in)",
                "PayloadContent": [output_payload],
            },
            fmt=plistlib.FMT_XML,
            sort_keys=True,
        )
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
