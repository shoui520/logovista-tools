import plistlib
import sqlite3
from argparse import Namespace

import pytest

from logovista_tools.colscr import (
    decode_bcd_decimal,
    parse_colscr_image_header,
    parse_media_pointer,
    validate_bmp_header,
)
from logovista_tools.entries import (
    decode_tokens,
    discover_dictionaries,
    iter_entry_slices_reader,
    iter_entry_slices_with_boundaries,
    is_useless_body,
    normalize_fullwidth_ascii,
    resolve_section_image_sources,
    tokens_to_html,
    tokens_to_text,
)
from logovista_tools.gaiji_report import (
    SqliteSource,
    TextTable,
    collect_sqlite_text_evidence,
    informative_display,
    iter_gaiji_codes_in_stream,
    nearest_aligned_text,
)
from logovista_tools.gaiji import (
    encode_png_rgba,
    load_gaiji_profile,
    load_uni_gaiji_map,
    parse_ga16_resource,
    parse_uni_resource,
    render_ga16_glyph_rgba,
    write_ga16_glyph_png,
)
from logovista_tools.indexes import (
    IndexPointer,
    internal_slot_size,
    parse_body_only_simple_leaf_page,
    parse_body_only_tagged_leaf_page,
    parse_cr_leaf_page,
    parse_internal_page,
    parse_kw_leaf_page,
    parse_multi_leaf_page,
    parse_simple_leaf_page,
    parse_tagged_leaf_page,
)
from logovista_tools.menus import build_menu_tree, parse_menu_destination, parse_menu_stream, resolve_menu_destination
from logovista_tools.multi import parse_multi_descriptor
from logovista_tools.pcmdata import (
    detect_shared_wave_stream,
    make_riff_wave,
    parse_pcm_pointer,
    parse_pcmdata_record,
    portable_audio_bytes,
)
from logovista_tools.profiles import ProfileTarget, build_profile
from logovista_tools.rendererdb import (
    blob_extension,
    discover_android_body_databases,
    discover_ziptomedia_dir,
    html_media_reference_rows,
    html_to_plain,
    html_ziptomedia_reference_rows,
    honbun_columns,
    iter_honmon_id_records,
    media_table_name,
    media_type_counts,
    parse_dense_honmon_id,
    parse_decimal_int,
    safe_media_name,
    t_contents_columns,
    ziptomedia_reference_names,
    ziptomedia_source_path,
)
from logovista_tools.decoded_model import static_package_resources_for_idx
from logovista_tools.resources import load_image_resource_profile, relative_image_source
from logovista_tools.lvcrypto import (
    decrypt_macos_logofont_cipher_bytes,
    decrypt_logofont_cipher_auto_file_to_path,
    decrypt_logofont_cipher_bytes,
    decrypt_logofont_cipher_file_to_path,
    logofont_cipher_key_iv,
    macos_logofont_cipher_key_iv,
)
from logovista_tools.ssed import (
    SsedInfoElement,
    load_sseddata_bytes,
    parse_ssedinfo,
    parse_ssedinfo_with_layout,
    sseddata_storage_for_bytes,
)
from logovista_tools.spindex import parse_spindex_pages, reverse_spindex_key
from logovista_tools.windows import (
    classify_vlpljbl_file,
    discover_numeric_aux_indexes,
    discover_renderer_sidecars,
    file_magic_kind,
    iter_aux_index_specs,
    parse_aux_index_text,
    parse_exinfo,
    sqlite_role_for_tables,
    vlpljbl_suffix,
)


def test_normalize_fullwidth_ascii() -> None:
    assert normalize_fullwidth_ascii("ＡＢＣ１２３　ｘｙｚ") == "ABC123 xyz"


def test_logofont_cipher_key_iv() -> None:
    key, iv = logofont_cipher_key_iv()

    assert key.hex() == "a3c48d86dabe8b0c91fb33d9fdf2941b"
    assert iv.hex() == "80f2f3736bcec2e51665d02b640edbb0"


def test_logofont_cipher_decrypts_pkcs7_payload() -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    plaintext = b"SSEDDATA" + bytes(range(24))
    key, iv = logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    assert decrypt_logofont_cipher_bytes(encrypted) == plaintext
    assert sseddata_storage_for_bytes(encrypted) == "logofont_cipher"
    assert load_sseddata_bytes(encrypted) == (plaintext, "logofont_cipher")


def test_macos_logofont_cipher_decrypts_pkcs7_payload() -> None:
    plaintext = b"SSEDDATA" + bytes(range(24))
    encrypted = _encrypt_macos_logofont(plaintext)

    assert decrypt_macos_logofont_cipher_bytes(encrypted) == plaintext
    assert sseddata_storage_for_bytes(encrypted) == "macos_logofont_cipher"
    assert load_sseddata_bytes(encrypted) == (plaintext, "macos_logofont_cipher")


def test_auto_decrypt_selects_macos_logofont_cipher(tmp_path) -> None:
    plaintext = _minimal_sseddata(b"body")
    encrypted = _encrypt_macos_logofont(plaintext)
    source = tmp_path / "HONMON.DIN"
    out = tmp_path / "HONMON.DIC"
    source.write_bytes(encrypted)

    written, storage = decrypt_logofont_cipher_auto_file_to_path(source, out)

    assert storage == "macos_logofont_cipher"
    assert written == len(plaintext)
    assert out.read_bytes() == plaintext


def test_logofont_cipher_stream_decrypts_to_path(tmp_path) -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    plaintext = b"SQLite format 3\x00" + (b"x" * 4096)
    key, iv = logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    encrypted_path = tmp_path / "vlpljblb"
    decrypted_path = tmp_path / "vlpljblb.sqlite"
    encrypted_path.write_bytes(encrypted)

    written = decrypt_logofont_cipher_file_to_path(encrypted_path, decrypted_path, chunk_size=31)

    assert written == len(plaintext)
    assert decrypted_path.read_bytes() == plaintext


def _ssedinfo_record(
    *,
    type_byte: int,
    start: int,
    end: int,
    data: bytes,
    filename: bytes,
) -> bytes:
    record = bytearray(0x30)
    record[3] = type_byte
    record[4:8] = start.to_bytes(4, "big")
    record[8:12] = end.to_bytes(4, "big")
    record[12:16] = data
    record[16] = len(filename)
    record[17 : 17 + len(filename)] = filename
    return bytes(record)


def _minimal_sseddata(payload: bytes) -> bytes:
    expanded = payload + bytes(2048 - len(payload))
    header = bytearray(64)
    header[:8] = b"SSEDDATA"
    header[0x16:0x18] = (1).to_bytes(2, "big")
    header[0x18:0x1C] = (2).to_bytes(4, "big")
    header[0x1C:0x20] = (2).to_bytes(4, "big")
    data = bytearray(header)
    data.extend((68).to_bytes(4, "big"))
    data.extend(b"\x00\x00")
    data.extend(len(expanded).to_bytes(2, "big"))
    data.append(0)
    for value in expanded:
        data.extend(bytes((0, 0, value)))
    return bytes(data)


