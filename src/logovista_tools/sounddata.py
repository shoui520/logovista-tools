"""Loose Sound/SoundData audio store support.

MWALEARN uses a package-local ``Sound/`` directory instead of ``PCMDATA.DIC``:

* ``SoundData`` is a concatenation of self-contained RIFF/WAVE files.
* ``WaveFile.map`` maps decimal sound IDs to byte offsets and lengths.
* HONMON wraps an eight-digit hexadecimal sound ID between nonliteral gaiji
  markers A427 and A428.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .cli_ux import status
from .entries import DictionarySource
from .pcmdata import RiffChunk, parse_riff_chunks
from .ssed import SsedRandomReader, resolve_case_insensitive_path


SOUNDDATA_START_MARKER = b"\xa4\x27"
SOUNDDATA_END_MARKER = b"\xa4\x28"
WAVEFILE_MAP_RE = re.compile(r"^([0-9A-Fa-f]{1,16}):([0-9A-Fa-f]+)\s+(-?[0-9]+)$")


@dataclass(frozen=True)
class SoundMapRecord:
    """One row from ``Sound/WaveFile.map``."""

    sound_id: int
    offset: int
    length: int
    line_index: int
    raw_line: str
    raw_sound_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.sound_id:08x}"

    def as_dict(self) -> dict[str, Any]:
        row = {
            "sound_id": self.sound_id,
            "sound_id_hex": f"{self.sound_id:08x}",
            "offset": self.offset,
            "length": self.length,
            "line_index": self.line_index,
        }
        if self.raw_sound_id is not None and self.raw_sound_id != self.sound_id:
            row["raw_sound_id"] = self.raw_sound_id
        return row


@dataclass(frozen=True)
class SoundDataIndex:
    """Resolved SoundData store and WaveFile.map rows."""

    sounddata_path: Path
    map_path: Path
    rows: tuple[SoundMapRecord, ...]
    records: dict[int, SoundMapRecord]

    @property
    def expanded_size(self) -> int:
        return self.sounddata_path.stat().st_size

    def record(self, sound_id: int) -> SoundMapRecord | None:
        return self.records.get(sound_id)


@dataclass(frozen=True)
class SoundDataReference:
    """One HONMON A427/A428 sound marker."""

    position: int
    sound_id: int
    code_hex: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "honmon_position": self.position,
            "sound_id": self.sound_id,
            "sound_id_hex": self.code_hex,
        }


@dataclass(frozen=True)
class SoundDataRecord:
    """Decoded SoundData RIFF/WAVE record summary."""

    relative_offset: int
    record_size: int
    content_size: int
    media_type: str
    codec: str
    extension: str
    chunk_tags: tuple[str, ...] = ()
    format_tag: int | None = None
    channels: int | None = None
    sample_rate: int | None = None
    byte_rate: int | None = None
    block_align: int | None = None
    bits_per_sample: int | None = None
    data_size: int | None = None
    data_offset: int | None = None
    info_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_offset": self.relative_offset,
            "record_size": self.record_size,
            "content_size": self.content_size,
            "media_type": self.media_type,
            "codec": self.codec,
            "extension": self.extension,
            "chunk_tags": list(self.chunk_tags),
            "format_tag": self.format_tag,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "byte_rate": self.byte_rate,
            "block_align": self.block_align,
            "bits_per_sample": self.bits_per_sample,
            "data_size": self.data_size,
            "data_offset": self.data_offset,
            "info_name": self.info_name,
        }


def _decode_wave_format(fmt_payload: bytes) -> tuple[int, int, int, int, int, int] | None:
    if len(fmt_payload) < 16:
        return None
    format_tag = int.from_bytes(fmt_payload[0:2], "little")
    channels = int.from_bytes(fmt_payload[2:4], "little")
    sample_rate = int.from_bytes(fmt_payload[4:8], "little")
    byte_rate = int.from_bytes(fmt_payload[8:12], "little")
    block_align = int.from_bytes(fmt_payload[12:14], "little")
    bits_per_sample = int.from_bytes(fmt_payload[14:16], "little")
    if channels <= 0 or sample_rate <= 0 or byte_rate <= 0 or block_align <= 0:
        return None
    return format_tag, channels, sample_rate, byte_rate, block_align, bits_per_sample


def _riff_info_name(chunks_data: bytes, chunks: Iterable[RiffChunk]) -> str | None:
    for chunk in chunks:
        if chunk.tag != "LIST" or chunk.size < 12:
            continue
        payload = chunks_data[chunk.payload_offset : chunk.end_offset]
        if not payload.startswith(b"INFO"):
            continue
        pos = 4
        while pos + 8 <= len(payload):
            tag = payload[pos : pos + 4]
            size = int.from_bytes(payload[pos + 4 : pos + 8], "little")
            start = pos + 8
            end = start + size
            if end > len(payload):
                break
            if tag == b"INAM":
                return payload[start:end].rstrip(b"\x00").decode("cp932", errors="replace")
            pos = end + (size & 1)
    return None


def parse_sounddata_record(raw: bytes, relative_offset: int = 0) -> tuple[SoundDataRecord, list[RiffChunk]] | None:
    """Parse one self-contained SoundData RIFF/WAVE record."""

    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    riff_size = int.from_bytes(raw[4:8], "little")
    content_size = min(len(raw), riff_size + 8)
    chunks_data = raw[12:content_size]
    parsed = parse_riff_chunks(chunks_data)
    if parsed is None:
        return None
    chunks, parsed_size = parsed
    by_tag = {chunk.tag: chunk for chunk in chunks}
    fmt = by_tag.get("fmt ")
    data_chunk = by_tag.get("data")
    if fmt is None or data_chunk is None:
        return None
    fmt_payload = chunks_data[fmt.payload_offset : fmt.end_offset]
    decoded_format = _decode_wave_format(fmt_payload)
    if decoded_format is None:
        return None
    format_tag, channels, sample_rate, byte_rate, block_align, bits_per_sample = decoded_format
    codec = "pcm" if format_tag == 0x0001 else f"wave_format_{format_tag:04x}"
    if format_tag == 0x0055:
        codec = "mpeg_layer3_wave"
    return (
        SoundDataRecord(
            relative_offset=relative_offset,
            record_size=len(raw),
            content_size=content_size,
            media_type="riff_wave",
            codec=codec,
            extension="wav",
            chunk_tags=tuple(chunk.tag for chunk in chunks),
            format_tag=format_tag,
            channels=channels,
            sample_rate=sample_rate,
            byte_rate=byte_rate,
            block_align=block_align,
            bits_per_sample=bits_per_sample,
            data_size=data_chunk.size,
            data_offset=12 + data_chunk.payload_offset,
            info_name=_riff_info_name(chunks_data, chunks),
        ),
        chunks,
    )


def portable_sounddata_audio_bytes(record: SoundDataRecord, raw: bytes) -> bytes:
    """Return a browser-playable SoundData payload.

    SoundData records are already self-contained RIFF/WAVE files, so the
    portable form is the original RIFF bytes trimmed to the declared size.
    """

    return raw[: record.content_size]


def find_sounddata_paths(idx: Path) -> tuple[Path, Path] | None:
    sound_dir = resolve_case_insensitive_path(idx.parent, "Sound")
    if sound_dir is None or not sound_dir.is_dir():
        return None
    sounddata = resolve_case_insensitive_path(sound_dir, "SoundData")
    wave_map = resolve_case_insensitive_path(sound_dir, "WaveFile.map")
    if sounddata is None or wave_map is None or not sounddata.is_file() or not wave_map.is_file():
        return None
    return sounddata, wave_map


def parse_wavefile_map(path: Path) -> tuple[SoundMapRecord, ...]:
    records: list[SoundMapRecord] = []
    previous_sound_id: int | None = None
    for line_index, raw_line in enumerate(path.read_text(encoding="ascii", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = WAVEFILE_MAP_RE.match(line)
        if match is None:
            raise ValueError(f"Malformed WaveFile.map row {line_index}: {raw_line!r}")
        raw_sound_id = int(match.group(3), 10)
        sound_id = raw_sound_id
        if raw_sound_id < 0 and previous_sound_id is not None and previous_sound_id >= 0:
            # MWALEARN has four consecutive rows printed as "-3276" after
            # sound ID 32767. HONMON references them as 0x8000..0x8003, so the
            # map field is a truncated/signed display bug rather than the
            # addressable ID.
            sound_id = previous_sound_id + 1
        records.append(
            SoundMapRecord(
                sound_id=sound_id,
                offset=int(match.group(1), 16),
                length=int(match.group(2), 16),
                line_index=line_index,
                raw_line=raw_line,
                raw_sound_id=raw_sound_id,
            )
        )
        previous_sound_id = sound_id
    return tuple(records)


def load_sounddata_index(idx: Path) -> SoundDataIndex | None:
    paths = find_sounddata_paths(idx)
    if paths is None:
        return None
    sounddata_path, map_path = paths
    rows = parse_wavefile_map(map_path)
    records: dict[int, SoundMapRecord] = {}
    for record in rows:
        records.setdefault(record.sound_id, record)
    return SoundDataIndex(sounddata_path=sounddata_path, map_path=map_path, rows=rows, records=records)


def read_sounddata_record(index: SoundDataIndex, sound_id: int) -> tuple[SoundMapRecord, bytes, SoundDataRecord, list[RiffChunk]] | None:
    record = index.record(sound_id)
    if record is None:
        return None
    with index.sounddata_path.open("rb") as handle:
        handle.seek(record.offset)
        raw = handle.read(record.length)
    parsed = parse_sounddata_record(raw, record.offset)
    if parsed is None:
        return None
    summary, chunks = parsed
    return record, raw, summary, chunks


def _decode_marker_hex(data: bytes, start: int) -> tuple[str, int] | None:
    chars: list[str] = []
    pos = start
    for _ in range(8):
        if pos + 1 >= len(data) or data[pos] != 0x23:
            return None
        value = data[pos + 1]
        char = chr(value)
        if char not in "0123456789abcdefABCDEF":
            return None
        chars.append(char.upper())
        pos += 2
    return "".join(chars), pos


def parse_sounddata_marker_at(data: bytes, position: int) -> tuple[SoundDataReference, int] | None:
    if data[position : position + 2] != SOUNDDATA_START_MARKER:
        return None
    decoded = _decode_marker_hex(data, position + 2)
    if decoded is None:
        return None
    code_hex, end = decoded
    if data[end : end + 2] != SOUNDDATA_END_MARKER:
        return None
    return SoundDataReference(position=position, sound_id=int(code_hex, 16), code_hex=code_hex), end + 2


def iter_sounddata_references(data: bytes) -> Iterable[SoundDataReference]:
    position = 0
    while True:
        position = data.find(SOUNDDATA_START_MARKER, position)
        if position < 0:
            break
        parsed = parse_sounddata_marker_at(data, position)
        if parsed is None:
            position += 1
            continue
        reference, end = parsed
        yield reference
        position = end


def sounddata_references_for_limit(honmon_path: Path, limit: int | None) -> tuple[list[SoundDataReference], int, int, bool]:
    reader = SsedRandomReader(honmon_path)
    if not limit:
        data = reader.read(0, reader.expanded_size)
        return list(iter_sounddata_references(data)), reader.expanded_size, reader.expanded_size, True

    size = min(reader.expanded_size, 512 * 1024)
    while True:
        data = reader.read(0, size)
        references = list(iter_sounddata_references(data))
        complete = size >= reader.expanded_size
        if len(references) >= limit or complete:
            return references[:limit], reader.expanded_size, size, complete
        size = min(reader.expanded_size, size + 512 * 1024)


def sounddata_target(sound_id: int) -> dict[str, Any]:
    return {
        "component": "SoundData",
        "kind": "wavefile_map",
        "sound_id": sound_id,
        "sound_id_hex": f"{sound_id:08x}",
        "resource_id": f"sounddata:{sound_id}",
    }


def _sound_id_stem(sound_id: int) -> str:
    return f"{sound_id:08x}" if sound_id >= 0 else f"neg{abs(sound_id):08x}"


def _manifest_record(
    *,
    index: int,
    source: str,
    reference: SoundDataReference | None,
    map_record: SoundMapRecord,
    record: SoundDataRecord,
    chunks: list[RiffChunk],
    file: str | None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "index": index,
        "source": source,
        "valid": True,
        "reference": reference.as_dict() if reference is not None else None,
        **map_record.as_dict(),
        **record.as_dict(),
        "chunks": [chunk.as_dict() for chunk in chunks],
    }
    if file is not None:
        item["file"] = file
    return item


def extract_sounddata_for_source(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    index = load_sounddata_index(source.idx)
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    manifest_path = dict_out / "pcmdata_manifest.jsonl"
    status(args, f"pcmdata: {source.dict_id}: scanning HONMON refs and SoundData map", verbose=True)
    if index is None:
        manifest_path.write_text("", encoding="utf-8")
        summary = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "container_kind": None,
            "pcmdata": None,
            "sounddata": None,
            "audio_references": 0,
            "valid_referenced_records": 0,
            "unreferenced_records": 0,
            "warnings": ["No PCMDATA.DIC component or Sound/SoundData store found."],
            "manifest_path": str(manifest_path),
        }
        (dict_out / "pcmdata_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    references, honmon_expanded_bytes, honmon_bytes_scanned, honmon_scan_complete = sounddata_references_for_limit(
        source.honmon,
        args.limit,
    )
    media_dir = dict_out / "audio"
    if args.write_audio:
        media_dir.mkdir(parents=True, exist_ok=True)

    referenced_ids = [reference.sound_id for reference in references]
    referenced_id_set = set(referenced_ids)
    duplicate_references = len(referenced_ids) - len(referenced_id_set)
    invalid_referenced_records = 0
    valid_referenced_records = 0
    unreferenced_records = 0
    total_payload_bytes = 0
    codec_counts: Counter[str] = Counter()
    media_type_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    format_counts: Counter[str] = Counter()

    with manifest_path.open("w", encoding="utf-8") as out:
        row_index = 0
        for reference in references:
            row_index += 1
            decoded = read_sounddata_record(index, reference.sound_id)
            if decoded is None:
                invalid_referenced_records += 1
                item = {
                    "index": row_index,
                    "source": "honmon",
                    "valid": False,
                    "reason": "missing_or_unparsed_sounddata_record",
                    "reference": reference.as_dict(),
                }
            else:
                map_record, raw, record, chunks = decoded
                valid_referenced_records += 1
                total_payload_bytes += record.content_size
                codec_counts[record.codec] += 1
                media_type_counts[record.media_type] += 1
                extension_counts[record.extension] += 1
                if record.format_tag is not None:
                    format_counts[f"0x{record.format_tag:04x}"] += 1
                file = None
                if args.write_audio:
                    name = f"{row_index:05d}_{reference.sound_id:08x}.{record.extension}"
                    (media_dir / name).write_bytes(portable_sounddata_audio_bytes(record, raw))
                    file = f"audio/{name}"
                item = _manifest_record(
                    index=row_index,
                    source="honmon",
                    reference=reference,
                    map_record=map_record,
                    record=record,
                    chunks=chunks,
                    file=file,
                )
            out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            out.write("\n")

        if args.include_unreferenced and not args.limit:
            seen_unreferenced_rows: set[int] = set()
            for map_record in index.rows:
                if map_record.line_index in seen_unreferenced_rows or map_record.sound_id in referenced_id_set:
                    continue
                seen_unreferenced_rows.add(map_record.line_index)
                sound_id = map_record.sound_id
                with index.sounddata_path.open("rb") as handle:
                    handle.seek(map_record.offset)
                    raw = handle.read(map_record.length)
                parsed = parse_sounddata_record(raw, map_record.offset)
                if parsed is None:
                    continue
                record, chunks = parsed
                row_index += 1
                unreferenced_records += 1
                total_payload_bytes += record.content_size
                codec_counts[record.codec] += 1
                media_type_counts[record.media_type] += 1
                extension_counts[record.extension] += 1
                if record.format_tag is not None:
                    format_counts[f"0x{record.format_tag:04x}"] += 1
                file = None
                if args.write_audio:
                    name = f"{row_index:05d}_unreferenced_{_sound_id_stem(sound_id)}.{record.extension}"
                    (media_dir / name).write_bytes(portable_sounddata_audio_bytes(record, raw))
                    file = f"audio/{name}"
                item = _manifest_record(
                    index=row_index,
                    source="unreferenced",
                    reference=None,
                    map_record=map_record,
                    record=record,
                    chunks=chunks,
                    file=file,
                )
                out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")

    summary = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "container_kind": "sounddata",
        "pcmdata": None,
        "sounddata": str(index.sounddata_path),
        "wavefile_map": str(index.map_path),
        "sounddata_bytes": index.expanded_size,
        "wavefile_map_records": len(index.rows),
        "wavefile_map_unique_ids": len(index.records),
        "honmon_expanded_bytes": honmon_expanded_bytes,
        "honmon_bytes_scanned": honmon_bytes_scanned,
        "honmon_scan_complete": honmon_scan_complete,
        "audio_references": len(references),
        "unique_referenced_records": len(referenced_id_set),
        "duplicate_references": duplicate_references,
        "valid_referenced_records": valid_referenced_records,
        "invalid_referenced_records": invalid_referenced_records,
        "unreferenced_records": unreferenced_records,
        "total_content_bytes": total_payload_bytes,
        "media_type_counts": dict(sorted(media_type_counts.items())),
        "codec_counts": dict(sorted(codec_counts.items())),
        "extension_counts": dict(sorted(extension_counts.items())),
        "format_tag_counts": dict(sorted(format_counts.items())),
        "audio_files_written": valid_referenced_records + unreferenced_records if args.write_audio else 0,
        "manifest_path": str(manifest_path),
        "warnings": [],
    }
    (dict_out / "pcmdata_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
