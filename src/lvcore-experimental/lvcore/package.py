"""High-level package API."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile

from .body_source import BodySourceInfo, SidecarInfo
from .detect import detect_family
from .diagnostics import Location
from .errors import UnsupportedPackageError
from .gaiji import Ga16Resource, GaijiMap, ImageGaijiResource
from .index import IndexParse, IndexRow
from .model import Address, Component, ComponentRole, PackageFamily, PackageInfo
from .ssed import BLOCK_SIZE, Catalog, SsedData, find_file_case_insensitive, parse_catalog
from .package_entries import PackageEntryMixin
from .package_gaiji import PackageGaijiMixin
from .package_resources import PackageResourceMixin
from .package_search import PackageSearchMixin
from .package_sidecars import PackageSidecarMixin
from .package_utils import SearchValueRow
from .package_validation import PackageValidationMixin


def open_package(path: str | Path) -> "LogoVistaPackage":
    info = detect_family(Path(path))
    if info.family != PackageFamily.SSED:
        raise UnsupportedPackageError(f"{info.family.value} package support is deferred")
    if info.idx_path is None:
        raise UnsupportedPackageError("SSED package did not expose an IDX path")
    return LogoVistaPackage(info.idx_path)


class LogoVistaPackage(
    PackageGaijiMixin,
    PackageSidecarMixin,
    PackageEntryMixin,
    PackageSearchMixin,
    PackageResourceMixin,
    PackageValidationMixin,
):
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
        self._ga16: tuple[Ga16Resource, ...] | None = None
        self._gaiji_images: tuple[ImageGaijiResource, ...] | None = None
        self._gaiji_image_by_code_cache: dict[str, ImageGaijiResource] | None = None
        self._gaiji_image_info_cache: dict[str, dict[str, object] | None] = {}
        self._gaiji_glyph_info_cache: dict[tuple[str, bool], dict[str, object] | None] = {}
        self._component_by_name = {component.name.lower(): component for component in self.components}
        self._data_cache: dict[str, SsedData] = {}
        self._index_cache: dict[str, IndexParse] = {}
        self._search_value_cache: dict[str, tuple[SearchValueRow, ...]] = {}
        self._exact_search_cache: dict[str, dict[str, tuple[tuple[IndexRow, str], ...]]] = {}
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

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

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
        self._search_value_cache.clear()
        self._exact_search_cache.clear()
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
