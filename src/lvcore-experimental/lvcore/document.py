"""Structured entry documents built from decoded LogoVista spans."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Any, Iterable

from .diagnostics import Diagnostic, DiagnosticArea, DiagnosticBag, Location, Severity
from .json_types import JsonObject
from .model import Address, Entry, Span
from .opcodes import KNOWN_NEUTRAL_OPS, OpcodeCategory, behavior_for


DOCUMENT_SCHEMA = "lvcore.entry_document.v1"
DOCUMENT_MODEL_VERSION = 1

DEBUG_ATTR_KEYS = {
    "address",
    "anchor_raw",
    "block_column",
    "end_address",
    "end_payload",
    "details",
    "offset_column",
    "payload",
    "payload_hex",
    "raw",
    "raw_payload",
    "raw_spans",
    "raw_text",
    "row_id",
    "sidecar",
    "source_address",
    "source_sidecar",
    "source_table",
    "end_op",
    "span_offset",
    "start_op",
    "start_payload",
    "storage",
    "table",
}


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    EXAMPLE = "example"
    LIST = "list"
    TABLE = "table"
    IMAGE = "image"
    AUDIO = "audio"
    MEDIA = "media"
    UNKNOWN = "unknown"


class InlineKind(str, Enum):
    TEXT = "text"
    GAIJI = "gaiji"
    LINE_BREAK = "line_break"
    EMPHASIS = "emphasis"
    BOLD = "bold"
    ITALIC = "italic"
    SUBSCRIPT = "subscript"
    SUPERSCRIPT = "superscript"
    RUBY = "ruby_or_reading_placeholder"
    LINK = "link"
    MEDIA_REF = "media_ref"
    UNKNOWN_CONTROL = "unknown_control"


class ResourceKind(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    MEDIA = "media"
    GAIJI = "gaiji"
    UNKNOWN = "unknown"


class ResourceStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    DEFERRED = "deferred"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"


class LinkTargetKind(str, Enum):
    EXTERNAL_URL = "external_url"
    INTERNAL_ADDRESS = "internal_address"
    MENU_NAVIGATION = "menu_navigation"
    TOC_INTERNAL = "toc_internal"
    BODY_REFERENCE = "body_reference"
    EXTENDED_REFERENCE = "extended_reference"
    JUMP_OR_AUDIO_RANGE = "jump_or_audio_range"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"


class LinkTargetStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    DEFERRED = "deferred"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    CONTENT = "content"
    PENDING = "pending"


def _stable_private_ref(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"lvcore-entry://ref-{digest}"


def _public_mapping(value: Any, *, debug: bool) -> Any:
    if isinstance(value, dict):
        out: JsonObject = {}
        for key, item in value.items():
            if not debug and key in DEBUG_ATTR_KEYS:
                continue
            if not debug and key == "href" and isinstance(item, str) and item.startswith("lvcore-entry://"):
                out[key] = _stable_private_ref(item)
                continue
            out[key] = _public_mapping(item, debug=debug)
        return out
    if isinstance(value, (list, tuple)):
        return [_public_mapping(item, debug=debug) for item in value]
    return value


def _diagnostic_to_dict(diagnostic: Diagnostic, *, debug: bool) -> JsonObject:
    data = diagnostic.to_dict()
    if not debug:
        data["details"] = _public_mapping(data.get("details", {}), debug=False)
    return data


STYLE_START_TO_KIND = {
    "bold": InlineKind.BOLD,
    "italic": InlineKind.ITALIC,
    "em": InlineKind.EMPHASIS,
    "sub": InlineKind.SUBSCRIPT,
    "sup": InlineKind.SUPERSCRIPT,
    "url": InlineKind.LINK,
    "link": InlineKind.LINK,
}


STYLE_START_OPS = {0x06, 0x0E, 0x10, 0x12, 0x3B, 0x42, 0x43, 0x44, 0x49, 0x4A, 0xE0}
STYLE_END_OPS = {0x07, 0x0F, 0x11, 0x13, 0x5B, 0x62, 0x63, 0x64, 0x69, 0x6A, 0xE1}
LINK_START_OPS = {0x3B, 0x42, 0x43, 0x44, 0x49, 0x4A}
LINK_END_OPS = {0x5B, 0x62, 0x63, 0x64, 0x69, 0x6A}
LINK_END_TARGET_OPS = {0x62, 0x63, 0x64}
LINK_START_KINDS = {
    0x3B: LinkTargetKind.EXTERNAL_URL,
    0x42: LinkTargetKind.BODY_REFERENCE,
    0x43: LinkTargetKind.MENU_NAVIGATION,
    0x44: LinkTargetKind.EXTENDED_REFERENCE,
    0x49: LinkTargetKind.TOC_INTERNAL,
    0x4A: LinkTargetKind.JUMP_OR_AUDIO_RANGE,
}


IGNORED_CONTROL_TAGS = {
    "halfwidth",
    "head",
    "literal",
    "private",
    "tab_column",
    "media_layout",
    "title_separator",
}


@dataclass(frozen=True)
class ResourceRef:
    id: str
    kind: ResourceKind
    label: str
    status: ResourceStatus | str = ResourceStatus.UNRESOLVED
    mime_type: str | None = None
    component: str | None = None
    code: str | None = None
    source_offset: int | None = None
    source_path: str | None = None
    details: JsonObject = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        status = self.status.value if isinstance(self.status, ResourceStatus) else str(self.status)
        data: JsonObject = {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "status": status,
            "mime_type": self.mime_type,
        }
        if debug:
            data["code"] = self.code
            data["source_path"] = self.source_path
            data["component"] = self.component
            data["source_offset"] = self.source_offset
            data["details"] = _public_mapping(self.details, debug=True)
        else:
            public_details = {key: value for key, value in self.details.items() if key in {"resolved", "reason"}}
            if public_details:
                data["details"] = public_details
        return data


@dataclass(frozen=True)
class LinkTarget:
    kind: LinkTargetKind | str
    href: str | None = None
    address: Address | None = None
    end_address: Address | None = None
    resource_id: str | None = None
    label: str | None = None
    raw_payload: str | None = None
    start_op: str | None = None
    end_op: str | None = None
    start_payload: str | None = None
    end_payload: str | None = None
    status: LinkTargetStatus | str = LinkTargetStatus.UNRESOLVED
    details: JsonObject = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        kind = self.kind.value if isinstance(self.kind, LinkTargetKind) else str(self.kind)
        status = self.status.value if isinstance(self.status, LinkTargetStatus) else str(self.status)
        data = {
            "kind": kind,
            "href": self.href,
            "status": status,
            "label": self.label,
            "resource_id": self.resource_id,
        }
        if not debug and self.href and self.href.startswith("lvcore-entry://"):
            data["href"] = _stable_private_ref(self.href)
        if debug:
            data["address"] = self.address.to_dict() if self.address else None
            data["end_address"] = self.end_address.to_dict() if self.end_address else None
            data["raw_payload"] = self.raw_payload
            data["start_op"] = self.start_op
            data["end_op"] = self.end_op
            data["start_payload"] = self.start_payload
            data["end_payload"] = self.end_payload
            data["details"] = _public_mapping(self.details, debug=True)
        return data


@dataclass(frozen=True)
class InlineNode:
    kind: InlineKind
    text: str | None = None
    children: tuple["InlineNode", ...] = ()
    code: str | None = None
    resource_id: str | None = None
    attrs: JsonObject = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "kind": self.kind.value,
            "text": self.text,
            "children": [child.to_dict(debug=debug) for child in self.children],
            "resource_id": self.resource_id,
        }
        if debug:
            data["code"] = self.code
        attrs = _public_mapping(self.attrs, debug=debug)
        if attrs:
            data["attrs"] = attrs
        return data


@dataclass(frozen=True)
class BlockNode:
    kind: BlockKind
    inlines: tuple[InlineNode, ...] = ()
    attrs: JsonObject = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        return {
            "kind": self.kind.value,
            "inlines": [node.to_dict(debug=debug) for node in self.inlines],
            "attrs": _public_mapping(self.attrs, debug=debug),
        }


@dataclass(frozen=True)
class EntryDocument:
    blocks: tuple[BlockNode, ...]
    resources: tuple[ResourceRef, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    metadata: JsonObject = field(default_factory=dict)
    debug_metadata: JsonObject = field(default_factory=dict)

    def resource_map(self) -> dict[str, ResourceRef]:
        return {resource.id: resource for resource in self.resources}

    def diagnostics_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for diagnostic in self.diagnostics:
            counts[diagnostic.code.value] = counts.get(diagnostic.code.value, 0) + 1
        return counts

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": DOCUMENT_SCHEMA,
            "model_version": DOCUMENT_MODEL_VERSION,
            "blocks": [block.to_dict(debug=debug) for block in self.blocks],
            "resources": [resource.to_dict(debug=debug) for resource in self.resources],
            "diagnostics": [_diagnostic_to_dict(diagnostic, debug=debug) for diagnostic in self.diagnostics],
            "metadata": _public_mapping(self.metadata, debug=debug),
        }
        if debug:
            data["debug_metadata"] = _public_mapping(self.debug_metadata, debug=True)
        return data

    def to_debug_dict(self) -> JsonObject:
        return self.to_dict(debug=True)


def _gaiji_unresolved(span: Span) -> bool:
    status = span.debug.attrs.get("gaiji_display_status") if isinstance(span.debug.attrs, dict) else None
    if status:
        return str(status) == "unresolved"
    if not span.debug.code:
        return False
    if span.text is None:
        return True
    upper = span.debug.code.upper()
    return span.text in {f"<h{upper}>", f"<z{upper}>", f"<g{upper}>"}


@dataclass
class ActiveInline:
    kind: InlineKind
    attrs: JsonObject = field(default_factory=dict)


def _decode_bcd_decimal(data: bytes) -> int | None:
    value = 0
    for byte in data:
        for nibble in (byte >> 4, byte & 0x0F):
            if nibble > 9:
                return None
            value = value * 10 + nibble
    return value


def _address_from_bcd_payload(payload: bytes) -> Address | None:
    if len(payload) < 6:
        return None
    block = _decode_bcd_decimal(payload[:4])
    offset = _decode_bcd_decimal(payload[4:6])
    if block is None or offset is None:
        return None
    return Address(block, offset)


def _range_from_pcm_payload(payload: bytes) -> tuple[Address, Address] | None:
    if len(payload) < 16:
        return None
    start = _address_from_bcd_payload(payload[4:10])
    end = _address_from_bcd_payload(payload[10:16])
    if start is None or end is None:
        return None
    return start, end


def _media_descriptor_from_payload(payload: bytes) -> JsonObject:
    descriptor: JsonObject = {
        "payload_length": len(payload),
        "resolved": False,
        "reason": "unresolved_media_payload",
    }
    if payload:
        descriptor["payload_hex"] = payload.hex()
    if len(payload) != 18:
        descriptor["status"] = ResourceStatus.MALFORMED.value
        descriptor["reason"] = "malformed_media_descriptor"
        return descriptor
    address = _address_from_bcd_payload(payload[12:18])
    descriptor.update(
        {
            "status": ResourceStatus.DEFERRED.value,
            "descriptor_prefix": payload[:12].hex(),
            "pointer_payload": payload[12:18].hex(),
            "reason": "media_payload_address_only",
        }
    )
    if address is not None:
        descriptor["target_address"] = address.to_dict()
        descriptor["target_block"] = address.block
        descriptor["target_offset"] = address.offset
    else:
        descriptor["status"] = ResourceStatus.MALFORMED.value
        descriptor["reason"] = "malformed_media_pointer"
    return descriptor


def _pcm_descriptor_from_payload(payload: bytes) -> JsonObject:
    descriptor: JsonObject = {
        "payload_length": len(payload),
        "resolved": False,
        "reason": "unresolved_audio_range",
    }
    if payload:
        descriptor["payload_hex"] = payload.hex()
    address_range = _range_from_pcm_payload(payload)
    if address_range is None:
        descriptor["status"] = ResourceStatus.MALFORMED.value if payload else ResourceStatus.UNRESOLVED.value
        descriptor["reason"] = "malformed_audio_range" if payload else "missing_audio_range_payload"
        return descriptor
    start, end = address_range
    descriptor.update(
        {
            "status": ResourceStatus.DEFERRED.value,
            "reason": "pcmdata_range_address_only",
            "range_start": start.to_dict(),
            "range_end": end.to_dict(),
            "kind_flags": payload[:4].hex(),
        }
    )
    return descriptor


def _link_attrs_for_start(span: Span, *, resource_id: str | None = None) -> JsonObject:
    op_hex = f"{span.debug.op:02x}" if span.debug.op is not None else None
    kind = LINK_START_KINDS.get(span.debug.op, LinkTargetKind.UNKNOWN)
    if span.debug.op == 0x3B:
        target = LinkTarget(kind=kind, status=LinkTargetStatus.CONTENT, start_op=op_hex, start_payload=span.debug.payload.hex())
    elif span.debug.op == 0x4A:
        address_range = _range_from_pcm_payload(span.debug.payload)
        if address_range is not None:
            start, end = address_range
            target = LinkTarget(
                kind=kind,
                address=start,
                end_address=end,
                raw_payload=span.debug.payload.hex() or None,
                start_op=op_hex,
                start_payload=span.debug.payload.hex(),
                status=LinkTargetStatus.DEFERRED,
                details={
                    "range_start": start.to_dict(),
                    "range_end": end.to_dict(),
                    "kind_flags": span.debug.payload[:4].hex(),
                    "resource_id": resource_id,
                },
                resource_id=resource_id,
            )
        else:
            target = LinkTarget(
                kind=kind,
                raw_payload=span.debug.payload.hex() or None,
                start_op=op_hex,
                start_payload=span.debug.payload.hex(),
                status=LinkTargetStatus.MALFORMED if span.debug.payload else LinkTargetStatus.UNRESOLVED,
                resource_id=resource_id,
            )
    else:
        target = LinkTarget(
            kind=kind,
            raw_payload=span.debug.payload.hex() or None,
            start_op=op_hex,
            start_payload=span.debug.payload.hex(),
            status=LinkTargetStatus.PENDING,
        )
    return {
        "link_target": target.to_dict(debug=True),
        "start_op": op_hex,
        "start_payload": span.debug.payload.hex(),
        "resource_id": resource_id,
    }


def _wrap_styles(node: InlineNode, active_styles: Iterable[ActiveInline]) -> InlineNode:
    wrapped = node
    for style in reversed(tuple(active_styles)):
        wrapped = InlineNode(style.kind, children=(wrapped,), attrs=style.attrs)
    return wrapped


def _merge_inline(left: InlineNode, right: InlineNode) -> InlineNode | None:
    if left.kind != right.kind or left.code != right.code or left.resource_id != right.resource_id or left.attrs != right.attrs:
        return None
    if not left.children and not right.children and left.text is not None and right.text is not None:
        return InlineNode(left.kind, text=left.text + right.text, code=left.code, resource_id=left.resource_id, attrs=left.attrs)
    if len(left.children) == 1 and len(right.children) == 1:
        child = _merge_inline(left.children[0], right.children[0])
        if child is not None:
            return InlineNode(left.kind, children=(child,), code=left.code, resource_id=left.resource_id, attrs=left.attrs)
    return None


def _append_inline(target: list[InlineNode], node: InlineNode) -> None:
    if target:
        merged = _merge_inline(target[-1], node)
        if merged is not None:
            target[-1] = merged
            return
    target.append(node)


def _add_text(target: list[InlineNode], text: str | None, active_styles: list[ActiveInline]) -> None:
    if not text:
        return
    _append_inline(target, _wrap_styles(InlineNode(InlineKind.TEXT, text=text), active_styles))


def _flush_paragraph(blocks: list[BlockNode], inlines: list[InlineNode]) -> None:
    if not inlines:
        return
    blocks.append(BlockNode(BlockKind.PARAGRAPH, tuple(inlines)))
    inlines.clear()


def build_entry_document(entry: Entry) -> EntryDocument:
    """Build a reader-facing document from an entry's decoded spans."""

    diagnostics = DiagnosticBag()
    diagnostics.diagnostics.extend(entry.entry_diagnostics)
    renderer_entry_backed = any(
        getattr(diagnostic, "code", None) == "sidecar_body_resolved"
        for diagnostic in entry.entry_diagnostics
    )
    blocks: list[BlockNode] = []
    inlines: list[InlineNode] = []
    resources: list[ResourceRef] = []
    active_styles: list[ActiveInline] = []
    resource_counter = 0

    def location(span: Span) -> Location:
        block = entry.address.block + ((entry.address.offset + span.offset) // 2048)
        offset = (entry.address.offset + span.offset) % 2048
        return Location(component=entry.address.component, block=block, offset=offset, span_offset=span.offset)

    for span in entry.spans:
        if span.hidden:
            if span.kind == "control" and span.debug.op in {0xE2, 0xE3}:
                diagnostics.add(
                    Severity.INFO,
                    DiagnosticArea.OPCODE,
                    "private_renderer_directive",
                    "private renderer directive hidden from friendly document",
                    location=location(span),
                    details={"op": f"{span.debug.op:02x}" if span.debug.op is not None else None},
                )
            continue

        if span.kind == "text":
            _add_text(inlines, span.text, active_styles)
            continue

        if span.kind == "break":
            _flush_paragraph(blocks, inlines)
            continue

        if span.kind == "gaiji":
            attrs = dict(span.debug.attrs)
            status = str(attrs.get("gaiji_display_status") or "")
            if renderer_entry_backed and (not status or status == "unresolved") and _gaiji_unresolved(span):
                status = "renderer_entry_backed"
                attrs["gaiji_display_status"] = status
                attrs["gaiji_reason"] = "renderer_contextual_required"
            unresolved = _gaiji_unresolved(span.with_debug_attrs(attrs))
            reason = str(attrs.get("gaiji_reason") or ("missing_unicode_mapping" if unresolved else "unicode_mapping"))
            display_text = attrs.get("display_text")
            if not isinstance(display_text, str) or not display_text:
                display_text = span.text if not _gaiji_unresolved(span) else None
            if status == "formatting_helper":
                display_text = ""
            resource_counter += 1
            resource_id = str(attrs.get("resource_id") or f"gaiji-{resource_counter}")
            resources.append(
                ResourceRef(
                    id=resource_id,
                    kind=ResourceKind.GAIJI,
                    label=display_text or span.text or "gaiji",
                    status=ResourceStatus.UNRESOLVED if unresolved else ResourceStatus.RESOLVED,
                    mime_type=str(attrs.get("mime_type")) if attrs.get("mime_type") else None,
                    component=entry.address.component,
                    code=span.debug.code,
                    source_offset=span.offset,
                    details={
                        "resolved": not unresolved,
                        "reason": reason,
                        "display_status": status or ("unresolved" if unresolved else "unicode_mapped"),
                        "display_text": display_text,
                        "fallback_text": attrs.get("fallback_text"),
                        "source": attrs.get("gaiji_source"),
                        "byte_length": attrs.get("byte_length"),
                    },
                )
            )
            if unresolved:
                diagnostics.add(
                    Severity.WARNING,
                    DiagnosticArea.GAIJI,
                    "unresolved_gaiji",
                    "gaiji has no Unicode mapping in the current package context",
                    location=location(span),
                    details={"code": span.debug.code, "reason": reason},
                )
            node = InlineNode(
                InlineKind.GAIJI,
                text=None if unresolved else display_text,
                code=span.debug.code,
                resource_id=resource_id,
                attrs={
                    "resolved": not unresolved,
                    "raw_text": span.text,
                    "gaiji_display_status": status or ("unresolved" if unresolved else "unicode_mapped"),
                    "gaiji_reason": reason,
                },
            )
            _append_inline(inlines, _wrap_styles(node, active_styles))
            continue

        if span.kind == "media_ref":
            resource_counter += 1
            resource_kind_name = str(span.debug.attrs.get("resource_kind") or "media")
            try:
                resource_kind = ResourceKind(resource_kind_name)
            except ValueError:
                resource_kind = ResourceKind.MEDIA
            descriptor = _media_descriptor_from_payload(span.debug.payload)
            status = ResourceStatus(descriptor.get("status", ResourceStatus.UNRESOLVED.value))
            resource = ResourceRef(
                id=f"media-{resource_counter}",
                kind=resource_kind,
                label=resource_kind.value,
                status=status,
                component=entry.address.component,
                source_offset=span.offset,
                details=descriptor,
            )
            resources.append(resource)
            _append_inline(
                inlines,
                _wrap_styles(
                    InlineNode(
                        InlineKind.MEDIA_REF,
                        resource_id=resource.id,
                        attrs={
                            "label": resource.label,
                            "resource_kind": resource.kind.value,
                            "payload_hex": span.debug.payload.hex(),
                            "resource_status": status.value,
                            "media_descriptor": descriptor,
                        },
                    ),
                    active_styles,
                ),
            )
            if status != ResourceStatus.DEFERRED:
                diagnostics.add(
                    Severity.WARNING,
                    DiagnosticArea.MEDIA,
                    "unresolved_media_ref",
                    "media reference is preserved but not resolved to a payload",
                    location=location(span),
                    details={"resource_id": resource.id, "reason": descriptor.get("reason"), "target_address": descriptor.get("target_address")},
                )
            continue

        if span.kind == "unknown_byte":
            diagnostics.add(
                Severity.WARNING,
                DiagnosticArea.BODY,
                "unknown_byte",
                "unknown body byte skipped from friendly document",
                location=location(span),
                details={"raw": span.debug.raw.hex()},
            )
            inlines.append(InlineNode(InlineKind.UNKNOWN_CONTROL, attrs={"raw": span.debug.raw.hex(), "span_offset": span.offset}))
            continue

        if span.kind in {"control", "section"}:
            tag = span.debug.attrs.get("tag")
            behavior = behavior_for(span.debug.op)
            if tag in STYLE_START_TO_KIND and span.debug.op in STYLE_START_OPS:
                attrs: JsonObject
                if span.debug.op == 0x4A:
                    resource_counter += 1
                    descriptor = _pcm_descriptor_from_payload(span.debug.payload)
                    status = ResourceStatus(descriptor.get("status", ResourceStatus.UNRESOLVED.value))
                    resource = ResourceRef(
                        id=f"audio-{resource_counter}",
                        kind=ResourceKind.AUDIO,
                        label="audio",
                        status=status,
                        component=entry.address.component,
                        source_offset=span.offset,
                        details=descriptor,
                    )
                    resources.append(resource)
                    attrs = _link_attrs_for_start(span, resource_id=resource.id)
                    attrs["resource_kind"] = resource.kind.value
                    attrs["resource_status"] = status.value
                    attrs["media_descriptor"] = descriptor
                    if status == ResourceStatus.MALFORMED:
                        diagnostics.add(
                            Severity.WARNING,
                            DiagnosticArea.MEDIA,
                            "unresolved_media_ref",
                            "audio range is preserved but not resolved to a payload",
                            location=location(span),
                            details={"resource_id": resource.id, "reason": descriptor.get("reason")},
                        )
                else:
                    attrs = _link_attrs_for_start(span) if span.debug.op in LINK_START_OPS else {}
                active_styles.append(ActiveInline(STYLE_START_TO_KIND[tag], attrs))
            elif tag in STYLE_START_TO_KIND and span.debug.op in STYLE_END_OPS:
                style = STYLE_START_TO_KIND[tag]
                active_index = next((index for index in range(len(active_styles) - 1, -1, -1) if active_styles[index].kind == style), None)
                if active_index is not None:
                    active = active_styles[active_index]
                    if span.debug.op in LINK_END_OPS:
                        target = active.attrs.get("link_target")
                        if isinstance(target, dict):
                            target["end_op"] = f"{span.debug.op:02x}" if span.debug.op is not None else None
                            target["end_payload"] = span.debug.payload.hex()
                            if span.debug.op in LINK_END_TARGET_OPS:
                                address = _address_from_bcd_payload(span.debug.payload)
                                if address is not None:
                                    target["address"] = address.to_dict()
                                    target["href"] = f"lvcore-entry://{address.block}/{address.offset}"
                                    target["status"] = LinkTargetStatus.RESOLVED.value
                                else:
                                    target["status"] = LinkTargetStatus.MALFORMED.value
                                    target["reason"] = "malformed_address_payload"
                            elif target.get("kind") in {"url", LinkTargetKind.EXTERNAL_URL.value}:
                                target["status"] = LinkTargetStatus.CONTENT.value
                            elif target.get("kind") == LinkTargetKind.JUMP_OR_AUDIO_RANGE.value and target.get("address"):
                                target["status"] = LinkTargetStatus.DEFERRED.value
                            else:
                                target["status"] = LinkTargetStatus.UNRESOLVED.value
                                target["reason"] = "no_decoded_target"
                            if target.get("kind") not in {"url", LinkTargetKind.EXTERNAL_URL.value} and target.get("status") not in {
                                LinkTargetStatus.RESOLVED.value,
                                LinkTargetStatus.DEFERRED.value,
                            }:
                                diagnostics.add(
                                    Severity.WARNING,
                                    DiagnosticArea.BODY,
                                    "unresolved_link_target",
                                    "link control did not expose a resolvable target",
                                    location=location(span),
                                    details={
                                        "op": f"{span.debug.op:02x}" if span.debug.op is not None else None,
                                        "payload": span.debug.payload.hex(),
                                        "reason": target.get("reason") or "unresolved_target",
                                        "kind": target.get("kind"),
                                    },
                                )
                    for index in range(len(active_styles) - 1, -1, -1):
                        if active_styles[index].kind == style:
                            del active_styles[index]
                            break
                else:
                    diagnostics.add(
                        Severity.WARNING,
                        DiagnosticArea.BODY,
                        "unmatched_style_end",
                        "style end control had no matching start",
                        location=location(span),
                        details={"style": style.value, "op": f"{span.debug.op:02x}" if span.debug.op is not None else None},
                    )
            elif behavior is not None and behavior.category == OpcodeCategory.TAB:
                diagnostics.add(
                    Severity.INFO,
                    DiagnosticArea.OPCODE,
                    behavior.diagnostic_code or "tab_column_control",
                    "tab/column positioning control preserved as a nonprinting layout hint",
                    location=location(span),
                    details={"op": f"{span.debug.op:02x}" if span.debug.op is not None else None, "payload": span.debug.payload.hex()},
                )
            elif behavior is not None and behavior.category == OpcodeCategory.MEDIA_LAYOUT:
                diagnostics.add(
                    Severity.INFO,
                    DiagnosticArea.MEDIA,
                    behavior.diagnostic_code or "media_layout_control",
                    "media layout control preserved as a nonprinting resource hint",
                    location=location(span),
                    details={"op": f"{span.debug.op:02x}" if span.debug.op is not None else None, "payload": span.debug.payload.hex()},
                )
            elif tag in IGNORED_CONTROL_TAGS or span.debug.op in KNOWN_NEUTRAL_OPS:
                pass
            elif tag is None:
                diagnostics.add(
                    Severity.WARNING,
                    DiagnosticArea.OPCODE,
                    "unknown_control",
                    "unknown control opcode skipped from friendly document",
                    location=location(span),
                    details={"op": f"{span.debug.op:02x}" if span.debug.op is not None else None, "payload": span.debug.payload.hex()},
                )
                inlines.append(
                    InlineNode(
                        InlineKind.UNKNOWN_CONTROL,
                        attrs={
                            "op": f"{span.debug.op:02x}" if span.debug.op is not None else None,
                            "payload": span.debug.payload.hex(),
                            "span_offset": span.offset,
                        },
                    )
                )
            continue

    if active_styles:
        diagnostics.add(
            Severity.WARNING,
            DiagnosticArea.BODY,
            "unclosed_style",
            "entry ended with unclosed inline style controls",
            recoverable=True,
            details={"styles": [style.kind.value for style in active_styles]},
        )
    _flush_paragraph(blocks, inlines)

    if not blocks and entry.text:
        blocks.append(BlockNode(BlockKind.PARAGRAPH, (InlineNode(InlineKind.TEXT, text=entry.text),)))

    return EntryDocument(
        blocks=tuple(blocks),
        resources=tuple(resources),
        diagnostics=tuple(diagnostics.diagnostics),
        metadata={
            "headword": entry.headword,
            "address": entry.address.to_dict(),
            "end_address": entry.end_address.to_dict(),
        },
        debug_metadata={
            "span_summaries": [span.to_debug_summary() for span in entry.spans],
        },
    )
