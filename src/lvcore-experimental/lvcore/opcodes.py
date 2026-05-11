"""lvcore-local SSED control behavior atlas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OpcodeCategory(str, Enum):
    TEXT = "text"
    LINE_BREAK = "line_break"
    PARAGRAPH = "paragraph"
    STYLE = "style"
    PRIVATE_DIRECTIVE = "private_directive"
    LITERAL = "literal"
    URL = "url"
    TAB = "tab"
    MEDIA_LAYOUT = "media_layout"
    LINK = "link"
    EXTENDED_LINK = "extended_link"
    MEDIA = "media"
    GAIJI = "gaiji"
    RUBY = "ruby"
    TABLE = "table"
    LIST = "list"
    UNKNOWN = "unknown"


class FriendlyVisibility(str, Enum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    CONTROL_ONLY = "control_only"
    RESOURCE_HINT = "resource_hint"


class PlainTextBehavior(str, Enum):
    CONTENTS = "contents"
    NEWLINE = "newline"
    HIDE = "hide"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True)
class OpcodeBehavior:
    code: int
    category: OpcodeCategory
    semantic_name: str
    argument_shape: str
    argument_length: int = 0
    friendly_visibility: FriendlyVisibility = FriendlyVisibility.CONTROL_ONLY
    plain_text_behavior: PlainTextBehavior = PlainTextBehavior.HIDE
    debug_behavior: str = "show control"
    diagnostic_code: str | None = None
    confidence: str = "observed"
    tag: str | None = None
    pair_code: int | None = None
    pair_role: str = "none"


def _behavior(
    code: int,
    category: OpcodeCategory,
    semantic_name: str,
    argument_shape: str = "none",
    *,
    argument_length: int = 0,
    friendly_visibility: FriendlyVisibility = FriendlyVisibility.CONTROL_ONLY,
    plain_text_behavior: PlainTextBehavior = PlainTextBehavior.HIDE,
    debug_behavior: str = "show control",
    diagnostic_code: str | None = None,
    confidence: str = "observed",
    tag: str | None = None,
    pair_code: int | None = None,
    pair_role: str = "none",
) -> OpcodeBehavior:
    return OpcodeBehavior(
        code=code,
        category=category,
        semantic_name=semantic_name,
        argument_shape=argument_shape,
        argument_length=argument_length,
        friendly_visibility=friendly_visibility,
        plain_text_behavior=plain_text_behavior,
        debug_behavior=debug_behavior,
        diagnostic_code=diagnostic_code,
        confidence=confidence,
        tag=tag,
        pair_code=pair_code,
        pair_role=pair_role,
    )


OPCODE_BEHAVIORS: dict[int, OpcodeBehavior] = {
    0x00: _behavior(0x00, OpcodeCategory.TEXT, "neutral/no-op control", confidence="low"),
    0x02: _behavior(0x02, OpcodeCategory.PARAGRAPH, "entry/wrapper start", tag="wrapper", pair_code=0x03, pair_role="start"),
    0x03: _behavior(0x03, OpcodeCategory.PARAGRAPH, "entry/wrapper end", tag="wrapper", pair_code=0x02, pair_role="end"),
    0x04: _behavior(
        0x04,
        OpcodeCategory.TEXT,
        "halfwidth conversion start",
        friendly_visibility=FriendlyVisibility.CONTROL_ONLY,
        plain_text_behavior=PlainTextBehavior.CONTENTS,
        tag="halfwidth",
        pair_code=0x05,
        pair_role="start",
        confidence="high",
    ),
    0x05: _behavior(0x05, OpcodeCategory.TEXT, "halfwidth conversion end", tag="halfwidth", pair_code=0x04, pair_role="end", confidence="high"),
    0x06: _behavior(0x06, OpcodeCategory.STYLE, "subscript start", tag="sub", pair_code=0x07, pair_role="start"),
    0x07: _behavior(0x07, OpcodeCategory.STYLE, "subscript end", tag="sub", pair_code=0x06, pair_role="end"),
    0x09: _behavior(0x09, OpcodeCategory.PARAGRAPH, "section/entry marker", "u16", argument_length=2, tag="section", confidence="high"),
    0x0A: _behavior(0x0A, OpcodeCategory.LINE_BREAK, "line break", plain_text_behavior=PlainTextBehavior.NEWLINE, tag="break", confidence="high"),
    0x0B: _behavior(0x0B, OpcodeCategory.LITERAL, "literal/preformatted start", tag="literal", pair_code=0x0C, pair_role="start"),
    0x0C: _behavior(0x0C, OpcodeCategory.LITERAL, "literal/preformatted end", tag="literal", pair_code=0x0B, pair_role="end"),
    0x0E: _behavior(0x0E, OpcodeCategory.STYLE, "superscript start", tag="sup", pair_code=0x0F, pair_role="start"),
    0x0F: _behavior(0x0F, OpcodeCategory.STYLE, "superscript end", tag="sup", pair_code=0x0E, pair_role="end"),
    0x10: _behavior(0x10, OpcodeCategory.STYLE, "italic start", tag="italic", pair_code=0x11, pair_role="start"),
    0x11: _behavior(0x11, OpcodeCategory.STYLE, "italic end", tag="italic", pair_code=0x10, pair_role="end"),
    0x12: _behavior(0x12, OpcodeCategory.STYLE, "emphasis start", tag="em", pair_code=0x13, pair_role="start"),
    0x13: _behavior(0x13, OpcodeCategory.STYLE, "emphasis end", tag="em", pair_code=0x12, pair_role="end"),
    0x1A: _behavior(
        0x1A,
        OpcodeCategory.TAB,
        "tab/column positioning",
        "u16",
        argument_length=2,
        diagnostic_code="tab_column_control",
        tag="tab_column",
        confidence="medium",
    ),
    0x1C: _behavior(
        0x1C,
        OpcodeCategory.MEDIA_LAYOUT,
        "media layout directive",
        "u16",
        argument_length=2,
        friendly_visibility=FriendlyVisibility.RESOURCE_HINT,
        diagnostic_code="media_layout_control",
        tag="media_layout",
        confidence="medium",
    ),
    0x3B: _behavior(0x3B, OpcodeCategory.URL, "URL span start", tag="url", pair_code=0x5B, pair_role="start", confidence="high"),
    0x5B: _behavior(0x5B, OpcodeCategory.URL, "URL span end", tag="url", pair_code=0x3B, pair_role="end"),
    0x41: _behavior(0x41, OpcodeCategory.STYLE, "headword/title span start", "u16", argument_length=2, tag="head", pair_code=0x61, pair_role="start"),
    0x61: _behavior(0x61, OpcodeCategory.STYLE, "headword/title span end", tag="head", pair_code=0x41, pair_role="end"),
    0x42: _behavior(0x42, OpcodeCategory.LINK, "body/cross-reference link start", tag="link", pair_code=0x62, pair_role="start"),
    0x62: _behavior(0x62, OpcodeCategory.LINK, "body/cross-reference link end", "bcd-address", argument_length=6, tag="link", pair_code=0x42, pair_role="end"),
    0x43: _behavior(0x43, OpcodeCategory.LINK, "menu/navigation link start", tag="link", pair_code=0x63, pair_role="start"),
    0x63: _behavior(0x63, OpcodeCategory.LINK, "menu/navigation link end", "bcd-address", argument_length=6, tag="link", pair_code=0x43, pair_role="end"),
    0x44: _behavior(0x44, OpcodeCategory.EXTENDED_LINK, "extended link start", "extended-link-start", argument_length=10, tag="link", pair_code=0x64, pair_role="start"),
    0x64: _behavior(0x64, OpcodeCategory.EXTENDED_LINK, "extended link end", "bcd-address", argument_length=6, tag="link", pair_code=0x44, pair_role="end"),
    0x49: _behavior(0x49, OpcodeCategory.LINK, "TOC/internal link start", "link-start", argument_length=10, tag="link", pair_code=0x69, pair_role="start"),
    0x69: _behavior(0x69, OpcodeCategory.LINK, "TOC/internal link end", tag="link", pair_code=0x49, pair_role="end"),
    0x4A: _behavior(0x4A, OpcodeCategory.EXTENDED_LINK, "jump/audio range start", "jump-range", argument_length=16, tag="link", pair_code=0x6A, pair_role="start"),
    0x6A: _behavior(0x6A, OpcodeCategory.EXTENDED_LINK, "jump/audio range end", tag="link", pair_code=0x4A, pair_role="end"),
    0x4D: _behavior(
        0x4D,
        OpcodeCategory.MEDIA,
        "inline media/reference start",
        "media-descriptor",
        argument_length=18,
        friendly_visibility=FriendlyVisibility.RESOURCE_HINT,
        diagnostic_code="unresolved_media_ref",
        tag="media",
        pair_code=0x6D,
        pair_role="start",
        confidence="high",
    ),
    0x6D: _behavior(0x6D, OpcodeCategory.MEDIA, "media/reference end", tag="media", pair_code=0x4D, pair_role="end"),
    0xE0: _behavior(0xE0, OpcodeCategory.STYLE, "bold start", "u16", argument_length=2, tag="bold", pair_code=0xE1, pair_role="start"),
    0xE1: _behavior(0xE1, OpcodeCategory.STYLE, "bold end", tag="bold", pair_code=0xE0, pair_role="end"),
    0xE2: _behavior(
        0xE2,
        OpcodeCategory.PRIVATE_DIRECTIVE,
        "private renderer directive start",
        "u16",
        argument_length=2,
        friendly_visibility=FriendlyVisibility.HIDDEN,
        diagnostic_code="private_renderer_directive",
        tag="private",
        pair_code=0xE3,
        pair_role="start",
        confidence="high",
    ),
    0xE3: _behavior(
        0xE3,
        OpcodeCategory.PRIVATE_DIRECTIVE,
        "private renderer directive end",
        friendly_visibility=FriendlyVisibility.HIDDEN,
        diagnostic_code="private_renderer_directive",
        tag="private",
        pair_code=0xE2,
        pair_role="end",
        confidence="high",
    ),
}

CONTROL_ARG_LENGTHS = {code: behavior.argument_length for code, behavior in OPCODE_BEHAVIORS.items() if behavior.argument_length}

START_TAGS = {
    code: behavior.tag
    for code, behavior in OPCODE_BEHAVIORS.items()
    if behavior.tag and behavior.pair_role == "start"
}
END_TAGS = {
    code: behavior.tag
    for code, behavior in OPCODE_BEHAVIORS.items()
    if behavior.tag and behavior.pair_role == "end"
}
SEMANTIC_CONTROL_TAGS = {
    code: behavior.tag
    for code, behavior in OPCODE_BEHAVIORS.items()
    if behavior.tag and behavior.pair_role == "none"
}
KNOWN_NEUTRAL_OPS = {0x00, 0x02, 0x03}


def behavior_for(code: int | None) -> OpcodeBehavior | None:
    if code is None:
        return None
    return OPCODE_BEHAVIORS.get(code)


def tag_for(code: int) -> str | None:
    behavior = behavior_for(code)
    return behavior.tag if behavior is not None else None


def is_known_opcode(code: int) -> bool:
    return code in OPCODE_BEHAVIORS
