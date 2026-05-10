import pytest

from logovista_tools.entries import decode_tokens, iter_entry_slices_with_boundaries, tokens_to_html, tokens_to_text
from logovista_tools.gaiji import parse_ga16_resource, parse_uni_resource
from logovista_tools.indexes import IndexPointer, scan_index_component
from logovista_tools.spans import decode_lossless_spans
from logovista_tools.ssed import BLOCK_SIZE, expand_sseddata_bytes, expand_sseddata_file, parse_sseddata_header, parse_ssedinfo
from logovista_tools.writer import (
    FULL_GAIJI_START,
    HALF_GAIJI_START,
    WriterEntry,
    IndexTarget,
    build_plain_honmon_package,
    encode_body_text,
    encode_ga16_resource,
    encode_jis_cell,
    encode_search_key,
    encode_simple_index_pages,
    encode_sseddata,
    encode_sseddata_literal,
    encode_ssedinfo,
    encode_tagged_index_pages,
    encode_title_stream,
    encode_uni_record,
    encode_uni_resource,
    rows_to_ga16_glyph,
    SsedInfoComponent,
    GaijiAllocator,
    write_plain_package,
)


def test_sseddata_literal_encoder_roundtrips_multiple_chunks() -> None:
    payload = bytes((i * 17) % 256 for i in range(40000))
    encoded = encode_sseddata_literal(payload, start_block=12, kind=3)
    expanded = expand_sseddata_bytes(encoded)

    assert expanded.startswith(payload)
    assert len(expanded) % BLOCK_SIZE == 0
    assert expanded[len(payload) :] == bytes(len(expanded) - len(payload))


def test_sseddata_compressed_encoder_roundtrips_and_reduces_repetitive_data() -> None:
    payload = (b"abc abc abc abc " * 3000) + bytes(range(256)) * 8
    encoded = encode_sseddata(payload, start_block=12, kind=3)
    literal = encode_sseddata_literal(payload, start_block=12, kind=3)
    expanded = expand_sseddata_bytes(encoded)

    assert expanded.startswith(payload)
    assert expanded[len(payload) :] == bytes(len(expanded) - len(payload))
    assert len(encoded) < len(literal) // 3


def test_ssedinfo_encoder_roundtrips_component_table(tmp_path) -> None:
    path = tmp_path / "TEST.IDX"
    path.write_bytes(
        encode_ssedinfo(
            "テスト辞書",
            [
                SsedInfoComponent("HONMON.DIC", 0x00, 2, 4, b"\x00\x00\x00\x02"),
                SsedInfoComponent("FHINDEX.DIC", 0x91, 5, 5, b"\x00\x00\x00\x05"),
            ],
        )
    )

    title, elements = parse_ssedinfo(path)

    assert title == "テスト辞書"
    assert [(e.filename, e.type, e.start, e.end) for e in elements] == [
        ("HONMON.DIC", 0x00, 2, 4),
        ("FHINDEX.DIC", 0x91, 5, 5),
    ]


def test_body_and_title_text_encoder_roundtrips_through_reader_decoder() -> None:
    gaiji = GaijiAllocator()
    body = encode_body_text("abc あ 𰻞", gaiji)
    tokens, stats = decode_tokens(body, gaiji_map=gaiji.mapping())

    assert body.startswith(b"\x1f\x04")
    assert b"\x1f\x05" in body
    assert tokens_to_text(tokens) == "abc あ 𰻞"
    assert stats["gaiji"] == 1

    title_stream, offsets = encode_title_stream(["abc", "𰻞"], gaiji)
    tokens, _stats = decode_tokens(title_stream, gaiji_map=gaiji.mapping())

    assert offsets == [0, len(encode_body_text("abc", gaiji)) + 2]
    assert tokens_to_text(tokens).splitlines() == ["abc", "𰻞"]


