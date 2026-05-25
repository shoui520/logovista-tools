"""High-level package API."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Callable

from .body_source import BodyPointerInspection, BodySourceInfo, SidecarAddressMatch, SidecarInfo
from .component_store import ComponentStore
from .detect import detect_family
from .diagnostics import Diagnostic, Location, Severity
from .document import ResourceRef
from .dictionary import Dictionary
from .errors import UnsupportedPackageError
from .gaiji import Ga16Resource, GaijiMap, GaijiSources, ImageGaijiResource
from .index import IndexParse, IndexRow
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile
from .reader import (
    EntryView,
    HomeSurface,
    UnsupportedReaderPackage,
)
from .render import HtmlProfile
from .resources import ResourceLocation
from .search import SearchHit, SearchResults
from .ssed import Catalog, SsedData, parse_catalog
from .surfaces import SurfaceStore, SurfaceStoreDeps
from .package_entries import EntryStore
from .package_gaiji import GaijiRegistry
from .package_resources import ResourceResolver
from .package_search import IndexStore
from .package_services import EntryStoreDeps, IndexStoreDeps
from .package_sidecars import SidecarRegistry


def open_package(path: str | Path) -> "LogoVistaPackage":
    info = detect_family(Path(path))
    if info.family != PackageFamily.SSED:
        raise UnsupportedPackageError(f"{info.family.value} package support is deferred")
    if info.idx_path is None:
        raise UnsupportedPackageError("SSED package did not expose an IDX path")
    return LogoVistaPackage(info.idx_path)


def open_reader_package(path: str | Path) -> "LogoVistaPackage | UnsupportedReaderPackage":
    info = detect_family(Path(path))
    if info.family == PackageFamily.SSED and info.idx_path is not None:
        return LogoVistaPackage(info.idx_path)
    return UnsupportedReaderPackage(info)


class LogoVistaPackage:
    """Reader/parser API for one SSED package."""

    def __init__(self, idx_path: Path):
        self.catalog: Catalog = parse_catalog(idx_path)
        self.info = PackageInfo(
            family=PackageFamily.SSED,
            root=idx_path.parent,
            idx_path=idx_path,
            dict_id=idx_path.stem,
            title=self.catalog.title,
        )
        self._component_store = ComponentStore(idx_path, self.catalog)
        self._closed = False
        self._gaiji_registry = self._make_gaiji_registry()
        self._sidecar_registry = self._make_sidecar_registry()
        self._resource_resolver = self._make_resource_resolver()
        self._entry_store = EntryStore(self._entry_store_deps())
        self._index_store = IndexStore(self._index_store_deps())
        self._surface_store = self._make_surface_store()
        self._dictionaries: tuple[Dictionary, ...] | None = None

    def _make_gaiji_registry(self) -> GaijiRegistry:
        return GaijiRegistry(
            info=self.info,
            catalog=self.catalog,
            components=self.components,
            expanded=self.expanded,
            ensure_open=self._ensure_open,
        )

    def _make_sidecar_registry(self) -> SidecarRegistry:
        return SidecarRegistry(info=self.info, ensure_open=self._ensure_open)

    def _make_resource_resolver(self) -> ResourceResolver:
        return ResourceResolver(
            component_for_address=lambda address, role: self.component_for_address(address, role=role),
            relative_offset=self._relative_offset,
            data=self.data,
            body_sidecars=lambda: self._sidecar_registry._body_sidecars(),
            sqlite_connection_for_sidecar=self._sidecar_registry._sqlite_connection_for_sidecar,
            gaiji_info=self._gaiji_registry.gaiji_info,
            ga16=lambda: self._gaiji_registry.ga16,
        )

    def _entry_store_deps(self) -> EntryStoreDeps:
        return EntryStoreDeps(
            info=self.info,
            components_by_role=self.components_by_role,
            component_for_address=lambda address, role: self.component_for_address(address, role=role),
            data=self.data,
            relative_offset=self._relative_offset,
            qualified_address=lambda address, role: self._qualified_address(address, role=role),
            location_for_address=lambda address, role: self._location_for_address(address, role=role),
            honmon_component=self.honmon_component,
            decode_text_stream=lambda data, renderer_entry_backed: self._gaiji_registry._decode_text_stream(
                data,
                renderer_entry_backed=renderer_entry_backed,
            ),
            gaiji_mapping=lambda: self._gaiji_registry.gaiji.mapping,
            sidecar_file_candidates=self._sidecar_registry._sidecar_file_candidates,
            body_sidecars=lambda stop_after_body_resolver, allow_expensive: self._sidecar_registry._body_sidecars(
                stop_after_body_resolver=stop_after_body_resolver,
                allow_expensive=allow_expensive,
            ),
            sqlite_connection_for_sidecar=self._sidecar_registry._sqlite_connection_for_sidecar,
            indexes=lambda component=None: self._index_store.indexes(component=component),
            iter_index_rows_fast=lambda component, query, profile, budget, max_bytes, cancel: self._index_store._iter_index_rows_fast(
                component,
                query=query,
                profile=profile,
                budget=budget,
                max_bytes=max_bytes,
                cancel=cancel,
            ),
            make_hit=lambda hit_id, query, normalized_query, profile, component_name, row, matched_key, debug: self._index_store._make_hit(
                hit_id=hit_id,
                query=query,
                normalized_query=normalized_query,
                profile=profile,
                component_name=component_name,
                row=row,
                matched_key=matched_key,
                debug=debug,
            ),
            row_display_key=lambda row, backward: self._index_store._row_display_key(row, backward=backward),
            row_dedupe_key=lambda row: self._index_store._row_dedupe_key(row),
            is_backward_index=lambda component_name: self._index_store._is_backward_index(component_name),
            render_entry_html=lambda entry, profile, include_diagnostics: self._resource_resolver.render_entry_html(
                entry,
                profile=profile,
                include_diagnostics=include_diagnostics,
            ),
            render_entry_text=lambda entry: self._resource_resolver.render_entry_text(entry),
        )

    def _index_store_deps(self) -> IndexStoreDeps:
        return IndexStoreDeps(
            info=self.info,
            component=self.component,
            components_by_role=self.components_by_role,
            component_for_address=lambda address, role: self.component_for_address(address, role=role),
            data=self.data,
            expanded=self.expanded,
            relative_offset=self._relative_offset,
            qualified_address=lambda address, role: self._qualified_address(address, role=role),
            location_for_address=lambda address, role: self._location_for_address(address, role=role),
            gaiji_mapping=lambda: self._gaiji_registry.gaiji.mapping,
            decode_component=self._gaiji_registry.decode_component,
            decode_text_stream=lambda data, renderer_entry_backed: self._gaiji_registry._decode_text_stream(
                data,
                renderer_entry_backed=renderer_entry_backed,
            ),
            body_source=lambda debug: self._entry_store.body_source(debug=debug),
        )

    def _make_surface_store(self) -> SurfaceStore:
        return SurfaceStore(
            SurfaceStoreDeps(
                info=self.info,
                component=self.component,
                components_by_role=self.components_by_role,
            )
        )

    def __enter__(self) -> "LogoVistaPackage":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._component_store.clear()
        self._index_store.clear()
        self._entry_store.clear()
        self._gaiji_registry.clear()
        self._sidecar_registry.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("LogoVistaPackage is closed")

    @property
    def title(self) -> str:
        return self.catalog.title

    @property
    def dict_id(self) -> str:
        return self.catalog.dict_id

    def package_family(self) -> PackageFamily:
        return self.info.family

    @property
    def components(self) -> tuple[Component, ...]:
        return self._component_store.components

    def component(self, name: str) -> Component | None:
        self._ensure_open()
        return self._component_store.component(name)

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
        self._ensure_open()
        return self._component_store.components_by_role(role)

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None:
        self._ensure_open()
        return self._component_store.component_for_address(address, role=role)

    def data(self, component: str | Component) -> SsedData:
        self._ensure_open()
        return self._component_store.data(component)

    def expanded(self, component: str | Component) -> bytes:
        self._ensure_open()
        return self._component_store.expanded(component)

    def read_address(self, address: Address, size: int, *, role: ComponentRole | None = None) -> bytes:
        self._ensure_open()
        return self._component_store.read_address(address, size, role=role)

    @staticmethod
    def _relative_offset(component: Component, address: Address) -> int:
        return ComponentStore.relative_offset(component, address)

    def _qualified_address(self, address: Address, *, role: ComponentRole | None = None) -> Address:
        self._ensure_open()
        return self._component_store.qualified_address(address, role=role)

    def _location_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Location:
        self._ensure_open()
        return self._component_store.location_for_address(address, role=role)

    def honmon_component(self) -> Component | None:
        self._ensure_open()
        return self._component_store.honmon_component()

    @property
    def gaiji_sources(self) -> GaijiSources:
        return self._gaiji_registry.gaiji_sources

    @property
    def gaiji(self) -> GaijiMap:
        return self._gaiji_registry.gaiji

    @property
    def ga16(self) -> tuple[Ga16Resource, ...]:
        return self._gaiji_registry.ga16

    @property
    def gaiji_images(self) -> tuple[ImageGaijiResource, ...]:
        return self._gaiji_registry.gaiji_images

    @property
    def _tempdir(self) -> tempfile.TemporaryDirectory[str] | None:
        return self._sidecar_registry._tempdir

    @_tempdir.setter
    def _tempdir(self, value: tempfile.TemporaryDirectory[str] | None) -> None:
        self._sidecar_registry._tempdir = value

    def decode_component(self, component: str | Component):
        return self._gaiji_registry.decode_component(component)

    def gaiji_info(self, code_or_resource) -> object:
        return self._gaiji_registry.gaiji_info(code_or_resource)

    def gaiji_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        return self._gaiji_registry.gaiji_resources(limit=limit)

    def body_source(self, *, debug: bool = False) -> BodySourceInfo:
        return self._entry_store.body_source(debug=debug)

    def validate_body_source(self) -> BodySourceInfo:
        return self._entry_store.validate_body_source()

    def supports_entry_rendering(self) -> bool:
        return self._entry_store.supports_entry_rendering()

    def inspect_body_pointer(self, address: Address) -> BodyPointerInspection:
        return self._entry_store.inspect_body_pointer(address)

    def render_entry_html(
        self,
        entry: Entry,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self._resource_resolver.render_entry_html(
            entry,
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_entry_text(self, entry: Entry) -> str:
        return self._resource_resolver.render_entry_text(entry)

    def entry_document(self, entry: Entry):
        return self._resource_resolver.entry_document(entry)

    def entry_diagnostics(self, entry: Entry) -> tuple[Diagnostic, ...]:
        return self._resource_resolver.entry_diagnostics(entry)

    def resource_info(self, resource: ResourceRef | JsonObject | ResourceLocation) -> ResourceLocation:
        return self._resource_resolver.resource_info(resource)

    def resource_bytes(self, resource: ResourceRef | JsonObject | ResourceLocation) -> bytes | None:
        return self._resource_resolver.resource_bytes(resource)

    def resource_record_bytes(self, resource: ResourceRef | JsonObject | ResourceLocation) -> bytes | None:
        return self._resource_resolver.resource_record_bytes(resource)

    def resolve_resource(self, resource: ResourceRef | JsonObject | ResourceLocation) -> ResourceLocation:
        return self._resource_resolver.resolve_resource(resource)

    def sidecar_media_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        return self._sidecar_registry.sidecar_media_resources(limit=limit)

    def sidecar_address_matches(self, address: Address, *, limit: int = 32) -> tuple[SidecarAddressMatch, ...]:
        return self._sidecar_registry.sidecar_address_matches(address, limit=limit)

    def dictionaries(self) -> tuple[Dictionary, ...]:
        """Return logical dictionaries contained in this package."""

        if self._dictionaries is None:
            self._dictionaries = (
                Dictionary(
                    dictionary_id=self.info.dict_id or self.catalog.dict_id,
                    title=self.info.title or self.catalog.title,
                    entry_store=self._entry_store,
                    index_store=self._index_store,
                    surface_store=self._surface_store,
                ),
            )
        return self._dictionaries

    def dictionary(self) -> Dictionary:
        """Return the primary dictionary for current SSED packages."""

        return self.dictionaries()[0]

    def iter_entries(self, *, limit: int | None = None, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None):
        return self.dictionary().iter_entries(limit=limit, max_bytes=max_bytes, cancel=cancel)

    def entry_at(self, address: Address, *, max_bytes: int = 64 * 1024) -> Entry:
        return self.dictionary().entry_at(address, max_bytes=max_bytes)

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        return self.dictionary().titles(component=component, limit=limit)

    def indexes(self, component: str | Component | None = None) -> dict[str, IndexParse]:
        return self.dictionary().indexes(component=component)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
        debug: bool = False,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> SearchResults:
        return self.dictionary().search(query, limit=limit, profile=profile, debug=debug, max_bytes=max_bytes, cancel=cancel)

    def search_entries(
        self,
        term: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
    ) -> list[Entry]:
        return [self.entry_for_hit(hit) for hit in self.search(term, limit=limit, profile=profile).hits]

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        return self.dictionary().entry_for_hit(hit)

    def entry_view_for_hit(self, hit: SearchHit) -> EntryView:
        return self.dictionary().entry_view_for_hit(hit)

    def entry_view_at(self, address: Address, *, max_bytes: int = 64 * 1024) -> EntryView:
        return self.dictionary().entry_view_at(address, max_bytes=max_bytes)

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.render_entry_html(
            self.entry_for_hit(hit),
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.render_entry_text(self.entry_for_hit(hit))

    def home_surfaces(self) -> tuple[HomeSurface, ...]:
        return self._surface_store.home_surfaces()

    def iter_index_rows(self, component: Component, *, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None):
        """Iterate parsed native index rows from one component."""

        yield from self._index_store._iter_index_rows_fast(component, max_bytes=max_bytes, cancel=cancel)

    def _iter_index_rows_fast(self, component: Component, *, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None):
        yield from self._index_store._iter_index_rows_fast(component, max_bytes=max_bytes, cancel=cancel)

    def _make_hit(
        self,
        *,
        hit_id: int,
        query: str,
        normalized_query: str,
        profile: SearchProfile,
        component_name: str,
        row: IndexRow,
        matched_key: str,
        debug: bool = False,
    ) -> SearchHit:
        return self._index_store._make_hit(
            hit_id=hit_id,
            query=query,
            normalized_query=normalized_query,
            profile=profile,
            component_name=component_name,
            row=row,
            matched_key=matched_key,
            debug=debug,
        )

    def _row_display_key(self, row: IndexRow, *, backward: bool = False) -> str:
        return self._index_store._row_display_key(row, backward=backward)

    def _row_dedupe_key(self, row: IndexRow) -> tuple[int, int, int, int]:
        return self._index_store._row_dedupe_key(row)

    def _is_backward_index(self, component_name: str) -> bool:
        return self._index_store._is_backward_index(component_name)

    def _body_pointer_offsets(self, component: Component) -> list[int]:
        return self._entry_store._body_pointer_offsets(component)

    def _body_sidecars(self, *, stop_after_body_resolver: bool = False, allow_expensive: bool = True) -> tuple[SidecarInfo, ...]:
        return self._sidecar_registry._body_sidecars(
            stop_after_body_resolver=stop_after_body_resolver,
            allow_expensive=allow_expensive,
        )

    def _sidecar_file_candidates(self) -> list[Path]:
        return self._sidecar_registry._sidecar_file_candidates()

    def _entry_from_body_stream_pointer(self, hit: SearchHit) -> Entry:
        return self._entry_store.body_stream_entry_for_hit(hit)

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        return self._entry_store.sidecar_entry_for_hit(hit, sidecar, inspection)

    def _placeholder_entry(
        self,
        address: Address,
        *,
        headword: str,
        code: str,
        message: str,
        severity: Severity = Severity.WARNING,
        details: JsonObject | None = None,
        placeholder_text: str = "Entry body is not yet supported for this LogoVista body source.",
    ) -> Entry:
        return self._entry_store.placeholder_entry(
            address,
            headword=headword,
            code=code,
            message=message,
            severity=severity,
            details=details,
            placeholder_text=placeholder_text,
        )

    def sidecars(
        self,
        *,
        stop_after_body_resolver: bool = False,
        allow_expensive: bool = True,
    ) -> tuple[SidecarInfo, ...]:
        """Return classified package sidecars for inspection/audit callers."""

        return self._sidecar_registry._body_sidecars(
            stop_after_body_resolver=stop_after_body_resolver,
            allow_expensive=allow_expensive,
        )

    def sidecar_candidate_paths(self) -> tuple[Path, ...]:
        """Return package-local sidecar candidate paths for audit/inspection callers."""

        return tuple(self._sidecar_registry._sidecar_file_candidates())

    def summary(self, *, debug: bool = False) -> JsonObject:
        """Return a package summary for the reader CLI."""

        data: JsonObject = {
            "package": self.info.to_dict(),
            "components": [component.to_dict() for component in self.components],
        }
        if not debug:
            data["notes"] = ["fast summary; use --debug for body-source, gaiji, and resource evidence"]
            return data

        data["body_source"] = self.body_source(debug=True).to_dict(debug=False)
        data["gaiji"] = {
            "records": len(self.gaiji.records),
            "mapped": len(self.gaiji.mapping),
            "paths": [str(path) for path in self.gaiji.paths],
            "image_resources": len(self.gaiji_images),
            "plist_unicode_mappings": self.gaiji.plist_unicode_mappings,
            "plist_mapping_ambiguous": self.gaiji.plist_mapping_ambiguous,
            "plist_parse_failures": self.gaiji.plist_parse_failures,
            "ga16": [
                {
                    "path": str(resource.path),
                    "width": resource.width,
                    "height": resource.height,
                    "start_code": f"{resource.start_code:04x}",
                    "count": resource.count,
                    "glyph_bytes": resource.glyph_bytes,
                    "section": resource.section,
                }
                for resource in self.ga16
            ],
        }
        return data
