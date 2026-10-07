from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "verify-config.py"
SPEC = spec_from_file_location("verify_config", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
VERIFY_CONFIG = module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY_CONFIG)


def _mock_git_and_chezmoi(monkeypatch, current, worktree_output, source):
    monkeypatch.setattr(
        VERIFY_CONFIG.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(args, **kwargs):
        if args[-1] == "source-path":
            return SimpleNamespace(returncode=0, stdout=f"{source}\n")
        assert args == [
            "/usr/bin/git",
            "-C",
            str(current),
            "worktree",
            "list",
            "--porcelain",
        ]
        return SimpleNamespace(returncode=0, stdout=worktree_output)

    monkeypatch.setattr(VERIFY_CONFIG.subprocess, "run", fake_run)


def test_litellm_routing_validation_is_structural_and_port_aware():
    safe = """
# .mozart and http://127.0.0.1:4000 are comments, not routing
model_list:
  - model_name: openai/wolf.mozart-v1
    litellm_params:
      model: openai/wolf.mozart-v1
      api_base: http://evil.localhost:4000/v1
"""
    assert VERIFY_CONFIG.validate_litellm_routing_text(safe)
    assert not VERIFY_CONFIG.validate_litellm_routing_text(
        'model_list:\n  - litellm_params:\n      api_base: "http://LOCALHOST:4000/v1"\n'
    )


def test_litellm_venv_patch_status_reports_applied_and_drift(tmp_path):
    root = (
        tmp_path
        / ".local/share/litellm/venv/lib/python3.11/site-packages/litellm/proxy"
    )
    root.mkdir(parents=True)
    utils = root / "utils.py"
    env = {"DOTFILES_RUN_LITELLM_SETUP": "1", "HOME": str(tmp_path)}
    utils.write_text("# dotfiles listing-enrichment bypass: begin HF fast path\n")
    assert "run make deploy" in VERIFY_CONFIG.litellm_venv_patch_status(environ=env)[0]
    utils.write_text("plain upstream source\n")
    assert "run make deploy" in VERIFY_CONFIG.litellm_venv_patch_status(environ=env)[0]
    assert not VERIFY_CONFIG.validate_litellm_routing_text(
        'model_list:\n  - litellm_params:\n      gateway: "localhost:4000"\n'
    )
    assert VERIFY_CONFIG.validate_litellm_routing_text(
        'model_list:\n  - litellm_params:\n      api_base: "http://127.0.0.1:40000/v1"\n'
    )
    assert not VERIFY_CONFIG.validate_litellm_routing_text(
        'model_list:\n  - litellm_params:\n      api_base: "http://[::1]:4100/v1"\n',
        port=4100,
    )


def test_caddyfile_validation_runs_when_binary_is_available(tmp_path, monkeypatch):
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("example.com {\n  respond ok\n}\n")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="Valid configuration\n", stderr="")

    monkeypatch.setattr(VERIFY_CONFIG.shutil, "which", lambda name: "/usr/bin/caddy")
    monkeypatch.setattr(VERIFY_CONFIG.subprocess, "run", fake_run)

    valid, output = VERIFY_CONFIG.validate_caddyfile(caddyfile)

    assert valid
    assert "Valid configuration" in output
    assert calls == [["/usr/bin/caddy", "validate", "--config", str(caddyfile)]]


def test_caddyfile_validation_skips_without_binary(tmp_path, monkeypatch):
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("invalid")
    monkeypatch.setattr(VERIFY_CONFIG.shutil, "which", lambda name: None)

    assert VERIFY_CONFIG.validate_caddyfile(caddyfile) == (None, "")


