"""Entry rendering and explicit resource resolution APIs."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .body_source import (
    quote_sql_identifier,
)
from .document import ResourceKind, ResourceRef
from .gaiji import GaijiDisplayStatus
from .model import Address, ComponentRole, Entry
from .package_utils import _media_mime_and_format
from .render import HtmlProfile, render_html, render_text


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

    def _media_info_base(self, resource_id: str, kind: str) -> dict[str, object]:
        return {
            "id": resource_id,
            "kind": kind,
            "status": "unresolved",
            "details": {},
        }

    def _resolve_sidecar_media_resource(self, resource_id: str, kind: str, details: dict[str, object]) -> dict[str, object]:
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

    def _resolve_colscr_resource(self, resource_id: str, kind: str, address: Address, original_target: object) -> dict[str, object]:
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
    ) -> dict[str, object]:
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

    def resource_info(self, resource: ResourceRef | dict[str, object]) -> dict[str, object]:
        """Return package-local metadata for a document resource.

        This method does not transform or copy media. It reports whether lvcore
        can point at original package data for a resource and leaves actual
        presentation to the caller.
        """

        if isinstance(resource, ResourceRef):
            resource_id = resource.id
            kind = resource.kind.value
            code = resource.code
            details = dict(resource.details)
        else:
            resource_id = str(resource.get("id") or "")
            kind = str(resource.get("kind") or ResourceKind.UNKNOWN.value)
            code_value = resource.get("code")
            code = str(code_value) if code_value is not None else None
            details_value = resource.get("details") or {}
            details = dict(details_value) if isinstance(details_value, dict) else {}

        info: dict[str, object] = self._media_info_base(resource_id, kind)
        if details.get("sidecar_media"):
            return self._resolve_sidecar_media_resource(resource_id, kind, details)
        if kind == ResourceKind.GAIJI.value and code:
            return self.gaiji_info(resource)

        range_start = details.get("range_start")
        range_end = details.get("range_end")
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value} and isinstance(range_start, dict) and isinstance(range_end, dict):
            start = self._address_from_details(range_start)
            end = self._address_from_details(range_end)
            if start is None or end is None:
                info["status"] = "malformed"
                info["details"] = {"reason": "malformed_audio_range", "range_start": range_start, "range_end": range_end}
                return info
            return self._resolve_pcmdata_resource(resource_id, kind, start, end, range_start, range_end)

        target_address = details.get("target_address")
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value} and isinstance(target_address, dict):
            address = self._address_from_details(target_address)
            if address is None:
                info["status"] = "malformed"
                info["details"] = {"reason": "malformed_media_target_address"}
                return info
            return self._resolve_colscr_resource(resource_id, kind, address, target_address)

        info["details"] = {"reason": details.get("reason") or "resource_resolution_not_supported"}
        return info

    def resource_bytes(self, resource: ResourceRef | dict[str, object]) -> bytes | None:
        """Return untouched resource bytes when lvcore knows an exact extent."""

        if isinstance(resource, ResourceRef):
            kind = resource.kind.value
            code = resource.code
        else:
            kind = str(resource.get("kind") or ResourceKind.UNKNOWN.value)
            code_value = resource.get("code")
            code = str(code_value) if code_value is not None else None

        if kind == ResourceKind.GAIJI.value and code:
            info = self.gaiji_info(resource)
            if info.get("status") != "resolved":
                return None
            details = info.get("details") if isinstance(info.get("details"), dict) else {}
            status = str(info.get("display_status") or details.get("display_status") or "")
            if status == GaijiDisplayStatus.UNICODE_MAPPED.value and not info.get("byte_length"):
                return None
            source_path = info.get("source_path") or info.get("image_path")
            if isinstance(source_path, str) and info.get("image_path"):
                try:
                    return Path(source_path).read_bytes()
                except OSError:
                    return None
            glyph_index = info.get("glyph_index")
            source_path_value = info.get("source_path")
            if isinstance(glyph_index, int) and isinstance(source_path_value, str):
                for resource_info in self.ga16:
                    if str(resource_info.path) == source_path_value:
                        return resource_info.glyph_by_index(glyph_index)
            try:
                code_int = int(code, 16)
            except ValueError:
                return None
            for ga16 in self.ga16:
                glyph = ga16.glyph(code_int)
                if glyph is not None:
                    return glyph
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value}:
            info = self.resource_info(resource)
            if info.get("status") != "resolved":
                return None
            if info.get("store_kind") == "sidecar_media":
                details = resource.details if isinstance(resource, ResourceRef) else resource.get("details", {})
                if not isinstance(details, dict):
                    return None
                sidecar_name = str(details.get("sidecar") or "")
                table = str(details.get("table") or "")
                blob_column = str(details.get("blob_column") or "")
                row_id = details.get("row_id")
                if not sidecar_name or not table or not blob_column or not isinstance(row_id, int):
                    return None
                sidecar = next((item for item in self._body_sidecars() if item.path.name == sidecar_name), None)
                if sidecar is None:
                    return None
                try:
                    con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
                    row = con.execute(
                        f"select {quote_sql_identifier(blob_column)} from {quote_sql_identifier(table)} where rowid=?",
                        (row_id,),
                    ).fetchone()
                except sqlite3.DatabaseError:
                    return None
                if row is None:
                    return None
                return bytes(row[0] or b"")
            component_name = info.get("source_component")
            payload_offset = info.get("payload_offset")
            payload_length = info.get("payload_length")
            if not isinstance(component_name, str) or not isinstance(payload_offset, int) or not isinstance(payload_length, int):
                return None
            return self.data(component_name).read(payload_offset, payload_length)
        return None

    def resource_record_bytes(self, resource: ResourceRef | dict[str, object]) -> bytes | None:
        """Return original wrapped record bytes when a resolved store has a wrapper."""

        info = self.resource_info(resource)
        if info.get("status") != "resolved" or info.get("store_kind") != "colscr":
            return None
        component_name = info.get("source_component")
        record_offset = info.get("record_offset")
        record_length = info.get("record_length")
        if not isinstance(component_name, str) or not isinstance(record_offset, int) or not isinstance(record_length, int):
            return None
        return self.data(component_name).read(record_offset, record_length)

    def resolve_resource(self, resource: ResourceRef | dict[str, object]) -> dict[str, object]:
        return self.resource_info(resource)
