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
class MultiEmbeddedComponent:
    """A component slice referenced from inside a physical MULTI*.DIC file."""

    record_index: int
    ref_index: int
    component_type: int
    subtype: int
    start_block: int
    block_count: int
    relative_start: int
    relative_end: int
    flags: bytes

    @property
    def byte_count(self) -> int:
        return self.relative_end - self.relative_start

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_index": self.record_index,
            "ref_index": self.ref_index,
            "component_type": f"{self.component_type:02x}",
            "subtype": f"{self.subtype:02x}",
            "start_block": self.start_block,
            "block_count": self.block_count,
            "relative_start": self.relative_start,
            "relative_end": self.relative_end,
            "byte_count": self.byte_count,
            "flags": self.flags.hex(),
        }


@dataclass(frozen=True)
class MultiDescriptor:
    record_count: int
    reserved: bytes
    records: tuple[MultiRecord, ...]
    descriptor_bytes: int
    trailing_nonzero_bytes: int
    embedded_components: tuple[MultiEmbeddedComponent, ...] = ()
    embedded_nonzero_bytes: int = 0
    unreferenced_nonzero_bytes: int = 0
    external_reference_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_count": self.record_count,
            "reserved": self.reserved.hex(),
            "records": [record.as_dict() for record in self.records],
            "descriptor_bytes": self.descriptor_bytes,
            "trailing_nonzero_bytes": self.trailing_nonzero_bytes,
            "embedded_components": [component.as_dict() for component in self.embedded_components],
            "embedded_nonzero_bytes": self.embedded_nonzero_bytes,
            "unreferenced_nonzero_bytes": self.unreferenced_nonzero_bytes,
            "external_reference_count": self.external_reference_count,
        }


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((start, end) for start, end in intervals if end > start):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _nonzero_in_intervals(data: bytes, intervals: list[tuple[int, int]]) -> int:
    total = 0
    for start, end in intervals:
        total += sum(1 for byte in data[start:end] if byte)
    return total


def _unreferenced_nonzero_after_descriptor(data: bytes, descriptor_end: int, intervals: list[tuple[int, int]]) -> int:
    if descriptor_end >= len(data):
        return 0
    merged = _merge_intervals(intervals)
    total = 0
    pos = descriptor_end
    for start, end in merged:
        if end <= descriptor_end:
            continue
        if start > pos:
            total += sum(1 for byte in data[pos:start] if byte)
        pos = max(pos, end)
    if pos < len(data):
        total += sum(1 for byte in data[pos:] if byte)
    return total


def parse_multi_descriptor(
    data: bytes,
    *,
    gaiji: str = "h-placeholder",
    gaiji_map: dict[str, str] | None = None,
    parent_start_block: int | None = None,
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

    descriptor_end = pos
    trailing_nonzero = sum(1 for byte in data[pos:] if byte)
    embedded: list[MultiEmbeddedComponent] = []
    external_reference_count = 0
    intervals: list[tuple[int, int]] = []
    if parent_start_block is not None:
        for record in records:
            for ref_index, ref in enumerate(record.refs, start=1):
                relative_start = (ref.start_block - parent_start_block) * 2048
                relative_end = relative_start + ref.block_count * 2048
                if relative_start < descriptor_end or relative_start >= len(data) or relative_end <= relative_start:
                    external_reference_count += 1
                    continue
                relative_end = min(relative_end, len(data))
                embedded.append(
                    MultiEmbeddedComponent(
                        record_index=record.index,
                        ref_index=ref_index,
                        component_type=ref.component_type,
                        subtype=ref.subtype,
                        start_block=ref.start_block,
                        block_count=ref.block_count,
                        relative_start=relative_start,
                        relative_end=relative_end,
                        flags=ref.flags,
                    )
                )
                intervals.append((relative_start, relative_end))
    intervals = _merge_intervals(intervals)
    return MultiDescriptor(
        record_count=record_count,
        reserved=bytes(reserved),
        records=tuple(records),
        descriptor_bytes=descriptor_end,
        trailing_nonzero_bytes=trailing_nonzero,
        embedded_components=tuple(embedded),
        embedded_nonzero_bytes=_nonzero_in_intervals(data, intervals),
        unreferenced_nonzero_bytes=_unreferenced_nonzero_after_descriptor(data, descriptor_end, intervals),
        external_reference_count=external_reference_count,
    )
