import importlib.util
import json
import shutil
import subprocess
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "configure_mcp_tool", ROOT / "configure-mcp-tool.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_toml_runtime_check_is_scoped_to_toml_mcp_tool(monkeypatch):
    module = load_script()
    monkeypatch.setattr(
        module.sys, "argv", ["configure-mcp-tool.py", "--dry-run", "opencode"]
    )
    monkeypatch.setattr(module, "resolve_env_files", lambda *_: [])
    monkeypatch.setattr(module, "resolve_defs_list", lambda *_: [])
    monkeypatch.setattr(
        module,
        "_ensure_toml_runtime",
        lambda: pytest.fail("JSON must not select runtime"),
    )
    module.main()


def test_toml_merge_subcommand_requires_locked_runtime_without_tomli(monkeypatch):
    module = load_script()
    monkeypatch.setattr(
        module.sys,
        "argv",
        ["configure-mcp-tool.py", "merge-configs", "--format", "toml-mcpServers"],
    )
    monkeypatch.setitem(module.sys.modules, "tomllib", None)
    monkeypatch.setitem(module.sys.modules, "tomli", None)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("poetry")),
    )
    with pytest.raises(RuntimeError, match="Poetry and its locked runtime"):
        module._ensure_toml_runtime()


def test_python310_without_global_parser_hands_off_before_toml_writes(tmp_path):
    python310 = shutil.which("python3.10")
    if not python310:
        pytest.skip("Python 3.10 required for isolated no-parser regression")
    bare = tmp_path / "bare"
    subprocess.run([python310, "-m", "venv", "--without-pip", str(bare)], check=True)
    interpreter = bare / "bin/python"
    missing = subprocess.run(
        [str(interpreter), "-c", "import tomllib, tomli"], capture_output=True
    )
    assert missing.returncode != 0
    assert (
        subprocess.run(
            [str(interpreter), "-c", "import tomli"], capture_output=True
        ).returncode
        != 0
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    poetry = tools / "poetry"
    # Resolve only the environment lookup; the actual merger runs in the
    # locked interpreter executing this test, not in a fake parser.
    poetry.write_text('#!/bin/sh\nprintf "%s\\n" "$LOCKED_PYTHON"\n')
    poetry.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "PATH": f"{tools}:/usr/bin:/bin",
        "LOCKED_PYTHON": sys.executable,
    }
    target = tmp_path / "config.toml"
    original = (
        "# BEGIN DOTFILES MANAGED PROVIDERS\n"
        '[model_providers.fixture]\nname = "keep"\n'
        "# END DOTFILES MANAGED PROVIDERS\n"
    )
    target.write_text(original)
    command = [
        str(interpreter),
        str(ROOT / "configure-mcp-tool.py"),
        "merge-configs",
        "--format",
        "toml-mcpServers",
        "--mcp-path",
        str(target),
        "--output-content",
        '[mcp_servers.fixture]\ncommand = "dummy"\n',
    ]
    snapshots = []
    for _ in range(2):
        result = subprocess.run(
            command, env=env, cwd=tmp_path, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        snapshots.append(target.read_bytes())
    assert snapshots[0] == snapshots[1]
    assert original.strip() in target.read_text()

    project = tmp_path / "project"
    project.mkdir()
    for launcher in ("configure-mcp-tool.py", "configure-mcps.py"):
        args = [
            str(interpreter),
            str(ROOT / launcher),
            "--mode",
            "project",
            "--project-dir",
            str(project),
            "--project-mcps",
            "context7",
        ]
        args += ["--tools", "codex"] if launcher == "configure-mcps.py" else ["codex"]
        for _ in range(2):
            result = subprocess.run(
                args, env=env, cwd=tmp_path, capture_output=True, text=True
            )
            assert result.returncode == 0, result.stderr
        assert (project / ".codex/config.toml").is_file()


def test_chezmoi_mcp_failure_never_reports_success(tmp_path):
    template = (
        ROOT.parent / ".chezmoiscripts/run_onchange_15-configure-mcp.sh.tmpl"
    ).read_text()
    start = template.index('if [[ -x "$CONFIGURE_MCPS"')
    segment = template[start:]
    fake = tmp_path / "configure-mcps.py"
    fake.write_text("#!/bin/sh\nexit 37\n")
    fake.chmod(0o755)
    registry = tmp_path / "registry.json"
    registry.write_text("{}")
    script = (
        "set -eu\ninfo() { :; }; warn() { :; }; "
        'die() { exit 1; }; ok() { printf "SUCCESS\\n"; };\n'
        'CONFIGURE_MCPS="$1"; MCP_REGISTRY="$2";\n' + segment
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script, "fixture", str(fake), str(registry)],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "SUCCESS" not in result.stdout


def write_config(path, servers, **extra):
    path.write_text(json.dumps({"mcpServers": servers, **extra}), encoding="utf-8")


def read_config(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_pi_old_only_migrates_and_removes_old_file(tmp_path):
    module = load_script()
    agent_dir = tmp_path / ".pi" / "agent"
    agent_dir.mkdir(parents=True)
    old = agent_dir / "mcp.json"
    adapter = agent_dir / "mcp-adapter.json"
    write_config(old, {"old": {"command": "old"}})

    assert module.migrate_pi_mcp_config(old, adapter)
    assert not old.exists()
    assert read_config(adapter)["mcpServers"] == {"old": {"command": "old"}}


def test_pi_both_files_prefer_adapter_and_append_old_only(tmp_path):
    module = load_script()
    old = tmp_path / "mcp.json"
    adapter = tmp_path / "mcp-adapter.json"
    write_config(
        old,
        {"same": {"command": "old"}, "old": {"command": "old"}},
        custom_top_level={"keep": True},
    )
    write_config(
        adapter,
        {"same": {"command": "adapter"}, "new": {"command": "new"}},
        imports=["other.json"],
    )

    module.migrate_pi_mcp_config(old, adapter)
    result = read_config(adapter)
    assert not old.exists()
    assert result["custom_top_level"] == {"keep": True}
    assert result["imports"] == ["other.json"]
    assert result["mcpServers"] == {
        "same": {"command": "adapter"},
        "new": {"command": "new"},
        "old": {"command": "old"},
    }


def test_pi_new_only_is_a_no_op_for_migration(tmp_path):
    module = load_script()
    adapter = tmp_path / "mcp-adapter.json"
    write_config(adapter, {"new": {"command": "new"}})

    before = adapter.read_text(encoding="utf-8")
    assert not module.migrate_pi_mcp_config(tmp_path / "mcp.json", adapter)
    assert adapter.read_text(encoding="utf-8") == before


def test_pi_migration_is_gate_off_no_op(tmp_path, monkeypatch):
    module = load_script()
    old = tmp_path / "mcp.json"
    adapter = tmp_path / "mcp-adapter.json"
    write_config(old, {"old": {"command": "old"}})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_MCP_SETUP", "0")
    args = SimpleNamespace(
        tool="pi",
        mode="global",
        project_dir=str(tmp_path),
        env_file="",
        project_mcps="",
        include="",
        exclude="",
        dry_run=False,
        show_secrets=False,
        no_backup=True,
    )

    module.orchestrate_mcp_config(args)
    assert old.exists()
    assert not adapter.exists()


def _args(tmp_path, mode="global"):
    return SimpleNamespace(
        tool="pi",
        mode=mode,
        project_dir=str(tmp_path),
        env_file="",
        project_mcps="",
        include="",
        exclude="",
        dry_run=False,
        show_secrets=False,
        no_backup=True,
    )


def test_pi_global_orchestration_keeps_adapter_collision(tmp_path, monkeypatch):
    module = load_script()
    agent_dir = tmp_path / ".pi" / "agent"
    agent_dir.mkdir(parents=True)
    old = agent_dir / "mcp.json"
    adapter = agent_dir / "mcp-adapter.json"
    write_config(old, {"collision": {"source": "old"}})
    write_config(adapter, {"collision": {"source": "adapter"}, "adapter": {}})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_MCP_SETUP", "1")
    monkeypatch.setattr(
        module,
        "resolve_defs_list",
        lambda *_args: [{"name": "collision", "command": "generated"}],
    )
    monkeypatch.setattr(
        module,
        "format_configs_to_str",
        lambda *_args: json.dumps(
            {"mcpServers": {"collision": {"source": "generated"}}}
        ),
    )

    module.orchestrate_mcp_config(_args(tmp_path))
    result = read_config(adapter)
    assert result["mcpServers"]["collision"] == {"source": "adapter"}
    assert result["mcpServers"]["adapter"] == {}
    assert not old.exists()


def test_pi_project_orchestration_generates_native_project_file(tmp_path, monkeypatch):
    module = load_script()
    monkeypatch.setattr(
        module,
        "resolve_defs_list",
        lambda *_args: [{"name": "project", "command": "project"}],
    )
    monkeypatch.setattr(
        module,
        "format_configs_to_str",
        lambda *_args: json.dumps({"mcpServers": {"project": {"command": "project"}}}),
    )

    module.orchestrate_mcp_config(_args(tmp_path, mode="project"))
    assert (
        read_config(tmp_path / ".pi/mcp.json")["mcpServers"]["project"]["command"]
        == "project"
    )


def test_pi_atomic_migration_failure_leaves_old_file(tmp_path, monkeypatch):
    module = load_script()
    old = tmp_path / "mcp.json"
    adapter = tmp_path / "mcp-adapter.json"
    write_config(old, {"old": {"command": "old"}})
    old_before = old.read_text(encoding="utf-8")
    monkeypatch.setattr(
        module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("boom"))
    )

    try:
        module.migrate_pi_mcp_config(old, adapter)
    except OSError:
        pass
    else:
        raise AssertionError("expected atomic replacement failure")
    assert old.read_text(encoding="utf-8") == old_before
    assert not adapter.exists()


def test_registry_uses_adapter_global_and_native_project_paths():
    registry = json.loads(
        (ROOT.parent / "configs/mcp/global-mcps.json").read_text(encoding="utf-8")
    )
    pi = registry["tools"]["pi"]
    assert pi["mcp_path"] == "~/.pi/agent/mcp-adapter.json"
    assert pi["project_mcp_path"] == ".pi/mcp.json"


def test_toml_mcp_merge_preserves_unowned_comments_and_tables(tmp_path):
    module = load_script()
    path = tmp_path / "config.toml"
    path.write_text(
        "# before MCP\n"
        "[mcp_servers.old]\ncommand = 'old'\n"
        "# BEGIN DOTFILES MANAGED PROVIDERS\n"
        "[model_providers.meridian]\nname = 'Meridian'\n"
        "# END DOTFILES MANAGED PROVIDERS\n",
        encoding="utf-8",
    )
    generated = """[mcp_servers.alpha]
command = "alpha"

[mcp_servers.alpha.env]
TOKEN = "test"

[mcp_servers.beta]
command = "beta"
"""

    module.merge_configs_to_file("toml-mcpServers", path, generated)
    once = path.read_text(encoding="utf-8")
    module.merge_configs_to_file("toml-mcpServers", path, generated)
    twice = path.read_text(encoding="utf-8")

    assert twice == once
    assert "# before MCP" in twice
    assert (
        "# BEGIN DOTFILES MANAGED PROVIDERS\n[model_providers.meridian]\nname = 'Meridian'\n# END DOTFILES MANAGED PROVIDERS"
        in twice
    )
    parsed = tomllib.loads(twice)
    assert parsed["model_providers"]["meridian"]["name"] == "Meridian"
    assert parsed["mcp_servers"] == {
        "alpha": {"command": "alpha", "env": {"TOKEN": "test"}},
        "beta": {"command": "beta"},
    }


def test_toml_mcp_merge_handles_indented_headers_and_rejects_multiline_strings(
    tmp_path,
):
    module = load_script()
    path = tmp_path / "config.toml"
    path.write_text(
        '  [mcp_servers.old]\ncommand = "old"\n'
        '[model_providers.user]\nname = "keep"\n',
        encoding="utf-8",
    )
    module.merge_configs_to_file(
        "toml-mcpServers", path, '[mcp_servers.new]\ncommand = "new"\n'
    )
    assert tomllib.loads(path.read_text())["mcp_servers"] == {"new": {"command": "new"}}
    assert tomllib.loads(path.read_text())["model_providers"]["user"]["name"] == "keep"

    original = '[model_providers.user]\nname = """\n[mcp_servers.fake]\n"""\n'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="multiline strings"):
        module.merge_configs_to_file(
            "toml-mcpServers", path, '[mcp_servers.new]\ncommand = "new"\n'
        )
    assert path.read_text(encoding="utf-8") == original


