"""CCALTSTR alternate-string table helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .gaiji import gaiji_grid_code_for_index


CCALTSTR_HEADER_SIZE = 16
CCALTSTR_RECORD_SIZE = 62
CCALTSTR_VALUE_SIZE = 60


@dataclass(frozen=True)
class CcAltStrRecord:
    """One custom-character alternate-string row."""

    index: int
    code: int
    value_bytes: bytes
    value: str

    @property
    def code_hex(self) -> str:
        return f"{self.code:04x}"


@dataclass(frozen=True)
class CcAltStrTable:
    """Decoded ``CCALTSTR.HA`` or ``CCALTSTR.FU`` payload."""

    magic: bytes
    version: int
    start_code: int
    record_count: int
    reserved: bytes
    records: tuple[CcAltStrRecord, ...]

    @property
    def kind(self) -> str:
        if self.magic == b"SDICALTH":
            return "half"
        if self.magic == b"SDICALTF":
            return "full"
        return "unknown"

    @property
    def start_code_hex(self) -> str:
        return f"{self.start_code:04x}"


class CcAltStrParseError(ValueError):
    """Raised when a CCALTSTR payload does not match the observed grammar."""


def decode_ccaltstr_value(data: bytes) -> str:
    """Decode one NUL-terminated 60-byte alternate-string field."""

    value = data.split(b"\x00", 1)[0]
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        return value.decode("cp932", errors="replace")


def parse_ccaltstr(data: bytes) -> CcAltStrTable:
    """Parse an observed LogoVista ``CCALTSTR`` alternate-string table."""

    if len(data) < CCALTSTR_HEADER_SIZE:
        raise CcAltStrParseError("CCALTSTR payload is shorter than the 16-byte header")

    magic = data[:8]
    if magic not in {b"SDICALTH", b"SDICALTF"}:
        raise CcAltStrParseError(f"unsupported CCALTSTR magic {magic!r}")

    version = int.from_bytes(data[8:10], "little")
    start_code = int.from_bytes(data[10:12], "big")
    record_count = int.from_bytes(data[12:14], "big")
    reserved = data[14:16]
    expected_size = CCALTSTR_HEADER_SIZE + record_count * CCALTSTR_RECORD_SIZE
    if len(data) != expected_size:
        raise CcAltStrParseError(
            f"CCALTSTR size mismatch: count={record_count} "
            f"expected={expected_size} actual={len(data)}"
        )

    records: list[CcAltStrRecord] = []
    pos = CCALTSTR_HEADER_SIZE
    for index in range(record_count):
        expected_code = gaiji_grid_code_for_index(start_code, index)
        code = int.from_bytes(data[pos : pos + 2], "big")
        if code != expected_code:
            raise CcAltStrParseError(
                f"CCALTSTR code sequence mismatch at record {index}: "
                f"expected={expected_code:04x} actual={code:04x}"
            )
        value_field = data[pos + 2 : pos + 2 + CCALTSTR_VALUE_SIZE]
        value_bytes = value_field.split(b"\x00", 1)[0]
        records.append(
            CcAltStrRecord(
                index=index,
                code=code,
                value_bytes=value_bytes,
                value=decode_ccaltstr_value(value_field),
            )
        )
        pos += CCALTSTR_RECORD_SIZE

    return CcAltStrTable(
        magic=magic,
        version=version,
        start_code=start_code,
        record_count=record_count,
        reserved=reserved,
        records=tuple(records),
    )


def parse_ccaltstr_file(path: Path) -> CcAltStrTable:
    return parse_ccaltstr(path.read_bytes())
