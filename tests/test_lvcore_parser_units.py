from __future__ import annotations

from pathlib import Path

from test_lvcore_experimental import be16, be32, ga16_file, index_key, uni_file

from lvcore.gaiji import parse_ga16, parse_uni
from lvcore.index import parse_simple_leaf, parse_tagged_leaf
from lvcore.ssed import BLOCK_SIZE, expand_chunk
from lvcore.text import decode_jis_pair


def _pointer_pair(body_block: int, body_offset: int, title_block: int, title_offset: int) -> bytes:
    return be32(body_block) + be16(body_offset) + be32(title_block) + be16(title_offset)


def test_decode_jis_pair_decodes_one_character() -> None:
    assert decode_jis_pair("亜".encode("iso2022_jp").removeprefix(b"\x1b$B").removesuffix(b"\x1b(B")) == "亜"


def test_parse_simple_leaf_three_rows() -> None:
    rows = bytearray()
    for index, key in enumerate(("alpha", "beta", "gamma"), start=1):
        raw_key = index_key(key)
        rows.extend(bytes([len(raw_key)]) + raw_key + _pointer_pair(index + 10, index, 30, index))
    page = (be16(0x8000) + be16(3) + bytes(rows)).ljust(BLOCK_SIZE, b"\x00")

    parsed = parse_simple_leaf(page, 7, None)

    assert [row.key for row in parsed.rows] == ["alpha", "beta", "gamma"]
    assert [row.body.block for row in parsed.rows] == [11, 12, 13]
    assert parsed.row_type_counts == {"simple": 3}
    assert parsed.diagnostics == ()


def test_parse_tagged_leaf_group_and_target() -> None:
    group_key = index_key("group")
    target_key = index_key("target")
    body = _pointer_pair(42, 3, 50, 4)
    record = bytearray()
    record.extend(bytes([0x80, len(group_key)]) + be16(1) + group_key)
    record.extend(bytes([0xC0, len(target_key)]) + target_key + body)
    page = (be16(0x8000) + be16(2) + bytes(record)).ljust(BLOCK_SIZE, b"\x00")

    parsed = parse_tagged_leaf(page, 2, None, component_type=0x70)

    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert row.key == "group"
    assert row.target_key == "target"
    assert row.body.block == 42
    assert row.title.block == 50
    assert row.tagged is True
    assert parsed.row_type_counts == {"group": 1, "tag_80": 1, "target": 1, "tag_c0": 1}


def test_expand_chunk_literal_command_stream() -> None:
    chunk = b"\x00\x00" + be16(3) + b"\x00" + b"\x00\x00a" + b"\x00\x00b" + b"\x00\x00c"

    assert expand_chunk(chunk, 0) == b"abc"


def test_parse_uni_three_records(tmp_path: Path) -> None:
    path = tmp_path / "TEST.uni"
    path.write_bytes(uni_file([("a121", "亜", "亞"), ("a122", "唖", ""), ("a123", "", "仮")]))

    records = parse_uni(path)

    assert [record.code for record in records] == ["a121", "a122", "a123"]
    assert records[0].display == "亜"
    assert records[0].fallback == "亞"
    assert records[2].fallback == "仮"


def test_parse_ga16half_section(tmp_path: Path) -> None:
    path = tmp_path / "GA16HALF"
    path.write_bytes(ga16_file(width=8, height=16, start_code=0xA121, glyphs=[bytes([0x80] * 16)]))

    resource = parse_ga16(path)

    assert resource is not None
    assert resource.section == "half"
    assert resource.width == 8
    assert resource.height == 16
    assert resource.glyph_bytes == 16
    assert resource.glyph(0xA121) == bytes([0x80] * 16)