def test_litellm_client_gate_enforces_gateway_only_prerequisites(tmp_path, monkeypatch):
    # Direct mode reads deployed configs under the given home; isolate from
    # the real machine so the assert is a contract check, not a state check.
    assert not VERIFY_CONFIG.litellm_client_gate_errors(
        {"DOTFILES_USE_LITELLM_PROXY": "0"}, home=tmp_path
    )
    errors = VERIFY_CONFIG.litellm_client_gate_errors(
        {"DOTFILES_USE_LITELLM_PROXY": "1", "DOTFILES_RUN_LITELLM_SETUP": "0"},
        home=tmp_path,
    )
    assert errors == [
        "DOTFILES_USE_LITELLM_PROXY=1 requires DOTFILES_RUN_LITELLM_SETUP=1"
    ]

    key_dir = tmp_path / ".local/share/litellm/clients"
    key_dir.mkdir(parents=True)
    for client in VERIFY_CONFIG.APP_KEYS:
        (key_dir / f"{client}.key").write_text("key")
    config_path = tmp_path / ".local/share/litellm/config.yaml"
    config_path.write_text('  - model_name: "openai/gpt-x"\n')
    monkeypatch.setattr(
        VERIFY_CONFIG.litellm_config, "_registry_model_refs", lambda: {"openai/gpt-x"}
    )
    assert (
        VERIFY_CONFIG.litellm_client_gate_errors(
            {
                "DOTFILES_USE_LITELLM_PROXY": "1",
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "OPENAI_API_KEY": "direct-key",
            },
            home=tmp_path,
        )
        == []
    )
    config_path.write_text('  - model_name: "openrouter/missing"\n')
    assert any(
        "cannot resolve canonical alias" in error
        for error in VERIFY_CONFIG.litellm_client_gate_errors(
            {"DOTFILES_USE_LITELLM_PROXY": "1", "DOTFILES_RUN_LITELLM_SETUP": "1"},
            home=tmp_path,
        )
    )


def test_litellm_openai_subscription_preflight_direct_subscription_or_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        VERIFY_CONFIG.litellm_config,
        "_registry_model_refs",
        lambda: {"openai/gpt-verified"},
    )
    clients = tmp_path / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    for client in VERIFY_CONFIG.APP_KEYS:
        (clients / f"{client}.key").write_text("virtual-key")
    config = tmp_path / ".local/share/litellm/config.yaml"
    config.write_text('  - model_name: "openai/gpt-verified"\n')

    direct_env = {
        "HOME": str(tmp_path),
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "OPENAI_API_KEY": "direct-key",
    }
    assert VERIFY_CONFIG.litellm_client_gate_errors(direct_env, home=tmp_path) == []

    cache = tmp_path / ".config/litellm/chatgpt/auth.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800}
        )
    )
    verified = tmp_path / ".local/share/litellm/chatgpt_verified_models.json"
    verified.write_text('["gpt-verified"]')
    verified.chmod(0o600)
    subscription_env = {
        "HOME": str(tmp_path),
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
    }
    assert (
        VERIFY_CONFIG.litellm_client_gate_errors(subscription_env, home=tmp_path) == []
    )
    assert any(
        row[:2] == ("openai", "SUBSCRIPTION-chatgpt")
        for row in VERIFY_CONFIG.litellm_oauth_readiness(subscription_env)
    )

    config.write_text('  - model_name: "another/alias"\n')
    errors = VERIFY_CONFIG.litellm_client_gate_errors(
        {
            "HOME": str(tmp_path),
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
        },
        home=tmp_path,
    )
    assert errors == [
        "LiteLLM proxy mode cannot resolve canonical alias: openai/gpt-verified; set OPENAI_API_KEY or run litellm-oauth.py --provider chatgpt"
    ]


def test_litellm_direct_mode_rejects_gateway_rewrite_residue(tmp_path):
    config = tmp_path / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"model":"openai/gpt-x"}')
    assert (
        VERIFY_CONFIG.litellm_client_gate_errors(
            {"DOTFILES_USE_LITELLM_PROXY": "0"}, home=tmp_path
        )
        == []
    )
    config.write_text('{"model":"litellm/openai/gpt-x"}')
    assert any(
        "LiteLLM transport references" in error
        for error in VERIFY_CONFIG.litellm_client_gate_errors(
            {"DOTFILES_USE_LITELLM_PROXY": "0"}, home=tmp_path
        )
    )


