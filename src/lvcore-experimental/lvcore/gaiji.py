"""Dictionary-local gaiji resources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


UNI_MAGIC = b"Ver2  "


@dataclass(frozen=True)
class UniRecord:
    section: str
    index: int
    code: str
    display: str
    fallback: str
    legacy: str
    raw: bytes


@dataclass(frozen=True)
class GaijiMap:
    records: tuple[UniRecord, ...]
    mapping: dict[str, str]
    paths: tuple[Path, ...]

    def resolve(self, code: str) -> str | None:
        return self.mapping.get(code.lower())


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
        return gaiji_grid_code_for_index(self.start_code, index)

    def index_for_code(self, code: int) -> int:
        return gaiji_grid_index_for_code(self.start_code, code)

    def glyph(self, code: int) -> bytes | None:
        index = self.index_for_code(code)
        if index < 0 or index >= self.count:
            return None
        data = self.path.read_bytes()
        start = self.data_offset + index * self.glyph_bytes
        end = start + self.glyph_bytes
        if end > len(data):
            return None
        return data[start:end]


def ga16_row_size(width: int) -> int:
    return (width + 7) // 8


def ga16_glyph_size(width: int, height: int) -> int:
    return ga16_row_size(width) * height


def gaiji_grid_code_for_index(start_code: int, index: int) -> int:
    row = (start_code >> 8) & 0xFF
    cell = start_code & 0xFF
    if index < 0:
        raise ValueError("gaiji index must be non-negative")
    if not 0x21 <= cell <= 0x7E:
        return start_code + index
    cell_index = (cell - 0x21) + index
    row += cell_index // 0x5E
    cell = 0x21 + (cell_index % 0x5E)
    return (row << 8) | cell


def gaiji_grid_index_for_code(start_code: int, code: int) -> int:
    start_row = (start_code >> 8) & 0xFF
    start_cell = start_code & 0xFF
    row = (code >> 8) & 0xFF
    cell = code & 0xFF
    if not (0x21 <= start_cell <= 0x7E and 0x21 <= cell <= 0x7E):
        return code - start_code
    return (row - start_row) * 0x5E + (cell - start_cell)


def parse_ga16(path: Path) -> Ga16Resource | None:
    data = path.read_bytes()
    if len(data) < 16:
        return None
    width = data[8]
    height = data[9]
    if width <= 0 or height <= 0:
        return None
    count = int.from_bytes(data[12:14], "big")
    return Ga16Resource(
        path=path,
        width=width,
        height=height,
        start_code=int.from_bytes(data[10:12], "big"),
        count=count,
        glyph_bytes=ga16_glyph_size(width, height),
    )


def decode_utf16_units(values: tuple[int, ...]) -> str:
    data = b"".join(value.to_bytes(2, "big") for value in values if value)
    if not data:
        return ""
    return data.decode("utf-16-be", errors="ignore")


def _parse_records(data: bytes, offset: int, count: int, record_size: int, section: str) -> tuple[list[UniRecord], int]:
    records: list[UniRecord] = []
    for index in range(count):
        start = offset + index * record_size
        end = start + record_size
        if end > len(data):
            break
        raw = data[start:end]
        code = raw[:2].hex().lower()
        fields = tuple(int.from_bytes(raw[pos : pos + 2], "big") for pos in range(0, len(raw), 2))
        display_units = fields[2:4]
        fallback_units = fields[4:6]
        legacy_units = fields[6:8] if len(fields) >= 8 else ()
        records.append(
            UniRecord(
                section=section,
                index=index,
                code=code,
                display=decode_utf16_units(display_units),
                fallback=decode_utf16_units(fallback_units),
                legacy=decode_utf16_units(legacy_units),
                raw=raw,
            )
        )
    return records, offset + count * record_size


def parse_uni(path: Path) -> tuple[UniRecord, ...]:
    data = path.read_bytes()
    if len(data) < 4:
        return ()
    if data.startswith(UNI_MAGIC):
        half_count = int.from_bytes(data[6:10], "big")
        half, pos = _parse_records(data, 10, half_count, 16, "half")
        if pos + 4 > len(data):
            return tuple(half)
        full_count = int.from_bytes(data[pos : pos + 4], "big")
        full, _pos = _parse_records(data, pos + 4, full_count, 16, "full")
        return tuple(half + full)

    half_count = int.from_bytes(data[:4], "big")
    half, pos = _parse_records(data, 4, half_count, 12, "half")
    if pos == len(data):
        return tuple(half)
    if pos + 4 > len(data):
        return tuple(half)
    full_count = int.from_bytes(data[pos : pos + 4], "big")
    full, _pos = _parse_records(data, pos + 4, full_count, 12, "full")
    return tuple(half + full)


def _exinfo_uni_names(root: Path) -> list[Path]:
    paths: list[Path] = []
    exinfo = root / "EXINFO.INI"
    if not exinfo.exists():
        exinfo = root / "exinfo.ini"
    if not exinfo.exists():
        return paths
    for raw_line in exinfo.read_text(encoding="cp932", errors="ignore").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        if not key.strip().upper().startswith("GAIJI"):
            continue
        value = value.strip().strip('"').replace("\\", "/")
        if value.lower().endswith(".uni"):
            candidate = Path(value)
            paths.append(candidate if candidate.is_absolute() else root / candidate)
    return paths


def load_gaiji_map(root: Path, dict_id: str) -> GaijiMap:
    candidates = [
        root / f"{dict_id}.uni",
        root / f"{dict_id}.UNI",
        root / f"{dict_id.upper()}.uni",
        root / f"{dict_id.upper()}.UNI",
    ]
    candidates.extend(_exinfo_uni_names(root))
    candidates.extend(sorted(root.glob("*.uni")))
    candidates.extend(sorted(root.glob("*.UNI")))
    records: list[UniRecord] = []
    paths: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        parsed = parse_uni(path)
        if parsed:
            records.extend(parsed)
            paths.append(path)
    mapping = {record.code: record.display for record in records if record.display}
    return GaijiMap(records=tuple(records), mapping=mapping, paths=tuple(paths))
