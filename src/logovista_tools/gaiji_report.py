"""SQL/DictFULLDB-assisted gaiji validation reports."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import (
    CONTROL_ARG_LENGTHS,
    DictionarySource,
    discover_dictionaries,
    iter_entry_slices,
    iter_entry_slices_with_boundaries,
)
from .fulldb import find_fulldb
from .gaiji import (
    candidate_gaiji_paths,
    file_identity,
    is_bitmap_gaiji_resource_name,
    iter_ga16_code_sources,
    load_plist_gaiji_map_from_paths,
    load_uni_gaiji_map,
    parse_ga16_resource,
    parse_uni_resource,
)
from .lvcrypto import decrypt_logofont_cipher_file_to_path
from .parallel import parallel_map_ordered, worker_args
from .resources import load_image_resource_profile
from .ssed import BLOCK_SIZE, expand_sseddata_file, find_case_insensitive, parse_ssedinfo
from .windows import discover_renderer_sidecars, load_exinfo_for_idx


SQLITE_MAGIC = b"SQLite format 3\x00"
SQLITE_PATTERNS = ("*.db", "*.sql", "*.sqlite")
SKIPPED_SQLITE_TABLES = {"android_metadata", "indexinfo"}


@dataclass(frozen=True)
class TextTable:
    name: str
    text_columns: tuple[str, ...]
    row_count: int
    has_block_offset: bool


@dataclass(frozen=True)
class SqliteSource:
    path: Path
    kind: str
    tables: tuple[TextTable, ...]


@dataclass(frozen=True)
class AlignedRowIndex:
    source: SqliteSource
    rows_by_block: dict[int, list[tuple[int, str]]]
    rows_indexed: int


def quote_sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalized_limit(value: int | None) -> int | None:
    if value is None or value == 0:
        return None
    return value


def is_sqlite_file(path: Path) -> bool:
    try:
        with path.open("rb") as infile:
            return infile.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def is_ga16_resource_name(path: Path) -> bool:
    return is_bitmap_gaiji_resource_name(path)


def is_text_column(name: str, declared_type: str) -> bool:
    lowered = name.lower()
    type_upper = declared_type.upper()
    if any(marker in type_upper for marker in ("TEXT", "CHAR", "CLOB", "VARCHAR")):
        return True
    return lowered in {
        "body",
        "title",
        "titlejis",
        "f_title",
        "f_title_ss",
        "f_html",
        "f_plane",
        "f_keyword",
        "f_contents",
        "f_midashi",
        "f_midashi_hyoki",
        "f_midashi_kana",
    }


def has_named_columns(columns: list[tuple[str, str]], names: set[str]) -> bool:
    present = {name.lower() for name, _declared_type in columns}
    return names <= present


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def inspect_sqlite_source(path: Path, kind: str) -> SqliteSource | None:
    if not is_sqlite_file(path):
        return None
    try:
        con = open_sqlite_readonly(path)
    except sqlite3.Error:
        return None
    try:
        table_names = [
            row[0]
            for row in con.execute(
                "select name from sqlite_master "
                "where type='table' and name not like 'sqlite_%' order by name"
            )
        ]
        tables: list[TextTable] = []
        for table_name in table_names:
            if table_name.lower() in SKIPPED_SQLITE_TABLES:
                continue
            columns = [
                (row[1], row[2] or "")
                for row in con.execute(f"pragma table_info({quote_sqlite_identifier(table_name)})")
            ]
            text_columns = tuple(name for name, declared_type in columns if is_text_column(name, declared_type))
            if not text_columns:
                continue
            try:
                row_count = int(
                    con.execute(f"select count(*) from {quote_sqlite_identifier(table_name)}").fetchone()[0]
                )
            except sqlite3.Error:
                row_count = 0
            tables.append(
                TextTable(
                    name=table_name,
                    text_columns=text_columns,
                    row_count=row_count,
                    has_block_offset=has_named_columns(columns, {"block", "offset"}),
                )
            )
        return SqliteSource(path=path, kind=kind, tables=tuple(tables))
    finally:
        con.close()


def candidate_sqlite_sources(
    source: DictionarySource,
    *,
    include_cache: bool = True,
    renderer_sidecar_dir: Path | None = None,
) -> tuple[SqliteSource, ...]:
    candidates: list[tuple[Path, str]] = []
    declared = find_fulldb(source, allow_db_fallback=False)
    if declared is not None:
        candidates.append((declared, "declared_fulldb"))

    if include_cache:
        for root in (source.idx.parent, source.idx.parent.parent):
            for pattern in SQLITE_PATTERNS:
                for path in sorted(root.glob(pattern)):
                    kind = "declared_fulldb" if declared is not None and path.resolve() == declared.resolve() else "sqlite_cache"
                    candidates.append((path, kind))

    if renderer_sidecar_dir is not None:
        exinfo = load_exinfo_for_idx(source.idx)
        for sidecar in discover_renderer_sidecars(source.idx, exinfo):
            if sidecar.storage == "plain":
                candidates.append((sidecar.path, "renderer_sidecar"))
                continue
            renderer_sidecar_dir.mkdir(parents=True, exist_ok=True)
            decrypted = renderer_sidecar_dir / f"{sidecar.path.name}.sqlite"
            if not decrypted.exists():
                decrypt_logofont_cipher_file_to_path(sidecar.path, decrypted)
            candidates.append((decrypted, "renderer_sidecar"))

    seen: set[Any] = set()
    sources: list[SqliteSource] = []
    for path, kind in candidates:
        identity = file_identity(path)
        if identity in seen:
            continue
        seen.add(identity)
        inspected = inspect_sqlite_source(path, kind)
        if inspected is not None and inspected.tables:
            sources.append(inspected)
    return tuple(sources)


def iter_gaiji_codes_in_stream(data: bytes) -> Iterable[str]:
    """Yield gaiji code bytes from an expanded text stream.

    This mirrors the text decoder's stream walk enough to avoid counting JIS
    pairs or known binary control payloads as gaiji.
    """

    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            i += 2 + CONTROL_ARG_LENGTHS.get(op, 0)
            continue
        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            i += 2
            continue
        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            yield f"{b:02x}{data[i + 1]:02x}"
            i += 2
            continue
        i += 1


def raw_gaiji_counts(source: DictionarySource) -> tuple[Counter[str], dict[str, Any]]:
    title, elements = parse_ssedinfo(source.idx)
    del title
    total: Counter[str] = Counter()
    components: dict[str, Any] = {}
    for element in elements:
        name = element.filename.upper()
        if name != "HONMON.DIC" and not name.endswith("TITLE.DIC"):
            continue
        component_path = find_case_insensitive(source.idx.parent, element.filename)
        if component_path is None:
            continue
        expanded = expand_sseddata_file(component_path)
        counts = Counter(iter_gaiji_codes_in_stream(expanded))
        total.update(counts)
        components[element.filename] = {
            "bytes": len(expanded),
            "gaiji_occurrences": sum(counts.values()),
            "distinct_gaiji": len(counts),
            "top": [{"code": code, "count": count} for code, count in counts.most_common(20)],
        }
    return total, components


def load_mapping_sources(source: DictionarySource) -> dict[str, Any]:
    uni_candidates, plist_candidates = candidate_gaiji_paths(source.idx)
    uni_map: dict[str, str] = {}
    uni_paths: list[Path] = []
    seen_uni: set[Any] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        mapping, _entries = load_uni_gaiji_map(path)
        if mapping:
            uni_paths.append(path)
            uni_map.update(mapping)

    plist_map, plist_paths = load_plist_gaiji_map_from_paths(plist_candidates)
    merged = dict(plist_map)
    merged.update(uni_map)
    return {
        "uni_map": uni_map,
        "plist_map": plist_map,
        "merged_map": merged,
        "uni_paths": tuple(uni_paths),
        "plist_paths": plist_paths,
    }


def bitmap_gaiji_codes(source: DictionarySource) -> dict[str, Any]:
    codes: dict[str, Any] = {}
    uni_candidates, _plist_candidates = candidate_gaiji_paths(source.idx)
    uni_resource = None
    seen_uni: set[Any] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        uni_resource = parse_uni_resource(path)
        if uni_resource is not None:
            break
    for path in sorted(source.idx.parent.iterdir()):
        if not path.is_file() or not is_ga16_resource_name(path):
            continue
        resource = parse_ga16_resource(path)
        if resource is None:
            continue
        data = path.read_bytes()
        for code, index, code_source in iter_ga16_code_sources(resource, uni_resource):
            glyph = resource.glyph_for_index(data, index)
            blank = glyph is not None and not any(glyph)
            row = {
                "resource": path.name,
                "width": resource.width,
                "height": resource.height,
                "glyph_index": index,
                "code_source": code_source,
                "blank": blank,
            }
            existing = codes.get(code)
            if (
                existing is None
                or (existing.get("blank") and not blank)
                or (
                    code_source == "uni_record_order"
                    and existing.get("code_source") == "jis_grid"
                    and bool(existing.get("blank")) == blank
                )
            ):
                codes[code] = row
    return codes


def informative_display(value: str | None) -> bool:
    if not value:
        return False
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        return False
    if len(value) == 1 and ord(value) < 128:
        return False
    return True


def compile_display_regexes(display_values: Iterable[str], *, chunk_size: int = 400) -> list[re.Pattern[str]]:
    values = sorted(set(display_values), key=lambda item: (-len(item), item))
    regexes: list[re.Pattern[str]] = []
    for start in range(0, len(values), chunk_size):
        chunk = values[start : start + chunk_size]
        if chunk:
            regexes.append(re.compile("|".join(re.escape(value) for value in chunk)))
    return regexes


def iter_sqlite_table_text(
    con: sqlite3.Connection,
    table: TextTable,
    *,
    max_rows: int | None = None,
) -> Iterable[str]:
    table_name = quote_sqlite_identifier(table.name)
    column_names = ", ".join(quote_sqlite_identifier(column) for column in table.text_columns)
    sql = f"select {column_names} from {table_name}"
    if max_rows is not None:
        sql += f" limit {int(max_rows)}"
    for row in con.execute(sql):
        parts = [value for value in row if isinstance(value, str) and value]
        if parts:
            yield "\n".join(parts)


def collect_sqlite_text_evidence(
    sqlite_sources: tuple[SqliteSource, ...],
    display_values: Iterable[str],
    *,
    max_rows_per_table: int | None = None,
) -> tuple[Counter[str], list[dict[str, Any]]]:
    regexes = compile_display_regexes(display_values)
    display_counts: Counter[str] = Counter()
    source_summaries: list[dict[str, Any]] = []
    if not regexes:
        return display_counts, source_summaries

    for source in sqlite_sources:
        rows_scanned = 0
        table_summaries = []
        try:
            con = open_sqlite_readonly(source.path)
        except sqlite3.Error as exc:
            source_summaries.append({"path": str(source.path), "kind": source.kind, "error": str(exc)})
            continue
        try:
            for table in source.tables:
                table_rows = 0
                for text in iter_sqlite_table_text(con, table, max_rows=max_rows_per_table):
                    table_rows += 1
                    for regex in regexes:
                        display_counts.update(match.group(0) for match in regex.finditer(text))
                rows_scanned += table_rows
                table_summaries.append(
                    {
                        "name": table.name,
                        "text_columns": list(table.text_columns),
                        "row_count": table.row_count,
                        "rows_scanned": table_rows,
                        "has_block_offset": table.has_block_offset,
                    }
                )
        finally:
            con.close()
        source_summaries.append(
            {
                "path": str(source.path),
                "kind": source.kind,
                "tables": table_summaries,
                "rows_scanned": rows_scanned,
            }
        )
    return display_counts, source_summaries


def build_aligned_row_indexes(sqlite_sources: tuple[SqliteSource, ...]) -> tuple[AlignedRowIndex, ...]:
    indexes: list[AlignedRowIndex] = []
    for source in sqlite_sources:
        rows_by_block: dict[int, list[tuple[int, str]]] = defaultdict(list)
        rows_indexed = 0
        try:
            con = open_sqlite_readonly(source.path)
        except sqlite3.Error:
            continue
        try:
            for table in source.tables:
                if not table.has_block_offset:
                    continue
                table_name = quote_sqlite_identifier(table.name)
                text_columns = ", ".join(quote_sqlite_identifier(column) for column in table.text_columns)
                sql = f"select Block, Offset, {text_columns} from {table_name}"
                for row in con.execute(sql):
                    try:
                        block = int(row[0])
                        offset = int(row[1])
                    except (TypeError, ValueError):
                        continue
                    text = "\n".join(value for value in row[2:] if isinstance(value, str) and value)
                    rows_by_block[block].append((offset, text))
                    rows_indexed += 1
        finally:
            con.close()
        for rows in rows_by_block.values():
            rows.sort(key=lambda item: item[0])
        if rows_indexed:
            indexes.append(AlignedRowIndex(source=source, rows_by_block=dict(rows_by_block), rows_indexed=rows_indexed))
    return tuple(indexes)


def nearest_aligned_text(
    indexes: tuple[AlignedRowIndex, ...],
    block: int,
    offset: int,
    *,
    tolerance: int,
) -> tuple[str, str, int] | None:
    best: tuple[int, str, str, int] | None = None
    for index in indexes:
        rows = index.rows_by_block.get(block)
        if not rows:
            continue
        for candidate_offset, text in rows:
            delta = abs(candidate_offset - offset)
            if delta > tolerance:
                continue
            if best is None or delta < best[0]:
                best = (delta, text, str(index.source.path), candidate_offset)
    if best is None:
        return None
    _delta, text, path, candidate_offset = best
    return text, path, candidate_offset


def aligned_cache_validation(
    source: DictionarySource,
    sqlite_sources: tuple[SqliteSource, ...],
    gaiji_map: dict[str, str],
    *,
    tolerance: int = 16,
    max_entries: int | None = None,
) -> tuple[dict[str, Any], dict[str, Counter[str]]]:
    indexes = build_aligned_row_indexes(sqlite_sources)
    summary = {
        "sources_indexed": [
            {"path": str(index.source.path), "kind": index.source.kind, "rows_indexed": index.rows_indexed}
            for index in indexes
        ],
        "index_entry_boundaries": 0,
        "boundary_warning": None,
        "entries_seen": 0,
        "entries_with_gaiji": 0,
        "rows_matched": 0,
        "rows_missing": 0,
        "hit_occurrences": 0,
        "miss_occurrences": 0,
        "skipped_uninformative_occurrences": 0,
        "alignment_tolerance": tolerance,
    }
    code_hits: Counter[str] = Counter()
    code_misses: Counter[str] = Counter()
    code_skipped: Counter[str] = Counter()
    if not indexes:
        return summary, {"hits": code_hits, "misses": code_misses, "skipped": code_skipped}

    expanded = expand_sseddata_file(source.honmon)
    boundary_offsets: set[int] = set()
    try:
        from .indexes import collect_index_body_offsets_for_idx

        boundary_offsets = collect_index_body_offsets_for_idx(
            source.idx,
            honmon_start_block=source.honmon_start_block,
            expanded_size=len(expanded),
        )
        summary["index_entry_boundaries"] = len(boundary_offsets)
    except Exception as exc:
        summary["boundary_warning"] = str(exc)
    slices = iter_entry_slices_with_boundaries(expanded, boundary_offsets) if boundary_offsets else iter_entry_slices(expanded)
    for start, end in slices:
        if max_entries is not None and summary["entries_seen"] >= max_entries:
            break
        summary["entries_seen"] += 1
        counts = Counter(iter_gaiji_codes_in_stream(expanded[start:end]))
        if not counts:
            continue
        summary["entries_with_gaiji"] += 1
        block = source.honmon_start_block + start // BLOCK_SIZE
        offset = start % BLOCK_SIZE
        matched = nearest_aligned_text(indexes, block, offset, tolerance=tolerance)
        if matched is None:
            summary["rows_missing"] += 1
            continue
        row_text, _db_path, _db_offset = matched
        summary["rows_matched"] += 1
        for code, count in counts.items():
            mapped = gaiji_map.get(code)
            if not informative_display(mapped):
                code_skipped[code] += count
                summary["skipped_uninformative_occurrences"] += count
                continue
            if mapped in row_text:
                code_hits[code] += count
                summary["hit_occurrences"] += count
            else:
                code_misses[code] += count
                summary["miss_occurrences"] += count
    return summary, {"hits": code_hits, "misses": code_misses, "skipped": code_skipped}


def classify_code(
    *,
    mapped: str | None,
    raw_count: int,
    bitmap: bool,
    image: bool,
    sql_text_hits: int,
    aligned_hits: int,
) -> str:
    if mapped:
        if aligned_hits:
            return "validated_aligned"
        if sql_text_hits:
            return "db_text_evidence"
        if not informative_display(mapped):
            return "mapped_unvalidated_uninformative"
        if raw_count == 0:
            return "mapped_unused_in_raw_scan"
        return "mapped_no_db_evidence"
    if image:
        return "image_asset_only"
    if bitmap:
        return "bitmap_asset_only"
    return "unresolved"


def build_code_rows(
    *,
    raw_counts: Counter[str],
    component_counts: dict[str, Any],
    mapping_sources: dict[str, Any],
    bitmap_codes: dict[str, Any],
    image_codes: set[str],
    sqlite_display_counts: Counter[str],
    aligned_counts: dict[str, Counter[str]],
    include_unused: bool,
) -> list[dict[str, Any]]:
    uni_map: dict[str, str] = mapping_sources["uni_map"]
    plist_map: dict[str, str] = mapping_sources["plist_map"]
    merged_map: dict[str, str] = mapping_sources["merged_map"]
    all_codes = set(raw_counts) | set(bitmap_codes) | image_codes
    if include_unused:
        all_codes |= set(merged_map)

    component_by_code: dict[str, dict[str, int]] = defaultdict(dict)
    for component_name, summary in component_counts.items():
        for row in summary.get("top", []):
            component_by_code[row["code"]][component_name] = row["count"]

    rows = []
    for code in sorted(all_codes):
        mapped = merged_map.get(code)
        source = "uni" if code in uni_map else "plist" if code in plist_map else None
        sql_hits = sqlite_display_counts.get(mapped or "", 0) if informative_display(mapped) else 0
        aligned_hits = aligned_counts["hits"].get(code, 0)
        status = classify_code(
            mapped=mapped,
            raw_count=raw_counts.get(code, 0),
            bitmap=code in bitmap_codes,
            image=code in image_codes,
            sql_text_hits=sql_hits,
            aligned_hits=aligned_hits,
        )
        rows.append(
            {
                "code": code,
                "placeholder": f"<{'h' if int(code, 16) < 0xB000 else 'z'}{code.upper()}>",
                "raw_count": raw_counts.get(code, 0),
                "component_top_counts": component_by_code.get(code, {}),
                "mapped": mapped,
                "mapping_source": source,
                "display_informative": informative_display(mapped),
                "bitmap": bitmap_codes.get(code),
                "image_asset": code in image_codes,
                "sqlite_text_hits_for_display": sql_hits,
                "aligned_hits": aligned_hits,
                "aligned_misses": aligned_counts["misses"].get(code, 0),
                "aligned_skipped_uninformative": aligned_counts["skipped"].get(code, 0),
                "status": status,
            }
        )
    rows.sort(key=lambda row: (-int(row["raw_count"]), row["status"], row["code"]))
    return rows


def extract_gaiji_report(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)

    mapping_sources = load_mapping_sources(source)
    raw_counts, component_counts = raw_gaiji_counts(source)
    bitmaps = bitmap_gaiji_codes(source)
    image_profile = load_image_resource_profile(source.idx)
    image_codes = set(image_profile.gaiji_image_keys)
    renderer_sidecar_dir = dict_out / "renderer-sidecars" if getattr(args, "renderer_sidecars", False) else None
    sqlite_sources = candidate_sqlite_sources(
        source,
        include_cache=not getattr(args, "no_sql_cache", False),
        renderer_sidecar_dir=renderer_sidecar_dir,
    )

    informative_values = [
        value
        for value in mapping_sources["merged_map"].values()
        if informative_display(value)
    ]
    sqlite_display_counts, sqlite_summaries = collect_sqlite_text_evidence(
        sqlite_sources,
        informative_values,
        max_rows_per_table=normalized_limit(getattr(args, "max_sql_rows", None)),
    )
    aligned_summary, aligned_counts = aligned_cache_validation(
        source,
        sqlite_sources,
        mapping_sources["merged_map"],
        tolerance=getattr(args, "alignment_tolerance", 16),
        max_entries=normalized_limit(getattr(args, "max_aligned_entries", None)),
    )
    code_rows = build_code_rows(
        raw_counts=raw_counts,
        component_counts=component_counts,
        mapping_sources=mapping_sources,
        bitmap_codes=bitmaps,
        image_codes=image_codes,
        sqlite_display_counts=sqlite_display_counts,
        aligned_counts=aligned_counts,
        include_unused=getattr(args, "include_unused_mapped", False),
    )
    status_counts = Counter(row["status"] for row in code_rows)
    unresolved_raw = [row for row in code_rows if row["raw_count"] and row["status"] == "unresolved"]

    report = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "gaiji": {
            "raw_occurrences": sum(raw_counts.values()),
            "raw_distinct_codes": len(raw_counts),
            "mapped_codes": len(mapping_sources["merged_map"]),
            "uni_mapped_codes": len(mapping_sources["uni_map"]),
            "plist_mapped_codes": len(mapping_sources["plist_map"]),
            "bitmap_codes": len(bitmaps),
            "image_codes": len(image_codes),
            "status_counts": dict(sorted(status_counts.items())),
            "unresolved_raw_codes": len(unresolved_raw),
        },
        "mapping_sources": {
            "uni_paths": [str(path) for path in mapping_sources["uni_paths"]],
            "plist_paths": [str(path) for path in mapping_sources["plist_paths"]],
        },
        "raw_components": component_counts,
        "sqlite_sources": sqlite_summaries,
        "aligned_cache_validation": aligned_summary,
        "codes": code_rows,
    }
    write_json(dict_out / "gaiji_report.json", report)
    return {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "report": str(dict_out / "gaiji_report.json"),
        "raw_distinct_codes": len(raw_counts),
        "raw_occurrences": sum(raw_counts.values()),
        "mapped_codes": len(mapping_sources["merged_map"]),
        "sqlite_sources": len(sqlite_sources),
        "aligned_hits": aligned_summary["hit_occurrences"],
        "aligned_misses": aligned_summary["miss_occurrences"],
        "status_counts": dict(sorted(status_counts.items())),
    }


def _gaiji_report_task(payload: tuple[DictionarySource, Path, argparse.Namespace]) -> dict[str, Any]:
    source, out_dir, args = payload
    return extract_gaiji_report(source, out_dir, args)


def extract_gaiji_reports(sources: list[DictionarySource], out_dir: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)
    summaries = parallel_map_ordered(
        _gaiji_report_task,
        [(source, out_dir, task_args) for source in sources],
        jobs=getattr(args, "jobs", 1),
    )
    write_json(out_dir / "summary.json", summaries)
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-gaiji-report"))
    parser.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    parser.add_argument("--no-sql-cache", action="store_true", help="Only use declared DictFULLDB SQLite sources.")
    parser.add_argument(
        "--renderer-sidecars",
        action="store_true",
        help="Also decrypt/use Windows renderer SQLite sidecars such as vlpljblb.",
    )
    parser.add_argument(
        "--max-sql-rows",
        type=int,
        default=5000,
        help="Limit scanned rows per SQLite table; use 0 for a full scan.",
    )
    parser.add_argument(
        "--max-aligned-entries",
        type=int,
        default=50000,
        help="Limit raw HONMON entries used for aligned checks; use 0 for a full scan.",
    )
    parser.add_argument("--alignment-tolerance", type=int, default=16, help="Block/offset match tolerance in bytes.")
    parser.add_argument("--include-unused-mapped", action="store_true", help="Include mapped codes not seen in raw scans.")
    args = parser.parse_args()

    sources = discover_dictionaries(args.root or [Path(".")])
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        raise SystemExit("no dictionaries found")

    summaries = extract_gaiji_reports(sources, args.out_dir, args)
    for summary in summaries:
        print(
            f"{summary['dict_id']:12s} raw={summary['raw_distinct_codes']:4d} "
            f"mapped={summary['mapped_codes']:4d} sqlite={summary['sqlite_sources']:2d} "
            f"aligned_hits={summary['aligned_hits']:6d} aligned_misses={summary['aligned_misses']:6d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
