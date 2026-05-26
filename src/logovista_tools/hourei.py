"""LogoVista電子法令 Professional package inspection.

Hourei is a separate law-browser package family. It is not SSED, not
LVED_SQLITE3, and not the later LVLMultiView law package layout, even though
it shares the same law-domain concepts and some ``lved_*`` link prefixes.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .ssed import is_metadata_noise_path, read_file_prefix
from .windows import file_magic_kind, quote_identifier


HOUREI_SCHEMA = "logovista-hourei-package-v1"
HOUREI_AUDIT_SCHEMA = "logovista-hourei-audit-v1"

ERA_DIR_PREFIX = {
    "1": "M",
    "2": "T",
    "3": "S",
    "4": "H",
}

_ATTR_LINK_RE = re.compile(
    r"""(?<![-\w])(?P<attr>href|src)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
_LVED_REF_HORE_RE = re.compile(r"^lved_ref&(?P<mode>\d+):(?P<body>.+)$", re.IGNORECASE)
_LVED_REF_LOCAL_RE = re.compile(r"^lved_ref&&(?P<anchor>.+)$", re.IGNORECASE)
_LVED_MARK_RE = re.compile(r"^lved_mark&&(?P<anchor>.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class HoureiLinkTarget:
    kind: str
    original: str
    mode: str | None = None
    hore_id: str | None = None
    related_hore_id: str | None = None
    anchor: str | None = None
    query: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_hourei_packages(roots: Iterable[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in roots or [Path(".")]:
        root = root.resolve()
        if root.is_dir() and is_hourei_package(root):
            candidates.append(root)
            continue
        if root.is_dir():
            for db in root.rglob("hore_base.db"):
                package = db.parent.parent
                if is_hourei_package(package):
                    candidates.append(package.resolve())
    seen: set[Path] = set()
    result: list[Path] = []
    for path in sorted(candidates):
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def is_hourei_package(root: Path) -> bool:
    return (
        root.is_dir()
        and (root / "_DataBase" / "hore_base.db").is_file()
        and (root / "_DataBase" / "hore_search_a.db").is_file()
        and (root / "_DataBase" / "horejo_base.db").is_file()
        and (root / "_Programs" / "LVMultiView.exe").is_file()
    )


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"pragma table_info({quote_identifier(table)})")]


def _safe_count(con: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(con.execute(f"select count(*) from {quote_identifier(table)}").fetchone()[0])
    except sqlite3.Error:
        return None


def _sqlite_tables(path: Path) -> list[dict[str, Any]]:
    con = _sqlite_connect(path)
    try:
        tables: list[dict[str, Any]] = []
        for row in con.execute("select name from sqlite_master where type='table' order by name"):
            name = str(row["name"])
            tables.append({"name": name, "rows": _safe_count(con, name), "columns": _sqlite_columns(con, name)})
        return tables
    finally:
        con.close()


def _decode_hourei_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace")


def parse_hourei_link(value: str) -> HoureiLinkTarget | None:
    raw = html.unescape(value).strip()
    mark = _LVED_MARK_RE.match(raw)
    if mark:
        return HoureiLinkTarget(kind="local_mark", original=raw, anchor=mark.group("anchor"))
    local = _LVED_REF_LOCAL_RE.match(raw)
    if local:
        return HoureiLinkTarget(kind="local_ref", original=raw, anchor=local.group("anchor"))
    if raw.casefold().startswith("lved_ref:"):
        return HoureiLinkTarget(kind="kana_search", original=raw, query=raw.split(":", 1)[1])
    if raw.casefold() == "lved_unsafe":
        return HoureiLinkTarget(kind="unsafe", original=raw)
    target = _LVED_REF_HORE_RE.match(raw)
    if not target:
        return None
    mode = target.group("mode")
    body = target.group("body")
    if mode == "1":
        hore_id, _, anchor = body.partition("&")
        if hore_id.isdigit():
            return HoureiLinkTarget(kind="law_ref", original=raw, mode=mode, hore_id=hore_id, anchor=anchor or None)
    if mode == "4":
        primary, sep, related = body.partition(":")
        if sep and primary.isdigit() and related.isdigit():
            return HoureiLinkTarget(
                kind="future_or_revision_ref",
                original=raw,
                mode=mode,
                hore_id=primary,
                related_hore_id=related,
            )
    return HoureiLinkTarget(kind="unknown_ref", original=raw, mode=mode)


def _db_summary(path: Path, root: Path) -> dict[str, Any]:
    tables = _sqlite_tables(path)
    return {
        "path": str(path.relative_to(root)),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "kind": file_magic_kind(read_file_prefix(path, 64)),
        "table_count": len(tables),
        "row_count": sum(int(table["rows"] or 0) for table in tables),
        "tables": tables,
    }


def _law_db_path(root: Path, hore_id: str | int) -> Path | None:
    raw = str(hore_id)
    if len(raw) < 3:
        return None
    prefix = ERA_DIR_PREFIX.get(raw[0])
    if prefix is None:
        return None
    directory = root / "_DataBase" / f"{prefix}{int(raw[1:3]):02d}"
    path = directory / f"{raw}.db"
    return path if path.is_file() else None


def _html_asset_lookup(root: Path) -> dict[str, Path]:
    lookup: dict[str, Path] = {}
    roots = [root / "_DataBase" / "image", root / "_KanpouImg", root / "_Manual" / "image", root / "_Programs"]
    for base in roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or is_metadata_noise_path(path):
                continue
            for key in {path.name.casefold(), path.stem.casefold()}:
                lookup.setdefault(key, path)
    return lookup


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip())
    return safe.strip("._") or "item"


def _copy_hourei_asset(value: str, *, assets_dir: Path, lookup: dict[str, Path]) -> str | None:
    raw = html.unescape(value).strip()
    if not raw or raw.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.IGNORECASE):
        return None
    key = Path(raw.replace("\\", "/")).name.casefold()
    source = lookup.get(key) or lookup.get(Path(key).stem.casefold())
    if source is None:
        return None
    target = assets_dir / _safe_name(source.name)
    if not target.exists():
        shutil.copy2(source, target)
    return f"assets/{target.name}"


