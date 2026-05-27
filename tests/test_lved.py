import json
from argparse import Namespace
from pathlib import Path

from logovista_tools.cli import cmd_lved
from logovista_tools.decoded_model import dump_package_model_for_path
from logovista_tools.lved import (
    discover_lved_key_file,
    derive_lved_sqlcipher_key,
    infer_lved_dict_code,
    inspect_lved_loose_resources,
    inspect_lved_reader_features,
    inspect_lved_retained_resources,
    inspect_lved_sqlite_database,
    inspect_lved_roots,
    is_lved_payload_name,
    lved_sqlite_database_title,
    render_lved_sqlite_showcase,
    search_lved_sqlite_database,
    shannon_entropy,
)


def test_lved_payload_name_detection() -> None:
    assert is_lved_payload_name(Path("main.data"))
    assert is_lved_payload_name(Path("OXFPEU4.dbc"))
    assert not is_lved_payload_name(Path("main.data:Zone.Identifier"))
    assert not is_lved_payload_name(Path("HONMON.DIC"))


def test_infer_lved_dict_code() -> None:
    assert infer_lved_dict_code(Path("_DCT_OXFPEU4/main.data")) == "OXFPEU4"
    assert infer_lved_dict_code(Path("DIC/OXFPEU4/main.data")) == "OXFPEU4"
    assert infer_lved_dict_code(Path("OXFPEU4.dbc")) == "OXFPEU4"


def test_lved_key_file_detection_prefers_inferred_dict_code(tmp_path) -> None:
    package = tmp_path / "_DCT_OXFPEU4"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    key_file = package / "OXFPEU4.key"
    key_file.write_text("secret-key\n", encoding="utf-8")

    detected = discover_lved_key_file(payload)

    assert detected is not None
    assert detected.path == key_file
    assert detected.match == "inferred_dict_code"


def test_lved_inspection_uses_local_key_file_without_emitting_key(tmp_path) -> None:
    package = tmp_path / "_DCT_OXFPEU4"
    package.mkdir()
    (package / "main.data").write_bytes(bytes(range(256)) * 16)
    (package / "OXFPEU4.key").write_text("local-secret-key\n", encoding="utf-8")

    report = inspect_lved_roots([package], jobs=1)
    rendered = json.dumps(report)

    row = report["payloads"][0]
    assert row["key_file"] == {
        "detected": True,
        "name": "OXFPEU4.key",
        "match": "inferred_dict_code",
    }
    assert row["validation"][0]["source"] == "local_key_file"
    assert "local-secret-key" not in rendered


