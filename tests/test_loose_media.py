from __future__ import annotations

import sqlite3
from pathlib import Path

from logovista_tools.loose_media import (
    classify_loose_decoded_resource,
    expand_britannica_inline_address_markers,
    iter_candidate_files,
    is_britannica_chronology_db_candidate,
    is_britannica_top_dat_path,
    normalize_britannica_whatday_table,
    parse_britannica_chronology_db,
    parse_britannica_top_dat,
    parse_britannica_whatday_file,
    parse_lved_address,
    render_britannica_html_fragment,
)
from logovista_tools.lvcrypto import logofont_cipher_key_iv, macos_logofont_cipher_key_iv


def encrypt_logofont_cipher_bytes(data: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def encrypt_macos_logofont_cipher_bytes(data: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = macos_logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def test_parse_britannica_whatday_html_fragment(tmp_path: Path) -> None:
    path = tmp_path / "1-2.body"
    path.write_bytes(
        (
            '<BODY><TABLE><TR><TD><A class="lineLink" '
            'href="lved.addr0000002a:0010">label</A></TD></TR></TABLE></BODY>'
        ).encode("cp932")
    )

    decoded = parse_britannica_whatday_file(path)

    assert decoded.month == 1
    assert decoded.day == 2
    assert decoded.kind == "body"
    assert 'href="lvaddr://0000002a/0010"' in decoded.html
    assert "lved.addr0000002a:0010" not in decoded.html
    assert "<BODY" not in decoded.html
    assert "</BODY>" not in decoded.html
    assert decoded.references[0].address is not None
    assert decoded.references[0].address.block == 0x2A
    assert decoded.references[0].address.offset == 0x10


def test_britannica_inline_address_markers_render_as_links() -> None:
    rendered = expand_britannica_inline_address_markers("＃＃Ｓ0001bea5:0724モーツァルトＥ＃＃死去")

    assert 'href="lvaddr://0001bea5/0724"' in rendered
    assert 'data-lv-address="lved.addr0001bea5:0724"' in rendered
    assert "モーツァルト</a>死去" in rendered
    assert "＃＃Ｓ" not in rendered
    assert "Ｅ＃＃" not in rendered


def test_render_britannica_html_fragment_expands_markers_and_lved_links() -> None:
    rendered = render_britannica_html_fragment(
        '<TD>＃＃Ｓ0001bea5:0724モーツァルトＥ＃＃と'
        '<A href="lved.addr0000002a:0010">label</A></TD>'
    )

    assert 'href="lvaddr://0001bea5/0724"' in rendered
    assert 'href="lvaddr://0000002a/0010"' in rendered
    assert "＃＃Ｓ" not in rendered
    assert "Ｅ＃＃" not in rendered


def test_britannica_whatday_table_drops_viewer_spacer_column() -> None:
    rendered = normalize_britannica_whatday_table(
        '<TABLE><TR><TH colSpan=3>きょうの出来事</TH></TR>'
        '<TR><TD style="BACKGROUND-COLOR: #cce6ff">1901年</TD>'
        '<TD style="BACKGROUND-COLOR: #cce6ff">◎</TD>'
        '<TD style="BACKGROUND-COLOR: #cce6ff">ディズニー</TD></TR>'
        '<TR><TD colSpan="3">説明</TD></TR></TABLE>'
    )

    assert "◎" not in rendered
    assert "1901年" in rendered
    assert "ディズニー" in rendered
    assert "colSpan=2" in rendered
    assert 'colSpan="2"' in rendered
    assert rendered.lower().count("<td") == 3


def test_parse_britannica_top_dat_resolves_image_siblings(tmp_path: Path) -> None:
    top = tmp_path / "top"
    mini = tmp_path / "mini"
    full = tmp_path / "full"
    top.mkdir()
    mini.mkdir()
    full.mkdir()
    (mini / "sample.jpg").write_bytes(b"mini")
    (full / "sample.jpg").write_bytes(b"full")
    path = top / "top_art.dat"
    path.write_bytes(
        "001\r\nTitle\r\nDescription\r\n0000002a:0010\r\nsample.jpg\r\n\r\n".encode("cp932")
    )

    decoded = parse_britannica_top_dat(path)

    assert decoded.category == "art"
    assert len(decoded.records) == 1
    assert decoded.records[0].address.block == 0x2A
    assert [item.name for item in decoded.records[0].image_paths] == ["sample.jpg", "sample.jpg"]


def test_parse_britannica_split_top_dat_resolves_split_media_siblings(tmp_path: Path) -> None:
    top = tmp_path / "_DCT_BRI2017_Media_top"
    thumb = tmp_path / "_DCT_BRI2017_Media_thumb"
    mini = tmp_path / "_DCT_BRI2017_Media_mini"
    full = tmp_path / "_DCT_BRI2017_Media_full"
    top.mkdir()
    thumb.mkdir()
    mini.mkdir()
    full.mkdir()
    (thumb / "sample.jpg").write_bytes(b"thumb")
    (mini / "sample.jpg").write_bytes(b"mini")
    (full / "sample.jpg").write_bytes(b"full")
    path = top / "top_art.dat"
    path.write_bytes(
        "001\r\nTitle\r\nDescription\r\n0000002a:0010\r\nsample.jpg\r\n\r\n".encode("cp932")
    )

    assert is_britannica_top_dat_path(path)
    decoded = parse_britannica_top_dat(path)

    assert decoded.category == "art"
    assert [item.parent.name for item in decoded.records[0].image_paths] == [
        "_DCT_BRI2017_Media_thumb",
        "_DCT_BRI2017_Media_mini",
        "_DCT_BRI2017_Media_full",
    ]


def test_parse_britannica_chronology_db_expands_inline_links(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_BRI2019P"
    package.mkdir()
    db_path = package / "Bri2019.db"
    con = sqlite3.connect(db_path)
    con.execute(
        """
        create table D_InternationalChronology (
          INC_Code text,
          INC_Type_Code text,
          INC_Type_Name text,
          Year_Type text,
          Leap_Type text,
          Year integer,
          Month integer,
          Day integer,
          Sub_Disp_Order integer,
          Jpn_Year text,
          Year_Sub_Code_1 text,
          Year_Sub_Name_1 text,
          Year_Sub_Code_2 text,
          Year_Sub_Name_2 text,
          Year_Sub_Code_3 text,
          Year_Sub_Name_3 text,
          Value text
        )
        """
    )
    con.execute(
        "insert into D_InternationalChronology values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "1",
            "WOR",
            "世界史",
            "0",
            "0",
            1901,
            12,
            5,
            10,
            "明治34",
            "A",
            "区分",
            "",
            "",
            "",
            "",
            "＃＃Ｓ0001bea5:0724モーツァルトＥ＃＃死去",
        ),
    )
    con.commit()
    con.close()

    assert is_britannica_chronology_db_candidate(db_path)
    decoded = parse_britannica_chronology_db(db_path)

    assert decoded.records[0].type_code == "WOR"
    assert decoded.records[0].year == 1901
    assert 'href="lvaddr://0001bea5/0724"' in decoded.records[0].html
    assert "＃＃Ｓ" not in decoded.records[0].html
    assert decoded.records[0].references[0].address is not None
    assert decoded.records[0].year_subs == (("A", "区分"),)


def test_parse_lved_address_rejects_non_address() -> None:
    assert parse_lved_address("plain.html") is None
    assert parse_lved_address("lved.addr0000002a:0010").block == 0x2A


def test_classify_extensionless_logofont_sqlite_resource(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "body.sqlite"
    con = sqlite3.connect(sqlite_path)
    con.execute("create table main (ID integer primary key, body text)")
    con.commit()
    con.close()
    encrypted = tmp_path / "DICT"
    encrypted.write_bytes(encrypt_logofont_cipher_bytes(sqlite_path.read_bytes()))

    decoded = classify_loose_decoded_resource(encrypted)

    assert decoded is not None
    assert decoded.storage == "logofont_cipher"
    assert decoded.content_kind == "sqlite"
    assert decoded.output_extension == ".sqlite"


def test_classify_extensionless_logofont_wave_resource(tmp_path: Path) -> None:
    encrypted = tmp_path / "2-01-1"
    wave = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 24
    encrypted.write_bytes(encrypt_logofont_cipher_bytes(wave))

    decoded = classify_loose_decoded_resource(encrypted)

    assert decoded is not None
    assert decoded.storage == "logofont_cipher"
    assert decoded.content_kind == "wave"
    assert decoded.output_extension == ".wav"


def test_classify_extensionless_macos_logofont_pdf_resource(tmp_path: Path) -> None:
    encrypted = tmp_path / "minji"
    encrypted.write_bytes(encrypt_macos_logofont_cipher_bytes(b"%PDF-1.5\nbody"))

    decoded = classify_loose_decoded_resource(encrypted)

    assert decoded is not None
    assert decoded.storage == "macos_logofont_cipher"
    assert decoded.content_kind == "pdf"
    assert decoded.output_extension == ".pdf"


def test_iter_candidate_files_finds_targeted_families(tmp_path: Path) -> None:
    (tmp_path / "whatday").mkdir()
    (tmp_path / "whatday" / "1-1.top").write_text("<BODY></BODY>", encoding="utf-8")
    (tmp_path / "top").mkdir()
    (tmp_path / "top" / "top_art.dat").write_text("", encoding="utf-8")
    (tmp_path / "_DCT_BRI2017_Media_top").mkdir()
    (tmp_path / "_DCT_BRI2017_Media_top" / "top_nature.dat").write_text("", encoding="utf-8")
    (tmp_path / "_DCT_BRI2019P").mkdir()
    (tmp_path / "_DCT_BRI2019P" / "Bri2019.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "Resources").mkdir()
    (tmp_path / "Resources" / "minji").write_bytes(b"x" * 16)
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")

    names = sorted(path.name for path in iter_candidate_files([tmp_path]))

    assert names == ["1-1.top", "Bri2019.db", "minji", "top_art.dat", "top_nature.dat"]
