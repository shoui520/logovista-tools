"""Resource extent probing for package-local media references."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from .body_source import SidecarInfo, quote_sql_identifier
from .json_types import JsonObject, JsonValue
from .model import Address, Component, ComponentRole
from .package_utils import _media_mime_and_format
from .ssed import SsedData


class ResourceProbeHost(Protocol):
    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None: ...

    def _relative_offset(self, component: Component, address: Address) -> int: ...

    def data(self, component: str | Component) -> SsedData: ...

    def _body_sidecars(self) -> tuple[SidecarInfo, ...]: ...

    def _sqlite_connection_for_sidecar(self, path: Path, storage: str) -> sqlite3.Connection: ...


def media_info_base(resource_id: str, kind: str) -> JsonObject:
    return {
        "id": resource_id,
        "kind": kind,
        "status": "unresolved",
        "details": {},
    }


def resolve_sidecar_media_resource(host: ResourceProbeHost, resource_id: str, kind: str, details: JsonObject) -> JsonObject:
    info = media_info_base(resource_id, kind)
    sidecar_name = str(details.get("sidecar") or "")
    table = str(details.get("table") or "")
    blob_column = str(details.get("blob_column") or "")
    row_id = details.get("row_id")
    if not sidecar_name or not table or not blob_column or not isinstance(row_id, int):
        info["status"] = "malformed"
        info["details"] = {"reason": "malformed_sidecar_media_reference"}
        return info
    sidecar = next((item for item in host._body_sidecars() if item.path.name == sidecar_name), None)
    if sidecar is None:
        info["status"] = "deferred"
        info["details"] = {"reason": "sidecar_media_source_not_found", "sidecar": sidecar_name}
        return info
    try:
        con = host._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
    except sqlite3.DatabaseError:
        info["status"] = "deferred"
        info["details"] = {"reason": "sidecar_media_open_failed", "sidecar": sidecar_name}
        return info
    try:
        try:
            row = con.execute(
                (
                    f"select length({quote_sql_identifier(blob_column)}) as byte_length, "
                    f"substr({quote_sql_identifier(blob_column)}, 1, 1024) as prefix "
                    f"from {quote_sql_identifier(table)} where rowid=?"
                ),
                (row_id,),
            ).fetchone()
        except sqlite3.DatabaseError:
            row = None
        if row is None:
            info["status"] = "unresolved"
            info["details"] = {"reason": "sidecar_media_row_missing", "sidecar": sidecar_name, "table": table, "row_id": row_id}
            return info
        prefix = bytes(row["prefix"] or b"")
        byte_length = int(row["byte_length"] or 0)
        mime_type, format_hint, container_kind = _media_mime_and_format(prefix, store_kind="sidecar_media")
        info.update(
            {
                "status": "resolved",
                "reason": "sidecar_media_blob",
                "mime_type": mime_type,
                "byte_length": byte_length,
                "source_path": str(sidecar.path),
                "source_table": table,
                "source_row_id": row_id,
                "payload_length": byte_length,
                "store_kind": "sidecar_media",
                "format_hint": format_hint,
                "container_kind": container_kind,
                "details": {
                    "reason": "sidecar_media_blob",
                    "sidecar": sidecar_name,
                    "storage": sidecar.storage,
                    "table": table,
                    "row_id": row_id,
                    "blob_column": blob_column,
                },
            }
        )
        return info
    except sqlite3.DatabaseError:
        info["status"] = "deferred"
        info["details"] = {"reason": "sidecar_media_query_failed", "sidecar": sidecar_name, "table": table, "row_id": row_id}
        return info


def resolve_colscr_resource(host: ResourceProbeHost, resource_id: str, kind: str, address: Address, original_target: JsonValue) -> JsonObject:
    info = media_info_base(resource_id, kind)
    component = host.component_for_address(address, role=ComponentRole.MEDIA)
    if component is None:
        info["status"] = "deferred"
        info["details"] = {"reason": "target_media_component_not_found", "target_address": original_target}
        return info
    rel = host._relative_offset(component, address)
    expanded_size = host.data(component).expanded_size
    if rel < 0 or rel >= expanded_size:
        info["status"] = "malformed"
        info["source_component"] = component.name
        info["source_offset"] = rel
        info["available_bytes"] = max(0, expanded_size - rel)
        info["details"] = {"reason": "media_target_out_of_bounds", "target_address": original_target}
        return info
    header = host.data(component).read(rel, 8)
    if len(header) < 8:
        info["status"] = "malformed"
        info["source_component"] = component.name
        info["source_offset"] = rel
        info["available_bytes"] = max(0, expanded_size - rel)
        info["details"] = {"reason": "truncated_media_record_header", "target_address": original_target}
        return info
    if header[:4] != b"data":
        info["status"] = "unsupported"
        info["source_component"] = component.name
        info["source_offset"] = rel
        info["available_bytes"] = max(0, expanded_size - rel)
        info["details"] = {"reason": "missing_data_magic", "target_address": original_target}
        return info
    payload_length = int.from_bytes(header[4:8], "little")
    payload_offset = rel + 8
    record_length = 8 + payload_length
    if payload_length < 0 or record_length < 8:
        info["status"] = "malformed"
        info["source_component"] = component.name
        info["source_offset"] = rel
        info["details"] = {"reason": "malformed_data_size", "target_address": original_target}
        return info
    if payload_offset + payload_length > expanded_size:
        info["status"] = "malformed"
        info["source_component"] = component.name
        info["source_offset"] = rel
        info["available_bytes"] = max(0, expanded_size - payload_offset)
        info["details"] = {
            "reason": "truncated_data_payload",
            "target_address": original_target,
            "payload_length": payload_length,
        }
        return info
    payload_prefix = host.data(component).read(payload_offset, min(payload_length, 512))
    mime_type, format_hint, container_kind = _media_mime_and_format(payload_prefix, store_kind="colscr")
    source_path = str(component.path) if component.path is not None else None
    info.update(
        {
            "status": "resolved",
            "reason": "colscr_data_record",
            "mime_type": mime_type,
            "byte_length": payload_length,
            "source_component": component.name,
            "source_path": source_path,
            "source_offset": rel,
            "record_offset": rel,
            "record_length": record_length,
            "payload_offset": payload_offset,
            "payload_length": payload_length,
            "store_kind": "colscr",
            "format_hint": format_hint,
            "container_kind": container_kind,
            "details": {
                "reason": "colscr_data_record",
                "target_address": original_target,
                "record_magic": "data",
            },
        }
    )
    return info


def resolve_pcmdata_resource(
    host: ResourceProbeHost,
    resource_id: str,
    kind: str,
    start: Address,
    end: Address,
    original_start: JsonValue,
    original_end: JsonValue,
) -> JsonObject:
    info = media_info_base(resource_id, kind)
    start_component = host.component_for_address(start, role=ComponentRole.MEDIA)
    end_component = host.component_for_address(end, role=ComponentRole.MEDIA)
    if start_component is None or end_component is None:
        info["status"] = "deferred"
        info["details"] = {
            "reason": "target_pcm_component_not_found",
            "range_start": original_start,
            "range_end": original_end,
        }
        return info
    if start_component.name.casefold() != end_component.name.casefold():
        info["status"] = "unsupported"
        info["details"] = {
            "reason": "range_crosses_unsupported_components",
            "range_start": original_start,
            "range_end": original_end,
            "start_component": start_component.name,
            "end_component": end_component.name,
        }
        return info
    start_rel = host._relative_offset(start_component, start)
    end_rel = host._relative_offset(start_component, end)
    expanded_size = host.data(start_component).expanded_size
    if start_rel < 0 or end_rel < 0 or start_rel > expanded_size or end_rel > expanded_size:
        info["status"] = "malformed"
        info["source_component"] = start_component.name
        info["source_offset"] = start_rel
        info["details"] = {
            "reason": "range_out_of_bounds",
            "range_start": original_start,
            "range_end": original_end,
            "expanded_size": expanded_size,
        }
        return info
    if end_rel < start_rel:
        info["status"] = "malformed"
        info["source_component"] = start_component.name
        info["source_offset"] = start_rel
        info["details"] = {"reason": "malformed_audio_range", "range_start": original_start, "range_end": original_end}
        return info
    payload_length = end_rel - start_rel
    if payload_length == 0:
        info["status"] = "malformed"
        info["source_component"] = start_component.name
        info["source_offset"] = start_rel
        info["details"] = {"reason": "zero_length_audio_range", "range_start": original_start, "range_end": original_end}
        return info
    payload_prefix = host.data(start_component).read(start_rel, min(payload_length, 1024))
    mime_type, format_hint, container_kind = _media_mime_and_format(payload_prefix, store_kind="pcmdata")
    source_path = str(start_component.path) if start_component.path is not None else None
    info.update(
        {
            "status": "resolved",
            "reason": "pcmdata_range",
            "mime_type": mime_type,
            "byte_length": payload_length,
            "source_component": start_component.name,
            "source_path": source_path,
            "source_offset": start_rel,
            "payload_offset": start_rel,
            "payload_length": payload_length,
            "range_start_offset": start_rel,
            "range_end_offset": end_rel,
            "store_kind": "pcmdata",
            "format_hint": format_hint,
            "container_kind": container_kind,
            "details": {
                "reason": "pcmdata_range",
                "range_start": original_start,
                "range_end": original_end,
                "range_end_semantics": "exclusive",
            },
        }
    )
    return info
