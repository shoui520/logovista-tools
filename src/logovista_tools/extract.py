"""Interactive end-user extraction command for LogoVista dictionaries."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .cli_ux import dictionary_source_error, status
from .colscr import extract_colscr_for_source
from .entries import DictionarySource, discover_dictionaries, extract_dictionary, write_json
from .gaiji import ga16_preferred_code_for_index, parse_ga16_resource, parse_uni_resource
from .indexes import extract_indexes_for_idx
from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_bytes,
)
from .menus import extract_menus_for_idx
from .parallel import add_jobs_argument
from .pcmdata import extract_pcmdata_for_source
from .rendererdb import discover_android_body_databases, extract_rendererdb_dictionary
from .titles import extract_titles_for_idx
from .windows import (
    classify_vlpljbl_file,
    discover_renderer_sidecars,
    discover_vlpljbl_files,
    file_magic_kind,
    load_exinfo_for_idx,
    sqlite_storage_for_path,
)


FORMAT_CHOICES = ("json", "csv", "txt")
ALL_CATEGORIES = ("entries", "sqlite", "media", "indexes", "gaiji", "vlpljbl")
GA16_RESOURCE_NAMES = {
    "GA16HALF",
    "GA16FULL",
    "GAI16H",
    "GAI16F",
}


@dataclass(frozen=True)
class ExtractPlan:
    out_dir: Path
    categories: tuple[str, ...]
    formats: tuple[str, ...]
    proceed: bool


def add_extract_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="?", help="Dictionary directory, .IDX path, or collection root.")
    parser.add_argument("--out-dir", type=Path, help="Directory that will receive DICT_ID/ output trees.")
    parser.add_argument("--yes", action="store_true", help="Run non-interactively using selected options.")
    parser.add_argument("--all", action="store_true", help="Extract every supported category.")
    parser.add_argument("--entries", action="store_true", help="Extract dictionary entries.")
    parser.add_argument("--sqlite", action="store_true", help="Extract/copy applicable SQLite databases.")
    parser.add_argument("--media", action="store_true", help="Extract COLSCR/PCMDATA media.")
    parser.add_argument("--indexes", action="store_true", help="Extract titles, indexes, and menus.")
    parser.add_argument("--gaiji", action="store_true", help="Extract gaiji Unicode maps and GA16 glyph BMPs.")
    parser.add_argument("--vlpljbl", action="store_true", help="Extract non-executable vlpljbl resources.")
    parser.add_argument(
        "--formats",
        default="json,csv,txt",
        help="Comma-separated text output formats for entries/titles/indexes/menus: json,csv,txt.",
    )
    add_jobs_argument(parser)


def parse_formats(value: str) -> tuple[str, ...]:
    requested = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    invalid = sorted(set(requested) - set(FORMAT_CHOICES))
    if invalid:
        raise ValueError(f"extract: unsupported format(s): {', '.join(invalid)}")
    return requested or FORMAT_CHOICES


def selected_categories(args: argparse.Namespace) -> tuple[str, ...]:
    if args.all:
        return ALL_CATEGORIES
    selected = tuple(category for category in ALL_CATEGORIES if getattr(args, category, False))
    return selected


def ask(prompt: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def parse_category_answer(answer: str) -> tuple[str, ...]:
    answer = answer.strip().lower()
    if not answer or answer in {"a", "all"}:
        return ALL_CATEGORIES
    aliases = {
        "1": "entries",
        "e": "entries",
        "entries": "entries",
        "2": "sqlite",
        "s": "sqlite",
        "sqlite": "sqlite",
        "db": "sqlite",
        "3": "media",
        "m": "media",
        "media": "media",
        "4": "indexes",
        "i": "indexes",
        "index": "indexes",
        "indexes": "indexes",
        "titles": "indexes",
        "menus": "indexes",
        "5": "gaiji",
        "g": "gaiji",
        "gaiji": "gaiji",
        "6": "vlpljbl",
        "v": "vlpljbl",
        "vlpljbl": "vlpljbl",
        "resources": "vlpljbl",
    }
    selected: list[str] = []
    for part in answer.replace(",", " ").split():
        category = aliases.get(part)
        if category is None:
            raise ValueError(f"extract: unknown selection '{part}'")
        if category not in selected:
            selected.append(category)
    return tuple(selected)


def build_plan(args: argparse.Namespace, sources: list[DictionarySource]) -> ExtractPlan:
    default_out = args.out_dir or Path(".")
    formats = parse_formats(args.formats)
    categories = selected_categories(args)
    if args.yes:
        return ExtractPlan(default_out, categories or ALL_CATEGORIES, formats, True)

    print("logovista-tools extract")
    print(f"Found {len(sources)} dictionary package(s):")
    for source in sources:
        print(f"  {source.dict_id}: {source.title}")
    out_dir = Path(ask("Output directory", str(default_out)))
    print("Choose data to extract:")
    print("  all  entries, SQLite, media, titles/indexes/menus, gaiji, vlpljbl resources")
    print("  1    entries: full entries, using renderer SQLite sidecars automatically when needed")
    print("  2    SQLite: applicable database copies")
    print("  3    media: COLSCR images and PCMDATA audio")
    print("  4    indexes: all titles, indexes, and menus")
    print("  5    gaiji: Unicode map TSV and GA16 glyph BMPs")
    print("  6    vlpljbl: non-executable vlpljbl resources")
    categories = parse_category_answer(ask("Selection", "all"))
    formats = parse_formats(ask("Entry/index text formats", ",".join(formats)))
    proceed = ask("Proceed", "Y").lower() not in {"n", "no"}
    return ExtractPlan(out_dir, categories, formats, proceed)


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def line_text(value: Any) -> str:
    return " ".join(flatten(value).replace("\r", "\n").split())


def write_rows_json(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        for row in rows:
            if not first:
                out.write(",\n")
            out.write(json.dumps(row, ensure_ascii=False, indent=2))
            first = False
            count += 1
        out.write("\n]\n")
    return count


def write_rows_csv(path: Path, rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: flatten(row.get(field)) for field in fields})
            count += 1
    return count


def write_rows_txt(path: Path, rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as out:
        for row in rows:
            values = [line_text(row.get(field)) for field in fields]
            out.write("\t".join(values).rstrip())
            out.write("\n")
            count += 1
    return count


def convert_jsonl(
    jsonl_path: Path,
    out_base: Path,
    formats: tuple[str, ...],
    *,
    csv_fields: tuple[str, ...],
    txt_fields: tuple[str, ...],
) -> dict[str, Any]:
    outputs: dict[str, str] = {}
    counts: dict[str, int] = {}
    if "json" in formats:
        path = out_base.with_suffix(".json")
        counts["json"] = write_rows_json(path, read_jsonl(jsonl_path))
        outputs["json"] = str(path)
    if "csv" in formats:
        path = out_base.with_suffix(".csv")
        counts["csv"] = write_rows_csv(path, read_jsonl(jsonl_path), csv_fields)
        outputs["csv"] = str(path)
    if "txt" in formats:
        path = out_base.with_suffix(".txt")
        counts["txt"] = write_rows_txt(path, read_jsonl(jsonl_path), txt_fields)
        outputs["txt"] = str(path)
    return {"source_jsonl": str(jsonl_path), "outputs": outputs, "counts": counts}


def renderer_summary_is_usable(summary: dict[str, Any] | None) -> bool:
    if not summary:
        return False
    return str(summary.get("status", "")).startswith("ok") and int(summary.get("entries_emitted") or 0) > 0


def renderer_body_source_likely(source: DictionarySource) -> bool:
    exinfo = load_exinfo_for_idx(source.idx)
    if discover_renderer_sidecars(source.idx, exinfo):
        return True
    return bool(discover_android_body_databases(source.idx, source.dict_id))


def renderer_args(plan: ExtractPlan, jobs: int, *, limit: int | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        root=[],
        out_dir=plan.out_dir,
        dict=None,
        limit=limit,
        decrypted_db=None,
        include_html=True,
        write_media="vlpljbl" in plan.categories,
        media_limit=None,
        write_ziptomedia="vlpljbl" in plan.categories,
        ziptomedia_limit=None,
        jobs=jobs,
        verbose=False,
    )


def raw_entries_args(plan: ExtractPlan, jobs: int) -> argparse.Namespace:
    return argparse.Namespace(
        limit=None,
        min_chars=1,
        gaiji="placeholder",
        image_gaiji=True,
        media_placeholder=False,
        section_markers=False,
        html=True,
        section_image=None,
        skip_dense_marker_honmon=True,
        index_boundaries=False,
        full_scan=False,
        debug=False,
        out_dir=plan.out_dir,
        jobs=jobs,
        verbose=False,
    )


def extract_entries(
    source: DictionarySource,
    plan: ExtractPlan,
    *,
    jobs: int,
    renderer_summary: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    package_out = plan.out_dir / source.dict_id
    entries_out = package_out / "entries" / "entries"
    if renderer_summary is None and renderer_body_source_likely(source):
        renderer_summary = extract_rendererdb_dictionary(source, plan.out_dir, renderer_args(plan, jobs))
    if renderer_summary_is_usable(renderer_summary):
        jsonl = Path(str(renderer_summary["entries_path"]))
        converted = convert_jsonl(
            jsonl,
            entries_out,
            plan.formats,
            csv_fields=("dict_id", "data_id", "title", "title_plain", "keyword", "plain", "html"),
            txt_fields=("title_plain", "title", "plain"),
        )
        result = {
            "strategy": "renderer_sqlite_sidecar",
            "entries_emitted": renderer_summary.get("entries_emitted", 0),
            **converted,
        }
        return result, renderer_summary

    raw_summary = extract_dictionary(source, plan.out_dir, raw_entries_args(plan, jobs))
    jsonl = Path(str(raw_summary["entries_path"]))
    converted = convert_jsonl(
        jsonl,
        entries_out,
        plan.formats,
        csv_fields=("dict_id", "entry_index", "heading", "body", "body_html"),
        txt_fields=("heading", "body"),
    )
    result = {
        "strategy": "raw_honmon",
        "entries_emitted": raw_summary.get("entries_emitted", 0),
        "warnings": raw_summary.get("warnings", []),
        **converted,
    }
    return result, renderer_summary


def copy_sqlite_file(source_path: Path, out_dir: Path) -> Path | None:
    storage = sqlite_storage_for_path(source_path)
    if storage is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{source_path.stem}.sqlite"
    if storage == "plain":
        shutil.copy2(source_path, out)
    else:
        from .lvcrypto import decrypt_logofont_cipher_file_to_path

        decrypt_logofont_cipher_file_to_path(source_path, out)
    return out


def extract_sqlite_databases(
    source: DictionarySource,
    plan: ExtractPlan,
    *,
    jobs: int,
    renderer_summary: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    sqlite_dir = plan.out_dir / source.dict_id / "sqlite"
    copied: list[str] = []
    if renderer_summary is None and renderer_body_source_likely(source):
        renderer_summary = extract_rendererdb_dictionary(source, plan.out_dir, renderer_args(plan, jobs, limit=0))
    if renderer_summary and renderer_summary.get("sqlite_path"):
        copied_path = copy_sqlite_file(Path(str(renderer_summary["sqlite_path"])), sqlite_dir)
        if copied_path is not None:
            copied.append(str(copied_path))
    for child in sorted(source.idx.parent.iterdir()):
        if not child.is_file() or child.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        copied_path = copy_sqlite_file(child, sqlite_dir)
        if copied_path is not None and str(copied_path) not in copied:
            copied.append(str(copied_path))
    return {"files": copied, "count": len(copied)}, renderer_summary


def media_args(plan: ExtractPlan, jobs: int, *, kind: str) -> argparse.Namespace:
    return argparse.Namespace(
        root=[],
        out_dir=plan.out_dir,
        dict=None,
        limit=None,
        write_media=kind == "colscr",
        write_audio=kind == "pcmdata",
        include_unreferenced=True,
        json=False,
        jobs=jobs,
        verbose=False,
    )


def extract_media(source: DictionarySource, plan: ExtractPlan, *, jobs: int) -> dict[str, Any]:
    colscr_summary = extract_colscr_for_source(source, plan.out_dir, media_args(plan, jobs, kind="colscr"))
    pcm_summary = extract_pcmdata_for_source(source, plan.out_dir, media_args(plan, jobs, kind="pcmdata"))
    return {
        "colscr": colscr_summary,
        "pcmdata": pcm_summary,
    }


def title_args(plan: ExtractPlan, jobs: int) -> argparse.Namespace:
    return argparse.Namespace(out_dir=plan.out_dir, limit=None, gaiji="placeholder", dict=None, jobs=jobs, verbose=False)


def index_args(plan: ExtractPlan, jobs: int) -> argparse.Namespace:
    return argparse.Namespace(
        out_dir=plan.out_dir,
        limit=None,
        gaiji="placeholder",
        component=None,
        include_internal=True,
        dict=None,
        jobs=jobs,
        verbose=False,
    )


def extract_indexes_titles_menus(source: DictionarySource, plan: ExtractPlan, *, jobs: int) -> dict[str, Any]:
    titles_summary = extract_titles_for_idx(source.idx, plan.out_dir, title_args(plan, jobs))
    indexes_summary = extract_indexes_for_idx(source.idx, plan.out_dir, index_args(plan, jobs))
    menus_summary = extract_menus_for_idx(source.idx, plan.out_dir, title_args(plan, jobs))
    package_out = plan.out_dir / source.dict_id
    outputs = {
        "titles": convert_jsonl(
            Path(str(titles_summary["titles_path"])),
            package_out / "titles" / "titles",
            plan.formats,
            csv_fields=("dict_id", "component", "line_index", "text"),
            txt_fields=("component", "text"),
        ),
        "indexes": convert_jsonl(
            Path(str(indexes_summary["indexes_path"])),
            package_out / "indexes" / "indexes",
            plan.formats,
            csv_fields=("dict_id", "component", "kind", "key", "target_key", "body", "title"),
            txt_fields=("component", "kind", "key", "target_key"),
        ),
        "menus": convert_jsonl(
            Path(str(menus_summary["menus_path"])),
            package_out / "menus" / "menus",
            plan.formats,
            csv_fields=("dict_id", "component", "line_index", "depth", "text", "destination", "links"),
            txt_fields=("component", "depth", "text"),
        ),
    }
    return {"titles": titles_summary, "indexes": indexes_summary, "menus": menus_summary, "outputs": outputs}


def is_ga16_resource_path(path: Path) -> bool:
    name = path.name.upper()
    return name in GA16_RESOURCE_NAMES or name.startswith("GAI16H") or name.startswith("GAI16F")


def discover_ga16_resources(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if is_ga16_resource_path(path) else []
    return sorted(child for child in path.rglob("*") if child.is_file() and is_ga16_resource_path(child))


def bmp_bytes_from_ga16_glyph(glyph: bytes, width: int, height: int) -> bytes:
    row_in = (width + 7) // 8
    row_out = ((width * 3 + 3) // 4) * 4
    pixels = bytearray()
    for y in range(height - 1, -1, -1):
        row = bytearray()
        base = y * row_in
        for x in range(width):
            byte = glyph[base + x // 8]
            bit = (byte >> (7 - (x % 8))) & 1
            row.extend(b"\x00\x00\x00" if bit else b"\xff\xff\xff")
        row.extend(b"\x00" * (row_out - len(row)))
        pixels.extend(row)
    header_size = 14 + 40
    file_size = header_size + len(pixels)
    file_header = b"BM" + file_size.to_bytes(4, "little") + b"\x00\x00\x00\x00" + header_size.to_bytes(4, "little")
    dib = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + len(pixels).to_bytes(4, "little")
        + (2835).to_bytes(4, "little", signed=True)
        + (2835).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    return file_header + dib + bytes(pixels)


def extract_gaiji(source: DictionarySource, plan: ExtractPlan) -> dict[str, Any]:
    gaiji_dir = plan.out_dir / source.dict_id / "gaiji"
    gaiji_dir.mkdir(parents=True, exist_ok=True)
    map_path = gaiji_dir / "gaiji-map.tsv"
    with map_path.open("w", encoding="utf-8") as out:
        out.write("code\tunicode\n")
        for code, value in sorted(source.gaiji_map.items()):
            out.write(f"{code.upper()}\t{value}\n")

    glyph_dir = gaiji_dir / "ga16-bmp"
    glyph_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    resources = []
    for path in discover_ga16_resources(source.idx.parent):
        resource = parse_ga16_resource(path)
        if resource is None:
            continue
        uni_resource = None
        for candidate in sorted(path.parent.glob("*.uni")) + sorted(path.parent.glob("*.UNI")):
            uni_resource = parse_uni_resource(candidate)
            if uni_resource is not None:
                break
        data = path.read_bytes()
        component_dir = glyph_dir / path.name
        component_dir.mkdir(parents=True, exist_ok=True)
        resource_written = 0
        for index in range(resource.count):
            code, code_source = ga16_preferred_code_for_index(resource, index, uni_resource)
            glyph = resource.glyph_for_index(data, index)
            if glyph is None:
                continue
            (component_dir / f"{code.upper()}_{code_source}.bmp").write_bytes(
                bmp_bytes_from_ga16_glyph(glyph, resource.width, resource.height)
            )
            resource_written += 1
            written += 1
        resources.append(
            {
                "path": str(path),
                "width": resource.width,
                "height": resource.height,
                "count": resource.count,
                "bmp_written": resource_written,
            }
        )
    summary = {"map": str(map_path), "unicode_mappings": len(source.gaiji_map), "ga16_resources": resources, "bmp_written": written}
    write_json(gaiji_dir / "gaiji_summary.json", summary)
    return summary


def extension_for_kind(kind: str, fallback: str) -> str:
    return {
        "sqlite": ".sqlite",
        "pdf": ".pdf",
        "png": ".png",
        "zip": ".zip",
        "html": ".html",
        "opentype_cff": ".otf",
        "opentype_ttf": ".ttf",
        "riff": ".riff",
        "sseddata": ".dic",
    }.get(kind, fallback)


def safe_output_name(path: Path, kind: str) -> str:
    suffix = extension_for_kind(kind, path.suffix or ".bin")
    stem = path.name if path.suffix else path.name + suffix
    if Path(stem).suffix.lower() != suffix.lower():
        stem += suffix
    return stem


def extract_vlpljbl_resources(source: DictionarySource, plan: ExtractPlan) -> dict[str, Any]:
    out_dir = plan.out_dir / source.dict_id / "vlpljbl"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    written = 0
    for path in discover_vlpljbl_files([source.idx.parent]):
        classification = classify_vlpljbl_file(path, inspect_sqlite=True, compute_hash=False)
        row = {
            "path": str(path),
            "name": path.name,
            "storage": classification.storage,
            "content_kind": classification.content_kind,
            "role": classification.role,
            "written": None,
            "error": None,
        }
        if classification.content_kind == "pe_executable":
            rows.append(row)
            continue
        try:
            data = path.read_bytes()
            if classification.storage == "logofont_cipher":
                data = decrypt_logofont_cipher_bytes(data)
            kind = file_magic_kind(data)
            out_path = out_dir / safe_output_name(path, kind)
            out_path.write_bytes(data)
            row["written"] = str(out_path)
            written += 1
        except (OSError, LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError) as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    summary = {"resources_seen": len(rows), "resources_written": written, "rows": rows}
    write_json(out_dir / "vlpljbl_summary.json", summary)
    return summary


def extract_one_dictionary(source: DictionarySource, plan: ExtractPlan, *, jobs: int) -> dict[str, Any]:
    package_out = plan.out_dir / source.dict_id
    package_out.mkdir(parents=True, exist_ok=True)
    status(None, f"extract: {source.dict_id}: output {package_out}")
    summary: dict[str, Any] = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "out_dir": str(package_out),
        "categories": list(plan.categories),
        "formats": list(plan.formats),
    }
    renderer_summary: dict[str, Any] | None = None
    if "entries" in plan.categories:
        status(None, f"extract: {source.dict_id}: entries")
        summary["entries"], renderer_summary = extract_entries(source, plan, jobs=jobs, renderer_summary=renderer_summary)
    if "sqlite" in plan.categories:
        status(None, f"extract: {source.dict_id}: SQLite databases")
        summary["sqlite"], renderer_summary = extract_sqlite_databases(source, plan, jobs=jobs, renderer_summary=renderer_summary)
    if "media" in plan.categories:
        status(None, f"extract: {source.dict_id}: COLSCR/PCMDATA media")
        summary["media"] = extract_media(source, plan, jobs=jobs)
    if "indexes" in plan.categories:
        status(None, f"extract: {source.dict_id}: titles/indexes/menus")
        summary["indexes_titles_menus"] = extract_indexes_titles_menus(source, plan, jobs=jobs)
    if "gaiji" in plan.categories:
        status(None, f"extract: {source.dict_id}: gaiji map and GA16 BMPs")
        summary["gaiji"] = extract_gaiji(source, plan)
    if "vlpljbl" in plan.categories:
        status(None, f"extract: {source.dict_id}: vlpljbl resources")
        summary["vlpljbl"] = extract_vlpljbl_resources(source, plan)
    write_json(package_out / "extract-summary.json", summary)
    return summary


def cmd_extract(args: argparse.Namespace) -> int:
    root = args.root or Path(".")
    if not root.exists():
        raise FileNotFoundError(root)
    status(args, f"extract: discovering dictionary package(s) under {root}")
    sources = discover_dictionaries([root], jobs=args.jobs, include_gaiji=True, include_images=True)
    if not sources:
        raise ValueError(dictionary_source_error("extract", [root]))
    plan = build_plan(args, sources)
    if not plan.proceed:
        status(args, "extract: cancelled")
        return 1
    plan.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = [extract_one_dictionary(source, plan, jobs=args.jobs) for source in sources]
    write_json(plan.out_dir / "extract-summary.json", summaries)
    status(args, f"extract: wrote summary {plan.out_dir / 'extract-summary.json'}")
    return 0
