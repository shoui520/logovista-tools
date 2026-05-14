"""High-level package API."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Callable

from .body_source import BodyPointerInspection, BodySourceInfo, SidecarAddressMatch, SidecarInfo
from .detect import detect_family
from .diagnostics import Location, Severity
from .document import ResourceRef
from .dictionary import Dictionary
from .errors import UnsupportedPackageError
from .gaiji import Ga16Resource, GaijiMap, GaijiSources, ImageGaijiResource
from .index import IndexParse, IndexRow
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile
from .render import HtmlProfile
from .resources import ResourceLocation
from .search import SearchHit, SearchResults
from .ssed import BLOCK_SIZE, Catalog, SsedData, find_file_case_insensitive, parse_catalog
from .package_entries import PackageEntryMixin
from .package_gaiji import PackageGaijiMixin
from .package_resources import PackageResourceMixin
from .package_search import PackageSearchMixin
from .package_sidecars import PackageSidecarMixin


def open_package(path: str | Path) -> "LogoVistaPackage":
    info = detect_family(Path(path))
    if info.family != PackageFamily.SSED:
        raise UnsupportedPackageError(f"{info.family.value} package support is deferred")
    if info.idx_path is None:
        raise UnsupportedPackageError("SSED package did not expose an IDX path")
    return LogoVistaPackage(info.idx_path)


class _PackageServiceBase:
    """Base class for explicit package services.

    Services own their own caches and call across sibling services through
    concrete methods below. This keeps the Python proof-of-concept close to
    Rust's package-with-substores shape without relying on dynamic
    ``__getattr__`` dispatch.
    """

    def __init__(self, package: "LogoVistaPackage") -> None:
        self._package = package

    @property
    def catalog(self) -> Catalog:
        return self._package.catalog

    @property
    def info(self) -> PackageInfo:
        return self._package.info

    @property
    def components(self) -> tuple[Component, ...]:
        return self._package.components

    def _ensure_open(self) -> None:
        self._package._ensure_open()

    def component(self, name: str) -> Component | None:
        return self._package.component(name)

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
        return self._package.components_by_role(role)

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None:
        return self._package.component_for_address(address, role=role)

    def data(self, component: str | Component) -> SsedData:
        return self._package.data(component)

    def expanded(self, component: str | Component) -> bytes:
        return self._package.expanded(component)

    def read_address(self, address: Address, size: int, *, role: ComponentRole | None = None) -> bytes:
        return self._package.read_address(address, size, role=role)

    def _relative_offset(self, component: Component, address: Address) -> int:
        return self._package._relative_offset(component, address)

    def _qualified_address(self, address: Address, *, role: ComponentRole | None = None) -> Address:
        return self._package._qualified_address(address, role=role)

    def _location_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Location:
        return self._package._location_for_address(address, role=role)

    def honmon_component(self) -> Component | None:
        return self._package.honmon_component()

    @property
    def gaiji_sources(self) -> GaijiSources:
        return self._package._gaiji_registry.gaiji_sources

    @property
    def gaiji(self) -> GaijiMap:
        return self._package._gaiji_registry.gaiji

    @property
    def ga16(self) -> tuple[Ga16Resource, ...]:
        return self._package._gaiji_registry.ga16

    @property
    def gaiji_images(self) -> tuple[ImageGaijiResource, ...]:
        return self._package._gaiji_registry.gaiji_images

    @property
    def _gaiji_image_by_code(self) -> dict[str, ImageGaijiResource]:
        return self._package._gaiji_registry._gaiji_image_by_code

    def decode_component(self, component: str | Component):
        return self._package._gaiji_registry.decode_component(component)

    def _decode_text_stream(self, data: bytes, *, renderer_entry_backed: bool = False):
        return self._package._gaiji_registry._decode_text_stream(data, renderer_entry_backed=renderer_entry_backed)

    def _annotate_gaiji_spans(self, spans, *, renderer_entry_backed: bool = False):
        return self._package._gaiji_registry._annotate_gaiji_spans(spans, renderer_entry_backed=renderer_entry_backed)

    def gaiji_info(self, code_or_resource):
        return self._package._gaiji_registry.gaiji_info(code_or_resource)

    def gaiji_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        return self._package._gaiji_registry.gaiji_resources(limit=limit)

    def _body_sidecars(self, *, stop_after_body_resolver: bool = False, allow_expensive: bool = True) -> tuple[SidecarInfo, ...]:
        return self._package._sidecar_registry._body_sidecars(
            stop_after_body_resolver=stop_after_body_resolver,
            allow_expensive=allow_expensive,
        )

    def _sidecar_file_candidates(self) -> list[Path]:
        return self._package._sidecar_registry._sidecar_file_candidates()

    def _sqlite_connection_for_sidecar(self, path: Path, storage: str):
        return self._package._sidecar_registry._sqlite_connection_for_sidecar(path, storage)

    def sidecar_media_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        return self._package._sidecar_registry.sidecar_media_resources(limit=limit)

    def sidecar_address_matches(self, address: Address, *, limit: int = 32) -> tuple[SidecarAddressMatch, ...]:
        return self._package._sidecar_registry.sidecar_address_matches(address, limit=limit)

    def body_source(self, *, debug: bool = False) -> BodySourceInfo:
        return self._package._entry_store.body_source(debug=debug)

    def inspect_body_pointer(self, address: Address) -> BodyPointerInspection:
        return self._package._entry_store.inspect_body_pointer(address)

    def entry_at(self, address: Address, *, max_bytes: int = 64 * 1024) -> Entry:
        return self._package._entry_store.entry_at(address, max_bytes=max_bytes)

    def _try_entry_from_dense_sidecar(self, hit: SearchHit, source: BodySourceInfo | None = None) -> Entry | None:
        return self._package._entry_store._try_entry_from_dense_sidecar(hit, source)

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        return self._package._entry_store._entry_from_sidecar(hit, sidecar, inspection)

    def _entry_or_empty_body_placeholder(self, entry: Entry, hit: SearchHit) -> Entry:
        return self._package._entry_store._entry_or_empty_body_placeholder(entry, hit)

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
        return self._package._entry_store._placeholder_entry(
            address,
            headword=headword,
            code=code,
            message=message,
            severity=severity,
            details=details,
            placeholder_text=placeholder_text,
        )

    def render_entry_html(
        self,
        entry: Entry,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self._package._resource_resolver.render_entry_html(
            entry,
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_entry_text(self, entry: Entry) -> str:
        return self._package._resource_resolver.render_entry_text(entry)

    def indexes(self, component: str | Component | None = None) -> dict[str, IndexParse]:
        return self._package._index_store.indexes(component=component)

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        return self._package._index_store.titles(component=component, limit=limit)

    def _body_pointer_offsets_fast(self, component: Component, *, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None) -> list[int]:
        return self._package._entry_store._body_pointer_offsets_fast(component, max_bytes=max_bytes, cancel=cancel)

    def _body_pointer_offsets(self, component: Component) -> list[int]:
        return self._package._entry_store._body_pointer_offsets(component)

    def _iter_index_rows_fast(
        self,
        component: Component,
        *,
        query: str | None = None,
        profile: SearchProfile | None = None,
        budget=None,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ):
        return self._package._index_store._iter_index_rows_fast(
            component,
            query=query,
            profile=profile,
            budget=budget,
            max_bytes=max_bytes,
            cancel=cancel,
        )

    def _make_hit(self, **kwargs) -> SearchHit:
        return self._package._index_store._make_hit(**kwargs)

    def _row_display_key(self, row: IndexRow, *, backward: bool = False) -> str:
        return self._package._index_store._row_display_key(row, backward=backward)

    def _row_dedupe_key(self, row: IndexRow) -> tuple[int, int, int, int]:
        return self._package._index_store._row_dedupe_key(row)

    def _is_backward_index(self, component_name: str) -> bool:
        return self._package._index_store._is_backward_index(component_name)

    def resolve_title(self, address: Address, *, max_bytes: int = 4096):
        return self._package._index_store.resolve_title(address, max_bytes=max_bytes)

    def _media_info_base(self, resource_id: str, kind: str) -> JsonObject:
        return self._package._resource_resolver._media_info_base(resource_id, kind)

    def resource_info(self, resource) -> ResourceLocation:
        return self._package._resource_resolver.resource_info(resource)

    def resource_bytes(self, resource) -> bytes | None:
        return self._package._resource_resolver.resource_bytes(resource)


class GaijiRegistry(PackageGaijiMixin, _PackageServiceBase):
    """Gaiji mapping and resource discovery service."""

    def __init__(self, package: "LogoVistaPackage") -> None:
        super().__init__(package)
        self._gaiji: GaijiMap | None = None
        self._gaiji_sources: GaijiSources | None = None
        self._ga16: tuple[Ga16Resource, ...] | None = None
        self._gaiji_images: tuple[ImageGaijiResource, ...] | None = None
        self._gaiji_image_by_code_cache: dict[str, ImageGaijiResource] | None = None
        self._gaiji_image_info_cache: dict[str, JsonObject | None] = {}
        self._gaiji_glyph_info_cache: dict[tuple[str, bool], JsonObject | None] = {}

    def clear(self) -> None:
        self._gaiji_image_info_cache.clear()
        self._gaiji_glyph_info_cache.clear()


class SidecarRegistry(PackageSidecarMixin, _PackageServiceBase):
    """SQLite sidecar discovery/classification service."""

    def __init__(self, package: "LogoVistaPackage") -> None:
        super().__init__(package)
        self._sqlite_sidecar_cache: dict[str, Path] = {}
        self._sqlite_connection_cache: dict[tuple[str, str], object] = {}
        self._sqlite_schema_cache: dict[str, SidecarInfo | None] = {}
        self._sidecar_file_candidates_cache: list[Path] | None = None
        self._body_sidecars_cache: dict[tuple[bool, bool], tuple[SidecarInfo, ...]] = {}
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None

    def close(self) -> None:
        for connection in self._sqlite_connection_cache.values():
            try:
                connection.close()
            except Exception:
                pass
        self._sqlite_connection_cache.clear()
        self._sqlite_sidecar_cache.clear()
        self._sqlite_schema_cache.clear()
        self._sidecar_file_candidates_cache = None
        self._body_sidecars_cache.clear()
        tempdir = self._tempdir
        self._tempdir = None
        if tempdir is not None:
            tempdir.cleanup()


class EntryStore(PackageEntryMixin, _PackageServiceBase):
    """Entry slicing, dense-HONMON inspection, and body resolution service."""

    def __init__(self, package: "LogoVistaPackage") -> None:
        super().__init__(package)
        self._marker_cache: dict[str, list[int]] = {}
        self._body_pointer_cache: dict[str, list[int]] = {}
        self._body_source_cache: dict[bool, BodySourceInfo] = {}

    def clear(self) -> None:
        self._marker_cache.clear()
        self._body_pointer_cache.clear()
        self._body_source_cache.clear()


class IndexStore(PackageSearchMixin, _PackageServiceBase):
    """Title/index/search service."""

    def __init__(self, package: "LogoVistaPackage") -> None:
        super().__init__(package)
        self._index_cache: dict[str, IndexParse] = {}

    def clear(self) -> None:
        self._index_cache.clear()


class ResourceResolver(PackageResourceMixin, _PackageServiceBase):
    """Document rendering and explicit resource resolution service."""


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
        self.components: tuple[Component, ...] = tuple(
            replace(component, path=find_file_case_insensitive(idx_path.parent, component.name))
            for component in self.catalog.components
        )
        self._component_by_name = {component.name.lower(): component for component in self.components}
        self._data_cache: dict[str, SsedData] = {}
        self._closed = False
        self._gaiji_registry = GaijiRegistry(self)
        self._sidecar_registry = SidecarRegistry(self)
        self._entry_store = EntryStore(self)
        self._index_store = IndexStore(self)
        self._resource_resolver = ResourceResolver(self)
        self._dictionaries: tuple[Dictionary, ...] | None = None

    def __enter__(self) -> "LogoVistaPackage":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._data_cache.clear()
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

    def component(self, name: str) -> Component | None:
        self._ensure_open()
        return self._component_by_name.get(name.lower())

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
        self._ensure_open()
        return tuple(component for component in self.components if component.role == role)

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None:
        if address.component:
            component = self.component(address.component)
            if component is not None:
                return component
        candidates = self.components_by_role(role) if role is not None else self.components
        for component in candidates:
            if component.start_block <= address.block <= component.end_block:
                return component
        return None

    def data(self, component: str | Component) -> SsedData:
        self._ensure_open()
        name = component.name if isinstance(component, Component) else component
        key = name.lower()
        if key not in self._data_cache:
            item = self.component(name)
            if item is None:
                raise KeyError(name)
            if item.path is None:
                raise FileNotFoundError(name)
            self._data_cache[key] = SsedData(item.path)
        return self._data_cache[key]

    def expanded(self, component: str | Component) -> bytes:
        self._ensure_open()
        return self.data(component).expand()

    def read_address(self, address: Address, size: int, *, role: ComponentRole | None = None) -> bytes:
        component = self.component_for_address(address, role=role)
        if component is None:
            raise KeyError(f"no component contains address {address}")
        offset = (address.block - component.start_block) * BLOCK_SIZE + address.offset
        return self.data(component).read(offset, size)

    @staticmethod
    def _relative_offset(component: Component, address: Address) -> int:
        return (address.block - component.start_block) * BLOCK_SIZE + address.offset

    def _qualified_address(self, address: Address, *, role: ComponentRole | None = None) -> Address:
        if address.component:
            return address
        component = self.component_for_address(address, role=role)
        if component is None:
            return address
        return Address(address.block, address.offset, component.name)

    def _location_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Location:
        qualified = self._qualified_address(address, role=role)
        return Location(component=qualified.component, block=qualified.block, offset=qualified.offset)

    def honmon_component(self) -> Component | None:
        candidates = self.components_by_role(ComponentRole.HONMON)
        return candidates[0] if candidates else None

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

    def entry_diagnostics(self, entry: Entry) -> tuple[object, ...]:
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
                    package=self,
                    dictionary_id=self.info.dict_id or self.catalog.dict_id,
                    title=self.info.title or self.catalog.title,
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

    def search_index(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[JsonObject]:
        return self.dictionary().search_index(term, limit=limit, profile=profile)

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

    def iter_index_rows(self, component: Component, *, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None):
        """Iterate parsed native index rows from one component."""

        yield from self._index_store._iter_index_rows_fast(component, max_bytes=max_bytes, cancel=cancel)

    def _iter_index_rows_fast(self, component: Component, *, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None):
        yield from self._index_store._iter_index_rows_fast(component, max_bytes=max_bytes, cancel=cancel)

    def _make_hit(self, **kwargs) -> SearchHit:
        return self._index_store._make_hit(**kwargs)

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
        return self._index_store._entry_from_body_stream_pointer(hit)

    def _try_entry_from_dense_sidecar(self, hit: SearchHit, source: BodySourceInfo | None = None) -> Entry | None:
        return self._entry_store._try_entry_from_dense_sidecar(hit, source)

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        return self._entry_store._entry_from_sidecar(hit, sidecar, inspection)

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
        return self._entry_store._placeholder_entry(
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
