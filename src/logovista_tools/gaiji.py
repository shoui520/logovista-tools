"""Dictionary-specific gaiji mapping helpers."""

from __future__ import annotations

import plistlib
import re
import struct
import zlib
from binascii import crc32
from dataclasses import dataclass
from pathlib import Path
from typing import Hashable, Iterable


GAIJI_CODE_RE = re.compile(r"[A-Fa-f0-9]{4}")
UNI_MAGIC = b"Ver2  "
UNI_HEADER_SIZE = 10
UNI_RECORD_SIZE = 16
UNI_SIMPLE_HEADER_SIZE = 4
UNI_SIMPLE_RECORD_SIZE = 12


@dataclass(frozen=True)
class GaijiProfile:
    """Resolved gaiji mappings for one dictionary."""

    map: dict[str, str]
    uni_entries: int
    plist_entries: int
    uni_paths: tuple[Path, ...]
    plist_paths: tuple[Path, ...]


@dataclass(frozen=True)
class UniRecord:
    """One decoded .uni gaiji mapping record."""

    section: str
    index: int
    code: str
    metadata: int
    display_units: tuple[int, ...]
    display: str
    fallback_units: tuple[int, ...]
    fallback: str
    legacy_units: tuple[int, ...]
    legacy: str
    raw_fields: tuple[int, ...]


@dataclass(frozen=True)
class UniResource:
    """Parsed LogoVista .uni resource."""

    path: Path
    format: str
    half_count: int
    full_count: int
    records: tuple[UniRecord, ...]
    expected_size: int
    trailing_bytes: int


@dataclass(frozen=True)
class Ga16Resource:
    path: Path
    width: int
    height: int
    start_code: int
    count: int
    glyph_bytes: int
    data_offset: int = 2048

    def code_for_index(self, index: int) -> int:
        return self.start_code + index

    def glyph_for_code(self, data: bytes, code: int) -> bytes | None:
        index = code - self.start_code
        if index < 0 or index >= self.count:
            return None
        return self.glyph_for_index(data, index)

    def glyph_for_index(self, data: bytes, index: int) -> bytes | None:
        if index < 0 or index >= self.count:
            return None
        start = self.data_offset + index * self.glyph_bytes
        end = start + self.glyph_bytes
        if end > len(data):
            return None
        return data[start:end]

    def iter_glyphs(self, data: bytes) -> Iterable[tuple[int, bytes]]:
        for index in range(self.count):
            code = self.code_for_index(index)
            glyph = self.glyph_for_code(data, code)
            if glyph is not None:
                yield code, glyph


def ga16_section_for_path(path: Path) -> str | None:
    name = path.name.upper()
    if "HALF" in name or "16H" in name:
        return "half"
    if "FULL" in name or "16F" in name:
        return "full"
    return None


def ga16_preferred_code_for_index(
    resource: Ga16Resource,
    index: int,
    uni_resource: UniResource | None = None,
) -> tuple[str, str]:
    section = ga16_section_for_path(resource.path)
    if section is not None and uni_resource is not None:
        records = [record for record in uni_resource.records if record.section == section]
        if index < len(records):
            return records[index].code, "uni_record_order"
    return f"{resource.code_for_index(index):04x}", "sequential"


def iter_ga16_code_sources(
    resource: Ga16Resource,
    uni_resource: UniResource | None = None,
) -> Iterable[tuple[str, int, str]]:
    """Yield gaiji codes covered by a GA16 resource.

    Older tooling treated the code range as strictly ``start_code + index``.
    That is correct for many resources, but not all. In several Windows
    packages the GA16 glyph slots also align with dictionary-local ``.uni``
    record order; the record's code field is the raw body code, even when the
    code sequence is sparse or non-monotonic. We yield both views so callers
    can resolve raw codes without losing compatibility with sequential
    resources.
    """

    for index in range(resource.count):
        yield f"{resource.code_for_index(index):04x}", index, "sequential"

    section = ga16_section_for_path(resource.path)
    if section is None or uni_resource is None:
        return
    records = [record for record in uni_resource.records if record.section == section]
    for index, record in enumerate(records[: resource.count]):
        yield record.code, index, "uni_record_order"


def candidate_gaiji_paths(idx: Path) -> tuple[list[Path], list[Path]]:
    """Return candidate .uni and plist paths for an .IDX file."""

    stem = idx.stem
    uni_candidates = [
        idx.parent / f"{stem}.uni",
        idx.parent / f"{stem}.UNI",
        idx.parent.parent / f"{stem}.uni",
        idx.parent.parent / f"{stem}.UNI",
    ]
    plist_candidates = [
        idx.parent / "GaijiS.plist",
        idx.parent / "Gaiji.plist",
        idx.parent.parent / "GaijiS.plist",
        idx.parent.parent / "Gaiji.plist",
    ]
    return uni_candidates, plist_candidates


def file_identity(path: Path) -> Hashable:
    try:
        stat = path.stat()
    except OSError:
        return str(path).lower()
    return (stat.st_dev, stat.st_ino, stat.st_size)


