from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

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


def test_litellm_client_gate_requires_main_gate():
    assert VERIFY_CONFIG.litellm_client_gate_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "0",
            "DOTFILES_PI_USE_LITELLM": "1",
        }
    ) == ["DOTFILES_PI_USE_LITELLM"]
    assert not VERIFY_CONFIG.litellm_client_gate_errors(
        {
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_PI_USE_LITELLM": "1",
        }
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


def test_litellm_service_env_rejects_unexpected_extras():
    problems = VERIFY_CONFIG.validate_litellm_service_env(
        _litellm_env(UNRELATED_VAR="x"), _LITELLM_POLICY_CONFIG
    )
    assert any("UNRELATED_VAR" in problem for problem in problems)


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
