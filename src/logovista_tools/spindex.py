"""Inspect standalone ``SPINDEX.DIC`` suffix-index resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .indexes import be16, internal_slot_size, is_leaf_page, parse_internal_page
from .ssed import (
    BLOCK_SIZE,
    CHUNK_SIZE,
    be32,
    expand_sseddata_chunk,
    load_sseddata_file,
    ssed_chunk_offsets,
)


@dataclass(frozen=True)
class SpindexChunk:
    index: int
    offset: int
    physical_bytes: int
    expanded_bytes: int
    expected_expanded_bytes: int
    complete: bool


@dataclass(frozen=True)
class SpindexPage:
    page_index: int
    logical_block: int
    byte_offset: int
    bytes_available: int
    word: int
    count: int
    slot_size: int
    leaf: bool
    complete_page: bool
    rows_parsed: int


def reverse_spindex_key(key: str) -> str:
    """Return the forward spelling for an SPINDEX reversed key."""

    return key[::-1]


def expected_chunk_expanded_bytes(*, chunk_index: int, expected_expanded_bytes: int) -> int:
    remaining = expected_expanded_bytes - chunk_index * CHUNK_SIZE
    if remaining <= 0:
        return 0
    return min(CHUNK_SIZE, remaining)


def expand_present_spindex_chunks(data: bytes, expected_expanded_bytes: int) -> tuple[bytes, list[SpindexChunk]]:
    offsets = ssed_chunk_offsets(data)
    out = bytearray()
    chunks: list[SpindexChunk] = []
    for index, offset in enumerate(offsets):
        if offset >= len(data):
            break
        next_offsets = [candidate for candidate in offsets[index + 1 :] if candidate < len(data)]
        physical_end = next_offsets[0] if next_offsets else len(data)
        expanded = expand_sseddata_chunk(data, offset)
        expected = expected_chunk_expanded_bytes(
            chunk_index=index,
            expected_expanded_bytes=expected_expanded_bytes,
        )
        chunks.append(
            SpindexChunk(
                index=index,
                offset=offset,
                physical_bytes=max(0, physical_end - offset),
                expanded_bytes=len(expanded),
                expected_expanded_bytes=expected,
                complete=len(expanded) == expected,
            )
        )
        out.extend(expanded)
    return bytes(out), chunks


def child_status(child_block: int, *, start_block: int, full_pages: int, has_partial_page: bool, end_block: int) -> str:
    if child_block < start_block or child_block > end_block:
        return "outside_declared_range"
    full_end = start_block + full_pages - 1
    if start_block <= child_block <= full_end:
        return "present_full_page"
    if has_partial_page and child_block == start_block + full_pages:
        return "present_partial_page"
    return "missing_from_physical_file"


def parse_spindex_pages(
    expanded: bytes,
    *,
    start_block: int,
    end_block: int,
    emit_row: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[SpindexPage], dict[str, int]]:
    full_pages = len(expanded) // BLOCK_SIZE
    partial_bytes = len(expanded) % BLOCK_SIZE
    page_total = full_pages + (1 if partial_bytes else 0)
    pages: list[SpindexPage] = []
    child_status_counts: dict[str, int] = {}

    for zero_index in range(page_total):
        start = zero_index * BLOCK_SIZE
        page = expanded[start : start + BLOCK_SIZE]
        if len(page) < 4:
            continue
        word = be16(page, 0)
        count = be16(page, 2)
        slot = internal_slot_size(word)
        leaf = is_leaf_page(word)
        complete_page = len(page) == BLOCK_SIZE
        logical_block = start_block + zero_index
        rows_parsed = 0

        if not leaf:
            rows = list(
                parse_internal_page(
                    "SPINDEX.DIC",
                    page,
                    zero_index + 1,
                    logical_block,
                    gaiji="placeholder",
                    gaiji_map={},
                )
            )
            rows_parsed = len(rows)
            for row in rows:
                status = child_status(
                    row.child_block,
                    start_block=start_block,
                    full_pages=full_pages,
                    has_partial_page=bool(partial_bytes),
                    end_block=end_block,
                )
                child_status_counts[status] = child_status_counts.get(status, 0) + 1
                if emit_row is not None:
                    emit_row(
                        {
                            "kind": "internal",
                            "page_index": row.page_index,
                            "logical_block": row.logical_block,
                            "row_index": row.row_index,
                            "key_reversed": row.key,
                            "key": reverse_spindex_key(row.key),
                            "child_block": row.child_block,
                            "child_status": status,
                        }
                    )

        pages.append(
            SpindexPage(
                page_index=zero_index + 1,
                logical_block=logical_block,
                byte_offset=start,
                bytes_available=len(page),
                word=word,
                count=count,
                slot_size=slot,
                leaf=leaf,
                complete_page=complete_page,
                rows_parsed=rows_parsed,
            )
        )

    return pages, child_status_counts


def inspect_spindex(path: Path, *, out_dir: Path | None = None, row_limit: int | None = None) -> dict[str, Any]:
    data, storage = load_sseddata_file(path)
    offsets = ssed_chunk_offsets(data)
    start_block = be32(data, 0x18)
    end_block = be32(data, 0x1C)
    expected_expanded_bytes = (end_block - start_block + 1) * BLOCK_SIZE
    expanded, chunks = expand_present_spindex_chunks(data, expected_expanded_bytes)

    rows_path = None
    emitted = 0
    row_file = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        rows_path = out_dir / f"{path.stem}_internal_rows.jsonl"
        row_file = rows_path.open("w", encoding="utf-8")

    def emit_row(row: dict[str, Any]) -> None:
        nonlocal emitted
        if row_file is None:
            return
        if row_limit is not None and emitted >= row_limit:
            return
        row.update({"source": str(path)})
        row_file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        emitted += 1

    try:
        pages, child_counts = parse_spindex_pages(
            expanded,
            start_block=start_block,
            end_block=end_block,
            emit_row=emit_row if row_file is not None else None,
        )
    finally:
        if row_file is not None:
            row_file.close()

    expected_physical_end = None
    if len(offsets) >= 2:
        expected_physical_end = offsets[-1] + (offsets[-1] - offsets[-2])

    complete_pages = sum(1 for page in pages if page.complete_page)
    partial_pages = sum(1 for page in pages if not page.complete_page)
    internal_pages = sum(1 for page in pages if not page.leaf)
    leaf_pages = sum(1 for page in pages if page.leaf)
    summary = {
        "path": str(path),
        "storage": storage,
        "file_bytes": len(data),
        "magic": data[:8].decode("ascii", errors="replace"),
        "submagic": data[0x0B:0x11].decode("ascii", errors="replace"),
        "kind": f"{data[0x0F]:02x}",
        "data_flags": data[0x20:0x24].hex(),
        "declared_chunks": len(offsets),
        "chunks_present": len(chunks),
        "complete_chunks": sum(1 for chunk in chunks if chunk.complete),
        "chunk_table_end": 64 + len(offsets) * 4,
        "first_chunk_offset": offsets[0] if offsets else None,
        "last_declared_chunk_offset": offsets[-1] if offsets else None,
        "estimated_physical_end": expected_physical_end,
        "declared_start_block": start_block,
        "declared_end_block": end_block,
        "declared_blocks": end_block - start_block + 1,
        "expected_expanded_bytes": expected_expanded_bytes,
        "present_expanded_bytes": len(expanded),
        "full_pages_present": complete_pages,
        "partial_pages_present": partial_pages,
        "pages_parsed": len(pages),
        "internal_pages": internal_pages,
        "leaf_pages": leaf_pages,
        "internal_rows": sum(page.rows_parsed for page in pages if not page.leaf),
        "child_status_counts": child_counts,
        "truncated": len(chunks) < len(offsets) or any(not chunk.complete for chunk in chunks),
        "chunks": [
            {
                "index": chunk.index,
                "offset": chunk.offset,
                "physical_bytes": chunk.physical_bytes,
                "expanded_bytes": chunk.expanded_bytes,
                "expected_expanded_bytes": chunk.expected_expanded_bytes,
                "complete": chunk.complete,
            }
            for chunk in chunks[:10]
        ],
        "page_samples": [
            {
                "page_index": page.page_index,
                "logical_block": page.logical_block,
                "word": f"{page.word:04x}",
                "count": page.count,
                "slot_size": page.slot_size,
                "leaf": page.leaf,
                "complete_page": page.complete_page,
                "rows_parsed": page.rows_parsed,
            }
            for page in pages[:10]
        ],
        "rows_path": str(rows_path) if rows_path else None,
        "rows_emitted": emitted,
    }
    if out_dir is not None:
        (out_dir / f"{path.stem}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return summary


def discover_spindex_files(roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.name.upper() == "SPINDEX.DIC":
            candidates = [root]
        elif root.is_dir():
            candidates = list(root.rglob("SPINDEX.DIC")) + list(root.rglob("spindex.dic"))
        else:
            candidates = []
        for candidate in sorted(candidates):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(resolved)
    return paths
