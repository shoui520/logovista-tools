"""SSED container parsing and expansion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .crypto import decrypt_logofont, decrypt_logofont_prefix
from .errors import FormatError
from .model import Component, ComponentRole


BLOCK_SIZE = 2048
CHUNK_SIZE = 0x8000
WINDOW_SIZE = 0xFF0
SSEDINFO = b"SSEDINFO"
SSEDDATA = b"SSEDDATA"


TITLE_TYPES = {0x03, 0x04, 0x05, 0x06, 0x07, 0x09, 0x0A, 0x0D}
INDEX_TYPES = {0x30, 0x60, 0x70, 0x71, 0x72, 0x80, 0x81, 0x90, 0x91, 0x92, 0xA1}
MENU_TYPES = {0x01}
GAIJI_TYPES = {0xF1, 0xF2}
MEDIA_NAMES = {"COLSCR.DIC", "PCMDATA.DIC"}
TEXT_LIKE_INDEX_OUTLIER_TYPES = {0x27}


def read_file_prefix(path: Path, size: int) -> bytes:
    with path.open("rb") as fh:
        return fh.read(size)


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def component_role(name: str, typ: int) -> ComponentRole:
    upper = name.upper()
    if upper == "HONMON.DIC" or typ == 0x00:
        return ComponentRole.HONMON
    if typ in TITLE_TYPES or upper.endswith("TITLE.DIC"):
        return ComponentRole.TITLE
    if typ in TEXT_LIKE_INDEX_OUTLIER_TYPES and upper == "INDEX.DIC":
        return ComponentRole.RESOURCE
    if typ in INDEX_TYPES or upper.endswith("INDEX.DIC"):
        return ComponentRole.INDEX
    if typ in MENU_TYPES or upper == "MENU.DIC":
        return ComponentRole.MENU
    if typ in GAIJI_TYPES or upper.startswith(("GA16", "GAI16")):
        return ComponentRole.GAIJI
    if upper in MEDIA_NAMES:
        return ComponentRole.MEDIA
    return ComponentRole.UNKNOWN


def is_ssedinfo(path: Path) -> bool:
    try:
        return path.is_file() and read_file_prefix(path, 8) == SSEDINFO
    except OSError:
        return False


def candidate_idx_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".idx" else []
    return sorted(file for file in path.glob("*.IDX")) + sorted(file for file in path.glob("*.idx"))


def _filename_from_record(record: bytes) -> tuple[str, bool]:
    if len(record) < 0x11:
        return "", False
    length = record[0x10]
    if 0 < length <= len(record) - 0x11:
        raw = record[0x11 : 0x11 + length]
        if all(0x20 <= b < 0x7F for b in raw):
            return raw.decode("ascii"), True
    raw = record[0x11:].split(b"\x00", 1)[0]
    ok = bool(raw) and all(0x20 <= b < 0x7F for b in raw)
    return raw.decode("ascii", errors="replace"), ok


@dataclass(frozen=True)
class Catalog:
    path: Path
    title: str
    components: tuple[Component, ...]
    count_offset: int
    record_start: int

    @property
    def dict_id(self) -> str:
        return self.path.stem

    def component_named(self, name: str) -> Component | None:
        folded = name.lower()
        for component in self.components:
            if component.name.lower() == folded:
                return component
        return None


def parse_catalog(path: Path) -> Catalog:
    data = path.read_bytes()
    if data[:8] != SSEDINFO:
        raise FormatError(f"not SSEDINFO: {path}")
    title_len = data[12] if len(data) > 12 else 0
    title = data[13 : 13 + title_len].split(b"\x00", 1)[0].decode("cp932", errors="replace")

    candidates = ((0x4D, 0x80), (0x4C, 0x7F), (0x4C, 0x80), (0x4D, 0x7F))
    parsed: list[tuple[int, int, int, int, list[Component]]] = []
    for count_offset, record_start in candidates:
        if count_offset >= len(data):
            continue
        count = data[count_offset]
        end = record_start + count * 0x30
        if count == 0 or end > len(data):
            continue
        components: list[Component] = []
        score = 0
        for index in range(count):
            record = data[record_start + index * 0x30 : record_start + (index + 1) * 0x30]
            name, ok = _filename_from_record(record)
            score += int(ok)
            typ = record[3]
            components.append(
                Component(
                    name=name,
                    type=typ,
                    start_block=be32(record, 4),
                    end_block=be32(record, 8),
                    data=bytes(record[12:16]),
                    index=index,
                    multi=record[2],
                    role=component_role(name, typ),
                )
            )
        parsed.append((score, int((count_offset, record_start) == (0x4D, 0x80)), count_offset, record_start, components))
    if not parsed:
        raise FormatError(f"could not parse SSEDINFO component table: {path}")
    parsed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    score, _preferred, count_offset, record_start, components = parsed[0]
    if score != len(components):
        raise FormatError(f"could not identify SSEDINFO filename layout: {path}")
    invalid_ranges = [
        component.name or f"component[{component.index}]"
        for component in components
        if component.end_block < component.start_block and (component.start_block or component.end_block)
    ]
    if invalid_ranges:
        raise FormatError(f"SSEDINFO component has invalid block range: {', '.join(invalid_ranges[:3])}")
    return Catalog(path=path, title=title, components=tuple(components), count_offset=count_offset, record_start=record_start)


def find_file_case_insensitive(root: Path, name: str) -> Path | None:
    direct = root / name
    if direct.exists():
        return direct
    target = name.lower()
    try:
        for child in root.iterdir():
            if child.name.lower() == target:
                return child
    except OSError:
        return None
    return None


@dataclass(frozen=True)
class SsedHeader:
    kind: int
    chunk_count: int
    start_block: int
    end_block: int
    storage: str

    @property
    def expanded_size(self) -> int:
        if self.end_block < self.start_block and (self.start_block or self.end_block):
            raise FormatError("SSEDDATA header has invalid block range")
        return max(0, self.end_block - self.start_block + 1) * BLOCK_SIZE


def load_sseddata_bytes(raw: bytes) -> tuple[bytes, str]:
    if raw[:8] == SSEDDATA:
        return raw, "plain"
    try:
        prefix = decrypt_logofont_prefix(raw[:BLOCK_SIZE], size=64)
    except Exception as exc:
        raise FormatError(f"not SSEDDATA and LogoFontCipher detection failed: {exc}") from exc
    if prefix[:8] != SSEDDATA:
        raise FormatError("not SSEDDATA")
    data = decrypt_logofont(raw)
    if data[:8] != SSEDDATA:
        raise FormatError("LogoFontCipher plaintext is not SSEDDATA")
    return data, "logofont_cipher"


def parse_data_header(data: bytes, storage: str = "plain") -> SsedHeader:
    if data[:8] != SSEDDATA:
        raise FormatError("not SSEDDATA")
    if len(data) < 0x20:
        raise FormatError("SSEDDATA header truncated")
    return SsedHeader(
        kind=data[0x0F],
        chunk_count=be16(data, 0x16),
        start_block=be32(data, 0x18),
        end_block=be32(data, 0x1C),
        storage=storage,
    )


def chunk_offsets(data: bytes) -> list[int]:
    count = be16(data, 0x16)
    table_end = 64 + count * 4
    if table_end > len(data):
        raise FormatError("SSEDDATA chunk table exceeds file length")
    return [be32(data, 64 + index * 4) for index in range(count)]


def expand_chunk(data: bytes, offset: int) -> bytes:
    if offset + 5 > len(data):
        raise FormatError("SSEDDATA chunk offset outside file")
    pos = offset + 2
    command_count = be16(data, pos)
    init = data[pos + 2]
    pos += 3

    window = bytearray([init]) * WINDOW_SIZE
    wintop = 0
    out = bytearray()

    for command_index in range(command_count):
        if pos + 3 > len(data):
            raise FormatError("SSEDDATA chunk command stream truncated")
        b0, b1, literal = data[pos], data[pos + 1], data[pos + 2]
        pos += 3
        window_offset = (b0 << 4) | (b1 >> 4)
        copy_length = b1 & 0x0F

        for _ in range(copy_length):
            if len(out) >= CHUNK_SIZE or (command_index == command_count - 1 and len(out) % BLOCK_SIZE == 0):
                break
            read = window_offset + wintop
            if read >= WINDOW_SIZE:
                read -= WINDOW_SIZE
            value = window[read]
            window[wintop] = value
            wintop = (wintop + 1) % WINDOW_SIZE
            out.append(value)

        if len(out) >= CHUNK_SIZE or (command_index == command_count - 1 and len(out) % BLOCK_SIZE == 0):
            break

        window[wintop] = literal
        wintop = (wintop + 1) % WINDOW_SIZE
        out.append(literal)

    return bytes(out)


def expand_sseddata(data: bytes) -> bytes:
    data, _storage = load_sseddata_bytes(data)
    out = bytearray()
    for offset in chunk_offsets(data):
        out.extend(expand_chunk(data, offset))
    return bytes(out)


class SsedData:
    """Random-readable expanded view of one SSED component."""

    def __init__(self, path: Path):
        self.path = path
        prefix = read_file_prefix(path, 64)
        self.file_size = path.stat().st_size
        self.data: bytes | None
        if prefix[:8] == SSEDDATA:
            storage = "plain"
            chunk_count = be16(prefix, 0x16)
            header_bytes = read_file_prefix(path, 64 + chunk_count * 4)
            self.data = None
        else:
            self.data, storage = load_sseddata_bytes(path.read_bytes())
            header_bytes = self.data
        self.header = parse_data_header(header_bytes, storage)
        self.offsets = chunk_offsets(header_bytes)
        self._cache: dict[int, bytes] = {}

    @property
    def expanded_size(self) -> int:
        return self.header.expanded_size

    def chunk(self, index: int) -> bytes:
        if index < 0 or index >= len(self.offsets):
            return b""
        if index not in self._cache:
            offset = self.offsets[index]
            if self.data is None:
                next_offset = self.offsets[index + 1] if index + 1 < len(self.offsets) else self.file_size
                if next_offset <= offset:
                    next_offset = self.file_size
                with self.path.open("rb") as fh:
                    fh.seek(offset)
                    self._cache[index] = expand_chunk(fh.read(max(0, next_offset - offset)), 0)
            else:
                self._cache[index] = expand_chunk(self.data, offset)
        return self._cache[index]

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if size <= 0:
            return b""
        out = bytearray()
        current = offset
        while len(out) < size:
            chunk_index = current // CHUNK_SIZE
            chunk_offset = current % CHUNK_SIZE
            chunk = self.chunk(chunk_index)
            if not chunk or chunk_offset >= len(chunk):
                break
            take = min(size - len(out), len(chunk) - chunk_offset)
            out.extend(chunk[chunk_offset : chunk_offset + take])
            current += take
        return bytes(out)

    def expand(self) -> bytes:
        out = bytearray()
        for index in range(len(self.offsets)):
            out.extend(self.chunk(index))
        return bytes(out)
