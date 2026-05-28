"""KOJIEN6-style ``COLSMPL.DIC`` color sample parsing."""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .entries import decode_jis_pair
from .ssed import BLOCK_SIZE, SsedInfoElement, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


COLSMPL_TYPE = 0xE0
COLSMPL_RECORD_SIZE = BLOCK_SIZE

MUNSELL_BYTE_MAP = {
    **{0xF0 + value: str(value) for value in range(10)},
    0x4B: ".",
    0x61: "/",
    0xC2: "B",
    0xC7: "G",
    0xD5: "N",
    0xD7: "P",
    0xD9: "R",
    0xE8: "Y",
}

_MUNSELL_HUE_OR_VALUE_NUMBER = r"(?:10|\d(?:\.\d)?)"
_MUNSELL_CHROMA_NUMBER = r"(?:\d{1,2}(?:\.\d)?)"
MUNSELL_RE = re.compile(
    rf"^(?:(?P<hue_number>{_MUNSELL_HUE_OR_VALUE_NUMBER})(?P<hue_letters>R|YR|Y|GY|G|BG|B|PB|P|RP)|N)"
    rf"(?P<value>{_MUNSELL_HUE_OR_VALUE_NUMBER})(?:/(?P<chroma>{_MUNSELL_CHROMA_NUMBER}))?$"
)

HUE_BASE_DEGREES = {
    "R": 0.0,
    "YR": 36.0,
    "Y": 72.0,
    "GY": 108.0,
    "G": 144.0,
    "BG": 180.0,
    "B": 216.0,
    "PB": 252.0,
    "P": 288.0,
    "RP": 324.0,
}


@dataclass(frozen=True)
class MunsellSpec:
    notation: str
    hue_number: float | None
    hue_letters: str | None
    value: float
    chroma: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "notation": self.notation,
            "hue_number": self.hue_number,
            "hue_letters": self.hue_letters,
            "value": self.value,
            "chroma": self.chroma,
        }


@dataclass(frozen=True)
class ColorSampleRecord:
    record_index: int
    sample_number: int
    sample_key: str | None
    munsell_raw: bytes
    munsell: str
    label_raw: bytes
    label: str
    estimated_rgb: tuple[int, int, int] | None = None

    @property
    def estimated_rgb_hex(self) -> str | None:
        if self.estimated_rgb is None:
            return None
        return "".join(f"{value:02x}" for value in self.estimated_rgb)

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_index": self.record_index,
            "sample_number": self.sample_number,
            "sample_key": self.sample_key,
            "munsell_raw": self.munsell_raw.hex(),
            "munsell": self.munsell,
            "label_raw": self.label_raw.hex(),
            "label": self.label,
            "estimated_rgb": list(self.estimated_rgb) if self.estimated_rgb is not None else None,
            "estimated_rgb_hex": self.estimated_rgb_hex,
            "rgb_status": "estimated_from_munsell" if self.estimated_rgb is not None else "unresolved",
        }


@dataclass(frozen=True)
class ColorSampleTable:
    records: tuple[ColorSampleRecord, ...]

    @property
    def by_sample_key(self) -> dict[str, ColorSampleRecord]:
        return {record.sample_key: record for record in self.records if record.sample_key is not None}

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        records = self.records if limit is None else self.records[:limit]
        return {
            "schema": "logovista-colsmpl-v1",
            "record_count": len(self.records),
            "mapped_sample_keys": len(self.by_sample_key),
            "records": [record.as_dict() for record in records],
        }


def _trim_null(data: bytes) -> bytes:
    return data.split(b"\x00", 1)[0]


def decode_munsell_notation(raw: bytes) -> str:
    chars: list[str] = []
    for byte in _trim_null(raw):
        char = MUNSELL_BYTE_MAP.get(byte)
        if char is None:
            chars.append(f"<{byte:02x}>")
        else:
            chars.append(char)
    return "".join(chars)


