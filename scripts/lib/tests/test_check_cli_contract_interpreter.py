"""CLI contract launcher dispatch must respect extensionless Python shebangs."""

import importlib.util
from pathlib import Path

CHECKER_PATH = Path(__file__).resolve().parents[2] / "check-cli-contract.py"
SPEC = importlib.util.spec_from_file_location("cli_contract_script", CHECKER_PATH)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def test_extensionless_python_launcher_runs_under_python(tmp_path):
    path = tmp_path / "python-launcher"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.parse_args()\n",
        encoding="utf-8",
    )
    result = CHECKER.run_probe(path, ["--help"])
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_extensionless_shell_launcher_still_runs_under_bash(tmp_path):
    path = tmp_path / "shell-launcher"
    path.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "--help" ]]; then echo "Usage: shell"; exit 0; fi\n',
        encoding="utf-8",
    )
    result = CHECKER.run_probe(path, ["--help"])
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
