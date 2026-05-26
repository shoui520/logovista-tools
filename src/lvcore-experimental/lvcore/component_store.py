"""SSED component lookup and data-reader ownership."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .diagnostics import Location
from .model import Address, Component, ComponentRole
from .ssed import BLOCK_SIZE, Catalog, SsedData, resolve_component_file


class ComponentStore:
    """Case-insensitive SSED component catalog and data-reader cache."""

    def __init__(self, idx_path: Path, catalog: Catalog) -> None:
        self.root = idx_path.parent
        self.components: tuple[Component, ...] = tuple(
            replace(component, path=resolve_component_file(idx_path.parent, component.name))
            for component in catalog.components
        )
        self._component_by_name = {component.name.casefold(): component for component in self.components}
        self._data_cache: dict[str, SsedData] = {}

    def clear(self) -> None:
        self._data_cache.clear()

    def component(self, name: str) -> Component | None:
        return self._component_by_name.get(name.casefold())

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
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
        name = component.name if isinstance(component, Component) else component
        key = name.casefold()
        if key not in self._data_cache:
            item = self.component(name)
            if item is None:
                raise KeyError(name)
            if item.path is None:
                raise FileNotFoundError(name)
            self._data_cache[key] = SsedData(item.path)
        return self._data_cache[key]

    def expanded(self, component: str | Component) -> bytes:
        return self.data(component).expand()

    def read_address(self, address: Address, size: int, *, role: ComponentRole | None = None) -> bytes:
        component = self.component_for_address(address, role=role)
        if component is None:
            raise KeyError(f"no component contains address {address}")
        offset = self.relative_offset(component, address)
        return self.data(component).read(offset, size)

    @staticmethod
    def relative_offset(component: Component, address: Address) -> int:
        return (address.block - component.start_block) * BLOCK_SIZE + address.offset

    def qualified_address(self, address: Address, *, role: ComponentRole | None = None) -> Address:
        if address.component:
            return address
        component = self.component_for_address(address, role=role)
        if component is None:
            return address
        return Address(address.block, address.offset, component.name)

    def location_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Location:
        qualified = self.qualified_address(address, role=role)
        return Location(component=qualified.component, block=qualified.block, offset=qualified.offset)

    def honmon_component(self) -> Component | None:
        candidates = self.components_by_role(ComponentRole.HONMON)
        return candidates[0] if candidates else None
