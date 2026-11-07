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


def summarize(directory: Path, expected=GATES):
    """Return deterministic gate receipts and overall status without guessing."""
    receipts = {}
    errors = []
    if directory.exists():
        for receipt in sorted(directory.glob("*.txt")):
            if not receipt.is_file() or receipt.is_symlink():
                errors.append("unsafe receipt")
                continue
            values = {}
            for line in receipt.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    errors.append("malformed receipt")
                    continue
                key, value = line.split("=", 1)
                if key in values or key not in ("gate", "result"):
                    errors.append("invalid receipt field")
                values[key] = value
            gate, result = values.get("gate"), values.get("result")
            if gate not in expected or receipt.name != f"{gate}.txt":
                errors.append("unexpected gate receipt")
            elif gate in receipts:
                errors.append("duplicate gate receipt")
            elif result not in STATUSES:
                errors.append("invalid gate result")
            else:
                receipts[gate] = result
    missing = [gate for gate in expected if gate not in receipts]
    states = {gate: receipts.get(gate, "MISSING") for gate in expected}
    healthy = not errors and not missing and all(v == "success" for v in states.values())
    return {"overall": "PASS" if healthy else "DEGRADED", "gates": states, "errors": errors}


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
        lines.extend(["", "Receipt errors (no secrets): " + ", ".join(result["errors"])])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    result = summarize(args.results)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.summary.open("a", encoding="utf-8") as output:
        output.write(markdown(result))
    args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # No hard failure during the accepted stabilization period. The report
    # clearly labels DEGRADED and survives missing matrix artifacts.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
