"""Entry rendering and explicit resource resolution APIs."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Callable

from .body_source import (
    SidecarInfo,
    quote_sql_identifier,
)
from .diagnostics import Diagnostic
from .document import EntryDocument, ResourceKind, ResourceRef, ResourceStatus
from .gaiji import Ga16Resource, GaijiDisplayStatus
from .gaiji_resolution import GaijiResolution
from .inspect import InspectorRenderer
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry
from .resource_locations import address_from_details, resource_location_from_gaiji_resolution, resource_location_from_info
from .resource_probe import media_info_base, resolve_colscr_resource, resolve_pcmdata_resource, resolve_sidecar_media_resource
from .render import HtmlProfile, render_html, render_text
from .resources import ColscrLocator, GaijiLocator, PcmRangeLocator, ResourceLocation, SidecarBlobLocator
from .ssed import SsedData


class ResourceResolver:
    """Rendering and resource methods for LogoVistaPackage."""

    def __init__(
        self,
        *,
        component_for_address: Callable[[Address, ComponentRole | None], Component | None],
        relative_offset: Callable[[Component, Address], int],
        data: Callable[[str | Component], SsedData],
        body_sidecars: Callable[[], tuple[SidecarInfo, ...]],
        sqlite_connection_for_sidecar: Callable[[Path, str], sqlite3.Connection],
        gaiji_info: Callable[[ResourceRef | JsonObject | str], GaijiResolution],
        ga16: Callable[[], tuple[Ga16Resource, ...]],
    ) -> None:
        self._component_for_address = component_for_address
        self._relative_offset_provider = relative_offset
        self._data = data
        self._body_sidecars_provider = body_sidecars
        self._sqlite_connection_for_sidecar_provider = sqlite_connection_for_sidecar
        self._gaiji_info = gaiji_info
        self._ga16_provider = ga16

    def entry_document(self, entry: Entry) -> EntryDocument:
        return entry.document()

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None:
        return self._component_for_address(address, role)

    def _relative_offset(self, component: Component, address: Address) -> int:
        return self._relative_offset_provider(component, address)

    def data(self, component: str | Component) -> SsedData:
        return self._data(component)

    def _body_sidecars(self) -> tuple[SidecarInfo, ...]:
        return self._body_sidecars_provider()

    def _sqlite_connection_for_sidecar(self, path: Path, storage: str) -> sqlite3.Connection:
        return self._sqlite_connection_for_sidecar_provider(path, storage)

    def gaiji_info(self, resource: ResourceRef | JsonObject | str) -> GaijiResolution:
        return self._gaiji_info(resource)

    @property
    def ga16(self) -> tuple[Ga16Resource, ...]:
        return self._ga16_provider()

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

    def entry_diagnostics(self, entry: Entry) -> tuple[Diagnostic, ...]:
        return entry.document().diagnostics

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

        info: JsonObject = media_info_base(resource_id, kind)
        if details.get("sidecar_media"):
            info = resolve_sidecar_media_resource(self, resource_id, kind, details)
            return resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)
        if kind == ResourceKind.GAIJI.value and code:
            resolution = self.gaiji_info(resource)
            return resource_location_from_gaiji_resolution(resource_id=resource_id, kind=kind, label=label, resolution=resolution)

        range_start = details.get("range_start")
        range_end = details.get("range_end")
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value} and isinstance(range_start, dict) and isinstance(range_end, dict):
            start = address_from_details(range_start)
            end = address_from_details(range_end)
            if start is None or end is None:
                info["status"] = "malformed"
                info["details"] = {"reason": "malformed_audio_range", "range_start": range_start, "range_end": range_end}
                return resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)
            info = resolve_pcmdata_resource(self, resource_id, kind, start, end, range_start, range_end)
            return resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)

        target_address = details.get("target_address")
        if kind in {ResourceKind.MEDIA.value, ResourceKind.IMAGE.value, ResourceKind.AUDIO.value} and isinstance(target_address, dict):
            address = address_from_details(target_address)
            if address is None:
                info["status"] = "malformed"
                info["details"] = {"reason": "malformed_media_target_address"}
                return resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)
            info = resolve_colscr_resource(self, resource_id, kind, address, target_address)
            return resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)

        info["details"] = {"reason": details.get("reason") or "resource_resolution_not_supported"}
        return resource_location_from_info(resource_id=resource_id, kind=kind, label=label, code=code, info=info)

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
