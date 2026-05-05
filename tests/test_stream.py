import plistlib

from logovista_tools.entries import decode_tokens, normalize_fullwidth_ascii, tokens_to_text
from logovista_tools.gaiji import load_gaiji_profile, load_uni_gaiji_map, parse_ga16_resource


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


def uni_record(code: int, primary: tuple[int, int], fallback: tuple[int, int] = (0, 0)) -> bytes:
    values = [code, 0, primary[0], primary[1], fallback[0], fallback[1], 0, 0]
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
