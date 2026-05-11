"""Structured entry documents built from decoded LogoVista spans."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .diagnostics import Diagnostic, DiagnosticArea, DiagnosticBag, Location, Severity
from .model import Entry, Span


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


IGNORED_CONTROL_TAGS = {
    "halfwidth",
    "head",
    "literal",
    "private",
    "tab_column",
    "media_layout",
    "title_separator",
}


KNOWN_NEUTRAL_OPS = {0x00, 0x02, 0x03, 0x09, 0x1A, 0x1C}


@dataclass(frozen=True)
class ResourceRef:
    id: str
    kind: ResourceKind
    label: str
    component: str | None = None
    code: str | None = None
    source_offset: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "component": self.component,
            "code": self.code,
            "source_offset": self.source_offset,
            "details": self.details,
        }


@dataclass(frozen=True)
class InlineNode:
    kind: InlineKind
    text: str | None = None
    children: tuple["InlineNode", ...] = ()
    code: str | None = None
    resource_id: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "children": [child.to_dict() for child in self.children],
            "code": self.code,
            "resource_id": self.resource_id,
            "attrs": self.attrs,
        }


@dataclass(frozen=True)
class BlockNode:
    kind: BlockKind
    inlines: tuple[InlineNode, ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "inlines": [node.to_dict() for node in self.inlines],
            "attrs": self.attrs,
        }


@dataclass(frozen=True)
class EntryDocument:
    blocks: tuple[BlockNode, ...]
    resources: tuple[ResourceRef, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks": [block.to_dict() for block in self.blocks],
            "resources": [resource.to_dict() for resource in self.resources],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "metadata": self.metadata,
        }


def _gaiji_unresolved(span: Span) -> bool:
    if not span.code:
        return False
    if span.text is None:
        return True
    upper = span.code.upper()
    return span.text in {f"<h{upper}>", f"<z{upper}>", f"<g{upper}>"}


def _wrap_styles(node: InlineNode, active_styles: Iterable[InlineKind]) -> InlineNode:
    wrapped = node
    for style in reversed(tuple(active_styles)):
        wrapped = InlineNode(style, children=(wrapped,))
    return wrapped


def _add_text(target: list[InlineNode], text: str | None, active_styles: list[InlineKind]) -> None:
    if not text:
        return
    target.append(_wrap_styles(InlineNode(InlineKind.TEXT, text=text), active_styles))


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
    active_styles: list[InlineKind] = []
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
                attrs={"resolved": not unresolved, "raw_text": span.text},
            )
            inlines.append(_wrap_styles(node, active_styles))
            continue

        if span.kind == "media_ref":
            resource_counter += 1
            resource = ResourceRef(
                id=f"media-{resource_counter}",
                kind=ResourceKind.MEDIA,
                label="media",
                component=entry.address.component,
                source_offset=span.offset,
                details={"payload_hex": span.payload.hex()},
            )
            resources.append(resource)
            inlines.append(InlineNode(InlineKind.MEDIA_REF, resource_id=resource.id, attrs={"label": resource.label}))
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
            if tag in STYLE_START_TO_KIND and span.op in STYLE_START_OPS:
                active_styles.append(STYLE_START_TO_KIND[tag])
            elif tag in STYLE_START_TO_KIND and span.op in STYLE_END_OPS:
                style = STYLE_START_TO_KIND[tag]
                if style in active_styles:
                    for index in range(len(active_styles) - 1, -1, -1):
                        if active_styles[index] == style:
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
            elif tag in {kind.value for kind in InlineKind}:
                pass
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
            details={"styles": [style.value for style in active_styles]},
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
            "raw_spans": [span.to_dict() for span in entry.spans],
        },
    )
