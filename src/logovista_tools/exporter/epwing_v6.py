"""EPWING v6 export backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any

from ..ssed import BLOCK_SIZE
from ..writer import (
    GaijiAllocator,
    IndexPointer,
    IndexTarget,
    be16,
    be32,
    encode_body_text,
    encode_bcd_decimal,
    encode_ga16_resource,
    encode_simple_index_pages,
)
from .model import ExportBodyEvent, ExportBook, ExportDiagnostic, ExportEntry


@dataclass(frozen=True)
class EpwingV6Options:
    include_media: bool = True
    drop_style_markers: bool = True
    gaiji_policy: str = "unicode-map,ga16"
    progress: Callable[[str], None] | None = None
    progress_every: int = 5000


@dataclass(frozen=True)
class EpwingV6ExportReport:
    title: str
    source_family: str
    source_id: str
    out_dir: str
    subbook_dir: str
    entries: int
    indexes: int
    navigation: int
    copyright_pages: int
    gaiji_map_entries: int
    diagnostics: tuple[ExportDiagnostic, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source_family": self.source_family,
            "source_id": self.source_id,
            "out_dir": self.out_dir,
            "subbook_dir": self.subbook_dir,
            "entries": self.entries,
            "indexes": self.indexes,
            "navigation": self.navigation,
            "copyright_pages": self.copyright_pages,
            "gaiji_map_entries": self.gaiji_map_entries,
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


def _pad_to_page(data: bytes) -> bytes:
    remainder = len(data) % BLOCK_SIZE
    if remainder:
        return data + bytes(BLOCK_SIZE - remainder)
    return data


def _page_count(data: bytes) -> int:
    return max(1, (len(_pad_to_page(data)) // BLOCK_SIZE))


def _progress(options: EpwingV6Options, message: str) -> None:
    if options.progress is not None:
        options.progress(message)


def _progress_interval(options: EpwingV6Options) -> int:
    return max(1, int(options.progress_every or 5000))


def _write_bytes(path: Path, data: bytes, options: EpwingV6Options) -> None:
    path.write_bytes(data)
    _progress(options, f"export: wrote {path} ({len(data):,} bytes)")


def _write_text(path: Path, text: str, options: EpwingV6Options) -> None:
    path.write_text(text, encoding="utf-8")
    _progress(options, f"export: wrote {path} ({len(text.encode('utf-8')):,} bytes)")


def _safe_subbook_dir(source_id: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "", source_id.upper())
    return (text or "LVBOOK")[:8]


def _jis_label(text: str, max_bytes: int) -> bytes:
    out = bytearray()
    for ch in text:
        if ch == " ":
            pair = b"\x21\x21"
        else:
            try:
                from ..writer import encode_jis_cell, fullwidth_ascii

                pair = encode_jis_cell(ch) or encode_jis_cell(fullwidth_ascii(ch))
            except Exception:
                pair = None
        if pair is None:
            continue
        if len(out) + len(pair) > max_bytes:
            break
        out.extend(pair)
    return bytes(out).ljust(max_bytes, b"\x00")


def _catalogs(book: ExportBook, subbook_dir: str) -> bytes:
    header = bytearray(16)
    header[0:2] = be16(1)
    header[2:4] = be16(6)
    record = bytearray(164)
    record[0:2] = be16(0x0005)
    record[2:82] = _jis_label(book.title, 80)
    record[82:90] = subbook_dir.encode("ascii").ljust(8, b"\x00")
    record[94:96] = be16(1)
    wide_fonts = [b"GA16FULL", b"GA24FULL", b"GA30FULL", b"GA48FULL"]
    narrow_fonts = [b"GA16HALF", b"GA24HALF", b"GA30HALF", b"GA48HALF"]
    for index, name in enumerate(wide_fonts):
        record[100 + index * 8 : 108 + index * 8] = name[:8].ljust(8, b"\x00")
    for index, name in enumerate(narrow_fonts):
        record[132 + index * 8 : 140 + index * 8] = name[:8].ljust(8, b"\x00")

    extra = bytearray(164)
    extra[4:12] = b"HONMON2\x00"
    extra[41:43] = be16(0x0102)
    extra[44:52] = b"HONMONG\x00"
    extra[56:64] = b"HONMONS\x00"
    extra[53] = 0
    extra[54] = 0
    extra[55] = 0
    return _pad_to_page(bytes(header + record + extra))


def _index_record(index_id: int, start_page: int, page_count: int, data: bytes = b"\x02\x01\x55\x40") -> bytes:
    row = bytearray(16)
    row[0] = index_id & 0xFF
    row[2:6] = be32(start_page)
    row[6:10] = be32(page_count)
    row[10:14] = data[:4].ljust(4, b"\x00")
    return bytes(row)


def _index_table(records: list[bytes]) -> bytes:
    if len(records) > 0x7E:
        raise ValueError("EPWING index table has too many records")
    page = bytearray(BLOCK_SIZE)
    page[1] = len(records)
    page[4] = 0
    pos = 16
    for row in records:
        page[pos : pos + 16] = row
        pos += 16
    return bytes(page)


def _body_events_to_epwing(events: tuple[ExportBodyEvent, ...], gaiji: GaijiAllocator, diagnostics: list[ExportDiagnostic], source: str) -> bytes:
    out = bytearray()
    for event in events:
        if event.kind == "text":
            out.extend(encode_body_text(event.text, gaiji))
        elif event.kind == "line_break":
            out.extend(b"\x1f\x0a")
        elif event.kind == "style_start":
            if event.value == "bold":
                out.extend(b"\x1f\xe0\x00\x04")
            elif event.value == "italic":
                out.extend(b"\x1f\x10")
            elif event.value == "sub":
                out.extend(b"\x1f\x06")
            elif event.value == "sup":
                out.extend(b"\x1f\x0e")
        elif event.kind == "style_end":
            if event.value == "bold":
                out.extend(b"\x1f\xe1")
            elif event.value == "italic":
                out.extend(b"\x1f\x11")
            elif event.value == "sub":
                out.extend(b"\x1f\x07")
            elif event.value == "sup":
                out.extend(b"\x1f\x0f")
        elif event.kind == "gaiji_ref":
            out.extend(encode_body_text(f"[{event.value}]", gaiji))
            diagnostics.append(ExportDiagnostic("warning", "gaiji_ref_placeholder", f"gaiji ref {event.value} lowered as text placeholder", source=source))
        elif event.kind == "media_ref":
            out.extend(encode_body_text(f"［{event.text or 'media'}］", gaiji))
            diagnostics.append(ExportDiagnostic("warning", "media_ref_placeholder", "media reference lowered as text placeholder", source=source))
        elif event.kind in {"section", "link", "ruby"}:
            diagnostics.append(ExportDiagnostic("info", f"{event.kind}_not_lowered", f"{event.kind} event has no EPWING v6 lowering yet", source=source))
        elif event.kind == "unsupported":
            diagnostics.append(ExportDiagnostic("warning", "unsupported_body_event", event.text or event.value, source=source))
    return bytes(out)


def _candidate_bytes(label: str, pointer: IndexPointer, gaiji: GaijiAllocator) -> bytes:
    """Encode an EPWING menu/candidate item pointing to another text position."""

    out = bytearray()
    out.extend(b"\x1f\x43")
    out.extend(encode_body_text(label, gaiji))
    out.extend(b"\x1f\x63")
    out.extend(encode_bcd_decimal(pointer.block, 8))
    out.extend(encode_bcd_decimal(pointer.offset, 4))
    out.extend(b"\x1f\x0a")
    return bytes(out)


def _entry_bytes(entry: ExportEntry, gaiji: GaijiAllocator, diagnostics: list[ExportDiagnostic]) -> bytes:
    out = bytearray()
    out.extend(b"\x1f\x09\x00\x01")
    out.extend(b"\x1f\x41\x00\x00")
    out.extend(encode_body_text(entry.headword, gaiji))
    out.extend(b"\x1f\x61")
    out.extend(b"\x1f\x0a")
    out.extend(_body_events_to_epwing(entry.body.events, gaiji, diagnostics, entry.target))
    out.extend(b"\x1f\x0a")
    return bytes(out)


def _append_index_target(targets: list[IndexTarget], seen: set[tuple[str, int, int]], key: str, pointer: IndexPointer) -> None:
    clean = " ".join(key.split()).strip()
    if not clean:
        return
    marker = (clean.casefold(), pointer.block, pointer.offset)
    if marker in seen:
        return
    seen.add(marker)
    targets.append(IndexTarget(key=clean, body=pointer, title=pointer))


def _map_lines(gaiji: GaijiAllocator, book: ExportBook) -> str:
    source_map = {row.code.upper(): row.unicode for row in book.gaiji if row.unicode}
    lines = [f"# {book.title} generated by logovista-tools exporter", ""]
    for row in sorted(gaiji.assignments.values(), key=lambda item: item.code):
        prefix = "h" if row.space == "half" else "z"
        code = row.key.upper()
        value = source_map.get(code) or row.text
        units = ",".join(f"u{ord(ch):04X}" for ch in value)
        lines.append(f"{prefix}{code}\t{units}\t{value}")
    for code, value in sorted(source_map.items()):
        if any(row.key.upper() == code for row in gaiji.assignments.values()):
            continue
        prefix = "h" if int(code[:2], 16) < 0xB0 else "z"
        units = ",".join(f"u{ord(ch):04X}" for ch in value)
        lines.append(f"{prefix}{code}\t{units}\t{value}")
    return "\n".join(lines).rstrip() + "\n"


class EpwingV6Backend:
    """Write a conservative EPWING v6 package from an export book."""

    def __init__(self, options: EpwingV6Options | None = None) -> None:
        self.options = options or EpwingV6Options()

    def write(self, book: ExportBook, out_dir: Path) -> EpwingV6ExportReport:
        _progress(self.options, f"export: EPWING v6 backend preparing {out_dir}")
        out_dir.mkdir(parents=True, exist_ok=True)
        subbook_dir = _safe_subbook_dir(book.source_id)
        data_dir = out_dir / subbook_dir / "DATA"
        gaiji_dir = out_dir / subbook_dir / "GAIJI"
        data_dir.mkdir(parents=True, exist_ok=True)
        gaiji_dir.mkdir(parents=True, exist_ok=True)
        _progress(self.options, f"export: EPWING subbook={subbook_dir} entries={len(book.entries):,}")

        diagnostics: list[ExportDiagnostic] = list(book.diagnostics)
        gaiji = GaijiAllocator()
        body = bytearray(bytes(BLOCK_SIZE))
        word_targets: list[IndexTarget] = []
        endword_targets: list[IndexTarget] = []
        seen_word_targets: set[tuple[str, int, int]] = set()
        seen_endword_targets: set[tuple[str, int, int]] = set()
        target_positions: dict[str, IndexPointer] = {}

        interval = _progress_interval(self.options)
        _progress(self.options, "export: encoding entries into HONMON2 body stream")
        for entry_index, entry in enumerate(book.entries, start=1):
            offset = len(body)
            pointer = IndexPointer(block=(offset // BLOCK_SIZE) + 1, offset=offset % BLOCK_SIZE)
            target_positions[entry.target] = pointer
            body.extend(_entry_bytes(entry, gaiji, diagnostics))
            for key in entry.keys:
                _append_index_target(word_targets, seen_word_targets, key, pointer)
                _append_index_target(endword_targets, seen_endword_targets, key, pointer)
            if entry_index == 1 or entry_index % interval == 0 or entry_index == len(book.entries):
                _progress(self.options, f"export: encoded {entry_index:,}/{len(book.entries):,} entries ({len(body):,} body bytes)")
        entry_body_page_count = max(1, (len(_pad_to_page(bytes(body))) - BLOCK_SIZE) // BLOCK_SIZE)

        unmatched_index_rows = 0
        _progress(self.options, f"export: applying {len(book.indexes):,} source index row(s)")
        for index in book.indexes:
            pointer = target_positions.get(index.target)
            if pointer is None:
                unmatched_index_rows += 1
                continue
            if index.mode == "backward":
                _append_index_target(endword_targets, seen_endword_targets, index.key, pointer)
            elif index.mode in {"forward", "exact", "keyword", "cross-reference", "body-only", "unknown"}:
                _append_index_target(word_targets, seen_word_targets, index.key, pointer)
        if unmatched_index_rows:
            diagnostics.append(
                ExportDiagnostic(
                    "info",
                    "source_index_targets_unmatched",
                    f"{unmatched_index_rows} source index rows pointed outside the exported entry set.",
                )
            )
        _progress(
            self.options,
            f"export: prepared index targets word={len(word_targets):,} endword={len(endword_targets):,} unmatched={unmatched_index_rows:,}",
        )

        menu_start = 0
        menu_pages = 0
        if book.navigation:
            _progress(self.options, f"export: encoding {len(book.navigation):,} navigation row(s)")
            body = bytearray(_pad_to_page(bytes(body)))
            menu_start = len(body) // BLOCK_SIZE + 1
            for node in book.navigation:
                pointer = target_positions.get(node.target or "")
                if pointer is not None:
                    body.extend(_candidate_bytes(node.label, pointer, gaiji))
                else:
                    body.extend(encode_body_text(node.label, gaiji))
                    body.extend(b"\x1f\x0a")
                    diagnostics.append(
                        ExportDiagnostic(
                            "warning",
                            "epwing_menu_target_unmatched",
                            f"Navigation item {node.label!r} does not point to an exported entry; emitted as non-clickable text.",
                            source=node.target,
                        )
                    )
            menu_pages = _page_count(bytes(body[(menu_start - 1) * BLOCK_SIZE :]))

        copyright_start = 0
        copyright_pages = 0
        if book.copyright:
            _progress(self.options, f"export: encoding {len(book.copyright):,} copyright/info row(s)")
            body = bytearray(_pad_to_page(bytes(body)))
            copyright_start = len(body) // BLOCK_SIZE + 1
            for node in book.copyright:
                body.extend(encode_body_text(node.label, gaiji))
                body.extend(b"\x1f\x0a")
            copyright_pages = _page_count(bytes(body[(copyright_start - 1) * BLOCK_SIZE :]))

        body = bytearray(_pad_to_page(bytes(body)))
        index_records: list[bytes] = [_index_record(0x00, 2, entry_body_page_count, b"\x00\x00\x00\x00")]
        if word_targets:
            _progress(self.options, f"export: writing forward index with {len(word_targets):,} target(s)")
            word_start = len(body) // BLOCK_SIZE + 1
            word_index = encode_simple_index_pages(word_targets, start_block=word_start, gaiji=gaiji)
            body.extend(word_index)
            index_records.append(_index_record(0x91, word_start, _page_count(word_index)))
        if endword_targets:
            _progress(self.options, f"export: writing backward index with {len(endword_targets):,} target(s)")
            endword_start = len(body) // BLOCK_SIZE + 1
            endword_index = encode_simple_index_pages(endword_targets, start_block=endword_start, gaiji=gaiji, reverse_keys=True)
            body.extend(endword_index)
            index_records.append(_index_record(0x71, endword_start, _page_count(endword_index)))
        if menu_start:
            index_records.append(_index_record(0x01, menu_start, max(1, menu_pages), b"\x00\x00\x00\x00"))
        if copyright_start:
            index_records.append(_index_record(0x02, copyright_start, max(1, copyright_pages), b"\x00\x00\x00\x00"))
        body[0:BLOCK_SIZE] = _index_table(index_records)
        _progress(self.options, f"export: assembled HONMON2 with {len(index_records):,} index record(s)")

        _write_bytes(data_dir / "HONMON2", _pad_to_page(bytes(body)), self.options)
        _write_bytes(data_dir / "HONMONG", bytes(BLOCK_SIZE), self.options)
        _write_bytes(data_dir / "HONMONS", bytes(BLOCK_SIZE), self.options)
        _write_bytes(out_dir / "CATALOGS", _catalogs(book, subbook_dir), self.options)

        _write_bytes(gaiji_dir / "GA16HALF", encode_ga16_resource(gaiji.half_assignments, width=8, start_code=gaiji.half_start), self.options)
        _write_bytes(gaiji_dir / "GA16FULL", encode_ga16_resource(gaiji.full_assignments, width=16, start_code=gaiji.full_start), self.options)
        map_text = _map_lines(gaiji, book)
        _write_text(out_dir / f"{subbook_dir}.map", map_text, self.options)

        if self.options.include_media and book.resources:
            diagnostics.append(
                ExportDiagnostic(
                    "warning",
                    "epwing_media_payload_lowering_deferred",
                    "Resource payload cataloging is present in the export IR, but native HONMONG/HONMONS media controls are not implemented yet.",
                )
            )

        report = EpwingV6ExportReport(
            title=book.title,
            source_family=book.source_family.value,
            source_id=book.source_id,
            out_dir=str(out_dir),
            subbook_dir=subbook_dir,
            entries=len(book.entries),
            indexes=len(index_records),
            navigation=len(book.navigation),
            copyright_pages=len(book.copyright),
            gaiji_map_entries=sum(1 for line in map_text.splitlines() if line and not line.startswith("#")),
            diagnostics=tuple(diagnostics),
        )
        _write_text(out_dir / "logovista-export-report.json", json.dumps(report.as_dict(), ensure_ascii=False, indent=2), self.options)
        _progress(
            self.options,
            f"export: finished EPWING v6 export entries={report.entries:,} indexes={report.indexes:,} diagnostics={len(report.diagnostics):,}",
        )
        return report
