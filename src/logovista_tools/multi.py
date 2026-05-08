"""Parse MULTI*.DIC selector descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .indexes import decode_index_key


@dataclass(frozen=True)
class MultiComponentRef:
    component_type: int
    subtype: int
    start_block: int
    block_count: int
    flags: bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_type": f"{self.component_type:02x}",
            "subtype": f"{self.subtype:02x}",
            "start_block": self.start_block,
            "block_count": self.block_count,
            "flags": self.flags.hex(),
        }


@dataclass(frozen=True)
class MultiRecord:
    index: int
    offset: int
    component_count: int
    subtype: int
    label: str
    label_raw: bytes
    refs: tuple[MultiComponentRef, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "offset": self.offset,
            "component_count": self.component_count,
            "subtype": f"{self.subtype:02x}",
            "label": self.label,
            "label_raw": self.label_raw.hex(),
            "refs": [ref.as_dict() for ref in self.refs],
        }


@dataclass(frozen=True)
class MultiDescriptor:
    record_count: int
    reserved: bytes
    records: tuple[MultiRecord, ...]
    trailing_nonzero_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_count": self.record_count,
            "reserved": self.reserved.hex(),
            "records": [record.as_dict() for record in self.records],
            "trailing_nonzero_bytes": self.trailing_nonzero_bytes,
        }


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def parse_multi_descriptor(
    data: bytes,
    *,
    gaiji: str = "h-placeholder",
    gaiji_map: dict[str, str] | None = None,
) -> MultiDescriptor:
    gaiji_map = gaiji_map or {}
    if len(data) < 0x10:
        raise ValueError("MULTI descriptor is shorter than its 16-byte header")

    record_count = be16(data, 0)
    reserved = data[2:0x10]
    records: list[MultiRecord] = []
    pos = 0x10

    for index in range(1, record_count + 1):
        if pos + 0x20 > len(data):
            raise ValueError(f"MULTI record {index} header is truncated at offset {pos}")
        record_offset = pos
        component_count = data[pos]
        subtype = data[pos + 1]
        label_raw = data[pos + 2 : pos + 0x20]
        label = decode_index_key(label_raw, gaiji=gaiji, gaiji_map=gaiji_map)
        pos += 0x20

        refs: list[MultiComponentRef] = []
        for ref_index in range(component_count):
            if pos + 0x10 > len(data):
                raise ValueError(
                    f"MULTI record {index} component reference {ref_index + 1} "
                    f"is truncated at offset {pos}"
                )
            ref = data[pos : pos + 0x10]
            refs.append(
                MultiComponentRef(
                    component_type=ref[0],
                    subtype=ref[1],
                    start_block=be32(ref, 2),
                    block_count=be32(ref, 6),
                    flags=bytes(ref[10:16]),
                )
            )
            pos += 0x10

        records.append(
            MultiRecord(
                index=index,
                offset=record_offset,
                component_count=component_count,
                subtype=subtype,
                label=label,
                label_raw=bytes(label_raw),
                refs=tuple(refs),
            )
        )

    trailing_nonzero = sum(1 for byte in data[pos:] if byte)
    return MultiDescriptor(
        record_count=record_count,
        reserved=bytes(reserved),
        records=tuple(records),
        trailing_nonzero_bytes=trailing_nonzero,
    )
