from __future__ import annotations

import ast
import json
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


LVCORE_SRC = Path(__file__).resolve().parents[1] / "src" / "lvcore-experimental"
sys.path.insert(0, str(LVCORE_SRC))

from lvcore import Address, Diagnostic, DiagnosticArea, Location, PackageFamily, SearchHit, SearchProfile, SearchResults, Severity, Span, SsedBodySourceKind, detect_family, normalize_query, open_package  # noqa: E402
from lvcore.body_source import SidecarRole, classify_sqlite_sidecar_role, quote_sql_identifier, sqlite_columns  # noqa: E402
from lvcore.document import BlockKind, BlockNode, EntryDocument, InlineKind, InlineNode, LinkTargetKind, ResourceKind, ResourceRef, ResourceStatus, build_entry_document  # noqa: E402
from lvcore.errors import FormatError  # noqa: E402
from lvcore.gaiji import ga16_glyph_size, gaiji_grid_code_for_index, parse_ga16  # noqa: E402
from lvcore.index import parse_index  # noqa: E402
from lvcore.model import Component, ComponentRole, Entry  # noqa: E402
from lvcore.opcodes import OpcodeCategory, behavior_for  # noqa: E402
from lvcore.render import GaijiPolicy, HtmlProfile, render_html, render_text  # noqa: E402
from lvcore.ssed import BLOCK_SIZE, CHUNK_SIZE, expand_sseddata, parse_catalog  # noqa: E402
from lvcore.text import decode_text_stream  # noqa: E402


def test_lvcore_source_stays_independent_from_toolkit() -> None:
    forbidden_module = "logovista" "_tools"
    offenders: list[str] = []
    for path in sorted((LVCORE_SRC / "lvcore").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == forbidden_module or alias.name.startswith(f"{forbidden_module}."):
                        offenders.append(f"{path}:{node.lineno}: import {alias.name}")
                    if alias.name == "subprocess":
                        offenders.append(f"{path}:{node.lineno}: import subprocess")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == forbidden_module or module.startswith(f"{forbidden_module}."):
                    offenders.append(f"{path}:{node.lineno}: from {module} import ...")
                if module == "subprocess":
                    offenders.append(f"{path}:{node.lineno}: from subprocess import ...")

    assert offenders == []


def test_lvcore_sidecar_role_classification_is_structural() -> None:
    assert classify_sqlite_sidecar_role("t_contents", ("t_contents",)) == SidecarRole.BODY_CRITICAL
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("t_media",)) == SidecarRole.MEDIA_RESOURCE
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("D_Example", "D_Idiom")) == SidecarRole.EXAMPLES_IDIOMS
    assert (
        classify_sqlite_sidecar_role(
            "sqlite_unmapped",
            ("Supplemental",),
            {"Supplemental": ["No", "Block", "Offset", "Title"]},
        )
        == SidecarRole.LINK_REFERENCE
    )
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("t_Search_01", "t_zenbun")) == SidecarRole.SEARCH
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("t_all", "t_bushu", "t_jukugo")) == SidecarRole.KANJI_SUPPORT
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("D_InternationalChronology",)) == SidecarRole.ANCILLARY
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("t_data",), {"t_data": ["index", "data"]}) == SidecarRole.ANCILLARY
    assert classify_sqlite_sidecar_role("sqlite_unmapped", ("opaque",)) == SidecarRole.UNKNOWN


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


def ga16_file(*, width: int, height: int, start_code: int, glyphs: list[bytes]) -> bytes:
    header = bytearray(BLOCK_SIZE)
    header[8] = width
    header[9] = height
    header[10:12] = be16(start_code)
    header[12:14] = be16(len(glyphs))
    return bytes(header) + b"".join(glyphs)


def uni_file(records: list[tuple[str, str, str]], *, section: str = "half") -> bytes:
    def record(code: str, display: str, fallback: str) -> bytes:
        out = bytearray(bytes.fromhex(code))
        out.extend(b"\x00\x00")
        for text in (display, fallback, ""):
            units = text.encode("utf-16-be")[:4].ljust(4, b"\x00")
            out.extend(units)
        return bytes(out[:16]).ljust(16, b"\x00")

    half = records if section == "half" else []
    full = records if section == "full" else []
    out = bytearray(b"Ver2  ")
    out.extend(be32(len(half)))
    for item in half:
        out.extend(record(*item))
    out.extend(be32(len(full)))
    for item in full:
        out.extend(record(*item))
    return bytes(out)


def make_gaiji_package(
    root: Path,
    *,
    raw_code: bytes,
    uni_records: list[tuple[str, str, str]] | None = None,
    ga16_name: str | None = None,
    ga16_payload: bytes | None = None,
    plist_payload: dict | None = None,
    image_payload: tuple[str, bytes] | None = None,
) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    if ga16_name and ga16_payload is not None:
        components.append((ga16_name, 0xF0, 0, 0, b"\x00\x00\x00\x00"))
    entry = b"\x1f\x09\x00\x01" + body_text("gaiji") + b"\x1f\x0a" + raw_code + b"\x1f\x0a"
    title = body_text("gaiji") + b"\x1f\x0a"
    key = index_key("gaiji")
    row = bytes([len(key)]) + key + be32(honmon_start) + be16(0) + be32(title_start) + be16(0)
    index = be16(0x8000) + be16(1) + row
    root.mkdir(exist_ok=True)
    (root / "GAIJI.IDX").write_bytes(ssedinfo("Gaiji", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(entry, start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))
    if uni_records is not None:
        (root / "GAIJI.uni").write_bytes(uni_file(uni_records))
    if ga16_name and ga16_payload is not None:
        (root / ga16_name).write_bytes(ga16_payload)
    if plist_payload is not None:
        with (root / "Gaiji.plist").open("wb") as fh:
            plistlib.dump(plist_payload, fh)
    if image_payload is not None:
        rel, payload = image_payload
        image_path = root / rel
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(payload)


def make_media_resource_package(root: Path) -> None:
    honmon_start = 2
    media_start = 3
    title_start = 4
    index_start = 5
    media_payload = b"\x00" * 12 + be32(media_start) + be16(0)
    entry = body_text("media") + b"\x1f\x0a" + b"\x1f\x4d" + media_payload + body_text("caption") + b"\x1f\x6d\x1f\x0a"
    title = body_text("media") + b"\x1f\x0a"
    key = index_key("media")
    row = bytes([len(key)]) + key + be32(honmon_start) + be16(0) + be32(title_start) + be16(0)
    index = be16(0x8000) + be16(1) + row
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("COLSCR.DIC", 0xD2, media_start, media_start, b"\x00\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "MEDIA.IDX").write_bytes(ssedinfo("Media", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(entry, start_block=honmon_start, kind=0))
    image_payload = b"\x89PNG\r\n\x1a\n"
    media_record = b"data" + len(image_payload).to_bytes(4, "little") + image_payload
    (root / "COLSCR.DIC").write_bytes(literal_sseddata(media_record, start_block=media_start, kind=0xD2))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))


def make_pcmdata_resource_package(root: Path) -> None:
    honmon_start = 2
    pcm_start = 3
    title_start = 4
    index_start = 5
    audio_payload = b"ID3abc123"
    pcm_payload = b"\x00\x01\x00\x00" + be32(pcm_start) + be16(0) + be32(pcm_start) + be16(len(audio_payload))
    entry = body_text("audio") + b"\x1f\x0a" + b"\x1f\x4a" + pcm_payload + body_text("play") + b"\x1f\x6a\x1f\x0a"
    title = body_text("audio") + b"\x1f\x0a"
    key = index_key("audio")
    row = bytes([len(key)]) + key + be32(honmon_start) + be16(0) + be32(title_start) + be16(0)
    index = be16(0x8000) + be16(1) + row
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("PCMDATA.DIC", 0xD8, pcm_start, pcm_start, b"\x00\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "PCM.IDX").write_bytes(ssedinfo("PCM", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(entry, start_block=honmon_start, kind=0))
    (root / "PCMDATA.DIC").write_bytes(literal_sseddata(audio_payload, start_block=pcm_start, kind=0xD8))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))


def simple_index(rows: list[tuple[str, int, int, int, int]]) -> bytes:
    encoded = bytearray(be16(0x8000) + be16(len(rows)))
    for key, body_block, body_offset, title_block, title_offset in rows:
        raw_key = index_key(key)
        encoded.extend(bytes([len(raw_key)]))
        encoded.extend(raw_key)
        encoded.extend(be32(body_block))
        encoded.extend(be16(body_offset))
        encoded.extend(be32(title_block))
        encoded.extend(be16(title_offset))
    return bytes(encoded)


def leaf_page(records: list[bytes], *, word: int = 0x8000) -> bytes:
    return pad_block(be16(word) + be16(len(records)) + b"".join(records))


def direct_index_record(key: str, body_block: int = 10, body_offset: int = 0, title_block: int = 20, title_offset: int = 0) -> bytes:
    raw = index_key(key)
    return bytes([0x00, len(raw)]) + raw + be32(body_block) + be16(body_offset) + be32(title_block) + be16(title_offset)


def tagged_direct_body_only_record(key: str, body_block: int = 10, body_offset: int = 0) -> bytes:
    raw = index_key(key)
    return bytes([0x00, len(raw)]) + raw + be32(body_block) + be16(body_offset)


def tagged_group_record(key: str, count: int = 1) -> bytes:
    raw = index_key(key)
    return bytes([0x80, len(raw)]) + be16(count) + raw


def tagged_target_record(target: str, body_block: int = 10, body_offset: int = 0, title_block: int = 20, title_offset: int = 0) -> bytes:
    raw = index_key(target)
    return bytes([0xC0, len(raw)]) + raw + be32(body_block) + be16(body_offset) + be32(title_block) + be16(title_offset)


def tagged_target_body_only_record(target: str, body_block: int = 10, body_offset: int = 0) -> bytes:
    raw = index_key(target)
    return bytes([0xC0, len(raw)]) + raw + be32(body_block) + be16(body_offset)


def title_group_record(key: str, title_block: int = 20, title_offset: int = 0, count: int = 1) -> bytes:
    raw = index_key(key)
    return bytes([0x80, len(raw)]) + be32(count) + raw + be32(title_block) + be16(title_offset)


def compact_body_target_record(tag: int = 0xC0, body_block: int = 10, body_offset: int = 0) -> bytes:
    return bytes([tag]) + be32(body_block) + be16(body_offset)


def multi_group_record(key: str, count: int = 1) -> bytes:
    raw = index_key(key)
    return bytes([0x80, len(raw)]) + be32(count) + raw


def multi_target_record(body_block: int = 10, body_offset: int = 0, title_block: int = 20, title_offset: int = 0) -> bytes:
    return bytes([0xC0]) + be32(body_block) + be16(body_offset) + be32(title_block) + be16(title_offset)


def tagged_index(group_key: str, rows: list[tuple[str, int, int, int, int]]) -> bytes:
    encoded = bytearray(be16(0x8000) + be16(1 + len(rows)))
    raw_group = index_key(group_key)
    encoded.extend((0x80, len(raw_group)))
    encoded.extend(be16(len(rows)))
    encoded.extend(raw_group)
    for target, body_block, body_offset, title_block, title_offset in rows:
        raw_target = index_key(target)
        encoded.extend((0xC0, len(raw_target)))
        encoded.extend(raw_target)
        encoded.extend(be32(body_block))
        encoded.extend(be16(body_offset))
        encoded.extend(be32(title_block))
        encoded.extend(be16(title_offset))
    return bytes(encoded)


