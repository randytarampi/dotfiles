import json
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
    # Three trusted asset checks plus three exact PR-head checks.
    assert workflow.count("rev-parse HEAD") == 6
    assert workflow.count("verify-ci-assets.py") == 3
    assert "repository: randytarampi/dotfiles" not in workflow
    assert "ref: main" not in workflow
    # Every normal reviewer lane uses non-persistent read-only checkout credentials.
    assert "persist-credentials: true" not in workflow
    assert "contents: write" not in workflow
    assert "Configure git identity" not in workflow


def test_label_dispatch_keeps_fork_secrets_in_trusted_base_context():
    for path in (
        ".github/workflows/agent-review.yml",
        "configs/review/dispatcher-stub.yml",
    ):
        stub = read(path)
        assert "pull_request_target:" in stub
        assert "pull_request_target: # zizmor: ignore[dangerous-triggers]" in stub
        assert "never checks out or executes PR code" in stub
        assert "  pull_request:\n" not in stub
        assert "types: [labeled]" in stub
        assert "secrets: inherit" in stub
        assert "id-token: write" not in stub
        assert "issues: write" not in stub
        assert "pull-requests: write" in stub
        assert "actions/checkout@" not in stub
    reviewer = read(".github/workflows/agentic-review.yml")
    assert '$event_name == "pull_request_target"' in reviewer
    lint = read(".github/actionlint.yaml")
    assert ".github/workflows/agentic-review.yml:" in lint
    assert 'input "client-id" is not defined' in lint


def test_scheduled_runs_derive_a_nonempty_trusted_ref_from_the_commit_sha():
    workflow = read(".github/workflows/agentic-review.yml")
    assert workflow.count("TRUSTED_REF: ${{ inputs.trusted_ref }}") == 3
    assert workflow.count('trusted_sha="${TRUSTED_REF:-${GITHUB_SHA}}"') == 3
    assert "GITHUB_SHA" in workflow
    assert '[[ "${trusted_sha}" =~ ^[0-9a-f]{40}$ ]]' in workflow


def test_manifest_verification_precedes_codegraph_execution():
    workflow = read(".github/workflows/agentic-review.yml")
    assert workflow.index("verify-ci-assets.py") < workflow.index(
        "opencode run --standalone"
    )


def test_read_only_generation_and_scoped_publication():
    workflow = read(".github/workflows/agentic-review.yml")
    for agent in ("junie", "gemini"):
        section = re.search(rf"(?ms)^  {agent}:$(.*?)(?=^  \w)", workflow).group(1)
        assert "permissions:\n      contents: read\n" in section
        assert "issues: write" not in section
        assert "contents: write" not in section
        assert "persist-credentials: false" in section
        assert "refs/pull/{0}/head" in section
        assert "Verify exact requested PR head" in section
        assert "git rev-parse HEAD" in section
        assert "actions/upload-artifact@v7" in section
    assert 'silent_mode: "true"' in workflow
    assert "steps.junie.outputs.junie_summary" in workflow
    assert "steps.gemini.outputs.summary" in workflow
    gemini_step = workflow.split("      - id: gemini\n", 1)[1].split(
        "      - name: Save Gemini review", 1
    )[0]
    assert re.search(
        r'^          gemini_cli_version: "0[.]66[.]0"$',
        gemini_step,
        re.M,
    )
    assert re.search(
        r"^          gemini_model: .*vars[.]GEMINI_REVIEW_MODEL.*gemini-3[.]5-flash-lite.*$",
        gemini_step,
        re.M,
    )
    policy_line = next(
        line
        for line in gemini_step.splitlines()
        if line.startswith("          settings: ")
    )
    policy = json.loads(policy_line.split("settings: ", 1)[1].strip("'"))
    expected_policy = {
        "tools": {
            "core": ["ReadFileTool", "GlobTool"],
            "exclude": ["run_shell_command", "write_file", "replace", "web_fetch"],
        },
        "hooksConfig": {"enabled": False},
        "security": {"blockGitExtensions": True},
    }
    assert policy == expected_policy
    assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH=" in workflow
    assert "SYSTEM_POLICY: " in workflow
    assert "gemini-system-settings.json" in workflow
    notify = workflow.split("  notify:\n", 1)[1]
    assert "permissions:\n      contents: read" in notify
    assert "steps.junie_app.outputs.token" in notify
    assert "steps.gemini_app.outputs.token" in notify
    assert "vars.JUNIE_GITHUB_APP_ID" in notify
    assert "vars.GEMINI_GITHUB_APP_ID" in notify
    assert "secrets.JUNIE_GITHUB_APP_PRIVATE_KEY" in notify
    assert "secrets.GEMINI_GITHUB_APP_PRIVATE_KEY" in notify
    assert notify.count("permission-pull-requests: write") == 2
    assert "actions/download-artifact@v8" in notify
    assert "grep -q '[^[:space:]]'" in notify
    assert "gh api --paginate" in notify
    assert "jq -rs --arg marker" in notify
    assert "app_slug" in notify
    assert "gh api --method PATCH" in notify
    assert "gh api --method POST" in notify
    assert "FALLBACK_APP_TOKEN" not in notify
    assert "github.event.comment.id || github.run_id" in workflow