def test_direct_mode_gateway_residue_uses_configured_non_default_port(
    tmp_path, monkeypatch
):
    config = tmp_path / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"provider":{"openai":{"options":{"baseURL":"http://127.0.0.1:4567/v1"}}}}'
    )


def test_direct_mode_residue_port_is_independent_of_litellm_run_gate(
    tmp_path, monkeypatch
):
    config = tmp_path / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"provider":{"openai":{"options":{"baseURL":"http://127.0.0.1:4567/v1"}}}}'
    )
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "0")
    monkeypatch.setenv("LITELLM_PORT", "4567")
    errors = VERIFY_CONFIG.litellm_client_gate_errors(
        {"DOTFILES_USE_LITELLM_PROXY": "0", "DOTFILES_RUN_LITELLM_SETUP": "0"},
        home=tmp_path,
    )
    assert any("gateway endpoint on port 4567" in error for error in errors)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_PORT", "4567")
    assert any(
        "gateway endpoint" in error
        for error in VERIFY_CONFIG.litellm_client_gate_errors(
            {"DOTFILES_USE_LITELLM_PROXY": "0", "DOTFILES_RUN_LITELLM_SETUP": "1"},
            home=tmp_path,
        )
    )


def test_litellm_ui_contradiction_requires_enabled_ui():
    # Main gate off: never an error, regardless of switches.
    assert not VERIFY_CONFIG.litellm_ui_contradiction_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "0",
            "DOTFILES_LITELLM_UI_EXPOSED": "1",
        }
    )
    # Gate on, UI exposed, service default disables it: contradiction.
    assert VERIFY_CONFIG.litellm_ui_contradiction_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_LITELLM_UI_EXPOSED": "1",
        }
    )
    # Explicit override re-enables the UI: no contradiction.
    assert not VERIFY_CONFIG.litellm_ui_contradiction_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_LITELLM_UI_EXPOSED": "1",
            "LITELLM_DISABLE_ADMIN_UI": "False",
        }
    )
    # Persisted False is drift: generation resets to True without an override.
    assert VERIFY_CONFIG.litellm_ui_contradiction_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_LITELLM_UI_EXPOSED": "1",
        }
    )
    assert not VERIFY_CONFIG.litellm_ui_contradiction_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_LITELLM_UI_EXPOSED": "1",
            "LITELLM_DISABLE_ADMIN_UI": "False",
        },
    )


def test_backup_timer_accepts_current_and_valid_sibling_worktrees(
    tmp_path, monkeypatch
):
    current = tmp_path / "current-worktree"
    main = tmp_path / "main-worktree"
    source = tmp_path / "deployed-checkout"
    for path in (current, main, source):
        path.mkdir()
    _mock_git_and_chezmoi(
        monkeypatch,
        current,
        f"worktree {main}\nHEAD abc123\n\nworktree {current}\nHEAD def456\n",
        source,
    )

    paths = VERIFY_CONFIG.get_backup_timer_repo_paths(current)

    assert VERIFY_CONFIG.backup_timer_repo_path_is_allowed(
        ["/usr/bin/make", "-C", str(main), "openwebui-backup"], paths
    )
    assert VERIFY_CONFIG.backup_timer_repo_path_is_allowed(
        ["/usr/bin/make", "-C", str(current), "openwebui-backup"], paths
    )
    nested_command = (
        "exec /usr/bin/env -i HOME=/tmp PATH=/usr/bin /bin/bash "
        "--noprofile --norc -c "
        f"'exec make -C \"{main}\" openwebui-backup'"
    )
    assert VERIFY_CONFIG.backup_timer_repo_path_is_allowed(
        ["/bin/bash", "-c", nested_command], paths
    )


