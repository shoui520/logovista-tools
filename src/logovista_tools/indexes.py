"""Extract raw LogoVista/EPWING-style index pages.

The index streams are page-based binary search structures. This module parses
the page layer directly after S-SED expansion; it does not consult SQLite
caches.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .entries import decode_jis_pair, discover_dictionaries, gaiji_text, normalize_fullwidth_ascii
from .gaiji import load_gaiji_profile
from .ssed import BLOCK_SIZE, SsedRandomReader, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


INDEX_TYPES = {0x30, 0x60, 0x70, 0x71, 0x72, 0x80, 0x81, 0x90, 0x91, 0x92, 0xA1}
BODY_ONLY_TAGGED_LEAF_TYPES = {0x30}
BODY_ONLY_SIMPLE_LEAF_TYPES = {0x60}
TAGGED_LEAF_TYPES = {0x70, 0x90}
KW_LEAF_TYPES = {0x80}
CR_LEAF_TYPES = {0x81}
SIMPLE_LEAF_TYPES = {0x71, 0x72, 0x91, 0x92}
MULTI_LEAF_TYPES = {0xA1}


@dataclass(frozen=True)
class IndexPointer:
    block: int
    offset: int

    def as_dict(self) -> dict[str, int]:
        return {"block": self.block, "offset": self.offset}


@dataclass(frozen=True)
class InternalRow:
    component: str
    page_index: int
    logical_block: int
    row_index: int
    key: str
    child_block: int
    raw_key: bytes = b""


@dataclass(frozen=True)
class LeafRow:
    component: str
    page_index: int
    logical_block: int
    row_index: int
    key: str
    target_key: str
    body: IndexPointer
    title: IndexPointer
    tagged: bool
    target_count_hint: int | None = None
    continued_group: bool = False


@dataclass
class ComponentIndexResult:
    component: str
    type: int
    data_flags: str
    expanded_bytes: int
    internal_pages: int = 0
    leaf_pages: int = 0
    internal_rows: int = 0
    leaf_rows: int = 0
    search_groups: int = 0
    unknown_leaf_bytes: int = 0
    warnings: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "type": f"{self.type:02x}",
            "data_flags": self.data_flags,
            "expanded_bytes": self.expanded_bytes,
            "internal_pages": self.internal_pages,
            "leaf_pages": self.leaf_pages,
            "internal_rows": self.internal_rows,
            "leaf_rows": self.leaf_rows,
            "search_groups": self.search_groups,
            "unknown_leaf_bytes": self.unknown_leaf_bytes,
            "warnings": self.warnings or [],
        }


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def is_leaf_page(word: int) -> bool:
    return bool(word & 0x8000)


def internal_slot_size(word: int) -> int:
    return (word & 0xFF) + 4


def decode_index_key(
    data: bytes,
    *,
    gaiji: str = "h-placeholder",
    gaiji_map: dict[str, str] | None = None,
) -> str:
    gaiji_map = gaiji_map or {}
    chars: list[str] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0:
            break

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            chars.append(decode_jis_pair(data[i : i + 2]))
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            chars.append(gaiji_text(b, data[i + 1], gaiji, gaiji_map))
            i += 2
            continue

        if 0x20 <= b <= 0x7E:
            chars.append(chr(b))
        i += 1

    return normalize_fullwidth_ascii("".join(chars))


def parse_internal_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> Iterable[InternalRow]:
    word = be16(page, 0)
    count = be16(page, 2)
    slot = internal_slot_size(word)
    if slot < 6:
        return
    pos = 4
    for row_index in range(1, count + 1):
        if pos + slot > len(page):
            return
        row = page[pos : pos + slot]
        pos += slot
        raw_key = row[:-4].split(b"\x00", 1)[0]
        key = decode_index_key(raw_key, gaiji=gaiji, gaiji_map=gaiji_map)
        yield InternalRow(
            component=component,
            page_index=page_index,
            logical_block=logical_block,
            row_index=row_index,
            key=key,
            child_block=be32(row, len(row) - 4),
            raw_key=raw_key,
        )


def read_pointer_pair(data: bytes, pos: int) -> tuple[IndexPointer, IndexPointer]:
    body = IndexPointer(block=be32(data, pos), offset=be16(data, pos + 4))
    title = IndexPointer(block=be32(data, pos + 6), offset=be16(data, pos + 10))
    return body, title


def parse_simple_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], int]:
    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    unknown = 0
    for row_index in range(1, count + 1):
        if pos >= len(page):
            break
        key_len = page[pos]
        if key_len == 0:
            if any(page[pos : min(len(page), pos + 13)]):
                while pos + 13 <= len(page) and any(page[pos : pos + 13]):
                    body = IndexPointer(block=be32(page, pos), offset=be16(page, pos + 4))
                    title = IndexPointer(block=be32(page, pos + 7), offset=be16(page, pos + 11))
                    rows.append(
                        LeafRow(
                            component=component,
                            page_index=page_index,
                            logical_block=logical_block,
                            row_index=len(rows) + 1,
                            key="",
                            target_key="",
                            body=body,
                            title=title,
                            tagged=False,
                        )
                    )
                    pos += 13
                break
            break
        pos += 1
        if pos + key_len + 12 > len(page):
            unknown += 1
            break
        key_bytes = page[pos : pos + key_len]
        pos += key_len
        body, title = read_pointer_pair(page, pos)
        pos += 12
        key = decode_index_key(key_bytes, gaiji=gaiji, gaiji_map=gaiji_map)
        rows.append(
            LeafRow(
                component=component,
                page_index=page_index,
                logical_block=logical_block,
                row_index=row_index,
                key=key,
                target_key=key,
                body=body,
                title=title,
                tagged=False,
            )
        )
    return rows, unknown


def parse_body_only_simple_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], int]:
    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    unknown = 0
    for row_index in range(1, count + 1):
        if pos >= len(page) or page[pos] == 0:
            break
        key_len = page[pos]
        pos += 1
        if pos + key_len + 6 > len(page):
            unknown += 1
            break
        key_bytes = page[pos : pos + key_len]
        pos += key_len
        body = IndexPointer(block=be32(page, pos), offset=be16(page, pos + 4))
        pos += 6
        key = decode_index_key(key_bytes, gaiji=gaiji, gaiji_map=gaiji_map)
        rows.append(
            LeafRow(
                component=component,
                page_index=page_index,
                logical_block=logical_block,
                row_index=row_index,
                key=key,
                target_key=key,
                body=body,
                title=body,
                tagged=False,
            )
        )
    return rows, unknown


def parse_tagged_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    current_key: str | None,
    current_count_hint: int | None,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], str | None, int | None, int, int]:
    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    groups = 0
    unknown = 0
    subrecord = 0

    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            break
        pos += 2

        if tag == 0x00:
            if pos + key_len + 12 > len(page):
                unknown += 1
                break
            key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            body, title = read_pointer_pair(page, pos)
            pos += 12
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=False,
                )
            )
            subrecord += 1
            continue

        if tag == 0x80:
            if pos + 2 + key_len > len(page):
                unknown += 1
                break
            current_count_hint = be16(page, pos)
            pos += 2
            current_key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            groups += 1
            subrecord += 1
            continue

        if tag == 0xC0:
            if pos + key_len + 12 > len(page):
                unknown += 1
                break
            target_key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            body, title = read_pointer_pair(page, pos)
            pos += 12
            key = current_key if current_key is not None else target_key
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=target_key,
                    body=body,
                    title=title,
                    tagged=True,
                    target_count_hint=current_count_hint,
                    continued_group=current_key is None,
                )
            )
            subrecord += 1
            continue

        unknown += 1
        break

    return rows, current_key, current_count_hint, groups, unknown


def parse_body_only_tagged_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    current_key: str | None,
    current_count_hint: int | None,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], str | None, int | None, int, int]:
    """Parse type-0x30 KINDEX-style grouped leaves.

    The row grammar matches the 0x70/0x90 tagged family except target records
    carry only a 6-byte body pointer, not a body/title pointer pair.
    """

    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    groups = 0
    unknown = 0
    subrecord = 0

    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            break
        pos += 2

        if tag == 0x00:
            if pos + key_len + 6 > len(page):
                unknown += 1
                break
            key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            body = IndexPointer(block=be32(page, pos), offset=be16(page, pos + 4))
            pos += 6
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=body,
                    tagged=False,
                )
            )
            subrecord += 1
            continue

        if tag == 0x80:
            if pos + 2 + key_len > len(page):
                unknown += 1
                break
            current_count_hint = be16(page, pos)
            pos += 2
            current_key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            groups += 1
            subrecord += 1
            continue

        if tag == 0xC0:
            if pos + key_len + 6 > len(page):
                unknown += 1
                break
            target_key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            body = IndexPointer(block=be32(page, pos), offset=be16(page, pos + 4))
            pos += 6
            key = current_key if current_key is not None else target_key
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=target_key,
                    body=body,
                    title=body,
                    tagged=True,
                    target_count_hint=current_count_hint,
                    continued_group=current_key is None,
                )
            )
            subrecord += 1
            continue

        unknown += 1
        break

    return rows, current_key, current_count_hint, groups, unknown


def parse_cr_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    current_key: str | None,
    current_title: IndexPointer | None,
    current_count_hint: int | None,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], str | None, IndexPointer | None, int | None, int, int]:
    """Parse CRINDEX cross-reference leaf pages.

    Type-0x81 pages use direct rows with explicit body/title pointer pairs and
    grouped rows with a shared CRTITLE pointer followed by compact body targets.
    """

    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    groups = 0
    unknown = 0
    subrecord = 0

    while subrecord < count and pos + 2 <= len(page):
        first = page[pos]
        second = page[pos + 1]
        if first == 0 and second == 0:
            break

        if first == 0x00:
            key_len = second
            pos += 2
            if pos + key_len + 12 > len(page):
                unknown += 1
                break
            key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            body, title = read_pointer_pair(page, pos)
            pos += 12
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=False,
                )
            )
            subrecord += 1
            continue

        if first == 0x80:
            key_len = second
            pos += 2
            if pos + 4 + key_len + 6 > len(page):
                unknown += 1
                break
            current_count_hint = be32(page, pos)
            pos += 4
            current_key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            current_title = IndexPointer(block=be32(page, pos), offset=be16(page, pos + 4))
            pos += 6
            groups += 1
            subrecord += 1
            continue

        if first == 0xC0:
            if pos + 7 > len(page):
                unknown += 1
                break
            body = IndexPointer(block=be32(page, pos + 1), offset=be16(page, pos + 5))
            key = current_key or ""
            title = current_title or body
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=True,
                    target_count_hint=current_count_hint,
                    continued_group=current_key is None,
                )
            )
            pos += 7
            subrecord += 1
            continue

        unknown += 1
        break

    return rows, current_key, current_title, current_count_hint, groups, unknown


def parse_kw_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    current_key: str | None,
    current_title: IndexPointer | None,
    current_count_hint: int | None,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], str | None, IndexPointer | None, int | None, int, int]:
    """Parse KWINDEX keyword leaf pages.

    Observed type-0x80 pages use two related row layouts:

    - direct rows: 00 len, key bytes, 6-byte body pointer, 6-byte title pointer;
    - grouped rows: 80 len, 4-byte target count, key bytes, 6-byte KWTITLE
      pointer, then 0xc0/0xb0 target body pointers.
    """

    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    groups = 0
    unknown = 0
    subrecord = 0

    while subrecord < count and pos < len(page):
        tag = page[pos]
        if tag == 0 and (pos + 1 >= len(page) or page[pos + 1] == 0):
            break

        if tag == 0x00:
            if pos + 2 > len(page):
                unknown += 1
                break
            key_len = page[pos + 1]
            if key_len == 0:
                break
            pos += 2
            if pos + key_len + 12 > len(page):
                unknown += 1
                break
            key = decode_index_key(page[pos : pos + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += key_len
            body, title = read_pointer_pair(page, pos)
            pos += 12
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=False,
                )
            )
            subrecord += 1
            continue

        if tag == 0x80:
            if pos + 6 > len(page):
                unknown += 1
                break
            key_len = page[pos + 1]
            if pos + 6 + key_len + 6 > len(page):
                unknown += 1
                break
            current_count_hint = be32(page, pos + 2)
            current_key = decode_index_key(page[pos + 6 : pos + 6 + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += 6 + key_len
            current_title = IndexPointer(block=be32(page, pos), offset=be16(page, pos + 4))
            pos += 6
            groups += 1
            subrecord += 1
            continue

        if tag in (0xB0, 0xC0):
            if pos + 7 > len(page):
                unknown += 1
                break
            body = IndexPointer(block=be32(page, pos + 1), offset=be16(page, pos + 5))
            key = current_key or ""
            title = current_title or body
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=True,
                    target_count_hint=current_count_hint,
                    continued_group=current_key is None,
                )
            )
            pos += 7
            subrecord += 1
            continue

        unknown += 1
        break

    return rows, current_key, current_title, current_count_hint, groups, unknown


def parse_multi_leaf_page(
    component: str,
    page: bytes,
    page_index: int,
    logical_block: int,
    *,
    current_key: str | None,
    current_count_hint: int | None,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[LeafRow], str | None, int | None, int, int]:
    """Parse type-0xa1 MULTI leaf pages.

    This family is used by MULTI/MUL side indexes. Internal pages use the same
    slot grammar as other index components. Leaf pages use tagged rows:

    - ``00 len`` direct rows carry key bytes plus body/title pointer pair;
    - ``80 len`` group rows carry a 4-byte target count and a shared key;
    - ``c0`` target rows carry only a body/title pointer pair.
    """

    count = be16(page, 2)
    pos = 4
    rows: list[LeafRow] = []
    groups = 0
    unknown = 0
    subrecord = 0

    while subrecord < count and pos < len(page):
        tag = page[pos]
        if tag == 0 and (pos + 1 >= len(page) or page[pos + 1] == 0):
            break

        if tag == 0x00:
            if pos + 2 > len(page):
                unknown += 1
                break
            key_len = page[pos + 1]
            if pos + 2 + key_len + 12 > len(page):
                unknown += 1
                break
            key = decode_index_key(page[pos + 2 : pos + 2 + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += 2 + key_len
            body, title = read_pointer_pair(page, pos)
            pos += 12
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=False,
                )
            )
            subrecord += 1
            continue

        if tag == 0x80:
            if pos + 6 > len(page):
                unknown += 1
                break
            key_len = page[pos + 1]
            if pos + 6 + key_len > len(page):
                unknown += 1
                break
            current_count_hint = be32(page, pos + 2)
            current_key = decode_index_key(page[pos + 6 : pos + 6 + key_len], gaiji=gaiji, gaiji_map=gaiji_map)
            pos += 6 + key_len
            groups += 1
            subrecord += 1
            continue

        if tag == 0xC0:
            if pos + 13 > len(page):
                unknown += 1
                break
            body, title = read_pointer_pair(page, pos + 1)
            key = current_key or ""
            rows.append(
                LeafRow(
                    component=component,
                    page_index=page_index,
                    logical_block=logical_block,
                    row_index=len(rows) + 1,
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=True,
                    target_count_hint=current_count_hint,
                    continued_group=current_key is None,
                )
            )
            pos += 13
            subrecord += 1
            continue

        unknown += 1
        break

    return rows, current_key, current_count_hint, groups, unknown


def scan_index_component(
    component_name: str,
    component_type: int,
    data: bytes,
    start_block: int,
    *,
    gaiji: str,
    gaiji_map: dict[str, str],
    emit_internal: bool = False,
    emit_row: Callable[[dict[str, Any]], None] | None = None,
    row_limit: int | None = None,
) -> ComponentIndexResult:
    return _scan_index_component_pages(
        component_name,
        component_type,
        page_count=len(data) // BLOCK_SIZE,
        expanded_bytes=len(data),
        start_block=start_block,
        page_at=lambda page_zero: data[page_zero * BLOCK_SIZE : (page_zero + 1) * BLOCK_SIZE],
        gaiji=gaiji,
        gaiji_map=gaiji_map,
        emit_internal=emit_internal,
        emit_row=emit_row,
        row_limit=row_limit,
    )


def scan_index_component_reader(
    component_name: str,
    component_type: int,
    source: Path,
    start_block: int,
    *,
    gaiji: str,
    gaiji_map: dict[str, str],
    emit_internal: bool = False,
    emit_row: Callable[[dict[str, Any]], None] | None = None,
    row_limit: int | None = None,
) -> ComponentIndexResult:
    reader = SsedRandomReader(source)
    return _scan_index_component_pages(
        component_name,
        component_type,
        page_count=reader.expanded_size // BLOCK_SIZE,
        expanded_bytes=reader.expanded_size,
        start_block=start_block,
        page_at=lambda page_zero: reader.read(page_zero * BLOCK_SIZE, BLOCK_SIZE),
        gaiji=gaiji,
        gaiji_map=gaiji_map,
        emit_internal=emit_internal,
        emit_row=emit_row,
        row_limit=row_limit,
    )


def _scan_index_component_pages(
    component_name: str,
    component_type: int,
    *,
    page_count: int,
    expanded_bytes: int,
    start_block: int,
    page_at: Callable[[int], bytes],
    gaiji: str,
    gaiji_map: dict[str, str],
    emit_internal: bool,
    emit_row: Callable[[dict[str, Any]], None] | None,
    row_limit: int | None,
) -> ComponentIndexResult:
    result = ComponentIndexResult(
        component=component_name,
        type=component_type,
        data_flags="",
        expanded_bytes=expanded_bytes,
        warnings=[],
    )
    current_key: str | None = None
    current_count_hint: int | None = None
    current_title: IndexPointer | None = None
    emitted_rows = 0

    def maybe_emit(row: dict[str, Any]) -> bool:
        nonlocal emitted_rows
        if row_limit is not None and emitted_rows >= row_limit:
            return True
        if emit_row is not None:
            emit_row(row)
        emitted_rows += 1
        return row_limit is not None and emitted_rows >= row_limit

    stopped_by_limit = False
    for page_zero in range(page_count):
        page = page_at(page_zero)
        word = be16(page, 0)
        page_index = page_zero + 1
        logical_block = start_block + page_zero

        if not is_leaf_page(word):
            result.internal_pages += 1
            if row_limit is not None and not emit_internal:
                continue
            internal_rows = list(
                parse_internal_page(
                    component_name,
                    page,
                    page_index,
                    logical_block,
                    gaiji=gaiji,
                    gaiji_map=gaiji_map,
                )
            )
            result.internal_rows += len(internal_rows)
            if emit_internal:
                for row in internal_rows:
                    stopped_by_limit = maybe_emit(
                        {
                            "kind": "internal",
                            "component": row.component,
                            "page_index": row.page_index,
                            "logical_block": row.logical_block,
                            "row_index": row.row_index,
                            "key": row.key,
                            "raw_key_hex": row.raw_key.hex(),
                            "child_block": row.child_block,
                        }
                    )
                    if stopped_by_limit:
                        break
            if stopped_by_limit:
                break
            continue

        result.leaf_pages += 1
        if component_type in BODY_ONLY_SIMPLE_LEAF_TYPES:
            leaf_rows, unknown = parse_body_only_simple_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.unknown_leaf_bytes += unknown
        elif component_type in BODY_ONLY_TAGGED_LEAF_TYPES:
            leaf_rows, current_key, current_count_hint, groups, unknown = parse_body_only_tagged_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                current_key=current_key,
                current_count_hint=current_count_hint,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.search_groups += groups
            result.unknown_leaf_bytes += unknown
        elif component_type in TAGGED_LEAF_TYPES:
            leaf_rows, current_key, current_count_hint, groups, unknown = parse_tagged_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                current_key=current_key,
                current_count_hint=current_count_hint,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.search_groups += groups
            result.unknown_leaf_bytes += unknown
        elif component_type in KW_LEAF_TYPES:
            leaf_rows, current_key, current_title, current_count_hint, groups, unknown = parse_kw_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                current_key=current_key,
                current_title=current_title,
                current_count_hint=current_count_hint,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.search_groups += groups
            result.unknown_leaf_bytes += unknown
        elif component_type in CR_LEAF_TYPES:
            leaf_rows, current_key, current_title, current_count_hint, groups, unknown = parse_cr_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                current_key=current_key,
                current_title=current_title,
                current_count_hint=current_count_hint,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.search_groups += groups
            result.unknown_leaf_bytes += unknown
        elif component_type in MULTI_LEAF_TYPES:
            leaf_rows, current_key, current_count_hint, groups, unknown = parse_multi_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                current_key=current_key,
                current_count_hint=current_count_hint,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.search_groups += groups
            result.unknown_leaf_bytes += unknown
        elif component_type in SIMPLE_LEAF_TYPES:
            leaf_rows, unknown = parse_simple_leaf_page(
                component_name,
                page,
                page_index,
                logical_block,
                gaiji=gaiji,
                gaiji_map=gaiji_map,
            )
            result.unknown_leaf_bytes += unknown
        else:
            continue

        result.leaf_rows += len(leaf_rows)
        for row in leaf_rows:
            stopped_by_limit = maybe_emit(
                {
                    "kind": "leaf",
                    "component": row.component,
                    "page_index": row.page_index,
                    "logical_block": row.logical_block,
                    "row_index": row.row_index,
                    "key": row.key,
                    "target_key": row.target_key,
                    "body": row.body.as_dict(),
                    "title": row.title.as_dict(),
                    "tagged": row.tagged,
                    "target_count_hint": row.target_count_hint,
                    "continued_group": row.continued_group,
                }
            )
            if stopped_by_limit:
                break
        if stopped_by_limit:
            break

    if result.unknown_leaf_bytes:
        result.warnings = result.warnings or []
        result.warnings.append("Some leaf subrecords could not be parsed.")
    if stopped_by_limit:
        result.warnings = result.warnings or []
        result.warnings.append("Stopped after the row emission limit; component summary is partial.")
    return result


def collect_index_body_offsets_for_idx(
    idx: Path,
    *,
    honmon_start_block: int,
    expanded_size: int,
) -> set[int]:
    """Collect raw HONMON-relative body offsets from index leaf rows."""

    _title, elements = parse_ssedinfo(idx)
    gaiji_profile = load_gaiji_profile(idx)
    offsets: set[int] = set()
    for element in elements:
        if element.type not in INDEX_TYPES or not element.start:
            continue
        source = find_case_insensitive(idx.parent, element.filename)
        if source is None:
            continue
        expanded = expand_sseddata_file(source)

        def collect(row: dict[str, Any]) -> None:
            if row.get("kind") != "leaf":
                return
            body = row.get("body")
            if not isinstance(body, dict):
                return
            block = body.get("block")
            offset = body.get("offset")
            if not isinstance(block, int) or not isinstance(offset, int):
                return
            relative = (block - honmon_start_block) * BLOCK_SIZE + offset
            if 0 <= relative < expanded_size:
                offsets.add(relative)

        scan_index_component(
            element.filename,
            element.type,
            expanded,
            element.start,
            gaiji="drop",
            gaiji_map=gaiji_profile.map,
            emit_internal=False,
            emit_row=collect,
        )
    return offsets


def extract_indexes_for_idx(idx: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    title, elements = parse_ssedinfo(idx)
    dict_id = idx.parent.parent.name if idx.parent.name == idx.parent.parent.name else idx.stem
    dict_out = out_dir / dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    indexes_path = dict_out / "raw_indexes.jsonl"
    gaiji_profile = load_gaiji_profile(idx)

    summaries: list[dict[str, Any]] = []
    emitted = 0
    with indexes_path.open("w", encoding="utf-8") as out:
        for element in elements:
            if element.type not in INDEX_TYPES or not element.start:
                continue
            if args.component:
                selected_components = {name.upper() for name in args.component}
                if element.filename.upper() not in selected_components:
                    continue
            source = find_case_insensitive(idx.parent, element.filename)
            if source is None:
                continue

            def emit_row(row: dict[str, Any]) -> None:
                nonlocal emitted
                if args.limit and emitted >= args.limit:
                    return
                row.update({"dict_id": dict_id, "dict_title": title})
                out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")
                emitted += 1

            remaining = args.limit - emitted if args.limit else None
            if remaining is not None and remaining <= 0:
                break
            if remaining is not None:
                component_summary = scan_index_component_reader(
                    element.filename,
                    element.type,
                    source,
                    element.start,
                    gaiji=args.gaiji,
                    gaiji_map=gaiji_profile.map,
                    emit_internal=args.include_internal,
                    emit_row=emit_row,
                    row_limit=remaining,
                )
            else:
                expanded = expand_sseddata_file(source)
                component_summary = scan_index_component(
                    element.filename,
                    element.type,
                    expanded,
                    element.start,
                    gaiji=args.gaiji,
                    gaiji_map=gaiji_profile.map,
                    emit_internal=args.include_internal,
                    emit_row=emit_row,
                )
            component_summary.data_flags = element.data.hex()
            summaries.append(component_summary.as_dict())
            if args.limit and emitted >= args.limit:
                break

    summary = {
        "dict_id": dict_id,
        "dict_title": title,
        "idx": str(idx),
        "index_components": summaries,
        "rows_emitted": emitted,
        "gaiji_map_entries": len(gaiji_profile.map),
        "gaiji_uni_entries": gaiji_profile.uni_entries,
        "gaiji_plist_entries": gaiji_profile.plist_entries,
        "indexes_path": str(indexes_path),
    }
    (dict_out / "indexes_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def extract_indexes_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(args.root or [Path(".")], dict_ids=args.dict, include_gaiji=False, include_images=False)
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    summaries = []
    for source in sources:
        print(f"extracting indexes {source.dict_id}: {source.title}")
        summaries.append(extract_indexes_for_idx(source.idx, args.out_dir, args))
    return summaries
