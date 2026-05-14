"""Entry rendering and explicit resource resolution APIs."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .body_source import (
    quote_sql_identifier,
)
from .document import ResourceKind, ResourceRef, ResourceStatus
from .gaiji import GaijiDisplayStatus
from .gaiji_resolution import BitmapGaijiBacking, GaijiResolution, ImageGaijiBacking
from .inspect import InspectorRenderer
from .json_types import JsonObject
from .model import Address, ComponentRole, Entry
from .package_utils import _media_mime_and_format
from .render import HtmlProfile, render_html, render_text
from .resources import ColscrLocator, GaijiLocator, PcmRangeLocator, ResourceLocation, SidecarBlobLocator, UnresolvedAddress


class PackageResourceMixin:
    """Rendering and resource methods for LogoVistaPackage."""

    def entry_document(self, entry: Entry):
        return entry.document()

    def render_entry_html(
        self,
        entry: Entry,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        if isinstance(profile, str):
            if profile.replace("-", "_") == "debug":
                return InspectorRenderer().render_html(entry.document())
            profile = HtmlProfile(profile.replace("-", "_"))
        return render_html(entry.document(), profile=profile, include_diagnostics=include_diagnostics)

    def render_entry_text(self, entry: Entry) -> str:
        return render_text(entry.document())

    def entry_diagnostics(self, entry: Entry) -> tuple[object, ...]:
        return entry.document().diagnostics

    @staticmethod
    def _address_from_details(value: object) -> Address | None:
        if not isinstance(value, dict):
            return None
        try:
            component_value = value.get("component")
            component = str(component_value) if component_value else None
            return Address(int(value["block"]), int(value.get("offset", 0)), component)
        except (KeyError, TypeError, ValueError):
            return None

    def _media_info_base(self, resource_id: str, kind: str) -> JsonObject:
        return {
            "id": resource_id,
            "kind": kind,
            "status": "unresolved",
            "details": {},
        }

    @staticmethod
    def _resource_kind(value: str) -> ResourceKind:
        try:
            return ResourceKind(value)
        except ValueError:
            return ResourceKind.UNKNOWN

    @staticmethod
    def _resource_status(value: object) -> ResourceStatus:
        try:
            return ResourceStatus(str(value))
        except ValueError:
            return ResourceStatus.UNRESOLVED

    def _resource_location_from_info(
        self,
        *,
        resource_id: str,
        kind: str,
        label: str,
        code: str | None,
        info: JsonObject,
    ) -> ResourceLocation:
        details = info.get("details") if isinstance(info.get("details"), dict) else {}
        status = self._resource_status(info.get("status"))
        resource_kind = self._resource_kind(kind)
        reason = str(info.get("reason") or details.get("reason") or "") or None
        byte_length = info.get("byte_length")
        byte_length = byte_length if isinstance(byte_length, int) else None
        common = {
            "resource_id": str(info.get("resource_id") or info.get("id") or resource_id),
            "kind": resource_kind,
            "status": status,
            "mime_type": str(info.get("mime_type")) if info.get("mime_type") else None,
            "label": label,
            "reason": reason,
            "byte_length": byte_length,
            "format_hint": str(info.get("format_hint")) if info.get("format_hint") else None,
            "container_kind": str(info.get("container_kind")) if info.get("container_kind") else None,
            "store_kind": str(info.get("store_kind")) if info.get("store_kind") else None,
        }
        if resource_kind == ResourceKind.GAIJI:
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
            target = self._address_from_details(details.get("target_address"))
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
            start = self._address_from_details(details.get("range_start"))
            end = self._address_from_details(details.get("range_end"))
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

    def _resource_location_from_gaiji_resolution(
        self,
        *,
        resource_id: str,
        kind: str,
        label: str,
        resolution: GaijiResolution,
    ) -> ResourceLocation:
        resource_kind = self._resource_kind(kind)
        status = ResourceStatus.RESOLVED if resolution.resolved else ResourceStatus.UNRESOLVED
        common = {
            "resource_id": resolution.resource_id or resource_id,
            "kind": resource_kind,
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

    def _resolve_sidecar_media_resource(self, resource_id: str, kind: str, details: JsonObject) -> JsonObject:
        info = self._media_info_base(resource_id, kind)
        sidecar_name = str(details.get("sidecar") or "")
        table = str(details.get("table") or "")
        blob_column = str(details.get("blob_column") or "")
        row_id = details.get("row_id")
        if not sidecar_name or not table or not blob_column or not isinstance(row_id, int):
            info["status"] = "malformed"
            info["details"] = {"reason": "malformed_sidecar_media_reference"}
            return info
        sidecar = next((item for item in self._body_sidecars() if item.path.name == sidecar_name), None)
        if sidecar is None:
            info["status"] = "deferred"
            info["details"] = {"reason": "sidecar_media_source_not_found", "sidecar": sidecar_name}
            return info
        try:
            con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
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

    def _resolve_colscr_resource(self, resource_id: str, kind: str, address: Address, original_target: object) -> JsonObject:
        info = self._media_info_base(resource_id, kind)
        component = self.component_for_address(address, role=ComponentRole.MEDIA)
        if component is None:
            info["status"] = "deferred"
            info["details"] = {"reason": "target_media_component_not_found", "target_address": original_target}
            return info
        rel = self._relative_offset(component, address)
        expanded_size = self.data(component).expanded_size
        if rel < 0 or rel >= expanded_size:
            info["status"] = "malformed"
            info["source_component"] = component.name
            info["source_offset"] = rel
            info["available_bytes"] = max(0, expanded_size - rel)
            info["details"] = {"reason": "media_target_out_of_bounds", "target_address": original_target}
            return info
        header = self.data(component).read(rel, 8)
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
        payload_prefix = self.data(component).read(payload_offset, min(payload_length, 512))
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

    def _resolve_pcmdata_resource(
        self,
        resource_id: str,
        kind: str,
        start: Address,
        end: Address,
        original_start: object,
        original_end: object,
    ) -> JsonObject:
        info = self._media_info_base(resource_id, kind)
        start_component = self.component_for_address(start, role=ComponentRole.MEDIA)
        end_component = self.component_for_address(end, role=ComponentRole.MEDIA)
        if start_component is None or end_component is None:
            info["status"] = "deferred"
            info["details"] = {
                "reason": "target_pcm_component_not_found",
                "range_start": original_start,
                "range_end": original_end,
            }
            return info
        if start_component.name.lower() != end_component.name.lower():
            info["status"] = "unsupported"
            info["details"] = {
                "reason": "range_crosses_unsupported_components",
                "range_start": original_start,
                "range_end": original_end,
                "start_component": start_component.name,
                "end_component": end_component.name,
            }
            return info
        start_rel = self._relative_offset(start_component, start)
        end_rel = self._relative_offset(start_component, end)
        expanded_size = self.data(start_component).expanded_size
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
        payload_prefix = self.data(start_component).read(start_rel, min(payload_length, 1024))
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

    def resource_info(self, resource: ResourceRef | JsonObject | ResourceLocation) -> ResourceLocation:
        """Return package-local metadata for a document resource.

        This method does not transform or copy media. It reports whether lvcore
        can point at original package data for a resource and leaves actual
        presentation to the caller.
        """

        if isinstance(resource, ResourceLocation):
            return resource
        if isinstance(resource, ResourceRef):
            resource_id = resource.id
            kind = resource.kind.value
            code = resource.code
            details = dict(resource.details)
            label = resource.label
        else:
            resource_id = str(resource.get("id") or "")
            kind = str(resource.get("kind") or ResourceKind.UNKNOWN.value)
            code_value = resource.get("code")
            code = str(code_value) if code_value is not None else None
            details_value = resource.get("details") or {}
            details = dict(details_value) if isinstance(details_value, dict) else {}
            label = str(resource.get("label") or resource_id)

        info: JsonObject = self._media_info_base(resource_id, kind)
        if details.get("sidecar_media"):
            info = self._resolve_sidecar_media_resource(resource_id, kind, details)
            return self._resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)
        if kind == ResourceKind.GAIJI.value and code:
            resolution = self.gaiji_info(resource)
            return self._resource_location_from_gaiji_resolution(resource_id=resource_id, kind=kind, label=label, resolution=resolution)

        range_start = details.get("range_start")
        range_end = details.get("range_end")
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value} and isinstance(range_start, dict) and isinstance(range_end, dict):
            start = self._address_from_details(range_start)
            end = self._address_from_details(range_end)
            if start is None or end is None:
                info["status"] = "malformed"
                info["details"] = {"reason": "malformed_audio_range", "range_start": range_start, "range_end": range_end}
                return self._resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)
            info = self._resolve_pcmdata_resource(resource_id, kind, start, end, range_start, range_end)
            return self._resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)

        target_address = details.get("target_address")
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value} and isinstance(target_address, dict):
            address = self._address_from_details(target_address)
            if address is None:
                info["status"] = "malformed"
                info["details"] = {"reason": "malformed_media_target_address"}
                return self._resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)
            info = self._resolve_colscr_resource(resource_id, kind, address, target_address)
            return self._resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)

        info["details"] = {"reason": details.get("reason") or "resource_resolution_not_supported"}
        return self._resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)

    def resource_bytes(self, resource: ResourceRef | JsonObject | ResourceLocation) -> bytes | None:
        """Return untouched resource bytes when lvcore knows an exact extent."""

        location = self.resource_info(resource)
        if location.status != ResourceStatus.RESOLVED:
            return None
        locator = location.locator
        if isinstance(locator, GaijiLocator):
            if locator.display_status == GaijiDisplayStatus.UNICODE_MAPPED.value and location.byte_length is None:
                return None
            if locator.image_path:
                try:
                    return Path(locator.image_path).read_bytes()
                except OSError:
                    return None
            if locator.glyph_index is not None and locator.source_path:
                for resource_info in self.ga16:
                    if str(resource_info.path) == locator.source_path:
                        return resource_info.glyph_by_index(locator.glyph_index)
            return None
        if isinstance(locator, SidecarBlobLocator):
            sidecar = next((item for item in self._body_sidecars() if item.path.name == locator.sidecar_name), None)
            if sidecar is None:
                return None
            try:
                con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
                row = con.execute(
                    f"select {quote_sql_identifier(locator.blob_column)} from {quote_sql_identifier(locator.table)} where rowid=?",
                    (locator.row_id,),
                ).fetchone()
            except sqlite3.DatabaseError:
                return None
            if row is None:
                return None
            return bytes(row[0] or b"")
        if isinstance(locator, ColscrLocator):
            return self.data(locator.component).read(locator.payload_offset, locator.payload_length)
        if isinstance(locator, PcmRangeLocator):
            return self.data(locator.component).read(locator.payload_offset, locator.payload_length)
        return None

    def resource_record_bytes(self, resource: ResourceRef | JsonObject | ResourceLocation) -> bytes | None:
        """Return original wrapped record bytes when a resolved store has a wrapper."""

        location = self.resource_info(resource)
        if location.status != ResourceStatus.RESOLVED or not isinstance(location.locator, ColscrLocator):
            return None
        return self.data(location.locator.component).read(location.locator.record_offset, location.locator.record_length)

    def resolve_resource(self, resource: ResourceRef | JsonObject | ResourceLocation) -> ResourceLocation:
        return self.resource_info(resource)
