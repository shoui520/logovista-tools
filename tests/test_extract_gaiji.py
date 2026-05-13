from pathlib import Path

from logovista_tools.entries import DictionarySource
from logovista_tools.extract import ExtractPlan, extract_gaiji
from logovista_tools.gaiji import is_bitmap_gaiji_resource_name


def _write_ga16(path: Path, *, width: int, height: int, start_code: int, glyphs: list[bytes]) -> None:
    header = bytearray(2048)
    header[8] = width
    header[9] = height
    header[10:12] = start_code.to_bytes(2, "big")
    header[12:14] = len(glyphs).to_bytes(2, "big")
    path.write_bytes(bytes(header) + b"".join(glyphs))


def test_bitmap_gaiji_resource_name_accepts_ga16_and_gai16_families() -> None:
    assert is_bitmap_gaiji_resource_name("GA16FULL")
    assert is_bitmap_gaiji_resource_name("GA16HALF")
    assert is_bitmap_gaiji_resource_name("GAI16F")
    assert is_bitmap_gaiji_resource_name("GAI16F00")
    assert is_bitmap_gaiji_resource_name("GAI16H")
    assert is_bitmap_gaiji_resource_name("GAI16H00")
    assert not is_bitmap_gaiji_resource_name("GA16FULL.BAK")
    assert not is_bitmap_gaiji_resource_name("NOTGAI16F00")


def test_extract_gaiji_exports_image_backed_assets_and_flags_uniform_ga16(tmp_path) -> None:
    package = tmp_path / "DICT"
    templates = package / "Templates"
    templates.mkdir(parents=True)
    idx = package / "DICT.IDX"
    idx.write_bytes(b"")
    honmon = package / "HONMON.DIC"
    honmon.write_bytes(b"")
    (templates / "a121.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    (templates / "b121.svg").write_text("<svg/>", encoding="utf-8")
    glyph = bytes.fromhex("007e4242424242424242424242427e00")
    _write_ga16(package / "GAI16H00", width=8, height=16, start_code=0xA121, glyphs=[glyph, glyph])

    source = DictionarySource(
        dict_id="DICT",
        idx=idx,
        title="Dictionary",
        honmon=honmon,
        honmon_start_block=0,
        gaiji_map={"a121": "x"},
    )
    plan = ExtractPlan(out_dir=tmp_path / "out", categories=("gaiji",), formats=("json",), proceed=True)

    summary = extract_gaiji(source, plan)

    gaiji_dir = tmp_path / "out" / "DICT" / "gaiji"
    assert (gaiji_dir / "image-backed" / "A121" / "a121.png").read_bytes().startswith(b"\x89PNG")
    assert (gaiji_dir / "image-backed" / "B121" / "b121.svg").read_text(encoding="utf-8") == "<svg/>"
    assert (gaiji_dir / "ga16-bmp" / "GAI16H00" / "A121_jis_grid.bmp").is_file()
    assert summary["image_files_written"] == 2
    assert summary["gaiji_codes_with_image_assets"] == 2
    assert summary["ga16_resources"][0]["uniform_glyphs"] is True
    assert summary["ga16_resources"][0]["placeholder_candidate"] is True
    assert summary["ga16_resources"][0]["unique_glyph_hashes"] == 1
