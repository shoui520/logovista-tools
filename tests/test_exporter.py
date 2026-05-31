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
    ExportBodyEvent,
    ExportEntry,
    ExportGaiji,
    ExportIndex,
    ExportNavigation,
    ExportResource,
    ExportSourceOptions,
    SourceFamily,
    load_export_book,
)
from logovista_tools.entries import LinkEnd, LinkStart, Text, decode_tokens
from logovista_tools.exporter.sources import _clean_export_heading, _token_events
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
    stale_subbook = out_dir / "EXPORT" / "DATA"
    stale_subbook.mkdir(parents=True)
    (out_dir / "LANGUAGE").write_text("stale", encoding="ascii")
    (stale_subbook / "STALE").write_text("stale", encoding="ascii")

    report = EpwingV6Backend().write(book, out_dir)

    subbook = out_dir / report.subbook_dir
    assert (out_dir / "CATALOGS").is_file()
    assert not (out_dir / "LANGUAGE").exists()
    assert not (subbook / "DATA" / "STALE").exists()
    assert (subbook / "DATA" / "HONMON2").is_file()
    assert (subbook / "DATA" / "HONMONG").is_file()
    assert (subbook / "DATA" / "HONMONS").is_file()
    assert (subbook / "GAIJI" / "GA16HALF").is_file()
    assert (subbook / "GAIJI" / "GA16FULL").is_file()
    assert report.honmon2_bytes == (subbook / "DATA" / "HONMON2").stat().st_size
    assert report.honmong_bytes == (subbook / "DATA" / "HONMONG").stat().st_size
    assert report.honmons_bytes == (subbook / "DATA" / "HONMONS").stat().st_size

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


