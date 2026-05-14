"""High-level package API."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Callable

from .body_source import BodySourceInfo, SidecarInfo
from .detect import detect_family
from .diagnostics import Location
from .dictionary import Dictionary
from .errors import UnsupportedPackageError
from .gaiji import Ga16Resource, GaijiMap, GaijiSources, ImageGaijiResource
from .index import IndexParse, IndexRow
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile
from .render import HtmlProfile
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


class _PackageService:
    """Shared-state service wrapper used during the pre-Rust composition step."""

    def __init__(self, package: "LogoVistaPackage") -> None:
        object.__setattr__(self, "_package", package)

    def __getattribute__(self, name: str):
        if name in {"_package", "__class__", "__dict__", "__setattr__", "__getattr__", "__getattribute__"}:
            return object.__getattribute__(self, name)
        package = object.__getattribute__(self, "_package")
        package_dict = object.__getattribute__(package, "__dict__")
        if name in package_dict:
            return package_dict[name]
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_package"), name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_package":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_package"), name, value)


class GaijiRegistry(PackageGaijiMixin, _PackageService):
    """Gaiji mapping and resource discovery service."""


class SidecarRegistry(PackageSidecarMixin, _PackageService):
    """SQLite sidecar discovery/classification service."""


class EntryStore(PackageEntryMixin, _PackageService):
    """Entry slicing, dense-HONMON inspection, and body resolution service."""


class IndexStore(PackageSearchMixin, _PackageService):
    """Title/index/search service."""


class ResourceResolver(PackageResourceMixin, _PackageService):
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
        self._gaiji: GaijiMap | None = None
        self._gaiji_sources: GaijiSources | None = None
        self._ga16: tuple[Ga16Resource, ...] | None = None
        self._gaiji_images: tuple[ImageGaijiResource, ...] | None = None
        self._gaiji_image_by_code_cache: dict[str, ImageGaijiResource] | None = None
        self._gaiji_image_info_cache: dict[str, JsonObject | None] = {}
        self._gaiji_glyph_info_cache: dict[tuple[str, bool], JsonObject | None] = {}
        self._component_by_name = {component.name.lower(): component for component in self.components}
        self._data_cache: dict[str, SsedData] = {}
        self._index_cache: dict[str, IndexParse] = {}
        self._marker_cache: dict[str, list[int]] = {}
        self._body_pointer_cache: dict[str, list[int]] = {}
        self._body_source_cache: dict[bool, BodySourceInfo] = {}
        self._sqlite_sidecar_cache: dict[str, Path] = {}
        self._sqlite_connection_cache: dict[tuple[str, str], object] = {}
        self._sqlite_schema_cache: dict[str, SidecarInfo | None] = {}
        self._sidecar_file_candidates_cache: list[Path] | None = None
        self._body_sidecars_cache: dict[tuple[bool, bool], tuple[SidecarInfo, ...]] = {}
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._closed = False
        self._gaiji_registry = GaijiRegistry(self)
        self._sidecar_registry = SidecarRegistry(self)
        self._entry_store = EntryStore(self)
        self._index_store = IndexStore(self)
        self._resource_resolver = ResourceResolver(self)
        self._services: tuple[_PackageService, ...] = (
            self._gaiji_registry,
            self._sidecar_registry,
            self._entry_store,
            self._index_store,
            self._resource_resolver,
        )
        self._dictionaries: tuple[Dictionary, ...] | None = None

    def __getattr__(self, name: str):
        for service in self._services:
            if getattr(type(service), name, None) is not None:
                return getattr(service, name)
        raise AttributeError(f"{type(self).__name__!s} object has no attribute {name!r}")

    def __enter__(self) -> "LogoVistaPackage":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._data_cache.clear()
        self._index_cache.clear()
        self._marker_cache.clear()
        self._body_pointer_cache.clear()
        self._body_source_cache.clear()
        self._gaiji_image_info_cache.clear()
        self._gaiji_glyph_info_cache.clear()
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
        return self.dictionary().search_entries(term, limit=limit, profile=profile)

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        return self.dictionary().entry_for_hit(hit)

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.dictionary().render_hit_html(hit, profile=profile, include_diagnostics=include_diagnostics)

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.dictionary().render_hit_text(hit)

    def iter_index_rows(self, component: Component, *, max_bytes: int | None = None, cancel: Callable[[], bool] | None = None):
        """Iterate parsed native index rows from one component."""

        yield from self._iter_index_rows_fast(component, max_bytes=max_bytes, cancel=cancel)

    def sidecars(
        self,
        *,
        stop_after_body_resolver: bool = False,
        allow_expensive: bool = True,
    ) -> tuple[SidecarInfo, ...]:
        """Return classified package sidecars for inspection/audit callers."""

        return self._body_sidecars(
            stop_after_body_resolver=stop_after_body_resolver,
            allow_expensive=allow_expensive,
        )

    def sidecar_candidate_paths(self) -> tuple[Path, ...]:
        """Return package-local sidecar candidate paths for audit/inspection callers."""

        return tuple(self._sidecar_file_candidates())

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