def rewrite_hourei_fragment(
    fragment: str,
    *,
    assets_dir: Path,
    asset_lookup: dict[str, Path],
    local_hore_id: str | None = None,
    search_page_for_query: Callable[[str], str] | None = None,
) -> tuple[str, dict[str, int], set[str]]:
    stats: Counter[str] = Counter()
    linked_laws: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        attr = match.group("attr")
        quote = match.group("quote")
        value = html.unescape(match.group("value"))
        if attr.casefold() == "href":
            target = parse_hourei_link(value)
            if target and target.kind == "local_mark" and target.anchor is not None:
                stats["mark_links_rewritten"] += 1
                return f'{attr}={quote}#{html.escape(target.anchor, quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if target and target.kind == "local_ref" and target.anchor is not None:
                stats["local_ref_links_rewritten"] += 1
                if local_hore_id:
                    local_href = f"entry_{local_hore_id}.html#{target.anchor}"
                else:
                    local_href = f"#{target.anchor}"
                return f'{attr}={quote}{html.escape(local_href, quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if target and target.kind == "law_ref" and target.hore_id is not None:
                hore_id = target.hore_id
                linked_laws.add(hore_id)
                anchor = target.anchor or ""
                anchor_suffix = f"#{html.escape(anchor, quote=True)}" if anchor else ""
                stats["law_ref_links_rewritten"] += 1
                return f'{attr}={quote}entry_{hore_id}.html{anchor_suffix}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if target and target.kind == "future_or_revision_ref":
                stats["future_revision_ref_links_deferred"] += 1
                attrs = [
                    f'{attr}={quote}#{quote}',
                    f'data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}',
                    f'data-lv-ref-kind={quote}future_or_revision_ref{quote}',
                ]
                if target.hore_id:
                    attrs.append(f'data-lv-primary-hore-id={quote}{html.escape(target.hore_id, quote=True)}{quote}')
                if target.related_hore_id:
                    attrs.append(f'data-lv-related-hore-id={quote}{html.escape(target.related_hore_id, quote=True)}{quote}')
                return " ".join(attrs)
            if target and target.kind == "kana_search" and target.query is not None:
                query = target.query
                stats["kana_ref_links_deferred"] += 1
                href = search_page_for_query(query) if search_page_for_query else f"search.html?q={query}"
                return f'{attr}={quote}{html.escape(href, quote=True)}{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if target and target.kind == "unsafe":
                stats["unsafe_links_suppressed"] += 1
                return f'{attr}={quote}#{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote}'
            if target is not None:
                stats["unknown_ref_links_deferred"] += 1
                return f'{attr}={quote}#{quote} data-lv-original-href={quote}{html.escape(value, quote=True)}{quote} data-lv-ref-kind={quote}unknown{quote}'
        if attr.casefold() == "src":
            copied = _copy_hourei_asset(value, assets_dir=assets_dir, lookup=asset_lookup)
            if copied:
                stats["assets_resolved"] += 1
                return f'{attr}={quote}{html.escape(copied, quote=True)}{quote}'
            stats["assets_unresolved"] += 1
        return match.group(0)

    rewritten = _ATTR_LINK_RE.sub(repl, fragment)
    return rewritten, dict(sorted(stats.items())), linked_laws