def test_backup_timer_rejects_prunable_and_missing_worktrees(tmp_path, monkeypatch):
    current = tmp_path / "current-worktree"
    prunable = tmp_path / "prunable-worktree"
    missing = tmp_path / "missing-worktree"
    current.mkdir()
    prunable.mkdir()
    _mock_git_and_chezmoi(
        monkeypatch,
        current,
        (
            f"worktree {prunable}\nHEAD abc123\nprunable gitdir missing\n\n"
            f"worktree {missing}\nHEAD def456\n"
        ),
        current,
    )

    paths = VERIFY_CONFIG.get_backup_timer_repo_paths(current)

    assert not VERIFY_CONFIG.backup_timer_repo_path_is_allowed(
        ["/usr/bin/make", "-C", str(prunable), "openwebui-backup"], paths
    )
    assert not VERIFY_CONFIG.backup_timer_repo_path_is_allowed(
        ["/usr/bin/make", "-C", str(missing), "openwebui-backup"], paths
    )


def test_backup_timer_rejects_prefix_sharing_and_unrelated_repository_paths(
    tmp_path, monkeypatch
):
    current = tmp_path / "repo"
    prefix_sharing = tmp_path / "repo-unrelated"
    unrelated_repo = tmp_path / "other-repository"
    current.mkdir()
    prefix_sharing.mkdir()
    unrelated_repo.mkdir()
    _mock_git_and_chezmoi(
        monkeypatch,
        current,
        f"worktree {current}\nHEAD abc123\n",
        current,
    )

    paths = VERIFY_CONFIG.get_backup_timer_repo_paths(current)

    for path in (prefix_sharing, unrelated_repo):
        assert not VERIFY_CONFIG.backup_timer_repo_path_is_allowed(
            ["/usr/bin/make", "-C", str(path), "openwebui-backup"], paths
        )


def test_opencode_compaction_is_required_when_gate_runs(tmp_path, monkeypatch):
    opencode_dir = tmp_path / ".config" / "opencode"
    opencode_dir.mkdir(parents=True)
    (opencode_dir / "dcp.jsonc").write_text('{"compress": {}}', encoding="utf-8")
    monkeypatch.setattr(VERIFY_CONFIG, "HOME", tmp_path)
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")

    managed = opencode_dir / "opencode.json"
    managed.write_text('{"compaction": {"auto": false}}', encoding="utf-8")
    assert VERIFY_CONFIG.check_opencode_orphan_files() == 0

    managed.write_text('{"compaction": {"auto": "false"}}', encoding="utf-8")
    assert VERIFY_CONFIG.check_opencode_orphan_files() == 1

    managed.write_text("{}", encoding="utf-8")
    assert VERIFY_CONFIG.check_opencode_orphan_files() == 1


def test_adopted_opencode_memory_config_is_not_an_orphan(tmp_path, monkeypatch):
    opencode_dir = tmp_path / ".config" / "opencode"
    opencode_dir.mkdir(parents=True)
    (opencode_dir / "dcp.jsonc").write_text('{"compress": {}}', encoding="utf-8")
    (opencode_dir / "opencode-mem.jsonc").write_text(
        '{"webServerHost": "127.0.0.1", "embeddingModel": "local", "opencodeModel": "inherit"}',
        encoding="utf-8",
    )
    (opencode_dir / "opencode.json").write_text(
        '{"compaction": {"auto": false}}', encoding="utf-8"
    )
    monkeypatch.setattr(VERIFY_CONFIG, "HOME", tmp_path)
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_MEMORY_SETUP", "1")
    assert VERIFY_CONFIG.check_opencode_orphan_files() == 0
    assert any(
        gate == "DOTFILES_RUN_OPENCODE_MEMORY_SETUP"
        and any(path.name == "opencode-mem.jsonc" for path in paths)
        for gate, _description, paths in VERIFY_CONFIG.CHECKS
    )


def test_mcp_checks_include_pi_adapter_path():
    assert any(
        gate == "DOTFILES_RUN_MCP_SETUP"
        and any(path.name == "mcp-adapter.json" for path in paths)
        for gate, _description, paths in VERIFY_CONFIG.CHECKS
    )


