import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TESTS_DIR, "..", "..", ".."))

CLEANUP_SPEC = importlib.util.spec_from_file_location(
    "cleanup_project",
    os.path.join(REPO_ROOT, "scripts", "cleanup-project.py"),
)
assert CLEANUP_SPEC is not None and CLEANUP_SPEC.loader is not None
cleanup_project = importlib.util.module_from_spec(CLEANUP_SPEC)
CLEANUP_SPEC.loader.exec_module(cleanup_project)


def run_script(script_name, workspace, *args):
    """Run a repo script against a workspace; return CompletedProcess."""
    return subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", script_name)]
        + ["--workspace-root", workspace]
        + list(args),
        capture_output=True,
        text=True,
        timeout=120,
    )


class RoundTripTests(unittest.TestCase):
    """configure-project → seed network-bound artifacts → cleanup-project → assert clean.

    The full configure-project flow needs network, Ollama, codegraph, and
    JetBrains state, so the test runs the steps that are self-contained
    (opencode/jetbrains/skills) for real, seeds the artifacts the
    network-bound steps (tier/codegraph/mcps/pi/acp-agents/secrets) would
    write, then runs cleanup-project and asserts only user-authored and
    runtime files survive.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def seed_generated_artifacts(self):
        root = Path(self.workspace)
        (root / ".opencode" / "skills").mkdir(parents=True, exist_ok=True)
        (root / ".codegraph").mkdir()
        (root / ".ai" / "mcp").mkdir(parents=True)
        (root / ".ai" / "plans").mkdir()
        (root / ".aiassistant").mkdir()
        (root / ".pi" / "agent").mkdir(parents=True)
        (root / ".cursor").mkdir()
        # opencode step (generated marker: disabled_providers)
        (root / "opencode.json").write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "provider": {},
                    "disabled_providers": [],
                    "agent": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # skills step (skill-deny config + canonical-store symlink)
        (root / ".opencode" / "opencode.json").write_text("{}\n", encoding="utf-8")
        # configure-project (core profile) may already have linked add-lang.
        link = root / ".opencode" / "skills" / "add-lang"
        if not link.exists():
            os.symlink(
                os.path.expanduser("~/.local/share/dotfiles/skills/add-lang"),
                link,
            )
        # tier step
        (root / ".opencode" / "oh-my-opencode-slim.json").write_text(
            json.dumps({"preset": "pro-plus"}) + "\n", encoding="utf-8"
        )
        # acp-agents step
        (root / ".opencode" / "acp-agents.json").write_text(
            json.dumps({"acpAgents": {}}) + "\n", encoding="utf-8"
        )
        # secrets step
        (root / ".opencode" / ".env.local").write_text("X=1\n", encoding="utf-8")
        # codegraph step
        (root / ".codegraph" / "codegraph.db").write_text("db", encoding="utf-8")
        # jetbrains/junie steps
        os.symlink(".ai", root / ".junie")
        os.symlink("../.ai/rules", root / ".aiassistant" / "rules")
        (root / ".ai" / "mcp" / "mcp.json").write_text(
            json.dumps({"mcpServers": {"codegraph": {"command": "codegraph"}}}),
            encoding="utf-8",
        )
        for name in ("review", "agents", "rules"):
            (root / ".ai" / name).mkdir()
        # pi step
        (root / ".pi" / "agent" / "settings.json").write_text("{}", encoding="utf-8")
        (root / ".pi" / "agent" / "models.json").write_text("{}", encoding="utf-8")
        # mcps step (cursor merge with only known template servers)
        (root / ".cursor" / "mcp.json").write_text(
            json.dumps({"mcpServers": {"context7": {}, "codegraph": {}}}),
            encoding="utf-8",
        )

    def seed_user_and_runtime_files(self):
        root = Path(self.workspace)
        # user-authored env (must survive)
        (root / ".opencode" / ".env").write_text(
            "# user authored\nFOO=1\n", encoding="utf-8"
        )
        # runtime artifacts (must survive)
        (root / ".opencode" / "images").mkdir()
        (root / ".opencode" / "images" / "ses_abc.png").write_text(
            "x", encoding="utf-8"
        )
        (root / ".opencode" / "package.json").write_text("{}", encoding="utf-8")
        # hand-authored skill file (must survive)
        (root / ".opencode" / "skills" / "my-custom-skill").write_text(
            "custom", encoding="utf-8"
        )
        # user plan (must survive)
        (root / ".ai" / "plans" / "esm-to-dist-migration.md").write_text(
            "plan", encoding="utf-8"
        )
        # MCP merge with a user-declared server (must survive)
        (root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"my-custom-server": {}}}), encoding="utf-8"
        )
        # hand-authored root config without the generated marker (must survive)
        (root / "hand-authored.json").write_text("{}", encoding="utf-8")

    def test_configure_then_cleanup_round_trip(self):
        # 1. Run configure-project for the self-contained steps.
        result = run_script(
            "configure-project.py",
            self.workspace,
            "--steps",
            "opencode,jetbrains,skills",
            "--skill-profiles",
            "core",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])
        # configure-project created .opencode + skills symlinks (jetbrains
        # no-ops without .idea/modules.xml).
        skills_dir = Path(self.workspace) / ".opencode" / "skills"
        self.assertTrue(skills_dir.is_dir())
        self.assertGreater(len(list(skills_dir.iterdir())), 0)

        # 2. Seed artifacts of the network-bound steps.
        self.seed_generated_artifacts()
        self.seed_user_and_runtime_files()

        # 3. Dry-run: nothing is deleted.
        result = run_script("cleanup-project.py", self.workspace, "--dry-run")
        self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])
        self.assertTrue((Path(self.workspace) / "opencode.json").exists())

        # 4. Real cleanup.
        result = run_script("cleanup-project.py", self.workspace, "--force")
        self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])

        root = Path(self.workspace)
        # Generated artifacts are gone.
        gone = [
            root / "opencode.json",
            root / ".opencode" / "opencode.json",
            root / ".opencode" / "oh-my-opencode-slim.json",
            root / ".opencode" / "acp-agents.json",
            root / ".opencode" / ".env.local",
            root / ".codegraph",
            root / ".junie",
            root / ".aiassistant",
            root / ".ai" / "mcp" / "mcp.json",
            root / ".ai" / "mcp",
            root / ".ai" / "review",
            root / ".ai" / "agents",
            root / ".ai" / "rules",
            root / ".pi",
            root / ".cursor" / "mcp.json",
        ]
        for path in gone:
            self.assertFalse(path.exists(), msg=f"should be removed: {path}")
        # User-authored and runtime files survive.
        kept = [
            root / ".opencode",  # .env, images, package.json remain
            root / ".opencode" / ".env",
            root / ".opencode" / "images" / "ses_abc.png",
            root / ".opencode" / "package.json",
            root / ".opencode" / "skills" / "my-custom-skill",
            root / ".ai",
            root / ".ai" / "plans" / "esm-to-dist-migration.md",
            root / ".mcp.json",
        ]
        for path in kept:
            self.assertTrue(path.exists(), msg=f"should be kept: {path}")
        # A second run removes nothing (idempotent); kept dirs are logged.
        result = run_script("cleanup-project.py", self.workspace, "--force")
        self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])
        self.assertIn("removed: 0", result.stdout)
        self.assertNotIn("Removed /", result.stdout)

    def test_hand_authored_root_config_is_kept(self):
        root = Path(self.workspace)
        root.mkdir(parents=True, exist_ok=True)
        # Minimal per-key override: no disabled_providers marker.
        (root / "opencode.json").write_text(
            json.dumps({"$schema": "https://opencode.ai/config.json", "model": "x/y"}),
            encoding="utf-8",
        )
        actions = cleanup_project.plan_cleanup(str(root), ["opencode"])
        self.assertEqual(
            [action["path"] for action in actions if "opencode.json" in action["path"]],
            [],
        )

    def test_symlinked_root_config_is_kept(self):
        root = Path(self.workspace)
        root.mkdir(parents=True, exist_ok=True)
        os.symlink(
            os.path.join(REPO_ROOT, "scripts", "cleanup-project.py"),
            root / "opencode.json",
        )
        actions = cleanup_project.plan_cleanup(str(root), ["opencode"])
        self.assertFalse(
            any(action["path"].endswith("opencode.json") for action in actions)
        )

    def test_mcp_merge_with_internal_template_names_is_removed(self):
        # Project mode uses templates' internal "name" fields, which differ
        # from the filenames for mongodb ("MongoDB") and notion ("Notion").
        root = Path(self.workspace)
        (root / ".cursor").mkdir(parents=True)
        (root / ".cursor" / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "MongoDB": {"command": "x"},
                        "Notion": {"command": "y"},
                    }
                }
            ),
            encoding="utf-8",
        )
        actions = cleanup_project.plan_cleanup(str(root), ["mcps"])
        self.assertTrue(
            any(action["path"].endswith(".cursor/mcp.json") for action in actions)
        )


if __name__ == "__main__":
    unittest.main()
