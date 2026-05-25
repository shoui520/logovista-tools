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
            data["code"] = locator.code
            data["source"] = locator.source
            data["glyph_index"] = locator.glyph_index
            data["glyph_width"] = locator.glyph_width
            data["glyph_height"] = locator.glyph_height
            data["glyph_bytes"] = locator.glyph_bytes
            data["source_path"] = locator.source_path
            data["image_path"] = locator.image_path
            data["display_status"] = locator.display_status
            data["display_text"] = locator.display_text
            data["fallback_text"] = locator.fallback_text
        elif isinstance(locator, ColscrLocator):
            data["source_component"] = locator.component
            data["record_offset"] = locator.record_offset
            data["record_length"] = locator.record_length
            data["payload_offset"] = locator.payload_offset
            data["payload_length"] = locator.payload_length
            data["target_address"] = locator.target.to_dict() if locator.target else None
        elif isinstance(locator, PcmRangeLocator):
            data["source_component"] = locator.component
            data["range_start_offset"] = locator.range_start
            data["range_end_offset"] = locator.range_end
            data["payload_offset"] = locator.payload_offset
            data["payload_length"] = locator.payload_length
            data["range_start"] = locator.start_address.to_dict() if locator.start_address else None
            data["range_end"] = locator.end_address.to_dict() if locator.end_address else None
        elif isinstance(locator, SidecarBlobLocator):
            data["sidecar"] = locator.sidecar_name
            data["source_table"] = locator.table
            data["source_row_id"] = locator.row_id
            data["blob_column"] = locator.blob_column
            data["payload_length"] = locator.byte_length
        elif isinstance(locator, UnresolvedAddress):
            data["unresolved_reason"] = locator.reason
            data["target_address"] = locator.target.to_dict() if locator.target else None
            data["range_start"] = locator.range_start.to_dict() if locator.range_start else None
            data["range_end"] = locator.range_end.to_dict() if locator.range_end else None
        return data
