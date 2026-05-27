"""LogoVista LVLMultiView package forensics."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loose_media import classify_loose_decoded_resource, write_decoded_resource
from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_file_to_path,
    decrypt_logofont_cipher_prefix,
)
from .ssed import is_metadata_noise_path, iter_files_with_suffix, parse_ssedinfo_with_layout, read_file_prefix
from .windows import file_magic_kind, quote_identifier


MULTIVIEW_PAYLOAD_RE = re.compile(r"^[a-z]lv(?:bat|dat)$", re.IGNORECASE)
VIEWER_SPECIAL_HREFS = frozenset({"50on", "about", "hanrei", "index"})
_ATTR_LINK_RE = re.compile(
    r"""(?<![-\w])(?P<attr>href|src)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
_MULTIVIEW_MARK_RE = re.compile(r"^lved_mark:(?P<kind>[^:]+):(?P<anchor>.+)$", re.IGNORECASE)
_MULTIVIEW_REF_RE = re.compile(r"^lved_ref:(?P<kind>[^:]+):(?P<target>.+)$", re.IGNORECASE)

PAYLOAD_NAME_HINTS = {
    "blvdat": "content_search_body",
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


@dataclass(frozen=True)
class MultiviewLinkTarget:
    kind: str
    original: str
    link_kind: str | None = None
    anchor: str | None = None
    target: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_multiview_payload_path(path: Path) -> bool:
    return (
        path.is_file()
        and not is_metadata_noise_path(path)
        and MULTIVIEW_PAYLOAD_RE.fullmatch(path.name) is not None
    )


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


def multiview_package_title(package_dir: Path) -> tuple[str | None, str]:
    """Return the SSED facade title used by an LVLMultiView package, if present."""

    idx_paths = sorted(iter_files_with_suffix(package_dir, ".idx"), key=lambda path: (path.name.casefold(), path.name))
    menu_title = _multiview_menu_title(package_dir / "menuData.xml")
    if not idx_paths:
        return menu_title, "menuData.xml" if menu_title else "missing_idx"
    try:
        title, _elements, _layout = parse_ssedinfo_with_layout(idx_paths[0])
    except Exception as exc:
        return menu_title, "menuData.xml" if menu_title else f"idx_parse_error:{type(exc).__name__}"
    if _multiview_title_is_placeholder(title) and menu_title:
        return menu_title, "menuData.xml"
    return title or None, f"idx:{idx_paths[0].name}"


def _multiview_title_is_placeholder(title: str) -> bool:
    stripped = title.strip()
    return stripped.count("○") >= 2 or stripped.count("□") >= 2 or "\ufffd" in stripped


def _multiview_menu_title(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    for item in root.iter("item"):
        label = (item.attrib.get("label") or "").strip()
        href = (item.attrib.get("href") or "").strip()
        if label and not href:
            return label
    for item in root.iter("item"):
        label = (item.attrib.get("label") or "").strip()
        if label:
            return label
    return None


def decrypt_or_copy_to_sqlite(path: Path, out: Path) -> tuple[str, str]:
    prefix = read_file_prefix(path, 4096)
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
    if {"t_contents", "t_search"} <= names:
        if {"f_id", "f_title", "f_body"} <= columns["t_contents"] and {
            "f_id",
            "f_anchor",
            "f_keyword",
            "f_titlemain",
            "f_all",
        } <= columns["t_search"]:
            return "sqlite_content_search_body"
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
    prefix = read_file_prefix(path, 4096)
    raw_kind = file_magic_kind(prefix)
    storage = "plain"
    kind = raw_kind
    decrypted_kind: str | None = None
    out_path: Path | None = None
    decoded = classify_loose_decoded_resource(path)
    if decoded is not None:
        storage = decoded.storage
        kind = decoded.content_kind
        decrypted_kind = decoded.content_kind if decoded.storage != "plain" else None
        if out_dir is not None:
            out_path = write_decoded_resource(decoded, out_dir)
    elif raw_kind == "unknown" and path.stat().st_size % 16 == 0:
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


def _sqlite_columns_for_tables(con: sqlite3.Connection) -> dict[str, list[str]]:
    return {
        str(row[0]): [info[1] for info in con.execute(f"pragma table_info({quote_identifier(str(row[0]))})")]
        for row in con.execute("select name from sqlite_master where type='table'")
    }


def _chunked(values: list[str], size: int = 900) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _menu_reference_sets(db_paths: dict[str, Path], hrefs: set[str]) -> dict[str, set[str]]:
    anchors: set[str] = set()
    codes: set[str] = set()
    index_codes: set[str] = set()
    content_ids: set[str] = set()
    numeric_ids = sorted({int(href) for href in hrefs if href.isdigit()})

    for db_path in db_paths.values():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = _sqlite_columns_for_tables(con)
            for table, columns in tables.items():
                lowered_columns = {column.lower() for column in columns}
                quoted_table = quote_identifier(table)
                if table.lower() == "t_contents" and "f_id" in lowered_columns:
                    if numeric_ids:
                        for chunk in _chunked([str(value) for value in numeric_ids]):
                            placeholders = ",".join("?" for _ in chunk)
                            content_ids.update(
                                f"{int(row[0]):06d}"
                                for row in con.execute(
                                    f"select f_ID from {quoted_table} where f_ID in ({placeholders})",
                                    tuple(int(value) for value in chunk),
                                )
                            )
                    else:
                        content_ids.update(
                            f"{int(row[0]):06d}"
                            for row in con.execute(f"select f_ID from {quoted_table} where f_ID is not null")
                        )

                if "f_hore_code" in lowered_columns:
                    if table.lower().startswith("t_") and table.lower() != "t_index":
                        codes.add(table[2:])
                    if table.lower() == "t_index":
                        values = [
                            str(row[0])
                            for row in con.execute(
                                f"select distinct f_hore_code from {quoted_table} "
                                "where f_hore_code is not null and f_hore_code != ''"
                            )
                        ]
                        codes.update(values)
                        index_codes.update(values)
                    elif table.lower() == "t_hore":
                        codes.update(
                            str(row[0])
                            for row in con.execute(
                                f"select distinct f_hore_code from {quoted_table} "
                                "where f_hore_code is not null and f_hore_code != ''"
                            )
                        )

                if "f_anchor" in lowered_columns:
                    candidate_hrefs = sorted(
                        href
                        for href in hrefs
                        if href
                        and (
                            not table.lower().startswith("t_")
                            or href == table[2:]
                            or href.startswith(f"{table[2:]}_")
                        )
                    )
                    if not candidate_hrefs:
                        continue
                    for chunk in _chunked(candidate_hrefs):
                        placeholders = ",".join("?" for _ in chunk)
                        anchors.update(
                            str(row[0])
                            for row in con.execute(
                                f"select f_anchor from {quoted_table} where f_anchor in ({placeholders})",
                                tuple(chunk),
                            )
                            if row[0] is not None and row[0] != ""
                        )
        finally:
            con.close()

    return {
        "anchors": anchors,
        "codes": codes,
        "index_codes": index_codes,
        "content_ids": content_ids,
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

    reference_sets = _menu_reference_sets(db_paths, {row["href"] for row in href_rows})
    anchors = reference_sets["anchors"]
    codes = reference_sets["codes"]
    index_codes = reference_sets["index_codes"]
    content_ids = reference_sets["content_ids"]

    def classify_href(href: str) -> str:
        if href in content_ids:
            return "content_id"
        if href in anchors:
            return "anchor_exact"
        if href.startswith("index:") and href[6:] in index_codes:
            return "index_row"
        if href in codes:
            return "hore_code"
        if href.split("_", 1)[0] in codes:
            return "prefix_hore_code"
        if href in VIEWER_SPECIAL_HREFS:
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
    idx_paths = sorted(iter_files_with_suffix(package_dir, ".idx"), key=lambda path: (path.name.casefold(), path.name))
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
            [
                classify_resource(path, resource_out)
                for path in sorted(resource_dir.iterdir())
                if path.is_file() and not is_metadata_noise_path(path)
            ]
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
            "html_dirs": _html_directory_summaries(package_dir),
            "viewer_files": _viewer_file_listing(package_dir),
        }
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def write_multiview_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _multiview_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 0; padding: 20px; font-family: "Yu Gothic", Meiryo, sans-serif; line-height: 1.6; }}
nav a, .lv-hit {{ display: block; padding: 0.25rem 0; }}
.lv-meta {{ color: #666; font-size: 13px; border-bottom: 1px solid #ddd; margin-bottom: 1rem; padding-bottom: 0.4rem; }}
img {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _sqlite_table_names(con: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in con.execute("select name from sqlite_master where type='table'")}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "cp932"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                pass
        return value.decode("utf-8", errors="replace")
    return str(value)


def _html_title(value: str, fallback: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "").strip()
    return text or fallback


def parse_multiview_link(value: str) -> MultiviewLinkTarget | None:
    raw = html.unescape(value).strip()
    mark = _MULTIVIEW_MARK_RE.match(raw)
    if mark:
        return MultiviewLinkTarget(
            kind="mark",
            original=raw,
            link_kind=mark.group("kind"),
            anchor=mark.group("anchor"),
        )
    ref = _MULTIVIEW_REF_RE.match(raw)
    if ref:
        return MultiviewLinkTarget(
            kind="ref",
            original=raw,
            link_kind=ref.group("kind"),
            target=ref.group("target"),
        )
    return None


def _copy_multiview_asset(value: str, *, package_dir: Path, out_dir: Path) -> str | None:
    raw = html.unescape(value).strip().replace("\\", "/")
    if not raw or raw.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.IGNORECASE):
        return None
    package_root = package_dir.resolve()
    source = (package_dir / raw).resolve()
    try:
        source.relative_to(package_root)
    except ValueError:
        return None
    if not source.is_file():
        name = Path(raw).name.casefold()
        for base in (package_dir / "Templates", package_dir / "Help" / "image", package_dir / "Help"):
            if not base.is_dir():
                continue
            for candidate in sorted(base.iterdir()):
                if candidate.is_file() and not is_metadata_noise_path(candidate) and candidate.name.casefold() == name:
                    source = candidate.resolve()
                    break
            if source.is_file():
                break
    if not source.is_file():
        return None
    target = out_dir / "assets" / _safe_showcase_name(str(Path(raw)))
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return f"assets/{target.name}"


def _rewrite_multiview_fragment(
    fragment: str,
    known_pages: dict[str, str],
    *,
    package_dir: Path | None = None,
    out_dir: Path | None = None,
) -> tuple[str, dict[str, int]]:
    stats: Counter[str] = Counter()

    def repl(match: re.Match[str]) -> str:
        attr = match.group("attr")
        quote = match.group("quote")
        value = html.unescape(match.group("value"))
        if attr.casefold() == "href":
            target = parse_multiview_link(value)
            if target is not None and target.kind == "mark" and target.anchor:
                stats["mark_links_rewritten"] += 1
                return f'{attr}={quote}#{html.escape(target.anchor, quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if target is not None and target.kind == "ref" and target.target:
                key = target.target.removeprefix("index:")
                if target.target in known_pages:
                    stats["ref_links_resolved"] += 1
                    return f'{attr}={quote}{html.escape(known_pages[target.target], quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
                if key in known_pages:
                    stats["ref_links_resolved"] += 1
                    return f'{attr}={quote}{html.escape(known_pages[key], quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
                stats[f"{target.link_kind or 'unknown'}_ref_links_deferred"] += 1
                return (
                    f'{attr}={quote}#{quote} '
                    f'data-lv-original-href={quote}{html.escape(value, quote=True)}{quote} '
                    f'data-lv-ref-kind={quote}{html.escape(target.link_kind or "unknown", quote=True)}{quote}'
                )
            key = value.removeprefix("index:")
            if value in known_pages:
                stats["links_resolved"] += 1
                return f'{attr}={quote}{html.escape(known_pages[value], quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if key in known_pages:
                stats["links_resolved"] += 1
                return f'{attr}={quote}{html.escape(known_pages[key], quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if value.startswith("http://") or value.startswith("https://") or value.startswith("#"):
                return match.group(0)
            stats["links_deferred"] += 1
            return f'{attr}={quote}#{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
        if attr.casefold() == "src" and package_dir is not None and out_dir is not None:
            copied = _copy_multiview_asset(value, package_dir=package_dir, out_dir=out_dir)
            if copied is not None:
                stats["assets_resolved"] += 1
                return f'{attr}={quote}{html.escape(copied, quote=True)}{quote}'
            if not value.startswith(("http://", "https://", "data:")):
                stats["assets_unresolved"] += 1
        return match.group(0)

    return _ATTR_LINK_RE.sub(repl, fragment), dict(sorted(stats.items()))


def _write_multiview_content_page(
    out_dir: Path,
    *,
    content_id: str,
    title: str,
    body: str,
    source: str,
    known_pages: dict[str, str],
    package_dir: Path | None = None,
) -> dict[str, Any]:
    filename = f"entry_{_safe_showcase_name(content_id)}.html"
    rewritten, stats = _rewrite_multiview_fragment(body, known_pages, package_dir=package_dir, out_dir=out_dir)
    (out_dir / filename).write_text(
        _multiview_page(
            _html_title(title, content_id),
            f'<main><div class="lv-meta">{html.escape(source)} id={html.escape(content_id)}</div>'
            f"<h1>{title}</h1>{rewritten}</main>",
        ),
        encoding="utf-8",
    )
    return {"id": content_id, "title": _html_title(title, content_id), "path": filename, "source": source, "rewrite": stats}


def _safe_showcase_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("._") or "item"


def _collect_multiview_menu_hrefs(package_dir: Path, limit: int) -> list[dict[str, str]]:
    menu_path = package_dir / "menuData.xml"
    if not menu_path.is_file():
        return []
    root = ET.parse(menu_path).getroot()
    rows: list[dict[str, str]] = []
    for element in root.iter("item"):
        href = element.attrib.get("href", "")
        if not href:
            continue
        rows.append({"href": href, "label": element.attrib.get("label", "")})
        if len(rows) >= limit:
            break
    return rows


def _render_multiview_content_search(
    db_path: Path,
    out_dir: Path,
    known_pages: dict[str, str],
    limit: int,
    *,
    package_dir: Path,
    preferred_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = _sqlite_table_names(con)
        if "t_contents" not in tables:
            return []
        rows: list[sqlite3.Row] = []
        seen_ids: set[str] = set()
        numeric_ids = [int(value) for value in preferred_ids or [] if value.isdigit()]
        if numeric_ids:
            placeholders = ",".join("?" for _ in numeric_ids)
            rows.extend(
                con.execute(
                    f"select * from t_contents where f_ID in ({placeholders}) order by f_ID",
                    tuple(numeric_ids),
                ).fetchall()
            )
            seen_ids.update(str(row["f_ID"]) for row in rows)
        if len(rows) < limit:
            rows.extend(
                row
                for row in con.execute("select * from t_contents order by f_ID limit ?", (limit * 2,))
                if str(row["f_ID"]) not in seen_ids
            )
        rows = rows[:limit]
        rendered: list[dict[str, Any]] = []
        for row in rows:
            content_id = f"{int(row['f_ID']):06d}" if str(row["f_ID"]).isdigit() else str(row["f_ID"])
            known_pages[content_id] = f"entry_{_safe_showcase_name(content_id)}.html"
        for row in rows:
            content_id = f"{int(row['f_ID']):06d}" if str(row["f_ID"]).isdigit() else str(row["f_ID"])
            rendered.append(
                _write_multiview_content_page(
                    out_dir,
                    content_id=content_id,
                    title=_safe_text(row["f_Title"]),
                    body=_safe_text(row["f_Body"]),
                    source=f"{db_path.name}:t_contents",
                    known_pages=known_pages,
                    package_dir=package_dir,
                )
            )
        return rendered
    finally:
        con.close()


def _render_multiview_law_page(
    db_path: Path,
    out_dir: Path,
    known_pages: dict[str, str],
    href: str,
    *,
    package_dir: Path,
) -> dict[str, Any] | None:
    if href in VIEWER_SPECIAL_HREFS:
        return None
    table_hint = href.split("_", 1)[0]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = _sqlite_table_names(con)
        table = f"t_{table_hint}" if f"t_{table_hint}" in tables else None
        if table is None:
            for candidate in tables:
                columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(candidate)})")]
                if "f_anchor" in columns:
                    hit = con.execute(
                        f"select 1 from {quote_identifier(candidate)} where f_anchor = ? limit 1",
                        (href,),
                    ).fetchone()
                    if hit:
                        table = candidate
                        break
        if table is None:
            return None
        columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(table)})")]
        if "f_text" not in columns:
            return None
        if "f_anchor" in columns:
            rows = con.execute(
                f"select * from {quote_identifier(table)} where f_anchor = ? or f_hore_code = ? order by f_rec_id limit 80",
                (href, table_hint),
            ).fetchall()
        else:
            rows = con.execute(f"select * from {quote_identifier(table)} order by f_rec_id limit 80").fetchall()
        if not rows:
            rows = con.execute(f"select * from {quote_identifier(table)} order by f_rec_id limit 80").fetchall()
        if not rows:
            return None
        key = href
        known_pages[key] = f"entry_{_safe_showcase_name(key)}.html"
        body = "\n".join(_safe_text(row["f_text"]) for row in rows)
        title = href
        return _write_multiview_content_page(
            out_dir,
            content_id=key,
            title=html.escape(title),
            body=body,
            source=f"{db_path.name}:{table}",
            known_pages=known_pages,
            package_dir=package_dir,
        )
    finally:
        con.close()