def _page(title: str, body: str, *, css_href: str | None = None) -> str:
    css = f'<link rel="stylesheet" href="{html.escape(css_href, quote=True)}">\n' if css_href else ""
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{css}<style>
body {{ margin: 0; padding: 20px; font-family: "Yu Gothic", Meiryo, sans-serif; line-height: 1.6; }}
nav a, .lv-hit {{ display: block; padding: 0.3rem 0; }}
.lv-meta {{ color: #666; font-size: 13px; border-bottom: 1px solid #ddd; margin-bottom: 1rem; padding-bottom: 0.4rem; }}
img {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _search_page_name(query: str) -> str:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    return f"search_{digest}.html"


def _cached_html_path(root: Path, hore_id: str | int, kind: str) -> Path | None:
    hore_id = str(hore_id)
    if kind == "body":
        path = root / "_DataBase" / "HTMLs" / "H" / f"{hore_id}_H.html"
    elif kind == "index":
        path = root / "_DataBase" / "HTMLs" / "I" / f"{hore_id}_IDX.html"
    elif kind == "preview":
        path = root / "_DataBase" / "HTMLs" / "P" / f"{hore_id}_P.html"
    else:
        raise ValueError(f"unknown Hourei cached HTML kind: {kind}")
    return path if path.is_file() else None


def _cached_html_inventory(root: Path) -> dict[str, Any]:
    base = root / "_DataBase" / "HTMLs"
    if not base.is_dir():
        return {"present": False}
    groups = {
        "body": ("H", "_H.html"),
        "index": ("I", "_IDX.html"),
        "preview": ("P", "_P.html"),
    }
    ids: dict[str, set[str]] = {}
    rows: dict[str, Any] = {"present": True, "path": str(base.relative_to(root)), "groups": {}}
    for kind, (directory, suffix) in groups.items():
        files = sorted((base / directory).glob(f"*{suffix}")) if (base / directory).is_dir() else []
        values = {path.name[: -len(suffix)] for path in files}
        ids[kind] = values
        rows["groups"][kind] = {
            "path": str((base / directory).relative_to(root)) if (base / directory).is_dir() else None,
            "file_count": len(files),
            "sample_ids": sorted(values)[:20],
        }
    rows["paired_index_preview_count"] = len(ids.get("index", set()) & ids.get("preview", set()))
    rows["body_subset_of_index"] = not bool(ids.get("body", set()) - ids.get("index", set()))
    rows["body_without_index_count"] = len(ids.get("body", set()) - ids.get("index", set()))
    return rows


def _copy_hourei_css(root: Path, out_dir: Path, *, variant: str = "00") -> str | None:
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    css_source = root / "_Programs" / f"LVLawBrowser_{variant}.css"
    if not css_source.is_file():
        return None
    css_target = assets_dir / css_source.name
    if not css_target.exists():
        text = _decode_hourei_text(css_source.read_bytes())
        text = text.replace("$NORMAL_FONT_NAME$", '"Yu Gothic", Meiryo, sans-serif')
        text = text.replace("$NORMAL_FONT_SIZE$", "16px")
        text = text.replace("$ARTICLE_FONT_NAME$", '"Yu Gothic", Meiryo, sans-serif')
        text = text.replace("$ARTICLE_FONT_SIZE$", "16px")
        css_target.write_text(text, encoding="utf-8")
    return f"assets/{css_target.name}"


def _law_metadata(root: Path, hore_id: str | int) -> dict[str, Any] | None:
    base = root / "_DataBase" / "hore_base.db"
    if not base.is_file():
        return None
    con = _sqlite_connect(base)
    try:
        row = con.execute("select * from t_hore where f_hore_id = ? limit 1", (str(hore_id),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def write_hourei_law_page(root: Path, out_dir: Path, hore_id: str | int) -> dict[str, Any] | None:
    root = root.resolve()
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    asset_lookup = _html_asset_lookup(root)
    css_href = _copy_hourei_css(root, out_dir, variant="00")
    fragments: list[str] = []
    stats: Counter[str] = Counter()
    linked_laws: set[str] = set()

    cached_body = _cached_html_path(root, hore_id, "body")
    body_source = ""
    row_count = 0
    if cached_body is not None:
        body_source = str(cached_body.relative_to(root))
        path = _law_db_path(root, hore_id)
        if path is not None:
            con = _sqlite_connect(path)
            try:
                row_count = int(con.execute("select count(*) from t_page").fetchone()[0])
            finally:
                con.close()
        text = _decode_hourei_text(cached_body.read_bytes())
        rewritten, rewrite_stats, links = rewrite_hourei_fragment(
            text,
            assets_dir=assets_dir,
            asset_lookup=asset_lookup,
            local_hore_id=str(hore_id),
        )
        stats.update(rewrite_stats)
        linked_laws.update(links)
        fragments.append(rewritten)
    else:
        path = _law_db_path(root, hore_id)
        if path is None:
            return None
        con = _sqlite_connect(path)
        try:
            rows = list(con.execute("select * from t_page order by f_rec_id"))
        finally:
            con.close()
        if not rows:
            return None
        body_source = str(path.relative_to(root))
        row_count = len(rows)
        for row in rows:
            text = "" if row["f_text"] is None else str(row["f_text"])
            rewritten, rewrite_stats, links = rewrite_hourei_fragment(
                text,
                assets_dir=assets_dir,
                asset_lookup=asset_lookup,
            )
            stats.update(rewrite_stats)
            linked_laws.update(links)
            fragments.append(rewritten)
    metadata = _law_metadata(root, hore_id) or {}
    title = str(metadata.get("f_name") or metadata.get("f_abbr1") or hore_id)
    target = out_dir / f"entry_{hore_id}.html"
    target.write_text(
        _page(
            title,
            f'<main class="lv-hourei-entry"><div class="lv-meta">hore_id={hore_id} rows={row_count} source={html.escape(body_source)}</div>'
            + "\n".join(fragments)
            + "</main>",
            css_href=css_href,
        ),
        encoding="utf-8",
    )
    return {
        "hore_id": str(hore_id),
        "path": target.name,
        "title": title,
        "row_count": row_count,
        "source": body_source,
        "visual_source_kind": "cached_html_body" if cached_body is not None else "body_shard_pages",
        "rewrite": dict(sorted(stats.items())),
        "linked_laws": sorted(linked_laws)[:100],
        "linked_law_count": len(linked_laws),
    }


def _write_named_row_page(
    root: Path,
    out_dir: Path,
    *,
    source: str,
    row: sqlite3.Row,
    title_columns: tuple[str, ...],
    body_column: str,
    prefix: str,
) -> dict[str, Any]:
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    title = next((str(row[col]) for col in title_columns if col in row.keys() and row[col]), f"{prefix} {row[0]}")
    body = "" if row[body_column] is None else str(row[body_column])
    rewritten, stats, linked = rewrite_hourei_fragment(body, assets_dir=assets_dir, asset_lookup=_html_asset_lookup(root))
    target = out_dir / f"{prefix}_{row[0]}.html"
    target.write_text(
        _page(title, f'<main><div class="lv-meta">{html.escape(source)} id={row[0]}</div>{rewritten}</main>'),
        encoding="utf-8",
    )
    return {"id": str(row[0]), "title": title, "path": target.name, "rewrite": stats, "linked_law_count": len(linked)}


def search_hourei(root: Path, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    root = root.resolve()
    like = f"%{query}%"
    results: list[dict[str, Any]] = []

    base = root / "_DataBase" / "hore_search_a.db"
    if base.is_file():
        con = _sqlite_connect(base)
        try:
            columns = [
                "f_hore_id",
                "f_name",
                "f_name_sub",
                "f_abbr1",
                "f_abbr2",
                "f_abbr3",
                "f_kana_order",
                "f_text_plane",
            ]
            text_columns = [col for col in columns[1:] if col != "f_kana_order"]
            title_columns = [col for col in text_columns if col != "f_text_plane"]
            where = " or ".join(f"{quote_identifier(col)} like ?" for col in text_columns)
            exact = " or ".join(f"{quote_identifier(col)} = ?" for col in title_columns)
            prefix = " or ".join(f"{quote_identifier(col)} like ?" for col in title_columns)
            for row in con.execute(
                f"select {', '.join(quote_identifier(col) for col in columns)} from t_hore "
                f"where {where} "
                f"order by case when {exact} then 0 when {prefix} then 1 else 2 end, "
                "f_kana_order, f_hore_id limit ?",
                (
                    *([like] * len(text_columns)),
                    *([query] * len(title_columns)),
                    *([f"{query}%"] * len(title_columns)),
                    limit,
                ),
            ):
                results.append({"kind": "law", "hore_id": str(row["f_hore_id"]), "title": str(row["f_name"] or row["f_abbr1"] or ""), "source": "hore_search_a"})
        finally:
            con.close()

    if len(results) < limit:
        index_db = root / "_DataBase" / "horejo_base.db"
        if index_db.is_file():
            con = _sqlite_connect(index_db)
            try:
                for table in ("t_horejo", "t_horejomi"):
                    for row in con.execute(
                        f"select f_hore_id, f_rec_id, f_anchor, f_title_no, f_title_sub, f_text_plane from {table} "
                        "where f_text_plane like ? "
                        "order by case when f_title_no like ? or f_title_sub like ? then 0 else 1 end, "
                        "f_hore_id, f_rec_id limit ?",
                        (like, f"{query}%", f"{query}%", max(0, limit - len(results))),
                    ):
                        results.append(
                            {
                                "kind": table.removeprefix("t_"),
                                "hore_id": str(row["f_hore_id"]),
                                "rec_id": row["f_rec_id"],
                                "anchor": row["f_anchor"],
                                "title": " ".join(str(row[col] or "") for col in ("f_title_no", "f_title_sub")).strip(),
                                "source": "horejo_base",
                            }
                        )
                    if len(results) >= limit:
                        break
            finally:
                con.close()

    for db_name, table, kind in (
        ("hore_kanpou.db", "t_kanpou", "kanpou"),
        ("hore_aramashi.db", "t_aramashi", "aramashi"),
    ):
        if len(results) >= limit:
            break
        path = root / "_Kanpou" / db_name
        if not path.is_file():
            continue
        con = _sqlite_connect(path)
        try:
            for row in con.execute(
                f"select * from {table} where f_name like ? or f_text_plane like ? limit ?",
                (like, like, max(0, limit - len(results))),
            ):
                results.append(
                    {
                        "kind": kind,
                        "id": str(row["f_rec_id"]),
                        "hore_id": str(row["f_hore_id"]) if "f_hore_id" in row.keys() else None,
                        "title": str(row["f_name"] or row["f_name_sub"] or ""),
                        "source": db_name,
                    }
                )
        finally:
            con.close()
    return results[:limit]


def _file_extension_counts(root: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in root.rglob("*"):
        if path.is_file() and not is_metadata_noise_path(path):
            counts[path.suffix.casefold() or "<none>"] += 1
    return dict(sorted(counts.items()))


def _schema_inventory(root: Path) -> dict[str, Any]:
    dbs = sorted(path for path in root.rglob("*.db") if path.is_file() and not is_metadata_noise_path(path))
    table_counts: Counter[str] = Counter()
    column_sets: Counter[tuple[str, ...]] = Counter()
    directory_counts: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    for path in dbs:
        directory_counts[str(path.relative_to(root).parts[0])] += 1
        try:
            for table in _sqlite_tables(path):
                table_counts[str(table["name"])] += 1
                column_sets[tuple(str(column) for column in table["columns"])] += 1
        except sqlite3.Error as exc:
            errors.append({"path": str(path.relative_to(root)), "error": str(exc)})
    return {
        "db_count": len(dbs),
        "directory_counts": dict(sorted(directory_counts.items())),
        "errors": errors[:100],
        "error_count": len(errors),
        "top_tables": [{"name": key, "count": value} for key, value in table_counts.most_common(60)],
        "top_column_sets": [
            {"columns": list(columns), "count": count}
            for columns, count in column_sets.most_common(20)
        ],
    }


def _body_feature_scan(root: Path, *, max_databases: int | None = None) -> dict[str, Any]:
    paths = sorted(path for path in (root / "_DataBase").glob("*/*.db") if path.is_file())
    if max_databases is not None:
        paths = paths[:max_databases]
    classes: Counter[str] = Counter()
    hrefs: Counter[str] = Counter()
    srcs: Counter[str] = Counter()
    row_count = 0
    text_bytes = 0
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            con = _sqlite_connect(path)
            try:
                if not any(row["name"] == "t_page" for row in con.execute("select name from sqlite_master where type='table'")):
                    continue
                for row in con.execute("select f_text from t_page where f_text is not null and f_text != ''"):
                    text = str(row["f_text"] or "")
                    row_count += 1
                    text_bytes += len(text.encode("utf-8"))
                    classes.update(re.findall(r"""class=["']([^"']+)""", text, re.IGNORECASE))
                    for href in re.findall(r"""href=["']([^"']+)""", text, re.IGNORECASE):
                        prefix = re.sub(r"\d+", "N", href.split("&", 1)[0])
                        hrefs[prefix] += 1
                    for src in re.findall(r"""src=["']([^"']+)""", text, re.IGNORECASE):
                        srcs[Path(src).suffix.casefold() or "<none>"] += 1
            finally:
                con.close()
        except sqlite3.Error as exc:
            errors.append({"path": str(path.relative_to(root)), "error": str(exc)})
    return {
        "database_count": len(paths),
        "body_row_count": row_count,
        "body_text_bytes": text_bytes,
        "class_counts": dict(classes.most_common(80)),
        "href_prefix_counts": dict(hrefs.most_common(60)),
        "src_extension_counts": dict(srcs.most_common(20)),
        "errors": errors[:100],
        "error_count": len(errors),
    }


def _link_and_asset_readiness_scan(root: Path, *, max_databases: int | None = None) -> dict[str, Any]:
    paths = sorted(path for path in (root / "_DataBase").glob("*/*.db") if path.is_file())
    if max_databases is not None:
        paths = paths[:max_databases]
    link_forms: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    asset_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    asset_lookup = _html_asset_lookup(root)
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            con = _sqlite_connect(path)
            try:
                if not any(row["name"] == "t_page" for row in con.execute("select name from sqlite_master where type='table'")):
                    continue
                for row in con.execute("select f_text from t_page where f_text is not null and f_text != ''"):
                    text = str(row["f_text"] or "")
                    for match in _ATTR_LINK_RE.finditer(text):
                        attr = match.group("attr").casefold()
                        value = html.unescape(match.group("value"))
                        if attr == "href":
                            target = parse_hourei_link(value)
                            key = target.kind if target else "ordinary_href"
                            link_forms[key] += 1
                            if target and target.mode:
                                mode_counts[target.mode] += 1
                            examples.setdefault(key, [])
                            if len(examples[key]) < 8:
                                examples[key].append(value)
                        elif attr == "src":
                            key = Path(value.replace("\\", "/")).name.casefold()
                            source = asset_lookup.get(key) or asset_lookup.get(Path(key).stem.casefold())
                            asset_counts["resolved" if source else "missing"] += 1
                            if source is None:
                                examples.setdefault("missing_asset", [])
                                if len(examples["missing_asset"]) < 8:
                                    examples["missing_asset"].append(value)
            finally:
                con.close()
        except sqlite3.Error as exc:
            errors.append({"path": str(path.relative_to(root)), "error": str(exc)})
    return {
        "database_count": len(paths),
        "link_forms": dict(sorted(link_forms.items())),
        "ref_mode_counts": dict(sorted(mode_counts.items())),
        "asset_resolution": dict(sorted(asset_counts.items())),
        "examples": {key: values for key, values in sorted(examples.items())},
        "errors": errors[:100],
        "error_count": len(errors),
    }


def inspect_hourei_package(root: Path, *, deep: bool = False, sample_rows: int = 64) -> dict[str, Any]:
    root = root.resolve()
    if not is_hourei_package(root):
        raise ValueError(f"not a Hourei package root: {root}")
    core_paths = [
        root / "_DataBase" / "hore_base.db",
        root / "_DataBase" / "hore_search_a.db",
        root / "_DataBase" / "horejo_base.db",
        root / "_Kanpou" / "hore_kanpou.db",
        root / "_Kanpou" / "hore_aramashi.db",
        root / "_Programs" / "USER.db",
    ]
    report = {
        "schema": HOUREI_SCHEMA,
        "dict_id": root.name,
        "path": str(root),
        "classification": "hourei_professional",
        "file_extension_counts": _file_extension_counts(root),
        "top_level": {
            child.name: {
                "files": sum(1 for path in child.rglob("*") if path.is_file() and not is_metadata_noise_path(path)),
                "bytes": sum(path.stat().st_size for path in child.rglob("*") if path.is_file() and not is_metadata_noise_path(path)),
            }
            for child in sorted(root.iterdir())
            if child.is_dir() and not is_metadata_noise_path(child)
        },
        "core_databases": [_db_summary(path, root) for path in core_paths if path.is_file()],
        "schema_inventory": _schema_inventory(root),
        "body_feature_scan": _body_feature_scan(root, max_databases=None if deep else sample_rows),
        "link_and_asset_readiness": _link_and_asset_readiness_scan(root, max_databases=None if deep else sample_rows),
        "cached_html_surfaces": _cached_html_inventory(root),
        "manual": _html_directory_summary(root / "_Manual", root),
        "program_files": _program_file_summary(root),
        "program_hook_signals": _program_hook_signals(root),
    }
    return report


def _html_directory_summary(path: Path, root: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {"present": False}
    html_files = sorted(child for child in path.rglob("*") if child.is_file() and child.suffix.casefold() in {".html", ".htm"})
    return {
        "present": True,
        "path": str(path.relative_to(root)),
        "html_files": len(html_files),
        "css_files": len([child for child in path.rglob("*") if child.is_file() and child.suffix.casefold() == ".css"]),
        "js_files": len([child for child in path.rglob("*") if child.is_file() and child.suffix.casefold() == ".js"]),
        "image_files": len([child for child in path.rglob("*") if child.is_file() and child.suffix.casefold() in {".png", ".jpg", ".jpeg", ".gif"}]),
        "sample_html": [str(child.relative_to(path)) for child in html_files[:40]],
    }


def _program_file_summary(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = root / "_Programs"
    if not base.is_dir():
        return rows
    for path in sorted(base.rglob("*")):
        if not path.is_file() or is_metadata_noise_path(path) or path.suffix.casefold() not in {".exe", ".dll", ".css", ".html", ".db"}:
            continue
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "kind": file_magic_kind(read_file_prefix(path, 64)),
            }
        )
    return rows


def _decoded_program_text(path: Path) -> str:
    data = path.read_bytes()
    chunks = []
    for encoding in ("utf-8", "cp932", "utf-16le"):
        try:
            chunks.append(data.decode(encoding, errors="ignore"))
        except LookupError:
            continue
    return "\n".join(chunks)


def _program_hook_signals(root: Path) -> dict[str, Any]:
    base = root / "_Programs"
    if not base.is_dir():
        return {"present": False}
    tokens = {
        "lved_ref": "lved_ref",
        "lved_mark": "lved_mark",
        "lved_unsafe": "lved_unsafe",
        "law_css": "LVLawBrowser_00.css",
        "hore_base_db": "hore_base.db",
        "hore_search_db": "hore_search_a.db",
        "horejo_db": "horejo_base.db",
        "kanpou_db": "hore_kanpou.db",
        "table_t_hore": "t_hore",
        "table_t_horejo": "t_horejo",
        "table_t_horejomi": "t_horejomi",
        "table_t_kanpou": "t_kanpou",
        "table_t_haishi": "t_haishi",
        "table_t_miseko": "t_miseko",
        "open_hore_view": "OpenHoreView",
        "get_hore_infos": "GetHoreInfos",
        "kanpou_class": "LVLawKanpou",
    }
    totals: Counter[str] = Counter()
    files: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or is_metadata_noise_path(path):
            continue
        if path.suffix.casefold() not in {".exe", ".dll", ".css", ".html", ".js", ".config"}:
            continue
        text = _decoded_program_text(path)
        counts = {name: text.count(token) for name, token in tokens.items() if token in text}
        totals.update(counts)
        if counts:
            files.append({"path": str(path.relative_to(root)), "signals": dict(sorted(counts.items()))})
    return {
        "present": True,
        "signal_counts": dict(sorted(totals.items())),
        "files": files,
    }


def write_hourei_showcase(root: Path, out_dir: Path, *, queries: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
    root = root.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    queries = queries or ["民法", "会社法", "所得税", "個人情報", "不正競争"]
    rendered_entries: dict[str, dict[str, Any]] = {}
    rendered_navigation: dict[tuple[str, str], dict[str, Any]] = {}
    searches: list[dict[str, Any]] = []
    for query in queries:
        results = search_hourei(root, query, limit=limit)
        rendered: list[dict[str, Any]] = []
        for result in results:
            if result.get("kind") in {"law", "horejo", "horejomi"} and result.get("hore_id"):
                page = write_hourei_law_page(root, out_dir, str(result["hore_id"]))
                if page is not None:
                    rendered_entries[page["hore_id"]] = page
                    rendered.append(page)
                    for kind in ("index", "preview"):
                        nav_page = write_hourei_cached_navigation_page(root, out_dir, page["hore_id"], kind=kind)
                        if nav_page is not None:
                            rendered_navigation[(nav_page["hore_id"], nav_page["kind"])] = nav_page
            elif result.get("kind") == "kanpou":
                row_page = _write_kanpou_or_aramashi_page(root, out_dir, result, table="t_kanpou", db_name="hore_kanpou.db", prefix="kanpou")
                if row_page:
                    rendered.append(row_page)
            elif result.get("kind") == "aramashi":
                row_page = _write_kanpou_or_aramashi_page(root, out_dir, result, table="t_aramashi", db_name="hore_aramashi.db", prefix="aramashi")
                if row_page:
                    rendered.append(row_page)
        searches.append({"query": query, "result_count": len(results), "rendered_count": len(rendered), "results": results[:limit]})

    # Render first few linked laws discovered from body links.
    linked_ids = []
    for page in list(rendered_entries.values()):
        linked_ids.extend(page.get("linked_laws") or [])
    for hore_id in linked_ids:
        if len(rendered_entries) >= limit * max(1, len(queries)):
            break
        if hore_id not in rendered_entries:
            page = write_hourei_law_page(root, out_dir, hore_id)
            if page is not None:
                rendered_entries[page["hore_id"]] = page
                for kind in ("index", "preview"):
                    nav_page = write_hourei_cached_navigation_page(root, out_dir, page["hore_id"], kind=kind)
                    if nav_page is not None:
                        rendered_navigation[(nav_page["hore_id"], nav_page["kind"])] = nav_page

    index_panel = write_hourei_index_panel_page(root, out_dir, limit=min(limit, 20))

    hit_html = []
    if index_panel:
        hit_html.append(f'<h2>かな索引</h2><a class="lv-hit" href="{html.escape(str(index_panel["path"]), quote=True)}">かな索引パネル</a>')
    for query in searches:
        hit_html.append(f"<h2>{html.escape(query['query'])}</h2>")
        for result in query["results"]:
            hore_id = result.get("hore_id")
            title = result.get("title") or result.get("id") or ""
            if hore_id and str(hore_id) in rendered_entries:
                hit_html.append(f'<a class="lv-hit" href="{rendered_entries[str(hore_id)]["path"]}">{html.escape(str(title))}</a>')
            else:
                hit_html.append(f'<div class="lv-hit">{html.escape(str(result.get("kind")))}: {html.escape(str(title))}</div>')

    # Keep the bounded static showcase self-contained: every generated
    # entry_*.html link should either be a rendered page or an explicit
    # placeholder target.
    referenced_entry_ids: set[str] = set()
    for path in out_dir.glob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        referenced_entry_ids.update(re.findall(r"""href=["']entry_(\d+)\.html(?:[#?][^"']*)?["']""", text))
    placeholders = []
    for hore_id in sorted(referenced_entry_ids - set(rendered_entries)):
        placeholders.append(_write_hourei_placeholder_entry(root, out_dir, hore_id))

    index = out_dir / "index.html"
    index.write_text(_page("Hourei showcase", "<main><h1>Hourei showcase</h1>" + "\n".join(hit_html) + "</main>"), encoding="utf-8")
    validation = validate_hourei_showcase(out_dir)
    manifest = {
        "schema": "logovista-hourei-showcase-v1",
        "root": str(root),
        "index": str(index),
        "queries": searches,
        "rendered_entries": sorted(rendered_entries.values(), key=lambda row: row["hore_id"]),
        "navigation_pages": sorted(rendered_navigation.values(), key=lambda row: (row["hore_id"], row["kind"], row["path"])),
        "index_panel": index_panel,
        "placeholder_entries": placeholders,
        "validation": validation,
    }
    (out_dir / "showcase.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_kanpou_or_aramashi_page(
    root: Path,
    out_dir: Path,
    result: dict[str, Any],
    *,
    table: str,
    db_name: str,
    prefix: str,
) -> dict[str, Any] | None:
    path = root / "_Kanpou" / db_name
    if not path.is_file() or not result.get("id"):
        return None
    con = _sqlite_connect(path)
    try:
        row = con.execute(f"select * from {table} where f_rec_id = ? limit 1", (result["id"],)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return _write_named_row_page(
        root,
        out_dir,
        source=f"_Kanpou/{db_name}:{table}",
        row=row,
        title_columns=("f_name", "f_name_sub"),
        body_column="f_text",
        prefix=prefix,
    )


def write_hourei_cached_navigation_page(root: Path, out_dir: Path, hore_id: str | int, *, kind: str) -> dict[str, Any] | None:
    root = root.resolve()
    path = _cached_html_path(root, hore_id, kind)
    if path is None:
        return None
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    text = _decode_hourei_text(path.read_bytes())
    rewritten, stats, linked = rewrite_hourei_fragment(
        text,
        assets_dir=assets_dir,
        asset_lookup=_html_asset_lookup(root),
        local_hore_id=str(hore_id),
    )
    metadata = _law_metadata(root, hore_id) or {}
    title = str(metadata.get("f_name") or metadata.get("f_abbr1") or hore_id)
    prefix = "toc" if kind == "index" else "preview"
    target = out_dir / f"{prefix}_{hore_id}.html"
    target.write_text(
        _page(
            f"{title} {kind}",
            f'<main class="lv-hourei-{kind}"><div class="lv-meta">{html.escape(str(path.relative_to(root)))}</div>{rewritten}</main>',
            css_href=_copy_hourei_css(root, out_dir, variant="11"),
        ),
        encoding="utf-8",
    )
    return {
        "hore_id": str(hore_id),
        "kind": kind,
        "path": target.name,
        "title": title,
        "source": str(path.relative_to(root)),
        "rewrite": stats,
        "linked_law_count": len(linked),
    }


def write_hourei_search_results_page(root: Path, out_dir: Path, query: str, *, limit: int = 20) -> dict[str, Any]:
    results = search_hourei(root, query, limit=limit)
    rows: list[str] = [f"<h1>{html.escape(query)}</h1>"]
    for result in results:
        title = str(result.get("title") or result.get("id") or "")
        kind = str(result.get("kind") or "")
        hore_id = result.get("hore_id")
        if hore_id:
            rows.append(
                f'<a class="lv-hit" href="entry_{html.escape(str(hore_id), quote=True)}.html">'
                f'{html.escape(title)}</a><div class="lv-meta">{html.escape(kind)}</div>'
            )
        else:
            rows.append(f'<div class="lv-hit">{html.escape(kind)}: {html.escape(title)}</div>')
    target = out_dir / _search_page_name(query)
    target.write_text(_page(f"Hourei search {query}", "<main>" + "\n".join(rows) + "</main>"), encoding="utf-8")
    return {"query": query, "path": target.name, "result_count": len(results), "results": results}


def write_hourei_index_panel_page(root: Path, out_dir: Path, *, limit: int = 20) -> dict[str, Any] | None:
    source = root / "_Programs" / "index_panel.html"
    if not source.is_file():
        return None
    text = _decode_hourei_text(source.read_bytes())
    queries = sorted({target.query for target in (parse_hourei_link(value) for value in re.findall(r"""href=["']([^"']+)""", text, re.IGNORECASE)) if target and target.query})
    search_pages = {query: write_hourei_search_results_page(root, out_dir, query, limit=limit) for query in queries}

    def page_for_query(query: str) -> str:
        return str(search_pages[query]["path"])

    rewritten, stats, linked = rewrite_hourei_fragment(
        text,
        assets_dir=out_dir / "assets",
        asset_lookup=_html_asset_lookup(root),
        search_page_for_query=page_for_query,
    )
    target = out_dir / "kana_index_panel.html"
    target.write_text(rewritten, encoding="utf-8")
    return {
        "path": target.name,
        "source": str(source.relative_to(root)),
        "query_count": len(queries),
        "queries": queries,
        "search_pages": sorted(search_pages.values(), key=lambda row: row["query"]),
        "rewrite": stats,
        "linked_law_count": len(linked),
    }


def _write_hourei_placeholder_entry(root: Path, out_dir: Path, hore_id: str) -> dict[str, Any]:
    metadata = _law_metadata(root, hore_id) or {}
    title = str(metadata.get("f_name") or metadata.get("f_abbr1") or hore_id)
    target = out_dir / f"entry_{hore_id}.html"
    if not target.exists():
        target.write_text(
            _page(
                title,
                f'<main><h1>{html.escape(title)}</h1><div class="lv-meta">Target {html.escape(hore_id)} is recognized but was not rendered in this bounded showcase.</div></main>',
            ),
            encoding="utf-8",
        )
    return {"hore_id": hore_id, "path": target.name, "title": title, "placeholder": True}


def validate_hourei_showcase(out_dir: Path) -> dict[str, Any]:
    missing_href: list[dict[str, str]] = []
    missing_src: list[dict[str, str]] = []
    protocol_hrefs: Counter[str] = Counter()
    html_files = sorted(out_dir.glob("*.html"))
    for path in html_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _ATTR_LINK_RE.finditer(text):
            attr = match.group("attr")
            value = match.group("value")
            raw = html.unescape(value)
            lower = raw.casefold()
            if attr.casefold() == "href":
                if lower.startswith(("http:", "https:", "mailto:", "javascript:")) or raw.startswith("#"):
                    continue
                if lower.startswith("lved_"):
                    protocol_hrefs[raw.split(":", 1)[0].split("&", 1)[0]] += 1
                    continue
                target = out_dir / raw.split("#", 1)[0].split("?", 1)[0]
                if raw and not target.is_file():
                    missing_href.append({"page": path.name, "href": raw})
            elif attr.casefold() == "src":
                if lower.startswith(("http:", "https:", "data:")):
                    continue
                target = out_dir / raw.split("#", 1)[0].split("?", 1)[0]
                if raw and not target.is_file():
                    missing_src.append({"page": path.name, "src": raw})
    return {
        "html_file_count": len(html_files),
        "missing_href_count": len(missing_href),
        "missing_src_count": len(missing_src),
        "protocol_href_counts": dict(sorted(protocol_hrefs.items())),
        "missing_href_samples": missing_href[:40],
        "missing_src_samples": missing_src[:40],
    }


def write_hourei_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
