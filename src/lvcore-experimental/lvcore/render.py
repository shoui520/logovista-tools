"""Friendly and debug renderers for lvcore entry documents."""

from __future__ import annotations

from enum import Enum
from html import escape
from typing import Callable, Iterable
from urllib.parse import urlsplit

from .document import BlockKind, BlockNode, EntryDocument, InlineKind, InlineNode


class HtmlProfile(str, Enum):
    FRIENDLY = "friendly"
    SEMANTIC = "semantic"
    LOGOVISTA_LIKE = "logovista_like"
    DEBUG = "debug"


class GaijiPolicy(str, Enum):
    UNICODE_PREFERRED = "unicode_preferred"
    BITMAP_PREFERRED = "bitmap_preferred"
    BITMAP_ONLY = "bitmap_only"
    PLACEHOLDER = "placeholder"
    DEBUG_RAW_CODE = "debug_raw_code"


STYLE_TAGS = {
    InlineKind.BOLD: ("strong", "strong"),
    InlineKind.ITALIC: ("em", "em"),
    InlineKind.EMPHASIS: ("em", "em"),
    InlineKind.SUBSCRIPT: ("sub", "sub"),
    InlineKind.SUPERSCRIPT: ("sup", "sup"),
}


ResourceUrlMapper = Callable[[str], str]


def _default_resource_url(resource_id: str) -> str:
    return f"lvcore-resource://{resource_id}"


def _resource_url(resource_id: str, mapper: ResourceUrlMapper | None) -> str:
    return mapper(resource_id) if mapper is not None else _default_resource_url(resource_id)


def _is_safe_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme.lower() in {"http", "https", "mailto"} and bool(parsed.netloc or parsed.scheme == "mailto")


def _gaiji_text(node: InlineNode, policy: GaijiPolicy) -> str:
    if policy == GaijiPolicy.DEBUG_RAW_CODE and node.code:
        return f"<h{node.code.upper()}>"
    if policy == GaijiPolicy.PLACEHOLDER:
        return "□"
    if policy in {GaijiPolicy.UNICODE_PREFERRED, GaijiPolicy.BITMAP_PREFERRED} and node.text:
        return node.text
    return "□"


def _inline_text(node: InlineNode, *, gaiji_policy: GaijiPolicy = GaijiPolicy.UNICODE_PREFERRED) -> str:
    if node.kind == InlineKind.TEXT:
        return node.text or ""
    if node.kind == InlineKind.LINE_BREAK:
        return "\n"
    if node.kind == InlineKind.GAIJI:
        return _gaiji_text(node, gaiji_policy)
    if node.kind == InlineKind.MEDIA_REF:
        return f"[{node.attrs.get('label') or 'media'}]"
    if node.kind == InlineKind.UNKNOWN_CONTROL:
        return ""
    return "".join(_inline_text(child, gaiji_policy=gaiji_policy) for child in node.children)


def _render_gaiji_html(
    node: InlineNode,
    *,
    profile: HtmlProfile,
    gaiji_policy: GaijiPolicy,
    resource_url_mapper: ResourceUrlMapper | None,
) -> str:
    effective_policy = gaiji_policy if profile != HtmlProfile.DEBUG else GaijiPolicy.DEBUG_RAW_CODE
    text = escape(_gaiji_text(node, effective_policy))
    resource_id = node.resource_id or (f"gaiji-{node.code}" if node.code else "")
    if profile == HtmlProfile.DEBUG:
        return (
            f'<span class="lv-gaiji-debug" data-code="{escape(node.code or "")}" '
            f'data-resource-id="{escape(resource_id)}">{text}</span>'
        )
    if gaiji_policy in {GaijiPolicy.BITMAP_PREFERRED, GaijiPolicy.BITMAP_ONLY} and resource_id:
        fallback = text if gaiji_policy == GaijiPolicy.BITMAP_PREFERRED and node.text else "□"
        return (
            f'<span class="lv-gaiji lv-gaiji-resource" data-resource-url="{escape(_resource_url(resource_id, resource_url_mapper), quote=True)}" '
            f'data-resource-id="{escape(resource_id)}">{fallback}</span>'
        )
    if node.text:
        return f'<span class="lv-gaiji">{text}</span>'
    return '<span class="lv-gaiji lv-gaiji-unresolved">□</span>'


