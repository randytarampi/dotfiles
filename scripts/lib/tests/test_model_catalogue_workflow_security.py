from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/refresh-model-catalogues.yml"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_refresh_workflow_is_weekly_and_has_manual_dispatch():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'cron: "0 6 * * 1"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert 'GITHUB_EVENT_NAME}" == "workflow_dispatch"' in workflow
    assert 'GITHUB_ACTOR}" = "${GITHUB_REPOSITORY_OWNER}' in workflow
    assert 'GITHUB_REF_NAME}" = "${DEFAULT_BRANCH}' in workflow
    assert "gh api user" not in workflow
    assert "TRUSTED_SHA" in workflow
    assert workflow.count("TRUSTED_SHA: ${{ inputs.trusted_ref || github.sha }}") == 2
    assert workflow.count('test -n "${TRUSTED_REF}"') == 2
    assert (
        "required: true"
        in workflow.split("workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
    )
    assert "create-github-app-token@" in workflow
    assert "GH_TOKEN: ${{ github.token }}" not in workflow.split("  publish:", 1)[1]


def test_publish_permissions_and_environment_are_isolated():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    refresh, publish = workflow.split("  publish:", 1)
    assert "contents: write" not in refresh
    assert "pull-requests: write" not in refresh
    assert "environment: model-catalogue-publish" in publish
    assert "contents: read" in publish
    assert "pull-requests: read" in publish
    assert "permission-contents: write" in publish
    assert "permission-pull-requests: write" in publish
    assert 'test "$(git rev-parse HEAD)" = "${TRUSTED_SHA}"' in publish
    assert 'push origin "HEAD:refs/heads/main"' not in publish


def test_publish_branch_and_patch_path_are_strictly_allowlisted():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "^model-catalogue-bot/[a-z0-9-]+$" in workflow
    assert 'publish_branch="model-catalogue-bot/inventory"' in workflow
    assert "artifacts/model-catalogues/free-model-candidates.json" in workflow
    assert "git ls-files --stage" in workflow
    assert "160000" in workflow
    assert (
        '--force-with-lease="refs/heads/${publish_branch}:${BOT_BRANCH_EXPECTED_TIP}"'
        in workflow
    )
    assert "gh pr create --draft" in workflow
    assert "gh pr edit" in workflow
    assert 'git diff --name-only "origin/${DEFAULT_BRANCH}...HEAD"' in workflow


def test_refresher_uses_shared_catalogue_fetcher_without_duplicate_http_code():
    refresher = read("scripts/refresh-model-catalogues.py")
    shared = read("scripts/lib/model_catalogues.py")
    assert "from model_catalogues import get_catalogue" in refresher
    assert "urllib.request" not in refresher
    # W8B-A: all shared-fetch HTTP goes through model_catalogues.open_same_origin
    # (redirect-gated); direct urllib.request.urlopen calls no longer exist.
    assert "open_same_origin" in shared


def test_publication_credentials_are_checked_before_app_token_creation():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publish = workflow.split("  publish:\n", 1)[1]
    preflight = publish.split(
        "      - name: Validate GitHub App publication credentials\n", 1
    )[1].split("      - id: publish_app_token\n", 1)[0]
    assert publish.index(
        "      - name: Validate GitHub App publication credentials"
    ) < publish.index("      - id: publish_app_token")
    assert (
        "HAS_APP_CLIENT_ID: ${{ vars.WOOFDY_GITHUB_APP_CLIENT_ID != '' }}" in preflight
    )
    assert (
        "HAS_APP_PRIVATE_KEY: ${{ secrets.WOOFDY_GITHUB_APP_PRIVATE_KEY != '' }}"
        in preflight
    )
    assert (
        '"${HAS_APP_CLIENT_ID}" != "true" || "${HAS_APP_PRIVATE_KEY}" != "true"'
        in preflight
    )
    assert "docs/MODEL_UPDATES.md#publishing-credentials-and-recovery" in preflight
    assert "client-id: ${{ vars.WOOFDY_GITHUB_APP_CLIENT_ID }}" in publish
    assert "private-key: ${{ secrets.WOOFDY_GITHUB_APP_PRIVATE_KEY }}" in publish
    assert "app-id:" not in publish
    assert "secrets.APP_ID" not in publish


def test_publication_credentials_preflight_fails_closed_without_values():
    import subprocess

    workflow = WORKFLOW.read_text(encoding="utf-8")
    preflight = workflow.split(
        "      - name: Validate GitHub App publication credentials\n", 1
    )[1].split("      - id: publish_app_token\n", 1)[0]
    run_block = preflight.split("        run: |\n", 1)[1]
    script = "\n".join(
        line[10:] for line in run_block.splitlines() if line.startswith("          ")
    )
    for has_id, has_private_key, expected_exit in (
        ("false", "false", 1),
        ("true", "false", 1),
        ("false", "true", 1),
        ("true", "true", 0),
    ):
        outcome = subprocess.run(
            ["bash", "-c", script],
            env={"HAS_APP_CLIENT_ID": has_id, "HAS_APP_PRIVATE_KEY": has_private_key},
            capture_output=True,
            text=True,
            check=False,
        )
        assert outcome.returncode == expected_exit
        if expected_exit:
            assert (
                "::error title=Missing GitHub App publication credentials::"
                in outcome.stdout
            )


def test_actionlint_woofdy_client_id_compatibility_is_scoped():
    import yaml

    config = yaml.safe_load(read(".github/actionlint.yaml"))
    paths = config["paths"]
    allowed = {
        ".github/workflows/refresh-model-catalogues.yml",
        ".github/workflows/agentic-review-fix.yml",
    }
    known_false_positives = [
        'missing input "app-id" which is required by action '
        '"actions/create-github-app-token@v3"',
        'input "client-id" is not defined in action '
        '"actions/create-github-app-token@v3"',
    ]
    assert set(config) == {"paths"}
    assert set(paths) == allowed
    for path in allowed:
        assert paths[path]["ignore"] == known_false_positives
        workflow = read(path)
        assert "client-id: ${{ vars.WOOFDY_GITHUB_APP_CLIENT_ID }}" in workflow
        assert "private-key: ${{ secrets.WOOFDY_GITHUB_APP_PRIVATE_KEY }}" in workflow
        assert "app-id:" not in workflow


def test_publication_push_uses_scoped_github_cli_app_credentials():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publish = workflow.split("  publish:\n", 1)[1]
    helper = "git -c credential.helper='!gh auth git-credential' push"
    assert publish.count(helper) == 1
    assert "GH_TOKEN: ${{ steps.publish_app_token.outputs.token }}" in publish
    assert 'GIT_TERMINAL_PROMPT: "0"' in publish
    assert "persist-credentials: false" in publish
    assert "http.extraheader" not in publish
    assert "Authorization: Bearer" not in publish
    assert "x-access-token:${GH_TOKEN}" not in publish


def test_owner_only_issue_comment_dispatch_keeps_secret_job_gated():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    refresh, publish = workflow.split("  publish:\n", 1)
    assert "issue_comment:\n    types: [created]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    required = (
        "github.actor == github.repository_owner",
        "github.event.issue.number == 197",
        "github.event.issue.pull_request == null",
        "github.event.comment.body == '/refresh-model-catalogues'",
    )
    for condition in required:
        assert condition in refresh.split("    runs-on:", 1)[0]
    assert refresh.count('test "${COMMENT_ISSUE}" = "197"') == 1
    assert publish.count('test "${COMMENT_ISSUE}" = "197"') == 1
    assert workflow.count('test "${TRUSTED_SHA}" = "${GITHUB_SHA}"') == 2
    assert "GH_TOKEN: ${{ github.token }}" in refresh
    assert "environment: model-catalogue-publish" in publish


