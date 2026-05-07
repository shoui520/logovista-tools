"""Lossless span JSONL extraction for expanded HONMON entries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .entries import DictionarySource, discover_dictionaries, iter_entry_slices_with_boundaries
from .indexes import collect_index_body_offsets_for_idx
from .parallel import parallel_map_ordered, worker_args
from .spans import LosslessDecodeError, combine_span_stats, decode_lossless_spans
from .ssed import BLOCK_SIZE, expand_sseddata_file_with_storage


def extract_ir_for_source(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    entries_path = dict_out / "lossless_entries.jsonl"
    expanded, storage = expand_sseddata_file_with_storage(source.honmon)
    index_boundary_offsets: set[int] = set()
    warnings: list[str] = []
    if getattr(args, "index_boundaries", True):
        try:
            index_boundary_offsets = collect_index_body_offsets_for_idx(
                source.idx,
                honmon_start_block=source.honmon_start_block,
                expanded_size=len(expanded),
            )
        except Exception as exc:
            warnings.append(f"Could not collect index-derived entry boundaries: {exc}")

    emitted = 0
    strict_failures = 0
    span_results = []
    limit = args.limit
    with entries_path.open("w", encoding="utf-8") as out:
        for entry_index, (start, end) in enumerate(
            iter_entry_slices_with_boundaries(expanded, index_boundary_offsets),
            start=1,
        ):
            if limit and emitted >= limit:
                break
            segment = expanded[start:end]
            strict_error = None
            try:
                decoded = decode_lossless_spans(
                    segment,
                    gaiji_map=source.gaiji_map,
                    image_gaiji_keys=source.image_gaiji_keys,
                    mode=args.parse_mode,
                    include_padding=args.include_padding,
                )
            except LosslessDecodeError as exc:
                strict_failures += 1
                strict_error = str(exc)
                decoded = decode_lossless_spans(
                    segment,
                    gaiji_map=source.gaiji_map,
                    image_gaiji_keys=source.image_gaiji_keys,
                    mode="forensic",
                    include_padding=args.include_padding,
                )
            span_results.append(decoded)
            row = {
                "schema": "logovista-lossless-entry-v1",
                "dict_id": source.dict_id,
                "entry_index": entry_index,
                "address": {
                    "component": "HONMON.DIC",
                    "block": source.honmon_start_block + start // BLOCK_SIZE,
                    "offset": start % BLOCK_SIZE,
                    "component_offset": start,
                },
                "length": end - start,
                "strict_error": strict_error,
                **decoded.as_dict(
                    include_spans=True,
                    include_raw=args.include_raw,
                    max_issues=args.max_issues,
                ),
            }
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            out.write("\n")
            emitted += 1

    aggregate = combine_span_stats(span_results)
    summary = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "honmon_storage": storage,
        "expanded_bytes": len(expanded),
        "index_boundary_offsets": len(index_boundary_offsets),
        "entries_emitted": emitted,
        "strict_failures": strict_failures,
        "aggregate": aggregate,
        "warnings": warnings,
        "entries_path": str(entries_path),
    }
    (dict_out / "lossless_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _ir_source_task(payload: tuple[DictionarySource, Path, argparse.Namespace]) -> dict[str, Any]:
    source, out_dir, args = payload
    return extract_ir_for_source(source, out_dir, args)


def extract_ir_for_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(args.root or [Path(".")], jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)
    summaries = parallel_map_ordered(
        _ir_source_task,
        [(source, args.out_dir, task_args) for source in sources],
        jobs=getattr(args, "jobs", 1),
    )
    (args.out_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summaries
