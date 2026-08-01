#!/usr/bin/env python3
"""Check for documentation drift between AGENTS.md, README.md, and docs/."""

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import logger  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent
DOCUMENTS = (REPO_ROOT / "AGENTS.md", REPO_ROOT / "README.md")
BACKTICK_PATTERN = re.compile(r"`([^`]+)`")
MARKDOWN_LINK_PATTERN = re.compile(r"!?(?:\[[^]]*\])\(([^)]+)\)")
DOC_REFERENCE_PATTERN = re.compile(r"(?:^|/)docs/([^/\s)`#?]+\.md)(?:[#?]|$)")


def clean_target(target):
    """Return a local link target, or None for external/non-file targets."""
    target = target.strip().strip("<>")
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target or target.startswith(("#", "/", "~")):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
        return None
    return target


def looks_like_path(value):
    """Return whether a backtick value is likely a repository path."""
    value = value.strip()
    if (
        not value
        or any(char.isspace() for char in value)
        or any(
            marker in value
            for marker in ("*", "<", ">", "{{", "}}", "(", ")", "[", "]")
        )
    ):
        return False
    return "/" in value or Path(value).suffix in {
        ".md",
        ".py",
        ".sh",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
    }


def extract_references(document):
    """Extract local backtick and Markdown-link references from a document."""
    text = document.read_text(encoding="utf-8")
    references = []

    for match in BACKTICK_PATTERN.finditer(text):
        target = clean_target(match.group(1))
        if target and looks_like_path(target):
            references.append((target, "backtick"))

    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        target = clean_target(match.group(1))
        if target:
            references.append((target, "Markdown link"))

    return references


def resolve_reference(target):
    """Resolve a repository-relative reference without escaping the repository."""
    path = (REPO_ROOT / target).resolve()
    try:
        path.relative_to(REPO_ROOT)
    except ValueError:
        return None
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Check documentation references against files in the repository."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat orphaned documentation warnings as errors.",
    )
    args = parser.parse_args()

    errors = 0
    warnings = 0
    references = set()
    docs_referenced = set()

    for document in DOCUMENTS:
        if not document.exists():
            logger.error(
                "Documentation source is missing: %s", document.relative_to(REPO_ROOT)
            )
            errors += 1
            continue

        for target, reference_type in extract_references(document):
            references.add(target)
            resolved = resolve_reference(target)
            if resolved is None or not resolved.exists():
                logger.error(
                    "%s references missing path: %s (%s)",
                    document.relative_to(REPO_ROOT),
                    target,
                    reference_type,
                )
                errors += 1
            else:
                logger.info("Verified %s reference: %s", reference_type, target)

            match = DOC_REFERENCE_PATTERN.search(target)
            if match:
                docs_referenced.add(f"docs/{match.group(1)}")

    docs_dir = REPO_ROOT / "docs"
    actual_docs = (
        {str(path.relative_to(REPO_ROOT)) for path in docs_dir.rglob("*.md")}
        if docs_dir.exists()
        else set()
    )

    for doc_path in sorted(actual_docs - docs_referenced):
        warnings += 1
        logger.warning(
            "Documentation file is not referenced by AGENTS.md or README.md: %s",
            doc_path,
        )

    for doc_path in sorted(docs_referenced - actual_docs):
        errors += 1
        logger.error("Referenced documentation file does not exist: %s", doc_path)

    if errors:
        logger.error(
            "Documentation drift check failed: %d error(s), %d warning(s)",
            errors,
            warnings,
        )
    elif warnings and args.strict:
        logger.error(
            "Documentation drift check failed in strict mode: %d warning(s)", warnings
        )
    else:
        logger.info("Documentation drift check passed: %d warning(s)", warnings)

    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
