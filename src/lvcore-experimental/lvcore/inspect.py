"""Explicit inspection renderers for developer/debug output."""

from __future__ import annotations

import json
from html import escape

from .document import BlockKind, BlockNode, EntryDocument, InlineKind, InlineNode
from .render import (
    GaijiPolicy,
    ResourceUrlMapper,
    STYLE_TAGS,
    _gaiji_text,
    _inline_text,
    _resource_url,
    _sanitize_sidecar_html,
)


class InspectorRenderer:
    """Render bounded developer details outside the friendly HTML profile."""

    def __init__(self, *, resource_url_mapper: ResourceUrlMapper | None = None) -> None:
        self.resource_url_mapper = resource_url_mapper

    def render_html(self, document: EntryDocument) -> str:
        parts = ['<article class="lv-entry lv-profile-debug lv-entry-debug" data-render-profile="debug">']
        for block in document.blocks:
            parts.append(self._render_block(block))
        parts.append(self._diagnostics_section(document))
        parts.append("</article>")
        return "".join(parts)

    def _render_block(self, block: BlockNode) -> str:
        sidecar_html = block.attrs.get("sidecar_html")
        if isinstance(sidecar_html, str) and sidecar_html:
            body = _sanitize_sidecar_html(sidecar_html)
            return f'<div class="lv-sidecar-html lv-sidecar-html-debug">{body}</div>'
        body = "".join(self._render_inline(node) for node in block.inlines)
        if block.kind == BlockKind.HEADING:
            return f'<h3 class="lv-heading lv-heading-debug">{body}</h3>'
        if block.kind in {BlockKind.MEDIA, BlockKind.IMAGE, BlockKind.AUDIO}:
            return f'<div class="lv-{block.kind.value} lv-debug-block">{body}</div>'
        if block.kind == BlockKind.EXAMPLE:
            return f'<p class="lv-example lv-debug-block">{body}</p>'
        return f'<p class="lv-paragraph lv-debug-block">{body}</p>'

    def _render_inline(self, node: InlineNode) -> str:
        if node.kind == InlineKind.TEXT:
            return escape(node.text or "")
        if node.kind == InlineKind.LINE_BREAK:
            return "<br>"
        if node.kind == InlineKind.GAIJI:
            text = escape(_gaiji_text(node, GaijiPolicy.DEBUG_RAW_CODE))
            resource_id = node.resource_id or (f"gaiji-{node.code}" if node.code else "")
            status = str(node.attrs.get("gaiji_display_status") or "")
            reason = str(node.attrs.get("gaiji_reason") or "")
            return (
                f'<span class="lv-gaiji-debug" data-code="{escape(node.code or "")}" '
                f'data-resource-id="{escape(resource_id)}" data-gaiji-status="{escape(status)}" '
                f'data-gaiji-reason="{escape(reason)}">{text}</span>'
            )
        if node.kind == InlineKind.MEDIA_REF:
            resource_id = node.resource_id or "resource"
            label = escape(str(node.attrs.get("label") or "media"))
            payload = escape(str(node.attrs.get("payload_hex") or ""))
            return (
                f'<span class="lv-media-ref-debug" data-resource-id="{escape(resource_id)}" '
                f'data-resource-kind="{escape(str(node.attrs.get("resource_kind") or "media"))}" '
                f'data-payload="{payload}">[{label}:{escape(resource_id)}]</span>'
            )
        if node.kind == InlineKind.UNKNOWN_CONTROL:
            op = escape(str(node.attrs.get("op") or "byte"))
            payload = escape(str(node.attrs.get("payload") or node.attrs.get("raw") or ""))
            return f'<span class="lv-unknown-control" data-op="{op}" data-payload="{payload}">[unknown:{op}]</span>'
        if node.kind == InlineKind.LINK:
            return self._render_link(node)

        children = "".join(self._render_inline(child) for child in node.children)
        tag = STYLE_TAGS.get(node.kind)
        if tag is None:
            return children
        start, end = tag
        class_name = f"lv-{node.kind.value} lv-debug-inline"
        if start == "span":
            return f'<span class="{class_name}">{children}</span>'
        return f'<{start} class="{class_name}">{children}</{end}>'

    def _render_link(self, node: InlineNode) -> str:
        children = "".join(self._render_inline(child) for child in node.children)
        target = node.attrs.get("link_target") if isinstance(node.attrs, dict) else None
        target = target if isinstance(target, dict) else {}
        kind = str(target.get("kind") or "unknown")
        status = str(target.get("status") or "unresolved")
        visible_text = _inline_text(node, gaiji_policy=GaijiPolicy.UNICODE_PREFERRED).strip()
        href = str(target.get("href") or "")
        resource_id = str(target.get("resource_id") or node.attrs.get("resource_id") or "")
        debug_attrs = (
            f' data-link-kind="{escape(kind)}"'
            f' data-link-status="{escape(status)}"'
            f' data-start-op="{escape(str(node.attrs.get("start_op") or ""))}"'
            f' data-start-payload="{escape(str(node.attrs.get("start_payload") or ""))}"'
            f' data-end-payload="{escape(str(target.get("end_payload") or ""))}"'
        )
        if kind in {"url", "external_url"} and not href and visible_text:
            href = visible_text
        if href:
            class_name = "lv-link lv-link-url" if kind in {"url", "external_url"} else "lv-link lv-link-internal"
            return f'<a class="{class_name}" href="{escape(href, quote=True)}"{debug_attrs}>{children}</a>'
        if resource_id:
            return (
                f'<span class="lv-link lv-link-resource" '
                f'data-resource-url="{escape(_resource_url(resource_id, self.resource_url_mapper), quote=True)}" '
                f'data-resource-kind="audio"{debug_attrs}>{children}</span>'
            )
        return f'<span class="lv-link lv-link-unresolved"{debug_attrs}>{children}</span>'

    @staticmethod
    def _diagnostics_section(document: EntryDocument) -> str:
        parts = ['<section class="lv-diagnostics">', "<h4>Diagnostics</h4>", "<ul>"]
        for diagnostic in document.diagnostics:
            details_json = json.dumps(diagnostic.details, ensure_ascii=False, sort_keys=True)
            parts.append(
                '<li class="lv-diagnostic" '
                f'data-severity="{escape(diagnostic.severity.value)}" '
                f'data-area="{escape(diagnostic.area.value)}" '
                f'data-code="{escape(str(diagnostic.code))}" '
                f'data-details="{escape(details_json, quote=True)}">'
                f"{escape(diagnostic.message)}</li>"
            )
        parts.append("</ul>")
        debug_metadata = dict(document.debug_metadata)
        if "span_summaries" not in debug_metadata and "raw_spans" in document.metadata:
            debug_metadata["span_summaries"] = document.metadata.get("raw_spans")
        parts.append('<details class="lv-span-summaries"><summary>Span summaries</summary><pre>')
        for span in debug_metadata.get("span_summaries", []):
            parts.append(escape(str(span)))
            parts.append("\n")
        parts.append("</pre></details>")
        parts.append("</section>")
        return "".join(parts)
