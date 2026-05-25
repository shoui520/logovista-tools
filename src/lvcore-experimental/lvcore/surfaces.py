"""Reader home-surface detection for SSED packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .diagnostics import Diagnostic, DiagnosticArea, Severity
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, PackageInfo
from .reader import EntryTarget, EntryTargetKind, HomeSurface, HomeSurfaceKind, ReaderStatus
from .ssed import CaseFoldedDirectory, is_metadata_noise_path


class SurfaceHost(Protocol):
    @property
    def info(self) -> PackageInfo: ...

    def component(self, name: str) -> Component | None: ...

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]: ...


@dataclass(frozen=True)
class SurfaceStoreDeps:
    info: PackageInfo
    component: Callable[[str], Component | None]
    components_by_role: Callable[[ComponentRole], tuple[Component, ...]]


class SurfaceStore:
    """Home/interface surface detector for one SSED dictionary package."""

    def __init__(self, deps: SurfaceStoreDeps) -> None:
        self._deps = deps

    @property
    def info(self) -> PackageInfo:
        return self._deps.info

    def component(self, name: str) -> Component | None:
        return self._deps.component(name)

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
        return self._deps.components_by_role(role)

    def home_surfaces(self) -> tuple[HomeSurface, ...]:
        return _home_surfaces_from_host(self)


def _surface_diagnostic(*, code: str, area: DiagnosticArea, message: str, details: JsonObject | None = None) -> Diagnostic:
    return Diagnostic(
        severity=Severity.INFO,
        area=area,
        code=code,
        message=message,
        details=details or {},
    )


def _visible_root_entries(package: SurfaceHost) -> tuple[Path, ...]:
    try:
        children = package.info.root.iterdir()
    except OSError:
        return ()
    return tuple(sorted((path for path in children if not is_metadata_noise_path(path)), key=lambda path: path.name.casefold()))


def home_surfaces_for_package(package: SurfaceHost) -> tuple[HomeSurface, ...]:
    return _home_surfaces_from_host(package)


def _home_surfaces_from_host(package: SurfaceHost) -> tuple[HomeSurface, ...]:
    """Return ordered reader entry surfaces exposed by one SSED package."""

    surfaces: list[HomeSurface] = []
    package_id = package.info.dict_id
    lookup = CaseFoldedDirectory.from_path(package.info.root)

    menu = package.component("MENU.DIC")
    if menu is not None:
        surfaces.append(
            HomeSurface(
                kind=HomeSurfaceKind.MENU,
                status=ReaderStatus.AVAILABLE,
                label="MENU.DIC",
                target=EntryTarget(
                    kind=EntryTargetKind.MENU_TARGET,
                    package_id=package_id,
                    label="MENU.DIC",
                    address=Address(menu.start_block, 0, menu.name),
                    menu_id=menu.name,
                ),
                details={"component": menu.name},
            )
        )

    panel_files = tuple(
        path
        for name in ("Panels.xml", "Panels.dtd", "Panel.html", "Cell.html", "Panel")
        for path in (lookup.find(name),)
        if path is not None
    )
    if panel_files:
        diagnostic = _surface_diagnostic(
            code="visual_renderer_deferred",
            area=DiagnosticArea.RENDER,
            message="Panel surface is detected, but Panel interaction/rendering is not fully implemented in lvcore",
            details={"surface": "panels"},
        )
        surfaces.append(
            HomeSurface(
                kind=HomeSurfaceKind.PANELS,
                status=ReaderStatus.DEFERRED,
                label="Panels",
                target=EntryTarget(
                    kind=EntryTargetKind.PANEL_TARGET,
                    package_id=package_id,
                    label="Panels",
                    panel_id="Panels.xml",
                    diagnostics=(diagnostic,),
                ),
                diagnostics=(diagnostic,),
                details={"files": [path.name for path in panel_files]},
            )
        )

    hanrei_files = tuple(
        path
        for path in _visible_root_entries(package)
        if (
            path.name.casefold() == "hanrei"
            or path.name.casefold().startswith("hanrei.")
            or path.name.casefold().startswith("hanrei_")
        )
    )
    if hanrei_files:
        chm_only = all(path.is_file() and path.suffix.casefold() == ".chm" for path in hanrei_files)
        diagnostic = _surface_diagnostic(
            code="visual_renderer_deferred",
            area=DiagnosticArea.RENDER,
            message="HANREI surface is detected, but native HANREI presentation is deferred",
            details={"surface": "hanrei", "chm_only": chm_only},
        )
        surfaces.append(
            HomeSurface(
                kind=HomeSurfaceKind.HANREI,
                status=ReaderStatus.UNSUPPORTED if chm_only else ReaderStatus.DEFERRED,
                label="HANREI",
                target=EntryTarget(
                    kind=EntryTargetKind.HANREI_RESOURCE,
                    package_id=package_id,
                    label="HANREI",
                    resource_name=hanrei_files[0].name,
                    diagnostics=(diagnostic,),
                ),
                diagnostics=(diagnostic,),
                details={"files": [path.name for path in hanrei_files]},
            )
        )

    index_components = package.components_by_role(ComponentRole.INDEX)
    if index_components:
        surfaces.append(
            HomeSurface(
                kind=HomeSurfaceKind.NATIVE_SEARCH,
                status=ReaderStatus.AVAILABLE,
                label="Native index search",
                details={"component_count": len(index_components)},
            )
        )

    title_components = package.components_by_role(ComponentRole.TITLE)
    if title_components:
        surfaces.append(
            HomeSurface(
                kind=HomeSurfaceKind.TITLE_INDEX,
                status=ReaderStatus.AVAILABLE,
                label="Title/index browsing",
                details={"component_count": len(title_components)},
            )
        )

    diagnostic = _surface_diagnostic(
        code="visual_renderer_deferred",
        area=DiagnosticArea.INDEX,
        message="Full-text/fallback search is not a fast reader-core path",
        details={"surface": "fallback_search"},
    )
    surfaces.append(
        HomeSurface(
            kind=HomeSurfaceKind.FALLBACK_SEARCH,
            status=ReaderStatus.DEFERRED,
            label="Fallback search",
            diagnostics=(diagnostic,),
        )
    )
    return tuple(surfaces)
