"""Command line interface for logovista-tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .audit import extract_audit_for_sources
from .colscr import extract_colscr_for_sources
from .entries import discover_dictionaries, extract_dictionary
from .fulldb import extract_fulldb_dictionary
from .gaiji_report import extract_gaiji_reports
from .gaiji import UniRecord, parse_ga16_resource, parse_uni_resource, write_ga16_glyph_png
from .indexes import extract_indexes_for_idx
from .lvcrypto import decrypt_logofont_cipher_file_to_path
from .menus import extract_menus_for_idx
from .pcmdata import extract_pcmdata_for_sources
from .rendererdb import extract_rendererdb_for_sources
from .resources import ImageResource, load_image_resource_profile
from .spindex import discover_spindex_files, inspect_spindex
from .ssed import (
    BLOCK_SIZE,
    expand_sseddata_file,
    expand_sseddata_file_with_storage,
    find_case_insensitive,
    parse_sseddata_header,
    parse_ssedinfo,
    write_epwing_catalog_header,
)
from .titles import extract_titles_for_idx
from .windows import aux_index_row_to_json, iter_aux_index_specs, load_exinfo_for_idx, parse_aux_index_text


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
            f"end={header['end_block']:#x} kind={header['kind']:#x} "
            f"storage={header['storage']}"
        )
        return 0

    try:
        header = parse_sseddata_header(args.path)
    except ValueError:
        header = None
    if header is not None:
        print(
            f"chunks={header['n_chunk']} start={header['start_block']:#x} "
            f"end={header['end_block']:#x} kind={header['kind']:#x} "
            f"storage={header['storage']}"
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
                "honmon_storage": source.honmon_storage,
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
            f"honmon={row['honmon_storage']:15s} "
            f"gaiji={row['gaiji_map_entries']:4d} "
            f"uni={row['gaiji_uni_entries']:4d} plist={row['gaiji_plist_entries']:4d} "
            f"img={row['image_resource_entries']:4d} img-gaiji={row['image_gaiji_entries']:4d} "
            f"{row['title']}"
        )
        print(f"  idx: {row['idx']}")
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    expanded, storage = expand_sseddata_file_with_storage(args.dic)
    args.out.write_bytes(expanded)
    print(f"expanded {args.dic} -> {args.out}")
    print(f"storage: {storage}")
    print(f"bytes: {len(expanded)}")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = decrypt_logofont_cipher_file_to_path(args.file, args.out)
    print(f"decrypted {args.file} -> {args.out}")
    print(f"bytes: {written}")
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


def cmd_menus(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting menus {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_menus_for_idx(source.idx, args.out_dir, args)
        summaries.append(summary)
        print(f"  menu_lines={summary['lines_emitted']}", file=sys.stderr)
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


def cmd_colscr(args: argparse.Namespace) -> int:
    summaries = extract_colscr_for_sources(args)
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def cmd_pcmdata(args: argparse.Namespace) -> int:
    summaries = extract_pcmdata_for_sources(args)
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def cmd_extras(args: argparse.Namespace) -> int:
    sources = select_sources(args)
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        title, elements = parse_ssedinfo(source.idx)
        exinfo = load_exinfo_for_idx(source.idx)
        dict_out = args.out_dir / source.dict_id
        dict_out.mkdir(parents=True, exist_ok=True)
        row = {
            "dict_id": source.dict_id,
            "dict_title": title,
            "idx": str(source.idx),
            "exinfo": str(exinfo.path) if exinfo else None,
            "general": exinfo.general if exinfo else {},
            "aux_indexes": [],
        }
        if exinfo is not None:
            for spec in iter_aux_index_specs(exinfo):
                spec_row = {
                    "index": spec.index,
                    "name": spec.name,
                    "info": spec.info,
                    "path": str(spec.path) if spec.path else None,
                    "kind": None,
                    "rows": 0,
                    "rows_path": None,
                    "sample": [],
                }
                if spec.path and spec.path.exists():
                    if spec.path.suffix.lower() in {".html", ".htm"}:
                        spec_row["kind"] = "html"
                        spec_row["bytes"] = spec.path.stat().st_size
                    else:
                        parsed_rows = parse_aux_index_text(spec.path, elements)
                        if parsed_rows:
                            spec_row["kind"] = "text-index"
                            spec_row["rows"] = len(parsed_rows)
                            rows_path = dict_out / f"aux_{spec.index}_{spec.path.name}.jsonl"
                            with rows_path.open("w", encoding="utf-8") as out:
                                for item in parsed_rows:
                                    out.write(json.dumps(aux_index_row_to_json(item), ensure_ascii=False) + "\n")
                            spec_row["rows_path"] = str(rows_path)
                            spec_row["sample"] = [aux_index_row_to_json(item) for item in parsed_rows[: args.limit]]
                        else:
                            spec_row["kind"] = "unknown"
                            spec_row["bytes"] = spec.path.stat().st_size
                row["aux_indexes"].append(spec_row)
        (dict_out / "extras_summary.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(row)
        print(
            f"{source.dict_id:12s} exinfo={'yes' if exinfo else 'no':3s} "
            f"aux={len(row['aux_indexes']):2d}",
            file=sys.stderr,
        )

    write_json(args.out_dir / "summary.json", summaries)
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def cmd_rendererdb(args: argparse.Namespace) -> int:
    summaries = extract_rendererdb_for_sources(args)
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def cmd_spindex(args: argparse.Namespace) -> int:
    paths = discover_spindex_files(args.root or [Path(".")])
    if not paths:
        print("no SPINDEX.DIC files found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for path in paths:
        dict_out = args.out_dir / path.parent.name
        summary = inspect_spindex(path, out_dir=dict_out, row_limit=args.limit)
        summaries.append(summary)
        print(
            f"{path.parent.name:12s} chunks={summary['chunks_present']:3d}/{summary['declared_chunks']:3d} "
            f"pages={summary['pages_parsed']:4d} rows={summary['internal_rows']:5d} "
            f"truncated={'yes' if summary['truncated'] else 'no'}",
            file=sys.stderr,
        )

    write_json(args.out_dir / "summary.json", summaries)
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    rows = extract_audit_for_sources(args)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
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

    p_decrypt = sub.add_parser("decrypt", help="Decrypt a LogoFontCipher AES-CBC sidecar or encrypted .DIC.")
    p_decrypt.add_argument("file", type=Path)
    p_decrypt.add_argument("out", type=Path)
    p_decrypt.set_defaults(func=cmd_decrypt)

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

    p_colscr = sub.add_parser("colscr", help="Inspect or extract COLSCR.DIC media image records.")
    p_colscr.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_colscr.add_argument("--out-dir", type=Path, default=Path("logovista-colscr"))
    p_colscr.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    p_colscr.add_argument("--limit", type=int, help="Limit media references per dictionary.")
    p_colscr.add_argument(
        "--write-media",
        "--write-bmp",
        dest="write_media",
        action="store_true",
        help="Write referenced image files next to the manifest.",
    )
    p_colscr.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    p_colscr.set_defaults(func=cmd_colscr)

    p_pcmdata = sub.add_parser("pcmdata", help="Inspect or extract PCMDATA.DIC audio/media records.")
    p_pcmdata.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_pcmdata.add_argument("--out-dir", type=Path, default=Path("logovista-pcmdata"))
    p_pcmdata.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    p_pcmdata.add_argument("--limit", type=int, help="Limit HONMON audio references per dictionary.")
    p_pcmdata.add_argument(
        "--write-audio",
        action="store_true",
        help="Write portable audio files next to the manifest.",
    )
    p_pcmdata.add_argument(
        "--no-include-unreferenced",
        dest="include_unreferenced",
        action="store_false",
        help="Do not scan unreferenced records in PCMDATA gaps.",
    )
    p_pcmdata.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    p_pcmdata.set_defaults(include_unreferenced=True, func=cmd_pcmdata)

    p_extras = sub.add_parser("extras", help="Parse Windows EXINFO.INI auxiliary index/html metadata.")
    p_extras.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_extras.add_argument("--out-dir", type=Path, default=Path("logovista-extras"))
    p_extras.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    p_extras.add_argument("--limit", type=int, default=10, help="Number of sample aux-index rows per file.")
    p_extras.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    p_extras.set_defaults(func=cmd_extras)

    p_rendererdb = sub.add_parser(
        "rendererdb",
        help="Extract Windows renderer SQLite bodies linked from raw HONMON ID anchors.",
    )
    p_rendererdb.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_rendererdb.add_argument("--out-dir", type=Path, default=Path("logovista-rendererdb"))
    p_rendererdb.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    p_rendererdb.add_argument("--limit", type=int, help="Limit emitted body rows per dictionary.")
    p_rendererdb.add_argument(
        "--decrypted-db",
        type=Path,
        help="Use an existing plaintext SQLite database for a single dictionary instead of discovering/decrypting.",
    )
    p_rendererdb.add_argument(
        "--no-html",
        dest="include_html",
        action="store_false",
        help="Omit f_Html from JSONL rows and keep the plain/search body text only.",
    )
    p_rendererdb.add_argument("--write-media", action="store_true", help="Write media blobs from the sidecar database.")
    p_rendererdb.add_argument("--media-limit", type=int, help="Limit media blobs written.")
    p_rendererdb.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    p_rendererdb.set_defaults(include_html=True, func=cmd_rendererdb)

    p_spindex = sub.add_parser("spindex", help="Inspect standalone SPINDEX.DIC suffix-index resources.")
    p_spindex.add_argument("root", type=Path, nargs="*", help="Collection directory or direct SPINDEX.DIC path.")
    p_spindex.add_argument("--out-dir", type=Path, default=Path("logovista-spindex"))
    p_spindex.add_argument("--limit", type=int, help="Limit emitted internal rows per SPINDEX.DIC.")
    p_spindex.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    p_spindex.set_defaults(func=cmd_spindex)

    p_audit = sub.add_parser("audit-honmon", help="Audit raw HONMON/IDX readability without SQLite bodies.")
    p_audit.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_audit.add_argument("--out-dir", type=Path, default=Path("logovista-honmon-audit"))
    p_audit.add_argument("--dict", action="append", help="Only audit matching dictionary id(s).")
    p_audit.add_argument("--sample-limit", type=int, default=5)
    p_audit.add_argument("--max-slices", type=int, default=20000)
    p_audit.add_argument(
        "--max-id-records",
        type=int,
        default=50000,
        help="Probe at most N 32-byte HONMON records; 0 = full scan.",
    )
    p_audit.add_argument("--no-skip-dbc", dest="skip_dbc", action="store_false")
    p_audit.add_argument("--no-index-boundaries", dest="index_boundaries", action="store_false")
    p_audit.add_argument("--json", action="store_true", help="Also print machine-readable JSON.")
    p_audit.set_defaults(skip_dbc=True, index_boundaries=True, func=cmd_audit)

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
        "--renderer-sidecars",
        action="store_true",
        help="Also decrypt/use Windows renderer SQLite sidecars such as vlpljblb.",
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

    p_menus = sub.add_parser("menus", help="Extract MENU.DIC menu trees and destination pointers.")
    p_menus.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    p_menus.add_argument("--out-dir", type=Path, default=Path("logovista-raw-menus"))
    p_menus.add_argument("--limit", type=int, help="Limit emitted menu lines per component.")
    p_menus.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    p_menus.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    p_menus.set_defaults(func=cmd_menus)

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


if __name__ == "__main__":
    raise SystemExit(main())
