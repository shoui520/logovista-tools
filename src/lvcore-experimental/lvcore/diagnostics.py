"""Recoverable diagnostics for lvcore reader operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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


@dataclass(frozen=True)
class Location:
    component: str | None = None
    block: int | None = None
    offset: int | None = None
    span_offset: int | None = None
    page: int | None = None
    row: int | None = None
    entry_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
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
    code: str
    location: Location = field(default_factory=Location)
    recoverable: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "area": self.area.value,
            "code": self.code,
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
        code: str,
        message: str,
        *,
        location: Location | None = None,
        recoverable: bool = True,
        details: dict[str, Any] | None = None,
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
