"""Lossless SSED catalog/component repacker.

The authoring writer intentionally emits new, valid SSED containers.  This
module has a narrower purpose: parse an existing package's SSEDINFO/SSEDDATA
physical layout and re-emit the same bytes from that parsed model.  A successful
byte-identical round-trip proves the observed container grammar for that
package without depending on vendor-compressor parity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .ccaltstr import parse_ccaltstr
from .entries import DictionarySource, discover_dictionaries
from .gaiji import is_bitmap_gaiji_resource_name, parse_ga16_resource, parse_uni_resource
from .panels import parse_panel_bin
from .ssed import (
    BLOCK_SIZE,
    SSEDINFO_MAGIC,
    SSEDDATA_MAGIC,
    be16,
    be32,
    find_case_insensitive,
    parse_sseddata_header,
    parse_ssedinfo_with_layout,
    read_file_prefix,
    ssed_chunk_offsets,
    sseddata_storage_for_file,
)
from .spindex import inspect_spindex
from .windows import parse_aux_index_text
from .windows import (
    classify_sqlite_sidecar_file,
    file_magic_kind,
    is_vlpljbl_path,
    sqlite_storage_for_path,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


EXCLUDED_SUFFIXES = frozenset(
    {
        ".chm",
        ".css",
        ".dll",
        ".dtd",
        ".gif",
        ".htm",
        ".html",
        ".ini",
        ".jpg",
        ".jpeg",
        ".js",
        ".m4a",
        ".mp3",
        ".png",
        ".svg",
        ".wav",
        ".xml",
    }
)
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        "_sound_files",
        "gaijitemp",
        "hanrei",
        "html",
        "htmls",
        "image",
        "images",
        "img",
        "sound_files",
        "templates",
    }
)
SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})


def _relpath(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _has_excluded_directory(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(part.casefold() in EXCLUDED_DIRECTORY_NAMES for part in parts)


def _is_frontend_or_loose_media(path: Path, root: Path) -> bool:
    name = path.name.casefold()
    if name in {"vlpljbl.bin", "vlpljbl.exe"}:
        return True
    if _has_excluded_directory(path, root):
        return True
    return path.suffix.casefold() in EXCLUDED_SUFFIXES


def _looks_like_sqlite_database(path: Path) -> bool:
    suffix = path.suffix.casefold()
    if suffix in SQLITE_SUFFIXES:
        return True
    if is_vlpljbl_path(path) and path.name.casefold() not in {"vlpljbl.bin", "vlpljbl.exe"}:
        return sqlite_storage_for_path(path) is not None
    if suffix:
        return False
    return sqlite_storage_for_path(path) is not None


def _is_panel_bin(path: Path, root: Path) -> bool:
    if path.suffix.casefold() != ".bin":
        return False
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(part.casefold().startswith("panel") for part in parts)


def _is_requested_repack_file(path: Path, root: Path) -> bool:
    if not path.is_file() or _is_frontend_or_loose_media(path, root):
        return False
    suffix = path.suffix.casefold()
    name = path.name.casefold()
    if suffix in {".dic", ".idx", ".uni"}:
        return True
    if is_bitmap_gaiji_resource_name(path):
        return True
    if name.startswith("ccaltstr"):
        return True
    if _is_panel_bin(path, root):
        return True
    if _looks_like_sqlite_database(path):
        return True
    return False


@dataclass(frozen=True)
class SsedChunkModel:
    index: int
    offset: int
    byte_length: int
    reserved: bytes
    command_count: int
    init_byte: int
    command_bytes_sha256: str
    trailing_bytes: bytes
    expanded_length: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "offset": self.offset,
            "byte_length": self.byte_length,
            "reserved_hex": self.reserved.hex(),
            "command_count": self.command_count,
            "init_byte": self.init_byte,
            "command_bytes_sha256": self.command_bytes_sha256,
            "trailing_bytes_hex": self.trailing_bytes.hex(),
            "expanded_length": self.expanded_length,
        }


@dataclass(frozen=True)
class RepackedFile:
    path: str
    status: str
    kind: str
    storage: str | None
    original_size: int
    repacked_size: int
    original_sha256: str
    repacked_sha256: str
    byte_identical: bool
    expanded_sha256: str | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "kind": self.kind,
            "storage": self.storage,
            "original_size": self.original_size,
            "repacked_size": self.repacked_size,
            "original_sha256": self.original_sha256,
            "repacked_sha256": self.repacked_sha256,
            "byte_identical": self.byte_identical,
            "expanded_sha256": self.expanded_sha256,
            "details": self.details or {},
        }


@dataclass(frozen=True)
class PackageRepackReport:
    dict_id: str
    idx: str
    title: str
    output_dir: str
    files: tuple[RepackedFile, ...]
    missing_declared_components: tuple[str, ...]
    unsupported_components: tuple[dict[str, Any], ...]
    excluded_loose_files: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return all(row.byte_identical for row in self.files) and not self.unsupported_components

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "logovista-lossless-repack-report-v1",
            "dict_id": self.dict_id,
            "idx": self.idx,
            "title": self.title,
            "output_dir": self.output_dir,
            "ok": self.ok,
            "files": [row.as_dict() for row in self.files],
            "missing_declared_components": list(self.missing_declared_components),
            "unsupported_components": list(self.unsupported_components),
            "excluded_loose_files": list(self.excluded_loose_files),
        }


def _parse_chunk_model(
    data: bytes,
    index: int,
    offset: int,
    next_offset: int,
    *,
    validate_expand: bool = False,
) -> tuple[SsedChunkModel, bytes, bytes | None]:
    if offset < 0 or next_offset < offset or next_offset > len(data):
        raise ValueError(f"invalid chunk offset range {index}: {offset}..{next_offset}")
    payload = data[offset:next_offset]
    if len(payload) < 5:
        raise ValueError(f"short SSEDDATA chunk {index} at {offset}")
    command_count = be16(payload, 2)
    command_end = 5 + command_count * 3
    if command_end > len(payload):
        raise ValueError(
            f"SSEDDATA chunk {index} command table exceeds payload: "
            f"{command_end} > {len(payload)}"
        )
    command_bytes = payload[5:command_end]
    trailing = payload[command_end:]
    expanded = _expand_segment_payload(payload) if validate_expand else None
    expanded_length = len(expanded) if expanded is not None else None
    return (
        SsedChunkModel(
            index=index,
            offset=offset,
            byte_length=len(payload),
            reserved=payload[:2],
            command_count=command_count,
            init_byte=payload[4],
            command_bytes_sha256=_sha256(command_bytes),
            trailing_bytes=trailing,
            expanded_length=expanded_length,
        ),
        payload,
        expanded,
    )


def _expand_segment_payload(payload: bytes) -> bytes:
    # expand_sseddata_chunk is deliberately low-level but expects the containing
    # byte string plus a chunk offset.  A segment payload starts at offset 0.
    from .ssed import expand_sseddata_chunk

    return expand_sseddata_chunk(payload, 0)


def repack_sseddata_bytes(data: bytes, *, validate_expand: bool = False) -> tuple[bytes, dict[str, Any]]:
    if data[:8] != SSEDDATA_MAGIC:
        raise ValueError("not plain SSEDDATA")
    n_chunk = be16(data, 0x16)
    if n_chunk <= 0:
        raise ValueError("SSEDDATA has no chunks")
    header_size = 64 + n_chunk * 4
    if len(data) < header_size:
        raise ValueError("SSEDDATA header/offset table is truncated")
    offsets = ssed_chunk_offsets(data)
    if any(offset < header_size or offset >= len(data) for offset in offsets):
        raise ValueError("SSEDDATA chunk offset outside file")
    if offsets != sorted(offsets):
        raise ValueError("SSEDDATA chunk offsets are not sorted")

    pre_chunk_gap = data[header_size : offsets[0]]
    payloads: list[bytes] = []
    chunks: list[SsedChunkModel] = []
    expanded_hasher = hashlib.sha256() if validate_expand else None
    expanded_bytes = 0
    for index, offset in enumerate(offsets):
        next_offset = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        model, payload, expanded = _parse_chunk_model(data, index, offset, next_offset, validate_expand=validate_expand)
        chunks.append(model)
        payloads.append(payload)
        if expanded is not None and expanded_hasher is not None:
            expanded_hasher.update(expanded)
            expanded_bytes += len(expanded)

    header = bytearray(data[:64])
    header[:8] = SSEDDATA_MAGIC
    header[0x16:0x18] = n_chunk.to_bytes(2, "big")
    cursor = header_size + len(pre_chunk_gap)
    rebuilt_offsets: list[int] = []
    for payload in payloads:
        rebuilt_offsets.append(cursor)
        cursor += len(payload)

    out = bytearray(header)
    for offset in rebuilt_offsets:
        out.extend(offset.to_bytes(4, "big"))
    out.extend(pre_chunk_gap)
    for payload in payloads:
        out.extend(payload)

    details = {
        "kind": data[0x0F],
        "chunks": n_chunk,
        "start_block": be32(data, 0x18),
        "end_block": be32(data, 0x1C),
        "expanded_bytes": expanded_bytes if validate_expand else None,
        "expanded_sha256": expanded_hasher.hexdigest() if expanded_hasher is not None else None,
        "pre_chunk_gap_bytes": len(pre_chunk_gap),
        "nonzero_reserved_header_offsets": [
            offset
            for offset, value in enumerate(data[:64])
            if value and offset not in set(range(0, 8)) | {0x0F, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F}
        ],
        "chunk_models": [chunk.as_dict() for chunk in chunks],
    }
    return bytes(out), details


def repack_ssedinfo_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    if data[:8] != SSEDINFO_MAGIC:
        raise ValueError("not SSEDINFO")
    title, elements, layout = parse_ssedinfo_with_layout(path)
    out = bytearray(data[: layout.record_start])
    out[:8] = SSEDINFO_MAGIC
    out[layout.component_count_offset] = layout.component_count
    for element in elements:
        pos = layout.record_start + element.index * layout.record_size
        rec = bytearray(data[pos : pos + layout.record_size])
        filename = element.filename.encode("ascii")
        if len(filename) > len(rec) - 0x11:
            raise ValueError(f"SSEDINFO filename is too long: {element.filename}")
        rec[2] = element.multi
        rec[3] = element.type
        rec[4:8] = element.start.to_bytes(4, "big")
        rec[8:12] = element.end.to_bytes(4, "big")
        rec[12:16] = element.data
        rec[0x10] = len(filename)
        rec[0x11 : 0x11 + len(filename)] = filename
        out.extend(rec)
    table_end = layout.record_start + layout.component_count * layout.record_size
    out.extend(data[table_end:])
    details = {
        "title": title,
        "component_count": layout.component_count,
        "component_count_offset": layout.component_count_offset,
        "record_start": layout.record_start,
        "record_size": layout.record_size,
        "trailing_bytes": layout.trailing_bytes,
        "components": [
            {
                "index": element.index,
                "filename": element.filename,
                "multi": element.multi,
                "type": element.type,
                "start": element.start,
                "end": element.end,
                "data_hex": element.data.hex(),
            }
            for element in elements
        ],
    }
    return bytes(out), details


def _repacked_row(
    *,
    source_path: Path,
    package_root: Path,
    kind: str,
    storage: str | None,
    original: bytes,
    repacked: bytes,
    expanded_sha256: str | None,
    details: dict[str, Any],
) -> RepackedFile:
    try:
        rel = source_path.relative_to(package_root).as_posix()
    except ValueError:
        rel = source_path.name
    return RepackedFile(
        path=rel,
        status="byte_identical" if original == repacked else "changed",
        kind=kind,
        storage=storage,
        original_size=len(original),
        repacked_size=len(repacked),
        original_sha256=_sha256(original),
        repacked_sha256=_sha256(repacked),
        byte_identical=original == repacked,
        expanded_sha256=expanded_sha256,
        details=details,
    )


def repack_catalog_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    original = path.read_bytes()
    repacked, details = repack_ssedinfo_bytes(path)
    if write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(repacked)
    return _repacked_row(
        source_path=path,
        package_root=package_root,
        kind="SSEDINFO",
        storage="plain",
        original=original,
        repacked=repacked,
        expanded_sha256=None,
        details=details,
    )


def repack_data_file(
    path: Path,
    package_root: Path,
    out_path: Path,
    *,
    write: bool = True,
    expanded_hash: bool = False,
) -> RepackedFile:
    storage = sseddata_storage_for_file(path)
    original = path.read_bytes()
    if storage != "plain":
        raise ValueError(f"lossless physical repack currently supports plain SSEDDATA only; storage={storage}")
    repacked, details = repack_sseddata_bytes(original, validate_expand=expanded_hash)
    expanded_sha256 = details.get("expanded_sha256") if expanded_hash else None
    if write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(repacked)
    return _repacked_row(
        source_path=path,
        package_root=package_root,
        kind="SSEDDATA",
        storage=storage,
        original=original,
        repacked=repacked,
        expanded_sha256=expanded_sha256,
        details=details,
    )


def repack_encrypted_data_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    header = parse_sseddata_header(path)
    storage = str(header["storage"])
    if storage == "plain":
        raise ValueError("encrypted-data repacker called for plain SSEDDATA")
    return _identity_repacked_file(
        path,
        package_root,
        out_path,
        kind="SSEDDATA",
        storage=storage,
        write=write,
        details={
            "kind": int(header["kind"]),
            "chunks": int(header["n_chunk"]),
            "start_block": int(header["start_block"]),
            "end_block": int(header["end_block"]),
            "physical_policy": "byte_preserving_encrypted_payload_with_decrypted_header_validation",
        },
    )


def _write_repacked(out_path: Path, data: bytes, *, write: bool) -> None:
    if not write:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)


def _identity_repacked_file(
    path: Path,
    package_root: Path,
    out_path: Path,
    *,
    kind: str,
    storage: str | None,
    details: dict[str, Any],
    write: bool = True,
) -> RepackedFile:
    original = path.read_bytes()
    _write_repacked(out_path, original, write=write)
    return _repacked_row(
        source_path=path,
        package_root=package_root,
        kind=kind,
        storage=storage,
        original=original,
        repacked=original,
        expanded_sha256=None,
        details=details,
    )


def repack_ccaltstr_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    original = path.read_bytes()
    table = parse_ccaltstr(original)
    out = bytearray()
    out.extend(table.magic)
    out.extend(table.version.to_bytes(2, "little"))
    out.extend(table.start_code.to_bytes(2, "big"))
    out.extend(table.record_count.to_bytes(2, "big"))
    out.extend(table.reserved)
    for record in table.records:
        value = record.value_bytes
        if len(value) > 60:
            raise ValueError(f"CCALTSTR record {record.index} value exceeds 60 bytes")
        out.extend(record.code.to_bytes(2, "big"))
        out.extend(value.ljust(60, b"\x00"))
    repacked = bytes(out)
    _write_repacked(out_path, repacked, write=write)
    return _repacked_row(
        source_path=path,
        package_root=package_root,
        kind="CCALTSTR",
        storage="plain",
        original=original,
        repacked=repacked,
        expanded_sha256=None,
        details={
            "kind": table.kind,
            "magic": table.magic.decode("ascii", errors="replace"),
            "version": table.version,
            "start_code": table.start_code_hex,
            "record_count": table.record_count,
            "nonempty_values": sum(1 for record in table.records if record.value_bytes),
        },
    )


def repack_uni_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    resource = parse_uni_resource(path)
    if resource is None:
        raise ValueError("not a recognized LogoVista .uni resource")
    return _identity_repacked_file(
        path,
        package_root,
        out_path,
        kind="UNI",
        storage="plain",
        write=write,
        details={
            "format": resource.format,
            "half_count": resource.half_count,
            "full_count": resource.full_count,
            "parsed_records": len(resource.records),
            "expected_size": resource.expected_size,
            "trailing_bytes": resource.trailing_bytes,
        },
    )


def repack_ga16_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    resource = parse_ga16_resource(path)
    if resource is None:
        raise ValueError("not a recognized GA16/GAI16 bitmap-gaiji resource")
    data_size = path.stat().st_size
    expected_min_size = resource.data_offset + resource.count * resource.glyph_bytes
    if data_size < expected_min_size:
        raise ValueError(f"GA16 resource truncated: expected at least {expected_min_size} bytes, actual {data_size}")
    return _identity_repacked_file(
        path,
        package_root,
        out_path,
        kind="GA16",
        storage="plain",
        write=write,
        details={
            "width": resource.width,
            "height": resource.height,
            "start_code": f"{resource.start_code:04x}",
            "count": resource.count,
            "glyph_bytes": resource.glyph_bytes,
            "data_offset": resource.data_offset,
            "trailing_bytes": data_size - expected_min_size,
        },
    )


def repack_panel_bin_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    data = path.read_bytes()
    panel = parse_panel_bin(data)
    _write_repacked(out_path, data, write=write)
    return _repacked_row(
        source_path=path,
        package_root=package_root,
        kind="Panel BIN",
        storage="plain",
        original=data,
        repacked=data,
        expanded_sha256=None,
        details={
            "format": panel.format,
            "declared_record_count": panel.declared_record_count,
            "actual_record_count": panel.actual_record_count,
            "record_stride": panel.record_stride,
            "text_width": panel.text_width,
            "text_encoding": panel.text_encoding,
        },
    )


def repack_spindex_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    summary = inspect_spindex(path)
    details = dict(summary)
    details.pop("path", None)
    details.pop("rows_path", None)
    return _identity_repacked_file(
        path,
        package_root,
        out_path,
        kind="SPINDEX",
        storage=str(details.get("storage") or "plain"),
        write=write,
        details=details,
    )


def repack_aux_index_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    rows = parse_aux_index_text(path)
    if not rows:
        raise ValueError("not a recognized auxiliary text IDX")
    return _identity_repacked_file(
        path,
        package_root,
        out_path,
        kind="auxiliary text IDX",
        storage="cp932_text",
        write=write,
        details={
            "rows": len(rows),
            "max_depth": max(row.depth for row in rows),
            "first_block": rows[0].block,
            "last_block": rows[-1].block,
        },
    )


def _plain_sqlite_details(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = str(con.execute("pragma integrity_check").fetchone()[0])
        page_size = int(con.execute("pragma page_size").fetchone()[0])
        page_count = int(con.execute("pragma page_count").fetchone()[0])
        user_version = int(con.execute("pragma user_version").fetchone()[0])
        schema_version = int(con.execute("pragma schema_version").fetchone()[0])
    finally:
        con.close()
    return {
        "integrity_check": integrity,
        "page_size": page_size,
        "page_count": page_count,
        "user_version": user_version,
        "schema_version": schema_version,
    }


def repack_sqlite_file(path: Path, package_root: Path, out_path: Path, *, write: bool = True) -> RepackedFile:
    classification = classify_sqlite_sidecar_file(path)
    if classification.storage is None:
        raise ValueError(f"not a recognized SQLite database: content_kind={classification.content_kind}")
    details: dict[str, Any] = {
        "role": classification.role,
        "content_kind": classification.content_kind,
        "tables": list(classification.tables),
        "logical_rebuild": False,
        "physical_policy": "byte_preserving_with_sqlite_validation",
    }
    if classification.storage == "plain":
        details.update(_plain_sqlite_details(path))
    return _identity_repacked_file(
        path,
        package_root,
        out_path,
        kind="SQLite",
        storage=classification.storage,
        write=write,
        details=details,
    )


def _repack_requested_file(
    path: Path,
    package_root: Path,
    out_path: Path,
    *,
    write: bool = True,
    expanded_hash: bool = False,
) -> RepackedFile:
    prefix = read_file_prefix(path, BLOCK_SIZE)
    suffix = path.suffix.casefold()
    name = path.name.casefold()
    if prefix.startswith(SSEDINFO_MAGIC):
        return repack_catalog_file(path, package_root, out_path, write=write)
    if name == "spindex.dic":
        return repack_spindex_file(path, package_root, out_path, write=write)
    ssed_storage = sseddata_storage_for_file(path)
    if ssed_storage == "plain":
        return repack_data_file(path, package_root, out_path, write=write, expanded_hash=expanded_hash)
    if ssed_storage in {"logofont_cipher", "macos_logofont_cipher"}:
        return repack_encrypted_data_file(path, package_root, out_path, write=write)
    if name.startswith("ccaltstr"):
        return repack_ccaltstr_file(path, package_root, out_path, write=write)
    if suffix == ".uni":
        return repack_uni_file(path, package_root, out_path, write=write)
    if is_bitmap_gaiji_resource_name(path):
        return repack_ga16_file(path, package_root, out_path, write=write)
    if _is_panel_bin(path, package_root):
        return repack_panel_bin_file(path, package_root, out_path, write=write)
    if _looks_like_sqlite_database(path):
        return repack_sqlite_file(path, package_root, out_path, write=write)
    if suffix == ".idx":
        return repack_aux_index_file(path, package_root, out_path, write=write)
    magic_kind = file_magic_kind(prefix)
    raise ValueError(f"no lossless repacker for requested file family; magic={magic_kind}")


def _selected_extra_files(root: Path, selected: set[Path]) -> tuple[Path, ...]:
    rows: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: (_relpath(item, root).casefold(), _relpath(item, root))):
        if path.resolve() in selected or not _is_requested_repack_file(path, root):
            continue
        rows.append(path)
    return tuple(rows)


def _excluded_loose_files(root: Path, selected: set[Path]) -> tuple[str, ...]:
    excluded: list[str] = []
    for path in sorted(root.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        if path.resolve() in selected:
            continue
        if path.is_dir():
            excluded.append(f"{path.name}/")
        else:
            excluded.append(path.name)
    return tuple(excluded)


def repack_dictionary_source(
    source: DictionarySource,
    out_dir: Path,
    *,
    write: bool = True,
    expanded_hash: bool = False,
) -> PackageRepackReport:
    package_root = source.idx.parent
    dict_out = out_dir / source.dict_id
    title, elements, _layout = parse_ssedinfo_with_layout(source.idx)
    files: list[RepackedFile] = []
    missing: list[str] = []
    unsupported: list[dict[str, Any]] = []
    selected_paths: set[Path] = {source.idx.resolve()}

    files.append(repack_catalog_file(source.idx, package_root, dict_out / _relpath(source.idx, package_root), write=write))
    for element in elements:
        if not element.start:
            continue
        component_path = find_case_insensitive(package_root, element.filename)
        if component_path is None:
            missing.append(element.filename)
            continue
        selected_paths.add(component_path.resolve())
        try:
            files.append(
                _repack_requested_file(
                    component_path,
                    package_root,
                    dict_out / _relpath(component_path, package_root),
                    write=write,
                    expanded_hash=expanded_hash,
                )
            )
        except Exception as exc:
            unsupported.append(
                {
                    "filename": element.filename,
                    "type": f"{element.type:02x}",
                    "reason": str(exc),
                }
            )

    for extra_path in _selected_extra_files(package_root, selected_paths):
        selected_paths.add(extra_path.resolve())
        rel = _relpath(extra_path, package_root)
        try:
            files.append(
                _repack_requested_file(
                    extra_path,
                    package_root,
                    dict_out / rel,
                    write=write,
                    expanded_hash=expanded_hash,
                )
            )
        except Exception as exc:
            unsupported.append(
                {
                    "filename": rel,
                    "type": "loose",
                    "reason": str(exc),
                }
            )

    report = PackageRepackReport(
        dict_id=source.dict_id,
        idx=source.idx.name,
        title=title,
        output_dir=str(dict_out),
        files=tuple(files),
        missing_declared_components=tuple(missing),
        unsupported_components=tuple(unsupported),
        excluded_loose_files=_excluded_loose_files(package_root, selected_paths),
    )
    if write:
        _write_json(dict_out / "repack-report.json", report.as_dict())
    return report


def repack_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(
        [args.root],
        jobs=getattr(args, "jobs", 1),
        dict_ids=args.dict,
        include_gaiji=False,
        include_images=False,
    )
    if not sources:
        raise ValueError("repacker: no SSED dictionaries found")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    reports = [
        repack_dictionary_source(
            source,
            args.out_dir,
            write=not args.check_only,
            expanded_hash=bool(args.expanded_hash),
        ).as_dict()
        for source in sources
    ]
    _write_json(args.out_dir / "summary.json", reports)
    return reports


def add_repacker_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, help="Dictionary package directory, collection root, or direct .IDX path.")
    parser.add_argument("--dict", action="append", help="Dictionary id to repack. Repeat for multiple packages.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Reports/output directory for repacked dictionary files.")
    parser.add_argument("--check-only", action="store_true", help="Verify round-trip without writing repacked dictionary files.")
    parser.add_argument(
        "--expanded-hash",
        action="store_true",
        help="Also expand each SSEDDATA component and record expanded-byte SHA-256. Slower on large media/body components.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
