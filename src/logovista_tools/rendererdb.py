"""Extract Windows renderer SQLite bodies through raw HONMON anchors."""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import DictionarySource, discover_dictionaries, iter_entry_slices_with_boundaries
from .lvcrypto import decrypt_logofont_cipher_bytes, decrypt_logofont_cipher_file_to_path
from .parallel import parallel_map_ordered, worker_args
from .ssed import BLOCK_SIZE, expand_sseddata_file_with_storage
from .windows import RendererSidecar, discover_renderer_sidecars, load_exinfo_for_idx, sqlite_storage_for_path


JIS_SPACE = b"\x21\x21"
JIS_DIGIT_PREFIX = 0x23
SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")
ZIPTOMEDIA_RE = re.compile(r"lved\.ziptomedia:([^\"'>\s]+)")


@dataclass(frozen=True)
class HonmonIdRecord:
    data_id: int
    record_index: int
    record_offset: int
    block: int
    offset: int
    marker_block: int
    marker_offset: int


@dataclass(frozen=True)
class AndroidBodyDb:
    """An Android app body database keyed by ``rowid * 5`` raw HONMON IDs."""

    path: Path
    table: str


def parse_dense_honmon_id(record: bytes) -> int | None:
    """Return the decimal ID carried by one observed 32-byte HONMON anchor row."""

    parsed = parse_dense_honmon_id_with_marker(record)
    return parsed[0] if parsed else None


def parse_dense_honmon_id_with_marker(record: bytes) -> tuple[int, int] | None:
    """Return ``(data_id, marker_start)`` for one observed HONMON anchor row."""

    if len(record) < 32:
        return None
    for marker_start in (0, 2):
        if record[marker_start : marker_start + 4] != b"\x1f\x09\x00\x01":
            continue
        head_start = marker_start + 4
        text_start = marker_start + 8
        digits_start = marker_start + 10
        digits_end = marker_start + 26
        if record[head_start : head_start + 2] != b"\x1f\x41":
            continue
        if record[text_start : text_start + 2] != b"\x1f\x04":
            continue
        if record[digits_end : digits_end + 2] != b"\x1f\x05":
            continue
        digits: list[str] = []
        for start in range(digits_start, digits_end, 2):
            pair = record[start : start + 2]
            if pair == JIS_SPACE:
                continue
            if len(pair) != 2 or pair[0] != JIS_DIGIT_PREFIX or not 0x30 <= pair[1] <= 0x39:
                digits = []
                break
            digits.append(chr(pair[1]))
        if digits:
            return (int("".join(digits)), marker_start)
    return None


def iter_honmon_id_records(expanded: bytes, *, honmon_start_block: int) -> Iterable[HonmonIdRecord]:
    for record_index, start in enumerate(range(0, len(expanded), 32)):
        parsed = parse_dense_honmon_id_with_marker(expanded[start : start + 32])
        if parsed is None:
            continue
        data_id, marker_start = parsed
        record_block = honmon_start_block + start // BLOCK_SIZE
        record_offset = start % BLOCK_SIZE
        marker = start + marker_start
        marker_block = honmon_start_block + marker // BLOCK_SIZE
        marker_offset = marker % BLOCK_SIZE
        yield HonmonIdRecord(
            data_id=data_id,
            record_index=record_index,
            record_offset=start,
            block=record_block,
            offset=record_offset,
            marker_block=marker_block,
            marker_offset=marker_offset,
        )


def honmon_id_record_to_json(record: HonmonIdRecord) -> dict[str, Any]:
    return {
        "data_id": record.data_id,
        "record_index": record.record_index,
        "record_offset": record.record_offset,
        "block": record.block,
        "offset": record.offset,
        "marker_block": record.marker_block,
        "marker_offset": record.marker_offset,
    }


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_column_map(con: sqlite3.Connection, table: str) -> dict[str, str]:
    try:
        return {row[1].lower(): row[1] for row in con.execute(f"pragma table_info({quote_identifier(table)})")}
    except sqlite3.DatabaseError:
        return {}


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return set(table_column_map(con, table).values())


