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
    inspect_lved_retained_resources,
    inspect_lved_sqlite_database,
    inspect_lved_roots,
    is_lved_payload_name,
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


def test_lved_retained_text_tree_decodes_and_correlates_without_row_text(tmp_path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    payload = package / "main.data"
    payload.write_bytes(b"x" * 4096)
    res = package / "res"
    res.mkdir()
    (res / "tree.idx").write_text("-127\t0\tRoot Label\n42\t1\tVisible Entry\n", encoding="utf-8-sig")
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
    assert item["target_correlation"]["matches"]["content_id"]["matched_values"] == 1
    assert item["target_correlation"]["matches"]["list_refid"]["matched_values"] == 1
    assert "Visible Entry" not in rendered


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
