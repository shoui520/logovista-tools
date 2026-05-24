import sqlite3
from pathlib import Path

import pytest

from logovista_tools.entries import DictionarySource
from logovista_tools.repacker import (
    repack_dictionary_source,
    repack_sseddata_bytes,
    repack_ssedinfo_bytes,
)
from logovista_tools.ssed import expand_sseddata_bytes, parse_ssedinfo
from logovista_tools.lvcrypto import logofont_cipher_key_iv
from logovista_tools.writer import SsedInfoComponent, encode_sseddata, encode_ssedinfo


def _encrypt_logofont_cipher_bytes(data: bytes) -> bytes:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def test_repack_sseddata_reemits_byte_identical_container() -> None:
    payload = (b"abc abc abc " * 2000) + bytes(range(64))
    encoded = encode_sseddata(payload, start_block=7, kind=0x91)

    repacked, details = repack_sseddata_bytes(encoded, validate_expand=True)

    assert repacked == encoded
    assert details["chunks"] >= 1
    assert details["kind"] == 0x91
    assert expand_sseddata_bytes(repacked) == expand_sseddata_bytes(encoded)
    assert details["chunk_models"][0]["command_count"] > 0


def test_repack_ssedinfo_reemits_catalog_records_from_parsed_fields(tmp_path: Path) -> None:
    idx = tmp_path / "TEST.IDX"
    idx.write_bytes(
        encode_ssedinfo(
            "テスト",
            [
                SsedInfoComponent("HONMON.DIC", 0x00, 2, 3, b"\x02\x00\x00\x00"),
                SsedInfoComponent("FHINDEX.DIC", 0x91, 4, 4, b"\x02\x01\x55\x40"),
            ],
        )
    )

    repacked, details = repack_ssedinfo_bytes(idx)

    assert repacked == idx.read_bytes()
    assert details["title"] == "テスト"
    assert details["component_count"] == 2
    title, elements = parse_ssedinfo(idx)
    assert title == "テスト"
    assert [element.filename for element in elements] == ["HONMON.DIC", "FHINDEX.DIC"]


def test_repack_dictionary_source_writes_catalog_and_dictionary_resource_files(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_TEST"
    package.mkdir()
    honmon = package / "HONMON.DIC"
    index = package / "FHINDEX.DIC"
    honmon.write_bytes(encode_sseddata(b"\x1f\x09\x00\x01body", start_block=2, kind=0))
    index.write_bytes(encode_sseddata(b"index", start_block=3, kind=0x91))
    idx = package / "TEST.IDX"
    idx.write_bytes(
        encode_ssedinfo(
            "Test",
            [
                SsedInfoComponent("HONMON.DIC", 0x00, 2, 2, b"\x02\x00\x00\x00"),
                SsedInfoComponent("FHINDEX.DIC", 0x91, 3, 3, b"\x02\x01\x55\x40"),
            ],
        )
    )
    (package / "EXINFO.INI").write_text("[x]\n", encoding="ascii")
    (package / "HANREI.chm").write_bytes(b"chm")
    (package / "Templates").mkdir()
    (package / "Templates" / "b121.png").write_bytes(b"png")
    (package / "TEST.uni").write_bytes((1).to_bytes(4, "big") + bytes.fromhex("a121000003a9000000000000"))
    ga16 = bytearray(2048)
    ga16[0] = 1
    ga16[8] = 8
    ga16[9] = 16
    ga16[10:12] = bytes.fromhex("a121")
    ga16[12:14] = (1).to_bytes(2, "big")
    (package / "GA16HALF").write_bytes(bytes(ga16) + bytes(16))
    (package / "CCALTSTR.HA").write_bytes(
        b"SDICALTH"
        + (1).to_bytes(2, "little")
        + bytes.fromhex("a121")
        + (1).to_bytes(2, "big")
        + b"\x00\x00"
        + bytes.fromhex("a121")
        + b"ALT".ljust(60, b"\x00")
    )
    panel_dir = package / "Panel"
    panel_dir.mkdir()
    panel_bin = (
        (1).to_bytes(4, "little")
        + (4).to_bytes(4, "little")
        + (0x1234).to_bytes(4, "little")
        + (0x0056).to_bytes(4, "little")
        + bytes.fromhex("24220000")
    )
    (panel_dir / "bunya.bin").write_bytes(panel_bin)
    con = sqlite3.connect(package / "helper.db")
    try:
        con.execute("create table helper(id integer primary key, value text)")
        con.execute("insert into helper(value) values ('x')")
        con.commit()
    finally:
        con.close()
    (package / "vlpljbl.bin").write_bytes(b"MZ")
    (package / "SPINDEX.DIC").write_bytes(encode_sseddata(bytes(2048), start_block=10, kind=0x91))
    (package / "00000041.idx").write_bytes("00000002\t00000010\t\t補助\n".encode("cp932"))
    source = DictionarySource(
        dict_id="TEST",
        idx=idx,
        title="Test",
        honmon=honmon,
        honmon_start_block=2,
        gaiji_map={},
    )

    report = repack_dictionary_source(source, tmp_path / "reports")

    assert report.ok
    assert {row.path for row in report.files} == {
        "TEST.IDX",
        "HONMON.DIC",
        "FHINDEX.DIC",
        "TEST.uni",
        "GA16HALF",
        "CCALTSTR.HA",
        "Panel/bunya.bin",
        "helper.db",
        "SPINDEX.DIC",
        "00000041.idx",
    }
    assert all(row.byte_identical for row in report.files)
    assert (tmp_path / "reports" / "TEST" / "TEST.IDX").read_bytes() == idx.read_bytes()
    assert (tmp_path / "reports" / "TEST" / "Panel" / "bunya.bin").read_bytes() == panel_bin
    assert not (tmp_path / "reports" / "TEST" / "vlpljbl.bin").exists()
    assert not (tmp_path / "reports" / "TEST" / "EXINFO.INI").exists()
    assert not (tmp_path / "reports" / "TEST" / "Templates").exists()
    assert "EXINFO.INI" in report.excluded_loose_files
    assert "Templates/" in report.excluded_loose_files


def test_repack_dictionary_source_preserves_encrypted_sseddata_component(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_ENCRYPTED"
    package.mkdir()
    plain = encode_sseddata(b"\x1f\x09\x00\x01body", start_block=2, kind=0)
    honmon = package / "HONMON.DIC"
    honmon.write_bytes(_encrypt_logofont_cipher_bytes(plain))
    idx = package / "TEST.IDX"
    idx.write_bytes(
        encode_ssedinfo(
            "Encrypted",
            [SsedInfoComponent("HONMON.DIC", 0x00, 2, 2, b"\x02\x00\x00\x00")],
        )
    )
    source = DictionarySource(
        dict_id="TEST",
        idx=idx,
        title="Encrypted",
        honmon=honmon,
        honmon_start_block=2,
        gaiji_map={},
    )

    report = repack_dictionary_source(source, tmp_path / "reports")

    assert report.ok
    honmon_row = next(row for row in report.files if row.path == "HONMON.DIC")
    assert honmon_row.storage == "logofont_cipher"
    assert honmon_row.byte_identical
    assert (tmp_path / "reports" / "TEST" / "HONMON.DIC").read_bytes() == honmon.read_bytes()