def test_lved_cli_uses_local_key_file_without_status_shadow_crash(tmp_path, capsys) -> None:
    package = tmp_path / "_DCT_OXFPEU4"
    package.mkdir()
    (package / "main.data").write_bytes(bytes(range(256)) * 16)
    (package / "OXFPEU4.key").write_text("local-secret-key\n", encoding="utf-8")

    code = cmd_lved(
        Namespace(
            root=[package],
            dict_id=None,
            dict_code=None,
            key_file=None,
            memory_dump=None,
            write_decrypted=None,
            inspect_sqlite=False,
            inspect_retained=False,
            inspect_loose_resources=False,
            inspect_reader=False,
            search=[],
            search_mode=None,
            search_limit=20,
            render_link_limit=500,
            render_dir=None,
            json=False,
            jobs=1,
            verbose=False,
            debug=False,
        )
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "key_file: OXFPEU4.key" in out
    assert "validation[local_key_file]:" in out
    assert "local-secret-key" not in out


def test_lved_sqlite_inspection_classifies_core_tables_without_row_text(tmp_path) -> None:
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text, titlesub text);
        create table content(id integer, type integer, body text, media text);
        create table media(id integer, type integer, os integer, name text, main blob);
        create table info(id integer, type integer, name text, body text, media text);
        create virtual table search using fts4(forward, back, part, fts, advanced, filter);
        insert into list values(1, 1, 0, 'a', 'visible title', 'sub');
        insert into content values(1, 0, '<div><a href="lved.dataid:2"><img src="x.png"></a></div>', 'x.png');
        insert into media values(1, 0, 1, 'x.png', x'89504e47');
        insert into info values(1, 0, 'about', '<table><tr><td>about</td></tr></table>', '');
        insert into search(docid, forward, back, part, fts, advanced, filter)
          values(1, 'alpha', 'ahpla', 'alp', 'alpha body', 'advanced', 'filter');
        """
    )
    con.close()

    report = inspect_lved_sqlite_database(db)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["preferred_family_name"] == "LVED_SQLITE3"
    assert report["role_counts"]["entry_list"] == 1
    assert report["role_counts"]["entry_content"] == 1
    assert report["role_counts"]["media_blob_store"] == 1
    assert report["role_counts"]["fts_search"] == 1
    assert report["feature_counts"]["entry_body_store"] == 1
    assert report["feature_counts"]["search_column_advanced"] == 1
    content = next(item for item in report["objects"] if item["name"] == "content")
    body = next(item for item in content["column_profiles"] if item["name"] == "body")
    assert body["sample_text_signals"]["lved_dataid"] == 1
    assert body["sample_text_signals"]["html_img"] == 1
    assert "visible title" not in rendered
    assert "alpha body" not in rendered


def test_lved_sqlite_title_prefers_product_title_over_generic_info_pages(tmp_path) -> None:
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.execute("create table info(id integer, type integer, name text, body text, media text)")
    con.execute("insert into info values(1, 2, 'index.html', '<div class=\"title\">記号・略語表</div>', '')")
    con.execute(
        "insert into info values(2, 0, 'copyright.html', "
        "'<div>著作権</div><p>●『新明解国語辞典 第八版』</p><p>© publisher</p>', '')"
    )
    con.commit()
    con.close()

    assert lved_sqlite_database_title(db) == "新明解国語辞典 第八版"


def test_lved_sqlite_title_strips_ruby_annotations(tmp_path) -> None:
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.execute("create table info(id integer, type integer, name text, body text, media text)")
    con.execute(
        "insert into info values(1, 2, 'index.html', "
        "'<div class=\"title\"><ruby>明鏡<rt>めいきょう</rt></ruby>国語辞典 第三版</div>', '')"
    )
    con.commit()
    con.close()

    assert lved_sqlite_database_title(db) == "明鏡国語辞典 第三版"


def test_lved_sqlite_title_prefers_explicit_book_title_over_sub_index(tmp_path) -> None:
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.execute("create table info(id integer, type integer, name text, body text, media text)")
    con.execute(
        "insert into info values(1, 1, 'index.html', "
        "'<div class=\"索引\"><span class=\"title\">和英小辞典</span>"
        "<span>中高生にとって必要な語を中心に収録</span></div>', '')"
    )
    con.execute(
        "insert into info values(2, 104, 'h04.html', "
        "'<div class=\"Copyright\"><div class=\"凡例章見出\">著作権表示</div>"
        "<div class=\"凡例書籍名\">エースクラウン英和辞典 第4版</div></div>', '')"
    )
    con.commit()
    con.close()

    assert lved_sqlite_database_title(db) == "エースクラウン英和辞典 第4版"


def test_lved_search_modes_join_list_to_content(tmp_path) -> None:
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text, titlesub text);
        create table content(id integer, type integer, body text, media text);
        create virtual table search using fts4(forward, back, part, fts, advanced1, filter);
        insert into list values(1, 42, 0, 'hit-anchor', 'Alpha', '');
        insert into content values(42, 0, '<div>Alpha body</div>', '');
        insert into search(docid, forward, back, part, fts, advanced1, filter)
          values(1, 'alpha', 'ahpla', 'a l p h a', 'alpha body', 'example phrase', '∥alpha∥');
        """
    )
    con.close()

    exact = search_lved_sqlite_database(db, "alpha", mode="exact")
    forward = search_lved_sqlite_database(db, "al", mode="forward")
    backward = search_lved_sqlite_database(db, "ha", mode="backward")
    partial = search_lved_sqlite_database(db, "alp", mode="partial")
    advanced = search_lved_sqlite_database(db, "phrase", mode="advanced1")

    for report in (exact, forward, backward, partial, advanced):
        assert report["status"] == "ok"
        assert report["hits"][0]["list_id"] == 1
        assert report["hits"][0]["content_id"] == 42


