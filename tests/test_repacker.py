from pathlib import Path

from logovista_tools.entries import DictionarySource
from logovista_tools.repacker import (
    repack_dictionary_source,
    repack_sseddata_bytes,
    repack_ssedinfo_bytes,
)
from logovista_tools.ssed import expand_sseddata_bytes, parse_ssedinfo
from logovista_tools.writer import SsedInfoComponent, encode_sseddata, encode_ssedinfo


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


def test_repack_dictionary_source_writes_only_catalog_components(tmp_path: Path) -> None:
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
    assert {row.path for row in report.files} == {"TEST.IDX", "HONMON.DIC", "FHINDEX.DIC"}
    assert all(row.byte_identical for row in report.files)
    assert (tmp_path / "reports" / "TEST" / "TEST.IDX").read_bytes() == idx.read_bytes()
    assert not (tmp_path / "reports" / "TEST" / "EXINFO.INI").exists()
    assert not (tmp_path / "reports" / "TEST" / "Templates").exists()
    assert "EXINFO.INI" in report.excluded_loose_files
    assert "Templates/" in report.excluded_loose_files