def table_count(con: sqlite3.Connection, table: str) -> int | None:
    try:
        row = con.execute(f"select count(*) from {quote_identifier(table)}").fetchone()
    except sqlite3.DatabaseError:
        return None
    return int(row[0]) if row else None


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("select 1 from sqlite_master where type='table' and name=? limit 1", (table,)).fetchone()
    return row is not None


def blob_extension(blob: bytes) -> str:
    if blob.startswith(b"RIFF") and blob[8:12] == b"WAVE":
        return "wav"
    if blob.startswith(b"ID3") or blob.startswith(b"\xff\xfb"):
        return "mp3"
    if blob.lstrip().lower().startswith(b"<svg"):
        return "svg"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if blob.startswith(b"BM"):
        return "bmp"
    return "bin"


def safe_media_name(number: int, name: str | None, extension: str, used: set[str] | None = None) -> str:
    stem = SAFE_NAME_RE.sub("_", name or f"media_{number}").strip("._")
    if not stem:
        stem = f"media_{number}"
    filename = stem if Path(stem).suffix else f"{stem}.{extension}"
    if used is not None and filename in used:
        filename = f"{number:05d}_{filename}"
    if used is not None:
        used.add(filename)
    return filename


def ziptomedia_reference_names(html: str | None) -> list[str]:
    if not html:
        return []
    return ZIPTOMEDIA_RE.findall(html)


def ziptomedia_stem(name: str) -> str:
    return name[:-4] if name.lower().endswith(".wav") else name


