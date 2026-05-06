import plistlib

from logovista_tools.entries import (
    decode_tokens,
    normalize_fullwidth_ascii,
    resolve_section_image_sources,
    tokens_to_html,
    tokens_to_text,
)
from logovista_tools.gaiji import load_gaiji_profile, load_uni_gaiji_map, parse_ga16_resource, parse_uni_resource
from logovista_tools.indexes import (
    IndexPointer,
    parse_cr_leaf_page,
    parse_internal_page,
    parse_simple_leaf_page,
    parse_tagged_leaf_page,
)
from logovista_tools.resources import load_image_resource_profile


def test_normalize_fullwidth_ascii() -> None:
    assert normalize_fullwidth_ascii("ＡＢＣ１２３　ｘｙｚ") == "ABC123 xyz"


def test_decode_jis_pair_and_line_break() -> None:
    # 2422 is JIS X 0208 "あ"; 1f0a is a LogoVista/EPWING line break.
    tokens, stats = decode_tokens(bytes.fromhex("24221f0a2424"))
    assert tokens_to_text(tokens) == "あ\nい"
    assert stats["jis_pairs"] == 2
    assert stats["controls"] == 1


def test_gaiji_placeholder_modes() -> None:
    tokens, stats = decode_tokens(bytes.fromhex("a126"), gaiji="h-placeholder")
    assert tokens_to_text(tokens) == "<hA126>"
    assert stats["gaiji"] == 1

    tokens, _ = decode_tokens(bytes.fromhex("b126"), gaiji="h-placeholder")
    assert tokens_to_text(tokens) == ""

    tokens, _ = decode_tokens(bytes.fromhex("b126"), gaiji="placeholder")
    assert tokens_to_text(tokens) == "<zB126>"


def test_gaiji_map_wins_over_placeholder() -> None:
    tokens, _ = decode_tokens(bytes.fromhex("a126"), gaiji="h-placeholder", gaiji_map={"a126": "é"})
    assert tokens_to_text(tokens) == "é"


def test_image_gaiji_placeholder_for_png_backed_codes() -> None:
    tokens, stats = decode_tokens(
        bytes.fromhex("b13d"),
        gaiji="drop",
        image_gaiji_keys=frozenset({"b13d"}),
        preserve_image_gaiji=True,
    )

    assert tokens_to_text(tokens) == "<img:b13d>"
    assert stats["image_gaiji"] == 1


def test_tokens_to_html_renders_image_sources() -> None:
    tokens, _ = decode_tokens(
        bytes.fromhex("b13d1f0a2422"),
        image_gaiji_keys=frozenset({"b13d"}),
        preserve_image_gaiji=True,
    )

    assert tokens_to_html(tokens, image_sources={"b13d": "img/b13d_n.png"}) == (
        '<img src="img/b13d_n.png" alt="b13d" class="lv-gaiji lv-gaiji-b13d"><br>あ'
    )


def test_tokens_to_html_can_insert_section_images() -> None:
    tokens, _ = decode_tokens(bytes.fromhex("1f0900112422"), preserve_sections=True)

    assert tokens_to_html(tokens, section_image_sources={"0011": "img/exam.png"}) == (
        '<img src="img/exam.png" alt="0011" class="lv-section-image lv-section-image-0011">'
        '<span class="lv-section" data-lv-section="0011"></span>あ'
    )


def test_resolve_section_image_sources_uses_discovered_image_key() -> None:
    assert resolve_section_image_sources(["0011=exam"], {"exam": "img/exam.png"}) == {"0011": "img/exam.png"}


def test_media_control_uses_18_byte_payload() -> None:
    # 1f4d media starts carry 18 bytes of payload before visible text resumes.
    payload = bytes.fromhex("000000000000000000000000000186961670")
    tokens, stats = decode_tokens(b"\x1f\x4d" + payload + bytes.fromhex("2422"), preserve_media=True)

    assert tokens_to_text(tokens) == f"<media:{payload.hex()}>あ"
    assert stats["media"] == 1


def test_link_start_uses_16_byte_payload_then_visible_text() -> None:
    payload = bytes.fromhex("00010000000231930000000231991579")
    tokens, stats = decode_tokens(b"\x1f\x4a" + payload + bytes.fromhex("2422") + b"\x1f\x6a" + bytes.fromhex("2424"))

    assert tokens_to_text(tokens) == "あい"
    assert stats["links"] == 1


