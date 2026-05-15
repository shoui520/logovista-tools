"""Redacted corpus profiles for SSED dictionary packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import dict_id_for_idx
from .entries import ENTRY_MARKER, iter_entry_slices_with_boundaries
from .gaiji import is_bitmap_gaiji_resource_name, load_gaiji_profile
from .indexes import INDEX_TYPES, scan_index_component
from .model_types import BodySource, ComponentRole
from .parallel import parallel_map_ordered, worker_args
from .resources import load_image_resource_profile
from .spans import LosslessDecodeError, combine_span_stats, decode_lossless_spans
from .ssed import (
    BLOCK_SIZE,
    SsedInfoElement,
    expand_sseddata_file,
    expand_sseddata_file_with_storage,
    find_case_insensitive,
    honmon_component,
    is_honmon_component,
    parse_sseddata_header,
    parse_ssedinfo,
    sseddata_storage_for_file,
)
from .titles import TITLE_TYPES
from .windows import discover_numeric_aux_indexes, discover_renderer_sidecars, load_exinfo_for_idx


COLSCR_TYPE = 0xD2
PCMDATA_TYPE = 0xD8
MENU_TYPE = 0x01


@dataclass(frozen=True)
class ProfileTarget:
    dict_id: str
    idx: Path
    title: str
    elements: list[SsedInfoElement]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_profile_targets(roots: list[Path], *, jobs: int | None = 1) -> list[ProfileTarget]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix.upper() == ".IDX":
            candidates.append(root)
        elif root.is_dir():
            for dirpath, _dirnames, filenames in os.walk(root):
                base = Path(dirpath)
                candidates.extend(base / name for name in filenames if name.lower().endswith(".idx"))
    unique_candidates: list[Path] = []
    for idx in sorted(candidates):
        resolved = idx.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)
    rows = parallel_map_ordered(_profile_target_from_idx, unique_candidates, jobs=jobs)
    return [row for row in rows if row is not None]


def _profile_target_from_idx(idx: Path) -> ProfileTarget | None:
    try:
        title, elements = parse_ssedinfo(idx)
    except Exception:
        return None
    return ProfileTarget(dict_id=dict_id_for_idx(idx), idx=idx, title=title, elements=elements)


def component_role(element: SsedInfoElement) -> str:
    upper = element.filename.upper()
    if is_honmon_component(element):
        return ComponentRole.HONMON.value
    if element.type in TITLE_TYPES:
        return ComponentRole.TITLE.value
    if element.type in INDEX_TYPES:
        return ComponentRole.INDEX.value
    if upper == "MENU.DIC" or element.type == MENU_TYPE:
        return ComponentRole.MENU.value
    if element.type == 0xFF or upper.startswith("MULTI"):
        return ComponentRole.MULTI_DESCRIPTOR.value
    if element.type in {0x02, 0x20, 0x28} or upper in {"RIGHT.DIC", "TOC.DIC", "IDXJUMP.DIC"}:
        return ComponentRole.TEXT.value
    if upper == "COLSCR.DIC" or element.type == COLSCR_TYPE:
        return ComponentRole.COLSCR.value
    if upper == "PCMDATA.DIC" or element.type == PCMDATA_TYPE:
        return ComponentRole.PCMDATA.value
    if is_bitmap_gaiji_resource_name(upper):
        return ComponentRole.GAIJI_BITMAP.value
    return ComponentRole.COMPONENT.value


def safe_relative(path: Path, roots: list[Path]) -> str:
    resolved = path.resolve()
    for root in roots:
        try:
            return str(resolved.relative_to(root.resolve()))
        except ValueError:
            continue
    return path.name


def dictlist_flags(idx: Path) -> dict[str, Any]:
    paths = [idx.parent / "DictList.plist", idx.parent.parent / "DictList.plist"]
    flags = {
        "paths": [],
        "dictfulldb": False,
        "dictftsdb": False,
        "items": 0,
    }
    for path in paths:
        if not path.exists():
            continue
        flags["paths"].append(path.name)
        try:
            data = plistlib.load(path.open("rb"))
        except Exception:
            continue
        items = data.get("ItemArray") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        flags["items"] += len(items)
        for item in items:
            if not isinstance(item, dict):
                continue
            flags["dictfulldb"] = flags["dictfulldb"] or bool(item.get("DictFULLDB"))
            flags["dictftsdb"] = flags["dictftsdb"] or bool(item.get("DictFtsDB"))
    return flags


def component_profile(
    idx: Path,
    element: SsedInfoElement,
    *,
    hash_files: bool,
) -> dict[str, Any]:
    path = find_case_insensitive(idx.parent, element.filename)
    row: dict[str, Any] = {
        "index": element.index,
        "filename": element.filename,
        "role": component_role(element),
        "type": f"{element.type:02x}",
        "multi": f"{element.multi:02x}",
        "start_block": element.start,
        "end_block": element.end,
        "block_count": element.block_count if element.start else 0,
        "data_flags": element.data.hex(),
        "present": path is not None,
    }
    if path is None:
        return row
    row["size"] = path.stat().st_size
    if hash_files:
        row["sha256"] = sha256_file(path)
    if path.suffix.upper() in {".DIC", ""}:
        try:
            row["storage"] = sseddata_storage_for_file(path)
            row["ssed_header"] = {
                key: (value.hex() if isinstance(value, bytes) else value)
                for key, value in parse_sseddata_header(path).items()
            }
        except Exception as exc:
            row["storage_error"] = str(exc)
    return row


def classify_honmon_shape(
    *,
    expanded_size: int,
    marker_count: int,
    index_boundaries: int,
    sampled_slices: int,
    issue_counts: dict[str, int],
) -> str:
    if marker_count and marker_count * 64 > expanded_size:
        return "dense_marker_table"
    if sampled_slices:
        return "body_stream_indexed" if index_boundaries else "body_stream_marker_sliced"
    if index_boundaries:
        return "index_targets_without_sampled_body"
    if marker_count:
        return "marker_table_without_sampled_body"
    if issue_counts:
        return "opaque_or_binary_honmon"
    return "unknown"


def scan_indexes_for_profile(
    target: ProfileTarget,
    *,
    honmon_start_block: int | None = None,
    expanded_size: int | None = None,
) -> tuple[dict[str, Any], set[int], str | None]:
    gaiji_profile = load_gaiji_profile(target.idx)
    components: list[dict[str, Any]] = []
    boundary_offsets: set[int] = set()
    first_error: str | None = None
    aggregate: dict[str, int] = {
        "components": 0,
        "present_components": 0,
        "expand_errors": 0,
        "internal_pages": 0,
        "leaf_pages": 0,
        "internal_rows": 0,
        "leaf_rows": 0,
        "search_groups": 0,
        "unknown_leaf_bytes": 0,
    }
    for element in target.elements:
        if element.type not in INDEX_TYPES:
            continue
        aggregate["components"] += 1
        source = find_case_insensitive(target.idx.parent, element.filename)
        row: dict[str, Any] = {
            "filename": element.filename,
            "type": f"{element.type:02x}",
            "start_block": element.start,
            "present": source is not None,
        }
        if source is None:
            components.append(row)
            continue
        aggregate["present_components"] += 1
        try:
            expanded = expand_sseddata_file(source)

            def collect(row_data: dict[str, Any]) -> None:
                if honmon_start_block is None or expanded_size is None:
                    return
                if row_data.get("kind") != "leaf":
                    return
                body = row_data.get("body")
                if not isinstance(body, dict):
                    return
                block = body.get("block")
                offset = body.get("offset")
                if not isinstance(block, int) or not isinstance(offset, int):
                    return
                relative = (block - honmon_start_block) * BLOCK_SIZE + offset
                if 0 <= relative < expanded_size:
                    boundary_offsets.add(relative)

            scanned = scan_index_component(
                element.filename,
                element.type,
                expanded,
                element.start,
                gaiji="h-placeholder",
                gaiji_map=gaiji_profile.map,
                emit_row=collect if honmon_start_block is not None and expanded_size is not None else None,
            )
            scanned.data_flags = element.data.hex()
            row.update(scanned.as_dict())
            for key in (
                "internal_pages",
                "leaf_pages",
                "internal_rows",
                "leaf_rows",
                "search_groups",
                "unknown_leaf_bytes",
            ):
                aggregate[key] += int(row.get(key, 0) or 0)
        except Exception as exc:
            aggregate["expand_errors"] += 1
            row["error"] = str(exc)
            if first_error is None:
                first_error = str(exc)
        components.append(row)
    return {"aggregate": aggregate, "components": components}, boundary_offsets, first_error


def skipped_indexes_for_profile(target: ProfileTarget) -> tuple[dict[str, Any], set[int], str | None]:
    components: list[dict[str, Any]] = []
    aggregate: dict[str, int] = {
        "components": 0,
        "present_components": 0,
        "expand_errors": 0,
        "internal_pages": 0,
        "leaf_pages": 0,
        "internal_rows": 0,
        "leaf_rows": 0,
        "search_groups": 0,
        "unknown_leaf_bytes": 0,
        "scan_skipped": 1,
    }
    for element in target.elements:
        if element.type not in INDEX_TYPES:
            continue
        aggregate["components"] += 1
        source = find_case_insensitive(target.idx.parent, element.filename)
        if source is not None:
            aggregate["present_components"] += 1
        components.append(
            {
                "filename": element.filename,
                "type": f"{element.type:02x}",
                "start_block": element.start,
                "present": source is not None,
                "status": "not_scanned",
                "reason": "profile index scan skipped",
            }
        )
    return {"aggregate": aggregate, "components": components}, set(), None


def honmon_profile(target: ProfileTarget, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    skip_index_scan = bool(getattr(args, "skip_index_scan", False))
    honmon_element = honmon_component(target.elements)
    if honmon_element is None:
        if skip_index_scan:
            indexes, _boundaries, _index_error = skipped_indexes_for_profile(target)
        else:
            indexes, _boundaries, _index_error = scan_indexes_for_profile(target)
        return {"present": False, "status": "no_honmon_component"}, indexes
    honmon_path = find_case_insensitive(target.idx.parent, honmon_element.filename)
    if honmon_path is None:
        if skip_index_scan:
            indexes, _boundaries, _index_error = skipped_indexes_for_profile(target)
        else:
            indexes, _boundaries, _index_error = scan_indexes_for_profile(target)
        return {"present": False, "status": "missing_honmon_file"}, indexes

    row: dict[str, Any] = {
        "present": True,
        "status": "ok",
        "start_block": honmon_element.start,
    }
    try:
        expanded, storage = expand_sseddata_file_with_storage(honmon_path)
    except Exception as exc:
        row.update({"status": "expand_error", "error": str(exc)})
        indexes, _boundaries, _index_error = scan_indexes_for_profile(target)
        return row, indexes

    marker_count = expanded.count(ENTRY_MARKER)
    if skip_index_scan:
        indexes, index_boundaries, index_error = skipped_indexes_for_profile(target)
        index_error = "profile_index_scan_skipped"
    else:
        indexes, index_boundaries, index_error = scan_indexes_for_profile(
            target,
            honmon_start_block=honmon_element.start,
            expanded_size=len(expanded),
        )

    gaiji_profile = load_gaiji_profile(target.idx)
    image_profile = load_image_resource_profile(target.idx)
    samples = []
    strict_failures = 0
    issue_samples = []
    span_results = []
    max_slices = max(0, args.max_slices)
    for entry_index, (start, end) in enumerate(
        iter_entry_slices_with_boundaries(expanded, index_boundaries),
        start=1,
    ):
        if max_slices and len(samples) >= max_slices:
            break
        segment = expanded[start:end]
        try:
            decoded = decode_lossless_spans(
                segment,
                gaiji_map=gaiji_profile.map,
                image_gaiji_keys=image_profile.gaiji_image_keys,
                mode=args.parse_mode,
                include_padding=False,
            )
        except LosslessDecodeError as exc:
            strict_failures += 1
            decoded = decode_lossless_spans(
                segment,
                gaiji_map=gaiji_profile.map,
                image_gaiji_keys=image_profile.gaiji_image_keys,
                mode="forensic",
                include_padding=False,
            )
            issue_samples.append(
                {
                    "entry_index": entry_index,
                    "offset": start,
                    "absolute_block": honmon_element.start + start // BLOCK_SIZE,
                    "block_offset": start % BLOCK_SIZE,
                    "strict_error": str(exc),
                }
            )
        span_results.append(decoded)
        samples.append(
            {
                "entry_index": entry_index,
                "offset": start,
                "length": end - start,
                "absolute_block": honmon_element.start + start // BLOCK_SIZE,
                "block_offset": start % BLOCK_SIZE,
                "stats": decoded.stats,
                "issue_counts": decoded.issue_counts,
                "control_ops": decoded.control_ops,
                "unknown_control_ops": decoded.unknown_control_ops,
            }
        )
        for issue in decoded.issues:
            if len(issue_samples) >= args.max_issue_samples:
                break
            issue_samples.append(
                {
                    "entry_index": entry_index,
                    "offset": start + issue.offset,
                    "absolute_block": honmon_element.start + (start + issue.offset) // BLOCK_SIZE,
                    "block_offset": (start + issue.offset) % BLOCK_SIZE,
                    "kind": issue.kind,
                    "raw_hex": issue.raw_hex,
                    "message": issue.message,
                }
            )

    aggregate = combine_span_stats(span_results)
    row.update(
        {
            "storage": storage,
            "expanded_bytes": len(expanded),
            "entry_markers": marker_count,
            "index_boundary_offsets": len(index_boundaries),
            "index_error": index_error,
            "sampled_slices": len(samples),
            "strict_failures": strict_failures,
            "shape": classify_honmon_shape(
                expanded_size=len(expanded),
                marker_count=marker_count,
                index_boundaries=len(index_boundaries),
                sampled_slices=len(samples),
                issue_counts=aggregate["issue_counts"],
            ),
            "decode_aggregate": aggregate,
            "sample_metrics": samples,
            "issue_samples": issue_samples[: args.max_issue_samples],
        }
    )
    return row, indexes


def build_profile(target: ProfileTarget, roots: list[Path], args: argparse.Namespace) -> dict[str, Any]:
    components = [component_profile(target.idx, element, hash_files=args.hash_files) for element in target.elements]
    role_counts = Counter(row["role"] for row in components)
    type_counts = Counter(row["type"] for row in components)
    missing = [
        row["filename"]
        for row in components
        if not row["present"] and int(row.get("block_count") or 0) > 0
    ]
    gaiji_profile = load_gaiji_profile(target.idx)
    image_profile = load_image_resource_profile(target.idx)
    exinfo = load_exinfo_for_idx(target.idx)
    renderer_sidecars = discover_renderer_sidecars(target.idx)
    numeric_aux = discover_numeric_aux_indexes(target.idx)
    honmon, indexes = honmon_profile(target, args)

    body_source_hint = BodySource.UNKNOWN.value
    if honmon.get("shape") in {"body_stream_indexed", "body_stream_marker_sliced"}:
        body_source_hint = BodySource.HONMON.value
    elif honmon.get("shape") == "dense_marker_table":
        body_source_hint = BodySource.HONMON_ANCHOR_DEREFERENCE.value
    elif not honmon.get("present"):
        body_source_hint = BodySource.NONE.value

    return {
        "schema": "logovista-profile-v1",
        "package_family": "ssed",
        "dict_id": target.dict_id,
        "title": target.title,
        "idx": safe_relative(target.idx, roots),
        "package_dir": safe_relative(target.idx.parent, roots),
        "classification": {
            "status": "incomplete" if missing else "ok",
            "body_source_hint": body_source_hint,
            "honmon_shape": honmon.get("shape"),
            "missing_components": missing,
        },
        "catalog": {
            "component_count": len(target.elements),
            "role_counts": dict(sorted(role_counts.items())),
            "type_counts": dict(sorted(type_counts.items())),
            "components": components,
        },
        "honmon": honmon,
        "indexes": indexes,
        "gaiji": {
            "merged_map_entries": len(gaiji_profile.map),
            "uni_entries": gaiji_profile.uni_entries,
            "plist_entries": gaiji_profile.plist_entries,
        },
        "resources": {
            "image_dirs": [safe_relative(path, roots) for path in image_profile.image_dirs],
            "image_resource_entries": len(image_profile.resources),
            "image_gaiji_entries": len(image_profile.gaiji_image_keys),
        },
        "wrappers": {
            "dictlist": dictlist_flags(target.idx),
            "exinfo": safe_relative(exinfo.path, roots) if exinfo else None,
            "numeric_aux_indexes": [safe_relative(path, roots) for path in numeric_aux],
            "renderer_sidecars": [
                {
                    "path": safe_relative(sidecar.path, roots),
                    "storage": sidecar.storage,
                }
                for sidecar in renderer_sidecars
            ],
        },
    }


def _profile_task(payload: tuple[ProfileTarget, list[Path], Path, argparse.Namespace]) -> dict[str, Any]:
    target, roots, out_dir, args = payload
    profile = build_profile(target, roots, args)
    dict_out = out_dir / target.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    (dict_out / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "dict_id": profile["dict_id"],
        "title": profile["title"],
        "profile": str(dict_out / "profile.json"),
        "status": profile["classification"]["status"],
        "body_source_hint": profile["classification"]["body_source_hint"],
        "honmon_shape": profile["classification"]["honmon_shape"],
        "expanded_bytes": profile["honmon"].get("expanded_bytes", 0),
        "entry_markers": profile["honmon"].get("entry_markers", 0),
        "index_boundary_offsets": profile["honmon"].get("index_boundary_offsets", 0),
        "unknown_controls": profile["honmon"].get("decode_aggregate", {}).get("stats", {}).get("unknown_controls", 0),
        "unknown_bytes": profile["honmon"].get("decode_aggregate", {}).get("stats", {}).get("unknown_bytes", 0),
        "index_unknown_leaf_bytes": profile["indexes"].get("aggregate", {}).get("unknown_leaf_bytes", 0),
        "strict_failures": profile["honmon"].get("strict_failures", 0),
    }


def corpus_profile_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(row.get("status") or "unknown" for row in rows)
    shapes = Counter(row.get("honmon_shape") or "none" for row in rows)
    body_sources = Counter(row.get("body_source_hint") or "unknown" for row in rows)
    totals = {
        "expanded_bytes": sum(int(row.get("expanded_bytes", 0) or 0) for row in rows),
        "entry_markers": sum(int(row.get("entry_markers", 0) or 0) for row in rows),
        "index_boundary_offsets": sum(int(row.get("index_boundary_offsets", 0) or 0) for row in rows),
        "unknown_controls": sum(int(row.get("unknown_controls", 0) or 0) for row in rows),
        "unknown_bytes": sum(int(row.get("unknown_bytes", 0) or 0) for row in rows),
        "index_unknown_leaf_bytes": sum(int(row.get("index_unknown_leaf_bytes", 0) or 0) for row in rows),
        "strict_failures": sum(int(row.get("strict_failures", 0) or 0) for row in rows),
    }
    hot_unknown_controls = sorted(
        (
            {
                "dict_id": row.get("dict_id"),
                "honmon_shape": row.get("honmon_shape"),
                "unknown_controls": row.get("unknown_controls", 0),
                "unknown_bytes": row.get("unknown_bytes", 0),
                "profile": row.get("profile"),
            }
            for row in rows
            if row.get("unknown_controls") or row.get("unknown_bytes")
        ),
        key=lambda row: (int(row.get("unknown_controls", 0) or 0), int(row.get("unknown_bytes", 0) or 0)),
        reverse=True,
    )
    hot_index_unknowns = sorted(
        (
            {
                "dict_id": row.get("dict_id"),
                "index_unknown_leaf_bytes": row.get("index_unknown_leaf_bytes", 0),
                "profile": row.get("profile"),
            }
            for row in rows
            if row.get("index_unknown_leaf_bytes")
        ),
        key=lambda row: int(row.get("index_unknown_leaf_bytes", 0) or 0),
        reverse=True,
    )
    return {
        "schema": "logovista-corpus-profile-summary-v1",
        "total": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "honmon_shapes": dict(sorted(shapes.items())),
        "body_source_hints": dict(sorted(body_sources.items())),
        "totals": totals,
        "profiles": rows,
        "hotspots": {
            "unknown_text_controls_or_bytes": hot_unknown_controls[:50],
            "unknown_index_leaf_bytes": hot_index_unknowns[:50],
        },
    }


def extract_profiles_for_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    roots = args.root or [Path(".")]
    targets = discover_profile_targets(roots, jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        targets = [target for target in targets if target.dict_id in selected or target.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)
    summaries = parallel_map_ordered(
        _profile_task,
        [(target, roots, args.out_dir, task_args) for target in targets],
        jobs=getattr(args, "jobs", 1),
    )
    (args.out_dir / "summary.json").write_text(
        json.dumps(corpus_profile_summary(summaries), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summaries
