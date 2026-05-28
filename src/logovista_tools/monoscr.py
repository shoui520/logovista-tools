"""KOJIEN6 ``MONOSCR.DIC`` monochrome bitmap support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .colscr import decode_bcd_decimal
from .gaiji import encode_png_rgba
from .navigation import address_key
from .ssed import BLOCK_SIZE, SsedInfoElement, SsedRandomReader, find_case_insensitive, parse_ssedinfo


MONOSCR_TYPE = 0xD0
MONOSCR_FILENAME = "MONOSCR.DIC"
MONOSCR_WIDTH = 64
MONOSCR_HEIGHT = 64
MONOSCR_BITMAP_BYTES = (MONOSCR_WIDTH * MONOSCR_HEIGHT) // 8


@dataclass(frozen=True)
class MonoScrPointer:
    block: int
    offset: int
    relative_offset: int

    @property
    def address_key(self) -> str:
        return address_key(self.block, self.offset)

    def as_dict(self) -> dict[str, Any]:
        return {
            "block": self.block,
            "offset": self.offset,
            "relative_offset": self.relative_offset,
            "address_key": self.address_key,
        }


def monoscr_element(elements: Iterable[SsedInfoElement]) -> SsedInfoElement | None:
    return next((element for element in elements if element.filename.upper() == MONOSCR_FILENAME), None)


def find_monoscr_path(idx: Path) -> Path | None:
    _title, elements = parse_ssedinfo(idx)
    element = monoscr_element(elements)
    if element is None or not element.start:
        return None
    return find_case_insensitive(idx.parent, element.filename)


def parse_monoscr_pointer(payload: bytes, element: SsedInfoElement) -> MonoScrPointer | None:
    """Parse the packed-BCD pointer carried by KOJIEN6 ``1f64`` controls."""

    if len(payload) < 6 or element.filename.upper() != MONOSCR_FILENAME:
        return None
    block = decode_bcd_decimal(payload[:4])
    offset = decode_bcd_decimal(payload[4:6])
    if block is None or offset is None:
        return None
    if not (element.start <= block <= element.end):
        return None
    relative_offset = (block - element.start) * BLOCK_SIZE + offset
    return MonoScrPointer(block=block, offset=offset, relative_offset=relative_offset)


def read_monoscr_bitmap(reader: SsedRandomReader, pointer: MonoScrPointer) -> bytes | None:
    if pointer.relative_offset < 0 or pointer.relative_offset + MONOSCR_BITMAP_BYTES > reader.expanded_size:
        return None
    bitmap = reader.read(pointer.relative_offset, MONOSCR_BITMAP_BYTES)
    if len(bitmap) != MONOSCR_BITMAP_BYTES:
        return None
    return bitmap


def monoscr_bitmap_to_rgba(
    bitmap: bytes,
    *,
    foreground: tuple[int, int, int, int] = (0, 0, 0, 255),
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    """Convert a KOJIEN6 64x64 1bpp bitmap cell to RGBA pixels."""

    if len(bitmap) != MONOSCR_BITMAP_BYTES:
        raise ValueError(f"MONOSCR bitmap has {len(bitmap)} bytes; expected {MONOSCR_BITMAP_BYTES}")
    pixels = bytearray()
    for byte in bitmap:
        for bit in range(8):
            pixels.extend(foreground if byte & (0x80 >> bit) else background)
    return bytes(pixels)


def monoscr_bitmap_to_png(bitmap: bytes) -> bytes:
    return encode_png_rgba(MONOSCR_WIDTH, MONOSCR_HEIGHT, monoscr_bitmap_to_rgba(bitmap))