def make_reader_workflow_package(root: Path) -> None:
    honmon_start = 2
    fhtitle_start = 3
    bhtitle_start = 4
    fhindex_start = 5
    bhindex_start = 6

    entry_payloads = [
        body_text("alpha") + b"\x1f\x0a" + body_text("first entry") + b"\x1f\x0a",
        body_text("alpine") + b"\x1f\x0a" + body_text("second entry") + b"\x1f\x0a",
        body_text("beta") + b"\x1f\x0a" + body_text("third entry") + b"\x1f\x0a",
    ]
    body_offsets: list[int] = []
    honmon = bytearray()
    for payload in entry_payloads:
        body_offsets.append(len(honmon))
        honmon.extend(payload)

    titles = [body_text("alpha") + b"\x1f\x0a", body_text("alpine") + b"\x1f\x0a", body_text("beta") + b"\x1f\x0a"]
    title_offsets: list[int] = []
    fhtitle = bytearray()
    for title in titles:
        title_offsets.append(len(fhtitle))
        fhtitle.extend(title)
    bhtitle = bytes(fhtitle)

    forward_rows = [
        ("alpha", honmon_start, body_offsets[0], fhtitle_start, title_offsets[0]),
        ("alphabet", honmon_start, body_offsets[0], fhtitle_start, title_offsets[0]),
        ("alpine", honmon_start, body_offsets[1], fhtitle_start, title_offsets[1]),
        ("beta", honmon_start, body_offsets[2], fhtitle_start, title_offsets[2]),
    ]
    backward_rows = [
        ("ahpla", honmon_start, body_offsets[0], bhtitle_start, title_offsets[0]),
        ("enipla", honmon_start, body_offsets[1], bhtitle_start, title_offsets[1]),
        ("ateb", honmon_start, body_offsets[2], bhtitle_start, title_offsets[2]),
    ]

    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, fhtitle_start, fhtitle_start, b"\x01\x00\x00\x00"),
        ("BHTITLE.DIC", 0x07, bhtitle_start, bhtitle_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, fhindex_start, fhindex_start, b"\x02\x01\x55\x40"),
        ("BHINDEX.DIC", 0x71, bhindex_start, bhindex_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "READ.IDX").write_bytes(ssedinfo("Reader Workflow", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(bytes(honmon), start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(bytes(fhtitle), start_block=fhtitle_start, kind=5))
    (root / "BHTITLE.DIC").write_bytes(literal_sseddata(bhtitle, start_block=bhtitle_start, kind=7))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(simple_index(forward_rows), start_block=fhindex_start, kind=0x91))
    (root / "BHINDEX.DIC").write_bytes(literal_sseddata(simple_index(backward_rows), start_block=bhindex_start, kind=0x71))


def make_backward_only_package(root: Path) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    body = body_text("alpha") + b"\x1f\x0a" + body_text("backward only body") + b"\x1f\x0a"
    title = body_text("alpha") + b"\x1f\x0a"
    rows = [("ahpla", honmon_start, 0, title_start, 0)]
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("BHTITLE.DIC", 0x07, title_start, title_start, b"\x01\x00\x00\x00"),
        ("BHINDEX.DIC", 0x71, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "BACK.IDX").write_bytes(ssedinfo("Backward Only", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    (root / "BHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=7))
    (root / "BHINDEX.DIC").write_bytes(literal_sseddata(simple_index(rows), start_block=index_start, kind=0x71))


def make_body_pointer_title_package(root: Path, *, with_unused_title: bool = False) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4 if with_unused_title else 3
    body = body_text("fallback") + b"\x1f\x0a" + body_text("body title pointer") + b"\x1f\x0a"
    row = ("fallback", honmon_start, 0, honmon_start, 0)
    components = [("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00")]
    if with_unused_title:
        components.append(("KWTITLE.DIC", 0x03, title_start, title_start, b"\x01\x00\x00\x00"))
    components.append(("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"))

    root.mkdir(exist_ok=True)
    (root / "BODYTITLE.IDX").write_bytes(ssedinfo("Body Title Pointer", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    if with_unused_title:
        (root / "KWTITLE.DIC").write_bytes(literal_sseddata(body_text("unused") + b"\x1f\x0a", start_block=title_start, kind=3))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(simple_index([row]), start_block=index_start, kind=0x91))


def make_special_index_package(root: Path, *, component_type: int, grouped: bool) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    body = body_text("special") + b"\x1f\x0a" + body_text("special body") + b"\x1f\x0a"
    title = body_text("special title") + b"\x1f\x0a"
    if component_type == 0x80:
        title_name, title_type, index_name = "KWTITLE.DIC", 0x03, "KWINDEX.DIC"
        records = [title_group_record("special", title_start, 0), compact_body_target_record(0xB0, honmon_start, 0)] if grouped else [direct_index_record("special", honmon_start, 0, title_start, 0)]
    elif component_type == 0x81:
        title_name, title_type, index_name = "CRTITLE.DIC", 0x0A, "CRINDEX.DIC"
        records = [title_group_record("special", title_start, 0), compact_body_target_record(0xC0, honmon_start, 0)] if grouped else [direct_index_record("special", honmon_start, 0, title_start, 0)]
    elif component_type == 0xA1:
        title_name, title_type, index_name = "MUL1_1_1.DIC", 0x0D, "MUL1_1_2.DIC"
        records = [multi_group_record("special"), multi_target_record(honmon_start, 0, title_start, 0)] if grouped else [direct_index_record("special", honmon_start, 0, title_start, 0)]
    else:  # pragma: no cover - helper misuse
        raise AssertionError(component_type)
    index = leaf_page(records)
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        (title_name, title_type, title_start, title_start, b"\x01\x00\x00\x00"),
        (index_name, component_type, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "SPECIAL.IDX").write_bytes(ssedinfo("Special Index", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    (root / title_name).write_bytes(literal_sseddata(title, start_block=title_start, kind=title_type))
    (root / index_name).write_bytes(literal_sseddata(index, start_block=index_start, kind=component_type))


def make_text_like_index_outlier_package(root: Path) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    outlier_start = 5
    body = body_text("alpha") + b"\x1f\x0a" + body_text("first entry") + b"\x1f\x0a"
    title = body_text("alpha") + b"\x1f\x0a"
    key = index_key("alpha")
    row = bytes([len(key)]) + key + be32(honmon_start) + be16(0) + be32(title_start) + be16(0)
    index = be16(0x8000) + be16(1) + row
    outlier = b"\x1f\x02\x1f\x0a" + body_text("navigation text") + b"\x1f\x0a"
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
        ("INDEX.DIC", 0x27, outlier_start, outlier_start, b"\x00\x00\x00\x00"),
    ]
    root.mkdir(exist_ok=True)
    (root / "OUTLIER.IDX").write_bytes(ssedinfo("Text-like outlier", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))
    (root / "INDEX.DIC").write_bytes(literal_sseddata(outlier, start_block=outlier_start, kind=0x27))


def make_tagged_target_package(root: Path) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    body = body_text("primary") + b"\x1f\x0a" + body_text("tagged body") + b"\x1f\x0a"
    title = body_text("primary") + b"\x1f\x0a"
    rows = [
        ("alias", honmon_start, 0, title_start, 0),
        ("alternate", honmon_start, 0, title_start, 0),
    ]
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x90, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "TAGGED.IDX").write_bytes(ssedinfo("Tagged", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(tagged_index("primary", rows), start_block=index_start, kind=0x90))


def make_dense_anchor_package(root: Path, *, with_sidecar: bool = False) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    terms = ["alpha", "beta", "gamma", "delta"]
    honmon = bytearray()
    body_offsets: list[int] = []
    titles = bytearray()
    title_offsets: list[int] = []
    rows = []
    for index, term in enumerate(terms, start=1):
        anchor = f"{index:08d}"
        body_offsets.append(len(honmon))
        honmon.extend(b"\x1f\x09\x00\x01\x1f\x41\x01\x60" + body_text(anchor) + b"\x1f\x61\x1f\x0a")
        title_offsets.append(len(titles))
        titles.extend(body_text(term) + b"\x1f\x0a")
        rows.append((term, honmon_start, body_offsets[-1], title_start, title_offsets[-1]))

    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "DENSE.IDX").write_bytes(ssedinfo("Dense", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(bytes(honmon), start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(bytes(titles), start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(simple_index(rows), start_block=index_start, kind=0x91))
    if with_sidecar:
        con = sqlite3.connect(root / "body.db")
        try:
            con.execute("create table t_contents (f_DataId integer primary key, f_Title text, f_Html text, f_Plane text)")
            for index, term in enumerate(terms, start=1):
                con.execute(
                    "insert into t_contents values (?, ?, ?, ?)",
                    (index, term, f"<div>{term} sidecar html</div>", f"{term} sidecar body"),
                )
            con.commit()
        finally:
            con.close()


def add_example_idiom_sidecar(root: Path, *, block: int = 2, offset: int = 0) -> None:
    con = sqlite3.connect(root / "examples.db")
    try:
        con.execute("create table D_Example (No integer primary key, Block integer, Offset integer, Keyword text, Midashi text, Title text)")
        con.execute("create table D_Idiom (No integer primary key, Block integer, Offset integer, Keyword text, Midashi text, Title text)")
        con.execute("insert into D_Example values (?, ?, ?, ?, ?, ?)", (1, block, offset, "alpha", "alpha midashi", "alpha example title"))
        con.execute("insert into D_Idiom values (?, ?, ?, ?, ?, ?)", (1, block, offset, "alpha", "alpha idiom", "alpha idiom title"))
        con.commit()
    finally:
        con.close()


def add_media_sidecar(root: Path) -> None:
    con = sqlite3.connect(root / "vlpljblM")
    try:
        con.execute("create table t_media (f_name text primary key, f_blob blob)")
        con.execute("insert into t_media values (?, ?)", ("image-1", b"original bytes"))
        con.commit()
    finally:
        con.close()


def add_media_table_sidecar(root: Path) -> None:
    con = sqlite3.connect(root / "media.db")
    try:
        con.execute("create table media (No integer primary key, f_name text, f_type text, f_main blob)")
        con.execute("insert into media values (?, ?, ?, ?)", (1, "png-1", "image", b"\x89PNG\r\n\x1a\npayload"))
        con.commit()
    finally:
        con.close()


def add_link_reference_sidecar(root: Path, *, block: int = 2, offset: int = 0) -> None:
    con = sqlite3.connect(root / "links.db")
    try:
        con.execute("create table LINKS (No integer primary key, Block integer, Offset integer, Title text, Body text, TitleJIS text)")
        con.execute("insert into LINKS values (?, ?, ?, ?, ?, ?)", (1, block, offset, "related entry", "target body", "related jis"))
        con.commit()
    finally:
        con.close()


def add_search_metadata_sidecar(root: Path) -> None:
    con = sqlite3.connect(root / "search.db")
    try:
        con.execute("create table t_index (f_data_id integer, f_midashi_hyoki text, f_keyword text)")
        con.execute("insert into t_index values (?, ?, ?)", (1, "display label", "lookup label"))
        con.commit()
    finally:
        con.close()


def add_ancillary_t_data_sidecar(root: Path) -> None:
    con = sqlite3.connect(root / "ancillary.db")
    try:
        con.execute('create table t_data ("index" integer primary key, data blob)')
        con.execute('insert into t_data ("index", data) values (?, ?)', (1, b"\x00\x01"))
        con.commit()
    finally:
        con.close()


def make_bad_body_pointer_package(root: Path, *, component_end_block: int = 2, body_block: int = 9999) -> None:
    honmon_start = 2
    title_start = 4
    index_start = 5
    body = body_text("safe") + b"\x1f\x0a"
    title = body_text("bad pointer") + b"\x1f\x0a"
    index = simple_index([("bad", body_block, 0, title_start, 0)])
    components = [
        ("HONMON.DIC", 0x00, honmon_start, component_end_block, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    root.mkdir(exist_ok=True)
    (root / "BADPTR.IDX").write_bytes(ssedinfo("Bad Pointer", components))
    (root / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    (root / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (root / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))


def test_lvcore_malformed_ssedinfo_fails_with_clear_format_error(tmp_path: Path) -> None:
    (tmp_path / "BAD.IDX").write_bytes(b"SSEDINFO" + b"\x00" * 20)

    with pytest.raises(FormatError, match="could not parse SSEDINFO component table"):
        open_package(tmp_path)


def test_lvcore_invalid_component_ranges_do_not_expose_negative_block_counts(tmp_path: Path) -> None:
    component = Component("BAD.DIC", 0x00, 8, 7, role=ComponentRole.HONMON)
    assert component.block_count == 0
    assert component.to_dict()["blocks"] == 0

    (tmp_path / "BAD.IDX").write_bytes(ssedinfo("Bad Range", [("HONMON.DIC", 0x00, 8, 7, b"\x02\x00\x00\x00")]))
    with pytest.raises(FormatError, match="invalid block range"):
        parse_catalog(tmp_path / "BAD.IDX")


def test_lvcore_truncated_sseddata_component_fails_with_format_error(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    (tmp_path / "HONMON.DIC").write_bytes(b"SSEDDATA\x00")
    package = open_package(tmp_path)

    with pytest.raises(FormatError, match="SSEDDATA header truncated"):
        package.expanded("HONMON.DIC")


def test_lvcore_invalid_chunk_header_fails_with_format_error() -> None:
    data = bytearray(68)
    data[:8] = b"SSEDDATA"
    data[0x16:0x18] = be16(1)
    data[0x18:0x1C] = be32(2)
    data[0x1C:0x20] = be32(2)
    data[64:68] = be32(68)
    data.extend(b"\x00\x00\x00\x01\x00")

    with pytest.raises(FormatError, match="SSEDDATA chunk command stream truncated"):
        expand_sseddata(bytes(data))


def test_lvcore_invalid_index_row_is_counted_not_crashed() -> None:
    key = index_key("broken")
    page = be16(0x8000) + be16(1) + bytes([len(key)]) + key + b"\x00"
    parsed = parse_index(pad_block(page), start_block=4, component_type=0x91)

    assert parsed.rows == ()
    assert parsed.unknown_leaf_bytes == 1


def test_lvcore_bad_body_pointer_returns_diagnostic_placeholder(tmp_path: Path) -> None:
    make_bad_body_pointer_package(tmp_path)
    package = open_package(tmp_path)
    hit = package.search("bad", profile=SearchProfile.EXACT).hits[0]

    entry = package.entry_for_hit(hit)
    html = package.render_entry_html(entry)
    text = package.render_entry_text(entry)

    assert "Entry body is not yet supported" in text
    assert "9999" not in html
    assert "body pointer could not be resolved" in entry.diagnostics()[0].message
    assert entry.diagnostics()[0].code == "body_pointer_unresolved"


def test_lvcore_missing_honmon_is_named_component_integrity_issue(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    (tmp_path / "HONMON.DIC").unlink()
    package = open_package(tmp_path)

    source = package.body_source()
    report = package.validate(sample_entries=0, sample_search_hits=1)

    assert source.ssed_kind == SsedBodySourceKind.MISSING_BODY_COMPONENT
    assert source.support.value == "unsupported"
    assert report["body_source"]["ssed_kind"] == "missing_body_component"
    assert report["sidecar_resolution"]["missing_body_component"] == 1
    assert report["diagnostics"]["by_code"]["missing_body_component"] == 1
    assert report["diagnostics"]["by_severity"]["warning"] == 1
    assert report["ok"] is True


def test_lvcore_bad_component_size_reports_cleanly_during_validation(tmp_path: Path) -> None:
    make_bad_body_pointer_package(tmp_path, component_end_block=3, body_block=3)
    package = open_package(tmp_path)

    report = package.validate(sample_entries=0, sample_search_hits=1)

    assert report["sample_search_hits_dereferenced"] == 1
    assert report["sample_search_hits_rendered_html"] == 1
    assert report["diagnostics"]["by_code"]["body_pointer_unresolved"] == 1
    assert report["diagnostics"]["search_errors"] == []


def test_lvcore_truncated_honmon_entry_is_rendered_with_diagnostics_not_raw_leak() -> None:
    raw = body_text("visible") + b"\x1f\x44\x00"
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "visible", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "visible" in html
    assert "1f44" not in html.lower()
    assert "unclosed_style" in debug
    assert any(diagnostic.code == "unclosed_style" for diagnostic in document.diagnostics)


def test_lvcore_malformed_gaiji_reference_is_diagnostic_and_friendly_safe() -> None:
    decoded = decode_text_stream(b"\xa1")
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, 1, "HONMON.DIC"), "bad gaiji", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "a1" not in html.lower()
    assert "unknown_byte" in debug
    assert any(diagnostic.code == "unknown_byte" for diagnostic in document.diagnostics)


def test_lvcore_malformed_media_reference_is_diagnostic_and_friendly_safe() -> None:
    decoded = decode_text_stream(b"\x1f\x4d\x00")
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, 3, "HONMON.DIC"), "bad media", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "lvcore-resource://media-1" in html
    assert "1f4d" not in html.lower()
    assert "00" not in html
    assert "data-payload=\"00\"" in debug
    assert any(diagnostic.code == "unresolved_media_ref" for diagnostic in document.diagnostics)


def test_lvcore_invalid_sqlite_sidecar_is_deferred_without_anchor_leak(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    (tmp_path / "body.db").write_bytes(b"not a sqlite database")
    package = open_package(tmp_path)

    source = package.body_source()
    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    html = package.render_entry_html(entry)

    assert source.ssed_kind == SsedBodySourceKind.DENSE_ANCHOR_TABLE
    assert "00000001" not in html
    assert any(diagnostic.code == "unsupported_body_source" for diagnostic in entry.diagnostics())


def test_lvcore_invalid_text_encoding_bytes_are_recoverable_diagnostics() -> None:
    decoded = decode_text_stream(b"\x80\xff\x21")
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, 3, "HONMON.DIC"), "bad text", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)

    assert html
    assert decoded.unknown_bytes >= 2
    assert any(diagnostic.code == "unknown_byte" for diagnostic in document.diagnostics)


def test_lvcore_decode_telemetry_is_reported_once_by_validation(tmp_path: Path) -> None:
    honmon_start = 2
    title_start = 3
    index_start = 4
    body = b"\x1f\x09\x00\x01\x1f\x41\x00\x00" + body_text("bad") + b"\x1f\x61\x1f\x0a" + b"\x80\xff\x1f\x99" + body_text("visible") + b"\x1f\x0a"
    title = body_text("bad") + b"\x1f\x0a"
    index = simple_index([("bad", honmon_start, 0, title_start, 0)])
    components = [
        ("HONMON.DIC", 0x00, honmon_start, honmon_start, b"\x02\x00\x00\x00"),
        ("FHTITLE.DIC", 0x05, title_start, title_start, b"\x01\x00\x00\x00"),
        ("FHINDEX.DIC", 0x91, index_start, index_start, b"\x02\x01\x55\x40"),
    ]
    (tmp_path / "BADTEXT.IDX").write_bytes(ssedinfo("Bad Text", components))
    (tmp_path / "HONMON.DIC").write_bytes(literal_sseddata(body, start_block=honmon_start, kind=0))
    (tmp_path / "FHTITLE.DIC").write_bytes(literal_sseddata(title, start_block=title_start, kind=5))
    (tmp_path / "FHINDEX.DIC").write_bytes(literal_sseddata(index, start_block=index_start, kind=0x91))

    package = open_package(tmp_path)
    report = package.validate(sample_entries=1, sample_search_hits=1)
    entry = package.search_entries("bad", profile=SearchProfile.EXACT)[0]

    assert report["decode_telemetry"] == {"unknown_controls": 1, "unknown_bytes": 2}
    assert entry.decode_unknown_controls == 1
    assert entry.decode_unknown_bytes == 2
    assert entry.to_dict(debug=True)["decode_telemetry"] == {"unknown_controls": 1, "unknown_bytes": 2}


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
    assert package.search_index("alpha", profile=SearchProfile.FORWARD)[0]["component"] == "FHINDEX.DIC"
    assert package.search_index("alpha", profile=SearchProfile.BACKWARD) == []
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


def test_lvcore_search_models_and_native_profiles(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)

    exact = package.search("al-pha", profile=SearchProfile.EXACT)
    assert isinstance(exact, SearchResults)
    assert exact.normalized_query == normalize_query("alpha")
    assert len(exact.hits) == 1
    assert isinstance(exact.hits[0], SearchHit)
    assert exact.hits[0].heading == "alpha"
    assert exact.hits[0].heading_source == "title"
    assert exact.hits[0].title_status == "resolved"
    assert "body" not in exact.hits[0].to_dict()
    assert exact.hits[0].to_dict()["title_status"] == "resolved"
    assert exact.hits[0].to_dict(debug=True)["body"]["component"] == "HONMON.DIC"
    assert exact.hits[0].to_dict(debug=True)["title_resolution"]["status"] == "resolved"

    forward = package.search("alp", profile=SearchProfile.FORWARD)
    assert [hit.heading for hit in forward.hits] == ["alpha", "alpine"]

    backward = package.search("ta", profile=SearchProfile.BACKWARD)
    assert [hit.heading for hit in backward.hits] == ["beta"]

    native = package.search("alp", profile=SearchProfile.NATIVE)
    assert [hit.heading for hit in native.hits] == ["alpha", "alpine"]


def test_lvcore_backward_only_index_supports_exact_and_suffix_search(tmp_path: Path) -> None:
    make_backward_only_package(tmp_path)
    package = open_package(tmp_path)

    exact = package.search("alpha", profile=SearchProfile.EXACT)
    backward = package.search("ha", profile=SearchProfile.BACKWARD)
    native = package.search("alpha", profile=SearchProfile.NATIVE)

    assert [hit.display_key for hit in exact.hits] == ["alpha"]
    assert exact.hits[0].matched_key == "alpha"
    assert [hit.display_key for hit in backward.hits] == ["alpha"]
    assert [hit.display_key for hit in native.hits] == ["alpha"]

    report = package.validate(sample_entries=0, sample_search_hits=1)
    assert report["diagnostics"]["by_code"].get("sample_search_miss", 0) == 0
    assert report["sample_search_hits_dereferenced"] == 1


def test_lvcore_sample_search_skips_empty_normalized_index_keys(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    honmon_start = 2
    title_start = 3
    index_start = 4
    (tmp_path / "FHINDEX.DIC").write_bytes(
        literal_sseddata(simple_index([("・", honmon_start, 0, title_start, 0)]), start_block=index_start, kind=0x91)
    )
    package = open_package(tmp_path)

    report = package.validate(sample_entries=0, sample_search_hits=1)

    assert report["diagnostics"]["by_code"]["sample_search_skipped_empty_query"] == 1
    assert report["diagnostics"]["by_code"].get("sample_search_miss", 0) == 0


def test_lvcore_parses_keyword_index_direct_and_grouped_rows() -> None:
    direct = parse_index(leaf_page([direct_index_record("direct")]), 1, 0x80)
    assert [(row.key, row.row_type, row.title.block) for row in direct.rows] == [("direct", "direct", 20)]
    assert direct.row_type_counts["direct"] == 1

    grouped_b0 = parse_index(leaf_page([title_group_record("kw"), compact_body_target_record(0xB0, 11, 2)]), 1, 0x80)
    assert [(row.key, row.target_key, row.body.block, row.title.block, row.inherited_title) for row in grouped_b0.rows] == [
        ("kw", "kw", 11, 20, True)
    ]
    assert grouped_b0.row_type_counts["target"] == 1
    assert grouped_b0.row_type_counts["tag_b0"] == 1

    grouped_c0 = parse_index(leaf_page([title_group_record("kw"), compact_body_target_record(0xC0, 12, 4)]), 1, 0x80)
    assert [(row.key, row.body.block, row.title.block, row.inherited_title) for row in grouped_c0.rows] == [("kw", 12, 20, True)]
    assert grouped_c0.row_type_counts["tag_c0"] == 1


def test_lvcore_parses_keyword_continuation_target_across_pages() -> None:
    data = leaf_page([title_group_record("kw", count=2)]) + leaf_page([compact_body_target_record(0xC0, 11, 2)])
    parsed = parse_index(data, 1, 0x80)
    assert [(row.key, row.body.block, row.title.block, row.group_page, row.inherited_title) for row in parsed.rows] == [
        ("kw", 11, 20, 0, True)
    ]
    assert parsed.continuation_groups == 1
    assert parsed.dangling_continuation_rows == 0


def test_lvcore_parses_cross_reference_index_direct_grouped_and_continuation() -> None:
    direct = parse_index(leaf_page([direct_index_record("cross")]), 1, 0x81)
    assert [(row.key, row.row_type, row.title.block) for row in direct.rows] == [("cross", "direct", 20)]

    grouped = parse_index(leaf_page([title_group_record("cross"), compact_body_target_record(0xC0, 13, 6)]), 1, 0x81)
    assert [(row.key, row.body.block, row.title.block, row.inherited_title) for row in grouped.rows] == [("cross", 13, 20, True)]

    continued = parse_index(leaf_page([title_group_record("cross", count=2)]) + leaf_page([compact_body_target_record(0xC0, 14, 8)]), 1, 0x81)
    assert [(row.key, row.body.block, row.title.block, row.group_page, row.inherited_title) for row in continued.rows] == [
        ("cross", 14, 20, 0, True)
    ]
    assert continued.continuation_groups == 1


def test_lvcore_parses_multi_selector_index_direct_grouped_and_continuation() -> None:
    direct = parse_index(leaf_page([direct_index_record("multi")]), 1, 0xA1)
    assert [(row.key, row.row_type, row.title.block) for row in direct.rows] == [("multi", "direct", 20)]

    grouped = parse_index(leaf_page([multi_group_record("multi"), multi_target_record(15, 10, 25, 12)]), 1, 0xA1)
    assert [(row.key, row.body.block, row.title.block, row.inherited_title) for row in grouped.rows] == [("multi", 15, 25, False)]

    continued = parse_index(leaf_page([multi_group_record("multi", count=2)]) + leaf_page([multi_target_record(16, 14, 26, 16)]), 1, 0xA1)
    assert [(row.key, row.body.block, row.title.block, row.group_page) for row in continued.rows] == [("multi", 16, 26, 0)]
    assert continued.continuation_groups == 1


def test_lvcore_parses_direct_rows_inside_tagged_index_families() -> None:
    tagged_forward = parse_index(leaf_page([direct_index_record("direct")]), 1, 0x90)
    tagged_backward = parse_index(leaf_page([direct_index_record("direct")]), 1, 0x70)
    body_only = parse_index(leaf_page([tagged_direct_body_only_record("body")]), 1, 0x30)

    assert [(row.key, row.title.block, row.row_type) for row in tagged_forward.rows] == [("direct", 20, "direct")]
    assert [(row.key, row.title.block, row.row_type) for row in tagged_backward.rows] == [("direct", 20, "direct")]
    assert [(row.key, row.body.block, row.title.block, row.row_type) for row in body_only.rows] == [("body", 10, 10, "direct")]


def test_lvcore_parses_tagged_group_continuation_for_existing_families() -> None:
    tagged = parse_index(
        leaf_page([tagged_group_record("parent", count=2)]) + leaf_page([tagged_target_record("child", 17, 2, 27, 4)]),
        1,
        0x90,
    )
    body_only = parse_index(
        leaf_page([tagged_group_record("parent", count=2)]) + leaf_page([tagged_target_body_only_record("child", 18, 6)]),
        1,
        0x30,
    )

    assert [(row.key, row.target_key, row.body.block, row.title.block, row.group_page) for row in tagged.rows] == [
        ("parent", "child", 17, 27, 0)
    ]
    assert [(row.key, row.target_key, row.body.block, row.title.block, row.group_page) for row in body_only.rows] == [
        ("parent", "child", 18, 18, 0)
    ]
    assert tagged.continuation_groups == 1
    assert body_only.continuation_groups == 1


def test_lvcore_index_parser_reports_malformed_and_unsupported_rows() -> None:
    malformed = parse_index(pad_block(be16(0x8000) + be16(1) + b"\xff\x01"), 1, 0x80)
    assert malformed.rows == ()
    assert malformed.malformed_leaf_rows == 1
    assert malformed.diagnostics[0].code == "unknown_leaf_tag"

    unsupported = parse_index(leaf_page([direct_index_record("ignored")]), 1, 0x27)
    assert unsupported.rows == ()
    assert unsupported.unsupported_component_type == 0x27
    assert unsupported.unsupported_leaf_pages == 1
    assert unsupported.diagnostics[0].code == "unsupported_component_type"

    unsupported_branch_only = parse_index(pad_block(be16(0x0002) + be16(1) + b"\x00" * 6), 1, 0x27)
    assert unsupported_branch_only.rows == ()
    assert unsupported_branch_only.internal_pages == 1
    assert unsupported_branch_only.unsupported_component_type == 0x27
    assert unsupported_branch_only.diagnostics[0].code == "unsupported_component_type"


def test_lvcore_index_parser_reports_partial_physical_tail_separately() -> None:
    parsed = parse_index(pad_block(simple_index([("alpha", 10, 0, 20, 0)])) + b"\x80\x00\x00\x5e\x08", 1, 0x91)

    assert [(row.key, row.row_type) for row in parsed.rows] == [("alpha", "simple")]
    assert parsed.malformed_leaf_rows == 0
    assert parsed.unknown_leaf_bytes == 0
    assert parsed.physical_tail_bytes == 5
    assert parsed.physical_tail_nonzero_bytes == 3
    assert parsed.diagnostics[-1].code == "partial_index_page_tail"


def test_lvcore_classifies_text_like_index_outlier_as_resource(tmp_path: Path) -> None:
    make_text_like_index_outlier_package(tmp_path)
    catalog = parse_catalog(tmp_path / "OUTLIER.IDX")
    outlier = next(component for component in catalog.components if component.name == "INDEX.DIC")
    package = open_package(tmp_path)
    report = package.validate(sample_entries=1, sample_search_hits=1)

    assert outlier.type == 0x27
    assert outlier.role == ComponentRole.RESOURCE
    assert "INDEX.DIC" not in package.indexes()
    assert report["index_summary"]["text_like_index_outliers"] == {"INDEX.DIC": "27"}
    assert report["index_summary"]["unsupported_component_types"] == {}

    result = subprocess.run(
        [sys.executable, "-m", "lvcore", "validate", str(tmp_path), "--json", "--sample-entries", "1", "--sample-search-hits", "1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(result.stdout)
    assert data["index_summary"]["text_like_index_outliers"] == {"INDEX.DIC": "27"}


@pytest.mark.parametrize("component_type,grouped", [(0x80, False), (0x80, True), (0x81, False), (0x81, True), (0xA1, False), (0xA1, True)])
def test_lvcore_search_hit_title_resolution_from_special_index_rows(tmp_path: Path, component_type: int, grouped: bool) -> None:
    make_special_index_package(tmp_path, component_type=component_type, grouped=grouped)
    package = open_package(tmp_path)

    hit = package.search("special", profile=SearchProfile.EXACT).hits[0]
    debug = hit.to_dict(debug=True)

    assert hit.heading == "special title"
    assert hit.heading_source == "title"
    assert hit.title_status == "resolved"
    assert "title_resolution" not in hit.to_dict()
    assert debug["title_resolution"]["status"] == "resolved"
    assert debug["raw_row"]["row_type"] == ("target" if grouped else "direct")
    if component_type in {0x80, 0x81} and grouped:
        assert debug["raw_row"]["inherited_title"] is True
        assert debug["raw_row"]["group_key"] == "special"


def test_lvcore_tagged_target_key_matching_and_deduplication(tmp_path: Path) -> None:
    make_tagged_target_package(tmp_path)
    package = open_package(tmp_path)

    exact = package.search("alias", profile=SearchProfile.EXACT)
    forward = package.search("al", profile=SearchProfile.FORWARD)
    debug_forward = package.search("al", profile=SearchProfile.FORWARD, debug=True)

    assert [hit.target_key for hit in exact.hits] == ["alias"]
    assert [hit.heading for hit in forward.hits] == ["primary"]
    assert len(forward.hits) == 1
    assert len(debug_forward.hits) == 2
    assert {hit.target_key for hit in debug_forward.hits} == {"alias", "alternate"}


def test_lvcore_query_normalization_boundaries() -> None:
    assert normalize_query(" Ａ-Ｂ カナ・テスト ") == "ABかなてすと"
    assert normalize_query("かな‐れい") == "かなれい"
    assert normalize_query("漢字ABC") == "漢字ABC"
    assert normalize_query("alpha*") == "ALPHA*"


def test_lvcore_search_hit_dicts_are_friendly_unless_debug(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)
    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]

    friendly = hit.to_dict()
    debug = hit.to_dict(debug=True)

    assert "body" not in friendly
    assert "title" not in friendly
    assert "index_component" not in friendly
    assert "raw_row" not in friendly
    assert debug["index_component"] == "FHINDEX.DIC"
    assert debug["body"]["component"] == "HONMON.DIC"
    assert debug["raw_row"]["key"] == "alpha"


def test_lvcore_repeated_search_uses_cached_values_without_changing_results(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)

    exact = package.search("alpha", profile=SearchProfile.EXACT)
    first = package.search("alp", profile=SearchProfile.FORWARD)
    cache_keys_after_first = sorted(package._search_value_cache)
    exact_cache_keys_after_first = sorted(package._exact_search_cache)
    second = package.search("alp", profile=SearchProfile.FORWARD)

    assert [hit.display_key for hit in exact.hits] == ["alpha"]
    assert [hit.to_dict(debug=True) for hit in second.hits] == [hit.to_dict(debug=True) for hit in first.hits]
    assert cache_keys_after_first == sorted(package._search_value_cache)
    assert exact_cache_keys_after_first == sorted(package._exact_search_cache)
    assert "fhindex.dic" in package._search_value_cache
    assert "fhindex.dic" in package._exact_search_cache


def test_lvcore_search_entries_does_not_swallow_unexpected_exceptions(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)

    def explode(hit: SearchHit) -> Entry:
        raise RuntimeError("unexpected failure")

    package.entry_for_hit = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="unexpected failure"):
        package.search_entries("alpha", profile=SearchProfile.EXACT)


def test_lvcore_search_entries_keeps_recoverable_deferred_placeholders(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    package = open_package(tmp_path)

    entries = package.search_entries("alpha", profile=SearchProfile.EXACT)

    assert len(entries) == 1
    assert "Entry body is not yet supported" in entries[0].text
    assert any(diagnostic.code == "unsupported_body_source" for diagnostic in entries[0].diagnostics())


def test_lvcore_sqlite_identifier_quoting_escapes_embedded_quotes() -> None:
    con = sqlite3.connect(":memory:")
    try:
        table = 'odd"table'
        column = 'strange"column'
        con.execute(f"create table {quote_sql_identifier(table)} ({quote_sql_identifier(column)} integer)")
        con.execute(f"insert into {quote_sql_identifier(table)} ({quote_sql_identifier(column)}) values (7)")

        assert quote_sql_identifier(table) == '"odd""table"'
        assert sqlite_columns(con, table) == [column]
        value = con.execute(f"select {quote_sql_identifier(column)} from {quote_sql_identifier(table)}").fetchone()[0]
        assert value == 7
    finally:
        con.close()


def test_lvcore_package_context_manager_closes_and_cleans_temp_workspace(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    with open_package(tmp_path) as package:
        assert package.search("alpha", profile=SearchProfile.EXACT).hits
        package._tempdir = tempfile.TemporaryDirectory()
        temp_path = Path(package._tempdir.name)
        assert temp_path.exists()

    assert not temp_path.exists()
    with pytest.raises(RuntimeError, match="closed"):
        package.search("alpha", profile=SearchProfile.EXACT)
    package.close()


def test_lvcore_search_hit_dereference_document_and_entry_range(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)
    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]

    entry = hit.entry()
    assert entry.headword == "alpha"
    assert "first entry" in entry.text
    assert "second entry" not in entry.text

    html = package.render_hit_html(hit)
    text = package.render_hit_text(hit)
    assert "first entry" in html
    assert "first entry" in text
    assert "offset" not in html


def test_lvcore_body_source_classifies_body_stream(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)
    source = package.body_source()

    assert source.ssed_kind == SsedBodySourceKind.BODY_STREAM
    assert source.support.value == "renderable"
    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]
    assert "first entry" in package.render_hit_text(hit)


def test_lvcore_dense_anchor_without_sidecar_is_deferred_and_safe(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    package = open_package(tmp_path)
    source = package.body_source()

    assert source.ssed_kind == SsedBodySourceKind.DENSE_ANCHOR_TABLE
    assert source.support.value == "deferred"
    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    html = package.render_entry_html(entry)
    text = package.render_entry_text(entry)

    assert "Entry body is not yet supported" in text
    assert "00000001" not in html
    assert "00000001" not in text
    assert any(diagnostic.code == "unsupported_body_source" for diagnostic in entry.diagnostics())
    assert hit.to_dict(debug=True)["body_source"]["ssed_kind"] == "dense_anchor_table"


def test_lvcore_dense_anchor_with_sqlite_sidecar_renders_body(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path, with_sidecar=True)
    package = open_package(tmp_path)
    source = package.body_source()

    assert source.ssed_kind == SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR
    assert source.support.value == "partially_renderable"
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)

    assert entry.headword == "beta"
    assert "beta sidecar body" in package.render_entry_text(entry)
    assert any(diagnostic.code == "sidecar_body_resolved" for diagnostic in entry.diagnostics())


def test_lvcore_dense_anchor_sidecar_missing_row_is_safe_and_debuggable(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    con = sqlite3.connect(tmp_path / "body.db")
    try:
        con.execute("create table t_contents (f_DataId integer primary key, f_Title text, f_Html text, f_Plane text)")
        con.execute("insert into t_contents values (?, ?, ?, ?)", (1, "alpha", "<div>alpha html</div>", "alpha body"))
        con.commit()
    finally:
        con.close()

    package = open_package(tmp_path)
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    html = package.render_entry_html(entry)
    text = package.render_entry_text(entry)
    diagnostics = [diagnostic.to_dict() for diagnostic in entry.diagnostics()]

    assert "Entry body is not yet supported" in text
    assert "00000002" not in html
    assert "00000002" not in text
    assert any(diagnostic["code"] == "sidecar_body_not_found" for diagnostic in diagnostics)
    missing = next(diagnostic for diagnostic in diagnostics if diagnostic["code"] == "sidecar_body_not_found")
    assert missing["details"]["anchor_id"] == "00000002"
    assert missing["details"]["table"] == "t_contents"
    assert missing["details"]["id_column"] == "f_DataId"
    assert "2" in missing["details"]["query_values"]

    report = package.validate(sample_entries=1, sample_search_hits=2)
    assert report["sidecar_resolution"]["resolved"] == 1
    assert report["sidecar_resolution"]["missing_row"] == 1


def test_lvcore_dense_anchor_with_unsupported_sqlite_sidecar_schema_is_precise(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    con = sqlite3.connect(tmp_path / "body.db")
    try:
        con.execute("create table metadata (name text primary key, value text)")
        con.execute("insert into metadata values (?, ?)", ("kind", "not-a-body-store"))
        con.commit()
    finally:
        con.close()

    package = open_package(tmp_path)
    source = package.body_source()

    assert source.ssed_kind == SsedBodySourceKind.SIDECAR_UNKNOWN
    assert source.support.value == "deferred"
    assert source.sidecars[0].kind == "sqlite_unmapped"
    assert source.sidecar_kind == "sqlite_unmapped"
    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    html = package.render_entry_html(entry)

    assert "Entry body is not yet supported" in package.render_entry_text(entry)
    assert "00000001" not in html
    assert any(diagnostic.code == "unsupported_body_source" for diagnostic in entry.diagnostics())


def test_lvcore_example_idiom_sidecar_is_classified_and_address_mapped(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    add_example_idiom_sidecar(tmp_path)
    package = open_package(tmp_path)

    summary = package.sidecar_role_summary()
    assert summary["role_counts"]["examples_idioms"] == 1
    assert summary["supported_role_counts"]["examples_idioms"] == 1
    assert "examples_idioms" not in summary["unsupported_role_counts"]
    assert "examples_idioms" not in summary["compatibility_significant_unsupported_counts"]
    assert summary["support_status_counts"]["supplement_resolver"] == 1
    assert summary["unsupported_sidecars"] == []

    references = package.sidecar_references(Address(2, 0, "HONMON.DIC"), debug=True)
    assert {reference["table"] for reference in references} == {"D_Example", "D_Idiom"}
    assert all(reference["role"] == "examples_idioms" for reference in references)
    assert all(reference["match_count"] == 1 for reference in references)
    assert all("block_column" in reference for reference in references)

    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    html = package.render_entry_html(entry)
    debug_document = entry.document().to_dict(debug=True)
    assert "alpha example title" in package.render_entry_text(entry)
    assert "alpha idiom title" in package.render_entry_text(entry)
    assert "alpha example title" in html
    assert "examples.db" not in html
    assert "row_id" not in html
    assert debug_document["debug_metadata"]["sidecar_supplements"][0]["sidecar"] == "examples.db"

    report = package.validate(sample_entries=1, sample_search_hits=1)
    assert report["sidecar_references"]["matched"] == 2
    assert report["sidecar_references"]["by_role"]["examples_idioms"] == 2
    assert report["sidecar_supplements"]["examples_idioms_rows_seen"] == 2
    assert report["sidecar_supplements"]["examples_idioms_rows_attached"] >= 2
    assert "unsupported_examples_idioms_sidecar" not in report["diagnostics"]["by_code"]


def test_lvcore_media_sidecar_schema_is_compatibility_significant(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    add_media_sidecar(tmp_path)
    package = open_package(tmp_path)

    body_source = package.body_source()
    sidecar = package.sidecar_role_summary()
    assert body_source.ssed_kind == SsedBodySourceKind.BODY_STREAM
    assert sidecar["role_counts"]["media_resource"] == 1
    assert sidecar["supported_role_counts"]["media_resource"] == 1
    assert sidecar["support_status_counts"]["resource_resolver"] == 1
    assert "media_resource" not in sidecar["unsupported_role_counts"]
    assert "media_resource" not in sidecar["compatibility_significant_unsupported_counts"]

    resources = package.sidecar_media_resources()
    assert len(resources) == 1
    info = package.resource_info(resources[0])
    assert info["status"] == "resolved"
    assert info["store_kind"] == "sidecar_media"
    assert info["byte_length"] == len(b"original bytes")
    assert package.resource_bytes(resources[0]) == b"original bytes"

    report = package.validate(sample_entries=1, sample_search_hits=1)
    assert report["resource_resolution"]["sidecar_media_resolved"] == 1
    assert report["sidecar_supplements"]["sidecar_media_rows_resolved"] == 1
    assert "unsupported_media_resource_sidecar" not in report["diagnostics"]["by_code"]


def test_lvcore_sidecar_media_table_schema_resolves_untouched_blob(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    add_media_table_sidecar(tmp_path)
    package = open_package(tmp_path)

    resources = package.sidecar_media_resources()
    assert len(resources) == 1
    assert resources[0].kind == ResourceKind.IMAGE
    info = package.resource_info(resources[0])
    assert info["status"] == "resolved"
    assert info["mime_type"] == "image/png"
    assert package.resource_bytes(resources[0]) == b"\x89PNG\r\n\x1a\npayload"


def test_lvcore_link_reference_sidecar_attaches_safe_link_supplement(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    add_link_reference_sidecar(tmp_path)
    package = open_package(tmp_path)

    summary = package.sidecar_role_summary()
    assert summary["role_counts"]["link_reference"] == 1
    assert summary["supported_role_counts"]["link_reference"] == 1
    assert summary["support_status_counts"]["supplement_resolver"] == 1

    hit = package.search("alpha", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()
    dumped = json.dumps(document.to_dict(debug=False), ensure_ascii=False)
    debug_dumped = json.dumps(document.to_dict(debug=True), ensure_ascii=False)
    html = package.render_entry_html(entry)

    assert "related entry" in package.render_entry_text(entry)
    assert "lvcore-entry://ref-" in html
    assert "links.db" not in dumped
    assert "row_id" not in dumped
    assert "links.db" in debug_dumped
    assert "row_id" in debug_dumped


def test_lvcore_search_metadata_sidecar_is_supported_but_not_native_search(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    add_search_metadata_sidecar(tmp_path)
    package = open_package(tmp_path)

    summary = package.sidecar_role_summary()
    report = package.validate(sample_entries=0, sample_search_hits=1)

    assert summary["role_counts"]["search"] == 1
    assert summary["supported_role_counts"]["search"] == 1
    assert summary["support_status_counts"]["search_metadata"] == 1
    assert "search" not in summary["compatibility_significant_unsupported_counts"]
    assert package.search("lookup label", profile=SearchProfile.NATIVE).hits == ()
    assert report["sidecar_supplements"]["sidecar_search_rows_seen"] == 1
    assert report["sidecar_supplements"]["sidecar_search_rows_supported"] == 1
    assert report["sidecar_supplements"]["sidecar_search_rows_deferred"] == 0
    assert "unsupported_search_sidecar" not in report["diagnostics"]["by_code"]


def test_lvcore_t_data_sidecar_is_ancillary_not_unknown(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    add_ancillary_t_data_sidecar(tmp_path)
    package = open_package(tmp_path)

    summary = package.sidecar_role_summary()

    assert summary["role_counts"]["ancillary"] == 1
    assert "unknown" not in summary["role_counts"]
    assert "unknown" not in summary["compatibility_significant_unsupported_counts"]
    assert summary["unsupported_sidecars"][0]["compatibility_significant"] is False


def test_lvcore_dense_anchor_sidecar_html_only_body_is_readable(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    con = sqlite3.connect(tmp_path / "body.db")
    try:
        con.execute("create table t_contents (f_DataId integer primary key, f_Title text, f_Html text)")
        con.execute("insert into t_contents values (?, ?, ?)", (2, "beta title", "<div>beta <b>html</b> body</div>"))
        con.commit()
    finally:
        con.close()

    package = open_package(tmp_path)
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)

    assert entry.headword == "beta title"
    assert "beta html body" in package.render_hit_text(hit)
    assert "<b>" not in package.render_hit_text(hit)


def test_lvcore_dense_anchor_sidecar_plain_text_body_is_preferred(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    con = sqlite3.connect(tmp_path / "body.db")
    try:
        con.execute("create table t_contents (f_DataId integer primary key, f_Title text, f_Html text, f_Plane text)")
        con.execute("insert into t_contents values (?, ?, ?, ?)", (2, "beta title", "<div>html fallback</div>", "plain body wins"))
        con.commit()
    finally:
        con.close()

    package = open_package(tmp_path)
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]

    assert "plain body wins" in package.render_hit_text(hit)
    assert "html fallback" not in package.render_hit_text(hit)


def test_lvcore_dense_anchor_with_extensionless_main_sidecar_renders_body(tmp_path: Path) -> None:
    make_dense_anchor_package(tmp_path)
    con = sqlite3.connect(tmp_path / "DENSE")
    try:
        con.execute("create table main (ID text primary key, Class text, C_text text, J_text text, Pinyin text)")
        con.execute("insert into main values (?, ?, ?, ?, ?)", ("00000002", "class", "beta title", "beta main sidecar body", ""))
        con.commit()
    finally:
        con.close()

    package = open_package(tmp_path)
    source = package.body_source()

    assert source.ssed_kind == SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR
    assert source.sidecars[0].kind == "main_wordlist"
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)

    assert entry.headword == "beta title"
    assert "beta main sidecar body" in package.render_entry_text(entry)


def test_lvcore_dense_anchor_with_observed_t_contents_schema_variants(tmp_path: Path) -> None:
    first = tmp_path / "contents_id"
    make_dense_anchor_package(first)
    con = sqlite3.connect(first / "body.db")
    try:
        con.execute(
            "create table t_contents (f_contents_id integer primary key, f_title text, f_plane_text text, f_html_text text)"
        )
        con.execute("insert into t_contents values (?, ?, ?, ?)", (2, "beta title", "beta contents-id body", "<div>ignored</div>"))
        con.commit()
    finally:
        con.close()

    package = open_package(first)
    source = package.body_source()
    assert source.ssed_kind == SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR
    assert source.sidecars[0].id_column == "f_contents_id"
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]
    assert "beta contents-id body" in package.render_hit_text(hit)

    second = tmp_path / "order_id"
    make_dense_anchor_package(second)
    con = sqlite3.connect(second / "body.db")
    try:
        con.execute("create table t_contents (f_order_id integer primary key, f_midashi text, f_contents text)")
        con.execute("insert into t_contents values (?, ?, ?)", (2, "beta heading", "<div>beta order-id body</div>"))
        con.commit()
    finally:
        con.close()

    package = open_package(second)
    source = package.body_source()
    assert source.ssed_kind == SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR
    assert source.sidecars[0].id_column == "f_order_id"
    hit = package.search("beta", profile=SearchProfile.EXACT).hits[0]
    assert "beta order-id body" in package.render_hit_text(hit)


def test_lvcore_title_dereference_failure_falls_back_to_key(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    package = open_package(tmp_path)
    parsed = package.indexes("FHINDEX.DIC")["FHINDEX.DIC"]
    row = parsed.rows[0]
    bad_row = type(row)(
        key=row.key,
        target_key=row.target_key,
        body=row.body,
        title=Address(999999, 0),
        tagged=row.tagged,
        page=row.page,
        row=row.row,
    )
    hit = package._make_hit(
        hit_id=1,
        query="alpha",
        normalized_query="ALPHA",
        profile=SearchProfile.EXACT,
        component_name="FHINDEX.DIC",
        row=bad_row,
        matched_key="alpha",
    )
    assert hit.heading == "alpha"
    assert hit.heading_source == "display_key"
    assert hit.title_status == "failed"
    assert hit.title_diagnostic_code == "title_dereference_failed"
    assert hit.title_reason == "missing_title_component"
    assert hit.to_dict()["title_status"] == "failed"
    assert "title_resolution" not in hit.to_dict()
    assert hit.to_dict(debug=True)["title_resolution"]["reason"] == "missing_title_component"
    assert any(diagnostic.code == "title_dereference_failed" for diagnostic in hit.diagnostics)


def test_lvcore_body_pointer_title_slot_is_heading_fallback_not_failure(tmp_path: Path) -> None:
    make_body_pointer_title_package(tmp_path)
    package = open_package(tmp_path)

    hit = package.search("fallback", profile=SearchProfile.EXACT).hits[0]
    report = package.validate(sample_entries=0, sample_search_hits=1)

    assert hit.heading == "fallback"
    assert hit.heading_source == "display_key"
    assert hit.title_status == "fallback"
    assert hit.title_reason == "title_pointer_is_body_pointer"
    assert hit.diagnostics == ()
    friendly = hit.to_dict()
    debug = hit.to_dict(debug=True)
    assert friendly["title_status"] == "fallback"
    assert "title" not in friendly
    assert "title_resolution" not in friendly
    assert debug["title"]["block"] == 2
    assert debug["title_resolution"]["row_title_equals_body"] is True
    assert debug["title_resolution"]["reason"] == "title_pointer_is_body_pointer"
    assert report["title_dereference"]["failed"] == 0
    assert report["title_dereference"]["fallback"] == 1
    assert report["title_dereference"]["by_reason"] == {"title_pointer_is_body_pointer": 1}
    assert report["title_dereference"]["heading_source_counts"] == {"display_key": 1}


def test_lvcore_body_pointer_title_slot_with_other_titles_still_falls_back(tmp_path: Path) -> None:
    make_body_pointer_title_package(tmp_path, with_unused_title=True)
    package = open_package(tmp_path)

    hit = package.search("fallback", profile=SearchProfile.EXACT).hits[0]

    assert hit.heading == "fallback"
    assert hit.title_status == "fallback"
    assert hit.title_reason == "title_pointer_is_body_pointer"
    assert hit.diagnostics == ()


def test_lvcore_entry_document_and_friendly_rendering_from_spans() -> None:
    raw = body_text("<alpha>") + b"\x1f\x0a" + body_text("body & text")
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "<alpha>", decoded.text, decoded.spans)

    document = build_entry_document(entry)
    html = render_html(document)
    text = render_text(document)

    assert len(document.blocks) == 2
    assert "&lt;alpha&gt;" in html
    assert "body &amp; text" in html
    assert "body & text" in text
    assert "offset" not in html
    assert "opcode" not in html


def test_lvcore_entry_to_dict_is_friendly_unless_debug() -> None:
    raw = b"\x1f\x99" + body_text("visible")
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "visible", decoded.text, decoded.spans)

    friendly = entry.to_dict()
    debug = entry.to_dict(debug=True)
    inspected = entry.inspect()

    assert friendly == {"headword": "visible", "text": "visible", "diagnostics": []}
    assert "address" in debug
    assert "span_summaries" in debug
    assert '"raw":' not in json.dumps(debug, ensure_ascii=False)
    assert debug["span_summaries"][0]["op"] == "99"
    assert debug["span_summaries"][0]["raw_preview"] == "1f99"
    assert inspected["span_summaries"] == debug["span_summaries"]


def test_lvcore_entry_document_v1_to_dict_hides_debug_metadata_by_default() -> None:
    diagnostic = Diagnostic(
        severity=Severity.WARNING,
        area=DiagnosticArea.OPCODE,
        message="unknown control",
        code="unknown_control",
        details={"payload": "deadbeef"},
    )
    document = EntryDocument(
        blocks=(
            BlockNode(
                BlockKind.PARAGRAPH,
                (
                    InlineNode(InlineKind.TEXT, text="visible"),
                    InlineNode(InlineKind.UNKNOWN_CONTROL, attrs={"op": "99", "payload": "deadbeef", "span_offset": 3}),
                ),
            ),
        ),
        resources=(
            ResourceRef(
                id="media-1",
                kind=ResourceKind.MEDIA,
                label="media",
                source_offset=12,
                details={"payload_hex": "cafebabe", "resolved": False},
            ),
        ),
        diagnostics=(diagnostic,),
        metadata={"headword": "visible"},
        debug_metadata={"span_summaries": [{"op": "99", "raw_preview": "1f99"}]},
    )

    public = document.to_dict()
    debug = document.to_dict(debug=True)

    assert public["schema"] == "lvcore.entry_document.v1"
    assert public["model_version"] == 1
    assert public["metadata"] == {"headword": "visible"}
    assert "debug_metadata" not in public
    assert "deadbeef" not in json.dumps(public, ensure_ascii=False)
    assert "cafebabe" not in json.dumps(public, ensure_ascii=False)
    assert public["resources"][0]["details"] == {"resolved": False}
    assert debug["debug_metadata"]["span_summaries"][0]["raw_preview"] == "1f99"
    assert "deadbeef" in json.dumps(debug, ensure_ascii=False)
    assert "cafebabe" in json.dumps(debug, ensure_ascii=False)
    assert document.resource_map()["media-1"].kind == ResourceKind.MEDIA
    assert document.diagnostics_by_code() == {"unknown_control": 1}


def test_lvcore_debug_render_exposes_unknown_control_but_friendly_hides_it() -> None:
    raw = b"\x1f\x99" + body_text("visible")
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "visible", decoded.text, decoded.spans)
    document = entry.document()

    friendly = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "visible" in friendly
    assert "unknown:99" not in friendly
    assert "unknown:99" in debug
    assert any(diagnostic.code == "unknown_control" for diagnostic in document.diagnostics)


def test_lvcore_private_renderer_directives_are_hidden_from_friendly_output() -> None:
    raw = b"\x1f\xe2\x00\x07" + body_text("SQL:") + b"\x1f\xe3" + body_text("visible")
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "visible", decoded.text, decoded.spans)

    document = entry.document()
    html = entry.html()
    debug = entry.render_html("debug")

    assert "visible" in html
    assert "SQL" not in html
    assert "e2" in debug
    assert "private_renderer_directive" in debug
    assert any(diagnostic.code == "private_renderer_directive" for diagnostic in document.diagnostics)


def test_lvcore_opcode_behavior_atlas_exposes_clean_semantics() -> None:
    assert behavior_for(0x04).semantic_name == "halfwidth conversion start"
    assert behavior_for(0x04).category == OpcodeCategory.TEXT
    assert behavior_for(0x3B).category == OpcodeCategory.URL
    assert behavior_for(0x44).category == OpcodeCategory.EXTENDED_LINK
    assert behavior_for(0xE2).diagnostic_code == "private_renderer_directive"
    assert behavior_for(0x99) is None


def test_lvcore_literal_preformatted_span_renders_readable_text() -> None:
    raw = b"\x1f\x0b" + body_text("literal <tag> & text") + b"\x1f\x0c"
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "literal", decoded.text, decoded.spans)
    html = entry.html()
    text = entry.plain_text()

    assert "literal &lt;tag&gt; &amp; text" in html
    assert "literal <tag> & text" in text
    assert "1f0b" not in html.lower()
    assert "1f0c" not in html.lower()


def test_lvcore_url_span_renders_as_safe_link_semantics_or_text() -> None:
    raw = b"\x1f\x3b" + body_text("https://example.test/?q=<x>&ok=1") + b"\x1f\x5b"
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "url", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    text = render_text(document)

    assert "https://example.test/?q=&lt;x&gt;&amp;ok=1" in html
    assert '<a class="lv-link lv-link-url"' in html
    assert 'href="https://example.test/?q=&lt;x&gt;&amp;ok=1"' in html
    assert "https://example.test/?q=<x>&ok=1" in text
    assert "lv-link" in html
    assert "1f3b" not in html.lower()


def test_lvcore_tab_and_media_layout_controls_are_diagnostic_hints() -> None:
    raw = body_text("left") + b"\x1f\x1a\x20\x00" + body_text("right") + b"\x1f\x1c\x20\x00"
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "layout", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    text = render_text(document)

    assert "leftright" in text
    assert "left" in html and "right" in html
    assert "1f1a" not in html.lower()
    assert "1f1c" not in html.lower()
    assert any(diagnostic.code == "tab_column_control" for diagnostic in document.diagnostics)
    assert any(diagnostic.code == "media_layout_control" for diagnostic in document.diagnostics)


def test_lvcore_extended_link_control_does_not_leak_raw_payload() -> None:
    payload = bytes(range(10))
    target = b"\x00\x00\x00\x02\x00\x10"
    raw = b"\x1f\x44" + payload + body_text("linked label") + b"\x1f\x64" + target
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "link", decoded.text, decoded.spans)
    html = entry.html()
    debug = entry.render_html("debug")

    assert "linked label" in html
    assert "lv-link" in html
    assert payload.hex() not in html
    assert target.hex() not in html
    assert "1f44" not in html.lower()
    assert payload.hex() in debug


def test_lvcore_internal_link_renders_semantic_target_without_raw_payload() -> None:
    target = b"\x00\x00\x00\x02\x00\x10"
    raw = b"\x1f\x42" + body_text("see also") + b"\x1f\x62" + target
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "link", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "see also" in html
    assert '<a class="lv-link lv-link-internal" href="lvcore-entry://ref-' in html
    assert "lvcore-entry://2/10" not in html
    assert target.hex() not in html
    assert 'href="lvcore-entry://2/10"' in debug
    assert 'data-end-payload="000000020010"' in debug
    assert not any(diagnostic.code == "unresolved_link_target" for diagnostic in document.diagnostics)


def test_lvcore_link_targets_are_typed_and_debuggable() -> None:
    body_target = b"\x00\x00\x00\x02\x00\x10"
    menu_target = b"\x00\x00\x00\x03\x00\x20"
    jump_payload = b"\x00\x01\x00\x02" + b"\x00\x00\x00\x04\x00\x30" + b"\x00\x00\x00\x04\x00\x40"
    raw = (
        b"\x1f\x42" + body_text("body ref") + b"\x1f\x62" + body_target
        + b"\x1f\x43" + body_text("menu ref") + b"\x1f\x63" + menu_target
        + b"\x1f\x4a" + jump_payload + body_text("sound ref") + b"\x1f\x6a"
    )
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "link"), "link", decoded.text, decoded.spans)
    document = entry.document()
    link_nodes = [node for block in document.blocks for node in block.inlines if node.kind == InlineKind.LINK]

    assert [node.attrs["link_target"]["kind"] for node in link_nodes] == [
        LinkTargetKind.BODY_REFERENCE.value,
        LinkTargetKind.MENU_NAVIGATION.value,
        LinkTargetKind.JUMP_OR_AUDIO_RANGE.value,
    ]
    assert link_nodes[0].attrs["link_target"]["status"] == "resolved"
    assert link_nodes[1].attrs["link_target"]["href"] == "lvcore-entry://3/20"
    assert link_nodes[2].attrs["link_target"]["status"] == "deferred"
    assert link_nodes[2].attrs["link_target"]["address"] == {"block": 4, "offset": 30}
    assert link_nodes[2].attrs["link_target"]["end_address"] == {"block": 4, "offset": 40}

    friendly = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "body ref" in friendly and "menu ref" in friendly and "sound ref" in friendly
    assert "000000020010" not in friendly
    assert "00010002000000040030000000040040" not in friendly
    assert "lvcore-entry://ref-" in friendly
    assert "00010002000000040030000000040040" in debug


def test_lvcore_unresolved_link_is_diagnostic_and_friendly_safe() -> None:
    payload = bytes(range(10))
    raw = b"\x1f\x49" + payload + body_text("unresolved") + b"\x1f\x69"
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "link", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    assert "unresolved" in html
    assert "lv-link-unresolved" in html
    assert payload.hex() not in html
    assert any(diagnostic.code == "unresolved_link_target" for diagnostic in document.diagnostics)
    assert payload.hex() in debug


def test_lvcore_gaiji_document_nodes_and_render_policies() -> None:
    raw = b"\xa1\x26" + b"\xa1\x27"
    decoded = decode_text_stream(raw, {"a126": "é"})
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "gaiji", decoded.text, decoded.spans)
    document = entry.document()
    gaiji_nodes = [node for block in document.blocks for node in block.inlines if node.kind == InlineKind.GAIJI]

    assert [node.code for node in gaiji_nodes] == ["a126", "a127"]
    assert "é" in render_text(document)
    assert "é" in render_html(document)
    assert "é" in render_html(document, gaiji_policy=GaijiPolicy.BITMAP_PREFERRED)
    assert "lvcore-resource://gaiji-1" in render_html(document, gaiji_policy=GaijiPolicy.BITMAP_PREFERRED)
    assert "lvcore-resource://gaiji-2" in render_html(document, gaiji_policy=GaijiPolicy.BITMAP_ONLY)
    assert "&lt;hA126&gt;" in render_html(document, profile=HtmlProfile.DEBUG)
    assert any(diagnostic.code == "unresolved_gaiji" for diagnostic in document.diagnostics)


def test_lvcore_ga16_jis_grid_and_record_order_gaiji_resolution(tmp_path: Path) -> None:
    assert gaiji_grid_code_for_index(0xA121, 0x5D) == 0xA17E
    assert gaiji_grid_code_for_index(0xA121, 0x5E) == 0xA221

    glyph0 = bytes([0x80] * 16)
    glyph1 = bytes([0x40] * 16)
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("a130"),
        uni_records=[("a130", "", ""), ("a140", "", "")],
        ga16_name="GA16HALF",
        ga16_payload=ga16_file(width=8, height=16, start_code=0xA121, glyphs=[glyph0, glyph1]),
    )

    package = open_package(tmp_path)
    entry = package.search("gaiji", limit=1).hits[0].entry()
    document = entry.document()
    resource = next(item for item in document.resources if item.kind == ResourceKind.GAIJI)
    info = package.resource_info(resource)

    assert resource.details["display_status"] == "bitmap_backed"
    assert resource.details["reason"] == "uni_record_order_ga16"
    assert info["display_status"] == "bitmap_backed"
    assert info["glyph_index"] == 0
    assert package.resource_bytes(resource) == glyph0
    assert "lvcore-resource://" in render_html(document, gaiji_policy=GaijiPolicy.BITMAP_ONLY)


def test_lvcore_blank_ga16_glyph_is_formatting_helper(tmp_path: Path) -> None:
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("a130"),
        uni_records=[("a130", "", "")],
        ga16_name="GA16HALF",
        ga16_payload=ga16_file(width=8, height=16, start_code=0xA121, glyphs=[bytes(16)]),
    )

    package = open_package(tmp_path)
    entry = package.search("gaiji", limit=1).hits[0].entry()
    document = entry.document()
    resource = next(item for item in document.resources if item.kind == ResourceKind.GAIJI)

    assert resource.status == ResourceStatus.RESOLVED
    assert resource.details["display_status"] == "formatting_helper"
    assert resource.details["reason"] == "blank_bitmap_formatting_helper"
    assert "lv-gaiji-helper" in render_html(document)
    assert "□" not in render_text(document)
    report = package.validate(sample_entries=1, sample_search_hits=1)
    assert report["resource_resolution"]["gaiji_formatting_helper"] >= 1
    assert report["resource_resolution"]["unresolved_gaiji"] == 0


def test_lvcore_blank_fullwidth_uni_record_is_formatting_helper_candidate(tmp_path: Path) -> None:
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("b130"),
        uni_records=[("b130", "", "")],
        ga16_name=None,
        ga16_payload=None,
    )
    package = open_package(tmp_path)

    hit = package.search("gaiji", limit=1).hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()
    resource = next(item for item in document.resources if item.kind == ResourceKind.GAIJI)
    info = package.resource_info(resource)
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)
    report = package.validate(sample_entries=1, sample_search_hits=1)

    assert resource.details["display_status"] == "formatting_helper"
    assert resource.details["reason"] == "fullwidth_formatting_helper_candidate"
    assert info["display_status"] == "formatting_helper"
    assert "b130" not in html.lower()
    assert "fullwidth_formatting_helper_candidate" in debug
    assert report["resource_resolution"]["gaiji_formatting_helper"] >= 1
    assert report["resource_resolution"]["gaiji_display_unresolved"] == 0
    assert report["resource_resolution"]["unresolved_gaiji"] == 0


def test_lvcore_raw_fullwidth_gaiji_without_mapping_is_formatting_helper_candidate(tmp_path: Path) -> None:
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("b130"),
        uni_records=[],
        ga16_name=None,
        ga16_payload=None,
    )
    package = open_package(tmp_path)

    resource = next(item for item in package.search("gaiji", limit=1).hits[0].entry().document().resources if item.kind == ResourceKind.GAIJI)
    info = package.resource_info(resource)
    report = package.validate(sample_entries=1, sample_search_hits=1)

    assert resource.details["display_status"] == "formatting_helper"
    assert resource.details["source"] == "raw_fullwidth"
    assert info["reason"] == "fullwidth_formatting_helper_candidate"
    assert report["resource_resolution"]["gaiji_display_unresolved"] == 0
    assert report["resource_resolution"]["unresolved_gaiji"] == 0


def test_lvcore_plist_unicode_and_image_backed_gaiji_resources(tmp_path: Path) -> None:
    png = b"\x89PNG\r\n\x1a\nimage"
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("b123"),
        plist_payload={"a155": "Ω", "b123": "img/b123.png"},
        image_payload=("img/b123.png", png),
    )
    package = open_package(tmp_path)
    assert package.gaiji.resolve("a155") == "Ω"

    entry = package.search("gaiji", limit=1).hits[0].entry()
    document = entry.document()
    resource = next(item for item in document.resources if item.kind == ResourceKind.GAIJI)
    info = package.resource_info(resource)

    assert resource.details["display_status"] == "image_backed"
    assert info["display_status"] == "image_backed"
    assert info["mime_type"] == "image/png"
    assert package.resource_bytes(resource) == png
    assert "lvcore-resource://" in render_html(document, gaiji_policy=GaijiPolicy.BITMAP_ONLY)


def test_lvcore_renderer_entry_backed_gaiji_is_not_global_mapping() -> None:
    diagnostic = Diagnostic(
        severity=Severity.INFO,
        area=DiagnosticArea.BODY,
        code="sidecar_body_resolved",
        message="sidecar body resolved",
    )
    span = Span(kind="gaiji", text="<hA130>", code="a130", raw=bytes.fromhex("a130"))
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, 2, "HONMON.DIC"), "ctx", "<hA130>", (span,), entry_diagnostics=(diagnostic,))
    document = entry.document()
    resource = document.resources[0]

    assert resource.details["display_status"] == "renderer_entry_backed"
    assert resource.details["reason"] == "renderer_contextual_required"
    assert not any(diagnostic.code == "unresolved_gaiji" for diagnostic in document.diagnostics)
    assert "A130" not in render_html(document)
    assert "A130" in render_html(document, profile=HtmlProfile.DEBUG)


def test_lvcore_gaiji_cli_lists_display_readiness(tmp_path: Path) -> None:
    glyph = bytes([0x80] * 16)
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("a130"),
        uni_records=[("a130", "", "")],
        ga16_name="GA16HALF",
        ga16_payload=ga16_file(width=8, height=16, start_code=0xA121, glyphs=[glyph]),
    )

    result = subprocess.run(
        [sys.executable, "-m", "lvcore", "gaiji", str(tmp_path), "--json", "--debug"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(result.stdout)

    assert data["gaiji"]["ga16"][0]["section"] == "half"
    assert data["resources"][0]["info"]["display_status"] == "bitmap_backed"
    assert data["resources"][0]["info"]["reason"] == "uni_record_order_ga16"


def test_lvcore_media_refs_become_resources_and_safe_placeholders() -> None:
    raw = b"\x1f\x4d" + bytes(range(18)) + body_text("caption")
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "media", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)

    assert len(document.resources) == 1
    assert document.resources[0].id == "media-1"
    assert document.resources[0].status == ResourceStatus.MALFORMED
    assert document.resources[0].details["reason"] == "malformed_media_pointer"
    assert "lvcore-resource://media-1" in html
    assert bytes(range(18)).hex() not in html
    assert any(diagnostic.code == "unresolved_media_ref" for diagnostic in document.diagnostics)


def test_lvcore_media_resource_info_resolves_original_component_address(tmp_path: Path) -> None:
    make_media_resource_package(tmp_path)
    package = open_package(tmp_path)
    hit = package.search("media", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()
    resource = document.resources[0]

    info = package.resource_info(resource)

    assert resource.kind == ResourceKind.MEDIA
    assert resource.details["target_address"] == {"block": 3, "offset": 0}
    assert info["status"] == "resolved"
    assert info["reason"] == "colscr_data_record"
    assert info["source_component"] == "COLSCR.DIC"
    assert info["source_offset"] == 0
    assert info["record_offset"] == 0
    assert info["record_length"] == 16
    assert info["payload_offset"] == 8
    assert info["payload_length"] == 8
    assert info["mime_type"] == "image/png"
    assert info["store_kind"] == "colscr"
    assert package.resource_bytes(resource) == b"\x89PNG\r\n\x1a\n"
    assert package.resource_record_bytes(resource) == b"data\x08\x00\x00\x00\x89PNG\r\n\x1a\n"


def test_lvcore_pcmdata_audio_range_resolves_original_bytes(tmp_path: Path) -> None:
    make_pcmdata_resource_package(tmp_path)
    package = open_package(tmp_path)
    hit = package.search("audio", profile=SearchProfile.EXACT).hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()

    assert [resource.kind for resource in document.resources] == [ResourceKind.AUDIO]
    resource = document.resources[0]
    info = package.resource_info(resource)

    assert resource.id == "audio-1"
    assert resource.details["range_start"] == {"block": 3, "offset": 0}
    assert resource.details["range_end"] == {"block": 3, "offset": 9}
    assert info["status"] == "resolved"
    assert info["reason"] == "pcmdata_range"
    assert info["source_component"] == "PCMDATA.DIC"
    assert info["payload_offset"] == 0
    assert info["payload_length"] == 9
    assert info["mime_type"] == "audio/mpeg"
    assert info["store_kind"] == "pcmdata"
    assert package.resource_bytes(resource) == b"ID3abc123"
    assert "lvcore-resource://audio-1" in render_html(document)
    assert "ID3abc123" not in render_html(document)


def test_lvcore_validate_counts_resolved_media_stores(tmp_path: Path) -> None:
    make_media_resource_package(tmp_path)
    package = open_package(tmp_path)
    report = package.validate(sample_entries=1, sample_search_hits=1)

    media = report["resource_resolution"]
    assert media["resolved_media"] >= 1
    assert media["unresolved_media"] == 0
    assert media["colscr_records_resolved"] >= 1
    assert media["media_store_kind_counts"]["colscr"] >= 1
    assert media["media_mime_counts"]["image/png"] >= 1
    assert media["media_bytes_available"] >= 1


def test_lvcore_colscr_malformed_record_reports_reason(tmp_path: Path) -> None:
    make_media_resource_package(tmp_path)
    (tmp_path / "COLSCR.DIC").write_bytes(literal_sseddata(b"not-data", start_block=3, kind=0xD2))
    package = open_package(tmp_path)
    hit = package.search("media", profile=SearchProfile.EXACT).hits[0]
    resource = package.entry_for_hit(hit).document().resources[0]

    info = package.resource_info(resource)

    assert info["status"] == "unsupported"
    assert info["details"]["reason"] == "missing_data_magic"
    assert package.resource_bytes(resource) is None


def test_lvcore_resource_cli_lists_info_and_writes_bytes(tmp_path: Path) -> None:
    make_media_resource_package(tmp_path)
    output = tmp_path / "resource.bin"

    resources_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "resources", str(tmp_path), "media", "--json", "--debug"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    resources = json.loads(resources_result.stdout)
    assert resources["resources"][0]["resource"]["id"] == "media-1"
    assert resources["resources"][0]["info"]["status"] == "resolved"
    assert resources["resources"][0]["info"]["mime_type"] == "image/png"

    info_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "resource-info", str(tmp_path), "media", "media-1", "--json"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    info = json.loads(info_result.stdout)
    assert info["ok"] is True
    assert info["info"]["store_kind"] == "colscr"

    bytes_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "resource-bytes", str(tmp_path), "media", "media-1", "--output", str(output)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(bytes_result.stdout)
    assert data["ok"] is True
    assert data["byte_length"] == 8
    assert output.read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_lvcore_image_and_audio_media_refs_are_first_class_resources() -> None:
    spans = (
        Span(kind="media_ref", payload=b"image-payload", attrs={"resource_kind": "image"}, offset=0, length=1),
        Span(kind="media_ref", payload=b"audio-payload", attrs={"resource_kind": "audio"}, offset=1, length=1),
    )
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, 2, "HONMON.DIC"), "media", "", spans)
    document = entry.document()
    html = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)
    text = render_text(document)

    assert [resource.kind.value for resource in document.resources] == ["image", "audio"]
    assert 'data-resource-kind="image"' in html
    assert 'data-resource-kind="audio"' in html
    assert "lvcore-resource://media-1" in html
    assert "lvcore-resource://media-2" in html
    assert "image-payload".encode().hex() not in html
    assert "image-payload".encode().hex() in debug
    assert "[image]" in text and "[audio]" in text


