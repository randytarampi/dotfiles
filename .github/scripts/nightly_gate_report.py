"""Truthful, read-only aggregation of scheduled integration gate receipts.

A matrix job may remain allow-failure during stabilization, so only individual
per-gate receipts can distinguish passed, failed, skipped and missing gates.
Never promote a scheduled run to a required pull-request context.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

GATES = (
    "DOTFILES_RUN_OPENCODE_SETUP",
    "DOTFILES_RUN_MCP_SETUP",
    "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
)
STATUSES = {"success", "failure", "cancelled", "skipped"}


def read_receipt(receipt: Path, expected=GATES):
    """Validate one receipt completely; never trust an incomplete success."""
    if receipt.is_symlink() or not receipt.is_file():
        return None, None, ["unsafe receipt"]
    try:
        fields = {}
        for line in receipt.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                return None, None, ["malformed receipt"]
            key, value = line.split("=", 1)
            if key not in ("gate", "result") or key in fields:
                return None, None, ["invalid receipt field"]
            fields[key] = value
    except (OSError, UnicodeError):
        return None, None, ["unreadable receipt"]
    gate, status = fields.get("gate"), fields.get("result")
    if gate not in expected or receipt.name != f"{gate}.txt":
        return None, None, ["unexpected gate receipt"]
    if status not in STATUSES:
        return None, None, ["invalid gate result"]
    return gate, status, []


def summarize(directory: Path, expected=GATES):
    """Reject missing, malformed and failed gate results without false PASS."""
    receipts, errors = {}, []
    if directory.is_dir():
        for path in sorted(directory.glob("*.txt")):
            gate, status, problems = read_receipt(path, expected)
            errors.extend(problems)
            if gate is not None:
                if gate in receipts:
                    errors.append("duplicate gate receipt")
                else:
                    receipts[gate] = status
    states = {gate: receipts.get(gate, "MISSING") for gate in expected}
    healthy = not errors and all(status == "success" for status in states.values())
    return {
        "overall": "PASS" if healthy else "DEGRADED",
        "gates": states,
        "errors": errors,
    }


def markdown(result):
    lines = [
        "## Nightly integration gate results",
        "",
        "| Gate | Result |",
        "| --- | --- |",
    ]
    for gate, status in result["gates"].items():
        lines.append(f"| `{gate}` | {status} |")
    lines.extend(
        [
            "",
            f"**Aggregate: {result['overall']}**",
            "",
            "Configuration-only gates: OpenCode, MCP, agent guidance. This scheduled lane",
            "does not run package installations, privileged services, security hardening,",
            "system defaults or missing-service CodeGraph probes.",
            "",
            "This is a truthful report, not a required pull-request check. Matrix jobs",
            "remain allow-failure during stabilization; consult individual receipts",
            "and issue #212 before promoting the schedule to a hard failure.",
        ]
    )
    if result["errors"]:
        lines.extend(
            ["", "Receipt errors (no secrets): " + ", ".join(result["errors"])]
        )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    result = summarize(args.results)
    if result["overall"] != "PASS":
        print("::warning title=Nightly integration gates degraded::See gate report")
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.summary.open("a", encoding="utf-8") as output:
        output.write(markdown(result))
    args.json.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # No hard failure during the accepted stabilization period. The report
    # clearly labels DEGRADED and survives missing matrix artifacts.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
