"""Windows LogoVista sidecar helpers."""

from __future__ import annotations

import configparser
import hashlib
import html
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_file_to_path,
    decrypt_logofont_cipher_prefix,
)
from .ssed import BLOCK_SIZE, SsedInfoElement, find_case_insensitive


SQLITE_MAGIC = b"SQLite format 3\x00"
HEX_POINTER_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
NUMERIC_AUX_INDEX_RE = re.compile(r"^[0-9A-Fa-f]{8}\.idx$", re.IGNORECASE)
VLPLJBL_RE = re.compile(r"^vlpljbl(?:$|[A-Za-z]$|\.(?:bin|exe)$)", re.IGNORECASE)


@dataclass(frozen=True)
class Exinfo:
    """Parsed Windows ``EXINFO.INI`` fields."""

    path: Path
    general: dict[str, str]


@dataclass(frozen=True)
class AuxIndexSpec:
    """One ``IDXNAME`` / ``IDXINFO`` pair from ``EXINFO.INI``."""

    index: int
    name: str
    info: str
    path: Path | None


@dataclass(frozen=True)
class AuxIndexRow:
    """One row from a Windows text auxiliary index."""

    line_number: int
    block: int
    offset: int
    depth: int
    label: str
    path: tuple[str, ...]
    target: dict[str, Any] | None = None


@dataclass(frozen=True)
class RendererSidecar:
    """A Windows renderer sidecar that is or decrypts to SQLite."""

    path: Path
    storage: str


@dataclass(frozen=True)
class VlpljblClassification:
    """Forensic classification for one observed ``vlpljbl*`` sibling."""

    path: Path
    suffix: str
    size: int
    sha256: str | None
    storage: str
    content_kind: str
    role: str
    decrypt_prefix_kind: str | None = None
    sqlite_tables: tuple[dict[str, Any], ...] = ()


def exinfo_path_for_idx(idx: Path) -> Path | None:
    return find_case_insensitive(idx.parent, "EXINFO.INI")


def parse_exinfo(path: Path) -> Exinfo:
    text = path.read_text(encoding="cp932", errors="replace")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    general = dict(parser["GENERAL"]) if parser.has_section("GENERAL") else {}
    return Exinfo(path=path, general=general)


def load_exinfo_for_idx(idx: Path) -> Exinfo | None:
    path = exinfo_path_for_idx(idx)
    if path is None:
        return None
    return parse_exinfo(path)


def iter_aux_index_specs(exinfo: Exinfo) -> list[AuxIndexSpec]:
    raw_count = exinfo.general.get("IDXCOUNT", "0")
    try:
        count = int(raw_count)
    except ValueError:
        count = 0

    rows: list[AuxIndexSpec] = []
    for index in range(count):
        name = exinfo.general.get(f"IDXNAME{index}", "")
        if not name and index == 0:
            name = exinfo.general.get("IDXTITLE", "")
        info = exinfo.general.get(f"IDXINFO{index}", "")
        path = find_case_insensitive(exinfo.path.parent, info) if info else None
        if path is None and info:
            path = exinfo.path.parent / info
        rows.append(AuxIndexSpec(index=index, name=name, info=info, path=path))
    if not rows and exinfo.general.get("IDXINFO"):
        info = exinfo.general["IDXINFO"]
        name = exinfo.general.get("IDXTITLE", "")
        path = find_case_insensitive(exinfo.path.parent, info) or exinfo.path.parent / info
        rows.append(AuxIndexSpec(index=0, name=name, info=info, path=path))
    return rows


def discover_numeric_aux_indexes(idx: Path) -> list[Path]:
    """Return sibling eight-hex-digit ``*.idx`` sidecar trees.

    These files are distinct from the main SSEDINFO ``.IDX`` catalog. Windows
    packages usually reference them from ``EXINFO.INI``; some iOS-style
    packages also carry them, and a few local packages leave them unreferenced
    by EXINFO.
    """

    rows: list[Path] = []
    seen: set[Path] = set()
    for child in sorted(idx.parent.iterdir()):
        if not child.is_file() or not NUMERIC_AUX_INDEX_RE.fullmatch(child.name):
            continue
        with child.open("rb") as fh:
            if fh.read(8) == b"SSEDINFO":
                continue
        resolved = child.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        rows.append(resolved)
    return rows


def resolve_aux_virtual_target(block: int, offset: int) -> dict[str, Any] | None:
    if offset != 0xFFFF or block & 0x0FFFFFFF:
        return None
    selector = block >> 28
    if not selector:
        return None
    return {
        "kind": "virtual-index-selector",
        "selector": f"{selector:x}",
        "block": f"{block:08x}",
        "offset": f"{offset:04x}",
    }


def resolve_component_target(block: int, offset: int, elements: list[SsedInfoElement]) -> dict[str, Any] | None:
    for element in elements:
        if not element.start:
            continue
        if element.start <= block <= element.end:
            relative_offset = (block - element.start) * BLOCK_SIZE + offset
            return {
                "component": element.filename,
                "component_type": f"{element.type:02x}",
                "start_block": element.start,
                "end_block": element.end,
                "relative_offset": relative_offset,
                "offset_in_block": offset,
            }
    return resolve_aux_virtual_target(block, offset)


def parse_aux_index_text(path: Path, elements: list[SsedInfoElement] | None = None) -> list[AuxIndexRow]:
    """Parse Windows CP932 auxiliary ``*.IDX`` trees.

    These files are not ``SSEDINFO`` catalogs. Each line starts with two
    eight-digit hexadecimal numbers and then uses tab depth before the visible
    label.
    """

    data = path.read_bytes()
    if data.startswith(b"SSEDINFO"):
        return []
    text = data.decode("cp932", errors="replace")
    rows: list[AuxIndexRow] = []
    stack: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 3 or not HEX_POINTER_RE.fullmatch(parts[0]) or not HEX_POINTER_RE.fullmatch(parts[1]):
            continue
        label_index = None
        label = ""
        for idx, part in enumerate(parts[2:], start=1):
            if part:
                label_index = idx
                label = html.unescape(part)
        if label_index is None:
            continue
        depth = label_index
        if len(stack) < depth:
            stack.extend([""] * (depth - len(stack)))
        stack = stack[:depth]
        stack[depth - 1] = label
        block = int(parts[0], 16)
        offset = int(parts[1], 16)
        target = resolve_component_target(block, offset, elements) if elements is not None else None
        rows.append(
            AuxIndexRow(
                line_number=line_number,
                block=block,
                offset=offset,
                depth=depth,
                label=label,
                path=tuple(item for item in stack if item),
                target=target,
            )
        )
    return rows


def aux_index_row_to_json(row: AuxIndexRow) -> dict[str, Any]:
    return {
        "line_number": row.line_number,
        "block": row.block,
        "offset": row.offset,
        "depth": row.depth,
        "label": row.label,
        "path": list(row.path),
        "target": row.target,
    }


def sqlite_storage_for_path(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as infile:
        prefix = infile.read(64)
    if prefix.startswith(SQLITE_MAGIC):
        return "plain"
    if path.stat().st_size % 16:
        return None
    try:
        if decrypt_logofont_cipher_prefix(prefix, size=64).startswith(SQLITE_MAGIC):
            return "logofont_cipher"
    except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError):
        return None
    return None


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def vlpljbl_suffix(path: Path) -> str:
    name = path.name
    if name.lower() == "vlpljbl":
        return "<none>"
    if name.lower().startswith("vlpljbl."):
        return name[len("vlpljbl") :]
    return name[len("vlpljbl") :]


def is_vlpljbl_path(path: Path) -> bool:
    return path.is_file() and VLPLJBL_RE.fullmatch(path.name) is not None


def file_magic_kind(data: bytes) -> str:
    if data.startswith(SQLITE_MAGIC):
        return "sqlite"
    if data.startswith(b"MZ"):
        return "pe_executable"
    if data.startswith(b"OTTO"):
        return "opentype_cff"
    if data.startswith(b"\x00\x01\x00\x00"):
        return "opentype_ttf"
    if data.startswith(b"SSEDDATA"):
        return "sseddata"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(b"RIFF"):
        return "riff"
    return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_table_summaries(db_path: Path) -> tuple[dict[str, Any], ...]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows: list[dict[str, Any]] = []
        for name, sql in con.execute("select name, sql from sqlite_master where type='table' order by name"):
            columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(name)})")]
            try:
                count = int(con.execute(f"select count(*) from {quote_identifier(name)}").fetchone()[0])
            except sqlite3.Error:
                count = None
            rows.append({"name": name, "rows": count, "columns": columns, "sql": sql})
        return tuple(rows)
    finally:
        con.close()


def _column_sets(tables: tuple[dict[str, Any], ...]) -> dict[str, set[str]]:
    return {
        str(table["name"]).lower(): {str(column).lower() for column in table.get("columns", [])}
        for table in tables
    }


def sqlite_role_for_tables(tables: tuple[dict[str, Any], ...]) -> str:
    """Return an evidence-backed role for a ``vlpljbl*`` SQLite payload."""

    columns = _column_sets(tables)
    names = set(columns)
    if not names:
        return "sqlite_empty"
    if names <= {"media", "t_media"}:
        return "sqlite_media_store"
    if names and all(name.startswith("t_search_") for name in names):
        return "sqlite_category_search_index"
    if names == {"t_index"}:
        return "sqlite_search_index"
    if "honbun" in names:
        honbun = columns["honbun"]
        if {"id", "title_utf8", "contents_html_box"} <= honbun:
            return "sqlite_row_ordered_honbun_renderer_body"
        if {"f_data_id", "f_honbun"} <= honbun or {"f_data_id", "f_contents"} <= honbun:
            return "sqlite_honbun_data_id_body"
        return "sqlite_honbun"
    if any({"block", "offset", "body"} <= cols for cols in columns.values()):
        return "sqlite_block_offset_body"
    if "t_contents" in names:
        cols = columns["t_contents"]
        body_columns = {
            "f_html",
            "f_plane",
            "f_contents",
            "f_media",
            "f_html_text",
            "f_plane_text",
            "f_body",
        }
        if cols & body_columns:
            if {"media", "t_media"} & names:
                return "sqlite_renderer_body_with_media"
            return "sqlite_renderer_body"
    if any({"block", "offset", "title"} <= cols for cols in columns.values()):
        return "sqlite_block_offset_title_index"
    if {"t_search", "t_zenbun"} & names:
        return "sqlite_search_or_fulltext"
    return "sqlite_unclassified"


