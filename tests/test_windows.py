from pathlib import Path
import sqlite3

from logovista_tools.multiview import discover_multiview_packages, inspect_multiview_package
from logovista_tools.windows import (
    classify_hc_renderer_file,
    expected_numeric_index_for_hc_code,
    hc_code_from_name,
    parse_pe_summary,
)


def test_hc_code_and_expected_numeric_index() -> None:
    assert hc_code_from_name(Path("HC014F.dll")) == "014F"
    assert hc_code_from_name(Path("hc02d0.DLL")) == "02D0"
    assert hc_code_from_name(Path("KENCOLLO.IDX")) is None
    assert expected_numeric_index_for_hc_code("014F") == "0000014F.idx"


def test_hc_classification_uses_exinfo_and_numeric_sidecar(tmp_path) -> None:
    hc = tmp_path / "HC014F.dll"
    hc.write_bytes(b"not a PE file, but it mentions epwing2HtmlBodydata and vlpljblF")
    (tmp_path / "EXINFO.INI").write_text("[GENERAL]\nHTMLDLL=HC014F.dll\n", encoding="cp932")
    (tmp_path / "0000014F.idx").write_text("00000000\t00000000\troot\n", encoding="cp932")
    (tmp_path / "vlpljblF").write_bytes(b"sidecar")

    row = classify_hc_renderer_file(hc, compute_hash=False)

    assert row.code == "014F"
    assert row.exinfo_declares_this is True
    assert row.expected_numeric_index_present is True
    assert row.vlpljbl_siblings == ("vlpljblF",)
    assert row.pe.kind == "unknown"
    assert row.sha256 is None


def test_parse_pe_summary_reports_non_pe_kind(tmp_path) -> None:
    path = tmp_path / "HC0001.dll"
    path.write_bytes(b"SSEDDATA")

    summary = parse_pe_summary(path)

    assert summary.kind == "sseddata"
    assert summary.exports == ()


def test_multiview_package_resolves_menu_to_plain_sqlite_payload(tmp_path) -> None:
    package = tmp_path / "_DCT_TESTLAW"
    package.mkdir()
    (package / "menuData.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<list>
  <item label="Root" href="" />
  <item label="Law" href="M010" />
  <item label="Article" href="M010_HON-j1" />
  <item label="Index" href="index:jikou_01_001" />
</list>
""",
        encoding="utf-8",
    )

    body = package / "blvbat"
    con = sqlite3.connect(body)
    con.execute(
        "create table t_M010 ("
        "f_hore_code text, f_rec_id integer, f_anchor text, "
        "f_text text, f_text_plane text)"
    )
    con.execute(
        "insert into t_M010 values "
        "('M010', 10000, 'M010_HON-j1', '<div>本文</div>', '本文')"
    )
    con.commit()
    con.close()

    index = package / "ilvdat"
    con = sqlite3.connect(index)
    con.execute("create table t_index (f_hore_code text, f_title_no text, f_title_sub text, f_text text)")
    con.execute("insert into t_index values ('jikou_01_001', '事項', '見出し', '<div>索引</div>')")
    con.commit()
    con.close()

    assert discover_multiview_packages([tmp_path]) == [package.resolve()]

    report = inspect_multiview_package(package)

    assert report["payloads"][0]["role"] == "sqlite_law_body_table_store"
    assert report["menu"]["resolution_counts"]["hore_code"] == 1
    assert report["menu"]["resolution_counts"]["anchor_exact"] == 1
    assert report["menu"]["resolution_counts"]["index_row"] == 1
    assert report["menu"]["unresolved_count"] == 0


def test_multiview_content_search_payload_resolves_numeric_menu_ids(tmp_path) -> None:
    package = tmp_path / "_DCT_TESTCONTENT"
    package.mkdir()
    (package / "menuData.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<list>
  <item label="Root">
    <item label="Intro" href="000001" />
  </item>
</list>
""",
        encoding="utf-8",
    )
    static = package / "HANREI"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    (static / "menu.html").write_text("<html></html>", encoding="utf-8")

    body = package / "blvdat"
    con = sqlite3.connect(body)
    con.execute("create table t_contents (f_ID integer primary key, f_Title text, f_Body text)")
    con.execute(
        "create table t_search ("
        "f_No integer primary key, f_ID integer, f_Anchor integer, f_KeyWord text, "
        "f_MainFlag integer, f_Level integer, f_TitleMain text, f_TitleSub text, f_All text)"
    )
    con.execute("insert into t_contents values (1, '<b>Intro</b>', '<p>Body</p>')")
    con.execute("insert into t_search values (1, 1, 1, 'intro', 1, 0, '<b>Intro</b>', '', 'intro body')")
    con.commit()
    con.close()

    report = inspect_multiview_package(package)

    assert report["payloads"][0]["name_hint"] == "content_search_body"
    assert report["payloads"][0]["role"] == "sqlite_content_search_body"
    assert report["menu"]["resolution_counts"]["content_id"] == 1
    assert report["menu"]["unresolved_count"] == 0
    assert report["html_dirs"][0]["name"] == "HANREI"