def test_owner_comment_validation_rejects_wrong_actor_issue_body_and_pr():
    import subprocess

    workflow = WORKFLOW.read_text(encoding="utf-8")
    sha = "a" * 40
    base_env = {
        "GITHUB_EVENT_NAME": "issue_comment",
        "GITHUB_ACTOR": "randytarampi",
        "GITHUB_REPOSITORY_OWNER": "randytarampi",
        "GITHUB_REF_NAME": "main",
        "DEFAULT_BRANCH": "main",
        "TRUSTED_SHA": sha,
        "GITHUB_SHA": sha,
        "TRUSTED_REF": "",
        "COMMENT_ISSUE": "197",
        "COMMENT_IS_PR": "false",
        "COMMENT_BODY": "/refresh-model-catalogues",
    }
    cases = [
        ({}, 0),
        ({"GITHUB_ACTOR": "untrusted-user"}, 1),
        ({"COMMENT_ISSUE": "198"}, 1),
        ({"COMMENT_IS_PR": "true"}, 1),
        ({"COMMENT_BODY": "/refresh-model-catalogues extra"}, 1),
        ({"TRUSTED_REF": sha}, 1),
        ({"TRUSTED_SHA": "b" * 40}, 1),
        ({"GITHUB_REF_NAME": "feature"}, 1),
    ]
    for name in (
        "Validate the trusted manual dispatch boundary",
        "Validate publication inputs",
    ):
        block = workflow.split(f"      - name: {name}\n", 1)[1]
        block = block.split("      - name:", 1)[0]
        lines = block.split("        run: |\n", 1)[1].splitlines()
        script = "\n".join(line[10:] for line in lines if line.startswith("          "))
        for overrides, expected in cases:
            outcome = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, **overrides},
                capture_output=True,
                check=False,
            )
            assert outcome.returncode == expected, (name, overrides)


