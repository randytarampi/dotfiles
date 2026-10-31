import os
import builtins
import io
from pathlib import Path

import pytest


@pytest.fixture
def provisioning_write_boundary(tmp_path, monkeypatch):
    """Reject Python filesystem mutations outside this test's fixture tree."""
    root = tmp_path.resolve()

    def check(path):
        if isinstance(path, int):
            return
        target = Path(path).resolve()
        assert (
            target == root or root in target.parents
        ), "write outside provisioning fixture"

    for module in (builtins, io):
        original = module.open

        def guarded_open(path, mode="r", *args, _open=original, **kwargs):
            if any(flag in mode for flag in "wax+"):
                check(path)
            return _open(path, mode, *args, **kwargs)

        monkeypatch.setattr(module, "open", guarded_open)
    original_open = os.open

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            check(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in ("mkdir", "chmod", "unlink", "remove", "rmdir", "rename", "replace"):
        original = getattr(os, name)

        def guarded_mutation(path, *args, _call=original, _name=name, **kwargs):
            check(path)
            if _name in ("rename", "replace"):
                check(args[0])
            return _call(path, *args, **kwargs)

        monkeypatch.setattr(os, name, guarded_mutation)


@pytest.fixture(autouse=True)
def assume_v2_opencode_host(monkeypatch):
    """Emitter suites run on machines with any installed OpenCode host; the
    native v2 emitter contract applies. Gate tests override
    ``resolve_opencode_host`` in the test body (later monkeypatch wins)."""
    try:
        import opencode_host
    except ImportError:  # pragma: no cover - lib always present in this repo
        return
    monkeypatch.setattr(
        opencode_host, "resolve_opencode_host", lambda binary="opencode": ("known", 2)
    )


def pytest_configure():
    # coverage's installed .pth starts child Python processes when this points
    # at the in-repo config, including subprocesses launched by these tests.
    config = Path(__file__).resolve().parents[3] / "pyproject.toml"
    os.environ.setdefault("COVERAGE_PROCESS_START", str(config))
