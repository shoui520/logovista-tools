"""SSED body-source classification and sidecar body resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import html
import re
import sqlite3
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic
from .json_types import JsonObject
from .model import PackageFamily

BODY_SOURCE_SCHEMA = "lvcore.body_source.v1"
BODY_SOURCE_MODEL_VERSION = 1


class SsedBodySourceKind(str, Enum):
    BODY_STREAM = "body_stream"
    DENSE_ANCHOR_TABLE = "dense_anchor_table"
    DENSE_MARKER_TABLE = "dense_marker_table"
    DENSE_ANCHOR_WITH_SIDECAR = "dense_anchor_with_sidecar"
    RENDERER_SQLITE_SIDECAR = "renderer_sqlite_sidecar"
    DICTFULLDB_SIDECAR = "dictfulldb_sidecar"
    HONBUN_SIDECAR = "honbun_sidecar"
    VLPLJBL_SIDECAR = "vlpljbl_sidecar"
    SIDECAR_UNKNOWN = "sidecar_unknown"
    MISSING_BODY_COMPONENT = "missing_body_component"
    UNKNOWN = "unknown"


class BodySourceSupport(str, Enum):
    RENDERABLE = "renderable"
    PARTIALLY_RENDERABLE = "partially_renderable"
    DEFERRED = "deferred"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    PROVEN = "proven"
    INFERRED = "inferred"
    WEAK = "weak"
    UNKNOWN = "unknown"


class SidecarRole(str, Enum):
    BODY_CRITICAL = "body_critical"
    MEDIA_RESOURCE = "media_resource"
    EXAMPLES_IDIOMS = "examples_idioms"
    LINK_REFERENCE = "link_reference"
    SEARCH = "search"
    SUPPLEMENTAL = "supplemental"
    KANJI_SUPPORT = "kanji_support"
    ANCILLARY = "ancillary"
    NON_SQLITE_OR_UNKNOWN = "non_sqlite_or_unknown"
    UNKNOWN = "unknown"


class SidecarSupportStatus(str, Enum):
    BODY_RESOLVER = "body_resolver"
    SUPPLEMENT_RESOLVER = "supplement_resolver"
    RESOURCE_RESOLVER = "resource_resolver"
    SEARCH_METADATA = "search_metadata"
    SCHEMA_CLASSIFIED = "schema_classified"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    NON_SQLITE_OR_UNKNOWN = "non_sqlite_or_unknown"


@dataclass(frozen=True)
class SidecarTableInfo:
    table: str
    columns: tuple[str, ...] = ()
    row_count: int | None = None
    role: SidecarRole | str = SidecarRole.UNKNOWN
    id_column: str | None = None
    title_column: str | None = None
    html_column: str | None = None
    plain_column: str | None = None
    blob_column: str | None = None
    name_column: str | None = None
    block_column: str | None = None
    offset_column: str | None = None
    end_block_column: str | None = None
    end_offset_column: str | None = None

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        role = self.role.value if isinstance(self.role, SidecarRole) else str(self.role)
        data: JsonObject = {
            "table": self.table,
            "row_count": self.row_count,
            "role": role,
            "has_body_text": bool(self.html_column or self.plain_column),
            "has_blob": bool(self.blob_column),
            "has_address_mapping": bool(self.block_column and self.offset_column),
        }
        if debug:
            data.update(
                {
                    "columns": list(self.columns),
                    "id_column": self.id_column,
                    "title_column": self.title_column,
                    "html_column": self.html_column,
                    "plain_column": self.plain_column,
                    "blob_column": self.blob_column,
                    "name_column": self.name_column,
                    "block_column": self.block_column,
                    "offset_column": self.offset_column,
                    "end_block_column": self.end_block_column,
                    "end_offset_column": self.end_offset_column,
                }
            )
        return data


@dataclass(frozen=True)
class SidecarInfo:
    path: Path
    kind: str
    storage: str
    role: SidecarRole | str = SidecarRole.UNKNOWN
    support_status: SidecarSupportStatus | str = SidecarSupportStatus.UNSUPPORTED_SCHEMA
    table: str | None = None
    id_column: str | None = None
    title_column: str | None = None
    html_column: str | None = None
    plain_column: str | None = None
    blob_column: str | None = None
    name_column: str | None = None
    row_count: int | None = None
    tables: tuple[SidecarTableInfo, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        support_status = self.support_status.value if isinstance(self.support_status, SidecarSupportStatus) else str(self.support_status)
        data = {
            "path": str(self.path),
            "name": self.path.name,
            "kind": self.kind,
            "storage": self.storage,
            "role": self.role.value if isinstance(self.role, SidecarRole) else str(self.role),
            "support_status": support_status,
            "table": self.table,
            "id_column": self.id_column,
            "title_column": self.title_column,
            "html_column": self.html_column,
            "plain_column": self.plain_column,
            "blob_column": self.blob_column,
            "name_column": self.name_column,
            "row_count": self.row_count,
            "tables": [table.to_dict(debug=debug) for table in self.tables],
            "notes": list(self.notes),
        }
        if not debug:
            data.pop("path", None)
        return data


BODY_SIDECAR_KIND_ORDER = {
    "t_contents": 0,
    "honbun": 1,
    "main_wordlist": 2,
    "sqlite_body": 3,
}


def is_renderable_body_sidecar(sidecar: SidecarInfo) -> bool:
    return bool(sidecar.table and sidecar.id_column and (sidecar.html_column or sidecar.plain_column))


def body_sidecar_candidates(sidecars: tuple[SidecarInfo, ...], *, kind: str | None = None) -> tuple[SidecarInfo, ...]:
    candidates = [
        sidecar
        for sidecar in sidecars
        if is_renderable_body_sidecar(sidecar) and (kind is None or sidecar.kind == kind)
    ]
    return tuple(
        sorted(
            candidates,
            key=lambda sidecar: (
                BODY_SIDECAR_KIND_ORDER.get(sidecar.kind, 100),
                (sidecar.table or "").lower(),
                sidecar.path.name.lower(),
            ),
        )
    )


def select_body_sidecar(sidecars: tuple[SidecarInfo, ...], *, kind: str | None = None) -> SidecarInfo | None:
    candidates = body_sidecar_candidates(sidecars, kind=kind)
    return candidates[0] if candidates else None


def body_source_kind_for_sidecar(sidecar: SidecarInfo) -> SsedBodySourceKind:
    if sidecar.kind == "honbun":
        return SsedBodySourceKind.HONBUN_SIDECAR
    if sidecar.kind == "t_contents" and sidecar.storage == "logofont_cipher":
        return SsedBodySourceKind.RENDERER_SQLITE_SIDECAR
    return SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR


@dataclass(frozen=True)
class SidecarAddressMatch:
    sidecar_name: str
    kind: str
    role: SidecarRole | str
    support_status: SidecarSupportStatus | str
    table: str
    match_count: int
    status: str = "matched"
    block_column: str | None = None
    offset_column: str | None = None
    title_column: str | None = None
    plain_column: str | None = None

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        role = self.role.value if isinstance(self.role, SidecarRole) else str(self.role)
        support_status = self.support_status.value if isinstance(self.support_status, SidecarSupportStatus) else str(self.support_status)
        data: JsonObject = {
            "sidecar": self.sidecar_name,
            "kind": self.kind,
            "role": role,
            "support_status": support_status,
            "table": self.table,
            "match_count": self.match_count,
            "status": self.status,
        }
        if debug:
            data.update(
                {
                    "block_column": self.block_column,
                    "offset_column": self.offset_column,
                    "title_column": self.title_column,
                    "plain_column": self.plain_column,
                }
            )
        return data


@dataclass(frozen=True)
class BodySourceInfo:
    package_family: PackageFamily
    ssed_kind: SsedBodySourceKind = SsedBodySourceKind.UNKNOWN
    support: BodySourceSupport = BodySourceSupport.UNKNOWN
    confidence: Confidence = Confidence.UNKNOWN
    honmon_component: str | None = None
    expanded_size: int | None = None
    marker_count: int | None = None
    marker_density: float | None = None
    dense_record_size: int | None = None
    anchor_count: int | None = None
    sidecar_paths: tuple[Path, ...] = ()
    sidecar_kind: str | None = None
    render_provider: str | None = None
    notes: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    sidecars: tuple[SidecarInfo, ...] = ()

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data = {
            "schema": BODY_SOURCE_SCHEMA,
            "model_version": BODY_SOURCE_MODEL_VERSION,
            "package_family": self.package_family.value,
            "ssed_kind": self.ssed_kind.value,
            "support": self.support.value,
            "confidence": self.confidence.value,
            "honmon_component": self.honmon_component,
            "expanded_size": self.expanded_size,
            "marker_count": self.marker_count,
            "marker_density": self.marker_density,
            "dense_record_size": self.dense_record_size,
            "anchor_count": self.anchor_count,
            "sidecar_paths": [str(path) for path in self.sidecar_paths] if debug else [path.name for path in self.sidecar_paths],
            "sidecar_kind": self.sidecar_kind,
            "render_provider": self.render_provider,
            "notes": list(self.notes),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "sidecars": [sidecar.to_dict(debug=debug) for sidecar in self.sidecars],
        }
        return data


@dataclass(frozen=True)
class BodyPointerInspection:
    anchor_id: str | None = None
    raw_text_hash: str | None = None
    raw_text_length: int = 0
    record_offset: int | None = None
    record_length: int | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data = {
            "anchor_id": self.anchor_id if debug else None,
            "raw_text_hash": self.raw_text_hash,
            "raw_text_length": self.raw_text_length,
            "record_offset": self.record_offset,
            "record_length": self.record_length,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if not debug:
            data.pop("anchor_id", None)
        return data


@dataclass(frozen=True)
class SidecarBody:
    title: str
    text: str
    html: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    source: SidecarInfo | None = None


@dataclass(frozen=True)
class BodyResolution:
    entry: Any
    inspection: BodyPointerInspection | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


SQLITE_MAGIC = b"SQLite format 3\x00"
HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    return html.unescape(HTML_TAG_RE.sub("", value or "")).strip()


def quote_sql_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"pragma table_info({quote_sql_identifier(table)})")]


def find_column(columns: list[str], *candidates: str) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found is not None:
            return found
    return None


def compatibility_significant_sidecar_role(role: SidecarRole | str) -> bool:
    value = role.value if isinstance(role, SidecarRole) else str(role)
    return value in {
        SidecarRole.BODY_CRITICAL.value,
        SidecarRole.MEDIA_RESOURCE.value,
        SidecarRole.EXAMPLES_IDIOMS.value,
        SidecarRole.LINK_REFERENCE.value,
        SidecarRole.SEARCH.value,
        SidecarRole.SUPPLEMENTAL.value,
        SidecarRole.UNKNOWN.value,
    }


def _columns_for_table(columns_by_table: dict[str, list[str]] | None, table: str) -> set[str]:
    if not columns_by_table:
        return set()
    return {column.lower() for column in columns_by_table.get(table, [])}


def _has_any(columns: set[str], *candidates: str) -> bool:
    return any(candidate.lower() in columns for candidate in candidates)


ID_COLUMN_ALIASES = (
    "ID",
    "No",
    "ItemID",
    "ItemId",
    "DataID",
    "DataId",
    "ContentID",
    "ContentId",
    "contents_id",
    "content_id",
    "row_id",
    "f_DataId",
    "f_dataid",
    "f_data_id",
    "f_array_no",
    "f_contents_id",
    "f_order_id",
    "id",
    "index",
)
TITLE_COLUMN_ALIASES = (
    "Title",
    "Heading",
    "Headword",
    "Label",
    "TitleJIS",
    "JIS_Title",
    "Title_UTF8",
    "Title_SJIS",
    "f_Title",
    "f_title",
    "Keyword",
    "Midashi",
    "MidashiJ",
    "f_midasi",
    "f_midashi",
    "f_midashi_hyoki",
    "f_midashi_key",
    "f_abbr",
    "f_fullname",
    "C_text",
    "K_text",
    "J_text",
)
HTML_COLUMN_ALIASES = (
    "HTML",
    "Html",
    "body_html",
    "html_body",
    "content_html",
    "Contents_HTML_box",
    "Contents_HTML_list",
    "f_Html",
    "f_html",
    "f_html_text",
    "f_contents",
)
PLAIN_COLUMN_ALIASES = (
    "Text",
    "Plain",
    "Body",
    "body_text",
    "plain_text",
    "content_text",
    "f_body",
    "f_Plane",
    "f_plane",
    "f_plain",
    "f_plane_text",
    "h_text",
    "Value",
    "J_text",
    "C_text",
    "K_text",
    "Pinyin",
    "data",
)
BLOB_COLUMN_ALIASES = (
    "Blob",
    "blob",
    "BLOB",
    "payload",
    "payload_blob",
    "resource_blob",
    "image_blob",
    "media_blob",
    "f_blob",
    "f_main",
    "main",
)
NAME_COLUMN_ALIASES = (
    "Name",
    "name",
    "Filename",
    "FileName",
    "file_name",
    "Path",
    "path",
    "asset_name",
    "resource_name",
    "f_name",
)
BLOCK_COLUMN_ALIASES = ("Block", "Block_s", "StartBlock", "start_block", "f_block")
OFFSET_COLUMN_ALIASES = ("Offset", "Offset_s", "StartOffset", "start_offset", "f_offset")
END_BLOCK_COLUMN_ALIASES = ("Block_e", "EndBlock", "end_block", "f_end_block")
END_OFFSET_COLUMN_ALIASES = ("Offset_e", "EndOffset", "end_offset", "f_end_offset")
SUPPLEMENT_TEXT_ALIASES = (
    "Keyword",
    "Midashi",
    "MidashiJ",
    "Example",
    "Idiom",
    "Usage",
    "Title",
    "Body",
    "h_text",
)
SEARCH_TYPE_ALIASES = ("f_type", "type", "search_type", "category")


def _find_column(columns: list[str] | tuple[str, ...], *candidates: str) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found is not None:
            return found
    return None


def resolve_sqlite_sidecar_columns(columns: list[str] | tuple[str, ...]) -> dict[str, str | None]:
    """Return canonical column capabilities for a LogoVista SQLite sidecar table.

    The reader should generally care about capabilities (id/body/blob/address)
    rather than product-specific table names. The aliases here are conservative:
    they only describe columns that existing corpus evidence has shown to carry
    those roles or obvious spelling variants of those roles.
    """

    return {
        "id": _find_column(columns, *ID_COLUMN_ALIASES),
        "title": _find_column(columns, *TITLE_COLUMN_ALIASES),
        "html": _find_column(columns, *HTML_COLUMN_ALIASES),
        "plain": _find_column(columns, *PLAIN_COLUMN_ALIASES),
        "blob": _find_column(columns, *BLOB_COLUMN_ALIASES),
        "name": _find_column(columns, *NAME_COLUMN_ALIASES),
        "block": _find_column(columns, *BLOCK_COLUMN_ALIASES),
        "offset": _find_column(columns, *OFFSET_COLUMN_ALIASES),
        "end_block": _find_column(columns, *END_BLOCK_COLUMN_ALIASES),
        "end_offset": _find_column(columns, *END_OFFSET_COLUMN_ALIASES),
        "search_type": _find_column(columns, *SEARCH_TYPE_ALIASES),
    }


def classify_sqlite_table_role(table: str, columns: list[str] | tuple[str, ...]) -> SidecarRole:
    lower_table = table.lower()
    lowered = {column.lower() for column in columns}
    resolved = resolve_sqlite_sidecar_columns(columns)
    has_id = bool(resolved["id"])
    has_title = bool(resolved["title"])
    has_body_text = bool(resolved["html"] or resolved["plain"])
    has_blob = bool(resolved["blob"])
    has_name = bool(resolved["name"])
    has_address = bool(resolved["block"] and resolved["offset"])
    has_search_type = bool(resolved["search_type"])
    has_supplement_markers = _has_any(lowered, *SUPPLEMENT_TEXT_ALIASES)

    if lowered == {"index", "data"}:
        return SidecarRole.ANCILLARY
    if "chronology" in lower_table:
        return SidecarRole.ANCILLARY
    if lower_table in {"t_all", "t_bushu", "t_jukugo", "t_yomi", "t_exam"}:
        return SidecarRole.KANJI_SUPPORT
    if has_blob and (has_name or has_id):
        return SidecarRole.MEDIA_RESOURCE
    if has_id and has_body_text and not has_address:
        return SidecarRole.BODY_CRITICAL
    if has_address and has_search_type and has_title:
        return SidecarRole.SEARCH
    if has_address and has_supplement_markers and _has_any(lowered, "keyword", "midashi", "midashij", "example", "idiom", "usage"):
        return SidecarRole.EXAMPLES_IDIOMS
    if has_address and (has_title or has_body_text or has_supplement_markers):
        return SidecarRole.LINK_REFERENCE
    if has_id and has_title:
        return SidecarRole.SEARCH
    if lower_table.startswith("d_") and has_supplement_markers:
        return SidecarRole.SUPPLEMENTAL
    return SidecarRole.UNKNOWN


def _legacy_name_role(kind: str, lowered: set[str], columns_by_table: dict[str, list[str]] | None) -> SidecarRole:
    if kind in {"honbun", "main_wordlist", "t_contents", "sqlite_body"}:
        return SidecarRole.BODY_CRITICAL
    if lowered & {"media", "t_media"}:
        return SidecarRole.MEDIA_RESOURCE
    if lowered & {"d_example", "d_idiom"}:
        return SidecarRole.EXAMPLES_IDIOMS
    if "t_index" in lowered or any("search" in table or "zenbun" in table for table in lowered):
        return SidecarRole.SEARCH
    if lowered == {"t_data"}:
        columns = _columns_for_table(columns_by_table, "t_data")
        if _has_any(columns, "index") and _has_any(columns, "data"):
            return SidecarRole.ANCILLARY
    if lowered & {"t_all", "t_bushu", "t_jukugo", "t_yomi", "t_exam"}:
        return SidecarRole.KANJI_SUPPORT
    if any("chronology" in table for table in lowered):
        return SidecarRole.ANCILLARY
    if any(table.startswith("d_") for table in lowered):
        return SidecarRole.SUPPLEMENTAL
    return SidecarRole.UNKNOWN


def classify_sqlite_sidecar_role(
    kind: str,
    tables: list[str] | tuple[str, ...],
    columns_by_table: dict[str, list[str]] | None = None,
) -> SidecarRole:
    lowered = {table.lower() for table in tables}
    if kind != "sqlite_unmapped":
        legacy = _legacy_name_role(kind, lowered, columns_by_table)
        if legacy != SidecarRole.UNKNOWN:
            return legacy
    table_roles: list[SidecarRole] = []
    if columns_by_table:
        for table in tables:
            table_roles.append(classify_sqlite_table_role(table, columns_by_table.get(table, ())))
    for role in (
        SidecarRole.BODY_CRITICAL,
        SidecarRole.MEDIA_RESOURCE,
        SidecarRole.EXAMPLES_IDIOMS,
        SidecarRole.SEARCH,
        SidecarRole.LINK_REFERENCE,
        SidecarRole.KANJI_SUPPORT,
        SidecarRole.SUPPLEMENTAL,
    ):
        if role in table_roles:
            return role
    if table_roles and all(role == SidecarRole.ANCILLARY for role in table_roles):
        return SidecarRole.ANCILLARY
    return _legacy_name_role(kind, lowered, columns_by_table)