def test_publish_noop_is_scoped_to_an_open_pr_and_timestamp_only_diffs():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publish = workflow.split("  publish:\n", 1)[1]
    marker = "Preserve an existing draft when the only differences"
    assert marker in publish
    before, after = publish.split(marker, 1)
    assert before.index("git add --") < before.index("Publish the bot branch")
    assert after.index("Catalogue unchanged apart from observation times") < (
        after.index("git commit -m")
    )
    assert '[[ "${BOT_BRANCH_EXISTS}" == "true" ]]' in after
    assert '[[ -n "${existing_pr}" ]]' in after
    assert (
        "git cat-file -e"
        ' "${BOT_BRANCH_EXPECTED_TIP}:artifacts/model-catalogues/'
        'free-model-candidates.json"'
    ) in after
    assert "del(.fetched_at, .pricing_observed_at, .pricing_expires_at)" in after
    assert "git -c credential.helper='!gh auth git-credential' push" in after


def test_publish_noop_runs_only_for_equal_semantics_and_open_pr(tmp_path):
    import json
    import os
    import shutil
    import subprocess

    if not shutil.which("git") or not shutil.which("jq"):
        import pytest

        pytest.skip("Git and jq are required for the workflow-shell regression")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    publish = workflow.split(
        "      - name: Publish the bot branch and create or update its draft PR\n",
        1,
    )[1]
    start = publish.index('          publish_branch="model-catalogue-bot/inventory"')
    end = publish.index('          bot_login="woofdy-randytarampi-ca[bot]"')
    shell = "\n".join(
        line[10:]
        for line in publish[start:end].splitlines()
        if line.startswith("          ")
    )
    assert "existing_prs=" in shell
    assert "TRUSTED_SHA" not in shell
    checkout = tmp_path / "repo"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "config", "user.name", "woofdy-randytarampi-ca[bot]"],
        cwd=checkout,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "353209076+woofdy-randytarampi-ca[bot]@users.noreply.github.com",
        ],
        cwd=checkout,
        check=True,
    )
    registry = checkout / "configs/opencode/opencode-models.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"models":{"big-pickle":{}}}', encoding="utf-8")
    path = checkout / "artifacts/model-catalogues/free-model-candidates.json"
    path.parent.mkdir(parents=True)
    baseline = {
        "status": "available",
        "source": "https://opencode.ai/zen/v1/models",
        "pricing_source": "https://models.dev/api.json",
        "fetched_at": "2026-11-06T18:58:00Z",
        "pricing_observed_at": "2026-11-06T18:58:00Z",
        "pricing_expires_at": "2026-11-07T18:58:00Z",
        "counts": {"catalogue": 1, "classified": 1, "free": 1, "unclassified": 0},
        "models": [{"id": "big-pickle", "pricing": {"input": 0, "output": 0}}],
    }
    path.write_text(json.dumps(baseline) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-qm",
            "chore(agents): refresh free-model candidate inventory",
        ],
        cwd=checkout,
        check=True,
    )
    tip_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    subprocess.run(["git", "branch", "main"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(checkout)],
        cwd=checkout,
        check=True,
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        '  list) printf "%s" "${GH_PR_JSON:-[]}" ;;\n'
        '  view) printf "%s" "${GH_PR_DETAILS}" ;;\n'
        '  edit) if [ "${GH_PR_EDIT_FAIL:-0}" = 1 ]; then exit 42; fi ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "DEFAULT_BRANCH": "main",
            "BOT_BRANCH_EXISTS": "true",
            "BOT_BRANCH_EXPECTED_TIP": tip_sha,
        }
    )

    valid = {
        "number": 200,
        "isDraft": True,
        "baseRefName": "main",
        "headRefName": "model-catalogue-bot/inventory",
        "author": {"login": "woofdy-randytarampi-ca[bot]"},
    }

    report_dir = tmp_path / "model-catalogue"
    report_dir.mkdir()
    marker = "<!-- free-model-review-sha256: " + "a" * 64 + " -->"
    (report_dir / "review-pr.md").write_text(marker + "\n", encoding="utf-8")
    good_details = {
        "title": "chore(agents): review free-model candidates",
        "body": (
            "## Outcome and scope\n## Contributors and roles\n"
            "## Evidence\n## Review disposition\n## Risk and handoff\n" + marker
        ),
    }

    def evaluate(data, prs, *, success=True, details=None, tip=None, edit_fail=False):
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", shell],
            cwd=checkout,
            env={
                **env,
                "GH_PR_JSON": json.dumps(prs),
                "GH_PR_DETAILS": json.dumps(details or good_details),
                "GH_PR_EDIT_FAIL": "1" if edit_fail else "0",
                "RUNNER_TEMP": str(tmp_path),
                "BOT_BRANCH_EXPECTED_TIP": tip or tip_sha,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        if success:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0, result.stdout
            assert "Catalogue unchanged" not in result.stdout
        return result.stdout

    timestamp_only = {
        **baseline,
        "fetched_at": "2026-11-06T19:01:00Z",
        "pricing_observed_at": "2026-11-06T19:01:00Z",
        "pricing_expires_at": "2026-11-07T19:01:00Z",
    }
    assert "Catalogue unchanged" in evaluate(timestamp_only, [valid])
    stale = dict(good_details, body="## Outcome and scope\nold evidence")
    assert "Repaired stale catalogue PR metadata" in evaluate(
        timestamp_only, [valid], details=stale
    )
    assert "Repaired stale catalogue PR metadata" in evaluate(
        timestamp_only, [valid], details=dict(good_details, title="stale title")
    )
    evaluate(timestamp_only, [valid], details=stale, edit_fail=True, success=False)
    assert "Catalogue unchanged" not in evaluate(timestamp_only, [])
    price_changed = {
        **timestamp_only,
        "models": [{"id": "big-pickle", "pricing": {"input": 0, "output": 1}}],
    }
    assert "Catalogue unchanged" not in evaluate(price_changed, [valid])
    for invalid in (
        {**valid, "isDraft": False},
        {**valid, "baseRefName": "other"},
        {**valid, "headRefName": "other-branch"},
        {**valid, "author": {"login": "someone-else"}},
    ):
        evaluate(timestamp_only, [invalid], success=False)
    evaluate(timestamp_only, [valid, valid], success=False)

    # A closed draft on a known bot tip can be re-opened, but a human-owned
    # branch must not be destroyed by force-with-lease just because no PR exists.
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=human",
            "-c",
            "user.email=human@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "human-owned branch",
        ],
        cwd=checkout,
        check=True,
    )
    foreign_tip = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    evaluate(timestamp_only, [], tip=foreign_tip, success=False)


