#!/usr/bin/env python3
"""Extract LogoVista/SSED title streams as JSONL.

This is a raw helper for the index layer. It reads .IDX/.DIC files directly and
does not use SQLite caches. The output is not a full dictionary entry body; it
is the headword/title text stored in EPWING title components such as FKTITLE.DIC.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .cli_args import add_titles_args
from .entries import decode_tokens, discover_dictionaries, tokens_to_text
from .gaiji import load_gaiji_profile
from .ssed import CHUNK_SIZE, SsedRandomReader, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


TITLE_TYPES = {0x03, 0x04, 0x05, 0x06, 0x07, 0x09, 0x0A, 0x0D}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_title_lines(
    source: Path,
    *,
    limit: int | None,
    gaiji: str,
    gaiji_map: dict[str, str],
) -> tuple[list[tuple[int, str]], dict[str, int], int, int, bool]:
    if not limit:
        expanded = expand_sseddata_file(source)
        tokens, stats = decode_tokens(expanded, gaiji=gaiji, gaiji_map=gaiji_map)
        lines = [(index, line.strip()) for index, line in enumerate(tokens_to_text(tokens).splitlines(), start=1) if line.strip()]
        return lines, stats, len(expanded), len(expanded), True

    reader = SsedRandomReader(source)
    size = min(reader.expanded_size, CHUNK_SIZE)
    while True:
        data = reader.read(0, size)
        tokens, stats = decode_tokens(data, gaiji=gaiji, gaiji_map=gaiji_map)
        lines = [(index, line.strip()) for index, line in enumerate(tokens_to_text(tokens).splitlines(), start=1) if line.strip()]
        complete = size >= reader.expanded_size
        if len(lines) > limit or complete:
            return lines, stats, reader.expanded_size, size, complete
        size = min(reader.expanded_size, size + CHUNK_SIZE)


def extract_titles_for_idx(idx: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    title, elements = parse_ssedinfo(idx)
    dict_id = idx.parent.parent.name if idx.parent.name == idx.parent.parent.name else idx.stem
    dict_out = out_dir / dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    titles_path = dict_out / "raw_titles.jsonl"
    gaiji_profile = load_gaiji_profile(idx)

    component_summaries: list[dict[str, Any]] = []
    total_lines = 0
    with titles_path.open("w", encoding="utf-8") as out:
        for element in elements:
            if element.type not in TITLE_TYPES or not element.start:
                continue
            source = find_case_insensitive(idx.parent, element.filename)
            if source is None:
                continue
            lines, stats, expanded_bytes, bytes_decoded, stream_complete = _decode_title_lines(
                source,
                limit=args.limit,
                gaiji=args.gaiji,
                gaiji_map=gaiji_profile.map,
            )
            emitted = 0
            for line_index, line in lines:
                if args.limit and emitted >= args.limit:
                    break
                item = {
                    "dict_id": dict_id,
                    "dict_title": title,
                    "component": element.filename,
                    "line_index": line_index,
                    "text": line,
                }
                out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")
                emitted += 1
                total_lines += 1
            component_summaries.append(
                {
                    "component": element.filename,
                    "type": element.type,
                    "expanded_bytes": expanded_bytes,
                    "bytes_decoded": bytes_decoded,
                    "stream_complete": stream_complete,
                    "lines_total": len(lines) if stream_complete else None,
                    "lines_decoded": len(lines),
                    "lines_emitted": emitted,
                    "stats": stats,
                }
            )

    summary = {
        "dict_id": dict_id,
        "dict_title": title,
        "idx": str(idx),
        "title_components": component_summaries,
        "lines_emitted": total_lines,
        "gaiji_map_entries": len(gaiji_profile.map),
        "gaiji_uni_entries": gaiji_profile.uni_entries,
        "gaiji_plist_entries": gaiji_profile.plist_entries,
        "titles_path": str(titles_path),
    }
    write_json(dict_out / "titles_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_titles_args(parser)
    args = parser.parse_args()

    roots = args.root or [Path(".")]
    sources = discover_dictionaries(roots, jobs=args.jobs, dict_ids=args.dict, include_gaiji=False, include_images=False)
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting titles {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_titles_for_idx(source.idx, args.out_dir, args)
        summaries.append(summary)
        print(f"  title_lines={summary['lines_emitted']}", file=sys.stderr)
    write_json(args.out_dir / "summary.json", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
