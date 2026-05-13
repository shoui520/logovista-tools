"""Experimental LogoVista/SSED writer primitives.

This module is intentionally scoped to the author-core SSED subset:
plain body-stream ``HONMON.DIC`` packages with title streams, simple/tagged
indexes, dictionary-local gaiji, and optional GA16 bitmap resources.  It is not
a full historical package repacker and it does not emit platform sidecars.
"""

from __future__ import annotations

from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
import os
from pathlib import Path
import math
import unicodedata
from typing import Callable, Iterable, Literal

from .gaiji import (
    ga16_glyph_size,
    ga16_row_size,
    ga16_section_for_path,
    gaiji_grid_code_for_index,
    iter_ga16_code_sources,
    parse_ga16_resource,
    parse_uni_resource,
)
from .indexes import IndexPointer
from .ssed import BLOCK_SIZE, CHUNK_SIZE, SSEDDATA_MAGIC, SSEDINFO_MAGIC, WINDOW_SIZE


HALF_GAIJI_START = 0xA121
FULL_GAIJI_START = 0xB121

HONMON_CATALOG_DATA = b"\x02\x00\x00\x00"
TITLE_CATALOG_DATA = b"\x01\x00\x00\x00"
SIMPLE_INDEX_CATALOG_DATA = b"\x02\x01\x55\x40"
TAGGED_INDEX_CATALOG_DATA = b"\x02\x05\x55\x40"
RESOURCE_CATALOG_DATA = b"\x00\x00\x00\x00"
MAX_BRANCH_KEY_BYTES = 32
HASH_MATCH_BYTES = 3
MAX_HASH_CHAIN = 32
PARALLEL_COMPRESS_MIN_CHUNKS = 16


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


def _encode_sseddata_literal_chunk(chunk: bytes) -> bytes:
    if len(chunk) > CHUNK_SIZE:
        raise ValueError(f"SSEDDATA chunk exceeds {CHUNK_SIZE} bytes")
    if len(chunk) > 0xFFFF:
        # CHUNK_SIZE is 0x8000, so this is defensive only.
        raise ValueError("literal chunk command count does not fit in uint16")
    init = Counter(chunk).most_common(1)[0][0] if chunk else 0
    payload = bytearray()
    payload.extend(b"\x00\x00")
    payload.extend(be16(len(chunk)))
    payload.append(init)
    for value in chunk:
        payload.extend((0, 0, value))
    return bytes(payload)


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

    chunk_payloads = [_encode_sseddata_literal_chunk(chunk) for chunk in chunks]
    return _make_sseddata(chunks=chunks, chunk_payloads=chunk_payloads, start_block=start_block, n_blocks=n_blocks, kind=kind)


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
    hash_positions: dict[bytes, deque[int]] = {}
    wintop = 0
    commands = bytearray()
    n_commands = 0
    pos = 0

    def window_key(source: int) -> bytes:
        return bytes(window[(source + step) % WINDOW_SIZE] for step in range(HASH_MATCH_BYTES))

    def add_hash_position(source: int) -> None:
        key = window_key(source)
        hash_positions.setdefault(key, deque(maxlen=MAX_HASH_CHAIN)).append(source)

    for source in range(WINDOW_SIZE):
        add_hash_position(source)

    def write_window(value: int) -> None:
        nonlocal wintop
        written_at = wintop
        window[written_at] = value
        for delta in range(-(HASH_MATCH_BYTES - 1), 1):
            add_hash_position((written_at + delta) % WINDOW_SIZE)
        wintop = (wintop + 1) % WINDOW_SIZE

    while pos < len(chunk):
        remaining = len(chunk) - pos
        max_copy = min(15, remaining)
        best_len = 0
        best_source = 0

        if max_copy >= HASH_MATCH_BYTES:
            key = bytes(chunk[pos : pos + HASH_MATCH_BYTES])
            for source in reversed(hash_positions.get(key, ())):
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


def _resolve_compression_jobs(jobs: int | None) -> int:
    if jobs is None:
        return 1
    if jobs == 0:
        return os.cpu_count() or 1
    if jobs < 0:
        raise ValueError("compression jobs must be 0 or a positive integer")
    return max(1, jobs)