def test_lvcore_renderer_profiles_are_distinct_and_hide_or_show_raw_details() -> None:
    diagnostic = Diagnostic(
        severity=Severity.WARNING,
        area=DiagnosticArea.BODY,
        message="unsupported <body> & source",
        code="unsupported_body_source",
        location=Location(component="HONMON.DIC", block=2, offset=16),
        details={"body_source": "dense_anchor_table", "offset": 16},
    )
    document = EntryDocument(
        blocks=(
            BlockNode(
                BlockKind.HEADING,
                (InlineNode(InlineKind.TEXT, text="head <&>"),),
            ),
            BlockNode(
                BlockKind.PARAGRAPH,
                (
                    InlineNode(InlineKind.TEXT, text="body <raw> & "),
                    InlineNode(InlineKind.GAIJI, text="é", code="a126", resource_id="gaiji-1"),
                    InlineNode(
                        InlineKind.LINK,
                        children=(InlineNode(InlineKind.TEXT, text="see <target>"),),
                        attrs={
                            "start_op": "42",
                            "start_payload": "",
                            "link_target": {
                                "kind": "internal",
                                "href": "lvcore-entry://2/10",
                                "status": "resolved",
                                "end_payload": "000000020010",
                            },
                        },
                    ),
                    InlineNode(
                        InlineKind.MEDIA_REF,
                        resource_id="media-1",
                        attrs={"label": "image <1>", "resource_kind": "image", "payload_hex": "001122"},
                    ),
                    InlineNode(InlineKind.UNKNOWN_CONTROL, attrs={"op": "99", "payload": "deadbeef"}),
                ),
            ),
        ),
        resources=(),
        diagnostics=(diagnostic,),
        metadata={
            "address": {"component": "HONMON.DIC", "block": 2, "offset": 0},
        },
        debug_metadata={"span_summaries": [{"offset": 7, "op": "e2", "raw_preview": "1fe20007"}]},
    )

    friendly = render_html(document)
    semantic = render_html(document, profile=HtmlProfile.SEMANTIC)
    logovista_like = render_html(document, profile=HtmlProfile.LOGOVISTA_LIKE)
    debug = render_html(document, profile=HtmlProfile.DEBUG)

    for html in (friendly, semantic, logovista_like):
        assert "head &lt;&amp;&gt;" in html
        assert "body &lt;raw&gt; &amp;" in html
        assert "deadbeef" not in html
        assert "1fe20007" not in html
        assert "000000020010" not in html
        assert "lvcore-entry://2/10" not in html
        assert "lvcore-entry://ref-" in html

    assert 'data-render-profile="friendly"' in friendly
    assert "lv-entry-semantic" in semantic
    assert 'data-block-kind="heading"' in semantic
    assert "lv-inline-text" in semantic
    assert "lv-inline-resource" in semantic
    assert "lv-entry-logovista-like" in logovista_like
    assert "lv-lvlike-heading" in logovista_like
    assert "lv-lvlike-link" in logovista_like
    assert "lv-body-line" in logovista_like

    assert "lv-entry-debug" in debug
    assert 'href="lvcore-entry://2/10"' in debug
    assert 'data-end-payload="000000020010"' in debug
    assert 'data-payload="001122"' in debug
    assert 'data-payload="deadbeef"' in debug
    assert "Span summaries" in debug
    assert "1fe20007" in debug
    assert "dense_anchor_table" in debug
    assert "unsupported &lt;body&gt; &amp; source" in debug

    assert "lv-diagnostics" not in friendly
    friendly_with_diagnostics = render_html(document, include_diagnostics=True)
    assert "lv-diagnostics" in friendly_with_diagnostics
    assert "unsupported &lt;body&gt; &amp; source" in friendly_with_diagnostics


