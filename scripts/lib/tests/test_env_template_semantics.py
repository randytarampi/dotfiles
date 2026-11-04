import hashlib
import re
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[3] / "dot_dotfiles/shell/.env.example"
# Baseline also accounts for deliberate removal of obsolete --local ACP
# sample assignments; maintain order-sensitive protection for all remaining
# environment keys and defaults.
BASELINE_ASSIGNMENTS_SHA256 = (
    "beda1e25ecf7be7cdd8aab80db3a8076d0de261f4c59429527e77cac1affef5c"
)
ASSIGNMENT = re.compile(rb"^\s*(#\s*)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=.*$")


def assignment_records(content):
    records = []
    for line in content.splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            records.append((bool(match.group(1)), line))
    return records


REMOTE_GATES = (
    b"DOTFILES_RUN_TAILSCALE_ACCESS_SETUP",
    b"DOTFILES_RUN_PASEO_SETUP",
    b"DOTFILES_RUN_PASEO_HERMES_ACP_SETUP",
)


def baseline_records(content):
    """Freeze upstream assignments while checking opt-in gates separately."""
    return [
        (commented, line)
        for commented, line in assignment_records(content)
        if not any(line.lstrip(b"# ").startswith(key + b"=") for key in REMOTE_GATES)
    ]


class EnvTemplateSemanticsTests(unittest.TestCase):
    def test_ordered_assignment_records_match_baseline(self):
        records = baseline_records(TEMPLATE.read_bytes())
        digest = hashlib.sha256(b"\n".join(line for _, line in records)).hexdigest()
        self.assertEqual(digest, BASELINE_ASSIGNMENTS_SHA256)

    def test_baseline_detects_status_default_and_order_changes(self):
        original = TEMPLATE.read_bytes()
        mutations = (
            original.replace(b"DO_NOT_TRACK='1'", b"# DO_NOT_TRACK='1'", 1),
            original.replace(b"DO_NOT_TRACK='1'", b"DO_NOT_TRACK='0'", 1),
            original.replace(
                b"DO_NOT_TRACK='1'\nOPENSPEC_TELEMETRY='0'",
                b"OPENSPEC_TELEMETRY='0'\nDO_NOT_TRACK='1'",
                1,
            ),
        )
        for mutated in mutations:
            self.assertNotEqual(mutated, original)
            records = baseline_records(mutated)
            digest = hashlib.sha256(b"\n".join(line for _, line in records)).hexdigest()
            self.assertNotEqual(digest, BASELINE_ASSIGNMENTS_SHA256)

    def test_remote_access_gates_are_comment_only_and_disabled(self):
        lines = TEMPLATE.read_bytes().splitlines()
        seen = set()
        for line in lines:
            for key in REMOTE_GATES:
                if line.lstrip(b"# ").startswith(key + b"="):
                    self.assertNotIn(key, seen, "duplicate remote-access gate")
                    seen.add(key)
                    self.assertTrue(
                        line.startswith(b"# " + key + b"='0'"),
                        f"{key.decode()} must be commented and opt-in",
                    )

    def test_telemetry_defaults_and_litellm_gate_key_order_are_explicit(self):
        records = assignment_records(TEMPLATE.read_bytes())
        by_line = {
            line.split(b"=", 1)[0].lstrip(b"# ").strip(): i
            for i, (_, line) in enumerate(records)
        }
        active = {
            line.split(b"=", 1)[0].strip(): line
            for commented, line in records
            if not commented
        }
        self.assertEqual(active[b"DO_NOT_TRACK"], b"DO_NOT_TRACK='1'")
        self.assertEqual(active[b"OPENSPEC_TELEMETRY"], b"OPENSPEC_TELEMETRY='0'")
        self.assertLess(
            by_line[b"DOTFILES_RUN_LITELLM_SETUP"], by_line[b"LITELLM_MASTER_KEY"]
        )
        self.assertLess(
            by_line[b"LITELLM_MASTER_KEY"], by_line[b"LITELLM_OPENCODE_KEY"]
        )
        self.assertLess(
            by_line[b"LITELLM_OPENCODE_KEY"], by_line[b"LITELLM_COPILOT_CLI_KEY"]
        )
        self.assertLess(
            by_line[b"LITELLM_COPILOT_CLI_KEY"], by_line[b"DOTFILES_RUN_CODEX_SETUP"]
        )
        self.assertLess(
            by_line[b"DOTFILES_RUN_CODEX_SETUP"],
            by_line[b"DOTFILES_CODEX_LITELLM_MODEL"],
        )


if __name__ == "__main__":
    unittest.main()
