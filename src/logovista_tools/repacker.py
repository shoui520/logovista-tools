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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import DictionarySource, discover_dictionaries
from .ssed import (
    BLOCK_SIZE,
    SSEDINFO_MAGIC,
    SSEDDATA_MAGIC,
    be16,
    be32,
    find_case_insensitive,
    parse_ssedinfo_with_layout,
    ssed_chunk_offsets,
    sseddata_storage_for_file,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


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


def _excluded_loose_files(root: Path, selected: set[Path]) -> tuple[str, ...]:
    excluded: list[str] = []
    for path in sorted(root.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        if path in selected:
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
    selected_paths: set[Path] = {source.idx}

    files.append(repack_catalog_file(source.idx, package_root, dict_out / source.idx.name, write=write))
    for element in elements:
        if not element.start:
            continue
        component_path = find_case_insensitive(package_root, element.filename)
        if component_path is None:
            missing.append(element.filename)
            continue
        selected_paths.add(component_path)
        try:
            files.append(
                repack_data_file(
                    component_path,
                    package_root,
                    dict_out / component_path.name,
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
    if len(sources) > 1 and not args.dict:
        raise ValueError("repacker: multiple dictionaries found; pass --dict to select packages")
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
    parser.add_argument("--out-dir", type=Path, required=True, help="Reports/output directory for repacked SSED files.")
    parser.add_argument("--check-only", action="store_true", help="Verify round-trip without writing repacked component files.")
    parser.add_argument(
        "--expanded-hash",
        action="store_true",
        help="Also expand each SSEDDATA component and record expanded-byte SHA-256. Slower on large media/body components.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
