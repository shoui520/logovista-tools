"""Shared lvcore data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PackageFamily(str, Enum):
    SSED = "ssed"
    LVED = "lved_sqlcipher"
    LVLMULTI = "multiview_sqlite"
    UNKNOWN = "unknown"


class ComponentRole(str, Enum):
    HONMON = "honmon"
    TITLE = "title"
    INDEX = "index"
    MENU = "menu"
    GAIJI = "gaiji"
    MEDIA = "media"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


class SearchProfile(str, Enum):
    NATIVE = "native"
    EXACT = "exact"
    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass(frozen=True, order=True)
class Address:
    """Logical SSED address."""

    block: int
    offset: int = 0
    component: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"block": self.block, "offset": self.offset}
        if self.component is not None:
            out["component"] = self.component
        return out


@dataclass(frozen=True)
class Component:
    name: str
    type: int
    start_block: int
    end_block: int
    data: bytes = b""
    index: int = 0
    multi: int = 0
    role: ComponentRole = ComponentRole.UNKNOWN
    path: Path | None = None

    @property
    def block_count(self) -> int:
        return self.end_block - self.start_block + 1 if self.start_block or self.end_block else 0

    def contains(self, address: Address) -> bool:
        return self.start_block <= address.block <= self.end_block

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "type": f"{self.type:02x}",
            "role": self.role.value,
            "start_block": self.start_block,
            "end_block": self.end_block,
            "blocks": self.block_count,
            "data": self.data.hex(),
            "path": str(self.path) if self.path else None,
        }


@dataclass(frozen=True)
class PackageInfo:
    family: PackageFamily
    root: Path
    idx_path: Path | None = None
    dict_id: str | None = None
    title: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "root": str(self.root),
            "idx_path": str(self.idx_path) if self.idx_path else None,
            "dict_id": self.dict_id,
            "title": self.title,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class Span:
    kind: str
    text: str | None = None
    raw: bytes = b""
    offset: int = 0
    length: int = 0
    op: int | None = None
    payload: bytes = b""
    code: str | None = None
    hidden: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "raw": self.raw.hex(),
            "offset": self.offset,
            "length": self.length,
            "op": f"{self.op:02x}" if self.op is not None else None,
            "payload": self.payload.hex(),
            "code": self.code,
            "hidden": self.hidden,
            "attrs": self.attrs,
        }


@dataclass(frozen=True)
class Entry:
    address: Address
    end_address: Address
    headword: str
    text: str
    spans: tuple[Span, ...]
    entry_diagnostics: tuple[Any, ...] = ()

    def document(self):
        from .document import build_entry_document

        return build_entry_document(self)

    def render_html(self, profile: str = "friendly", *, include_diagnostics: bool = False) -> str:
        from .render import HtmlProfile, render_html

        return render_html(self.document(), profile=HtmlProfile(profile.replace("-", "_")), include_diagnostics=include_diagnostics)

    def html(self) -> str:
        return self.render_html()

    def plain_text(self) -> str:
        from .render import render_text

        return render_text(self.document())

    def diagnostics(self):
        return self.document().diagnostics

    def inspect(self) -> dict[str, Any]:
        return {
            "address": self.address.to_dict(),
            "end_address": self.end_address.to_dict(),
            "raw_spans": [span.to_dict() for span in self.spans],
            "diagnostics": [
                diagnostic.to_dict() if hasattr(diagnostic, "to_dict") else diagnostic
                for diagnostic in self.entry_diagnostics
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address.to_dict(),
            "end_address": self.end_address.to_dict(),
            "headword": self.headword,
            "text": self.text,
            "spans": [span.to_dict() for span in self.spans],
            "diagnostics": [
                diagnostic.to_dict() if hasattr(diagnostic, "to_dict") else diagnostic
                for diagnostic in self.entry_diagnostics
            ],
        }