def _render_link_html(
    node: InlineNode,
    *,
    profile: HtmlProfile,
    gaiji_policy: GaijiPolicy,
    resource_url_mapper: ResourceUrlMapper | None,
) -> str:
    children = "".join(
        _render_inline_html(child, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper)
        for child in node.children
    )
    target = node.attrs.get("link_target") if isinstance(node.attrs, dict) else None
    target = target if isinstance(target, dict) else {}
    kind = str(target.get("kind") or "unknown")
    status = str(target.get("status") or "unresolved")
    visible_text = _inline_text(node, gaiji_policy=GaijiPolicy.UNICODE_PREFERRED).strip()
    href = str(target.get("href") or "")
    if kind == "url" and not href and _is_safe_url(visible_text):
        href = visible_text

    debug_attrs = ""
    if profile == HtmlProfile.DEBUG:
        debug_attrs = (
            f' data-link-kind="{escape(kind)}"'
            f' data-link-status="{escape(status)}"'
            f' data-start-op="{escape(str(node.attrs.get("start_op") or ""))}"'
            f' data-start-payload="{escape(str(node.attrs.get("start_payload") or ""))}"'
            f' data-end-payload="{escape(str(target.get("end_payload") or ""))}"'
        )

    if href and (href.startswith("lvcore-entry://") or _is_safe_url(href)):
        class_name = "lv-link lv-link-url" if kind == "url" else "lv-link lv-link-internal"
        return f'<a class="{class_name}" href="{escape(href, quote=True)}"{debug_attrs}>{children}</a>'
    return f'<span class="lv-link lv-link-unresolved"{debug_attrs}>{children}</span>'


def _render_inline_html(
    node: InlineNode,
    *,
    profile: HtmlProfile,
    gaiji_policy: GaijiPolicy,
    resource_url_mapper: ResourceUrlMapper | None = None,
) -> str:
    if node.kind == InlineKind.TEXT:
        return escape(node.text or "")
    if node.kind == InlineKind.LINE_BREAK:
        return "<br>"
    if node.kind == InlineKind.GAIJI:
        return _render_gaiji_html(node, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper)
    if node.kind == InlineKind.MEDIA_REF:
        resource_id = node.resource_id or "resource"
        label = escape(str(node.attrs.get("label") or "media"))
        if profile == HtmlProfile.DEBUG:
            payload = escape(str(node.attrs.get("payload_hex") or ""))
            return (
                f'<span class="lv-media-ref-debug" data-resource-id="{escape(resource_id)}" '
                f'data-resource-kind="{escape(str(node.attrs.get("resource_kind") or "media"))}" '
                f'data-payload="{payload}">[{label}:{escape(resource_id)}]</span>'
            )
        return (
            f'<span class="lv-media-ref" data-resource-url="{escape(_resource_url(resource_id, resource_url_mapper), quote=True)}" '
            f'data-resource-kind="{escape(str(node.attrs.get("resource_kind") or "media"))}">[{label}]</span>'
        )
    if node.kind == InlineKind.UNKNOWN_CONTROL:
        if profile == HtmlProfile.DEBUG:
            op = escape(str(node.attrs.get("op") or "byte"))
            payload = escape(str(node.attrs.get("payload") or node.attrs.get("raw") or ""))
            return f'<span class="lv-unknown-control" data-op="{op}" data-payload="{payload}">[unknown:{op}]</span>'
        return ""
    if node.kind == InlineKind.LINK:
        return _render_link_html(node, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper)

    children = "".join(
        _render_inline_html(child, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper)
        for child in node.children
    )
    tag = STYLE_TAGS.get(node.kind)
    if tag is None:
        return children
    start, end = tag
    class_name = f"lv-{node.kind.value}"
    if start == "span":
        return f'<span class="{class_name}">{children}</span>'
    return f"<{start}>{children}</{end}>"


