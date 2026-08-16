#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat >"$TMP_DIR/bash-probe.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "$ROOT_DIR/scripts/lib/common.sh"
source "$ROOT_DIR/scripts/lib/common_args.sh"
COMMON_STRICT=1
parse_common_args "\$@"
printf '{"dry_run":%s,"no_backup":%s,"remaining":%s}\n' "\$COMMON_DRY_RUN" "\$COMMON_NO_BACKUP" "\${#COMMON_ARGS_REMAINING[@]}"
EOF
chmod +x "$TMP_DIR/bash-probe.sh"

cat >"$TMP_DIR/python-probe.py" <<EOF
import argparse, contextlib, io, json, sys
sys.path.insert(0, "$ROOT_DIR/scripts/lib")
from cli_helpers import add_common_args
parser = argparse.ArgumentParser(allow_abbrev=False)
add_common_args(parser)
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        args = parser.parse_args()
    print(json.dumps({"dry_run": args.dry_run, "no_backup": args.no_backup, "remaining": 0}, sort_keys=True))
except SystemExit as exc:
    print(json.dumps({"exit_code": exc.code, "help": exc.code == 0}, sort_keys=True))
EOF

python3 - "$TMP_DIR/bash-probe.sh" "$TMP_DIR/python-probe.py" <<'PY'
import json
import subprocess
import sys

bash_probe, python_probe = sys.argv[1:]
cases = [[], ["--dry-run"], ["--no-backup"], ["--dry-run", "--no-backup"], ["--help"], ["--invalid-flag"]]
failed = False
for argv in cases:
    bash = subprocess.run([bash_probe, *argv], capture_output=True, text=True)
    py = subprocess.run([sys.executable, python_probe, *argv], capture_output=True, text=True)
    if argv == ["--help"]:
        passed = bash.returncode == 0 and py.returncode == 0
    elif argv == ["--invalid-flag"]:
        passed = bash.returncode == 2 and py.returncode == 0 and json.loads(py.stdout)["exit_code"] == 2
    else:
        passed = bash.returncode == py.returncode == 0 and json.loads(bash.stdout) == json.loads(py.stdout)
    print(f"{'PASS' if passed else 'FAIL'} {' '.join(argv) or '<empty>'}")
    failed |= not passed
sys.exit(1 if failed else 0)
PY