def test_toml_mcp_merge_then_codex_owned_block_reconcile_is_idempotent(
    tmp_path, monkeypatch
):
    module = load_script()
    codex_spec = importlib.util.spec_from_file_location(
        "configure_codex_mcp_test", ROOT / "configure-codex.py"
    )
    assert codex_spec and codex_spec.loader
    codex = importlib.util.module_from_spec(codex_spec)
    codex_spec.loader.exec_module(codex)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("DOTFILES_RUN_CODEX_SETUP", raising=False)
    monkeypatch.delenv("DOTFILES_USE_LITELLM_PROXY", raising=False)
    path = tmp_path / "config.toml"
    original = (
        "# BEGIN DOTFILES MANAGED PROVIDERS\n"
        "[model_providers.meridian]\nname = 'Meridian'\n"
        "# END DOTFILES MANAGED PROVIDERS\n"
        "\n[model_providers.user]\nname = 'User'\n"
    )
    path.write_text(original, encoding="utf-8")
    module.merge_configs_to_file(
        "toml-mcpServers", path, '[mcp_servers.alpha]\ncommand = "alpha"\n'
    )
    merged = path.read_text(encoding="utf-8")
    stripped = codex.strip_owned_block(
        merged,
        "# BEGIN DOTFILES MANAGED PROVIDERS",
        "# END DOTFILES MANAGED PROVIDERS",
        "model_providers",
    )
    assert (
        codex.strip_owned_block(
            stripped,
            "# BEGIN DOTFILES MANAGED PROVIDERS",
            "# END DOTFILES MANAGED PROVIDERS",
            "model_providers",
        )
        == stripped
    )
    parsed = tomllib.loads(stripped)
    assert parsed["model_providers"]["user"]["name"] == "User"
    assert parsed["mcp_servers"]["alpha"]["command"] == "alpha"
    assert "# BEGIN DOTFILES MANAGED PROVIDERS" not in stripped


@pytest.mark.parametrize(
    "original",
    [
        '[mcp_servers.old]\ncommand = "old"\n[[user.items]]\nname = "keep"\n',
        '[mcp_servers."quoted-name"]\ncommand = "old"\n',
    ],
)
def test_unsupported_toml_merge_layout_refuses_without_writing(tmp_path, original):
    module = load_script()
    path = tmp_path / "config.toml"
    path.write_text(original, encoding="utf-8")
    before = path.stat()
    with pytest.raises(ValueError):
        module.merge_configs_to_file(
            "toml-mcpServers", path, '[mcp_servers.new]\ncommand = "new"\n'
        )
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime_ns == before.st_mtime_ns
