import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "check-heredocs.py"
SPEC = importlib.util.spec_from_file_location("check_heredocs", SCRIPT)
check_heredocs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_heredocs)


@pytest.mark.parametrize(
    "interpreter", ["python3", "/usr/bin/python", "./venv/bin/python3"]
)
@pytest.mark.parametrize("flags", ["", "-u ", "-u -I "])
def test_interpreter_and_delimiter_captures(interpreter, flags):
    line = f"{interpreter} {flags}- << 'PYTHON'"
    match = check_heredocs.INTERPRETER_LINE.search(line)
    assert match is not None
    assert match.group(1) == interpreter.lstrip("./")
    assert match.group("delimiter") == "PYTHON"
    assert check_heredocs.extract_heredocs(line + "\nprint('ok')\nPYTHON\n") == [
        (1, interpreter.rsplit("/", 1)[-1], "PYTHON", "print('ok')", True)
    ]


def test_atomic_flags_reject_nonmatching_line_without_backtracking():
    code = (
        "import runpy; "
        f"pattern = runpy.run_path({str(SCRIPT)!r})['INTERPRETER_LINE']; "
        "assert pattern.match('python3 ' + '-flag ' * 100 + 'missing') is None"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr.decode()
