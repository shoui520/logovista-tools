"""Command line interface for logovista-tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .entries import discover_dictionaries, extract_dictionary
from .fulldb import extract_fulldb_dictionary
from .ssed import (
    BLOCK_SIZE,
    expand_sseddata_file,
    find_case_insensitive,
    parse_sseddata_header,
    parse_ssedinfo,
    write_epwing_catalog_header,
)
from .titles import extract_titles_for_idx


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_info(args: argparse.Namespace) -> int:
    data = args.path.read_bytes()[:8]
    if data == b"SSEDINFO":
        title, elements = parse_ssedinfo(args.path)
        print(f"title: {title}")
        print(f"elements: {len(elements)}")
        for element in elements:
            if args.all or element.start:
                print(
                    f"{element.index:02d} {element.filename:16s} "
                    f"multi={element.multi:02x} type={element.type:02x} "
                    f"start={element.start:#x} end={element.end:#x} "
                    f"blocks={element.block_count:#x} data={element.data.hex()}"
                )
        return 0

    if data == b"SSEDDATA":
        header = parse_sseddata_header(args.path)
        print(
            f"chunks={header['n_chunk']} start={header['start_block']:#x} "
            f"end={header['end_block']:#x} kind={header['kind']:#x}"
        )
        return 0

    print(f"unknown raw file type: {args.path}", file=sys.stderr)
    return 1


def cmd_scan(args: argparse.Namespace) -> int:
    sources = discover_dictionaries(args.root or [Path(".")])
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    rows = []
    for source in sources:
        title, elements = parse_ssedinfo(source.idx)
        rows.append(
            {
                "dict_id": source.dict_id,
                "title": title,
                "idx": str(source.idx),
                "honmon": str(source.honmon),
                "honmon_start_block": source.honmon_start_block,
                "components": len(elements),
                "gaiji_map_entries": len(source.gaiji_map),
                "gaiji_uni_entries": source.gaiji_uni_entries,
                "gaiji_plist_entries": source.gaiji_plist_entries,
            }
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    for row in rows:
        print(
            f"{row['dict_id']:12s} components={row['components']:2d} "
            f"gaiji={row['gaiji_map_entries']:4d} "
            f"uni={row['gaiji_uni_entries']:4d} plist={row['gaiji_plist_entries']:4d} "
            f"{row['title']}"
        )
        print(f"  idx: {row['idx']}")
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    expanded = expand_sseddata_file(args.dic)
    args.out.write_bytes(expanded)
    print(f"expanded {args.dic} -> {args.out}")
    print(f"bytes: {len(expanded)}")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    title, elements = parse_ssedinfo(args.idx)
    source_dir = args.idx.parent
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w+b") as out:
        for element in elements:
            if not element.start:
                continue
            source = find_case_insensitive(source_dir, element.filename)
            if source is None:
                if args.strict:
                    raise FileNotFoundError(element.filename)
                print(f"skip missing {element.filename}", file=sys.stderr)
                continue
            expanded = expand_sseddata_file(source)
            out.seek((element.start - 1) * BLOCK_SIZE)
            out.write(expanded)
            if not args.quiet:
                print(
                    f"{element.filename:16s} start={element.start:#x} "
                    f"bytes={len(expanded)}"
                )
        write_epwing_catalog_header(out, elements)
    print(f"title: {title}")
    print(f"expanded book: {args.out}")
    return 0


def select_sources(args: argparse.Namespace):
    sources = discover_dictionaries(args.root or [Path(".")])
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    return sources


def cmd_entries(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_dictionary(source, args.out_dir, args)
        summaries.append(summary)
        print(
            f"  entries={summary['entries_emitted']} markers={summary['entry_markers']} "
            f"bytes={summary['expanded_bytes']}",
            file=sys.stderr,
        )
    write_json(args.out_dir / "summary.json", summaries)
    return 0


def cmd_titles(args: argparse.Namespace) -> int:
    sources = select_sources(args)
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


def cmd_fulldb(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting fulldb {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_fulldb_dictionary(source, args.out_dir, args)
        summaries.append(summary)
        print(
            f"  entries={summary['entries_emitted']} "
            f"ids={summary['honmon_ids_seen']} "
            f"missing={summary['honmon_ids_missing_in_fulldb']}",
            file=sys.stderr,
        )
    write_json(args.out_dir / "summary.json", summaries)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="logovista-tools",
        description="Raw-first tools for LogoVista/SystemSoft SSED dictionaries.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="Inspect an SSEDINFO .IDX or SSEDDATA .DIC file.")
    p_info.add_argument("path", type=Path)
    p_info.add_argument("--all", action="store_true", help="Show zero-start/resource components too.")
    p_info.set_defaults(func=cmd_info)

    p_scan = sub.add_parser("scan", help="Find LogoVista dictionaries under roots.")
    p_scan.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_scan.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p_scan.set_defaults(func=cmd_scan)

    p_expand = sub.add_parser("expand", help="Expand one SSEDDATA .DIC file.")
    p_expand.add_argument("dic", type=Path)
    p_expand.add_argument("out", type=Path)
    p_expand.set_defaults(func=cmd_expand)

    p_compose = sub.add_parser("compose", help="Compose an EPWING-like book image from one .IDX.")
    p_compose.add_argument("idx", type=Path)
    p_compose.add_argument("out", type=Path)
    p_compose.add_argument("--strict", action="store_true", help="Fail if a listed component is missing.")
    p_compose.add_argument("--quiet", action="store_true", help="Do not print every component.")
    p_compose.set_defaults(func=cmd_compose)

    p_entries = sub.add_parser("entries", help="Extract readable HONMON body entries as JSONL.")
    p_entries.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_entries.add_argument("--out-dir", type=Path, default=Path("logovista-raw-extract"))
    p_entries.add_argument("--limit", type=int, help="Limit entries per dictionary for smoke tests.")
    p_entries.add_argument("--min-chars", type=int, default=1)
    p_entries.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    p_entries.add_argument(
        "--no-skip-dense-marker-honmon",
        dest="skip_dense_marker_honmon",
        action="store_false",
        help="Attempt extraction even when HONMON looks like a placeholder table.",
    )
    p_entries.set_defaults(skip_dense_marker_honmon=True, func=cmd_entries)
    p_entries.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")

    p_titles = sub.add_parser("titles", help="Extract raw *TITLE.DIC headword/title lines as JSONL.")
    p_titles.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_titles.add_argument("--out-dir", type=Path, default=Path("logovista-raw-titles"))
    p_titles.add_argument("--limit", type=int, help="Limit emitted title lines per component.")
    p_titles.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    p_titles.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    p_titles.set_defaults(func=cmd_titles)

    p_fulldb = sub.add_parser(
        "fulldb",
        help="Extract DictFULLDB bodies by decoding raw HONMON numeric id records.",
    )
    p_fulldb.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_fulldb.add_argument("--out-dir", type=Path, default=Path("logovista-fulldb-extract"))
    p_fulldb.add_argument("--limit", type=int, help="Limit emitted entries per dictionary.")
    p_fulldb.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    p_fulldb.add_argument(
        "--allow-db-fallback",
        action="store_true",
        help="If DictList.plist has no DictFULLDB, try a neighboring .db/.sql file.",
    )
    p_fulldb.set_defaults(func=cmd_fulldb)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