def _encrypt_macos_logofont(payload: bytes) -> bytes:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = macos_logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(payload) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def test_parse_ssedinfo_supports_shifted_multiview_catalog(tmp_path) -> None:
    title = "模範六法".encode("cp932")
    header = bytearray(0x7F)
    header[:8] = b"SSEDINFO"
    header[12] = len(title)
    header[13 : 13 + len(title)] = title
    header[0x4C] = 2
    data = bytes(header)
    data += _ssedinfo_record(type_byte=0x00, start=2, end=2, data=b"\x02\x00\x00\x00", filename=b"HONMON.DIC")
    data += _ssedinfo_record(type_byte=0x90, start=3, end=4, data=b"\x02\x01\x55\x40", filename=b"FKINDEX.DIC")
    data += b"tail"
    path = tmp_path / "MOROKU26.IDX"
    path.write_bytes(data)

    parsed_title, elements, layout = parse_ssedinfo_with_layout(path)

    assert parsed_title == "模範六法"
    assert layout.component_count_offset == 0x4C
    assert layout.record_start == 0x7F
    assert layout.trailing_bytes == 4
    assert [element.filename for element in elements] == ["HONMON.DIC", "FKINDEX.DIC"]
    assert parse_ssedinfo(path)[1][1].type == 0x90


def test_discover_dictionaries_accepts_macos_honmon_din(tmp_path) -> None:
    header = bytearray(0x80)
    header[:8] = b"SSEDINFO"
    header[0x4D] = 1
    data = bytes(header)
    data += _ssedinfo_record(type_byte=0x00, start=2, end=2, data=b"\x02\x00\x00\x00", filename=b"HONMON.DIN")
    idx = tmp_path / "MACDICT.IDX"
    idx.write_bytes(data)
    (tmp_path / "._MACDICT.IDX").write_bytes(b"\x00\x05\x16\x07")
    (tmp_path / "HONMON.DIN").write_bytes(_encrypt_macos_logofont(_minimal_sseddata(b"\x1f\x09\x00\x01body")))

    sources = discover_dictionaries([tmp_path], include_gaiji=False, include_images=False)

    assert len(sources) == 1
    assert sources[0].honmon.name == "HONMON.DIN"
    assert sources[0].honmon_storage == "macos_logofont_cipher"


def test_profile_ignores_missing_zero_block_components(tmp_path) -> None:
    header = bytearray(0x80)
    header[:8] = b"SSEDINFO"
    header[0x4D] = 2
    data = bytes(header)
    data += _ssedinfo_record(type_byte=0x00, start=2, end=2, data=b"\x02\x00\x00\x00", filename=b"HONMON.DIC")
    data += _ssedinfo_record(type_byte=0xF1, start=0, end=0, data=b"\x00\x00\x00\x00", filename=b"GA16FULL")
    idx = tmp_path / "TEST.IDX"
    idx.write_bytes(data)
    (tmp_path / "HONMON.DIC").write_bytes(_minimal_sseddata(b""))

    _title, elements = parse_ssedinfo(idx)
    profile = build_profile(
        ProfileTarget(dict_id="TEST", idx=idx, title="", elements=elements),
        [tmp_path],
        Namespace(parse_mode="forensic", max_slices=1, max_issue_samples=5, hash_files=False, skip_index_scan=True),
    )

    assert profile["classification"]["status"] == "ok"
    assert profile["classification"]["missing_components"] == []
    ga16 = next(row for row in profile["catalog"]["components"] if row["filename"] == "GA16FULL")
    assert ga16["present"] is False
    assert ga16["block_count"] == 0


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


def test_section_only_and_section_numeric_bodies_are_useless() -> None:
    assert is_useless_body("<section:0001>")
    assert is_useless_body("<section:0001>00000001")
    assert is_useless_body("<section:0001>K0NVOzjh")
    assert not is_useless_body("<section:0001>あ")


def test_media_control_uses_18_byte_payload() -> None:
    # 1f4d media starts carry 18 bytes of payload before visible text resumes.
    payload = bytes.fromhex("000000000000000000000000000186961670")
    tokens, stats = decode_tokens(b"\x1f\x4d" + payload + bytes.fromhex("2422"), preserve_media=True)

    assert tokens_to_text(tokens) == f"<media:{payload.hex()}>あ"
    assert stats["media"] == 1


def test_toc_link_control_uses_10_byte_payload() -> None:
    payload = bytes.fromhex("00010203000000040130")
    tokens, stats = decode_tokens(b"\x1f\x49" + payload + bytes.fromhex("2422") + b"\x1f\x69")

    assert tokens_to_text(tokens) == "あ"
    assert stats["links"] == 1
    assert stats["unknown_controls"] == 0


def test_bare_title_separator_1103_is_nonprinting() -> None:
    tokens, stats = decode_tokens(bytes.fromhex("242211032424"))

    assert tokens_to_text(tokens) == "あい"
    assert stats["legacy_controls"] == 1


def test_link_start_uses_16_byte_payload_then_visible_text() -> None:
    payload = bytes.fromhex("00010000000231930000000231991579")
    tokens, stats = decode_tokens(b"\x1f\x4a" + payload + bytes.fromhex("2422") + b"\x1f\x6a" + bytes.fromhex("2424"))

    assert tokens_to_text(tokens) == "あい"
    assert stats["links"] == 1


def test_menu_link_end_uses_6_byte_destination_payload() -> None:
    payload = bytes.fromhex("000000020002")
    tokens, stats = decode_tokens(b"\x1f\x43" + bytes.fromhex("2422") + b"\x1f\x63" + payload + bytes.fromhex("2424"))

    assert tokens_to_text(tokens) == "あい"
    assert stats["links"] == 1


def test_menu_destination_uses_bcd_block_and_offset() -> None:
    destination = parse_menu_destination(bytes.fromhex("000256780002"))

    assert destination is not None
    assert destination.encoding == "bcd"
    assert destination.block == 25678
    assert destination.offset == 2
    assert not destination.is_null


def test_menu_destination_marks_zero_pointer_as_null() -> None:
    destination = parse_menu_destination(bytes.fromhex("000000000000"))

    assert destination is not None
    assert destination.encoding == "bcd"
    assert destination.block == 0
    assert destination.offset == 0
    assert destination.is_null


def test_menu_destination_resolves_to_idx_component() -> None:
    destination = parse_menu_destination(bytes.fromhex("000256780002"))

    assert destination is not None
    resolved = resolve_menu_destination(
        destination,
        [
            SsedInfoElement(
                index=1,
                multi=0,
                type=0x00,
                start=25678,
                end=30000,
                data=b"\x00\x00\x00\x00",
                filename="HONMON.DIC",
            )
        ],
    )

    assert resolved.target is not None
    assert resolved.target.component == "HONMON.DIC"
    assert resolved.target.kind == "body"
    assert resolved.target.relative_offset == 2