def test_lvcore_renderer_escapes_external_links_and_resource_mapper_urls() -> None:
    document = EntryDocument(
        blocks=(
            BlockNode(
                BlockKind.PARAGRAPH,
                (
                    InlineNode(
                        InlineKind.LINK,
                        children=(InlineNode(InlineKind.TEXT, text="https://example.test/?q=<x>&ok=1"),),
                        attrs={"link_target": {"kind": "url", "status": "content"}},
                    ),
                    InlineNode(
                        InlineKind.MEDIA_REF,
                        resource_id="media-1",
                        attrs={"label": "image & asset", "resource_kind": "image", "payload_hex": "cafebabe"},
                    ),
                ),
            ),
        )
    )

    html = render_html(
        document,
        resource_url_mapper=lambda resource_id: f"https://assets.example/{resource_id}?q=<x>&ok=1",
    )

    assert 'href="https://example.test/?q=&lt;x&gt;&amp;ok=1"' in html
    assert 'data-resource-url="https://assets.example/media-1?q=&lt;x&gt;&amp;ok=1"' in html
    assert "[image &amp; asset]" in html
    assert "cafebabe" not in html


def test_lvcore_unresolved_body_source_placeholder_is_clean() -> None:
    diagnostic = Diagnostic(
        severity=Severity.ERROR,
        area=DiagnosticArea.BODY,
        message="Entry body is not yet supported for this LogoVista body source.",
        code="unsupported_body_source",
        details={"body_source": "dense_anchor_table", "anchor_raw": "00010203"},
    )
    document = EntryDocument(
        blocks=(
            BlockNode(
                BlockKind.PARAGRAPH,
                (InlineNode(InlineKind.TEXT, text="Entry body is not yet supported for this LogoVista body source."),),
            ),
        ),
        diagnostics=(diagnostic,),
        metadata={"raw_spans": [{"anchor_raw": "00010203"}]},
    )

    friendly = render_html(document)
    debug = render_html(document, profile=HtmlProfile.DEBUG)
    text = render_text(document)

    assert "Entry body is not yet supported" in friendly
    assert "00010203" not in friendly
    assert "Entry body is not yet supported" in text
    assert "00010203" in debug


