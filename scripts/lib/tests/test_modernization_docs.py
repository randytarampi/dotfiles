"""Structural acceptance checks for the documentation reconciliation in PR #215.

These check navigation and consolidation, not installed-client acceptance or
the current status of external issues. No network or host configuration is used.
"""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS = (
    "AGENTS.md",
    "docs/basic-memory.md",
    "docs/capability-matrix.md",
    "docs/chatgpt-desktop.md",
    "docs/claude-desktop.md",
    "docs/junie.md",
    "docs/litellm.md",
    "docs/modernization-roadmap.md",
    "docs/paseo-metadata.md",
    "docs/paseo-security.md",
    "docs/unmanaged-clients.md",
    "docs/voice.md",
)
ROADMAP = "docs/modernization-roadmap.md"
RETIRED_PLAN = "PASEO-FOLLOWUP-PLAN.md"


def _links(document):
    # Match the inline Markdown links used in these guides, excluding examples
    # in fenced blocks. Backtick paths often describe generated host files.
    text = re.sub(r"^```.*?^```\s*$", "", document, flags=re.M | re.S)
    return re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", text)


@pytest.mark.parametrize("relative_path", DOCUMENTS)
def test_reconciled_document_links_resolve_inside_repository(relative_path):
    document = ROOT / relative_path
    links = _links(document.read_text(encoding="utf-8"))
    assert links, f"No navigation links checked in {relative_path}"
    for target in links:
        url = urlsplit(target)
        if url.scheme or url.netloc:
            continue
        resolved = (document.parent / unquote(url.path)).resolve()
        if not url.path:
            resolved = document
        assert resolved.is_relative_to(ROOT), (relative_path, target)
        assert resolved.is_file(), f"{relative_path}: missing link target {target}"
        if url.fragment and resolved.suffix == ".md":
            # The linked headings use GitHub's simple lowercase/punctuation
            # removal form; do not pin prose or the numeric section ordering.
            headings = re.findall(
                r"^#{1,6}\s+(.+?)\s*#*\s*$",
                resolved.read_text(encoding="utf-8"),
                flags=re.M,
            )
            anchors = {
                re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
                for heading in headings
            }
            assert unquote(url.fragment) in anchors, (relative_path, target)


def test_retired_paseo_plan_has_no_live_navigation_links():
    """Deletion must not strand readers; immutable historical links are fine."""
    assert not (ROOT / "docs" / RETIRED_PLAN).exists()
    for document in [ROOT / "AGENTS.md", ROOT / "README.md", *ROOT.glob("docs/*.md")]:
        for target in _links(document.read_text(encoding="utf-8")):
            url = urlsplit(target)
            if Path(unquote(url.path)).name != RETIRED_PLAN:
                continue
            assert url.scheme == "https" and url.netloc == "github.com", target
            assert re.fullmatch(
                r"/randytarampi/dotfiles/blob/[0-9a-f]{40}/docs/"
                + re.escape(RETIRED_PLAN),
                url.path,
            ), f"Retired plan must link to immutable history: {document}: {target}"


def test_consolidated_roadmap_preserves_paseo_component_navigation():
    """The replacement must still lead to each absorbed lane's detailed guide."""
    links = set(_links((ROOT / ROADMAP).read_text(encoding="utf-8")))
    assert {
        "paseo-acp.md",
        "paseo-mcp.md",
        "xcode-mcp.md",
        "paseo-voice.md",
        "paseo-metadata.md",
        "paseo-security.md",
    } <= links
    assert any(RETIRED_PLAN in target for target in links)


@pytest.mark.parametrize(
    "relative_path", ("AGENTS.md", "docs/basic-memory.md", "docs/capability-matrix.md")
)
def test_status_entry_points_link_to_canonical_roadmap(relative_path):
    document = ROOT / relative_path
    destinations = {
        (document.parent / unquote(urlsplit(target).path)).resolve()
        for target in _links(document.read_text(encoding="utf-8"))
        if not urlsplit(target).scheme and not urlsplit(target).netloc
    }
    assert ROOT / ROADMAP in destinations
