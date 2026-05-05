"""Dictionary-specific gaiji mapping helpers."""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Hashable


GAIJI_CODE_RE = re.compile(r"[A-Fa-f0-9]{4}")
UNI_MAGIC = b"Ver2  "
UNI_HEADER_SIZE = 10
UNI_RECORD_SIZE = 16


@dataclass(frozen=True)
class GaijiProfile:
    """Resolved gaiji mappings for one dictionary."""

    map: dict[str, str]
    uni_entries: int
    plist_entries: int
    uni_paths: tuple[Path, ...]
    plist_paths: tuple[Path, ...]


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
        start = self.data_offset + index * self.glyph_bytes
        end = start + self.glyph_bytes
        if end > len(data):
            return None
        return data[start:end]


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


def decode_uni_code_units(values: list[int]) -> str:
    chars: list[str] = []
    for value in values:
        if value == 0 or 0xD800 <= value <= 0xDFFF:
            continue
        try:
            chars.append(chr(value))
        except ValueError:
            continue
    return "".join(chars)


def parse_uni_records(data: bytes, offset: int, count: int) -> tuple[dict[str, str], int]:
    mapping: dict[str, str] = {}
    records_seen = 0
    for index in range(count):
        start = offset + index * UNI_RECORD_SIZE
        end = start + UNI_RECORD_SIZE
        if end > len(data):
            break
        record = data[start:end]
        code = record[:2].hex().lower()
        if not GAIJI_CODE_RE.fullmatch(code):
            continue
        values = [int.from_bytes(record[i : i + 2], "big") for i in range(0, UNI_RECORD_SIZE, 2)]
        primary = decode_uni_code_units(values[2:4])
        if primary:
            mapping.setdefault(code, primary)
        records_seen += 1
    return mapping, records_seen


def load_uni_gaiji_map(path: Path) -> tuple[dict[str, str], int]:
    """Load primary Unicode mappings from a LogoVista .uni/UNI file.

    Observed files start with ``Ver2  `` followed by a big-endian half-gaiji
    count, 16-byte half records, a big-endian full-gaiji count, then 16-byte
    full records. In each record, the first 16-bit field is the gaiji code and
    fields 2..3 are the primary Unicode sequence.
    """

    data = path.read_bytes()
    if len(data) < UNI_HEADER_SIZE or data[:6] != UNI_MAGIC:
        return {}, 0

    half_count = int.from_bytes(data[6:10], "big")
    half_offset = UNI_HEADER_SIZE
    half_map, half_seen = parse_uni_records(data, half_offset, half_count)

    full_count_offset = half_offset + half_count * UNI_RECORD_SIZE
    if full_count_offset + 4 > len(data):
        return half_map, half_seen

    full_count = int.from_bytes(data[full_count_offset : full_count_offset + 4], "big")
    full_offset = full_count_offset + 4
    full_map, full_seen = parse_uni_records(data, full_offset, full_count)

    merged = dict(half_map)
    merged.update(full_map)
    return merged, half_seen + full_seen


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
    if width <= 0 or height <= 0 or (width * height) % 8 != 0:
        return None
    start_code = int.from_bytes(data[10:12], "big")
    count = int.from_bytes(data[12:14], "big")
    glyph_bytes = (width * height) // 8
    if count <= 0:
        return None
    return Ga16Resource(
        path=path,
        width=width,
        height=height,
        start_code=start_code,
        count=count,
        glyph_bytes=glyph_bytes,
    )
