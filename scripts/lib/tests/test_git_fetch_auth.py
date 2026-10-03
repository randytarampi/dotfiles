"""Exercise trusted Git fetch auth with a fake git command and dummy token."""

import base64
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
TRUSTED_SHA = "0123456789abcdef0123456789abcdef01234567"
DUMMY_TOKEN = "test-only-dummy-token"
TRUSTED_URL = "https://github.com/randytarampi/dotfiles.git"


def _fetch_fragments():
    expected = {
        ".github/workflows/agentic-review.yml": 3,
        ".github/workflows/agentic-review-fix.yml": 1,
        ".github/workflows/copilot-setup-steps.yml": 1,
    }
    found = []
    for relative_path, count in expected.items():
        workflow = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
        for job_name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                if not step.get("name", "").startswith("Stage and verify trusted"):
                    continue
                run = step["run"]
                assert 'git -C "${trusted_root}"' in run
                assert "rev-parse HEAD" in run and "verify-ci-assets.py" in run
                lines = run.splitlines()
                fetch_index = next(
                    i
                    for i, line in enumerate(lines)
                    if "git -C" in line and " fetch " in line
                )
                auth_index = max(
                    i
                    for i, line in enumerate(lines[:fetch_index])
                    if "git_auth=" in line
                )
                found.append(
                    (
                        relative_path,
                        job_name,
                        "\n".join(lines[auth_index : fetch_index + 1]),
                    )
                )
        assert sum(item[0] == relative_path for item in found) == count
    assert len(found) == 5
    return found


def _git_stub(report: Path, root: Path) -> str:
    return f"""#!{sys.executable}
import base64, json, os, sys
from pathlib import Path
args = sys.argv[1:]
root = {str(root)!r}
url = {TRUSTED_URL!r}
sha = {TRUSTED_SHA!r}
token = {DUMMY_TOKEN!r}
report = Path({str(report)!r})
if len(args) != 6 or args[:3] != ["-C", root, "fetch"]:
    raise SystemExit(2)
header = os.environ.get("GIT_CONFIG_VALUE_0", "")
try:
    decoded = base64.b64decode(header.split("Basic ", 1)[1]).decode()
except (IndexError, ValueError, UnicodeDecodeError):
    decoded = ""
token_in_argv = any(token in arg or "Bearer " in arg for arg in args)
token_in_env = "GITHUB_TOKEN" in os.environ
url, sha_arg = args[-2:]
ok = (
    url == {TRUSTED_URL!r}
    and sha_arg == {TRUSTED_SHA!r}
    and decoded == "x-access-token:{DUMMY_TOKEN}"
    and os.environ.get("GIT_CONFIG_KEY_0") == "http.https://github.com/.extraheader"
    and os.environ.get("GIT_CONFIG_COUNT") == "1"
    and os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"
    and os.environ.get("GIT_CONFIG_GLOBAL") == "/dev/null"
    and os.environ.get("GIT_TERMINAL_PROMPT") == "0"
    and "GIT_CONFIG_PARAMETERS" not in os.environ
    and not token_in_argv
    and not token_in_env
    and "@" not in url.split("//", 1)[-1].split("/", 1)[0]
)
report.write_text(json.dumps({{"url": url, "sha": sha_arg, "ok": ok,
    "token_in_argv": token_in_argv, "token_in_env": token_in_env,
    "parameters_in_env": "GIT_CONFIG_PARAMETERS" in os.environ}}))
raise SystemExit(0 if ok else 128)
"""


def test_fetch_auth(tmp_path):
    encoded = base64.b64encode(f"x-access-token:{DUMMY_TOKEN}".encode()).decode()
    for index, (workflow, job, fragment) in enumerate(_fetch_fragments()):
        case = tmp_path / f"case-{index}"
        fake_bin = case / "bin"
        home = case / "home"
        trusted_root = case / "trusted-dotfiles"
        fake_bin.mkdir(parents=True)
        home.mkdir()
        report = case / "git-report.json"
        (fake_bin / "git").write_text(_git_stub(report, trusted_root), encoding="utf-8")
        (fake_bin / "git").chmod(0o755)
        env = {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "TRUSTED_REF": TRUSTED_SHA,
            "GITHUB_SHA": TRUSTED_SHA,
            "GITHUB_TOKEN": DUMMY_TOKEN,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": "Authorization: Bearer old-conflict",
            "GIT_CONFIG_PARAMETERS": "http.extraheader=Authorization: Bearer old-conflict",
        }
        script = (
            "set -euo pipefail\n"
            f'TRUSTED_REF="{TRUSTED_SHA}"\n'
            f'trusted_sha="{TRUSTED_SHA}"\n'
            f'trusted_root="{trusted_root}"\n' + fragment
        )
        result = subprocess.run(
            ["/bin/bash", "-euo", "pipefail", "-c", script],
            env=env,
            cwd=case,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, f"{workflow} {job}: {result.stderr}"
        report_text = report.read_text(encoding="utf-8")
        assert json.loads(report_text) == {
            "url": TRUSTED_URL,
            "sha": TRUSTED_SHA,
            "ok": True,
            "token_in_argv": False,
            "token_in_env": False,
            "parameters_in_env": False,
        }
        assert DUMMY_TOKEN not in report_text
        assert encoded not in report_text
        assert result.stdout.startswith("::add-mask::")
        assert script.index("::add-mask::") < script.index("GIT_CONFIG_VALUE_0=")
        assert not (trusted_root / ".git" / "config").exists()
