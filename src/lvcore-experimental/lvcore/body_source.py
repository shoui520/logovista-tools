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
from .model import PackageFamily


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
    SEARCH = "search"
    KANJI_SUPPORT = "kanji_support"
    ANCILLARY = "ancillary"
    NON_SQLITE_OR_UNKNOWN = "non_sqlite_or_unknown"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SidecarInfo:
    path: Path
    kind: str
    storage: str
    role: SidecarRole | str = SidecarRole.UNKNOWN
    table: str | None = None
    id_column: str | None = None
    title_column: str | None = None
    html_column: str | None = None
    plain_column: str | None = None
    row_count: int | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data = {
            "path": str(self.path),
            "name": self.path.name,
            "kind": self.kind,
            "storage": self.storage,
            "role": self.role.value if isinstance(self.role, SidecarRole) else str(self.role),
            "table": self.table,
            "id_column": self.id_column,
            "title_column": self.title_column,
            "html_column": self.html_column,
            "plain_column": self.plain_column,
            "row_count": self.row_count,
            "notes": list(self.notes),
        }
        if not debug:
            data.pop("path", None)
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

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data = {
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

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
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


def sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f'pragma table_info("{table}")')]


def find_column(columns: list[str], *candidates: str) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found is not None:
            return found
    return None


def classify_sqlite_sidecar_role(kind: str, tables: list[str] | tuple[str, ...]) -> SidecarRole:
    lowered = {table.lower() for table in tables}
    if kind in {"honbun", "main_wordlist", "t_contents"}:
        return SidecarRole.BODY_CRITICAL
    if kind == "sqlite_unmapped":
        if lowered & {"media", "t_media"}:
            return SidecarRole.MEDIA_RESOURCE
        if lowered & {"d_example", "d_idiom"}:
            return SidecarRole.EXAMPLES_IDIOMS
        if any("search" in table or "zenbun" in table for table in lowered):
            return SidecarRole.SEARCH
        if lowered & {"t_all", "t_bushu", "t_jukugo", "t_yomi", "t_exam"}:
            return SidecarRole.KANJI_SUPPORT
        if any("chronology" in table or table.startswith("d_") for table in lowered):
            return SidecarRole.ANCILLARY
    return SidecarRole.UNKNOWN
