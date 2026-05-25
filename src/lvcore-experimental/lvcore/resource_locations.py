"""Typed ResourceLocation construction helpers."""

from __future__ import annotations

from .document import ResourceKind, ResourceStatus
from .gaiji_resolution import BitmapGaijiBacking, GaijiResolution, ImageGaijiBacking
from .json_types import JsonObject
from .model import Address
from .resources import ColscrLocator, GaijiLocator, PcmRangeLocator, ResourceLocation, SidecarBlobLocator, UnresolvedAddress


def address_from_details(value: object) -> Address | None:
    if not isinstance(value, dict):
        return None
    try:
        component_value = value.get("component")
        component = str(component_value) if component_value else None
        return Address(int(value["block"]), int(value.get("offset", 0)), component)
    except (KeyError, TypeError, ValueError):
        return None


def resource_kind(value: str) -> ResourceKind:
    try:
        return ResourceKind(value)
    except ValueError:
        return ResourceKind.UNKNOWN


def resource_status(value: object) -> ResourceStatus:
    try:
        return ResourceStatus(str(value))
    except ValueError:
        return ResourceStatus.UNRESOLVED


def resource_location_from_info(
    *,
    resource_id: str,
    kind: str,
    label: str,
    code: str | None,
    info: JsonObject,
) -> ResourceLocation:
    details = info.get("details") if isinstance(info.get("details"), dict) else {}
    status = resource_status(info.get("status"))
    kind_value = resource_kind(kind)
    reason = str(info.get("reason") or details.get("reason") or "") or None
    byte_length = info.get("byte_length")
    byte_length = byte_length if isinstance(byte_length, int) else None
    common = {
        "resource_id": str(info.get("resource_id") or info.get("id") or resource_id),
        "kind": kind_value,
        "status": status,
        "mime_type": str(info.get("mime_type")) if info.get("mime_type") else None,
        "label": label,
        "reason": reason,
        "byte_length": byte_length,
        "format_hint": str(info.get("format_hint")) if info.get("format_hint") else None,
        "container_kind": str(info.get("container_kind")) if info.get("container_kind") else None,
        "store_kind": str(info.get("store_kind")) if info.get("store_kind") else None,
    }
    if kind_value == ResourceKind.GAIJI:
        locator = GaijiLocator(
            code=code or str(info.get("code") or ""),
            source=str(info.get("source") or details.get("source") or "") or None,
            glyph_index=info.get("glyph_index") if isinstance(info.get("glyph_index"), int) else None,
            glyph_width=info.get("glyph_width") if isinstance(info.get("glyph_width"), int) else None,
            glyph_height=info.get("glyph_height") if isinstance(info.get("glyph_height"), int) else None,
            glyph_bytes=info.get("glyph_bytes") if isinstance(info.get("glyph_bytes"), int) else None,
            source_path=str(info.get("source_path")) if info.get("source_path") else None,
            image_path=str(info.get("image_path")) if info.get("image_path") else None,
            display_status=str(info.get("display_status") or details.get("display_status") or "") or None,
            display_text=str(info.get("display_text")) if info.get("display_text") else None,
            fallback_text=str(info.get("fallback_text")) if info.get("fallback_text") else None,
        )
        return ResourceLocation(locator=locator, **common)
    if info.get("store_kind") == "sidecar_media":
        locator = SidecarBlobLocator(
            sidecar_name=str(details.get("sidecar") or ""),
            table=str(details.get("table") or ""),
            row_id=int(details.get("row_id") or 0),
            blob_column=str(details.get("blob_column") or ""),
            byte_length=byte_length or 0,
        )
        return ResourceLocation(locator=locator, **common)
    component_name = info.get("source_component")
    if info.get("store_kind") == "colscr" and isinstance(component_name, str):
        target = address_from_details(details.get("target_address"))
        locator = ColscrLocator(
            component=component_name,
            record_offset=int(info.get("record_offset") or 0),
            record_length=int(info.get("record_length") or 0),
            payload_offset=int(info.get("payload_offset") or 0),
            payload_length=int(info.get("payload_length") or 0),
            target=target,
        )
        return ResourceLocation(locator=locator, **common)
    if info.get("store_kind") == "pcmdata" and isinstance(component_name, str):
        start = address_from_details(details.get("range_start"))
        end = address_from_details(details.get("range_end"))
        locator = PcmRangeLocator(
            component=component_name,
            range_start=int(info.get("range_start_offset") or info.get("payload_offset") or 0),
            range_end=int(info.get("range_end_offset") or 0),
            payload_offset=int(info.get("payload_offset") or 0),
            payload_length=int(info.get("payload_length") or 0),
            start_address=start,
            end_address=end,
        )
        return ResourceLocation(locator=locator, **common)
    unresolved = UnresolvedAddress(reason=reason or "resource_resolution_not_supported")
    return ResourceLocation(locator=unresolved, **common)


def resource_location_from_gaiji_resolution(
    *,
    resource_id: str,
    kind: str,
    label: str,
    resolution: GaijiResolution,
) -> ResourceLocation:
    kind_value = resource_kind(kind)
    status = ResourceStatus.RESOLVED if resolution.resolved else ResourceStatus.UNRESOLVED
    common = {
        "resource_id": resolution.resource_id or resource_id,
        "kind": kind_value,
        "status": status,
        "mime_type": resolution.mime_type,
        "label": label,
        "reason": resolution.reason.value,
        "byte_length": resolution.byte_length,
    }
    backing = resolution.backing
    if isinstance(backing, BitmapGaijiBacking):
        locator = GaijiLocator(
            code=resolution.code,
            source=backing.source,
            glyph_index=backing.glyph_index,
            glyph_width=backing.width,
            glyph_height=backing.height,
            glyph_bytes=backing.glyph_bytes,
            source_path=backing.source_path,
            display_status=resolution.display_status.value,
            display_text=resolution.display_text,
            fallback_text=resolution.fallback_text,
        )
        return ResourceLocation(locator=locator, **common)
    if isinstance(backing, ImageGaijiBacking):
        locator = GaijiLocator(
            code=resolution.code,
            source=backing.source,
            image_path=backing.image_path,
            display_status=resolution.display_status.value,
            display_text=resolution.display_text,
            fallback_text=resolution.fallback_text,
        )
        return ResourceLocation(
            locator=locator,
            format_hint=backing.format_hint,
            container_kind=backing.container_kind,
            **common,
        )
    locator = GaijiLocator(
        code=resolution.code,
        display_status=resolution.display_status.value,
        display_text=resolution.display_text,
        fallback_text=resolution.fallback_text,
    )
    return ResourceLocation(locator=locator, **common)
