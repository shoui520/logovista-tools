"""Older ``FIGURE.DIC`` monochrome figure support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .colscr import decode_bcd_decimal
from .gaiji import encode_png_rgba
from .navigation import address_key
from .ssed import BLOCK_SIZE, SsedInfoElement, SsedRandomReader, find_case_insensitive, parse_ssedinfo


FIGURE_TYPE = 0xD0
FIGURE_FILENAME = "FIGURE.DIC"


@dataclass(frozen=True)
class FigureDimensions:
    width: int
    height: int

    @property
    def row_stride(self) -> int:
        return (self.width + 7) // 8

    @property
    def bitmap_bytes(self) -> int:
        return self.row_stride * self.height

    def as_dict(self) -> dict[str, int]:
        return {
            "width": self.width,
            "height": self.height,
            "row_stride": self.row_stride,
            "bitmap_bytes": self.bitmap_bytes,
        }


@dataclass(frozen=True)
class FigurePointer:
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


def figure_element(elements: Iterable[SsedInfoElement]) -> SsedInfoElement | None:
    return next((element for element in elements if element.filename.upper() == FIGURE_FILENAME), None)


def find_figure_path(idx: Path) -> Path | None:
    _title, elements = parse_ssedinfo(idx)
    element = figure_element(elements)
    if element is None or not element.start:
        return None
    return find_case_insensitive(idx.parent, element.filename)


def parse_figure_dimensions(payload: bytes) -> FigureDimensions | None:
    """Parse the ten-byte ``1f44`` figure descriptor.

    Older ``FIGURE.DIC`` dictionaries store the display dimensions as packed
    BCD, but the order is height then width. The leading two bytes are an
    observed figure-kind field and are currently constant ``0001`` in the
    corpus.
    """

    if len(payload) < 10:
        return None
    height = decode_bcd_decimal(payload[2:6])
    width = decode_bcd_decimal(payload[6:10])
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return FigureDimensions(width=width, height=height)


def parse_figure_pointer(payload: bytes, element: SsedInfoElement) -> FigurePointer | None:
    """Parse the six-byte packed-BCD ``1f64`` target for ``FIGURE.DIC``."""

    if len(payload) < 6 or element.filename.upper() != FIGURE_FILENAME:
        return None
    block = decode_bcd_decimal(payload[:4])
    offset = decode_bcd_decimal(payload[4:6])
    if block is None or offset is None:
        return None
    if not (element.start <= block <= element.end):
        return None
    relative_offset = (block - element.start) * BLOCK_SIZE + offset
    return FigurePointer(block=block, offset=offset, relative_offset=relative_offset)


def read_figure_bitmap(
    reader: SsedRandomReader,
    pointer: FigurePointer,
    dimensions: FigureDimensions,
) -> bytes | None:
    if pointer.relative_offset < 0 or pointer.relative_offset + dimensions.bitmap_bytes > reader.expanded_size:
        return None
    bitmap = reader.read(pointer.relative_offset, dimensions.bitmap_bytes)
    if len(bitmap) != dimensions.bitmap_bytes:
        return None
    return bitmap


def figure_bitmap_to_rgba(
    bitmap: bytes,
    dimensions: FigureDimensions,
    *,
    foreground: tuple[int, int, int, int] = (0, 0, 0, 255),
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    """Convert a row-padded 1bpp ``FIGURE.DIC`` bitmap to RGBA pixels."""

    if len(bitmap) != dimensions.bitmap_bytes:
        raise ValueError(f"FIGURE bitmap has {len(bitmap)} bytes; expected {dimensions.bitmap_bytes}")
    pixels = bytearray()
    stride = dimensions.row_stride
    for y in range(dimensions.height):
        row = bitmap[y * stride : (y + 1) * stride]
        for x in range(dimensions.width):
            byte = row[x // 8]
            on = byte & (0x80 >> (x % 8))
            pixels.extend(foreground if on else background)
    return bytes(pixels)


def figure_bitmap_to_png(bitmap: bytes, dimensions: FigureDimensions) -> bytes:
    return encode_png_rgba(dimensions.width, dimensions.height, figure_bitmap_to_rgba(bitmap, dimensions))
