"""SSED index page parsing."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Address
from .ssed import BLOCK_SIZE, be16, be32
from .text import decode_jis_pair, gaiji_placeholder, narrow_fullwidth


TAGGED_TYPES = {0x70, 0x90}
SIMPLE_TYPES = {0x71, 0x72, 0x91, 0x92}
BODY_ONLY_TAGGED_TYPES = {0x30}
BODY_ONLY_SIMPLE_TYPES = {0x60}


@dataclass(frozen=True)
class IndexRow:
    key: str
    body: Address
    title: Address
    target_key: str | None = None
    tagged: bool = False
    page: int = 0
    row: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "target_key": self.target_key,
            "body": self.body.to_dict(),
            "title": self.title.to_dict(),
            "tagged": self.tagged,
            "page": self.page,
            "row": self.row,
        }


@dataclass(frozen=True)
class InternalRow:
    key: str
    child_block: int
    page: int
    row: int


@dataclass(frozen=True)
class IndexParse:
    rows: tuple[IndexRow, ...]
    internal_rows: tuple[InternalRow, ...]
    leaf_pages: int
    internal_pages: int
    unknown_leaf_bytes: int


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


def valid_body_pointer(address: Address) -> bool:
    return address.block > 0


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
        rows.append(InternalRow(key=decode_key(raw[:-4], gaiji), child_block=be32(raw, len(raw) - 4), page=page_index, row=row_index))
        pos += size
    return rows


def parse_simple_leaf(page: bytes, page_index: int, gaiji: dict[str, str] | None, *, body_only: bool = False) -> tuple[list[IndexRow], int]:
    count = be16(page, 2)
    rows: list[IndexRow] = []
    unknown = 0
    pos = 4
    pointer_size = 6 if body_only else 12
    for row_index in range(1, count + 1):
        if pos >= len(page) or page[pos] == 0:
            break
        key_len = page[pos]
        pos += 1
        if pos + key_len + pointer_size > len(page):
            unknown += 1
            break
        key = decode_key(page[pos : pos + key_len], gaiji)
        pos += key_len
        if body_only:
            body = Address(be32(page, pos), be16(page, pos + 4))
            title = body
            pos += 6
        else:
            body, title = read_pointer_pair(page, pos)
            pos += 12
        if not valid_body_pointer(body):
            unknown += 1
            continue
        rows.append(IndexRow(key=key, target_key=key, body=body, title=title, page=page_index, row=row_index))
    return rows, unknown


def parse_tagged_leaf(page: bytes, page_index: int, gaiji: dict[str, str] | None, *, body_only: bool = False) -> tuple[list[IndexRow], int, str | None, int | None]:
    count = be16(page, 2)
    rows: list[IndexRow] = []
    unknown = 0
    pos = 4
    current_key: str | None = None
    current_count: int | None = None
    subrecord = 0
    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            break
        pos += 2
        if tag == 0x80:
            if pos + 2 + key_len > len(page):
                unknown += 1
                break
            current_count = be16(page, pos)
            current_key = decode_key(page[pos + 2 : pos + 2 + key_len], gaiji)
            pos += 2 + key_len
            subrecord += 1
            continue
        if tag == 0xC0:
            pointer_size = 6 if body_only else 12
            if pos + key_len + pointer_size > len(page):
                unknown += 1
                break
            target = decode_key(page[pos : pos + key_len], gaiji)
            pos += key_len
            if body_only:
                body = Address(be32(page, pos), be16(page, pos + 4))
                title = body
                pos += 6
            else:
                body, title = read_pointer_pair(page, pos)
                pos += 12
            if not valid_body_pointer(body):
                unknown += 1
                subrecord += 1
                continue
            rows.append(
                IndexRow(
                    key=current_key or target,
                    target_key=target,
                    body=body,
                    title=title,
                    tagged=True,
                    page=page_index,
                    row=len(rows) + 1,
                )
            )
            subrecord += 1
            continue
        unknown += 1
        break
    return rows, unknown, current_key, current_count


def parse_index(data: bytes, start_block: int, component_type: int, gaiji: dict[str, str] | None = None) -> IndexParse:
    rows: list[IndexRow] = []
    internal: list[InternalRow] = []
    leaf_pages = 0
    internal_pages = 0
    unknown = 0
    current_key: str | None = None
    current_count: int | None = None

    for page_index, pos in enumerate(range(0, len(data), BLOCK_SIZE)):
        page = data[pos : pos + BLOCK_SIZE]
        if len(page) < 4:
            continue
        word = be16(page, 0)
        if is_leaf(word):
            leaf_pages += 1
            if component_type in SIMPLE_TYPES:
                page_rows, page_unknown = parse_simple_leaf(page, page_index, gaiji)
            elif component_type in BODY_ONLY_SIMPLE_TYPES:
                page_rows, page_unknown = parse_simple_leaf(page, page_index, gaiji, body_only=True)
            elif component_type in TAGGED_TYPES:
                page_rows, page_unknown, current_key, current_count = parse_tagged_leaf(page, page_index, gaiji)
            elif component_type in BODY_ONLY_TAGGED_TYPES:
                page_rows, page_unknown, current_key, current_count = parse_tagged_leaf(page, page_index, gaiji, body_only=True)
            else:
                page_rows, page_unknown = [], 0
            rows.extend(page_rows)
            unknown += page_unknown
        else:
            internal_pages += 1
            internal.extend(parse_internal_page(page, page_index, gaiji))
    return IndexParse(rows=tuple(rows), internal_rows=tuple(internal), leaf_pages=leaf_pages, internal_pages=internal_pages, unknown_leaf_bytes=unknown)
