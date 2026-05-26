"""Inspection helpers for modern LogoVista SQLCipher dictionary packages.

``lved`` is kept as the command/module name because the recovered viewer and
older reports use that shorthand.  The payload family itself is modeled as a
SQLCipher package family, not as a variant of SSED.
"""

from __future__ import annotations

import hashlib
import html
import math
import re
import sqlite3
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable
from urllib.parse import parse_qsl

from .colscr import parse_jpeg_dimensions
from .indexes import scan_index_component
from .parallel import parallel_map_ordered
from .ssed import expand_sseddata_file, parse_sseddata_header


LVED_SQLCIPHER_PAGE_SIZE = 4096
LVED_SQLCIPHER_RESERVE_BYTES = 80
LVED_SQLCIPHER_KDF_ITER = 256000
LVED_SQLCIPHER_KDF_ALGORITHM = "PBKDF2-HMAC-SHA512"
LVED_SQLITE3_FAMILY_NAME = "LVED_SQLITE3"

_SQLITE_TEXT_SIGNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("html_tag", "%<%>%"),
    ("html_img", "%<img%"),
    ("html_table", "%<table%"),
    ("html_ruby", "%<ruby%"),
    ("html_math", "%<math%"),
    ("html_script", "%<script%"),
    ("lved_dataid", "%lved.dataid:%"),
    ("lved_dataid_result", "%lved.dataid.result:%"),
    ("lved_dataid_dict", "%lved.dataid.dict.%"),
    ("lved_dataid_no_colon", "%lved.dataid0%"),
    ("lved_contentlink", "%lved.contentlink:%"),
    ("lved_addr", "%lved.addr%"),
    ("lved_image", "%lved.image:%"),
    ("lved_imag", "%lved.imag:%"),
    ("lved_media_sound", "%lved.media.sound:%"),
    ("lved_media", "%lved.media:%"),
    ("lved_ziptomedia", "%lved.ziptomedia:%"),
    ("lved_bookmark", "%lved.bookmark:%"),
    ("lved_plugin", "%lved.plugin:%"),
    ("lved_sql", "%lved.sql:%"),
    ("lved_pdf", "%lved.pdf:%"),
    ("lved_dict", "%lved.dict.%"),
    ("lved_findnum", "%lved.findnum:%"),
    ("lved_select", "%lved.select:%"),
    ("lved_group", "%lved.group.%"),
    ("lved_browser", "%lved.browser.%"),
    ("lved_sound", "%lved.sond:%"),
    ("http_link", "%http://%"),
    ("https_link", "%https://%"),
)

_RETAINED_RESOURCE_NAMES = {"ga16half", "ga16full"}
_RETAINED_RESOURCE_SUFFIXES = (".dic", ".idx", ".uni")
_LOOSE_IMAGE_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"}
_LOOSE_FONT_SUFFIXES = {".otf", ".ttf", ".ttc", ".woff", ".woff2"}
_LOOSE_WEB_TEXT_SUFFIXES = {".css", ".htm", ".html", ".js", ".json", ".md", ".txt", ".yaml", ".yml"}
_PAYLOAD_NAMES = {"main.data"}
_LVED_SEARCH_MODES = {"exact", "forward", "backward", "partial", "fulltext", "filter", "advanced", "advanced1", "advanced2", "advanced3"}
_LVED_PROTOCOL_RE = re.compile(r"""(?P<attr>href|src)\s*=\s*(?P<quote>["'])(?P<value>[^"']+)(?P=quote)""", re.IGNORECASE)
_HTML_SRC_RE = re.compile(r"""src\s*=\s*(["'])([^"']+)\1""", re.IGNORECASE)
_HTML_HREF_RE = re.compile(r"""href\s*=\s*(["'])([^"']+)\1""", re.IGNORECASE)
_HTML_ANCHOR_NAME_RE = re.compile(r"""<a\s+[^>]*name\s*=\s*(["'])(?P<name>[^"']+)\1""", re.IGNORECASE)
_LVED_ADDR_RE = re.compile(r"""lved\.addr(?P<block>[0-9A-Fa-f]{8}):(?P<offset>[0-9A-Fa-f]{4})""", re.IGNORECASE)
_LVED_DATAID_NO_COLON_RE = re.compile(r"""lved\.dataid(?P<id>\d+)(?:#(?P<anchor>[^?#]+))?""", re.IGNORECASE)
_LVED_CONTENTLINK_RE = re.compile(
    r"""lved\.contentlink:(?P<dict>[A-Za-z0-9_]+)\.(?P<id>\d+)(?:#(?P<anchor>[^?#]+))?""",
    re.IGNORECASE,
)
_LVED_DATAID_DICT_RE = re.compile(
    r"""lved\.dataid\.dict\.(?P<dict>[A-Za-z0-9_]+):(?P<id>\d+)(?:#(?P<anchor>[^?#]+))?""",
    re.IGNORECASE,
)
_LVED_RELATIVE_ADDR_RE = re.compile(r"""[0-9A-Fa-f]{6}/[0-9A-Fa-f]{4}""", re.ASCII)
_LVED_TARGET_SPLIT_RE = re.compile(r"[?#]", re.ASCII)
_LVED_PROTOCOL_TOKEN_RE = re.compile(r"lved\.[a-z]+(?:\.[a-z]+)?:", re.IGNORECASE)
_LVED_CORE_TABLES = {"list", "content", "media", "info", "search"}


@dataclass(frozen=True)
class LvedPayload:
    path: Path
    kind: str
    size: int
    pages_4096: int
    mod_4096: int
    sha256: str
    entropy_sample: float
    header_hex: str
    classification: str
    inferred_dict_code: str | None


@dataclass(frozen=True)
class LvedKeyFile:
    path: Path
    match: str


@dataclass(frozen=True)
class LvedTextTreeRow:
    line_no: int
    raw_target: str
    depth: int
    label: str
    target_value: int | None
    target_form: str
    query_params: tuple[tuple[str, str], ...] = ()

    @property
    def is_entry_target(self) -> bool:
        return self.target_value is not None and self.target_value > 0


@dataclass(frozen=True)
class LvedAddrResolution:
    content_id: int
    anchor: str
    delta: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts if count)


