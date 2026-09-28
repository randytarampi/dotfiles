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


def test_publish_permissions_and_environment_are_isolated():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    refresh, publish = workflow.split("  publish:", 1)
    assert "contents: write" not in refresh
    assert "pull-requests: write" not in refresh
    assert "environment: model-catalogue-publish" in publish
    assert "contents: write" in publish
    assert "pull-requests: write" in publish
    assert 'push origin "HEAD:refs/heads/main"' not in publish


def test_publish_branch_and_patch_path_are_strictly_allowlisted():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "^model-catalogue-bot/[0-9]+$" in workflow
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
    assert "urllib.request.urlopen" in shared