def test_lved_secondary_searchsub_provider(tmp_path) -> None:
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text, titlesub text);
        create table content(id integer, type integer, body text, media text);
        create table listsub(id integer, refid integer, type integer, anchor text, title text, titlesub text);
        create virtual table search using fts4(forward, back, part, fts, filter);
        create virtual table searchsub using fts4(advanced3);
        insert into list values(1, 42, 0, '', 'Main', '');
        insert into listsub values(7, 99, 0, '', 'Secondary', '');
        insert into content values(42, 0, '<div>Main body</div>', '');
        insert into content values(99, 0, '<div>Secondary body</div>', '');
        insert into search(docid, forward, back, part, fts, filter) values(1, 'main', 'niam', 'm a i n', 'main body', '∥main∥');
        insert into searchsub(docid, advanced3) values(7, 'secondary phrase');
        """
    )
    con.close()

    report = search_lved_sqlite_database(db, "phrase", mode="advanced3")

    assert report["status"] == "ok"
    assert report["search_table"] == "searchsub"
    assert report["list_table"] == "listsub"
    assert report["hits"][0]["list_id"] == 7
    assert report["hits"][0]["content_id"] == 99


def test_lved_render_showcase_rewrites_media_assets_and_info_surfaces(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text, titlesub text);
        create table content(id integer, type integer, body text, media text);
        create table media(id integer, name text, type integer, main);
        create table sound(id integer, name text, type integer, main blob);
        create table info(id integer, type integer, name text, body text, media text);
        create table binran(id integer, type integer, name text, body text, media text);
        create virtual table search using fts4(forward, back, part, fts, filter);
        insert into list values(1, 42, 0, 'hit-anchor', 'Alpha', '');
        insert into content values(42, 0, '<div class="midashi">Alpha</div><img src="pic.png"><img src="txtasset"><img src="invalid_text_png"><a href="lved.dataid:42#anchor">self</a><a href="lved.dataid.result:99?TEST">result</a><a href="lved.dataid00000100#named">no colon</a><a href="lved.contentlink:MEIKYOU3.00083000#id_00083010">cross</a><a href="lved.dataid.dict.READERS3:01063210">other dict</a><a href="99">bare numeric</a><a href="050000/0000">relative addr</a><a href="lved.info:hanrei.html#top">help</a><a href="lved.media.sound:snd.mp3">sound</a><a href="lved.pdf:fig000001.pdf">pdf</a><a href="lved.dict.TEST:pictlink.picture.html#map">picture</a><a href="lved.addr00000001:0004">addr resolved</a><a href="lved.addr00001234:0567">addr deferred</a><a href="lved.imag00000001:0004:0000002c">image addr deferred</a><a href="lved.bookmark:42">bookmark</a><a href="lved.binran:ref.html.html#x">ref</a><a href="lved.plugin:noop">plugin</a><a href="lved.sql:2:0:0:0:42">sql</a><a href="lved.findnum:3">findnum</a><a href="lved.select:true?TEST">select</a><a href="lved.group.settings">group</a><a href="lved.browser.close">browser</a><a href="lved.future:hook">unknown</a>', '');
        insert into media values(7, 'pic', 2, x'47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b');
        insert into media values(9, 'txtasset', 2, 'text storage resource');
        insert into media values(10, 'fig000001', 6, x'255044462d312e360a');
        insert into media values(11, 'invalid_text_png', 2, cast(x'89504E470D0A1A0A' as text));
        insert into sound values(8, 'snd', 5, x'49443303000000000000');
        insert into info values(1, 0, 'hanrei.html', '<div><img src="pic.png"><a href="lved.dataid:99">Help</a></div>', '');
        insert into info values(2, 0, 'picture.html', '<div>Picture dictionary page</div>', '');
        insert into binran values(1, 2, 'ref.html', '<div>Reference</div>', '');
        insert into search(docid, forward, back, part, fts, filter) values(1, 'alpha', 'ahpla', 'a l p h a', 'alpha body', '∥alpha∥');
        """
    )
    con.execute(
        "insert into content values(99, 0, ?, '')",
        (sqlite3.Binary('<a name="000000010020"></a><div>Linked from BLOB HTML</div>'.encode("utf-8")),),
    )
    con.commit()
    con.close()

    out = tmp_path / "render"
    report = render_lved_sqlite_showcase(db, payload, out, queries=["alpha"], modes=["exact"], limit=5)
    index = (out / "index.html").read_text(encoding="utf-8")
    entry = (out / "entry_42.html").read_text(encoding="utf-8")

    assert report["status"] == "rendered"
    assert (out / "assets" / "pic.gif").is_file()
    assert (out / "assets" / "txtasset.bin").read_text(encoding="utf-8") == "text storage resource"
    assert (out / "assets" / "fig000001.pdf").is_file()
    assert (out / "assets" / "invalid_text_png.png").is_file()
    assert "entry_42.html" in index
    assert 'href="entry_42.html#hit-anchor"' in index
    assert 'src="assets/pic.gif"' in entry
    assert 'src="assets/txtasset.bin"' in entry
    assert 'src="assets/invalid_text_png.png"' in entry
    assert 'href="entry_42.html#anchor"' in entry
    assert 'href="entry_99.html" data-lv-original-href="lved.dataid.result:99?TEST" data-lv-hook="search-result-detail"' in entry
    assert 'href="entry_100.html#named" data-lv-original-href="lved.dataid00000100#named" data-lv-hook="dataid-no-colon"' in entry
    assert 'href="#" data-lv-original-href="lved.contentlink:MEIKYOU3.00083000#id_00083010" data-lv-hook="contentlink" data-lv-dict="MEIKYOU3" data-lv-content-id="83000" data-lv-anchor="id_00083010"' in entry
    assert 'href="#" data-lv-original-href="lved.dataid.dict.READERS3:01063210" data-lv-hook="dataid-dict" data-lv-dict="READERS3" data-lv-content-id="1063210"' in entry
    assert 'href="entry_99.html" data-lv-original-href="99"' in entry
    assert 'href="#" data-lv-original-href="050000/0000" data-lv-hook="relative-address"' in entry
    assert 'href="info_hanrei.html#top"' in entry
    assert 'href="assets/snd.mp3"' in entry
    assert 'href="assets/fig000001.pdf"' in entry
    assert 'href="info_picture.html#map"' in entry
    assert 'href="entry_99.html#000000010020" data-lv-original-href="lved.addr00000001:0004" data-lv-addr-delta="28"' in entry
    assert 'href="#" data-lv-original-href="lved.addr00001234:0567"' in entry
    assert 'href="#" data-lv-original-href="lved.imag00000001:0004:0000002c" data-lv-hook="image"' in entry
    assert 'href="#" data-lv-original-href="lved.bookmark:42" data-lv-hook="bookmark"' in entry
    assert 'href="binran_ref.html#x"' in entry
    assert 'href="#" data-lv-original-href="lved.plugin:noop" data-lv-hook="plugin"' in entry
    assert 'href="#" data-lv-original-href="lved.sql:2:0:0:0:42" data-lv-hook="sql"' in entry
    assert 'href="#" data-lv-original-href="lved.findnum:3" data-lv-hook="findnum"' in entry
    assert 'href="#" data-lv-original-href="lved.select:true?TEST" data-lv-hook="select"' in entry
    assert 'href="#" data-lv-original-href="lved.group.settings" data-lv-hook="group"' in entry
    assert 'href="#" data-lv-original-href="lved.browser.close" data-lv-hook="browser"' in entry
    assert 'href="#" data-lv-original-href="lved.future:hook" data-lv-hook="unknown"' in entry
    assert 'href="entry_99.html"' in (out / "info_hanrei.html").read_text(encoding="utf-8")
    assert (out / "entry_99.html").is_file()
    entry_99 = (out / "entry_99.html").read_text(encoding="utf-8")
    assert "Linked from BLOB HTML" in entry_99
    assert "b&#x27;" not in entry_99
    assert "b'" not in entry_99
    assert (out / "binran_ref.html").is_file()
    assert report["binran_pages"][0]["path"] == "binran_ref.html"


