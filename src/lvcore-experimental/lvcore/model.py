"""Shared lvcore data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
from typing import Any

from .json_types import JsonObject

SPAN_SCHEMA = "lvcore.span.v1"
SPAN_DEBUG_SCHEMA = "lvcore.span_debug.v1"
ENTRY_SCHEMA = "lvcore.entry.v1"
SPAN_MODEL_VERSION = 1
SPAN_DEBUG_MODEL_VERSION = 1
ENTRY_MODEL_VERSION = 1


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

    def to_dict(self) -> JsonObject:
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

    def to_dict(self) -> JsonObject:
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

    def to_dict(self) -> JsonObject:
        return {
            "family": self.family.value,
            "root": str(self.root),
            "idx_path": str(self.idx_path) if self.idx_path else None,
            "dict_id": self.dict_id,
            "title": self.title,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SpanDebug:
    span_id: int
    raw: bytes = b""
    payload: bytes = b""
    op: int | None = None
    code: str | None = None
    attrs: JsonObject = field(default_factory=dict)

    def to_dict(self, *, max_preview_bytes: int = 32) -> JsonObject:
        data: JsonObject = {
            "schema": SPAN_DEBUG_SCHEMA,
            "model_version": SPAN_DEBUG_MODEL_VERSION,
            "span_id": self.span_id,
        }
        if self.op is not None:
            data["op"] = f"{self.op:02x}"
        if self.code is not None:
            data["code"] = self.code
        if self.payload:
            data["payload_length"] = len(self.payload)
            data["payload_hash"] = hashlib.sha1(self.payload).hexdigest()[:12]
            data["payload_preview"] = self.payload[:max_preview_bytes].hex()
            data["payload_truncated"] = len(self.payload) > max_preview_bytes
        if self.raw:
            data["raw_length"] = len(self.raw)
            data["raw_hash"] = hashlib.sha1(self.raw).hexdigest()[:12]
            data["raw_preview"] = self.raw[:max_preview_bytes].hex()
            data["raw_truncated"] = len(self.raw) > max_preview_bytes
        if self.attrs:
            data["attrs"] = self.attrs
        return data


@dataclass(init=False)
class Span:
    kind: str
    text: str | None = None
    offset: int = 0
    length: int = 0
    hidden: bool = False
    span_id: int = 0
    debug: SpanDebug = field(default_factory=lambda: SpanDebug(0), repr=False, compare=False)

    def __init__(
        self,
        kind: str,
        text: str | None = None,
        raw: bytes = b"",
        offset: int = 0,
        length: int = 0,
        op: int | None = None,
        payload: bytes = b"",
        code: str | None = None,
        hidden: bool = False,
        attrs: JsonObject | None = None,
        span_id: int | None = None,
        debug: SpanDebug | None = None,
    ) -> None:
        resolved_span_id = offset if span_id is None else span_id
        self.kind = kind
        self.text = text
        self.offset = offset
        self.length = length
        self.hidden = hidden
        self.span_id = resolved_span_id
        self.debug = debug or SpanDebug(
            span_id=resolved_span_id,
            raw=raw,
            payload=payload,
            op=op,
            code=code,
            attrs=dict(attrs or {}),
        )

    def with_debug_attrs(self, attrs: JsonObject) -> "Span":
        return Span(
            kind=self.kind,
            text=self.text,
            raw=self.debug.raw,
            offset=self.offset,
            length=self.length,
            op=self.debug.op,
            payload=self.debug.payload,
            code=self.debug.code,
            hidden=self.hidden,
            attrs=attrs,
            span_id=self.span_id,
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema": SPAN_SCHEMA,
            "model_version": SPAN_MODEL_VERSION,
            "kind": self.kind,
            "text": self.text,
            "offset": self.offset,
            "length": self.length,
            "hidden": self.hidden,
        }

    def to_debug_summary(self, *, max_preview_bytes: int = 32) -> JsonObject:
        """Return bounded raw span details for explicit inspection output."""

        data = self.debug.to_dict(max_preview_bytes=max_preview_bytes)
        data.update(
            {
            "kind": self.kind,
            "offset": self.offset,
            "length": self.length,
            "hidden": self.hidden,
            }
        )
        if self.text is not None:
            data["text_length"] = len(self.text)
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
    decode_invalid_jis_pairs: int = 0

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

    def inspect(self) -> JsonObject:
        return {
            "address": self.address.to_dict(),
            "end_address": self.end_address.to_dict(),
            "decode_unknown_controls": self.decode_unknown_controls,
            "decode_unknown_bytes": self.decode_unknown_bytes,
            "decode_invalid_jis_pairs": self.decode_invalid_jis_pairs,
            "span_summaries": [span.to_debug_summary() for span in self.spans],
            "diagnostics": [
                diagnostic.to_dict() if hasattr(diagnostic, "to_dict") else diagnostic
                for diagnostic in self.entry_diagnostics
            ],
        }

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": ENTRY_SCHEMA,
            "model_version": ENTRY_MODEL_VERSION,
            "headword": self.headword,
            "text": self.text,
            "diagnostics": [
                diagnostic.to_dict() if hasattr(diagnostic, "to_dict") else diagnostic
                for diagnostic in self.entry_diagnostics
            ],
        }
        if not debug:
            return data
        decode_telemetry: JsonObject = {
            "unknown_controls": self.decode_unknown_controls,
            "unknown_bytes": self.decode_unknown_bytes,
        }
        if self.decode_invalid_jis_pairs:
            decode_telemetry["invalid_jis_pairs"] = self.decode_invalid_jis_pairs
        data.update(
            {
                "address": self.address.to_dict(),
                "end_address": self.end_address.to_dict(),
                "decode_telemetry": decode_telemetry,
                "decode_unknown_controls": self.decode_unknown_controls,
                "decode_unknown_bytes": self.decode_unknown_bytes,
                "decode_invalid_jis_pairs": self.decode_invalid_jis_pairs,
                "span_summaries": [span.to_debug_summary() for span in self.spans],
            }
        )
        return data