def test_halfwidth_controls_scope_fullwidth_ascii_normalization() -> None:
    fullwidth_a = encode_jis_cell("Ａ")
    assert fullwidth_a is not None
    raw = fullwidth_a + b"\x1f\x04" + fullwidth_a + b"\x1f\x05" + fullwidth_a

    tokens, _stats = decode_tokens(raw)
    assert tokens_to_text(tokens) == "ＡAＡ"
    assert tokens_to_html(tokens) == 'Ａ<span class="lv-halfwidth">A</span>Ａ'

    spans = decode_lossless_spans(raw).spans
    text_spans = [span for span in spans if span.kind == "text"]
    assert [(span.text, span.normalized) for span in text_spans] == [
        ("Ａ", "Ａ"),
        ("Ａ", "A"),
        ("Ａ", "Ａ"),
    ]


def test_private_renderer_directive_span_is_hidden_from_rendered_output() -> None:
    raw = b"\x1f\xe2\x00\x07" + encode_body_text("SQL:") + b"\x1f\xe3" + encode_body_text("visible")

    tokens, stats = decode_tokens(raw)
    assert tokens_to_text(tokens) == "visible"
    assert tokens_to_html(tokens) == '<span class="lv-halfwidth">visible</span>'
    assert stats["unknown_controls"] == 0

    spans = decode_lossless_spans(raw).spans
    hidden_text = [span for span in spans if span.kind == "text" and span.hidden]
    visible_text = [span for span in spans if span.kind == "text" and not span.hidden]
    assert "".join(span.normalized or "" for span in hidden_text) == "SQL:"
    assert "".join(span.normalized or "" for span in visible_text) == "visible"


def test_search_key_encoder_uses_jis_cells_and_reverses_by_character() -> None:
    assert len(encode_search_key("AC入試")) == 8
    assert encode_search_key("AC入試", reverse=True).endswith(encode_search_key("A"))


def test_uni_and_ga16_resource_encoders_roundtrip(tmp_path) -> None:
    def renderer(text: str, width: int, height: int, space: str) -> bytes:
        rows = ["#" * width if y in {0, height - 1} else "#" + "." * (width - 2) + "#" for y in range(height)]
        return rows_to_ga16_glyph(rows, width=width, height=height)

    gaiji = GaijiAllocator(glyph_renderer=renderer)
    half = gaiji.allocate("ə", prefer_half=True)
    full = gaiji.allocate("𰻞", prefer_half=False)

    uni_path = tmp_path / "TEST.uni"
    uni_path.write_bytes(encode_uni_resource(gaiji))
    uni = parse_uni_resource(uni_path)

    assert uni is not None
    assert [(r.code, r.display) for r in uni.records] == [(f"{half.code:04x}", "ə"), (f"{full.code:04x}", "𰻞")]

    half_path = tmp_path / "GA16HALF"
    full_path = tmp_path / "GA16FULL"
    half_path.write_bytes(encode_ga16_resource([half], width=8, start_code=HALF_GAIJI_START))
    full_path.write_bytes(encode_ga16_resource([full], width=16, start_code=FULL_GAIJI_START))

    half_resource = parse_ga16_resource(half_path)
    full_resource = parse_ga16_resource(full_path)

    assert half_resource is not None
    assert (half_resource.width, half_resource.height, half_resource.count) == (8, 16, 1)
    assert full_resource is not None
    assert (full_resource.width, full_resource.height, full_resource.count) == (16, 16, 1)


def test_uni_writer_rejects_mappings_that_do_not_fit_record_fields() -> None:
    with pytest.raises(ValueError, match="two UTF-16 code units"):
        encode_uni_record(0xB121, "ab𰻞")


def test_gaiji_allocator_rejects_codes_outside_gaiji_rows() -> None:
    gaiji = GaijiAllocator(full_start=0xFE7E)
    assert gaiji.allocate("𰻞", prefer_half=False).code == 0xFE7E
    with pytest.raises(ValueError, match="code space exhausted"):
        gaiji.allocate("𰻟", prefer_half=False)


