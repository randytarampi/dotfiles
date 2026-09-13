#!/usr/bin/env python3
"""Validate heredocs in .chezmoiscripts/*.tmpl.

Catches the deploy-time failure class where a missing heredoc terminator
makes bash -n pass (the unterminated heredoc silently swallows the next
heredoc's terminator) while the embedded script receives shell lines and
dies with a SyntaxError at runtime.

For each heredoc body belonging to an interpreter invocation (python3 -),
the body is compiled; for shell heredocs, only terminator pairing is
checked. Exit codes: 0 — all heredocs valid; 1 — a mismatch or a body
that does not compile.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / ".chezmoiscripts"

# Interpreters whose heredoc bodies must compile as Python.
PYTHON_INTERPRETERS = {"python3", "python"}

HEREDOC_OPEN = re.compile(r"<<\s*'(?!<)(\w+)'")
INTERPRETER_LINE = re.compile(r"(\w[\w./-]*)\s+(?:-\S+\s+)*-+\s*<<\s*'(\w+)'")
TERMINATOR = re.compile(r"^{tag}$".format(tag=r"(\w+)"), re.MULTILINE)


def extract_heredocs(text):
    """Return [(line_no, interpreter_or_None, tag, body, closed)] entries.

    A heredoc is (line of the << operator, interpreter command word if the
    line invokes one, quoted tag, body up to the first standalone
    terminator line, and whether a terminator was found before EOF).
    """
    heredocs = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = HEREDOC_OPEN.search(line)
        if not match:
            i += 1
            continue
        tag = match.group(1)
        interpreter = None
        interp_match = INTERPRETER_LINE.search(line)
        if interp_match:
            interpreter = interp_match.group(1).rsplit("/", 1)[-1]
        body_lines = []
        j = i + 1
        closed = False
        while j < len(lines):
            if lines[j] == tag:
                closed = True
                break
            body_lines.append(lines[j])
            j += 1
        heredocs.append((i + 1, interpreter, tag, "\n".join(body_lines), closed))
        i = j + 1 if closed else len(lines)
    return heredocs


def main():
    if not SCRIPTS_DIR.exists():
        print("No .chezmoiscripts directory — nothing to check.")
        return 0

    failures = []
    checked = 0
    for path in sorted(SCRIPTS_DIR.glob("*.tmpl")):
        text = path.read_text(encoding="utf-8")
        for line_no, interpreter, tag, body, closed in extract_heredocs(text):
            checked += 1
            label = f"{path.name}:{line_no} ({tag})"
            if not closed:
                failures.append(
                    f"{label}: unterminated heredoc — no standalone '{tag}' terminator"
                )
                continue
            if interpreter in PYTHON_INTERPRETERS:
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".py", delete=False
                ) as handle:
                    handle.write(body)
                    temp_name = handle.name
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "py_compile", temp_name],
                        capture_output=True,
                    )
                    if result.returncode != 0:
                        detail = (
                            result.stderr.decode(errors="replace").strip().splitlines()
                        )
                        failures.append(
                            f"{label}: heredoc body does not compile as Python\n    "
                            + "\n    ".join(detail[-4:])
                        )
                finally:
                    Path(temp_name).unlink(missing_ok=True)

    print(f"Checked {checked} heredoc(s) in {SCRIPTS_DIR.name}/.")
    if failures:
        print("\nHeredoc validation failed:")
        for failure in failures:
            print(f"  ✗ {failure}")
        return 1
    print("All heredocs valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
