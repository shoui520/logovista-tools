"""Experimental LogoVista/SSED writer primitives.

This module is intentionally scoped to the author-core SSED subset:
plain body-stream ``HONMON.DIC`` packages with title streams, simple/tagged
indexes, dictionary-local gaiji, and optional GA16 bitmap resources.  It is not
a full historical package repacker and it does not emit platform sidecars.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import math
import unicodedata
from typing import Callable, Iterable

from .gaiji import ga16_glyph_size, ga16_row_size, gaiji_grid_code_for_index
from .indexes import IndexPointer
from .ssed import BLOCK_SIZE, CHUNK_SIZE, SSEDDATA_MAGIC, SSEDINFO_MAGIC, WINDOW_SIZE


HALF_GAIJI_START = 0xA121
FULL_GAIJI_START = 0xB121

HONMON_CATALOG_DATA = b"\x02\x00\x00\x00"
TITLE_CATALOG_DATA = b"\x01\x00\x00\x00"
SIMPLE_INDEX_CATALOG_DATA = b"\x02\x01\x55\x40"
TAGGED_INDEX_CATALOG_DATA = b"\x02\x05\x55\x40"
RESOURCE_CATALOG_DATA = b"\x00\x00\x00\x00"


def be16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"value outside uint16 range: {value}")
    return value.to_bytes(2, "big")


def be32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"value outside uint32 range: {value}")
    return value.to_bytes(4, "big")


def pad_to_block(data: bytes, *, block_size: int = BLOCK_SIZE) -> bytes:
    remainder = len(data) % block_size
    if remainder == 0:
        return data
    return data + bytes(block_size - remainder)


def logical_block_count(data: bytes, *, block_size: int = BLOCK_SIZE) -> int:
    return max(1, math.ceil(len(data) / block_size))


def component_offset_to_pointer(start_block: int, component_offset: int) -> IndexPointer:
    return IndexPointer(block=start_block + component_offset // BLOCK_SIZE, offset=component_offset % BLOCK_SIZE)


def pointer_to_component_offset(start_block: int, pointer: IndexPointer) -> int:
    if pointer.block < start_block:
        raise ValueError(f"pointer block {pointer.block} is before component start block {start_block}")
    return (pointer.block - start_block) * BLOCK_SIZE + pointer.offset


def _expanded_chunks(expanded: bytes) -> list[bytes]:
    expanded = pad_to_block(expanded)
    return [expanded[pos : pos + CHUNK_SIZE] for pos in range(0, len(expanded), CHUNK_SIZE)] or [bytes(BLOCK_SIZE)]


def encode_sseddata_literal(expanded: bytes, *, start_block: int, kind: int = 0) -> bytes:
    """Encode expanded component bytes as a valid literal-only ``SSEDDATA``.

    The compressor is deliberately simple: every command emits zero copied
    bytes plus one literal.  The result is larger than LogoVista's production
    output, but it exercises the exact same SSED expansion path and is valid for
    writer/model roundtrip tests.
    """

    if not 0 <= kind <= 0xFF:
        raise ValueError("SSEDDATA kind must fit in one byte")
    expanded = pad_to_block(expanded)
    n_blocks = logical_block_count(expanded)
    chunks = _expanded_chunks(expanded)

    header_len = 64 + 4 * len(chunks)
    offsets: list[int] = []
    chunk_payloads: list[bytes] = []
    cursor = header_len
    for chunk in chunks:
        if len(chunk) > 0xFFFF:
            # CHUNK_SIZE is 0x8000, so this is defensive only.
            raise ValueError("literal chunk command count does not fit in uint16")
        payload = bytearray()
        payload.extend(b"\x00\x00")
        payload.extend(be16(len(chunk)))
        payload.append(0)
        for value in chunk:
            payload.extend((0, 0, value))
        offsets.append(cursor)
        chunk_payloads.append(bytes(payload))
        cursor += len(payload)

    header = bytearray(64)
    header[:8] = SSEDDATA_MAGIC
    header[0x0F] = kind
    header[0x16:0x18] = be16(len(chunks))
    header[0x18:0x1C] = be32(start_block)
    header[0x1C:0x20] = be32(start_block + n_blocks - 1)

    out = bytearray(header)
    for offset in offsets:
        out.extend(be32(offset))
    for payload in chunk_payloads:
        out.extend(payload)
    return bytes(out)


def _make_sseddata(
    *,
    chunks: list[bytes],
    chunk_payloads: list[bytes],
    start_block: int,
    n_blocks: int,
    kind: int,
) -> bytes:
    header_len = 64 + 4 * len(chunks)
    offsets: list[int] = []
    cursor = header_len
    for payload in chunk_payloads:
        offsets.append(cursor)
        cursor += len(payload)

    header = bytearray(64)
    header[:8] = SSEDDATA_MAGIC
    header[0x0F] = kind
    header[0x16:0x18] = be16(len(chunks))
    header[0x18:0x1C] = be32(start_block)
    header[0x1C:0x20] = be32(start_block + n_blocks - 1)

    out = bytearray(header)
    for offset in offsets:
        out.extend(be32(offset))
    for payload in chunk_payloads:
        out.extend(payload)
    return bytes(out)


def _window_match_length(window: bytearray, wintop: int, source: int, chunk: bytes, pos: int, max_len: int) -> int:
    scratch = bytearray(window)
    out = 0
    while out < max_len:
        read_index = (source + out) % WINDOW_SIZE
        value = scratch[read_index]
        if value != chunk[pos + out]:
            break
        scratch[(wintop + out) % WINDOW_SIZE] = value
        out += 1
    return out


def _encode_sseddata_chunk(chunk: bytes) -> bytes:
    if len(chunk) > CHUNK_SIZE:
        raise ValueError(f"SSEDDATA chunk exceeds {CHUNK_SIZE} bytes")

    init = Counter(chunk).most_common(1)[0][0] if chunk else 0
    window = bytearray([init]) * WINDOW_SIZE
    positions_by_value: dict[int, set[int]] = {init: set(range(WINDOW_SIZE))}
    wintop = 0
    commands = bytearray()
    n_commands = 0
    pos = 0

    def write_window(value: int) -> None:
        nonlocal wintop
        old = window[wintop]
        old_positions = positions_by_value.get(old)
        if old_positions is not None:
            old_positions.discard(wintop)
        window[wintop] = value
        positions_by_value.setdefault(value, set()).add(wintop)
        wintop = (wintop + 1) % WINDOW_SIZE

    while pos < len(chunk):
        remaining = len(chunk) - pos
        max_copy = min(15, remaining)
        best_len = 0
        best_source = 0

        for source in sorted(positions_by_value.get(chunk[pos], ())):
            match_len = _window_match_length(window, wintop, source, chunk, pos, max_copy)
            if match_len > best_len:
                best_len = match_len
                best_source = source
                if best_len == max_copy:
                    break

        if best_len and pos + best_len == len(chunk):
            literal = 0
            literal_written = False
        else:
            if best_len == remaining:
                best_len -= 1
            literal = chunk[pos + best_len]
            literal_written = True

        wp = (best_source - wintop) % WINDOW_SIZE if best_len else 0
        commands.append((wp >> 4) & 0xFF)
        commands.append(((wp & 0x0F) << 4) | best_len)
        commands.append(literal)
        n_commands += 1
        if n_commands > 0xFFFF:
            raise ValueError("SSEDDATA chunk command count exceeds uint16")

        for _step in range(best_len):
            value = window[(wp + wintop) % WINDOW_SIZE]
            write_window(value)
            pos += 1
        if literal_written:
            write_window(literal)
            pos += 1

    return b"\x00\x00" + be16(n_commands) + bytes([init]) + bytes(commands)


def encode_sseddata(expanded: bytes, *, start_block: int, kind: int = 0) -> bytes:
    """Encode expanded component bytes as compressed ``SSEDDATA``.

    The encoder is intentionally conservative and greedy. It does not attempt
    to reproduce vendor byte-for-byte compression, but it emits standard
    LogoVista chunks that roundtrip through the normal expander and avoid the
    severe size inflation of literal-only diagnostics.
    """

    if not 0 <= kind <= 0xFF:
        raise ValueError("SSEDDATA kind must fit in one byte")
    expanded = pad_to_block(expanded)
    n_blocks = logical_block_count(expanded)
    chunks = _expanded_chunks(expanded)
    payloads = [_encode_sseddata_chunk(chunk) for chunk in chunks]
    return _make_sseddata(chunks=chunks, chunk_payloads=payloads, start_block=start_block, n_blocks=n_blocks, kind=kind)


@dataclass(frozen=True)
class SsedInfoComponent:
    filename: str
    type: int
    start_block: int = 0
    end_block: int = 0
    data: bytes = b"\x00\x00\x00\x00"
    multi: int = 0


def default_catalog_data(component_type: int, filename: str = "") -> bytes:
    """Return observed default SSEDINFO data flags for writer-v0 components."""

    upper = filename.upper()
    if upper == "HONMON.DIC" or component_type == 0x00:
        return HONMON_CATALOG_DATA
    if "TITLE" in upper or component_type in {0x03, 0x04, 0x05, 0x06, 0x07, 0x09, 0x0A, 0x0D}:
        return TITLE_CATALOG_DATA
    if component_type in {0x70, 0x90}:
        return TAGGED_INDEX_CATALOG_DATA
    if component_type in {0x30, 0x60, 0x71, 0x72, 0x80, 0x81, 0x91, 0x92, 0xA1}:
        return SIMPLE_INDEX_CATALOG_DATA
    if component_type in {0xF1, 0xF2} or upper.startswith(("GA16", "GAI16")):
        return RESOURCE_CATALOG_DATA
    return RESOURCE_CATALOG_DATA


def encode_ssedinfo(title: str, components: Iterable[SsedInfoComponent]) -> bytes:
    """Encode the normal observed SSEDINFO catalog layout."""

    rows = list(components)
    if len(rows) > 0xFF:
        raise ValueError("SSEDINFO component count must fit in one byte")
    title_bytes = title.encode("cp932", errors="replace")
    if len(title_bytes) > 0x40:
        raise ValueError("SSEDINFO title is too long for the normal header layout")

    header = bytearray(0x80)
    header[:8] = SSEDINFO_MAGIC
    header[12] = len(title_bytes)
    header[13 : 13 + len(title_bytes)] = title_bytes
    header[0x4D] = len(rows)
    out = bytearray(header)

    for row in rows:
        filename = row.filename.encode("ascii")
        if len(filename) > 0x1F:
            raise ValueError(f"SSEDINFO filename too long: {row.filename}")
        if len(row.data) != 4:
            raise ValueError(f"SSEDINFO component data must be exactly 4 bytes: {row.filename}")
        rec = bytearray(0x30)
        rec[2] = row.multi & 0xFF
        rec[3] = row.type & 0xFF
        rec[4:8] = be32(row.start_block)
        rec[8:12] = be32(row.end_block)
        rec[12:16] = row.data
        rec[16] = len(filename)
        rec[17 : 17 + len(filename)] = filename
        out.extend(rec)
    return bytes(out)


def _iso2022_body_pair(ch: str) -> bytes | None:
    try:
        encoded = ch.encode("iso2022_jp")
    except UnicodeEncodeError:
        return None
    prefix = b"\x1b$B"
    suffix = b"\x1b(B"
    if encoded.startswith(prefix) and encoded.endswith(suffix):
        body = encoded[len(prefix) : -len(suffix)]
        if len(body) == 2 and 0x21 <= body[0] <= 0x7E and 0x21 <= body[1] <= 0x7E:
            return body
    return None


def sjis_to_jis_pair(sjis: bytes) -> bytes | None:
    if len(sjis) != 2:
        return None
    lead, trail = sjis
    if 0x81 <= lead <= 0x9F:
        row_base = (lead - 0x81) * 2
    elif 0xE0 <= lead <= 0xEF:
        row_base = (lead - 0xC1) * 2
    else:
        return None

    if 0x9F <= trail <= 0xFC:
        row = row_base + 1
        cell = trail - 0x9F
    elif 0x40 <= trail <= 0xFC and trail != 0x7F:
        row = row_base
        adjusted = trail - 1 if trail >= 0x80 else trail
        cell = adjusted - 0x40
    else:
        return None

    first = row + 0x21
    second = cell + 0x21
    if 0x21 <= first <= 0x7E and 0x21 <= second <= 0x7E:
        return bytes((first, second))
    return None


def encode_jis_cell(ch: str) -> bytes | None:
    pair = _iso2022_body_pair(ch)
    if pair is not None:
        return pair
    try:
        sjis = ch.encode("cp932")
    except UnicodeEncodeError:
        return None
    return sjis_to_jis_pair(sjis)


def fullwidth_ascii(ch: str) -> str:
    if ch == " ":
        return "\u3000"
    code = ord(ch)
    if 0x21 <= code <= 0x7E:
        return chr(code + 0xFEE0)
    return ch


def is_halfwidth_gaiji_char(ch: str) -> bool:
    if len(ch) != 1:
        return False
    if unicodedata.combining(ch):
        return True
    return unicodedata.east_asian_width(ch) in {"Na", "H", "N"}


def code_hex(code: int) -> str:
    return f"{code:04x}"


def utf16_units(text: str) -> tuple[int, int]:
    encoded = text.encode("utf-16-be")
    units = [int.from_bytes(encoded[pos : pos + 2], "big") for pos in range(0, len(encoded), 2)]
    if not units:
        return (0, 0)
    if len(units) == 1:
        return (0, units[0])
    if len(units) > 2:
        raise ValueError(f".uni field cannot store more than two UTF-16 code units: {text!r}")
    return (units[0], units[1])


@dataclass
class GaijiAssignment:
    text: str
    code: int
    space: str
    glyph: bytes | None = None

    @property
    def key(self) -> str:
        return code_hex(self.code)


GlyphRenderer = Callable[[str, int, int, str], bytes]


@dataclass
class GaijiAllocator:
    """Deterministic dictionary-local gaiji allocator."""

    half_start: int = HALF_GAIJI_START
    full_start: int = FULL_GAIJI_START
    glyph_renderer: GlyphRenderer | None = None
    assignments: dict[str, GaijiAssignment] = field(default_factory=dict)
    _half_count: int = 0
    _full_count: int = 0

    @staticmethod
    def _validate_code(code: int, text: str, space: str) -> None:
        row = (code >> 8) & 0xFF
        cell = code & 0xFF
        if not (0xA1 <= row <= 0xFE and 0x21 <= cell <= 0x7E):
            raise ValueError(f"{space} gaiji code space exhausted while allocating {text!r}")

    def allocate(self, text: str, *, prefer_half: bool | None = None) -> GaijiAssignment:
        if text in self.assignments:
            return self.assignments[text]
        if prefer_half is None:
            prefer_half = len(text) == 1 and is_halfwidth_gaiji_char(text)
        if prefer_half:
            code = gaiji_grid_code_for_index(self.half_start, self._half_count)
            self._half_count += 1
            space = "half"
            width = 8
        else:
            code = gaiji_grid_code_for_index(self.full_start, self._full_count)
            self._full_count += 1
            space = "full"
            width = 16
        self._validate_code(code, text, space)
        glyph = self.glyph_renderer(text, width, 16, space) if self.glyph_renderer else None
        assignment = GaijiAssignment(text=text, code=code, space=space, glyph=glyph)
        self.assignments[text] = assignment
        return assignment

    @property
    def half_assignments(self) -> list[GaijiAssignment]:
        return [row for row in self.assignments.values() if row.space == "half"]

    @property
    def full_assignments(self) -> list[GaijiAssignment]:
        return [row for row in self.assignments.values() if row.space == "full"]

    def mapping(self) -> dict[str, str]:
        return {row.key: row.text for row in self.assignments.values()}


def encode_body_text(text: str, gaiji: GaijiAllocator | None = None) -> bytes:
    out = bytearray()
    halfwidth_open = False

    def close_halfwidth() -> None:
        nonlocal halfwidth_open
        if halfwidth_open:
            out.extend(b"\x1f\x05")
            halfwidth_open = False

    for ch in text:
        if ch == "\n":
            close_halfwidth()
            out.extend(b"\x1f\x0a")
            continue

        is_ascii = 0x20 <= ord(ch) <= 0x7E
        if is_ascii and not halfwidth_open:
            out.extend(b"\x1f\x04")
            halfwidth_open = True
        elif not is_ascii:
            close_halfwidth()

        body_ch = fullwidth_ascii(ch) if is_ascii else ch
        pair = encode_jis_cell(body_ch)
        if pair is not None:
            out.extend(pair)
            continue
        if gaiji is None:
            raise UnicodeEncodeError("logovista-jis", ch, 0, 1, "character requires gaiji")
        out.extend(be16(gaiji.allocate(ch).code))
    close_halfwidth()
    return bytes(out)


def encode_search_key(text: str, gaiji: GaijiAllocator | None = None, *, reverse: bool = False) -> bytes:
    """Encode a LogoVista index key.

    Observed ASCII lookup indexes store row-3 JIS uppercase cells even
    when the displayed title/body text remains lowercase.
    """

    chars = []
    for ch in text:
        if "a" <= ch <= "z":
            ch = chr(ord(ch) - 0x20)
        elif "\uff41" <= ch <= "\uff5a":
            ch = chr(ord(ch) - 0x20)
        chars.append(ch)
    if reverse:
        chars.reverse()
    out = bytearray()
    for ch in chars:
        key_ch = fullwidth_ascii(ch) if 0x20 <= ord(ch) <= 0x7E else ch
        pair = encode_jis_cell(key_ch)
        if pair is not None:
            out.extend(pair)
            continue
        if gaiji is None:
            raise UnicodeEncodeError("logovista-index", ch, 0, 1, "character requires gaiji")
        out.extend(be16(gaiji.allocate(ch).code))
    if len(out) > 0xFF:
        raise ValueError(f"search key too long: {text!r}")
    return bytes(out)


def encode_title_stream(titles: Iterable[str], gaiji: GaijiAllocator | None = None) -> tuple[bytes, list[int]]:
    out = bytearray()
    offsets: list[int] = []
    for title in titles:
        offsets.append(len(out))
        out.extend(encode_body_text(title, gaiji))
        out.extend(b"\x1f\x0a")
    return bytes(out), offsets


@dataclass(frozen=True)
class WriterEntry:
    headword: str
    body: str
    search_keys: tuple[str, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return self.search_keys or (self.headword,)


@dataclass(frozen=True)
class EncodedEntry:
    entry: WriterEntry
    body_offset: int
    title_offset: int
    body_pointer: IndexPointer
    title_pointer: IndexPointer
    keys: tuple[str, ...]


def encode_honmon_entry(entry: WriterEntry, gaiji: GaijiAllocator | None = None) -> bytes:
    out = bytearray()
    out.extend(b"\x1f\x09\x00\x01")
    out.extend(b"\x1f\x41\x00\x00")
    out.extend(encode_body_text(entry.headword, gaiji))
    out.extend(b"\x1f\x61")
    out.extend(b"\x1f\x0a")
    out.extend(encode_body_text(entry.body, gaiji))
    out.extend(b"\x1f\x0a")
    return bytes(out)


def encode_honmon_stream(entries: Iterable[WriterEntry], gaiji: GaijiAllocator | None = None) -> tuple[bytes, list[int]]:
    out = bytearray()
    offsets: list[int] = []
    for entry in entries:
        offsets.append(len(out))
        out.extend(encode_honmon_entry(entry, gaiji))
    return bytes(out), offsets


def pointer_pair(body: IndexPointer, title: IndexPointer) -> bytes:
    return be32(body.block) + be16(body.offset) + be32(title.block) + be16(title.offset)


@dataclass(frozen=True)
class IndexTarget:
    key: str
    body: IndexPointer
    title: IndexPointer
    target_key: str | None = None


@dataclass
class _IndexNode:
    branch_key: bytes
    page: bytes | None = None
    children: list["_IndexNode"] = field(default_factory=list)
    block: int = 0


def _page(rows: bytes, *, leaf: bool, count: int, word: int = 0) -> bytes:
    first = (0x8000 if leaf else 0) | (word & 0x7FFF)
    data = be16(first) + be16(count) + rows
    if len(data) > BLOCK_SIZE:
        raise ValueError(f"index page overflow: {len(data)} bytes")
    return data + bytes(BLOCK_SIZE - len(data))


def _split_records(records: list[bytes]) -> list[list[bytes]]:
    pages: list[list[bytes]] = []
    current: list[bytes] = []
    size = 4
    for record in records:
        if len(record) + 4 > BLOCK_SIZE:
            raise ValueError(f"single index record exceeds page size: {len(record)}")
        if current and size + len(record) > BLOCK_SIZE:
            pages.append(current)
            current = []
            size = 4
        current.append(record)
        size += len(record)
    pages.append(current)
    return pages


def _split_keyed_records(records: list[tuple[bytes, bytes]], *, max_bytes: int = BLOCK_SIZE) -> list[list[tuple[bytes, bytes]]]:
    pages: list[list[tuple[bytes, bytes]]] = []
    current: list[tuple[bytes, bytes]] = []
    size = 4

    groups: list[list[tuple[bytes, bytes]]] = []
    for record in records:
        if groups and groups[-1][0][1] == record[1]:
            groups[-1].append(record)
        else:
            groups.append([record])

    for group in groups:
        group_size = sum(len(record) for record, _branch_key in group)
        if group_size + 4 > max_bytes:
            raise ValueError(f"single index key group exceeds page size: {group[0][1].hex()}")
        if current and size + group_size > max_bytes:
            pages.append(current)
            current = []
            size = 4
        current.extend(group)
        size += group_size
    pages.append(current)
    return pages


def _build_leaf_nodes(records: list[tuple[bytes, bytes]]) -> list[_IndexNode]:
    return [
        _IndexNode(branch_key=group[0][1], page=_page(b"".join(record for record, _key in group), leaf=True, count=len(group)))
        for group in _split_keyed_records(records)
    ]


def _build_parent_level(children: list[_IndexNode]) -> list[_IndexNode]:
    if not children:
        return []
    slot_key_len = max(2, max(len(child.branch_key) for child in children))
    if slot_key_len > 0xFF:
        raise ValueError("branch key length exceeds observed one-byte slot-size field")
    slot_size = slot_key_len + 4
    per_page = max(1, (BLOCK_SIZE - 4) // slot_size)
    parents: list[_IndexNode] = []
    for start in range(0, len(children), per_page):
        group = children[start : start + per_page]
        parents.append(_IndexNode(branch_key=group[0].branch_key, children=group))
    return parents


def _assign_blocks_level_order(root: _IndexNode, start_block: int) -> list[_IndexNode]:
    ordered: list[_IndexNode] = []
    level = [root]
    while level:
        ordered.extend(level)
        next_level: list[_IndexNode] = []
        for node in level:
            next_level.extend(node.children)
        level = next_level
    for index, node in enumerate(ordered):
        node.block = start_block + index
    return ordered


def _materialize_branch_page(node: _IndexNode) -> bytes:
    if node.page is not None:
        return node.page
    if not node.children:
        raise ValueError("branch node has no children")
    slot_key_len = max(2, max(len(child.branch_key) for child in node.children))
    if slot_key_len > 0xFF:
        raise ValueError("branch key length exceeds observed one-byte slot-size field")
    rows = bytearray()
    for child in node.children:
        rows.extend(child.branch_key[:slot_key_len].ljust(slot_key_len, b"\x00"))
        rows.extend(be32(child.block))
    return _page(bytes(rows), leaf=False, count=len(node.children), word=slot_key_len)


def _encode_index_tree(records: list[tuple[bytes, bytes]], *, start_block: int) -> bytes:
    if not records:
        return _page(b"", leaf=True, count=0)
    level = _build_leaf_nodes(records)
    while len(level) > 1:
        level = _build_parent_level(level)
    root = level[0]
    ordered = _assign_blocks_level_order(root, start_block)
    return b"".join(_materialize_branch_page(node) for node in ordered)


def encode_simple_index_pages(
    targets: Iterable[IndexTarget],
    *,
    start_block: int,
    gaiji: GaijiAllocator | None = None,
    reverse_keys: bool = False,
) -> bytes:
    sorted_targets = sorted(targets, key=lambda row: encode_search_key(row.key, gaiji, reverse=reverse_keys))
    records: list[tuple[bytes, bytes]] = []
    for target in sorted_targets:
        key_bytes = encode_search_key(target.key, gaiji, reverse=reverse_keys)
        records.append((bytes([len(key_bytes)]) + key_bytes + pointer_pair(target.body, target.title), key_bytes))
    return _encode_index_tree(records, start_block=start_block)


def encode_tagged_index_pages(
    targets: Iterable[IndexTarget],
    *,
    start_block: int,
    gaiji: GaijiAllocator | None = None,
    reverse_keys: bool = False,
) -> bytes:
    groups: dict[str, list[IndexTarget]] = {}
    for target in targets:
        groups.setdefault(target.key, []).append(target)
    records: list[tuple[bytes, bytes]] = []
    for key in sorted(groups, key=lambda value: encode_search_key(value, gaiji, reverse=reverse_keys)):
        key_bytes = encode_search_key(key, gaiji, reverse=reverse_keys)
        rows = groups[key]
        records.append((b"\x80" + bytes([len(key_bytes)]) + be16(len(rows)) + key_bytes, key_bytes))
        for row in rows:
            target_key = row.target_key or row.key
            target_bytes = encode_search_key(target_key, gaiji, reverse=reverse_keys)
            records.append(
                (
                    b"\xc0" + bytes([len(target_bytes)]) + target_bytes + pointer_pair(row.body, row.title),
                    key_bytes,
                )
            )
    return _encode_index_tree(records, start_block=start_block)


def encode_uni_resource(allocator: GaijiAllocator) -> bytes:
    out = bytearray(b"Ver2  ")
    half = allocator.half_assignments
    full = allocator.full_assignments
    out.extend(be32(len(half)))
    for row in half:
        out.extend(encode_uni_record(row.code, row.text))
    out.extend(be32(len(full)))
    for row in full:
        out.extend(encode_uni_record(row.code, row.text))
    return bytes(out)


def encode_uni_record(code: int, display: str, fallback: str | None = None, legacy: str | None = None) -> bytes:
    display_units = utf16_units(display)
    fallback_units = utf16_units(fallback or "")
    legacy_units = utf16_units(legacy or "")
    values = [code, 0, display_units[0], display_units[1], fallback_units[0], fallback_units[1], legacy_units[0], legacy_units[1]]
    return b"".join(be16(value) for value in values)


def blank_glyph(width: int, height: int = 16) -> bytes:
    return bytes(ga16_glyph_size(width, height))


def rows_to_ga16_glyph(rows: Iterable[str], *, width: int, height: int = 16) -> bytes:
    row_texts = list(rows)
    if len(row_texts) != height:
        raise ValueError(f"expected {height} bitmap rows, got {len(row_texts)}")
    row_size = ga16_row_size(width)
    out = bytearray()
    for row in row_texts:
        if len(row) != width:
            raise ValueError(f"expected row width {width}, got {len(row)}")
        encoded = bytearray(row_size)
        for x, ch in enumerate(row):
            if ch not in {".", "0", " "}:
                encoded[x // 8] |= 0x80 >> (x % 8)
        out.extend(encoded)
    return bytes(out)


def encode_ga16_resource(
    assignments: Iterable[GaijiAssignment],
    *,
    width: int,
    height: int = 16,
    start_code: int,
) -> bytes:
    rows = list(assignments)
    header = bytearray(BLOCK_SIZE)
    header[0] = 1
    header[8] = width
    header[9] = height
    header[10:12] = be16(start_code)
    header[12:14] = be16(len(rows))
    glyph_size = ga16_glyph_size(width, height)
    payload = bytearray()
    for row in rows:
        glyph = row.glyph or blank_glyph(width, height)
        if len(glyph) != glyph_size:
            raise ValueError(f"gaiji {row.key} glyph has {len(glyph)} bytes; expected {glyph_size}")
        payload.extend(glyph)
    return bytes(header) + bytes(payload)


def render_vector_gaiji_glyph(
    text: str,
    width: int,
    height: int,
    _space: str,
    *,
    font_path: Path,
    face_index: int = 0,
    threshold: int = 220,
) -> bytes:
    """Render a user-supplied vector font glyph into GA16 1bpp bytes.

    This is a fallback path.  It keeps aspect ratio and thresholds grayscale
    antialiasing to the 1bpp GA16 target.  The project never bundles fonts.
    """

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - depends on optional local tooling
        raise RuntimeError("Pillow is required for vector gaiji rendering") from exc

    def text_bbox(font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
        probe = Image.new("L", (256, 256), 255)
        return ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)

    chosen = None
    for size in range(64, 3, -1):
        font = ImageFont.truetype(str(font_path), size=size, index=face_index)
        bbox = text_bbox(font)
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        if bw <= width and bh <= height:
            chosen = (font, bbox, bw, bh)
            break
    if chosen is None:
        font = ImageFont.truetype(str(font_path), size=4, index=face_index)
        bbox = text_bbox(font)
        chosen = (font, bbox, bbox[2] - bbox[0], bbox[3] - bbox[1])

    font, bbox, bw, bh = chosen
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    draw.text(((width - bw) // 2 - bbox[0], (height - bh) // 2 - bbox[1]), text, font=font, fill=0)
    mono = image.point(lambda px: 0 if px < threshold else 255, mode="1").convert("L")
    row_size = ga16_row_size(width)
    out = bytearray()
    pixels = mono.load()
    for y in range(height):
        row = bytearray(row_size)
        for x in range(width):
            if pixels[x, y] < 128:
                row[x // 8] |= 0x80 >> (x % 8)
        out.extend(row)
    return bytes(out)


@dataclass(frozen=True)
class PlainPackage:
    dict_id: str
    title: str
    files: dict[str, bytes]
    encoded_entries: tuple[EncodedEntry, ...]
    gaiji_allocator: GaijiAllocator


def build_plain_honmon_package(
    *,
    dict_id: str,
    title: str,
    entries: Iterable[WriterEntry],
    include_tagged_indexes: bool = True,
    glyph_renderer: GlyphRenderer | None = None,
) -> PlainPackage:
    """Build an in-memory plain body-stream SSED package."""

    entry_list = list(entries)
    gaiji = GaijiAllocator(glyph_renderer=glyph_renderer)

    honmon_expanded, body_offsets = encode_honmon_stream(entry_list, gaiji)
    title_expanded, title_offsets = encode_title_stream([entry.headword for entry in entry_list], gaiji)

    honmon_start = 2
    honmon_blocks = logical_block_count(honmon_expanded)
    title_blocks = logical_block_count(title_expanded)
    cursor = honmon_start + honmon_blocks

    fktitle_start: int | None = None
    bktitle_start: int | None = None
    if include_tagged_indexes:
        fktitle_start = cursor
        cursor += title_blocks
    fhtitle_start = cursor
    cursor += title_blocks
    if include_tagged_indexes:
        bktitle_start = cursor
        cursor += title_blocks
    bhtitle_start = cursor
    cursor += title_blocks

    encoded_entries: list[EncodedEntry] = []
    for entry, body_offset, title_offset in zip(entry_list, body_offsets, title_offsets):
        encoded_entries.append(
            EncodedEntry(
                entry=entry,
                body_offset=body_offset,
                title_offset=title_offset,
                body_pointer=component_offset_to_pointer(honmon_start, body_offset),
                title_pointer=component_offset_to_pointer(fhtitle_start, title_offset),
                keys=entry.keys,
            )
        )

    def title_pointer(start_block: int, offset: int) -> IndexPointer:
        return component_offset_to_pointer(start_block, offset)

    forward_targets = [
        IndexTarget(key=key, body=encoded.body_pointer, title=encoded.title_pointer)
        for encoded in encoded_entries
        for key in encoded.keys
    ]
    backward_targets = [
        IndexTarget(
            key=key,
            body=encoded.body_pointer,
            title=title_pointer(bhtitle_start, encoded.title_offset),
        )
        for encoded in encoded_entries
        for key in encoded.keys
    ]

    fkindex_expanded: bytes | None = None
    bkindex_expanded: bytes | None = None
    fkindex_start: int | None = None
    bkindex_start: int | None = None
    fkindex_blocks = 0
    bkindex_blocks = 0

    if include_tagged_indexes:
        assert fktitle_start is not None and bktitle_start is not None
        tagged_forward_targets = [
            IndexTarget(
                key=key,
                target_key=encoded.entry.headword,
                body=encoded.body_pointer,
                title=title_pointer(fktitle_start, encoded.title_offset),
            )
            for encoded in encoded_entries
            for key in encoded.keys
        ]
        tagged_backward_targets = [
            IndexTarget(
                key=key,
                target_key=encoded.entry.headword,
                body=encoded.body_pointer,
                title=title_pointer(bktitle_start, encoded.title_offset),
            )
            for encoded in encoded_entries
            for key in encoded.keys
        ]
        fkindex_start = cursor
        fkindex_expanded = encode_tagged_index_pages(tagged_forward_targets, start_block=fkindex_start, gaiji=gaiji)
        fkindex_blocks = logical_block_count(fkindex_expanded)
        cursor += fkindex_blocks

    fhindex_start = cursor
    fhindex_expanded = encode_simple_index_pages(forward_targets, start_block=fhindex_start, gaiji=gaiji)
    fhindex_blocks = logical_block_count(fhindex_expanded)
    cursor += fhindex_blocks

    if include_tagged_indexes:
        assert bkindex_start is None and bktitle_start is not None
        bkindex_start = cursor
        assert bkindex_expanded is None
        tagged_backward_targets = [
            IndexTarget(
                key=key,
                target_key=encoded.entry.headword,
                body=encoded.body_pointer,
                title=title_pointer(bktitle_start, encoded.title_offset),
            )
            for encoded in encoded_entries
            for key in encoded.keys
        ]
        bkindex_expanded = encode_tagged_index_pages(
            tagged_backward_targets,
            start_block=bkindex_start,
            gaiji=gaiji,
            reverse_keys=True,
        )
        bkindex_blocks = logical_block_count(bkindex_expanded)
        cursor += bkindex_blocks

    bhindex_start = cursor
    bhindex_expanded = encode_simple_index_pages(backward_targets, start_block=bhindex_start, gaiji=gaiji, reverse_keys=True)
    bhindex_blocks = logical_block_count(bhindex_expanded)

    half = gaiji.half_assignments
    full = gaiji.full_assignments
    gaiji_required = bool(half or full)

    components = [
        SsedInfoComponent("HONMON.DIC", 0x00, honmon_start, honmon_start + honmon_blocks - 1, default_catalog_data(0x00, "HONMON.DIC")),
    ]
    if include_tagged_indexes:
        assert fktitle_start is not None and bktitle_start is not None
        assert fkindex_start is not None and bkindex_start is not None
        components.extend(
            [
                SsedInfoComponent("FKTITLE.DIC", 0x04, fktitle_start, fktitle_start + title_blocks - 1, default_catalog_data(0x04, "FKTITLE.DIC")),
                SsedInfoComponent("FHTITLE.DIC", 0x05, fhtitle_start, fhtitle_start + title_blocks - 1, default_catalog_data(0x05, "FHTITLE.DIC")),
                SsedInfoComponent("BKTITLE.DIC", 0x06, bktitle_start, bktitle_start + title_blocks - 1, default_catalog_data(0x06, "BKTITLE.DIC")),
                SsedInfoComponent("BHTITLE.DIC", 0x07, bhtitle_start, bhtitle_start + title_blocks - 1, default_catalog_data(0x07, "BHTITLE.DIC")),
                SsedInfoComponent("FKINDEX.DIC", 0x90, fkindex_start, fkindex_start + fkindex_blocks - 1, default_catalog_data(0x90, "FKINDEX.DIC")),
                SsedInfoComponent("FHINDEX.DIC", 0x91, fhindex_start, fhindex_start + fhindex_blocks - 1, default_catalog_data(0x91, "FHINDEX.DIC")),
                SsedInfoComponent("BKINDEX.DIC", 0x70, bkindex_start, bkindex_start + bkindex_blocks - 1, default_catalog_data(0x70, "BKINDEX.DIC")),
                SsedInfoComponent("BHINDEX.DIC", 0x71, bhindex_start, bhindex_start + bhindex_blocks - 1, default_catalog_data(0x71, "BHINDEX.DIC")),
            ]
        )
    else:
        components.extend(
            [
                SsedInfoComponent("FHTITLE.DIC", 0x05, fhtitle_start, fhtitle_start + title_blocks - 1, default_catalog_data(0x05, "FHTITLE.DIC")),
                SsedInfoComponent("BHTITLE.DIC", 0x07, bhtitle_start, bhtitle_start + title_blocks - 1, default_catalog_data(0x07, "BHTITLE.DIC")),
                SsedInfoComponent("FHINDEX.DIC", 0x91, fhindex_start, fhindex_start + fhindex_blocks - 1, default_catalog_data(0x91, "FHINDEX.DIC")),
                SsedInfoComponent("BHINDEX.DIC", 0x71, bhindex_start, bhindex_start + bhindex_blocks - 1, default_catalog_data(0x71, "BHINDEX.DIC")),
            ]
        )
    if gaiji_required:
        # Real SSED packages declare loose GA16 resources in the SSEDINFO
        # catalog with zero block addresses. Compatible readers use this catalog
        # declaration to discover the sidecar files.
        components.extend(
            [
                SsedInfoComponent("GA16FULL", 0xF1, 0, 0, b"\x00\x00\x00\x00"),
                SsedInfoComponent("GA16HALF", 0xF2, 0, 0, b"\x00\x00\x00\x00"),
            ]
        )
    files = {
        f"{dict_id}.IDX": encode_ssedinfo(title, components),
        "HONMON.DIC": encode_sseddata(honmon_expanded, start_block=honmon_start, kind=0x00),
        "FHINDEX.DIC": encode_sseddata(fhindex_expanded, start_block=fhindex_start, kind=0x91),
        "BHINDEX.DIC": encode_sseddata(bhindex_expanded, start_block=bhindex_start, kind=0x71),
        "FHTITLE.DIC": encode_sseddata(title_expanded, start_block=fhtitle_start, kind=0x05),
        "BHTITLE.DIC": encode_sseddata(title_expanded, start_block=bhtitle_start, kind=0x07),
    }

    if include_tagged_indexes:
        assert fktitle_start is not None and bktitle_start is not None
        assert fkindex_start is not None and bkindex_start is not None
        assert fkindex_expanded is not None and bkindex_expanded is not None
        files.update(
            {
                "FKTITLE.DIC": encode_sseddata(title_expanded, start_block=fktitle_start, kind=0x04),
                "BKTITLE.DIC": encode_sseddata(title_expanded, start_block=bktitle_start, kind=0x06),
                "FKINDEX.DIC": encode_sseddata(fkindex_expanded, start_block=fkindex_start, kind=0x90),
                "BKINDEX.DIC": encode_sseddata(bkindex_expanded, start_block=bkindex_start, kind=0x70),
            }
        )

    if gaiji_required:
        files[f"{dict_id}.uni"] = encode_uni_resource(gaiji)
        files["GA16HALF"] = encode_ga16_resource(half, width=8, start_code=HALF_GAIJI_START)
        files["GA16FULL"] = encode_ga16_resource(full, width=16, start_code=FULL_GAIJI_START)

    return PlainPackage(
        dict_id=dict_id,
        title=title,
        files=files,
        encoded_entries=tuple(encoded_entries),
        gaiji_allocator=gaiji,
    )


def write_plain_package(package: PlainPackage, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in package.files.items():
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
