"""Typed resource resolution models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .document import ResourceKind, ResourceStatus
from .json_types import JsonObject, JsonValue
from .model import Address

RESOURCE_LOCATION_SCHEMA = "lvcore.resource_location.v1"
RESOURCE_LOCATION_MODEL_VERSION = 1


@dataclass(frozen=True)
class GaijiLocator:
    code: str
    source: str | None = None
    glyph_index: int | None = None
    glyph_width: int | None = None
    glyph_height: int | None = None
    glyph_bytes: int | None = None
    source_path: str | None = None
    image_path: str | None = None
    display_status: str | None = None
    display_text: str | None = None
    fallback_text: str | None = None


@dataclass(frozen=True)
class ColscrLocator:
    component: str
    record_offset: int
    record_length: int
    payload_offset: int
    payload_length: int
    target: Address | None = None


@dataclass(frozen=True)
class PcmRangeLocator:
    component: str
    range_start: int
    range_end: int
    payload_offset: int
    payload_length: int
    start_address: Address | None = None
    end_address: Address | None = None


@dataclass(frozen=True)
class SidecarBlobLocator:
    sidecar_name: str
    table: str
    row_id: int
    blob_column: str
    byte_length: int


@dataclass(frozen=True)
class UnresolvedAddress:
    reason: str
    target: Address | None = None
    range_start: Address | None = None
    range_end: Address | None = None


ResourceLocator: TypeAlias = GaijiLocator | ColscrLocator | PcmRangeLocator | SidecarBlobLocator | UnresolvedAddress


@dataclass(frozen=True)
class ResourceLocation:
    resource_id: str
    kind: ResourceKind
    status: ResourceStatus
    mime_type: str | None
    label: str
    locator: ResourceLocator
    reason: str | None = None
    byte_length: int | None = None
    format_hint: str | None = None
    container_kind: str | None = None
    store_kind: str | None = None

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": RESOURCE_LOCATION_SCHEMA,
            "model_version": RESOURCE_LOCATION_MODEL_VERSION,
            "id": self.resource_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "mime_type": self.mime_type,
            "label": self.label,
        }
        if not debug:
            return data
        if self.reason is not None:
            data["reason"] = self.reason
        if self.byte_length is not None:
            data["byte_length"] = self.byte_length
        if self.store_kind is not None:
            data["store_kind"] = self.store_kind
        data["format_hint"] = self.format_hint
        data["container_kind"] = self.container_kind
        locator = self.locator
        if isinstance(locator, GaijiLocator):
            data.update(
                {
                    "code": locator.code,
                    "source": locator.source,
                    "glyph_index": locator.glyph_index,
                    "glyph_width": locator.glyph_width,
                    "glyph_height": locator.glyph_height,
                    "glyph_bytes": locator.glyph_bytes,
                    "source_path": locator.source_path,
                    "image_path": locator.image_path,
                    "display_status": locator.display_status,
                    "display_text": locator.display_text,
                    "fallback_text": locator.fallback_text,
                }
            )  # type: ignore[arg-type]
        elif isinstance(locator, ColscrLocator):
            data.update(
                {
                    "source_component": locator.component,
                    "record_offset": locator.record_offset,
                    "record_length": locator.record_length,
                    "payload_offset": locator.payload_offset,
                    "payload_length": locator.payload_length,
                    "target_address": locator.target.to_dict() if locator.target else None,
                }
            )  # type: ignore[arg-type]
        elif isinstance(locator, PcmRangeLocator):
            data.update(
                {
                    "source_component": locator.component,
                    "range_start_offset": locator.range_start,
                    "range_end_offset": locator.range_end,
                    "payload_offset": locator.payload_offset,
                    "payload_length": locator.payload_length,
                    "range_start": locator.start_address.to_dict() if locator.start_address else None,
                    "range_end": locator.end_address.to_dict() if locator.end_address else None,
                }
            )  # type: ignore[arg-type]
        elif isinstance(locator, SidecarBlobLocator):
            data.update(
                {
                    "sidecar": locator.sidecar_name,
                    "source_table": locator.table,
                    "source_row_id": locator.row_id,
                    "blob_column": locator.blob_column,
                    "payload_length": locator.byte_length,
                }
            )  # type: ignore[arg-type]
        elif isinstance(locator, UnresolvedAddress):
            data.update(
                {
                    "unresolved_reason": locator.reason,
                    "target_address": locator.target.to_dict() if locator.target else None,
                    "range_start": locator.range_start.to_dict() if locator.range_start else None,
                    "range_end": locator.range_end.to_dict() if locator.range_end else None,
                }
            )  # type: ignore[arg-type]
        return data