def decode_uni_code_units(values: list[int] | tuple[int, ...]) -> str:
    chars: list[str] = []
    i = 0
    while i < len(values):
        value = values[i]
        if value == 0 or 0xD800 <= value <= 0xDFFF:
            if (
                0xD800 <= value <= 0xDBFF
                and i + 1 < len(values)
                and 0xDC00 <= values[i + 1] <= 0xDFFF
            ):
                codepoint = 0x10000 + ((value - 0xD800) << 10) + (values[i + 1] - 0xDC00)
                try:
                    chars.append(chr(codepoint))
                except ValueError:
                    pass
                i += 2
                continue
            i += 1
            continue
        try:
            chars.append(chr(value))
        except ValueError:
            continue
        i += 1
    return "".join(chars)


def parse_uni_records(
    data: bytes, offset: int, count: int, *, record_size: int, section: str
) -> tuple[list[UniRecord], int]:
    records: list[UniRecord] = []
    for index in range(count):
        start = offset + index * record_size
        end = start + record_size
        if end > len(data):
            break
        record = data[start:end]
        code = record[:2].hex().lower()
        if not GAIJI_CODE_RE.fullmatch(code):
            continue
        values = tuple(int.from_bytes(record[i : i + 2], "big") for i in range(0, record_size, 2))
        display_units = values[2:4]
        fallback_units = values[4:6]
        legacy_units = values[6:8] if len(values) >= 8 else ()
        records.append(
            UniRecord(
                section=section,
                index=index,
                code=code,
                metadata=values[1] if len(values) > 1 else 0,
                display_units=display_units,
                display=decode_uni_code_units(display_units),
                fallback_units=fallback_units,
                fallback=decode_uni_code_units(fallback_units),
                legacy_units=legacy_units,
                legacy=decode_uni_code_units(legacy_units),
                raw_fields=values,
            )
        )
    return records, len(records)


def parse_uni_resource(path: Path) -> UniResource | None:
    """Parse a LogoVista .uni/UNI file.

    Two layouts have been observed:

    - ``Ver2  `` files: magic, 32-bit half count, 16-byte half records,
      32-bit full count, 16-byte full records.
    - simple files: 32-bit half count, 12-byte half records, 32-bit full
      count, 12-byte full records. This layout appears in IWKOKUG8/KENROWA.
    """

    data = path.read_bytes()
    if len(data) < 8:
        return None

    if data[:6] == UNI_MAGIC:
        half_count = int.from_bytes(data[6:10], "big")
        half_offset = UNI_HEADER_SIZE
        half_records, _half_seen = parse_uni_records(
            data, half_offset, half_count, record_size=UNI_RECORD_SIZE, section="half"
        )
        full_count_offset = half_offset + half_count * UNI_RECORD_SIZE
        if full_count_offset + 4 > len(data):
            return UniResource(
                path=path,
                format="ver2",
                half_count=half_count,
                full_count=0,
                records=tuple(half_records),
                expected_size=full_count_offset,
                trailing_bytes=max(0, len(data) - full_count_offset),
            )
        full_count = int.from_bytes(data[full_count_offset : full_count_offset + 4], "big")
        full_offset = full_count_offset + 4
        full_records, _full_seen = parse_uni_records(
            data, full_offset, full_count, record_size=UNI_RECORD_SIZE, section="full"
        )
        expected_size = full_offset + full_count * UNI_RECORD_SIZE
        return UniResource(
            path=path,
            format="ver2",
            half_count=half_count,
            full_count=full_count,
            records=tuple(half_records + full_records),
            expected_size=expected_size,
            trailing_bytes=max(0, len(data) - expected_size),
        )

    half_count = int.from_bytes(data[0:4], "big")
    half_offset = UNI_SIMPLE_HEADER_SIZE
    full_count_offset = half_offset + half_count * UNI_SIMPLE_RECORD_SIZE
    if full_count_offset + 4 > len(data):
        return None
    full_count = int.from_bytes(data[full_count_offset : full_count_offset + 4], "big")
    full_offset = full_count_offset + 4
    expected_size = full_offset + full_count * UNI_SIMPLE_RECORD_SIZE
    if expected_size > len(data):
        return None
    half_records, _half_seen = parse_uni_records(
        data, half_offset, half_count, record_size=UNI_SIMPLE_RECORD_SIZE, section="half"
    )
    full_records, _full_seen = parse_uni_records(
        data, full_offset, full_count, record_size=UNI_SIMPLE_RECORD_SIZE, section="full"
    )
    return UniResource(
        path=path,
        format="simple12",
        half_count=half_count,
        full_count=full_count,
        records=tuple(half_records + full_records),
        expected_size=expected_size,
        trailing_bytes=max(0, len(data) - expected_size),
    )