_LITELLM_POLICY_CONFIG = """
litellm_settings:
  telemetry: false
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
"""


def _litellm_env(**overrides):
    values = {
        "LITELLM_MASTER_KEY": "sk-" + "x" * 20,
        "LITELLM_PORT": "4000",
        "DISABLE_ADMIN_UI": "False",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "DATABASE_URL": "postgresql://localhost/litellm",
    }
    values.update(overrides)
    return values


def test_litellm_service_env_accepts_pre_provisioning_state():
    """Fresh installs: per-app virtual keys may be absent before first run."""
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(), _LITELLM_POLICY_CONFIG
    )
    assert problems == []


def test_litellm_service_env_accepts_provisioned_per_app_keys():
    env = _litellm_env(
        **{name: "sk-" + "y" * 20 for name in VERIFY_CONFIG.APP_KEYS.values()}
    )
    problems = VERIFY_CONFIG.validate_litellm_service_env(env, _LITELLM_POLICY_CONFIG)
    assert problems == []


def test_litellm_service_env_accepts_oauth_cache_overrides_and_defaults():
    overrides = {
        "CHATGPT_TOKEN_DIR": "/custom/chatgpt",
        "CHATGPT_AUTH_FILE": "auth.json",
        "GITHUB_COPILOT_TOKEN_DIR": "/custom/copilot",
        "GITHUB_COPILOT_ACCESS_TOKEN_FILE": "access-token.json",
        "GITHUB_COPILOT_API_KEY_FILE": "api-key.json",
    }
    assert (
        VERIFY_CONFIG.validate_litellm_service_env(
            _litellm_env(**overrides), _LITELLM_POLICY_CONFIG
        )
        == []
    )
    assert (
        VERIFY_CONFIG.validate_litellm_service_env(
            _litellm_env(), _LITELLM_POLICY_CONFIG
        )
        == []
    )
    rejected = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(UNKNOWN_CACHE_OVERRIDE="x"), _LITELLM_POLICY_CONFIG
    )
    assert any("UNKNOWN_CACHE_OVERRIDE" in problem for problem in rejected)


@pytest.mark.parametrize(
    "provider,key_env,model",
    [
        ("cohere_chat", "COHERE_API_KEY", "cohere_chat/command-r"),
        ("cerebras", "CEREBRAS_API_KEY", "cerebras/llama-3.3-70b"),
        ("huggingface", "HF_TOKEN", "huggingface/org/model"),
    ],
)
def test_litellm_policy_accepts_api_key_provider_references(provider, key_env, model):
    config = (
        "model_list:\n"
        f"  - model_name: {provider}/test\n    litellm_params:\n"
        f"      model: {model}\n      api_key: os.environ/{key_env}\n"
        + _LITELLM_POLICY_CONFIG
    )
    env = _litellm_env(**{key_env: "provider-key"})
    assert VERIFY_CONFIG.validate_litellm_service_env(env, config) == []


def test_litellm_policy_accepts_model_entry_without_api_key():
    config = (
        "model_list:\n  - model_name: cohere_chat/test\n    litellm_params:\n"
        "      model: cohere_chat/command-r\n" + _LITELLM_POLICY_CONFIG
    )
    assert VERIFY_CONFIG.validate_litellm_service_env(_litellm_env(), config) == []


def test_litellm_coverage_warnings_read_persisted_notes_and_skip_malformed(
    tmp_path, monkeypatch
):
    path = tmp_path / "last_generation_notes.json"
    monkeypatch.setattr(VERIFY_CONFIG, "LITELLM_NOTES_PATH", path)
    assert VERIFY_CONFIG.litellm_coverage_warnings() == []
    path.write_text('["opencode/model: UNKNOWN"]')
    assert VERIFY_CONFIG.litellm_coverage_warnings() == [
        "LiteLLM coverage: opencode/model: UNKNOWN"
    ]
    path.write_text("not json")
    assert VERIFY_CONFIG.litellm_coverage_warnings() == []


