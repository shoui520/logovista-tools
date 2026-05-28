from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from logovista_tools.entries import DictionarySource
from logovista_tools.hcrender import HcRenderOptions, render_hc_body
from logovista_tools.lvcrypto import logofont_cipher_key_iv
from logovista_tools.pcmdata import PcmPointer, extract_pcmdata_for_source
from logovista_tools.pcmu import load_pcmu_index, read_pcmu_record
from logovista_tools.sounddata import (
    iter_sounddata_references,
    load_sounddata_index,
    parse_sounddata_marker_at,
    parse_sounddata_record,
    parse_wavefile_map,
    read_sounddata_record,
)
from logovista_tools.writer import SsedInfoComponent, encode_sseddata_literal, encode_ssedinfo


def jis_hex(value: str) -> bytes:
    return b"".join(bytes((0x23, ord(ch))) for ch in value)


def sound_marker(value: int) -> bytes:
    return b"\xa4\x27" + jis_hex(f"{value:08X}") + b"\xa4\x28"


def wave_bytes(payload: bytes = b"\x80\x81\x82\x83") -> bytes:
    fmt_payload = (
        (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (11025).to_bytes(4, "little")
        + (11025).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
    )
    chunks = b"fmt " + len(fmt_payload).to_bytes(4, "little") + fmt_payload
    chunks += b"data" + len(payload).to_bytes(4, "little") + payload
    return b"RIFF" + (4 + len(chunks)).to_bytes(4, "little") + b"WAVE" + chunks


def encrypt_logofont(data: bytes) -> bytes:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key, iv = logofont_cipher_key_iv()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def test_wavefile_map_indexes_concatenated_sounddata(tmp_path: Path) -> None:
    sound_dir = tmp_path / "Sound"
    sound_dir.mkdir()
    first = wave_bytes(b"\x80\x81")
    second = wave_bytes(b"\x82\x83\x84")
    (sound_dir / "SoundData").write_bytes(first + second)
    (sound_dir / "WaveFile.map").write_text(
        f"0000000000000000:{len(first):x}\t1\r\n"
        f"{len(first):016x}:{len(second):x}\t2\r\n",
        encoding="ascii",
    )
    (tmp_path / "TEST.IDX").write_bytes(b"")

    rows = parse_wavefile_map(sound_dir / "WaveFile.map")
    assert rows[0].sound_id == 1
    assert rows[1].offset == len(first)

    index = load_sounddata_index(tmp_path / "TEST.IDX")
    assert index is not None
    decoded = read_sounddata_record(index, 2)
    assert decoded is not None
    map_record, raw, record, chunks = decoded
    assert map_record.length == len(second)
    assert raw == second
    assert record.codec == "pcm"
    assert record.sample_rate == 11025
    assert record.bits_per_sample == 8
    assert [chunk.tag for chunk in chunks] == ["fmt ", "data"]


def test_wavefile_map_repairs_truncated_negative_sound_ids(tmp_path: Path) -> None:
    path = tmp_path / "WaveFile.map"
    path.write_text(
        "0000000000000000:10\t32767\r\n"
        "0000000000000010:10\t-3276\r\n"
        "0000000000000020:10\t-3276\r\n"
        "0000000000000030:10\t65537\r\n",
        encoding="ascii",
    )

    rows = parse_wavefile_map(path)

    assert [row.sound_id for row in rows] == [32767, 32768, 32769, 65537]
    assert rows[1].raw_sound_id == -3276
    assert rows[1].as_dict()["raw_sound_id"] == -3276


def test_sounddata_honmon_marker_decodes_hex_sound_id() -> None:
    marker = sound_marker(0x1CDF)

    parsed = parse_sounddata_marker_at(marker, 0)

    assert parsed is not None
    reference, end = parsed
    assert end == len(marker)
    assert reference.sound_id == 7391
    assert reference.code_hex == "00001CDF"
    assert list(iter_sounddata_references(b"xx" + marker + b"yy"))[0].position == 2


def test_hc0089_sounddata_marker_renders_audio_button() -> None:
    rendered = render_hc_body(
        sound_marker(0x1CDF),
        HcRenderOptions(
            renderer_code="0089",
            image_sources={"sound": "Templates/sound.png"},
            sounddata_ids=frozenset({0x1CDF}),
        ),
    )

    assert "00001CDF" not in rendered.html
    assert 'data-gaiji-code="a427"' not in rendered.html
    assert 'data-gaiji-code="a428"' not in rendered.html
    assert 'data-lv-resource="sounddata:7391"' in rendered.html
    assert '<img src="Templates/sound.png" class="img_mark2">' in rendered.html
    assert rendered.audio[0]["target"]["component"] == "SoundData"
    assert rendered.audio[0]["status"] == "resolved_sounddata_id"


def test_pcmdata_command_falls_back_to_sounddata_store(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    sound_dir = package / "Sound"
    sound_dir.mkdir(parents=True)
    raw = wave_bytes()
    (sound_dir / "SoundData").write_bytes(raw)
    (sound_dir / "WaveFile.map").write_text(f"0000000000000000:{len(raw):x}\t7391\r\n", encoding="ascii")
    (package / "HONMON.DIC").write_bytes(encode_sseddata_literal(sound_marker(7391), start_block=2))
    (package / "TEST.IDX").write_bytes(
        encode_ssedinfo(
            "Test",
            [SsedInfoComponent("HONMON.DIC", 0x00, 2, 2, b"\x00\x00\x00\x02")],
        )
    )
    source = DictionarySource(
        dict_id="TEST",
        idx=package / "TEST.IDX",
        title="Test",
        honmon=package / "HONMON.DIC",
        honmon_start_block=2,
        gaiji_map={},
    )
    args = SimpleNamespace(limit=None, write_audio=True, include_unreferenced=True, verbose=False)

    summary = extract_pcmdata_for_source(source, tmp_path / "out", args)

    assert summary["container_kind"] == "sounddata"
    assert summary["audio_references"] == 1
    assert summary["valid_referenced_records"] == 1
    assert summary["wavefile_map_records"] == 1
    written = sorted((tmp_path / "out" / "TEST" / "audio").glob("*.wav"))
    assert len(written) == 1
    assert written[0].read_bytes() == raw


def test_pcmu_wavefile_map_resolves_logofont_encrypted_mp3_by_pcm_pointer(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_TEST"
    pcmu = tmp_path / "_DCT_TEST_PCM_U"
    package.mkdir()
    pcmu.mkdir()
    mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90d" + b"\x55" * 32
    (pcmu / "00000001").write_bytes(encrypt_logofont(mp3))
    (pcmu / "WaveFile.map").write_text("00000001\t100\r\n", encoding="ascii")
    (package / "TEST.IDX").write_bytes(b"")

    index = load_pcmu_index(package / "TEST.IDX")
    assert index is not None
    pointer = PcmPointer(
        payload=b"",
        kind=0x0201,
        flags=0,
        start_block=100,
        start_offset=0,
        end_block=100,
        end_offset=0,
    )
    decoded = read_pcmu_record(index, pointer)

    assert decoded is not None
    map_record, raw, record, _chunks = decoded
    assert map_record.stem == "00000001"
    assert raw.startswith(b"ID3")
    assert record.codec == "mp3"
