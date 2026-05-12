"""Panel XML/BIN helpers for observed Windows LogoVista Panel assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from .entries import decode_tokens, tokens_to_text


@dataclass(frozen=True)
class PanelBinRecord:
    """One fixed-width Panel BIN cell record."""

    index: int
    block: int
    offset: int
    text_bytes: bytes
    text: str


@dataclass(frozen=True)
class PanelBin:
    """Decoded fixed-record Panel BIN payload."""

    record_count: int
    text_width: int
    records: tuple[PanelBinRecord, ...]


@dataclass(frozen=True)
class PanelDataReference:
    """One external ``<data type="bin" filename="...">`` reference."""

    panel_index: str
    panel_type: str
    data_type: str
    filename: str
    title: str


class PanelBinParseError(ValueError):
    """Raised when a Panel BIN payload does not match the fixed-record grammar."""


def decode_panel_text(data: bytes) -> str:
    """Decode fixed-width Panel cell text bytes.

    Observed Panel text fields use the same JIS-pair text stream conventions as
    body/title text: NUL padding, 7-bit JIS pairs, gaiji pairs, and lightweight
    0x1f display controls such as halfwidth/superscript spans.
    """

    tokens, _stats = decode_tokens(data.rstrip(b"\x00"), gaiji="placeholder")
    return tokens_to_text(tokens)


def parse_panel_bin(data: bytes) -> PanelBin:
    """Parse an observed fixed-record Panel BIN payload."""

    if len(data) < 8:
        raise PanelBinParseError("panel BIN is shorter than the 8-byte header")
    record_count = int.from_bytes(data[0:4], "little")
    text_width = int.from_bytes(data[4:8], "little")
    record_size = 8 + text_width
    expected = 8 + record_count * record_size
    if text_width <= 0:
        raise PanelBinParseError("panel BIN text width must be positive")
    if len(data) != expected:
        raise PanelBinParseError(
            f"panel BIN size mismatch: count={record_count} text_width={text_width} "
            f"expected={expected} actual={len(data)}"
        )

    records: list[PanelBinRecord] = []
    pos = 8
    for index in range(record_count):
        block = int.from_bytes(data[pos : pos + 4], "little")
        offset = int.from_bytes(data[pos + 4 : pos + 8], "little")
        text_bytes = data[pos + 8 : pos + 8 + text_width]
        records.append(
            PanelBinRecord(
                index=index,
                block=block,
                offset=offset,
                text_bytes=text_bytes.rstrip(b"\x00"),
                text=decode_panel_text(text_bytes),
            )
        )
        pos += record_size
    return PanelBin(record_count=record_count, text_width=text_width, records=tuple(records))


def parse_panel_bin_file(path: Path) -> PanelBin:
    return parse_panel_bin(path.read_bytes())


def iter_panel_data_references(path: Path) -> Iterable[PanelDataReference]:
    """Yield external Panel data references from a ``Panels.xml`` file."""

    tree = ElementTree.parse(path)
    root = tree.getroot()
    for panel in root.findall("panel"):
        title = panel.findtext("title") or ""
        panel_index = panel.attrib.get("index", "")
        panel_type = panel.attrib.get("paneltype", "")
        for data in panel.findall("data"):
            filename = data.attrib.get("filename")
            if not filename:
                continue
            yield PanelDataReference(
                panel_index=panel_index,
                panel_type=panel_type,
                data_type=data.attrib.get("type", ""),
                filename=filename,
                title=title,
            )
