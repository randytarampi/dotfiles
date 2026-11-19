"""Tests for documentation reachability.

`check-docs-drift` used to count only *direct* references from `AGENTS.md` and
`README.md`, which reported 22 unreachable documents when 11 of them were
reachable through another document. The check now walks the link graph.

These tests pin the semantics with small synthetic trees so a future rewrite
cannot quietly reintroduce the direct-reference-only rule.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "check-docs-drift.py"
_SPEC = spec_from_file_location("check_docs_drift", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
DRIFT = module_from_spec(_SPEC)
_SPEC.loader.exec_module(DRIFT)


def _tree(tmp_path: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _reachable(monkeypatch, tmp_path: Path, roots: list[str]) -> set[str]:
    monkeypatch.setattr(DRIFT, "REPO_ROOT", tmp_path)
    return {
        str(path.relative_to(tmp_path))
        for path in DRIFT.reachable_docs([tmp_path / root for root in roots])
    }


def test_grandchild_is_reachable_through_an_intermediate(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            "README.md": "see [a](docs/a.md)\n",
            "docs/a.md": "see [b](b.md)\n",
            "docs/b.md": "leaf\n",
        },
    )
    reachable = _reachable(monkeypatch, tmp_path, ["README.md"])
    assert "docs/a.md" in reachable
    assert (
        "docs/b.md" in reachable
    ), "a document linked from another document is reachable"


def test_unlinked_document_is_an_orphan(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            "README.md": "[a](docs/a.md)\n",
            "docs/a.md": "leaf\n",
            "docs/orphan.md": "nobody links me\n",
        },
    )
    reachable = _reachable(monkeypatch, tmp_path, ["README.md"])
    assert "docs/orphan.md" not in reachable


def test_cycle_terminates(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            "README.md": "[a](docs/a.md)\n",
            "docs/a.md": "[b](b.md)\n",
            "docs/b.md": "[a](a.md)\n",
        },
    )
    reachable = _reachable(monkeypatch, tmp_path, ["README.md"])
    assert {"docs/a.md", "docs/b.md"} <= reachable


def test_external_and_anchor_targets_are_ignored(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            "README.md": (
                "[ext](https://example.com/docs/other.md) "
                "[anchor](#section) "
                "[local](docs/local.md#frag)\n"
            ),
            "docs/local.md": "leaf\n",
        },
    )
    reachable = _reachable(monkeypatch, tmp_path, ["README.md"])
    assert "docs/local.md" in reachable
    assert all("example.com" not in item for item in reachable)


def test_relative_link_resolves_from_the_linking_documents_directory(
    monkeypatch, tmp_path
):
    _tree(
        tmp_path,
        {
            "README.md": "[a](docs/nested/a.md)\n",
            "docs/nested/a.md": "[up](../up.md)\n",
            "docs/up.md": "leaf\n",
        },
    )
    reachable = _reachable(monkeypatch, tmp_path, ["README.md"])
    assert "docs/up.md" in reachable
