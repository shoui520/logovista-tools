"""Structured entry documents built from decoded LogoVista spans."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Any, Iterable

from .diagnostics import Diagnostic, DiagnosticArea, DiagnosticBag, Location, Severity
from .model import Address, Entry, Span
from .opcodes import KNOWN_NEUTRAL_OPS, OpcodeCategory, behavior_for


DOCUMENT_SCHEMA = "lvcore.entry_document.v1"
DOCUMENT_MODEL_VERSION = 1

DEBUG_ATTR_KEYS = {
    "address",
    "anchor_raw",
    "end_payload",
    "payload",
    "payload_hex",
    "raw",
    "raw_payload",
    "raw_spans",
    "raw_text",
    "span_offset",
    "start_op",
    "start_payload",
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


def _stable_private_ref(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"lvcore-entry://ref-{digest}"


def _public_mapping(value: Any, *, debug: bool) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
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


def _diagnostic_to_dict(diagnostic: Diagnostic, *, debug: bool) -> dict[str, Any]:
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
    component: str | None = None
    code: str | None = None
    source_offset: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "component": self.component,
            "source_offset": self.source_offset,
        }
        if debug:
            data["code"] = self.code
            data["details"] = _public_mapping(self.details, debug=True)
        else:
            public_details = {key: value for key, value in self.details.items() if key in {"resolved"}}
            if public_details:
                data["details"] = public_details
        return data


@dataclass(frozen=True)
class LinkTarget:
    kind: str
    href: str | None = None
    address: Address | None = None
    raw_payload: str | None = None
    status: str = "unresolved"

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "href": self.href,
            "status": self.status,
        }
        if not debug and self.href and self.href.startswith("lvcore-entry://"):
            data["href"] = _stable_private_ref(self.href)
        if debug:
            data["address"] = self.address.to_dict() if self.address else None
            data["raw_payload"] = self.raw_payload
        return data


@dataclass(frozen=True)
class InlineNode:
    kind: InlineKind
    text: str | None = None
    children: tuple["InlineNode", ...] = ()
    code: str | None = None
    resource_id: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
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
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
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
    metadata: dict[str, Any] = field(default_factory=dict)
    debug_metadata: dict[str, Any] = field(default_factory=dict)

    def resource_map(self) -> dict[str, ResourceRef]:
        return {resource.id: resource for resource in self.resources}

    def diagnostics_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for diagnostic in self.diagnostics:
            counts[diagnostic.code] = counts.get(diagnostic.code, 0) + 1
        return counts

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
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

    def to_debug_dict(self) -> dict[str, Any]:
        return self.to_dict(debug=True)


def _gaiji_unresolved(span: Span) -> bool:
    if not span.code:
        return False
    if span.text is None:
        return True
    upper = span.code.upper()
    return span.text in {f"<h{upper}>", f"<z{upper}>", f"<g{upper}>"}


@dataclass
class ActiveInline:
    kind: InlineKind
    attrs: dict[str, Any] = field(default_factory=dict)


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


def _link_attrs_for_start(span: Span) -> dict[str, Any]:
    if span.op == 0x3B:
        target = LinkTarget(kind="url", status="content")
    else:
        target = LinkTarget(kind="internal", raw_payload=span.payload.hex() or None, status="pending")
    return {
        "link_target": target.to_dict(),
        "start_op": f"{span.op:02x}" if span.op is not None else None,
        "start_payload": span.payload.hex(),
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
            if span.kind == "control" and span.op in {0xE2, 0xE3}:
                diagnostics.add(
                    Severity.INFO,
                    DiagnosticArea.OPCODE,
                    "private_renderer_directive",
                    "private renderer directive hidden from friendly document",
                    location=location(span),
                    details={"op": f"{span.op:02x}" if span.op is not None else None},
                )
            continue

        if span.kind == "text":
            _add_text(inlines, span.text, active_styles)
            continue

        if span.kind == "break":
            _flush_paragraph(blocks, inlines)
            continue

        if span.kind == "gaiji":
            unresolved = _gaiji_unresolved(span)
            resource_counter += 1
            resource_id = f"gaiji-{resource_counter}"
            resources.append(
                ResourceRef(
                    id=resource_id,
                    kind=ResourceKind.GAIJI,
                    label=span.text or "gaiji",
                    component=entry.address.component,
                    code=span.code,
                    source_offset=span.offset,
                    details={"resolved": not unresolved},
                )
            )
            if unresolved:
                diagnostics.add(
                    Severity.WARNING,
                    DiagnosticArea.GAIJI,
                    "unresolved_gaiji",
                    "gaiji has no Unicode mapping in the current package context",
                    location=location(span),
                    details={"code": span.code},
                )
            node = InlineNode(
                InlineKind.GAIJI,
                text=None if unresolved else span.text,
                code=span.code,
                resource_id=resource_id,
                attrs={"resolved": not unresolved, "raw_text": span.text},
            )
            _append_inline(inlines, _wrap_styles(node, active_styles))
            continue

        if span.kind == "media_ref":
            resource_counter += 1
            resource_kind_name = str(span.attrs.get("resource_kind") or "media")
            try:
                resource_kind = ResourceKind(resource_kind_name)
            except ValueError:
                resource_kind = ResourceKind.MEDIA
            resource = ResourceRef(
                id=f"media-{resource_counter}",
                kind=resource_kind,
                label=resource_kind.value,
                component=entry.address.component,
                source_offset=span.offset,
                details={"payload_hex": span.payload.hex()},
            )
            resources.append(resource)
            _append_inline(
                inlines,
                _wrap_styles(
                    InlineNode(
                        InlineKind.MEDIA_REF,
                        resource_id=resource.id,
                        attrs={"label": resource.label, "resource_kind": resource.kind.value, "payload_hex": span.payload.hex()},
                    ),
                    active_styles,
                ),
            )
            diagnostics.add(
                Severity.WARNING,
                DiagnosticArea.MEDIA,
                "unresolved_media_ref",
                "media reference is preserved but not resolved to a payload in document v0",
                location=location(span),
                details={"resource_id": resource.id},
            )
            continue

        if span.kind == "unknown_byte":
            diagnostics.add(
                Severity.WARNING,
                DiagnosticArea.BODY,
                "unknown_byte",
                "unknown body byte skipped from friendly document",
                location=location(span),
                details={"raw": span.raw.hex()},
            )
            inlines.append(InlineNode(InlineKind.UNKNOWN_CONTROL, attrs={"raw": span.raw.hex(), "span_offset": span.offset}))
            continue

        if span.kind in {"control", "section"}:
            tag = span.attrs.get("tag")
            behavior = behavior_for(span.op)
            if tag in STYLE_START_TO_KIND and span.op in STYLE_START_OPS:
                attrs = _link_attrs_for_start(span) if span.op in LINK_START_OPS else {}
                active_styles.append(ActiveInline(STYLE_START_TO_KIND[tag], attrs))
            elif tag in STYLE_START_TO_KIND and span.op in STYLE_END_OPS:
                style = STYLE_START_TO_KIND[tag]
                active_index = next((index for index in range(len(active_styles) - 1, -1, -1) if active_styles[index].kind == style), None)
                if active_index is not None:
                    active = active_styles[active_index]
                    if span.op in LINK_END_OPS:
                        target = active.attrs.get("link_target")
                        if isinstance(target, dict):
                            target["end_op"] = f"{span.op:02x}" if span.op is not None else None
                            target["end_payload"] = span.payload.hex()
                            if span.op in LINK_END_TARGET_OPS:
                                address = _address_from_bcd_payload(span.payload)
                                if address is not None:
                                    target["address"] = address.to_dict()
                                    target["href"] = f"lvcore-entry://{address.block}/{address.offset}"
                                    target["status"] = "resolved"
                                else:
                                    target["status"] = "invalid"
                            elif target.get("kind") == "url":
                                target["status"] = "content"
                            else:
                                target["status"] = "unresolved"
                            if target.get("kind") != "url" and target.get("status") != "resolved":
                                diagnostics.add(
                                    Severity.WARNING,
                                    DiagnosticArea.BODY,
                                    "unresolved_link_target",
                                    "link control did not expose a resolvable target",
                                    location=location(span),
                                    details={"op": f"{span.op:02x}" if span.op is not None else None, "payload": span.payload.hex()},
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
                        details={"style": style.value, "op": f"{span.op:02x}" if span.op is not None else None},
                    )
            elif behavior is not None and behavior.category == OpcodeCategory.TAB:
                diagnostics.add(
                    Severity.INFO,
                    DiagnosticArea.OPCODE,
                    behavior.diagnostic_code or "tab_column_control",
                    "tab/column positioning control preserved as a nonprinting layout hint",
                    location=location(span),
                    details={"op": f"{span.op:02x}" if span.op is not None else None, "payload": span.payload.hex()},
                )
            elif behavior is not None and behavior.category == OpcodeCategory.MEDIA_LAYOUT:
                diagnostics.add(
                    Severity.INFO,
                    DiagnosticArea.MEDIA,
                    behavior.diagnostic_code or "media_layout_control",
                    "media layout control preserved as a nonprinting resource hint",
                    location=location(span),
                    details={"op": f"{span.op:02x}" if span.op is not None else None, "payload": span.payload.hex()},
                )
            elif tag in IGNORED_CONTROL_TAGS or span.op in KNOWN_NEUTRAL_OPS:
                pass
            elif tag is None:
                diagnostics.add(
                    Severity.WARNING,
                    DiagnosticArea.OPCODE,
                    "unknown_control",
                    "unknown control opcode skipped from friendly document",
                    location=location(span),
                    details={"op": f"{span.op:02x}" if span.op is not None else None, "payload": span.payload.hex()},
                )
                inlines.append(
                    InlineNode(
                        InlineKind.UNKNOWN_CONTROL,
                        attrs={
                            "op": f"{span.op:02x}" if span.op is not None else None,
                            "payload": span.payload.hex(),
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