def _render_multiview_index_page(
    db_path: Path,
    out_dir: Path,
    known_pages: dict[str, str],
    code: str,
    *,
    package_dir: Path,
) -> dict[str, Any] | None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = _sqlite_table_names(con)
        if "t_index" not in tables:
            return None
        rows = con.execute("select * from t_index where f_hore_code = ? limit 50", (code,)).fetchall()
        if not rows:
            return None
        known_pages[code] = f"entry_{_safe_showcase_name(code)}.html"
        body = "\n".join(_safe_text(row["f_text"]) for row in rows)
        title = " ".join(
            part for part in (_safe_text(rows[0]["f_title_no"]), _safe_text(rows[0]["f_title_sub"])) if part
        )
        return _write_multiview_content_page(
            out_dir,
            content_id=code,
            title=html.escape(title or code),
            body=body,
            source=f"{db_path.name}:t_index",
            known_pages=known_pages,
            package_dir=package_dir,
        )
    finally:
        con.close()


def _copy_multiview_static_dir(source: Path, target: Path) -> str | None:
    if not source.is_dir():
        return None
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        if not path.is_file() or is_metadata_noise_path(path):
            continue
        relative = path.relative_to(source)
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            shutil.copy2(path, out)
    index = target / "index.html"
    if index.is_file():
        return str(index.relative_to(target.parent.parent))
    html_files = sorted(target.rglob("*.html"))
    if html_files:
        return str(html_files[0].relative_to(target.parent.parent))
    return None