def test_lved_reader_features_classify_hanrei_and_fragments(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text);
        create table content(id integer, type integer, body text, media text);
        create table media(id integer, name text, type integer, main blob);
        create table info(id integer, type integer, name text, body text, media text);
        create virtual table search using fts4(forward, back, part, fts, filter);
        insert into content values(42, 0, '<div class="midashi"><img src="pic.png"><a href="lved.dataid:42">Alpha</a><a href="lved.addr00000001:0004">addr</a></div>', '');
        insert into content values(99, 0, '<a name="000000010020"></a><div>Addr target</div>', '');
        insert into info values(1, 0, 'hanrei.html', '<div><a href="lved.info:index.html">Help</a></div>', '');
        insert into info values(null, 0, 'index.html', '<div>Home</div>', '');
        """
    )
    con.close()

    report = inspect_lved_reader_features(db, payload)

    assert report["home_surfaces"][0]["kind"] == "hanrei"
    assert any(surface["kind"] == "home_index" and surface["id"] is None for surface in report["home_surfaces"])
    assert report["render_readiness"]["requires_viewer_templates_or_css"] is True
    assert report["render_readiness"]["sample_image_ref_count"] == 1
    assert report["render_readiness"]["protocol_counts"]["lved.dataid:"] == 1
    assert report["render_readiness"]["lved_addr_resolution"]["target_count"] == 1
    assert report["render_readiness"]["lved_addr_resolution"]["resolved_target_count"] == 1
    assert report["render_readiness"]["lved_addr_resolution"]["status"] == "available"
    assert report["render_readiness"]["info_body"]["protocol_counts"]["lved.info:"] == 1
    assert report["search_providers"][0]["available_modes"] == ["exact", "forward", "backward", "partial", "fulltext", "filter"]


def test_lved_reader_features_summarize_optional_providers(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text);
        create table content(id integer, type integer, body text, media text);
        create table t_contents(f_DataId integer, f_Type integer, f_Title text, f_Html text, f_Plane text, f_Keyword text);
        create table t_convert(f_array_no integer, block integer, offset integer, old_title text, id integer, title text, type integer);
        create table sound(id integer, name text, type integer, main blob);
        insert into list values(1, 42, 0, '', 'Alpha');
        insert into content values(42, 0, '<div>Alpha body</div>', '');
        insert into t_contents values(42, 2, 'Alpha', '<div class="midashi">Alpha</div>', 'Alpha body', 'alpha');
        insert into t_convert values(1, 3, 128, 'old alpha', 42, 'Alpha', 2);
        insert into sound values(1, 'alpha.mp3', 5, x'49443303000000000000');
        """
    )
    con.close()

    report = inspect_lved_reader_features(db, payload)
    providers = {item["table"]: item for item in report["optional_provider_readiness"]}

    assert providers["t_contents"]["provider_kind"] == "legacy_html_content_rows"
    assert providers["t_contents"]["html_rows"] == 1
    assert providers["t_contents"]["target_columns"]["f_DataId"]["best_match"] == "content_id"
    assert providers["t_convert"]["provider_kind"] == "legacy_block_offset_bridge"
    assert providers["t_convert"]["target_columns"]["id"]["best_match"] == "content_id"
    assert providers["sound"]["provider_kind"] == "audio_blob_store"
    assert providers["sound"]["type_counts"] == [{"value": "5", "count": 1}]


