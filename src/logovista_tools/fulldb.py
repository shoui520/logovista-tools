"""Extract entries from LogoVista DictFULLDB using raw HONMON ID records.

Some LogoVista products ship a placeholder-like HONMON.DIC. The records are not
definition bodies, but they are not meaningless either: each 32-byte slot can
hold an 8-digit body id. DictList.plist can name the sibling "DictFULLDB"
payload that contains the corresponding formatted bodies.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .entries import DictionarySource, decode_tokens, discover_dictionaries, tokens_to_text
from .ssed import BLOCK_SIZE, expand_sseddata_file


ID_RE = re.compile(r"\d{1,12}")


@dataclass(frozen=True)
class HonmonIdRecord:
    data_id: int
    record_index: int
    block: int
    offset: int


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_dictlist_paths(source: DictionarySource) -> list[Path]:
    return [
        source.idx.parent / "DictList.plist",
        source.idx.parent.parent / "DictList.plist",
    ]


def find_fulldb(source: DictionarySource, *, allow_db_fallback: bool = False) -> Path | None:
    for plist_path in candidate_dictlist_paths(source):
        if not plist_path.exists():
            continue
        try:
            data = plistlib.load(plist_path.open("rb"))
        except Exception:
            continue
        items = data.get("ItemArray") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            rel = item.get("DictFULLDB")
            if not isinstance(rel, str) or not rel:
                continue
            candidate = plist_path.parent / rel
            if candidate.exists():
                return candidate

    if allow_db_fallback:
        for pattern in ("*.db", "*.sql"):
            matches = sorted(source.idx.parent.glob(pattern))
            if matches:
                return matches[0]
    return None


def iter_honmon_id_records(source: DictionarySource) -> Iterable[HonmonIdRecord]:
    expanded = expand_sseddata_file(source.honmon)
    for record_index, start in enumerate(range(0, len(expanded), 32)):
        record = expanded[start : start + 32]
        tokens, _stats = decode_tokens(record, gaiji="drop")
        text = tokens_to_text(tokens).strip()
        if not ID_RE.fullmatch(text):
            continue
        marker_offset = start + 2
        yield HonmonIdRecord(
            data_id=int(text),
            record_index=record_index,
            block=source.honmon_start_block + marker_offset // BLOCK_SIZE,
            offset=marker_offset % BLOCK_SIZE,
        )


def open_t_contents(db_path: Path) -> tuple[sqlite3.Connection, int]:
    con = sqlite3.connect(db_path)
    table = con.execute(
        "select name from sqlite_master where type='table' and name='t_contents'"
    ).fetchone()
    if table is None:
        con.close()
        raise ValueError(f"{db_path} has no t_contents table")
    row_count = con.execute("select count(*) from t_contents").fetchone()[0]
    return con, int(row_count)


def extract_fulldb_dictionary(
    source: DictionarySource, out_dir: Path, args: argparse.Namespace
) -> dict[str, Any]:
    db_path = find_fulldb(source, allow_db_fallback=getattr(args, "allow_db_fallback", False))
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    entries_path = dict_out / "fulldb_entries.jsonl"

    warnings: list[str] = []
    if db_path is None:
        warnings.append("No DictFULLDB payload declared in DictList.plist.")
        entries_path.write_text("", encoding="utf-8")
        summary = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "honmon": str(source.honmon),
            "fulldb": None,
            "entries_emitted": 0,
            "honmon_ids_seen": 0,
            "honmon_ids_missing_in_fulldb": 0,
            "warnings": warnings,
            "entries_path": str(entries_path),
        }
        write_json(dict_out / "fulldb_summary.json", summary)
        return summary

    con, body_row_count = open_t_contents(db_path)
    emitted = 0
    ids_seen = 0
    missing = 0
    seen_ids: set[int] = set()
    try:
        with entries_path.open("w", encoding="utf-8") as out:
            for id_record in iter_honmon_id_records(source):
                ids_seen += 1
                if id_record.data_id in seen_ids:
                    continue
                seen_ids.add(id_record.data_id)
                row = con.execute(
                    "select f_DataId, f_Type, f_Title, f_Title_SS, f_Html, f_Plane, f_Keyword "
                    "from t_contents where f_DataId=?",
                    (id_record.data_id,),
                ).fetchone()
                if row is None:
                    missing += 1
                    continue
                _data_id, f_type, title, title_ss, html, plain, keyword = row
                if args.limit and emitted >= args.limit:
                    break
                item = {
                    "dict_id": source.dict_id,
                    "dict_title": source.title,
                    "data_id": id_record.data_id,
                    "record_index": id_record.record_index,
                    "block": id_record.block,
                    "offset": id_record.offset,
                    "type": f_type,
                    "title": title,
                    "title_ss": title_ss,
                    "keyword": keyword,
                    "html": html,
                    "plain": plain,
                }
                out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")
                emitted += 1
    finally:
        con.close()

    summary = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "fulldb": str(db_path),
        "entries_emitted": emitted,
        "honmon_ids_seen": ids_seen,
        "honmon_ids_missing_in_fulldb": missing,
        "fulldb_t_contents_rows": body_row_count,
        "warnings": warnings,
        "entries_path": str(entries_path),
    }
    write_json(dict_out / "fulldb_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-fulldb-extract"))
    parser.add_argument("--limit", type=int, help="Limit emitted entries per dictionary.")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    parser.add_argument(
        "--allow-db-fallback",
        action="store_true",
        help="If DictList.plist has no DictFULLDB, try a neighboring .db/.sql file.",
    )
    args = parser.parse_args()

    sources = discover_dictionaries(args.root or [Path(".")])
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        raise SystemExit("no dictionaries found")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting fulldb {source.dict_id}: {source.title}")
        summary = extract_fulldb_dictionary(source, args.out_dir, args)
        summaries.append(summary)
        print(
            f"  entries={summary['entries_emitted']} "
            f"ids={summary['honmon_ids_seen']} missing={summary['honmon_ids_missing_in_fulldb']}"
        )
    write_json(args.out_dir / "summary.json", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
