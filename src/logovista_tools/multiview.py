"""LogoVista LVLMultiView law-package forensics."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_file_to_path,
    decrypt_logofont_cipher_prefix,
)
from .ssed import parse_ssedinfo_with_layout
from .windows import file_magic_kind, quote_identifier


MULTIVIEW_PAYLOAD_RE = re.compile(r"^[a-z]lv(?:bat|dat)$", re.IGNORECASE)

PAYLOAD_NAME_HINTS = {
    "blvbat": "law_body",
    "hlvbat": "case_digest_body",
    "nlvbat": "law_metadata_yroppo",
    "nlvdat": "law_metadata_moroku",
    "ilvbat": "html_index",
    "ilvdat": "html_index",
    "jlvbat": "subject_index",
    "jlvdat": "subject_index",
}

VIEWER_DECRYPTED_NAME_HINTS = {
    "blvbat": "hore_body.db",
    "hlvbat": "hanrei_youshi.db",
    "nlvbat": "yroppo.db",
    "nlvdat": "mo6.db",
    "ilvbat": "index.db",
    "ilvdat": "index.db",
    "jlvbat": "jiko_sakuin.db",
    "jlvdat": "jiko_sakuin.db",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_multiview_payload_path(path: Path) -> bool:
    return path.is_file() and MULTIVIEW_PAYLOAD_RE.fullmatch(path.name) is not None


def discover_multiview_packages(roots: list[Path]) -> list[Path]:
    if not roots:
        roots = [Path(".")]
    packages: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidates = [root] if root.is_dir() else [root.parent]
        if root.is_dir():
            candidates.extend(path.parent for path in root.rglob("menuData.xml"))
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            if not (candidate / "menuData.xml").is_file():
                continue
            if not any(is_multiview_payload_path(child) for child in candidate.iterdir() if child.is_file()):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            packages.append(resolved)
    return sorted(packages)


def decrypt_or_copy_to_sqlite(path: Path, out: Path) -> tuple[str, str]:
    prefix = path.read_bytes()[:4096]
    raw_kind = file_magic_kind(prefix)
    if raw_kind == "sqlite":
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        return "plain", "sqlite"
    if path.stat().st_size % 16:
        return "unknown", raw_kind
    try:
        decrypted_prefix = decrypt_logofont_cipher_prefix(prefix, size=min(len(prefix), 4096))
    except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError):
        return "unknown", raw_kind
    decrypted_kind = file_magic_kind(decrypted_prefix)
    if decrypted_kind != "sqlite":
        return "unknown", raw_kind
    out.parent.mkdir(parents=True, exist_ok=True)
    decrypt_logofont_cipher_file_to_path(path, out)
    return "logofont_cipher", "sqlite"


def sqlite_tables_compact(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows: list[dict[str, Any]] = []
        for (name,) in con.execute("select name from sqlite_master where type='table' order by name"):
            columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(name)})")]
            try:
                count = int(con.execute(f"select count(*) from {quote_identifier(name)}").fetchone()[0])
            except sqlite3.Error:
                count = None
            rows.append({"name": name, "rows": count, "columns": columns})
        return rows
    finally:
        con.close()


def sqlite_indexes_compact(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [
            {"name": name, "sql": sql}
            for name, sql in con.execute(
                "select name, sql from sqlite_master where type='index' order by name"
            )
        ]
    finally:
        con.close()


def _column_sets(tables: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {str(table["name"]).lower(): {str(column).lower() for column in table["columns"]} for table in tables}


def multiview_sqlite_role(tables: list[dict[str, Any]]) -> str:
    columns = _column_sets(tables)
    names = set(columns)
    if not names:
        return "sqlite_empty"
    if "t_index" in names and {"f_hore_code", "f_text"} <= columns["t_index"]:
        return "sqlite_html_index"
    if "t_hore" in names:
        return "sqlite_law_metadata"
    if "t_page" in names and {"f_name", "f_name_key", "f_name_kana", "f_anchor"} <= columns["t_page"]:
        return "sqlite_subject_index"
    if "t_base" in names and "t_page" in names and {"f_text", "f_text_plane"} <= columns["t_page"]:
        return "sqlite_case_digest_body"
    if names == {"t_page"} and {"f_hore_code", "f_text", "f_text_plane"} <= columns["t_page"]:
        return "sqlite_case_digest_body"
    body_like = [
        cols
        for cols in columns.values()
        if {"f_hore_code", "f_rec_id", "f_text", "f_text_plane"} <= cols
    ]
    if len(body_like) >= max(1, len(columns) // 2):
        return "sqlite_law_body_table_store"
    return "sqlite_multiview_unclassified"


def table_family_counts(tables: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for table in tables:
        name = str(table["name"])
        match = re.match(r"t_([A-Za-z]+)", name)
        counts[match.group(1) if match else name] += 1
    return dict(counts.most_common(30))


def column_set_counts(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(tuple(table["columns"]) for table in tables)
    return [{"count": count, "columns": list(columns)} for columns, count in counts.most_common(10)]


def classify_payload(path: Path, decrypted_root: Path) -> tuple[dict[str, Any], Path | None]:
    db_out = decrypted_root / f"{path.name}.sqlite"
    storage, kind = decrypt_or_copy_to_sqlite(path, db_out)
    tables: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    role = kind
    db_path: Path | None = None
    if kind == "sqlite":
        db_path = db_out
        tables = sqlite_tables_compact(db_out)
        indexes = sqlite_indexes_compact(db_out)
        role = multiview_sqlite_role(tables)
    return (
        {
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "storage": storage,
            "content_kind": kind,
            "name_hint": PAYLOAD_NAME_HINTS.get(path.name.lower()),
            "viewer_decrypted_name_hint": VIEWER_DECRYPTED_NAME_HINTS.get(path.name.lower()),
            "role": role,
            "table_count": len(tables),
            "index_count": len(indexes),
            "row_count": sum(int(table["rows"] or 0) for table in tables),
            "table_family_counts": table_family_counts(tables),
            "column_set_counts": column_set_counts(tables),
            "table_names_sample": [str(table["name"]) for table in tables[:40]],
            "indexes": indexes[:20],
        },
        db_path,
    )


def classify_resource(path: Path, out_dir: Path | None = None) -> dict[str, Any]:
    prefix = path.read_bytes()[:4096]
    raw_kind = file_magic_kind(prefix)
    storage = "plain"
    kind = raw_kind
    decrypted_kind: str | None = None
    out_path: Path | None = None
    if raw_kind == "unknown" and path.stat().st_size % 16 == 0:
        try:
            decrypted_prefix = decrypt_logofont_cipher_prefix(prefix, size=min(len(prefix), 4096))
            decrypted_kind = file_magic_kind(decrypted_prefix)
        except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError) as exc:
            decrypted_kind = f"decrypt_error:{type(exc).__name__}"
        if decrypted_kind not in (None, "unknown") and not str(decrypted_kind).startswith("decrypt_error"):
            storage = "logofont_cipher"
            kind = decrypted_kind
            if out_dir is not None:
                suffix = ".pdf" if decrypted_kind == "pdf" else ".bin"
                out_path = out_dir / f"{path.name}{suffix}"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                decrypt_logofont_cipher_file_to_path(path, out_path)
    return {
        "name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "storage": storage,
        "content_kind": kind,
        "decrypted_prefix_kind": decrypted_kind,
        "output": str(out_path) if out_path else None,
    }


def inspect_menu(path: Path, db_paths: dict[str, Path]) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    tags = Counter(element.tag for element in root.iter())
    attrs = Counter(key for element in root.iter() for key in element.attrib)
    href_rows = [
        {
            "href": element.attrib.get("href", ""),
            "label": element.attrib.get("label", ""),
            "genre": element.attrib.get("genre", ""),
            "index": element.attrib.get("index", ""),
        }
        for element in root.iter("item")
        if element.attrib.get("href")
    ]

    anchors: set[str] = set()
    codes: set[str] = set()
    index_codes: set[str] = set()
    for db_path in db_paths.values():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for (table,) in con.execute("select name from sqlite_master where type='table'"):
                columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(table)})")]
                if "f_anchor" in columns:
                    anchors.update(
                        row[0]
                        for row in con.execute(
                            f"select f_anchor from {quote_identifier(table)} "
                            "where f_anchor is not null and f_anchor != ''"
                        )
                    )
                if "f_hore_code" in columns:
                    values = [
                        row[0]
                        for row in con.execute(
                            f"select distinct f_hore_code from {quote_identifier(table)} "
                            "where f_hore_code is not null and f_hore_code != ''"
                        )
                    ]
                    codes.update(values)
                    if table == "t_index":
                        index_codes.update(values)
        finally:
            con.close()

    def classify_href(href: str) -> str:
        if href in anchors:
            return "anchor_exact"
        if href.startswith("index:") and href[6:] in index_codes:
            return "index_row"
        if href in codes:
            return "hore_code"
        if href.split("_", 1)[0] in codes:
            return "prefix_hore_code"
        if href in {"50on", "about", "hanrei", "index"}:
            return "viewer_special"
        return "unresolved"

    resolution = Counter(classify_href(row["href"]) for row in {row["href"]: row for row in href_rows}.values())
    unresolved = [row for row in href_rows if classify_href(row["href"]) == "unresolved"]
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "root_tag": root.tag,
        "tag_counts": dict(tags.most_common()),
        "attribute_counts": dict(attrs.most_common()),
        "href_count": len(href_rows),
        "unique_href_count": len({row["href"] for row in href_rows}),
        "resolution_counts": dict(resolution),
        "unresolved_count": len(unresolved),
        "unresolved_sample": unresolved[:25],
    }


def inspect_multiview_package(
    package_dir: Path,
    *,
    decrypted_dir: Path | None = None,
    write_resources: bool = False,
) -> dict[str, Any]:
    idx_paths = sorted(package_dir.glob("*.IDX")) + sorted(package_dir.glob("*.idx"))
    idx_report: dict[str, Any] | None = None
    if idx_paths:
        title, elements, layout = parse_ssedinfo_with_layout(idx_paths[0])
        idx_report = {
            "path": str(idx_paths[0]),
            "title": title,
            "layout": {
                "component_count_offset": layout.component_count_offset,
                "record_start": layout.record_start,
                "record_size": layout.record_size,
                "component_count": layout.component_count,
                "trailing_bytes": layout.trailing_bytes,
            },
            "components": [
                {
                    "index": element.index,
                    "filename": element.filename,
                    "type": f"{element.type:02x}",
                    "multi": f"{element.multi:02x}",
                    "start": element.start,
                    "end": element.end,
                    "block_count": element.block_count,
                    "data": element.data.hex(),
                    "physical_file_present": (package_dir / element.filename).is_file(),
                }
                for element in elements
            ],
        }

    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if decrypted_dir is None:
        temp_context = tempfile.TemporaryDirectory(prefix="lv-multiview-")
        decrypted_root = Path(temp_context.name)
    else:
        decrypted_root = decrypted_dir
        decrypted_root.mkdir(parents=True, exist_ok=True)

    try:
        payloads: list[dict[str, Any]] = []
        db_paths: dict[str, Path] = {}
        for path in sorted(child for child in package_dir.iterdir() if is_multiview_payload_path(child)):
            payload, db_path = classify_payload(path, decrypted_root)
            payloads.append(payload)
            if db_path is not None:
                db_paths[path.name] = db_path

        resource_out = decrypted_root / "Resources" if write_resources and decrypted_dir is not None else None
        resource_dir = package_dir / "Resources"
        resources = (
            [classify_resource(path, resource_out) for path in sorted(resource_dir.iterdir()) if path.is_file()]
            if resource_dir.is_dir()
            else []
        )

        menu_path = package_dir / "menuData.xml"
        menu = inspect_menu(menu_path, db_paths) if menu_path.is_file() else None

        return {
            "schema": "logovista-multiview-package-v1",
            "dict_id": package_dir.name.removeprefix("_DCT_"),
            "path": str(package_dir),
            "idx": idx_report,
            "payloads": payloads,
            "menu": menu,
            "resources": resources,
            "templates": _file_listing(package_dir / "Templates", with_kind=True),
            "help_files": _file_listing(package_dir / "Help", with_kind=False),
        }
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def write_multiview_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_listing(path: Path, *, with_kind: bool) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(path.iterdir()):
        if not child.is_file():
            continue
        row: dict[str, Any] = {"name": child.name, "path": str(child), "size": child.stat().st_size}
        if with_kind:
            row["kind"] = file_magic_kind(child.read_bytes()[:64])
        rows.append(row)
    return rows
