import os
from pathlib import Path


def pytest_configure():
    # coverage's installed .pth starts child Python processes when this points
    # at the in-repo config, including subprocesses launched by these tests.
    config = Path(__file__).resolve().parents[3] / "pyproject.toml"
    os.environ.setdefault("COVERAGE_PROCESS_START", str(config))