def _render_multiview_special_page(
    package_dir: Path,
    out_dir: Path,
    known_pages: dict[str, str],
    href: str,
    label: str = "",
) -> dict[str, Any]:
    filename = f"surface_{_safe_showcase_name(href)}.html"
    known_pages[href] = filename
    static_link: str | None = None
    if href == "hanrei":
        static_link = _copy_multiview_static_dir(package_dir / "HANREI", out_dir / "static" / "HANREI")
    if static_link:
        body = (
            f'<main><h1>{html.escape(label or href)}</h1>'
            f'<p><a href="{html.escape(static_link, quote=True)}">Open bundled static HTML</a></p></main>'
        )
        status = "static_html"
    else:
        body = (
            f'<main><h1>{html.escape(label or href)}</h1>'
            '<div class="lv-meta">Viewer surface recognized; this bounded showcase records it as a '
            "reader-interface target rather than pretending it is a decoded body row.</div></main>"
        )
        status = "deferred_viewer_surface"
    (out_dir / filename).write_text(_multiview_page(label or href, body), encoding="utf-8")
    return {"id": href, "title": label or href, "path": filename, "source": "menuData.xml", "surface_status": status}


def validate_multiview_showcase(out_dir: Path) -> dict[str, Any]:
    missing_href: list[dict[str, str]] = []
    missing_src: list[dict[str, str]] = []
    protocol_hrefs: Counter[str] = Counter()
    tag_balance_issues: list[dict[str, Any]] = []
    html_files = sorted(out_dir.rglob("*.html"))
    for path in html_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative_parent = path.parent
        for match in _ATTR_LINK_RE.finditer(text):
            attr = match.group("attr")
            raw = html.unescape(match.group("value")).strip()
            lower = raw.casefold()
            if attr.casefold() == "href":
                if lower.startswith(("http:", "https:", "mailto:", "javascript:")) or raw.startswith("#"):
                    continue
                if lower.startswith(("lved_", "lved:")):
                    protocol_hrefs[raw.split(":", 1)[0].split("&", 1)[0]] += 1
                    continue
                target = relative_parent / raw.split("#", 1)[0].split("?", 1)[0]
                if raw and not target.is_file():
                    missing_href.append({"page": str(path.relative_to(out_dir)), "href": raw})
            elif attr.casefold() == "src":
                if lower.startswith(("http:", "https:", "data:")):
                    continue
                target = relative_parent / raw.split("#", 1)[0].split("?", 1)[0]
                if raw and not target.is_file():
                    missing_src.append({"page": str(path.relative_to(out_dir)), "src": raw})
        # Missing table/cell closes are the browser-hostile case: they can create
        # pathological nested table DOMs. A lone missing <tr> close is common in
        # legacy fragments and browsers repair it without deep nesting.
        for tag in ("table", "td"):
            opens = len(re.findall(fr"<{tag}(?:\s|>)", text, re.IGNORECASE))
            closes = len(re.findall(fr"</{tag}\s*>", text, re.IGNORECASE))
            if opens != closes:
                tag_balance_issues.append(
                    {"page": str(path.relative_to(out_dir)), "tag": tag, "opens": opens, "closes": closes}
                )
    return {
        "html_file_count": len(html_files),
        "missing_href_count": len(missing_href),
        "missing_src_count": len(missing_src),
        "protocol_href_counts": dict(sorted(protocol_hrefs.items())),
        "tag_balance_issue_count": len(tag_balance_issues),
        "missing_href_samples": missing_href[:40],
        "missing_src_samples": missing_src[:40],
        "tag_balance_issue_samples": tag_balance_issues[:40],
    }


