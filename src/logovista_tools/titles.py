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

from .entries import decode_tokens, discover_dictionaries, tokens_to_text
from .gaiji import load_gaiji_profile
from .ssed import expand_sseddata_file, find_case_insensitive, parse_ssedinfo


TITLE_TYPES = {0x03, 0x04, 0x05, 0x06, 0x07, 0x0A}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
            expanded = expand_sseddata_file(source)
            tokens, stats = decode_tokens(expanded, gaiji=args.gaiji, gaiji_map=gaiji_profile.map)
            text = tokens_to_text(tokens)
            emitted = 0
            for line_index, line in enumerate(text.splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
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
                    "expanded_bytes": len(expanded),
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
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        help="Dictionary collection directory or a direct .IDX path. Can repeat.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-titles"))
    parser.add_argument("--limit", type=int, help="Limit emitted title lines per component.")
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    args = parser.parse_args()

    roots = args.root or [Path(".")]
    sources = discover_dictionaries(roots)
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
