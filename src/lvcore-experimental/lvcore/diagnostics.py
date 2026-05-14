"""Recoverable diagnostics for lvcore reader operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from .json_types import JsonObject


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticArea(str, Enum):
    PACKAGE = "package"
    COMPONENT = "component"
    INDEX = "index"
    BODY = "body"
    OPCODE = "opcode"
    GAIJI = "gaiji"
    MEDIA = "media"
    RENDER = "render"
    VALIDATION = "validation"


class DiagnosticCode(str, Enum):
    BODY_POINTER_OUTSIDE_HONMON = "body_pointer_outside_honmon"
    BODY_POINTER_UNRESOLVED = "body_pointer_unresolved"
    DANGLING_CONTINUATION_ROW = "dangling_continuation_row"
    DANGLING_MULTI_TARGET_ROW = "dangling_multi_target_row"
    DENSE_ANCHOR_MISSING_ID = "dense_anchor_missing_id"
    EMPTY_BODY_AT_POINTER = "empty_body_at_pointer"
    ENTRY_RANGE_FALLBACK = "entry_range_fallback"
    INVALID_BODY_POINTER = "invalid_body_pointer"
    MALFORMED_DIRECT_LEAF_ROW = "malformed_direct_leaf_row"
    MALFORMED_GROUP_LEAF_ROW = "malformed_group_leaf_row"
    MALFORMED_SIMPLE_LEAF_ROW = "malformed_simple_leaf_row"
    MALFORMED_TARGET_LEAF_ROW = "malformed_target_leaf_row"
    MEDIA_LAYOUT_CONTROL = "media_layout_control"
    MISSING_BODY_COMPONENT = "missing_body_component"
    PARTIAL_INDEX_PAGE_TAIL = "partial_index_page_tail"
    PRIVATE_RENDERER_DIRECTIVE = "private_renderer_directive"
    SAMPLE_SEARCH_MISS = "sample_search_miss"
    SAMPLE_SEARCH_SKIPPED_EMPTY_QUERY = "sample_search_skipped_empty_query"
    SCAN_TRUNCATED = "scan_truncated"
    SIDECAR_BODY_NOT_FOUND = "sidecar_body_not_found"
    SIDECAR_BODY_RESOLVED = "sidecar_body_resolved"
    TAB_COLUMN_CONTROL = "tab_column_control"
    TITLE_DEREFERENCE_EMPTY = "title_dereference_empty"
    TITLE_DEREFERENCE_FAILED = "title_dereference_failed"
    UNKNOWN_BYTE = "unknown_byte"
    UNKNOWN_CONTROL = "unknown_control"
    UNKNOWN_LEAF_TAG = "unknown_leaf_tag"
    UNCLOSED_STYLE = "unclosed_style"
    UNMATCHED_STYLE_END = "unmatched_style_end"
    UNRESOLVED_GAIJI = "unresolved_gaiji"
    UNRESOLVED_LINK_TARGET = "unresolved_link_target"
    UNRESOLVED_MEDIA_REF = "unresolved_media_ref"
    UNSUPPORTED_BODY_SOURCE = "unsupported_body_source"
    UNSUPPORTED_COMPONENT_TYPE = "unsupported_component_type"
    UNSUPPORTED_SIDECAR_SCHEMA = "unsupported_sidecar_schema"


def diagnostic_code(value: DiagnosticCode | str) -> DiagnosticCode:
    return value if isinstance(value, DiagnosticCode) else DiagnosticCode(str(value))


@dataclass(frozen=True)
class Location:
    component: str | None = None
    block: int | None = None
    offset: int | None = None
    span_offset: int | None = None
    page: int | None = None
    row: int | None = None
    entry_id: str | None = None

    def to_dict(self) -> JsonObject:
        return {
            key: value
            for key, value in {
                "component": self.component,
                "block": self.block,
                "offset": self.offset,
                "span_offset": self.span_offset,
                "page": self.page,
                "row": self.row,
                "entry_id": self.entry_id,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    area: DiagnosticArea
    message: str
    code: DiagnosticCode
    location: Location = field(default_factory=Location)
    recoverable: bool = True
    details: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", diagnostic_code(self.code))

    def to_dict(self) -> JsonObject:
        return {
            "severity": self.severity.value,
            "area": self.area.value,
            "code": self.code.value,
            "message": self.message,
            "location": self.location.to_dict(),
            "recoverable": self.recoverable,
            "details": self.details,
        }


@dataclass
class DiagnosticBag:
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def add(
        self,
        severity: Severity,
        area: DiagnosticArea,
        code: DiagnosticCode | str,
        message: str,
        *,
        location: Location | None = None,
        recoverable: bool = True,
        details: JsonObject | None = None,
    ) -> Diagnostic:
        diagnostic = Diagnostic(
            severity=severity,
            area=area,
            code=code,
            message=message,
            location=location or Location(),
            recoverable=recoverable,
            details=details or {},
        )
        self.diagnostics.append(diagnostic)
        return diagnostic

    def counts_by_severity(self) -> dict[str, int]:
        counts = {severity.value: 0 for severity in Severity}
        for diagnostic in self.diagnostics:
            counts[diagnostic.severity.value] += 1
        return counts

    def counts_by_area(self) -> dict[str, int]:
        counts = {area.value: 0 for area in DiagnosticArea}
        for diagnostic in self.diagnostics:
            counts[diagnostic.area.value] += 1
        return {key: value for key, value in counts.items() if value}
