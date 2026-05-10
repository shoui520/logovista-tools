from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


LVCORE_SRC = Path(__file__).resolve().parents[1] / "src" / "lvcore-experimental"
sys.path.insert(0, str(LVCORE_SRC))

from lvcore import PackageFamily, detect_family, open_package  # noqa: E402
from lvcore.gaiji import ga16_glyph_size, parse_ga16  # noqa: E402
from lvcore.ssed import BLOCK_SIZE, CHUNK_SIZE  # noqa: E402


def be16(value: int) -> bytes:
    return value.to_bytes(2, "big")


def be32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def jis_pair(ch: str) -> bytes:
    encoded = ch.encode("iso2022_jp")
    return encoded.removeprefix(b"\x1b$B").removesuffix(b"\x1b(B")


def body_text(text: str) -> bytes:
    out = bytearray(b"\x1f\x04")
    for ch in text:
        if ch == " ":
            out.extend(jis_pair("\u3000"))
        elif 0x21 <= ord(ch) <= 0x7E:
            out.extend(jis_pair(chr(ord(ch) + 0xFEE0)))
        else:
            out.extend(jis_pair(ch))
    out.extend(b"\x1f\x05")
    return bytes(out)


def index_key(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        if ch == " ":
            out.extend(jis_pair("\u3000"))
        elif 0x21 <= ord(ch) <= 0x7E:
            out.extend(jis_pair(chr(ord(ch) + 0xFEE0)))
        else:
            out.extend(jis_pair(ch))
    return bytes(out)


def pad_block(data: bytes) -> bytes:
    rem = len(data) % BLOCK_SIZE
    return data if rem == 0 else data + bytes(BLOCK_SIZE - rem)


def literal_sseddata(expanded: bytes, *, start_block: int, kind: int) -> bytes:
    expanded = pad_block(expanded)
    chunks = [expanded[pos : pos + CHUNK_SIZE] for pos in range(0, len(expanded), CHUNK_SIZE)]
    header_len = 64 + 4 * len(chunks)
    payloads = []
    cursor = header_len
    offsets = []
    for chunk in chunks:
        payload = bytearray(b"\x00\x00")
        payload.extend(be16(len(chunk)))
        payload.append(0)
        for value in chunk:
            payload.extend((0, 0, value))
        offsets.append(cursor)
        payloads.append(bytes(payload))
        cursor += len(payload)
    header = bytearray(64)
    header[:8] = b"SSEDDATA"
    header[0x0F] = kind
    header[0x16:0x18] = be16(len(chunks))
    header[0x18:0x1C] = be32(start_block)
    header[0x1C:0x20] = be32(start_block + len(expanded) // BLOCK_SIZE - 1)
    out = bytearray(header)
    for offset in offsets:
        out.extend(be32(offset))
    for payload in payloads:
        out.extend(payload)
    return bytes(out)


def ssedinfo(title: str, components: list[tuple[str, int, int, int, bytes]]) -> bytes:
    title_bytes = title.encode("cp932")
    header = bytearray(0x80)
    header[:8] = b"SSEDINFO"
    header[12] = len(title_bytes)
    header[13 : 13 + len(title_bytes)] = title_bytes
    header[0x4D] = len(components)
    out = bytearray(header)
    for name, typ, start, end, data in components:
        rec = bytearray(0x30)
        raw_name = name.encode("ascii")
        rec[3] = typ
        rec[4:8] = be32(start)
        rec[8:12] = be32(end)
        rec[12:16] = data
        rec[16] = len(raw_name)
        rec[17 : 17 + len(raw_name)] = raw_name
        out.extend(rec)
    return bytes(out)


def make_synthetic_package(root: Path) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4

    entry = b"\x1f\x09\x00\x01\x1f\x41\x00\x00" + body_text("alpha") + b"\x1f\x61\x1f\x0a" + body_text("first entry") + b"\x1f\x0a"
    title = body_text("alpha") + b"\x1f\x0a"
    key = index_key("alpha")
    row = bytes([len(key)]) + key + be32(honmon_start) + be16(0) + be32(title_start) + be16(0)
    index = be16(0x8000) + be16(1) + row

    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "TEST.IDX").write_bytes(ssedinfo("Synthetic", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(entry, start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))


def test_lvcore_detects_and_reads_synthetic_ssed(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)

    info = detect_family(tmp_path)
    assert info.family == PackageFamily.SSED
    assert info.title == "Synthetic"

    package = open_package(tmp_path)
    assert package.title == "Synthetic"
    assert [component.name for component in package.components] == ["HONMON.DIC", "FHTITLE.DIC", "FHINDEX.DIC"]

    entries = list(package.iter_entries())
    assert len(entries) == 1
    assert entries[0].headword == "alpha"
    assert "first entry" in entries[0].text
    assert package.titles() == ["alpha"]
    assert package.search_index("alpha")[0]["component"] == "FHINDEX.DIC"
    assert package.entry_at(package.search_entries("alpha")[0].address).headword == "alpha"


def test_lvcore_cli_outputs_json(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "lvcore", "info", str(tmp_path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(result.stdout)
    assert data["package"]["family"] == "ssed"
    assert data["package"]["title"] == "Synthetic"


def test_lvcore_detects_deferred_families(tmp_path: Path) -> None:
    lved = tmp_path / "_DCT_FAKE_LVED"
    lved.mkdir()
    (lved / "main.data").write_bytes(b"not real")
    assert detect_family(lved).family == PackageFamily.LVED

    multi = tmp_path / "_DCT_FAKE_MULTI"
    multi.mkdir()
    (multi / "vlpljbl.exe").write_bytes(b"")
    (multi / "blvbat").write_bytes(b"")
    assert detect_family(multi).family == PackageFamily.LVLMULTI

    hybrid = tmp_path / "_DCT_FAKE_MULTI_WITH_IDX"
    hybrid.mkdir()
    (hybrid / "vlpljbl.exe").write_bytes(b"")
    (hybrid / "blvbat").write_bytes(b"")
    (hybrid / "FAKE.IDX").write_bytes(
        ssedinfo(
            "Hybrid",
            [("HONMON.DIC", 0x00, 2, 2, b"\x02\x00\x00\x00")],
        )
    )
    detected = detect_family(hybrid)
    assert detected.family == PackageFamily.LVLMULTI
    assert detected.title == "Hybrid"


def test_lvcore_parses_ga16_resources(tmp_path: Path) -> None:
    path = tmp_path / "GA16FULL"
    header = bytearray(BLOCK_SIZE)
    header[8] = 16
    header[9] = 16
    header[10:12] = bytes.fromhex("b121")
    header[12:14] = be16(1)
    glyph = bytes([0x80, 0x01] * 16)
    path.write_bytes(bytes(header) + glyph)

    resource = parse_ga16(path)
    assert resource is not None
    assert resource.width == 16
    assert resource.height == 16
    assert resource.glyph_bytes == ga16_glyph_size(16, 16)
    assert resource.glyph(0xB121) == glyph