def test_lved_retained_text_tree_decodes_and_correlates_without_row_text(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    res = package / "res"
    res.mkdir()
    (res / "tree.idx").write_text("-127\t0\tRoot Label\n42?key=visible\t1\tVisible Entry\n", encoding="utf-8-sig")
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text);
        create table content(id integer, type integer, body text, media text);
        insert into list values(1, 42, 0, '00000042', 'Visible Entry');
        insert into content values(42, 0, '<div>Visible Entry</div>', '');
        """
    )
    con.close()

    report = inspect_lved_retained_resources(payload, db)
    rendered = json.dumps(report, ensure_ascii=False)
    item = report["files"][0]

    assert item["kind"] == "text_tree"
    assert item["role"] == "sqlite_content_navigation_tree"
    assert item["rows"] == 2
    assert item["sentinel_rows"] == 1
    assert item["query_rows"] == 1
    assert item["query_key_counts"] == {"key": 1}
    assert item["target_forms"]["decimal_with_query"] == 1
    assert item["target_correlation"]["matches"]["content_id"]["matched_values"] == 1
    assert item["target_correlation"]["matches"]["list_refid"]["matched_values"] == 1
    assert "Visible Entry" not in rendered


def test_lved_render_showcase_materializes_retained_tree_navigation(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    res = package / "res"
    res.mkdir()
    (res / "tree.idx").write_text("-127\t0\tRoot Label\n42?key=visible\t1\tVisible Entry\n", encoding="utf-8-sig")
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text);
        create table content(id integer, type integer, body text, media text);
        create table media(id integer, name text, type integer, main blob);
        create table info(id integer, type integer, name text, body text, media text);
        create virtual table search using fts4(forward, back, part, fts, filter);
        insert into list values(1, 42, 0, '', 'Visible Entry');
        insert into content values(42, 0, '<div>Visible Entry</div>', '');
        insert into search(docid, forward, back, part, fts, filter) values(1, 'visible', 'elbisiv', 'v i s i b l e', 'visible entry', '∥visible∥');
        """
    )
    con.close()

    out = tmp_path / "showcase"
    report = render_lved_sqlite_showcase(db, payload, out, queries=["visible"], modes=["exact"], limit=5)
    tree_page = out / report["tree_pages"][0]["path"]
    html = tree_page.read_text(encoding="utf-8")

    assert report["tree_pages"][0]["source"] == "res/tree.idx"
    assert report["tree_pages"][0]["linked_rows"] == 1
    assert 'href="entry_42.html"' in html
    assert 'data-lv-query="key=visible"' in html
    assert "Visible Entry" in html