def _render_block_html(
    block: BlockNode,
    *,
    profile: HtmlProfile,
    gaiji_policy: GaijiPolicy,
    resource_url_mapper: ResourceUrlMapper | None,
) -> str:
    body = "".join(
        _render_inline_html(node, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper)
        for node in block.inlines
    )
    if block.kind == BlockKind.HEADING:
        return f'<h3 class="lv-heading">{body}</h3>'
    if block.kind in {BlockKind.MEDIA, BlockKind.IMAGE, BlockKind.AUDIO}:
        return f'<div class="lv-{block.kind.value}">{body}</div>'
    class_name = "lv-paragraph" if profile != HtmlProfile.LOGOVISTA_LIKE else "lv-body-line"
    return f'<p class="{class_name}">{body}</p>'


def render_html(
    document: EntryDocument,
    *,
    profile: HtmlProfile = HtmlProfile.FRIENDLY,
    gaiji_policy: GaijiPolicy = GaijiPolicy.UNICODE_PREFERRED,
    resource_url_mapper: ResourceUrlMapper | None = None,
    include_diagnostics: bool = False,
) -> str:
    """Render an EntryDocument to safe HTML."""

    classes = ["lv-entry", f"lv-profile-{profile.value}"]
    parts = [f'<article class="{" ".join(classes)}">']
    for block in document.blocks:
        parts.append(_render_block_html(block, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper))
    if include_diagnostics or profile == HtmlProfile.DEBUG:
        parts.append('<section class="lv-diagnostics">')
        parts.append("<h4>Diagnostics</h4>")
        parts.append("<ul>")
        for diagnostic in document.diagnostics:
            parts.append(
                '<li class="lv-diagnostic" '
                f'data-severity="{escape(diagnostic.severity.value)}" '
                f'data-area="{escape(diagnostic.area.value)}" '
                f'data-code="{escape(diagnostic.code)}">'
                f"{escape(diagnostic.message)}</li>"
            )
        parts.append("</ul>")
        if profile == HtmlProfile.DEBUG:
            parts.append('<details class="lv-raw-spans"><summary>Raw spans</summary><pre>')
            for span in document.metadata.get("raw_spans", []):
                parts.append(escape(str(span)))
                parts.append("\n")
            parts.append("</pre></details>")
        parts.append("</section>")
    parts.append("</article>")
    return "".join(parts)


def _render_inline_text(node: InlineNode, *, gaiji_policy: GaijiPolicy) -> str:
    if node.kind == InlineKind.TEXT:
        return node.text or ""
    if node.kind == InlineKind.LINE_BREAK:
        return "\n"
    if node.kind == InlineKind.GAIJI:
        return _gaiji_text(node, gaiji_policy)
    if node.kind == InlineKind.MEDIA_REF:
        return f"[{node.attrs.get('label') or 'media'}]"
    if node.kind == InlineKind.UNKNOWN_CONTROL:
        return ""
    return "".join(_render_inline_text(child, gaiji_policy=gaiji_policy) for child in node.children)


def render_text(document: EntryDocument, *, gaiji_policy: GaijiPolicy = GaijiPolicy.UNICODE_PREFERRED) -> str:
    lines: list[str] = []
    for block in document.blocks:
        text = "".join(_render_inline_text(node, gaiji_policy=gaiji_policy) for node in block.inlines).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def diagnostics_to_dict(document: EntryDocument) -> list[dict[str, object]]:
    return [diagnostic.to_dict() for diagnostic in document.diagnostics]
