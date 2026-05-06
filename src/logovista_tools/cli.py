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
from .gaiji_report import extract_gaiji_reports
from .gaiji import UniRecord, parse_ga16_resource, parse_uni_resource, write_ga16_glyph_png
from .indexes import extract_indexes_for_idx
from .resources import ImageResource, load_image_resource_profile
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
                "image_resource_entries": source.image_resource_entries,
                "image_gaiji_entries": len(source.image_gaiji_keys),
                "image_dirs": [str(path) for path in source.image_dirs],
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
            f"img={row['image_resource_entries']:4d} img-gaiji={row['image_gaiji_entries']:4d} "
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


def image_resource_to_json(resource: ImageResource) -> dict[str, Any]:
    return {
        "key": resource.key,
        "files": [str(path) for path in resource.files],
        "normal": str(resource.normal) if resource.normal else None,
        "white": str(resource.white) if resource.white else None,
        "default": str(resource.default) if resource.default else None,
        "listed_in_resources_copy": resource.listed_in_resources_copy,
        "listed_in_gaijiicon": resource.listed_in_gaijiicon,
    }


def cmd_resources(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    rows = []
    for source in sources:
        profile = load_image_resource_profile(source.idx)
        row = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "image_dirs": [str(path) for path in profile.image_dirs],
            "resources": {key: image_resource_to_json(value) for key, value in profile.resources.items()},
            "gaiji_image_keys": sorted(profile.gaiji_image_keys),
            "resources_copy_paths": [str(path) for path in profile.resources_copy_paths],
            "gaijiicon_paths": [str(path) for path in profile.gaijiicon_paths],
            "resources_copy_entries": list(profile.resources_copy_entries),
            "gaijiicon_entries": list(profile.gaijiicon_entries),
        }
        rows.append(row)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    for row in rows:
        named = sorted(
            key for key in row["resources"] if key not in set(row["gaiji_image_keys"])
        )
        print(
            f"{row['dict_id']:12s} resources={len(row['resources']):4d} "
            f"gaiji-images={len(row['gaiji_image_keys']):4d} {row['dict_title']}"
        )
        for image_dir in row["image_dirs"]:
            print(f"  img: {image_dir}")
        print(f"  gaiji sample: {', '.join(row['gaiji_image_keys'][:16])}")
        print(f"  named sample: {', '.join(named[:16])}")
    return 0


def uni_record_to_json(record: UniRecord) -> dict[str, Any]:
    return {
        "section": record.section,
        "index": record.index,
        "code": record.code,
        "metadata": record.metadata,
        "display_units": [f"{value:04x}" for value in record.display_units],
        "display": record.display,
        "fallback_units": [f"{value:04x}" for value in record.fallback_units],
        "fallback": record.fallback,
        "legacy_units": [f"{value:04x}" for value in record.legacy_units],
        "legacy": record.legacy,
        "raw_fields": [f"{value:04x}" for value in record.raw_fields],
    }


