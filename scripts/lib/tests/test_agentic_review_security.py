import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_normal_lanes_pin_and_verify_trusted_assets_without_workspace_checkout():
    workflow = read(".github/workflows/agentic-review.yml")
    assert workflow.count('trusted_sha="${TRUSTED_REF:-${GITHUB_SHA}}"') == 3
    assert workflow.count("TRUSTED_REF: ${{ inputs.trusted_ref }}") == 3
    assert "github.workflow_sha" not in workflow
    assert (
        'description: "Required immutable 40-hex dotfiles commit containing trusted review assets"'
        in workflow
    )
    assert "required: true\n        type: string" in workflow
    assert 'required: false\n        default: ""' not in workflow
    assert "inputs.trusted_ref || github.sha" not in workflow
    assert 'trusted_sha="b144' not in workflow
    assert workflow.count("rev-parse HEAD") == 3
    assert workflow.count("verify-ci-assets.py") == 3
    assert "repository: randytarampi/dotfiles" not in workflow
    assert "ref: main" not in workflow
    assert "persist-credentials: true" not in workflow
    assert "contents: write" not in workflow
    assert "Configure git identity" not in workflow


def test_scheduled_runs_derive_a_nonempty_trusted_ref_from_the_commit_sha():
    workflow = read(".github/workflows/agentic-review.yml")
    assert workflow.count("TRUSTED_REF: ${{ inputs.trusted_ref }}") == 3
    assert workflow.count('trusted_sha="${TRUSTED_REF:-${GITHUB_SHA}}"') == 3
    assert "GITHUB_SHA" in workflow
    assert '[[ "${trusted_sha}" =~ ^[0-9a-f]{40}$ ]]' in workflow


def test_manifest_verification_precedes_codegraph_execution():
    workflow = read(".github/workflows/agentic-review.yml")
    assert workflow.index("verify-ci-assets.py") < workflow.index("ci-codegraph.sh")


def test_agent_lanes_have_read_only_job_permissions_and_app_only_publication():
    workflow = read(".github/workflows/agentic-review.yml")
    for job in ("opencode", "junie", "gemini"):
        section = re.search(rf"(?ms)^  {job}:$(.*?)(?=^  \w)", workflow).group(1)
        assert "permissions:\n      contents: read\n" in section
        assert "contents: write" not in section
        assert "pull-requests: write" not in section
        assert "issues: write" not in section
        assert "Upload " in section
        assert "actions/upload-artifact@v7" in section
        assert "gh api" not in section
        assert "gh pr comment" not in section
    junie = re.search(r"(?ms)^  junie:$(.*?)(?=^  \w)", workflow).group(1)
    assert 'silent_mode: "true"' in junie
    assert "steps.junie.outputs.junie_summary" in junie
    assert (
        "outputs:\n      review: ${{ steps.opencode_review.outputs.review }}"
        in workflow
    )
    assert (
        "outputs:\n      review: ${{ steps.junie_review.outputs.review }}" in workflow
    )
    assert (
        "outputs:\n      review: ${{ steps.gemini_review.outputs.review }}" in workflow
    )
    notify = workflow.split("  notify:\n", 1)[1]
    assert "actions/download-artifact@v8" in notify
    assert 'review_file="reviews/${agent}-review.md"' in notify
    assert '$(<"${review_file}")' in notify
    assert (
        'gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" -f body="${body}"'
        in notify
    )
    assert "FALLBACK_TOKEN: ${{ github.token }}" not in notify
    assert "GH_TOKEN: ${{ github.token }}" not in notify
    assert "JUNIE_APP_TOKEN" in notify
    assert "OPENCODE_APP_TOKEN" in notify
    assert "GEMINI_APP_TOKEN" in notify
    assert "github-actions[bot]" not in workflow
    assert "*_BOT_TOKEN" not in workflow


