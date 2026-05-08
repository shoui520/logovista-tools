"""Corpus-scale gaiji readiness reports.

This pass is intentionally lighter and more policy-oriented than
``gaiji-report``. It does not use SQLite evidence by default. Instead it asks:

- which raw text components actually contain gaiji references;
- whether each raw code has a Unicode mapping, package image, GA16 bitmap, or
  only formatting-helper evidence;
- which mapped codes lack search/fallback text;
- which mappings/resources are present but unused by scanned raw text.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .component_forensics import component_role
from .entries import CONTROL_ARG_LENGTHS
from .gaiji import (
    UniRecord,
    candidate_gaiji_paths,
    file_identity,
    iter_ga16_code_sources,
    load_plist_gaiji_map_from_paths,
    parse_ga16_resource,
    parse_uni_resource,
)
from .parallel import parallel_map_ordered, worker_args
from .profiles import ProfileTarget, discover_profile_targets
from .resources import load_image_resource_profile
from .ssed import expand_sseddata_file, find_case_insensitive


GA16_RESOURCE_NAMES = {"GA16HALF", "GA16FULL", "GAI16H", "GAI16F"}
TEXT_ROLES = {"honmon", "menu", "title", "text_index"}


@dataclass
class MappingEvidence:
    code: str
    display: str | None = None
    fallback: str | None = None
    legacy: str | None = None
    source: str | None = None
    source_path: str | None = None
    section: str | None = None
    metadata: int | None = None


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def gaiji_space(code: str) -> str:
    return "half" if int(code, 16) < 0xB000 else "full"


def placeholder_for_space(code: str, space: str) -> str:
    return f"<{'h' if space == 'half' else 'z'}{code.upper()}>"


def observed_gaiji_space(
    code: str,
    mapping: MappingEvidence | None,
    bitmap: dict[str, Any] | None,
) -> str:
    if mapping is not None and mapping.section in {"half", "full"}:
        return mapping.section
    if bitmap is not None:
        resource = str(bitmap.get("resource") or "").upper()
        if "HALF" in resource or "16H" in resource:
            return "half"
        if "FULL" in resource or "16F" in resource:
            return "full"
    return gaiji_space(code)


def is_ga16_resource_name(path: Path) -> bool:
    name = path.name.upper()
    return name in GA16_RESOURCE_NAMES or name.startswith(("GAI16H", "GAI16F"))


def text_role_for_component(filename: str, component_type: int) -> str | None:
    upper = filename.upper()
    if upper == "HONMON.DIC":
        return "honmon"
    role = component_role(filename, component_type)
    return role if role in TEXT_ROLES else None


def iter_gaiji_codes_in_stream(data: bytes) -> Iterable[str]:
    """Yield raw gaiji codes while skipping known control payload bytes."""

    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            i += 2 + CONTROL_ARG_LENGTHS.get(op, 0)
            continue
        if i + 1 < len(data) and 0x21 <= byte <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            i += 2
            continue
        if i + 1 < len(data) and 0xA1 <= byte <= 0xFE:
            yield f"{byte:02x}{data[i + 1]:02x}"
            i += 2
            continue
        i += 1


def scan_raw_text_gaiji(target: ProfileTarget) -> tuple[Counter[str], dict[str, Any], list[dict[str, str]]]:
    total: Counter[str] = Counter()
    component_reports: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for element in target.elements:
        role = text_role_for_component(element.filename, element.type)
        if role is None:
            continue
        source = find_case_insensitive(target.idx.parent, element.filename)
        if source is None:
            errors.append({"component": element.filename, "error": "missing_file"})
            component_reports[element.filename] = {
                "role": role,
                "status": "missing_file",
            }
            continue
        try:
            expanded = expand_sseddata_file(source)
        except Exception as exc:
            errors.append({"component": element.filename, "error": str(exc)})
            component_reports[element.filename] = {
                "role": role,
                "status": "expand_error",
                "error": str(exc),
            }
            continue
        counts = Counter(iter_gaiji_codes_in_stream(expanded))
        total.update(counts)
        component_reports[element.filename] = {
            "role": role,
            "status": "ok",
            "expanded_bytes": len(expanded),
            "gaiji_occurrences": sum(counts.values()),
            "distinct_gaiji": len(counts),
            "top": [{"code": code, "count": count} for code, count in counts.most_common(25)],
        }
    return total, component_reports, errors


def _record_mapping_from_uni(record: UniRecord, path: Path) -> MappingEvidence:
    return MappingEvidence(
        code=record.code,
        display=record.display or None,
        fallback=record.fallback or None,
        legacy=record.legacy or None,
        source="uni",
        source_path=str(path),
        section=record.section,
        metadata=record.metadata,
    )


def load_mapping_evidence(idx: Path) -> tuple[dict[str, MappingEvidence], dict[str, Any]]:
    uni_candidates, plist_candidates = candidate_gaiji_paths(idx)
    mappings: dict[str, MappingEvidence] = {}
    uni_paths: list[Path] = []
    uni_files: list[dict[str, Any]] = []
    seen_uni: set[Any] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        resource = parse_uni_resource(path)
        if resource is None:
            uni_files.append({"path": str(path), "status": "unparsable"})
            continue
        uni_paths.append(path)
        uni_files.append(
            {
                "path": str(path),
                "format": resource.format,
                "half_count": resource.half_count,
                "full_count": resource.full_count,
                "records": len(resource.records),
                "trailing_bytes": resource.trailing_bytes,
            }
        )
        for record in resource.records:
            if record.display or record.fallback or record.legacy:
                mappings[record.code] = _record_mapping_from_uni(record, path)

    plist_map, plist_paths = load_plist_gaiji_map_from_paths(plist_candidates)
    for code, display in plist_map.items():
        mappings.setdefault(
            code,
            MappingEvidence(
                code=code,
                display=display or None,
                source="plist",
                source_path=str(plist_paths[0]) if plist_paths else None,
            ),
        )

    return mappings, {
        "uni_paths": [str(path) for path in uni_paths],
        "plist_paths": [str(path) for path in plist_paths],
        "uni_files": uni_files,
    }


def bitmap_gaiji_codes(idx: Path) -> dict[str, Any]:
    codes: dict[str, Any] = {}
    uni_candidates, _plist_candidates = candidate_gaiji_paths(idx)
    uni_resource = None
    seen_uni: set[Any] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        uni_resource = parse_uni_resource(path)
        if uni_resource is not None:
            break
    roots = [idx.parent, idx.parent.parent]
    seen: set[Any] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file() or not is_ga16_resource_name(path):
                continue
            identity = file_identity(path)
            if identity in seen:
                continue
            seen.add(identity)
            resource = parse_ga16_resource(path)
            if resource is None:
                continue
            data = path.read_bytes()
            for code, index, code_source in iter_ga16_code_sources(resource, uni_resource):
                glyph = resource.glyph_for_index(data, index)
                blank = glyph is not None and not any(glyph)
                row = {
                    "resource": path.name,
                    "width": resource.width,
                    "height": resource.height,
                    "start_code": f"{resource.start_code:04x}",
                    "count": resource.count,
                    "glyph_index": index,
                    "code_source": code_source,
                    "blank": blank,
                }
                existing = codes.get(code)
                if (
                    existing is None
                    or (existing.get("blank") and not blank)
                    or (
                        code_source == "uni_record_order"
                        and existing.get("code_source") == "sequential"
                        and bool(existing.get("blank")) == blank
                    )
                ):
                    codes[code] = row
    return codes


def is_search_fallback_missing(mapping: MappingEvidence | None, raw_count: int) -> bool:
    if mapping is None or raw_count <= 0:
        return False
    if not mapping.display:
        return False
    if mapping.fallback:
        return False
    # ASCII and kana/kanji display values already search as themselves in most
    # exporters. The missing fallback matters most for compatibility characters,
    # combining sequences, Latin letters with marks, symbols, and non-BMP text.
    for char in mapping.display:
        name = unicodedata.name(char, "")
        category = unicodedata.category(char)
        if ord(char) > 0xFFFF:
            return True
        if unicodedata.combining(char):
            return True
        if category.startswith(("S", "M")):
            return True
        if "WITH" in name and ("LATIN" in name or "GREEK" in name or "CYRILLIC" in name):
            return True
        if category == "So":
            return True
    return False


def is_formatting_helper_candidate(
    code: str,
    *,
    raw_count: int,
    mapping: MappingEvidence | None,
    bitmap: dict[str, Any] | None,
    image: bool,
) -> bool:
    if raw_count <= 0 or mapping is not None or image:
        return False
    if bitmap and bitmap.get("blank") and mapping is None:
        return True
    if bitmap:
        return False
    # The observed unbacked full-width gaiji namespace is heavily used by
    # renderer-only markers. Real display gaiji in this space normally have
    # .uni, GA16, or package image coverage. Keep this as an explicit
    # confidence-bounded bucket rather than silently treating it as resolved.
    return gaiji_space(code) == "full"


def primary_bucket(
    code: str,
    *,
    raw_count: int,
    mapping: MappingEvidence | None,
    bitmap: dict[str, Any] | None,
    image: bool,
) -> str:
    if raw_count == 0:
        if mapping is not None:
            return "unused_mapping"
        if bitmap is not None:
            return "unused_bitmap"
        if image:
            return "unused_image_asset"
    if mapping is not None and mapping.display:
        return "unicode_mapped"
    if image:
        return "image_backed"
    if is_formatting_helper_candidate(
        code,
        raw_count=raw_count,
        mapping=mapping,
        bitmap=bitmap,
        image=image,
    ):
        return "formatting_helper"
    if bitmap is not None:
        return "bitmap_backed"
    return "display_unresolved"


def build_component_counts_by_code(component_reports: dict[str, Any]) -> dict[str, dict[str, int]]:
    by_code: dict[str, dict[str, int]] = defaultdict(dict)
    for component_name, summary in component_reports.items():
        for row in summary.get("top", []):
            by_code[row["code"]][component_name] = row["count"]
    return by_code


def code_row(
    code: str,
    *,
    raw_count: int,
    component_counts: dict[str, int],
    mapping: MappingEvidence | None,
    bitmap: dict[str, Any] | None,
    image: bool,
) -> dict[str, Any]:
    space = observed_gaiji_space(code, mapping, bitmap)
    bucket = primary_bucket(
        code,
        raw_count=raw_count,
        mapping=mapping,
        bitmap=bitmap,
        image=image,
    )
    flags: list[str] = []
    if raw_count and mapping is None:
        flags.append("raw_occurrence_unmapped")
    if raw_count and bucket == "display_unresolved":
        flags.append("display_unresolved")
    if raw_count and bucket == "formatting_helper":
        flags.append("formatting_helper_candidate")
    if is_search_fallback_missing(mapping, raw_count):
        flags.append("search_fallback_missing")
    return {
        "code": code,
        "placeholder": placeholder_for_space(code, space),
        "space": space,
        "raw_count": raw_count,
        "component_top_counts": component_counts,
        "bucket": bucket,
        "flags": flags,
        "display": mapping.display if mapping else None,
        "fallback": mapping.fallback if mapping else None,
        "legacy": mapping.legacy if mapping else None,
        "mapping_source": mapping.source if mapping else None,
        "mapping_section": mapping.section if mapping else None,
        "mapping_metadata": f"{mapping.metadata:04x}" if mapping and mapping.metadata is not None else None,
        "bitmap": bitmap,
        "image_asset": image,
    }


def readiness_status(
    *,
    raw_occurrences: int,
    display_unresolved_occurrences: int,
    resolved_occurrences: int,
) -> str:
    if raw_occurrences == 0:
        return "n/a"
    if display_unresolved_occurrences == 0:
        return "yes"
    if resolved_occurrences:
        return "partial"
    return "no"


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bucket_distinct = Counter(row["bucket"] for row in rows)
    bucket_occurrences: Counter[str] = Counter()
    flags_distinct: Counter[str] = Counter()
    flags_occurrences: Counter[str] = Counter()
    raw_occurrences = 0
    raw_distinct = 0
    resolved_occurrences = 0
    for row in rows:
        raw_count = int(row.get("raw_count") or 0)
        if raw_count:
            raw_occurrences += raw_count
            raw_distinct += 1
            if row["bucket"] != "display_unresolved":
                resolved_occurrences += raw_count
        bucket_occurrences[row["bucket"]] += raw_count
        for flag in row.get("flags", []):
            flags_distinct[flag] += 1
            flags_occurrences[flag] += raw_count
    display_unresolved_occurrences = bucket_occurrences["display_unresolved"]
    status = readiness_status(
        raw_occurrences=raw_occurrences,
        display_unresolved_occurrences=display_unresolved_occurrences,
        resolved_occurrences=resolved_occurrences,
    )
    return {
        "raw_occurrences": raw_occurrences,
        "raw_distinct_codes": raw_distinct,
        "readiness_status": status,
        "bucket_distinct_counts": dict(sorted(bucket_distinct.items())),
        "bucket_occurrence_counts": dict(sorted(bucket_occurrences.items())),
        "flag_distinct_counts": dict(sorted(flags_distinct.items())),
        "flag_occurrence_counts": dict(sorted(flags_occurrences.items())),
        "display_unresolved_codes": bucket_distinct["display_unresolved"],
        "display_unresolved_occurrences": display_unresolved_occurrences,
        "search_fallback_missing_codes": flags_distinct["search_fallback_missing"],
        "search_fallback_missing_occurrences": flags_occurrences["search_fallback_missing"],
        "formatting_helper_candidate_codes": bucket_distinct["formatting_helper"],
        "formatting_helper_candidate_occurrences": bucket_occurrences["formatting_helper"],
    }


def extract_gaiji_readiness(target: ProfileTarget, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / target.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)

    raw_counts, component_reports, errors = scan_raw_text_gaiji(target)
    mappings, mapping_sources = load_mapping_evidence(target.idx)
    bitmaps = bitmap_gaiji_codes(target.idx)
    image_profile = load_image_resource_profile(target.idx)
    image_codes = set(image_profile.gaiji_image_keys)

    component_counts_by_code = build_component_counts_by_code(component_reports)
    all_codes = set(raw_counts) | set(mappings) | set(bitmaps) | image_codes
    rows = [
        code_row(
            code,
            raw_count=raw_counts.get(code, 0),
            component_counts=component_counts_by_code.get(code, {}),
            mapping=mappings.get(code),
            bitmap=bitmaps.get(code),
            image=code in image_codes,
        )
        for code in sorted(all_codes)
    ]
    rows.sort(key=lambda row: (-int(row["raw_count"]), row["bucket"], row["code"]))
    summary = summarize_rows(rows)
    report = {
        "schema": "logovista-gaiji-readiness-v1",
        "dict_id": target.dict_id,
        "dict_title": target.title,
        "idx": str(target.idx),
        "summary": {
            **summary,
            "mapping_codes": len(mappings),
            "bitmap_codes": len(bitmaps),
            "image_codes": len(image_codes),
            "scan_errors": len(errors),
        },
        "mapping_sources": mapping_sources,
        "raw_components": component_reports,
        "scan_errors": errors,
        "codes": rows,
    }
    write_json(dict_out / "gaiji_readiness.json", report)
    return {
        "dict_id": target.dict_id,
        "dict_title": target.title,
        "idx": str(target.idx),
        "report": str(dict_out / "gaiji_readiness.json"),
        **report["summary"],
    }


def _gaiji_readiness_task(payload: tuple[ProfileTarget, Path, argparse.Namespace]) -> dict[str, Any]:
    target, out_dir, args = payload
    return extract_gaiji_readiness(target, out_dir, args)


def aggregate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row.get("readiness_status") or "unknown") for row in rows)
    bucket_distinct: Counter[str] = Counter()
    bucket_occurrences: Counter[str] = Counter()
    flags_distinct: Counter[str] = Counter()
    flags_occurrences: Counter[str] = Counter()
    for row in rows:
        for key, value in row.get("bucket_distinct_counts", {}).items():
            bucket_distinct[key] += int(value)
        for key, value in row.get("bucket_occurrence_counts", {}).items():
            bucket_occurrences[key] += int(value)
        for key, value in row.get("flag_distinct_counts", {}).items():
            flags_distinct[key] += int(value)
        for key, value in row.get("flag_occurrence_counts", {}).items():
            flags_occurrences[key] += int(value)
    return {
        "schema": "logovista-gaiji-readiness-summary-v1",
        "total": len(rows),
        "readiness_status_counts": dict(sorted(status_counts.items())),
        "bucket_distinct_counts": dict(sorted(bucket_distinct.items())),
        "bucket_occurrence_counts": dict(sorted(bucket_occurrences.items())),
        "flag_distinct_counts": dict(sorted(flags_distinct.items())),
        "flag_occurrence_counts": dict(sorted(flags_occurrences.items())),
        "display_unresolved_dictionaries": [
            {
                "dict_id": row["dict_id"],
                "codes": row.get("display_unresolved_codes", 0),
                "occurrences": row.get("display_unresolved_occurrences", 0),
                "report": row.get("report"),
            }
            for row in rows
            if int(row.get("display_unresolved_codes") or 0) > 0
        ],
        "search_fallback_missing_dictionaries": [
            {
                "dict_id": row["dict_id"],
                "codes": row.get("search_fallback_missing_codes", 0),
                "occurrences": row.get("search_fallback_missing_occurrences", 0),
                "report": row.get("report"),
            }
            for row in rows
            if int(row.get("search_fallback_missing_codes") or 0) > 0
        ],
        "rows": rows,
    }


def write_csv_report(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "dict_id",
        "dict_title",
        "readiness_status",
        "raw_occurrences",
        "raw_distinct_codes",
        "display_unresolved_codes",
        "display_unresolved_occurrences",
        "formatting_helper_candidate_codes",
        "formatting_helper_candidate_occurrences",
        "search_fallback_missing_codes",
        "search_fallback_missing_occurrences",
        "mapping_codes",
        "bitmap_codes",
        "image_codes",
        "scan_errors",
        "report",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_markdown_report(path: Path, summary: dict[str, Any]) -> None:
    rows = summary["rows"]
    lines = [
        "# Gaiji Readiness",
        "",
        f"Dictionaries: {summary['total']}",
        "",
        "## Status Counts",
        "",
        "| Status | Dictionaries |",
        "| --- | ---: |",
    ]
    for status, count in summary["readiness_status_counts"].items():
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Bucket Occurrences",
            "",
            "| Bucket | Occurrences | Distinct dictionary-local codes |",
            "| --- | ---: | ---: |",
        ]
    )
    for bucket, occurrences in summary["bucket_occurrence_counts"].items():
        distinct = summary["bucket_distinct_counts"].get(bucket, 0)
        lines.append(f"| {bucket} | {occurrences} | {distinct} |")
    lines.extend(
        [
            "",
            "## Per-Dictionary Summary",
            "",
            "| Dict | Status | Raw occ. | Raw codes | Display unresolved | Formatting helpers | Search fallback missing |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["dict_id"]),
                    str(row.get("readiness_status", "")),
                    str(row.get("raw_occurrences", 0)),
                    str(row.get("raw_distinct_codes", 0)),
                    f"{row.get('display_unresolved_codes', 0)} / {row.get('display_unresolved_occurrences', 0)}",
                    f"{row.get('formatting_helper_candidate_codes', 0)} / {row.get('formatting_helper_candidate_occurrences', 0)}",
                    f"{row.get('search_fallback_missing_codes', 0)} / {row.get('search_fallback_missing_occurrences', 0)}",
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_gaiji_readiness_for_args(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    targets = discover_profile_targets(args.root or [Path(".")], jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        targets = [target for target in targets if target.dict_id in selected or target.idx.stem in selected]
    task_args = worker_args(args)

    def log_summary(row: dict[str, Any]) -> None:
        print(
            f"{row['dict_id']:12s} gaiji={row.get('raw_distinct_codes', 0):4d} "
            f"status={row.get('readiness_status', 'unknown'):7s} "
            f"display_unresolved={row.get('display_unresolved_codes', 0):4d} "
            f"helpers={row.get('formatting_helper_candidate_codes', 0):4d} "
            f"fallback_missing={row.get('search_fallback_missing_codes', 0):4d}",
            file=sys.stderr,
        )

    rows = parallel_map_ordered(
        _gaiji_readiness_task,
        [(target, args.out_dir, task_args) for target in targets],
        jobs=getattr(args, "jobs", 1),
        on_result=log_summary,
    )
    summary = aggregate_summary(rows)
    write_json(args.out_dir / "summary.json", summary)
    write_csv_report(args.out_dir / "gaiji_readiness.csv", rows)
    write_markdown_report(args.out_dir / "gaiji_readiness.md", summary)
    return summary