def test_epwing_v6_backend_lowers_entry_links_to_references(tmp_path: Path) -> None:
    book = ExportBook(
        title="Link Fixture",
        source_family=SourceFamily.SSED,
        source_id="LINKFIX",
        entries=(
            ExportEntry(
                target="entry:alpha",
                headword="alpha",
                body=ExportBody(
                    events=(
                        ExportBodyEvent.text_event("see "),
                        ExportBodyEvent(kind="link", text="beta", value="entry:beta"),
                    ),
                    source_kind="synthetic",
                ),
                search_keys=("alpha",),
            ),
            ExportEntry(
                target="entry:beta",
                headword="beta",
                body=ExportBody.text("target body"),
                search_keys=("beta",),
            ),
        ),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")
    honmon2 = (tmp_path / "epwing" / report.subbook_dir / "DATA" / "HONMON2").read_bytes()

    assert report.entry_link_controls == 1
    assert report.unresolved_link_placeholders == 0
    assert b"\x1f\x42\x00\x00" in honmon2
    assert b"\x1f\x62" in honmon2
    assert all(diagnostic.code != "epwing_link_target_unresolved" for diagnostic in report.diagnostics)


def test_epwing_v6_backend_preserves_source_ga16_bitmap_rows(tmp_path: Path) -> None:
    glyph = b"\xff\x00" * 16
    book = ExportBook(
        title="Gaiji Fixture",
        source_family=SourceFamily.SSED,
        source_id="GAIJIFIX",
        entries=(
            ExportEntry(
                target="entry:alpha",
                headword="alpha",
                body=ExportBody(events=(ExportBodyEvent(kind="gaiji_ref", value="a121"),), source_kind="synthetic"),
                search_keys=("alpha",),
            ),
        ),
        gaiji=(
            ExportGaiji(
                code="A121",
                space="full",
                unicode="〓",
                bitmap=glyph,
                bitmap_width=16,
                bitmap_height=16,
                bitmap_source="GAI16F00",
                bitmap_backed=True,
            ),
        ),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")
    subbook = tmp_path / "epwing" / report.subbook_dir
    honmon2 = (subbook / "DATA" / "HONMON2").read_bytes()
    ga16full = (subbook / "GAIJI" / "GA16FULL").read_bytes()
    map_text = (tmp_path / "epwing" / f"{report.subbook_dir}.map").read_text(encoding="utf-8")

    assert b"\xa1\x21" in honmon2
    assert b"[a121]" not in honmon2
    assert len(ga16full) == 4096
    assert ga16full[8] == 16
    assert ga16full[9] == 16
    assert int.from_bytes(ga16full[10:12], "big") == 0xA121
    assert int.from_bytes(ga16full[12:14], "big") == 1
    assert ga16full[2048:2080] == glyph
    assert "zA121" in map_text
    assert report.gaiji_full_entries == 1
    assert all(diagnostic.code != "gaiji_ref_placeholder" for diagnostic in report.diagnostics)


def test_ssed_body_link_payload_uses_bcd_block_offset() -> None:
    events = _token_events(
        [
            LinkStart(op=0x42),
            Text("識別ボード「が」"),
            LinkEnd(op=0x62, payload="000009621076"),
        ],
        idx_name="ARCHSIC3.IDX",
    )

    assert len(events) == 1
    assert events[0].kind == "link"
    assert events[0].text == "識別ボード「が」"
    assert events[0].value == "ssed:ARCHSIC3.IDX:962:1076"


def test_exporter_preserves_source_bitmap_gaiji_codes_as_refs() -> None:
    tokens, stats = decode_tokens(
        b"\xA3\x22",
        gaiji="placeholder",
        image_gaiji_keys={"a322"},
        preserve_image_gaiji=True,
    )

    events = _token_events(tokens, idx_name="ARCHSIC3.IDX")

    assert stats["image_gaiji"] == 1
    assert len(events) == 1
    assert events[0].kind == "gaiji_ref"
    assert events[0].value == "a322"


def test_exporter_strips_gaiji_refs_from_wrapper_headings() -> None:
    assert _clean_export_heading("<img:b025>かれ【彼】") == "かれ【彼】"
    assert _clean_export_heading("<hA322>けれ") == "けれ"


def test_epwing_v6_backend_menu_can_target_address_inside_exported_entry(tmp_path: Path) -> None:
    book = ExportBook(
        title="Containing Target Fixture",
        source_family=SourceFamily.SSED,
        source_id="MENULINK",
        entries=(
            ExportEntry(
                target="ssed:MENULINK.IDX:2:0",
                headword="alpha",
                body=ExportBody.text("body"),
                search_keys=("alpha",),
            ),
            ExportEntry(
                target="ssed:MENULINK.IDX:2:200",
                headword="beta",
                body=ExportBody.text("next body"),
                search_keys=("beta",),
            ),
        ),
        navigation=(ExportNavigation(kind="menu", label="inside alpha", target="ssed:MENULINK.IDX:2:8"),),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")
    honmon2 = (tmp_path / "epwing" / report.subbook_dir / "DATA" / "HONMON2").read_bytes()

    assert report.navigation == 1
    assert report.containing_target_links == 1
    assert b"\x1f\x43" in honmon2
    assert b"\x1f\x63" in honmon2
    assert all(diagnostic.code != "epwing_menu_target_unmatched" for diagnostic in report.diagnostics)


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


def test_epwing_v6_backend_lowers_image_resources_to_honmong(tmp_path: Path) -> None:
    bmp_1x1 = bytes.fromhex(
        "424d3a0000000000000036000000280000000100000001000000010018000000000004000000130b0000130b00000000000000000000000000"
    )
    book = ExportBook(
        title="Image Fixture",
        source_family=SourceFamily.SSED,
        source_id="IMGFIX",
        entries=(
            ExportEntry(
                target="ssed:IMGFIX:2:0",
                headword="image",
                body=ExportBody(
                    events=(ExportBodyEvent(kind="media_ref", text="figure", value="image:one"),),
                    source_kind="synthetic",
                ),
            ),
        ),
        resources=(ExportResource(resource_id="image:one", kind="image", payload=bmp_1x1, mime_type="image/bmp"),),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")
    data_dir = tmp_path / "epwing" / report.subbook_dir / "DATA"

    assert report.graphic_resources == 1
    assert report.image_controls == 1
    assert report.audio_controls == 0
    assert (data_dir / "HONMONG").read_bytes().startswith(b"data")
    assert b"BM" in (data_dir / "HONMONG").read_bytes()[:64]
    honmon2 = (data_dir / "HONMON2").read_bytes()
    assert b"\x1f\x3c" in honmon2
    assert b"\x1f\x6d" in honmon2
    assert all(diagnostic.code != "media_ref_placeholder" for diagnostic in report.diagnostics)


def test_epwing_v6_backend_lowers_audio_resources_to_honmons(tmp_path: Path) -> None:
    fmt_payload = (
        (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
    )
    wave_chunks = b"fmt " + len(fmt_payload).to_bytes(4, "little") + fmt_payload + b"data" + (2).to_bytes(4, "little") + b"\x00\x00"
    book = ExportBook(
        title="Sound Fixture",
        source_family=SourceFamily.SSED,
        source_id="SNDFIX",
        entries=(
            ExportEntry(
                target="ssed:SNDFIX:2:0",
                headword="sound",
                body=ExportBody(
                    events=(ExportBodyEvent(kind="media_ref", text="play", value="audio:one"),),
                    source_kind="synthetic",
                ),
            ),
        ),
        resources=(ExportResource(resource_id="audio:one", kind="audio", payload=wave_chunks, mime_type="audio/wav"),),
    )

    report = EpwingV6Backend().write(book, tmp_path / "epwing")
    data_dir = tmp_path / "epwing" / report.subbook_dir / "DATA"

    assert report.sound_resources == 1
    assert report.audio_controls == 1
    assert report.image_controls == 0
    assert (data_dir / "HONMONS").read_bytes().startswith(b"fmt ")
    honmon2 = (data_dir / "HONMON2").read_bytes()
    assert b"\x1f\x4a" in honmon2
    assert b"\x1f\x6a" in honmon2
    control_offset = honmon2.index(b"\x1f\x4a")
    assert honmon2[control_offset + 2 : control_offset + 6] == b"\x00\x01\x00\x12"
    index_ids = {honmon2[16 + offset] for offset in range(0, honmon2[1] * 16, 16)}
    assert 0xD8 in index_ids
    assert all(diagnostic.code != "media_ref_placeholder" for diagnostic in report.diagnostics)


def test_ssed_export_uses_rendererdb_for_dense_honmon_sidecar() -> None:
    root = REPO_ROOT / "tests" / "fixtures" / "synthetic" / "dense_honmon_t_contents"

    book = load_export_book(root, ExportSourceOptions(source_family=SourceFamily.SSED))

    assert len(book.entries) == 1
    assert book.entries[0].target == "ssed:DENSETC.IDX:2:0"
    assert book.entries[0].headword == "alpha"
    assert book.entries[0].body.source_kind == "rendererdb_t_contents_html"
    assert any(event.kind == "text" and event.text == "alpha body" for event in book.entries[0].body.events)
    assert any(diagnostic.code == "rendererdb_dense_honmon_source" for diagnostic in book.diagnostics)


def test_ssed_export_extracts_hanrei_copyright_html(tmp_path: Path) -> None:
    idx = _write_source_ssed(tmp_path)
    hanrei = tmp_path / "HANREI" / "contents"
    hanrei.mkdir(parents=True)
    (hanrei / "copyright.html").write_text(
        "<html><head><title>著作権</title></head><body><p>著作権</p><p>Copyright fixture body</p></body></html>",
        encoding="utf-8",
    )

    book = load_export_book(idx, ExportSourceOptions(source_family=SourceFamily.SSED))

    assert book.copyright
    assert book.copyright[0].body is not None
    assert any(event.kind == "text" and "Copyright fixture body" in event.text for event in book.copyright[0].body.events)


def test_lved_sqlite_source_flattens_content_html_and_copyright(tmp_path: Path) -> None:
    root = tmp_path / "_DCT_LVEDX"
    root.mkdir()
    con = sqlite3.connect(root / "main.data")
    con.execute("create table content(id integer primary key, body text)")
    con.execute("create table list(id integer primary key, content_id integer, title text, subtitle text, type integer, anchor text)")
    con.execute("create table info(id integer primary key, type integer, name text, body text, media text)")
    con.execute('create table media(No integer primary key, f_name text, f_type integer, f_main blob)')
    con.execute("insert into content values(10, '<div><b>Alpha</b><br>definition<a href=\"lved.dataid:10\">self link</a><img src=\"figure.gif\"></div>')")
    con.execute("insert into list values(1, 10, '<span>alpha</span>', '', 0, '')")
    con.execute("insert into info values(1, 2, 'copyright.html', '<p>Copyright fixture</p>', '')")
    con.execute("insert into media values(1, 'figure', 1, ?)", (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",))
    con.commit()
    con.close()

    book = load_export_book(root, ExportSourceOptions(source_family=SourceFamily.LVED_SQLITE3))

    assert book.source_family == SourceFamily.LVED_SQLITE3
    assert len(book.entries) == 1
    assert book.entries[0].headword == "alpha"
    assert any(event.kind == "style_start" and event.value == "bold" for event in book.entries[0].body.events)
    assert any(event.kind == "link" and event.value == "lved:content:10" for event in book.entries[0].body.events)
    assert book.resources and book.resources[0].resource_id == "rendererdb-media:figure"
    assert any(event.kind == "media_ref" and event.value == "rendererdb-media:figure" for event in book.entries[0].body.events)
    assert book.copyright and book.copyright[0].label == "copyright.html"
    assert book.copyright[0].body is not None
    assert any(event.kind == "text" and event.text == "Copyright fixture" for event in book.copyright[0].body.events)


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