def test_parse_menu_stream_emits_tree_records_and_destinations() -> None:
    data = bytes.fromhex(
        "1f090001"
        "1f4324221f63000000020002"
        "1f0a"
        "1f090002"
        "1f4324241f63000000030004"
        "1f0a"
    )
    parsed = parse_menu_stream(data, gaiji="drop")

    assert parsed.stats["sections"] == 2
    assert parsed.stats["links"] == 2
    assert parsed.stats["destinations"] == 2
    assert parsed.stats["null_destinations"] == 0
    assert len(parsed.records) == 2
    assert parsed.records[0].text == "あ"
    assert parsed.records[0].section_code == "0001"
    assert parsed.records[0].depth == 1
    assert parsed.records[0].destination is not None
    assert parsed.records[0].destination.block == 2
    assert parsed.records[1].text == "い"
    assert parsed.records[1].section_code == "0002"
    assert parsed.records[1].depth == 2
    assert parsed.records[1].path == ["あ", "い"]

    tree = build_menu_tree(parsed.records)
    assert tree[0]["text"] == "あ"
    assert tree[0]["children"][0]["text"] == "い"


def test_parse_menu_stream_counts_null_destinations() -> None:
    data = bytes.fromhex("1f4324221f630000000000001f0a")
    parsed = parse_menu_stream(data, gaiji="drop")

    assert parsed.stats["links"] == 1
    assert parsed.stats["destinations"] == 1
    assert parsed.stats["null_destinations"] == 1
    assert parsed.records[0].destination is not None
    assert parsed.records[0].destination.is_null


def test_parse_menu_stream_supports_legacy_link_wrapper() -> None:
    data = bytes.fromhex("1f0900021f421f0024221f62000000030004")
    parsed = parse_menu_stream(data, gaiji="drop")

    assert parsed.stats["unknown_controls"] == 0
    assert parsed.stats["links"] == 1
    assert parsed.records[0].text == "あ"
    assert parsed.records[0].links[0].control == "1f42"
    assert parsed.records[0].destination is not None
    assert parsed.records[0].destination.block == 3
    assert parsed.records[0].destination.offset == 4


def test_colscr_media_pointer_uses_packed_bcd_decimal() -> None:
    payload = bytes.fromhex("000000000000000000000000002175530478")
    pointer = parse_media_pointer(payload)

    assert pointer is not None
    assert pointer.block == 217553
    assert pointer.offset == 478
    assert decode_bcd_decimal(bytes.fromhex("00017649")) == 17649