def load_uni_gaiji_map(path: Path) -> tuple[dict[str, str], int]:
    """Load primary Unicode mappings from a LogoVista .uni/UNI file.

    Observed files start with ``Ver2  `` followed by a big-endian half-gaiji
    count, 16-byte half records, a big-endian full-gaiji count, then 16-byte
    full records. In each record, the first 16-bit field is the gaiji code and
    fields 2..3 are the primary Unicode sequence.
    """

    resource = parse_uni_resource(path)
    if resource is None:
        return {}, 0

    mapping: dict[str, str] = {}
    for record in resource.records:
        if record.display:
            mapping[record.code] = record.display
    return mapping, len(resource.records)


def load_plist_gaiji_map_from_paths(paths: list[Path]) -> tuple[dict[str, str], tuple[Path, ...]]:
    gaiji_map: dict[str, str] = {}
    loaded: list[Path] = []
    seen: set[Hashable] = set()
    for path in paths:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            data = plistlib.load(path.open("rb"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        loaded.append(path)
        for key, value in data.items():
            if not isinstance(key, str) or not GAIJI_CODE_RE.fullmatch(key):
                continue
            if isinstance(value, str):
                gaiji_map.setdefault(key.lower(), value)
    return gaiji_map, tuple(loaded)


def load_gaiji_profile(idx: Path) -> GaijiProfile:
    """Load a dictionary-specific gaiji profile.

    Unicode mappings from .uni/UNI files are preferred. Plist mappings are kept
    as fallbacks for codes missing from .uni.
    """

    uni_candidates, plist_candidates = candidate_gaiji_paths(idx)

    uni_map: dict[str, str] = {}
    uni_entries = 0
    uni_paths: list[Path] = []
    seen_uni: set[Hashable] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        mapping, entries = load_uni_gaiji_map(path)
        if entries:
            uni_paths.append(path)
            uni_map.update(mapping)
    uni_entries = len(uni_map)

    plist_map, plist_paths = load_plist_gaiji_map_from_paths(plist_candidates)
    merged = dict(plist_map)
    merged.update(uni_map)
    return GaijiProfile(
        map=merged,
        uni_entries=uni_entries,
        plist_entries=len(plist_map),
        uni_paths=tuple(uni_paths),
        plist_paths=plist_paths,
    )


def load_gaiji_map(idx: Path) -> dict[str, str]:
    return load_gaiji_profile(idx).map


def parse_ga16_resource(path: Path) -> Ga16Resource | None:
    """Parse a GA16HALF/GA16FULL bitmap-gaiji resource header."""

    data = path.read_bytes()
    if len(data) < 16:
        return None
    width = data[8]
    height = data[9]
    if width <= 0 or height <= 0:
        return None
    start_code = int.from_bytes(data[10:12], "big")
    count = int.from_bytes(data[12:14], "big")
    glyph_bytes = ga16_glyph_size(width, height)
    return Ga16Resource(
        path=path,
        width=width,
        height=height,
        start_code=start_code,
        count=count,
        glyph_bytes=glyph_bytes,
    )


def ga16_row_size(width: int) -> int:
    """Return stored bytes per bitmap row."""

    return (width + 7) // 8


def ga16_glyph_size(width: int, height: int) -> int:
    """Return stored bytes per bitmap glyph."""

    return ga16_row_size(width) * height


def render_ga16_glyph_rgba(
    glyph: bytes,
    width: int,
    height: int,
    *,
    foreground: tuple[int, int, int, int] = (0, 0, 0, 255),
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    """Render one GA16 bitmap glyph to packed RGBA bytes.

    Observed GA16 glyphs are 1bpp, row-major, and MSB-first within each row.
    Set bits are ink pixels.
    """

    row_size = ga16_row_size(width)
    required = row_size * height
    if len(glyph) < required:
        raise ValueError(f"glyph has {len(glyph)} bytes; expected at least {required}")

    fg = bytes(foreground)
    bg = bytes(background)
    pixels = bytearray(width * height * 4)
    pos = 0
    for y in range(height):
        row = glyph[y * row_size : (y + 1) * row_size]
        for x in range(width):
            byte = row[x // 8]
            bit = 0x80 >> (x % 8)
            pixels[pos : pos + 4] = fg if byte & bit else bg
            pos += 4
    return bytes(pixels)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = crc32(kind)
    crc = crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def encode_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Encode packed RGBA pixels as a PNG image without external dependencies."""

    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"RGBA payload has {len(rgba)} bytes; expected {expected}")

    stride = width * 4
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # filter type 0
        start = y * stride
        scanlines.extend(rgba[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines)))
        + _png_chunk(b"IEND", b"")
    )


def write_ga16_glyph_png(
    path: Path,
    glyph: bytes,
    width: int,
    height: int,
    *,
    foreground: tuple[int, int, int, int] = (0, 0, 0, 255),
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> None:
    rgba = render_ga16_glyph_rgba(
        glyph,
        width,
        height,
        foreground=foreground,
        background=background,
    )
    path.write_bytes(encode_png_rgba(width, height, rgba))