def entropy_sample_for_file(path: Path, *, sample_size: int = 1024 * 1024) -> float:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= sample_size:
            data = handle.read()
        else:
            first = handle.read(sample_size // 2)
            handle.seek(max(0, size - sample_size // 2))
            data = first + handle.read(sample_size // 2)
    return shannon_entropy(data)


def is_lved_payload_name(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(":zone.identifier"):
        return False
    return name == "main.data" or name.endswith(".dbc")


def discover_lved_payloads(roots: Iterable[Path]) -> list[Path]:
    payloads: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates = [root] if is_lved_payload_name(root) else []
        elif root.is_dir():
            candidates = [path for path in root.rglob("*") if path.is_file() and is_lved_payload_name(path)]
        else:
            candidates = []
        for path in sorted(candidates):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            payloads.append(resolved)
    return payloads


def infer_lved_dict_code(path: Path) -> str | None:
    if path.suffix.lower() == ".dbc":
        return path.stem.upper()
    for part in (path.parent.name, path.parent.parent.name if path.parent.parent else ""):
        upper = part.upper()
        if upper.startswith("_DCT_") and len(upper) > 5:
            return upper[5:]
        if re.fullmatch(r"[A-Z0-9]{4,16}", upper) and upper not in {"DIC", "DATA"}:
            return upper
    return None


def discover_lved_key_file(path: Path) -> LvedKeyFile | None:
    """Return the best local ``.key`` file for a SQLCipher payload.

    These files are local analysis artifacts, not dictionary resources.  The
    key text is consumed for validation/decryption but is never emitted in
    reports.
    """

    parent = path.parent
    candidates: list[tuple[Path, str]] = []
    dict_code = infer_lved_dict_code(path)
    if dict_code:
        candidates.append((parent / f"{dict_code}.key", "inferred_dict_code"))
        candidates.append((parent / f"{dict_code.lower()}.key", "inferred_dict_code_lower"))
    if path.suffix:
        candidates.append((path.with_suffix(path.suffix + ".key"), "payload_name"))
    candidates.append((parent / f"{path.stem}.key", "payload_stem"))

    seen: set[Path] = set()
    for candidate, match in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return LvedKeyFile(path=candidate, match=match)

    key_files = sorted(parent.glob("*.key"), key=lambda item: item.name.casefold())
    if len(key_files) == 1:
        return LvedKeyFile(path=key_files[0], match="single_key_in_payload_dir")
    return None


def read_lved_key_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in _safe_pragma_rows(con, "table_info", table)]


def _safe_count(con: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(con.execute(f"select count(*) from {_quote_identifier(table)}").fetchone()[0])
    except sqlite3.Error:
        return None


def _safe_pragma_rows(con: sqlite3.Connection, pragma: str, name: str) -> list[tuple[Any, ...]]:
    try:
        return list(con.execute(f"pragma {pragma}({_quote_identifier(name)})"))
    except sqlite3.Error:
        return []


def _sqlite_table_role(name: str, sql: str | None) -> str:
    lower = name.casefold()
    sql_lower = (sql or "").casefold()
    if lower == "list":
        return "entry_list"
    if lower == "content":
        return "entry_content"
    if lower == "media":
        return "media_blob_store"
    if lower == "info":
        return "info_page_store"
    if lower == "exam":
        return "example_lookup"
    if lower == "genre":
        return "genre_or_category_lookup"
    if lower == "bunya":
        return "field_category_lookup"
    if lower == "binran":
        return "reference_page_store"
    if lower in {"kanji", "kanji_all"}:
        return "kanji_metadata_lookup"
    if lower == "kwit":
        return "keyword_lookup"
    if lower == "joken":
        return "condition_lookup"
    if lower == "sakuin":
        return "index_lookup"
    if lower == "item":
        return "item_metadata_lookup"
    if lower == "t_contents":
        return "legacy_html_content_store"
    if lower == "t_convert":
        return "legacy_address_conversion"
    if lower == "listsub":
        return "secondary_entry_list"
    if lower == "searchsub":
        return "secondary_fts_search"
    if lower.startswith("searchsub_"):
        return "secondary_fts_shadow"
    if lower == "sound":
        return "audio_blob_store"
    if lower == "mediasub":
        return "secondary_media_blob_store"
    if lower == "search":
        return "fts_search"
    if lower.startswith("search_"):
        return "fts_shadow"
    if "using fts" in sql_lower:
        return "fts_search"
    return "unclassified"


def _sqlite_text_signals(values: list[object]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        if isinstance(value, bytes):
            continue
        text = str(value).casefold()
        for name, pattern in _SQLITE_TEXT_SIGNAL_PATTERNS:
            needle = pattern.strip("%").casefold()
            if needle and needle in text:
                counts[name] += 1
    return dict(sorted(counts.items()))


def _sqlite_column_profile(
    con: sqlite3.Connection,
    table: str,
    column: str,
    declared_type: str,
    *,
    sample_rows: int,
) -> dict[str, Any]:
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    profile: dict[str, Any] = {
        "name": column,
        "declared_type": declared_type,
        "sample_limit": sample_rows,
    }
    try:
        rows = con.execute(
            f"select {quoted_column} from {quoted_table} where {quoted_column} is not null limit ?",
            (sample_rows,),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    values = [row[0] for row in rows]
    profile["non_null_sample_count"] = len(values)
    if values:
        storage_types = Counter(type(value).__name__ for value in values)
        profile["sample_storage_types"] = dict(sorted(storage_types.items()))
        profile["sample_max_length"] = max(len(value) if isinstance(value, bytes) else len(str(value)) for value in values)
    type_hint = declared_type.casefold()
    if "text" in type_hint or not declared_type:
        signals = _sqlite_text_signals(values)
        if signals:
            profile["sample_text_signals"] = signals
    return profile


def _sqlite_value_distribution(con: sqlite3.Connection, table: str, column: str, *, limit: int = 64) -> list[dict[str, Any]]:
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    try:
        rows = con.execute(
            f"select {quoted_column}, count(*) as n from {quoted_table} "
            f"group by {quoted_column} order by n desc, {quoted_column} limit ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "value": None if row[0] is None else str(row[0]),
            "count": int(row[1]),
        }
        for row in rows
    ]


def _sqlite_table_exists(con: sqlite3.Connection, table: str) -> bool:
    try:
        row = con.execute("select 1 from sqlite_master where type = 'table' and name = ? limit 1", (table,)).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _sqlite_distinct_matches(con: sqlite3.Connection, table: str, column: str, values: set[int]) -> dict[str, int]:
    if not values or not _sqlite_table_exists(con, table):
        return {"matched_values": 0, "row_matches": 0}
    matched_values: set[int] = set()
    row_matches = 0
    ordered = sorted(values)
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        try:
            rows = con.execute(
                f"select {quoted_column}, count(*) from {quoted_table} "
                f"where {quoted_column} in ({placeholders}) group by {quoted_column}",
                chunk,
            )
        except sqlite3.Error:
            continue
        for value, count in rows:
            try:
                matched_values.add(int(value))
            except (TypeError, ValueError):
                continue
            row_matches += int(count)
    return {"matched_values": len(matched_values), "row_matches": row_matches}


def _sqlite_anchor_matches(con: sqlite3.Connection, values: set[int]) -> dict[str, int]:
    if not values or not _sqlite_table_exists(con, "list"):
        return {"matched_values": 0, "row_matches": 0}
    anchors = {f"{value:08d}": value for value in values if 0 <= value <= 99999999}
    matched_values: set[int] = set()
    row_matches = 0
    ordered = sorted(anchors)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        try:
            rows = con.execute(
                f"select anchor, count(*) from list where anchor in ({placeholders}) group by anchor",
                chunk,
            )
        except sqlite3.Error:
            continue
        for anchor, count in rows:
            value = anchors.get(str(anchor))
            if value is not None:
                matched_values.add(value)
                row_matches += int(count)
    return {"matched_values": len(matched_values), "row_matches": row_matches}


def _sqlite_table_features(name: str, columns: list[str]) -> list[str]:
    lower = name.casefold()
    colset = {column.casefold() for column in columns}
    features: list[str] = []
    if lower == "list":
        features.append("entry_listing")
        if {"refid", "anchor"} <= colset:
            features.append("anchor_to_content_reference")
        if "title" in colset:
            features.append("display_title")
        if "titlesub" in colset:
            features.append("display_subtitle_titlesub")
        if "subtext" in colset:
            features.append("display_subtext")
    elif lower == "content":
        features.append("entry_body_store")
        if "body" in colset:
            features.append("html_or_text_body")
        if "media" in colset:
            features.append("entry_media_reference_field")
    elif lower == "media":
        features.append("binary_media_store")
        if "os" in colset:
            features.append("os_specific_media_rows")
    elif lower == "info":
        features.append("auxiliary_info_pages")
    elif lower == "exam":
        features.append("example_sentence_lookup")
    elif lower == "genre":
        features.append("genre_or_filter_metadata")
    elif lower == "bunya":
        features.append("field_category_metadata")
    elif lower in {"kanji", "kanji_all"}:
        features.append("kanji_search_metadata")
    elif lower in {"kwit", "joken", "sakuin", "item"}:
        features.append(f"{lower}_metadata")
    elif lower == "binran":
        features.append("reference_page_store")
    elif lower == "t_contents":
        features.append("legacy_html_content_store")
    elif lower == "t_convert":
        features.append("legacy_address_conversion")
    elif lower == "listsub":
        features.append("secondary_entry_listing")
    elif lower == "searchsub":
        features.append("secondary_native_fts_search")
    elif lower.startswith("searchsub_"):
        features.append("secondary_native_fts_shadow_table")
    elif lower == "sound":
        features.append("audio_blob_store")
    elif lower == "mediasub":
        features.append("secondary_media_blob_store")
    elif lower == "search":
        features.append("native_fts_search")
        for search_column in ("forward", "back", "part", "fts", "advanced", "advanced1", "advanced2", "advanced3", "filter"):
            if search_column in colset:
                features.append(f"search_column_{search_column}")
    elif lower.startswith("search_"):
        features.append("native_fts_shadow_table")
    return features


def inspect_lved_sqlite_database(path: Path, *, sample_rows: int = 1024) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        pragma: dict[str, Any] = {}
        for key in ("page_size", "page_count", "encoding", "user_version", "application_id", "schema_version"):
            try:
                pragma[key] = con.execute(f"pragma {key}").fetchone()[0]
            except sqlite3.Error:
                pass
        objects: list[dict[str, Any]] = []
        feature_counts: Counter[str] = Counter()
        role_counts: Counter[str] = Counter()
        for row in con.execute(
            "select type, name, tbl_name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ):
            object_type = str(row["type"])
            name = str(row["name"])
            sql = row["sql"]
            role = _sqlite_table_role(name, sql if isinstance(sql, str) else None)
            item: dict[str, Any] = {
                "type": object_type,
                "name": name,
                "tbl_name": row["tbl_name"],
                "role": role,
            }
            role_counts[role] += 1
            if isinstance(sql, str):
                sql_lower = sql.casefold()
                item["virtual"] = "create virtual table" in sql_lower
                if "using fts" in sql_lower:
                    item["virtual_module"] = "fts"
            if object_type == "table":
                item["row_count"] = _safe_count(con, name)
                column_rows = _safe_pragma_rows(con, "table_info", name)
                columns: list[str] = [str(column[1]) for column in column_rows]
                item["columns"] = [
                    {
                        "cid": int(column[0]),
                        "name": str(column[1]),
                        "declared_type": str(column[2] or ""),
                        "notnull": bool(column[3]),
                        "pk": int(column[5] or 0),
                    }
                    for column in column_rows
                ]
                features = _sqlite_table_features(name, columns)
                if features:
                    item["features"] = features
                    feature_counts.update(features)
                if name.casefold() in {"list", "content", "media", "info", "exam", "genre", "search"}:
                    item["column_profiles"] = [
                        _sqlite_column_profile(
                            con,
                            name,
                            str(column[1]),
                            str(column[2] or ""),
                            sample_rows=sample_rows,
                        )
                        for column in column_rows
                    ]
                distributions: dict[str, list[dict[str, Any]]] = {}
                for distribution_column in ("type", "os"):
                    if distribution_column in {column.casefold() for column in columns}:
                        source_column = next(column for column in columns if column.casefold() == distribution_column)
                        distribution = _sqlite_value_distribution(con, name, source_column)
                        if distribution:
                            distributions[source_column] = distribution
                if distributions:
                    item["value_distributions"] = distributions
                indexes = _safe_pragma_rows(con, "index_list", name)
                if indexes:
                    item["indexes"] = [
                        {
                            "name": str(index[1]),
                            "unique": bool(index[2]),
                            "origin": str(index[3]) if len(index) > 3 else "",
                            "partial": bool(index[4]) if len(index) > 4 else False,
                        }
                        for index in indexes
                    ]
            objects.append(item)
        return {
            "preferred_family_name": LVED_SQLITE3_FAMILY_NAME,
            "sqlite_pragmas": pragma,
            "object_count": len(objects),
            "role_counts": dict(sorted(role_counts.items())),
            "feature_counts": dict(sorted(feature_counts.items())),
            "objects": objects,
        }
    finally:
        con.close()


def _decode_retained_text(data: bytes) -> tuple[str, str] | None:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return encoding, data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _int_token(value: str) -> int | None:
    stripped = value.strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped, 10)
    return None


def _hex_token(value: str) -> int | None:
    stripped = value.strip()
    if re.fullmatch(r"[0-9A-Fa-f]{8}", stripped):
        return int(stripped, 16)
    return None


def _parse_lved_tree_target(value: str) -> tuple[int | None, str, tuple[tuple[str, str], ...]]:
    stripped = value.strip()
    target = stripped
    query_params: tuple[tuple[str, str], ...] = ()
    if "?" in stripped:
        target, query = stripped.split("?", 1)
        query_params = tuple(parse_qsl(query, keep_blank_values=True))
    int_value = _int_token(target)
    if int_value is not None:
        if query_params:
            form = "decimal_with_query"
        elif int_value < 0:
            form = "negative_sentinel"
        elif int_value == 0:
            form = "zero_root_or_sentinel"
        elif target.startswith("0") and len(target) > 1:
            form = "decimal_with_leading_zero"
        else:
            form = "decimal"
        return int_value, form, query_params
    hex_value = _hex_token(target)
    if hex_value is not None:
        return hex_value, "hex", query_params
    return None, "other", query_params


def _parse_lved_text_tree_rows(text: str) -> tuple[list[LvedTextTreeRow], int]:
    rows: list[LvedTextTreeRow] = []
    unparsed = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 3:
            unparsed += 1
            continue
        depth_value = _int_token(parts[1])
        if depth_value is None:
            unparsed += 1
            continue
        raw_target = parts[0].strip()
        label = "\t".join(parts[2:]).strip()
        target_value, target_form, query_params = _parse_lved_tree_target(raw_target)
        rows.append(
            LvedTextTreeRow(
                line_no=line_no,
                raw_target=raw_target,
                depth=depth_value,
                label=label,
                target_value=target_value,
                target_form=target_form,
                query_params=query_params,
            )
        )
    return rows, unparsed


def _retained_resource_paths(payload_path: Path) -> list[Path]:
    root = payload_path.parent
    paths: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if ":zone.identifier" in rel.casefold():
            continue
        name = path.name.casefold()
        if name.endswith(_RETAINED_RESOURCE_SUFFIXES) or name in _RETAINED_RESOURCE_NAMES:
            paths.append(path)
    return paths


def _is_retained_resource_file(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(_RETAINED_RESOURCE_SUFFIXES) or name in _RETAINED_RESOURCE_NAMES


def _is_lved_payload_file(path: Path) -> bool:
    name = path.name.casefold()
    return name in _PAYLOAD_NAMES or path.suffix.casefold() == ".dbc"


def _loose_resource_paths(payload_path: Path) -> list[Path]:
    root = payload_path.parent
    paths: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        lower_rel = rel.casefold()
        if ":zone.identifier" in lower_rel:
            continue
        name = path.name.casefold()
        if _is_lved_payload_file(path) or _is_retained_resource_file(path) or name.endswith(".key"):
            continue
        paths.append(path)
    return paths


def _png_profile(data: bytes) -> dict[str, Any] | None:
    if len(data) < 29 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    return {
        "media_type": "png",
        "width": int.from_bytes(data[16:20], "big"),
        "height": int.from_bytes(data[20:24], "big"),
        "bit_depth": data[24],
        "color_type": data[25],
    }


def _gif_profile(data: bytes) -> dict[str, Any] | None:
    if len(data) < 10 or not data.startswith((b"GIF87a", b"GIF89a")):
        return None
    return {
        "media_type": "gif",
        "version": data[:6].decode("ascii", errors="replace"),
        "width": int.from_bytes(data[6:8], "little"),
        "height": int.from_bytes(data[8:10], "little"),
    }


def _bmp_profile(data: bytes) -> dict[str, Any] | None:
    if len(data) < 54 or not data.startswith(b"BM"):
        return None
    dib_size = int.from_bytes(data[14:18], "little")
    if dib_size < 40:
        return None
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    bits_per_pixel = int.from_bytes(data[28:30], "little")
    compression = int.from_bytes(data[30:34], "little")
    return {
        "media_type": "bmp",
        "width": abs(width),
        "height": abs(height),
        "bits_per_pixel": bits_per_pixel,
        "compression": compression,
    }


def _webp_profile(data: bytes) -> dict[str, Any] | None:
    if len(data) < 30 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return {"media_type": "webp", "profile": "VP8X", "width": width, "height": height}
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return {"media_type": "webp", "profile": "VP8", "width": width, "height": height}
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return {"media_type": "webp", "profile": "VP8L", "width": width, "height": height}
    return {"media_type": "webp", "profile": "unknown", "width": None, "height": None}


def _svg_profile(data: bytes) -> dict[str, Any] | None:
    stripped = data.lstrip()[:8192]
    lower = stripped.lower()
    if not (lower.startswith(b"<svg") or (lower.startswith(b"<?xml") and b"<svg" in lower[:512])):
        return None
    text = stripped.decode("utf-8", errors="ignore")
    width = None
    height = None
    viewbox = re.search(r"\bviewBox\s*=\s*['\"]([^'\"]+)['\"]", text)
    if viewbox:
        parts = viewbox.group(1).replace(",", " ").split()
        if len(parts) == 4:
            try:
                width = float(parts[2])
                height = float(parts[3])
            except ValueError:
                width = height = None
    if width is None or height is None:
        width_attr = re.search(r"\bwidth\s*=\s*['\"]([0-9.]+)", text)
        height_attr = re.search(r"\bheight\s*=\s*['\"]([0-9.]+)", text)
        if width_attr and height_attr:
            try:
                width = float(width_attr.group(1))
                height = float(height_attr.group(1))
            except ValueError:
                width = height = None
    return {"media_type": "svg", "width": width, "height": height}


def _image_profile(data: bytes) -> dict[str, Any]:
    for parser in (_png_profile, _gif_profile, _webp_profile, _bmp_profile, _svg_profile):
        profile = parser(data)
        if profile is not None:
            return profile
    dimensions = parse_jpeg_dimensions(data)
    if dimensions is not None:
        width, height = dimensions
        return {"media_type": "jpeg", "width": width, "height": height}
    if data.startswith(b"\xff\xd8\xff"):
        return {"media_type": "jpeg", "width": None, "height": None}
    return {"media_type": "unknown_image", "width": None, "height": None}


def _font_profile(data: bytes, suffix: str) -> dict[str, Any]:
    if data.startswith(b"OTTO"):
        return {"font_type": "opentype_cff"}
    if data.startswith(b"\x00\x01\x00\x00") or data.startswith(b"true"):
        return {"font_type": "truetype"}
    if data.startswith(b"ttcf"):
        faces = int.from_bytes(data[8:12], "big") if len(data) >= 12 else None
        return {"font_type": "truetype_collection", "faces": faces}
    if data.startswith(b"wOFF"):
        return {"font_type": "woff"}
    if data.startswith(b"wOF2"):
        return {"font_type": "woff2"}
    return {"font_type": suffix.removeprefix(".") or "unknown"}


def _decode_loose_resource(path: Path, root: Path) -> dict[str, Any]:
    suffix = path.suffix.casefold()
    data = path.read_bytes()
    item: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "size": len(data),
        "suffix": suffix or "<none>",
    }
    if suffix in _LOOSE_IMAGE_SUFFIXES:
        item["kind"] = "image"
        item.update(_image_profile(data[: max(8192, min(len(data), 65536))]))
    elif suffix in _LOOSE_FONT_SUFFIXES:
        item["kind"] = "font"
        item.update(_font_profile(data[:64], suffix))
    elif suffix == ".pdf" or data.startswith(b"%PDF-"):
        item["kind"] = "pdf"
        item["signature"] = "pdf"
    elif path.name.casefold() in {"thumbs.db", ".ds_store", "desktop.ini"}:
        item["kind"] = "desktop_metadata"
    elif suffix in _LOOSE_WEB_TEXT_SUFFIXES:
        decoded = _decode_retained_text(data[:65536])
        item["kind"] = "web_runtime_text"
        item["encoding"] = decoded[0] if decoded else "unknown"
        if suffix in {".html", ".htm"}:
            item["contains_html"] = True
        elif suffix == ".js":
            item["contains_javascript"] = True
        elif suffix == ".css":
            item["contains_css"] = True
    else:
        item["kind"] = "other"
    return item


def inspect_lved_loose_resources(payload_path: Path) -> dict[str, Any]:
    root = payload_path.parent
    files: list[dict[str, Any]] = []
    kind_counts: Counter[str] = Counter()
    suffix_counts: Counter[str] = Counter()
    media_counts: Counter[str] = Counter()
    dimension_counts: Counter[str] = Counter()
    font_counts: Counter[str] = Counter()
    for path in _loose_resource_paths(payload_path):
        try:
            item = _decode_loose_resource(path, root)
        except Exception as exc:
            item = {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "suffix": path.suffix.casefold() or "<none>",
                "kind": "inspect_error",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        kind = str(item.get("kind", "other"))
        suffix = str(item.get("suffix", "<none>"))
        kind_counts[kind] += 1
        suffix_counts[suffix] += 1
        if kind == "image":
            media_type = str(item.get("media_type", "unknown_image"))
            media_counts[media_type] += 1
            width = item.get("width")
            height = item.get("height")
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                dimension_counts[f"{media_type}:{width:g}x{height:g}"] += 1
        if kind == "font":
            font_counts[str(item.get("font_type", "unknown"))] += 1
        files.append(item)
    return {
        "status": "inspected",
        "file_count": len(files),
        "kind_counts": dict(sorted(kind_counts.items())),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "media_counts": dict(sorted(media_counts.items())),
        "dimension_counts": dict(sorted(dimension_counts.items())),
        "font_counts": dict(sorted(font_counts.items())),
        "files": files,
    }


def _normalize_lved_query(query: str) -> str:
    return " ".join(query.strip().casefold().split())


def _lved_fts_tokens(query: str, *, split_chars: bool = False) -> list[str]:
    normalized = _normalize_lved_query(query)
    if not normalized:
        return []
    if split_chars:
        return [char for char in normalized if not char.isspace()]
    return re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſƀ-ɏ\u0370-\u03ff\u0400-\u052f\u3040-\u30ff\u3400-\u9fffー]+", normalized)


def _lved_fts_term(term: str, *, prefix: bool = False) -> str:
    cleaned = re.sub(r"""["':*()\s]+""", "", term)
    if not cleaned:
        return ""
    return f"{cleaned}*" if prefix else cleaned


def _lved_fts_expr(column: str, tokens: list[str], *, prefix_last: bool = False) -> str:
    terms = []
    for index, token in enumerate(tokens):
        term = _lved_fts_term(token, prefix=prefix_last and index == len(tokens) - 1)
        if term:
            terms.append(f"{column}:{term}")
    return " ".join(terms)


def _lved_should_split_chars(query: str) -> bool:
    return not bool(re.search(r"[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-ſƀ-ɏ]", query))


def _lved_list_select_columns(con: sqlite3.Connection, table: str = "list") -> str:
    columns = {column.casefold() for column in _sqlite_columns(con, table)}
    parts = ["l.id as list_id", "l.refid as content_id"]
    parts.append("l.anchor as anchor" if "anchor" in columns else "'' as anchor")
    parts.append("l.title as title" if "title" in columns else "'' as title")
    if "titlesub" in columns:
        parts.append("l.titlesub as subtitle")
    elif "subtext" in columns:
        parts.append("l.subtext as subtitle")
    elif "titleplain" in columns:
        parts.append("l.titleplain as subtitle")
    else:
        parts.append("'' as subtitle")
    parts.append("l.type as list_type" if "type" in columns else "null as list_type")
    return ", ".join(parts)


def _lved_search_tables_for_mode(con: sqlite3.Connection, mode: str) -> tuple[str, str, str | None]:
    search_columns = {column.casefold() for column in _sqlite_columns(con, "search")} if _sqlite_table_exists(con, "search") else set()
    if mode in {"exact", "forward", "backward", "partial", "fulltext", "filter"} or mode in search_columns:
        return "search", "list", None
    searchsub_columns = {column.casefold() for column in _sqlite_columns(con, "searchsub")} if _sqlite_table_exists(con, "searchsub") else set()
    if mode in searchsub_columns and _sqlite_table_exists(con, "listsub"):
        return "searchsub", "listsub", "secondary searchsub/listsub provider"
    return "search", "list", None


def _lved_search_where(
    con: sqlite3.Connection,
    query: str,
    mode: str,
    *,
    search_table: str = "search",
) -> tuple[str, tuple[Any, ...], str, str]:
    mode = mode.casefold()
    if mode not in _LVED_SEARCH_MODES:
        raise ValueError(f"unsupported LVED search mode: {mode}")
    normalized = _normalize_lved_query(query)
    search_columns = {column.casefold() for column in _sqlite_columns(con, search_table)}
    if not normalized:
        return "0", (), mode, "empty query"
    if mode == "exact":
        if "filter" in search_columns:
            return "s.filter like ? escape '\\'", (f"%∥{normalized}∥%",), mode, "filter boundary LIKE"
        return "s.forward = ?", (normalized,), mode, "forward equality fallback"
    if mode == "filter":
        if "filter" not in search_columns:
            return "0", (), mode, "missing filter column"
        return "s.filter like ? escape '\\'", (f"%{normalized}%",), mode, "filter substring LIKE"
    if mode == "backward":
        tokens = _lved_fts_tokens(normalized[::-1])
        expr = _lved_fts_expr("back", tokens, prefix_last=True)
    elif mode == "forward":
        tokens = _lved_fts_tokens(normalized)
        expr = _lved_fts_expr("forward", tokens, prefix_last=True)
    elif mode == "partial":
        tokens = _lved_fts_tokens(normalized, split_chars=True)
        expr = _lved_fts_expr("part", tokens)
    elif mode == "fulltext":
        tokens = _lved_fts_tokens(normalized, split_chars=_lved_should_split_chars(normalized))
        expr = _lved_fts_expr("fts", tokens)
    else:
        column = mode
        if column not in search_columns:
            return "0", (), mode, f"missing {column} column"
        tokens = _lved_fts_tokens(normalized, split_chars=_lved_should_split_chars(normalized))
        expr = _lved_fts_expr(column, tokens)
    if not expr:
        return "0", (), mode, "query produced no FTS terms"
    quoted_search = _quote_identifier(search_table)
    return f"s.rowid in (select rowid from {quoted_search} where {quoted_search} match ?)", (expr,), mode, expr


def search_lved_sqlite_database(path: Path, query: str, *, mode: str = "forward", limit: int = 20) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        normalized_mode = mode.casefold()
        search_table, list_table, provider_note = _lved_search_tables_for_mode(con, normalized_mode)
        if not _sqlite_table_exists(con, search_table) or not _sqlite_table_exists(con, list_table):
            return {"query": query, "mode": mode, "status": "unsupported", "reason": f"missing {search_table}/{list_table} tables", "hits": []}
        where, params, normalized_mode, strategy = _lved_search_where(con, query, normalized_mode, search_table=search_table)
        select_columns = _lved_list_select_columns(con, list_table)
        quoted_search = _quote_identifier(search_table)
        quoted_list = _quote_identifier(list_table)
        sql = (
            f"select {select_columns} from {quoted_search} s "
            f"join {quoted_list} l on l.id = s.rowid where {where} "
            "order by l.id limit ?"
        )
        hits = [
            {
                "list_id": int(row["list_id"]),
                "content_id": int(row["content_id"]),
                "anchor": "" if row["anchor"] is None else str(row["anchor"]),
                "title_html": "" if row["title"] is None else str(row["title"]),
                "subtitle_html": "" if row["subtitle"] is None else str(row["subtitle"]),
                "list_type": None if row["list_type"] is None else int(row["list_type"]),
            }
            for row in con.execute(sql, (*params, max(0, limit)))
        ]
        return {
            "query": query,
            "mode": normalized_mode,
            "status": "ok",
            "strategy": strategy,
            "search_table": search_table,
            "list_table": list_table,
            "provider": provider_note or "primary search/list provider",
            "limit": limit,
            "hit_count": len(hits),
            "hits": hits,
        }
    except sqlite3.Error as exc:
        return {"query": query, "mode": mode, "status": "sqlite_error", "reason": str(exc), "hits": []}
    finally:
        con.close()


def _lved_blob_extension(blob: bytes, name: str | None = None) -> str:
    lower_name = (name or "").casefold()
    if blob.startswith(b"%PDF-"):
        return ".pdf"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if blob.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return ".webp"
    if blob.startswith(b"BM"):
        return ".bmp"
    stripped = blob.lstrip()
    if stripped.lower().startswith(b"<svg") or (stripped.lower().startswith(b"<?xml") and b"<svg" in stripped[:512].lower()):
        return ".svg"
    if blob.startswith(b"RIFF") and blob[8:12] == b"WAVE":
        return ".wav"
    if blob.startswith(b"ID3") or blob.startswith(b"\xff\xfb"):
        return ".mp3"
    suffix = Path(lower_name).suffix
    return suffix if suffix else ".bin"


def _safe_asset_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("._")
    return cleaned or "resource"


def _safe_lved_named_page_stem(target: str, *, fallback: str) -> str:
    """Return the generated page stem for info/binran-style named HTML rows.

    Some real LVED payloads link to ``foo.html.html`` while the row name is
    ``foo.html``.  Treat repeated HTML suffixes as a protocol wart and collapse
    them so generated named pages and rewritten links agree.
    """

    name = _LVED_TARGET_SPLIT_RE.split(target.strip(), 1)[0]
    while name.casefold().endswith(".html"):
        name = name[:-5]
    return _safe_asset_name(Path(name).stem or name or fallback)


def _lved_dict_info_target(value: str) -> str | None:
    raw = value.split(":", 1)[1] if ":" in value else value
    raw = _LVED_TARGET_SPLIT_RE.split(raw.strip(), 1)[0]
    if raw.casefold().startswith("pictlink."):
        raw = raw[len("pictlink.") :]
    return raw or None


def _sqlite_blob_to_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    return str(value).encode("utf-8")


def _lved_entry_href(content_id: int, anchor: str | None = None) -> str:
    href = f"entry_{int(content_id)}.html"
    if anchor:
        href += "#" + html.escape(str(anchor), quote=True)
    return href


def _lved_deferred_href(attr: str, quote: str, value: str, *, hook: str | None = None) -> str:
    hook_attr = f" data-lv-hook={quote}{html.escape(hook, quote=True)}{quote}" if hook else ""
    return (
        f"{attr}={quote}#{quote} "
        f"data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}"
        f"{hook_attr}"
    )


def _lved_cross_dict_href(
    attr: str,
    quote: str,
    value: str,
    *,
    hook: str,
    dict_code: str,
    content_id: str,
    anchor: str | None = None,
) -> str:
    anchor_attr = f" data-lv-anchor={quote}{html.escape(anchor, quote=True)}{quote}" if anchor else ""
    return (
        f"{attr}={quote}#{quote} "
        f"data-lv-original-href={quote}{html.escape(value, quote=True)}{quote} "
        f"data-lv-hook={quote}{html.escape(hook, quote=True)}{quote} "
        f"data-lv-dict={quote}{html.escape(dict_code.upper(), quote=True)}{quote} "
        f"data-lv-content-id={quote}{html.escape(str(int(content_id)), quote=True)}{quote}"
        f"{anchor_attr}"
    )


def _sqlite_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sqlite_text_from_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    data = _sqlite_blob_to_bytes(value)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp932", errors="replace")


def _lved_addr_key(value: str) -> str | None:
    match = _LVED_ADDR_RE.search(value)
    if not match:
        return None
    return f"{match.group('block').casefold()}:{match.group('offset').casefold()}"


def _lved_addr_link_keys(con: sqlite3.Connection) -> set[str]:
    if not _sqlite_table_exists(con, "content"):
        return set()
    columns = {column.casefold() for column in _sqlite_columns(con, "content")}
    if not {"id", "body"} <= columns:
        return set()
    link_keys: set[str] = set()
    for row in con.execute("select cast(body as blob) from content where cast(body as text) like '%lved.addr%'"):
        body = _sqlite_text_from_value(row[0])
        link_keys.update(key for key in (_lved_addr_key(match.group(0)) for match in _LVED_ADDR_RE.finditer(body)) if key)
    return link_keys


def _lved_addr_resolution_lookup(con: sqlite3.Connection, *, max_delta: int = 0x100, link_keys: set[str] | None = None) -> dict[str, LvedAddrResolution]:
    if not _sqlite_table_exists(con, "content"):
        return {}
    columns = {column.casefold() for column in _sqlite_columns(con, "content")}
    if not {"id", "body"} <= columns:
        return {}

    link_keys = link_keys if link_keys is not None else _lved_addr_link_keys(con)
    if not link_keys:
        return {}

    anchors_by_block: dict[str, list[tuple[int, str, int]]] = {}
    for row in con.execute("select id, cast(body as blob) from content where cast(body as text) like '%name=%'"):
        content_id = int(row[0])
        body = _sqlite_text_from_value(row[1])
        for match in _HTML_ANCHOR_NAME_RE.finditer(body):
            anchor = match.group("name").strip()
            lower_anchor = anchor.casefold()
            if not re.fullmatch(r"[0-9a-f]{12,}", lower_anchor):
                continue
            block = lower_anchor[:8]
            offset = int(lower_anchor[8:12], 16)
            anchors_by_block.setdefault(block, []).append((offset, anchor, content_id))
    for anchors in anchors_by_block.values():
        anchors.sort(key=lambda item: item[0])

    resolved: dict[str, LvedAddrResolution] = {}
    for key in sorted(link_keys):
        block, offset_text = key.split(":", 1)
        target_offset = int(offset_text, 16)
        for offset, anchor, content_id in anchors_by_block.get(block, ()):
            delta = offset - target_offset
            if delta < 0:
                continue
            if delta > max_delta:
                break
            resolved[key] = LvedAddrResolution(content_id=content_id, anchor=anchor, delta=delta)
            break
    return resolved


def _media_lookup(con: sqlite3.Connection) -> dict[str, tuple[int, str, bytes]]:
    lookup: dict[str, tuple[int, str, bytes]] = {}
    for table in ("media", "mediasub", "sound"):
        if not _sqlite_table_exists(con, table):
            continue
        columns = {column.casefold() for column in _sqlite_columns(con, table)}
        if not {"id", "name", "main"} <= columns:
            continue
        quoted_table = _quote_identifier(table)
        for row in con.execute(f"select id, name, cast(main as blob) from {quoted_table} where main is not null"):
            media_id = int(row[0])
            name = "" if row[1] is None else str(row[1])
            blob = _sqlite_blob_to_bytes(row[2])
            if not blob:
                continue
            keys = {name.casefold(), Path(name).stem.casefold(), str(media_id)}
            for key in keys:
                if key and key not in lookup:
                    lookup[key] = (media_id, name, blob)
    return lookup


def _loose_resource_lookup(payload_path: Path) -> dict[str, Path]:
    lookup: dict[str, Path] = {}
    for path in _loose_resource_paths(payload_path):
        rel_key = path.relative_to(payload_path.parent).as_posix().casefold()
        for key in {rel_key, path.name.casefold(), path.stem.casefold()}:
            lookup.setdefault(key, path)
    return lookup


def _write_lved_asset(
    value: str,
    *,
    assets_dir: Path,
    media: dict[str, tuple[int, str, bytes]],
    loose: dict[str, Path],
) -> str | None:
    raw = value.strip()
    if not raw or re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE) or raw.startswith("#") or raw.startswith("data:"):
        return None
    lookup_key = raw.casefold()
    for prefix in ("lved.image:", "lved.imag:", "lved.media.sound:", "lved.sound:", "lved.media:", "lved.pdf:"):
        if lookup_key.startswith(prefix):
            lookup_key = raw.split(":", 1)[1].split("?", 1)[0].casefold()
            break
    if lookup_key.startswith("./"):
        lookup_key = lookup_key[2:]
    media_item = media.get(lookup_key) or media.get(Path(lookup_key).stem.casefold())
    if media_item is not None:
        media_id, name, blob = media_item
        ext = _lved_blob_extension(blob, name)
        stem = _safe_asset_name(Path(name).stem or f"media_{media_id}")
        target = assets_dir / f"{stem}{ext}"
        if not target.exists():
            target.write_bytes(blob)
        return f"assets/{target.name}"
    loose_path = loose.get(lookup_key) or loose.get(Path(lookup_key).name.casefold()) or loose.get(Path(lookup_key).stem.casefold())
    if loose_path is not None:
        target = assets_dir / _safe_asset_name(loose_path.name)
        if not target.exists():
            shutil.copy2(loose_path, target)
        return f"assets/{target.name}"
    return None


def _rewrite_lved_fragment(
    fragment: str,
    *,
    assets_dir: Path,
    media: dict[str, tuple[int, str, bytes]],
    loose: dict[str, Path],
    addr_lookup: dict[str, LvedAddrResolution] | None = None,
) -> tuple[str, dict[str, int], set[int]]:
    stats: Counter[str] = Counter()
    linked_content_ids: set[int] = set()

    def repl(match: re.Match[str]) -> str:
        attr = match.group("attr")
        quote = match.group("quote")
        value = html.unescape(match.group("value"))
        lower = value.casefold()
        if attr.casefold() == "href":
            contentlink = _LVED_CONTENTLINK_RE.fullmatch(value)
            if contentlink:
                stats["contentlink_links_deferred"] += 1
                return _lved_cross_dict_href(
                    attr,
                    quote,
                    value,
                    hook="contentlink",
                    dict_code=contentlink.group("dict"),
                    content_id=contentlink.group("id"),
                    anchor=contentlink.group("anchor"),
                )
            dataid_dict = _LVED_DATAID_DICT_RE.fullmatch(value)
            if dataid_dict:
                stats["dataid_dict_links_deferred"] += 1
                return _lved_cross_dict_href(
                    attr,
                    quote,
                    value,
                    hook="dataid-dict",
                    dict_code=dataid_dict.group("dict"),
                    content_id=dataid_dict.group("id"),
                    anchor=dataid_dict.group("anchor"),
                )
            dataid_no_colon = _LVED_DATAID_NO_COLON_RE.fullmatch(value)
            if dataid_no_colon:
                target = int(dataid_no_colon.group("id"))
                anchor = dataid_no_colon.group("anchor")
                linked_content_ids.add(target)
                stats["dataid_no_colon_links_rewritten"] += 1
                return (
                    f'{attr}={quote}{_lved_entry_href(target, anchor)}{quote} '
                    f'data-lv-original-href={quote}{html.escape(value, quote=True)}{quote} '
                    f'data-lv-hook={quote}dataid-no-colon{quote}'
                )
        if attr.casefold() == "href" and lower.startswith("lved.dataid.result:"):
            raw_target = value.split(":", 1)[1]
            target = _LVED_TARGET_SPLIT_RE.split(raw_target, 1)[0]
            anchor: str | None = None
            if "#" in raw_target:
                anchor = raw_target.split("#", 1)[1]
            if target.isdigit():
                linked_content_ids.add(int(target))
                stats["dataid_result_links_rewritten"] += 1
                return (
                    f'{attr}={quote}{_lved_entry_href(int(target), anchor)}{quote} '
                    f'data-lv-original-href={quote}{html.escape(value, quote=True)}{quote} '
                    f'data-lv-hook={quote}search-result-detail{quote}'
                )
            stats["dataid_result_links_deferred"] += 1
            return _lved_deferred_href(attr, quote, value, hook="search-result-detail")
        if attr.casefold() == "href" and lower.startswith("lved.dataid:"):
            raw_target = value.split(":", 1)[1]
            target = _LVED_TARGET_SPLIT_RE.split(raw_target, 1)[0]
            anchor: str | None = None
            if "#" in raw_target:
                anchor = raw_target.split("#", 1)[1]
            if target.isdigit():
                linked_content_ids.add(int(target))
                stats["entry_links_rewritten"] += 1
                return f'{attr}={quote}{_lved_entry_href(int(target), anchor)}{quote}'
            stats["entry_links_unresolved"] += 1
            return match.group(0)
        if attr.casefold() == "href" and value.isdigit():
            target = int(value)
            linked_content_ids.add(target)
            stats["bare_entry_links_rewritten"] += 1
            return f'{attr}={quote}{_lved_entry_href(target)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
        if attr.casefold() == "href" and _LVED_RELATIVE_ADDR_RE.fullmatch(value):
            stats["relative_addr_links_deferred"] += 1
            return _lved_deferred_href(attr, quote, value, hook="relative-address")
        if attr.casefold() == "href" and lower.startswith("lved.info:"):
            raw_target = value.split(":", 1)[1]
            target = _LVED_TARGET_SPLIT_RE.split(raw_target, 1)[0]
            anchor = ""
            if "#" in raw_target:
                anchor = "#" + raw_target.split("#", 1)[1]
            if target:
                stats["info_links_rewritten"] += 1
                safe_name = _safe_lved_named_page_stem(target, fallback="info")
                return f'{attr}={quote}info_{safe_name}.html{html.escape(anchor, quote=True)}{quote}'
            stats["info_links_unresolved"] += 1
            return match.group(0)
        if attr.casefold() == "href" and lower.startswith("lved.binran:"):
            raw_target = value.split(":", 1)[1]
            target = _LVED_TARGET_SPLIT_RE.split(raw_target, 1)[0]
            anchor = ""
            if "#" in raw_target:
                anchor = "#" + raw_target.split("#", 1)[1]
            if target:
                stats["binran_links_rewritten"] += 1
                safe_name = _safe_lved_named_page_stem(target, fallback="binran")
                return f'{attr}={quote}binran_{safe_name}.html{html.escape(anchor, quote=True)}{quote}'
            stats["binran_links_unresolved"] += 1
            return match.group(0)
        if attr.casefold() == "href" and lower.startswith("lved.dict."):
            raw_target = value.split(":", 1)[1] if ":" in value else ""
            target = _lved_dict_info_target(value)
            anchor = ""
            if "#" in raw_target:
                anchor = "#" + raw_target.split("#", 1)[1]
            if target:
                stats["dict_info_links_rewritten"] += 1
                safe_name = _safe_lved_named_page_stem(target, fallback="dict")
                return f'{attr}={quote}info_{safe_name}.html{html.escape(anchor, quote=True)}{quote}'
            stats["dict_info_links_unresolved"] += 1
            return match.group(0)
        if attr.casefold() == "href" and lower.startswith("lved.pdf:"):
            href = _write_lved_asset(value, assets_dir=assets_dir, media=media, loose=loose)
            if href:
                stats["pdf_links_resolved"] += 1
                return f'{attr}={quote}{html.escape(href, quote=True)}{quote}'
            stats["pdf_links_unresolved"] += 1
            return _lved_deferred_href(attr, quote, value, hook="pdf")
        if attr.casefold() == "href" and lower.startswith(("lved.media.sound:", "lved.sound:", "lved.media:")):
            href = _write_lved_asset(value, assets_dir=assets_dir, media=media, loose=loose)
            if href:
                stats["media_links_resolved"] += 1
                return f'{attr}={quote}{html.escape(href, quote=True)}{quote}'
            stats["media_links_unresolved"] += 1
        if attr.casefold() == "href" and lower.startswith(("lved.image", "lved.imag")):
            href = _write_lved_asset(value, assets_dir=assets_dir, media=media, loose=loose)
            if href:
                stats["image_links_resolved"] += 1
                return f'{attr}={quote}{html.escape(href, quote=True)}{quote}'
            stats["image_links_deferred"] += 1
            return _lved_deferred_href(attr, quote, value, hook="image")
        if attr.casefold() == "href" and lower.startswith("lved.addr"):
            addr_key = _lved_addr_key(value)
            if addr_key and addr_lookup:
                resolution = addr_lookup.get(addr_key)
                if resolution is not None:
                    linked_content_ids.add(resolution.content_id)
                    stats["addr_links_rewritten"] += 1
                    return f'{attr}={quote}{_lved_entry_href(resolution.content_id, resolution.anchor)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote} data-lv-addr-delta={quote}{resolution.delta}{quote}'
            stats["addr_links_deferred"] += 1
            return _lved_deferred_href(attr, quote, value, hook="address")
        if attr.casefold() == "href" and lower.startswith("lved.bookmark:"):
            stats["bookmark_links_suppressed"] += 1
            return _lved_deferred_href(attr, quote, value, hook="bookmark")
        if attr.casefold() == "href" and lower.startswith("lved.plugin:"):
            stats["plugin_links_suppressed"] += 1
            return _lved_deferred_href(attr, quote, value, hook="plugin")
        if attr.casefold() == "href" and lower.startswith("lved.sql:"):
            stats["sql_links_deferred"] += 1
            return _lved_deferred_href(attr, quote, value, hook="sql")
        if attr.casefold() == "href" and lower.startswith("lved.findnum:"):
            stats["findnum_links_suppressed"] += 1
            return _lved_deferred_href(attr, quote, value, hook="findnum")
        if attr.casefold() == "href" and lower.startswith("lved.select:"):
            stats["select_links_suppressed"] += 1
            return _lved_deferred_href(attr, quote, value, hook="select")
        if attr.casefold() == "href" and lower.startswith("lved.group."):
            stats["group_links_suppressed"] += 1
            return _lved_deferred_href(attr, quote, value, hook="group")
        if attr.casefold() == "href" and lower.startswith("lved.browser."):
            stats["browser_links_suppressed"] += 1
            return _lved_deferred_href(attr, quote, value, hook="browser")
        if attr.casefold() == "src" or lower.startswith(("lved.image:", "lved.imag:")):
            src = _write_lved_asset(value, assets_dir=assets_dir, media=media, loose=loose)
            if src:
                stats["assets_resolved"] += 1
                return f'{attr}={quote}{html.escape(src, quote=True)}{quote}'
            stats["assets_unresolved"] += 1
        if attr.casefold() == "href" and lower.startswith("lved."):
            stats["unknown_lved_links_deferred"] += 1
            return _lved_deferred_href(attr, quote, value, hook="unknown")
        return match.group(0)

    return _LVED_PROTOCOL_RE.sub(repl, fragment), dict(sorted(stats.items())), linked_content_ids


def _lved_page(title: str, body: str, *, extra_head: str = "") -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 0; padding: 24px; font-family: system-ui, -apple-system, "Yu Gothic", "Meiryo", sans-serif; line-height: 1.65; color: #222; background: #fff; }}
a {{ color: #2f6fb4; }}
img {{ max-width: 100%; height: auto; vertical-align: middle; }}
.lv-entry, .lv-info, .lv-tree {{ max-width: 960px; margin: 0 auto; }}
.lv-meta {{ color: #555; font-size: 13px; border-bottom: 1px solid #ddd; margin-bottom: 16px; padding-bottom: 8px; }}
.lv-search-hit {{ display: block; padding: 8px 10px; border-bottom: 1px solid #eee; text-decoration: none; }}
.lv-search-hit:hover {{ background: #f6f8fa; }}
.lv-lved-tree {{ list-style: none; padding: 0; margin: 0; }}
.lv-lved-tree li {{ padding-block: 0.15rem; border-bottom: 1px solid #f1f1f1; }}
.lv-lved-tree a {{ text-decoration: none; }}
.midashi, .head {{ font-weight: 700; font-size: 1.25em; margin: 0.4em 0; }}
.honbun, .contents {{ margin: 0.35em 0 1em; }}
.img_gaiji, .img_gaiji_midashi, .img_hinshi, .img_mark, .img_mark2, .ex_img {{ max-height: 1.4em; width: auto; }}
table {{ border-collapse: collapse; }}
td, th {{ padding: 0.25em 0.5em; border: 1px solid #ddd; }}
</style>
{extra_head}
</head>
<body>
{body}
</body>
</html>
"""


def _content_row(con: sqlite3.Connection, content_id: int) -> sqlite3.Row | None:
    if not _sqlite_table_exists(con, "content"):
        return None
    columns = {column.casefold() for column in _sqlite_columns(con, "content")}
    if "id" not in columns or "body" not in columns:
        return None
    return con.execute("select * from content where id = ? limit 1", (content_id,)).fetchone()


def _write_lved_entry_page(
    con: sqlite3.Connection,
    payload_path: Path,
    out_dir: Path,
    content_id: int,
    *,
    title: str | None = None,
    media_lookup: dict[str, tuple[int, str, bytes]] | None = None,
    loose_lookup: dict[str, Path] | None = None,
    addr_lookup: dict[str, LvedAddrResolution] | None = None,
) -> dict[str, Any] | None:
    row = _content_row(con, content_id)
    if row is None:
        return None
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    media_lookup = media_lookup if media_lookup is not None else _media_lookup(con)
    loose_lookup = loose_lookup if loose_lookup is not None else _loose_resource_lookup(payload_path)
    body = _sqlite_text_from_value(row["body"])
    rewritten, stats, linked_content_ids = _rewrite_lved_fragment(body, assets_dir=assets_dir, media=media_lookup, loose=loose_lookup, addr_lookup=addr_lookup)
    page_title = title or f"entry {content_id}"
    html_body = (
        f'<main class="lv-entry"><div class="lv-meta">content.id={content_id}</div>'
        f"{rewritten}</main>"
    )
    target = out_dir / f"entry_{content_id}.html"
    target.write_text(_lved_page(page_title, html_body), encoding="utf-8")
    return {
        "content_id": content_id,
        "path": target.name,
        "body_bytes": len(body.encode("utf-8")),
        "rewrite": stats,
        "linked_content_ids": sorted(linked_content_ids),
    }


def _write_lved_entry_stub(out_dir: Path, content_id: int) -> dict[str, Any]:
    target = out_dir / f"entry_{content_id}.html"
    if not target.exists():
        target.write_text(
            _lved_page(
                f"entry {content_id}",
                '<main class="lv-entry">'
                f'<div class="lv-meta">content.id={content_id}</div>'
                "<p>This linked LVED entry was discovered but not materialized in this static showcase. "
                "A reader frontend should resolve this target dynamically through lved.dataid navigation.</p>"
                "</main>",
            ),
            encoding="utf-8",
        )
    return {"content_id": content_id, "path": target.name, "stub": True}


def _write_lved_info_pages(
    con: sqlite3.Connection,
    payload_path: Path,
    out_dir: Path,
    *,
    limit: int,
    media_lookup: dict[str, tuple[int, str, bytes]],
    loose_lookup: dict[str, Path],
    addr_lookup: dict[str, LvedAddrResolution] | None = None,
) -> list[dict[str, Any]]:
    if not _sqlite_table_exists(con, "info"):
        return []
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    for row in con.execute("select rowid as sqlite_rowid, id, name, body from info order by rowid limit ?", (limit,)):
        name = _sqlite_text_from_value(row["name"])
        body = _sqlite_text_from_value(row["body"])
        rewritten, stats, linked_content_ids = _rewrite_lved_fragment(body, assets_dir=assets_dir, media=media_lookup, loose=loose_lookup, addr_lookup=addr_lookup)
        safe_name = _safe_lved_named_page_stem(name, fallback=f"info_{row['id']}")
        target = out_dir / f"info_{safe_name}.html"
        target.write_text(
            _lved_page(name or f"info {row['id']}", f'<main class="lv-info"><div class="lv-meta">info.id={row["id"]} name={html.escape(name)}</div>{rewritten}</main>'),
            encoding="utf-8",
        )
        pages.append(
            {
                "id": _sqlite_optional_int(row["id"]),
                "rowid": int(row["sqlite_rowid"]),
                "name": name,
                "path": target.name,
                "body_bytes": len(body.encode("utf-8")),
                "rewrite": stats,
                "linked_content_ids": sorted(linked_content_ids),
            }
        )
    return pages


def _write_lved_named_body_pages(
    con: sqlite3.Connection,
    out_dir: Path,
    *,
    table: str,
    path_prefix: str,
    page_class: str,
    media_lookup: dict[str, tuple[int, str, bytes]],
    loose_lookup: dict[str, Path],
    limit: int,
    addr_lookup: dict[str, LvedAddrResolution] | None = None,
) -> list[dict[str, Any]]:
    if not _sqlite_table_exists(con, table):
        return []
    columns = {column.casefold() for column in _sqlite_columns(con, table)}
    if not {"id", "name", "body"} <= columns:
        return []
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    for row in con.execute(f"select id, name, body from {_quote_identifier(table)} order by id limit ?", (limit,)):
        name = _sqlite_text_from_value(row["name"])
        body = _sqlite_text_from_value(row["body"])
        rewritten, stats, linked_content_ids = _rewrite_lved_fragment(body, assets_dir=assets_dir, media=media_lookup, loose=loose_lookup, addr_lookup=addr_lookup)
        safe_name = _safe_lved_named_page_stem(name, fallback=f"{table}_{row['id']}")
        target = out_dir / f"{path_prefix}_{safe_name}.html"
        target.write_text(
            _lved_page(
                name or f"{table} {row['id']}",
                f'<main class="{html.escape(page_class)}"><div class="lv-meta">{html.escape(table)}.id={row["id"]} name={html.escape(name)}</div>{rewritten}</main>',
            ),
            encoding="utf-8",
        )
        pages.append(
            {
                "id": int(row["id"]),
                "name": name,
                "path": target.name,
                "body_bytes": len(body.encode("utf-8")),
                "rewrite": stats,
                "linked_content_ids": sorted(linked_content_ids),
            }
        )
    return pages


def _tree_rows_for_report(retained: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in retained.get("files") or []:
        if item.get("kind") == "text_tree":
            rows.append(item)
    return rows


def _lved_tree_href(row: LvedTextTreeRow) -> str | None:
    if row.target_value is None or row.target_value <= 0:
        return None
    return _lved_entry_href(row.target_value)


def _write_lved_tree_pages(
    payload_path: Path,
    out_dir: Path,
    *,
    limit: int = 20000,
    stub_entry_limit: int = 500,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    stubbed_entry_ids: set[int] = set()
    for path in _retained_resource_paths(payload_path):
        data = path.read_bytes()
        if data.startswith(b"SSEDDATA"):
            continue
        decoded = _decode_retained_text(data)
        if decoded is None:
            continue
        encoding, text = decoded
        first_line = next((line for line in text.splitlines() if line.strip()), "")
        first_parts = first_line.split("\t")
        if not (len(first_parts) >= 3 and _int_token(first_parts[1]) is not None):
            continue
        rows, unparsed = _parse_lved_text_tree_rows(text)
        if not rows:
            continue
        safe_name = _safe_asset_name(path.relative_to(payload_path.parent).as_posix().replace("/", "_"))
        target = out_dir / f"tree_{safe_name}.html"
        items: list[str] = []
        linked_rows = 0
        deferred_rows = 0
        page_stubbed_rows = 0
        query_rows = 0
        for row in rows[:limit]:
            href = _lved_tree_href(row)
            label = html.escape(row.label or row.raw_target)
            style = f"padding-inline-start:{max(0, row.depth) * 1.25:.2f}rem"
            attrs = [
                f'data-lv-depth="{row.depth}"',
                f'data-lv-target="{html.escape(row.raw_target, quote=True)}"',
                f'data-lv-target-form="{html.escape(row.target_form, quote=True)}"',
            ]
            if row.query_params:
                query_rows += 1
                attrs.append(
                    'data-lv-query="'
                    + html.escape("&".join(f"{key}={value}" for key, value in row.query_params), quote=True)
                    + '"'
                )
            if href and row.target_value is not None:
                entry_path = out_dir / f"entry_{row.target_value}.html"
                if not entry_path.exists():
                    if len(stubbed_entry_ids) < stub_entry_limit:
                        _write_lved_entry_stub(out_dir, row.target_value)
                        stubbed_entry_ids.add(row.target_value)
                        page_stubbed_rows += 1
                    else:
                        href = None
                        deferred_rows += 1
                        attrs.append(f'data-lv-deferred-entry="{row.target_value}"')
            if href:
                linked_rows += 1
                content = f'<a href="{html.escape(href, quote=True)}">{label}</a>'
            else:
                content = f"<span>{label}</span>"
            items.append(f'<li style="{style}" {" ".join(attrs)}>{content}</li>')
        if len(rows) > limit:
            items.append(f"<li>truncated after {limit} rows</li>")
        body = (
            '<main class="lv-tree">'
            f'<div class="lv-meta">tree={html.escape(path.relative_to(payload_path.parent).as_posix())} '
            f"encoding={html.escape(encoding)} rows={len(rows)} unparsed={unparsed}</div>"
            '<ul class="lv-lved-tree">'
            + "".join(items)
            + "</ul></main>"
        )
        target.write_text(_lved_page(path.name, body), encoding="utf-8")
        pages.append(
            {
                "path": target.name,
                "source": path.relative_to(payload_path.parent).as_posix(),
                "rows": len(rows),
                "rendered_rows": min(len(rows), limit),
                "linked_rows": linked_rows,
                "deferred_entry_rows": deferred_rows,
                "stubbed_entry_rows": page_stubbed_rows,
                "query_rows": query_rows,
                "unparsed_lines": unparsed,
            }
        )
    return pages


def _sqlite_non_empty_count(con: sqlite3.Connection, table: str, column: str) -> int | None:
    try:
        return int(
            con.execute(
                f"select count(*) from {_quote_identifier(table)} "
                f"where {_quote_identifier(column)} is not null and {_quote_identifier(column)} != ''"
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return None


def _sqlite_like_count(con: sqlite3.Connection, table: str, column: str, pattern: str) -> int | None:
    try:
        return int(
            con.execute(
                f"select count(*) from {_quote_identifier(table)} "
                f"where {_quote_identifier(column)} like ?",
                (pattern,),
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return None


def _sqlite_int_values_from_column(con: sqlite3.Connection, table: str, column: str, *, limit: int = 4096) -> set[int]:
    if not _sqlite_table_exists(con, table):
        return set()
    values: set[int] = set()
    try:
        rows = con.execute(
            f"select {_quote_identifier(column)} from {_quote_identifier(table)} "
            f"where {_quote_identifier(column)} is not null limit ?",
            (limit,),
        )
    except sqlite3.Error:
        return values
    for row in rows:
        value = _int_token(str(row[0]))
        if value is not None:
            values.add(value)
    return values


def _sqlite_correlate_common_targets(con: sqlite3.Connection, values: set[int]) -> dict[str, Any]:
    correlations = {
        "content_id": _sqlite_distinct_matches(con, "content", "id", values),
        "list_id": _sqlite_distinct_matches(con, "list", "id", values),
        "list_refid": _sqlite_distinct_matches(con, "list", "refid", values),
        "info_id": _sqlite_distinct_matches(con, "info", "id", values),
        "media_id": _sqlite_distinct_matches(con, "media", "id", values),
    }
    if _sqlite_table_exists(con, "listsub"):
        correlations["listsub_id"] = _sqlite_distinct_matches(con, "listsub", "id", values)
        correlations["listsub_refid"] = _sqlite_distinct_matches(con, "listsub", "refid", values)
    best_name = "none"
    best_count = 0
    for name, result in correlations.items():
        count = int(result.get("matched_values", 0))
        if count > best_count:
            best_name = name
            best_count = count
    return {
        "status": "matched" if best_count else "unmatched",
        "sampled_distinct_values": len(values),
        "best_match": best_name,
        "best_matched_values": best_count,
        "matches": correlations,
    }


def _sqlite_optional_feature_tables(con: sqlite3.Connection) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    shadow_suffixes = ("_content", "_docsize", "_segdir", "_segments", "_stat")
    for row in con.execute("select name, sql from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
        name = str(row["name"])
        lower = name.casefold()
        if lower in _LVED_CORE_TABLES or ((lower.startswith("search_") or lower.startswith("searchsub_")) and lower.endswith(shadow_suffixes)):
            continue
        columns = _sqlite_columns(con, name)
        item: dict[str, Any] = {
            "name": name,
            "role": _sqlite_table_role(name, row["sql"] if isinstance(row["sql"], str) else None),
            "row_count": _safe_count(con, name),
            "columns": columns,
            "features": _sqlite_table_features(name, columns),
        }
        target_columns: dict[str, Any] = {}
        for column in columns:
            lower_column = column.casefold()
            if lower_column in {"id", "refid", "data_id", "f_dataid", "f_id", "anchor"}:
                values = _sqlite_int_values_from_column(con, name, column)
                if values:
                    target_columns[column] = _sqlite_correlate_common_targets(con, values)
        if {"block", "offset"} <= {column.casefold() for column in columns}:
            item["address_shape"] = "block_offset_bridge_candidate"
        if target_columns:
            item["target_columns"] = target_columns
        tables.append(item)
    return tables


def _lved_provider_target_columns(con: sqlite3.Connection, table: str, columns: list[str]) -> dict[str, Any]:
    target_columns: dict[str, Any] = {}
    for column in columns:
        lower_column = column.casefold()
        if lower_column in {"id", "refid", "data_id", "f_dataid", "f_id", "anchor"}:
            values = _sqlite_int_values_from_column(con, table, column)
            if values:
                target_columns[column] = _sqlite_correlate_common_targets(con, values)
    return target_columns


def _lved_optional_provider_readiness(con: sqlite3.Connection) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for table, provider_kind, status in (
        ("binran", "named_html_reference_pages", "active_render_provider"),
        ("t_contents", "legacy_html_content_rows", "understood_legacy_content_source"),
        ("t_convert", "legacy_block_offset_bridge", "understood_address_bridge"),
        ("exam", "example_lookup", "metadata_provider"),
        ("genre", "genre_filter_lookup", "metadata_provider"),
        ("bunya", "field_category_lookup", "metadata_provider"),
        ("kanji", "kanji_lookup", "metadata_provider"),
        ("kanji_all", "kanji_lookup", "metadata_provider"),
        ("kwit", "keyword_lookup", "metadata_provider"),
        ("joken", "condition_lookup", "metadata_provider"),
        ("sakuin", "index_lookup", "metadata_provider"),
        ("item", "advanced_filter_lookup", "metadata_provider"),
        ("mediasub", "secondary_media_store", "active_resource_provider"),
        ("sound", "audio_blob_store", "active_resource_provider"),
    ):
        if not _sqlite_table_exists(con, table):
            continue
        columns = _sqlite_columns(con, table)
        item: dict[str, Any] = {
            "table": table,
            "provider_kind": provider_kind,
            "status": status,
            "row_count": _safe_count(con, table),
            "columns": columns,
        }
        if table == "binran":
            item["named_body_rows"] = _sqlite_non_empty_count(con, table, "body")
            item["body_signals"] = _lved_fragment_signal_summary(con, table, "body", limit=256)
        elif table == "t_contents":
            item["html_rows"] = _sqlite_non_empty_count(con, table, "f_Html")
            item["plain_rows"] = _sqlite_non_empty_count(con, table, "f_Plane")
            item["keyword_rows"] = _sqlite_non_empty_count(con, table, "f_Keyword")
            item["html_signals"] = _lved_fragment_signal_summary(con, table, "f_Html", limit=256)
        elif table == "t_convert":
            item["block_offset_rows"] = _sqlite_non_empty_count(con, table, "block")
            item["old_title_rows"] = _sqlite_non_empty_count(con, table, "old_title")
        elif table in {"mediasub", "sound"}:
            if "type" in {column.casefold() for column in columns}:
                item["type_counts"] = _sqlite_value_distribution(con, table, "type", limit=20)
        targets = _lved_provider_target_columns(con, table, columns)
        if targets:
            item["target_columns"] = targets
        providers.append(item)
    return providers


def _lved_search_provider_summaries(con: sqlite3.Connection) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for search_table, list_table in (("search", "list"), ("searchsub", "listsub")):
        if not _sqlite_table_exists(con, search_table):
            continue
        columns = _sqlite_columns(con, search_table)
        column_set = {column.casefold() for column in columns}
        modes = []
        for mode, required in (
            ("exact", "filter"),
            ("forward", "forward"),
            ("backward", "back"),
            ("partial", "part"),
            ("fulltext", "fts"),
            ("filter", "filter"),
            ("advanced", "advanced"),
            ("advanced1", "advanced1"),
            ("advanced2", "advanced2"),
            ("advanced3", "advanced3"),
        ):
            if required in column_set:
                modes.append(mode)
        column_counts = {
            column: {"non_empty_rows": _sqlite_non_empty_count(con, search_table, column)}
            for column in columns
            if not column.casefold().startswith("docid")
        }
        if "filter" in column_set:
            column_counts["filter"]["boundary_token_rows"] = _sqlite_like_count(con, search_table, "filter", "%∥%")
        providers.append(
            {
                "search_table": search_table,
                "list_table": list_table,
                "role": _sqlite_table_role(search_table, None),
                "row_count": _safe_count(con, search_table),
                "list_row_count": _safe_count(con, list_table) if _sqlite_table_exists(con, list_table) else None,
                "columns": columns,
                "available_modes": modes,
                "column_counts": column_counts,
            }
        )
    return providers


def _lved_fragment_signal_summary(con: sqlite3.Connection, table: str, column: str, *, limit: int = 4096) -> dict[str, Any]:
    if not _sqlite_table_exists(con, table) or column not in _sqlite_columns(con, table):
        return {"status": "missing"}
    shape_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    protocol_counts: Counter[str] = Counter()
    href_counts: Counter[str] = Counter()
    image_ref_count = 0
    rows = 0
    try:
        source = con.execute(
            f"select {_quote_identifier(column)} from {_quote_identifier(table)} "
            f"where {_quote_identifier(column)} is not null and length({_quote_identifier(column)}) > 0 "
            "order by rowid limit ?",
            (limit,),
        )
    except sqlite3.Error:
        return {"status": "sqlite_error"}
    for row in source:
        rows += 1
        body = str(row[0])
        lower = body[:512].casefold()
        if "<html" in lower or "<head" in lower:
            shape_counts["self_contained_html"] += 1
        else:
            shape_counts["html_fragment"] += 1
        class_counts.update(re.findall(r"""class=["']([^"']+)["']""", body))
        protocol_counts.update(token.casefold() for token in _LVED_PROTOCOL_TOKEN_RE.findall(body))
        for _quote, href in _HTML_HREF_RE.findall(body):
            href_lower = href.casefold()
            if href_lower.startswith("lved."):
                href_counts[href_lower.split(":", 1)[0] + ":"] += 1
        image_ref_count += len(_HTML_SRC_RE.findall(body))
    return {
        "status": "inspected",
        "sampled_rows": rows,
        "shape_counts": dict(sorted(shape_counts.items())),
        "top_classes": dict(class_counts.most_common(40)),
        "protocol_counts": dict(protocol_counts.most_common()),
        "href_protocol_counts": dict(href_counts.most_common()),
        "image_ref_count": image_ref_count,
    }


def render_lved_sqlite_showcase(
    sqlite_path: Path,
    payload_path: Path,
    out_dir: Path,
    *,
    queries: list[str] | None = None,
    modes: list[str] | None = None,
    limit: int = 10,
    linked_entry_limit: int = 500,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rendered_entries: dict[int, dict[str, Any]] = {}
    try:
        media_lookup = _media_lookup(con)
        loose_lookup = _loose_resource_lookup(payload_path)
        addr_lookup = _lved_addr_resolution_lookup(con)
        search_reports: list[dict[str, Any]] = []
        queries = queries or []
        modes = modes or ["exact", "forward", "backward", "partial", "fulltext"]
        for query in queries:
            for mode in modes:
                report = search_lved_sqlite_database(sqlite_path, query, mode=mode, limit=limit)
                search_reports.append(report)
                for hit in report.get("hits", []):
                    content_id = int(hit["content_id"])
                    if content_id in rendered_entries:
                        continue
                    rendered = _write_lved_entry_page(
                        con,
                        payload_path,
                        out_dir,
                        content_id,
                        title=re.sub(r"<[^>]+>", "", hit.get("title_html") or "") or f"entry {content_id}",
                        media_lookup=media_lookup,
                        loose_lookup=loose_lookup,
                        addr_lookup=addr_lookup,
                    )
                    if rendered is not None:
                        rendered_entries[content_id] = rendered
        if not rendered_entries and _sqlite_table_exists(con, "content"):
            for row in con.execute("select id from content order by id limit ?", (limit,)):
                rendered = _write_lved_entry_page(
                    con,
                    payload_path,
                    out_dir,
                    int(row["id"]),
                    media_lookup=media_lookup,
                    loose_lookup=loose_lookup,
                    addr_lookup=addr_lookup,
                )
                if rendered is not None:
                    rendered_entries[int(row["id"])] = rendered
        pending_links: list[int] = []
        seen_pending: set[int] = set()
        for rendered in rendered_entries.values():
            for linked_id in rendered.get("linked_content_ids", []):
                if linked_id not in rendered_entries and linked_id not in seen_pending:
                    pending_links.append(int(linked_id))
                    seen_pending.add(int(linked_id))
        while pending_links and len(seen_pending) <= linked_entry_limit:
            linked_id = pending_links.pop(0)
            if linked_id in rendered_entries:
                continue
            rendered = _write_lved_entry_page(
                con,
                payload_path,
                out_dir,
                linked_id,
                media_lookup=media_lookup,
                loose_lookup=loose_lookup,
                addr_lookup=addr_lookup,
            )
            if rendered is None:
                continue
            rendered_entries[linked_id] = rendered
            for next_id in rendered.get("linked_content_ids", []):
                if next_id not in rendered_entries and next_id not in seen_pending and len(seen_pending) < linked_entry_limit:
                    pending_links.append(int(next_id))
                    seen_pending.add(int(next_id))
        info_pages = _write_lved_info_pages(
            con,
            payload_path,
            out_dir,
            limit=max(limit, 1000),
            media_lookup=media_lookup,
            loose_lookup=loose_lookup,
            addr_lookup=addr_lookup,
        )
        binran_pages = _write_lved_named_body_pages(
            con,
            out_dir,
            table="binran",
            path_prefix="binran",
            page_class="lv-info",
            media_lookup=media_lookup,
            loose_lookup=loose_lookup,
            limit=1000,
            addr_lookup=addr_lookup,
        )
        stubbed_entries: dict[int, dict[str, Any]] = {}
        for source in [*rendered_entries.values(), *info_pages, *binran_pages]:
            for linked_id in source.get("linked_content_ids", []):
                linked_id = int(linked_id)
                if linked_id not in rendered_entries and linked_id not in stubbed_entries:
                    stubbed_entries[linked_id] = _write_lved_entry_stub(out_dir, linked_id)
        retained = inspect_lved_retained_resources(payload_path, sqlite_path)
        tree_summaries = _tree_rows_for_report(retained)
        tree_pages = _write_lved_tree_pages(payload_path, out_dir, stub_entry_limit=linked_entry_limit)
        links = []
        for rendered in rendered_entries.values():
            links.append(f'<a class="lv-search-hit" href="{html.escape(rendered["path"])}">entry {rendered["content_id"]}</a>')
        for page in info_pages:
            links.append(f'<a class="lv-search-hit" href="{html.escape(page["path"])}">info {html.escape(page["name"])}</a>')
        for page in binran_pages:
            links.append(f'<a class="lv-search-hit" href="{html.escape(page["path"])}">binran {html.escape(page["name"])}</a>')
        for page in tree_pages:
            links.append(f'<a class="lv-search-hit" href="{html.escape(page["path"])}">tree {html.escape(page["source"])}</a>')
        search_html = []
        for report in search_reports:
            search_html.append(f'<h2>{html.escape(report["mode"])}: {html.escape(report["query"])}</h2>')
            search_html.append(f'<p>{html.escape(report.get("strategy", report.get("reason", "")))}; hits={report.get("hit_count", 0)}</p>')
            for hit in report.get("hits", []):
                title_fragment, _title_stats, _title_links = _rewrite_lved_fragment(
                    hit.get("title_html") or html.escape(str(hit["content_id"])),
                    assets_dir=out_dir / "assets",
                    media=media_lookup,
                    loose=loose_lookup,
                    addr_lookup=addr_lookup,
                )
                href = _lved_entry_href(int(hit["content_id"]), hit.get("anchor") or "")
                search_html.append(
                    f'<a class="lv-search-hit" href="{href}">'
                    f"{title_fragment}"
                    f'<small> content={int(hit["content_id"])} list={int(hit["list_id"])}</small></a>'
                )
        tree_html = "".join(
            f'<li>{html.escape(str(item.get("path")))}: rows={item.get("rows")} role={html.escape(str(item.get("role")))}</li>'
            for item in tree_summaries
        )
        index_body = (
            '<main class="lv-tree">'
            "<h1>LVED_SQLITE3 showcase</h1>"
            "<h2>Rendered pages</h2>"
            + "".join(links)
            + "<h2>Search probes</h2>"
            + "".join(search_html)
            + "<h2>Retained trees</h2><ul>"
            + tree_html
            + "</ul></main>"
        )
        (out_dir / "index.html").write_text(_lved_page("LVED_SQLITE3 showcase", index_body), encoding="utf-8")
        return {
            "status": "rendered",
            "out_dir": str(out_dir),
            "entry_pages": list(rendered_entries.values()),
            "stub_entry_pages": list(stubbed_entries.values()),
            "info_pages": info_pages,
            "binran_pages": binran_pages,
            "tree_pages": tree_pages,
            "search_reports": search_reports,
            "retained_tree_summaries": tree_summaries,
        }
    finally:
        con.close()


def inspect_lved_reader_features(sqlite_path: Path, payload_path: Path, *, sample_queries: list[str] | None = None) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        info_pages: list[dict[str, Any]] = []
        if _sqlite_table_exists(con, "info"):
            for row in con.execute("select rowid as sqlite_rowid, id, name, length(body) as body_length, media from info order by rowid"):
                name = "" if row["name"] is None else str(row["name"])
                lower = name.casefold()
                if "hanrei" in lower or "凡例" in name:
                    role = "hanrei"
                elif lower in {"index.html", "index"}:
                    role = "home_index"
                elif "copyright" in lower:
                    role = "copyright"
                else:
                    role = "info"
                info_pages.append(
                    {
                        "rowid": int(row["sqlite_rowid"]),
                        "id": _sqlite_optional_int(row["id"]),
                        "name": name,
                        "role": role,
                        "body_length": int(row["body_length"] or 0),
                        "has_media_field": bool(row["media"]),
                    }
                )
        table_roles = {}
        for row in con.execute("select name, sql from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
            name = str(row["name"])
            table_roles[name] = _sqlite_table_role(name, row["sql"] if isinstance(row["sql"], str) else None)
        content_signals = _lved_fragment_signal_summary(con, "content", "body", limit=4096)
        info_signals = _lved_fragment_signal_summary(con, "info", "body", limit=4096)
        addr_link_keys = _lved_addr_link_keys(con)
        addr_lookup = _lved_addr_resolution_lookup(con, link_keys=addr_link_keys)
        retained = inspect_lved_retained_resources(payload_path, sqlite_path)
        search_probes = []
        for query in sample_queries or []:
            for mode in ("exact", "forward", "backward", "partial", "fulltext", "advanced", "advanced1", "advanced2", "advanced3"):
                search_probes.append(search_lved_sqlite_database(sqlite_path, query, mode=mode, limit=5))
        return {
            "status": "inspected",
            "table_roles": table_roles,
            "info_pages": info_pages,
            "optional_feature_tables": _sqlite_optional_feature_tables(con),
            "optional_provider_readiness": _lved_optional_provider_readiness(con),
            "search_providers": _lved_search_provider_summaries(con),
            "home_surfaces": [
                {"kind": page["role"], "name": page["name"], "id": page["id"], "status": "available"}
                for page in info_pages
                if page["role"] in {"home_index", "hanrei", "copyright"}
            ],
            "render_readiness": {
                "content_body": content_signals,
                "info_body": info_signals,
                "lved_addr_resolution": {
                    "target_count": len(addr_link_keys),
                    "resolved_target_count": len(addr_lookup),
                    "strategy": "first_following_same_block_html_anchor_within_0x100",
                    "status": "available" if addr_lookup else ("unresolved" if addr_link_keys else "not_present"),
                },
                "body_shape_counts": content_signals.get("shape_counts", {}),
                "top_classes": content_signals.get("top_classes", {}),
                "protocol_counts": content_signals.get("protocol_counts", {}),
                "sample_image_ref_count": content_signals.get("image_ref_count", 0),
                "requires_viewer_templates_or_css": bool((content_signals.get("shape_counts") or {}).get("html_fragment")),
            },
            "retained_resources": {
                "file_count": retained["file_count"],
                "role_counts": retained["role_counts"],
                "tree_files": [
                    {"path": item.get("path"), "role": item.get("role"), "rows": item.get("rows"), "target_correlation": item.get("target_correlation")}
                    for item in retained.get("files", [])
                    if item.get("kind") == "text_tree"
                ],
            },
            "search_probes": search_probes,
        }
    finally:
        con.close()


def _sqlite_correlate_tree_targets(con: sqlite3.Connection | None, values: set[int]) -> dict[str, Any]:
    if con is None:
        return {"status": "not_attempted"}
    positive = {value for value in values if value > 0}
    correlations = {
        "content_id": _sqlite_distinct_matches(con, "content", "id", positive),
        "list_refid": _sqlite_distinct_matches(con, "list", "refid", positive),
        "list_id": _sqlite_distinct_matches(con, "list", "id", positive),
        "list_anchor_padded_decimal": _sqlite_anchor_matches(con, positive),
        "info_id": _sqlite_distinct_matches(con, "info", "id", positive),
        "media_id": _sqlite_distinct_matches(con, "media", "id", positive),
    }
    best_name = "none"
    best_count = 0
    for name, result in correlations.items():
        count = int(result.get("matched_values", 0))
        if count > best_count:
            best_name = name
            best_count = count
    status = "matched" if best_count else "unmatched"
    return {
        "status": status,
        "distinct_positive_targets": len(positive),
        "best_match": best_name,
        "best_matched_values": best_count,
        "matches": correlations,
    }


def _inspect_lved_text_tree(path: Path, text: str, encoding: str, con: sqlite3.Connection | None) -> dict[str, Any]:
    blank_labels = 0
    max_depth = 0
    target_values: set[int] = set()
    target_forms: Counter[str] = Counter()
    query_keys: Counter[str] = Counter()
    depth_counts: Counter[str] = Counter()
    sentinel_rows = 0
    zero_rows = 0
    tree_rows, unresolved_rows = _parse_lved_text_tree_rows(text)
    for row in tree_rows:
        max_depth = max(max_depth, row.depth)
        depth_counts[str(row.depth)] += 1
        if not row.label:
            blank_labels += 1
        target_forms[row.target_form] += 1
        query_keys.update(key for key, _value in row.query_params)
        if row.target_value is None:
            continue
        target_values.add(row.target_value)
        if row.target_form == "negative_sentinel":
            sentinel_rows += 1
        elif row.target_form == "zero_root_or_sentinel":
            zero_rows += 1
    correlation = _sqlite_correlate_tree_targets(con, target_values)
    role = "lved_text_navigation_tree"
    if correlation.get("best_match") in {"content_id", "list_refid", "list_anchor_padded_decimal"}:
        role = "sqlite_content_navigation_tree"
    elif tree_rows and int(correlation.get("best_matched_values", 0) or 0) == 0:
        role = "unmatched_text_navigation_tree"
    return {
        "kind": "text_tree",
        "role": role,
        "encoding": encoding,
        "rows": len(tree_rows),
        "unparsed_lines": unresolved_rows,
        "blank_label_rows": blank_labels,
        "max_depth": max_depth,
        "sentinel_rows": sentinel_rows,
        "zero_target_rows": zero_rows,
        "query_rows": sum(1 for row in tree_rows if row.query_params),
        "query_key_counts": dict(query_keys.most_common()),
        "target_forms": dict(sorted(target_forms.items())),
        "depth_counts": dict(sorted(depth_counts.items(), key=lambda item: int(item[0]))),
        "target_correlation": correlation,
    }


def _inspect_lved_aux_text_tree(path: Path, text: str, encoding: str, con: sqlite3.Connection | None) -> dict[str, Any]:
    rows = 0
    blank_labels = 0
    max_depth = 0
    first_values: set[int] = set()
    second_values: set[int] = set()
    second_sentinel_rows = 0
    depth_counts: Counter[str] = Counter()
    unparsed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 3:
            unparsed += 1
            continue
        first = _hex_token(parts[0])
        second = _hex_token(parts[1])
        if first is None or second is None:
            unparsed += 1
            continue
        label_index = None
        label = ""
        for index, part in enumerate(parts[2:], start=1):
            if part:
                label_index = index
                label = part
        if label_index is None:
            blank_labels += 1
            label_index = 1
        rows += 1
        first_values.add(first)
        second_values.add(second)
        if second == 0xFFFF:
            second_sentinel_rows += 1
        max_depth = max(max_depth, label_index)
        depth_counts[str(label_index)] += 1
    # These auxiliary trees use two fixed-width hexadecimal fields rather than
    # direct decimal content ids. Try the first field against SQLite anyway so
    # the report can prove whether it is a content target or a filter/rank key.
    correlation = _sqlite_correlate_tree_targets(con, first_values)
    role = "auxiliary_hex_text_tree"
    if int(correlation.get("best_matched_values", 0) or 0):
        role = "auxiliary_hex_tree_with_sqlite_targets"
    return {
        "kind": "aux_text_tree",
        "role": role,
        "encoding": encoding,
        "rows": rows,
        "unparsed_lines": unparsed,
        "blank_label_rows": blank_labels,
        "max_depth": max_depth,
        "second_field_sentinel_rows": second_sentinel_rows,
        "distinct_first_values": len(first_values),
        "distinct_second_values": len(second_values),
        "depth_counts": dict(sorted(depth_counts.items(), key=lambda item: int(item[0]))),
        "target_correlation": correlation,
    }


def _inspect_lved_ssed_index(path: Path) -> dict[str, Any]:
    header = parse_sseddata_header(path)
    component_type = int(header["kind"])
    expanded = expand_sseddata_file(path)
    result = scan_index_component(
        path.name,
        component_type,
        expanded,
        int(header["start_block"]),
        gaiji="h-placeholder",
        gaiji_map={},
    )
    return {
        "kind": "ssed_index",
        "role": "retained_ssed_index",
        "storage": str(header["storage"]),
        "component_type": f"{component_type:02x}",
        "start_block": int(header["start_block"]),
        "end_block": int(header["end_block"]),
        "expanded_bytes": len(expanded),
        "index_summary": result.as_dict(),
        "target_correlation": {
            "status": "not_applicable",
            "reason": "SSED index leaf targets are block/offset pointers, not SQLite content ids.",
        },
    }


def inspect_lved_retained_resources(payload_path: Path, sqlite_path: Path | None = None) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) if sqlite_path is not None else None
    if con is not None:
        con.row_factory = sqlite3.Row
    files: list[dict[str, Any]] = []
    kind_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    try:
        for path in _retained_resource_paths(payload_path):
            rel = path.relative_to(payload_path.parent).as_posix()
            data = path.read_bytes()
            item: dict[str, Any] = {
                "path": rel,
                "size": path.stat().st_size,
            }
            try:
                if data.startswith(b"SSEDDATA"):
                    item.update(_inspect_lved_ssed_index(path))
                else:
                    decoded = _decode_retained_text(data)
                    if decoded is None:
                        item.update({"kind": "unknown", "role": "unknown", "reason": "not UTF-8/CP932 text or SSEDDATA"})
                    else:
                        encoding, text = decoded
                        first_line = next((line for line in text.splitlines() if line.strip()), "")
                        first_parts = first_line.split("\t")
                        if len(first_parts) >= 3 and _hex_token(first_parts[0]) is not None and _hex_token(first_parts[1]) is not None:
                            item.update(_inspect_lved_aux_text_tree(path, text, encoding, con))
                        elif len(first_parts) >= 3 and _int_token(first_parts[1]) is not None:
                            item.update(_inspect_lved_text_tree(path, text, encoding, con))
                        else:
                            item.update({"kind": "text_unknown", "role": "unknown_text", "encoding": encoding})
            except Exception as exc:
                item.update({"kind": "inspect_error", "role": "inspect_error", "reason": f"{type(exc).__name__}: {exc}"})
            kind_counts[str(item.get("kind", "unknown"))] += 1
            role_counts[str(item.get("role", "unknown"))] += 1
            files.append(item)
    finally:
        if con is not None:
            con.close()
    return {
        "status": "inspected",
        "file_count": len(files),
        "kind_counts": dict(sorted(kind_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
        "files": files,
    }


def _package_layout_features(payload_path: Path) -> dict[str, Any]:
    root = payload_path.parent
    resource_dirs = []
    retained_ssed = []
    tree_indexes = []
    extension_counts: Counter[str] = Counter()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        rel = path.relative_to(root).as_posix()
        if ":zone.identifier" in rel.casefold():
            continue
        if path.is_dir():
            if path.name.casefold() in {"res", "resources", "templates", "img", "images", "sound", "media"}:
                resource_dirs.append(rel)
            continue
        extension = path.suffix.casefold() or "<none>"
        extension_counts[extension] += 1
        name = path.name.casefold()
        if name.endswith(_RETAINED_RESOURCE_SUFFIXES) or name in _RETAINED_RESOURCE_NAMES:
            retained_ssed.append(rel)
        if name == "tree.idx":
            tree_indexes.append(rel)
    return {
        "resource_dirs": resource_dirs,
        "retained_ssed_like_files": retained_ssed,
        "tree_idx_files": tree_indexes,
        "extension_counts": dict(sorted(extension_counts.items())),
    }


def classify_lved_payload(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(16)
    size = path.stat().st_size
    if header == b"SQLite format 3\x00":
        return "plaintext_sqlite"
    if header.startswith(b"SSEDINFO") or header.startswith(b"SSEDDATA"):
        return "ssed"
    if size and size % LVED_SQLCIPHER_PAGE_SIZE == 0 and entropy_sample_for_file(path) > 7.95:
        return "sqlcipher_lved_candidate"
    return "unknown"


def inspect_lved_payload(path: Path) -> LvedPayload:
    size = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(32)
    return LvedPayload(
        path=path,
        kind="dbc" if path.suffix.lower() == ".dbc" else "main.data",
        size=size,
        pages_4096=size // LVED_SQLCIPHER_PAGE_SIZE,
        mod_4096=size % LVED_SQLCIPHER_PAGE_SIZE,
        sha256=sha256_file(path),
        entropy_sample=entropy_sample_for_file(path),
        header_hex=header.hex(),
        classification=classify_lved_payload(path),
        inferred_dict_code=infer_lved_dict_code(path),
    )


def derive_lved_sqlcipher_key(dict_id: int, dict_code: str) -> str:
    """Return the SQLCipher key string constructed by the observed LVED viewer.

    The viewer uses only dictionary metadata: the numeric dictionary id and the
    product/dictionary code. It does not use the product serial number for this
    SQLCipher key derivation path. Callers should derive it locally when
    decrypting a payload; reports must not emit final key strings.
    """

    if dict_id < 0:
        raise ValueError("dict_id must be non-negative")
    if not dict_code:
        raise ValueError("dict_code must be non-empty")
    key_code = (dict_code[0] + dict_code[-1]).lower()
    return "jlasgoi" + "ahoiam" + "pvsjhosD" + "Hfopj" + key_code + str(dict_id * 19286)


def _crypto_primitives():
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "SQLCipher validation requires the optional crypto dependency. "
            "Install with: python -m pip install -e '.[crypto]'"
        ) from exc
    return default_backend, hashes, Cipher, algorithms, modes, PBKDF2HMAC


def _derive_sqlcipher4_key(payload: bytes, key: str) -> bytes:
    default_backend, hashes, _cipher, _algorithms, _modes, PBKDF2HMAC = _crypto_primitives()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=32,
        salt=payload[:16],
        iterations=LVED_SQLCIPHER_KDF_ITER,
        backend=default_backend(),
    )
    return kdf.derive(key.encode("utf-8"))


def decrypt_lved_sqlcipher_page(
    page: bytes,
    key_material: bytes,
    *,
    page_no: int,
) -> bytes:
    default_backend, _hashes, Cipher, algorithms, modes, _PBKDF2HMAC = _crypto_primitives()
    page_size = LVED_SQLCIPHER_PAGE_SIZE
    reserve = LVED_SQLCIPHER_RESERVE_BYTES
    usable = page_size - reserve
    if len(page) != page_size:
        raise ValueError(f"expected {page_size}-byte page")
    if page_no == 1:
        ciphertext = page[16:usable]
        plaintext = Cipher(
            algorithms.AES(key_material),
            modes.CBC(page[usable : usable + 16]),
            backend=default_backend(),
        ).decryptor().update(ciphertext)
        return b"SQLite format 3\x00" + plaintext + bytes(reserve)
    plaintext = Cipher(
        algorithms.AES(key_material),
        modes.CBC(page[usable : usable + 16]),
        backend=default_backend(),
    ).decryptor().update(page[:usable])
    return plaintext + bytes(reserve)


def validate_lved_sqlcipher4(path: Path, key: str) -> dict[str, Any]:
    with path.open("rb") as handle:
        first_page = handle.read(LVED_SQLCIPHER_PAGE_SIZE)
    if len(first_page) != LVED_SQLCIPHER_PAGE_SIZE:
        return {"valid": False, "reason": "file is shorter than one page"}
    try:
        key_material = _derive_sqlcipher4_key(first_page, key)
        first_plain = decrypt_lved_sqlcipher_page(first_page, key_material, page_no=1)
    except Exception as exc:
        return {"valid": False, "reason": str(exc)}

    header_tail = first_plain[16:64]
    valid = (
        first_plain.startswith(b"SQLite format 3\x00")
        and header_tail[:2] in (b"\x10\x00", b"\x20\x00", b"\x04\x00")
        and header_tail[2] in (1, 2)
        and header_tail[3] in (1, 2)
        and header_tail[4] == LVED_SQLCIPHER_RESERVE_BYTES
    )
    return {
        "valid": valid,
        "page_size": LVED_SQLCIPHER_PAGE_SIZE,
        "reserve_bytes": LVED_SQLCIPHER_RESERVE_BYTES,
        "kdf_iter": LVED_SQLCIPHER_KDF_ITER,
        "kdf_algorithm": LVED_SQLCIPHER_KDF_ALGORITHM,
        "sqlite_header_tail_hex": header_tail[:16].hex(),
        "reason": None if valid else "decrypted first page did not match SQLite header fields",
    }


def decrypt_lved_sqlcipher4_to_path(path: Path, out_path: Path, key: str) -> int:
    raw_size = path.stat().st_size
    if raw_size % LVED_SQLCIPHER_PAGE_SIZE:
        raise ValueError("payload size is not a 4096-byte page multiple")
    with path.open("rb") as source:
        first_page = source.read(LVED_SQLCIPHER_PAGE_SIZE)
    key_material = _derive_sqlcipher4_key(first_page, key)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("rb") as source, out_path.open("wb") as out:
        for page_no in range(1, raw_size // LVED_SQLCIPHER_PAGE_SIZE + 1):
            page = source.read(LVED_SQLCIPHER_PAGE_SIZE)
            out.write(decrypt_lved_sqlcipher_page(page, key_material, page_no=page_no))
            written += LVED_SQLCIPHER_PAGE_SIZE
    return written


MEMORY_KEY_RE = re.compile(r"jlasgoiahoiampvsjhosDHfopj[A-Za-z0-9]{2,40}")


def find_lved_key_candidates_in_dump(path: Path) -> list[str]:
    data = path.read_bytes()
    keys: set[str] = set()
    for encoding in ("latin1", "utf-16le"):
        text = data.decode(encoding, errors="ignore")
        keys.update(match.group(0) for match in MEMORY_KEY_RE.finditer(text) if len(match.group(0)) < 96)
    return sorted(keys)


def _inspect_lved_payload_task(
    payload: tuple[
        Path,
        int | None,
        str | None,
        str | None,
        list[str],
        bool,
        bool,
        bool,
        bool,
        tuple[str, ...],
        tuple[str, ...],
        int,
        int,
        str | None,
    ]
) -> dict[str, Any]:
    (
        path,
        dict_id,
        dict_code,
        explicit_key,
        memory_keys,
        inspect_sqlite,
        inspect_retained,
        inspect_loose_resources,
        inspect_reader,
        search_queries,
        search_modes,
        search_limit,
        render_link_limit,
        render_dir,
    ) = payload
    inspected = inspect_lved_payload(path)
    row: dict[str, Any] = {
        "path": str(inspected.path),
        "kind": inspected.kind,
        "size": inspected.size,
        "pages_4096": inspected.pages_4096,
        "mod_4096": inspected.mod_4096,
        "sha256": inspected.sha256,
        "entropy_sample": inspected.entropy_sample,
        "header_hex": inspected.header_hex,
        "classification": inspected.classification,
        "inferred_dict_code": inspected.inferred_dict_code,
        "package_layout": _package_layout_features(path),
        "validation": [],
    }
    validation_keys: list[tuple[str, str]] = []
    if explicit_key:
        validation_keys.append(("explicit_key", explicit_key))
    key_file = discover_lved_key_file(path)
    if key_file is not None:
        row["key_file"] = {
            "detected": True,
            "name": key_file.path.name,
            "match": key_file.match,
        }
        validation_keys.append(("local_key_file", read_lved_key_file(key_file.path)))
    else:
        row["key_file"] = {"detected": False}
    if dict_id is not None:
        code = dict_code or inspected.inferred_dict_code
        if code:
            validation_keys.append(("derived_metadata", derive_lved_sqlcipher_key(dict_id, code)))
    for candidate in memory_keys:
        validation_keys.append(("memory_dump_candidate", candidate))
    first_valid_key: str | None = None
    for source, candidate in validation_keys:
        result = validate_lved_sqlcipher4(path, candidate)
        if first_valid_key is None and result.get("valid"):
            first_valid_key = candidate
        safe_result = {key: value for key, value in result.items() if key != "reason" or value}
        safe_result["source"] = source
        row["validation"].append(safe_result)
    needs_decrypted = inspect_sqlite or inspect_retained or inspect_reader or bool(search_queries) or bool(render_dir)
    if needs_decrypted:
        if first_valid_key is None:
            if inspect_sqlite:
                row["sqlite"] = {
                    "status": "not_inspected",
                    "reason": "no valid SQLCipher key candidate",
                }
            if inspect_retained:
                row["retained_resources"] = inspect_lved_retained_resources(path)
            if inspect_reader:
                row["reader_features"] = {"status": "not_inspected", "reason": "no valid SQLCipher key candidate"}
            if search_queries:
                row["search"] = {"status": "not_inspected", "reason": "no valid SQLCipher key candidate", "queries": list(search_queries)}
            if render_dir:
                row["render"] = {"status": "not_rendered", "reason": "no valid SQLCipher key candidate"}
        else:
            with TemporaryDirectory() as temp_dir:
                decrypted = Path(temp_dir) / f"{path.name}.sqlite"
                try:
                    decrypt_lved_sqlcipher4_to_path(path, decrypted, first_valid_key)
                    if inspect_sqlite:
                        sqlite_report = inspect_lved_sqlite_database(decrypted)
                        sqlite_report["status"] = "inspected"
                        row["sqlite"] = sqlite_report
                    if inspect_retained:
                        row["retained_resources"] = inspect_lved_retained_resources(path, decrypted)
                    if inspect_reader:
                        row["reader_features"] = inspect_lved_reader_features(decrypted, path, sample_queries=list(search_queries))
                    if search_queries:
                        row["search"] = {
                            "status": "searched",
                            "queries": [
                                search_lved_sqlite_database(decrypted, query, mode=mode, limit=search_limit)
                                for query in search_queries
                                for mode in search_modes
                            ],
                        }
                    if render_dir:
                        package_dir = Path(render_dir) / (inspected.inferred_dict_code or path.parent.name)
                        row["render"] = render_lved_sqlite_showcase(
                            decrypted,
                            path,
                            package_dir,
                            queries=list(search_queries),
                            modes=list(search_modes),
                            limit=search_limit,
                            linked_entry_limit=render_link_limit,
                        )
                except Exception as exc:
                    error = {
                        "status": "inspect_error",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                    if inspect_sqlite:
                        row["sqlite"] = error
                    if inspect_retained:
                        retained = inspect_lved_retained_resources(path)
                        retained["sqlite_correlation_status"] = "not_attempted_after_decrypt_error"
                        row["retained_resources"] = retained
                    if inspect_reader:
                        row["reader_features"] = error
                    if search_queries:
                        row["search"] = error
                    if render_dir:
                        row["render"] = error
    if inspect_loose_resources:
        row["loose_resources"] = inspect_lved_loose_resources(path)
    return row


def inspect_lved_roots(
    roots: Iterable[Path],
    *,
    dict_id: int | None = None,
    dict_code: str | None = None,
    key: str | None = None,
    memory_dump: Path | None = None,
    inspect_sqlite: bool = False,
    inspect_retained: bool = False,
    inspect_loose_resources: bool = False,
    inspect_reader: bool = False,
    search_queries: Iterable[str] = (),
    search_modes: Iterable[str] = ("exact", "forward", "backward", "partial", "fulltext"),
    search_limit: int = 20,
    render_link_limit: int = 500,
    render_dir: Path | None = None,
    jobs: int | None = 1,
) -> dict[str, Any]:
    payload_paths = discover_lved_payloads(roots)
    explicit_key = key
    memory_keys = find_lved_key_candidates_in_dump(memory_dump) if memory_dump else []
    search_query_tuple = tuple(search_queries)
    search_mode_tuple = tuple(search_modes)
    payloads = parallel_map_ordered(
        _inspect_lved_payload_task,
        [
            (
                path,
                dict_id,
                dict_code,
                explicit_key,
                memory_keys,
                inspect_sqlite,
                inspect_retained,
                inspect_loose_resources,
                inspect_reader,
                search_query_tuple,
                search_mode_tuple,
                search_limit,
                render_link_limit,
                str(render_dir) if render_dir is not None else None,
            )
            for path in payload_paths
        ],
        jobs=jobs,
    )

    return {
        "payloads": payloads,
        "memory_dump": {
            "path": str(memory_dump) if memory_dump else None,
            "candidate_keys": len(memory_keys),
            "candidate_key_lengths": sorted({len(item) for item in memory_keys}),
        },
        "notes": [
            "Memory dump key candidates are counted and used for validation but never emitted.",
            "The LVED_SQLITE3 SQLCipher key derivation uses dictionary id/code metadata, not the product serial.",
            "LVED_SQLITE3 is the preferred reader-facing name for the decrypted SQLite3 data model; lved remains the historical command shorthand.",
        ],
    }
