#!/usr/bin/env python3
"""Raw LogoVista/SystemSoft SSED probes.

This is intentionally low-level: it treats the .IDX/.DIC files as the primary
dictionary source, not the mobile SQLite cache.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


BLOCK_SIZE = 2048
CHUNK_SIZE = 0x8000
WINDOW_SIZE = 0xFF0


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


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def parse_ssedinfo(path: Path) -> tuple[str, list[SsedInfoElement]]:
    data = path.read_bytes()
    if data[:8] != b"SSEDINFO":
        raise ValueError(f"not SSEDINFO: {path}")

    title_len = data[12]
    title = data[13 : 13 + title_len].split(b"\x00", 1)[0].decode("cp932", errors="replace")
    n_element = data[0x4D]
    elements: list[SsedInfoElement] = []
    pos = 0x80
    for index in range(n_element):
        rec = data[pos : pos + 0x30]
        pos += 0x30
        filename = rec[17:].split(b"\x00", 1)[0].decode("ascii", errors="replace")
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
    return title, elements


def parse_sseddata_header(path: Path) -> dict[str, int | bytes]:
    data = path.read_bytes()[:64]
    if data[:8] != b"SSEDDATA":
        raise ValueError(f"not SSEDDATA: {path}")
    return {
        "magic": data[:8],
        "kind": data[0x0F],
        "n_chunk": be16(data, 0x16),
        "start_block": be32(data, 0x18),
        "end_block": be32(data, 0x1C),
    }


def expand_sseddata_bytes(data: bytes) -> bytes:
    if data[:8] != b"SSEDDATA":
        raise ValueError("not SSEDDATA")

    n_chunk = be16(data, 0x16)
    offsets = [be32(data, 64 + i * 4) for i in range(n_chunk)]
    out = bytearray()

    for chunk_offset in offsets:
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

        out.extend(chunk_out)

    return bytes(out)


def expand_sseddata_file(path: Path) -> bytes:
    return expand_sseddata_bytes(path.read_bytes())


def command_info(args: argparse.Namespace) -> None:
    path = args.path
    data = path.read_bytes()[:8]
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
