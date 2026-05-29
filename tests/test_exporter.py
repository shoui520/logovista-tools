from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from logovista_tools.exporter import (
    EpwingV6Backend,
    ExportBook,
    ExportBody,
    ExportEntry,
    ExportIndex,
    ExportNavigation,
    ExportSourceOptions,
    SourceFamily,
    load_export_book,
)
from logovista_tools.writer import WriterEntry, build_plain_honmon_package, write_plain_package


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"


def _env() -> dict[str, str]:
    return {"PYTHONPATH": str(SRC_PATH)}


def _write_source_ssed(tmp_path: Path) -> Path:
    package = build_plain_honmon_package(
        dict_id="EXPORT",
        title="Export Fixture",
        entries=[
            WriterEntry("alpha", "first entry"),
            WriterEntry("biang", "rare 𰻞 entry", search_keys=("biang", "𰻞")),
        ],
        include_tagged_indexes=False,
        compression="literal",
    )
    write_plain_package(package, tmp_path)
    return tmp_path / "EXPORT.IDX"


def test_export_ir_loads_plain_ssed_entries_and_indexes(tmp_path: Path) -> None:
    idx = _write_source_ssed(tmp_path)

    book = load_export_book(idx, ExportSourceOptions(source_family=SourceFamily.SSED))

    assert book.title == "Export Fixture"
    assert book.source_family == SourceFamily.SSED
    assert len(book.entries) == 2
    assert {entry.headword for entry in book.entries} >= {"alpha", "biang"}
    assert any(index.mode == "forward" and index.key.casefold() == "alpha" for index in book.indexes)
    assert any(row.unicode == "𰻞" for row in book.gaiji)


def test_epwing_v6_backend_writes_required_layout_and_gaiji_map(tmp_path: Path) -> None:
    idx = _write_source_ssed(tmp_path / "source")
    book = load_export_book(idx, ExportSourceOptions(source_family=SourceFamily.SSED))
    out_dir = tmp_path / "epwing"

    report = EpwingV6Backend().write(book, out_dir)

    subbook = out_dir / report.subbook_dir
    assert (out_dir / "CATALOGS").is_file()
    assert not (out_dir / "LANGUAGE").exists()
    assert (subbook / "DATA" / "HONMON2").is_file()
    assert (subbook / "DATA" / "HONMONG").is_file()
    assert (subbook / "DATA" / "HONMONS").is_file()
    assert (subbook / "GAIJI" / "GA16HALF").is_file()
    assert (subbook / "GAIJI" / "GA16FULL").is_file()

    catalogs = (out_dir / "CATALOGS").read_bytes()
    assert len(catalogs) == 2048
    assert int.from_bytes(catalogs[0:2], "big") == 1
    assert int.from_bytes(catalogs[2:4], "big") == 6
    assert b"HONMON2" in catalogs
    assert b"HONMONG" in catalogs
    assert b"HONMONS" in catalogs

    honmon2 = (subbook / "DATA" / "HONMON2").read_bytes()
    index_ids = {honmon2[16 + offset] for offset in range(0, honmon2[1] * 16, 16)}
    assert {0x00, 0x91, 0x71} <= index_ids
    assert "𰻞" in (out_dir / f"{report.subbook_dir}.map").read_text(encoding="utf-8")


