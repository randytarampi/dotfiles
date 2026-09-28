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
