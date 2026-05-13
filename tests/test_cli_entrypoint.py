from pathlib import Path
import re


def test_console_script_name_is_hyphenated() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    scripts = pyproject.split("[project.scripts]", 1)[1].split("\n[", 1)[0]

    assert 'logovista-tools = "logovista_tools.fast_cli:main"' in scripts
    assert re.search(r"^logovista_tools\\s*=", scripts, re.MULTILINE) is None


def test_source_checkout_wrapper_is_hyphenated() -> None:
    wrapper = Path("logovista-tools")

    assert wrapper.is_file()
    assert "from logovista_tools.fast_cli import main" in wrapper.read_text(encoding="utf-8")
