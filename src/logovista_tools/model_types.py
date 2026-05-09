"""Shared Decoded LogoVista Model v0 vocabulary.

The Python toolkit is still a research tool, but model vocabulary should not
drift between commands.  Keep package/body/component/status names here and make
commands normalize through these enums before emitting package-level reports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ModelEnum(str, Enum):
    """String enum that serializes cleanly to JSON."""

    def __str__(self) -> str:
        return self.value


class PackageFamily(ModelEnum):
    SSED = "ssed"
    SSED_SIZK_READ_ALOUD = "ssed-sizk-read-aloud"
    LVED_SQLCIPHER = "lved_sqlcipher"
    MULTIVIEW_SQLITE = "multiview_sqlite"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class PlatformWrapper(ModelEnum):
    WINDOWS = "windows"
    WINDOWS_SIZK = "windows-sizk"
    IOS = "ios"
    ANDROID = "android"
    LVED_WINDOWS = "lved-windows"
    MULTIVIEW_WINDOWS = "multiview-windows"
    UNKNOWN = "unknown"


class HonmonShape(ModelEnum):
    BODY_STREAM_INDEXED = "body_stream_indexed"
    BODY_STREAM_MARKER_SLICED = "body_stream_marker_sliced"
    MARKER_RICH_TEXT_STREAM = "marker_rich_text_stream"
    TEXT_STREAM_WITHOUT_ENTRY_MARKERS = "text_stream_without_entry_markers"
    DENSE_MARKER_TABLE = "dense_marker_table"
    DENSE_NUMERIC_ID_TABLE = "dense_numeric_id_table"
    DENSE_TOKEN_TABLE = "dense_token_table"
    INDEX_TARGETS_WITHOUT_SAMPLED_BODY = "index_targets_without_sampled_body"
    MARKER_TABLE_WITHOUT_SAMPLED_BODY = "marker_table_without_sampled_body"
    OPAQUE_OR_BINARY_HONMON = "opaque_or_binary_honmon"
    MISSING = "missing"
    UNKNOWN = "unknown"


class BodySource(ModelEnum):
    HONMON = "honmon"
    HONMON_ANCHOR_DEREFERENCE = "honmon_anchor_dereference"
    SIDECAR = "sidecar"
    DICTFULLDB = "dictfulldb"
    RENDERER_DB = "renderer_db"
    LVED_SQLCIPHER = "lved_sqlcipher"
    MULTIVIEW_SQLITE = "multiview_sqlite"
    NONE = "none"
    UNKNOWN = "unknown"


class ComponentRole(ModelEnum):
    HONMON = "honmon"
    TITLE = "title"
    INDEX = "index"
    MENU = "menu"
    MULTI_DESCRIPTOR = "multi_descriptor"
    TEXT = "text"
    COLSCR = "colscr"
    PCMDATA = "pcmdata"
    GAIJI_BITMAP = "gaiji_bitmap"
    COMPONENT = "component"
    UNKNOWN = "unknown"


class AddressKind(ModelEnum):
    COMPONENT = "component"
    BOOK = "book"
    DENSE_ANCHOR = "dense_anchor"
    DATABASE_ROW = "database_row"
    VIRTUAL_SELECTOR = "virtual_selector"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


class SpanKind(ModelEnum):
    TEXT = "text"
    ASCII = "ascii"
    CONTROL = "control"
    SECTION = "section"
    BREAK = "break"
    GAIJI = "gaiji"
    MEDIA_REF = "media_ref"
    PADDING = "padding"
    UNKNOWN_CONTROL = "unknown_control"
    PROBLEM = "problem"


class ControlConfidence(ModelEnum):
    PROVEN = "proven"
    STRONGLY_INFERRED = "strongly_inferred"
    CORPUS_INFERRED = "corpus_inferred"
    DICTIONARY_SPECIFIC = "dictionary_specific"
    STRUCTURAL_ONLY = "structural_only"
    UNKNOWN = "unknown"


class ReadinessStatus(ModelEnum):
    YES = "yes"
    PARTIAL = "partial"
    NO = "no"
    NA = "n/a"
    UNKNOWN = "unknown"


class WriterStatus(ModelEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    GRAY = "gray"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelAddress:
    kind: AddressKind
    component: str | None = None
    component_type: str | None = None
    block: int | None = None
    offset: int | None = None
    component_offset: int | None = None
    absolute_book_offset: int | None = None
    row_id: str | int | None = None
    selector: str | None = None

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        return {key: value for key, value in row.items() if value is not None}


@dataclass(frozen=True)
class StatusRecord:
    status: ReadinessStatus
    reason: str
    metrics: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"status": self.status.value, "reason": self.reason}
        if self.metrics:
            row["metrics"] = self.metrics
        return row


def enum_value(value: Any) -> str:
    if isinstance(value, ModelEnum):
        return value.value
    return str(value) if value is not None else ""


def normalize_enum(enum_type: type[ModelEnum], value: Any, default: ModelEnum) -> str:
    raw = enum_value(value)
    for item in enum_type:
        if item.value == raw:
            return item.value
    return default.value


def normalize_package_family(value: Any) -> str:
    return normalize_enum(PackageFamily, value, PackageFamily.UNKNOWN)


def normalize_platform(value: Any) -> str:
    return normalize_enum(PlatformWrapper, value, PlatformWrapper.UNKNOWN)


def normalize_honmon_shape(value: Any) -> str:
    return normalize_enum(HonmonShape, value, HonmonShape.UNKNOWN)


def normalize_body_source(value: Any) -> str:
    return normalize_enum(BodySource, value, BodySource.UNKNOWN)


def normalize_component_role(value: Any) -> str:
    return normalize_enum(ComponentRole, value, ComponentRole.UNKNOWN)


def normalize_readiness_status(value: Any) -> str:
    return normalize_enum(ReadinessStatus, value, ReadinessStatus.UNKNOWN)


def normalize_writer_status(value: Any) -> str:
    return normalize_enum(WriterStatus, value, WriterStatus.UNKNOWN)