def test_epwing_v6_backend_indexes_menu_and_copyright_surfaces(tmp_path: Path) -> None:
    book = ExportBook(
        title="Navigation Fixture",
        source_family=SourceFamily.SSED,
        source_id="NAVFIX",
        entries=(
            ExportEntry(
                target="ssed:NAVFIX:1:0",
                headword="alpha",
                body=ExportBody.text("body"),
                search_keys=("alpha",),
            ),
        ),
        navigation=(ExportNavigation(kind="menu", label="Main menu", target="ssed:NAVFIX:1:0"),),
        copyright=(ExportNavigation(kind="copyright", label="Copyright notice", target="ssed-info:copyright"),),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")

    honmon2 = (tmp_path / "epwing" / report.subbook_dir / "DATA" / "HONMON2").read_bytes()
    index_ids = {honmon2[16 + offset] for offset in range(0, honmon2[1] * 16, 16)}
    assert {0x00, 0x91, 0x71, 0x01, 0x02} <= index_ids
    assert b"\x1f\x43" in honmon2
    assert b"\x1f\x63\x00\x00\x00\x02\x00\x00" in honmon2
    assert report.navigation == 1
    assert report.copyright_pages == 1


def test_epwing_v6_backend_uses_export_index_rows_when_targets_match(tmp_path: Path) -> None:
    book = ExportBook(
        title="Index Fixture",
        source_family=SourceFamily.SSED,
        source_id="IDXFIX",
        entries=(
            ExportEntry(
                target="ssed:IDXFIX:2:0",
                headword="display",
                body=ExportBody.text("body"),
                search_keys=("display",),
            ),
        ),
        indexes=(ExportIndex(mode="forward", key="alias", target="ssed:IDXFIX:2:0", label="alias"),),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")
    honmon2 = (tmp_path / "epwing" / report.subbook_dir / "DATA" / "HONMON2").read_bytes()

    assert b"\x23\x41\x23\x4c\x23\x49\x23\x41\x23\x53" in honmon2
    assert all(diagnostic.code != "source_index_targets_unmatched" for diagnostic in report.diagnostics)


def test_ssed_export_uses_rendererdb_for_dense_honmon_sidecar() -> None:
    root = REPO_ROOT / "tests" / "fixtures" / "synthetic" / "dense_honmon_t_contents"

    book = load_export_book(root, ExportSourceOptions(source_family=SourceFamily.SSED))

    assert len(book.entries) == 1
    assert book.entries[0].target == "ssed:DENSETC.IDX:2:0"
    assert book.entries[0].headword == "alpha"
    assert book.entries[0].body.source_kind == "rendererdb_t_contents_html"
    assert any(event.kind == "text" and event.text == "alpha body" for event in book.entries[0].body.events)
    assert any(diagnostic.code == "rendererdb_dense_honmon_source" for diagnostic in book.diagnostics)


def test_lved_sqlite_source_flattens_content_html_and_copyright(tmp_path: Path) -> None:
    root = tmp_path / "_DCT_LVEDX"
    root.mkdir()
    con = sqlite3.connect(root / "main.data")
    con.execute("create table content(id integer primary key, body text)")
    con.execute("create table list(id integer primary key, content_id integer, title text, subtitle text, type integer, anchor text)")
    con.execute("create table info(id integer primary key, type integer, name text, body text, media text)")
    con.execute("insert into content values(10, '<div><b>Alpha</b><br>definition</div>')")
    con.execute("insert into list values(1, 10, '<span>alpha</span>', '', 0, '')")
    con.execute("insert into info values(1, 2, 'copyright.html', '<p>Copyright fixture</p>', '')")
    con.commit()
    con.close()

    book = load_export_book(root, ExportSourceOptions(source_family=SourceFamily.LVED_SQLITE3))

    assert book.source_family == SourceFamily.LVED_SQLITE3
    assert len(book.entries) == 1
    assert book.entries[0].headword == "alpha"
    assert any(event.kind == "style_start" and event.value == "bold" for event in book.entries[0].body.events)
    assert book.copyright and book.copyright[0].label == "copyright.html"


def test_logovista_tools_export_epwing_v6_cli(tmp_path: Path) -> None:
    idx = _write_source_ssed(tmp_path / "source")
    out_dir = tmp_path / "cli-epwing"

    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "export", "epwing-v6", str(idx), str(out_dir), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(),
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["title"] == "Export Fixture"
    assert report["entries"] == 2
    assert (out_dir / "CATALOGS").is_file()
    assert "export: starting target=epwing-v6" in result.stderr
    assert "export: decoded 2 SSED entries total" in result.stderr
    assert "export: finished EPWING v6 export" in result.stderr


def test_logovista_tools_export_epwing_v6_cli_quiet_keeps_stderr_empty(tmp_path: Path) -> None:
    idx = _write_source_ssed(tmp_path / "source")
    out_dir = tmp_path / "quiet-epwing"

    result = subprocess.run(
        [sys.executable, "-m", "logovista_tools", "export", "epwing-v6", str(idx), str(out_dir), "--json", "--quiet"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["entries"] == 2
