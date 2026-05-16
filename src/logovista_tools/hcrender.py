"""Common HC HTML renderer semantics for SSED body streams.

This module intentionally implements the shared renderer behavior visible
across HC????.dll plugins. Product-specific hooks remain classified metadata;
they are not treated as exact renderer parity.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cli_ux import status
from .colscr import MediaPointer, parse_media_pointer
from .controls import KNOWN_NONPRINTING_CONTROLS, control_arg_length
from .entries import (
    DictionarySource,
    decode_jis_pair,
    discover_dictionaries,
    iter_entry_slices_reader,
    normalize_fullwidth_ascii,
)
from .hcprofiles import build_hc_behavior_profile
from .parallel import parallel_map_ordered, worker_args
from .pcmdata import PcmPointer, parse_pcm_pointer
from .rendererdb import extract_rendererdb_dictionary
from .ssed import SsedRandomReader
from .windows import (
    HcRendererClassification,
    classify_vlpljbl_file,
    classify_hc_renderer_file,
    discover_hc_renderer_files,
    discover_renderer_sidecars,
    discover_sqlite_sidecars,
    hc_renderer_classification_to_json,
    load_exinfo_for_idx,
)


STYLE_START_TAGS = {
    0x04: ("span", ' class="lv-hc-halfwidth"'),
    0x06: ("sub", ""),
    0x0B: ("span", ' class="lv-hc-literal"'),
    0x0E: ("sup", ""),
    0x10: ("i", ""),
    0x12: ("em", ""),
    0x41: ("span", ' class="lv-hc-heading"'),
    0xE0: ("b", ""),
}

STYLE_END_OPS = {
    0x05: 0x04,
    0x07: 0x06,
    0x0C: 0x0B,
    0x0F: 0x0E,
    0x11: 0x10,
    0x13: 0x12,
    0x61: 0x41,
    0xE1: 0xE0,
}

LINK_START_OPS = {0x42, 0x43, 0x44, 0x49}
LINK_END_OPS = {0x62, 0x63, 0x64, 0x69}
URL_START_OPS = {0x3B}
URL_END_OPS = {0x5B}
MEDIA_OPS = {0x3C, 0x4D}
AUDIO_START_OPS = {0x4A}
AUDIO_END_OPS = {0x6A}
PRIVATE_START_OPS = {0xE2}
PRIVATE_END_OPS = {0xE3}
VERTICAL_HINT_OPS = {0x36, 0x37, 0x4B, 0x4C}
PRIVATE_RENDERER_DIRECTIVE_OPS = {0x4E, 0x4F, 0xE4, 0xE6}


@dataclass(frozen=True)
class HcRenderOptions:
    """Options for shared HC-style rendering."""

    gaiji_map: dict[str, str] = field(default_factory=dict)
    image_sources: dict[str, str] = field(default_factory=dict)
    renderer_code: str | None = None
    vertical: bool = False
    include_debug_metadata: bool = False


@dataclass(frozen=True)
class HcRenderResult:
    """Rendered body plus behavior-level metadata."""

    html: str
    plain: str
    stats: dict[str, int]
    links: tuple[dict[str, Any], ...] = ()
    media: tuple[dict[str, Any], ...] = ()
    audio: tuple[dict[str, Any], ...] = ()
    private_directives: tuple[dict[str, Any], ...] = ()
    named_behavior_gaps: tuple[str, ...] = ()

    def as_dict(self, *, include_html: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "schema": "logovista-hc-render-result-v1",
            "plain": self.plain,
            "stats": dict(sorted(self.stats.items())),
            "links": list(self.links),
            "media": list(self.media),
            "audio": list(self.audio),
            "private_directives": list(self.private_directives),
            "named_behavior_gaps": list(self.named_behavior_gaps),
        }
        if include_html:
            row["html"] = self.html
        return row


@dataclass
class _Context:
    kind: str
    start_op: int
    payload: bytes
    parent: list[str]
    parts: list[str] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    start_offset: int = 0


@dataclass(frozen=True)
class _SectionImageRule:
    image_key: str
    css_class: str
    group_codes: frozenset[str]
    break_after: bool = True


HC_SECTION_IMAGE_RULES: dict[str, dict[str, _SectionImageRule]] = {
    # HC013A emits the exam badge when entering a contiguous example block.
    # The body stream encodes this as 1f09 0011; HC013A decodes the payload as
    # packed decimal 11 and keeps the block open across 0010/0011/0012.
    "013A": {
        "0011": _SectionImageRule(
            image_key="exam",
            css_class="ex_img",
            group_codes=frozenset({"0010", "0011", "0012"}),
        )
    }
}


def _escape_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def _escape_text(value: str) -> str:
    return html.escape(value, quote=False)


def _append_text(parts: list[str], text: str) -> None:
    if text:
        parts.append(_escape_text(text))


def _plain_from_html(value: str) -> str:
    text = value
    text = text.replace("<br>", "\n")
    text = html.unescape(text)
    out: list[str] = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return "\n".join(line.rstrip() for line in "".join(out).splitlines()).strip()


def _decode_pointer_payload(payload: bytes) -> dict[str, int] | None:
    if len(payload) < 6:
        return None
    block = int.from_bytes(payload[:4], "big")
    offset = int.from_bytes(payload[4:6], "big")
    return {"block": block, "offset": offset}


def _pointer_href(pointer: dict[str, int] | None) -> str:
    if pointer is None:
        return "lvaddr://unresolved"
    return f"lvaddr://{pointer['block']:08d}/{pointer['offset']:04d}"


def _media_target(pointer: MediaPointer | None) -> dict[str, Any] | None:
    if pointer is None:
        return None
    return {
        "component": "COLSCR.DIC",
        "block": pointer.block,
        "offset": pointer.offset,
        "resource_id": f"colscr:{pointer.block:08d}:{pointer.offset:04d}",
    }


def _audio_target(pointer: PcmPointer | None) -> dict[str, Any] | None:
    if pointer is None:
        return None
    return {
        "component": "PCMDATA.DIC",
        "kind": pointer.kind,
        "flags": pointer.flags,
        "start_block": pointer.start_block,
        "start_offset": pointer.start_offset,
        "end_block": pointer.end_block,
        "end_offset": pointer.end_offset,
        "resource_id": (
            f"pcmdata:{pointer.start_block:08d}:{pointer.start_offset:04d}-"
            f"{pointer.end_block:08d}:{pointer.end_offset:04d}"
        ),
    }


def _audio_href(target: dict[str, Any] | None) -> str:
    if target is None:
        return "lved.sond:unresolved"
    return f"lved.sond:{target['resource_id']}"


def _style_close_tag(start_op: int) -> str | None:
    spec = STYLE_START_TAGS.get(start_op)
    return spec[0] if spec else None


def _pop_context(contexts: list[_Context], kind: str) -> _Context | None:
    for index in range(len(contexts) - 1, -1, -1):
        if contexts[index].kind == kind:
            ctx = contexts.pop(index)
            if index != len(contexts):
                # Close nested unsupported contexts conservatively by flattening
                # their visible content into the popped context.
                for nested in contexts[index:]:
                    ctx.parts.extend(nested.parts)
                    ctx.text_parts.extend(nested.text_parts)
                del contexts[index:]
            return ctx
    return None


def _current_parts(root_parts: list[str], contexts: list[_Context]) -> list[str]:
    return contexts[-1].parts if contexts else root_parts


def _current_text_parts(contexts: list[_Context]) -> list[str] | None:
    return contexts[-1].text_parts if contexts else None


def _renderer_section_rules(options: HcRenderOptions) -> dict[str, _SectionImageRule]:
    code = (options.renderer_code or "").upper()
    return HC_SECTION_IMAGE_RULES.get(code, {})


def _section_image_src(rule: _SectionImageRule, image_sources: dict[str, str]) -> str | None:
    return image_sources.get(rule.image_key.lower()) or image_sources.get(f"{rule.image_key.lower()}.png")


def render_hc_body(data: bytes, options: HcRenderOptions | None = None) -> HcRenderResult:
    """Render one expanded HONMON body slice with common HC semantics."""

    options = options or HcRenderOptions()
    section_rules = _renderer_section_rules(options)
    active_section_image_rules: set[str] = set()
    root_parts: list[str] = []
    contexts: list[_Context] = []
    style_stack: list[int] = []
    stats: Counter[str] = Counter()
    links: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    private_directives: list[dict[str, Any]] = []
    gaps: set[str] = set()
    halfwidth_depth = 0
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0:
            i += 1
            continue
        if byte == 0x0A:
            _current_parts(root_parts, contexts).append("<br>")
            stats["line_breaks"] += 1
            i += 1
            continue
        if i + 1 < len(data) and data[i : i + 2] == b"\x11\x03":
            stats["legacy_controls"] += 1
            i += 2
            continue
        if byte == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            arg_len = control_arg_length(data, i)
            payload = data[i + 2 : i + 2 + arg_len]
            stats["controls"] += 1
            if len(payload) < arg_len:
                stats["truncated_controls"] += 1
                gaps.add(f"truncated_control_1f{op:02x}")
                break

            if op == 0x09:
                stats["section_markers"] += 1
                if payload:
                    code = payload.hex()
                    root = _current_parts(root_parts, contexts)
                    if active_section_image_rules and all(
                        code not in section_rules[active].group_codes for active in active_section_image_rules
                    ):
                        active_section_image_rules.clear()
                    rule = section_rules.get(code)
                    if rule is not None and code not in active_section_image_rules:
                        image_src = _section_image_src(rule, options.image_sources)
                        if image_src is None:
                            gaps.add(f"missing_section_image_{rule.image_key}")
                        else:
                            root.append(f'<img src="{_escape_attr(image_src)}" class="{_escape_attr(rule.css_class)}">')
                            if rule.break_after:
                                root.append("<br>")
                            stats["section_images"] += 1
                            active_section_image_rules.add(code)
                    root.append(f'<span class="lv-hc-section" data-lv-section="{_escape_attr(code)}"></span>')
                i += 2 + arg_len
                continue

            if op == 0x0A:
                _current_parts(root_parts, contexts).append("<br>")
                stats["line_breaks"] += 1
                i += 2 + arg_len
                continue

            if op in STYLE_START_TAGS:
                tag, attrs = STYLE_START_TAGS[op]
                _current_parts(root_parts, contexts).append(f"<{tag}{attrs}>")
                style_stack.append(op)
                if op == 0x04:
                    halfwidth_depth += 1
                if op == 0x41:
                    stats["headings"] += 1
                i += 2 + arg_len
                continue

            if op in STYLE_END_OPS:
                start_op = STYLE_END_OPS[op]
                close_tag = _style_close_tag(start_op)
                if close_tag and start_op in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        popped_tag = _style_close_tag(popped)
                        if popped_tag:
                            _current_parts(root_parts, contexts).append(f"</{popped_tag}>")
                        if popped == start_op:
                            break
                if op == 0x05 and halfwidth_depth:
                    halfwidth_depth -= 1
                i += 2 + arg_len
                continue

            if op in URL_START_OPS:
                contexts.append(_Context(kind="url", start_op=op, payload=payload, parent=_current_parts(root_parts, contexts), start_offset=i))
                i += 2 + arg_len
                continue

            if op in URL_END_OPS:
                ctx = _pop_context(contexts, "url")
                if ctx is not None:
                    label = "".join(ctx.parts)
                    ctx.parent.append(f'<span class="lv-hc-url">{label}</span>')
                i += 2 + arg_len
                continue

            if op in LINK_START_OPS:
                contexts.append(_Context(kind="link", start_op=op, payload=payload, parent=_current_parts(root_parts, contexts), start_offset=i))
                stats["links"] += 1
                i += 2 + arg_len
                continue

            if op in LINK_END_OPS:
                ctx = _pop_context(contexts, "link")
                target_payload = payload or (ctx.payload[-6:] if ctx and len(ctx.payload) >= 6 else b"")
                target = _decode_pointer_payload(target_payload)
                link = {
                    "start_control": f"1f{ctx.start_op:02x}" if ctx else None,
                    "end_control": f"1f{op:02x}",
                    "target": target,
                    "status": "resolved_address" if target else "unresolved_target",
                }
                links.append(link)
                label = "".join(ctx.parts) if ctx else ""
                if not label:
                    label = "link"
                attrs = [
                    'class="lv-hc-link"',
                    f'href="{_escape_attr(_pointer_href(target))}"',
                    f'data-lv-link-status="{_escape_attr(link["status"])}"',
                ]
                if target:
                    attrs.append(f'data-lv-block="{target["block"]}"')
                    attrs.append(f'data-lv-offset="{target["offset"]}"')
                parent = ctx.parent if ctx else _current_parts(root_parts, contexts)
                parent.append(f"<a {' '.join(attrs)}>{label}</a>")
                i += 2 + arg_len
                continue

            if op in AUDIO_START_OPS:
                contexts.append(_Context(kind="audio", start_op=op, payload=payload, parent=_current_parts(root_parts, contexts), start_offset=i))
                stats["audio_links"] += 1
                i += 2 + arg_len
                continue

            if op in AUDIO_END_OPS:
                ctx = _pop_context(contexts, "audio")
                pointer = parse_pcm_pointer(ctx.payload if ctx else payload)
                target = _audio_target(pointer)
                audio.append(
                    {
                        "start_control": f"1f{ctx.start_op:02x}" if ctx else None,
                        "end_control": f"1f{op:02x}",
                        "target": target,
                        "status": "resolved_range" if target else "unresolved_range",
                    }
                )
                label = "".join(ctx.parts) if ctx else ""
                if not label:
                    label = "audio"
                attrs = [
                    'class="lv-hc-audio"',
                    f'href="{_escape_attr(_audio_href(target))}"',
                    f'data-lv-audio-status="{_escape_attr("resolved_range" if target else "unresolved_range")}"',
                ]
                if target:
                    attrs.append(f'data-lv-resource="{_escape_attr(target["resource_id"])}"')
                parent = ctx.parent if ctx else _current_parts(root_parts, contexts)
                parent.append(f"<a {' '.join(attrs)}>{label}</a>")
                i += 2 + arg_len
                continue

            if op in MEDIA_OPS:
                pointer = parse_media_pointer(payload) if len(payload) == 18 else None
                target = _media_target(pointer)
                control = f"1f{op:02x}"
                kind = "colscr" if target else "media"
                media.append(
                    {
                        "control": control,
                        "target": target,
                        "status": "resolved_address" if target else "unresolved_payload",
                    }
                )
                attrs = [
                    'class="lv-hc-media"',
                    f'data-lv-control="{control}"',
                    f'data-lv-media-kind="{kind}"',
                    f'data-lv-media-status="{_escape_attr("resolved_address" if target else "unresolved_payload")}"',
                ]
                if target:
                    attrs.append(f'data-lv-resource="{_escape_attr(target["resource_id"])}"')
                    attrs.append(f'data-lv-block="{target["block"]}"')
                    attrs.append(f'data-lv-offset="{target["offset"]}"')
                _current_parts(root_parts, contexts).append(f"<span {' '.join(attrs)}></span>")
                stats["media_placeholders"] += 1
                i += 2 + arg_len
                continue

            if op in PRIVATE_START_OPS:
                contexts.append(
                    _Context(kind="private", start_op=op, payload=payload, parent=_current_parts(root_parts, contexts), start_offset=i)
                )
                stats["private_directives"] += 1
                i += 2 + arg_len
                continue

            if op in PRIVATE_END_OPS:
                ctx = _pop_context(contexts, "private")
                if ctx is not None:
                    private_directives.append(
                        {
                            "start_control": f"1f{ctx.start_op:02x}",
                            "end_control": f"1f{op:02x}",
                            "text_length": len("".join(ctx.text_parts)),
                        }
                    )
                i += 2 + arg_len
                continue

            if op in VERTICAL_HINT_OPS:
                stats["vertical_hints"] += 1
                i += 2 + arg_len
                continue

            if op in PRIVATE_RENDERER_DIRECTIVE_OPS:
                stats["renderer_private_directives"] += 1
                i += 2 + arg_len
                continue

            if op in KNOWN_NONPRINTING_CONTROLS:
                stats["nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            stats["unknown_controls"] += 1
            gaps.add(f"unknown_control_1f{op:02x}")
            i += 2 + arg_len
            continue

        if i + 1 < len(data) and 0x21 <= byte <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            text = decode_jis_pair(data[i : i + 2])
            if text:
                stats["jis_pairs"] += 1
                value = normalize_fullwidth_ascii(text) if halfwidth_depth else text
                _append_text(_current_parts(root_parts, contexts), value)
                text_parts = _current_text_parts(contexts)
                if text_parts is not None:
                    text_parts.append(value)
            else:
                stats["invalid_jis_pairs"] += 1
                gaps.add("invalid_jis_pair")
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= byte <= 0xFE:
            key = f"{byte:02x}{data[i + 1]:02x}"
            mapped = options.gaiji_map.get(key)
            if mapped:
                stats["gaiji_unicode"] += 1
                _append_text(_current_parts(root_parts, contexts), mapped)
                text_parts = _current_text_parts(contexts)
                if text_parts is not None:
                    text_parts.append(mapped)
            else:
                image_src = options.image_sources.get(key.lower())
                if image_src:
                    stats["gaiji_image"] += 1
                    _current_parts(root_parts, contexts).append(
                        f'<img class="lv-hc-gaiji lv-hc-gaiji-image" '
                        f'src="{_escape_attr(image_src)}" alt="{_escape_attr(key)}" '
                        f'data-gaiji-code="{_escape_attr(key)}">'
                    )
                else:
                    stats["gaiji_placeholder"] += 1
                    _current_parts(root_parts, contexts).append(
                        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
                        f'data-gaiji-code="{_escape_attr(key)}"></span>'
                    )
            i += 2
            continue

        stats["unknown_bytes"] += 1
        i += 1

    while style_stack:
        close_tag = _style_close_tag(style_stack.pop())
        if close_tag:
            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
    while contexts:
        ctx = contexts.pop()
        if ctx.kind == "private":
            private_directives.append(
                {
                    "start_control": f"1f{ctx.start_op:02x}",
                    "end_control": None,
                    "text_length": len("".join(ctx.text_parts)),
                    "status": "unterminated",
                }
            )
            gaps.add("unterminated_private_directive")
        else:
            ctx.parent.extend(ctx.parts)
            gaps.add(f"unterminated_{ctx.kind}")

    classes = ["lv-hc-render"]
    if options.vertical:
        classes.append("lv-hc-vertical")
    body = "".join(root_parts)
    rendered_html = f'<div class="{" ".join(classes)}">{body}</div>'
    return HcRenderResult(
        html=rendered_html,
        plain=_plain_from_html(rendered_html),
        stats={key: int(value) for key, value in sorted(stats.items())},
        links=tuple(links),
        media=tuple(media),
        audio=tuple(audio),
        private_directives=tuple(private_directives),
        named_behavior_gaps=tuple(sorted(gaps)),
    )


def _renderer_for_source(source: DictionarySource, *, compute_hash: bool = False) -> HcRendererClassification | None:
    exinfo = load_exinfo_for_idx(source.idx)
    html_dll = exinfo.general.get("HTMLDLL") if exinfo is not None else None
    if html_dll:
        candidate = source.idx.parent / html_dll
        if candidate.is_file():
            return classify_hc_renderer_file(candidate, compute_hash=compute_hash)
    candidates = discover_hc_renderer_files([source.idx.parent])
    return classify_hc_renderer_file(candidates[0], compute_hash=compute_hash) if candidates else None


def _renderer_behavior_gaps(renderer: HcRendererClassification | None) -> list[str]:
    if renderer is None:
        return ["no_hc_renderer_plugin_declared"]
    features = set(renderer.features)
    gaps: list[str] = []
    for feature, gap in (
        ("panel_hooks", "panel_hooks"),
        ("plugin_hooks", "plugin_hooks"),
        ("user_data_hooks", "user_data_hooks"),
        ("sql_hooks", "sql_search_or_helper_hooks"),
        ("headword_modifier", "modify_headword_hook"),
        ("custom_gaiji_dib", "custom_gaiji_bitmap_hook"),
    ):
        if feature in features:
            gaps.append(gap)
    return sorted(gaps)


def _schema_backed_sidecars(source: DictionarySource) -> dict[str, Any]:
    exinfo = load_exinfo_for_idx(source.idx)
    rows = discover_sqlite_sidecars(source.idx, exinfo)
    renderer_sidecars = discover_renderer_sidecars(source.idx, exinfo)
    role_counts = Counter(row.role for row in rows)
    supported_roles = {
        "sqlite_renderer_body",
        "sqlite_renderer_body_with_media",
        "sqlite_row_ordered_honbun_renderer_body",
        "sqlite_honbun_data_id_body",
        "sqlite_block_offset_body",
        "sqlite_media_store",
    }
    clear_search_roles = {
        "sqlite_search_index",
        "sqlite_category_search_index",
        "sqlite_search_or_fulltext",
        "sqlite_search_or_conjugation",
    }
    sidecar_rows = [
        {
            "name": row.path.name,
            "storage": row.storage,
            "content_kind": row.content_kind,
            "role": row.role,
            "tables": list(row.tables),
            "hc_render_support": (
                "entry_body_or_media" if row.role in supported_roles else "classified_search_helper" if row.role in clear_search_roles else "classified_only"
            ),
        }
        for row in rows
    ]
    seen = {str(row.path.resolve()) for row in rows}
    for sidecar in renderer_sidecars:
        resolved = str(sidecar.path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        role = "sqlite_renderer_body"
        content_kind = "sqlite"
        tables: list[dict[str, Any]] = []
        if sidecar.path.name.lower().startswith("vlpljbl"):
            classified = classify_vlpljbl_file(sidecar.path, inspect_sqlite=True, compute_hash=False)
            role = classified.role
            content_kind = classified.content_kind
            tables = list(classified.sqlite_tables)
        role_counts[role] += 1
        sidecar_rows.append(
            {
                "name": sidecar.path.name,
                "storage": sidecar.storage,
                "content_kind": content_kind,
                "role": role,
                "tables": tables,
                "hc_render_support": (
                    "entry_body_or_media" if role in supported_roles else "classified_search_helper" if role in clear_search_roles else "classified_only"
                ),
            }
        )
    return {
        "sidecars": sidecar_rows,
        "role_counts": dict(sorted(role_counts.items())),
    }


def _has_entry_body_sidecar(schema_sidecars: dict[str, Any]) -> bool:
    return any(
        row.get("hc_render_support") == "entry_body_or_media"
        for row in schema_sidecars.get("sidecars", [])
        if isinstance(row, dict)
    )


def _render_raw_honmon_entries(source: DictionarySource, dict_out: Path, args: argparse.Namespace) -> dict[str, Any]:
    renderer = getattr(args, "_hc_renderer", None)
    reader = SsedRandomReader(source.honmon)
    entries_path = dict_out / "hc_entries.jsonl"
    html_path = dict_out / "hc_entries.html"
    options = HcRenderOptions(
        gaiji_map=source.gaiji_map,
        image_sources=source.image_sources or {},
        renderer_code=renderer.code if renderer is not None else None,
        vertical=bool(getattr(args, "vertical", False)),
    )
    totals: Counter[str] = Counter()
    emitted = 0
    named_gaps: Counter[str] = Counter()
    with entries_path.open("w", encoding="utf-8") as jsonl, html_path.open("w", encoding="utf-8") as html_out:
        html_out.write("<!doctype html><meta charset=\"utf-8\">\n")
        for entry_index, (start, end) in enumerate(iter_entry_slices_reader(reader), start=1):
            if args.limit is not None and emitted >= args.limit:
                break
            body = reader.read(start, end - start)
            result = render_hc_body(body, options)
            totals.update(result.stats)
            named_gaps.update(result.named_behavior_gaps)
            record = {
                "dict_id": source.dict_id,
                "entry_index": entry_index,
                "start_offset": start,
                "end_offset": end,
                **result.as_dict(include_html=args.include_html),
            }
            jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")
            html_out.write(f"<!-- {source.dict_id} #{entry_index} -->\n{result.html}\n")
            emitted += 1
    return {
        "raw_honmon_entries_emitted": emitted,
        "raw_honmon_entries_path": str(entries_path),
        "raw_honmon_html_path": str(html_path),
        "raw_honmon_stats": dict(sorted(totals.items())),
        "raw_honmon_named_behavior_gaps": dict(sorted(named_gaps.items())),
    }


def _rendererdb_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        decrypted_db=None,
        include_html=True,
        write_media=bool(args.write_sidecar_media),
        media_limit=args.media_limit,
        write_ziptomedia=bool(args.write_ziptomedia),
        ziptomedia_limit=args.ziptomedia_limit,
        limit=args.limit,
    )


def extract_hc_render_for_source(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    renderer = _renderer_for_source(source, compute_hash=not args.no_hash)
    setattr(args, "_hc_renderer", renderer)
    renderer_json = hc_renderer_classification_to_json(renderer) if renderer else None
    status(args, f"hc-render: {source.dict_id}: rendering raw HONMON controls", verbose=True)
    raw_summary = _render_raw_honmon_entries(source, dict_out, args)
    schema_sidecars = _schema_backed_sidecars(source)
    rendererdb_summary = None
    uses_renderer_sidecar_body = _has_entry_body_sidecar(schema_sidecars)
    if args.compare_rendererdb or uses_renderer_sidecar_body:
        status(args, f"hc-render: {source.dict_id}: resolving renderer SQLite/body sidecars", verbose=True)
        rendererdb_summary = extract_rendererdb_dictionary(source, dict_out / "rendererdb", _rendererdb_args(args))
    profile = build_hc_behavior_profile(
        renderer,
        schema_sidecars=schema_sidecars,
        rendererdb_summary=rendererdb_summary,
        raw_gaps=raw_summary.get("raw_honmon_named_behavior_gaps", {}),
    )
    behavior_gaps = _renderer_behavior_gaps(renderer)
    behavior_gaps.extend(raw_summary.get("raw_honmon_named_behavior_gaps", {}).keys())
    summary = {
        "schema": "logovista-hc-render-summary-v1",
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "hc_renderer": renderer_json,
        "schema_backed_sidecars": schema_sidecars,
        "behavior_profile": profile.as_dict(),
        "exact_body_strategy": profile.body_strategy,
        "common_semantics": {
            "controls": [
                "SSED text and style controls",
                "internal address links",
                "COLSCR media placeholders",
                "PCMDATA audio ranges",
                "Unicode/image gaiji fallback",
                "private renderer directive suppression",
                "vertical-rendering hints as metadata/classes",
            ],
            "sidecars": [
                "t_contents",
                "HONBUN",
                "Android-style body SQLite tables",
                "media/t_media blob tables",
                "ziptomedia references",
            ],
            "exact_body_html_available": profile.exact_body_html_available,
            "exact_hc_parity": profile.exact_hc_parity,
        },
        **raw_summary,
        "rendererdb_comparison": rendererdb_summary,
        "named_behavior_gaps": sorted(set(behavior_gaps) | set(profile.named_gaps)),
    }
    (dict_out / "hc_render_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _source_task(payload: tuple[DictionarySource, Path, argparse.Namespace]) -> dict[str, Any]:
    source, out_dir, args = payload
    return extract_hc_render_for_source(source, out_dir, args)


def extract_hc_render_for_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(
        args.root or [Path(".")],
        jobs=getattr(args, "jobs", 1),
        dict_ids=args.dict,
        include_gaiji=True,
        include_images=True,
    )
    if not sources:
        raise ValueError("hc-render: no SSED dictionaries found")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    task_args = worker_args(args)

    def log_summary(row: dict[str, Any]) -> None:
        stats = row.get("raw_honmon_stats") or {}
        media_count = int(stats.get("media_placeholders", 0) or 0)
        audio_count = int(stats.get("audio_links", 0) or 0)
        print(
            f"{row['dict_id']:12s} hc_entries={row.get('raw_honmon_entries_emitted', 0):5d} "
            f"links={stats.get('links', 0):5d} media={media_count:5d} audio={audio_count:5d} "
            f"gaps={len(row.get('named_behavior_gaps') or [])}",
            file=sys.stderr,
        )

    rows = parallel_map_ordered(
        _source_task,
        [(source, args.out_dir, task_args) for source in sources],
        jobs=getattr(args, "jobs", 1),
        on_result=log_summary,
    )
    (args.out_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def add_hc_render_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory, package directory, or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-hc-render"))
    parser.add_argument("--dict", action="append", help="Only render matching dictionary id(s).")
    parser.add_argument("--limit", type=int, default=20, help="Limit rendered raw HONMON entries per dictionary.")
    parser.add_argument("--no-html", dest="include_html", action="store_false", help="Omit per-entry HTML from JSONL rows.")
    parser.add_argument("--vertical", action="store_true", help="Apply vertical-rendering class hints to raw body HTML.")
    parser.add_argument("--compare-rendererdb", action="store_true", help="Also run renderer SQLite/body-sidecar comparison.")
    parser.add_argument("--write-sidecar-media", action="store_true", help="When comparing rendererdb, write supported media blobs.")
    parser.add_argument("--media-limit", type=int, help="Limit sidecar media blobs written during rendererdb comparison.")
    parser.add_argument("--write-ziptomedia", action="store_true", help="When comparing rendererdb, decrypt/write ziptomedia sound files.")
    parser.add_argument("--ziptomedia-limit", type=int, help="Limit ziptomedia files written during rendererdb comparison.")
    parser.add_argument("--no-hash", action="store_true", help="Skip SHA-256 calculation for HC plugin classification.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    parser.set_defaults(include_html=True)