def encode_sseddata(expanded: bytes, *, start_block: int, kind: int = 0, jobs: int | None = None) -> bytes:
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
    jobs = _resolve_compression_jobs(jobs)
    if jobs > 1 and len(chunks) >= PARALLEL_COMPRESS_MIN_CHUNKS:
        chunksize = max(1, len(chunks) // (jobs * 4))
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            payloads = list(executor.map(_encode_sseddata_chunk, chunks, chunksize=chunksize))
    else:
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


def encode_bcd_decimal(value: int, digits: int) -> bytes:
    if value < 0:
        raise ValueError("BCD value must be non-negative")
    text = f"{value:0{digits}d}"
    if len(text) > digits:
        raise ValueError(f"BCD value {value} does not fit in {digits} digits")
    out = bytearray()
    for pos in range(0, digits, 2):
        out.append((int(text[pos]) << 4) | int(text[pos + 1]))
    return bytes(out)


def encode_gaiji_bmp(glyph: bytes, *, width: int, height: int = 16) -> bytes:
    row_size = ga16_row_size(width)
    if len(glyph) != row_size * height:
        raise ValueError(f"gaiji glyph has {len(glyph)} bytes; expected {row_size * height}")
    bmp_stride = ((width + 31) // 32) * 4
    pixel_rows = bytearray()
    for y in range(height - 1, -1, -1):
        src = glyph[y * row_size : (y + 1) * row_size]
        row = bytearray(bmp_stride)
        for x in range(width):
            if src[x // 8] & (0x80 >> (x % 8)):
                row[x // 8] |= 0x80 >> (x % 8)
        pixel_rows.extend(row)
    pixel_offset = 14 + 40 + 8
    file_size = pixel_offset + len(pixel_rows)
    header = bytearray()
    header.extend(b"BM")
    header.extend(file_size.to_bytes(4, "little"))
    header.extend(b"\x00\x00\x00\x00")
    header.extend(pixel_offset.to_bytes(4, "little"))
    header.extend((40).to_bytes(4, "little"))
    header.extend(width.to_bytes(4, "little", signed=True))
    header.extend(height.to_bytes(4, "little", signed=True))
    header.extend((1).to_bytes(2, "little"))
    header.extend((1).to_bytes(2, "little"))
    header.extend((0).to_bytes(4, "little"))
    header.extend(len(pixel_rows).to_bytes(4, "little"))
    header.extend((0).to_bytes(4, "little", signed=True))
    header.extend((0).to_bytes(4, "little", signed=True))
    header.extend((2).to_bytes(4, "little"))
    header.extend((2).to_bytes(4, "little"))
    # Palette entry 0 is white background; 1 is black foreground.
    header.extend(b"\xff\xff\xff\x00")
    header.extend(b"\x00\x00\x00\x00")
    return bytes(header) + bytes(pixel_rows)


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


@dataclass(frozen=True)
class ExternalGaijiAssignment:
    text: str
    index: int
    width: int
    height: int
    glyph: bytes
    bmp: bytes
    record_offset: int = 0
    record_length: int = 0
    payload_offset: int = 0
    payload_length: int = 0

    @property
    def resource_id(self) -> str:
        return f"XGAIJI{self.index:05d}"


GlyphRenderer = Callable[[str, int, int, str], bytes]
ProgressCallback = Callable[[str], None]
CompressionMode = Literal["compressed", "literal"]


def _text_codepoints(text: str) -> frozenset[int]:
    return frozenset(ord(ch) for ch in text if not unicodedata.combining(ch))


@dataclass(frozen=True)
class FontFallbackFace:
    path: Path
    face_index: int
    coverage: frozenset[int]
    renderer: GlyphRenderer
    kind: str = "vector"

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.path}#{self.face_index}"


@dataclass
class FontFallbackGlyphRenderer:
    """Choose the first configured font face that can render a gaiji string."""

    faces: list[FontFallbackFace]
    fallback_misses: Counter[str] = field(default_factory=Counter)
    font_use_counts: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if not self.faces:
            raise ValueError("at least one font fallback face is required")

    @classmethod
    def from_paths(
        cls,
        font_paths: Iterable[Path],
        *,
        threshold: int = 220,
        face_index: int | None = None,
    ) -> "FontFallbackGlyphRenderer":
        faces: list[FontFallbackFace] = []
        for font_path in font_paths:
            faces.extend(_fallback_faces_for_font(Path(font_path), threshold=threshold, face_index=face_index))
        return cls(faces)

    @classmethod
    def from_sources(
        cls,
        *,
        bitmap_paths: Iterable[Path] = (),
        vector_paths: Iterable[Path] = (),
        threshold: int = 220,
        vector_face_index: int | None = None,
    ) -> "FontFallbackGlyphRenderer":
        faces: list[FontFallbackFace] = []
        bitmap_path_list = [Path(path) for path in bitmap_paths]
        if bitmap_path_list:
            bitmap_renderer = BitmapGaijiFontRenderer.from_paths(bitmap_path_list)
            coverage: set[int] = set()
            for text, _width, _height in bitmap_renderer.glyphs:
                coverage.update(_text_codepoints(text))
            faces.append(
                FontFallbackFace(
                    path=bitmap_path_list[0],
                    face_index=0,
                    coverage=frozenset(coverage),
                    renderer=bitmap_renderer,
                    kind="bitmap",
                )
            )
        for font_path in vector_paths:
            faces.extend(_fallback_faces_for_font(Path(font_path), threshold=threshold, face_index=vector_face_index))
        return cls(faces)

    def _select_face(self, text: str) -> FontFallbackFace:
        selected, _glyph = self._render_with_face(text, 16, 16, "full")
        return selected

    def _render_with_face(self, text: str, width: int, height: int, space: str) -> tuple[FontFallbackFace, bytes]:
        codepoints = _text_codepoints(text)
        if not codepoints:
            face = self.faces[0]
            return face, face.renderer(text, width, height, space)
        for face in self.faces:
            if not face.coverage or not codepoints.issubset(face.coverage):
                continue
            try:
                glyph = face.renderer(text, width, height, space)
            except Exception:
                continue
            if _rendered_glyph_is_usable(face.renderer, glyph, width, height, space):
                return face, glyph
        self.fallback_misses[text] += 1
        face = self.faces[0]
        return face, face.renderer(text, width, height, space)

    def __call__(self, text: str, width: int, height: int, space: str) -> bytes:
        face, glyph = self._render_with_face(text, width, height, space)
        self.font_use_counts[face.label] += 1
        return glyph

    def as_dict(self) -> dict[str, object]:
        return {
            "faces": [
                {
                    "path": str(face.path),
                    "face_index": face.face_index,
                    "kind": face.kind,
                    "coverage_size": len(face.coverage),
                    "sources": list(getattr(face.renderer, "sources", ())),
                }
                for face in self.faces
            ],
            "font_use_counts": dict(sorted(self.font_use_counts.items())),
            "fallback_misses": sum(self.fallback_misses.values()),
            "fallback_miss_samples": list(self.fallback_misses.keys())[:20],
        }


@dataclass
class GaijiAllocator:
    """Deterministic dictionary-local gaiji allocator."""

    half_start: int = HALF_GAIJI_START
    full_start: int = FULL_GAIJI_START
    glyph_renderer: GlyphRenderer | None = None
    force_full: bool = False
    assignments: dict[str, GaijiAssignment] = field(default_factory=dict)
    external_assignments: dict[str, ExternalGaijiAssignment] = field(default_factory=dict)
    media_start_block: int | None = None
    index_key_drops: int = 0
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
        if self.force_full:
            prefer_half = False
        if prefer_half:
            code = gaiji_grid_code_for_index(self.half_start, self._half_count)
            space = "half"
            width = 8
            self._validate_code(code, text, space)
            self._half_count += 1
        else:
            code = gaiji_grid_code_for_index(self.full_start, self._full_count)
            space = "full"
            width = 16
            self._validate_code(code, text, space)
            self._full_count += 1
        glyph = self.glyph_renderer(text, width, 16, space) if self.glyph_renderer else None
        assignment = GaijiAssignment(text=text, code=code, space=space, glyph=glyph)
        self.assignments[text] = assignment
        return assignment

    def allocate_external(self, text: str, *, prefer_half: bool | None = None) -> ExternalGaijiAssignment:
        if text in self.external_assignments:
            return self.external_assignments[text]
        if self.glyph_renderer is None:
            raise ValueError(f"gaiji code space exhausted and no glyph renderer is available for {text!r}")
        if prefer_half is None:
            prefer_half = len(text) == 1 and is_halfwidth_gaiji_char(text)
        if self.force_full:
            prefer_half = False
        width = 8 if prefer_half else 16
        space = "half" if prefer_half else "full"
        glyph = self.glyph_renderer(text, width, 16, space)
        assignment = ExternalGaijiAssignment(
            text=text,
            index=len(self.external_assignments) + 1,
            width=width,
            height=16,
            glyph=glyph,
            bmp=encode_gaiji_bmp(glyph, width=width, height=16),
        )
        self.external_assignments[text] = assignment
        return assignment

    def set_external_media_layout(self, *, start_block: int, assignments: dict[str, ExternalGaijiAssignment]) -> None:
        self.media_start_block = start_block
        self.external_assignments = assignments

    def external_media_control(self, text: str, *, prefer_half: bool | None = None) -> bytes:
        assignment = self.allocate_external(text, prefer_half=prefer_half)
        block = (self.media_start_block or 0) + (assignment.record_offset // BLOCK_SIZE)
        offset = assignment.record_offset % BLOCK_SIZE
        payload = bytearray(18)
        payload[12:16] = encode_bcd_decimal(block, 8)
        payload[16:18] = encode_bcd_decimal(offset, 4)
        return b"\x1f\x4d" + bytes(payload) + b"\x1f\x6d"

    @property
    def half_assignments(self) -> list[GaijiAssignment]:
        return [row for row in self.assignments.values() if row.space == "half"]

    @property
    def full_assignments(self) -> list[GaijiAssignment]:
        return [row for row in self.assignments.values() if row.space == "full"]

    def mapping(self) -> dict[str, str]:
        return {row.key: row.text for row in self.assignments.values()}


@dataclass(frozen=True)
class ColscrMediaAssignment:
    resource_key: str
    index: int
    payload: bytes
    mime_type: str
    label: str = ""
    source_path: str = ""
    converted_from: str | None = None
    record_offset: int = 0
    record_length: int = 0
    payload_offset: int = 0
    payload_length: int = 0

    @property
    def resource_id(self) -> str:
        return f"MEDIA{self.index:05d}"


@dataclass
class ColscrMediaAllocator:
    assignments: dict[str, ColscrMediaAssignment] = field(default_factory=dict)
    media_start_block: int | None = None

    def allocate(self, media: "BodyMedia") -> ColscrMediaAssignment:
        assignment = self.assignments.get(media.resource_key)
        if assignment is not None:
            return assignment
        assignment = ColscrMediaAssignment(
            resource_key=media.resource_key,
            index=len(self.assignments) + 1,
            payload=media.payload,
            mime_type=media.mime_type,
            label=media.label,
            source_path=media.source_path,
            converted_from=media.converted_from,
        )
        self.assignments[media.resource_key] = assignment
        return assignment

    def set_layout(self, *, start_block: int, assignments: dict[str, ColscrMediaAssignment]) -> None:
        self.media_start_block = start_block
        self.assignments = assignments

    def media_control(self, media: "BodyMedia") -> bytes:
        assignment = self.allocate(media)
        block = (self.media_start_block or 0) + (assignment.record_offset // BLOCK_SIZE)
        offset = assignment.record_offset % BLOCK_SIZE
        payload = bytearray(18)
        payload[12:16] = encode_bcd_decimal(block, 8)
        payload[16:18] = encode_bcd_decimal(offset, 4)
        return b"\x1f\x4d" + bytes(payload) + b"\x1f\x6d"


@dataclass(frozen=True)
class BodyControl:
    raw: bytes


@dataclass(frozen=True)
class BodyMedia:
    resource_key: str
    payload: bytes
    mime_type: str
    label: str = ""
    source_path: str = ""
    converted_from: str | None = None


@dataclass(frozen=True)
class BodyMarkup:
    """Small writer-side body IR for HTML/structured-content import.

    This is intentionally narrower than Decoded Model v0. It only represents
    text plus already-chosen SSED controls needed by the author-core writer.
    """

    parts: tuple[str | BodyControl | BodyMedia, ...]

    @staticmethod
    def text(value: str) -> "BodyMarkup":
        return BodyMarkup((value,))


WriterBody = str | BodyMarkup


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
        try:
            out.extend(be16(gaiji.allocate(ch).code))
        except ValueError as exc:
            if "code space exhausted" not in str(exc):
                raise
            out.extend(gaiji.external_media_control(ch))
    close_halfwidth()
    return bytes(out)


def encode_writer_body(
    body: WriterBody,
    gaiji: GaijiAllocator | None = None,
    media: ColscrMediaAllocator | None = None,
) -> bytes:
    if isinstance(body, BodyMarkup):
        out = bytearray()
        for part in body.parts:
            if isinstance(part, BodyControl):
                out.extend(part.raw)
            elif isinstance(part, BodyMedia):
                if media is None:
                    if part.label:
                        out.extend(encode_body_text(f"［{part.label}］", gaiji))
                    continue
                out.extend(media.media_control(part))
            else:
                out.extend(encode_body_text(part, gaiji))
        return bytes(out)
    return encode_body_text(body, gaiji)


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


def _try_encode_search_key(text: str, gaiji: GaijiAllocator | None, *, reverse: bool = False) -> bytes | None:
    try:
        return encode_search_key(text, gaiji, reverse=reverse)
    except (UnicodeEncodeError, ValueError):
        if gaiji is not None:
            gaiji.index_key_drops += 1
        return None


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
    body: WriterBody
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


def encode_honmon_entry(
    entry: WriterEntry,
    gaiji: GaijiAllocator | None = None,
    media: ColscrMediaAllocator | None = None,
) -> bytes:
    out = bytearray()
    out.extend(b"\x1f\x09\x00\x01")
    out.extend(b"\x1f\x41\x00\x00")
    out.extend(encode_body_text(entry.headword, gaiji))
    out.extend(b"\x1f\x61")
    out.extend(b"\x1f\x0a")
    out.extend(encode_writer_body(entry.body, gaiji, media))
    out.extend(b"\x1f\x0a")
    return bytes(out)


def encode_honmon_stream(
    entries: Iterable[WriterEntry],
    gaiji: GaijiAllocator | None = None,
    media: ColscrMediaAllocator | None = None,
) -> tuple[bytes, list[int]]:
    out = bytearray()
    offsets: list[int] = []
    for entry in entries:
        offsets.append(len(out))
        out.extend(encode_honmon_entry(entry, gaiji, media))
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
    records: list[bytes] | None = None
    tagged_leaf: bool = False
    children: list["_IndexNode"] = field(default_factory=list)
    block: int = 0
    flags: int = 0
    branch_key_len: int = 0


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


def _branch_prefix(key: bytes, width: int) -> bytes:
    return key[:width].ljust(width, b"\x00")


def _split_keyed_records(
    records: list[tuple[bytes, bytes]],
    *,
    max_bytes: int = BLOCK_SIZE,
    branch_prefix_len: int | None = None,
) -> list[list[tuple[bytes, bytes]]]:
    pages: list[list[tuple[bytes, bytes]]] = []
    current: list[tuple[bytes, bytes]] = []
    size = 4

    def add_record(record: tuple[bytes, bytes]) -> None:
        nonlocal current, size
        row, _branch_key = record
        if len(row) + 4 > max_bytes:
            raise ValueError(f"single index record exceeds page size: {record[1].hex()}")
        if current and size + len(row) > max_bytes:
            pages.append(current)
            current = []
            size = 4
        current.append(record)
        size += len(row)

    if branch_prefix_len is None:
        for record in records:
            add_record(record)
    else:
        groups: list[list[tuple[bytes, bytes]]] = []
        current_group: list[tuple[bytes, bytes]] = []
        current_prefix: bytes | None = None
        for record in records:
            prefix = _branch_prefix(record[1], branch_prefix_len)
            if current_group and prefix != current_prefix:
                groups.append(current_group)
                current_group = []
            current_prefix = prefix
            current_group.append(record)
        if current_group:
            groups.append(current_group)

        for group in groups:
            group_size = sum(len(row) for row, _key in group)
            if group_size + 4 <= max_bytes:
                if current and size + group_size > max_bytes:
                    pages.append(current)
                    current = []
                    size = 4
                current.extend(group)
                size += group_size
                continue

            # Rare fallback for an oversized branch-prefix group. This keeps
            # the writer total, while verify-written-package will still flag
            # any exact traversal ambiguity that cannot fit in one leaf page.
            for record in group:
                add_record(record)
    pages.append(current)
    return pages


def _build_leaf_nodes(records: list[tuple[bytes, bytes]]) -> list[_IndexNode]:
    branch_prefix_len = min(max((len(key) for _row, key in records), default=0), _branch_key_len_cap(1)) or None
    return [
        _IndexNode(branch_key=group[-1][1], records=[record for record, _key in group])
        for group in _split_keyed_records(records, branch_prefix_len=branch_prefix_len)
    ]


def _build_leaf_nodes_from_pages(pages: list[tuple[bytes, list[bytes]]], *, tagged_leaf: bool = False) -> list[_IndexNode]:
    return [_IndexNode(branch_key=branch_key, records=records, tagged_leaf=tagged_leaf) for branch_key, records in pages]


def _branch_key_len_cap(distance_from_leaves: int) -> int:
    # Real LogoVista indexes keep the widest separator keys nearest the leaves.
    # Large Japanese indexes observed in the corpus use 32-byte keys for the
    # parent-of-leaves level, 30-byte keys above that, and 28-byte root keys
    # above that. Short Latin-only indexes still shrink to the actual key width.
    return max(2, MAX_BRANCH_KEY_BYTES - (2 * max(0, distance_from_leaves - 1)))


def _build_parent_level(children: list[_IndexNode], *, distance_from_leaves: int) -> list[_IndexNode]:
    if not children:
        return []
    slot_key_len = max(
        2,
        min(
            max(len(child.branch_key) for child in children),
            _branch_key_len_cap(distance_from_leaves),
        ),
    )
    if slot_key_len > 0xFF:
        raise ValueError("branch key length exceeds observed one-byte slot-size field")
    slot_size = slot_key_len + 4
    per_page = max(1, (BLOCK_SIZE - 4) // slot_size)
    parents: list[_IndexNode] = []

    prefix_groups: list[list[_IndexNode]] = []
    current_group: list[_IndexNode] = []
    current_prefix: bytes | None = None
    for child in children:
        prefix = _branch_prefix(child.branch_key, slot_key_len)
        if current_group and prefix != current_prefix:
            prefix_groups.append(current_group)
            current_group = []
        current_prefix = prefix
        current_group.append(child)
    if current_group:
        prefix_groups.append(current_group)

    current_children: list[_IndexNode] = []

    def flush() -> None:
        nonlocal current_children
        if current_children:
            parents.append(_IndexNode(branch_key=current_children[-1].branch_key, children=current_children, branch_key_len=slot_key_len))
        current_children = []

    for group in prefix_groups:
        if len(group) <= per_page:
            if current_children and len(current_children) + len(group) > per_page:
                flush()
            current_children.extend(group)
            continue

        # Oversized same-prefix groups are structurally ambiguous under the
        # observed fixed-width upper-bound branch rows. Keep the writer total;
        # the verifier will report any remaining unreachable keys.
        for start in range(0, len(group), per_page):
            if current_children:
                flush()
            current_children.extend(group[start : start + per_page])
            flush()
    flush()
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


def _apply_index_page_flags(root: _IndexNode) -> None:
    """Mark index pages with the positional flags observed in LogoVista indexes.

    EBWin/LogoVista tolerate several row grammars, but branch/leaf page words
    are not just a leaf bit plus a payload length.  Official indexes mark the
    first sibling with 0x4000, the last sibling with 0x2000, and tagged leaves
    with 0x1000.  A single root page therefore carries both first/last flags.
    """

    def visit_siblings(nodes: list[_IndexNode]) -> None:
        for index, node in enumerate(nodes):
            flags = 0
            if index == 0:
                flags |= 0x4000
            if index == len(nodes) - 1:
                flags |= 0x2000
            node.flags = flags
            if node.children:
                visit_siblings(node.children)

    root.flags = 0x6000
    if root.children:
        visit_siblings(root.children)


def _materialize_branch_page(node: _IndexNode) -> bytes:
    if node.records is not None:
        leaf_word = node.flags | (0x1000 if node.tagged_leaf else 0)
        return _page(b"".join(node.records), leaf=True, count=len(node.records), word=leaf_word)
    if not node.children:
        raise ValueError("branch node has no children")
    slot_key_len = node.branch_key_len or max(2, max(min(len(child.branch_key), MAX_BRANCH_KEY_BYTES) for child in node.children))
    if slot_key_len > 0xFF:
        raise ValueError("branch key length exceeds observed one-byte slot-size field")
    rows = bytearray()
    for index, child in enumerate(node.children):
        if index == len(node.children) - 1 and node.flags & 0x2000:
            key_bytes = b"\xff" * slot_key_len
        else:
            key_bytes = child.branch_key[:slot_key_len].ljust(slot_key_len, b"\x00")
        rows.extend(key_bytes)
        rows.extend(be32(child.block))
    return _page(bytes(rows), leaf=False, count=len(node.children), word=node.flags | slot_key_len)


def _encode_index_tree_from_leaf_nodes(leaves: list[_IndexNode], *, start_block: int, tagged_leaf: bool = False) -> bytes:
    if not leaves:
        word = 0x6000 | (0x1000 if tagged_leaf else 0)
        return _page(b"", leaf=True, count=0, word=word)
    level = leaves
    distance_from_leaves = 1
    while len(level) > 1:
        level = _build_parent_level(level, distance_from_leaves=distance_from_leaves)
        distance_from_leaves += 1
    root = level[0]
    _apply_index_page_flags(root)
    ordered = _assign_blocks_level_order(root, start_block)
    return b"".join(_materialize_branch_page(node) for node in ordered)


def _encode_index_tree(records: list[tuple[bytes, bytes]], *, start_block: int, tagged_leaf: bool = False) -> bytes:
    leaves = _build_leaf_nodes(records)
    for node in leaves:
        node.tagged_leaf = tagged_leaf
    return _encode_index_tree_from_leaf_nodes(leaves, start_block=start_block, tagged_leaf=tagged_leaf)


def encode_simple_index_pages(
    targets: Iterable[IndexTarget],
    *,
    start_block: int,
    gaiji: GaijiAllocator | None = None,
    reverse_keys: bool = False,
) -> bytes:
    encoded_targets: list[tuple[bytes, IndexTarget]] = []
    for target in targets:
        key_bytes = _try_encode_search_key(target.key, gaiji, reverse=reverse_keys)
        if key_bytes is None:
            continue
        encoded_targets.append((key_bytes, target))
    sorted_targets = sorted(encoded_targets, key=lambda row: row[0])
    records: list[tuple[bytes, bytes]] = []
    for key_bytes, target in sorted_targets:
        records.append((bytes([len(key_bytes)]) + key_bytes + pointer_pair(target.body, target.title), key_bytes))
    return _encode_index_tree(records, start_block=start_block)


def encode_tagged_index_pages(
    targets: Iterable[IndexTarget],
    *,
    start_block: int,
    gaiji: GaijiAllocator | None = None,
    reverse_keys: bool = False,
) -> bytes:
    groups: dict[bytes, list[tuple[bytes, IndexTarget]]] = {}
    for target in targets:
        key_bytes = _try_encode_search_key(target.key, gaiji, reverse=reverse_keys)
        target_key = target.target_key or target.key
        target_bytes = _try_encode_search_key(target_key, gaiji, reverse=reverse_keys)
        if key_bytes is None or target_bytes is None:
            continue
        groups.setdefault(key_bytes, []).append((target_bytes, target))
    grouped_records: list[tuple[bytes, bytes, list[bytes]]] = []
    for key_bytes in sorted(groups):
        rows = groups[key_bytes]
        header = b"\x80" + bytes([len(key_bytes)]) + be16(len(rows)) + key_bytes
        target_records: list[bytes] = []
        for target_bytes, row in rows:
            target_records.append(b"\xc0" + bytes([len(target_bytes)]) + target_bytes + pointer_pair(row.body, row.title))
        grouped_records.append((key_bytes, header, target_records))
    pages = _split_tagged_record_pages(grouped_records)
    leaves = _build_leaf_nodes_from_pages(pages, tagged_leaf=True)
    return _encode_index_tree_from_leaf_nodes(leaves, start_block=start_block, tagged_leaf=True)


def _split_tagged_record_pages(groups: list[tuple[bytes, bytes, list[bytes]]]) -> list[tuple[bytes, list[bytes]]]:
    """Split tagged index groups into leaf pages, allowing group continuation.

    A tagged group starts with an ``0x80`` header and has one or more ``0xc0``
    targets. Large groups may continue onto following leaf pages without a
    repeated header; readers carry the current group key across leaf pages.
    """

    pages: list[tuple[bytes, list[bytes]]] = []
    current_records: list[bytes] = []
    current_branch_key = b""
    current_size = 4

    def flush() -> None:
        nonlocal current_records, current_branch_key, current_size
        if current_records:
            pages.append((current_branch_key, current_records))
        current_records = []
        current_branch_key = b""
        current_size = 4

    def add_record(record: bytes, branch_key: bytes) -> None:
        nonlocal current_branch_key, current_size
        if len(record) + 4 > BLOCK_SIZE:
            raise ValueError(f"single tagged index record exceeds page size: {branch_key.hex()}")
        current_branch_key = branch_key
        current_records.append(record)
        current_size += len(record)

    branch_prefix_len = min(max((len(key_bytes) for key_bytes, _header, _targets in groups), default=0), _branch_key_len_cap(1)) or 0
    prefix_groups: list[list[tuple[bytes, bytes, list[bytes]]]] = []
    current_group: list[tuple[bytes, bytes, list[bytes]]] = []
    current_prefix: bytes | None = None
    for group in groups:
        key_bytes = group[0]
        prefix = _branch_prefix(key_bytes, branch_prefix_len) if branch_prefix_len else key_bytes
        if current_group and prefix != current_prefix:
            prefix_groups.append(current_group)
            current_group = []
        current_prefix = prefix
        current_group.append(group)
    if current_group:
        prefix_groups.append(current_group)

    def add_tagged_group(key_bytes: bytes, header: bytes, targets: list[bytes]) -> None:
        if targets and len(header) + len(targets[0]) + 4 > BLOCK_SIZE:
            raise ValueError(f"single tagged index key header plus target exceeds page size: {key_bytes.hex()}")
        if current_records and current_size + len(header) + (len(targets[0]) if targets else 0) > BLOCK_SIZE:
            flush()
        add_record(header, key_bytes)
        for target in targets:
            if current_size + len(target) > BLOCK_SIZE:
                flush()
            add_record(target, key_bytes)

    for prefix_group in prefix_groups:
        total_size = sum(len(header) + sum(len(target) for target in targets) for _key_bytes, header, targets in prefix_group)
        if total_size + 4 <= BLOCK_SIZE:
            if current_records and current_size + total_size > BLOCK_SIZE:
                flush()
            for key_bytes, header, targets in prefix_group:
                add_record(header, key_bytes)
                for target in targets:
                    add_record(target, key_bytes)
            continue

        # Fallback for an oversized branch-prefix group. Exact duplicate groups
        # can legitimately continue across leaves; distinct keys sharing only a
        # truncated branch prefix remain verifier-visible if they cannot fit.
        for key_bytes, header, targets in prefix_group:
            add_tagged_group(key_bytes, header, targets)
    flush()
    return pages


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


def build_external_gaiji_colscr(
    assignments: dict[str, ExternalGaijiAssignment],
) -> tuple[bytes, dict[str, ExternalGaijiAssignment]]:
    out = bytearray()
    updated: dict[str, ExternalGaijiAssignment] = {}
    for text, assignment in sorted(assignments.items(), key=lambda item: item[1].index):
        record_offset = len(out)
        record = b"data" + len(assignment.bmp).to_bytes(4, "little") + assignment.bmp
        out.extend(record)
        updated[text] = ExternalGaijiAssignment(
            text=assignment.text,
            index=assignment.index,
            width=assignment.width,
            height=assignment.height,
            glyph=assignment.glyph,
            bmp=assignment.bmp,
            record_offset=record_offset,
            record_length=len(record),
            payload_offset=record_offset + 8,
            payload_length=len(assignment.bmp),
        )
    return bytes(out), updated


def build_colscr_records(
    media_assignments: dict[str, ColscrMediaAssignment],
    external_gaiji_assignments: dict[str, ExternalGaijiAssignment],
) -> tuple[bytes, dict[str, ColscrMediaAssignment], dict[str, ExternalGaijiAssignment]]:
    out = bytearray()
    updated_media: dict[str, ColscrMediaAssignment] = {}
    updated_gaiji: dict[str, ExternalGaijiAssignment] = {}

    for key, assignment in sorted(media_assignments.items(), key=lambda item: item[1].index):
        record_offset = len(out)
        record = b"data" + len(assignment.payload).to_bytes(4, "little") + assignment.payload
        out.extend(record)
        updated_media[key] = ColscrMediaAssignment(
            resource_key=assignment.resource_key,
            index=assignment.index,
            payload=assignment.payload,
            mime_type=assignment.mime_type,
            label=assignment.label,
            source_path=assignment.source_path,
            converted_from=assignment.converted_from,
            record_offset=record_offset,
            record_length=len(record),
            payload_offset=record_offset + 8,
            payload_length=len(assignment.payload),
        )

    for text, assignment in sorted(external_gaiji_assignments.items(), key=lambda item: item[1].index):
        record_offset = len(out)
        record = b"data" + len(assignment.bmp).to_bytes(4, "little") + assignment.bmp
        out.extend(record)
        updated_gaiji[text] = ExternalGaijiAssignment(
            text=assignment.text,
            index=assignment.index,
            width=assignment.width,
            height=assignment.height,
            glyph=assignment.glyph,
            bmp=assignment.bmp,
            record_offset=record_offset,
            record_length=len(record),
            payload_offset=record_offset + 8,
            payload_length=len(assignment.bmp),
        )
    return bytes(out), updated_media, updated_gaiji


class VectorGlyphRenderer:
    """Render a user-supplied vector font glyph into GA16 1bpp bytes.

    This is a fallback path.  It keeps aspect ratio and thresholds grayscale
    antialiasing to the 1bpp GA16 target.  The project never bundles fonts.
    """

    def __init__(self, font_path: Path, *, face_index: int = 0, threshold: int = 220) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:  # pragma: no cover - depends on optional local tooling
            raise RuntimeError("Pillow is required for vector gaiji rendering") from exc
        self.font_path = Path(font_path)
        self.face_index = face_index
        self.threshold = threshold
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.ImageFont = ImageFont
        self._font_cache: dict[int, ImageFont.FreeTypeFont] = {}
        self._missing_cache: dict[tuple[int, int, str], bytes] = {}

    def _font(self, size: int):
        cached = self._font_cache.get(size)
        if cached is None:
            cached = self.ImageFont.truetype(str(self.font_path), size=size, index=self.face_index)
            self._font_cache[size] = cached
        return cached

    def _text_bbox(self, text: str, font) -> tuple[int, int, int, int]:
        probe = self.Image.new("L", (256, 256), 255)
        return self.ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)

    def _render_text(self, text: str, width: int, height: int, _space: str) -> bytes:
        chosen = None
        for size in range(64, 3, -1):
            font = self._font(size)
            bbox = self._text_bbox(text, font)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            if bw <= width and bh <= height:
                chosen = (font, bbox, bw, bh)
                break
        if chosen is None:
            font = self._font(4)
            bbox = self._text_bbox(text, font)
            chosen = (font, bbox, bbox[2] - bbox[0], bbox[3] - bbox[1])

        font, bbox, bw, bh = chosen
        image = self.Image.new("L", (width, height), 255)
        draw = self.ImageDraw.Draw(image)
        draw.text(((width - bw) // 2 - bbox[0], (height - bh) // 2 - bbox[1]), text, font=font, fill=0)
        mono = image.point(lambda px: 0 if px < self.threshold else 255, mode="1").convert("L")
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

    def __call__(self, text: str, width: int, height: int, space: str) -> bytes:
        return self._render_text(text, width, height, space)

    def missing_glyph(self, width: int, height: int, space: str) -> bytes | None:
        key = (width, height, space)
        cached = self._missing_cache.get(key)
        if cached is None:
            try:
                cached = self._render_text("\U0010ffff", width, height, space)
            except Exception:
                return None
            self._missing_cache[key] = cached
        return cached


@dataclass
class BitmapGaijiFontRenderer:
    """Use existing GA16/GAI16 bitmap glyph resources as writer glyph input."""

    glyphs: dict[tuple[str, int, int], bytes]
    sources: tuple[str, ...] = ()

    @classmethod
    def from_paths(cls, paths: Iterable[Path]) -> "BitmapGaijiFontRenderer":
        glyphs: dict[tuple[str, int, int], bytes] = {}
        sources: list[str] = []
        for path in paths:
            found, path_sources = _load_bitmap_gaiji_font(Path(path))
            glyphs.update(found)
            sources.extend(path_sources)
        if not glyphs:
            raise ValueError("no mapped GA16/GAI16 bitmap glyphs found in supplied bitmap font paths")
        return cls(glyphs=glyphs, sources=tuple(sources))

    def __call__(self, text: str, width: int, height: int, _space: str) -> bytes:
        glyph = self.glyphs.get((text, width, height))
        if glyph is None:
            raise ValueError(f"bitmap gaiji font has no {width}x{height} glyph for {text!r}")
        return glyph


def _rendered_glyph_is_usable(
    renderer: GlyphRenderer,
    glyph: bytes,
    width: int,
    height: int,
    space: str,
) -> bool:
    if not any(glyph):
        return False
    missing_glyph = getattr(renderer, "missing_glyph", None)
    if missing_glyph is not None:
        marker = missing_glyph(width, height, space)
        if marker is not None and marker == glyph:
            return False
    return True


def _bitmap_font_candidate_files(path: Path) -> tuple[list[Path], list[Path]]:
    if path.is_dir():
        children = [child for child in path.iterdir() if child.is_file()]
        uni_paths = [child for child in children if child.suffix.lower() == ".uni"]
        ga16_paths = [child for child in children if child.name.upper().startswith(("GA16", "GAI16"))]
        return sorted(uni_paths), sorted(ga16_paths)
    if path.suffix.lower() == ".uni":
        siblings = [child for child in path.parent.iterdir() if child.is_file()]
        return [path], sorted(child for child in siblings if child.name.upper().startswith(("GA16", "GAI16")))
    if path.name.upper().startswith(("GA16", "GAI16")):
        siblings = [child for child in path.parent.iterdir() if child.is_file()]
        return sorted(child for child in siblings if child.suffix.lower() == ".uni"), [path]
    return [], []


def _load_bitmap_gaiji_font(path: Path) -> tuple[dict[tuple[str, int, int], bytes], list[str]]:
    uni_paths, ga16_paths = _bitmap_font_candidate_files(path)
    glyphs: dict[tuple[str, int, int], bytes] = {}
    sources: list[str] = []
    uni_resources = [resource for resource in (parse_uni_resource(uni_path) for uni_path in uni_paths) if resource is not None]
    if not uni_resources or not ga16_paths:
        return glyphs, sources

    for ga16_path in ga16_paths:
        resource = parse_ga16_resource(ga16_path)
        if resource is None:
            continue
        section = ga16_section_for_path(ga16_path)
        if section is None:
            continue
        data = ga16_path.read_bytes()
        before = len(glyphs)
        for uni_resource in uni_resources:
            records_by_code = {record.code.lower(): record for record in uni_resource.records if record.section == section}
            if not records_by_code:
                continue
            for code, index, _source in iter_ga16_code_sources(resource, uni_resource):
                record = records_by_code.get(code.lower())
                if record is None:
                    continue
                text = record.display or record.fallback or record.legacy
                if not text:
                    continue
                glyph = resource.glyph_for_index(data, index)
                if glyph is None or not any(glyph):
                    continue
                glyphs.setdefault((text, resource.width, resource.height), glyph)
        if len(glyphs) > before:
            sources.append(str(ga16_path))
    return glyphs, sources


def _font_coverage(font) -> frozenset[int]:
    cmap = font.get("cmap")
    if cmap is None:
        return frozenset()
    codepoints: set[int] = set()
    for table in cmap.tables:
        if table.isUnicode():
            for codepoint, glyph_name in table.cmap.items():
                if str(glyph_name).lower() in {".notdef", "glyph0", "glyph00000"}:
                    continue
                codepoints.add(int(codepoint))
    return frozenset(codepoints)


def _fallback_faces_for_font(
    font_path: Path,
    *,
    threshold: int,
    face_index: int | None,
) -> list[FontFallbackFace]:
    try:
        from fontTools.ttLib import TTCollection, TTFont
    except ImportError as exc:  # pragma: no cover - depends on optional local tooling
        raise RuntimeError("fontTools is required for multi-font gaiji fallback") from exc

    path = Path(font_path)
    faces: list[FontFallbackFace] = []
    suffix = path.suffix.lower()
    if suffix in {".ttc", ".otc"} and face_index is None:
        collection = TTCollection(str(path), lazy=True)
        try:
            for index, font in enumerate(collection.fonts):
                faces.append(
                    FontFallbackFace(
                        path=path,
                        face_index=index,
                        coverage=_font_coverage(font),
                        renderer=VectorGlyphRenderer(path, face_index=index, threshold=threshold),
                        kind="vector",
                    )
                )
        finally:
            for font in collection.fonts:
                close = getattr(font, "close", None)
                if close is not None:
                    close()
        return faces

    index = 0 if face_index is None else face_index
    font = TTFont(str(path), fontNumber=index, lazy=True)
    try:
        coverage = _font_coverage(font)
    finally:
        close = getattr(font, "close", None)
        if close is not None:
            close()
    return [
        FontFallbackFace(
            path=path,
            face_index=index,
            coverage=coverage,
            renderer=VectorGlyphRenderer(path, face_index=index, threshold=threshold),
            kind="vector",
        )
    ]


def render_vector_gaiji_glyph(
    text: str,
    width: int,
    height: int,
    space: str,
    *,
    font_path: Path,
    face_index: int = 0,
    threshold: int = 220,
) -> bytes:
    renderer = VectorGlyphRenderer(font_path, face_index=face_index, threshold=threshold)
    return renderer(text, width, height, space)


@dataclass(frozen=True)
class PlainPackage:
    dict_id: str
    title: str
    files: dict[str, bytes]
    encoded_entries: tuple[EncodedEntry, ...]
    gaiji_allocator: GaijiAllocator
    media_allocator: ColscrMediaAllocator


def build_plain_honmon_package(
    *,
    dict_id: str,
    title: str,
    entries: Iterable[WriterEntry],
    include_tagged_indexes: bool = True,
    glyph_renderer: GlyphRenderer | None = None,
    compression: CompressionMode = "compressed",
    progress: ProgressCallback | None = None,
    gaiji_half_start: int = HALF_GAIJI_START,
    gaiji_full_start: int = FULL_GAIJI_START,
    force_full_gaiji: bool = False,
    compression_jobs: int | None = 1,
) -> PlainPackage:
    """Build an in-memory plain body-stream SSED package."""

    entry_list = list(entries)
    if progress:
        progress(f"entries loaded: {len(entry_list)}")
    gaiji = GaijiAllocator(
        half_start=gaiji_half_start,
        full_start=gaiji_full_start,
        glyph_renderer=glyph_renderer,
        force_full=force_full_gaiji,
    )
    media = ColscrMediaAllocator()

    honmon_expanded, body_offsets = encode_honmon_stream(entry_list, gaiji, media)
    if progress:
        progress(f"HONMON expanded: {len(honmon_expanded)} bytes; gaiji={len(gaiji.assignments)}")
    title_expanded, title_offsets = encode_title_stream([entry.headword for entry in entry_list], gaiji)
    if progress:
        progress(f"TITLE expanded: {len(title_expanded)} bytes; gaiji={len(gaiji.assignments)}")

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
        if progress:
            progress(f"FKINDEX expanded: {len(fkindex_expanded)} bytes")

    fhindex_start = cursor
    fhindex_expanded = encode_simple_index_pages(forward_targets, start_block=fhindex_start, gaiji=gaiji)
    fhindex_blocks = logical_block_count(fhindex_expanded)
    cursor += fhindex_blocks
    if progress:
        progress(f"FHINDEX expanded: {len(fhindex_expanded)} bytes")

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
        if progress:
            progress(f"BKINDEX expanded: {len(bkindex_expanded)} bytes")

    bhindex_start = cursor
    bhindex_expanded = encode_simple_index_pages(backward_targets, start_block=bhindex_start, gaiji=gaiji, reverse_keys=True)
    bhindex_blocks = logical_block_count(bhindex_expanded)
    cursor += bhindex_blocks
    if progress:
        progress(f"BHINDEX expanded: {len(bhindex_expanded)} bytes")

    colscr_expanded: bytes | None = None
    colscr_start: int | None = None
    colscr_blocks = 0
    colscr_media_assignment_count = 0
    if media.assignments or gaiji.external_assignments:
        colscr_start = cursor
        colscr_expanded, media_assignments, external_assignments = build_colscr_records(media.assignments, gaiji.external_assignments)
        colscr_media_assignment_count = len(media_assignments)
        colscr_blocks = logical_block_count(colscr_expanded)
        media.set_layout(start_block=colscr_start, assignments=media_assignments)
        gaiji.set_external_media_layout(start_block=colscr_start, assignments=external_assignments)
        updated_honmon, updated_body_offsets = encode_honmon_stream(entry_list, gaiji, media)
        updated_title, updated_title_offsets = encode_title_stream([entry.headword for entry in entry_list], gaiji)
        if updated_body_offsets != body_offsets or len(updated_honmon) != len(honmon_expanded):
            raise ValueError("external gaiji media rewrite changed HONMON entry offsets")
        if updated_title_offsets != title_offsets or len(updated_title) != len(title_expanded):
            raise ValueError("external gaiji media rewrite changed TITLE row offsets")
        honmon_expanded = updated_honmon
        title_expanded = updated_title
        cursor += colscr_blocks
        if progress:
            progress(
                f"COLSCR expanded: {len(colscr_expanded)} bytes; "
                f"media={len(media.assignments)} external_gaiji={len(gaiji.external_assignments)}"
            )

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
    if colscr_expanded is not None and colscr_start is not None:
        components.append(
            SsedInfoComponent(
                "COLSCR.DIC",
                0xD2,
                colscr_start,
                colscr_start + colscr_blocks - 1,
                default_catalog_data(0xD2, "COLSCR.DIC"),
            )
        )
    if compression == "compressed":
        component_encoder = encode_sseddata
    elif compression == "literal":
        component_encoder = encode_sseddata_literal
    else:
        raise ValueError(f"unknown compression mode: {compression}")

    if progress:
        progress(f"encoding SSEDDATA components with {compression} compression")

    media_literal_components: set[str] = set()
    if colscr_media_assignment_count:
        media_literal_components.add("COLSCR.DIC")

    def encode_component(name: str, expanded: bytes, start_block: int, kind: int) -> bytes:
        if progress:
            progress(f"encoding {name}: expanded={len(expanded)} bytes")
        if compression == "compressed" and name in media_literal_components:
            if progress:
                progress(f"encoding {name}: media payloads use fast literal SSEDDATA chunks")
            encoded = encode_sseddata_literal(expanded, start_block=start_block, kind=kind)
        elif compression == "compressed":
            encoded = component_encoder(expanded, start_block=start_block, kind=kind, jobs=compression_jobs)
        else:
            encoded = component_encoder(expanded, start_block=start_block, kind=kind)
        if progress:
            progress(f"encoded {name}: file={len(encoded)} bytes")
        return encoded

    files = {
        f"{dict_id}.IDX": encode_ssedinfo(title, components),
        "HONMON.DIC": encode_component("HONMON.DIC", honmon_expanded, honmon_start, 0x00),
        "FHINDEX.DIC": encode_component("FHINDEX.DIC", fhindex_expanded, fhindex_start, 0x91),
        "BHINDEX.DIC": encode_component("BHINDEX.DIC", bhindex_expanded, bhindex_start, 0x71),
        "FHTITLE.DIC": encode_component("FHTITLE.DIC", title_expanded, fhtitle_start, 0x05),
        "BHTITLE.DIC": encode_component("BHTITLE.DIC", title_expanded, bhtitle_start, 0x07),
    }

    if include_tagged_indexes:
        assert fktitle_start is not None and bktitle_start is not None
        assert fkindex_start is not None and bkindex_start is not None
        assert fkindex_expanded is not None and bkindex_expanded is not None
        files.update(
            {
                "FKTITLE.DIC": encode_component("FKTITLE.DIC", title_expanded, fktitle_start, 0x04),
                "BKTITLE.DIC": encode_component("BKTITLE.DIC", title_expanded, bktitle_start, 0x06),
                "FKINDEX.DIC": encode_component("FKINDEX.DIC", fkindex_expanded, fkindex_start, 0x90),
                "BKINDEX.DIC": encode_component("BKINDEX.DIC", bkindex_expanded, bkindex_start, 0x70),
            }
        )
    if colscr_expanded is not None and colscr_start is not None:
        files["COLSCR.DIC"] = encode_component("COLSCR.DIC", colscr_expanded, colscr_start, 0xD2)

    if gaiji_required:
        files[f"{dict_id}.uni"] = encode_uni_resource(gaiji)
        files["GA16HALF"] = encode_ga16_resource(half, width=8, start_code=gaiji_half_start)
        files["GA16FULL"] = encode_ga16_resource(full, width=16, start_code=gaiji_full_start)
    if progress:
        progress(f"package files encoded: {len(files)}")

    return PlainPackage(
        dict_id=dict_id,
        title=title,
        files=files,
        encoded_entries=tuple(encoded_entries),
        gaiji_allocator=gaiji,
        media_allocator=media,
    )


def write_plain_package(package: PlainPackage, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in package.files.items():
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
