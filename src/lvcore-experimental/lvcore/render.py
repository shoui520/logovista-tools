"""Friendly and debug renderers for lvcore entry documents."""

from __future__ import annotations

from enum import Enum
import hashlib
from html import escape
import json
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


def _public_href(href: str, *, profile: HtmlProfile) -> str:
    if profile != HtmlProfile.DEBUG and href.startswith("lvcore-entry://"):
        digest = hashlib.sha1(href.encode("utf-8")).hexdigest()[:12]
        return f"lvcore-entry://ref-{digest}"
    return href


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
    if profile == HtmlProfile.SEMANTIC:
        return f'<span class="lv-inline lv-inline-gaiji" data-kind="gaiji">{text}</span>'
    if profile == HtmlProfile.LOGOVISTA_LIKE and node.text:
        return f'<span class="lv-lvlike-gaiji">{text}</span>'
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
    resource_id = str(target.get("resource_id") or node.attrs.get("resource_id") or "")
    is_external = kind in {"url", "external_url"}
    if is_external and not href and _is_safe_url(visible_text):
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
        class_name = "lv-link lv-link-url" if is_external else "lv-link lv-link-internal"
        if profile == HtmlProfile.SEMANTIC:
            class_name = f"lv-inline lv-inline-link {class_name}"
        elif profile == HtmlProfile.LOGOVISTA_LIKE:
            class_name = f"lv-lvlike-link {class_name}"
        public_href = _public_href(href, profile=profile)
        data_link = ""
        if not is_external:
            data_link = f' data-lvcore-link="{escape(public_href, quote=True)}"'
        return f'<a class="{class_name}" href="{escape(public_href, quote=True)}"{data_link}{debug_attrs}>{children}</a>'
    if resource_id:
        class_name = "lv-link lv-link-resource"
        if profile == HtmlProfile.SEMANTIC:
            class_name = "lv-inline lv-inline-link lv-link lv-link-resource"
        elif profile == HtmlProfile.LOGOVISTA_LIKE:
            class_name = "lv-lvlike-link lv-link lv-link-resource"
        return (
            f'<span class="{class_name}" data-resource-url="{escape(_resource_url(resource_id, resource_url_mapper), quote=True)}" '
            f'data-resource-kind="audio"{debug_attrs}>{children}</span>'
        )
    class_name = "lv-link lv-link-unresolved"
    if profile == HtmlProfile.SEMANTIC:
        class_name = "lv-inline lv-inline-link lv-link lv-link-unresolved"
    elif profile == HtmlProfile.LOGOVISTA_LIKE:
        class_name = "lv-lvlike-link lv-link lv-link-unresolved"
    return f'<span class="{class_name}"{debug_attrs}>{children}</span>'


def _render_inline_html(
    node: InlineNode,
    *,
    profile: HtmlProfile,
    gaiji_policy: GaijiPolicy,
    resource_url_mapper: ResourceUrlMapper | None = None,
) -> str:
    if node.kind == InlineKind.TEXT:
        text = escape(node.text or "")
        if profile == HtmlProfile.SEMANTIC:
            return f'<span class="lv-inline lv-inline-text">{text}</span>'
        return text
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
        if profile == HtmlProfile.SEMANTIC:
            return (
                f'<span class="lv-inline lv-inline-resource lv-media-ref" data-kind="{escape(str(node.attrs.get("resource_kind") or "media"))}" '
                f'data-resource-url="{escape(_resource_url(resource_id, resource_url_mapper), quote=True)}">[{label}]</span>'
            )
        if profile == HtmlProfile.LOGOVISTA_LIKE:
            return (
                f'<span class="lv-lvlike-media lv-media-ref" data-resource-url="{escape(_resource_url(resource_id, resource_url_mapper), quote=True)}">'
                f'[{label}]</span>'
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
    if profile == HtmlProfile.SEMANTIC:
        class_name = f"lv-inline lv-inline-{node.kind.value}"
    elif profile == HtmlProfile.LOGOVISTA_LIKE:
        class_name = f"lv-lvlike-{node.kind.value}"
    if start == "span":
        return f'<span class="{class_name}">{children}</span>'
    if profile in {HtmlProfile.SEMANTIC, HtmlProfile.LOGOVISTA_LIKE}:
        return f'<{start} class="{class_name}">{children}</{end}>'
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
        if profile == HtmlProfile.SEMANTIC:
            return f'<section class="lv-block lv-block-heading" data-block-kind="heading"><h3 class="lv-heading">{body}</h3></section>'
        if profile == HtmlProfile.LOGOVISTA_LIKE:
            return f'<div class="lv-lvlike-heading lv-heading">{body}</div>'
        return f'<h3 class="lv-heading">{body}</h3>'
    if block.kind in {BlockKind.MEDIA, BlockKind.IMAGE, BlockKind.AUDIO}:
        if profile == HtmlProfile.SEMANTIC:
            return f'<section class="lv-block lv-block-{block.kind.value}" data-block-kind="{block.kind.value}">{body}</section>'
        return f'<div class="lv-{block.kind.value}">{body}</div>'
    if block.kind == BlockKind.EXAMPLE:
        if profile == HtmlProfile.SEMANTIC:
            return f'<section class="lv-block lv-block-example" data-block-kind="example">{body}</section>'
        class_name = "lv-example" if profile != HtmlProfile.LOGOVISTA_LIKE else "lv-lvlike-example"
        return f'<p class="{class_name}">{body}</p>'
    if profile == HtmlProfile.SEMANTIC:
        return f'<section class="lv-block lv-block-{block.kind.value}" data-block-kind="{block.kind.value}">{body}</section>'
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
    if profile == HtmlProfile.SEMANTIC:
        classes.append("lv-entry-semantic")
    elif profile == HtmlProfile.LOGOVISTA_LIKE:
        classes.append("lv-entry-logovista-like")
    elif profile == HtmlProfile.DEBUG:
        classes.append("lv-entry-debug")
    parts = [f'<article class="{" ".join(classes)}" data-render-profile="{escape(profile.value)}">']
    for block in document.blocks:
        parts.append(_render_block_html(block, profile=profile, gaiji_policy=gaiji_policy, resource_url_mapper=resource_url_mapper))
    if include_diagnostics or profile == HtmlProfile.DEBUG:
        parts.append('<section class="lv-diagnostics">')
        parts.append("<h4>Diagnostics</h4>")
        parts.append("<ul>")
        for diagnostic in document.diagnostics:
            details = ""
            if profile == HtmlProfile.DEBUG:
                details_json = json.dumps(diagnostic.details, ensure_ascii=False, sort_keys=True)
                details = f' data-details="{escape(details_json, quote=True)}"'
            parts.append(
                '<li class="lv-diagnostic" '
                f'data-severity="{escape(diagnostic.severity.value)}" '
                f'data-area="{escape(diagnostic.area.value)}" '
                f'data-code="{escape(diagnostic.code)}"{details}>'
                f"{escape(diagnostic.message)}</li>"
            )
        parts.append("</ul>")
        if profile == HtmlProfile.DEBUG:
            debug_metadata = dict(document.debug_metadata)
            # Backward-compatible escape hatch for older synthetic tests or
            # callers that still place debug-only fields in metadata.
            if "span_summaries" not in debug_metadata and "raw_spans" in document.metadata:
                debug_metadata["span_summaries"] = document.metadata.get("raw_spans")
            parts.append('<details class="lv-span-summaries"><summary>Span summaries</summary><pre>')
            for span in debug_metadata.get("span_summaries", []):
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
