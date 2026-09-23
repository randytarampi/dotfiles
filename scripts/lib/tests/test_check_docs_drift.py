from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_script():
    path = Path(__file__).parents[2] / "check-docs-drift.py"
    spec = spec_from_file_location("check_docs_drift", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fenced_code_is_not_scanned_but_inline_missing_path_is(tmp_path):
    module = load_script()
    document = tmp_path / "README.md"
    document.write_text(
        "```text\n`missing/fenced.py`\n```\nThen `missing/inline.py`.\n"
    )
    references = module.extract_references(document)
    assert ("missing/inline.py", "backtick") in references
    assert ("missing/fenced.py", "backtick") not in references
