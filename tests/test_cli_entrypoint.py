from pathlib import Path
import re
import subprocess
import sys

from logovista_tools.writer import WriterEntry, build_plain_honmon_package, write_plain_package


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


def test_logovista_tools_verbose_expected_error_stays_friendly(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "info", "--verbose", str(tmp_path / "missing.DIC")],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode != 0
    assert "file not found" in result.stderr
    assert "Traceback" not in result.stderr


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


def test_logovista_tools_colscr_direct_component_reports_expected_input(tmp_path: Path) -> None:
    component = tmp_path / "COLSCR.DIC"
    component.write_bytes(b"SSEDDATA")

    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "colscr", str(component), "--verbose"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode != 0
    assert "COLSCR.DIC was provided directly" in result.stderr
    assert "dictionary .IDX or package directory" in result.stderr
    assert "logovista-tools colscr" in result.stderr
    assert "completed colscr" not in result.stderr


def test_logovista_tools_entries_print_outputs_rows_to_terminal(tmp_path: Path) -> None:
    package = build_plain_honmon_package(
        dict_id="PRINTME",
        title="Print Me",
        entries=[WriterEntry("alpha", "first entry"), WriterEntry("beta", "second entry")],
        include_tagged_indexes=False,
    )
    write_plain_package(package, tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "logovista_tools",
            "entries",
            str(tmp_path / "PRINTME.IDX"),
            "--limit",
            "1",
            "--print",
            "--out-dir",
            str(tmp_path / "out"),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode == 0
    assert "## PRINTME #1" in result.stdout
    assert "alpha" in result.stdout
    assert "first entry" in result.stdout
    assert "second entry" not in result.stdout
    assert "entries: wrote summary" in result.stderr


def test_logovista_tools_extract_writes_entry_formats(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    package = build_plain_honmon_package(
        dict_id="EXTRACTME",
        title="Extract Me",
        entries=[WriterEntry("alpha", "first entry"), WriterEntry("beta", "second entry")],
        include_tagged_indexes=False,
    )
    write_plain_package(package, package_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "logovista_tools",
            "extract",
            str(package_dir),
            "--yes",
            "--entries",
            "--formats",
            "json,csv,txt",
            "--out-dir",
            str(tmp_path / "out"),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode == 0
    entry_dir = tmp_path / "out" / "EXTRACTME" / "entries"
    assert (entry_dir / "entries.json").is_file()
    assert (entry_dir / "entries.csv").is_file()
    assert (entry_dir / "entries.txt").is_file()
    assert "alpha" in (entry_dir / "entries.txt").read_text(encoding="utf-8")
    assert "first entry" in (entry_dir / "entries.json").read_text(encoding="utf-8")
    assert "extract: EXTRACTME: entries" in result.stderr


def test_logovista_tools_extract_interactive_selection(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    package = build_plain_honmon_package(
        dict_id="WIZARD",
        title="Wizard",
        entries=[WriterEntry("alpha", "first entry")],
        include_tagged_indexes=False,
    )
    write_plain_package(package, package_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "logovista_tools",
            "extract",
            str(package_dir),
            "--out-dir",
            str(tmp_path / "interactive-out"),
        ],
        input="\n1\ntxt\n\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": "src"},
    )

    assert result.returncode == 0
    assert "Choose data to extract" in result.stdout
    assert (tmp_path / "interactive-out" / "WIZARD" / "entries" / "entries.txt").is_file()
    assert not (tmp_path / "interactive-out" / "WIZARD" / "entries" / "entries.json").exists()
