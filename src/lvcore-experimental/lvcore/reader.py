"""Reader-first application contracts.

These types are the forward-facing contract for a future Rust lvcore.  The
semantic ``Entry``/``EntryDocument`` path remains useful for diagnostics and
plain-text fallbacks, but dictionary applications should start from
``EntryTarget`` and ``EntryView``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .diagnostics import Diagnostic, DiagnosticArea, Location, Severity
from .json_types import JsonObject, JsonValue
from .model import Address, PackageFamily, PackageInfo

if TYPE_CHECKING:
    from .document import EntryDocument, ResourceRef

ENTRY_TARGET_SCHEMA = "lvcore.entry_target.v1"
ENTRY_TARGET_MODEL_VERSION = 1
ENTRY_TARGET_TOKEN_SCHEMA = "lvcore.entry_target_token.v1"
ENTRY_TARGET_TOKEN_MODEL_VERSION = 1
VISUAL_BODY_SCHEMA = "lvcore.visual_body.v1"
VISUAL_BODY_MODEL_VERSION = 1
ENTRY_VIEW_SCHEMA = "lvcore.entry_view.v1"
ENTRY_VIEW_MODEL_VERSION = 1
HOME_SURFACE_SCHEMA = "lvcore.home_surface.v1"
HOME_SURFACE_MODEL_VERSION = 1
READER_PACKAGE_SCHEMA = "lvcore.reader_package.v1"
READER_PACKAGE_MODEL_VERSION = 1


class EntryTargetKind(str, Enum):
    SSED_ADDRESS = "ssed_address"
    NATIVE_INDEX_HIT = "native_index_hit"
    DENSE_SIDECAR_ROW = "dense_sidecar_row"
    MENU_TARGET = "menu_target"
    PANEL_TARGET = "panel_target"
    HANREI_RESOURCE = "hanrei_resource"
    RESOURCE = "resource"
    UNSUPPORTED_FAMILY = "unsupported_family"
    UNKNOWN = "unknown"


class VisualBodyKind(str, Enum):
    PRESERVED_HTML = "preserved_html"
    SSED_STREAM = "ssed_stream"
    SEMANTIC_DOCUMENT = "semantic_document"
    UNSUPPORTED = "unsupported"


class ReaderStatus(str, Enum):
    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"
    DEFERRED = "deferred"


class HomeSurfaceKind(str, Enum):
    MENU = "menu"
    HANREI = "hanrei"
    PANELS = "panels"
    NATIVE_SEARCH = "native_search"
    FALLBACK_SEARCH = "fallback_search"
    TITLE_INDEX = "title_index"
    UNSUPPORTED_PACKAGE = "unsupported_package"


def _safe_json(value: object) -> JsonValue:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Address):
        return value.to_dict()
    if isinstance(value, Path):
        return value.name
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        out: JsonObject = {}
        for key, item in value.items():
            if isinstance(key, str):
                out[key] = _safe_json(item)
        return out
    return str(value)


def _stable_id(kind: EntryTargetKind, package_id: str | None, fields: JsonObject) -> str:
    material = {
        "kind": kind.value,
        "package_id": package_id,
        "fields": _safe_json(fields),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:20]


def _address_from_json(value: object) -> Address | None:
    if not isinstance(value, dict):
        return None
    try:
        block = int(value["block"])
        offset = int(value.get("offset", 0))
    except (KeyError, TypeError, ValueError):
        return None
    component_value = value.get("component")
    component = str(component_value) if component_value is not None else None
    return Address(block, offset, component)


@dataclass(frozen=True)
class EntryTarget:
    kind: EntryTargetKind
    package_id: str | None = None
    label: str | None = None
    address: Address | None = None
    title_address: Address | None = None
    index_component: str | None = None
    index_page: int | None = None
    index_row: int | None = None
    sidecar_name: str | None = None
    sidecar_table: str | None = None
    sidecar_row_id: str | None = None
    resource_name: str | None = None
    panel_id: str | None = None
    menu_id: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def target_id(self) -> str:
        return _stable_id(self.kind, self.package_id, self.to_token())

    @classmethod
    def native_index_hit(
        cls,
        *,
        package_id: str | None,
        label: str,
        body: Address,
        title: Address,
        index_component: str,
        page: int | None,
        row: int | None,
    ) -> "EntryTarget":
        return cls(
            kind=EntryTargetKind.NATIVE_INDEX_HIT,
            package_id=package_id,
            label=label,
            address=body,
            title_address=title,
            index_component=index_component,
            index_page=page,
            index_row=row,
        )

    @classmethod
    def ssed_address(cls, *, package_id: str | None, address: Address, label: str | None = None) -> "EntryTarget":
        return cls(kind=EntryTargetKind.SSED_ADDRESS, package_id=package_id, label=label, address=address)

    def with_sidecar_row(self, *, sidecar_name: str, table: str | None, row_id: str) -> "EntryTarget":
        return EntryTarget(
            kind=EntryTargetKind.DENSE_SIDECAR_ROW,
            package_id=self.package_id,
            label=self.label,
            address=self.address,
            title_address=self.title_address,
            index_component=self.index_component,
            index_page=self.index_page,
            index_row=self.index_row,
            sidecar_name=sidecar_name,
            sidecar_table=table,
            sidecar_row_id=row_id,
            diagnostics=self.diagnostics,
        )

    def to_token(self) -> JsonObject:
        token: JsonObject = {
            "schema": ENTRY_TARGET_TOKEN_SCHEMA,
            "model_version": ENTRY_TARGET_TOKEN_MODEL_VERSION,
            "kind": self.kind.value,
            "package_id": self.package_id,
            "label": self.label,
        }
        if self.address is not None:
            token["address"] = self.address.to_dict()
        if self.title_address is not None:
            token["title_address"] = self.title_address.to_dict()
        if self.index_component is not None or self.index_page is not None or self.index_row is not None:
            token["index"] = {
                "component": self.index_component,
                "page": self.index_page,
                "row": self.index_row,
            }
        if self.sidecar_name is not None or self.sidecar_table is not None or self.sidecar_row_id is not None:
            token["sidecar"] = {
                "name": self.sidecar_name,
                "table": self.sidecar_table,
                "row_id": self.sidecar_row_id,
            }
        if self.resource_name is not None:
            token["resource"] = {"name": self.resource_name}
        if self.panel_id is not None:
            token["panel"] = {"id": self.panel_id}
        if self.menu_id is not None:
            token["menu"] = {"id": self.menu_id}
        return token

    @classmethod
    def from_token(cls, value: object) -> "EntryTarget":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("entry target token is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("entry target token must be an object")
        raw_kind = str(value.get("kind") or EntryTargetKind.UNKNOWN.value)
        try:
            kind = EntryTargetKind(raw_kind)
        except ValueError:
            kind = EntryTargetKind.UNKNOWN
        index = value.get("index") if isinstance(value.get("index"), dict) else {}
        sidecar = value.get("sidecar") if isinstance(value.get("sidecar"), dict) else {}
        resource = value.get("resource") if isinstance(value.get("resource"), dict) else {}
        panel = value.get("panel") if isinstance(value.get("panel"), dict) else {}
        menu = value.get("menu") if isinstance(value.get("menu"), dict) else {}
        package_id_value = value.get("package_id")
        label_value = value.get("label")
        return cls(
            kind=kind,
            package_id=str(package_id_value) if package_id_value is not None else None,
            label=str(label_value) if label_value is not None else None,
            address=_address_from_json(value.get("address")),
            title_address=_address_from_json(value.get("title_address")),
            index_component=str(index["component"]) if index.get("component") is not None else None,
            index_page=int(index["page"]) if index.get("page") is not None else None,
            index_row=int(index["row"]) if index.get("row") is not None else None,
            sidecar_name=str(sidecar["name"]) if sidecar.get("name") is not None else None,
            sidecar_table=str(sidecar["table"]) if sidecar.get("table") is not None else None,
            sidecar_row_id=str(sidecar["row_id"]) if sidecar.get("row_id") is not None else None,
            resource_name=str(resource["name"]) if resource.get("name") is not None else None,
            panel_id=str(panel["id"]) if panel.get("id") is not None else None,
            menu_id=str(menu["id"]) if menu.get("id") is not None else None,
        )

    @classmethod
    def from_dict(cls, value: object) -> "EntryTarget":
        if not isinstance(value, dict):
            raise ValueError("entry target must be an object")
        token = value.get("token")
        if token is not None:
            return cls.from_token(token)
        # Compatibility for debug dictionaries emitted before public tokens.
        try:
            kind = EntryTargetKind(str(value.get("kind") or EntryTargetKind.UNKNOWN.value))
        except ValueError:
            kind = EntryTargetKind.UNKNOWN
        return cls(
            kind=kind,
            package_id=str(value["package_id"]) if value.get("package_id") is not None else None,
            label=str(value["label"]) if value.get("label") is not None else None,
            address=_address_from_json(value.get("address")),
            title_address=_address_from_json(value.get("title_address")),
            index_component=str(value["index_component"]) if value.get("index_component") is not None else None,
            index_page=int(value["index_page"]) if value.get("index_page") is not None else None,
            index_row=int(value["index_row"]) if value.get("index_row") is not None else None,
            sidecar_name=str(value["sidecar_name"]) if value.get("sidecar_name") is not None else None,
            sidecar_table=str(value["sidecar_table"]) if value.get("sidecar_table") is not None else None,
            sidecar_row_id=str(value["sidecar_row_id"]) if value.get("sidecar_row_id") is not None else None,
            resource_name=str(value["resource_name"]) if value.get("resource_name") is not None else None,
            panel_id=str(value["panel_id"]) if value.get("panel_id") is not None else None,
            menu_id=str(value["menu_id"]) if value.get("menu_id") is not None else None,
        )

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": ENTRY_TARGET_SCHEMA,
            "model_version": ENTRY_TARGET_MODEL_VERSION,
            "id": self.target_id,
            "kind": self.kind.value,
            "package_id": self.package_id,
            "label": self.label,
            "token": self.to_token(),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if not debug:
            return data
        data["address"] = self.address.to_dict() if self.address else None
        data["title_address"] = self.title_address.to_dict() if self.title_address else None
        data["index_component"] = self.index_component
        data["index_page"] = self.index_page
        data["index_row"] = self.index_row
        data["sidecar_name"] = self.sidecar_name
        data["sidecar_table"] = self.sidecar_table
        data["sidecar_row_id"] = self.sidecar_row_id
        data["resource_name"] = self.resource_name
        data["panel_id"] = self.panel_id
        data["menu_id"] = self.menu_id
        return data


@dataclass(frozen=True)
class VisualBody:
    kind: VisualBodyKind
    status: ReaderStatus
    html: str | None = None
    text: str | None = None
    component: str | None = None
    address: Address | None = None
    end_address: Address | None = None
    renderer_profile: str | None = None
    source: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": VISUAL_BODY_SCHEMA,
            "model_version": VISUAL_BODY_MODEL_VERSION,
            "kind": self.kind.value,
            "status": self.status.value,
            "source": self.source,
            "renderer_profile": self.renderer_profile,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if self.kind == VisualBodyKind.PRESERVED_HTML:
            data["html"] = self.html
        elif self.kind == VisualBodyKind.SEMANTIC_DOCUMENT:
            data["text"] = self.text
        if debug:
            data["component"] = self.component
            data["address"] = self.address.to_dict() if self.address else None
            data["end_address"] = self.end_address.to_dict() if self.end_address else None
        return data


@dataclass(frozen=True)
class EntryView:
    target: EntryTarget
    heading: str | None
    visual_body: VisualBody
    semantic_document: "EntryDocument | None" = None
    resources: tuple["ResourceRef", ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    body_source: str | None = None

    def to_dict(self, *, debug: bool = False, include_semantic: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": ENTRY_VIEW_SCHEMA,
            "model_version": ENTRY_VIEW_MODEL_VERSION,
            "target": self.target.to_dict(debug=debug),
            "heading": self.heading,
            "body_source": self.body_source,
            "visual_body": self.visual_body.to_dict(debug=debug),
            "resources": [resource.to_dict(debug=debug) for resource in self.resources],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if include_semantic and self.semantic_document is not None:
            data["semantic_document"] = self.semantic_document.to_dict(debug=debug)
        return data


@dataclass(frozen=True)
class HomeSurface:
    kind: HomeSurfaceKind
    status: ReaderStatus
    label: str
    target: EntryTarget | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    details: JsonObject = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": HOME_SURFACE_SCHEMA,
            "model_version": HOME_SURFACE_MODEL_VERSION,
            "kind": self.kind.value,
            "status": self.status.value,
            "label": self.label,
            "target": self.target.to_dict(debug=debug) if self.target else None,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if debug:
            data["details"] = _safe_json(self.details)
        return data


@dataclass(frozen=True)
class UnsupportedReaderPackage:
    info: PackageInfo
    diagnostics: tuple[Diagnostic, ...] = ()

    def package_family(self) -> PackageFamily:
        return self.info.family

    def home_surfaces(self) -> tuple[HomeSurface, ...]:
        diagnostic = Diagnostic(
            severity=Severity.WARNING,
            area=DiagnosticArea.PACKAGE,
            code="unsupported_package_family",
            message=f"{self.info.family.value} reader support is deferred",
            location=Location(),
            details={"family": self.info.family.value},
        )
        return (
            HomeSurface(
                kind=HomeSurfaceKind.UNSUPPORTED_PACKAGE,
                status=ReaderStatus.UNSUPPORTED,
                label=self.info.title or self.info.dict_id or self.info.family.value,
                target=EntryTarget(
                    kind=EntryTargetKind.UNSUPPORTED_FAMILY,
                    package_id=self.info.dict_id,
                    label=self.info.title or self.info.dict_id,
                    diagnostics=(diagnostic,),
                ),
                diagnostics=(diagnostic,),
            ),
        )

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        return {
            "schema": READER_PACKAGE_SCHEMA,
            "model_version": READER_PACKAGE_MODEL_VERSION,
            "package": self.info.to_dict(),
            "surfaces": [surface.to_dict(debug=debug) for surface in self.home_surfaces()],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
