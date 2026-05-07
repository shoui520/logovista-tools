"""Extract Windows renderer SQLite bodies through raw HONMON anchors."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import DictionarySource, discover_dictionaries
from .lvcrypto import decrypt_logofont_cipher_file_to_path
from .ssed import BLOCK_SIZE, expand_sseddata_file_with_storage
from .windows import RendererSidecar, discover_renderer_sidecars, load_exinfo_for_idx, sqlite_storage_for_path


JIS_SPACE = b"\x21\x21"
JIS_DIGIT_PREFIX = 0x23
SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass(frozen=True)
class HonmonIdRecord:
    data_id: int
    record_index: int
    record_offset: int
    block: int
    offset: int
    marker_block: int
    marker_offset: int


def parse_dense_honmon_id(record: bytes) -> int | None:
    """Return the decimal ID carried by one observed 32-byte HONMON anchor row."""

    if len(record) < 32:
        return None
    if record[2:6] != b"\x1f\x09\x00\x01" or record[10:12] != b"\x1f\x04":
        return None
    digits: list[str] = []
    for start in range(12, 28, 2):
        pair = record[start : start + 2]
        if pair == JIS_SPACE:
            continue
        if len(pair) != 2 or pair[0] != JIS_DIGIT_PREFIX or not 0x30 <= pair[1] <= 0x39:
            return None
        digits.append(chr(pair[1]))
    if not digits:
        return None
    return int("".join(digits))


def iter_honmon_id_records(expanded: bytes, *, honmon_start_block: int) -> Iterable[HonmonIdRecord]:
    for record_index, start in enumerate(range(0, len(expanded), 32)):
        data_id = parse_dense_honmon_id(expanded[start : start + 32])
        if data_id is None:
            continue
        record_block = honmon_start_block + start // BLOCK_SIZE
        record_offset = start % BLOCK_SIZE
        marker = start + 2
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


def table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"pragma table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def table_count(con: sqlite3.Connection, table: str) -> int | None:
    try:
        row = con.execute(f"select count(*) from {table}").fetchone()
    except sqlite3.DatabaseError:
        return None
    return int(row[0]) if row else None


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("select 1 from sqlite_master where type='table' and name=? limit 1", (table,)).fetchone()
    return row is not None


def blob_extension(blob: bytes) -> str:
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if blob.startswith(b"BM"):
        return "bmp"
    return "bin"


def safe_media_name(number: int, name: str | None, extension: str) -> str:
    stem = SAFE_NAME_RE.sub("_", name or f"media_{number}").strip("._")
    if not stem:
        stem = f"media_{number}"
    return f"{number:05d}_{stem}.{extension}"


def html_to_plain(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def prepare_sidecar_database(sidecar: RendererSidecar, dict_out: Path, args: argparse.Namespace) -> Path:
    if sidecar.storage == "plain":
        return sidecar.path
    dict_out.mkdir(parents=True, exist_ok=True)
    out = dict_out / f"{sidecar.path.name}.sqlite"
    decrypt_logofont_cipher_file_to_path(sidecar.path, out)
    return out


def write_media_records(con: sqlite3.Connection, media_dir: Path, *, limit: int | None = None) -> dict[str, Any]:
    if not table_exists(con, "media"):
        return {"media_rows": 0, "media_written": 0, "media_type_counts": {}}
    media_dir.mkdir(parents=True, exist_ok=True)
    rows = con.execute("select No, f_name, f_type, f_main from media order by No")
    type_counts: Counter[int] = Counter()
    written = 0
    for row in rows:
        number, name, media_type, blob = row
        type_counts[int(media_type)] += 1
        if limit is not None and written >= limit:
            continue
        blob = bytes(blob)
        extension = blob_extension(blob)
        (media_dir / safe_media_name(int(number), name, extension)).write_bytes(blob)
        written += 1
    return {
        "media_rows": sum(type_counts.values()),
        "media_written": written,
        "media_type_counts": {str(key): value for key, value in sorted(type_counts.items())},
    }


def media_type_counts(con: sqlite3.Connection) -> dict[str, int]:
    if not table_exists(con, "media"):
        return {}
    return {
        str(int(row[0])): int(row[1])
        for row in con.execute("select f_type, count(*) from media group by f_type order by f_type")
    }


def html_media_reference_rows(con: sqlite3.Connection) -> int | None:
    if not table_exists(con, "t_contents") or "f_Html" not in table_columns(con, "t_contents"):
        return None
    row = con.execute("select count(*) from t_contents where f_Html like '%class=\"media\"%'").fetchone()
    return int(row[0]) if row else None


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
        "entries_path": str(dict_out / "rendererdb_entries.jsonl"),
    }

    if not sidecars:
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
            summary.update({"status": "missing_t_contents", "entries_emitted": 0})
            return summary

        columns = table_columns(con, "t_contents")
        wanted = [
            "f_DataId",
            "f_Type",
            "f_DataGroupId",
            "f_Anchor",
            "f_Title",
            "f_Title_SS",
            "f_Html",
            "f_Keyword",
            "f_Plane",
        ]
        select_columns = [column for column in wanted if column in columns]
        query = f"select {', '.join(select_columns)} from t_contents order by f_DataId"
        emitted = 0
        matched_ids = 0
        extra_rows = 0
        type_counts: Counter[int] = Counter()
        group_count = 0
        group_ids: set[int] = set()
        with (dict_out / "rendererdb_entries.jsonl").open("w", encoding="utf-8") as out:
            for row in con.execute(query):
                data_id = int(row["f_DataId"])
                raw = ids_by_data_id.get(data_id)
                if raw is None:
                    extra_rows += 1
                    continue
                matched_ids += 1
                if row["f_Type"] is not None:
                    type_counts[int(row["f_Type"])] += 1
                if "f_DataGroupId" in row.keys() and row["f_DataGroupId"] is not None:
                    group_ids.add(int(row["f_DataGroupId"]))
                html_value = row["f_Html"] if "f_Html" in row.keys() else None
                plane = row["f_Plane"] if "f_Plane" in row.keys() else None
                if args.limit is None or emitted < args.limit:
                    record = {
                        "dict_id": source.dict_id,
                        "dict_title": source.title,
                        "data_id": data_id,
                        "raw_honmon": honmon_id_record_to_json(raw),
                        "type": row["f_Type"] if "f_Type" in row.keys() else None,
                        "data_group_id": row["f_DataGroupId"] if "f_DataGroupId" in row.keys() else None,
                        "anchor": row["f_Anchor"] if "f_Anchor" in row.keys() else None,
                        "title": row["f_Title"] if "f_Title" in row.keys() else None,
                        "title_search": row["f_Title_SS"] if "f_Title_SS" in row.keys() else None,
                        "keyword": row["f_Keyword"] if "f_Keyword" in row.keys() else None,
                        "plain": plane or (html_to_plain(html_value) if html_value else ""),
                    }
                    if args.include_html:
                        record["html"] = html_value
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    emitted += 1
        group_count = len(group_ids)
        content_rows = table_count(con, "t_contents")
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
                "media_rows": table_count(con, "media") if table_exists(con, "media") else None,
                "media_type_counts": media_type_counts(con),
                "html_rows_with_media_references": html_media_reference_rows(con),
            }
        )
        if args.write_media:
            summary.update(write_media_records(con, dict_out / "media", limit=args.media_limit))
    finally:
        con.close()

    (dict_out / "rendererdb_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def extract_rendererdb_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(args.root or [Path(".")])
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for source in sources:
        print(f"extracting rendererdb {source.dict_id}: {source.title}")
        row = extract_rendererdb_dictionary(source, args.out_dir, args)
        rows.append(row)
        print(
            f"  status={row['status']} raw_ids={row.get('raw_honmon_id_records', 0)} "
            f"emitted={row.get('entries_emitted', 0)} sidecars={len(row.get('sidecars', []))}"
        )
    (args.out_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows
