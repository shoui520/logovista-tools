"""High-level package API."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterable

from .body_source import (
    BodyPointerInspection,
    BodySourceInfo,
    BodySourceSupport,
    Confidence,
    SQLITE_MAGIC,
    SidecarBody,
    SidecarInfo,
    SidecarRole,
    SidecarSupportStatus,
    SidecarTableInfo,
    SsedBodySourceKind,
    classify_sqlite_sidecar_role,
    compatibility_significant_sidecar_role,
    find_column,
    quote_sql_identifier,
    sqlite_columns,
    strip_html,
)
from .crypto import decrypt_logofont, decrypt_logofont_prefix
from .detect import detect_family
from .diagnostics import Diagnostic, DiagnosticArea, Location, Severity
from .document import BlockNode, InlineKind, InlineNode, ResourceKind, ResourceRef, ResourceStatus
from .errors import FormatError, UnsupportedPackageError
from .gaiji import (
    Ga16Resource,
    GaijiDisplayStatus,
    GaijiMap,
    GaijiResolutionReason,
    ImageGaijiResource,
    load_gaiji_map,
    load_image_gaiji_resources,
    parse_ga16,
)
from .index import IndexRow
from .index import IndexParse, parse_index
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile, Span
from .render import HtmlProfile, render_html, render_text
from .search import SearchHit, SearchResults, natural_backward_key, normalize_query, query_candidates
from .ssed import BLOCK_SIZE, CHUNK_SIZE, Catalog, SsedData, TEXT_LIKE_INDEX_OUTLIER_TYPES, find_file_case_insensitive, parse_catalog
from .text import DecodeResult, decode_text_stream


ENTRY_MARKER = b"\x1f\x09\x00\x01"
SearchValueRow = tuple[IndexRow, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]


def _media_mime_and_format(payload: bytes, *, store_kind: str) -> tuple[str, str, str]:
    if payload.startswith(b"BM"):
        return "image/bmp", "bmp", "bitmap"
    if payload.startswith(b"\xff\xd8"):
        return "image/jpeg", "jpeg", "image"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png", "image"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif", "image"
    if payload.startswith(b"ID3") or (len(payload) >= 2 and payload[0] == 0xFF and (payload[1] & 0xE0) == 0xE0):
        return "audio/mpeg", "mp3", "audio"
    if payload.startswith(b"RIFF") and b"WAVE" in payload[:16]:
        return "audio/wav", "wave", "audio"
    if payload.startswith(b"fmt ") or (b"fmt " in payload[:64] and b"data" in payload[:256]):
        return "audio/x-logovista-wave-chunks", "wave_chunks", "audio"
    if store_kind == "pcmdata" and payload:
        return "application/octet-stream", "unknown_pcmdata_payload", "audio"
    return "application/octet-stream", "unknown", "binary"


def open_package(path: str | Path) -> "LogoVistaPackage":
    info = detect_family(Path(path))
    if info.family != PackageFamily.SSED:
        raise UnsupportedPackageError(f"{info.family.value} package support is deferred")
    if info.idx_path is None:
        raise UnsupportedPackageError("SSED package did not expose an IDX path")
    return LogoVistaPackage(info.idx_path)


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
        self.gaiji: GaijiMap = load_gaiji_map(idx_path.parent, idx_path.stem)
        self.components: tuple[Component, ...] = tuple(
            replace(component, path=find_file_case_insensitive(idx_path.parent, component.name))
            for component in self.catalog.components
        )
        self.ga16: tuple[Ga16Resource, ...] = self._load_ga16_resources(idx_path.parent, self.components)
        self.gaiji_images: tuple[ImageGaijiResource, ...] = load_image_gaiji_resources(idx_path.parent)
        self._gaiji_image_by_code = {resource.code: resource for resource in self.gaiji_images}
        self._component_by_name = {component.name.lower(): component for component in self.components}
        self._data_cache: dict[str, SsedData] = {}
        self._index_cache: dict[str, IndexParse] = {}
        self._search_value_cache: dict[str, tuple[SearchValueRow, ...]] = {}
        self._exact_search_cache: dict[str, dict[str, tuple[tuple[IndexRow, str], ...]]] = {}
        self._marker_cache: dict[str, list[int]] = {}
        self._body_pointer_cache: dict[str, list[int]] = {}
        self._body_source_cache: BodySourceInfo | None = None
        self._sqlite_sidecar_cache: dict[str, Path] = {}
        self._sqlite_schema_cache: dict[str, SidecarInfo | None] = {}
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._closed = False

    @staticmethod
    def _load_ga16_resources(root: Path, components: tuple[Component, ...]) -> tuple[Ga16Resource, ...]:
        paths: list[Path] = []
        for component in components:
            if component.role == ComponentRole.GAIJI and component.path is not None:
                paths.append(component.path)
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                upper = path.name.upper()
                if upper.startswith(("GA16", "GAI16")):
                    paths.append(path)
        except OSError:
            pass
        resources: list[Ga16Resource] = []
        seen: set[Path] = set()
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            seen.add(resolved)
            parsed = parse_ga16(path)
            if parsed is not None:
                resources.append(parsed)
        return tuple(resources)

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
        self._body_source_cache = None
        self._sqlite_sidecar_cache.clear()
        self._sqlite_schema_cache.clear()
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

    def decode_component(self, component: str | Component) -> DecodeResult:
        self._ensure_open()
        decoded = decode_text_stream(self.expanded(component), self.gaiji.mapping)
        return DecodeResult(
            spans=self._annotate_gaiji_spans(decoded.spans),
            text=decoded.text,
            unknown_controls=decoded.unknown_controls,
            unknown_bytes=decoded.unknown_bytes,
        )

    def _decode_text_stream(self, data: bytes, *, renderer_entry_backed: bool = False) -> DecodeResult:
        decoded = decode_text_stream(data, self.gaiji.mapping)
        return DecodeResult(
            spans=self._annotate_gaiji_spans(decoded.spans, renderer_entry_backed=renderer_entry_backed),
            text=decoded.text,
            unknown_controls=decoded.unknown_controls,
            unknown_bytes=decoded.unknown_bytes,
        )

    @staticmethod
    def _gaiji_resource_id(code: str, *, source: str = "gaiji") -> str:
        digest = hashlib.sha1(f"{source}:{code.lower()}".encode("utf-8")).hexdigest()[:12]
        return f"gaiji-{code.lower()}-{digest}"

    @staticmethod
    def _is_blank_glyph(glyph: bytes | None) -> bool:
        return bool(glyph is not None) and all(byte == 0 for byte in glyph)

    def _gaiji_image_info(self, code: str) -> dict[str, object] | None:
        image = self._gaiji_image_by_code.get(code.lower())
        if image is None:
            return None
        try:
            payload = image.path.read_bytes()
        except OSError:
            return {
                "display_status": GaijiDisplayStatus.UNRESOLVED.value,
                "reason": GaijiResolutionReason.MISSING_IMAGE_RESOURCE.value,
                "source": image.source,
                "image_path": str(image.path),
            }
        mime_type, format_hint, container_kind = _media_mime_and_format(payload[:256], store_kind="gaiji_image")
        if mime_type == "application/octet-stream":
            suffix = image.path.suffix.lower()
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
            }.get(suffix, mime_type)
            format_hint = suffix.removeprefix(".") or format_hint
            container_kind = "image" if mime_type.startswith("image/") else container_kind
        return {
            "display_status": GaijiDisplayStatus.IMAGE_BACKED.value,
            "reason": GaijiResolutionReason.IMAGE_ASSET.value,
            "source": "image",
            "resource_id": self._gaiji_resource_id(code, source="image"),
            "resource_kind": ResourceKind.GAIJI.value,
            "mime_type": mime_type,
            "format_hint": format_hint,
            "container_kind": container_kind,
            "byte_length": len(payload),
            "image_path": str(image.path),
            "image_source": image.source,
            "image_key": image.key,
        }

    def _gaiji_glyph_info(self, code: str, *, prefer_record_order: bool = True) -> dict[str, object] | None:
        try:
            code_int = int(code, 16)
        except ValueError:
            return {
                "display_status": GaijiDisplayStatus.UNRESOLVED.value,
                "reason": GaijiResolutionReason.MALFORMED_GAIJI_CODE.value,
            }
        record = self.gaiji.record_for_code(code)
        if prefer_record_order and record is not None and record.index >= 0:
            for resource in self.ga16:
                if resource.section not in {record.section, "unknown"}:
                    continue
                glyph = resource.glyph_by_index(record.index)
                if glyph is None:
                    continue
                status = GaijiDisplayStatus.FORMATTING_HELPER if self._is_blank_glyph(glyph) else GaijiDisplayStatus.BITMAP_BACKED
                reason = (
                    GaijiResolutionReason.BLANK_BITMAP_FORMATTING_HELPER
                    if status == GaijiDisplayStatus.FORMATTING_HELPER
                    else GaijiResolutionReason.UNI_RECORD_ORDER_GA16
                )
                return {
                    "display_status": status.value,
                    "reason": reason.value,
                    "source": "ga16_record_order",
                    "resource_id": self._gaiji_resource_id(code, source="ga16-record"),
                    "resource_kind": ResourceKind.GAIJI.value,
                    "mime_type": "application/x-logovista-ga16-bitmap",
                    "byte_length": len(glyph),
                    "source_path": str(resource.path),
                    "glyph_index": record.index,
                    "glyph_width": resource.width,
                    "glyph_height": resource.height,
                    "glyph_bytes": resource.glyph_bytes,
                    "ga16_section": resource.section,
                }
        for resource in self.ga16:
            index = resource.index_for_code(code_int)
            glyph = resource.glyph_by_index(index)
            if glyph is None:
                continue
            status = GaijiDisplayStatus.FORMATTING_HELPER if self._is_blank_glyph(glyph) else GaijiDisplayStatus.BITMAP_BACKED
            reason = (
                GaijiResolutionReason.BLANK_BITMAP_FORMATTING_HELPER
                if status == GaijiDisplayStatus.FORMATTING_HELPER
                else GaijiResolutionReason.JIS_GRID_GA16
            )
            return {
                "display_status": status.value,
                "reason": reason.value,
                "source": "ga16_grid",
                "resource_id": self._gaiji_resource_id(code, source="ga16-grid"),
                "resource_kind": ResourceKind.GAIJI.value,
                "mime_type": "application/x-logovista-ga16-bitmap",
                "byte_length": len(glyph),
                "source_path": str(resource.path),
                "glyph_index": index,
                "glyph_width": resource.width,
                "glyph_height": resource.height,
                "glyph_bytes": resource.glyph_bytes,
                "ga16_section": resource.section,
            }
        return None

    def gaiji_info(self, code_or_resource: str | ResourceRef | dict[str, object]) -> dict[str, object]:
        if isinstance(code_or_resource, ResourceRef):
            code = code_or_resource.code or ""
            resource_id = code_or_resource.id
            details = dict(code_or_resource.details)
        elif isinstance(code_or_resource, dict):
            code_value = code_or_resource.get("code")
            code = str(code_value or "")
            resource_id = str(code_or_resource.get("id") or self._gaiji_resource_id(code))
            details_value = code_or_resource.get("details") or {}
            details = dict(details_value) if isinstance(details_value, dict) else {}
        else:
            code = str(code_or_resource or "")
            resource_id = self._gaiji_resource_id(code)
            details = {}
        code = code.lower()
        info: dict[str, object] = self._media_info_base(resource_id, ResourceKind.GAIJI.value)
        info["code"] = code
        if len(code) != 4:
            info["status"] = "malformed"
            info["display_status"] = GaijiDisplayStatus.UNRESOLVED.value
            info["details"] = {"reason": GaijiResolutionReason.MALFORMED_GAIJI_CODE.value}
            return info

        record = self.gaiji.record_for_code(code)
        display_text = self.gaiji.resolve(code)
        image_info = self._gaiji_image_info(code)
        glyph_info = self._gaiji_glyph_info(code)
        if display_text:
            reason = (
                GaijiResolutionReason.PLIST_MAPPING.value
                if record is not None and record.source == "plist"
                else GaijiResolutionReason.UNICODE_MAPPING.value
            )
            if record is not None and not record.display and record.fallback:
                reason = GaijiResolutionReason.UNICODE_FALLBACK_MAPPING.value
            info.update(
                {
                    "status": "resolved",
                    "display_status": GaijiDisplayStatus.UNICODE_MAPPED.value,
                    "reason": reason,
                    "display_text": display_text,
                    "fallback_text": record.fallback if record is not None else None,
                    "source": record.source if record is not None else "uni",
                    "details": {
                        "reason": reason,
                        "display_status": GaijiDisplayStatus.UNICODE_MAPPED.value,
                        "source": record.source if record is not None else "uni",
                    },
                }
            )
            backing = glyph_info or image_info
            if backing and backing.get("display_status") != GaijiDisplayStatus.UNRESOLVED.value:
                info["resource_id"] = backing.get("resource_id")
                info["mime_type"] = backing.get("mime_type")
                info["byte_length"] = backing.get("byte_length")
                for key in ("source_path", "image_path", "glyph_index", "glyph_width", "glyph_height", "glyph_bytes", "ga16_section"):
                    if key in backing:
                        info[key] = backing[key]
                info["details"] = {**info["details"], "backing_resource": backing}
            return info

        if details.get("display_status") == GaijiDisplayStatus.RENDERER_ENTRY_BACKED.value:
            info.update(
                {
                    "status": "resolved",
                    "display_status": GaijiDisplayStatus.RENDERER_ENTRY_BACKED.value,
                    "reason": GaijiResolutionReason.RENDERER_CONTEXTUAL_REQUIRED.value,
                    "details": {
                        "reason": GaijiResolutionReason.RENDERER_CONTEXTUAL_REQUIRED.value,
                        "display_status": GaijiDisplayStatus.RENDERER_ENTRY_BACKED.value,
                    },
                }
            )
            return info

        backing = image_info or glyph_info
        if backing is not None and backing.get("display_status") != GaijiDisplayStatus.UNRESOLVED.value:
            status = str(backing.get("display_status"))
            info.update(backing)
            info["status"] = "resolved"
            info["display_status"] = status
            info["details"] = dict(backing)
            return info

        if code.startswith("b") and (record is None or (not record.display and not record.fallback)):
            reason = GaijiResolutionReason.FULLWIDTH_FORMATTING_HELPER_CANDIDATE.value
            info.update(
                {
                    "status": "resolved",
                    "display_status": GaijiDisplayStatus.FORMATTING_HELPER.value,
                    "reason": reason,
                    "source": record.source if record is not None else "raw_fullwidth",
                    "details": {
                        "reason": reason,
                        "display_status": GaijiDisplayStatus.FORMATTING_HELPER.value,
                        "source": record.source if record is not None else "raw_fullwidth",
                    },
                }
            )
            return info

        reason = GaijiResolutionReason.MISSING_UNICODE_MAPPING.value
        if not self.ga16 and not self.gaiji_images:
            reason = GaijiResolutionReason.MISSING_GAIJI_TABLE.value
        info.update(
            {
                "status": "unresolved",
                "display_status": GaijiDisplayStatus.UNRESOLVED.value,
                "reason": reason,
                "details": {"reason": reason, "display_status": GaijiDisplayStatus.UNRESOLVED.value},
            }
        )
        return info

    def _annotate_gaiji_spans(self, spans: tuple[Span, ...], *, renderer_entry_backed: bool = False) -> tuple[Span, ...]:
        out: list[Span] = []
        for span in spans:
            if span.kind != "gaiji" or not span.code:
                out.append(span)
                continue
            info = self.gaiji_info(span.code)
            details = info.get("details") if isinstance(info.get("details"), dict) else {}
            attrs = dict(span.attrs)
            attrs.update(
                {
                    "gaiji_display_status": info.get("display_status"),
                    "gaiji_reason": info.get("reason") or details.get("reason"),
                    "display_text": info.get("display_text"),
                    "fallback_text": info.get("fallback_text"),
                    "resource_id": info.get("resource_id"),
                    "resource_kind": info.get("resource_kind"),
                    "mime_type": info.get("mime_type"),
                    "byte_length": info.get("byte_length"),
                    "gaiji_source": info.get("source") or details.get("source"),
                }
            )
            if renderer_entry_backed and attrs.get("gaiji_display_status") == GaijiDisplayStatus.UNRESOLVED.value:
                attrs["gaiji_display_status"] = GaijiDisplayStatus.RENDERER_ENTRY_BACKED.value
                attrs["gaiji_reason"] = GaijiResolutionReason.RENDERER_CONTEXTUAL_REQUIRED.value
            out.append(replace(span, attrs=attrs))
        return tuple(out)

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

    def _sidecar_file_candidates(self) -> list[Path]:
        self._ensure_open()
        candidates: list[Path] = []
        try:
            children = sorted(self.info.root.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            return candidates
        dict_id = (self.info.dict_id or "").lower()
        for child in children:
            if not child.is_file():
                continue
            lower = child.name.lower()
            if lower == "vlpljbl.bin":
                continue
            is_dict_id_payload = bool(dict_id and child.suffix == "" and lower == dict_id)
            if lower.startswith("vlpljbl") or child.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".sql"} or is_dict_id_payload:
                candidates.append(child)
        return candidates

    @staticmethod
    def _sqlite_storage(path: Path) -> str | None:
        try:
            raw = path.read_bytes()[:2048]
        except OSError:
            return None
        if raw.startswith(SQLITE_MAGIC):
            return "plain"
        try:
            prefix = decrypt_logofont_prefix(raw, size=64)
        except Exception:
            return None
        if prefix.startswith(SQLITE_MAGIC):
            return "logofont_cipher"
        return None

    def _sqlite_path_for_sidecar(self, path: Path, storage: str) -> Path:
        key = str(path)
        if storage == "plain":
            return path
        if key in self._sqlite_sidecar_cache:
            return self._sqlite_sidecar_cache[key]
        if self._tempdir is None:
            self._tempdir = tempfile.TemporaryDirectory(prefix="lvcore-sidecar-")
        decrypted = Path(self._tempdir.name) / f"{path.name}.sqlite"
        decrypted.write_bytes(decrypt_logofont(path.read_bytes()))
        self._sqlite_sidecar_cache[key] = decrypted
        return decrypted

    @staticmethod
    def _row_count(con: sqlite3.Connection, table: str) -> int | None:
        try:
            return int(con.execute(f"select count(*) from {quote_sql_identifier(table)}").fetchone()[0])
        except sqlite3.DatabaseError:
            return None

    def _sidecar_table_info(self, con: sqlite3.Connection, table: str) -> SidecarTableInfo:
        columns = sqlite_columns(con, table)
        lower = {column.lower(): column for column in columns}

        def first(*names: str) -> str | None:
            for name in names:
                found = lower.get(name.lower())
                if found is not None:
                    return found
            return None

        block_col = first("Block", "Block_s", "f_block")
        offset_col = first("Offset", "Offset_s", "f_offset")
        role = classify_sqlite_sidecar_role("sqlite_unmapped", (table,), {table: columns})
        return SidecarTableInfo(
            table=table,
            columns=tuple(columns),
            row_count=self._row_count(con, table),
            role=role,
            id_column=first("ID", "No", "ItemID", "f_DataId", "f_data_id", "f_array_no", "f_contents_id", "f_order_id", "id", "index"),
            title_column=first(
                "Title",
                "TitleJIS",
                "JIS_Title",
                "Title_UTF8",
                "Title_SJIS",
                "f_Title",
                "f_title",
                "Keyword",
                "Midashi",
                "MidashiJ",
                "f_midasi",
                "f_midashi_hyoki",
                "f_midashi_key",
            ),
            html_column=first("f_Html", "f_html_text", "Contents_HTML_box", "Contents_HTML_list", "f_contents"),
            plain_column=first("Body", "f_body", "f_Plane", "f_plane", "f_plane_text", "h_text", "Value", "data"),
            blob_column=first("f_blob", "f_main"),
            name_column=first("f_name", "name"),
            block_column=block_col,
            offset_column=offset_col,
            end_block_column=first("Block_e"),
            end_offset_column=first("Offset_e"),
        )

    @staticmethod
    def _sidecar_support_status(role: SidecarRole | str, tables: tuple[SidecarTableInfo, ...]) -> SidecarSupportStatus:
        role_value = role.value if isinstance(role, SidecarRole) else str(role)
        if role_value == SidecarRole.MEDIA_RESOURCE.value:
            if any(table.blob_column and (table.name_column or table.id_column) for table in tables):
                return SidecarSupportStatus.RESOURCE_RESOLVER
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        if role_value in {
            SidecarRole.EXAMPLES_IDIOMS.value,
            SidecarRole.LINK_REFERENCE.value,
            SidecarRole.SUPPLEMENTAL.value,
        }:
            if any(table.block_column and table.offset_column and (table.title_column or table.plain_column or table.html_column) for table in tables):
                return SidecarSupportStatus.SUPPLEMENT_RESOLVER
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        if role_value == SidecarRole.SEARCH.value:
            if any(table.block_column and table.offset_column and (table.title_column or table.plain_column) for table in tables):
                return SidecarSupportStatus.SEARCH_METADATA
            if any(table.id_column and table.title_column for table in tables):
                return SidecarSupportStatus.SEARCH_METADATA
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        if role_value in {SidecarRole.ANCILLARY.value, SidecarRole.KANJI_SUPPORT.value}:
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        return SidecarSupportStatus.UNSUPPORTED_SCHEMA

    def _inspect_sqlite_sidecar(self, path: Path) -> SidecarInfo | None:
        key = str(path)
        if key in self._sqlite_schema_cache:
            return self._sqlite_schema_cache[key]
        storage = self._sqlite_storage(path)
        if storage is None:
            self._sqlite_schema_cache[key] = None
            return None
        sqlite_path = self._sqlite_path_for_sidecar(path, storage)
        try:
            con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        except sqlite3.DatabaseError:
            self._sqlite_schema_cache[key] = None
            return None
        try:
            tables = [row[0] for row in con.execute("select name from sqlite_master where type='table' order by name")]
            table_infos = tuple(self._sidecar_table_info(con, table) for table in tables)
            columns_by_table = {info.table: list(info.columns) for info in table_infos}
            for table in ("t_contents", "HONBUN", "main"):
                if table not in tables:
                    continue
                columns = sqlite_columns(con, table)
                table_info = next((info for info in table_infos if info.table == table), None)
                if table_info is not None:
                    table_info = replace(table_info, role=SidecarRole.BODY_CRITICAL)
                if table == "HONBUN":
                    id_col = find_column(columns, "ID", "f_DataId", "f_data_id")
                    title_col = find_column(columns, "Title_UTF8", "Title_SJIS", "Title", "f_Title")
                    html_col = find_column(columns, "Contents_HTML_box", "Contents_HTML_list", "f_Html", "f_contents")
                    plain_col = find_column(columns, "f_Plane", "f_body", "Body")
                    if id_col and (html_col or plain_col or title_col):
                        info = SidecarInfo(
                            path=path,
                            kind="honbun",
                            storage=storage,
                            role=classify_sqlite_sidecar_role("honbun", tables),
                            support_status=SidecarSupportStatus.BODY_RESOLVER,
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=html_col,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table),
                            tables=(table_info,) if table_info is not None else (),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
                elif table == "main":
                    id_col = find_column(columns, "ID")
                    title_col = find_column(columns, "C_text", "K_text", "J_text")
                    plain_col = find_column(columns, "J_text", "C_text", "K_text")
                    if id_col and (title_col or plain_col):
                        info = SidecarInfo(
                            path=path,
                            kind="main_wordlist",
                            storage=storage,
                            role=classify_sqlite_sidecar_role("main_wordlist", tables),
                            support_status=SidecarSupportStatus.BODY_RESOLVER,
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=None,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table),
                            tables=(table_info,) if table_info is not None else (),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
                else:
                    id_col = find_column(columns, "f_DataId", "f_data_id", "f_array_no", "f_contents_id", "f_order_id")
                    title_col = find_column(columns, "f_Title", "f_title", "f_midashi", "f_midashi_hyoki", "f_midashi_key", "f_abbr", "f_fullname")
                    html_col = find_column(columns, "f_Html", "f_html_text", "f_contents", "f_body")
                    plain_col = find_column(columns, "f_Plane", "f_plane", "f_plane_text", "f_body")
                    if id_col and (html_col or plain_col):
                        info = SidecarInfo(
                            path=path,
                            kind="t_contents",
                            storage=storage,
                            role=classify_sqlite_sidecar_role("t_contents", tables),
                            support_status=SidecarSupportStatus.BODY_RESOLVER,
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=html_col,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table),
                            tables=(table_info,) if table_info is not None else (),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
            role = classify_sqlite_sidecar_role("sqlite_unmapped", tables, columns_by_table)
            support_status = self._sidecar_support_status(role, table_infos)
            self._sqlite_schema_cache[key] = SidecarInfo(
                path=path,
                kind="sqlite_unmapped",
                storage=storage,
                role=role,
                support_status=support_status,
                tables=table_infos,
                notes=tuple(tables[:8]),
            )
            return self._sqlite_schema_cache[key]
        finally:
            con.close()

    def _body_sidecars(self) -> tuple[SidecarInfo, ...]:
        rows: list[SidecarInfo] = []
        for path in self._sidecar_file_candidates():
            sidecar = self._inspect_sqlite_sidecar(path)
            if sidecar is not None:
                rows.append(sidecar)
        return tuple(rows)

    def sidecar_role_summary(self) -> dict[str, object]:
        role_counts: dict[str, int] = {}
        unsupported_role_counts: dict[str, int] = {}
        supported_role_counts: dict[str, int] = {}
        compatibility_significant_unsupported_counts: dict[str, int] = {}
        support_status_counts: dict[str, int] = {}
        unsupported_sidecars: list[dict[str, object]] = []
        sqlite_count = 0
        non_sqlite_count = 0
        candidates = self._sidecar_file_candidates()
        for path in candidates:
            sidecar = self._inspect_sqlite_sidecar(path)
            if sidecar is None:
                non_sqlite_count += 1
                role = SidecarRole.NON_SQLITE_OR_UNKNOWN.value
                role_counts[role] = role_counts.get(role, 0) + 1
                status = SidecarSupportStatus.NON_SQLITE_OR_UNKNOWN.value
                support_status_counts[status] = support_status_counts.get(status, 0) + 1
                continue
            sqlite_count += 1
            role = sidecar.role.value if isinstance(sidecar.role, SidecarRole) else str(sidecar.role)
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            role_counts[role] = role_counts.get(role, 0) + 1
            support_status_counts[status] = support_status_counts.get(status, 0) + 1
            if status in {
                SidecarSupportStatus.BODY_RESOLVER.value,
                SidecarSupportStatus.SUPPLEMENT_RESOLVER.value,
                SidecarSupportStatus.RESOURCE_RESOLVER.value,
                SidecarSupportStatus.SEARCH_METADATA.value,
            }:
                supported_role_counts[role] = supported_role_counts.get(role, 0) + 1
            else:
                unsupported_role_counts[role] = unsupported_role_counts.get(role, 0) + 1
                significant = compatibility_significant_sidecar_role(role)
                if significant:
                    compatibility_significant_unsupported_counts[role] = compatibility_significant_unsupported_counts.get(role, 0) + 1
                unsupported_sidecars.append(
                    {
                        "name": sidecar.path.name,
                        "kind": sidecar.kind,
                        "role": role,
                        "support_status": status,
                        "compatibility_significant": significant,
                        "tables": [table.table for table in sidecar.tables] or list(sidecar.notes),
                    }
                )
        return {
            "candidate_count": len(candidates),
            "sqlite_count": sqlite_count,
            "non_sqlite_or_unknown_count": non_sqlite_count,
            "role_counts": role_counts,
            "supported_role_counts": supported_role_counts,
            "unsupported_role_counts": unsupported_role_counts,
            "compatibility_significant_unsupported_counts": compatibility_significant_unsupported_counts,
            "support_status_counts": support_status_counts,
            "unsupported_sidecars": unsupported_sidecars,
        }

    @staticmethod
    def _safe_sidecar_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            for encoding in ("utf-8", "cp932"):
                try:
                    return value.decode(encoding).strip()
                except UnicodeDecodeError:
                    continue
            return ""
        return str(value).strip()

    @staticmethod
    def _sidecar_supplement_kind(role: SidecarRole | str, table: str) -> str:
        role_value = role.value if isinstance(role, SidecarRole) else str(role)
        table_lower = table.lower()
        if role_value == SidecarRole.EXAMPLES_IDIOMS.value:
            if "idiom" in table_lower:
                return "idiom"
            if any(token in table_lower for token in ("goyo", "keigo", "kininaru")):
                return "usage_note"
            return "example"
        if role_value == SidecarRole.LINK_REFERENCE.value:
            return "link_reference"
        if role_value == SidecarRole.SEARCH.value:
            return "sidecar_search"
        return "supplemental"

    @staticmethod
    def _sidecar_table_text_columns(table: SidecarTableInfo) -> tuple[str, ...]:
        candidates = [
            table.title_column,
            table.plain_column,
            table.html_column,
            table.name_column,
            "Keyword",
            "Midashi",
            "MidashiJ",
            "Title",
            "TitleJIS",
            "JIS_Title",
            "Body",
            "h_text",
        ]
        columns = set(table.columns)
        out: list[str] = []
        for column in candidates:
            if column and column in columns and column not in out:
                out.append(column)
        return tuple(out)

    def sidecar_supplements(self, address: Address, *, limit: int = 32, debug: bool = False) -> list[dict[str, object]]:
        """Return readable supplemental sidecar rows for one entry address."""

        supplements: list[dict[str, object]] = []
        for sidecar in self._body_sidecars():
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            if status not in {SidecarSupportStatus.SUPPLEMENT_RESOLVER.value, SidecarSupportStatus.SEARCH_METADATA.value}:
                continue
            candidate_tables = [table for table in sidecar.tables if table.block_column and table.offset_column]
            if not candidate_tables:
                continue
            sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
            try:
                con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
            except sqlite3.DatabaseError:
                continue
            try:
                for table in candidate_tables:
                    text_columns = self._sidecar_table_text_columns(table)
                    select_columns: list[str] = []
                    for column in (table.id_column, table.block_column, table.offset_column, *text_columns):
                        if column and column in table.columns and column not in select_columns:
                            select_columns.append(column)
                    quoted_columns = [quote_sql_identifier(column) for column in select_columns]
                    quoted = ", ".join(["rowid as __rowid", *quoted_columns])
                    sql = (
                        f"select {quoted} from {quote_sql_identifier(table.table)} "
                        f"where {quote_sql_identifier(table.block_column or '')}=? "
                        f"and {quote_sql_identifier(table.offset_column or '')}=? "
                        f"order by rowid limit ?"
                    )
                    try:
                        rows = con.execute(sql, (address.block, address.offset, max(1, limit - len(supplements)))).fetchall()
                    except sqlite3.DatabaseError:
                        continue
                    role = table.role.value if isinstance(table.role, SidecarRole) else str(table.role)
                    for row in rows:
                        values = {column: self._safe_sidecar_text(row[column]) for column in text_columns if column in row.keys()}
                        heading = values.get(table.title_column or "") or values.get("Title") or values.get("Midashi") or values.get("Keyword") or ""
                        text = (
                            values.get(table.plain_column or "")
                            or values.get(table.html_column or "")
                            or values.get("Body")
                            or values.get("h_text")
                            or heading
                        )
                        kind = self._sidecar_supplement_kind(table.role, table.table)
                        row_id = int(row["__rowid"])
                        supplement: dict[str, object] = {
                            "id": f"sidecar-{kind}-{len(supplements) + 1}",
                            "kind": kind,
                            "role": role,
                            "status": "address_matched",
                            "sidecar": sidecar.path.name,
                            "table": table.table,
                            "row_id": row_id,
                            "address": address.to_dict(),
                            "heading": strip_html(heading),
                            "text": strip_html(text),
                            "keyword": values.get("Keyword") or "",
                        }
                        if kind in {"link_reference", "sidecar_search"}:
                            label = str(supplement.get("heading") or supplement.get("text") or "reference")
                            supplement["link_target"] = {
                                "kind": "internal_address" if kind == "link_reference" else "sidecar_search",
                                "href": f"lvcore-entry://{address.block}/{address.offset}",
                                "status": "resolved",
                                "label": label,
                                "address": address.to_dict(),
                                "details": {
                                    "status": "address_matched",
                                    "source_sidecar": sidecar.path.name,
                                    "source_table": table.table,
                                    "row_id": row_id,
                                },
                            }
                        if debug:
                            supplement["debug"] = {
                                "storage": sidecar.storage,
                                "block_column": table.block_column,
                                "offset_column": table.offset_column,
                                "id_column": table.id_column,
                                "text_columns": list(text_columns),
                            }
                        supplements.append(supplement)
                        if len(supplements) >= limit:
                            return supplements
            finally:
                con.close()
        return supplements

    def _attach_sidecar_supplements(self, entry: Entry) -> Entry:
        supplements = tuple(self.sidecar_supplements(entry.address, debug=True))
        if not supplements:
            return entry
        return replace(entry, supplements=supplements)

    @staticmethod
    def _resource_kind_from_container(container_kind: str) -> ResourceKind:
        if container_kind == "image" or container_kind == "bitmap":
            return ResourceKind.IMAGE
        if container_kind == "audio":
            return ResourceKind.AUDIO
        return ResourceKind.MEDIA

    def sidecar_media_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        """List package-level sidecar BLOB media resources with exact byte access."""

        resources: list[ResourceRef] = []
        for sidecar in self._body_sidecars():
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            if status != SidecarSupportStatus.RESOURCE_RESOLVER.value:
                continue
            candidate_tables = [table for table in sidecar.tables if table.blob_column]
            if not candidate_tables:
                continue
            sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
            try:
                con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
            except sqlite3.DatabaseError:
                continue
            try:
                for table in candidate_tables:
                    name_column = table.name_column or table.id_column
                    select_columns: list[str] = []
                    for column in (name_column, table.id_column):
                        if column and column in table.columns and column not in select_columns:
                            select_columns.append(column)
                    quoted_columns = [quote_sql_identifier(column) for column in select_columns]
                    quoted = ", ".join(["rowid as __rowid", *quoted_columns])
                    sql = (
                        f"select {quoted}, "
                        f"length({quote_sql_identifier(table.blob_column)}) as __blob_length, "
                        f"substr({quote_sql_identifier(table.blob_column)}, 1, 1024) as __blob_prefix "
                        f"from {quote_sql_identifier(table.table)} order by rowid"
                    )
                    if limit is not None:
                        sql += " limit ?"
                        params: tuple[object, ...] = (max(0, limit - len(resources)),)
                    else:
                        params = ()
                    try:
                        rows = con.execute(sql, params).fetchall()
                    except sqlite3.DatabaseError:
                        continue
                    for row in rows:
                        row_id = int(row["__rowid"])
                        name = self._safe_sidecar_text(row[name_column]) if name_column and name_column in row.keys() else ""
                        prefix = bytes(row["__blob_prefix"] or b"")
                        mime_type, format_hint, container_kind = _media_mime_and_format(prefix, store_kind="sidecar_media")
                        digest = hashlib.sha1(f"{sidecar.path.name}:{table.table}:{row_id}:{name}".encode("utf-8")).hexdigest()[:12]
                        resources.append(
                            ResourceRef(
                                id=f"sidecar-media-{digest}",
                                kind=self._resource_kind_from_container(container_kind),
                                label=name or f"{table.table}#{row_id}",
                                status="resolved",
                                mime_type=mime_type,
                                source_path=str(sidecar.path),
                                details={
                                    "reason": "sidecar_media_blob",
                                    "resolved": True,
                                    "sidecar_media": True,
                                    "store_kind": "sidecar_media",
                                    "sidecar": sidecar.path.name,
                                    "storage": sidecar.storage,
                                    "table": table.table,
                                    "row_id": row_id,
                                    "name": name,
                                    "id_column": table.id_column,
                                    "name_column": name_column,
                                    "blob_column": table.blob_column,
                                    "byte_length": int(row["__blob_length"] or 0),
                                    "format_hint": format_hint,
                                    "container_kind": container_kind,
                                },
                            )
                        )
                        if limit is not None and len(resources) >= limit:
                            return tuple(resources)
            finally:
                con.close()
        return tuple(resources)

    def gaiji_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        """List package-level gaiji resources and display mappings."""

        codes = sorted({record.code for record in self.gaiji.records} | set(self._gaiji_image_by_code))
        resources: list[ResourceRef] = []
        for code in codes:
            info = self.gaiji_info(code)
            details = info.get("details") if isinstance(info.get("details"), dict) else {}
            resources.append(
                ResourceRef(
                    id=str(info.get("resource_id") or self._gaiji_resource_id(code)),
                    kind=ResourceKind.GAIJI,
                    label=str(info.get("display_text") or code),
                    status=ResourceStatus.RESOLVED if info.get("status") == "resolved" else ResourceStatus.UNRESOLVED,
                    mime_type=str(info.get("mime_type")) if info.get("mime_type") else None,
                    code=code,
                    source_path=str(info.get("source_path") or info.get("image_path") or "") or None,
                    details={
                        "resolved": info.get("status") == "resolved",
                        "reason": info.get("reason") or details.get("reason"),
                        "display_status": info.get("display_status") or details.get("display_status"),
                        "display_text": info.get("display_text"),
                        "source": info.get("source") or details.get("source"),
                        "byte_length": info.get("byte_length"),
                    },
                )
            )
            if limit is not None and len(resources) >= limit:
                break
        return tuple(resources)

    def sidecar_supplement_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "examples_idioms_rows_seen": 0,
            "examples_idioms_rows_attached": 0,
            "entry_supplements_attached": 0,
            "link_reference_rows_seen": 0,
            "link_reference_rows_matched": 0,
            "link_reference_targets_resolved": 0,
            "sidecar_search_rows_seen": 0,
            "sidecar_search_rows_supported": 0,
            "sidecar_search_rows_deferred": 0,
            "sidecar_media_rows_seen": 0,
            "sidecar_media_rows_resolved": 0,
            "sidecar_media_bytes_available": 0,
            "sidecar_media_mime_counts": {},
        }
        for sidecar in self._body_sidecars():
            role = sidecar.role.value if isinstance(sidecar.role, SidecarRole) else str(sidecar.role)
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            for table in sidecar.tables:
                rows = int(table.row_count or 0)
                if role == SidecarRole.EXAMPLES_IDIOMS.value:
                    summary["examples_idioms_rows_seen"] = int(summary["examples_idioms_rows_seen"]) + rows
                elif role == SidecarRole.LINK_REFERENCE.value:
                    summary["link_reference_rows_seen"] = int(summary["link_reference_rows_seen"]) + rows
                elif role == SidecarRole.SEARCH.value:
                    summary["sidecar_search_rows_seen"] = int(summary["sidecar_search_rows_seen"]) + rows
                    if status == SidecarSupportStatus.SEARCH_METADATA.value:
                        summary["sidecar_search_rows_supported"] = int(summary["sidecar_search_rows_supported"]) + rows
                    else:
                        summary["sidecar_search_rows_deferred"] = int(summary["sidecar_search_rows_deferred"]) + rows
                elif role == SidecarRole.MEDIA_RESOURCE.value and table.blob_column:
                    summary["sidecar_media_rows_seen"] = int(summary["sidecar_media_rows_seen"]) + rows
        media_mime_counts = summary["sidecar_media_mime_counts"]
        for resource in self.sidecar_media_resources():
            info = self.resource_info(resource)
            if info.get("status") == "resolved":
                summary["sidecar_media_rows_resolved"] = int(summary["sidecar_media_rows_resolved"]) + 1
                summary["sidecar_media_bytes_available"] = int(summary["sidecar_media_bytes_available"]) + 1
                if isinstance(media_mime_counts, dict):
                    self._increment_reason(media_mime_counts, info.get("mime_type"))
        return summary

    def sidecar_references(self, address: Address, *, limit: int = 32, debug: bool = False) -> list[dict[str, object]]:
        """Return structural sidecar rows that point at an entry address.

        This is a read-only metadata resolver for supplemental sidecars such as
        example/idiom/search/navigation tables. It reports table relationships
        without returning dictionary text.
        """

        matches: list[dict[str, object]] = []
        for sidecar in self._body_sidecars():
            candidate_tables = [table for table in sidecar.tables if table.block_column and table.offset_column]
            if not candidate_tables:
                continue
            sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
            try:
                con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
            except sqlite3.DatabaseError:
                continue
            try:
                for table in candidate_tables:
                    sql = (
                        f"select count(*) from {quote_sql_identifier(table.table)} "
                        f"where {quote_sql_identifier(table.block_column or '')}=? "
                        f"and {quote_sql_identifier(table.offset_column or '')}=?"
                    )
                    try:
                        count = int(con.execute(sql, (address.block, address.offset)).fetchone()[0])
                    except sqlite3.DatabaseError:
                        continue
                    if count <= 0:
                        continue
                    role = table.role.value if isinstance(table.role, SidecarRole) else str(table.role)
                    support_status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
                    row: dict[str, object] = {
                        "sidecar": sidecar.path.name,
                        "kind": sidecar.kind,
                        "role": role,
                        "support_status": support_status,
                        "table": table.table,
                        "match_count": count,
                        "status": "matched",
                    }
                    if debug:
                        row["block_column"] = table.block_column
                        row["offset_column"] = table.offset_column
                        row["title_column"] = table.title_column
                        row["plain_column"] = table.plain_column
                    matches.append(row)
                    if len(matches) >= limit:
                        return matches
            finally:
                con.close()
        return matches

    @staticmethod
    def _marker_offsets(reader: SsedData, *, limit: int | None = None) -> list[int]:
        offsets: list[int] = []
        tail = b""
        tail_base = 0
        for chunk_index in range(len(reader.offsets)):
            chunk = reader.chunk(chunk_index)
            if not chunk:
                continue
            chunk_base = chunk_index * CHUNK_SIZE
            data = tail + chunk
            data_base = tail_base
            search = 0
            while True:
                found = data.find(ENTRY_MARKER, search)
                if found < 0:
                    break
                absolute = data_base + found
                if not offsets or absolute != offsets[-1]:
                    offsets.append(absolute)
                search = found + 1
                if limit is not None and len(offsets) >= limit + 1:
                    return offsets
            keep = max(0, len(ENTRY_MARKER) - 1)
            tail = data[-keep:] if keep else b""
            tail_base = chunk_base + len(chunk) - len(tail)
        return offsets

    def _markers_for_component(self, component: Component, *, limit: int | None = None) -> list[int]:
        key = component.name.lower()
        if limit is not None and key not in self._marker_cache:
            return self._marker_offsets(self.data(component), limit=limit)
        if key not in self._marker_cache:
            self._marker_cache[key] = self._marker_offsets(self.data(component))
        return self._marker_cache[key]

    def iter_entry_slices(self, *, limit: int | None = None) -> Iterable[tuple[int, int]]:
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            return
        reader = self.data(honmon)
        starts = self._markers_for_component(honmon, limit=limit)
        if not starts:
            sample = reader.read(0, min(reader.expanded_size, BLOCK_SIZE))
            if sample.strip(b"\x00"):
                yield 0, reader.expanded_size
            return
        for index, start in enumerate(starts[:limit]):
            end = starts[index + 1] if index + 1 < len(starts) else reader.expanded_size
            yield start, end

    def iter_entries(self, *, limit: int | None = None) -> Iterable[Entry]:
        honmon = self.honmon_component()
        if honmon is None:
            return
        reader = self.data(honmon)
        count = 0
        for start, end in self.iter_entry_slices(limit=limit):
            decoded = self._decode_text_stream(reader.read(start, end - start))
            text = decoded.text.strip("\x00")
            head = ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                head = lines[0]
            entry = Entry(
                address=Address(honmon.start_block + start // 2048, start % 2048, honmon.name),
                end_address=Address(honmon.start_block + end // 2048, end % 2048, honmon.name),
                headword=head,
                text=text,
                spans=decoded.spans,
                decode_unknown_controls=decoded.unknown_controls,
                decode_unknown_bytes=decoded.unknown_bytes,
            )
            yield self._attach_sidecar_supplements(entry)
            count += 1
            if limit is not None and count >= limit:
                break

    def _body_pointer_offsets(self, component: Component) -> list[int]:
        key = component.name.lower()
        if key in self._body_pointer_cache:
            return self._body_pointer_cache[key]
        offsets: set[int] = set()
        for parsed in self.indexes().values():
            for row in parsed.rows:
                for address in (row.body,):
                    target = self.component_for_address(address, role=ComponentRole.HONMON)
                    if target is None or target.name.lower() != key:
                        continue
                    offset = self._relative_offset(target, address)
                    if 0 <= offset < self.data(target).expanded_size:
                        offsets.add(offset)
        self._body_pointer_cache[key] = sorted(offsets)
        return self._body_pointer_cache[key]

    def _decode_anchor_at(self, component: Component, start: int, *, max_bytes: int = 96) -> tuple[str, int]:
        # Observed dense HONMON sidecar packages expose short decimal anchor
        # identifiers in the raw HONMON record. Non-numeric records are left
        # unresolved instead of guessed; a future provider can add another
        # anchor scheme when corpus evidence is strong enough.
        reader = self.data(component)
        data = reader.read(start, min(max_bytes, max(reader.expanded_size - start, 0)))
        next_marker = data.find(ENTRY_MARKER, 1)
        if next_marker > 0:
            data = data[:next_marker]
        decoded = decode_text_stream(data, self.gaiji.mapping)
        compact = "".join(ch for ch in decoded.text if not ch.isspace() and ch != "\x00")
        if compact.isdigit() and 4 <= len(compact) <= 16:
            return compact, len(data)
        return "", len(data)

    def _dense_anchor_evidence(self, component: Component) -> dict[str, object]:
        pointer_offsets = self._body_pointer_offsets(component)
        sample_offsets = pointer_offsets[:64]
        if not sample_offsets:
            sample_offsets = self._markers_for_component(component, limit=64)[:64]
        ids: list[str] = []
        lengths: list[int] = []
        for offset in sample_offsets:
            anchor_id, size = self._decode_anchor_at(component, offset)
            if anchor_id:
                ids.append(anchor_id)
            lengths.append(size)
        numeric_ratio = len(ids) / len(sample_offsets) if sample_offsets else 0.0
        common_gap = None
        unique_offsets = sorted(set(pointer_offsets[:4096]))
        gaps = [b - a for a, b in zip(unique_offsets, unique_offsets[1:]) if b > a]
        if gaps:
            counts: dict[int, int] = {}
            for gap in gaps:
                counts[gap] = counts.get(gap, 0) + 1
            common_gap = max(counts.items(), key=lambda item: (item[1], -item[0]))[0]
        dense_record_size = common_gap if common_gap in {16, 32, 40, 48, 64, 80, 96, 112, 128, 160, 320} else None
        if dense_record_size is None and lengths:
            median = int(sorted(lengths)[len(lengths) // 2])
            if median <= 160:
                dense_record_size = median
        return {
            "anchor_ids": ids,
            "numeric_ratio": numeric_ratio,
            "sample_count": len(sample_offsets),
            "common_gap": common_gap,
            "dense_record_size": dense_record_size,
        }

    def _choose_body_sidecar(self, sidecars: tuple[SidecarInfo, ...]) -> SidecarInfo | None:
        renderable = [sidecar for sidecar in sidecars if sidecar.table and sidecar.id_column and (sidecar.html_column or sidecar.plain_column)]
        if not renderable:
            return None
        for sidecar in renderable:
            lower = sidecar.path.name.lower()
            if lower.startswith("vlpljbl") and sidecar.kind in {"t_contents", "honbun"}:
                return sidecar
        return renderable[0]

    def body_source(self) -> BodySourceInfo:
        if self._body_source_cache is not None:
            return self._body_source_cache
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            self._body_source_cache = BodySourceInfo(
                package_family=self.info.family,
                ssed_kind=SsedBodySourceKind.MISSING_BODY_COMPONENT,
                support=BodySourceSupport.UNSUPPORTED,
                confidence=Confidence.PROVEN,
                notes=("missing HONMON component",),
            )
            return self._body_source_cache
        reader = self.data(honmon)
        marker_count = len(self._markers_for_component(honmon, limit=2000000))
        marker_density = marker_count / max(reader.expanded_size, 1)
        sidecar_paths = tuple(self._sidecar_file_candidates())
        evidence = self._dense_anchor_evidence(honmon)
        numeric_ratio = float(evidence["numeric_ratio"])
        is_dense = numeric_ratio >= 0.6 and int(evidence["sample_count"]) >= 4
        sidecars = self._body_sidecars() if is_dense else ()
        chosen_sidecar = self._choose_body_sidecar(sidecars)

        if is_dense:
            if chosen_sidecar is not None:
                if chosen_sidecar.kind == "honbun":
                    kind = SsedBodySourceKind.HONBUN_SIDECAR
                elif chosen_sidecar.path.name.lower().startswith("vlpljbl"):
                    kind = SsedBodySourceKind.RENDERER_SQLITE_SIDECAR
                else:
                    kind = SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR
                support = BodySourceSupport.PARTIALLY_RENDERABLE
                provider = "sqlite_sidecar"
                sidecar_kind = chosen_sidecar.kind
                notes = ("HONMON body pointers resolve to short numeric anchor records; selected SQLite sidecar body provider",)
            elif sidecars:
                kind = SsedBodySourceKind.SIDECAR_UNKNOWN
                support = BodySourceSupport.DEFERRED
                provider = "sqlite_sidecar_deferred"
                sidecar_kind = ",".join(dict.fromkeys(sidecar.kind for sidecar in sidecars))
                notes = ("HONMON body pointers resolve to short numeric anchor records; sidecar files were found but no supported body table schema was identified",)
            else:
                kind = SsedBodySourceKind.DENSE_ANCHOR_TABLE
                support = BodySourceSupport.DEFERRED
                provider = "dense_anchor_deferred"
                sidecar_kind = None
                notes = ("HONMON body pointers resolve to short numeric anchor records; no supported body sidecar found",)
        else:
            kind = SsedBodySourceKind.BODY_STREAM
            support = BodySourceSupport.RENDERABLE
            provider = "honmon_body_stream"
            sidecar_kind = None
            notes = ("HONMON body pointers resolve directly into readable body-stream data",)

        self._body_source_cache = BodySourceInfo(
            package_family=self.info.family,
            ssed_kind=kind,
            support=support,
            confidence=Confidence.INFERRED if is_dense else Confidence.PROVEN,
            honmon_component=honmon.name,
            expanded_size=reader.expanded_size,
            marker_count=marker_count,
            marker_density=round(marker_density, 8),
            dense_record_size=evidence.get("dense_record_size") if is_dense else None,
            anchor_count=len(evidence.get("anchor_ids") or ()) if is_dense else None,
            sidecar_paths=sidecar_paths,
            sidecar_kind=sidecar_kind,
            render_provider=provider,
            notes=notes,
            sidecars=sidecars,
        )
        return self._body_source_cache

    def validate_body_source(self) -> BodySourceInfo:
        return self.body_source()

    def supports_entry_rendering(self) -> bool:
        return self.body_source().support in {BodySourceSupport.RENDERABLE, BodySourceSupport.PARTIALLY_RENDERABLE}

    @staticmethod
    def _next_after(sorted_offsets: list[int], start: int) -> int | None:
        lo = 0
        hi = len(sorted_offsets)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_offsets[mid] <= start:
                lo = mid + 1
            else:
                hi = mid
        return sorted_offsets[lo] if lo < len(sorted_offsets) else None

    def _entry_range_for_address(self, address: Address, *, max_bytes: int = 512 * 1024) -> tuple[Component, int, int, tuple[Diagnostic, ...]]:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None:
            raise KeyError(f"no HONMON component contains address {address}")
        reader = self.data(honmon)
        start = self._relative_offset(honmon, address)
        if start < 0 or start >= reader.expanded_size:
            raise ValueError(f"entry address outside HONMON bounds: {address}")

        candidates: list[tuple[int, str]] = []
        next_pointer = self._next_after(self._body_pointer_offsets(honmon), start)
        if next_pointer is not None:
            candidates.append((next_pointer, "next_body_pointer"))
        if next_pointer is None:
            next_marker = self._next_after(self._markers_for_component(honmon), start)
            if next_marker is not None:
                candidates.append((next_marker, "next_marker"))
        candidates.append((reader.expanded_size, "component_end"))

        fallback_end = min(reader.expanded_size, start + max_bytes)
        if fallback_end < reader.expanded_size:
            candidates.append((fallback_end, "max_bytes_fallback"))

        end, source = min((candidate for candidate in candidates if candidate[0] > start), key=lambda item: item[0])
        diagnostics: list[Diagnostic] = []
        if source == "max_bytes_fallback":
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    area=DiagnosticArea.BODY,
                    code="entry_range_fallback",
                    message="entry end was limited by max_bytes fallback",
                    location=self._location_for_address(address, role=ComponentRole.HONMON),
                    details={"max_bytes": max_bytes},
                )
            )
        return honmon, start, end, tuple(diagnostics)

    def entry_at(self, address: Address, *, max_bytes: int = 512 * 1024) -> Entry:
        honmon, start, end_offset, diagnostics = self._entry_range_for_address(address, max_bytes=max_bytes)
        reader = self.data(honmon)
        decoded = self._decode_text_stream(reader.read(start, end_offset - start))
        lines = [line.strip() for line in decoded.text.splitlines() if line.strip()]
        headword = lines[0] if lines else ""
        entry = Entry(
            address=Address(address.block, address.offset, honmon.name),
            end_address=Address(honmon.start_block + end_offset // BLOCK_SIZE, end_offset % BLOCK_SIZE, honmon.name),
            headword=headword,
            text=decoded.text.strip("\x00"),
            spans=decoded.spans,
            entry_diagnostics=diagnostics,
            decode_unknown_controls=decoded.unknown_controls,
            decode_unknown_bytes=decoded.unknown_bytes,
        )
        return self._attach_sidecar_supplements(entry)

    def inspect_body_pointer(self, address: Address) -> BodyPointerInspection:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None:
            return BodyPointerInspection(
                diagnostics=(
                    Diagnostic(
                        severity=Severity.ERROR,
                        area=DiagnosticArea.BODY,
                        code="body_pointer_outside_honmon",
                        message="body pointer does not resolve to a HONMON component",
                        location=self._location_for_address(address, role=ComponentRole.HONMON),
                    ),
                )
            )
        start = self._relative_offset(honmon, address)
        anchor_id, length = self._decode_anchor_at(honmon, start)
        raw_text_hash = hashlib.sha256(anchor_id.encode("utf-8")).hexdigest()[:16] if anchor_id else None
        return BodyPointerInspection(anchor_id=anchor_id or None, raw_text_hash=raw_text_hash, raw_text_length=len(anchor_id), record_offset=start, record_length=length)

    @staticmethod
    def _anchor_query_values(anchor_id: str, sidecar: SidecarInfo) -> tuple[object, ...]:
        values: list[object] = [anchor_id]
        stripped = anchor_id.lstrip("0") or "0"
        if stripped != anchor_id:
            values.append(stripped)
        if sidecar.kind != "honbun":
            try:
                values.append(int(stripped))
            except ValueError:
                pass
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _sidecar_debug_details(sidecar: SidecarInfo, anchor_id: str) -> dict[str, object]:
        query_values = [str(value) for value in LogoVistaPackage._anchor_query_values(anchor_id, sidecar)]
        return {
            "anchor_id": anchor_id,
            "query_values": query_values,
            "sidecar": sidecar.path.name,
            "sidecar_kind": sidecar.kind,
            "storage": sidecar.storage,
            "table": sidecar.table,
            "id_column": sidecar.id_column,
            "title_column": sidecar.title_column,
            "html_column": sidecar.html_column,
            "plain_column": sidecar.plain_column,
        }

    def _fetch_sidecar_body(self, sidecar: SidecarInfo, anchor_id: str) -> SidecarBody | None:
        if not sidecar.table or not sidecar.id_column:
            return None
        sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
        try:
            con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
        except sqlite3.DatabaseError:
            return None
        try:
            select_columns = [sidecar.id_column]
            for column in (sidecar.title_column, sidecar.html_column, sidecar.plain_column):
                if column and column not in select_columns:
                    select_columns.append(column)
            quoted = ", ".join(quote_sql_identifier(column) for column in select_columns)
            sql = (
                f"select {quoted} from {quote_sql_identifier(sidecar.table)} "
                f"where {quote_sql_identifier(sidecar.id_column)}=? limit 1"
            )
            row = None
            for value in self._anchor_query_values(anchor_id, sidecar):
                row = con.execute(sql, (value,)).fetchone()
                if row is not None:
                    break
            if row is None:
                return None
            title = str(row[sidecar.title_column]) if sidecar.title_column and row[sidecar.title_column] is not None else ""
            html_value = str(row[sidecar.html_column]) if sidecar.html_column and row[sidecar.html_column] is not None else ""
            plain_value = str(row[sidecar.plain_column]) if sidecar.plain_column and row[sidecar.plain_column] is not None else ""
            text = plain_value.strip() or strip_html(html_value) or title.strip()
            return SidecarBody(title=strip_html(title), text=text, html=html_value or None, source=sidecar)
        finally:
            con.close()

    def _placeholder_entry(
        self,
        address: Address,
        *,
        headword: str,
        code: str,
        message: str,
        severity: Severity = Severity.WARNING,
        details: dict[str, object] | None = None,
    ) -> Entry:
        placeholder = "Entry body is not yet supported for this LogoVista body source."
        diagnostic = Diagnostic(
            severity=severity,
            area=DiagnosticArea.BODY,
            code=code,
            message=message,
            location=self._location_for_address(address, role=ComponentRole.HONMON),
            details=details or {},
        )
        qualified = self._qualified_address(address, role=ComponentRole.HONMON)
        return Entry(
            address=qualified,
            end_address=qualified,
            headword=headword,
            text=placeholder,
            spans=(Span(kind="text", text=placeholder),),
            entry_diagnostics=(diagnostic,),
        )

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        anchor_id = inspection.anchor_id
        if not anchor_id:
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="dense_anchor_missing_id",
                message="dense HONMON record did not expose a numeric anchor id",
                severity=Severity.ERROR,
            )
        body = self._fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="sidecar_body_not_found",
                message="body sidecar did not contain a row for the dense HONMON anchor",
                severity=Severity.ERROR,
                details=self._sidecar_debug_details(sidecar, anchor_id),
            )
        note = Diagnostic(
            severity=Severity.INFO,
            area=DiagnosticArea.BODY,
            code="sidecar_body_resolved",
            message="entry body resolved from SSED sidecar database",
            location=self._location_for_address(hit.body, role=ComponentRole.HONMON),
            details=self._sidecar_debug_details(sidecar, anchor_id),
        )
        text = body.text or body.title or hit.heading
        entry = Entry(
            address=hit.body,
            end_address=hit.body,
            headword=body.title or hit.heading,
            text=text,
            spans=(Span(kind="text", text=text),),
            entry_diagnostics=(note,),
        )
        return self._attach_sidecar_supplements(entry)

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        comps = [component] if component is not None else list(self.components_by_role(ComponentRole.TITLE))
        out: list[str] = []
        for comp in comps:
            decoded = self.decode_component(comp)
            for line in decoded.text.splitlines():
                line = line.strip()
                if line:
                    out.append(line)
                    if limit is not None and len(out) >= limit:
                        return out
        return out

    def resolve_title(self, address: Address, *, max_bytes: int = 4096) -> tuple[str, tuple[Diagnostic, ...]]:
        component = self.component_for_address(address, role=ComponentRole.TITLE)
        if component is None:
            return "", (
                Diagnostic(
                    severity=Severity.WARNING,
                    area=DiagnosticArea.INDEX,
                    code="title_dereference_failed",
                    message="no title component contains the title pointer",
                    location=self._location_for_address(address, role=ComponentRole.TITLE),
                    details={"reason": "missing_title_component"},
                ),
            )
        reader = self.data(component)
        start = self._relative_offset(component, address)
        if start < 0 or start >= reader.expanded_size:
            return "", (
                Diagnostic(
                    severity=Severity.WARNING,
                    area=DiagnosticArea.INDEX,
                    code="title_dereference_failed",
                    message="title pointer is outside component bounds",
                    location=self._location_for_address(address, role=ComponentRole.TITLE),
                    details={"reason": "pointer_outside_component"},
                ),
            )
        data = reader.read(start, min(max_bytes, reader.expanded_size - start))
        if not data:
            return "", ()
        end_candidates = [
            pos
            for marker in (b"\x1f\x0a", b"\x0a")
            for pos in [data.find(marker)]
            if pos >= 0
        ]
        end = min(end_candidates) if end_candidates else len(data)
        decoded = self._decode_text_stream(data[:end])
        title = " ".join(line.strip() for line in decoded.text.splitlines() if line.strip()).strip("\x00 ")
        if title:
            return title, ()
        return "", (
            Diagnostic(
                severity=Severity.WARNING,
                area=DiagnosticArea.INDEX,
                code="title_dereference_empty",
                message="title pointer decoded to an empty title",
                location=self._location_for_address(address, role=ComponentRole.TITLE),
                details={"reason": "empty_title"},
            ),
        )

    def indexes(self, component: str | Component | None = None) -> dict[str, IndexParse]:
        comps = [component] if component is not None else list(self.components_by_role(ComponentRole.INDEX))
        out: dict[str, IndexParse] = {}
        for comp in comps:
            item = self.component(comp) if isinstance(comp, str) else comp
            if item is None or item.path is None:
                continue
            key = item.name.lower()
            if key not in self._index_cache:
                self._index_cache[key] = parse_index(self.expanded(item), item.start_block, item.type, self.gaiji.mapping)
            out[item.name] = self._index_cache[key]
        return out

    @staticmethod
    def _index_component_matches_profile(component: Component, profile: SearchProfile) -> bool:
        name = component.name.upper()
        if profile in {SearchProfile.NATIVE, SearchProfile.EXACT}:
            return True
        if profile == SearchProfile.FORWARD:
            return name.startswith(("FK", "FH", "KW"))
        if profile == SearchProfile.BACKWARD:
            return name.startswith(("BK", "BH"))
        return True

    @staticmethod
    def _is_backward_index(component_name: str) -> bool:
        return component_name.upper().startswith(("BK", "BH"))

    @staticmethod
    def _row_display_key(row: IndexRow, *, backward: bool = False) -> str:
        value = row.target_key or row.key
        return natural_backward_key(value) if backward else value

    @staticmethod
    def _row_dedupe_key(row: IndexRow) -> tuple[int, int, int, int]:
        return (row.body.block, row.body.offset, row.title.block, row.title.offset)

    @staticmethod
    def _row_key_values(row: IndexRow) -> tuple[str, ...]:
        values = [row.key]
        if row.target_key and row.target_key != row.key:
            values.append(row.target_key)
        return tuple(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _heading_fallback(
        *,
        display_key: str,
        matched_key: str,
        row: IndexRow,
    ) -> tuple[str, str]:
        if display_key:
            return display_key, "display_key"
        if row.target_key:
            return row.target_key, "target_key"
        if row.key:
            return row.key, "row_key"
        return matched_key, "fallback"

    def _cached_search_values(
        self,
        component_name: str,
        parsed: IndexParse,
    ) -> tuple[SearchValueRow, ...]:
        key = component_name.lower()
        if key in self._search_value_cache:
            return self._search_value_cache[key]
        backward = self._is_backward_index(component_name)
        cached = []
        for row in parsed.rows:
            stored_values = self._row_key_values(row)
            natural_values = tuple(dict.fromkeys(natural_backward_key(value) if backward else value for value in stored_values))
            stored_normalized = tuple(dict.fromkeys(normalize_query(value) for value in stored_values if value))
            natural_normalized = tuple(dict.fromkeys(normalize_query(value) for value in natural_values if value))
            cached.append((row, stored_values, natural_values, stored_normalized, natural_normalized))
        self._search_value_cache[key] = tuple(cached)
        return self._search_value_cache[key]

    def _cached_exact_values(self, component_name: str, parsed: IndexParse) -> dict[str, tuple[tuple[IndexRow, str], ...]]:
        key = component_name.lower()
        if key in self._exact_search_cache:
            return self._exact_search_cache[key]
        backward = self._is_backward_index(component_name)
        rows_by_key: dict[str, list[tuple[IndexRow, str]]] = {}
        for row, stored_values, natural_values, stored_normalized, natural_normalized in self._cached_search_values(component_name, parsed):
            values: list[tuple[str, str]] = []
            values.extend(zip(natural_values, natural_normalized))
            if backward:
                values.extend((natural_backward_key(value), normalized) for value, normalized in zip(stored_values, stored_normalized))
            seen: set[tuple[int, int, int, int, str]] = set()
            for display_value, normalized_value in values:
                if not normalized_value:
                    continue
                dedupe_key = (*self._row_dedupe_key(row), display_value)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows_by_key.setdefault(normalized_value, []).append((row, display_value))
        self._exact_search_cache[key] = {normalized: tuple(rows) for normalized, rows in rows_by_key.items()}
        return self._exact_search_cache[key]

    def _row_matches(
        self,
        *,
        stored_values: tuple[str, ...],
        natural_values: tuple[str, ...],
        stored_normalized: tuple[str, ...],
        natural_normalized: tuple[str, ...],
        query: str,
        candidates: tuple[str, ...],
        profile: SearchProfile,
        backward: bool,
    ) -> str | None:
        normalized_query = normalize_query(query)
        normalized_candidates = {normalize_query(candidate) for candidate in candidates if candidate}

        if profile == SearchProfile.EXACT:
            for value in natural_values:
                if value in candidates or normalize_query(value) in normalized_candidates:
                    return value
            for value, normalized_value in zip(stored_values, stored_normalized):
                if value in candidates or normalized_value in normalized_candidates:
                    return natural_backward_key(value) if backward else value
            return None

        if profile == SearchProfile.FORWARD:
            for value, normalized_value in zip(natural_values, natural_normalized):
                if normalized_value.startswith(normalized_query) or any(value.startswith(candidate) for candidate in candidates):
                    return value
            return None

        if profile == SearchProfile.BACKWARD:
            reversed_query = normalized_query[::-1]
            for stored_value, stored_norm, natural_value, natural_norm in zip(
                stored_values,
                stored_normalized,
                natural_values,
                natural_normalized,
            ):
                if stored_norm.startswith(reversed_query) or natural_norm.endswith(normalized_query) or stored_norm.endswith(normalized_query):
                    return natural_value if backward else stored_value
            return None

        return None

    def _iter_matching_rows(
        self,
        query: str,
        profile: SearchProfile,
        *,
        include_backward_exact: bool = True,
    ) -> Iterable[tuple[str, Component, IndexRow, str]]:
        candidates = query_candidates(query)
        for name, parsed in self.indexes().items():
            component = self.component(name)
            if component is None or not self._index_component_matches_profile(component, profile):
                continue
            backward = self._is_backward_index(name)
            if profile == SearchProfile.EXACT and backward and not include_backward_exact:
                continue
            if profile == SearchProfile.EXACT:
                yielded: set[tuple[int, int, int, int, str]] = set()
                for candidate in candidates:
                    normalized_candidate = normalize_query(candidate)
                    if not normalized_candidate:
                        continue
                    for row, matched in self._cached_exact_values(name, parsed).get(normalized_candidate, ()):
                        dedupe_key = (*self._row_dedupe_key(row), matched)
                        if dedupe_key in yielded:
                            continue
                        yielded.add(dedupe_key)
                        yield name, component, row, matched
                continue
            for row, stored_values, natural_values, stored_normalized, natural_normalized in self._cached_search_values(name, parsed):
                matched = self._row_matches(
                    stored_values=stored_values,
                    natural_values=natural_values,
                    stored_normalized=stored_normalized,
                    natural_normalized=natural_normalized,
                    query=query,
                    candidates=candidates,
                    profile=profile,
                    backward=backward,
                )
                if matched is not None:
                    yield name, component, row, matched

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
    ) -> SearchHit:
        backward = self._is_backward_index(component_name)
        display_key = self._row_display_key(row, backward=backward)
        body = self._qualified_address(row.body, role=ComponentRole.HONMON)
        title = self._qualified_address(row.title, role=ComponentRole.TITLE)
        fallback_heading, fallback_source = self._heading_fallback(display_key=display_key, matched_key=matched_key, row=row)
        diagnostics: tuple[Diagnostic, ...] = ()
        title_text = ""
        heading = fallback_heading
        heading_source = fallback_source
        title_status = "fallback"
        title_diagnostic_code: str | None = None
        title_reason: str | None = None
        raw_title_component = self.component_for_address(row.title)
        title_components = self.components_by_role(ComponentRole.TITLE)
        title_resolution: dict[str, object] = {
            "body": body.to_dict(),
            "title": title.to_dict(),
            "raw_title": row.title.to_dict(),
            "raw_body": row.body.to_dict(),
            "row_title_equals_body": row.title == row.body,
            "fallback_heading_source": fallback_source,
        }
        if raw_title_component is not None:
            title_resolution["raw_title_component"] = {
                "name": raw_title_component.name,
                "role": raw_title_component.role.value,
                "type": f"{raw_title_component.type:02x}",
            }
        if row.title == row.body and raw_title_component is not None and raw_title_component.role == ComponentRole.HONMON:
            title_reason = "title_pointer_is_body_pointer"
        elif not title_components and raw_title_component is not None and raw_title_component.role == ComponentRole.HONMON:
            title_reason = "title_pointer_hits_honmon_without_title_components"
        else:
            title_text, diagnostics = self.resolve_title(title)
            if title_text:
                heading = title_text
                heading_source = "title"
                title_status = "resolved"
            elif diagnostics:
                first = diagnostics[0]
                title_status = "failed" if first.code == "title_dereference_failed" else "missing"
                title_diagnostic_code = first.code
                title_reason = str(first.details.get("reason") or first.code)
            else:
                title_status = "missing"
                title_reason = "empty_title_data"
        if title_reason:
            title_resolution["reason"] = title_reason
        title_resolution["status"] = title_status
        title_resolution["heading_source"] = heading_source
        title_resolution["diagnostic_code"] = title_diagnostic_code
        return SearchHit(
            id=hit_id,
            query=query,
            normalized_query=normalized_query,
            search_profile=profile,
            package_id=self.info.dict_id,
            index_component=component_name,
            display_key=display_key,
            matched_key=matched_key,
            target_key=row.target_key,
            heading=heading,
            heading_source=heading_source,
            title_status=title_status,
            body=body,
            title=title,
            tagged=row.tagged,
            title_diagnostic_code=title_diagnostic_code,
            title_reason=title_reason,
            diagnostics=diagnostics,
            page=row.page,
            row=row.row,
            raw_row=row,
            body_source=self.body_source().to_dict(debug=False),
            title_resolution=title_resolution,
            _package=self,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
        debug: bool = False,
    ) -> SearchResults:
        if isinstance(profile, str):
            profile = SearchProfile(profile)
        normalized_query = normalize_query(query)
        if not normalized_query and not str(query or "").strip():
            return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=())

        profiles = (SearchProfile.EXACT, SearchProfile.FORWARD, SearchProfile.BACKWARD) if profile == SearchProfile.NATIVE else (profile,)
        hits: list[SearchHit] = []
        seen: set[tuple[int, int, int, int]] = set()
        for effective_profile in profiles:
            before_profile = len(hits)
            if effective_profile == SearchProfile.EXACT:
                primary_matches = list(self._iter_matching_rows(query, effective_profile, include_backward_exact=False))
                row_matches = primary_matches or list(self._iter_matching_rows(query, effective_profile, include_backward_exact=True))
            else:
                row_matches = self._iter_matching_rows(query, effective_profile)
            for name, _component, row, matched_key in row_matches:
                dedupe_key = self._row_dedupe_key(row)
                if not debug and dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                hits.append(
                    self._make_hit(
                        hit_id=len(hits) + 1,
                        query=query,
                        normalized_query=normalized_query,
                        profile=effective_profile if profile == SearchProfile.NATIVE else profile,
                        component_name=name,
                        row=row,
                        matched_key=matched_key,
                    )
                )
                if len(hits) >= limit:
                    return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))
            if profile == SearchProfile.NATIVE and len(hits) > before_profile:
                return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))
        return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))

    def search_index(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[dict[str, object]]:
        return [
            {
                "component": hit.index_component,
                **(hit.raw_row.to_dict() if hit.raw_row is not None else {}),
                "heading": hit.heading,
                "display_key": hit.display_key,
            }
            for hit in self.search(term, limit=limit, profile=profile).hits
        ]

    def search_entries(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[Entry]:
        return [self.entry_for_hit(hit) for hit in self.search(term, limit=limit, profile=profile).hits]

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        source = self.body_source()
        if source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            try:
                return self.entry_at(hit.body)
            except (FormatError, KeyError, ValueError, OSError) as exc:
                return self._placeholder_entry(
                    hit.body,
                    headword=hit.heading,
                    code="body_pointer_unresolved",
                    message="body pointer could not be resolved to a readable HONMON entry",
                    severity=Severity.ERROR,
                    details={
                        "body": hit.body.to_dict(),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
        if source.ssed_kind in {
            SsedBodySourceKind.DENSE_ANCHOR_TABLE,
            SsedBodySourceKind.DENSE_MARKER_TABLE,
            SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR,
            SsedBodySourceKind.RENDERER_SQLITE_SIDECAR,
            SsedBodySourceKind.DICTFULLDB_SIDECAR,
            SsedBodySourceKind.HONBUN_SIDECAR,
            SsedBodySourceKind.VLPLJBL_SIDECAR,
            SsedBodySourceKind.SIDECAR_UNKNOWN,
        }:
            inspection = self.inspect_body_pointer(hit.body)
            sidecar = self._choose_body_sidecar(source.sidecars)
            if sidecar is not None:
                return self._entry_from_sidecar(hit, sidecar, inspection)
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="unsupported_body_source",
                message="SSED dense HONMON body source is not renderable without a supported sidecar",
                severity=Severity.ERROR,
                details={"body_source": source.ssed_kind.value},
            )
        if source.ssed_kind == SsedBodySourceKind.MISSING_BODY_COMPONENT:
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="missing_body_component",
                message="local SSED package declares no readable HONMON component for entry bodies",
                severity=Severity.ERROR,
                details={"body_source": source.ssed_kind.value, "missing_component": "HONMON.DIC"},
            )
        return self._placeholder_entry(
            hit.body,
            headword=hit.heading,
            code="unsupported_body_source",
            message="entry body source is not supported by lvcore",
            severity=Severity.ERROR,
            details={"body_source": source.ssed_kind.value},
        )

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.render_entry_html(self.entry_for_hit(hit), profile=profile, include_diagnostics=include_diagnostics)

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.render_entry_text(self.entry_for_hit(hit))

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
        sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
        try:
            con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
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
        finally:
            con.close()

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
                sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
                con: sqlite3.Connection | None = None
                try:
                    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
                    row = con.execute(
                        f"select {quote_sql_identifier(blob_column)} from {quote_sql_identifier(table)} where rowid=?",
                        (row_id,),
                    ).fetchone()
                except sqlite3.DatabaseError:
                    return None
                finally:
                    if con is not None:
                        con.close()
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

    @staticmethod
    def _count_diagnostics(target: tuple[Diagnostic, ...], by_severity: dict[str, int], by_area: dict[str, int], by_code: dict[str, int]) -> None:
        for diagnostic in target:
            by_severity[diagnostic.severity.value] = by_severity.get(diagnostic.severity.value, 0) + 1
            by_area[diagnostic.area.value] = by_area.get(diagnostic.area.value, 0) + 1
            by_code[diagnostic.code] = by_code.get(diagnostic.code, 0) + 1

    @staticmethod
    def _increment_reason(counts: dict[str, int], reason: object) -> None:
        key = str(reason or "unknown")
        counts[key] = counts.get(key, 0) + 1

    def _count_document_resources(self, resources: tuple[ResourceRef, ...], counters: dict[str, object]) -> None:
        gaiji_by_reason = counters.setdefault("unresolved_gaiji_by_reason", {})
        gaiji_by_status = counters.setdefault("gaiji_by_status", {})
        gaiji_by_source = counters.setdefault("gaiji_by_source", {})
        gaiji_by_reason = counters.setdefault("gaiji_by_reason", gaiji_by_reason)
        media_by_reason = counters.setdefault("unresolved_media_by_reason", {})
        media_kind_counts = counters.setdefault("media_kind_counts", {})
        media_mime_counts = counters.setdefault("media_mime_counts", {})
        media_store_kind_counts = counters.setdefault("media_store_kind_counts", {})
        colscr_malformed = counters.setdefault("colscr_records_malformed_by_reason", {})
        pcmdata_unresolved = counters.setdefault("pcmdata_ranges_unresolved_by_reason", {})
        media_bytes_unavailable = counters.setdefault("media_bytes_unavailable_by_reason", {})
        for key, value in (
            ("unresolved_gaiji_by_reason", gaiji_by_reason),
            ("gaiji_by_status", gaiji_by_status),
            ("gaiji_by_source", gaiji_by_source),
            ("gaiji_by_reason", counters.get("gaiji_by_reason")),
        ):
            if not isinstance(value, dict):
                value = {}
                counters[key] = value
        gaiji_by_reason = counters["unresolved_gaiji_by_reason"]
        if not isinstance(media_by_reason, dict):
            media_by_reason = {}
            counters["unresolved_media_by_reason"] = media_by_reason
        for key, value in (
            ("media_kind_counts", media_kind_counts),
            ("media_mime_counts", media_mime_counts),
            ("media_store_kind_counts", media_store_kind_counts),
            ("colscr_records_malformed_by_reason", colscr_malformed),
            ("pcmdata_ranges_unresolved_by_reason", pcmdata_unresolved),
            ("media_bytes_unavailable_by_reason", media_bytes_unavailable),
        ):
            if not isinstance(value, dict):
                value = {}
                counters[key] = value
        for resource in resources:
            status = resource.status.value if hasattr(resource.status, "value") else str(resource.status)
            reason = resource.details.get("reason")
            if resource.kind == ResourceKind.GAIJI:
                info = self.resource_info(resource)
                info_details = info.get("details") if isinstance(info.get("details"), dict) else {}
                display_status = str(
                    resource.details.get("display_status")
                    or info.get("display_status")
                    or info_details.get("display_status")
                    or ("unresolved" if status != "resolved" else "unicode_mapped")
                )
                display_reason = resource.details.get("reason") or info.get("reason") or info_details.get("reason") or reason
                source = resource.details.get("source") or info.get("source") or info_details.get("source") or "unknown"
                counters["gaiji_occurrences"] = int(counters.get("gaiji_occurrences", 0)) + 1
                status_key = f"gaiji_{display_status}"
                counters[status_key] = int(counters.get(status_key, 0)) + 1
                if isinstance(counters.get("gaiji_by_status"), dict):
                    self._increment_reason(counters["gaiji_by_status"], display_status)
                if isinstance(counters.get("gaiji_by_source"), dict):
                    self._increment_reason(counters["gaiji_by_source"], source)
                if isinstance(counters.get("gaiji_by_reason"), dict):
                    self._increment_reason(counters["gaiji_by_reason"], display_reason)
                byte_length = info.get("byte_length")
                if isinstance(byte_length, int) and byte_length > 0:
                    counters["gaiji_resource_bytes_available"] = int(counters.get("gaiji_resource_bytes_available", 0)) + 1
                elif display_status in {GaijiDisplayStatus.BITMAP_BACKED.value, GaijiDisplayStatus.IMAGE_BACKED.value}:
                    unavailable = counters.setdefault("gaiji_resource_bytes_unavailable_by_reason", {})
                    if isinstance(unavailable, dict):
                        self._increment_reason(unavailable, display_reason)
                if display_status != GaijiDisplayStatus.UNRESOLVED.value:
                    counters["resolved_gaiji"] = int(counters.get("resolved_gaiji", 0)) + 1
                else:
                    counters["unresolved_gaiji"] = int(counters.get("unresolved_gaiji", 0)) + 1
                    counters["gaiji_display_unresolved"] = int(counters.get("gaiji_display_unresolved", 0)) + 1
                    self._increment_reason(gaiji_by_reason, display_reason)
            elif resource.kind in {ResourceKind.MEDIA, ResourceKind.IMAGE, ResourceKind.AUDIO}:
                info = self.resource_info(resource)
                kind_value = resource.kind.value
                if isinstance(counters["media_kind_counts"], dict):
                    self._increment_reason(counters["media_kind_counts"], kind_value)
                if info.get("status") == "resolved":
                    counters["resolved_media"] = int(counters.get("resolved_media", 0)) + 1
                    counters["media_bytes_available"] = int(counters.get("media_bytes_available", 0)) + 1
                    if isinstance(counters["media_mime_counts"], dict):
                        self._increment_reason(counters["media_mime_counts"], info.get("mime_type"))
                    if isinstance(counters["media_store_kind_counts"], dict):
                        self._increment_reason(counters["media_store_kind_counts"], info.get("store_kind"))
                    if info.get("store_kind") == "colscr":
                        counters["colscr_records_resolved"] = int(counters.get("colscr_records_resolved", 0)) + 1
                    elif info.get("store_kind") == "pcmdata":
                        counters["pcmdata_ranges_resolved"] = int(counters.get("pcmdata_ranges_resolved", 0)) + 1
                else:
                    counters["unresolved_media"] = int(counters.get("unresolved_media", 0)) + 1
                    info_details = info.get("details") if isinstance(info.get("details"), dict) else {}
                    unresolved_reason = info_details.get("reason") if isinstance(info_details, dict) else reason
                    self._increment_reason(media_by_reason, unresolved_reason)
                    if isinstance(counters["media_bytes_unavailable_by_reason"], dict):
                        self._increment_reason(counters["media_bytes_unavailable_by_reason"], unresolved_reason)
                    store_reason = str(unresolved_reason or "unknown")
                    if store_reason.startswith(("missing_data", "malformed_data", "truncated_data", "media_target", "target_media", "truncated_media")):
                        if isinstance(counters["colscr_records_malformed_by_reason"], dict):
                            self._increment_reason(counters["colscr_records_malformed_by_reason"], unresolved_reason)
                    if store_reason.startswith(("target_pcm", "range_", "zero_length", "malformed_audio")):
                        if isinstance(counters["pcmdata_ranges_unresolved_by_reason"], dict):
                            self._increment_reason(counters["pcmdata_ranges_unresolved_by_reason"], unresolved_reason)

    @staticmethod
    def _iter_inline_nodes(blocks: tuple[BlockNode, ...]):
        stack: list[InlineNode] = [node for block in reversed(blocks) for node in reversed(block.inlines)]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def _count_document_links(self, blocks: tuple[BlockNode, ...], counters: dict[str, object]) -> None:
        link_by_reason = counters.setdefault("unresolved_link_by_reason", {})
        if not isinstance(link_by_reason, dict):
            link_by_reason = {}
            counters["unresolved_link_by_reason"] = link_by_reason
        for node in self._iter_inline_nodes(blocks):
            if node.kind != InlineKind.LINK:
                continue
            target = node.attrs.get("link_target") if isinstance(node.attrs, dict) else None
            target = target if isinstance(target, dict) else {}
            status = str(target.get("status") or "unresolved")
            if status in {"resolved", "content", "deferred"}:
                counters["resolved_link"] = int(counters.get("resolved_link", 0)) + 1
            else:
                counters["unresolved_link"] = int(counters.get("unresolved_link", 0)) + 1
                self._increment_reason(link_by_reason, target.get("reason") or status)

    def validate(self, *, sample_entries: int = 3, sample_search_hits: int = 5) -> dict[str, object]:
        body_source = self.body_source()
        sidecar_roles = self.sidecar_role_summary()
        sidecar_supplement_counters = self.sidecar_supplement_summary()
        diagnostics_by_severity = {"info": 0, "warning": 0, "error": 0}
        diagnostics_by_area: dict[str, int] = {}
        diagnostics_by_code: dict[str, int] = {}
        title_failure_by_reason: dict[str, int] = {}
        title_status_counts: dict[str, int] = {}
        heading_source_counts: dict[str, int] = {}
        title_attempts = 0
        resource_counters: dict[str, object] = {
            "resolved_gaiji": 0,
            "unresolved_gaiji": 0,
            "unresolved_gaiji_by_reason": {},
            "gaiji_occurrences": 0,
            "gaiji_unicode_mapped": 0,
            "gaiji_bitmap_backed": 0,
            "gaiji_image_backed": 0,
            "gaiji_formatting_helper": 0,
            "gaiji_renderer_entry_backed": 0,
            "gaiji_display_unresolved": 0,
            "gaiji_search_fallback_missing": 0,
            "gaiji_resource_bytes_available": 0,
            "gaiji_resource_bytes_unavailable_by_reason": {},
            "gaiji_by_reason": {},
            "gaiji_by_source": {},
            "gaiji_by_status": {},
            "resolved_media": 0,
            "unresolved_media": 0,
            "unresolved_media_by_reason": {},
            "media_kind_counts": {},
            "media_mime_counts": {},
            "media_store_kind_counts": {},
            "colscr_records_resolved": 0,
            "colscr_records_malformed_by_reason": {},
            "pcmdata_ranges_resolved": 0,
            "pcmdata_ranges_unresolved_by_reason": {},
            "sidecar_media_resolved": 0,
            "sidecar_media_unresolved_by_reason": {},
            "media_bytes_available": 0,
            "media_bytes_unavailable_by_reason": {},
            "resolved_link": 0,
            "unresolved_link": 0,
            "unresolved_link_by_reason": {},
        }
        resource_counters["sidecar_media_resolved"] = int(sidecar_supplement_counters.get("sidecar_media_rows_resolved", 0) or 0)
        resource_counters["media_bytes_available"] = int(resource_counters.get("media_bytes_available", 0)) + int(
            sidecar_supplement_counters.get("sidecar_media_bytes_available", 0) or 0
        )
        if isinstance(resource_counters.get("media_mime_counts"), dict) and isinstance(sidecar_supplement_counters.get("sidecar_media_mime_counts"), dict):
            for mime, count in sidecar_supplement_counters["sidecar_media_mime_counts"].items():
                resource_counters["media_mime_counts"][mime] = resource_counters["media_mime_counts"].get(mime, 0) + int(count)
        if isinstance(resource_counters.get("media_store_kind_counts"), dict) and resource_counters["sidecar_media_resolved"]:
            resource_counters["media_store_kind_counts"]["sidecar_media"] = int(resource_counters["sidecar_media_resolved"])
        sidecar_reference_counters: dict[str, object] = {
            "addresses_checked": 0,
            "matched": 0,
            "by_role": {},
            "by_status": {},
            "by_table": {},
        }
        sidecar_reference_seen: set[tuple[int, int, str | None]] = set()
        decode_counters = {"unknown_controls": 0, "unknown_bytes": 0}
        decode_counter_seen: set[tuple[int, int, str | None]] = set()
        entries_checked = 0
        render_ok = 0
        entry_errors: list[str] = []

        def count_sidecar_references(address: Address) -> None:
            key = (address.block, address.offset, address.component)
            if key in sidecar_reference_seen:
                return
            sidecar_reference_seen.add(key)
            sidecar_reference_counters["addresses_checked"] = int(sidecar_reference_counters.get("addresses_checked", 0)) + 1
            matches = self.sidecar_references(address)
            if not matches:
                return
            sidecar_reference_counters["matched"] = int(sidecar_reference_counters.get("matched", 0)) + len(matches)
            by_role = sidecar_reference_counters.setdefault("by_role", {})
            by_status = sidecar_reference_counters.setdefault("by_status", {})
            by_table = sidecar_reference_counters.setdefault("by_table", {})
            if isinstance(by_role, dict) and isinstance(by_status, dict) and isinstance(by_table, dict):
                for match in matches:
                    self._increment_reason(by_role, match.get("role"))
                    self._increment_reason(by_status, match.get("support_status"))
                    self._increment_reason(by_table, match.get("table"))

        def count_entry_supplements(entry: Entry) -> None:
            if not entry.supplements:
                return
            sidecar_supplement_counters["entry_supplements_attached"] = int(sidecar_supplement_counters.get("entry_supplements_attached", 0)) + len(entry.supplements)
            for supplement in entry.supplements:
                role = str(supplement.get("role") or "")
                kind = str(supplement.get("kind") or "")
                if role == SidecarRole.EXAMPLES_IDIOMS.value:
                    sidecar_supplement_counters["examples_idioms_rows_attached"] = int(sidecar_supplement_counters.get("examples_idioms_rows_attached", 0)) + 1
                elif role == SidecarRole.LINK_REFERENCE.value:
                    sidecar_supplement_counters["link_reference_rows_matched"] = int(sidecar_supplement_counters.get("link_reference_rows_matched", 0)) + 1
                    if supplement.get("link_target"):
                        sidecar_supplement_counters["link_reference_targets_resolved"] = int(sidecar_supplement_counters.get("link_reference_targets_resolved", 0)) + 1
                elif role == SidecarRole.SEARCH.value or kind == "sidecar_search":
                    sidecar_supplement_counters["sidecar_search_rows_supported"] = int(sidecar_supplement_counters.get("sidecar_search_rows_supported", 0)) + 1

        for unsupported in sidecar_roles.get("unsupported_sidecars", []) if isinstance(sidecar_roles.get("unsupported_sidecars"), list) else []:
            if not isinstance(unsupported, dict):
                continue
            role = str(unsupported.get("role") or "unknown")
            significant = bool(unsupported.get("compatibility_significant"))
            severity = Severity.WARNING if significant else Severity.INFO
            diagnostics_by_severity[severity.value] = diagnostics_by_severity.get(severity.value, 0) + 1
            diagnostics_by_area[DiagnosticArea.VALIDATION.value] = diagnostics_by_area.get(DiagnosticArea.VALIDATION.value, 0) + 1
            diagnostics_by_code["unsupported_sidecar_schema"] = diagnostics_by_code.get("unsupported_sidecar_schema", 0) + 1
            if significant:
                diagnostics_by_code[f"unsupported_{role}_sidecar"] = diagnostics_by_code.get(f"unsupported_{role}_sidecar", 0) + 1

        def count_decode_telemetry(entry: Entry) -> None:
            key = (entry.address.block, entry.address.offset, entry.address.component)
            if key in decode_counter_seen:
                return
            decode_counter_seen.add(key)
            decode_counters["unknown_controls"] += entry.decode_unknown_controls
            decode_counters["unknown_bytes"] += entry.decode_unknown_bytes

        if body_source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            for entry in self.iter_entries(limit=sample_entries):
                entries_checked += 1
                try:
                    document = entry.document()
                    count_entry_supplements(entry)
                    count_sidecar_references(entry.address)
                    count_decode_telemetry(entry)
                    render_html(document)
                    render_text(document)
                    render_ok += 1
                    self._count_diagnostics(document.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                    self._count_document_resources(document.resources, resource_counters)
                    self._count_document_links(document.blocks, resource_counters)
                except Exception as exc:  # pragma: no cover - defensive validation report path
                    diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                    entry_errors.append(f"{entry.address.block}:{entry.address.offset}: {exc}")

        index_rows_sampled = 0
        search_hits_dereferenced = 0
        search_hits_rendered_html = 0
        search_hits_rendered_text = 0
        search_errors: list[str] = []
        sampled_rows: list[tuple[str, IndexRow]] = []
        for component_name, parsed in self.indexes().items():
            for row in parsed.rows:
                sampled_rows.append((component_name, row))
                if len(sampled_rows) >= sample_search_hits:
                    break
            if len(sampled_rows) >= sample_search_hits:
                break
        for component_name, row in sampled_rows:
            index_rows_sampled += 1
            query = self._row_display_key(row, backward=self._is_backward_index(component_name))
            if not normalize_query(query):
                diagnostics_by_severity["info"] = diagnostics_by_severity.get("info", 0) + 1
                diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                diagnostics_by_code["sample_search_skipped_empty_query"] = diagnostics_by_code.get("sample_search_skipped_empty_query", 0) + 1
                continue
            try:
                results = self.search(query, profile=SearchProfile.EXACT, limit=1)
                self._count_diagnostics(results.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                if not results.hits:
                    search_errors.append(f"no hit for sampled index key on page {row.page} row {row.row}")
                    diagnostics_by_severity["warning"] = diagnostics_by_severity.get("warning", 0) + 1
                    diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                    diagnostics_by_code["sample_search_miss"] = diagnostics_by_code.get("sample_search_miss", 0) + 1
                    continue
                hit = results.hits[0]
                title_attempts += 1
                title_status_counts[hit.title_status] = title_status_counts.get(hit.title_status, 0) + 1
                heading_source_counts[hit.heading_source] = heading_source_counts.get(hit.heading_source, 0) + 1
                if hit.title_reason and hit.title_status == "fallback":
                    self._increment_reason(title_failure_by_reason, hit.title_reason)
                self._count_diagnostics(hit.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                for diagnostic in hit.diagnostics:
                    if diagnostic.code.startswith("title_dereference"):
                        self._increment_reason(title_failure_by_reason, diagnostic.details.get("reason"))
                entry = self.entry_for_hit(hit)
                search_hits_dereferenced += 1
                count_sidecar_references(entry.address)
                count_entry_supplements(entry)
                document = entry.document()
                count_decode_telemetry(entry)
                self._count_diagnostics(document.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                self._count_document_resources(document.resources, resource_counters)
                self._count_document_links(document.blocks, resource_counters)
                render_html(document)
                search_hits_rendered_html += 1
                render_text(document)
                search_hits_rendered_text += 1
            except Exception as exc:  # pragma: no cover - defensive validation report path
                diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                search_errors.append(f"{row.page}:{row.row}: {exc}")

        index_stats = {
            name: {
                "rows": len(parsed.rows),
                "internal_rows": len(parsed.internal_rows),
                "leaf_pages": parsed.leaf_pages,
                "internal_pages": parsed.internal_pages,
                "unknown_leaf_bytes": parsed.unknown_leaf_bytes,
                "component_type": f"{self.component(name).type:02x}" if self.component(name) is not None else None,
                "unsupported_component_type": f"{parsed.unsupported_component_type:02x}" if parsed.unsupported_component_type is not None else None,
                "unsupported_leaf_pages": parsed.unsupported_leaf_pages,
                "malformed_leaf_rows": parsed.malformed_leaf_rows,
                "physical_tail_bytes": parsed.physical_tail_bytes,
                "physical_tail_nonzero_bytes": parsed.physical_tail_nonzero_bytes,
                "row_type_counts": dict(parsed.row_type_counts),
                "continuation_groups": parsed.continuation_groups,
                "dangling_continuation_rows": parsed.dangling_continuation_rows,
                "diagnostics": [diagnostic.to_dict() for diagnostic in parsed.diagnostics],
            }
            for name, parsed in self.indexes().items()
        }
        index_component_type_counts: dict[str, int] = {}
        index_rows_by_component_type: dict[str, int] = {}
        for name, parsed in self.indexes().items():
            component = self.component(name)
            component_type = f"{component.type:02x}" if component is not None else "unknown"
            index_component_type_counts[component_type] = index_component_type_counts.get(component_type, 0) + 1
            index_rows_by_component_type[component_type] = index_rows_by_component_type.get(component_type, 0) + len(parsed.rows)
            for diagnostic in parsed.diagnostics:
                diagnostics_by_severity["warning"] = diagnostics_by_severity.get("warning", 0) + 1
                diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                diagnostics_by_code[diagnostic.code] = diagnostics_by_code.get(diagnostic.code, 0) + 1
        sidecar_resolution = {
            "resolved": diagnostics_by_code.get("sidecar_body_resolved", 0),
            "missing_anchor_id": diagnostics_by_code.get("dense_anchor_missing_id", 0),
            "missing_row": diagnostics_by_code.get("sidecar_body_not_found", 0),
            "unsupported_body_source": diagnostics_by_code.get("unsupported_body_source", 0),
            "missing_body_component": diagnostics_by_code.get("missing_body_component", 0),
        }
        resource_resolution = {
            "unresolved_gaiji": diagnostics_by_code.get("unresolved_gaiji", 0),
            "unresolved_media": resource_counters["unresolved_media"],
            "unresolved_link": diagnostics_by_code.get("unresolved_link_target", 0),
            "resolved_gaiji": resource_counters["resolved_gaiji"],
            "gaiji_occurrences": resource_counters.get("gaiji_occurrences", 0),
            "gaiji_unicode_mapped": resource_counters.get("gaiji_unicode_mapped", 0),
            "gaiji_bitmap_backed": resource_counters.get("gaiji_bitmap_backed", 0),
            "gaiji_image_backed": resource_counters.get("gaiji_image_backed", 0),
            "gaiji_formatting_helper": resource_counters.get("gaiji_formatting_helper", 0),
            "gaiji_renderer_entry_backed": resource_counters.get("gaiji_renderer_entry_backed", 0),
            "gaiji_display_unresolved": resource_counters.get("gaiji_display_unresolved", 0),
            "gaiji_search_fallback_missing": resource_counters.get("gaiji_search_fallback_missing", 0),
            "gaiji_resource_bytes_available": resource_counters.get("gaiji_resource_bytes_available", 0),
            "gaiji_resource_bytes_unavailable_by_reason": resource_counters.get("gaiji_resource_bytes_unavailable_by_reason", {}),
            "gaiji_by_reason": resource_counters.get("gaiji_by_reason", {}),
            "gaiji_by_source": resource_counters.get("gaiji_by_source", {}),
            "gaiji_by_status": resource_counters.get("gaiji_by_status", {}),
            "resolved_media": resource_counters["resolved_media"],
            "resolved_link": resource_counters["resolved_link"],
            "unresolved_gaiji_by_reason": resource_counters["unresolved_gaiji_by_reason"],
            "unresolved_media_by_reason": resource_counters["unresolved_media_by_reason"],
            "unresolved_link_by_reason": resource_counters["unresolved_link_by_reason"],
            "media_kind_counts": resource_counters["media_kind_counts"],
            "media_mime_counts": resource_counters["media_mime_counts"],
            "media_store_kind_counts": resource_counters["media_store_kind_counts"],
            "colscr_records_resolved": resource_counters["colscr_records_resolved"],
            "colscr_records_malformed_by_reason": resource_counters["colscr_records_malformed_by_reason"],
            "pcmdata_ranges_resolved": resource_counters["pcmdata_ranges_resolved"],
            "pcmdata_ranges_unresolved_by_reason": resource_counters["pcmdata_ranges_unresolved_by_reason"],
            "sidecar_media_resolved": resource_counters["sidecar_media_resolved"],
            "sidecar_media_unresolved_by_reason": resource_counters["sidecar_media_unresolved_by_reason"],
            "media_bytes_available": resource_counters["media_bytes_available"],
            "media_bytes_unavailable_by_reason": resource_counters["media_bytes_unavailable_by_reason"],
        }
        return {
            "package": self.info.to_dict(),
            "body_source": body_source.to_dict(debug=True),
            "sidecar_resolution": sidecar_resolution,
            "sidecar_roles": sidecar_roles,
            "resource_resolution": resource_resolution,
            "sidecar_references": sidecar_reference_counters,
            "sidecar_supplements": sidecar_supplement_counters,
            "decode_telemetry": decode_counters,
            "title_dereference": {
                "attempts": title_attempts,
                "resolved": title_status_counts.get("resolved", 0),
                "fallback": title_status_counts.get("fallback", 0),
                "failed": diagnostics_by_code.get("title_dereference_failed", 0),
                "empty": diagnostics_by_code.get("title_dereference_empty", 0),
                "by_reason": title_failure_by_reason,
                "title_status_counts": title_status_counts,
                "heading_source_counts": heading_source_counts,
            },
            "component_count": len(self.components),
            "components": [
                {
                    "name": component.name,
                    "role": component.role.value,
                    "type": f"{component.type:02x}",
                    "present": component.path is not None,
                }
                for component in self.components
            ],
            "gaiji": {
                "uni_records": len(self.gaiji.records),
                "unicode_mappings": len(self.gaiji.mapping),
                "ga16_resources": len(self.ga16),
                "image_resources": len(self.gaiji_images),
                "plist_unicode_mappings": self.gaiji.plist_unicode_mappings,
                "plist_mapping_ambiguous": self.gaiji.plist_mapping_ambiguous,
                "plist_parse_failures": self.gaiji.plist_parse_failures,
            },
            "indexes": index_stats,
            "index_summary": {
                "component_type_counts": index_component_type_counts,
                "rows_by_component_type": index_rows_by_component_type,
                "unsupported_component_types": {
                    name: f"{parsed.unsupported_component_type:02x}"
                    for name, parsed in self.indexes().items()
                    if parsed.unsupported_component_type is not None
                },
                "malformed_leaf_rows": sum(parsed.malformed_leaf_rows for parsed in self.indexes().values()),
                "physical_tail_bytes": sum(parsed.physical_tail_bytes for parsed in self.indexes().values()),
                "physical_tail_nonzero_bytes": sum(parsed.physical_tail_nonzero_bytes for parsed in self.indexes().values()),
                "text_like_index_outliers": {
                    component.name: f"{component.type:02x}"
                    for component in self.components
                    if component.name.upper() == "INDEX.DIC" and component.type in TEXT_LIKE_INDEX_OUTLIER_TYPES
                },
                "continuation_groups": sum(parsed.continuation_groups for parsed in self.indexes().values()),
                "dangling_continuation_rows": sum(parsed.dangling_continuation_rows for parsed in self.indexes().values()),
            },
            "title_components": len(self.components_by_role(ComponentRole.TITLE)),
            "sample_entries_checked": entries_checked,
            "sample_entries_rendered": render_ok,
            "sample_index_rows_checked": index_rows_sampled,
            "sample_search_hits_dereferenced": search_hits_dereferenced,
            "sample_search_hits_rendered_html": search_hits_rendered_html,
            "sample_search_hits_rendered_text": search_hits_rendered_text,
            "diagnostics": {
                "by_severity": diagnostics_by_severity,
                "by_area": diagnostics_by_area,
                "by_code": diagnostics_by_code,
                "entry_errors": entry_errors,
                "search_errors": search_errors,
            },
            "ok": diagnostics_by_severity.get("error", 0) == 0 and not entry_errors,
        }

    def summary(self) -> dict[str, object]:
        return {
            "package": self.info.to_dict(),
            "body_source": self.body_source().to_dict(debug=False),
            "components": [component.to_dict() for component in self.components],
            "gaiji": {
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
            },
        }
