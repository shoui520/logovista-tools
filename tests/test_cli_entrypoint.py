from pathlib import Path
import re
import subprocess
import sys


def test_console_script_name_is_hyphenated() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    scripts = pyproject.split("[project.scripts]", 1)[1].split("\n[", 1)[0]

    assert 'logovista-tools = "logovista_tools.fast_cli:main"' in scripts
    assert re.search(r"^logovista_tools\\s*=", scripts, re.MULTILINE) is None


def test_source_checkout_wrapper_is_hyphenated() -> None:
    wrapper = Path("logovista-tools")

    assert wrapper.is_file()
    assert "from logovista_tools.fast_cli import main" in wrapper.read_text(encoding="utf-8")


def test_logovista_tools_missing_file_error_is_friendly(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "info", str(tmp_path / "missing.DIC")],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode != 0
    assert "logovista-tools: running info" in result.stderr
    assert "file not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_logovista_tools_verbose_error_shows_traceback(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "info", "--verbose", str(tmp_path / "missing.DIC")],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode != 0
    assert "file not found" in result.stderr
    assert "Traceback" in result.stderr


def test_logovista_tools_entries_missing_root_reports_path(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "entries", str(tmp_path / "missing-root")],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode != 0
    assert "logovista-tools: running entries" in result.stderr
    assert "file not found" in result.stderr
    assert "Traceback" not in result.stderr
