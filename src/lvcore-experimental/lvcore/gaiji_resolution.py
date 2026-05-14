"""Typed gaiji display/resource resolution models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .diagnostics import Diagnostic
from .gaiji import GaijiDisplayStatus, GaijiResolutionReason
from .json_types import JsonObject

GAIJI_RESOLUTION_SCHEMA = "lvcore.gaiji_resolution.v1"
GAIJI_RESOLUTION_MODEL_VERSION = 1


@dataclass(frozen=True)
class UnicodeGaijiBacking:
    text: str
    source: str


@dataclass(frozen=True)
class BitmapGaijiBacking:
    source: str
    source_path: str
    glyph_index: int
    width: int
    height: int
    glyph_bytes: int
    section: str


@dataclass(frozen=True)
class ImageGaijiBacking:
    source: str
    image_path: str
    image_key: str | None
    byte_length: int
    format_hint: str | None
    container_kind: str | None


@dataclass(frozen=True)
class FormattingHelperGaijiBacking:
    source: str


@dataclass(frozen=True)
class RendererEntryGaijiBacking:
    reason: GaijiResolutionReason = GaijiResolutionReason.RENDERER_CONTEXTUAL_REQUIRED


@dataclass(frozen=True)
class UnresolvedGaijiBacking:
    reason: GaijiResolutionReason


GaijiBacking: TypeAlias = (
    UnicodeGaijiBacking
    | BitmapGaijiBacking
    | ImageGaijiBacking
    | FormattingHelperGaijiBacking
    | RendererEntryGaijiBacking
    | UnresolvedGaijiBacking
)


@dataclass(frozen=True)
class GaijiResolution:
    code: str
    display_status: GaijiDisplayStatus
    display_text: str | None
    fallback_text: str | None
    resource_id: str
    mime_type: str | None
    reason: GaijiResolutionReason
    backing: GaijiBacking
    source: str | None = None
    byte_length: int | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.display_status != GaijiDisplayStatus.UNRESOLVED

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": GAIJI_RESOLUTION_SCHEMA,
            "model_version": GAIJI_RESOLUTION_MODEL_VERSION,
            "code": self.code,
            "display_status": self.display_status.value,
            "display_text": self.display_text,
            "fallback_text": self.fallback_text,
            "resource_id": self.resource_id,
            "mime_type": self.mime_type,
            "reason": self.reason.value,
        }
        if self.source is not None:
            data["source"] = self.source
        if self.byte_length is not None:
            data["byte_length"] = self.byte_length
        if not debug:
            return data
        backing = self.backing
        if isinstance(backing, UnicodeGaijiBacking):
            data["backing"] = {"kind": "unicode", "source": backing.source}
        elif isinstance(backing, BitmapGaijiBacking):
            data["backing"] = {
                "kind": "bitmap",
                "source": backing.source,
                "source_path": backing.source_path,
                "glyph_index": backing.glyph_index,
                "glyph_width": backing.width,
                "glyph_height": backing.height,
                "glyph_bytes": backing.glyph_bytes,
                "section": backing.section,
            }
        elif isinstance(backing, ImageGaijiBacking):
            data["backing"] = {
                "kind": "image",
                "source": backing.source,
                "image_path": backing.image_path,
                "image_key": backing.image_key,
                "format_hint": backing.format_hint,
                "container_kind": backing.container_kind,
            }
        elif isinstance(backing, FormattingHelperGaijiBacking):
            data["backing"] = {"kind": "formatting_helper", "source": backing.source}
        elif isinstance(backing, RendererEntryGaijiBacking):
            data["backing"] = {"kind": "renderer_entry", "reason": backing.reason.value}
        elif isinstance(backing, UnresolvedGaijiBacking):
            data["backing"] = {"kind": "unresolved", "reason": backing.reason.value}
        if self.diagnostics:
            data["diagnostics"] = [diagnostic.to_dict() for diagnostic in self.diagnostics]
        return data
