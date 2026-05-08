"""Forensic byte accounting for non-HONMON SSED components."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .colscr import iter_media_references, parse_colscr_image_header, read_colscr_record
from .entries import CONTROL_ARG_LENGTHS, control_tag_for_end, control_tag_for_start
from .gaiji import (
    candidate_gaiji_paths,
    file_identity,
    load_gaiji_profile,
    parse_ga16_resource,
    parse_uni_resource,
)
from .indexes import (
    BODY_ONLY_TAGGED_LEAF_TYPES,
    BODY_ONLY_SIMPLE_LEAF_TYPES,
    CR_LEAF_TYPES,
    INDEX_TYPES,
    KW_LEAF_TYPES,
    SIMPLE_LEAF_TYPES,
    TAGGED_LEAF_TYPES,
    be16,
    be32,
    internal_slot_size,
    is_leaf_page,
)
from .menus import MENU_TYPE, parse_menu_stream, resolve_menu_record_destinations
from .parallel import parallel_map_ordered, worker_args
from .pcmdata import (
    PCMDATA_DIRECTORY_BYTES,
    PCMDATA_TYPE,
    PCM_CONTROL,
    PcmReference,
    mp3_frame_sync,
    parse_pcm_pointer,
    pointer_relative_range,
    unreferenced_gaps,
)
from .profiles import ProfileTarget, discover_profile_targets, safe_relative
from .ssed import BLOCK_SIZE, SsedRandomReader, expand_sseddata_file, find_case_insensitive
from .titles import TITLE_TYPES


GA16_RESOURCE_NAMES = {"GA16HALF", "GA16FULL", "GAI16H", "GAI16F"}


def component_role(filename: str, component_type: int) -> str | None:
    upper = filename.upper()
    if component_type == MENU_TYPE or upper == "MENU.DIC":
        return "menu"
    if component_type in TITLE_TYPES or upper.endswith("TITLE.DIC"):
        return "title"
    if component_type in INDEX_TYPES:
        return "index"
    if upper.endswith("INDEX.DIC"):
        return "text_index"
    if component_type in {0xF1, 0xF2} or upper in GA16_RESOURCE_NAMES or upper.startswith(("GAI16H", "GAI16F")):
        return "ga16"
    if component_type == 0xD2 or upper == "COLSCR.DIC":
        return "colscr"
    if component_type == PCMDATA_TYPE or upper == "PCMDATA.DIC":
        return "pcmdata"
    return None


def nonzero_count(data: bytes) -> int:
    return sum(1 for byte in data if byte)


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if end < start:
            continue
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def interval_bytes(intervals: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in intervals)


def uncovered_segments(size: int, intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    gaps: list[tuple[int, int]] = []
    previous = 0
    for start, end in merge_intervals(intervals):
        if start > previous:
            gaps.append((previous, start - 1))
        previous = max(previous, end + 1)
    if previous < size:
        gaps.append((previous, size - 1))
    return gaps


def nonzero_uncovered_bytes(data: bytes, intervals: list[tuple[int, int]]) -> int:
    return sum(nonzero_count(data[start : end + 1]) for start, end in uncovered_segments(len(data), intervals))


def reader_nonzero_segments(
    reader: SsedRandomReader,
    segments: list[tuple[int, int]],
    *,
    sample_limit: int = 20,
) -> tuple[int, list[dict[str, Any]]]:
    count = 0
    samples: list[dict[str, Any]] = []
    for start, end in segments:
        pos = start
        while pos <= end:
            chunk_size = min(0x10000, end - pos + 1)
            chunk = reader.read(pos, chunk_size)
            if not chunk:
                break
            count += nonzero_count(chunk)
            if len(samples) < sample_limit:
                for index, byte in enumerate(chunk):
                    if byte:
                        absolute = pos + index
                        sample_end = min(len(chunk), index + 32)
                        samples.append({"offset": absolute, "bytes": chunk[index:sample_end].hex()})
                        if len(samples) >= sample_limit:
                            break
            pos += len(chunk)
    return count, samples


def uncovered_nonzero_samples(
    data: bytes,
    intervals: list[tuple[int, int]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for start, end in uncovered_segments(len(data), intervals):
        pos = start
        while pos <= end:
            if data[pos]:
                sample_end = min(end + 1, pos + 32)
                samples.append(
                    {
                        "offset": pos,
                        "bytes": data[pos:sample_end].hex(),
                    }
                )
                if len(samples) >= limit:
                    return samples
                pos = sample_end
                continue
            pos += 1
    return samples


def inspect_pcmdata_prefix(prefix: bytes) -> tuple[str, str] | None:
    """Classify a PCMDATA record from its leading bytes.

    Referenced PCMDATA pointers already declare the record extent. For forensic
    validation we only need to know whether the pointed record starts like an
    observed audio payload, not read the whole sound sample.
    """

    if prefix.startswith(b"ID3") or (len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xE0) == 0xE0):
        return "mp3", "mp3"
    if not prefix.startswith(b"fmt "):
        return None

    pos = 0
    format_tag: int | None = None
    saw_data = False
    while pos + 8 <= len(prefix):
        tag = prefix[pos : pos + 4]
        size = int.from_bytes(prefix[pos + 4 : pos + 8], "little")
        payload_offset = pos + 8
        if tag == b"fmt " and payload_offset + min(size, 16) <= len(prefix) and size >= 16:
            format_tag = int.from_bytes(prefix[payload_offset : payload_offset + 2], "little")
        if tag == b"data":
            saw_data = True
            break
        padded_end = payload_offset + size + (size & 1)
        if padded_end <= pos:
            break
        pos = padded_end

    if format_tag is None:
        return "wave_chunks", "wave_unknown"
    if format_tag == 0x0001:
        codec = "pcm"
    elif format_tag == 0x0055:
        codec = "mpeg_layer3_wave"
    else:
        codec = f"wave_format_{format_tag:04x}"
    return "wave_chunks" if saw_data else "wave_chunks_no_data_header", codec


def wave_content_size_from_prefix(prefix: bytes) -> int | None:
    if not prefix.startswith(b"fmt "):
        return None
    pos = 0
    while pos + 8 <= len(prefix):
        tag = prefix[pos : pos + 4]
        size = int.from_bytes(prefix[pos + 4 : pos + 8], "little")
        payload_offset = pos + 8
        padded_end = payload_offset + size + (size & 1)
        if tag == b"data":
            return padded_end
        if padded_end <= pos:
            return None
        pos = padded_end
    return None


def iter_pcm_references_fast(data: bytes) -> list[PcmReference]:
    references: list[PcmReference] = []
    position = 0
    while True:
        position = data.find(PCM_CONTROL, position)
        if position < 0:
            break
        payload = data[position + 2 : position + 18]
        pointer = parse_pcm_pointer(payload)
        if pointer is not None:
            references.append(PcmReference(position=position, label="", pointer=pointer))
        position += 1
    return references


def iter_gap_record_headers_from_bytes(
    data: bytes,
    gaps: list[tuple[int, int]],
) -> list[tuple[int, int, str, str]]:
    records: list[tuple[int, int, str, str]] = []
    for start, end in gaps:
        if end < PCMDATA_DIRECTORY_BYTES:
            continue
        if start < PCMDATA_DIRECTORY_BYTES:
            start = PCMDATA_DIRECTORY_BYTES
        if end < start:
            continue
        gap = data[start : end + 1]
        if not any(gap):
            continue
        pos = 0
        while pos < len(gap):
            while pos < len(gap) and gap[pos] == 0:
                pos += 1
            if pos >= len(gap):
                break
            current = gap[pos:]
            classified = inspect_pcmdata_prefix(current[:4096])
            if classified is None:
                break
            media_type, codec = classified
            if current.startswith(b"fmt "):
                content_size = wave_content_size_from_prefix(current[:4096])
                if content_size is None:
                    break
                record_size = min(content_size, len(current))
                records.append((start + pos, start + pos + record_size - 1, media_type, codec))
                pos += record_size
                continue
            if current.startswith(b"ID3") or mp3_frame_sync(current):
                next_positions = [
                    found
                    for signature in (b"ID3\x03", b"fmt ")
                    if (found := current.find(signature, 1)) > 0
                ]
                record_size = min(next_positions) if next_positions else len(current)
                records.append((start + pos, start + pos + record_size - 1, media_type, codec))
                pos += record_size
                continue
            break
    return records


def span_text_report(
    data: bytes,
    *,
    gaiji_map: dict[str, str],
    image_gaiji_keys: frozenset[str] | set[str],
    parse_mode: str,
    max_issues: int,
) -> dict[str, Any]:
    stats = {
        "bytes_total": len(data),
        "bytes_covered": 0,
        "padding_bytes": 0,
        "ascii_bytes": 0,
        "jis_pairs": 0,
        "jis_bytes": 0,
        "controls": 0,
        "known_controls": 0,
        "unknown_controls": 0,
        "control_payload_bytes": 0,
        "sections": 0,
        "breaks": 0,
        "links": 0,
        "media": 0,
        "gaiji": 0,
        "gaiji_resolved": 0,
        "gaiji_unresolved": 0,
        "gaiji_image_backed": 0,
        "invalid_jis_pairs": 0,
        "unknown_bytes": 0,
        "truncated_controls": 0,
        "truncated_gaiji": 0,
    }
    control_ops: Counter[str] = Counter()
    unknown_control_ops: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []

    def issue(kind: str, offset: int, raw: bytes, message: str) -> None:
        issue_counts[kind] += 1
        if len(issues) < max_issues:
            issues.append(
                {
                    "kind": kind,
                    "offset": offset,
                    "length": len(raw),
                    "raw_hex": raw.hex(),
                    "message": message,
                }
            )

    i = 0
    while i < len(data):
        b = data[i]
        if b == 0:
            start = i
            while i < len(data) and data[i] == 0:
                i += 1
            size = i - start
            stats["padding_bytes"] += size
            stats["bytes_covered"] += size
            continue

        if b == 0x1F:
            start = i
            if i + 1 >= len(data):
                raw = data[start:]
                stats["truncated_controls"] += 1
                issue("truncated_control", start, raw, "0x1f control introducer has no opcode byte")
                stats["bytes_covered"] += len(raw)
                break
            op = data[i + 1]
            op_hex = f"{op:02x}"
            arg_len = CONTROL_ARG_LENGTHS.get(op, 0)
            length = 2 + arg_len
            raw = data[start : min(len(data), start + length)]
            stats["controls"] += 1
            stats["control_payload_bytes"] += max(0, len(raw) - 2)
            control_ops[op_hex] += 1
            if len(raw) < length:
                stats["truncated_controls"] += 1
                issue("truncated_control", start, raw, f"control 1f{op_hex} expected {length} bytes, got {len(raw)}")
                stats["bytes_covered"] += len(raw)
                break
            if op == 0x09:
                stats["known_controls"] += 1
                stats["sections"] += 1
            elif op == 0x0A:
                stats["known_controls"] += 1
                stats["breaks"] += 1
            elif op == 0x4D:
                stats["known_controls"] += 1
                stats["media"] += 1
            elif control_tag_for_start(op) is not None or control_tag_for_end(op) is not None or op in (
                0x00,
                0x02,
                0x03,
                0x04,
                0x05,
                0x1A,
                0x1C,
            ):
                stats["known_controls"] += 1
                if control_tag_for_start(op) in {"link", "url"}:
                    stats["links"] += 1
            else:
                stats["unknown_controls"] += 1
                unknown_control_ops[op_hex] += 1
                issue("unknown_control", start, raw, f"unknown control opcode 1f{op_hex}; argument length is not known")
            stats["bytes_covered"] += length
            i += length
            continue

        if b == 0x0A:
            stats["breaks"] += 1
            stats["bytes_covered"] += 1
            i += 1
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            stats["jis_pairs"] += 1
            stats["jis_bytes"] += 2
            stats["bytes_covered"] += 2
            i += 2
            continue

        if 0xA1 <= b <= 0xFE:
            if i + 1 >= len(data):
                raw = data[i:]
                stats["truncated_gaiji"] += 1
                issue("truncated_gaiji", i, raw, "gaiji lead byte has no trailing byte")
                stats["bytes_covered"] += len(raw)
                break
            raw = data[i : i + 2]
            key = raw.hex()
            stats["gaiji"] += 1
            if key in gaiji_map:
                stats["gaiji_resolved"] += 1
            else:
                stats["gaiji_unresolved"] += 1
            if key in image_gaiji_keys:
                stats["gaiji_image_backed"] += 1
            stats["bytes_covered"] += 2
            i += 2
            continue

        if 0x20 <= b <= 0x7E:
            stats["ascii_bytes"] += 1
            stats["bytes_covered"] += 1
            i += 1
            continue

        stats["unknown_bytes"] += 1
        issue("unknown_byte", i, data[i : i + 1], f"byte 0x{b:02x} is not classified by the text decoder")
        stats["bytes_covered"] += 1
        i += 1

    strict_error = None
    if parse_mode == "strict" and issues:
        strict_error = f"{issues[0]['kind']} at offset {issues[0]['offset']}: {issues[0]['message']}"
    return {
        "stats": dict(sorted(stats.items())),
        "control_ops": dict(sorted(control_ops.items())),
        "unknown_control_ops": dict(sorted(unknown_control_ops.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "issues": issues,
        "strict_error": strict_error,
    }


def text_component_report(
    target: ProfileTarget,
    roots: list[Path],
    element: Any,
    source: Path,
    data: bytes,
    args: argparse.Namespace,
    gaiji_profile: Any,
) -> dict[str, Any]:
    row = {
        "filename": element.filename,
        "role": component_role(element.filename, element.type),
        "type": f"{element.type:02x}",
        "start_block": element.start,
        "expanded_bytes": len(data),
        "path": safe_relative(source, roots),
    }
    row["decode"] = span_text_report(
        data,
        gaiji_map=gaiji_profile.map,
        image_gaiji_keys=frozenset(),
        parse_mode=args.parse_mode,
        max_issues=args.max_issue_samples,
    )
    stats = row["decode"]["stats"]
    row["coverage"] = {
        "bytes_total": stats.get("bytes_total", 0),
        "bytes_covered": stats.get("bytes_covered", 0),
        "uncovered_bytes": max(0, int(stats.get("bytes_total", 0)) - int(stats.get("bytes_covered", 0))),
        "unknown_controls": stats.get("unknown_controls", 0),
        "unknown_bytes": stats.get("unknown_bytes", 0),
        "invalid_jis_pairs": stats.get("invalid_jis_pairs", 0),
        "truncated_controls": stats.get("truncated_controls", 0),
        "truncated_gaiji": stats.get("truncated_gaiji", 0),
    }
    if row["role"] == "menu":
        parsed = parse_menu_stream(data, gaiji="h-placeholder", gaiji_map=gaiji_profile.map)
        resolved = resolve_menu_record_destinations(parsed.records, target.elements)
        row["menu"] = {
            "records": len(parsed.records),
            "links": parsed.stats.get("links", 0),
            "destinations": parsed.stats.get("destinations", 0),
            "resolved_destinations": resolved,
            "unknown_controls": parsed.stats.get("unknown_controls", 0),
            "sections": parsed.stats.get("sections", 0),
        }
    return row


def page_nonzero_tail(page: bytes, pos: int) -> int:
    if pos >= len(page):
        return 0
    return nonzero_count(page[pos:])


def simple_leaf_consumption(page: bytes) -> tuple[int, str | None]:
    count = be16(page, 2)
    pos = 4
    rows = 0
    while rows < count:
        if pos >= len(page):
            return pos, None
        if page[pos] == 0:
            if any(page[pos : min(len(page), pos + 13)]):
                while pos + 13 <= len(page) and any(page[pos : pos + 13]):
                    pos += 13
                    rows += 1
                if len(page) - pos <= 3:
                    return len(page), None
                return pos, None
            return pos, None
        key_len = page[pos]
        pos += 1
        if pos + key_len + 12 > len(page):
            return pos, "truncated_simple_leaf"
        pos += key_len + 12
        rows += 1
    return pos, None


def body_only_simple_leaf_consumption(page: bytes) -> tuple[int, str | None]:
    count = be16(page, 2)
    pos = 4
    rows = 0
    while rows < count:
        if pos >= len(page) or page[pos] == 0:
            return pos, None
        key_len = page[pos]
        pos += 1
        if pos + key_len + 6 > len(page):
            return pos, "truncated_body_only_simple_leaf"
        pos += key_len + 6
        rows += 1
    return pos, None


def tagged_leaf_consumption(page: bytes) -> tuple[int, str | None]:
    count = be16(page, 2)
    pos = 4
    subrecord = 0
    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            return pos, None
        pos += 2
        if tag == 0x00:
            if pos + key_len + 12 > len(page):
                return pos, "truncated_tagged_direct"
            pos += key_len + 12
            subrecord += 1
            continue
        if tag == 0x80:
            if pos + 2 + key_len > len(page):
                return pos, "truncated_tagged_group"
            pos += 2 + key_len
            subrecord += 1
            continue
        if tag == 0xC0:
            if pos + key_len + 12 > len(page):
                return pos, "truncated_tagged_target"
            pos += key_len + 12
            subrecord += 1
            continue
        return pos - 2, f"unknown_tag_{tag:02x}"
    return pos, None


def body_only_tagged_leaf_consumption(page: bytes) -> tuple[int, str | None]:
    count = be16(page, 2)
    pos = 4
    subrecord = 0
    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            return pos, None
        pos += 2
        if tag == 0x00:
            if pos + key_len + 6 > len(page):
                return pos, "truncated_body_only_tagged_direct"
            pos += key_len + 6
            subrecord += 1
            continue
        if tag == 0x80:
            if pos + 2 + key_len > len(page):
                return pos, "truncated_body_only_tagged_group"
            pos += 2 + key_len
            subrecord += 1
            continue
        if tag == 0xC0:
            if pos + key_len + 6 > len(page):
                return pos, "truncated_body_only_tagged_target"
            pos += key_len + 6
            subrecord += 1
            continue
        return pos - 2, f"unknown_tag_{tag:02x}"
    return pos, None


def cr_leaf_consumption(page: bytes) -> tuple[int, str | None]:
    count = be16(page, 2)
    pos = 4
    subrecord = 0
    while subrecord < count and pos + 2 <= len(page):
        first = page[pos]
        second = page[pos + 1]
        if first == 0 and second == 0:
            return pos, None
        if first == 0x00:
            key_len = second
            if pos + 2 + key_len + 12 > len(page):
                return pos, "truncated_cr_direct"
            pos += 2 + key_len + 12
            subrecord += 1
            continue
        if first == 0x80:
            key_len = second
            if pos + 2 + 4 + key_len + 6 > len(page):
                return pos, "truncated_cr_group"
            pos += 2 + 4 + key_len + 6
            subrecord += 1
            continue
        if first == 0xC0:
            if pos + 7 > len(page):
                return pos, "truncated_cr_target"
            pos += 7
            subrecord += 1
            continue
        return pos, f"unknown_tag_{first:02x}"
    return pos, None


def kw_leaf_consumption(page: bytes) -> tuple[int, str | None]:
    count = be16(page, 2)
    pos = 4
    subrecord = 0
    while subrecord < count and pos < len(page):
        tag = page[pos]
        if tag == 0 and (pos + 1 >= len(page) or page[pos + 1] == 0):
            return pos, None
        if tag == 0x00:
            if pos + 2 > len(page):
                return pos, "truncated_kw_direct"
            key_len = page[pos + 1]
            if key_len == 0:
                return pos, None
            if pos + 2 + key_len + 12 > len(page):
                return pos, "truncated_kw_direct"
            pos += 2 + key_len + 12
            subrecord += 1
            continue
        if tag == 0x80:
            if pos + 6 > len(page):
                return pos, "truncated_kw_group"
            key_len = page[pos + 1]
            if pos + 6 + key_len + 6 > len(page):
                return pos, "truncated_kw_group"
            pos += 6 + key_len + 6
            subrecord += 1
            continue
        if tag in (0xB0, 0xC0):
            if pos + 7 > len(page):
                return pos, "truncated_kw_target"
            pos += 7
            subrecord += 1
            continue
        return pos, f"unknown_tag_{tag:02x}"
    return pos, None


def index_page_counts(component_type: int, page: bytes) -> tuple[int, int, int]:
    """Return internal_rows, leaf_rows, search_groups without decoding keys."""

    word = be16(page, 0)
    count = be16(page, 2)
    if not is_leaf_page(word):
        return count, 0, 0
    if component_type in SIMPLE_LEAF_TYPES:
        if len(page) > 4 and page[4] == 0 and any(page[4:17]):
            pos = 4
            rows = 0
            while pos + 13 <= len(page) and any(page[pos : pos + 13]):
                rows += 1
                pos += 13
            return 0, rows, 0
        return 0, count, 0
    if component_type in BODY_ONLY_SIMPLE_LEAF_TYPES:
        return 0, count, 0

    pos = 4
    subrecord = 0
    leaf_rows = 0
    groups = 0
    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            break
        if component_type in BODY_ONLY_TAGGED_LEAF_TYPES:
            if tag == 0x00:
                if key_len == 0 or pos + 2 + key_len + 6 > len(page):
                    break
                leaf_rows += 1
                pos += 2 + key_len + 6
            elif tag == 0x80:
                if pos + 2 + 2 + key_len > len(page):
                    break
                groups += 1
                pos += 2 + 2 + key_len
            elif tag == 0xC0:
                if pos + 2 + key_len + 6 > len(page):
                    break
                leaf_rows += 1
                pos += 2 + key_len + 6
            else:
                break
        elif component_type in TAGGED_LEAF_TYPES:
            if tag == 0x00:
                if key_len == 0 or pos + 2 + key_len + 12 > len(page):
                    break
                leaf_rows += 1
                pos += 2 + key_len + 12
            elif tag == 0x80:
                if pos + 2 + 2 + key_len > len(page):
                    break
                groups += 1
                pos += 2 + 2 + key_len
            elif tag == 0xC0:
                if pos + 2 + key_len + 12 > len(page):
                    break
                leaf_rows += 1
                pos += 2 + key_len + 12
            else:
                break
        elif component_type in KW_LEAF_TYPES:
            if tag == 0x00:
                if key_len == 0 or pos + 2 + key_len + 12 > len(page):
                    break
                leaf_rows += 1
                pos += 2 + key_len + 12
            elif tag == 0x80:
                if pos + 6 + key_len + 6 > len(page):
                    break
                groups += 1
                pos += 6 + key_len + 6
            elif tag in (0xB0, 0xC0):
                if pos + 7 > len(page):
                    break
                leaf_rows += 1
                pos += 7
            else:
                break
        elif component_type in CR_LEAF_TYPES:
            if tag == 0x00:
                if key_len == 0 or pos + 2 + key_len + 12 > len(page):
                    break
                leaf_rows += 1
                pos += 2 + key_len + 12
            elif tag == 0x80:
                if pos + 2 + 4 + key_len + 6 > len(page):
                    break
                groups += 1
                pos += 2 + 4 + key_len + 6
            elif tag == 0xC0:
                if pos + 7 > len(page):
                    break
                leaf_rows += 1
                pos += 7
            else:
                break
        else:
            break
        subrecord += 1
    return 0, leaf_rows, groups


def index_page_consumption(component_type: int, page: bytes) -> tuple[str, int, str | None]:
    word = be16(page, 0)
    if not is_leaf_page(word):
        count = be16(page, 2)
        slot = internal_slot_size(word)
        if slot < 6:
            return "internal", 4, "invalid_internal_slot"
        end = 4 + count * slot
        if end > len(page):
            return "internal", len(page), "truncated_internal_page"
        return "internal", end, None
    if component_type in BODY_ONLY_SIMPLE_LEAF_TYPES:
        pos, issue = body_only_simple_leaf_consumption(page)
    elif component_type in BODY_ONLY_TAGGED_LEAF_TYPES:
        pos, issue = body_only_tagged_leaf_consumption(page)
    elif component_type in TAGGED_LEAF_TYPES:
        pos, issue = tagged_leaf_consumption(page)
    elif component_type in SIMPLE_LEAF_TYPES:
        pos, issue = simple_leaf_consumption(page)
    elif component_type in KW_LEAF_TYPES:
        pos, issue = kw_leaf_consumption(page)
    elif component_type in CR_LEAF_TYPES:
        pos, issue = cr_leaf_consumption(page)
    else:
        pos, issue = 4, "unknown_index_component_type"
    return "leaf", pos, issue


def index_component_report(
    roots: list[Path],
    element: Any,
    source: Path,
    data: bytes,
    gaiji_profile: Any,
) -> dict[str, Any]:
    page_words: Counter[str] = Counter()
    page_issues: Counter[str] = Counter()
    internal_pages = 0
    leaf_pages = 0
    internal_rows = 0
    leaf_rows = 0
    search_groups = 0
    nonzero_residual = 0
    residual_pages = 0
    parsed_record_bytes = 0
    page_count = len(data) // BLOCK_SIZE
    trailing_component_bytes = len(data) % BLOCK_SIZE
    trailing_component_nonzero = nonzero_count(data[page_count * BLOCK_SIZE :])
    samples: list[dict[str, Any]] = []

    for page_zero in range(page_count):
        page = data[page_zero * BLOCK_SIZE : (page_zero + 1) * BLOCK_SIZE]
        word = be16(page, 0)
        page_words[f"{word:04x}"] += 1
        kind, consumed, issue = index_page_consumption(element.type, page)
        page_internal_rows, page_leaf_rows, page_groups = index_page_counts(element.type, page)
        internal_rows += page_internal_rows
        leaf_rows += page_leaf_rows
        search_groups += page_groups
        if kind == "internal":
            internal_pages += 1
        else:
            leaf_pages += 1
        parsed_record_bytes += consumed
        tail_nonzero = page_nonzero_tail(page, consumed)
        if issue is not None:
            page_issues[issue] += 1
        if tail_nonzero:
            residual_pages += 1
            nonzero_residual += tail_nonzero
            if len(samples) < 20:
                samples.append(
                    {
                        "page_index": page_zero + 1,
                        "logical_block": element.start + page_zero,
                        "kind": kind,
                        "word": f"{word:04x}",
                        "consumed": consumed,
                        "issue": issue,
                        "nonzero_residual_bytes": tail_nonzero,
                        "residual_prefix": page[consumed : min(len(page), consumed + 32)].hex(),
                    }
                )

    return {
        "filename": element.filename,
        "role": "index",
        "type": f"{element.type:02x}",
        "start_block": element.start,
        "path": safe_relative(source, roots),
        "expanded_bytes": len(data),
        "scan": {
            "component": element.filename,
            "type": f"{element.type:02x}",
            "data_flags": element.data.hex(),
            "expanded_bytes": len(data),
            "internal_pages": internal_pages,
            "leaf_pages": leaf_pages,
            "internal_rows": internal_rows,
            "leaf_rows": leaf_rows,
            "search_groups": search_groups,
            "unknown_leaf_bytes": 0,
            "warnings": ["page issues recorded in page_issues"] if page_issues else [],
        },
        "page_count": page_count,
        "trailing_component_bytes": trailing_component_bytes,
        "trailing_component_nonzero": trailing_component_nonzero,
        "page_words": dict(sorted(page_words.items())),
        "parsed_record_prefix_bytes": parsed_record_bytes,
        "nonzero_residual_bytes": nonzero_residual + trailing_component_nonzero,
        "residual_pages": residual_pages,
        "page_issues": dict(sorted(page_issues.items())),
        "residual_samples": samples,
    }


def ga16_report(roots: list[Path], element: Any, source: Path) -> dict[str, Any]:
    data = source.read_bytes()
    resource = parse_ga16_resource(source)
    row: dict[str, Any] = {
        "filename": element.filename,
        "role": "ga16",
        "type": f"{element.type:02x}",
        "path": safe_relative(source, roots),
        "file_bytes": len(data),
        "status": "ok" if resource is not None else "unparsed",
    }
    if resource is None:
        return row
    glyph_bytes_total = resource.count * resource.glyph_bytes
    expected_end = resource.data_offset + glyph_bytes_total
    header = data[: min(resource.data_offset, len(data))]
    known_header_offsets = {0, *range(8, 14)}
    unknown_header_nonzero = [
        {"offset": index, "value": f"{byte:02x}"}
        for index, byte in enumerate(header)
        if byte and index not in known_header_offsets
    ]
    row.update(
        {
            "width": resource.width,
            "height": resource.height,
            "start_code": f"{resource.start_code:04x}",
            "count": resource.count,
            "row_bytes": (resource.width + 7) // 8,
            "glyph_bytes": resource.glyph_bytes,
            "glyph_bytes_total": glyph_bytes_total,
            "data_offset": resource.data_offset,
            "expected_end": expected_end,
            "missing_glyph_bytes": max(0, expected_end - len(data)),
            "trailing_bytes": max(0, len(data) - expected_end),
            "trailing_nonzero_bytes": nonzero_count(data[expected_end:]) if len(data) > expected_end else 0,
            "unknown_header_nonzero": len(unknown_header_nonzero),
            "unknown_header_nonzero_samples": unknown_header_nonzero[:20],
        }
    )
    return row


def uni_report(path: Path, roots: list[Path]) -> dict[str, Any]:
    resource = parse_uni_resource(path)
    row: dict[str, Any] = {
        "path": safe_relative(path, roots),
        "file_bytes": path.stat().st_size,
        "status": "ok" if resource is not None else "unparsed",
    }
    if resource is None:
        return row
    mapped = sum(1 for record in resource.records if record.display)
    fallback = sum(1 for record in resource.records if record.fallback)
    legacy = sum(1 for record in resource.records if record.legacy)
    metadata = sum(1 for record in resource.records if record.metadata)
    duplicates = len(resource.records) - len({(record.section, record.code) for record in resource.records})
    flat_duplicates = len(resource.records) - len({record.code for record in resource.records})
    row.update(
        {
            "format": resource.format,
            "half_count": resource.half_count,
            "full_count": resource.full_count,
            "records": len(resource.records),
            "mapped_records": mapped,
            "fallback_records": fallback,
            "legacy_records": legacy,
            "metadata_records": metadata,
            "expected_size": resource.expected_size,
            "trailing_bytes": resource.trailing_bytes,
            "trailing_nonzero_bytes": nonzero_count(path.read_bytes()[resource.expected_size :])
            if path.stat().st_size > resource.expected_size
            else 0,
            "duplicate_section_codes": duplicates,
            "duplicate_flat_codes": flat_duplicates,
        }
    )
    return row


def referenced_honmon_bytes(target: ProfileTarget) -> bytes:
    element = next((item for item in target.elements if item.filename.upper() == "HONMON.DIC"), None)
    if element is None:
        return b""
    path = find_case_insensitive(target.idx.parent, element.filename)
    if path is None:
        return b""
    return expand_sseddata_file(path)


def colscr_report(
    target: ProfileTarget,
    roots: list[Path],
    element: Any,
    source: Path,
    honmon: bytes,
) -> dict[str, Any]:
    reader = SsedRandomReader(source)
    data = expand_sseddata_file(source)
    references = list(iter_media_references(honmon)) if honmon else []
    intervals: list[tuple[int, int]] = []
    valid_refs = 0
    invalid_refs = 0
    media_types: Counter[str] = Counter()

    for reference in references:
        parsed = read_colscr_record(reader, reference.pointer)
        if parsed is None:
            invalid_refs += 1
            continue
        record, _image = parsed
        start = record.relative_offset
        end = start + 8 + record.payload_size - 1
        if 0 <= start <= end < len(data):
            intervals.append((start, end))
        valid_refs += 1
        media_types[record.media_type] += 1

    scanned_records = 0
    scanned_unreferenced = 0
    first_record = data.find(b"data")
    if first_record > 0:
        intervals.append((0, first_record - 1))

    pos = 0
    merged_ref = merge_intervals(intervals)
    while True:
        pos = data.find(b"data", pos)
        if pos < 0:
            break
        if any(start <= pos <= end for start, end in merged_ref):
            pos += 4
            continue
        parsed = parse_colscr_image_header(data[pos : min(len(data), pos + 70)])
        if parsed is None:
            pos += 1
            continue
        payload_size, media_type, _extension, _width, _height, _bpp, _compression = parsed
        end = pos + 8 + payload_size - 1
        if end >= len(data):
            pos += 1
            continue
        intervals.append((pos, end))
        scanned_records += 1
        scanned_unreferenced += 1
        media_types[media_type] += 1
        pos = end + 1

    intervals = merge_intervals(intervals)
    nonzero_uncovered = nonzero_uncovered_bytes(data, intervals)
    return {
        "filename": element.filename,
        "role": "colscr",
        "type": f"{element.type:02x}",
        "path": safe_relative(source, roots),
        "start_block": reader.start_block,
        "end_block": reader.end_block,
        "expanded_bytes": len(data),
        "honmon_references": len(references),
        "valid_referenced_records": valid_refs,
        "invalid_referenced_records": invalid_refs,
        "scanned_unreferenced_records": scanned_unreferenced,
        "record_intervals": len(intervals),
        "record_bytes_covered": interval_bytes(intervals),
        "zero_or_padding_bytes": len(data) - interval_bytes(intervals) - nonzero_uncovered,
        "nonzero_unparsed_bytes": nonzero_uncovered,
        "nonzero_unparsed_samples": uncovered_nonzero_samples(data, intervals),
        "media_type_counts": dict(sorted(media_types.items())),
    }


def pcmdata_report(
    target: ProfileTarget,
    roots: list[Path],
    element: Any,
    source: Path,
    honmon: bytes,
) -> dict[str, Any]:
    reader = SsedRandomReader(source)
    data = expand_sseddata_file(source)
    references = iter_pcm_references_fast(honmon) if honmon else []
    intervals: list[tuple[int, int]] = []
    valid_refs = 0
    invalid_refs = 0
    codecs: Counter[str] = Counter()
    media_types: Counter[str] = Counter()
    expanded_size = len(data)
    directory_end = min(expanded_size, PCMDATA_DIRECTORY_BYTES) - 1
    covered: list[tuple[int, int]] = [(0, directory_end)] if directory_end >= 0 else []

    for reference in references:
        start, end, size = pointer_relative_range(reference.pointer, reader)
        if start < 0 or end >= expanded_size or size <= 0:
            invalid_refs += 1
            continue
        intervals.append((start, end))
        covered.append((start, end))
        classified = inspect_pcmdata_prefix(data[start : start + min(size, 4096)])
        if classified is None:
            invalid_refs += 1
            media_types["unknown_audio_payload"] += 1
            codecs["unknown"] += 1
            continue
        valid_refs += 1
        media_type, codec = classified
        codecs[codec] += 1
        media_types[media_type] += 1

    unreferenced = 0
    gaps = unreferenced_gaps(intervals, expanded_size)
    for start, end, media_type, codec in iter_gap_record_headers_from_bytes(data, gaps):
        if start < PCMDATA_DIRECTORY_BYTES:
            continue
        unreferenced += 1
        codecs[codec] += 1
        media_types[media_type] += 1
        covered.append((start, end))

    covered = merge_intervals(covered)
    nonzero_uncovered = nonzero_uncovered_bytes(data, covered)
    nonzero_samples = uncovered_nonzero_samples(data, covered)
    return {
        "filename": element.filename,
        "role": "pcmdata",
        "type": f"{element.type:02x}",
        "path": safe_relative(source, roots),
        "start_block": reader.start_block,
        "end_block": reader.end_block,
        "expanded_bytes": expanded_size,
        "directory_bytes": min(expanded_size, PCMDATA_DIRECTORY_BYTES),
        "honmon_references": len(references),
        "unique_honmon_references": len({reference.pointer.payload for reference in references}),
        "valid_referenced_records": valid_refs,
        "invalid_referenced_records": invalid_refs,
        "unreferenced_records": unreferenced,
        "record_intervals": len(covered),
        "record_bytes_covered": interval_bytes(covered),
        "zero_or_padding_bytes": expanded_size - interval_bytes(covered) - nonzero_uncovered,
        "nonzero_unparsed_bytes": nonzero_uncovered,
        "nonzero_unparsed_samples": nonzero_samples,
        "media_type_counts": dict(sorted(media_types.items())),
        "codec_counts": dict(sorted(codecs.items())),
    }


def component_forensics_for_target(
    target: ProfileTarget,
    roots: list[Path],
    args: argparse.Namespace,
) -> dict[str, Any]:
    gaiji_profile = load_gaiji_profile(target.idx)
    components: list[dict[str, Any]] = []
    honmon_cache: bytes | None = None
    for element in target.elements:
        role = component_role(element.filename, element.type)
        if role is None:
            continue
        if role != "ga16" and not element.start:
            continue
        source = find_case_insensitive(target.idx.parent, element.filename)
        if source is None:
            components.append(
                {
                    "filename": element.filename,
                    "role": role,
                    "type": f"{element.type:02x}",
                    "status": "missing_file",
                }
            )
            continue
        try:
            if role in {"menu", "title", "text_index", "index"}:
                data = expand_sseddata_file(source)
            else:
                data = b""
            if role in {"menu", "title", "text_index"}:
                row = text_component_report(target, roots, element, source, data, args, gaiji_profile)
            elif role == "index":
                row = index_component_report(roots, element, source, data, gaiji_profile)
            elif role == "ga16":
                row = ga16_report(roots, element, source)
            elif role == "colscr":
                if honmon_cache is None:
                    honmon_cache = referenced_honmon_bytes(target)
                row = colscr_report(target, roots, element, source, honmon_cache)
            elif role == "pcmdata":
                if honmon_cache is None:
                    honmon_cache = referenced_honmon_bytes(target)
                row = pcmdata_report(target, roots, element, source, honmon_cache)
            else:
                continue
            row["status"] = row.get("status", "ok")
            components.append(row)
        except Exception as exc:
            components.append(
                {
                    "filename": element.filename,
                    "role": role,
                    "type": f"{element.type:02x}",
                    "status": "parse_error",
                    "error": str(exc),
                }
            )

    uni_candidates, _plist_candidates = candidate_gaiji_paths(target.idx)
    uni_reports: list[dict[str, Any]] = []
    seen_uni: set[Any] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        uni_reports.append(uni_report(path, roots))

    return {
        "schema": "logovista-component-forensics-v1",
        "dict_id": target.dict_id,
        "title": target.title,
        "idx": safe_relative(target.idx, roots),
        "package_dir": safe_relative(target.idx.parent, roots),
        "components": components,
        "uni_files": uni_reports,
    }


def _forensics_task(payload: tuple[ProfileTarget, list[Path], Path, argparse.Namespace]) -> dict[str, Any]:
    target, roots, out_dir, args = payload
    report = component_forensics_for_target(target, roots, args)
    dict_out = out_dir / target.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    (dict_out / "component_forensics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def summarize_report(report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    component_counts = Counter(component.get("role") for component in report["components"])
    status_counts = Counter(component.get("status") for component in report["components"])
    totals = Counter()
    issue_components: list[dict[str, Any]] = []
    for component in report["components"]:
        role = component.get("role")
        totals[f"{role}_components"] += 1
        if component.get("status") != "ok":
            issue_components.append({"component": component.get("filename"), "role": role, "status": component.get("status")})
            continue
        if role in {"menu", "title", "text_index"}:
            coverage = component.get("coverage", {})
            for key in (
                "uncovered_bytes",
                "unknown_controls",
                "unknown_bytes",
                "invalid_jis_pairs",
                "truncated_controls",
                "truncated_gaiji",
            ):
                totals[f"text_{key}"] += int(coverage.get(key, 0) or 0)
        elif role == "index":
            totals["index_nonzero_residual_bytes"] += int(component.get("nonzero_residual_bytes", 0) or 0)
            totals["index_trailing_component_nonzero"] += int(component.get("trailing_component_nonzero", 0) or 0)
            if component.get("page_issues"):
                issue_components.append(
                    {
                        "component": component.get("filename"),
                        "role": role,
                        "page_issues": component.get("page_issues"),
                    }
                )
        elif role == "ga16":
            totals["ga16_trailing_nonzero_bytes"] += int(component.get("trailing_nonzero_bytes", 0) or 0)
            totals["ga16_missing_glyph_bytes"] += int(component.get("missing_glyph_bytes", 0) or 0)
            totals["ga16_unknown_header_nonzero"] += int(component.get("unknown_header_nonzero", 0) or 0)
        elif role in {"colscr", "pcmdata"}:
            totals[f"{role}_nonzero_unparsed_bytes"] += int(component.get("nonzero_unparsed_bytes", 0) or 0)
            totals[f"{role}_invalid_referenced_records"] += int(component.get("invalid_referenced_records", 0) or 0)
    for uni in report["uni_files"]:
        totals["uni_files"] += 1
        totals["uni_trailing_bytes"] += int(uni.get("trailing_bytes", 0) or 0)
        totals["uni_trailing_nonzero_bytes"] += int(uni.get("trailing_nonzero_bytes", 0) or 0)
        if uni.get("status") != "ok":
            issue_components.append({"component": uni.get("path"), "role": "uni", "status": uni.get("status")})

    return {
        "dict_id": report["dict_id"],
        "title": report["title"],
        "profile": str(out_dir / report["dict_id"] / "component_forensics.json"),
        "component_counts": dict(sorted(component_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "totals": dict(sorted(totals.items())),
        "issues": issue_components[:50],
    }


def corpus_component_forensics_summary(reports: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    profiles = [summarize_report(report, out_dir) for report in reports]
    aggregate = Counter()
    component_counts = Counter()
    status_counts = Counter()
    hotspots: list[dict[str, Any]] = []
    for row in profiles:
        component_counts.update(row["component_counts"])
        status_counts.update(row["status_counts"])
        for key, value in row["totals"].items():
            aggregate[key] += int(value or 0)
        interesting = {key: value for key, value in row["totals"].items() if value and (
            key.endswith("unknown_controls")
            or key.endswith("unknown_bytes")
            or key.endswith("invalid_jis_pairs")
            or key.endswith("nonzero_residual_bytes")
            or key.endswith("nonzero_unparsed_bytes")
            or key.endswith("invalid_referenced_records")
            or key.endswith("trailing_nonzero_bytes")
            or key.endswith("missing_glyph_bytes")
            or key.endswith("unknown_header_nonzero")
        )}
        if interesting or row["issues"]:
            hotspots.append(
                {
                    "dict_id": row["dict_id"],
                    "totals": interesting,
                    "issues": row["issues"][:10],
                    "profile": row["profile"],
                }
            )
    return {
        "schema": "logovista-corpus-component-forensics-summary-v1",
        "total": len(reports),
        "component_counts": dict(sorted(component_counts.items())),
        "component_status_counts": dict(sorted(status_counts.items())),
        "totals": dict(sorted(aggregate.items())),
        "profiles": profiles,
        "hotspots": hotspots[:100],
    }


def extract_component_forensics_for_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    roots = args.root or [Path(".")]
    targets = discover_profile_targets(roots, jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        targets = [target for target in targets if target.dict_id in selected or target.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)
    reports = parallel_map_ordered(
        _forensics_task,
        [(target, roots, args.out_dir, task_args) for target in targets],
        jobs=getattr(args, "jobs", 1),
    )
    (args.out_dir / "summary.json").write_text(
        json.dumps(corpus_component_forensics_summary(reports, args.out_dir), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [summarize_report(report, args.out_dir) for report in reports]
