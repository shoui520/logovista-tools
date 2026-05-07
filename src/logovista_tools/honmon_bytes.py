"""Full-stream HONMON byte accounting for SSED dictionaries."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .entries import ENTRY_MARKER
from .gaiji import load_gaiji_profile
from .parallel import parallel_map_ordered, worker_args
from .profiles import ProfileTarget, discover_profile_targets, safe_relative
from .resources import load_image_resource_profile
from .spans import LosslessDecodeError, decode_lossless_spans
from .ssed import BLOCK_SIZE, expand_sseddata_file_with_storage, find_case_insensitive


def honmon_byte_shape(*, expanded_size: int, marker_count: int, stats: dict[str, int]) -> str:
    """Classify the expanded HONMON at byte-stream level.

    This intentionally does not infer the authoritative body source. It only
    describes the bytes seen in HONMON itself.
    """

    if expanded_size == 0:
        return "empty"
    if marker_count and marker_count * 64 > expanded_size:
        return "dense_marker_table"
    if marker_count:
        return "marker_rich_text_stream"
    if stats.get("controls") or stats.get("jis_pairs") or stats.get("gaiji"):
        return "text_stream_without_entry_markers"
    if stats.get("padding_bytes") == expanded_size:
        return "all_padding"
    if stats.get("unknown_bytes") or stats.get("unknown_controls"):
        return "opaque_or_binary"
    return "unclassified"


def issue_to_address(issue: Any, start_block: int) -> dict[str, Any]:
    return {
        "kind": issue.kind,
        "offset": issue.offset,
        "absolute_block": start_block + issue.offset // BLOCK_SIZE,
        "block_offset": issue.offset % BLOCK_SIZE,
        "length": issue.length,
        "raw_hex": issue.raw_hex,
        "message": issue.message,
    }


def _target_honmon_element(target: ProfileTarget) -> Any | None:
    return next((element for element in target.elements if element.filename.upper() == "HONMON.DIC"), None)


def scan_honmon_bytes(target: ProfileTarget, roots: list[Path], args: argparse.Namespace) -> dict[str, Any]:
    honmon_element = _target_honmon_element(target)
    dict_out = args.out_dir / target.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)

    row: dict[str, Any] = {
        "schema": "logovista-honmon-byte-scan-v1",
        "dict_id": target.dict_id,
        "title": target.title,
        "idx": safe_relative(target.idx, roots),
        "package_dir": safe_relative(target.idx.parent, roots),
        "status": "ok",
        "honmon": {
            "present": honmon_element is not None,
        },
    }
    if honmon_element is None:
        row["status"] = "no_honmon_component"
        (dict_out / "honmon_bytes.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return row

    row["honmon"].update(
        {
            "filename": honmon_element.filename,
            "start_block": honmon_element.start,
            "end_block": honmon_element.end,
            "declared_blocks": honmon_element.block_count if honmon_element.start else 0,
        }
    )
    honmon_path = find_case_insensitive(target.idx.parent, honmon_element.filename)
    if honmon_path is None:
        row["status"] = "missing_honmon_file"
        (dict_out / "honmon_bytes.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return row

    try:
        expanded, storage = expand_sseddata_file_with_storage(honmon_path)
    except Exception as exc:
        row["status"] = "expand_error"
        row["error"] = str(exc)
        (dict_out / "honmon_bytes.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return row

    gaiji_profile = load_gaiji_profile(target.idx)
    image_profile = load_image_resource_profile(target.idx)
    strict_error = None
    try:
        decoded = decode_lossless_spans(
            expanded,
            gaiji_map=gaiji_profile.map,
            image_gaiji_keys=image_profile.gaiji_image_keys,
            mode=args.parse_mode,
            include_padding=False,
            collect_spans=False,
            max_issues=args.max_issue_samples,
        )
    except LosslessDecodeError as exc:
        strict_error = str(exc)
        decoded = decode_lossless_spans(
            expanded,
            gaiji_map=gaiji_profile.map,
            image_gaiji_keys=image_profile.gaiji_image_keys,
            mode="forensic",
            include_padding=False,
            collect_spans=False,
            max_issues=args.max_issue_samples,
        )

    stats = decoded.stats
    marker_count = expanded.count(ENTRY_MARKER)
    bytes_total = int(stats.get("bytes_total", 0))
    bytes_covered = int(stats.get("bytes_covered", 0))
    row["honmon"].update(
        {
            "path": safe_relative(honmon_path, roots),
            "storage": storage,
            "expanded_bytes": len(expanded),
            "entry_markers": marker_count,
            "byte_shape": honmon_byte_shape(
                expanded_size=len(expanded),
                marker_count=marker_count,
                stats=stats,
            ),
        }
    )
    row["coverage"] = {
        "bytes_total": bytes_total,
        "bytes_covered": bytes_covered,
        "uncovered_bytes": max(0, bytes_total - bytes_covered),
        "classified_without_issues": (
            bytes_total == bytes_covered
            and not decoded.issue_counts
            and not strict_error
        ),
    }
    row["decode"] = decoded.as_dict(include_spans=False, max_issues=args.max_issue_samples)
    row["decode"]["issues"] = [
        issue_to_address(issue, honmon_element.start)
        for issue in decoded.issues[: args.max_issue_samples]
    ]
    if strict_error is not None:
        row["strict_error"] = strict_error

    (dict_out / "honmon_bytes.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return row


def _scan_task(payload: tuple[ProfileTarget, list[Path], argparse.Namespace]) -> dict[str, Any]:
    target, roots, args = payload
    return scan_honmon_bytes(target, roots, args)


def summary_row(row: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    stats = row.get("decode", {}).get("stats", {})
    issue_counts = row.get("decode", {}).get("issue_counts", {})
    honmon = row.get("honmon", {})
    coverage = row.get("coverage", {})
    return {
        "dict_id": row.get("dict_id"),
        "title": row.get("title"),
        "status": row.get("status"),
        "profile": str(out_dir / str(row.get("dict_id")) / "honmon_bytes.json"),
        "byte_shape": honmon.get("byte_shape"),
        "storage": honmon.get("storage"),
        "expanded_bytes": honmon.get("expanded_bytes", 0),
        "entry_markers": honmon.get("entry_markers", 0),
        "bytes_covered": coverage.get("bytes_covered", 0),
        "uncovered_bytes": coverage.get("uncovered_bytes", 0),
        "classified_without_issues": coverage.get("classified_without_issues"),
        "unknown_controls": stats.get("unknown_controls", 0),
        "unknown_bytes": stats.get("unknown_bytes", 0),
        "invalid_jis_pairs": stats.get("invalid_jis_pairs", 0),
        "truncated_controls": stats.get("truncated_controls", 0),
        "truncated_gaiji": stats.get("truncated_gaiji", 0),
        "gaiji_unresolved": stats.get("gaiji_unresolved", 0),
        "issue_counts": issue_counts,
        "strict_error": row.get("strict_error"),
    }


def corpus_honmon_byte_summary(rows: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    summaries = [summary_row(row, out_dir) for row in rows]
    statuses = Counter(str(row.get("status")) for row in summaries)
    shapes = Counter(str(row.get("byte_shape") or "none") for row in summaries)
    storage_modes = Counter(str(row.get("storage") or "none") for row in summaries)
    totals = Counter()
    control_ops = Counter()
    unknown_control_ops = Counter()
    issue_counts = Counter()
    for row in rows:
        decode = row.get("decode", {})
        for key, value in decode.get("stats", {}).items():
            totals[key] += int(value or 0)
        totals["uncovered_bytes"] += int(row.get("coverage", {}).get("uncovered_bytes", 0) or 0)
        totals["entry_markers"] += int(row.get("honmon", {}).get("entry_markers", 0) or 0)
        for key, value in decode.get("control_ops", {}).items():
            control_ops[key] += int(value or 0)
        for key, value in decode.get("unknown_control_ops", {}).items():
            unknown_control_ops[key] += int(value or 0)
        for key, value in decode.get("issue_counts", {}).items():
            issue_counts[key] += int(value or 0)

    hotspots = sorted(
        (
            row
            for row in summaries
            if row.get("uncovered_bytes")
            or row.get("unknown_controls")
            or row.get("unknown_bytes")
            or row.get("invalid_jis_pairs")
            or row.get("truncated_controls")
            or row.get("truncated_gaiji")
            or row.get("strict_error")
        ),
        key=lambda row: (
            int(row.get("uncovered_bytes", 0) or 0),
            int(row.get("unknown_controls", 0) or 0),
            int(row.get("unknown_bytes", 0) or 0),
            int(row.get("invalid_jis_pairs", 0) or 0),
            int(row.get("truncated_controls", 0) or 0),
            int(row.get("truncated_gaiji", 0) or 0),
        ),
        reverse=True,
    )
    unresolved_gaiji = sorted(
        (
            {
                "dict_id": row.get("dict_id"),
                "gaiji_unresolved": row.get("gaiji_unresolved", 0),
                "profile": row.get("profile"),
            }
            for row in summaries
            if row.get("gaiji_unresolved")
        ),
        key=lambda row: int(row.get("gaiji_unresolved", 0) or 0),
        reverse=True,
    )
    return {
        "schema": "logovista-corpus-honmon-byte-summary-v1",
        "total": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "byte_shapes": dict(sorted(shapes.items())),
        "storage_modes": dict(sorted(storage_modes.items())),
        "totals": dict(sorted(totals.items())),
        "control_ops": dict(sorted(control_ops.items())),
        "unknown_control_ops": dict(sorted(unknown_control_ops.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "profiles": summaries,
        "hotspots": {
            "byte_or_control_issues": hotspots[:50],
            "unresolved_gaiji": unresolved_gaiji[:50],
        },
    }


def extract_honmon_byte_reports_for_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    roots = args.root or [Path(".")]
    targets = discover_profile_targets(roots, jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        targets = [target for target in targets if target.dict_id in selected or target.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)
    rows = parallel_map_ordered(
        _scan_task,
        [(target, roots, task_args) for target in targets],
        jobs=getattr(args, "jobs", 1),
    )
    (args.out_dir / "summary.json").write_text(
        json.dumps(corpus_honmon_byte_summary(rows, args.out_dir), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [summary_row(row, args.out_dir) for row in rows]
