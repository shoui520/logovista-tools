"""PCMDATA.DIC media extraction."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .colscr import decode_bcd_decimal
from .entries import decode_tokens, discover_dictionaries, tokens_to_text
from .ssed import BLOCK_SIZE, SsedRandomReader, find_case_insensitive, parse_ssedinfo


PCMDATA_TYPE = 0xD8
PCM_CONTROL = b"\x1f\x4a"
PCM_END_CONTROL = b"\x1f\x6a"
PCMDATA_DIRECTORY_BYTES = 2048
RECORD_TRAILER_BYTES = 12


@dataclass(frozen=True)
class PcmPointer:
    payload: bytes
    kind: int
    flags: int
    start_block: int
    start_offset: int
    end_block: int
    end_offset: int

    @property
    def key(self) -> str:
        return f"{self.start_block:08d}_{self.start_offset:04d}"


@dataclass(frozen=True)
class PcmReference:
    position: int
    label: str
    pointer: PcmPointer


@dataclass(frozen=True)
class RiffChunk:
    tag: str
    size: int
    offset: int
    payload_offset: int
    end_offset: int
    padded_end_offset: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "size": self.size,
            "offset": self.offset,
            "payload_offset": self.payload_offset,
            "end_offset": self.end_offset,
            "padded_end_offset": self.padded_end_offset,
        }


@dataclass(frozen=True)
class PcmRecord:
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
    trailing_zero_bytes: int = 0

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
            "trailing_zero_bytes": self.trailing_zero_bytes,
        }


def parse_pcm_pointer(payload: bytes) -> PcmPointer | None:
    """Parse the 16-byte payload from a 1f4a media/link control.

    For observed PCMDATA dictionaries, bytes 4..15 are packed BCD decimal:
    start block, start offset, end block, end offset.
    """

    if len(payload) != 16:
        return None
    start_block = decode_bcd_decimal(payload[4:8])
    start_offset = decode_bcd_decimal(payload[8:10])
    end_block = decode_bcd_decimal(payload[10:14])
    end_offset = decode_bcd_decimal(payload[14:16])
    if None in (start_block, start_offset, end_block, end_offset):
        return None
    return PcmPointer(
        payload=payload,
        kind=int.from_bytes(payload[0:2], "big"),
        flags=int.from_bytes(payload[2:4], "big"),
        start_block=int(start_block),
        start_offset=int(start_offset),
        end_block=int(end_block),
        end_offset=int(end_offset),
    )


def pointer_relative_range(pointer: PcmPointer, reader: SsedRandomReader) -> tuple[int, int, int]:
    start = (pointer.start_block - reader.start_block) * BLOCK_SIZE + pointer.start_offset
    end = (pointer.end_block - reader.start_block) * BLOCK_SIZE + pointer.end_offset
    return start, end, end - start + 1


def iter_pcm_references(data: bytes, gaiji_map: dict[str, str] | None = None) -> Iterable[PcmReference]:
    position = 0
    while True:
        position = data.find(PCM_CONTROL, position)
        if position < 0:
            break
        payload = data[position + 2 : position + 18]
        pointer = parse_pcm_pointer(payload)
        if pointer is not None:
            label = ""
            end = data.find(PCM_END_CONTROL, position + 18, position + 18 + 256)
            if end >= 0:
                tokens, _stats = decode_tokens(
                    data[position + 18 : end],
                    gaiji="h-placeholder",
                    gaiji_map=gaiji_map,
                )
                label = tokens_to_text(tokens)
            yield PcmReference(position=position, label=label, pointer=pointer)
        position += 1


def is_ascii_chunk_tag(tag: bytes) -> bool:
    return len(tag) == 4 and all(32 <= byte <= 126 for byte in tag)


def parse_riff_chunks(data: bytes) -> tuple[list[RiffChunk], int] | None:
    pos = 0
    chunks: list[RiffChunk] = []
    while pos + 8 <= len(data):
        tag = data[pos : pos + 4]
        if not is_ascii_chunk_tag(tag):
            break
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        payload_offset = pos + 8
        end = payload_offset + size
        padded_end = end + (size & 1)
        if end > len(data):
            return None
        chunks.append(
            RiffChunk(
                tag=tag.decode("ascii", errors="replace"),
                size=size,
                offset=pos,
                payload_offset=payload_offset,
                end_offset=end,
                padded_end_offset=padded_end,
            )
        )
        pos = padded_end
        if tag == b"data":
            break
    if not chunks:
        return None
    return chunks, pos


def mp3_frame_sync(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def trailing_zero_count(data: bytes, start: int = 0) -> int:
    count = 0
    pos = len(data) - 1
    while pos >= start and data[pos] == 0:
        count += 1
        pos -= 1
    return count


def parse_pcmdata_record(data: bytes, relative_offset: int = 0) -> tuple[PcmRecord, list[RiffChunk]] | None:
    if data.startswith(b"fmt "):
        parsed = parse_riff_chunks(data)
        if parsed is None:
            return None
        chunks, content_size = parsed
        by_tag = {chunk.tag: chunk for chunk in chunks}
        fmt = by_tag.get("fmt ")
        data_chunk = by_tag.get("data")
        if fmt is None or data_chunk is None or fmt.size < 16:
            return None
        fmt_payload = data[fmt.payload_offset : fmt.end_offset]
        format_tag = int.from_bytes(fmt_payload[0:2], "little")
        channels = int.from_bytes(fmt_payload[2:4], "little")
        sample_rate = int.from_bytes(fmt_payload[4:8], "little")
        byte_rate = int.from_bytes(fmt_payload[8:12], "little")
        block_align = int.from_bytes(fmt_payload[12:14], "little")
        bits_per_sample = int.from_bytes(fmt_payload[14:16], "little")
        trailer = trailing_zero_count(data, content_size)
        if format_tag == 0x0001:
            codec = "pcm"
            extension = "wav"
        elif format_tag == 0x0055:
            codec = "mpeg_layer3_wave"
            extension = "mp3"
        else:
            codec = f"wave_format_{format_tag:04x}"
            extension = "wav"
        return (
            PcmRecord(
                relative_offset=relative_offset,
                record_size=len(data),
                content_size=content_size,
                media_type="wave_chunks",
                codec=codec,
                extension=extension,
                chunk_tags=tuple(chunk.tag for chunk in chunks),
                format_tag=format_tag,
                channels=channels,
                sample_rate=sample_rate,
                byte_rate=byte_rate,
                block_align=block_align,
                bits_per_sample=bits_per_sample,
                data_size=data_chunk.size,
                data_offset=data_chunk.payload_offset,
                trailing_zero_bytes=trailer,
            ),
            chunks,
        )

    if data.startswith(b"ID3") or mp3_frame_sync(data):
        trailer = trailing_zero_count(data)
        content_size = len(data) - trailer
        return (
            PcmRecord(
                relative_offset=relative_offset,
                record_size=len(data),
                content_size=content_size,
                media_type="mp3",
                codec="mp3",
                extension="mp3",
                trailing_zero_bytes=trailer,
            ),
            [],
        )

    return None


def make_riff_wave(chunks: bytes) -> bytes:
    size = 4 + len(chunks)
    return b"RIFF" + size.to_bytes(4, "little") + b"WAVE" + chunks


def portable_audio_bytes(record: PcmRecord, raw: bytes) -> bytes:
    if record.media_type == "wave_chunks":
        if record.codec == "mpeg_layer3_wave" and record.data_offset is not None and record.data_size is not None:
            return raw[record.data_offset : record.data_offset + record.data_size]
        return make_riff_wave(raw[: record.content_size])
    return raw[: record.content_size]


def find_pcmdata_path(idx: Path) -> Path | None:
    _title, elements = parse_ssedinfo(idx)
    element = next(
        (item for item in elements if item.type == PCMDATA_TYPE or item.filename.upper() == "PCMDATA.DIC"),
        None,
    )
    if element is None:
        return None
    return find_case_insensitive(idx.parent, element.filename)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def block_offset_for_relative(reader: SsedRandomReader, relative_offset: int) -> tuple[int, int]:
    return reader.start_block + relative_offset // BLOCK_SIZE, relative_offset % BLOCK_SIZE


def manifest_record(
    *,
    index: int,
    source: str,
    reference: PcmReference | None,
    record: PcmRecord,
    chunks: list[RiffChunk],
    file: str | None = None,
) -> dict[str, Any]:
    block = None
    offset = None
    payload = None
    kind = None
    flags = None
    label = ""
    honmon_position = None
    if reference is not None:
        pointer = reference.pointer
        block = pointer.start_block
        offset = pointer.start_offset
        payload = pointer.payload.hex()
        kind = pointer.kind
        flags = pointer.flags
        label = reference.label
        honmon_position = reference.position
    item = {
        "index": index,
        "source": source,
        "valid": True,
        "honmon_position": honmon_position,
        "label": label,
        "payload": payload,
        "kind": kind,
        "flags": flags,
        "block": block,
        "offset": offset,
        **record.as_dict(),
        "chunks": [chunk.as_dict() for chunk in chunks],
    }
    if file is not None:
        item["file"] = file
    return item


def referenced_intervals(
    references: list[PcmReference],
    reader: SsedRandomReader,
) -> tuple[list[tuple[int, int]], int]:
    intervals = []
    invalid = 0
    for reference in references:
        start, end, size = pointer_relative_range(reference.pointer, reader)
        if start < 0 or end >= reader.expanded_size or size <= 0:
            invalid += 1
            continue
        intervals.append((start, end))
    return sorted(set(intervals)), invalid


def unreferenced_gaps(intervals: list[tuple[int, int]], expanded_size: int) -> list[tuple[int, int]]:
    gaps = []
    previous = 0
    for start, end in sorted(intervals):
        if start > previous:
            gaps.append((previous, start - 1))
        previous = max(previous, end + 1)
    if previous < expanded_size:
        gaps.append((previous, expanded_size - 1))
    return gaps


def iter_gap_records(reader: SsedRandomReader, gaps: list[tuple[int, int]]) -> Iterable[tuple[int, bytes, PcmRecord, list[RiffChunk]]]:
    for start, end in gaps:
        if end < PCMDATA_DIRECTORY_BYTES:
            continue
        size = end - start + 1
        if size <= 0:
            continue
        data = reader.read(start, size)
        if not any(data):
            continue
        pos = 0
        while pos < len(data):
            while pos < len(data) and data[pos] == 0:
                pos += 1
            if pos >= len(data):
                break
            current = data[pos:]
            if current.startswith(b"fmt "):
                parsed = parse_riff_chunks(current)
                if parsed is None:
                    break
                chunks, content_size = parsed
                trailer = 0
                trailer_bytes = current[content_size : content_size + RECORD_TRAILER_BYTES]
                if len(trailer_bytes) == RECORD_TRAILER_BYTES and not any(trailer_bytes):
                    trailer = RECORD_TRAILER_BYTES
                record_size = content_size + trailer
                parsed_record = parse_pcmdata_record(current[:record_size], start + pos)
                if parsed_record is None:
                    break
                record, parsed_chunks = parsed_record
                yield start + pos, current[:record_size], record, parsed_chunks
                pos += record_size
                continue
            if current.startswith(b"ID3") or mp3_frame_sync(current):
                next_positions = [
                    found
                    for signature in (b"ID3\x03", b"fmt ")
                    if (found := current.find(signature, 1)) > 0
                ]
                record_size = min(next_positions) if next_positions else len(current)
                parsed_record = parse_pcmdata_record(current[:record_size], start + pos)
                if parsed_record is None:
                    break
                record, parsed_chunks = parsed_record
                yield start + pos, current[:record_size], record, parsed_chunks
                pos += record_size
                continue
            break


def extract_pcmdata_for_source(source: Any, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    pcmdata_path = find_pcmdata_path(source.idx)
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    manifest_path = dict_out / "pcmdata_manifest.jsonl"

    if pcmdata_path is None:
        manifest_path.write_text("", encoding="utf-8")
        summary = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "pcmdata": None,
            "audio_references": 0,
            "valid_referenced_records": 0,
            "unreferenced_records": 0,
            "warnings": ["No PCMDATA.DIC component found."],
            "manifest_path": str(manifest_path),
        }
        write_json(dict_out / "pcmdata_summary.json", summary)
        return summary

    honmon_data = source.honmon.read_bytes()
    if honmon_data[:8] == b"SSEDDATA":
        from .ssed import expand_sseddata_bytes

        honmon_data = expand_sseddata_bytes(honmon_data)
    references = list(iter_pcm_references(honmon_data, source.gaiji_map))
    if args.limit:
        references = references[: args.limit]

    reader = SsedRandomReader(pcmdata_path)
    intervals, range_invalid = referenced_intervals(references, reader)
    gaps = unreferenced_gaps(intervals, reader.expanded_size)

    media_dir = dict_out / "audio"
    if args.write_audio:
        media_dir.mkdir(parents=True, exist_ok=True)

    valid_referenced_records = 0
    invalid_referenced_records = range_invalid
    unreferenced_records = 0
    duplicate_references = len(references) - len({reference.pointer.payload for reference in references})
    total_payload_bytes = 0
    codec_counts: Counter[str] = Counter()
    media_type_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    format_counts: Counter[str] = Counter()
    kind_flag_counts: Counter[str] = Counter()

    with manifest_path.open("w", encoding="utf-8") as out:
        row_index = 0
        for reference in references:
            row_index += 1
            start, _end, size = pointer_relative_range(reference.pointer, reader)
            if start < 0 or start + size > reader.expanded_size or size <= 0:
                item = {
                    "index": row_index,
                    "source": "honmon",
                    "valid": False,
                    "reason": "out_of_range",
                    "honmon_position": reference.position,
                    "label": reference.label,
                    "payload": reference.pointer.payload.hex(),
                    "kind": reference.pointer.kind,
                    "flags": reference.pointer.flags,
                    "block": reference.pointer.start_block,
                    "offset": reference.pointer.start_offset,
                    "relative_offset": start,
                    "record_size": size,
                }
                out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")
                continue
            raw = reader.read(start, size)
            parsed = parse_pcmdata_record(raw, start)
            if parsed is None:
                invalid_referenced_records += 1
                item = {
                    "index": row_index,
                    "source": "honmon",
                    "valid": False,
                    "honmon_position": reference.position,
                    "label": reference.label,
                    "payload": reference.pointer.payload.hex(),
                    "kind": reference.pointer.kind,
                    "flags": reference.pointer.flags,
                    "block": reference.pointer.start_block,
                    "offset": reference.pointer.start_offset,
                    "relative_offset": start,
                    "record_size": size,
                }
            else:
                record, chunks = parsed
                valid_referenced_records += 1
                total_payload_bytes += record.content_size
                codec_counts[record.codec] += 1
                media_type_counts[record.media_type] += 1
                extension_counts[record.extension] += 1
                if record.format_tag is not None:
                    format_counts[f"0x{record.format_tag:04x}"] += 1
                kind_flag_counts[f"0x{reference.pointer.kind:04x}:0x{reference.pointer.flags:04x}"] += 1
                file = None
                if args.write_audio:
                    name = f"{row_index:05d}_{reference.pointer.key}.{record.extension}"
                    (media_dir / name).write_bytes(portable_audio_bytes(record, raw))
                    file = f"audio/{name}"
                item = manifest_record(
                    index=row_index,
                    source="honmon",
                    reference=reference,
                    record=record,
                    chunks=chunks,
                    file=file,
                )
            out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            out.write("\n")

        if args.include_unreferenced and not args.limit:
            for start, raw, record, chunks in iter_gap_records(reader, gaps):
                if start < PCMDATA_DIRECTORY_BYTES:
                    continue
                row_index += 1
                block, offset = block_offset_for_relative(reader, start)
                unreferenced_records += 1
                total_payload_bytes += record.content_size
                codec_counts[record.codec] += 1
                media_type_counts[record.media_type] += 1
                extension_counts[record.extension] += 1
                if record.format_tag is not None:
                    format_counts[f"0x{record.format_tag:04x}"] += 1
                file = None
                if args.write_audio:
                    name = f"{row_index:05d}_unreferenced_{block:08d}_{offset:04d}.{record.extension}"
                    (media_dir / name).write_bytes(portable_audio_bytes(record, raw))
                    file = f"audio/{name}"
                item = manifest_record(
                    index=row_index,
                    source="unreferenced",
                    reference=None,
                    record=record,
                    chunks=chunks,
                    file=file,
                )
                item["block"] = block
                item["offset"] = offset
                out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")

    header_sample = reader.read(0, min(PCMDATA_DIRECTORY_BYTES, 256))
    nonzero_gaps = 0
    for start, end in gaps:
        if end < PCMDATA_DIRECTORY_BYTES:
            continue
        sample = reader.read(start, min(128, end - start + 1))
        if any(sample):
            nonzero_gaps += 1

    summary = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "pcmdata": str(pcmdata_path),
        "pcmdata_start_block": reader.start_block,
        "pcmdata_end_block": reader.end_block,
        "pcmdata_expanded_bytes": reader.expanded_size,
        "directory_header_sample": header_sample.hex(),
        "audio_references": len(references),
        "unique_referenced_records": len({reference.pointer.payload for reference in references}),
        "duplicate_references": duplicate_references,
        "valid_referenced_records": valid_referenced_records,
        "invalid_referenced_records": invalid_referenced_records,
        "unreferenced_records": unreferenced_records,
        "gap_count": len(gaps),
        "nonzero_gap_count": nonzero_gaps,
        "total_content_bytes": total_payload_bytes,
        "media_type_counts": dict(sorted(media_type_counts.items())),
        "codec_counts": dict(sorted(codec_counts.items())),
        "extension_counts": dict(sorted(extension_counts.items())),
        "format_tag_counts": dict(sorted(format_counts.items())),
        "kind_flag_counts": dict(sorted(kind_flag_counts.items())),
        "audio_files_written": valid_referenced_records + unreferenced_records if args.write_audio else 0,
        "manifest_path": str(manifest_path),
        "warnings": [],
    }
    write_json(dict_out / "pcmdata_summary.json", summary)
    return summary


def extract_pcmdata_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(args.root or [Path(".")])
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting PCMDATA {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_pcmdata_for_source(source, args.out_dir, args)
        print(
            f"  refs={summary['audio_references']} valid={summary['valid_referenced_records']} "
            f"unreferenced={summary['unreferenced_records']} bytes={summary.get('pcmdata_expanded_bytes', 0)}",
            file=sys.stderr,
        )
        summaries.append(summary)
    write_json(args.out_dir / "summary.json", summaries)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    parser.add_argument("--out-dir", type=Path, default=Path("pcmdata-out"), help="Output directory.")
    parser.add_argument("--limit", type=int, help="Limit HONMON references per dictionary.")
    parser.add_argument("--write-audio", action="store_true", help="Write portable audio files next to the manifest.")
    parser.add_argument(
        "--no-include-unreferenced",
        dest="include_unreferenced",
        action="store_false",
        help="Do not scan unreferenced records in PCMDATA gaps.",
    )
    parser.set_defaults(include_unreferenced=True)
    args = parser.parse_args()
    extract_pcmdata_for_sources(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