def cmd_uni(args: argparse.Namespace) -> int:
    resource = parse_uni_resource(args.path)
    if resource is None:
        print(f"could not parse .uni resource: {args.path}", file=sys.stderr)
        return 1

    mapped = sum(1 for record in resource.records if record.display)
    fallback = sum(1 for record in resource.records if record.fallback)
    legacy = sum(1 for record in resource.records if record.legacy)
    metadata = sum(1 for record in resource.records if record.metadata)
    if args.json:
        records = resource.records
        if args.limit is not None:
            records = records[: args.limit]
        print(
            json.dumps(
                {
                    "path": str(resource.path),
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
                    "items": [uni_record_to_json(record) for record in records],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"path: {resource.path}")
    print(f"format: {resource.format}")
    print(
        f"records: {len(resource.records)} "
        f"half={resource.half_count} full={resource.full_count} "
        f"mapped={mapped} fallback={fallback} legacy={legacy} metadata={metadata}"
    )
    print(f"expected_size: {resource.expected_size} trailing_bytes: {resource.trailing_bytes}")
    limit = args.limit if args.limit is not None else 24
    for record in resource.records[:limit]:
        print(
            f"{record.section:4s} {record.code.upper()} "
            f"meta={record.metadata:04X} "
            f"display={record.display!r} "
            f"fallback={record.fallback!r} "
            f"legacy={record.legacy!r} "
            f"raw={' '.join(f'{value:04X}' for value in record.raw_fields)}"
        )
    return 0


GA16_RESOURCE_NAMES = {
    "GA16HALF",
    "GA16FULL",
    "GAI16H",
    "GAI16F",
}


def is_ga16_resource_path(path: Path) -> bool:
    name = path.name.upper()
    return (
        name in GA16_RESOURCE_NAMES
        or name.startswith("GAI16H")
        or name.startswith("GAI16F")
    )


def discover_ga16_resources(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path] if is_ga16_resource_path(path) else []

    direct = sorted(
        child for child in path.iterdir() if child.is_file() and is_ga16_resource_path(child)
    )
    if direct:
        return direct

    return sorted(child for child in path.rglob("*") if child.is_file() and is_ga16_resource_path(child))


def ga16_prefix_for_path(path: Path, override: str) -> str:
    if override != "auto":
        return override
    name = path.name.upper()
    if "HALF" in name or "16H" in name:
        return "h"
    if "FULL" in name or "16F" in name:
        return "z"
    return "g"


def parse_hex_color(value: str) -> tuple[int, int, int, int]:
    cleaned = value.strip().removeprefix("#")
    if len(cleaned) not in (6, 8) or any(ch not in "0123456789abcdefABCDEF" for ch in cleaned):
        raise argparse.ArgumentTypeError("color must be RRGGBB or RRGGBBAA")
    if len(cleaned) == 6:
        cleaned += "ff"
    return (
        int(cleaned[0:2], 16),
        int(cleaned[2:4], 16),
        int(cleaned[4:6], 16),
        int(cleaned[6:8], 16),
    )


def parse_gaiji_code_arg(value: str) -> int:
    cleaned = value.strip().strip("<>").lower()
    if len(cleaned) == 5 and cleaned[0] in ("h", "z", "g"):
        cleaned = cleaned[1:]
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) != 4 or any(ch not in "0123456789abcdef" for ch in cleaned):
        raise argparse.ArgumentTypeError("gaiji code must be four hex digits, e.g. A126 or hA126")
    return int(cleaned, 16)


def cmd_ga16(args: argparse.Namespace) -> int:
    paths = discover_ga16_resources(args.path)
    if not paths:
        print(f"no GA16/GAI16 bitmap resources found under: {args.path}", file=sys.stderr)
        return 1

    selected_codes = set(args.code or [])
    group_by_dict = args.path.is_dir()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    total_written = 0
    for path in paths:
        data = path.read_bytes()
        resource = parse_ga16_resource(path)
        if resource is None:
            print(f"skip unparsable GA16 resource: {path}", file=sys.stderr)
            continue

        prefix = ga16_prefix_for_path(path, args.prefix)
        target_dir = args.out_dir / path.parent.name if group_by_dict else args.out_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        written = 0
        considered = 0
        for code, glyph in resource.iter_glyphs(data):
            if selected_codes and code not in selected_codes:
                continue
            considered += 1
            base_name = f"{prefix}{code:04X}"
            if args.variants:
                write_ga16_glyph_png(
                    target_dir / f"{base_name}_n.png",
                    glyph,
                    resource.width,
                    resource.height,
                    foreground=(0, 0, 0, 255),
                    background=(0, 0, 0, 0),
                )
                write_ga16_glyph_png(
                    target_dir / f"{base_name}_w.png",
                    glyph,
                    resource.width,
                    resource.height,
                    foreground=(255, 255, 255, 255),
                    background=(0, 0, 0, 0),
                )
                written += 2
            else:
                write_ga16_glyph_png(
                    target_dir / f"{base_name}.png",
                    glyph,
                    resource.width,
                    resource.height,
                    foreground=args.foreground,
                    background=args.background,
                )
                written += 1
            if args.limit is not None and considered >= args.limit:
                break

        total_written += written
        summaries.append(
            {
                "path": str(path),
                "out_dir": str(target_dir),
                "prefix": prefix,
                "width": resource.width,
                "height": resource.height,
                "start_code": f"{resource.start_code:04X}",
                "count": resource.count,
                "glyph_bytes": resource.glyph_bytes,
                "glyphs_selected": considered,
                "png_files_written": written,
            }
        )

    if args.json:
        print(json.dumps({"resources": summaries, "png_files_written": total_written}, ensure_ascii=False, indent=2))
        return 0

    for row in summaries:
        print(
            f"{Path(row['path']).name:10s} {row['width']}x{row['height']} "
            f"start={row['start_code']} count={row['count']} "
            f"selected={row['glyphs_selected']} wrote={row['png_files_written']} "
            f"out={row['out_dir']}"
        )
    print(f"png files written: {total_written}")
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


def cmd_indexes(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting indexes {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_indexes_for_idx(source.idx, args.out_dir, args)
        summaries.append(summary)
        print(f"  index_rows={summary['rows_emitted']}", file=sys.stderr)
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


def cmd_gaiji_report(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    summaries = extract_gaiji_reports(sources, args.out_dir, args)
    for summary in summaries:
        print(
            f"{summary['dict_id']:12s} raw={summary['raw_distinct_codes']:4d} "
            f"mapped={summary['mapped_codes']:4d} sqlite={summary['sqlite_sources']:2d} "
            f"aligned_hits={summary['aligned_hits']:6d} "
            f"aligned_misses={summary['aligned_misses']:6d}",
            file=sys.stderr,
        )
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
        "--image-gaiji",
        action="store_true",
        help="Preserve unresolved gaiji that have PNG assets as <img:code> placeholders.",
    )
    p_entries.add_argument(
        "--media-placeholder",
        action="store_true",
        help="Preserve 1f4d media controls as <media:payload-hex> placeholders.",
    )
    p_entries.add_argument(
        "--section-markers",
        action="store_true",
        help="Preserve 1f09 section markers as <section:xxxx> placeholders.",
    )
    p_entries.add_argument(
        "--html",
        action="store_true",
        help="Also emit body_html with conservative inline HTML and img tags for image gaiji.",
    )
    p_entries.add_argument(
        "--section-image",
        action="append",
        help="For HTML output, insert an image at a section marker. Format: CODE=IMAGE_KEY, e.g. 0011=exam.",
    )
    p_entries.add_argument(
        "--no-skip-dense-marker-honmon",
        dest="skip_dense_marker_honmon",
        action="store_false",
        help="Attempt extraction even when HONMON looks like a placeholder table.",
    )
    p_entries.add_argument(
        "--no-index-boundaries",
        dest="index_boundaries",
        action="store_false",
        help="Do not add raw index body pointers as extra entry boundaries.",
    )
    p_entries.set_defaults(skip_dense_marker_honmon=True, index_boundaries=True, func=cmd_entries)
    p_entries.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")

    p_resources = sub.add_parser("resources", help="List package image resources and image-backed gaiji.")
    p_resources.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_resources.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    p_resources.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p_resources.set_defaults(func=cmd_resources)

    p_gaiji_report = sub.add_parser(
        "gaiji-report",
        help="Write SQL/DictFULLDB-assisted gaiji validation reports.",
    )
    p_gaiji_report.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_gaiji_report.add_argument("--out-dir", type=Path, default=Path("logovista-gaiji-report"))
    p_gaiji_report.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    p_gaiji_report.add_argument(
        "--no-sql-cache",
        action="store_true",
        help="Only use declared DictFULLDB SQLite sources, not sibling app-cache databases.",
    )
    p_gaiji_report.add_argument(
        "--max-sql-rows",
        type=int,
        default=5000,
        help="Limit scanned rows per SQLite table; use 0 for a full scan.",
    )
    p_gaiji_report.add_argument(
        "--max-aligned-entries",
        type=int,
        default=50000,
        help="Limit raw HONMON entries used for aligned Block/Offset checks; use 0 for a full scan.",
    )
    p_gaiji_report.add_argument(
        "--alignment-tolerance",
        type=int,
        default=16,
        help="Block/offset match tolerance in bytes for cache rows.",
    )
    p_gaiji_report.add_argument(
        "--include-unused-mapped",
        action="store_true",
        help="Include mapped gaiji codes not seen in raw HONMON/TITLE scans.",
    )
    p_gaiji_report.set_defaults(func=cmd_gaiji_report)

    p_uni = sub.add_parser("uni", help="Inspect a LogoVista .uni/UNI gaiji mapping file.")
    p_uni.add_argument("path", type=Path)
    p_uni.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    p_uni.add_argument("--limit", type=int, help="Limit records printed/emitted.")
    p_uni.set_defaults(func=cmd_uni)

    p_ga16 = sub.add_parser("ga16", help="Render GA16HALF/GA16FULL bitmap gaiji to PNG assets.")
    p_ga16.add_argument("path", type=Path, help="GA16 file, dictionary directory, or collection root.")
    p_ga16.add_argument("out_dir", type=Path, help="Directory to write PNG files.")
    p_ga16.add_argument("--limit", type=int, help="Limit glyphs rendered per resource.")
    p_ga16.add_argument(
        "--code",
        action="append",
        type=parse_gaiji_code_arg,
        help="Only render one gaiji code. May be repeated. Accepts A126, 0xA126, or hA126.",
    )
    p_ga16.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    p_ga16.add_argument(
        "--prefix",
        choices=("auto", "h", "z", "g"),
        default="auto",
        help="Filename prefix. auto uses h for half-width resources and z for full-width resources.",
    )
    p_ga16.add_argument(
        "--foreground",
        type=parse_hex_color,
        default=(0, 0, 0, 255),
        help="Ink color for single-variant output, as RRGGBB or RRGGBBAA.",
    )
    p_ga16.add_argument(
        "--background",
        type=parse_hex_color,
        default=(0, 0, 0, 0),
        help="Background color for single-variant output, as RRGGBB or RRGGBBAA.",
    )
    p_ga16.add_argument(
        "--variants",
        action="store_true",
        help="Write LogoVista-style black _n.png and white _w.png theme variants.",
    )
    p_ga16.set_defaults(func=cmd_ga16)

    p_titles = sub.add_parser("titles", help="Extract raw *TITLE.DIC headword/title lines as JSONL.")
    p_titles.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_titles.add_argument("--out-dir", type=Path, default=Path("logovista-raw-titles"))
    p_titles.add_argument("--limit", type=int, help="Limit emitted title lines per component.")
    p_titles.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    p_titles.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    p_titles.set_defaults(func=cmd_titles)

    p_indexes = sub.add_parser("indexes", help="Extract raw *INDEX.DIC search rows as JSONL.")
    p_indexes.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_indexes.add_argument("--out-dir", type=Path, default=Path("logovista-raw-indexes"))
    p_indexes.add_argument("--limit", type=int, help="Limit emitted index rows per run.")
    p_indexes.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    p_indexes.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    p_indexes.add_argument("--component", action="append", help="Only extract matching component filename(s).")
    p_indexes.add_argument(
        "--include-internal",
        action="store_true",
        help="Also emit binary-search tree internal rows, not only leaf search records.",
    )
    p_indexes.set_defaults(func=cmd_indexes)

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