def test_lvcore_cli_render_and_validate_commands(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    render_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "render", str(tmp_path), "alpha", "--format", "html", "--limit", "1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    assert "first entry" in render_result.stdout
    assert "offset" not in render_result.stdout

    semantic_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvcore",
            "render",
            str(tmp_path),
            "alpha",
            "--format",
            "html",
            "--profile",
            "semantic",
            "--limit",
            "1",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    assert "lv-entry-semantic" in semantic_result.stdout

    logovista_like_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvcore",
            "render",
            str(tmp_path),
            "alpha",
            "--format",
            "html",
            "--profile",
            "logovista-like",
            "--limit",
            "1",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    assert "lv-entry-logovista-like" in logovista_like_result.stdout

    debug_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvcore",
            "render",
            str(tmp_path),
            "alpha",
            "--format",
            "html",
            "--profile",
            "debug",
            "--limit",
            "1",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    assert "lv-entry-debug" in debug_result.stdout

    validate_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "validate", str(tmp_path), "--json"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(validate_result.stdout)
    assert data["ok"] is True
    assert data["sample_entries_rendered"] == 1
    assert data["title_dereference"]["attempts"] == 1
    assert data["title_dereference"]["resolved"] == 1
    assert data["title_dereference"]["heading_source_counts"] == {"title": 1}
    assert data["resource_resolution"]["unresolved_gaiji"] == 0
    assert data["resource_resolution"]["unresolved_media"] == 0
    assert data["resource_resolution"]["unresolved_link"] == 0
    assert data["resource_resolution"]["unresolved_gaiji_by_reason"] == {}
    assert data["resource_resolution"]["unresolved_media_by_reason"] == {}
    assert data["resource_resolution"]["unresolved_link_by_reason"] == {}
    assert data["title_dereference"]["by_reason"] == {}
    assert "sidecar_roles" in data


def test_lvcore_friendly_reader_example_runs_without_raw_internals(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    example = LVCORE_SRC / "examples" / "friendly_reader.py"
    result = subprocess.run(
        [sys.executable, str(example), str(tmp_path), "alpha"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(result.stdout)
    dumped = json.dumps(data, ensure_ascii=False)

    assert data["package"]["family"] == "ssed"
    assert data["body_source"]["kind"] == "body_stream"
    assert data["search"]["hit_count"] == 1
    assert "first entry" in data["entry"]["plain_text"]
    assert "first entry" in data["entry"]["html"]
    assert "raw_spans" not in dumped
    assert "span_summaries" not in dumped
    assert "raw_row" not in dumped
    assert '"spans"' not in dumped
    assert '"body":' not in dumped


def test_lvcore_debug_inspection_example_is_explicit_raw_path(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    example = LVCORE_SRC / "examples" / "debug_inspection.py"
    result = subprocess.run(
        [sys.executable, str(example), str(tmp_path), "alpha"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    data = json.loads(result.stdout)
    dumped = json.dumps(data, ensure_ascii=False)

    assert data["search"]["hits"][0]["body"]["component"] == "HONMON.DIC"
    assert data["entry"]["document"]["schema"] == "lvcore.entry_document.v1"
    assert "span_summaries" in dumped
    assert "raw_row" in dumped
    assert "debug_html" in data["entry"]
    assert "Span summaries" in data["entry"]["debug_html"]


def test_lvcore_cli_search_debug_and_render_profiles(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
    friendly_search = subprocess.run(
        [sys.executable, "-m", "lvcore", "search", str(tmp_path), "alpha", "--search-profile", "exact", "--json", "--limit", "1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    friendly_search_data = json.loads(friendly_search.stdout)
    assert "body" not in friendly_search_data["hits"][0]
    assert "title" not in friendly_search_data["hits"][0]
    assert "index_component" not in friendly_search_data["hits"][0]
    assert "raw_row" not in friendly_search_data["hits"][0]

    search_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "search", str(tmp_path), "alp", "--search-profile", "forward", "--json", "--debug", "--limit", "3"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    search_data = json.loads(search_result.stdout)
    assert search_data["profile"] == "forward"
    assert len(search_data["hits"]) == 3
    assert search_data["hits"][0]["index_component"] == "FHINDEX.DIC"
    assert search_data["hits"][0]["body"]["component"] == "HONMON.DIC"
    assert search_data["hits"][1]["body"] == search_data["hits"][0]["body"]

    render_json = subprocess.run(
        [sys.executable, "-m", "lvcore", "render", str(tmp_path), "alpha", "--search-profile", "exact", "--format", "json", "--limit", "1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    render_json_data = json.loads(render_json.stdout)
    rendered_dump = json.dumps(render_json_data, ensure_ascii=False)
    assert "first entry" in render_json_data["entries"][0]["text"]
    assert "address" not in render_json_data["entries"][0]
    assert "entry" not in render_json_data["entries"][0]
    assert "span_summaries" not in rendered_dump
    assert "raw_row" not in rendered_dump

    render_debug_json = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvcore",
            "render",
            str(tmp_path),
            "alpha",
            "--search-profile",
            "exact",
            "--format",
            "json",
            "--debug",
            "--limit",
            "1",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    render_debug_data = json.loads(render_debug_json.stdout)
    assert render_debug_data["entries"][0]["address"]["component"] == "HONMON.DIC"
    assert "span_summaries" in json.dumps(render_debug_data, ensure_ascii=False)
    assert "raw_row" in json.dumps(render_debug_data, ensure_ascii=False)

    render_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "render", str(tmp_path), "ta", "--search-profile", "backward", "--format", "text", "--limit", "1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    assert "third entry" in render_result.stdout


def test_lvcore_cli_body_source_validate_and_corpus_validate(tmp_path: Path) -> None:
    dense = tmp_path / "_DCT_DENSE"
    make_dense_anchor_package(dense, with_sidecar=True)
    lved = tmp_path / "_DCT_LVED"
    lved.mkdir()
    (lved / "main.data").write_bytes(b"not real")
    multi = tmp_path / "_DCT_MULTI"
    multi.mkdir()
    (multi / "vlpljbl.exe").write_bytes(b"")
    (multi / "blvbat").write_bytes(b"")
    bad = tmp_path / "_DCT_BAD"
    make_synthetic_package(bad)
    (bad / "HONMON.DIC").unlink()

    body_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "body-source", str(dense), "--json"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    body_data = json.loads(body_result.stdout)
    assert body_data["body_source"]["ssed_kind"] == "dense_anchor_with_sidecar"
    assert body_data["body_source"]["sidecar_paths"] == ["body.db"]
    assert "path" not in body_data["body_source"]["sidecars"][0]

    body_debug_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "body-source", str(dense), "--json", "--debug"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    body_debug_data = json.loads(body_debug_result.stdout)
    assert body_debug_data["body_source"]["sidecar_paths"][0].endswith("_DCT_DENSE/body.db")
    assert body_debug_data["body_source"]["sidecars"][0]["path"].endswith("_DCT_DENSE/body.db")

    validate_result = subprocess.run(
        [sys.executable, "-m", "lvcore", "validate", str(dense), "--json", "--debug"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    validate_data = json.loads(validate_result.stdout)
    assert validate_data["body_source"]["ssed_kind"] == "dense_anchor_with_sidecar"
    assert validate_data["sample_search_hits_rendered_html"] >= 1

    corpus_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvcore",
            "corpus-validate",
            str(tmp_path),
            "--json",
            "--jobs",
            "1",
            "--sample-entries",
            "1",
            "--sample-search-hits",
            "1",
            "--output-dir",
            str(tmp_path / "reports"),
            "--progress",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PYTHONPATH": str(LVCORE_SRC)},
    )
    assert corpus_result.returncode == 0
    assert "progress" in corpus_result.stderr
    corpus_data = json.loads(corpus_result.stdout)
    assert corpus_data["schema"] == "lvcore.corpus_validate.v1"
    assert corpus_data["sample_limits"]["sample_entries"] == 1
    assert corpus_data["sample_limits"]["sample_search_hits"] == 1
    assert corpus_data["family_counts"]["ssed"] == 2
    assert corpus_data["family_counts"]["lved_sqlcipher"] == 1
    assert corpus_data["family_counts"]["multiview_sqlite"] == 1
    assert corpus_data["family_deferred_counts"]["lved_sqlcipher"] == 1
    assert corpus_data["family_deferred_counts"]["multiview_sqlite"] == 1
    assert corpus_data["ssed_body_source_kind_counts"]["dense_anchor_with_sidecar"] == 1
    assert corpus_data["ssed_body_source_kind_counts"]["missing_body_component"] == 1
    assert corpus_data["ssed_renderable_count"] == 0
    assert corpus_data["ssed_unsupported_or_unknown_count"] == 1
    assert corpus_data["sidecar_backed_count"] == 1
    assert corpus_data["render_summary"]["search_hits_rendered_html"] >= 1
    assert corpus_data["render_summary"]["search_hits_rendered_text"] >= 1
    assert corpus_data["sidecar_resolution_counts"]["resolved"] >= 1
    assert corpus_data["sidecar_role_counts"]["body_critical"] >= 1
    assert corpus_data["supported_sidecar_role_counts"]["body_critical"] >= 1
    assert corpus_data["sidecar_resolution_counts"]["missing_body_component"] == 1
    assert "sidecar_reference_counts" in corpus_data
    assert corpus_data["sidecar_reference_counts"]["addresses_checked"] >= 1
    assert "resource_resolution_counts" in corpus_data
    assert corpus_data["diagnostics"]["by_severity"]["warning"] >= 1
    assert corpus_data["top_diagnostics_by_code"]["missing_body_component"] >= 1
    assert "top_diagnostics_by_area" in corpus_data
    assert corpus_data["closure_scorecard"]["status"] == "closure_ready_for_deeper_audit"
    assert corpus_data["closure_scorecard"]["hard_ssed_failures"] == 0
    assert corpus_data["closure_scorecard"]["named_residuals"] == []
    assert corpus_data["closure_scorecard"]["ignored_package_integrity_residuals"][0]["kind"] == "missing_body_component"
    assert not any(blocker["code"] == "validation_failed" for blocker in corpus_data["top_blockers"])
    assert corpus_data["failure_count"] == 0
    output_files = corpus_data["output_files"]
    assert Path(output_files["summary_json"]).is_file()
    assert Path(output_files["targets_jsonl"]).is_file()
    assert Path(output_files["failures_jsonl"]).is_file()
    assert Path(output_files["diagnostics_jsonl"]).is_file()
    failure_lines = Path(output_files["failures_jsonl"]).read_text(encoding="utf-8").splitlines()
    assert failure_lines == []
    diagnostics_lines = Path(output_files["diagnostics_jsonl"]).read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["name"] == "_DCT_BAD" for line in diagnostics_lines)
    ssed_target = next(target for target in corpus_data["targets"] if target["package_family"] == "ssed")
    assert ssed_target["gaiji"]["uni_records"] == 0
    assert ssed_target["sample_search_hits_rendered_text"] >= 1


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