def test_dispatcher_is_read_only_and_fix_lane_is_manual_and_allowlisted():
    dispatcher = read("configs/review/dispatcher-stub.yml")
    generated = read(".github/workflows/agent-review.yml")
    fix = read(".github/workflows/agentic-review-fix.yml")
    copilot = read(".github/workflows/copilot-setup-steps.yml")
    assert "contents: read" in dispatcher
    assert "contents: read" in generated
    assert "workflow_dispatch:" in fix
    assert "inputs.trusted_ref" in fix
    assert "github.repository_owner == 'randytarampi'" in fix
    assert "github.actor == github.repository_owner" in fix
    assert "contents: write" in fix
    assert "secrets: inherit" not in fix
    assert "160000" in fix
    assert "environment: agentic-review-fix" in fix
    assert "needs: generate" in fix
    assert "actions/upload-artifact@v7" in fix
    assert "gh pr create --draft" in fix
    assert "trusted_ref" in copilot
    assert 'trusted_sha="b144' not in copilot


def test_manual_fix_uses_read_only_cli_generation_and_exact_patch_inputs():
    fix = read(".github/workflows/agentic-review-fix.yml")
    generator, publisher = fix.split("  publish:", 1)
    assert "opencode run --auto" in generator
    assert "anomalyco/opencode/github" not in generator
    assert "use_github_token" not in generator
    assert 'npm install --global "opencode-ai@${OPENCODE_VERSION}"' in generator
    assert "command -v opencode" in generator
    assert "opencode --version" in generator
    assert 'OPENCODE_VERSION: "1.18.33"' in generator
    assert "version_pattern=" in generator
    assert '[[ "${version_output}" =~ ${version_pattern} ]]' in generator
    assert "git commit" not in generator
    assert "git push" not in generator
    assert "gh pr create" not in generator
    assert "base_sha" in generator
    assert "git rev-parse HEAD" in generator
    assert "allowed_paths" in generator
    assert "is_allowed_path" in generator
    assert ".github/*|configs/*|scripts/*|docs/*" not in fix
    assert "git ls-files --others --exclude-standard" in generator
    assert "git add -N" in generator
    assert "git diff --binary" in generator
    assert "agentic-review-bot/${GITHUB_RUN_ID}" in publisher
    assert "^agentic-review-bot/[0-9]+$" in publisher
    assert "git apply --name-only" in publisher
    assert "is_allowed_path" in publisher
    assert "gh pr list --head" in publisher
    assert "gh pr edit" in publisher
    assert "gh pr create --draft" in publisher
    assert "git ls-remote --heads origin" in publisher
    assert "BOT_BRANCH_EXPECTED_TIP" in publisher
    assert 'if [[ "${BOT_BRANCH_EXISTS}" == "true" ]]; then' in publisher
    assert (
        '--force-with-lease="refs/heads/${publish_branch}:${BOT_BRANCH_EXPECTED_TIP}"'
        in publisher
    )


def test_generated_companion_passes_the_same_immutable_sha_to_the_reusable_workflow():
    trusted_sha = "0123456789abcdef0123456789abcdef01234567"
    generated = read("configs/review/dispatcher-stub.yml").replace(
        "__REF__", trusted_sha
    )
    assert f"agentic-review.yml@{trusted_sha}" in generated
    assert f"trusted_ref: {trusted_sha}" in generated
    assert generated.count(trusted_sha) == 3
    reusable = read(".github/workflows/agentic-review.yml")
    assert "trusted_ref:" in reusable
    assert "type: string" in reusable
    incompatible = generated.replace(
        f"trusted_ref: {trusted_sha}", "trusted_ref: deadbeef"
    )
    pin_match = re.search(r"agentic-review\.yml@([0-9a-f]{40})", incompatible)
    trusted_match = re.search(r"trusted_ref: ([0-9a-f]+)", incompatible)
    assert pin_match and trusted_match
    pin = pin_match.group(1)
    trusted = trusted_match.group(1)
    assert pin != trusted
