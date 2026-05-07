"""COLSCR.DIC media resource extraction."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import discover_dictionaries
from .parallel import parallel_map_ordered, worker_args
from .ssed import BLOCK_SIZE, SsedRandomReader, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


COLSCR_TYPE = 0xD2
MEDIA_CONTROL = b"\x1f\x4d"


@dataclass(frozen=True)
class MediaPointer:
    payload: bytes
    block: int
    offset: int

    @property
    def key(self) -> str:
        return f"{self.block:08d}_{self.offset:04d}"


@dataclass(frozen=True)
class MediaReference:
    position: int
    section_code: str | None
    pointer: MediaPointer


@dataclass(frozen=True)
class ColscrImageRecord:
    pointer: MediaPointer
    relative_offset: int
    payload_size: int
    media_type: str
    extension: str
    width: int | None = None
    height: int | None = None
    bits_per_pixel: int | None = None
    compression: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": self.pointer.payload.hex(),
            "block": self.pointer.block,
            "offset": self.pointer.offset,
            "relative_offset": self.relative_offset,
            "payload_size": self.payload_size,
            "media_type": self.media_type,
            "extension": self.extension,
            "width": self.width,
            "height": self.height,
            "bits_per_pixel": self.bits_per_pixel,
            "compression": self.compression,
        }


def decode_bcd_decimal(data: bytes) -> int | None:
    value = 0
    for byte in data:
        high = byte >> 4
        low = byte & 0x0F
        if high > 9 or low > 9:
            return None
        value = value * 100 + high * 10 + low
    return value


def parse_media_pointer(payload: bytes) -> MediaPointer | None:
    """Parse the 18-byte payload from a 1f4d media control.

    OUKOKU11 stores the pointed logical block and offset as packed BCD decimal
    in the final six bytes: four bytes for block, two bytes for offset.
    """

    if len(payload) != 18:
        return None
    block = decode_bcd_decimal(payload[12:16])
    offset = decode_bcd_decimal(payload[16:18])
    if block is None or offset is None:
        return None
    return MediaPointer(payload=payload, block=block, offset=offset)


def nearest_section_code(data: bytes, position: int, *, window: int = 96) -> str | None:
    start = max(0, position - window)
    marker = data.rfind(b"\x1f\x09", start, position)
    if marker < 0 or marker + 4 > len(data):
        return None
    return data[marker + 2 : marker + 4].hex()


def iter_media_references(data: bytes) -> Iterable[MediaReference]:
    position = 0
    while True:
        position = data.find(MEDIA_CONTROL, position)
        if position < 0:
            break
        payload = data[position + 2 : position + 20]
        pointer = parse_media_pointer(payload)
        if pointer is not None:
            yield MediaReference(
                position=position,
                section_code=nearest_section_code(data, position),
                pointer=pointer,
            )
        position += 1


def parse_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in {0x01, *range(0xD0, 0xD8), 0xD8, 0xD9}:
            continue
        if pos + 2 > len(data):
            break
        segment_length = int.from_bytes(data[pos : pos + 2], "big")
        if segment_length < 2 or pos + segment_length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_length >= 7:
                height = int.from_bytes(data[pos + 3 : pos + 5], "big")
                width = int.from_bytes(data[pos + 5 : pos + 7], "big")
                return width, height
            break
        pos += segment_length
    return None


def validate_bmp_header(data: bytes) -> tuple[int, int, int, int, int] | None:
    """Return payload size, width, height, bpp, compression for a wrapped BMP header."""

    if len(data) < 70 or data[:4] != b"data" or data[8:10] != b"BM":
        return None
    size = int.from_bytes(data[4:8], "little")
    bmp = data[8:]
    if int.from_bytes(bmp[2:6], "little") != size:
        return None
    pixel_offset = int.from_bytes(bmp[10:14], "little")
    if pixel_offset < 54 or pixel_offset > size:
        return None
    if int.from_bytes(bmp[14:18], "little") < 40:
        return None
    width = int.from_bytes(bmp[18:22], "little", signed=True)
    height = int.from_bytes(bmp[22:26], "little", signed=True)
    planes = int.from_bytes(bmp[26:28], "little")
    bits_per_pixel = int.from_bytes(bmp[28:30], "little")
    compression = int.from_bytes(bmp[30:34], "little")
    if planes != 1:
        return None
    if width <= 0 or height == 0:
        return None
    if bits_per_pixel not in {1, 4, 8, 16, 24, 32}:
        return None
    if compression not in {0, 1, 2, 3}:
        return None
    return size, width, height, bits_per_pixel, compression


def parse_colscr_image_header(
    data: bytes,
) -> tuple[int, str, str, int | None, int | None, int | None, int | None] | None:
    if len(data) < 12 or data[:4] != b"data":
        return None
    payload_size = int.from_bytes(data[4:8], "little")
    image = data[8:]
    if payload_size <= 0:
        return None
    if image.startswith(b"BM"):
        parsed_bmp = validate_bmp_header(data)
        if parsed_bmp is None:
            return None
        size, width, height, bits_per_pixel, compression = parsed_bmp
        return size, "bmp", "bmp", width, height, bits_per_pixel, compression
    if image.startswith(b"\xff\xd8\xff"):
        return payload_size, "jpeg", "jpg", None, None, None, None
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        width = None
        height = None
        if len(image) >= 24 and image[12:16] == b"IHDR":
            width = int.from_bytes(image[16:20], "big")
            height = int.from_bytes(image[20:24], "big")
        return payload_size, "png", "png", width, height, None, None
    return None


def read_colscr_record(reader: SsedRandomReader, pointer: MediaPointer) -> tuple[ColscrImageRecord, bytes] | None:
    relative_offset = (pointer.block - reader.start_block) * BLOCK_SIZE + pointer.offset
    header = reader.read(relative_offset, 70)
    parsed = parse_colscr_image_header(header)
    if parsed is None:
        return None
    payload_size, media_type, extension, width, height, bits_per_pixel, compression = parsed
    wrapped = reader.read(relative_offset, 8 + payload_size)
    if len(wrapped) != 8 + payload_size:
        return None
    image = wrapped[8:]
    if media_type == "jpeg":
        dimensions = parse_jpeg_dimensions(image)
        if dimensions is not None:
            width, height = dimensions
    record = ColscrImageRecord(
        pointer=pointer,
        relative_offset=relative_offset,
        payload_size=payload_size,
        media_type=media_type,
        extension=extension,
        width=width,
        height=height,
        bits_per_pixel=bits_per_pixel,
        compression=compression,
    )
    return record, image


def find_colscr_path(idx: Path) -> Path | None:
    _title, elements = parse_ssedinfo(idx)
    element = next(
        (item for item in elements if item.type == COLSCR_TYPE or item.filename.upper() == "COLSCR.DIC"),
        None,
    )
    if element is None:
        return None
    return find_case_insensitive(idx.parent, element.filename)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_colscr_for_source(source: Any, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    colscr_path = find_colscr_path(source.idx)
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    manifest_path = dict_out / "colscr_manifest.jsonl"

    if colscr_path is None:
        manifest_path.write_text("", encoding="utf-8")
        summary = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "colscr": None,
            "media_references": 0,
            "valid_records": 0,
            "warnings": ["No COLSCR.DIC component found."],
            "manifest_path": str(manifest_path),
        }
        write_json(dict_out / "colscr_summary.json", summary)
        return summary

    honmon_data = expand_sseddata_file(source.honmon)
    references = list(iter_media_references(honmon_data))
    if args.limit:
        references = references[: args.limit]

    reader = SsedRandomReader(colscr_path)
    media_dir = dict_out / "media"
    if args.write_media:
        media_dir.mkdir(parents=True, exist_ok=True)

    valid_records = 0
    invalid_records = 0
    total_payload_bytes = 0
    section_counts: Counter[str] = Counter()
    media_type_counts: Counter[str] = Counter()
    dimension_counts: Counter[tuple[str, int | None, int | None, int | None, int | None]] = Counter()

    with manifest_path.open("w", encoding="utf-8") as out:
        for index, reference in enumerate(references, start=1):
            parsed = read_colscr_record(reader, reference.pointer)
            item: dict[str, Any] = {
                "index": index,
                "honmon_position": reference.position,
                "section_code": reference.section_code,
                "payload": reference.pointer.payload.hex(),
                "block": reference.pointer.block,
                "offset": reference.pointer.offset,
            }
            if parsed is None:
                invalid_records += 1
                item["valid"] = False
            else:
                record, image = parsed
                valid_records += 1
                total_payload_bytes += record.payload_size
                if reference.section_code:
                    section_counts[reference.section_code] += 1
                media_type_counts[record.media_type] += 1
                dimension_counts[
                    (record.media_type, record.width, record.height, record.bits_per_pixel, record.compression)
                ] += 1
                item.update(record.as_dict())
                item["valid"] = True
                if args.write_media:
                    section = reference.section_code or "unknown"
                    name = f"{index:05d}_{section}_{reference.pointer.key}.{record.extension}"
                    (media_dir / name).write_bytes(image)
                    item["file"] = f"media/{name}"
            out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            out.write("\n")

    summary = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "colscr": str(colscr_path),
        "colscr_start_block": reader.start_block,
        "colscr_end_block": reader.end_block,
        "colscr_expanded_bytes": reader.expanded_size,
        "media_references": len(references),
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "total_payload_bytes": total_payload_bytes,
        "section_counts": dict(sorted(section_counts.items())),
        "media_type_counts": dict(sorted(media_type_counts.items())),
        "top_dimensions": [
            {
                "media_type": media_type,
                "width": width,
                "height": height,
                "bits_per_pixel": bpp,
                "compression": compression,
                "count": count,
            }
            for (media_type, width, height, bpp, compression), count in dimension_counts.most_common(20)
        ],
        "media_files_written": valid_records if args.write_media else 0,
        "manifest_path": str(manifest_path),
        "warnings": [],
    }
    write_json(dict_out / "colscr_summary.json", summary)
    return summary


def _colscr_source_task(payload: tuple[Any, Path, argparse.Namespace]) -> dict[str, Any]:
    source, out_dir, args = payload
    return extract_colscr_for_source(source, out_dir, args)


def extract_colscr_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(args.root or [Path(".")], jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)

    def log_summary(summary: dict[str, Any]) -> None:
        print(
            f"{summary['dict_id']:12s} media={summary['media_references']} valid={summary['valid_records']} "
            f"bytes={summary.get('colscr_expanded_bytes', 0)}",
            file=sys.stderr,
        )

    summaries = parallel_map_ordered(
        _colscr_source_task,
        [(source, args.out_dir, task_args) for source in sources],
        jobs=getattr(args, "jobs", 1),
        on_result=log_summary,
    )
    write_json(args.out_dir / "summary.json", summaries)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    parser.add_argument("--out-dir", type=Path, default=Path("colscr-out"), help="Output directory.")
    parser.add_argument("--limit", type=int, help="Limit media references per dictionary.")
    parser.add_argument(
        "--write-media",
        "--write-bmp",
        dest="write_media",
        action="store_true",
        help="Write referenced image files next to the manifest.",
    )
    args = parser.parse_args()
    extract_colscr_for_sources(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
