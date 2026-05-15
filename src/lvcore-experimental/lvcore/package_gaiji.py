"""Gaiji mapping, bitmap/image fallback, and gaiji resource helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .document import ResourceKind, ResourceRef, ResourceStatus
from .gaiji import (
    Ga16Resource,
    GaijiDisplayStatus,
    GaijiMap,
    GaijiResolutionReason,
    GaijiSources,
    ImageGaijiResource,
    load_gaiji_map,
    load_image_gaiji_resources,
    parse_ga16,
    resolve_gaiji_sources,
)
from .gaiji_resolution import (
    BitmapGaijiBacking,
    FormattingHelperGaijiBacking,
    GaijiResolution,
    ImageGaijiBacking,
    RendererEntryGaijiBacking,
    UnicodeGaijiBacking,
    UnresolvedGaijiBacking,
)
from .json_types import JsonObject
from .model import Component, ComponentRole, Span
from .package_utils import _media_mime_and_format
from .text import DecodeResult, decode_text_stream


class PackageGaijiMixin:
    """Gaiji mapping and resource methods for LogoVistaPackage."""

    @staticmethod
    def _load_ga16_resources(sources: GaijiSources) -> tuple[Ga16Resource, ...]:
        resources: list[Ga16Resource] = []
        seen: set[Path] = set()
        for path in sources.ga16_files:
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

    @property
    def gaiji_sources(self) -> GaijiSources:
        if self._gaiji_sources is None:
            component_paths = tuple(
                component.path
                for component in self.components
                if component.role == ComponentRole.GAIJI and component.path is not None
            )
            self._gaiji_sources = resolve_gaiji_sources(
                self.info.root,
                self.info.dict_id or self.catalog.dict_id,
                component_paths=component_paths,
            )
        return self._gaiji_sources

    @property
    def gaiji(self) -> GaijiMap:
        if self._gaiji is None:
            self._gaiji = load_gaiji_map(
                self.info.root,
                self.info.dict_id or self.catalog.dict_id,
                self.gaiji_sources,
            )
        return self._gaiji

    @property
    def ga16(self) -> tuple[Ga16Resource, ...]:
        if self._ga16 is None:
            self._ga16 = self._load_ga16_resources(self.gaiji_sources)
        return self._ga16

    @property
    def gaiji_images(self) -> tuple[ImageGaijiResource, ...]:
        if self._gaiji_images is None:
            self._gaiji_images = load_image_gaiji_resources(self.info.root, self.gaiji_sources)
        return self._gaiji_images

    @property
    def _gaiji_image_by_code(self) -> dict[str, ImageGaijiResource]:
        if self._gaiji_image_by_code_cache is None:
            self._gaiji_image_by_code_cache = {resource.code: resource for resource in self.gaiji_images}
        return self._gaiji_image_by_code_cache

    def decode_component(self, component: str | Component) -> DecodeResult:
        self._ensure_open()
        decoded = decode_text_stream(self.expanded(component), self.gaiji.mapping)
        spans = self._annotate_gaiji_spans(decoded.spans)
        return DecodeResult(
            spans=spans,
            text=decoded.text,
            unknown_controls=decoded.unknown_controls,
            unknown_bytes=decoded.unknown_bytes,
            spans_debug=tuple(span.debug for span in spans),
        )

    def _decode_text_stream(self, data: bytes, *, renderer_entry_backed: bool = False) -> DecodeResult:
        decoded = decode_text_stream(data, self.gaiji.mapping)
        spans = self._annotate_gaiji_spans(decoded.spans, renderer_entry_backed=renderer_entry_backed)
        return DecodeResult(
            spans=spans,
            text=decoded.text,
            unknown_controls=decoded.unknown_controls,
            unknown_bytes=decoded.unknown_bytes,
            spans_debug=tuple(span.debug for span in spans),
        )

    @staticmethod
    def _gaiji_resource_id(code: str, *, source: str = "gaiji") -> str:
        digest = hashlib.sha1(f"{source}:{code.lower()}".encode("utf-8")).hexdigest()[:12]
        return f"gaiji-{digest}"

    @staticmethod
    def _is_blank_glyph(glyph: bytes | None) -> bool:
        return bool(glyph is not None) and all(byte == 0 for byte in glyph)

    def _gaiji_image_info(self, code: str) -> JsonObject | None:
        if code in self._gaiji_image_info_cache:
            cached = self._gaiji_image_info_cache[code]
            return dict(cached) if cached is not None else None
        image = self._gaiji_image_by_code.get(code.lower())
        if image is None:
            self._gaiji_image_info_cache[code] = None
            return None
        try:
            byte_length = image.path.stat().st_size
            with image.path.open("rb") as fh:
                prefix = fh.read(256)
        except OSError:
            info = {
                "display_status": GaijiDisplayStatus.UNRESOLVED.value,
                "reason": GaijiResolutionReason.MISSING_IMAGE_RESOURCE.value,
                "source": image.source,
                "image_path": str(image.path),
            }
            self._gaiji_image_info_cache[code] = info
            return dict(info)
        mime_type, format_hint, container_kind = _media_mime_and_format(prefix, store_kind="gaiji_image")
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
        info = {
            "display_status": GaijiDisplayStatus.IMAGE_BACKED.value,
            "reason": GaijiResolutionReason.IMAGE_ASSET.value,
            "source": "image",
            "resource_id": self._gaiji_resource_id(code, source="image"),
            "resource_kind": ResourceKind.GAIJI.value,
            "mime_type": mime_type,
            "format_hint": format_hint,
            "container_kind": container_kind,
            "byte_length": byte_length,
            "image_path": str(image.path),
            "image_source": image.source,
            "image_key": image.key,
        }
        self._gaiji_image_info_cache[code] = info
        return dict(info)

    def _gaiji_glyph_info(self, code: str, *, prefer_record_order: bool = True) -> JsonObject | None:
        cache_key = (code, prefer_record_order)
        if cache_key in self._gaiji_glyph_info_cache:
            cached = self._gaiji_glyph_info_cache[cache_key]
            return dict(cached) if cached is not None else None
        try:
            code_int = int(code, 16)
        except ValueError:
            info = {
                "display_status": GaijiDisplayStatus.UNRESOLVED.value,
                "reason": GaijiResolutionReason.MALFORMED_GAIJI_CODE.value,
            }
            self._gaiji_glyph_info_cache[cache_key] = info
            return dict(info)
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
                info = {
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
                self._gaiji_glyph_info_cache[cache_key] = info
                return dict(info)
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
            info = {
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
            self._gaiji_glyph_info_cache[cache_key] = info
            return dict(info)
        self._gaiji_glyph_info_cache[cache_key] = None
        return None

    @staticmethod
    def _gaiji_status(value: object) -> GaijiDisplayStatus:
        try:
            return GaijiDisplayStatus(str(value))
        except ValueError:
            return GaijiDisplayStatus.UNRESOLVED

    @staticmethod
    def _gaiji_reason(value: object) -> GaijiResolutionReason:
        try:
            return GaijiResolutionReason(str(value))
        except ValueError:
            return GaijiResolutionReason.UNKNOWN

    def _gaiji_resolution_from_info(self, code: str, resource_id: str, info: JsonObject) -> GaijiResolution:
        status = self._gaiji_status(info.get("display_status"))
        details = info.get("details") if isinstance(info.get("details"), dict) else {}
        backing_info = details.get("backing_resource") if isinstance(details.get("backing_resource"), dict) else {}
        reason = self._gaiji_reason(info.get("reason") or details.get("reason"))
        display_text = str(info.get("display_text")) if info.get("display_text") is not None else None
        fallback_text = str(info.get("fallback_text")) if info.get("fallback_text") is not None else None
        resolved_resource_id = str(info.get("resource_id") or resource_id)
        mime_type = str(info.get("mime_type")) if info.get("mime_type") else None
        byte_length = info.get("byte_length") if isinstance(info.get("byte_length"), int) else None
        display_source = str(info.get("source") or details.get("source") or "") or "unknown"
        source = str(backing_info.get("source") or display_source) or "unknown"
        source_path = backing_info.get("source_path") or info.get("source_path")
        image_path = backing_info.get("image_path") or info.get("image_path")
        if source_path and isinstance(backing_info.get("glyph_index", info.get("glyph_index")), int):
            glyph_index = backing_info.get("glyph_index", info.get("glyph_index"))
            backing = BitmapGaijiBacking(
                source=source,
                source_path=str(source_path),
                glyph_index=int(glyph_index or 0),
                width=int(backing_info.get("glyph_width", info.get("glyph_width")) or 0),
                height=int(backing_info.get("glyph_height", info.get("glyph_height")) or 0),
                glyph_bytes=int(backing_info.get("glyph_bytes", info.get("glyph_bytes")) or 0),
                section=str(backing_info.get("ga16_section", info.get("ga16_section")) or "unknown"),
            )
        elif image_path:
            backing = ImageGaijiBacking(
                source=source,
                image_path=str(image_path),
                image_key=str(backing_info.get("image_key", info.get("image_key"))) if backing_info.get("image_key", info.get("image_key")) is not None else None,
                byte_length=byte_length or 0,
                format_hint=str(backing_info.get("format_hint", info.get("format_hint"))) if backing_info.get("format_hint", info.get("format_hint")) else None,
                container_kind=str(backing_info.get("container_kind", info.get("container_kind"))) if backing_info.get("container_kind", info.get("container_kind")) else None,
            )
        elif status == GaijiDisplayStatus.UNICODE_MAPPED:
            backing = UnicodeGaijiBacking(text=display_text or "", source=source)
        elif status == GaijiDisplayStatus.BITMAP_BACKED:
            backing = BitmapGaijiBacking(
                source=source,
                source_path=str(info.get("source_path") or ""),
                glyph_index=int(info.get("glyph_index") or 0),
                width=int(info.get("glyph_width") or 0),
                height=int(info.get("glyph_height") or 0),
                glyph_bytes=int(info.get("glyph_bytes") or 0),
                section=str(info.get("ga16_section") or "unknown"),
            )
        elif status == GaijiDisplayStatus.IMAGE_BACKED:
            backing = ImageGaijiBacking(
                source=source,
                image_path=str(info.get("image_path") or ""),
                image_key=str(info.get("image_key")) if info.get("image_key") is not None else None,
                byte_length=byte_length or 0,
                format_hint=str(info.get("format_hint")) if info.get("format_hint") else None,
                container_kind=str(info.get("container_kind")) if info.get("container_kind") else None,
            )
        elif status == GaijiDisplayStatus.FORMATTING_HELPER:
            backing = FormattingHelperGaijiBacking(source=source)
        elif status == GaijiDisplayStatus.RENDERER_ENTRY_BACKED:
            backing = RendererEntryGaijiBacking()
        else:
            backing = UnresolvedGaijiBacking(reason=reason)
        return GaijiResolution(
            code=code,
            display_status=status,
            display_text=display_text,
            fallback_text=fallback_text,
            resource_id=resolved_resource_id,
            mime_type=mime_type,
            reason=reason,
            backing=backing,
            source=display_source,
            byte_length=byte_length,
        )

    def _gaiji_info_dict(self, code_or_resource: str | ResourceRef | JsonObject) -> JsonObject:
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
        info: JsonObject = self._media_info_base(resource_id, ResourceKind.GAIJI.value)
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

    def gaiji_info(self, code_or_resource: str | ResourceRef | JsonObject) -> GaijiResolution:
        if isinstance(code_or_resource, ResourceRef):
            code = code_or_resource.code or ""
            resource_id = code_or_resource.id
        elif isinstance(code_or_resource, dict):
            code_value = code_or_resource.get("code")
            code = str(code_value or "")
            resource_id = str(code_or_resource.get("id") or self._gaiji_resource_id(code))
        else:
            code = str(code_or_resource or "")
            resource_id = self._gaiji_resource_id(code)
        code = code.lower()
        return self._gaiji_resolution_from_info(code, resource_id, self._gaiji_info_dict(code_or_resource))

    def _annotate_gaiji_spans(self, spans: tuple[Span, ...], *, renderer_entry_backed: bool = False) -> tuple[Span, ...]:
        out: list[Span] = []
        for span in spans:
            if span.kind != "gaiji" or not span.debug.code:
                out.append(span)
                continue
            info = self.gaiji_info(span.debug.code)
            attrs = dict(span.debug.attrs)
            attrs.update(
                {
                    "gaiji_display_status": info.display_status.value,
                    "gaiji_reason": info.reason.value,
                    "display_text": info.display_text,
                    "fallback_text": info.fallback_text,
                    "resource_id": info.resource_id,
                    "resource_kind": ResourceKind.GAIJI.value,
                    "mime_type": info.mime_type,
                    "byte_length": info.byte_length,
                    "gaiji_source": info.source,
                }
            )
            if renderer_entry_backed and attrs.get("gaiji_display_status") == GaijiDisplayStatus.UNRESOLVED.value:
                attrs["gaiji_display_status"] = GaijiDisplayStatus.RENDERER_ENTRY_BACKED.value
                attrs["gaiji_reason"] = GaijiResolutionReason.RENDERER_CONTEXTUAL_REQUIRED.value
            out.append(span.with_debug_attrs(attrs))
        return tuple(out)

    def gaiji_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        """List package-level gaiji resources and display mappings."""

        codes = sorted({record.code for record in self.gaiji.records} | set(self._gaiji_image_by_code))
        resources: list[ResourceRef] = []
        for code in codes:
            info = self.gaiji_info(code)
            debug = info.to_dict(debug=True)
            backing = debug.get("backing") if isinstance(debug.get("backing"), dict) else {}
            resources.append(
                ResourceRef(
                    id=info.resource_id,
                    kind=ResourceKind.GAIJI,
                    label=str(info.display_text or code),
                    status=ResourceStatus.RESOLVED if info.resolved else ResourceStatus.UNRESOLVED,
                    mime_type=info.mime_type,
                    code=code,
                    source_path=str(backing.get("source_path") or backing.get("image_path") or "") or None,
                    details={
                        "resolved": info.resolved,
                        "reason": info.reason.value,
                        "display_status": info.display_status.value,
                        "display_text": info.display_text,
                        "source": info.source,
                        "byte_length": info.byte_length,
                    },
                )
            )
            if limit is not None and len(resources) >= limit:
                break
        return tuple(resources)