def test_opencode_v2_labels_comments_and_isolated_app_publication():
    workflow = read(".github/workflows/agentic-review.yml")
    opencode = workflow.split("\n  opencode:\n", 1)[1].split(
        "\n  opencode_publish:\n", 1
    )[0]
    publisher = workflow.split("\n  opencode_publish:\n", 1)[1].split(
        "\n  junie:\n", 1
    )[0]
    assert "contents: read" in opencode
    assert "contents: write" not in opencode
    assert "id-token: write" not in opencode
    assert "persist-credentials: false" in opencode
    assert "path: review-source" in opencode
    assert "Verify exact PR head" in opencode
    assert "npm install --global --allow-scripts=@opencode/cli @opencode/cli@2.3.0" in opencode
    assert "--dangerously-allow-all-scripts" not in opencode
    assert "npm config set allow-scripts" not in opencode
    # Install before exporting the ephemeral LiteLLM bearer to GITHUB_ENV.
    assert opencode.index("name: Install pinned OpenCode v2") < opencode.index(
        "name: Start runner-local LiteLLM native free pool"
    )
    assert "opencode run --standalone --archived --format json" in opencode
    assert "--agent review" in opencode
    assert '"action": "*", "resource": "*", "effect": "deny"' in opencode
    assert '"action": "read", "resource": "*", "effect": "allow"' in opencode
    assert 'config["mcp"] = {}' in opencode
    assert 'config["plugins"] = []' in opencode
    assert 'config["default_agent"] = "review"' in opencode
    assert "OPENCODE_CONFIG_DIR=" in opencode
    assert "XDG_CONFIG_HOME=" in opencode
    assert "anomalyco/opencode/github@" not in opencode
    assert "WOOFBOT_GITHUB_APP_PRIVATE_KEY" not in opencode
    assert "actions/upload-artifact@v7" in opencode
    assert "contents: read" in publisher
    assert "actions/checkout@" not in publisher
    assert "OPENCODE_API_KEY" not in publisher
    assert "actions/create-github-app-token@v3" in publisher
    assert "vars.WOOFBOT_GITHUB_APP_CLIENT_ID" in publisher
    assert "client-id: ${{ vars.WOOFBOT_GITHUB_APP_CLIENT_ID }}" in publisher
    assert "secrets.WOOFBOT_GITHUB_APP_PRIVATE_KEY" in publisher
    assert "permission-pull-requests: write" in publisher
    assert "woofbot-randytarampi-ca" in publisher
    assert "needs.opencode.outputs.head" in publisher
    assert "agent-review:opencode:v2 head=" in publisher
    assert "gh api --paginate" in publisher
    assert "gh api --method PATCH" in publisher
    assert "gh api --method POST" in publisher
    assert 'review-opencode" then "opencode"' in workflow
    assert 'review-all" then "junie,copilot"' in workflow
    assert 'review-gemini" then "gemini"' in workflow
    fix = read(".github/workflows/agentic-review-fix.yml")
    assert 'OPENCODE_VERSION: "2.2.5"' in fix


def test_ci_opencode_custom_provider_packages_match_v2_contract():
    """V2 accepts aisdk: AI SDK locators; the legacy internal path hides models."""
    config = json.loads(read("configs/opencode/ci/opencode.json"))
    workflow = read(".github/workflows/agentic-review.yml")
    package = config["providers"]["opencode"]["package"]
    assert package == "aisdk:@ai-sdk/openai-compatible"
    assert workflow.count('"package": "aisdk:@ai-sdk/openai-compatible"') == 1
    assert '"npm": "@ai-sdk/openai-compatible"' not in workflow
    assert 'config.setdefault("provider", {})[name]' not in workflow
    assert "@opencode/ai/providers/openai-compatible" not in workflow


def test_multiline_review_outputs_keep_closers_on_their_own_line():
    workflow = read(".github/workflows/agentic-review.yml")
    # Bodies may lack a trailing newline (printf '%s'); always force a newline
    # before the GITHUB_OUTPUT / GITHUB_ENV closer so Actions can parse it.
    assert workflow.count("printf '\\n%s\\n'") >= 4
    assert "printf '%s' \"${JUNIE_REVIEW}\"" in workflow
    assert "printf '%s' \"${GEMINI_SUMMARY}\"" in workflow


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