def test_lved_render_showcase_marks_retained_tree_deferred_targets(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    res = package / "res"
    res.mkdir()
    (res / "tree.idx").write_text(
        "-127\t0\tRoot Label\n42\t1\tVisible Entry\n43\t1\tDeferred Entry\n",
        encoding="utf-8-sig",
    )
    db = tmp_path / "payload.sqlite"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        """
        create table list(id integer, refid integer, type integer, anchor text, title text);
        create table content(id integer, type integer, body text, media text);
        create table media(id integer, name text, type integer, main blob);
        create table info(id integer, type integer, name text, body text, media text);
        create virtual table search using fts4(forward, back, part, fts, filter);
        insert into list values(1, 42, 0, '', 'Visible Entry');
        insert into list values(2, 43, 0, '', 'Deferred Entry');
        insert into content values(42, 0, '<div>Visible Entry</div>', '');
        insert into content values(43, 0, '<div>Deferred Entry</div>', '');
        insert into search(docid, forward, back, part, fts, filter) values(1, 'visible', 'elbisiv', 'v i s i b l e', 'visible entry', '∥visible∥');
        """
    )
    con.close()

    out = tmp_path / "showcase"
    report = render_lved_sqlite_showcase(
        db,
        payload,
        out,
        queries=["visible"],
        modes=["exact"],
        limit=1,
        linked_entry_limit=0,
    )
    tree_page = out / report["tree_pages"][0]["path"]
    html = tree_page.read_text(encoding="utf-8")

    assert report["tree_pages"][0]["linked_rows"] == 1
    assert report["tree_pages"][0]["deferred_entry_rows"] == 1
    assert report["tree_pages"][0]["stubbed_entry_rows"] == 0
    assert 'href="entry_42.html"' in html
    assert 'data-lv-deferred-entry="43"' in html
    assert 'href="entry_43.html"' not in html


