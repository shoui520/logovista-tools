"""Shared lvcore data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
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
        if not (self.start_block or self.end_block):
            return 0
        return max(0, self.end_block - self.start_block + 1)

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

    def to_debug_summary(self, *, max_preview_bytes: int = 32) -> dict[str, Any]:
        """Return bounded raw span details for explicit inspection output."""

        data: dict[str, Any] = {
            "kind": self.kind,
            "offset": self.offset,
            "length": self.length,
            "hidden": self.hidden,
        }
        if self.op is not None:
            data["op"] = f"{self.op:02x}"
        if self.code is not None:
            data["code"] = self.code
        if self.text is not None:
            data["text_length"] = len(self.text)
        if self.payload:
            data["payload_length"] = len(self.payload)
            data["payload_hash"] = hashlib.sha1(self.payload).hexdigest()[:12]
            data["payload_preview"] = self.payload[:max_preview_bytes].hex()
            data["payload_truncated"] = len(self.payload) > max_preview_bytes
        if self.raw:
            data["raw_length"] = len(self.raw)
            data["raw_hash"] = hashlib.sha1(self.raw).hexdigest()[:12]
            if self.kind != "text":
                data["raw_preview"] = self.raw[:max_preview_bytes].hex()
                data["raw_truncated"] = len(self.raw) > max_preview_bytes
        if self.attrs:
            data["attrs"] = self.attrs
        return data


@dataclass(frozen=True)
class Entry:
    address: Address
    end_address: Address
    headword: str | None
    text: str
    spans: tuple[Span, ...]
    entry_diagnostics: tuple[Any, ...] = ()
    decode_unknown_controls: int = 0
    decode_unknown_bytes: int = 0

    def document(self):
        from .document import build_entry_document

        return build_entry_document(self)

    def render_html(self, profile: str = "friendly", *, include_diagnostics: bool = False) -> str:
        if profile.replace("-", "_") == "debug":
            from .inspect import InspectorRenderer

            return InspectorRenderer().render_html(self.document())
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
            "decode_unknown_controls": self.decode_unknown_controls,
            "decode_unknown_bytes": self.decode_unknown_bytes,
            "span_summaries": [span.to_debug_summary() for span in self.spans],
            "diagnostics": [
                diagnostic.to_dict() if hasattr(diagnostic, "to_dict") else diagnostic
                for diagnostic in self.entry_diagnostics
            ],
        }

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "headword": self.headword,
            "text": self.text,
            "diagnostics": [
                diagnostic.to_dict() if hasattr(diagnostic, "to_dict") else diagnostic
                for diagnostic in self.entry_diagnostics
            ],
        }
        if not debug:
            return data
        data.update(
            {
                "address": self.address.to_dict(),
                "end_address": self.end_address.to_dict(),
                "decode_telemetry": {
                    "unknown_controls": self.decode_unknown_controls,
                    "unknown_bytes": self.decode_unknown_bytes,
                },
                "decode_unknown_controls": self.decode_unknown_controls,
                "decode_unknown_bytes": self.decode_unknown_bytes,
                "span_summaries": [span.to_debug_summary() for span in self.spans],
            }
        )
        return data