def write_multiview_showcase(package_dir: Path, out_dir: Path, *, limit: int = 40) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lv-multiview-showcase-") as temp:
        temp_root = Path(temp)
        db_paths: dict[str, Path] = {}
        payloads: list[dict[str, Any]] = []
        for path in sorted(child for child in package_dir.iterdir() if is_multiview_payload_path(child)):
            payload, db_path = classify_payload(path, temp_root)
            payloads.append(payload)
            if db_path is not None:
                db_paths[path.name] = db_path

        known_pages: dict[str, str] = {}
        rendered: list[dict[str, Any]] = []
        menu_hrefs = _collect_multiview_menu_hrefs(package_dir, limit)
        content_db = next((db_paths[row["name"]] for row in payloads if row["role"] == "sqlite_content_search_body" and row["name"] in db_paths), None)
        if content_db is not None:
            rendered.extend(
                _render_multiview_content_search(
                    content_db,
                    out_dir,
                    known_pages,
                    limit,
                    package_dir=package_dir,
                    preferred_ids=[row["href"] for row in menu_hrefs],
                )
            )

        law_db = next((db_paths[row["name"]] for row in payloads if row["role"] == "sqlite_law_body_table_store" and row["name"] in db_paths), None)
        index_db = next((db_paths[row["name"]] for row in payloads if row["role"] == "sqlite_html_index" and row["name"] in db_paths), None)
        unresolved: list[dict[str, str]] = []
        for item in menu_hrefs:
            href = item["href"]
            if href in known_pages:
                continue
            page = None
            if href in VIEWER_SPECIAL_HREFS:
                page = _render_multiview_special_page(package_dir, out_dir, known_pages, href, item.get("label", ""))
            elif href.startswith("index:") and index_db is not None:
                page = _render_multiview_index_page(index_db, out_dir, known_pages, href[6:], package_dir=package_dir)
            elif law_db is not None:
                page = _render_multiview_law_page(law_db, out_dir, known_pages, href, package_dir=package_dir)
            if page is None:
                unresolved.append(item)
                continue
            rendered.append(page)

        index_rows = []
        for page in rendered:
            index_rows.append(f'<a class="lv-hit" href="{html.escape(page["path"], quote=True)}">{html.escape(page["title"])}</a>')
        if unresolved:
            index_rows.append("<h2>Deferred menu targets</h2>")
            for row in unresolved[:100]:
                index_rows.append(
                    f'<div class="lv-hit">{html.escape(row["href"])} {html.escape(row.get("label", ""))}</div>'
                )
        (out_dir / "index.html").write_text(
            _multiview_page(
                f"{package_dir.name} showcase",
                f"<main><h1>{html.escape(package_dir.name)} showcase</h1>{''.join(index_rows)}</main>",
            ),
            encoding="utf-8",
        )
        manifest = {
            "schema": "logovista-multiview-showcase-v1",
            "package": str(package_dir),
            "index": str(out_dir / "index.html"),
            "payloads": payloads,
            "rendered": rendered,
            "rendered_count": len(rendered),
            "deferred_menu_targets": unresolved,
        }
        manifest["validation"] = validate_multiview_showcase(out_dir)
        (out_dir / "showcase.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


def _file_listing(path: Path, *, with_kind: bool) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(path.iterdir()):
        if not child.is_file() or is_metadata_noise_path(child):
            continue
        row: dict[str, Any] = {"name": child.name, "path": str(child), "size": child.stat().st_size}
        if with_kind:
            row["kind"] = file_magic_kind(read_file_prefix(child, 64))
        rows.append(row)
    return rows


def _html_directory_summaries(package_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for child in sorted(package_dir.iterdir()):
        if not child.is_dir() or child.name in {"Templates", "Help", "Resources"}:
            continue
        html_files = sorted(
            path
            for path in child.rglob("*")
            if path.is_file() and not is_metadata_noise_path(path) and path.suffix.lower() in {".html", ".htm"}
        )
        if not html_files and not (child / "index.html").is_file():
            continue
        rows.append(
            {
                "name": child.name,
                "path": str(child),
                "html_files": len(html_files),
                "total_bytes": sum(path.stat().st_size for path in html_files),
                "root_files": [
                    grandchild.name
                    for grandchild in sorted(child.iterdir())
                    if grandchild.is_file() and not is_metadata_noise_path(grandchild)
                ][:40],
                "sample_html_files": [str(path.relative_to(child)) for path in html_files[:40]],
            }
        )
    return rows


def _viewer_file_listing(package_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or is_metadata_noise_path(path) or path.suffix.lower() not in {".exe", ".dll"}:
            continue
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "relative_path": str(path.relative_to(package_dir)),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "kind": file_magic_kind(read_file_prefix(path, 64)),
            }
        )
    return rows