def test_lved_retained_aux_text_tree_decodes_cp932_without_row_text(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    (package / "000002C5.idx").write_bytes("00000000\t0000ffff\t\tランク A\n".encode("cp932"))

    report = inspect_lved_retained_resources(payload)
    rendered = json.dumps(report, ensure_ascii=False)
    item = report["files"][0]

    assert item["kind"] == "aux_text_tree"
    assert item["role"] == "auxiliary_hex_text_tree"
    assert item["encoding"] == "cp932"
    assert item["rows"] == 1
    assert item["second_field_sentinel_rows"] == 1
    assert "ランク" not in rendered


def test_lved_loose_resources_decode_images_fonts_and_runtime_without_text(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    res = package / "res"
    res.mkdir()
    # Retained resources are handled by --inspect-retained, not this loose scan.
    (res / "tree.idx").write_text("1\t1\tHidden Label\n", encoding="utf-8")
    (res / "dummy.gif").write_bytes(
        bytes.fromhex(
            "47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b"
        )
    )
    (res / "style.css").write_text(".hidden { color: red; }", encoding="utf-8")
    (res / "font.woff").write_bytes(b"wOFF" + b"\x00" * 32)

    report = inspect_lved_loose_resources(payload)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["file_count"] == 3
    assert report["kind_counts"]["image"] == 1
    assert report["kind_counts"]["font"] == 1
    assert report["kind_counts"]["web_runtime_text"] == 1
    assert report["media_counts"]["gif"] == 1
    assert report["dimension_counts"]["gif:1x1"] == 1
    assert report["font_counts"]["woff"] == 1
    assert "Hidden Label" not in rendered
    assert "color: red" not in rendered


def test_lved_key_derivation_metadata_shape() -> None:
    key = derive_lved_sqlcipher_key(750, "OXFPEU4")

    assert key.startswith("jlasgoiahoiampvsjhosDHfopj")
    assert key.endswith("o4" + str(750 * 19286))


def test_shannon_entropy_bounds() -> None:
    assert shannon_entropy(b"") == 0.0
    assert shannon_entropy(b"\x00" * 256) == 0.0
    assert shannon_entropy(bytes(range(256))) == 8.0


def test_memory_dump_candidates_are_not_emitted(tmp_path) -> None:
    dump = tmp_path / "viewer.dmp"
    candidate = "jlasgoiahoiampvsjhosDHfopjxx123456"
    dump.write_text(f"noise {candidate} noise", encoding="utf-16le")

    report = inspect_lved_roots([], memory_dump=dump)
    rendered = json.dumps(report)

    assert report["memory_dump"]["candidate_keys"] == 1
    assert candidate not in rendered


def test_dump_package_model_classifies_lved_as_deferred_family(tmp_path) -> None:
    package = tmp_path / "_DCT_TESTLVED"
    package.mkdir()
    # 4096 bytes with high entropy but no SQLite header is enough for LVED
    # classification; validation/decryption remains a separate command path.
    (package / "main.data").write_bytes(bytes(range(256)) * 16)
    (package / "main.data:Zone.Identifier").write_text("noise", encoding="utf-8")
    (package / "res").mkdir()
    (package / "res" / "CID00001_t.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (package / "res" / "CID00001_t.png:Zone.Identifier").write_text("noise", encoding="utf-8")

    model = dump_package_model_for_path(package, Namespace(dict=None, sample_limit=20))

    assert model["classification"]["package_family"] == "lved_sqlcipher"
    assert model["classification"]["status"] == "deferred"
    assert model["classification"]["body_source_hint"] == "lved_sqlcipher"
    assert model["readiness"]["requirements"]["deferred_package_family"] is True
    assert model["writer_readiness"]["read_existing"] == "gray"
    assert model["writer_readiness"]["export_existing"] == "gray"
    assert model["writer_readiness"]["author_core_ssed_v0"] == "gray"
    assert model["writer_readiness"]["lossless_repack_existing"] == "gray"
    assert model["writer_readiness"]["legacy_ssed_subset"] == "gray"
    assert model["writer_readiness"]["legacy_ssed_subset_blockers"] == ["non_ssed_package_family"]
    assert model["families"]["lved"]["payloads"][0]["classification"] == "sqlcipher_lved_candidate"
    assert model["resources"]["static_sidecars"]["extension_counts"].get(".identifier") is None


def test_dump_package_model_classifies_multiview_as_deferred_family(tmp_path) -> None:
    package = tmp_path / "_DCT_TESTMULTI"
    package.mkdir()
    (package / "menuData.xml").write_text("<menu><item href=\"about\" label=\"About\" /></menu>", encoding="utf-8")
    (package / "blvbat").write_bytes(b"not sqlite")
    (package / "blvbat:Zone.Identifier").write_text("noise", encoding="utf-8")

    model = dump_package_model_for_path(package, Namespace(dict=None, sample_limit=20))

    assert model["classification"]["package_family"] == "multiview_sqlite"
    assert model["classification"]["status"] == "deferred"
    assert model["classification"]["body_source_hint"] == "multiview_sqlite"
    assert model["readiness"]["requirements"]["deferred_package_family"] is True
    assert model["writer_readiness"]["lossless_repack_existing"] == "gray"
    assert model["writer_readiness"]["lossless_repacker"] == "gray"
    assert model["families"]["multiview"]["payloads"][0]["name"] == "blvbat"
    assert len(model["families"]["multiview"]["payloads"]) == 1
