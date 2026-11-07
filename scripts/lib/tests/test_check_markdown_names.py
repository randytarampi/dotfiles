from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_checker():
    file = Path(__file__).parents[2] / "check-markdown-names.py"
    spec = spec_from_file_location("check_markdown_names", file)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_filename_kebab_case_and_reserved_contract():
    check = load_checker()
    assert check.permitted_name("docs/modernization-roadmap.md")
    assert check.permitted_name(".github/ISSUE_TEMPLATE/bug-report.md")
    assert check.permitted_name("AGENTS.md")
    assert check.permitted_name("README.md")
    assert check.permitted_name("configs/skills/review/SKILL.md")
    assert check.permitted_name(".github/PULL_REQUEST_TEMPLATE.md")
    assert check.permitted_name(
        "dot_config/opencode/oh-my-opencode-slim/orchestrator_append.md"
    )
    assert not check.permitted_name("docs/MODERNIZATION_ROADMAP.md")
    assert not check.permitted_name("docs/paseo_security.md")
    assert not check.permitted_name("docs/mixed-Case.md")
    assert not check.permitted_name("docs/example.MD")
    assert not check.permitted_name("docs/OTHER_SKILL.md")


def test_local_links_and_code_fences(tmp_path):
    check = load_checker()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ok-link.md").write_text("# Target\n", encoding="utf-8")
    document = docs / "example.md"
    document.write_text(
        "# Guide\n"
        "[works](ok-link.md#section)\n"
        "[external](https://example.com/OLD.md)\n"
        "[missing](old_link.md)\n"
        "~~~\n"
        "[fixture](not-real.md)\n"
        "~~~\n"
        "Inline " + chr(96) + "[example](not-link.md)" + chr(96) + " is not a link.\n",
        encoding="utf-8",
    )
    assert check.markdown_link_failures(document, tmp_path) == [
        (4, "old_link.md")
    ]


def test_hidden_markdown_does_not_bypass_naming_policy():
    check = load_checker()
    assert not check.permitted_name("docs/_hidden.md")
    assert check.permitted_name("docs/valid-123.md")