def test_candidate_report_uses_template_and_woofdy_commit_identity():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    refresh, publish = workflow.split("  publish:\n", 1)
    assert "free-model-candidates.json" in refresh
    assert "Prepare a template-compliant candidate review" in publish
    assert "Refusing to update a non-draft or foreign catalogue PR" in publish
    assert "Catalogue branch has no open PR and an unrecognised commit" in publish
    assert "6fbf14bfeec1222b0ae376b500e1888384a26e4d" in publish
    assert "Curated model registry changed during refresh" in publish
    assert publish.index("Check the curated source before") < publish.index(
        "Preserve an existing draft when the only differences"
    )
    assert "Repaired stale catalogue PR metadata" in publish
    assert "free-model-review-sha256" in publish
    assert "scripts/lib/catalogue_review.py" in publish
    assert "Invalid trusted publishing SHA" in publish
    assert 'bot_login="woofdy-randytarampi-ca[bot]"' in publish
    assert "353209076+${bot_login}@users.noreply.github.com" in publish
    assert 'git config user.name "${bot_login}"' in publish
    assert (
        'git commit -m "chore(agents): refresh free-model candidate inventory"'
        in publish
    )
    assert (
        'gh pr create --draft --base "${DEFAULT_BRANCH}" '
        '--head "${publish_branch}"' in publish
    )
    assert 'gh pr edit "${existing_pr}"' in publish
    assert publish.count('--body-file "${review_body}"') == 3
    assert "model-catalogue-bot[bot]@users.noreply.github.com" not in publish
    assert (
        'git show "${BOT_BRANCH_EXPECTED_TIP}:artifacts/model-catalogues/'
        'opencode-zen-free.json"' in publish
    )
    assert (
        '[[ "${published_paths[0]}" == "artifacts/model-catalogues/'
        'free-model-candidates.json" ]]' in publish
    )
    # The renamed source path is only accepted for reading old PR #200 data.
    allowed = publish.split('allowed_path="', 1)[1].split('"', 1)[0]
    assert allowed == "artifacts/model-catalogues/free-model-candidates.json"
    assert 'git add -- "${allowed_path}"' in publish
    assert "artefact" not in workflow


