import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "configure_agent_guidance", ROOT / "configure-agent-guidance.py"
)
assert SPEC is not None
assert SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class RepoGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.source = self.repo / "shared.md"
        self.source.write_text("## Repository Guidance\n\nShared policy.\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_file(self):
        self.assertTrue(module.stamp_repo_guidance(self.repo, self.source))
        text = (self.repo / "AGENTS.md").read_text()
        self.assertIn(module.REPO_MARKER_START, text)
        self.assertIn("repository-specific guidance", text)

    def test_replace_preserves_outside_markers(self):
        path = self.repo / "AGENTS.md"
        path.write_text(
            "# Local\n\nold\n"
            + module.REPO_MARKER_START
            + "\nold\n"
            + module.REPO_MARKER_END
            + "\n\n# End\n"
        )
        module.stamp_repo_guidance(self.repo, self.source)
        text = path.read_text()
        self.assertTrue(text.startswith("# Local\n\nold\n"))
        self.assertTrue(text.endswith("\n\n# End\n"))
        self.assertIn("Shared policy.", text)

    def test_check_and_dry_run(self):
        self.assertFalse(module.stamp_repo_guidance(self.repo, self.source, check=True))
        self.assertTrue(
            module.stamp_repo_guidance(self.repo, self.source, dry_run=True)
        )
        self.assertFalse((self.repo / "AGENTS.md").exists())
        module.stamp_repo_guidance(self.repo, self.source)
        self.assertTrue(module.stamp_repo_guidance(self.repo, self.source, check=True))


if __name__ == "__main__":
    unittest.main()