def test_colscr_validates_wrapped_bmp_header() -> None:
    width = 131
    height = 640
    row_bytes = ((width * 3 + 3) // 4) * 4
    size = 54 + row_bytes * height
    bmp = bytearray(size)
    bmp[0:2] = b"BM"
    bmp[2:6] = size.to_bytes(4, "little")
    bmp[10:14] = (54).to_bytes(4, "little")
    bmp[14:18] = (40).to_bytes(4, "little")
    bmp[18:22] = width.to_bytes(4, "little")
    bmp[22:26] = height.to_bytes(4, "little")
    bmp[26:28] = (1).to_bytes(2, "little")
    bmp[28:30] = (24).to_bytes(2, "little")

    assert validate_bmp_header(b"data" + size.to_bytes(4, "little") + bytes(bmp[:62])) == (
        size,
        width,
        height,
        24,
        0,
    )


def test_colscr_accepts_palette_bmp_header() -> None:
    width = 64
    height = 175
    row_bytes = ((width + 3) // 4) * 4
    pixel_offset = 54 + 256 * 4
    size = pixel_offset + row_bytes * height
    bmp = bytearray(size)
    bmp[0:2] = b"BM"
    bmp[2:6] = size.to_bytes(4, "little")
    bmp[10:14] = pixel_offset.to_bytes(4, "little")
    bmp[14:18] = (40).to_bytes(4, "little")
    bmp[18:22] = width.to_bytes(4, "little")
    bmp[22:26] = height.to_bytes(4, "little")
    bmp[26:28] = (1).to_bytes(2, "little")
    bmp[28:30] = (8).to_bytes(2, "little")

    assert validate_bmp_header(b"data" + size.to_bytes(4, "little") + bytes(bmp[:62])) == (
        size,
        width,
        height,
        8,
        0,
    )


def test_colscr_detects_jpeg_record() -> None:
    payload_size = 12
    wrapped = b"data" + payload_size.to_bytes(4, "little") + bytes.fromhex("ffd8ffe000104a4649460001")

    assert parse_colscr_image_header(wrapped) == (payload_size, "jpeg", "jpg", None, None, None, None)


def test_colscr_detects_png_record_dimensions() -> None:
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (32).to_bytes(4, "big")
        + (48).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    wrapped = b"data" + len(payload).to_bytes(4, "little") + payload

    assert parse_colscr_image_header(wrapped) == (len(payload), "png", "png", 32, 48, None, None)


def test_pcmdata_pointer_uses_bcd_start_and_end() -> None:
    payload = bytes.fromhex("00010000000231930000000231991579")
    pointer = parse_pcm_pointer(payload)

    assert pointer is not None
    assert pointer.kind == 1
    assert pointer.flags == 0
    assert pointer.start_block == 23193
    assert pointer.start_offset == 0
    assert pointer.end_block == 23199
    assert pointer.end_offset == 1579


def pcm_wave_chunks(data_size: int = 4) -> bytes:
    fmt = (
        b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
    )
    data = b"data" + data_size.to_bytes(4, "little") + (b"\x01\x02" * ((data_size + 1) // 2))[:data_size]
    return fmt + data + (b"\x00" if data_size & 1 else b"") + (b"\x00" * 12)


def test_pcmdata_parses_wave_chunks_and_writes_riff_wrapper() -> None:
    raw = pcm_wave_chunks(4)
    parsed = parse_pcmdata_record(raw, 2048)

    assert parsed is not None
    record, chunks = parsed
    assert record.codec == "pcm"
    assert record.extension == "wav"
    assert record.sample_rate == 16000
    assert record.bits_per_sample == 16
    assert record.data_size == 4
    assert record.trailing_zero_bytes == 12
    assert [chunk.tag for chunk in chunks] == ["fmt ", "data"]
    assert portable_audio_bytes(record, raw) == make_riff_wave(raw[: record.content_size])


def test_pcmdata_detects_shared_wave_slices_and_writes_riff_wrapper() -> None:
    wave = pcm_wave_chunks(8)[:-12]
    shared = b"\x01\x00\x20\x00" + (b"\x00" * 28) + wave
    shared_wave = detect_shared_wave_stream(shared[:80])

    assert shared_wave is not None
    assert shared_wave.fmt_offset == 32
    assert shared_wave.data_offset == 64
    assert shared_wave.data_size == 8

    raw_slice = shared[shared_wave.data_offset : shared_wave.data_offset + 4]
    parsed = parse_pcmdata_record(raw_slice, shared_wave.data_offset, shared_wave=shared_wave)

    assert parsed is not None
    record, chunks = parsed
    assert chunks == []
    assert record.media_type == "wave_data_slice"
    assert record.codec == "pcm"
    assert record.extension == "wav"
    assert record.shared_fmt_offset == 32
    assert record.shared_data_offset == 64
    assert portable_audio_bytes(record, raw_slice) == make_riff_wave(pcm_wave_chunks(4)[:-12])


def test_pcmdata_extracts_mpeg_wave_data_as_mp3() -> None:
    fmt_payload = (
        (0x55).to_bytes(2, "little")
        + (2).to_bytes(2, "little")
        + (44100).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (12).to_bytes(2, "little")
        + bytes.fromhex("0100020000009f0101007105")
    )
    mp3 = bytes.fromhex("fffb9060") + (b"\x55" * 8)
    raw = b"fmt " + len(fmt_payload).to_bytes(4, "little") + fmt_payload
    raw += b"fact" + (4).to_bytes(4, "little") + (123).to_bytes(4, "little")
    raw += b"data" + len(mp3).to_bytes(4, "little") + mp3 + (b"\x00" * 12)
    parsed = parse_pcmdata_record(raw, 0)

    assert parsed is not None
    record, chunks = parsed
    assert record.codec == "mpeg_layer3_wave"
    assert record.extension == "mp3"
    assert [chunk.tag for chunk in chunks] == ["fmt ", "fact", "data"]
    assert portable_audio_bytes(record, raw) == mp3


def test_pcmdata_detects_id3_mp3_record() -> None:
    raw = b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes.fromhex("fff354c4") + (b"\x55" * 8) + (b"\x00" * 12)
    parsed = parse_pcmdata_record(raw, 0)

    assert parsed is not None
    record, _chunks = parsed
    assert record.media_type == "mp3"
    assert record.codec == "mp3"
    assert record.extension == "mp3"
    assert record.trailing_zero_bytes == 12
    assert portable_audio_bytes(record, raw).startswith(b"ID3")


def test_index_boundaries_are_sorted_before_entry_slicing() -> None:
    data = b"\x00\x00" + bytes.fromhex("1f09000824221f0a") + bytes.fromhex("1f0900032424") + bytes.fromhex("1f0900012426")

    slices = list(iter_entry_slices_with_boundaries(data, [2, 10]))

    assert slices == [(2, 10), (10, 16), (16, len(data))]


def test_streaming_entry_slices_handle_chunk_boundary_marker() -> None:
    class Reader:
        def __init__(self, data: bytes):
            self.data = data
            self.expanded_size = len(data)

        def read(self, offset: int, size: int) -> bytes:
            return self.data[offset : offset + size]

    marker = bytes.fromhex("1f090001")
    data = b"A" * (0x8000 - 2) + marker + b"first" + marker + b"second"

    slices = list(iter_entry_slices_reader(Reader(data)))  # type: ignore[arg-type]

    assert slices == [(0x8000 - 2, 0x8000 + 7), (0x8000 + 7, len(data))]


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


def test_load_simple12_single_section_uni_gaiji_map(tmp_path) -> None:
    path = tmp_path / "HABGESPA.uni"
    path.write_bytes(
        (2).to_bytes(4, "big")
        + simple_uni_record(0xA121, (0, 0x00A1))
        + simple_uni_record(0xA122, (0, 0x00AA))
    )

    resource = parse_uni_resource(path)
    mapping, records_seen = load_uni_gaiji_map(path)

    assert resource is not None
    assert resource.format == "simple12-single"
    assert resource.half_count == 2
    assert resource.full_count == 0
    assert records_seen == 2
    assert mapping["a121"] == "¡"
    assert mapping["a122"] == "ª"


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


def test_ga16_resource_glyph_lookup_uses_jis_grid(tmp_path) -> None:
    path = tmp_path / "GA16HALF"
    header = bytearray(2048)
    header[0] = 1
    header[8] = 8
    header[9] = 16
    header[10:12] = bytes.fromhex("a121")
    header[12:14] = (95).to_bytes(2, "big")
    glyphs = [bytes([index]) * 16 for index in range(95)]
    path.write_bytes(bytes(header) + b"".join(glyphs))

    resource = parse_ga16_resource(path)

    assert resource is not None
    assert resource.glyph_for_code(path.read_bytes(), 0xA17E) == glyphs[93]
    assert resource.glyph_for_code(path.read_bytes(), 0xA221) == glyphs[94]


def test_parse_ga16_resource_accepts_empty_bitmap_resource(tmp_path) -> None:
    path = tmp_path / "GA16FULL"
    header = bytearray(2048)
    header[0] = 1
    header[8] = 16
    header[9] = 16
    header[10:12] = bytes.fromhex("b121")
    header[12:14] = (0).to_bytes(2, "big")
    path.write_bytes(bytes(header))

    resource = parse_ga16_resource(path)

    assert resource is not None
    assert resource.count == 0
    assert list(resource.iter_glyphs(path.read_bytes())) == []


def test_render_ga16_glyph_rgba_uses_msb_first_rows() -> None:
    glyph = bytes([0b1000_0000, 0b0100_0000, 0b0010_0000, 0b0001_0000])

    rgba = render_ga16_glyph_rgba(glyph, 8, 4)

    pixels = [rgba[i : i + 4] for i in range(0, len(rgba), 4)]
    assert pixels[0] == bytes([0, 0, 0, 255])
    assert pixels[1] == bytes([0, 0, 0, 0])
    assert pixels[9] == bytes([0, 0, 0, 255])
    assert pixels[18] == bytes([0, 0, 0, 255])
    assert pixels[27] == bytes([0, 0, 0, 255])


def test_encode_png_rgba_and_write_ga16_glyph_png(tmp_path) -> None:
    path = tmp_path / "hA121.png"
    glyph = bytes([0x80] + [0] * 15)

    write_ga16_glyph_png(path, glyph, 8, 16)
    png = path.read_bytes()

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert png[12:16] == b"IHDR"
    assert int.from_bytes(png[16:20], "big") == 8
    assert int.from_bytes(png[20:24], "big") == 16
    assert encode_png_rgba(1, 1, bytes([0, 0, 0, 0])).startswith(b"\x89PNG\r\n\x1a\n")


def test_iter_gaiji_codes_in_stream_skips_jis_and_control_payloads() -> None:
    data = bytes.fromhex("24221f4a00010000000231930000000231991579a1261f0a")

    assert list(iter_gaiji_codes_in_stream(data)) == ["a126"]


def test_collect_sqlite_text_evidence_counts_informative_displays(tmp_path) -> None:
    db_path = tmp_path / "DICT.sql"
    con = sqlite3.connect(db_path)
    con.execute("create table entries (Block integer, Offset integer, Title text, Body text)")
    con.execute("insert into entries values (1, 2, 'café', 'niño café')")
    con.execute("insert into entries values (1, 8, 'plain', 'nothing')")
    con.commit()
    con.close()
    source = SqliteSource(
        path=db_path,
        kind="sqlite_cache",
        tables=(TextTable("entries", ("Title", "Body"), 2, True),),
    )

    counts, summaries = collect_sqlite_text_evidence((source,), ["é", "ñ", "a"])

    assert informative_display("é")
    assert not informative_display("a")
    assert counts["é"] == 2
    assert counts["ñ"] == 1
    assert summaries[0]["rows_scanned"] == 2


def test_nearest_aligned_text_uses_tolerance(tmp_path) -> None:
    source = SqliteSource(path=tmp_path / "DICT.sql", kind="sqlite_cache", tables=())
    index = type(
        "Index",
        (),
        {
            "source": source,
            "rows_by_block": {10: [(100, "too far"), (156, "matched")]},
            "rows_indexed": 2,
        },
    )()

    assert nearest_aligned_text((index,), 10, 158, tolerance=4) == ("matched", str(source.path), 156)
    assert nearest_aligned_text((index,), 10, 158, tolerance=1) is None


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


def test_load_image_resource_profile_discovers_android_kmkimges(tmp_path) -> None:
    package = tmp_path / "PKG"
    dict_dir = package / "DICT"
    image_dir = package / "resource" / "kmkimges"
    dict_dir.mkdir(parents=True)
    image_dir.mkdir(parents=True)
    idx = dict_dir / "DICT.IDX"
    idx.write_bytes(b"")
    (image_dir / "b167_1.png").write_bytes(b"")
    (image_dir / "b167_3.png").write_bytes(b"")
    (image_dir / "gogen_w.png").write_bytes(b"")

    profile = load_image_resource_profile(idx)

    assert profile.image_dirs == (image_dir,)
    assert "b167" in profile.gaiji_image_keys
    assert profile.resources["b167"].normal == image_dir / "b167_1.png"
    assert profile.resources["b167"].white == image_dir / "b167_3.png"
    assert profile.resources["gogen"].white == image_dir / "gogen_w.png"
    assert relative_image_source(image_dir / "b167_1.png", idx) == "resource/kmkimges/b167_1.png"


def test_load_image_resource_profile_discovers_windows_templates(tmp_path) -> None:
    package = tmp_path / "DICT"
    templates = package / "Templates"
    hanrei_img = package / "HANREI" / "img"
    templates.mkdir(parents=True)
    hanrei_img.mkdir(parents=True)
    idx = package / "DICT.IDX"
    idx.write_bytes(b"")
    (templates / "exam.png").write_bytes(b"png")
    (templates / "B222.png").write_bytes(b"png")
    (templates / "1652-1.bmp").write_bytes(b"bmp")
    (templates / "inline.svg").write_bytes(b"<svg/>")
    (hanrei_img / "b159_M.png").write_bytes(b"png")

    profile = load_image_resource_profile(idx)

    assert templates in profile.image_dirs
    assert hanrei_img in profile.image_dirs
    assert profile.resources["exam"].default == templates / "exam.png"
    assert "b222" in profile.gaiji_image_keys
    assert "b159" in profile.gaiji_image_keys
    assert profile.resources["b159"].default == hanrei_img / "b159_M.png"
    assert profile.resources["1652-1"].default == templates / "1652-1.bmp"
    assert profile.resources["inline"].default == templates / "inline.svg"
    assert relative_image_source(templates / "exam.png", idx) == "Templates/exam.png"
    assert relative_image_source(hanrei_img / "b159_M.png", idx) == "HANREI/img/b159_M.png"


def test_load_image_resource_profile_discovers_platformless_resource_dirs(tmp_path) -> None:
    package = tmp_path / "DICT"
    res = package / "res"
    templates = package / "templates"
    res.mkdir(parents=True)
    templates.mkdir(parents=True)
    idx = package / "DICT.IDX"
    idx.write_bytes(b"")
    (res / "B123.png").write_bytes(b"png")
    (res / "exam.png").write_bytes(b"png")
    (templates / "A126.png").write_bytes(b"png")

    profile = load_image_resource_profile(idx)
    static = static_package_resources_for_idx(idx)

    assert res in profile.image_dirs
    assert any(path.name.lower() == "templates" for path in profile.image_dirs)
    assert profile.resources["b123"].default == res / "B123.png"
    assert profile.resources["a126"].default is not None
    assert profile.resources["a126"].default.name == "A126.png"
    assert profile.resources["a126"].default.parent.name.lower() == "templates"
    assert "b123" in profile.gaiji_image_keys
    assert "a126" in profile.gaiji_image_keys
    assert relative_image_source(res / "B123.png", idx) == "res/B123.png"
    assert relative_image_source(templates / "A126.png", idx) == "templates/A126.png"
    assert "res" in static["directories"]
    assert "templates" in static["directories"]
    assert static["extension_counts"][".png"] == 3


def test_load_image_resource_profile_discovers_sibling_gaiji_companion(tmp_path) -> None:
    collection = tmp_path / "corpus"
    package = collection / "_DCT_KANJIGN5"
    companion = collection / "_DCT_KANJIGN5_GAIJI"
    package.mkdir(parents=True)
    companion.mkdir()
    idx = package / "KANJIGN5.IDX"
    idx.write_bytes(b"")
    (companion / "b44c.png").write_bytes(b"png")

    profile = load_image_resource_profile(idx)
    static = static_package_resources_for_idx(idx)

    assert companion in profile.image_dirs
    assert "b44c" in profile.gaiji_image_keys
    assert relative_image_source(companion / "b44c.png", idx) == "_DCT_KANJIGN5_GAIJI/b44c.png"
    assert "_DCT_KANJIGN5_GAIJI" in static["directories"]
    assert static["extension_counts"][".png"] == 1


def test_parse_windows_exinfo_and_auxiliary_text_idx(tmp_path) -> None:
    exinfo = tmp_path / "EXINFO.INI"
    exinfo.write_text(
        "[GENERAL]\nIDXCOUNT=1\nIDXNAME0=分野\nIDXINFO0=0000015E.IDX\nROSQLNAME=DICT.db\n",
        encoding="cp932",
    )
    idx_text = tmp_path / "0000015E.IDX"
    idx_text.write_text(
        "00000000\t00000000\t大辞林 第四版\n"
        "00005221\t00000722\t\t季語\n"
        "00005221\t000007C2\t\t\t春\n",
        encoding="cp932",
    )
    element = SsedInfoElement(
        index=0,
        multi=0,
        type=0,
        start=0x5221,
        end=0x5230,
        data=b"",
        filename="HONMON.DIC",
    )

    parsed = parse_exinfo(exinfo)
    specs = iter_aux_index_specs(parsed)
    rows = parse_aux_index_text(idx_text, [element])

    assert specs[0].name == "分野"
    assert specs[0].info == "0000015E.IDX"
    assert rows[0].label == "大辞林 第四版"
    assert rows[0].depth == 1
    assert rows[1].path == ("大辞林 第四版", "季語")
    assert rows[2].path == ("大辞林 第四版", "季語", "春")
    assert rows[1].target["component"] == "HONMON.DIC"


def test_parse_windows_exinfo_legacy_single_aux_idx(tmp_path) -> None:
    exinfo = tmp_path / "EXINFO.INI"
    exinfo.write_text("[GENERAL]\nIDXINFO=0000013A.idx\nIDXTITLE=インデックス\n", encoding="cp932")
    idx_text = tmp_path / "0000013A.idx"
    idx_text.write_text(
        "00000000\t00000000\tRoot\n"
        "10000000\t0000FFFF\t\t西和ABC順\n",
        encoding="cp932",
    )

    specs = iter_aux_index_specs(parse_exinfo(exinfo))
    rows = parse_aux_index_text(idx_text, [])

    assert len(specs) == 1
    assert specs[0].name == "インデックス"
    assert specs[0].info == "0000013A.idx"
    assert specs[0].path == tmp_path / "0000013A.idx"
    assert rows[1].target["kind"] == "virtual-index-selector"
    assert rows[1].target["selector"] == "1"


def test_parse_windows_exinfo_count_uses_idxtitle_for_first_aux_idx(tmp_path) -> None:
    exinfo = tmp_path / "EXINFO.INI"
    exinfo.write_text(
        "[GENERAL]\nIDXCOUNT=2\nIDXTITLE=索引\nIDXINFO0=00000152.idx\nIDXNAME1=オプション\nIDXINFO1=select.html\n",
        encoding="cp932",
    )
    aux = tmp_path / "00000152.idx"
    aux.write_text("00000000\t00000000\tRoot\n", encoding="cp932")

    specs = iter_aux_index_specs(parse_exinfo(exinfo))

    assert specs[0].name == "索引"
    assert specs[0].path == aux
    assert specs[1].name == "オプション"


def test_discover_numeric_aux_indexes_excludes_main_ssedinfo(tmp_path) -> None:
    idx = tmp_path / "DICT.IDX"
    idx.write_bytes(b"SSEDINFO")
    numeric = tmp_path / "0000015f.idx"
    numeric.write_text("00000000\t00000000\tRoot\n", encoding="cp932")
    sharded_numeric = tmp_path / "0000015f_1.idx"
    sharded_numeric.write_text("00000002\t00000010\tShard\n", encoding="cp932")
    ssedinfo_named = tmp_path / "00000001.idx"
    ssedinfo_named.write_bytes(b"SSEDINFO")
    (tmp_path / "not_numeric.idx").write_text("", encoding="utf-8")

    assert discover_numeric_aux_indexes(idx) == [numeric.resolve(), sharded_numeric.resolve()]


def test_plain_sqlite_renderer_sidecar_requires_supported_schema(tmp_path) -> None:
    idx = tmp_path / "DICT.IDX"
    idx.write_bytes(b"")
    cache = tmp_path / "DICT.db"
    con = sqlite3.connect(cache)
    con.execute("create table cache (Title text)")
    con.commit()
    con.close()

    assert discover_renderer_sidecars(idx) == []

    con = sqlite3.connect(cache)
    con.execute("create table EntryPayload (content_id integer primary key, body_html text)")
    con.commit()
    con.close()

    sidecars = discover_renderer_sidecars(idx)

    assert len(sidecars) == 1
    assert sidecars[0].path == cache.resolve()
    assert sidecars[0].storage == "plain"


def test_plain_sqlite_renderer_sidecar_accepts_honbun(tmp_path) -> None:
    idx = tmp_path / "DICT.IDX"
    idx.write_bytes(b"")
    cache = tmp_path / "DICT.db"
    con = sqlite3.connect(cache)
    con.execute("create table HONBUN (ID text primary key, Title_UTF8 text, Contents_HTML_box text)")
    con.commit()
    con.close()

    sidecars = discover_renderer_sidecars(idx)

    assert len(sidecars) == 1
    assert sidecars[0].path == cache.resolve()
    assert sidecars[0].storage == "plain"


def test_vlpljbl_classifier_identifies_plain_sqlite_role(tmp_path) -> None:
    path = tmp_path / "vlpljblF"
    con = sqlite3.connect(path)
    con.execute("create table t_contents (f_DataId integer primary key, f_Html text, f_Plane text)")
    con.commit()
    con.close()

    row = classify_vlpljbl_file(path, inspect_sqlite=True, compute_hash=False)

    assert vlpljbl_suffix(path) == "F"
    assert row.storage == "plain"
    assert row.content_kind == "sqlite"
    assert row.role == "sqlite_renderer_body"
    assert row.sqlite_tables[0]["name"] == "t_contents"


def test_vlpljbl_magic_and_sqlite_roles() -> None:
    assert file_magic_kind(b"MZ" + b"\x00" * 20) == "pe_executable"
    assert file_magic_kind(b"OTTO" + b"\x00" * 20) == "opentype_cff"
    assert sqlite_role_for_tables(({"name": "AssetRows", "columns": ["asset_name", "payload_blob"]},)) == "sqlite_media_store"
    assert (
        sqlite_role_for_tables(({"name": "HONBUN", "columns": ["ID", "Title_UTF8", "Contents_HTML_box"]},))
        == "sqlite_row_ordered_honbun_renderer_body"
    )
    assert sqlite_role_for_tables(({"name": "MAIN", "columns": ["Block", "Offset", "Body"]},)) == "sqlite_block_offset_body"
    assert sqlite_role_for_tables(({"name": "EntryPayload", "columns": ["content_id", "body_html"]},)) == "sqlite_renderer_body"


def test_discover_android_body_database_and_media_schema(tmp_path) -> None:
    idx = tmp_path / "DICT.IDX"
    idx.write_bytes(b"")
    body_db = tmp_path / "DICT.db"
    con = sqlite3.connect(body_db)
    con.execute("create table DICT (Html text)")
    con.execute("insert into DICT (Html) values ('<p>body</p>')")
    con.execute("create table media (id integer, name text, type integer, main blob)")
    con.execute("insert into media values (1, '02766', 4, ?)", (b'<svg id="x"/>',))
    con.commit()

    assert discover_android_body_databases(idx, "DICT")[0].path == body_db.resolve()
    assert media_type_counts(con) == {"4": 1}
    assert blob_extension(b'  <svg id="x"/>') == "svg"

    con.close()


def test_rendererdb_lowercase_content_and_t_media_schema(tmp_path) -> None:
    cache = tmp_path / "renderer.sqlite"
    con = sqlite3.connect(cache)
    con.execute("create table t_contents (f_dataid integer primary key, f_type integer, f_html text)")
    con.execute(
        "insert into t_contents values (5, 1, '<a href=\"lved.ziptomedia:000010.wav\">sound</a>')"
    )
    con.execute("insert into t_contents values (10, 1, '<img src=\"x.gif\" class=\"media\">')")
    con.execute("create table t_media (id integer, name text, type integer, main blob)")
    con.execute("insert into t_media values (1, 'fig', 2, ?)", (b'\x89PNG\r\n\x1a\nx',))
    con.commit()

    assert t_contents_columns(con)["f_DataId"] == "f_dataid"
    assert html_ziptomedia_reference_rows(con) == 1
    assert html_media_reference_rows(con) == 1
    assert media_type_counts(con) == {"2": 1}

    con.close()


def test_rendererdb_underscore_content_aliases() -> None:
    con = sqlite3.connect(":memory:")
    con.execute(
        "create table t_contents ("
        "f_array_no integer, f_data_id integer, f_midashi text, f_contents text, f_media text)"
    )

    columns = t_contents_columns(con)

    assert columns["f_DataId"] == "f_data_id"
    assert columns["f_Title"] == "f_midashi"
    assert columns["f_Html"] == "f_contents"
    assert columns["f_Media"] == "f_media"

    con.close()


def test_rendererdb_decimal_id_parser_is_lossless() -> None:
    assert parse_decimal_int(123) == 123
    assert parse_decimal_int("00123") == 123
    assert parse_decimal_int("99A00001") is None
    assert parse_decimal_int("") is None
    assert parse_decimal_int(None) is None


def test_rendererdb_two_column_t_media_schema() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("create table t_media (f_name text, f_blob blob)")
    con.execute("insert into t_media values ('figure.jpg', ?)", (b"\xff\xd8\xffx",))

    assert media_type_counts(con) == {"0": 1}

    con.close()


def test_rendererdb_generic_media_blob_schema() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("create table AssetRows (asset_name text, payload_blob blob)")
    con.execute("insert into AssetRows values ('figure.jpg', ?)", (b"\xff\xd8\xffx",))

    assert media_table_name(con) == "AssetRows"
    assert media_type_counts(con) == {"0": 1}

    con.close()


def test_rendererdb_honbun_columns_are_case_insensitive() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("create table HONBUN (id text primary key, title_utf8 text, contents_html_box text)")

    columns = honbun_columns(con)

    assert columns["ID"] == "id"
    assert columns["Title_UTF8"] == "title_utf8"
    assert columns["Contents_HTML_box"] == "contents_html_box"

    con.close()


def test_safe_media_name_preserves_renderer_filename_when_possible() -> None:
    used: set[str] = set()

    assert safe_media_name(1, "3djr_0002.gif", "gif", used) == "3djr_0002.gif"
    assert safe_media_name(2, "00002153-0082-000006ec", "png", used) == "00002153-0082-000006ec.png"
    assert safe_media_name(3, "3djr_0002.gif", "gif", used) == "00003_3djr_0002.gif"


def test_ziptomedia_reference_discovery_uses_sibling_sound_folder(tmp_path) -> None:
    dict_dir = tmp_path / "_DCT_TEST"
    dict_dir.mkdir()
    idx = dict_dir / "TEST.IDX"
    idx.write_bytes(b"")
    sound_dir = tmp_path / "_DCT_TEST_Sound_Files"
    sound_dir.mkdir()
    sound_file = sound_dir / "000010"
    sound_file.write_bytes(b"encrypted")

    refs = ziptomedia_reference_names('<a href="lved.ziptomedia:000010.wav">sound</a>')

    assert refs == ["000010.wav"]
    assert discover_ziptomedia_dir(idx) == sound_dir.resolve()
    assert ziptomedia_source_path(sound_dir, refs[0]) == sound_file


def test_parse_dense_honmon_id_record_and_pointer() -> None:
    record = bytes.fromhex(
        "1f0a1f0900011f4101601f04"
        "23302330233023302330233123322333"
        "1f051f61"
    )
    expanded = (b"\x00" * 32) + record
    records = list(iter_honmon_id_records(expanded, honmon_start_block=2))

    assert parse_dense_honmon_id(record) == 123
    assert records[0].data_id == 123
    assert records[0].record_offset == 32
    assert records[0].block == 2
    assert records[0].offset == 32
    assert records[0].marker_offset == 34


def test_parse_dense_honmon_id_record_with_marker_at_record_start() -> None:
    record = bytes.fromhex(
        "1f0900011f4101001f04"
        "23312330233123302330233023302330"
        "1f051f611f0a"
    )
    expanded = record
    records = list(iter_honmon_id_records(expanded, honmon_start_block=2))

    assert parse_dense_honmon_id(record) == 10100000
    assert records[0].data_id == 10100000
    assert records[0].record_offset == 0
    assert records[0].block == 2
    assert records[0].offset == 0
    assert records[0].marker_offset == 0


def test_parse_spindex_internal_pages_and_reversed_keys() -> None:
    def jis_ascii(value: str, width: int) -> bytes:
        out = bytearray()
        for char in value:
            out.extend((0x23, ord(char)))
        return bytes(out).ljust(width, b"\x00")

    page = bytearray(2048)
    page[0:2] = (0x601E).to_bytes(2, "big")
    page[2:4] = (2).to_bytes(2, "big")
    page[4:38] = jis_ascii("CITEROHPAID", 30) + (0xD9C9).to_bytes(4, "big")
    page[38:72] = jis_ascii("DEZIRECREM", 30) + (0xE200).to_bytes(4, "big")
    rows: list[dict] = []

    pages, child_counts = parse_spindex_pages(
        bytes(page),
        start_block=0xD9C8,
        end_block=0xE101,
        emit_row=rows.append,
    )

    assert reverse_spindex_key("CITEROHPAID") == "DIAPHORETIC"
    assert pages[0].rows_parsed == 2
    assert rows[0]["key"] == "DIAPHORETIC"
    assert rows[0]["child_status"] == "missing_from_physical_file"
    assert rows[1]["child_status"] == "outside_declared_range"
    assert child_counts == {"missing_from_physical_file": 1, "outside_declared_range": 1}


def test_rendererdb_html_to_plain_preserves_line_breaks() -> None:
    assert html_to_plain("<div>あ<br />い<br>う</div>") == "あ\nい\nう"
    assert html_to_plain("<span>Abb&eacute;</span>") == "Abbé"


def test_parse_internal_index_page_uses_32bit_child() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("601e")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:6] = bytes.fromhex("2422")
    page[34:38] = (0x000112BD).to_bytes(4, "big")

    rows = list(parse_internal_page("FKINDEX.DIC", bytes(page), 1, 100, gaiji_map={}, gaiji="drop"))

    assert len(rows) == 1
    assert rows[0].key == "あ"
    assert rows[0].raw_key == bytes.fromhex("2422")
    assert rows[0].child_block == 0x000112BD


def test_parse_internal_index_page_uses_full_low_byte_slot_size() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("6068")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:6] = bytes.fromhex("2422")
    page[108:112] = (0x12345678).to_bytes(4, "big")

    rows = list(parse_internal_page("BHINDEX.DIC", bytes(page), 1, 100, gaiji_map={}, gaiji="drop"))

    assert internal_slot_size(0x6068) == 108
    assert len(rows) == 1
    assert rows[0].key == "あ"
    assert rows[0].child_block == 0x12345678


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


def test_parse_simple_leaf_keyless_pointer_table() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("c000")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:17] = bytes.fromhex("0000000100027f000000030004")

    rows, unknown = parse_simple_leaf_page("MUL2_1_2.DIC", bytes(page), 1, 100, gaiji_map={}, gaiji="drop")

    assert unknown == 0
    assert rows[0].key == ""
    assert rows[0].body == IndexPointer(1, 2)
    assert rows[0].title == IndexPointer(3, 4)


def test_parse_body_only_simple_leaf_index_page() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("c000")
    page[2:4] = (1).to_bytes(2, "big")
    page[4] = 2
    page[5:7] = bytes.fromhex("2422")
    page[7:13] = bytes.fromhex("000000010002")

    rows, unknown = parse_body_only_simple_leaf_page("HINDEX.DIC", bytes(page), 1, 100, gaiji_map={}, gaiji="drop")

    assert unknown == 0
    assert rows[0].key == "あ"
    assert rows[0].body == IndexPointer(1, 2)
    assert rows[0].title == IndexPointer(1, 2)


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


def test_parse_body_only_tagged_leaf_index_page() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (2).to_bytes(2, "big")
    page[4:10] = bytes.fromhex("800200012422")
    page[10:20] = bytes.fromhex("c0022424000000010002")

    rows, current_key, hint, groups, unknown = parse_body_only_tagged_leaf_page(
        "KINDEX.DIC",
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
    assert rows[0].title == IndexPointer(1, 2)


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


def test_parse_cr_leaf_group_and_body_targets() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (3).to_bytes(2, "big")
    page[4:12] = bytes.fromhex("8002000000022422")
    page[12:18] = bytes.fromhex("000000030004")
    page[18:25] = bytes.fromhex("c0000000100020")
    page[25:32] = bytes.fromhex("c0000000110030")

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
    assert groups == 1
    assert hint == 2
    assert current_key == "あ"
    assert current_title == IndexPointer(3, 4)
    assert [row.key for row in rows] == ["あ", "あ"]
    assert rows[0].body == IndexPointer(0x10, 0x20)
    assert rows[0].title == IndexPointer(3, 4)
    assert rows[1].body == IndexPointer(0x11, 0x30)


def test_parse_kw_leaf_page_group_and_body_targets() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (3).to_bytes(2, "big")
    page[4:12] = bytes.fromhex("8002000000022422")
    page[12:18] = bytes.fromhex("000000030004")
    page[18:25] = bytes.fromhex("c0000000100020")
    page[25:32] = bytes.fromhex("b0000000110030")

    rows, current_key, current_title, hint, groups, unknown = parse_kw_leaf_page(
        "KWINDEX.DIC",
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
    assert groups == 1
    assert hint == 2
    assert current_key == "あ"
    assert current_title == IndexPointer(3, 4)
    assert [row.key for row in rows] == ["あ", "あ"]
    assert rows[0].body == IndexPointer(0x10, 0x20)
    assert rows[0].title == IndexPointer(3, 4)
    assert rows[1].body == IndexPointer(0x11, 0x30)


def test_parse_kw_leaf_page_direct_row() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:8] = bytes.fromhex("00022422")
    page[8:20] = bytes.fromhex("000000010002000000030004")

    rows, current_key, current_title, hint, groups, unknown = parse_kw_leaf_page(
        "KWINDEX.DIC",
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


def test_parse_multi_leaf_page_group_and_body_title_targets() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (3).to_bytes(2, "big")
    page[4:12] = bytes.fromhex("8002000000022422")
    page[12:25] = bytes.fromhex("c0000000100020000000030004")
    page[25:38] = bytes.fromhex("c0000000110030000000040005")

    rows, current_key, hint, groups, unknown = parse_multi_leaf_page(
        "MUL1_1_2.DIC",
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
    assert hint == 2
    assert current_key == "あ"
    assert [row.key for row in rows] == ["あ", "あ"]
    assert rows[0].body == IndexPointer(0x10, 0x20)
    assert rows[0].title == IndexPointer(3, 4)
    assert rows[1].body == IndexPointer(0x11, 0x30)
    assert rows[1].title == IndexPointer(4, 5)


def test_parse_multi_leaf_page_direct_row() -> None:
    page = bytearray(2048)
    page[0:2] = bytes.fromhex("d000")
    page[2:4] = (1).to_bytes(2, "big")
    page[4:8] = bytes.fromhex("00022422")
    page[8:20] = bytes.fromhex("000000010002000000030004")

    rows, current_key, hint, groups, unknown = parse_multi_leaf_page(
        "MUL1_1_2.DIC",
        bytes(page),
        1,
        100,
        current_key=None,
        current_count_hint=None,
        gaiji_map={},
        gaiji="drop",
    )

    assert unknown == 0
    assert groups == 0
    assert hint is None
    assert current_key is None
    assert rows[0].key == "あ"
    assert rows[0].body == IndexPointer(1, 2)
    assert rows[0].title == IndexPointer(3, 4)


def test_parse_multi_descriptor_records_and_component_refs() -> None:
    data = bytearray(2048)
    data[0:2] = (1).to_bytes(2, "big")
    data[0x10:0x12] = bytes.fromhex("0200")
    data[0x12:0x18] = bytes.fromhex("4331386c2331")  # 単語1
    data[0x30:0x40] = bytes.fromhex("0d000000542800001026010000000000")
    data[0x40:0x50] = bytes.fromhex("a10000001bc30000021e010000000003")

    descriptor = parse_multi_descriptor(bytes(data), gaiji="drop")

    assert descriptor.record_count == 1
    assert descriptor.trailing_nonzero_bytes == 0
    assert descriptor.records[0].component_count == 2
    assert descriptor.records[0].subtype == 0
    assert descriptor.records[0].label == "単語1"
    assert descriptor.records[0].refs[0].component_type == 0x0D
    assert descriptor.records[0].refs[0].start_block == 0x5428
    assert descriptor.records[0].refs[0].block_count == 0x1026
    assert descriptor.records[0].refs[1].component_type == 0xA1
    assert descriptor.records[0].refs[1].flags.hex() == "010000000003"