@pytest.mark.parametrize(
    "provider,filename,envkey",
    [
        ("github_copilot", "api-key.json", "GITHUB_COPILOT_TOKEN_DIR"),
        ("chatgpt", "auth.json", "CHATGPT_TOKEN_DIR"),
    ],
)
def test_litellm_oauth_readiness_candidate_and_unknown(
    tmp_path, monkeypatch, provider, filename, envkey
):
    cache = tmp_path / filename
    cache.write_text(
        json.dumps(
            {"expires_at": 4102444800, "token": "copilot"}
            if provider == "github_copilot"
            else {
                "expires_at": 4102444800,
                "access_token": "access",
                "refresh_token": "refresh",
            }
        )
    )
    rows = VERIFY_CONFIG.litellm_oauth_readiness(
        {
            envkey: str(tmp_path),
            "HOME": str(tmp_path),
            "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
        }
    )
    candidate = next(row for row in rows if row[0] == provider)
    assert candidate[1] == "MATCH-candidate"
    assert "expires" in candidate[2]
    unknown = next(
        row
        for row in VERIFY_CONFIG.litellm_oauth_readiness(
            {"HOME": str(tmp_path / "missing"), "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1"}
        )
        if row[0] == provider
    )
    assert unknown[1] == "UNKNOWN"
    assert "litellm-oauth.py" in unknown[2]


def test_litellm_oauth_readiness_reports_gate_withheld():
    rows = VERIFY_CONFIG.litellm_oauth_readiness(
        {"DOTFILES_LITELLM_OAUTH_PROVIDERS": "0"}
    )
    assert len(rows) == 3
    assert all(row[1] == "WITHHELD" for row in rows)
    assert all("DOTFILES_LITELLM_OAUTH_PROVIDERS=0" in row[2] for row in rows)


def test_litellm_service_env_validation_stays_on_service_database_name():
    env = _litellm_env(LITELLM_DATABASE_URL="postgresql://localhost/litellm")
    env.pop("DATABASE_URL")
    problems = VERIFY_CONFIG.validate_litellm_service_env(env, _LITELLM_POLICY_CONFIG)
    assert any("DATABASE_URL" in problem for problem in problems)
    assert any("unexpected entries" in problem for problem in problems)


def test_litellm_service_env_rejects_unexpected_extras():
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(UNRELATED_VAR="x"), _LITELLM_POLICY_CONFIG
    )
    assert any("UNRELATED_VAR" in problem for problem in problems)


def test_litellm_service_env_accepts_default_lru_size():
    env = _litellm_env(DEFAULT_MAX_LRU_CACHE_SIZE="4096")
    assert VERIFY_CONFIG.validate_litellm_service_env(env, _LITELLM_POLICY_CONFIG) == []


def test_litellm_service_env_requires_core_and_config_refs():
    config = _LITELLM_POLICY_CONFIG + "\n  api_key: os.environ/OPENROUTER_API_KEY\n"
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        {key: value for key, value in _litellm_env().items() if key != "DATABASE_URL"},
        config,
    )
    assert any("DATABASE_URL" in problem for problem in problems)
    assert any("OPENROUTER_API_KEY" in problem for problem in problems)


def test_litellm_service_env_detects_master_key_port_drift():
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(LITELLM_MASTER_KEY="short"), _LITELLM_POLICY_CONFIG, "4100"
    )
    assert any("master-key shape" in problem for problem in problems)
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(), _LITELLM_POLICY_CONFIG, "4100"
    )
    assert any("LITELLM_PORT drift" in problem for problem in problems)


def test_litellm_service_env_rejects_inline_api_keys_and_missing_policy():
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(), _LITELLM_POLICY_CONFIG + "  api_key: sk-inline-secret\n"
    )
    assert any("inline api_key" in problem for problem in problems)
    problems = VERIFY_CONFIG.validate_litellm_service_env({}, "")
    assert any("telemetry/master-key policy" in problem for problem in problems)
