"""Audit raw HONMON/IDX readability across LogoVista dictionaries."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import (
    ENTRY_MARKER,
    decode_tokens,
    extract_heading,
    is_useless_body,
    iter_entry_slices_with_boundaries,
    tokens_to_text,
)
from .gaiji import load_gaiji_profile
from .indexes import collect_index_body_offsets_for_idx
from .rendererdb import discover_android_body_databases
from .ssed import (
    BLOCK_SIZE,
    SsedInfoElement,
    expand_sseddata_file,
    expand_sseddata_file_with_storage,
    find_case_insensitive,
    parse_ssedinfo,
)
from .titles import TITLE_TYPES
from .windows import discover_renderer_sidecars, load_exinfo_for_idx


ID_RE = re.compile(r"\d{1,12}")
DBC_GLOB = "*.dbc"


@dataclass(frozen=True)
class AuditSource:
    dict_id: str
    idx: Path
    title: str
    elements: list[SsedInfoElement]


def dict_id_for_idx(idx: Path) -> str:
    return idx.parent.parent.name if idx.parent.name == idx.parent.parent.name else idx.stem


def candidate_dictlist_paths_for_idx(idx: Path) -> list[Path]:
    return [
        idx.parent / "DictList.plist",
        idx.parent.parent / "DictList.plist",
    ]


def discover_audit_sources(roots: list[Path]) -> list[AuditSource]:
    sources: list[AuditSource] = []
    seen: set[Path] = set()
    for root in roots:
        candidates: list[Path] = []
        if root.is_file() and root.suffix.upper() == ".IDX":
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(root.rglob("*.IDX"))
            candidates.extend(root.rglob("*.idx"))
        for idx in sorted(candidates):
            idx = idx.resolve()
            if idx in seen:
                continue
            seen.add(idx)
            try:
                title, elements = parse_ssedinfo(idx)
            except Exception:
                continue
            sources.append(AuditSource(dict_id_for_idx(idx), idx, title, elements))
    return sources


def dictlist_summary(source: AuditSource) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for plist_path in candidate_dictlist_paths_for_idx(source.idx):
        if not plist_path.exists():
            continue
        try:
            data = plistlib.load(plist_path.open("rb"))
        except Exception:
            continue
        items = data.get("ItemArray") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            row = {
                key: item[key]
                for key in ("DictID", "DictNAME", "DictFULLDB", "DictFtsDB")
                if isinstance(item.get(key), str) and item.get(key)
            }
            if row:
                rows.append(row)
    return rows


def has_dictfulldb(dictlist: list[dict[str, str]]) -> bool:
    return any("DictFULLDB" in row for row in dictlist)


def has_dictftsdb(dictlist: list[dict[str, str]]) -> bool:
    return any("DictFtsDB" in row for row in dictlist)


def count_honmon_id_records(
    expanded: bytes,
    gaiji_map: dict[str, str],
    *,
    max_records: int,
) -> tuple[int, int, list[dict[str, int]]]:
    count = 0
    scanned = 0
    samples: list[dict[str, int]] = []
    for record_index, start in enumerate(range(0, len(expanded), 32)):
        if max_records and scanned >= max_records:
            break
        scanned += 1
        record = expanded[start : start + 32]
        tokens, _stats = decode_tokens(record, gaiji="drop", gaiji_map=gaiji_map)
        text = tokens_to_text(tokens).strip()
        if not ID_RE.fullmatch(text):
            continue
        count += 1
        if len(samples) < 5:
            samples.append({"record_index": record_index, "offset": start, "id": int(text)})
    return count, scanned, samples


def title_probe(
    source: AuditSource,
    gaiji_map: dict[str, str],
    *,
    sample_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    components: list[dict[str, Any]] = []
    samples: list[dict[str, str]] = []
    for element in source.elements:
        if element.type not in TITLE_TYPES or not element.start:
            continue
        path = find_case_insensitive(source.idx.parent, element.filename)
        if path is None:
            continue
        expanded = expand_sseddata_file(path)
        probe = expanded[: min(len(expanded), 131072)]
        tokens, stats = decode_tokens(probe, gaiji="h-placeholder", gaiji_map=gaiji_map)
        lines = [line.strip() for line in tokens_to_text(tokens).splitlines() if line.strip()]
        components.append(
            {
                "component": element.filename,
                "type": f"{element.type:02x}",
                "expanded_bytes": len(expanded),
                "sample_lines_in_first_128k": len(lines),
                "unknown_controls": stats["unknown_controls"],
            }
        )
        for line in lines[: max(0, sample_limit - len(samples))]:
            samples.append({"component": element.filename, "text": line[:160]})
    return components, samples


def sample_honmon_bodies(
    expanded: bytes,
    boundary_offsets: Iterable[int],
    gaiji_map: dict[str, str],
    *,
    sample_limit: int,
    max_slices: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    samples: list[dict[str, Any]] = []
    stats = {
        "slices_seen": 0,
        "skipped_useless": 0,
        "unknown_controls": 0,
        "jis_pairs": 0,
    }
    for entry_index, (start, end) in enumerate(
        iter_entry_slices_with_boundaries(expanded, boundary_offsets),
        start=1,
    ):
        if stats["slices_seen"] >= max_slices or len(samples) >= sample_limit:
            break
        stats["slices_seen"] += 1
        tokens, token_stats = decode_tokens(
            expanded[start:end],
            gaiji="h-placeholder",
            gaiji_map=gaiji_map,
            preserve_sections=True,
        )
        stats["unknown_controls"] += token_stats["unknown_controls"]
        stats["jis_pairs"] += token_stats["jis_pairs"]
        body = tokens_to_text(tokens)
        if is_useless_body(body) or len(body.strip()) < 4:
            stats["skipped_useless"] += 1
            continue
        samples.append(
            {
                "entry_index": entry_index,
                "relative_offset": start,
                "block_delta": start // BLOCK_SIZE,
                "offset": start % BLOCK_SIZE,
                "heading": extract_heading(tokens, body)[:160],
                "body": body[:500],
            }
        )
    return samples, stats


def classify_audit(
    *,
    body_samples: list[dict[str, Any]],
    dense_marker_honmon: bool,
    id_records: int,
    dictfulldb: bool,
    rendererdb: bool = False,
    android_body_db: bool = False,
    title_components: list[dict[str, Any]],
    index_boundaries: int,
) -> str:
    if body_samples and not dense_marker_honmon:
        return "raw_honmon_body_stream"
    if body_samples and dense_marker_honmon:
        return "mixed_or_dense_but_raw_slices_readable"
    if dense_marker_honmon and dictfulldb and id_records:
        return "dense_honmon_id_table_dictfulldb"
    if dense_marker_honmon and dictfulldb:
        return "dense_honmon_token_table_dictfulldb"
    if dense_marker_honmon and rendererdb and id_records:
        return "dense_honmon_id_table_rendererdb"
    if dense_marker_honmon and android_body_db and id_records:
        return "dense_honmon_id_table_androiddb"
    if title_components or index_boundaries:
        return "idx_title_only_no_readable_honmon_body"
    return "unreadable_or_empty"


def audit_source(source: AuditSource, args: argparse.Namespace) -> dict[str, Any]:
    dictlist = dictlist_summary(source)
    if args.skip_dbc and list(source.idx.parent.glob(DBC_GLOB)):
        return {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "status": "skipped_dbc",
            "dictlist": dictlist,
        }

    honmon = next((e for e in source.elements if e.filename.upper() == "HONMON.DIC"), None)
    if honmon is None:
        return {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "status": "no_honmon",
            "dictlist": dictlist,
        }
    honmon_path = find_case_insensitive(source.idx.parent, honmon.filename)
    if honmon_path is None:
        return {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "status": "missing_honmon",
            "dictlist": dictlist,
        }

    gaiji_profile = load_gaiji_profile(source.idx)
    expanded, honmon_storage = expand_sseddata_file_with_storage(honmon_path)
    marker_count = expanded.count(ENTRY_MARKER)
    dense_marker_honmon = marker_count > 0 and marker_count * 64 > len(expanded)
    id_records, id_scanned, id_samples = count_honmon_id_records(
        expanded,
        gaiji_profile.map,
        max_records=args.max_id_records,
    )

    index_boundaries: set[int] = set()
    index_error = None
    if args.index_boundaries:
        try:
            index_boundaries = collect_index_body_offsets_for_idx(
                source.idx,
                honmon_start_block=honmon.start,
                expanded_size=len(expanded),
            )
        except Exception as exc:
            index_error = str(exc)

    body_samples, body_stats = sample_honmon_bodies(
        expanded,
        index_boundaries,
        gaiji_profile.map,
        sample_limit=args.sample_limit,
        max_slices=args.max_slices,
    )
    title_components, title_samples = title_probe(
        source,
        gaiji_profile.map,
        sample_limit=args.sample_limit,
    )
    exinfo = load_exinfo_for_idx(source.idx)
    renderer_sidecars = discover_renderer_sidecars(source.idx, exinfo)
    android_body_dbs = discover_android_body_databases(source.idx, source.dict_id) if not renderer_sidecars else []

    status = classify_audit(
        body_samples=body_samples,
        dense_marker_honmon=dense_marker_honmon,
        id_records=id_records,
        dictfulldb=has_dictfulldb(dictlist),
        rendererdb=bool(renderer_sidecars),
        android_body_db=bool(android_body_dbs),
        title_components=title_components,
        index_boundaries=len(index_boundaries),
    )
    return {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "status": status,
        "honmon": str(honmon_path),
        "honmon_start_block": honmon.start,
        "honmon_end_block": honmon.end,
        "honmon_storage": honmon_storage,
        "expanded_bytes": len(expanded),
        "entry_markers": marker_count,
        "dense_marker_honmon": dense_marker_honmon,
        "honmon_id_records_in_probe": id_records,
        "honmon_id_records_scanned": id_scanned,
        "honmon_id_samples": id_samples,
        "dictlist": dictlist,
        "dictfulldb_declared": has_dictfulldb(dictlist),
        "dictftsdb_declared": has_dictftsdb(dictlist),
        "exinfo": str(exinfo.path) if exinfo else None,
        "renderer_sidecars": [
            {"path": str(sidecar.path), "storage": sidecar.storage}
            for sidecar in renderer_sidecars
        ],
        "android_body_dbs": [
            {"path": str(body_db.path), "table": body_db.table}
            for body_db in android_body_dbs
        ],
        "index_boundary_offsets": len(index_boundaries),
        "index_error": index_error,
        "title_components": title_components,
        "title_samples": title_samples,
        "body_sample_count": len(body_samples),
        "body_sample_stats": body_stats,
        "body_samples": body_samples,
    }


def extract_audit_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_audit_sources(args.root or [Path(".")])
    if args.dict:
        selected = set(args.dict)
        sources = [
            source
            for source in sources
            if source.dict_id in selected or source.idx.stem in selected
        ]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for source in sources:
        row = audit_source(source, args)
        rows.append(row)
        print(
            f"{row['dict_id']:12s} {row['status']:38s} "
            f"samples={row.get('body_sample_count', 0):2d} "
            f"markers={row.get('entry_markers', 0):8d} "
            f"idx={row.get('index_boundary_offsets', 0):8d}",
        )
    (args.out_dir / "honmon_audit.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-honmon-audit"))
    parser.add_argument("--dict", action="append", help="Only audit matching dictionary id(s).")
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--max-slices", type=int, default=20000)
    parser.add_argument(
        "--max-id-records",
        type=int,
        default=50000,
        help="Probe at most N 32-byte HONMON records; 0 = full scan.",
    )
    parser.add_argument("--no-skip-dbc", dest="skip_dbc", action="store_false")
    parser.add_argument("--no-index-boundaries", dest="index_boundaries", action="store_false")
    parser.set_defaults(skip_dbc=True, index_boundaries=True)
    args = parser.parse_args()
    extract_audit_for_sources(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