def test_simple_index_writer_splits_pages_and_parses_as_branch_plus_leaves() -> None:
    targets = [
        IndexTarget(
            key=f"k{i:03d}",
            body=IndexPointer(block=10 + i, offset=i % 2048),
            title=IndexPointer(block=500, offset=i * 4),
        )
        for i in range(300)
    ]
    rows = []
    pages = encode_simple_index_pages(targets, start_block=100, gaiji=None)
    result = scan_index_component(
        "FHINDEX.DIC",
        0x91,
        pages,
        100,
        gaiji="placeholder",
        gaiji_map={},
        emit_internal=True,
        emit_row=rows.append,
    )

    assert result.internal_pages == 1
    assert result.leaf_pages > 1
    assert result.leaf_rows == 300
    assert result.unknown_leaf_bytes == 0
    leaf_keys = [row["key"] for row in rows if row["kind"] == "leaf"]
    assert leaf_keys == sorted(leaf_keys)


def test_simple_index_writer_generates_multi_level_branches() -> None:
    targets = [
        IndexTarget(
            key=f"k{i:04d}-" + ("x" * 96),
            body=IndexPointer(block=10 + i, offset=i % 2048),
            title=IndexPointer(block=500, offset=i * 2),
        )
        for i in range(900)
    ]
    rows = []
    pages = encode_simple_index_pages(targets, start_block=900, gaiji=None)
    result = scan_index_component(
        "FHINDEX.DIC",
        0x91,
        pages,
        900,
        gaiji="placeholder",
        gaiji_map={},
        emit_internal=True,
        emit_row=rows.append,
    )

    assert result.internal_pages > 1
    assert result.leaf_pages > result.internal_pages
    assert result.leaf_rows == 900
    assert result.unknown_leaf_bytes == 0


def test_tagged_index_writer_parses_grouped_targets() -> None:
    targets = [
        IndexTarget(key="run", target_key="run", body=IndexPointer(10, 2), title=IndexPointer(20, 4)),
        IndexTarget(key="run", target_key="running", body=IndexPointer(11, 2), title=IndexPointer(20, 8)),
        IndexTarget(key="walk", target_key="walk", body=IndexPointer(12, 2), title=IndexPointer(20, 12)),
    ]
    rows = []
    pages = encode_tagged_index_pages(targets, start_block=100, gaiji=None)
    result = scan_index_component(
        "FKINDEX.DIC",
        0x90,
        pages,
        100,
        gaiji="placeholder",
        gaiji_map={},
        emit_row=rows.append,
    )

    assert result.search_groups == 2
    assert result.leaf_rows == 3
    assert [row["key"] for row in rows if row["kind"] == "leaf"] == ["run", "run", "walk"]
    assert [row["target_key"] for row in rows if row["kind"] == "leaf"] == ["run", "running", "walk"]

    many = [
        IndexTarget(key=f"group-{i:03d}", target_key=f"group-{i:03d}", body=IndexPointer(100 + i, 2), title=IndexPointer(200, i * 2))
        for i in range(180)
    ]
    rows = []
    pages = encode_tagged_index_pages(many, start_block=300, gaiji=None)
    result = scan_index_component(
        "FKINDEX.DIC",
        0x90,
        pages,
        300,
        gaiji="placeholder",
        gaiji_map={},
        emit_internal=True,
        emit_row=rows.append,
    )
    assert result.internal_pages == 1
    assert result.leaf_pages > 1
    assert result.search_groups == 180
    assert result.leaf_rows == 180
    assert {row["key"] for row in rows if row["kind"] == "leaf"} == {f"group-{i:03d}" for i in range(180)}