def parse_munsell_spec(notation: str) -> MunsellSpec | None:
    match = MUNSELL_RE.match(notation)
    if match is None:
        return None
    hue_number_text = match.group("hue_number")
    return MunsellSpec(
        notation=notation,
        hue_number=float(hue_number_text) if hue_number_text is not None else None,
        hue_letters=match.group("hue_letters"),
        value=float(match.group("value")),
        chroma=float(match.group("chroma")) if match.group("chroma") is not None else None,
    )


def munsell_to_estimated_rgb(notation: str) -> tuple[int, int, int] | None:
    """Return a conservative display estimate for a decoded Munsell notation.

    ``COLSMPL.DIC`` stores Munsell notation, not direct RGB. The official
    renderer calls a separate color-map bridge to obtain display RGB values.
    This estimate exists only so open HTML reports can show an intelligible
    swatch while keeping the exact decoded notation in metadata.
    """

    spec = parse_munsell_spec(notation)
    if spec is None:
        return None

    lightness = max(0.0, min(1.0, spec.value / 10.0))
    if spec.hue_letters is None or spec.chroma is None:
        channel = round(lightness * 255)
        return (channel, channel, channel)

    base = HUE_BASE_DEGREES[spec.hue_letters]
    hue_number = spec.hue_number if spec.hue_number is not None else 5.0
    hue = ((base + ((hue_number - 5.0) / 10.0) * 36.0) % 360.0) / 360.0
    saturation = max(0.0, min(0.82, (spec.chroma / 16.0) * 0.82))
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return (round(red * 255), round(green * 255), round(blue * 255))


def decode_color_sample_label(raw: bytes) -> str:
    field = _trim_null(raw)
    chars: list[str] = []
    for offset in range(0, len(field) - 1, 2):
        first = field[offset]
        second = field[offset + 1]
        if 0x21 <= first <= 0x7E and 0x21 <= second <= 0x7E:
            chars.append(decode_jis_pair(bytes((first, second))) or f"<{first:02x}{second:02x}>")
        else:
            chars.append(f"<{first:02x}{second:02x}>")
    return "".join(chars)


def _sample_key_for_number(sample_number: int) -> str | None:
    if not 1 <= sample_number <= 99:
        return None
    tens, ones = divmod(sample_number, 10)
    return f"1e{tens:x}{ones:x}"


def parse_color_sample_table(data: bytes) -> ColorSampleTable:
    records: list[ColorSampleRecord] = []
    for record_index, offset in enumerate(range(0, len(data), COLSMPL_RECORD_SIZE)):
        record = data[offset : offset + COLSMPL_RECORD_SIZE]
        if not any(record):
            continue
        munsell_raw = _trim_null(record[:20])
        label_raw = _trim_null(record[20:148])
        munsell = decode_munsell_notation(munsell_raw)
        label = decode_color_sample_label(label_raw)
        sample_number = record_index + 1
        records.append(
            ColorSampleRecord(
                record_index=record_index,
                sample_number=sample_number,
                sample_key=_sample_key_for_number(sample_number),
                munsell_raw=munsell_raw,
                munsell=munsell,
                label_raw=label_raw,
                label=label,
                estimated_rgb=munsell_to_estimated_rgb(munsell),
            )
        )
    return ColorSampleTable(records=tuple(records))


def _color_sample_element(elements: list[SsedInfoElement]) -> SsedInfoElement | None:
    return next(
        (
            element
            for element in elements
            if element.type == COLSMPL_TYPE or element.filename.upper() == "COLSMPL.DIC"
        ),
        None,
    )


def find_color_sample_path(idx: Path) -> Path | None:
    _title, elements = parse_ssedinfo(idx)
    element = _color_sample_element(elements)
    if element is None or not element.start:
        return None
    return find_case_insensitive(idx.parent, element.filename)


def load_color_sample_table(idx: Path) -> ColorSampleTable | None:
    path = find_color_sample_path(idx)
    if path is None:
        return None
    return parse_color_sample_table(expand_sseddata_file(path))


def load_color_sample_map(idx: Path) -> dict[str, dict[str, Any]]:
    table = load_color_sample_table(idx)
    if table is None:
        return {}
    return {key: record.as_dict() for key, record in table.by_sample_key.items()}