def uni_record(
    code: int,
    primary: tuple[int, int],
    fallback: tuple[int, int] = (0, 0),
    legacy: tuple[int, int] = (0, 0),
) -> bytes:
    values = [code, 0, primary[0], primary[1], fallback[0], fallback[1], legacy[0], legacy[1]]
    return b"".join(value.to_bytes(2, "big") for value in values)


def simple_uni_record(code: int, primary: tuple[int, int], fallback: tuple[int, int] = (0, 0)) -> bytes:
    values = [code, 0, primary[0], primary[1], fallback[0], fallback[1]]
    return b"".join(value.to_bytes(2, "big") for value in values)


def test_load_uni_gaiji_map_primary_sequences(tmp_path) -> None:
    path = tmp_path / "TEST.uni"
    path.write_bytes(
        b"Ver2  "
        + (2).to_bytes(4, "big")
        + uni_record(0xA126, (0, 0x00E9))
        + uni_record(0xA12A, (0x0075, 0x032F))
        + (1).to_bytes(4, "big")
        + uni_record(0xB121, (0, 0x4E00))
    )

    mapping, records_seen = load_uni_gaiji_map(path)

    assert records_seen == 3
    assert mapping["a126"] == "é"
    assert mapping["a12a"] == "u̯"
    assert mapping["b121"] == "一"


def test_parse_uni_resource_keeps_fallback_and_legacy_fields(tmp_path) -> None:
    path = tmp_path / "TEST.uni"
    path.write_bytes(
        b"Ver2  "
        + (1).to_bytes(4, "big")
        + uni_record(0xA121, (0, 0x00E1), fallback=(0, 0x0061), legacy=(0, 0x00C2))
        + (0).to_bytes(4, "big")
    )

    resource = parse_uni_resource(path)

    assert resource is not None
    assert resource.format == "ver2"
    assert resource.records[0].display == "á"
    assert resource.records[0].fallback == "a"
    assert resource.records[0].legacy == "Â"


def test_load_uni_gaiji_map_combines_surrogate_pairs(tmp_path) -> None:
    path = tmp_path / "TEST.uni"
    path.write_bytes(
        b"Ver2  "
        + (1).to_bytes(4, "big")
        + uni_record(0xA44F, (0xD834, 0xDD10))
        + (0).to_bytes(4, "big")
    )

    mapping, records_seen = load_uni_gaiji_map(path)

    assert records_seen == 1
    assert mapping["a44f"] == "\U0001d110"


def test_load_simple12_uni_gaiji_map(tmp_path) -> None:
    path = tmp_path / "KENROWA.uni"
    path.write_bytes(
        (1).to_bytes(4, "big")
        + simple_uni_record(0xA128, (0x025B, 0x0303))
        + (1).to_bytes(4, "big")
        + simple_uni_record(0xB121, (0, 0x0401))
    )

    resource = parse_uni_resource(path)
    mapping, records_seen = load_uni_gaiji_map(path)

    assert resource is not None
    assert resource.format == "simple12"
    assert resource.half_count == 1
    assert resource.full_count == 1
    assert records_seen == 2
    assert mapping["a128"] == "ɛ̃"
    assert mapping["b121"] == "Ё"


def test_load_uni_gaiji_map_later_sections_override_duplicate_codes(tmp_path) -> None:
    path = tmp_path / "TEST.uni"
    path.write_bytes(
        b"Ver2  "
        + (1).to_bytes(4, "big")
        + uni_record(0xA121, (0, 0x00E1))
        + (1).to_bytes(4, "big")
        + uni_record(0xA121, (0, 0x00E0))
    )

    mapping, records_seen = load_uni_gaiji_map(path)

    assert records_seen == 2
    assert mapping["a121"] == "à"


def test_load_gaiji_profile_prefers_uni_over_plist(tmp_path) -> None:
    dict_dir = tmp_path / "DICT"
    dict_dir.mkdir()
    idx = dict_dir / "DICT.IDX"
    idx.write_bytes(b"")
    (dict_dir / "DICT.uni").write_bytes(
        b"Ver2  " + (1).to_bytes(4, "big") + uni_record(0xA126, (0, 0x00E9)) + (0).to_bytes(4, "big")
    )
    with (dict_dir / "Gaiji.plist").open("wb") as out:
        plistlib.dump({"A126": "e", "A127": "x"}, out)

    profile = load_gaiji_profile(idx)

    assert profile.map["a126"] == "é"
    assert profile.map["a127"] == "x"
    assert profile.uni_entries == 1
    assert profile.plist_entries == 2


