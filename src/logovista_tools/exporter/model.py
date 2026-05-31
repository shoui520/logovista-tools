"""Target-neutral export model for LogoVista dictionaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class SourceFamily(str, Enum):
    AUTO = "auto"
    SSED = "ssed"
    LVED_SQLITE3 = "lved-sqlite3"


DiagnosticSeverity = Literal["info", "warning", "error"]
BodyEventKind = Literal[
    "text",
    "line_break",
    "style_start",
    "style_end",
    "ruby",
    "gaiji_ref",
    "link",
    "media_ref",
    "section",
    "unsupported",
]


@dataclass(frozen=True)
class ExportDiagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportBodyEvent:
    kind: BodyEventKind
    text: str = ""
    value: str = ""
    attrs: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def text_event(value: str) -> "ExportBodyEvent":
        return ExportBodyEvent(kind="text", text=value)

    @staticmethod
    def line_break() -> "ExportBodyEvent":
        return ExportBodyEvent(kind="line_break")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportBody:
    events: tuple[ExportBodyEvent, ...]
    source_kind: str
    diagnostics: tuple[ExportDiagnostic, ...] = ()

    @staticmethod
    def text(value: str, *, source_kind: str = "plain_text") -> "ExportBody":
        parts: list[ExportBodyEvent] = []
        lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, line in enumerate(lines):
            if index:
                parts.append(ExportBodyEvent.line_break())
            if line:
                parts.append(ExportBodyEvent.text_event(line))
        return ExportBody(events=tuple(parts), source_kind=source_kind)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "events": [event.as_dict() for event in self.events],
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True)
class ExportEntry:
    target: str
    headword: str
    body: ExportBody
    search_keys: tuple[str, ...] = ()
    readings: tuple[str, ...] = ()
    diagnostics: tuple[ExportDiagnostic, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return self.search_keys or ((self.headword,) if self.headword else ())

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "headword": self.headword,
            "search_keys": list(self.search_keys),
            "readings": list(self.readings),
            "body": self.body.as_dict(),
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True)
class ExportIndex:
    mode: str
    key: str
    target: str
    label: str = ""
    source_component: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExportNavigation:
    kind: str
    label: str
    target: str | None = None
    children: tuple["ExportNavigation", ...] = ()
    body: ExportBody | None = None
    diagnostics: tuple[ExportDiagnostic, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "target": self.target,
            "children": [child.as_dict() for child in self.children],
            "body": self.body.as_dict() if self.body is not None else None,
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True)
class ExportResource:
    resource_id: str
    kind: str
    source_path: str | None = None
    payload: bytes | None = None
    mime_type: str | None = None
    role: str = "unknown"
    diagnostics: tuple[ExportDiagnostic, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "source_path": self.source_path,
            "payload_bytes": len(self.payload) if self.payload is not None else None,
            "mime_type": self.mime_type,
            "role": self.role,
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True)
class ExportGaiji:
    code: str
    space: str
    unicode: str | None = None
    template_resource: str | None = None
    bitmap: bytes | None = None
    bitmap_width: int | None = None
    bitmap_height: int | None = None
    bitmap_source: str | None = None
    bitmap_backed: bool = False
    nonliteral_marker: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        bitmap = data.pop("bitmap", None)
        data["bitmap_bytes"] = len(bitmap) if bitmap is not None else None
        return data


@dataclass(frozen=True)
class ExportBook:
    title: str
    source_family: SourceFamily
    source_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    entries: tuple[ExportEntry, ...] = ()
    indexes: tuple[ExportIndex, ...] = ()
    navigation: tuple[ExportNavigation, ...] = ()
    resources: tuple[ExportResource, ...] = ()
    gaiji: tuple[ExportGaiji, ...] = ()
    copyright: tuple[ExportNavigation, ...] = ()
    diagnostics: tuple[ExportDiagnostic, ...] = ()

    def entry_by_target(self) -> dict[str, ExportEntry]:
        return {entry.target: entry for entry in self.entries}

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source_family": self.source_family.value,
            "source_id": self.source_id,
            "metadata": self.metadata,
            "entries": [entry.as_dict() for entry in self.entries],
            "indexes": [index.as_dict() for index in self.indexes],
            "navigation": [node.as_dict() for node in self.navigation],
            "resources": [resource.as_dict() for resource in self.resources],
            "gaiji": [gaiji.as_dict() for gaiji in self.gaiji],
            "copyright": [node.as_dict() for node in self.copyright],
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }
