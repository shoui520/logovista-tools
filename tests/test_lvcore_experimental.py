from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


LVCORE_SRC = Path(__file__).resolve().parents[1] / "src" / "lvcore-experimental"
sys.path.insert(0, str(LVCORE_SRC))

from lvcore import Address, Diagnostic, DiagnosticArea, Location, PackageFamily, SearchHit, SearchProfile, SearchResults, Severity, Span, SsedBodySourceKind, detect_family, normalize_query, open_package  # noqa: E402
from lvcore.document import BlockKind, BlockNode, EntryDocument, InlineKind, InlineNode, build_entry_document  # noqa: E402
from lvcore.errors import FormatError  # noqa: E402
from lvcore.gaiji import ga16_glyph_size, parse_ga16  # noqa: E402
from lvcore.index import parse_index  # noqa: E402
from lvcore.model import Entry  # noqa: E402
from lvcore.opcodes import OpcodeCategory, behavior_for  # noqa: E402
from lvcore.render import GaijiPolicy, HtmlProfile, render_html, render_text  # noqa: E402
from lvcore.ssed import BLOCK_SIZE, CHUNK_SIZE, expand_sseddata  # noqa: E402
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
    assert "body" not in exact.hits[0].to_dict()
    assert exact.hits[0].to_dict(debug=True)["body"]["component"] == "HONMON.DIC"

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
    assert "raw_row" not in friendly
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
    assert any(diagnostic.code == "title_dereference_failed" for diagnostic in hit.diagnostics)


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


def test_lvcore_media_refs_become_resources_and_safe_placeholders() -> None:
    raw = b"\x1f\x4d" + bytes(range(18)) + body_text("caption")
    decoded = decode_text_stream(raw)
    entry = Entry(Address(2, 0, "HONMON.DIC"), Address(2, len(raw), "HONMON.DIC"), "media", decoded.text, decoded.spans)
    document = entry.document()
    html = render_html(document)

    assert len(document.resources) == 1
    assert document.resources[0].id == "media-1"
    assert "lvcore-resource://media-1" in html
    assert bytes(range(18)).hex() not in html
    assert any(diagnostic.code == "unresolved_media_ref" for diagnostic in document.diagnostics)


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
            "raw_spans": [{"offset": 7, "op": "e2", "raw": "1fe20007"}],
            "address": {"component": "HONMON.DIC", "block": 2, "offset": 0},
        },
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
    assert "Raw spans" in debug
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
    assert data["resource_resolution"] == {"unresolved_gaiji": 0, "unresolved_media": 0, "unresolved_link": 0}


def test_lvcore_cli_search_debug_and_render_profiles(tmp_path: Path) -> None:
    make_reader_workflow_package(tmp_path)
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
    assert search_data["hits"][0]["body"]["component"] == "HONMON.DIC"
    assert search_data["hits"][1]["body"] == search_data["hits"][0]["body"]

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
    assert corpus_result.returncode == 1
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
    assert corpus_data["ssed_body_source_kind_counts"]["unknown"] == 1
    assert corpus_data["ssed_renderable_count"] == 0
    assert corpus_data["ssed_unsupported_or_unknown_count"] == 1
    assert corpus_data["sidecar_backed_count"] == 1
    assert corpus_data["render_summary"]["search_hits_rendered_html"] >= 1
    assert corpus_data["render_summary"]["search_hits_rendered_text"] >= 1
    assert corpus_data["sidecar_resolution_counts"]["resolved"] >= 1
    assert "resource_resolution_counts" in corpus_data
    assert corpus_data["diagnostics"]["by_severity"]["error"] >= 1
    assert corpus_data["top_diagnostics_by_code"]["unsupported_body_source"] >= 1
    assert "top_diagnostics_by_area" in corpus_data
    assert any(blocker["code"] == "validation_failed" for blocker in corpus_data["top_blockers"])
    assert corpus_data["failure_count"] == 1
    output_files = corpus_data["output_files"]
    assert Path(output_files["summary_json"]).is_file()
    assert Path(output_files["targets_jsonl"]).is_file()
    assert Path(output_files["failures_jsonl"]).is_file()
    assert Path(output_files["diagnostics_jsonl"]).is_file()
    failure_lines = Path(output_files["failures_jsonl"]).read_text(encoding="utf-8").splitlines()
    assert len(failure_lines) == 1
    assert json.loads(failure_lines[0])["name"] == "_DCT_BAD"
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