def vlpljbl_role_for_content(content_kind: str, suffix: str, tables: tuple[dict[str, Any], ...]) -> str:
    if suffix in {".bin", ".exe"} and content_kind == "pe_executable":
        return "logofont_decryptor_binary"
    if content_kind in {"opentype_cff", "opentype_ttf"}:
        return "font"
    if content_kind == "sqlite":
        return sqlite_role_for_tables(tables)
    return content_kind


def classify_vlpljbl_file(path: Path, *, inspect_sqlite: bool = True, compute_hash: bool = True) -> VlpljblClassification:
    """Classify one ``vlpljbl*`` file by magic and optional SQLite schema."""

    prefix = path.read_bytes()[:4096]
    suffix = vlpljbl_suffix(path)
    plain_kind = file_magic_kind(prefix)
    storage = "plain"
    content_kind = plain_kind
    decrypt_prefix_kind: str | None = None
    if plain_kind == "unknown" and path.stat().st_size % 16 == 0:
        try:
            decrypted_prefix = decrypt_logofont_cipher_prefix(prefix, size=4096)
            decrypt_prefix_kind = file_magic_kind(decrypted_prefix)
            if decrypt_prefix_kind != "unknown":
                storage = "logofont_cipher"
                content_kind = decrypt_prefix_kind
        except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError) as exc:
            decrypt_prefix_kind = f"decrypt_error:{type(exc).__name__}"

    sqlite_tables: tuple[dict[str, Any], ...] = ()
    if inspect_sqlite and content_kind == "sqlite":
        if storage == "plain":
            sqlite_tables = sqlite_table_summaries(path)
        else:
            with tempfile.TemporaryDirectory(prefix="lv-vlpljbl-") as tmp:
                db_path = Path(tmp) / f"{path.name}.sqlite"
                decrypt_logofont_cipher_file_to_path(path, db_path)
                sqlite_tables = sqlite_table_summaries(db_path)

    return VlpljblClassification(
        path=path.resolve(),
        suffix=suffix,
        size=path.stat().st_size,
        sha256=sha256_file(path) if compute_hash else None,
        storage=storage,
        content_kind=content_kind,
        decrypt_prefix_kind=decrypt_prefix_kind,
        role=vlpljbl_role_for_content(content_kind, suffix, sqlite_tables),
        sqlite_tables=sqlite_tables,
    )


def vlpljbl_classification_to_json(row: VlpljblClassification) -> dict[str, Any]:
    return {
        "path": str(row.path),
        "dict_dir": row.path.parent.name,
        "name": row.path.name,
        "suffix": row.suffix,
        "size": row.size,
        "sha256": row.sha256,
        "storage": row.storage,
        "content_kind": row.content_kind,
        "decrypt_prefix_kind": row.decrypt_prefix_kind,
        "role": row.role,
        "sqlite_tables": list(row.sqlite_tables),
    }


def discover_vlpljbl_files(roots: list[Path]) -> list[Path]:
    if not roots:
        roots = [Path(".")]
    rows: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("vlpljbl*"))
        for candidate in candidates:
            if not is_vlpljbl_path(candidate):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rows.append(resolved)
    return rows


def sqlite_has_table(path: Path, table_name: str) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = con.execute(
            "select 1 from sqlite_master where type='table' and name=? limit 1",
            (table_name,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        con.close()
    return row is not None


def discover_renderer_sidecars(idx: Path, exinfo: Exinfo | None = None) -> list[RendererSidecar]:
    """Return sibling files that are plain/encrypted SQLite renderer payloads."""

    candidates: list[Path] = []
    if exinfo is not None:
        rosql = exinfo.general.get("ROSQLNAME")
        if rosql:
            candidate = idx.parent / rosql
            candidates.append(candidate)
    for child in sorted(idx.parent.iterdir()):
        lower = child.name.lower()
        if not child.is_file():
            continue
        if lower == "vlpljbl.bin":
            continue
        if lower.startswith("vlpljbl"):
            candidates.append(child)
        elif child.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            candidates.append(child)

    rows: list[RendererSidecar] = []
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        storage = sqlite_storage_for_path(candidate)
        if storage is None:
            continue
        if storage == "plain" and not (
            sqlite_has_table(candidate, "t_contents") or sqlite_has_table(candidate, "HONBUN")
        ):
            continue
        rows.append(RendererSidecar(path=candidate, storage=storage))
    return rows