def test_manual_dispatch_sha_must_be_default_branch_ancestor(tmp_path):
    import os
    import subprocess

    workflow = WORKFLOW.read_text(encoding="utf-8")
    section = workflow.split(
        "      - name: Validate the trusted manual dispatch boundary\n", 1
    )[1].split("      - name: Check out the triggering commit\n", 1)[0]
    run_block = section.split("        run: |\n", 1)[1]
    shell = "\n".join(
        line[10:] for line in run_block.splitlines() if line.startswith("          ")
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *branches/main*) printf "%s" "${MOCK_DEFAULT_SHA}" ;;\n'
        '  *compare/*) printf "%s" "${MOCK_ANCESTRY}" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    trusted = "a" * 40
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_ACTOR": "randytarampi",
        "GITHUB_REPOSITORY_OWNER": "randytarampi",
        "GITHUB_REPOSITORY": "randytarampi/dotfiles",
        "GITHUB_REF_NAME": "main",
        "DEFAULT_BRANCH": "main",
        "TRUSTED_REF": trusted,
        "TRUSTED_SHA": trusted,
        "MOCK_DEFAULT_SHA": "b" * 40,
        "GH_TOKEN": "synthetic-contents-read-token",
    }
    for status, expected in (
        ("identical", 0),
        ("ahead", 0),
        ("diverged", 1),
        ("behind", 1),
        ("", 1),
    ):
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", shell],
            env={**env, "MOCK_ANCESTRY": status},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) == (expected == 0), status
        if expected:
            assert "not on default-branch history" in result.stderr
    for changed in (
        {"MOCK_DEFAULT_SHA": "bad"},
        {"MOCK_DEFAULT_SHA": "z" * 40},
        {"TRUSTED_SHA": "not-a-sha"},
        {"TRUSTED_SHA": "z" * 40},
        {"GITHUB_ACTOR": "foreign"},
        {"GITHUB_REF_NAME": "untrusted"},
    ):
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", shell],
            env={**env, **changed, "MOCK_ANCESTRY": "ahead"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, changed


def test_curated_registry_drift_gate_rejects_stale_snapshot(tmp_path):
    import os
    import subprocess

    workflow = WORKFLOW.read_text(encoding="utf-8")
    publish = workflow.split("  publish:\n", 1)[1]
    section = publish.split(
        "          # Check the curated source before", 1
    )[1].split(
        "          # Preserve an existing draft when", 1
    )[0]
    block = section.splitlines()
    start = next(i for i, line in enumerate(block) if "trusted_registry=" in line)
    script = "\n".join(
        line[10:] for line in block[start:] if line.startswith("          ")
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI fixture"],
        cwd=repo,
        check=True,
    )
    registry = repo / "configs/opencode/opencode-models.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"models":{"a":{}}}', encoding="utf-8")
    subprocess.run(["git", "add", str(registry)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()

    def run():
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            cwd=repo,
            env={**os.environ, "DEFAULT_BRANCH": "main"},
            capture_output=True,
            text=True,
            check=False,
        )

    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base],
        cwd=repo,
        check=True,
    )
    assert run().returncode == 0
    registry.write_text('{"models":{"b":{}}}', encoding="utf-8")
    subprocess.run(["git", "add", str(registry)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "updated registry"], cwd=repo, check=True)
    new_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", new_head],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "--detach", base], cwd=repo, check=True)
    outcome = run()
    assert outcome.returncode != 0
    assert "Curated model registry changed during refresh" in outcome.stderr
