#!/usr/bin/env python3
"""Raw LogoVista/SystemSoft SSED probes.

This is intentionally low-level: it treats the .IDX/.DIC files as the primary
dictionary source, not the mobile SQLite cache.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_bytes,
    decrypt_logofont_cipher_prefix,
)


BLOCK_SIZE = 2048
CHUNK_SIZE = 0x8000
WINDOW_SIZE = 0xFF0
SSEDDATA_MAGIC = b"SSEDDATA"
SSEDINFO_MAGIC = b"SSEDINFO"


def read_file_prefix(path: Path, size: int) -> bytes:
    with path.open("rb") as fh:
        return fh.read(size)


@dataclass(frozen=True)
class SsedInfoElement:
    index: int
    multi: int
    type: int
    start: int
    end: int
    data: bytes
    filename: str

    @property
    def block_count(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class SsedInfoLayout:
    """Physical layout details for an observed ``SSEDINFO`` catalog."""

    component_count_offset: int
    record_start: int
    record_size: int
    component_count: int
    trailing_bytes: int


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _is_ascii_filename(data: bytes) -> bool:
    if not data:
        return False
    return all(0x20 <= value < 0x7F for value in data)


def _decode_ssedinfo_filename(rec: bytes) -> tuple[str, bool]:
    if len(rec) < 0x11:
        return "", False
    length = rec[0x10]
    if 0 < length <= len(rec) - 0x11:
        raw = rec[0x11 : 0x11 + length]
        if _is_ascii_filename(raw):
            return raw.decode("ascii", errors="replace"), True
    raw = rec[0x11:].split(b"\x00", 1)[0]
    if _is_ascii_filename(raw):
        return raw.decode("ascii", errors="replace"), True
    return raw.decode("ascii", errors="replace"), False


def _parse_ssedinfo_records(
    data: bytes,
    *,
    component_count_offset: int,
    record_start: int,
    record_size: int = 0x30,
) -> tuple[list[SsedInfoElement], SsedInfoLayout, int]:
    if component_count_offset >= len(data):
        raise ValueError("component count offset outside SSEDINFO")
    n_element = data[component_count_offset]
    end = record_start + n_element * record_size
    if n_element == 0 or record_start < 0 or end > len(data):
        raise ValueError("component records outside SSEDINFO")

    elements: list[SsedInfoElement] = []
    valid_filenames = 0
    for index in range(n_element):
        pos = record_start + index * record_size
        rec = data[pos : pos + record_size]
        if len(rec) != record_size:
            raise ValueError("short SSEDINFO component record")
        filename, valid_filename = _decode_ssedinfo_filename(rec)
        valid_filenames += int(valid_filename)
        elements.append(
            SsedInfoElement(
                index=index,
                multi=rec[2],
                type=rec[3],
                start=int.from_bytes(rec[4:8], "big"),
                end=int.from_bytes(rec[8:12], "big"),
                data=bytes(rec[12:16]),
                filename=filename,
            )
        )

    layout = SsedInfoLayout(
        component_count_offset=component_count_offset,
        record_start=record_start,
        record_size=record_size,
        component_count=n_element,
        trailing_bytes=len(data) - end,
    )
    return elements, layout, valid_filenames


def parse_ssedinfo_with_layout(path: Path) -> tuple[str, list[SsedInfoElement], SsedInfoLayout]:
    data = path.read_bytes()
    if data[:8] != SSEDINFO_MAGIC:
        raise ValueError(f"not SSEDINFO: {path}")

    title_len = data[12]
    title = data[13 : 13 + title_len].split(b"\x00", 1)[0].decode("cp932", errors="replace")
    candidates = (
        (0x4D, 0x80),
        # Observed in LVLMultiView packages: the header/record table is
        # shifted left by one byte, but the record body layout is unchanged.
        (0x4C, 0x7F),
        (0x4C, 0x80),
        (0x4D, 0x7F),
    )
    parsed: list[tuple[int, int, list[SsedInfoElement], SsedInfoLayout]] = []
    for component_count_offset, record_start in candidates:
        try:
            elements, layout, score = _parse_ssedinfo_records(
                data,
                component_count_offset=component_count_offset,
                record_start=record_start,
            )
        except ValueError:
            continue
        parsed.append((score, int(component_count_offset == 0x4D and record_start == 0x80), elements, layout))

    if not parsed:
        raise ValueError(f"could not parse SSEDINFO component records: {path}")

    parsed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _score, _preferred, elements, layout = parsed[0]
    if _score != layout.component_count:
        raise ValueError(f"could not identify SSEDINFO component filename layout: {path}")
    return title, elements, layout


def parse_ssedinfo(path: Path) -> tuple[str, list[SsedInfoElement]]:
    title, elements, _layout = parse_ssedinfo_with_layout(path)
    return title, elements


def is_sseddata_bytes(data: bytes) -> bool:
    return data[:8] == SSEDDATA_MAGIC


def sseddata_storage_for_bytes(data: bytes) -> str:
    if is_sseddata_bytes(data):
        return "plain"
    try:
        if decrypt_logofont_cipher_prefix(data).startswith(SSEDDATA_MAGIC):
            return "logofont_cipher"
    except (LogoVistaCryptoError, LogoVistaCryptoUnavailable):
        return "unknown"
    return "unknown"


def sseddata_storage_for_file(path: Path) -> str:
    return sseddata_storage_for_bytes(read_file_prefix(path, BLOCK_SIZE))


def load_sseddata_bytes(data: bytes) -> tuple[bytes, str]:
    if is_sseddata_bytes(data):
        return data, "plain"
    try:
        decrypted = decrypt_logofont_cipher_bytes(data)
    except LogoVistaCryptoUnavailable as exc:
        raise ValueError(f"not SSEDDATA and encrypted support is unavailable: {exc}") from exc
    except LogoVistaCryptoError as exc:
        raise ValueError(f"not SSEDDATA: {exc}") from exc
    if is_sseddata_bytes(decrypted):
        return decrypted, "logofont_cipher"
    raise ValueError("not SSEDDATA")


def load_sseddata_file(path: Path) -> tuple[bytes, str]:
    try:
        return load_sseddata_bytes(path.read_bytes())
    except ValueError as exc:
        raise ValueError(f"{exc}: {path}") from exc


def parse_sseddata_header(path: Path) -> dict[str, int | bytes | str]:
    data = read_file_prefix(path, 64)
    storage = "plain"
    if not is_sseddata_bytes(data):
        storage = sseddata_storage_for_bytes(data)
        if storage != "logofont_cipher":
            raise ValueError(f"not SSEDDATA: {path}")
        data = decrypt_logofont_cipher_prefix(data, size=64)
    return {
        "magic": data[:8],
        "storage": storage,
        "kind": data[0x0F],
        "n_chunk": be16(data, 0x16),
        "start_block": be32(data, 0x18),
        "end_block": be32(data, 0x1C),
    }


def ssed_chunk_offsets(data: bytes) -> list[int]:
    n_chunk = be16(data, 0x16)
    return [be32(data, 64 + i * 4) for i in range(n_chunk)]


def expand_sseddata_chunk(data: bytes, chunk_offset: int) -> bytes:
    pos = chunk_offset + 2
    n_data = be16(data, pos)
    init = data[pos + 2]
    pos += 3

    window = bytearray([init]) * WINDOW_SIZE
    wintop = 0
    chunk_out = bytearray()

    for d_index in range(n_data):
        if pos + 3 > len(data):
            break
        b0, b1, literal = data[pos], data[pos + 1], data[pos + 2]
        pos += 3

        wp = (b0 << 4) | (b1 >> 4)
        length = b1 & 0x0F

        for _ in range(length):
            if len(chunk_out) >= CHUNK_SIZE or (
                d_index == n_data - 1 and len(chunk_out) % BLOCK_SIZE == 0
            ):
                break
            w = wp + wintop
            if w >= WINDOW_SIZE:
                w -= WINDOW_SIZE
            value = window[w]
            window[wintop] = value
            wintop = (wintop + 1) % WINDOW_SIZE
            chunk_out.append(value)

        if len(chunk_out) >= CHUNK_SIZE or (
            d_index == n_data - 1 and len(chunk_out) % BLOCK_SIZE == 0
        ):
            break

        window[wintop] = literal
        wintop = (wintop + 1) % WINDOW_SIZE
        chunk_out.append(literal)

    return bytes(chunk_out)


def expand_sseddata_bytes(data: bytes) -> bytes:
    data, _storage = load_sseddata_bytes(data)

    offsets = ssed_chunk_offsets(data)
    out = bytearray()

    for chunk_offset in offsets:
        out.extend(expand_sseddata_chunk(data, chunk_offset))

    return bytes(out)


def expand_sseddata_file(path: Path) -> bytes:
    data, _storage = load_sseddata_file(path)
    return expand_sseddata_bytes(data)


def expand_sseddata_file_with_storage(path: Path) -> tuple[bytes, str]:
    data, storage = load_sseddata_file(path)
    return expand_sseddata_bytes(data), storage


class SsedRandomReader:
    """Read slices from a SSED component without expanding the whole file."""

    def __init__(self, path: Path):
        self.path = path
        prefix = read_file_prefix(path, 64)
        self.data: bytes | None = None
        self.file_size = path.stat().st_size
        if is_sseddata_bytes(prefix):
            self.storage = "plain"
            n_chunk = be16(prefix, 0x16)
            header_size = 64 + (4 * n_chunk)
            header_bytes = read_file_prefix(path, header_size)
        else:
            # Encrypted components still need full-file decryption before chunk
            # offsets can be used. Plain SSEDDATA stays file-backed.
            self.data, self.storage = load_sseddata_file(path)
            header_bytes = self.data
        self.header = {
            "kind": header_bytes[0x0F],
            "n_chunk": be16(header_bytes, 0x16),
            "start_block": be32(header_bytes, 0x18),
            "end_block": be32(header_bytes, 0x1C),
        }
        self.offsets = ssed_chunk_offsets(header_bytes)
        self._chunk_cache: dict[int, bytes] = {}

    @property
    def start_block(self) -> int:
        return int(self.header["start_block"])

    @property
    def end_block(self) -> int:
        return int(self.header["end_block"])

    @property
    def expanded_size(self) -> int:
        return (self.end_block - self.start_block + 1) * BLOCK_SIZE

    def _chunk(self, index: int) -> bytes:
        if index < 0 or index >= len(self.offsets):
            return b""
        if index not in self._chunk_cache:
            chunk_offset = self.offsets[index]
            if self.data is not None:
                self._chunk_cache[index] = expand_sseddata_chunk(self.data, chunk_offset)
            else:
                next_offset = self.offsets[index + 1] if index + 1 < len(self.offsets) else self.file_size
                with self.path.open("rb") as fh:
                    fh.seek(chunk_offset)
                    chunk_data = fh.read(max(0, next_offset - chunk_offset))
                self._chunk_cache[index] = expand_sseddata_chunk(chunk_data, 0)
        return self._chunk_cache[index]

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or size <= 0:
            return b""
        out = bytearray()
        current = offset
        while len(out) < size:
            chunk_index = current // CHUNK_SIZE
            chunk_offset = current % CHUNK_SIZE
            chunk = self._chunk(chunk_index)
            if not chunk or chunk_offset >= len(chunk):
                break
            take = min(size - len(out), len(chunk) - chunk_offset)
            out.extend(chunk[chunk_offset : chunk_offset + take])
            current += take
        return bytes(out)


def command_info(args: argparse.Namespace) -> None:
    path = args.path
    data = read_file_prefix(path, 8)
    if data == b"SSEDINFO":
        title, elements = parse_ssedinfo(path)
        print(f"title: {title}")
        print(f"elements: {len(elements)}")
        for element in elements:
            if args.all or element.start:
                print(
                    f"{element.index:02d} {element.filename:16s} "
                    f"multi={element.multi:02x} type={element.type:02x} "
                    f"start={element.start:#x} end={element.end:#x} "
                    f"blocks={element.block_count:#x} data={element.data.hex()}"
                )
    elif data == b"SSEDDATA":
        header = parse_sseddata_header(path)
        print(
            f"chunks={header['n_chunk']} start={header['start_block']:#x} "
            f"end={header['end_block']:#x} kind={header['kind']:#x}"
        )
    else:
        raise ValueError(f"unknown raw file type: {path}")


def command_expand(args: argparse.Namespace) -> None:
    expanded = expand_sseddata_file(args.dic)
    args.out.write_bytes(expanded)
    print(f"expanded {args.dic} -> {args.out}")
    print(f"bytes: {len(expanded)}")


def find_case_insensitive(directory: Path, name: str) -> Path | None:
    direct = directory / name
    if direct.exists():
        return direct
    lower = name.lower()
    for child in directory.iterdir():
        if child.name.lower() == lower:
            return child
    return None


def write_epwing_catalog_header(out, elements: list[SsedInfoElement]) -> None:
    visible = [element for element in elements if element.start and element.multi != 0xFF]
    out.seek(0)
    out.write(len(visible).to_bytes(2, "big"))
    out.write(b"\x00" * 14)
    for element in visible:
        record = bytearray(16)
        record[0] = element.type
        record[2:6] = element.start.to_bytes(4, "big")
        record[6:10] = element.block_count.to_bytes(4, "big")
        record[10:14] = element.data
        out.write(record)
    for _ in range(len(visible) + 1, 0x80):
        out.write(b"\x00" * 16)


def command_expand_book(args: argparse.Namespace) -> None:
    title, elements = parse_ssedinfo(args.idx)
    source_dir = args.idx.parent
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w+b") as out:
        for element in elements:
            if not element.start:
                continue
            source = find_case_insensitive(source_dir, element.filename)
            if source is None:
                if args.strict:
                    raise FileNotFoundError(element.filename)
                print(f"skip missing {element.filename}")
                continue
            expanded = expand_sseddata_file(source)
            out.seek((element.start - 1) * BLOCK_SIZE)
            out.write(expanded)
            print(
                f"{element.filename:16s} start={element.start:#x} "
                f"bytes={len(expanded)}"
            )
        write_epwing_catalog_header(out, elements)
    print(f"title: {title}")
    print(f"expanded book: {args.out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info")
    p_info.add_argument("path", type=Path)
    p_info.add_argument("--all", action="store_true")
    p_info.set_defaults(func=command_info)

    p_expand = sub.add_parser("expand")
    p_expand.add_argument("dic", type=Path)
    p_expand.add_argument("out", type=Path)
    p_expand.set_defaults(func=command_expand)

    p_book = sub.add_parser("expand-book")
    p_book.add_argument("idx", type=Path)
    p_book.add_argument("out", type=Path)
    p_book.add_argument("--strict", action="store_true")
    p_book.set_defaults(func=command_expand_book)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