def test_copilot_setup_has_a_pinned_default_for_automatic_runs():
    copilot = read(".github/workflows/copilot-setup-steps.yml")
    dispatcher = read(".github/workflows/agent-review.yml")
    pin = re.search(r"(?m)^\s*trusted_ref: ([0-9a-f]{40})$", dispatcher).group(1)
    assert "workflow_dispatch:" in copilot
    assert f"default: {pin}" in copilot
    assert "required: false" in copilot
    assert f"TRUSTED_REF: ${{{{ inputs.trusted_ref || '{pin}' }}}}" in copilot
    assert 'trusted_sha="${TRUSTED_REF}"' in copilot
    assert '[[ "${trusted_sha}" =~ ^[0-9a-f]{40}$ ]]' in copilot
    assert "Authorization: Basic ${git_auth}" in copilot
    assert "inputs.trusted_ref || github.sha" not in copilot
    assert 'trusted_sha="${TRUSTED_REF:-${GITHUB_SHA}}"' not in copilot
    assert (
        "push:\n    paths:\n      - .github/workflows/copilot-setup-steps.yml"
        in copilot
    )
    assert (
        "pull_request:\n    paths:\n      - .github/workflows/copilot-setup-steps.yml"
        in copilot
    )


def test_manual_fix_uses_read_only_cli_generation_and_exact_patch_inputs():
    fix = read(".github/workflows/agentic-review-fix.yml")
    generator, publisher = fix.split("  publish:", 1)
    assert "opencode run --auto" in generator
    # OpenCode v2 run does not accept the legacy --dir flag. The runner's
    # working-directory selects the checked-out PR workspace instead.
    assert "working-directory: ${{ github.workspace }}" in generator
    assert "opencode run --auto --model opencode/big-pickle" in generator
    assert ' --dir "${GITHUB_WORKSPACE}"' not in generator
    assert "anomalyco/opencode/github" not in generator
    assert "use_github_token" not in generator
    assert (
        'OPENCODE_VERSION: "2.2.5"' in generator
        or "OPENCODE_VERSION: '2.2.5'" in generator
    )
    assert (
        'curl -fsSL https://opencode.ai/v2/install | bash -s -- --version "${OPENCODE_VERSION}"'
        in generator
    )
    assert 'export PATH="$HOME/.opencode/bin:$PATH"' in generator
    assert 'printf \'%s/.opencode/bin\\n\' "${HOME}" >> "${GITHUB_PATH}"' in generator
    assert "command -v opencode" in generator
    assert "opencode --version" in generator
    assert 'escaped_version="${OPENCODE_VERSION//./\\\\.}"' in generator
    assert 'version_pattern="(^|[^0-9])${escaped_version}([^0-9]|$)"' in generator
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


def test_manual_fix_uses_woofbot_app_publisher_credentials():
    workflow = read(".github/workflows/agentic-review-fix.yml")
    generate, publish = workflow.split("  publish:\n", 1)
    assert "WOOFBOT_GITHUB_APP_CLIENT_ID" not in generate
    assert "WOOFBOT_GITHUB_APP_PRIVATE_KEY" not in generate
    assert "environment: agentic-review-fix" in publish
    assert "client-id: ${{ vars.WOOFBOT_GITHUB_APP_CLIENT_ID }}" in publish
    assert "private-key: ${{ secrets.WOOFBOT_GITHUB_APP_PRIVATE_KEY }}" in publish
    assert "app-id:" not in publish
    assert "secrets.APP_ID" not in publish
    assert "permission-contents: write" in publish
    assert "permission-pull-requests: write" in publish
    assert "GH_TOKEN: ${{ steps.publish.outputs.token }}" in publish
    assert "PUBLISH_APP_SLUG: ${{ steps.publish.outputs.app-slug }}" in publish
    assert "Unexpected GitHub App slug for manual fix publisher" in publish
    assert 'bot_id="$(gh api "/users/${PUBLISH_APP_SLUG}[bot]" --jq .id)"' in publish
    assert '"${bot_id}" != "353209076"' in publish
    assert 'publisher_login="${PUBLISH_APP_SLUG}[bot]"' in publish
    assert 'git config user.name "${publisher_login}"' in publish
    assert "353209076+${publisher_login}@users.noreply.github.com" in publish
    assert "agentic-review-bot[bot]@users.noreply.github.com" not in publish


def test_manual_fix_push_uses_scoped_github_cli_app_credentials():
    workflow = read(".github/workflows/agentic-review-fix.yml")
    generate, publish = workflow.split("  publish:\n", 1)
    helper = "git -c credential.helper='!gh auth git-credential' push"
    assert "gh auth git-credential" not in generate
    assert publish.count(helper) == 2
    assert "GH_TOKEN: ${{ steps.publish.outputs.token }}" in publish
    assert 'GIT_TERMINAL_PROMPT: "0"' in publish
    assert "persist-credentials: false" in publish
    assert "http.extraheader" not in publish
    assert "Authorization: Bearer" not in publish
