from __future__ import annotations

import sqlite3
from pathlib import Path

from logovista_tools.hourei import discover_hourei_packages, inspect_hourei_package, search_hourei, write_hourei_showcase


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def make_hourei_fixture(root: Path) -> Path:
    package = root / "LogoVista電子法令 Professional"
    (package / "_DataBase" / "image").mkdir(parents=True)
    (package / "_Programs").mkdir(parents=True)
    (package / "_Programs" / "LVMultiView.exe").write_bytes(b"MZ")
    (package / "_Programs" / "index_panel.html").write_bytes(
        '<html><body><a href="lved_ref:み">み</a></body></html>'.encode("cp932")
    )
    (package / "_Programs" / "LVLawBrowser_00.css").write_text(
        ".ahref{color:#5da24c}.hon_jyo{font-family:$ARTICLE_FONT_NAME$;font-size:$ARTICLE_FONT_SIZE$}",
        encoding="utf-8",
    )
    (package / "_Programs" / "LVLawBrowser_11.css").write_text(
        ".ahref{color:#5da24c}",
        encoding="utf-8",
    )
    (package / "_DataBase" / "image" / "fig.gif").write_bytes(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )

    con = _connect(package / "_DataBase" / "hore_base.db")
    con.execute("create table t_hore(f_hore_id text, f_name text, f_abbr1 text)")
    con.execute("insert into t_hore values('401200000000000042', '民法', '民法')")
    con.execute("insert into t_hore values('401200000000000043', '商法', '商法')")
    con.commit()
    con.close()

    con = _connect(package / "_DataBase" / "hore_search_a.db")
    con.execute(
        "create table t_hore("
        "f_hore_id text, f_name text, f_name_sub text, f_abbr1 text, f_abbr2 text, f_abbr3 text, f_kana_order text, f_text_plane text)"
    )
    con.execute("insert into t_hore values('401200000000000042', '民法', '', '民法', '', '', '100', '民法 本文')")
    con.commit()
    con.close()

    con = _connect(package / "_DataBase" / "horejo_base.db")
    con.execute(
        "create table t_horejo("
        "f_hore_id text, f_rec_id integer, f_anchor text, f_title_no text, f_title_sub text, f_text_plane text)"
    )
    con.execute(
        "create table t_horejomi("
        "f_hore_id text, f_rec_id integer, f_anchor text, f_title_no text, f_title_sub text, f_text_plane text)"
    )
    con.execute("insert into t_horejo values('401200000000000042', 1, 'A1', '第一条', '', '民法')")
    con.commit()
    con.close()

    for hore_id, body in {
        "401200000000000042": '<div class="hon_jyo"><a href="lved_mark&&A1">第1条</a><img src="fig.gif"><a href="lved_ref&1:401200000000000043&TOP">参照</a><a href="lved_ref&4:401200000000000042:428200000000000044">未施行</a>本文</div>',
        "401200000000000043": '<div class="hon_jyo"><a href="lved_ref&&TOP">第1条</a>商法</div>',
    }.items():
        con = _connect(package / "_DataBase" / "H01" / f"{hore_id}.db")
        con.execute("create table t_page(f_rec_id integer, f_text text)")
        con.execute("insert into t_page values(1, ?)", (body,))
        con.commit()
        con.close()
        for subdir, suffix in (("I", "_IDX.html"), ("P", "_P.html")):
            target = package / "_DataBase" / "HTMLs" / subdir / f"{hore_id}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('<a name="10000"></a><a href="lved_ref&&A1">第1条</a>', encoding="utf-8")

    con = _connect(package / "_Kanpou" / "hore_kanpou.db")
    con.execute("create table t_kanpou(f_rec_id integer, f_hore_id text, f_name text, f_name_sub text, f_text text, f_text_plane text)")
    con.commit()
    con.close()
    con = _connect(package / "_Kanpou" / "hore_aramashi.db")
    con.execute("create table t_aramashi(f_rec_id integer, f_hore_id text, f_name text, f_name_sub text, f_text text, f_text_plane text)")
    con.commit()
    con.close()
    return package


def test_hourei_discovery_search_and_showcase_render(tmp_path: Path) -> None:
    package = make_hourei_fixture(tmp_path)

    assert discover_hourei_packages([tmp_path]) == [package.resolve()]
    assert search_hourei(package, "民法", limit=5)[0]["hore_id"] == "401200000000000042"
    audit = inspect_hourei_package(package, deep=True)
    assert audit["cached_html_surfaces"]["groups"]["index"]["file_count"] == 2
    assert audit["cached_html_surfaces"]["paired_index_preview_count"] == 2
    assert audit["link_and_asset_readiness"]["link_forms"]["future_or_revision_ref"] == 1

    report = write_hourei_showcase(package, tmp_path / "showcase", queries=["民法"], limit=5)

    assert len(report["rendered_entries"]) == 2
    assert report["index_panel"]["query_count"] == 1
    assert report["validation"]["missing_href_count"] == 0
    assert report["validation"]["missing_src_count"] == 0
    assert report["validation"]["protocol_href_counts"] == {}
    entry = (tmp_path / "showcase" / "entry_401200000000000042.html").read_text(encoding="utf-8")
    assert 'href="#A1" data-lv-original-href="lved_mark&amp;&amp;A1"' in entry
    assert 'href="entry_401200000000000043.html#TOP"' in entry
    assert 'data-lv-ref-kind="future_or_revision_ref"' in entry
    assert 'href="#" data-lv-original-href="lved_ref&amp;4:' in entry
    assert 'src="assets/fig.gif"' in entry
    assert (tmp_path / "showcase" / "assets" / "fig.gif").exists()
    assert (tmp_path / "showcase" / "toc_401200000000000042.html").exists()
    assert (tmp_path / "showcase" / "preview_401200000000000042.html").exists()
    panel = (tmp_path / "showcase" / "kana_index_panel.html").read_text(encoding="utf-8")
    assert "み" in panel
    assert "��" not in panel
