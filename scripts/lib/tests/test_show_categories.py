import importlib.util
from pathlib import Path


def load_script():
    path = Path(__file__).parents[2] / "show-categories.py"
    spec = importlib.util.spec_from_file_location("show_categories", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_show_categories_reports_effective_state(tmp_path, monkeypatch, capsys, caplog):
    module = load_script()
    categories = tmp_path / "categories.yaml"
    categories.write_text("categories:\n  dev_cli: true\n  gaming: false\n")
    monkeypatch.setattr(module, "CATEGORIES_YAML", categories)
    monkeypatch.setattr(module, "CHEZMOI_TOML", tmp_path / "chezmoi.toml")
    monkeypatch.setattr(module.sys, "argv", ["show-categories.py"])
    module.main()
    output = capsys.readouterr().out
    assert "dev_cli" in output
    assert "gaming" in output
    assert "1/2 categories active" in caplog.text