def test_index_writer_rejects_single_key_groups_that_cannot_fit_one_leaf() -> None:
    duplicate_simple = [
        IndexTarget(
            key="duplicate",
            body=IndexPointer(block=10 + i, offset=2),
            title=IndexPointer(block=20, offset=i * 2),
        )
        for i in range(180)
    ]
    with pytest.raises(ValueError, match="single index key group"):
        encode_simple_index_pages(duplicate_simple, start_block=100)

    duplicate_tagged = [
        IndexTarget(
            key="duplicate",
            target_key=f"duplicate-{i:03d}",
            body=IndexPointer(block=10 + i, offset=2),
            title=IndexPointer(block=20, offset=i * 2),
        )
        for i in range(180)
    ]
    with pytest.raises(ValueError, match="single index key group"):
        encode_tagged_index_pages(duplicate_tagged, start_block=100)


def test_plain_honmon_package_writer_is_readable_by_existing_parsers(tmp_path) -> None:
    package = build_plain_honmon_package(
        dict_id="TESTDICT",
        title="Synthetic Test Dictionary",
        entries=[
            WriterEntry("alpha", "first entry"),
            WriterEntry("裏", "kanji already representable"),
            WriterEntry("biang", "rare fallback 𰻞", search_keys=("biang", "𰻞")),
        ],
        glyph_renderer=lambda _text, width, height, _space: rows_to_ga16_glyph(
            ["#" * width if y in {0, height - 1} else "#" + "." * (width - 2) + "#" for y in range(height)],
            width=width,
            height=height,
        ),
    )
    write_plain_package(package, tmp_path)

    title, elements = parse_ssedinfo(tmp_path / "TESTDICT.IDX")
    assert title == "Synthetic Test Dictionary"
    assert {element.filename for element in elements} == {
        "HONMON.DIC",
        "FKTITLE.DIC",
        "FHTITLE.DIC",
        "BKTITLE.DIC",
        "BHTITLE.DIC",
        "FKINDEX.DIC",
        "FHINDEX.DIC",
        "BKINDEX.DIC",
        "BHINDEX.DIC",
        "GA16FULL",
        "GA16HALF",
    }
    ga16full = next(element for element in elements if element.filename == "GA16FULL")
    ga16half = next(element for element in elements if element.filename == "GA16HALF")
    component_data = {element.filename: element.data.hex() for element in elements}
    assert component_data == {
        "HONMON.DIC": "02000000",
        "FKTITLE.DIC": "01000000",
        "FHTITLE.DIC": "01000000",
        "BKTITLE.DIC": "01000000",
        "BHTITLE.DIC": "01000000",
        "FKINDEX.DIC": "02055540",
        "FHINDEX.DIC": "02015540",
        "BKINDEX.DIC": "02055540",
        "BHINDEX.DIC": "02015540",
        "GA16FULL": "00000000",
        "GA16HALF": "00000000",
    }
    assert (ga16full.type, ga16full.start, ga16full.end) == (0xF1, 0, 0)
    assert (ga16half.type, ga16half.start, ga16half.end) == (0xF2, 0, 0)
    assert parse_sseddata_header(tmp_path / "HONMON.DIC")["kind"] == 0
    assert parse_sseddata_header(tmp_path / "FKTITLE.DIC")["kind"] == 4
    assert parse_sseddata_header(tmp_path / "FHTITLE.DIC")["kind"] == 5
    assert parse_sseddata_header(tmp_path / "BKTITLE.DIC")["kind"] == 6
    assert parse_sseddata_header(tmp_path / "BHTITLE.DIC")["kind"] == 7
    assert parse_sseddata_header(tmp_path / "FKINDEX.DIC")["kind"] == 0x90
    assert parse_sseddata_header(tmp_path / "FHINDEX.DIC")["kind"] == 0x91
    assert parse_sseddata_header(tmp_path / "BKINDEX.DIC")["kind"] == 0x70
    assert parse_sseddata_header(tmp_path / "BHINDEX.DIC")["kind"] == 0x71

    gaiji_map = package.gaiji_allocator.mapping()
    honmon = expand_sseddata_file(tmp_path / "HONMON.DIC")
    bodies = []
    for start, end in iter_entry_slices_with_boundaries(honmon, []):
        tokens, _stats = decode_tokens(honmon[start:end], gaiji_map=gaiji_map)
        text = tokens_to_text(tokens)
        if text:
            bodies.append(text)

    assert any("alpha" in body and "first entry" in body for body in bodies)
    assert any("biang" in body and "𰻞" in body for body in bodies)

    fh_rows = []
    fh = next(element for element in elements if element.filename == "FHINDEX.DIC")
    fh_result = scan_index_component(
        "FHINDEX.DIC",
        fh.type,
        expand_sseddata_file(tmp_path / "FHINDEX.DIC"),
        fh.start,
        gaiji="placeholder",
        gaiji_map=gaiji_map,
        emit_row=fh_rows.append,
    )
    assert fh_result.leaf_rows == 4
    assert {row["key"] for row in fh_rows if row["kind"] == "leaf"} == {"alpha", "裏", "biang", "𰻞"}

    fk_rows = []
    fk = next(element for element in elements if element.filename == "FKINDEX.DIC")
    fk_result = scan_index_component(
        "FKINDEX.DIC",
        fk.type,
        expand_sseddata_file(tmp_path / "FKINDEX.DIC"),
        fk.start,
        gaiji="placeholder",
        gaiji_map=gaiji_map,
        emit_row=fk_rows.append,
    )
    assert fk_result.search_groups == 4
    assert fk_result.leaf_rows == 4
    assert {row["key"] for row in fk_rows if row["kind"] == "leaf"} == {"alpha", "裏", "biang", "𰻞"}
    assert {row["target_key"] for row in fk_rows if row["kind"] == "leaf"} == {"alpha", "裏", "biang"}

    bk_rows = []
    bk = next(element for element in elements if element.filename == "BKINDEX.DIC")
    bk_result = scan_index_component(
        "BKINDEX.DIC",
        bk.type,
        expand_sseddata_file(tmp_path / "BKINDEX.DIC"),
        bk.start,
        gaiji="placeholder",
        gaiji_map=gaiji_map,
        emit_row=bk_rows.append,
    )
    assert bk_result.search_groups == 4
    assert bk_result.leaf_rows == 4
    assert {row["title"]["block"] for row in bk_rows if row["kind"] == "leaf"} == {
        next(element.start for element in elements if element.filename == "BKTITLE.DIC")
    }

    bh_rows = []
    bh = next(element for element in elements if element.filename == "BHINDEX.DIC")
    scan_index_component(
        "BHINDEX.DIC",
        bh.type,
        expand_sseddata_file(tmp_path / "BHINDEX.DIC"),
        bh.start,
        gaiji="placeholder",
        gaiji_map=gaiji_map,
        emit_row=bh_rows.append,
    )
    assert {row["title"]["block"] for row in bh_rows if row["kind"] == "leaf"} == {
        next(element.start for element in elements if element.filename == "BHTITLE.DIC")
    }

    uni = parse_uni_resource(tmp_path / "TESTDICT.uni")
    assert uni is not None
    assert any(row.display == "𰻞" for row in uni.records)
    assert parse_ga16_resource(tmp_path / "GA16FULL") is not None


def test_plain_honmon_package_writer_can_emit_simple_only_layout(tmp_path) -> None:
    package = build_plain_honmon_package(
        dict_id="SIMPLE",
        title="Simple Layout",
        entries=[WriterEntry("alpha", "body")],
        include_tagged_indexes=False,
    )
    write_plain_package(package, tmp_path)

    _title, elements = parse_ssedinfo(tmp_path / "SIMPLE.IDX")
    assert {element.filename for element in elements} == {
        "HONMON.DIC",
        "FHTITLE.DIC",
        "BHTITLE.DIC",
        "FHINDEX.DIC",
        "BHINDEX.DIC",
    }
    assert not (tmp_path / "FKINDEX.DIC").exists()
