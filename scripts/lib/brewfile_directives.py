"""Single source of Brewfile directive parsing and status classification.

Both the Linux preflight (`scripts/check-linux-brewfile-eligibility.py`) and the
bundle filter (`scripts/filter-brewfile.py`) import this module, so the audited
selection and the emitted bundle cannot disagree about what a directive is or
what an operator-ignored name means. Nothing here reads, evaluates or executes
Brewfile content beyond parsing a single line.
"""

import re

DIRECTIVE_RE = re.compile(
    r'^(tap|brew|cask|npm|uv)\s+"([A-Za-z0-9@/_.+\-]+)"(?:\s*,\s*.+)?$'
)

# A macOS-specific name deserves a separate qualification, but is not declared
# impossible on Linux without actual endpoint/formula evidence.
MACOS_NAMED_CANDIDATES = frozenset(
    ("pinentry-mac", "carthage", "cocoapods", "ios-deploy", "wix/brew/applesimutils")
)

# DOTFILES_BREWFILE_IGNORE removes exactly these directive kinds, matching
# `.env.example` ("comma-separated brew/cask names") and the bundle filter.
IGNORABLE_KINDS = frozenset(("brew", "cask"))


def split_ignore(value):
    """Turn a comma-separated operator list into exact names.

    The result is compared literally; operator input is never compiled into a
    pattern, so a name containing regex metacharacters cannot widen the match.
    """
    return frozenset(name.strip() for name in (value or "").split(",") if name.strip())


def parse_line(line):
    """Return ``(kind, name)`` for a well-formed directive, else ``None``."""
    found = DIRECTIVE_RE.fullmatch(line.strip())
    return found.groups() if found else None


def classify(kind, name, ignored=frozenset()):
    """Map one parsed directive to its report status."""
    if kind in IGNORABLE_KINDS and name in ignored:
        return "ignored_by_operator"
    if kind == "cask":
        return "blocked_linux_cask"
    if kind == "brew" and name in MACOS_NAMED_CANDIDATES:
        return "macos_named_needs_evidence"
    if kind in ("npm", "uv"):
        return "external_runtime_not_qualified"
    return "needs_linux_formula_or_tap_evidence"