def discover_ziptomedia_dir(idx: Path) -> Path | None:
    candidates = [
        idx.parent / "Sound_Files",
        idx.parent / "sound",
        idx.parent / "sounds",
        idx.parent.parent / f"{idx.parent.name}_Sound_Files",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def ziptomedia_source_path(sound_dir: Path, reference: str) -> Path | None:
    candidates = [
        sound_dir / reference,
        sound_dir / ziptomedia_stem(reference),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def safe_ziptomedia_name(reference: str, data: bytes) -> str:
    name = SAFE_NAME_RE.sub("_", reference).strip("._")
    if not name:
        name = "audio"
    if "." not in Path(name).name:
        name = f"{name}.{blob_extension(data)}"
    return name


def write_ziptomedia_records(
    sound_dir: Path | None,
    refs: Counter[str],
    out_dir: Path,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    if sound_dir is None:
        return {"ziptomedia_written": 0}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    errors: list[dict[str, str]] = []
    for reference in sorted(refs):
        if limit is not None and written >= limit:
            break
        source = ziptomedia_source_path(sound_dir, reference)
        if source is None:
            continue
        try:
            data = decrypt_logofont_cipher_bytes(source.read_bytes())
        except Exception as exc:  # pragma: no cover - exercised by corpus probes.
            errors.append({"reference": reference, "error": str(exc)})
            continue
        (out_dir / safe_ziptomedia_name(reference, data)).write_bytes(data)
        written += 1
    return {
        "ziptomedia_written": written,
        "ziptomedia_write_errors": errors[:20],
    }


def summarize_ziptomedia_refs(idx: Path, refs: Counter[str]) -> dict[str, Any]:
    sound_dir = discover_ziptomedia_dir(idx)
    files = {path.name for path in sound_dir.iterdir() if path.is_file()} if sound_dir else set()
    ref_stems = {ziptomedia_stem(reference) for reference in refs}
    missing = sorted(stem for stem in ref_stems if stem not in files)
    unreferenced = sorted(files - ref_stems)
    return {
        "ziptomedia_dir": str(sound_dir) if sound_dir else None,
        "ziptomedia_references": sum(refs.values()),
        "ziptomedia_distinct_references": len(refs),
        "ziptomedia_files_available": len(ref_stems & files),
        "ziptomedia_missing_references": len(missing),
        "ziptomedia_missing_samples": missing[:20],
        "ziptomedia_unreferenced_files": len(unreferenced),
        "ziptomedia_unreferenced_samples": unreferenced[:20],
    }


def media_column_names(con: sqlite3.Connection) -> tuple[str, str, str | None, str] | None:
    table = media_table_name(con)
    if table is None:
        return None
    columns = table_column_map(con, table)
    if {"no", "f_name", "f_type", "f_main"} <= set(columns):
        return (columns["no"], columns["f_name"], columns["f_type"], columns["f_main"])
    if {"id", "name", "type", "main"} <= set(columns):
        return (columns["id"], columns["name"], columns["type"], columns["main"])
    if {"f_name", "f_blob"} <= set(columns):
        return ("rowid", columns["f_name"], None, columns["f_blob"])
    return None


def media_table_name(con: sqlite3.Connection) -> str | None:
    if table_exists(con, "media"):
        return "media"
    if table_exists(con, "t_media"):
        return "t_media"
    return None


def html_to_plain(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def prepare_sidecar_database(sidecar: RendererSidecar, dict_out: Path, args: argparse.Namespace) -> Path:
    if sidecar.storage == "plain":
        return sidecar.path
    dict_out.mkdir(parents=True, exist_ok=True)
    out = dict_out / f"{sidecar.path.name}.sqlite"
    decrypt_logofont_cipher_file_to_path(sidecar.path, out)
    return out


def write_media_records(con: sqlite3.Connection, media_dir: Path, *, limit: int | None = None) -> dict[str, Any]:
    media_table = media_table_name(con)
    media_columns = media_column_names(con)
    if media_table is None or media_columns is None:
        return {"media_rows": 0, "media_written": 0, "media_type_counts": {}}
    number_col, name_col, type_col, blob_col = media_columns
    media_dir.mkdir(parents=True, exist_ok=True)
    type_expr = "0 as _media_type" if type_col is None else quote_identifier(type_col)
    rows = con.execute(
        f"select {quote_identifier(number_col)}, {quote_identifier(name_col)}, "
        f"{type_expr}, {quote_identifier(blob_col)} "
        f"from {quote_identifier(media_table)} order by {quote_identifier(number_col)}"
    )
    type_counts: Counter[int] = Counter()
    written = 0
    used_names: set[str] = set()
    for row in rows:
        number, name, media_type, blob = row
        type_counts[int(media_type)] += 1
        if limit is not None and written >= limit:
            continue
        blob = bytes(blob)
        extension = blob_extension(blob)
        (media_dir / safe_media_name(int(number), name, extension, used_names)).write_bytes(blob)
        written += 1
    return {
        "media_rows": sum(type_counts.values()),
        "media_written": written,
        "media_type_counts": {str(key): value for key, value in sorted(type_counts.items())},
    }


def media_type_counts(con: sqlite3.Connection) -> dict[str, int]:
    media_table = media_table_name(con)
    media_columns = media_column_names(con)
    if media_table is None or media_columns is None:
        return {}
    _number_col, _name_col, type_col, _blob_col = media_columns
    if type_col is None:
        count = table_count(con, media_table) or 0
        return {"0": count} if count else {}
    return {
        str(int(row[0])): int(row[1])
        for row in con.execute(
            f"select {quote_identifier(type_col)}, count(*) from {quote_identifier(media_table)} "
            f"group by {quote_identifier(type_col)} order by {quote_identifier(type_col)}"
        )
    }


def t_contents_columns(con: sqlite3.Connection) -> dict[str, str]:
    columns = table_column_map(con, "t_contents")
    aliases = {
        "f_DataId": ("f_dataid", "f_data_id"),
        "f_Type": ("f_type",),
        "f_DataGroupId": ("f_datagroupid", "f_data_group_id"),
        "f_Anchor": ("f_anchor",),
        "f_Title": ("f_title", "f_midashi"),
        "f_Title_SS": ("f_title_ss", "f_title_sjis", "f_midashi_hyoki"),
        "f_Html": ("f_html", "f_contents", "f_body"),
        "f_Keyword": ("f_keyword",),
        "f_Plane": ("f_plane", "f_plain", "f_plane_text"),
        "f_Media": ("f_media",),
    }
    resolved: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            if candidate in columns:
                resolved[canonical] = columns[candidate]
                break
    return resolved


def honbun_columns(con: sqlite3.Connection) -> dict[str, str]:
    columns = table_column_map(con, "HONBUN")
    wanted = [
        "ID",
        "Title_UTF8",
        "Title_SJIS",
        "Contents_HTML_box",
        "Contents_HTML_list",
        "LEVEL1",
        "LEVEL2",
        "LEVEL3",
    ]
    return {canonical: columns[canonical.lower()] for canonical in wanted if canonical.lower() in columns}


def html_rows_matching(con: sqlite3.Connection, pattern: str) -> int | None:
    if not table_exists(con, "t_contents"):
        return None
    html_col = t_contents_columns(con).get("f_Html")
    if html_col is None:
        return None
    row = con.execute(
        f"select count(*) from t_contents where {quote_identifier(html_col)} like ?",
        (pattern,),
    ).fetchone()
    return int(row[0]) if row else None


def html_media_reference_rows(con: sqlite3.Connection) -> int | None:
    return html_rows_matching(con, '%class="media"%')


def html_ziptomedia_reference_rows(con: sqlite3.Connection) -> int | None:
    return html_rows_matching(con, "%lved.ziptomedia:%")


def is_android_body_database(path: Path, dict_id: str) -> bool:
    if sqlite_storage_for_path(path) != "plain":
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return table_exists(con, dict_id) and "Html" in table_columns(con, dict_id)
    finally:
        con.close()


def discover_android_body_databases(idx: Path, dict_id: str) -> list[AndroidBodyDb]:
    rows: list[AndroidBodyDb] = []
    for child in sorted(idx.parent.iterdir()):
        if not child.is_file() or child.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        if is_android_body_database(child, dict_id):
            rows.append(AndroidBodyDb(path=child.resolve(), table=dict_id))
    return rows


def extract_android_body_database(
    source: DictionarySource,
    summary: dict[str, Any],
    raw_ids: list[HonmonIdRecord],
    ids_by_data_id: dict[int, HonmonIdRecord],
    body_db: AndroidBodyDb,
    dict_out: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    con = sqlite3.connect(body_db.path)
    try:
        con.row_factory = sqlite3.Row
        emitted = 0
        matched_ids = 0
        with (dict_out / "rendererdb_entries.jsonl").open("w", encoding="utf-8") as out:
            for row in con.execute(f"select rowid as _rowid, Html from {quote_identifier(body_db.table)} order by rowid"):
                db_rowid = int(row["_rowid"])
                data_id = db_rowid * 5
                raw = ids_by_data_id.get(data_id)
                if raw is None:
                    continue
                matched_ids += 1
                html_value = row["Html"] or ""
                if args.limit is None or emitted < args.limit:
                    record = {
                        "dict_id": source.dict_id,
                        "dict_title": source.title,
                        "data_id": data_id,
                        "raw_honmon": honmon_id_record_to_json(raw),
                        "android_rowid": db_rowid,
                        "type": None,
                        "title": None,
                        "plain": html_to_plain(html_value),
                    }
                    if args.include_html:
                        record["html"] = html_value
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    emitted += 1
        summary.update(
            {
                "status": "ok_android_body_db",
                "android_body_db": str(body_db.path),
                "android_body_table": body_db.table,
                "android_body_id_rule": "data_id = rowid * 5",
                "android_body_rows": table_count(con, body_db.table),
                "entries_matched_to_raw_honmon": matched_ids,
                "entries_emitted": emitted,
                "raw_honmon_ids_missing_in_db": max(0, len(raw_ids) - matched_ids),
                "media_rows": table_count(con, media_table_name(con)) if media_table_name(con) else None,
                "media_type_counts": media_type_counts(con),
                "html_rows_with_media_references": None,
            }
        )
        if args.write_media:
            summary.update(write_media_records(con, dict_out / "media", limit=args.media_limit))
        return summary
    finally:
        con.close()


def extract_honbun_ordered_database(
    source: DictionarySource,
    summary: dict[str, Any],
    expanded: bytes,
    db_path: Path,
    con: sqlite3.Connection,
    dict_out: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    columns = honbun_columns(con)
    id_col = columns.get("ID")
    title_col = columns.get("Title_UTF8")
    html_box_col = columns.get("Contents_HTML_box")
    html_list_col = columns.get("Contents_HTML_list")
    if id_col is None or title_col is None or html_box_col is None:
        summary.update({"status": "unsupported_honbun_schema", "entries_emitted": 0})
        return summary

    raw_slices = list(iter_entry_slices_with_boundaries(expanded))
    select_parts = [
        f"{quote_identifier(actual)} as {quote_identifier(canonical)}"
        for canonical, actual in columns.items()
    ]
    query = f"select {', '.join(select_parts)} from HONBUN order by {quote_identifier(id_col)}"
    emitted = 0
    row_count = 0
    ziptomedia_refs: Counter[str] = Counter()
    with (dict_out / "rendererdb_entries.jsonl").open("w", encoding="utf-8") as out:
        for row_count, ((start, end), row) in enumerate(zip(raw_slices, con.execute(query)), start=1):
            row_keys = row.keys()
            html_value = row["Contents_HTML_box"] if "Contents_HTML_box" in row_keys else None
            html_list = row["Contents_HTML_list"] if "Contents_HTML_list" in row_keys else None
            title_value = row["Title_UTF8"] if "Title_UTF8" in row_keys else None
            html_for_plain = html_value or html_list or ""
            ziptomedia_refs.update(ziptomedia_reference_names(html_for_plain))
            if args.limit is None or emitted < args.limit:
                record = {
                    "dict_id": source.dict_id,
                    "dict_title": source.title,
                    "id": row["ID"] if "ID" in row_keys else None,
                    "raw_honmon": {
                        "entry_index": row_count,
                        "start_offset": start,
                        "end_offset": end,
                        "start_block": source.honmon_start_block + start // BLOCK_SIZE,
                        "start_block_offset": start % BLOCK_SIZE,
                    },
                    "type": "HONBUN",
                    "title": title_value,
                    "title_search": row["Title_SJIS"] if "Title_SJIS" in row_keys else None,
                    "levels": [
                        row[column]
                        for column in ("LEVEL1", "LEVEL2", "LEVEL3")
                        if column in row_keys and row[column]
                    ],
                    "plain": html_to_plain(html_for_plain),
                }
                if args.include_html:
                    record["html"] = html_value
                    if html_list is not None:
                        record["html_list"] = html_list
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                emitted += 1

    content_rows = table_count(con, "HONBUN") or 0
    ziptomedia_summary = summarize_ziptomedia_refs(source.idx, ziptomedia_refs)
    summary.update(
        {
            "status": "ok_honbun_ordered",
            "sqlite_path": str(db_path),
            "honbun_rows": content_rows,
            "raw_honmon_entry_slices": len(raw_slices),
            "row_order_matches_raw_entries": content_rows == len(raw_slices),
            "entries_matched_to_raw_honmon": min(content_rows, len(raw_slices)),
            "entries_emitted": emitted,
            "db_rows_without_raw_honmon_entry": max(0, content_rows - len(raw_slices)),
            "raw_honmon_entries_missing_in_db": max(0, len(raw_slices) - content_rows),
            "media_table": media_table_name(con),
            "media_rows": table_count(con, media_table_name(con)) if media_table_name(con) else None,
            "media_type_counts": media_type_counts(con),
            "html_rows_with_media_references": None,
            "html_rows_with_ziptomedia_references": None,
            **ziptomedia_summary,
        }
    )
    if args.write_media:
        summary.update(write_media_records(con, dict_out / "media", limit=args.media_limit))
    if args.write_ziptomedia:
        sound_dir = Path(ziptomedia_summary["ziptomedia_dir"]) if ziptomedia_summary["ziptomedia_dir"] else None
        summary.update(
            write_ziptomedia_records(
                sound_dir,
                ziptomedia_refs,
                dict_out / "ziptomedia",
                limit=args.ziptomedia_limit,
            )
        )
    return summary


def extract_rendererdb_dictionary(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    expanded, honmon_storage = expand_sseddata_file_with_storage(source.honmon)
    raw_ids = list(iter_honmon_id_records(expanded, honmon_start_block=source.honmon_start_block))
    ids_by_data_id = {record.data_id: record for record in raw_ids}

    exinfo = load_exinfo_for_idx(source.idx)
    if args.decrypted_db is not None:
        storage = sqlite_storage_for_path(args.decrypted_db)
        sidecars = [RendererSidecar(path=args.decrypted_db, storage=storage or "plain")]
    else:
        sidecars = discover_renderer_sidecars(source.idx, exinfo)
    android_body_dbs = [] if sidecars else discover_android_body_databases(source.idx, source.dict_id)

    summary: dict[str, Any] = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "honmon_storage": honmon_storage,
        "expanded_bytes": len(expanded),
        "raw_honmon_id_records": len(raw_ids),
        "raw_honmon_id_samples": [honmon_id_record_to_json(record) for record in raw_ids[:10]],
        "exinfo": str(exinfo.path) if exinfo else None,
        "sidecars": [{"path": str(row.path), "storage": row.storage} for row in sidecars],
        "android_body_dbs": [{"path": str(row.path), "table": row.table} for row in android_body_dbs],
        "entries_path": str(dict_out / "rendererdb_entries.jsonl"),
    }

    if not sidecars:
        if android_body_dbs:
            summary = extract_android_body_database(
                source,
                summary,
                raw_ids,
                ids_by_data_id,
                android_body_dbs[0],
                dict_out,
                args,
            )
        else:
            summary.update({"status": "no_renderer_sqlite_sidecar", "entries_emitted": 0})
        (dict_out / "rendererdb_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    sidecar = sidecars[0]
    db_path = prepare_sidecar_database(sidecar, dict_out, args)
    summary["sqlite_path"] = str(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        if not table_exists(con, "t_contents"):
            if table_exists(con, "HONBUN"):
                summary = extract_honbun_ordered_database(source, summary, expanded, db_path, con, dict_out, args)
                (dict_out / "rendererdb_summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return summary
            summary.update({"status": "missing_t_contents", "entries_emitted": 0})
            (dict_out / "rendererdb_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return summary

        columns = t_contents_columns(con)
        data_id_col = columns.get("f_DataId")
        if data_id_col is None:
            summary.update({"status": "missing_t_contents_data_id", "entries_emitted": 0})
            (dict_out / "rendererdb_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return summary
        select_columns = [
            f"{quote_identifier(actual)} as {quote_identifier(canonical)}"
            for canonical, actual in columns.items()
        ]
        query = f"select {', '.join(select_columns)} from t_contents order by {quote_identifier(data_id_col)}"
        emitted = 0
        matched_ids = 0
        extra_rows = 0
        type_counts: Counter[int] = Counter()
        ziptomedia_refs: Counter[str] = Counter()
        group_count = 0
        group_ids: set[int] = set()
        with (dict_out / "rendererdb_entries.jsonl").open("w", encoding="utf-8") as out:
            for row in con.execute(query):
                row_keys = row.keys()
                data_id = int(row["f_DataId"])
                raw = ids_by_data_id.get(data_id)
                if raw is None:
                    extra_rows += 1
                    continue
                matched_ids += 1
                if "f_Type" in row_keys and row["f_Type"] is not None:
                    type_counts[int(row["f_Type"])] += 1
                if "f_DataGroupId" in row_keys and row["f_DataGroupId"] is not None:
                    group_ids.add(int(row["f_DataGroupId"]))
                html_value = row["f_Html"] if "f_Html" in row_keys else None
                title_value = row["f_Title"] if "f_Title" in row_keys else None
                plane = row["f_Plane"] if "f_Plane" in row_keys else None
                media_value = row["f_Media"] if "f_Media" in row_keys else None
                ziptomedia_refs.update(ziptomedia_reference_names(html_value))
                if args.limit is None or emitted < args.limit:
                    record = {
                        "dict_id": source.dict_id,
                        "dict_title": source.title,
                        "data_id": data_id,
                        "raw_honmon": honmon_id_record_to_json(raw),
                        "type": row["f_Type"] if "f_Type" in row_keys else None,
                        "data_group_id": row["f_DataGroupId"] if "f_DataGroupId" in row_keys else None,
                        "anchor": row["f_Anchor"] if "f_Anchor" in row_keys else None,
                        "title": title_value,
                        "title_plain": html_to_plain(title_value) if title_value else None,
                        "title_search": row["f_Title_SS"] if "f_Title_SS" in row_keys else None,
                        "keyword": row["f_Keyword"] if "f_Keyword" in row_keys else None,
                        "media": media_value,
                        "plain": plane or (html_to_plain(html_value) if html_value else ""),
                    }
                    if args.include_html:
                        record["html"] = html_value
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    emitted += 1
        group_count = len(group_ids)
        content_rows = table_count(con, "t_contents")
        media_table = media_table_name(con)
        media_col = columns.get("f_Media")
        rows_with_media_field = None
        if media_col is not None:
            media_row = con.execute(
                f"select count(*) from t_contents where {quote_identifier(media_col)} is not null "
                f"and {quote_identifier(media_col)} <> ''"
            ).fetchone()
            rows_with_media_field = int(media_row[0]) if media_row else 0
        ziptomedia_summary = summarize_ziptomedia_refs(source.idx, ziptomedia_refs)
        summary.update(
            {
                "status": "ok",
                "t_contents_rows": content_rows,
                "entries_matched_to_raw_honmon": matched_ids,
                "entries_emitted": emitted,
                "db_rows_without_raw_honmon_id": extra_rows,
                "raw_honmon_ids_missing_in_db": max(0, len(raw_ids) - matched_ids),
                "type_counts": {str(key): value for key, value in sorted(type_counts.items())},
                "data_group_ids_matched": group_count,
                "t_bunya_rows": table_count(con, "t_bunya") if table_exists(con, "t_bunya") else None,
                "media_table": media_table,
                "media_rows": table_count(con, media_table) if media_table else None,
                "media_type_counts": media_type_counts(con),
                "rows_with_media_field": rows_with_media_field,
                "html_rows_with_media_references": html_media_reference_rows(con),
                "html_rows_with_ziptomedia_references": html_ziptomedia_reference_rows(con),
                **ziptomedia_summary,
            }
        )
        if args.write_media:
            summary.update(write_media_records(con, dict_out / "media", limit=args.media_limit))
        if args.write_ziptomedia:
            sound_dir = Path(ziptomedia_summary["ziptomedia_dir"]) if ziptomedia_summary["ziptomedia_dir"] else None
            summary.update(
                write_ziptomedia_records(
                    sound_dir,
                    ziptomedia_refs,
                    dict_out / "ziptomedia",
                    limit=args.ziptomedia_limit,
                )
            )
    finally:
        con.close()

    (dict_out / "rendererdb_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _rendererdb_dictionary_task(payload: tuple[DictionarySource, Path, argparse.Namespace]) -> dict[str, Any]:
    source, out_dir, args = payload
    return extract_rendererdb_dictionary(source, out_dir, args)


def extract_rendererdb_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(args.root or [Path(".")], jobs=getattr(args, "jobs", 1))
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)

    def log_summary(row: dict[str, Any]) -> None:
        print(
            f"{row['dict_id']:12s} status={row['status']} raw_ids={row.get('raw_honmon_id_records', 0)} "
            f"emitted={row.get('entries_emitted', 0)} sidecars={len(row.get('sidecars', []))}"
        )

    rows = parallel_map_ordered(
        _rendererdb_dictionary_task,
        [(source, args.out_dir, task_args) for source in sources],
        jobs=getattr(args, "jobs", 1),
        on_result=log_summary,
    )
    (args.out_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows
