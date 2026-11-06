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
    assert "artifacts/model-catalogues/opencode-zen-free.json" in workflow
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