def test_parse_ga16_resource_header_and_glyph(tmp_path) -> None:
    path = tmp_path / "GA16HALF"
    header = bytearray(2048)
    header[0] = 1
    header[8] = 8
    header[9] = 16
    header[10:12] = bytes.fromhex("a121")
    header[12:14] = (2).to_bytes(2, "big")
    glyph0 = bytes(range(16))
    glyph1 = bytes(range(16, 32))
    path.write_bytes(bytes(header) + glyph0 + glyph1)

    resource = parse_ga16_resource(path)

    assert resource is not None
    assert resource.width == 8
    assert resource.height == 16
    assert resource.start_code == 0xA121
    assert resource.count == 2
    assert resource.glyph_bytes == 16
    assert resource.glyph_for_code(path.read_bytes(), 0xA122) == glyph1


def test_load_image_resource_profile_discovers_theme_variants(tmp_path) -> None:
    package = tmp_path / "PKG"
    dict_dir = package / "DICT"
    image_dir = package / "img"
    dict_dir.mkdir(parents=True)
    image_dir.mkdir()
    idx = dict_dir / "DICT.IDX"
    idx.write_bytes(b"")
    (image_dir / "b13d_n.png").write_bytes(b"")
    (image_dir / "b13d_w.png").write_bytes(b"")
    (image_dir / "exam.png").write_bytes(b"")
    with (package / "resourcesCopy.plist").open("wb") as out:
        plistlib.dump(["b13d_n.png", "b13d_w.png", "exam.png"], out)
    with (package / "gaijiicon.plist").open("wb") as out:
        plistlib.dump(["b13d"], out)

    profile = load_image_resource_profile(idx)

    assert profile.image_dirs == (image_dir,)
    assert "b13d" in profile.gaiji_image_keys
    assert profile.resources["b13d"].normal == image_dir / "b13d_n.png"
    assert profile.resources["b13d"].white == image_dir / "b13d_w.png"
    assert profile.resources["b13d"].listed_in_gaijiicon
    assert profile.resources["exam"].default == image_dir / "exam.png"


def test_parse_internal_index_page_uses_32bit_child() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("601e")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:6] = bytes.fromhex("2422")
    page[34:38] = (0x000112BD).to_bytes(4, "big")

    rows = list(parse_internal_page("FKINDEX.DIC", bytes(page), 1, 100, gaiji_map={}, gaiji="drop"))

    assert len(rows) == 1
    assert rows[0].key == "あ"
    assert rows[0].child_block == 0x000112BD


def test_parse_simple_leaf_index_page() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("c000")
    page[2:4] = (1).to_bytes(2, "big")
    page[4] = 2
    page[5:7] = bytes.fromhex("2422")
    page[7:19] = bytes.fromhex("000000010002000000030004")

    rows, unknown = parse_simple_leaf_page("FHINDEX.DIC", bytes(page), 1, 100, gaiji_map={}, gaiji="drop")

    assert unknown == 0
    assert rows[0].key == "あ"
    assert rows[0].body == IndexPointer(1, 2)
    assert rows[0].title == IndexPointer(3, 4)


def test_parse_tagged_leaf_index_page() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (2).to_bytes(2, "big")
    page[4:10] = bytes.fromhex("800200012422")
    page[10:26] = bytes.fromhex("c0022424000000010002000000030004")

    rows, current_key, hint, groups, unknown = parse_tagged_leaf_page(
        "FKINDEX.DIC",
        bytes(page),
        1,
        100,
        current_key=None,
        current_count_hint=None,
        gaiji_map={},
        gaiji="drop",
    )

    assert unknown == 0
    assert groups == 1
    assert hint == 1
    assert current_key == "あ"
    assert rows[0].key == "あ"
    assert rows[0].target_key == "い"
    assert rows[0].body == IndexPointer(1, 2)
    assert rows[0].title == IndexPointer(3, 4)


def test_parse_cr_leaf_primary_row() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:8] = bytes.fromhex("00022422")
    page[8:20] = bytes.fromhex("000000010002000000030004")

    rows, current_key, current_title, hint, groups, unknown = parse_cr_leaf_page(
        "CRINDEX.DIC",
        bytes(page),
        1,
        100,
        current_key=None,
        current_title=None,
        current_count_hint=None,
        gaiji_map={},
        gaiji="drop",
    )

    assert unknown == 0
    assert groups == 0
    assert hint is None
    assert current_key is None
    assert current_title is None
    assert rows[0].key == "あ"
    assert rows[0].body == IndexPointer(1, 2)
    assert rows[0].title == IndexPointer(3, 4)
