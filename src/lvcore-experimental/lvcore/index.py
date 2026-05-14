"""SSED index page parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Address
from .ssed import BLOCK_SIZE, be16, be32
from .text import decode_jis_pair, gaiji_placeholder, narrow_fullwidth


TAGGED_TYPES = {0x70, 0x90}
SIMPLE_TYPES = {0x71, 0x72, 0x91, 0x92}
BODY_ONLY_TAGGED_TYPES = {0x30}
BODY_ONLY_SIMPLE_TYPES = {0x60}
KEYWORD_TYPES = {0x80}
CROSS_REFERENCE_TYPES = {0x81}
MULTI_SELECTOR_TYPES = {0xA1}
SUPPORTED_INDEX_TYPES = TAGGED_TYPES | SIMPLE_TYPES | BODY_ONLY_TAGGED_TYPES | BODY_ONLY_SIMPLE_TYPES | KEYWORD_TYPES | CROSS_REFERENCE_TYPES | MULTI_SELECTOR_TYPES


@dataclass(frozen=True)
class IndexRow:
    key: str
    body: Address
    title: Address
    target_key: str | None = None
    tagged: bool = False
    page: int = 0
    row: int = 0
    row_type: str = "simple"
    tag: int | None = None
    group_key: str | None = None
    group_count_hint: int | None = None
    inherited_title: bool = False
    group_page: int | None = None
    group_row: int | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "key": self.key,
            "target_key": self.target_key,
            "body": self.body.to_dict(),
            "title": self.title.to_dict(),
            "tagged": self.tagged,
            "page": self.page,
            "row": self.row,
            "row_type": self.row_type,
        }
        if self.tag is not None:
            data["tag"] = f"{self.tag:02x}"
        if self.group_key is not None:
            data["group_key"] = self.group_key
        if self.group_count_hint is not None:
            data["group_count_hint"] = self.group_count_hint
        if self.inherited_title:
            data["inherited_title"] = True
        if self.group_page is not None:
            data["group_page"] = self.group_page
        if self.group_row is not None:
            data["group_row"] = self.group_row
        return data


@dataclass(frozen=True)
class InternalRow:
    key: str
    child_block: int
    page: int
    row: int
    raw_key: bytes = b""


@dataclass(frozen=True)
class IndexDiagnostic:
    code: str
    message: str
    page: int | None = None
    row: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "page": self.page,
            "row": self.row,
            "details": self.details,
        }


@dataclass(frozen=True)
class IndexParse:
    rows: tuple[IndexRow, ...]
    internal_rows: tuple[InternalRow, ...]
    leaf_pages: int
    internal_pages: int
    unknown_leaf_bytes: int
    malformed_leaf_rows: int = 0
    physical_tail_bytes: int = 0
    physical_tail_nonzero_bytes: int = 0
    row_type_counts: dict[str, int] = field(default_factory=dict)
    continuation_groups: int = 0
    dangling_continuation_rows: int = 0
    diagnostics: tuple[IndexDiagnostic, ...] = ()


@dataclass(frozen=True)
class GroupContext:
    key: str
    count_hint: int | None = None
    title: Address | None = None
    component_type: int = 0
    page: int = 0
    row: int = 0


@dataclass(frozen=True)
class LeafParse:
    rows: list[IndexRow]
    unknown_leaf_bytes: int = 0
    context: GroupContext | None = None
    malformed_leaf_rows: int = 0
    row_type_counts: dict[str, int] = field(default_factory=dict)
    continuation_groups: int = 0
    dangling_continuation_rows: int = 0
    diagnostics: tuple[IndexDiagnostic, ...] = ()


def is_leaf(word: int) -> bool:
    return bool(word & 0x8000)


def slot_size(word: int) -> int:
    return (word & 0xFF) + 4


def decode_key(data: bytes, gaiji: dict[str, str] | None = None) -> str:
    gaiji = gaiji or {}
    chars: list[str] = []
    i = 0
    while i < len(data):
        if data[i] == 0:
            break
        if i + 1 < len(data) and 0x21 <= data[i] <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            chars.append(decode_jis_pair(data[i : i + 2]))
            i += 2
            continue
        if i + 1 < len(data) and 0xA1 <= data[i] <= 0xFE:
            code = data[i : i + 2].hex().lower()
            chars.append(gaiji.get(code) or gaiji_placeholder(code))
            i += 2
            continue
        if 0x20 <= data[i] <= 0x7E:
            chars.append(chr(data[i]))
        i += 1
    return narrow_fullwidth("".join(chars))


def read_pointer_pair(data: bytes, pos: int) -> tuple[Address, Address]:
    return Address(be32(data, pos), be16(data, pos + 4)), Address(be32(data, pos + 6), be16(data, pos + 10))


def read_body_pointer(data: bytes, pos: int) -> Address:
    return Address(be32(data, pos), be16(data, pos + 4))


def valid_body_pointer(address: Address) -> bool:
    return address.block > 0


def increment(counts: dict[str, int], key: str, amount: int = 1) -> None:
    counts[key] = counts.get(key, 0) + amount


def parse_internal_page(page: bytes, page_index: int, gaiji: dict[str, str] | None) -> list[InternalRow]:
    word = be16(page, 0)
    count = be16(page, 2)
    size = slot_size(word)
    rows: list[InternalRow] = []
    pos = 4
    for row_index in range(1, count + 1):
        if size < 6 or pos + size > len(page):
            break
        raw = page[pos : pos + size]
        raw_key = raw[:-4].split(b"\x00", 1)[0]
        rows.append(InternalRow(key=decode_key(raw_key, gaiji), child_block=be32(raw, len(raw) - 4), page=page_index, row=row_index, raw_key=raw_key))
        pos += size
    return rows


def parse_simple_leaf(page: bytes, page_index: int, gaiji: dict[str, str] | None, *, body_only: bool = False) -> LeafParse:
    count = be16(page, 2)
    rows: list[IndexRow] = []
    unknown = 0
    malformed = 0
    diagnostics: list[IndexDiagnostic] = []
    row_type_counts: dict[str, int] = {}
    pos = 4
    pointer_size = 6 if body_only else 12
    for row_index in range(1, count + 1):
        if pos >= len(page) or page[pos] == 0:
            break
        key_len = page[pos]
        pos += 1
        if pos + key_len + pointer_size > len(page):
            unknown += 1
            malformed += 1
            diagnostics.append(
                IndexDiagnostic(
                    code="malformed_simple_leaf_row",
                    message="simple index row exceeds leaf page bounds",
                    page=page_index,
                    row=row_index,
                    details={"key_length": key_len, "body_only": body_only},
                )
            )
            break
        key = decode_key(page[pos : pos + key_len], gaiji)
        pos += key_len
        if body_only:
            body = read_body_pointer(page, pos)
            title = body
            pos += 6
        else:
            body, title = read_pointer_pair(page, pos)
            pos += 12
        if not valid_body_pointer(body):
            unknown += 1
            malformed += 1
            diagnostics.append(
                IndexDiagnostic(
                    code="invalid_body_pointer",
                    message="index row body pointer has no positive block",
                    page=page_index,
                    row=row_index,
                    details={"row_type": "simple"},
                )
            )
            continue
        increment(row_type_counts, "simple")
        rows.append(IndexRow(key=key, target_key=key, body=body, title=title, page=page_index, row=row_index))
    return LeafParse(rows=rows, unknown_leaf_bytes=unknown, malformed_leaf_rows=malformed, row_type_counts=row_type_counts, diagnostics=tuple(diagnostics))


def _tagged_group_count_size(component_type: int) -> int:
    return 4 if component_type in KEYWORD_TYPES | CROSS_REFERENCE_TYPES | MULTI_SELECTOR_TYPES else 2


def _group_has_title(component_type: int) -> bool:
    return component_type in KEYWORD_TYPES | CROSS_REFERENCE_TYPES


def _target_has_inline_key(component_type: int) -> bool:
    return component_type in TAGGED_TYPES | BODY_ONLY_TAGGED_TYPES


def _target_pointer_size(component_type: int) -> int:
    if component_type in KEYWORD_TYPES | CROSS_REFERENCE_TYPES | BODY_ONLY_TAGGED_TYPES:
        return 6
    return 12


def _target_tags(component_type: int) -> set[int]:
    if component_type in KEYWORD_TYPES:
        return {0xB0, 0xC0}
    return {0xC0}


def _direct_pointer_size(component_type: int) -> int:
    return 6 if component_type in BODY_ONLY_TAGGED_TYPES else 12


def parse_tagged_leaf(
    page: bytes,
    page_index: int,
    gaiji: dict[str, str] | None,
    *,
    component_type: int,
    context: GroupContext | None = None,
) -> LeafParse:
    count = be16(page, 2)
    rows: list[IndexRow] = []
    unknown = 0
    malformed = 0
    row_type_counts: dict[str, int] = {}
    diagnostics: list[IndexDiagnostic] = []
    pos = 4
    current = context
    used_inherited_context = False
    dangling = 0
    subrecord = 0
    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        if tag == 0 and page[pos + 1] == 0:
            break
        row_number = subrecord + 1

        if tag == 0x00:
            key_len = page[pos + 1]
            pointer_size = _direct_pointer_size(component_type)
            record_start = pos
            pos += 2
            if pos + key_len + pointer_size > len(page):
                unknown += 1
                malformed += 1
                diagnostics.append(
                    IndexDiagnostic(
                        code="malformed_direct_leaf_row",
                        message="direct tagged index row exceeds leaf page bounds",
                        page=page_index,
                        row=row_number,
                        details={"component_type": f"{component_type:02x}", "key_length": key_len},
                    )
                )
                break
            key = decode_key(page[pos : pos + key_len], gaiji)
            pos += key_len
            body = read_body_pointer(page, pos)
            if pointer_size == 6:
                title = body
                pos += 6
            else:
                body, title = read_pointer_pair(page, pos)
                pos += 12
            if not valid_body_pointer(body):
                unknown += 1
                malformed += 1
                diagnostics.append(
                    IndexDiagnostic(
                        code="invalid_body_pointer",
                        message="direct tagged index row body pointer has no positive block",
                        page=page_index,
                        row=row_number,
                        details={"component_type": f"{component_type:02x}", "offset": record_start},
                    )
                )
                subrecord += 1
                continue
            increment(row_type_counts, "direct")
            increment(row_type_counts, f"tag_{tag:02x}")
            rows.append(
                IndexRow(
                    key=key,
                    target_key=key,
                    body=body,
                    title=title,
                    tagged=False,
                    page=page_index,
                    row=row_number,
                    row_type="direct",
                    tag=tag,
                )
            )
            subrecord += 1
            continue

        if tag == 0x80:
            key_len = page[pos + 1]
            count_size = _tagged_group_count_size(component_type)
            pointer_size = 6 if _group_has_title(component_type) else 0
            pos += 2
            if pos + count_size + key_len + pointer_size > len(page):
                unknown += 1
                malformed += 1
                diagnostics.append(
                    IndexDiagnostic(
                        code="malformed_group_leaf_row",
                        message="grouped index header exceeds leaf page bounds",
                        page=page_index,
                        row=row_number,
                        details={"component_type": f"{component_type:02x}", "key_length": key_len},
                    )
                )
                break
            count_hint = be32(page, pos) if count_size == 4 else be16(page, pos)
            pos += count_size
            key = decode_key(page[pos : pos + key_len], gaiji)
            pos += key_len
            title = read_body_pointer(page, pos) if pointer_size else None
            pos += pointer_size
            current = GroupContext(key=key, count_hint=count_hint, title=title, component_type=component_type, page=page_index, row=row_number)
            increment(row_type_counts, "group")
            increment(row_type_counts, "tag_80")
            subrecord += 1
            continue

        if tag in _target_tags(component_type):
            record_start = pos
            pos += 1
            target_key = ""
            if _target_has_inline_key(component_type):
                if pos >= len(page):
                    unknown += 1
                    malformed += 1
                    diagnostics.append(
                        IndexDiagnostic(
                            code="malformed_target_leaf_row",
                            message="target row missing target key length",
                            page=page_index,
                            row=row_number,
                            details={"component_type": f"{component_type:02x}"},
                        )
                    )
                    break
                key_len = page[pos]
                pos += 1
                pointer_size = _target_pointer_size(component_type)
                if pos + key_len + pointer_size > len(page):
                    unknown += 1
                    malformed += 1
                    diagnostics.append(
                        IndexDiagnostic(
                            code="malformed_target_leaf_row",
                            message="target index row exceeds leaf page bounds",
                            page=page_index,
                            row=row_number,
                            details={"component_type": f"{component_type:02x}", "key_length": key_len},
                        )
                    )
                    break
                target_key = decode_key(page[pos : pos + key_len], gaiji)
                pos += key_len
            else:
                pointer_size = _target_pointer_size(component_type)
                if pos + pointer_size > len(page):
                    unknown += 1
                    malformed += 1
                    diagnostics.append(
                        IndexDiagnostic(
                            code="malformed_target_leaf_row",
                            message="compact target index row exceeds leaf page bounds",
                            page=page_index,
                            row=row_number,
                            details={"component_type": f"{component_type:02x}", "offset": record_start},
                        )
                    )
                    break

            if component_type in KEYWORD_TYPES | CROSS_REFERENCE_TYPES:
                if current is None:
                    unknown += 1
                    malformed += 1
                    dangling += 1
                    diagnostics.append(
                        IndexDiagnostic(
                            code="dangling_continuation_row",
                            message="compact target row appeared without an active group context",
                            page=page_index,
                            row=row_number,
                            details={"component_type": f"{component_type:02x}", "tag": f"{tag:02x}"},
                        )
                    )
                    pos += pointer_size
                    subrecord += 1
                    continue
                body = read_body_pointer(page, pos)
                title = current.title or body
                pos += 6
                key = current.key
                display = current.key
                inherited_title = current.title is not None
            elif component_type in MULTI_SELECTOR_TYPES:
                body, title = read_pointer_pair(page, pos)
                pos += 12
                key = current.key if current is not None else ""
                display = key
                inherited_title = False
                if current is None:
                    dangling += 1
                    diagnostics.append(
                        IndexDiagnostic(
                            code="dangling_multi_target_row",
                            message="MULTI target row appeared without an active group key",
                            page=page_index,
                            row=row_number,
                            details={"component_type": f"{component_type:02x}", "tag": f"{tag:02x}"},
                        )
                    )
            else:
                body = read_body_pointer(page, pos)
                if pointer_size == 6:
                    title = body
                    pos += 6
                else:
                    body, title = read_pointer_pair(page, pos)
                    pos += 12
                key = current.key if current is not None else target_key
                display = target_key
                inherited_title = False

            if not valid_body_pointer(body):
                unknown += 1
                malformed += 1
                diagnostics.append(
                    IndexDiagnostic(
                        code="invalid_body_pointer",
                        message="target index row body pointer has no positive block",
                        page=page_index,
                        row=row_number,
                        details={"component_type": f"{component_type:02x}", "tag": f"{tag:02x}"},
                    )
                )
                subrecord += 1
                continue
            if current is not None and current.page != page_index and component_type in TAGGED_TYPES | BODY_ONLY_TAGGED_TYPES | KEYWORD_TYPES | CROSS_REFERENCE_TYPES | MULTI_SELECTOR_TYPES:
                used_inherited_context = True
            increment(row_type_counts, "target")
            increment(row_type_counts, f"tag_{tag:02x}")
            rows.append(
                IndexRow(
                    key=key or display,
                    target_key=display or key,
                    body=body,
                    title=title,
                    tagged=True,
                    page=page_index,
                    row=row_number,
                    row_type="target",
                    tag=tag,
                    group_key=current.key if current is not None else None,
                    group_count_hint=current.count_hint if current is not None else None,
                    inherited_title=inherited_title,
                    group_page=current.page if current is not None else None,
                    group_row=current.row if current is not None else None,
                )
            )
            subrecord += 1
            continue

        unknown += 1
        malformed += 1
        diagnostics.append(
            IndexDiagnostic(
                code="unknown_leaf_tag",
                message="unknown tagged index leaf row tag",
                page=page_index,
                row=row_number,
                details={"component_type": f"{component_type:02x}", "tag": f"{tag:02x}", "offset": pos},
            )
        )
        break
    return LeafParse(
        rows=rows,
        unknown_leaf_bytes=unknown,
        context=current,
        malformed_leaf_rows=malformed,
        row_type_counts=row_type_counts,
        continuation_groups=1 if used_inherited_context else 0,
        dangling_continuation_rows=dangling,
        diagnostics=tuple(diagnostics),
    )


def parse_index(data: bytes, start_block: int, component_type: int, gaiji: dict[str, str] | None = None) -> IndexParse:
    rows: list[IndexRow] = []
    internal: list[InternalRow] = []
    leaf_pages = 0
    internal_pages = 0
    unknown = 0
    malformed_leaf_rows = 0
    physical_tail_bytes = 0
    physical_tail_nonzero_bytes = 0
    row_type_counts: dict[str, int] = {}
    continuation_groups = 0
    dangling_continuation_rows = 0
    diagnostics: list[IndexDiagnostic] = []
    current_context: GroupContext | None = None

    for page_index, pos in enumerate(range(0, len(data), BLOCK_SIZE)):
        page = data[pos : pos + BLOCK_SIZE]
        if len(page) < BLOCK_SIZE:
            if page:
                nonzero = sum(1 for value in page if value)
                physical_tail_bytes += len(page)
                physical_tail_nonzero_bytes += nonzero
                if nonzero:
                    diagnostics.append(
                        IndexDiagnostic(
                            code="partial_index_page_tail",
                            message="index component ended with a partial physical page tail",
                            page=page_index,
                            details={
                                "tail_bytes": len(page),
                                "nonzero_bytes": nonzero,
                            },
                        )
                    )
            continue
        word = be16(page, 0)
        if is_leaf(word):
            leaf_pages += 1
            if component_type in SIMPLE_TYPES:
                parsed = parse_simple_leaf(page, page_index, gaiji)
            elif component_type in BODY_ONLY_SIMPLE_TYPES:
                parsed = parse_simple_leaf(page, page_index, gaiji, body_only=True)
            elif component_type in TAGGED_TYPES | BODY_ONLY_TAGGED_TYPES | KEYWORD_TYPES | CROSS_REFERENCE_TYPES | MULTI_SELECTOR_TYPES:
                parsed = parse_tagged_leaf(page, page_index, gaiji, component_type=component_type, context=current_context)
                current_context = parsed.context
            else:
                parsed = LeafParse(
                    rows=[],
                    unknown_leaf_bytes=0,
                    diagnostics=(
                        IndexDiagnostic(
                            code="unsupported_component_type",
                            message="index component type has no lvcore parser",
                            page=page_index,
                            details={"component_type": f"{component_type:02x}"},
                        ),
                    ),
                )
            rows.extend(parsed.rows)
            unknown += parsed.unknown_leaf_bytes
            malformed_leaf_rows += parsed.malformed_leaf_rows
            continuation_groups += parsed.continuation_groups
            dangling_continuation_rows += parsed.dangling_continuation_rows
            diagnostics.extend(parsed.diagnostics)
            for key, value in parsed.row_type_counts.items():
                increment(row_type_counts, key, value)
        else:
            internal_pages += 1
            internal.extend(parse_internal_page(page, page_index, gaiji))
    if component_type not in SUPPORTED_INDEX_TYPES and (leaf_pages or internal_pages or data.strip(b"\x00")) and not any(
        diagnostic.code == "unsupported_component_type" for diagnostic in diagnostics
    ):
        diagnostics.append(
            IndexDiagnostic(
                code="unsupported_component_type",
                message="index component type has no lvcore parser",
                details={
                    "component_type": f"{component_type:02x}",
                    "leaf_pages": leaf_pages,
                    "internal_pages": internal_pages,
                },
            )
        )
    return IndexParse(
        rows=tuple(rows),
        internal_rows=tuple(internal),
        leaf_pages=leaf_pages,
        internal_pages=internal_pages,
        unknown_leaf_bytes=unknown,
        malformed_leaf_rows=malformed_leaf_rows,
        physical_tail_bytes=physical_tail_bytes,
        physical_tail_nonzero_bytes=physical_tail_nonzero_bytes,
        row_type_counts=row_type_counts,
        continuation_groups=continuation_groups,
        dangling_continuation_rows=dangling_continuation_rows,
        diagnostics=tuple(diagnostics),
    )
