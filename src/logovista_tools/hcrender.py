"""Common HC HTML renderer semantics for SSED body streams.

This module intentionally implements the shared renderer behavior visible
across HC????.dll plugins. Product-specific hooks remain classified metadata;
they are not treated as exact renderer parity.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
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
from .resources import candidate_package_roots
from .ssed import resolve_case_insensitive_path
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

HC_CSS_DEFAULTS = {
    "$font-family-Jpn$": '"Yu Mincho", "Hiragino Mincho ProN", "Meiryo", serif',
    "$body-font-family$": '"Yu Mincho", "Hiragino Mincho ProN", "Meiryo", serif',
    "$midashi-font-family$": '"Yu Mincho", "Hiragino Mincho ProN", "Meiryo", serif',
    "$font-family-Eng$": '"Times New Roman", "Arial", sans-serif',
    "$ref-font-family$": '"Yu Gothic", "Meiryo", sans-serif',
    "$Jpn-font-size$": "16px",
    "$body-font-size$": "16px",
    "$Eng-font-size$": "0.95em",
    "$ref-font-size$": "1em",
    "$midashi-font-size$": "1.35em",
    "$midashi-bold$": "bold",
    "$body-color$": "#111111",
    "$midashi-color$": "#111111",
    "$body-bgcolor$": "#ffffff",
    "$ref-color$": "#0645ad",
    "$line-height$": "1.65",
    "$body_vertical_height$": "640",
    "$body_vertical_width$": "480",
}

HC_RENDER_BASE_CSS = """
.lv-hc-render {
  margin: 0.75rem 0 1.25rem;
  line-height: 1.65;
}
.lv-hc-render .lv-hc-heading {
  display: block;
}
.lv-hc-render .lv-hc-section {
  display: none;
}
.lv-hc-render img {
  max-height: 1.4em;
  vertical-align: middle;
}
.lv-hc-render .lv-hc-gaiji-placeholder {
  display: inline-block;
  min-width: 1em;
  min-height: 1em;
  border: 1px solid #999;
  vertical-align: -0.15em;
}
.lv-hc-render .lv-hc-link,
.lv-hc-render .lv-hc-audio {
  text-decoration: none;
}
""".strip()


HC00A0_HEADER_FALLBACK = """
<table width="100%" border="0" cellpadding="10" cellspacing="0">
<tr style="display:$$DISPLAY_MIDASHI$$;">
<td colspan="2">
<span class="title">$$MIDASHI$$</span>$$SUBMIDASHI$$
</td>
<td rowspan="2" style="width:48px;vertical-align:top">
<img id="cmdPlay" alt="" height="48px" src="play.gif" onclick="lvedPlayAll();" style="display:inline;">
<img id="cmdStop" alt="" height="48px" src="pause.gif" onclick="lvedStop();" style="display:none;">
</td>
</tr>
<tr height="25px">
<td><img src="ej-$$MODE_EJ$$.png" height="25px" width="55px" name="ej" id="ej">
<img src="eng-$$MODE_E$$.png" height="25px" width="55px" name="eng" id="eng">
<img src="jpn-$$MODE_J$$.png" height="25px" width="55px" name="jpn" id="jpn"></td>
<td valign="bottom" style="text-align:right">
<img src="al-$$MODE_ALL$$.png" height="25px" width="50px" name="al" id="al">
<img src="ok-$$MODE_OK$$.png" height="25px" width="50px" name="ok" id="ok">
<img src="qu-$$MODE_QU$$.png" height="25px" width="50px" name="qu" id="qu">
<img src="ex-$$MODE_EX$$.png" height="25px" width="50px" name="ex" id="ex"></td>
</tr>
</table>
""".strip()


HC00A0_DETAIL_FALLBACK = """
<div style="border-bottom:thin black solid;" id="$$DETAIL_NO$$">
<span id="mp3" style="display:none;">$$DETAIL_PLAY$$</span>
<span style="display:none;" id="LINE$$LINE_NO$$"></span>
<div style="line-height:3px;">&nbsp;</div>
<table class="main" id="detail" style="display:$$DISPLAY_DETAIL$$;">
<tr>
<td width="32px;"><img height="32px" src="sound.png" width="32px" onclick="lvedPlay('$$DETAIL_NO$$','$$DETAIL_PLAY$$');"></td>
<td width="32px;"><img height="22px" src="eng.png" width="32px" id="eng_check_$$DETAIL_NO$$"></td>
<td class="english" id="english" style="display:$$DISPLAY_ENGLISH$$;">$$ENGLISH$$</td>
</tr>
<tr>
<td width="32px;"></td>
<td width="32px;"><img height="22px" src="jpn.png" width="32px" id="jpn_check_$$DETAIL_NO$$"></td>
<td class="japanese" id="japanese" style="display:$$DISPLAY_JAPANESE$$;">$$JAPANESE$$</td>
</tr>
</table>
<div id="check">
<img height="20" src="ok-$$DETAIL_OK$$.png" id="ok_check_$$DETAIL_NO$$">&nbsp;
<img height="20" src="ex-$$DETAIL_EX$$.png" id="ex_check_$$DETAIL_NO$$">&nbsp;&nbsp;&nbsp;
<img height="20" src="play.png" style="cursor:default;"><span id="$$DETAIL_NO$$_playcount">$$DETAIL_PLAYNUM$$</span>回
</div>
</div>
""".strip()


@dataclass(frozen=True)
class HcRenderOptions:
    """Options for shared HC-style rendering."""

    gaiji_map: dict[str, str] = field(default_factory=dict)
    image_sources: dict[str, str] = field(default_factory=dict)
    html_templates: dict[str, str] = field(default_factory=dict)
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


@dataclass(frozen=True)
class _RendererGaijiRule:
    html: str
    close_code: str | None = None
    close_html: str = "</span>"
    render_self: bool = False


@dataclass(frozen=True)
class _RendererImageGaijiRule:
    css_class: str


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

HC0157_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    # HC0157 uses selected gaiji-plane values as CSS/style delimiters.  A few
    # markers also render their own custom glyph/image inside the opened span.
    "b156": _RendererGaijiRule('<span class="midashi_word">', "b22a"),
    "b157": _RendererGaijiRule('<span class="midashi_word red">', "b22a", render_self=True),
    "b158": _RendererGaijiRule('<span class="midashi_word red">', "b22a", render_self=True),
    "b15a": _RendererGaijiRule('<span class="hatsuon">', "b15b"),
    "b15c": _RendererGaijiRule('<span class="hinshi_ej">', "b15d"),
    "b160": _RendererGaijiRule('<span class="text_yourei_en">', "b161"),
    "b162": _RendererGaijiRule('<span class="text_seiku_en">', "b163"),
    "b164": _RendererGaijiRule('<span class="text_hasei_en">', "b165"),
    "b166": _RendererGaijiRule('<span class="text_henka_en">', "b167"),
    "b168": _RendererGaijiRule('<span class="ico_henka">', "b169"),
    "b16a": _RendererGaijiRule('<span class="ico_jisei">', "b16b"),
    "b16e": _RendererGaijiRule('<span class="ico_senmon">', "b16f"),
    "b170": _RendererGaijiRule('<span class="ico_yurai">', "b171"),
    "b172": _RendererGaijiRule('<span class="shiyougroup">', "b173", render_self=True),
    "b174": _RendererGaijiRule('<span class="shiyou">', "b175"),
    "b176": _RendererGaijiRule('<span class="bunsu">', "b177"),
    "b178": _RendererGaijiRule('<span class="bunshi"><sup>', "b179", "</sup></span>"),
    "b17a": _RendererGaijiRule('<span class="bunbo"><sub>', "b17b", "</sub></span>"),
    "b17c": _RendererGaijiRule('<span class="ruby">', "b17d"),
    "b221": _RendererGaijiRule('<span class="smallcap">', "b222"),
    "b223": _RendererGaijiRule('<span class="serifbold">', "b224"),
    "b225": _RendererGaijiRule('<span class="normalfont">', "b226"),
    "b228": _RendererGaijiRule('<span class="jerank0">', "b22a"),
    "b229": _RendererGaijiRule('<span class="jerank1">', "b22a"),
    "b23c": _RendererGaijiRule('<span class="kyousei">', "b23d"),
    "b23e": _RendererGaijiRule('<span class="hinshi_je">', "b23f"),
    "b240": _RendererGaijiRule('<sup><span class="jousu">', "b241", "</span></sup>"),
}

HC0157_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC0157_OPEN_MARKERS.values() if rule.close_code)
HC0157_SELF_RENDERING_CLOSE_MARKERS = {"b173"}
HC0157_STANDALONE_MARKERS = {
    "a14d": '<span class="accent">&#x0300;</span>',
    "a14e": '<span class="accent">&#x0301;</span>',
}
HC0157_NOOP_MARKERS = {"b17e"}
HC0157_RED_GAIJI_RANGE = range(0xB22D, 0xB23C)

HC0158_OPEN_MARKERS: dict[str, tuple[str, str, str]] = {
    # These B3xx values are not display glyphs for HC0158. The DLL maps them
    # directly to CSS spans while normal image gaiji such as B253/B347 still
    # flow through the SVG/custom-character path.
    "b353": ('<span class="rank4">', "b354", "</span>"),
    "b355": ('<span class="rank1"><sup>&#x2605;&#x2605;</sup>', "b354", "</span>"),
    "b356": ('<span class="rank2">', "b354", "</span>"),
    "b357": ('<span class="rank3">', "b354", "</span>"),
    "b360": ('<span class="midashi_kana">', "b361", "</span>"),
    "b362": ('<span class="meishi_kana_rekishi">', "b363", "</span>"),
    "b364": ('<span class="meishi_kana">', "b365", "</span>"),
    "b366": ('<span class="iso">', "b367", "</span>"),
    "b368": ('<span class="hinshi">', "b369", "</span>"),
    "b36a": ('<span class="gogi">', "b36b", "</span>"),
    "b36c": ('<span class="katsuyou">', "b36d", "</span>"),
    "b36e": ('<span class="gogen">', "b36f", "</span>"),
    "b370": ('<span class="seibotsu">', "b371", "</span>"),
    "b372": ('<span class="waka_haiku">', "b373", "</span>"),
    "b375": ('<span class="red">', "b376", "</span>"),
    "b37b": ('<span class="small">', "b37c", "</span>"),
    "b37d": ('<span class="underline">', "b37e", "</span>"),
}

HC0158_CLOSE_MARKERS = {
    "b354",
    "b361",
    "b363",
    "b365",
    "b367",
    "b369",
    "b36b",
    "b36d",
    "b36f",
    "b371",
    "b373",
    "b376",
    "b37a",
    "b37c",
    "b37e",
}

HC0158_NOOP_MARKERS = {"b358", "b359", "b35a", "b35b", "b35c", "b35d", "b35e", "b35f"}

HC0146_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    # HC0146 maps this pair to a color-font span. The close side is an
    # explicit </font> branch in the renderer loop; the open pointer is set up
    # by the renderer's template globals and matches 00000146.css.
    "b232": _RendererGaijiRule('<font class="color_font">', "b233", "</font>"),
}
HC0146_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC0146_OPEN_MARKERS.values() if rule.close_code)
HC0146_NOOP_MARKERS = {
    # Decompilation routes these marker codes to the same nonprinting path as
    # consumed controls. They are renderer state/template selectors, not glyphs.
    "b236",
    "b237",
    "b241",
    "b44f",
    "b450",
    "b451",
    "b457",
    "b458",
    "b459",
    "b45a",
}
HC0146_LITERAL_MARKERS = {
    "b240": "略：",
}
HC0146_IMAGE_MARKERS: dict[str, _RendererImageGaijiRule] = {
    **{f"b{value:03x}": _RendererImageGaijiRule("img_mark4") for value in range(0x157, 0x15A)},
    **{f"b{value:03x}": _RendererImageGaijiRule("gaiji_icon") for value in range(0x25A, 0x352)},
    "b23b": _RendererImageGaijiRule("gaiji_full"),
    **{f"b{value:03x}": _RendererImageGaijiRule("gaiji_full") for value in range(0x357, 0x425)},
}

HC00C6_SECTION_DIVS = {
    "0001": '<div class="midashi">',
    "0002": '<div class="midashi_JE">',
    "0003": '<div class="midashi_JE">',
    "0004": '<div class="honbun" style="margin-left:1.000000em;">',
    "0006": '<div class="yakugo" style="margin-left:2.000000em;">',
    "0007": '<div class="contents">',
    "0008": '<div class="exampleyakugo">',
    "0009": '<div class="honbun" style="margin-left:1.000000em;">',
    "000f": '<div class="honbun" style="margin-left:1.000000em;">',
}

HC00C6_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    # The DLL template string is a <div>, but the product stylesheet makes
    # .partwaku display:inline and these markers often occur inside active
    # inline style spans.  The toolkit emits a span to preserve the observed
    # renderer box without producing invalid standalone HTML.
    "a23c": _RendererGaijiRule('<span class="partwaku">', "a23d", "</span>"),
    "a24c": _RendererGaijiRule('<span class="partwaku">', "a24d", "</span>"),
}
HC00C6_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC00C6_OPEN_MARKERS.values() if rule.close_code)
HC00C6_NOOP_MARKERS = {
    "a238",
    "a239",
    "a23a",
    "a23b",
    "a23e",
    "a23f",
    "a240",
    "a241",
    "a242",
    "a243",
    "a245",
    "a250",
    "a253",
    "a254",
}
HC00C6_IMAGE_MARKERS = frozenset(
    {
        *(f"a{value:03x}" for value in range(0x122, 0x238)),
        "b121",
        "b122",
        "b123",
        "b124",
        "b125",
        "b127",
        "b128",
        "b129",
        "b12b",
        "b12c",
        "b12d",
        "b12f",
        "b130",
        "b150",
        "b151",
        "b152",
        "b153",
        "b168",
        "b252",
        "b260",
    }
)

HC00A6_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x6D}

HC009B_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}

HC_HKDKSR_MEDICAL_RENDERERS = {"014A", "02C3"}
HC_HKDKSR_MEDICAL_NONPRINTING_CONTROL_OPS = {0x6D}


def _hc00a6_honbun_div(indent: int) -> str:
    return f'<div class="honbun" style="margin-left:{indent:.6f}em;">'


def _hc00a6_section_parts(code: str, *, vertical: bool) -> tuple[list[str], str | None]:
    try:
        value = int(code, 16)
    except ValueError:
        value = 0
    if value == 1:
        return ['<div class="midashi">'], "</div>"
    if value == 2:
        return ['<div class="midashi_kana">'], "</div>"
    if value == 3:
        return ['<div class="midashi_eng">'], "</div>"
    if value == 4:
        return ['<div style="color:#990000; font-weight:bold; margin-left:1.0em;">'], "</div>"
    if value == 6:
        return ['<div style="color:#990000; margin-left:1.2em;">'], "</div>"
    if value == 10:
        return ['<div class="chosha">'], "</div>"
    if value == 11:
        return [f'<div class="image_caption" style="margin-left:{value:.6f}em;">'], "</div>"
    if value == 20:
        suffix = "V" if vertical else "H"
        return ['<div class="header">', f'<img src="b12f{suffix}.gif" class="img_mark2">'], "</div>"
    return [_hc00a6_honbun_div(value)], "</div>"


def _hc009b_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        return int(code, 16)
    except ValueError:
        return None


def _hc009b_section_parts(code: str, *, vertical: bool) -> tuple[list[str], str | None, str | None]:
    value = _hc009b_section_value(code)
    if value is None:
        return [], None, None
    if value == 0x0C:
        return ['<div class="header">'], "</div>", "header"
    margin_prop = "margin-left" if vertical else "margin-top"
    return [f'<div class="honbun" style="{margin_prop}:{value << 2}px">'], "</div>", "honbun"


def _hc_hkdksr_medical_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        if all(ch.isdigit() for ch in code):
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc_hkdksr_medical_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    """Return the decoded HKDKSR medical section wrapper for the subset.

    HC014A and HC02C3 use the section payload as a decimal-coded class/state
    value in several branches: for example body bytes ``00 40`` map to CSS
    class ``indent40``.  Non-decimal payloads such as ``002a`` still use their
    raw numeric value for table-state controls.
    """

    value = _hc_hkdksr_medical_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return [], None, "state_only"
    if value == 3:
        return ['<div style="font-size:1.0em; margin-top:13px;">'], "</div>", "body"
    if value == 4:
        return ['<div class="title3">'], "</div>", "title3"
    if value == 6:
        return ['<span class="med">'], "</span>", "med"
    if value == 8:
        return ['<div class="medblk">'], "</div>", "medblk"
    if value == 10:
        return ['<div class="medprice">'], "</div>", "medprice"
    if value == 11:
        return ['<div class="medimage">'], "</div>", "medimage"
    if value in {14, 15, 16}:
        return [f'<div class="mednamelist{value - 13}">'], "</div>", "mednamelist"
    if value == 42:
        return ['<table class="table_pc"><tr class="tr_pc"><td class="td_pc1">'], None, "table_open"
    if value == 43:
        return ['</td><td class="td_pc2">'], "</td></tr></table>", "table_cell"
    if value in {40, 41} or 50 <= value < 70:
        return [f'<div class="indent{value}">'], "</div>", "indent"
    if value == 46:
        return ['<div><span class="clickmenu">'], "</span></div>", "clickmenu"
    if value == 47:
        return ['<div style="display:none;">'], "</div>", "hidden_field"
    if value == 48:
        return ["</div>"], None, "field_close"
    return [f'<div style="margin-left:{value * 4}px;">'], "</div>", "indented_fallback"


def _private_directive_text(text: str) -> str:
    cleaned = "".join(ch for ch in text if ord(ch) >= 0x20)
    return normalize_fullwidth_ascii(cleaned).replace("：", ":").replace("⦿", ":")


HC02BE_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    "b928": _RendererGaijiRule('<font class="hatsuon">', "b929", "</font>"),
    "b92c": _RendererGaijiRule('<span class="yomigana">（', "b92d", "）</span>"),
}
HC02BE_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC02BE_OPEN_MARKERS.values() if rule.close_code)
HC02BE_LITERAL_MARKERS = {
    "b926": "（",
    "b927": "）",
    "a154": '<span class="smallcap">U</span>',
    "a155": '<span class="smallcap">U</span>',
    "a153": '<span class="smallcap">U</span>',
    "a62f": '<span class="gai_a62f"><sup>4</sup><sub>2</sub></span>',
}
HC02BE_NOOP_MARKERS = {"b924", "b925", "b92a", "b933", "b935"}
HC02BE_ACCENT_MARKERS: dict[str, tuple[str, str, str, str]] = {
    # marker -> (visible base HTML, wrapper class, image class, image key)
    "a138": ("A", "nowrap_half", "aigu_half", "aigu"),
    "a139": ("E", "nowrap_half", "aigu_half", "aigu"),
    "a13a": ("I", "nowrap_half", "aigu_half", "aigu"),
    "a13b": ("O", "nowrap_half", "aigu_half", "aigu"),
    "a13c": ("U", "nowrap_half", "aigu_half", "aigu"),
    "a13e": ("a", "nowrap_half", "aigu_half", "aigu"),
    "a13f": ("e", "nowrap_half", "aigu_half", "aigu"),
    "a140": ("&#x00ed;", "nowrap_half", "grave_half", "grave"),
    "a141": ("E", "nowrap_half", "aigu_half", "aigu"),
    "a142": ("u", "nowrap_half", "aigu_half", "aigu"),
    "a143": ("y", "nowrap_half", "aigu_half", "aigu"),
    "a151": ("I", "nowrap_half", "aigu_half", "aigu"),
    "a160": ("&#x28c;", "nowrap_half", "aigu_half", "aigu"),
    "a16e": ("A", "nowrap_half", "aigu_half", "aigu"),
    "a16f": ("E", "nowrap_half", "aigu_half", "aigu"),
    "a170": ("I", "nowrap_half", "aigu_half", "aigu"),
    "a172": ("U", "nowrap_half", "aigu_half", "aigu"),
    "a174": ("a", "nowrap_half", "aigu_half", "aigu"),
    "a175": ("e", "nowrap_half", "aigu_half", "aigu"),
    "a176": ("&#x00ec;", "nowrap_half", "grave_half", "grave"),
    "a177": ("E", "nowrap_half", "aigu_half", "aigu"),
    "a178": ("u", "nowrap_half", "aigu_half", "aigu"),
    "a179": ("y", "nowrap_half", "aigu_half", "aigu"),
    "a17a": ("&#x28c;", "nowrap_half", "aigu_half", "aigu"),
    "a23e": ("&#x259;", "nowrap_half", "aigu_half", "aigu"),
    "a249": ("&#x25b;", "nowrap_half", "aigu_half", "aigu"),
    "a24a": ("&#x26a;", "nowrap_half", "grave_half", "grave"),
    "a24b": ("&#x254;", "nowrap_half", "aigu_half", "aigu"),
    "a24f": ("&#x251;", "nowrap_half", "grave_half", "grave"),
    "a250": ("&#x259;", "nowrap_half", "aigu_half", "aigu"),
    "a252": ("&#x25b;", "nowrap_half", "tilde_half", "tilde"),
    "a253": ("&#x26a;", "nowrap_half", "grave_half", "grave"),
    "a254": ("&#x254;", "nowrap_half", "aigu_half", "aigu"),
    "a258": ("&#x251;", "nowrap_half", "tilde_aigu_half", "tilde_aigu"),
    "a25a": ("&#x25b;", "nowrap_half", "tilde_half", "tilde"),
    "a25c": ("&#x254;", "nowrap_half", "aigu_half", "aigu"),
    "a25d": ("&#x251;", "nowrap_half", "tilde_aigu_half", "tilde_aigu"),
    "a261": ("&#x25b;", "nowrap_half", "aigu_half", "aigu"),
    "a263": ("&#x254;", "nowrap_half", "aigu_half", "aigu"),
    "a264": ("&#x251;", "nowrap_half", "tilde_aigu_half", "tilde_aigu"),
    "a26a": ("&#x251;", "nowrap_half", "tilde_grave_half", "tilde_grave"),
    "a36e": ("&#x254;", "nowrap_half", "macron_half", "macron"),
    "b14b": ("｜", "nowrap_full", "aigu_full", "aigu"),
    "b14c": ("｜", "nowrap_full", "grave_full", "grave"),
    "b14e": ("&#xe6;", "nowrap_full", "aigu_full", "aigu"),
    "b14f": ("&#xe6;", "nowrap_full", "grave_full", "grave"),
    "b152": ("&#xe6;", "nowrap_full", "tilde_aigu_full", "tilde_aigu"),
    "b154": ("&#xe6;", "nowrap_full", "tilde_full", "tilde"),
    "b156": ("&#x153;", "nowrap_full", "aigu_full", "aigu"),
    "b159": ("&#x153;", "nowrap_full", "tilde_full", "tilde"),
}

HC02BC_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    "b121": _RendererGaijiRule('<span class="blue">', "b125"),
    "b122": _RendererGaijiRule('<span class="blue">', "b125"),
    "b123": _RendererGaijiRule('<span class="blue">', "b125"),
    "b124": _RendererGaijiRule('<span class="blue">', "b125"),
    "b132": _RendererGaijiRule('<span class="sc">', "b133"),
    "b134": _RendererGaijiRule('<span style="color:#800000;">', "b135"),
    "b136": _RendererGaijiRule('<span style="color:#990000;"><b>', "b137", "</b></span>"),
    "b138": _RendererGaijiRule('<span style="color:#A0522D;">', "b139"),
    "b13d": _RendererGaijiRule('<div style="margin-left:1em;">', "b13e", "</div>"),
}
HC02BC_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC02BC_OPEN_MARKERS.values() if rule.close_code)
HC02BC_NOOP_MARKERS = {"b13a", "b13b", "b13f", "b140", "b141", "b16e", "b16f"}
HC02BC_LITERAL_MARKERS = {
    "a167": '<font face="Meiryo,MS UI Gothic">&#x2032;</font>',
    "b13c": "<br>",
}
HC02BC_COMPOSITE_MARKERS = {
    # Direct HTML snippets from HC02BC epwing2HtmlBodydataVertical's gaiji
    # branch ladder. These are renderer-formatting glyphs, not image gaiji.
    "a145": '<p style="line-height:0em;display:inline-table;"><span>Q</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p>',
    "a146": '<p style="line-height:0em;display:inline-table;"><span>Q</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p>',
    "a147": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p>',
    "a148": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p>',
    "a159": '<p style="line-height:0em;display:inline-table;"><span>c</span><br><span style="position: relative;top:-0.8em;">_</span></p>',
    "a15e": '<p style="line-height:0em;display:inline-table;"><span>s</span><br><span style="position: relative;top:-0.8em;">_</span></p>',
    "a160": '<p style="line-height:0em;display:inline-table;"><span>v</span><br><span style="position: relative;top:-0.8em;">_</span></p>',
    "b126": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span>o<small><small>2</small></small></p>',
    "b127": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span>o<small><small>2</small></small></p>',
    "b128": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span>co<small><small>2</small></small></p>',
    "b129": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span>co<small><small>2</small></small></p>',
    "b12a": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p>a:<p style="line-height:0em;display:inline-table;"><span>Q</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p>',
    "b12b": '<p style="line-height:0em;display:inline-table;"><span>V</span><br><span style="position: relative;top:-0.8em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span></p><small><small>A</small></small>',
    "b12c": '<p style="line-height:0em;display:inline-table;"><small><small><small><small><small><b>&#x2571;</b></small></small></small></small></small><br><span style="position: relative;top:-0.7em;"><small><small><small><small><small><b>&#x2572;</b></small></small></small></small></small></span></p>',
    "b12d": '<p style="line-height:0em;display:inline-table;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>&#x0394;<br><span style="position: relative;top:0em;">&#xff0f;</span></p>',
    "b12e": '<p style="line-height:0em;display:inline-table;"><span style="position: relative;top:-0.1em;"><small>&#x0394;</small></span><br><span style="position: relative;top:0.4em;"><small>&#x2192;</small></span></p>',
    "b12f": '<p style="line-height:0em;display:inline-table;"><small><small>&#xff24;</small></small><br><span style="position: relative;top:-0.6em;"><small><small><small><small>20</small></small></small></small></span></p>',
    "b130": 'O<p style="line-height:0em;display:inline-table;"><span><small><font face="Meiryo,MS UI Gothic">&thinsp;</font><small>2</small></small></span><br><span style="position: relative;top:-0.7em;"><font face="Meiryo,MS UI Gothic">&thinsp;</font>.</span><br><span style="position: relative;top:-1em;">_</span></p>',
    "b131": '<p style="line-height:0em;display:inline-table;"><small><small><small>N</small></small></small><br><span style="position: relative;top:-0.6em;"><small><small><small>+</small></small></small></span></p>',
}

HC012E_HONBUN_SECTION_CODES = {
    "0009",
    "000a",
    "000b",
    "000d",
    "000e",
    "0015",
    "0016",
    "0017",
    "0018",
    "0019",
    "001a",
    "001b",
    "001c",
    "0021",
    "0022",
    "0023",
    "0024",
    "0025",
    "0026",
    "0027",
    "0028",
    "0029",
    "002a",
    "002b",
    "002c",
    "003c",
    "003d",
    "003e",
    "0040",
    "0041",
    "0042",
    "0043",
    "0044",
    "0046",
}
HC012E_INDENTED_SECTION_CODES = frozenset(f"{value:04x}" for value in range(0x32, 0x39))
HC012E_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    "b238": _RendererGaijiRule('<span style="color:#000000;">', "b242"),
    "b239": _RendererGaijiRule('<span style="color:#FF0000;">', "b242"),
    "b241": _RendererGaijiRule('<span class="sizedown">', "b242"),
}
HC012E_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC012E_OPEN_MARKERS.values() if rule.close_code)
HC012E_NOOP_MARKERS = {"b128", "b129", "b12a", "b12b", "b132", "b23a", "b23b"}
HC012E_LITERAL_MARKERS = {"a149": "&nbsp;&nbsp;"}
HC012E_DIRECT_IMAGE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x136, 0x13A))
HC012E_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}

HC012F_NOOP_SECTION_CODES = {"0006"}
HC012F_TEMPLATE_GAIJI_MARKERS = frozenset(
    [
        "a127",
        "a130",
        "b167",
        "b16e",
        "b16f",
        "b178",
        "b179",
        "b243",
    ]
    + [f"b{value:03x}" for value in range(0x121, 0x161)]
)
HC012F_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x61}

HC0131_SECTION_CLASSES = {
    "0004": "content_IND4",
    "0005": "content_IND5",
    "0006": "content_IND6",
    "000a": "content_IND10",
    "000b": "content_IND11",
    "000c": "content_IND12",
    "000e": "content_IND14",
    "000f": "content_IND15",
    "0011": "contents",
    "0012": "content_IND18",
}
HC0131_SECTION_HR_CODES = {"0004", "0006", "000e"}
HC0131_NONPRINTING_CONTROL_OPS = {0x4C, 0x6D}

HC02C2_ICON_SECTION_IMAGES = {
    "0007": "1.png",
    "0008": "2.png",
    "0009": "3.png",
    "000a": "4.png",
}
HC02C2_TEMPLATE_IMAGE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x13E, 0x15E))
HC02C2_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC02C1_ICON_SECTION_IMAGES = {
    "0003": "1.png",
    "0004": "2.png",
    "0005": "3.png",
    "0006": "5.png",
}
HC02C1_NOOP_SECTION_CODES = {"270f", "9999"}
HC02C1_MOJI_DOWN_MARKERS = frozenset(f"b{value:03x}" for value in range(0x121, 0x139))
HC02C1_TEMPLATE_IMAGE_MARKERS = {
    "b13e",
    "b13f",
    "b140",
    "b141",
    "b142",
    "b143",
    "b144",
    "b145",
    "b146",
    "b147",
    "b148",
    "b14a",
    "b14b",
    "b14c",
    "b14d",
}
HC02C1_NONPRINTING_CONTROL_OPS = {0x02, 0x41, 0x4C, 0x61}
HC02BF_ICON_SECTION_IMAGES = {
    "0005": "hasei.png",
}
HC02BF_NOOP_SECTION_CODES = {"270f", "9999"}
HC02BF_MOJI_DOWN_MARKERS = frozenset(f"b{value:03x}" for value in range(0x128, 0x151))
HC02BF_NONPRINTING_CONTROL_OPS = {0x02, 0x41, 0x4C, 0x61}
HC02C0_NOOP_SECTION_CODES = {"270f", "9999"}
HC02C0_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x6D}
HC02C0_ICON_DIRECTIVES = {
    "2331": "1.png",
    "2332": "2.png",
    "2333": "3.png",
    "2334": "4.png",
}
HC02C0_NOOP_MARKERS = {"b138", "b14c", "b14d"}
HC02CA_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x6D}
HC02CA_ICON_DIRECTIVES = HC02C0_ICON_DIRECTIVES
HC02CA_IMG_MARK_MARKERS = {"b12d", "b12e", "b12f"}
HC02CA_NOOP_MARKERS = {"b130", "b131"}
HC02CA_LITERAL_MARKERS = {
    "b135": "\U00020bb7",
}
HC0136_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x6D}
HC0136_ICON_DIRECTIVES = HC02C0_ICON_DIRECTIVES
HC013C_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x6D}
HC013C_ICON_DIRECTIVES = {
    "2331": "1.png",
    "2332": "2.png",
    "2333": "3.png",
    "2334": "4.png",
}
HC013C_NOOP_MARKERS = {"a435", "a436"}
HC00B3_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC_GEN_YEAR_RENDERERS = {"02C4", "02C7"}
HC_GEN_YEAR_NOOP_SECTION_CODES = {"270f", "9999"}
HC_GEN_YEAR_NONPRINTING_CONTROL_OPS = {0x02, 0x4C}
HC_GEN_YEAR_ICON_DIRECTIVES = {
    "2331": "1.png",
    "2332": "2.png",
    "2333": "3.png",
    "2334": "4.png",
}
HC_GEN_YEAR_IMG_MARK_MARKERS = {"b12d", "b12e", "b12f"}
HC_GEN_YEAR_IMG_MARK2_MARKERS = frozenset(f"b{value:03x}" for value in range(0x132, 0x138))
HC_GEN_YEAR_NOOP_MARKERS = {"b130", "b131", "b138"}

HC0065_LITERAL_MARKERS = {
    # HC0065 routes these grammar-label gaiji through short literal branches.
    # A174 has an empty display field but a "B" fallback in GENIUSEB.UNI.
    "a174": "B",
    "a430": "c",
    "a431": "u",
    "a432": "S",
    "a433": "D",
}
HC0065_TEMPLATE_IMAGE_MARKERS = {
    "a251": "img_gaiji",
    "a253": "img_gaiji",
}
HC0065_NONPRINTING_CONTROL_OPS = {0x4C, 0x61}
HC0048_NONPRINTING_CONTROL_OPS = {0x02, 0x41, 0x4C, 0x5C, 0x6D}
HC0048_MIDASHI_MARKERS = {"2178", "217a", "2221", "2223", "2227"}

HC009D_KAKOMI_OPEN_MARKERS = {
    "b142": ('<div class="columnKakomi">', "b143", "</div>", "b142.gif"),
    "b144": ('<div class="komattaKakomi">', "b145", "</div>", "b144.gif"),
    "b146": ('<div class="checkKakomi">', "b147", "</div>", "b146.gif"),
    "b148": ('<div class="presentKakomi">', "b149", "</div>", "b148.gif"),
    "b14a": ('<div class="tokuKakomi">', "b14b", "</div>", "b14a.gif"),
    "b150": ('<div class="letterKakomi">', "b151", "</div>", None),
    "b152": ('<div class="speechKakomi">', "b153", "</div>", None),
    "b154": ('<div class="dotKakomi">', "b155", "</div>", None),
    "b156": ('<div class="decorateKakomi">', "b157", "</div>", None),
    "b158": ('<div class="simpleKakomi">', "b159", "</div>", None),
}
HC009D_KAKOMI_CLOSE_MARKERS = frozenset(rule[1] for rule in HC009D_KAKOMI_OPEN_MARKERS.values())
HC009D_TABLE_OPEN_MARKER = "b140"
HC009D_TABLE_CLOSE_MARKER = "b141"
HC009D_TABLE_HEADER_MARKER = "b14f"
HC009D_LITERAL_MARKERS = {
    "b121": "☞",
}
HC009D_HTML_MARKERS = {
    "b125": '<input type="checkbox" name="chk" value="chk">',
}
HC009D_BREAK_MARKERS = {"b130", "b131", "b138", "b139", "b13a", "b13b", "b13c", "b13d"}
HC009D_NOOP_MARKERS = {
    "b12d",
    "b12e",
    "b12f",
    HC009D_TABLE_OPEN_MARKER,
    *HC009D_KAKOMI_OPEN_MARKERS.keys(),
}
HC009D_NONPRINTING_CONTROL_OPS = {0x6D}

HC012D_HONBUN_START_MARKERS = {
    "2227",
    "b87c",
    *(f"b{value:03x}" for value in range(0x121, 0x176)),
    *(f"b{value:03x}" for value in range(0x926, 0x92D)),
}
HC012D_INLINE_IMAGE_JIS = {
    "217e": "kaisetsu_s",
    "2221": "kaisetsu_m",
    "224e": "link_t",
}
HC012D_LITERAL_MARKERS = {
    "a134": " ",
}
HC012D_INLINE_HTML_MARKERS = {
    "a137": '<span class="mini_space"> </span>',
}
HC012D_NOOP_MARKERS = {"b87c", "b87d"}
HC012D_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}

HC0145_LITERAL_MARKERS = {
    "a921": "≪",
    "a922": "≫",
    "a923": "<sup>*</sup>",
    "a924": "<sup>||</sup>",
    "a130": "[",
    "a131": "]",
    "b92a": "（",
    "b92b": "）",
    "b934": "[",
    "b936": "&nbsp;",
}
HC0145_OPEN_MARKERS = {
    "b924": _RendererGaijiRule("<b><i>", "b925", "</i></b>"),
}
HC0145_CLOSE_MARKERS = frozenset(rule.close_code for rule in HC0145_OPEN_MARKERS.values() if rule.close_code)
HC0145_NOOP_MARKERS = {
    "b87d",
    "b926",
    "b927",
    "b928",
    "b929",
    "b92c",
    "b92d",
    "b92e",
    "b92f",
    "b931",
    "b932",
    "b933",
    "b935",
    "b937",
}
HC0144_LITERAL_MARKERS = HC0145_LITERAL_MARKERS
HC0144_OPEN_MARKERS = HC0145_OPEN_MARKERS
HC0144_CLOSE_MARKERS = HC0145_CLOSE_MARKERS
HC0144_NOOP_MARKERS = HC0145_NOOP_MARKERS | {"b921"}
HC03E8_LITERAL_MARKERS = {**HC0145_LITERAL_MARKERS, "b936": "]&nbsp;"}
HC03E8_OPEN_MARKERS = HC0145_OPEN_MARKERS
HC03E8_CLOSE_MARKERS = HC0145_CLOSE_MARKERS
HC03E8_NOOP_MARKERS = HC0144_NOOP_MARKERS | {"b939"}
HC0141_LITERAL_MARKERS = HC03E8_LITERAL_MARKERS
HC0141_OPEN_MARKERS = HC0145_OPEN_MARKERS
HC0141_CLOSE_MARKERS = HC0145_CLOSE_MARKERS
HC0141_NOOP_MARKERS = HC0145_NOOP_MARKERS
HC0190_TEMPLATE_MARKERS = {"b121", "b122", "b123", "b124"}
HC0190_BREAK_SECTIONS = {3, 7, 0x1C}
HC009C_NONPRINTING_CONTROL_OPS = {0x41, 0xE0, 0xE1}
HC009C_DIRECT_IMAGE_MARKERS = {
    "b122": "img_mark2",
}
HC009C_SEASON_IMAGE_MARKERS = {"b148", "b149", "b14a", "b14b"}
HC009C_KO_MIDASHI_MARKERS = {"b128", "b129"}
HC009C_FEATURE_TABLE_MARKERS = {"b12a", "b12b", "b12c", "b12d"}
HC009C_DATA_TABLE_MARKERS = {"b12e", "b12f", "b130", "b131", "b132", "b133", "b134", "b135", "b136"}
HC009C_MEMO_TABLE_MARKERS = {"b137"}
HC009C_NOOP_MARKERS = {
    "b138",
    "b139",
    "b140",
    "b141",
    "b142",
    "b143",
    "b144",
    "b145",
    "b146",
    "b147",
    "b14c",
    "b14d",
}
HC02C5_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC02C5_IMG_HIN_MARKERS = {"b347", "b348", "b273", "b372"}
HC02C5_STRONG_MARKERS = {
    "b146": "1",
    "b443": "1",
    "b147": "2",
    "b444": "2",
    "b148": "3",
    "b445": "3",
    "b149": "4",
    "b446": "4",
    "b14a": "5",
    "b447": "5",
    "b14b": "6",
    "b448": "6",
    "b14c": "7",
    "b449": "7",
    "b14d": "8",
    "b44a": "8",
    "b14e": "9",
    "b44b": "9",
    "b14f": "10",
    "b150": "11",
    "b373": "12",
    "b374": "13",
    "b375": "14",
    "b376": "15",
    "b377": "16",
    "b378": "17",
    "b44c": "17",
    "b379": "18",
    "b44d": "18",
    "b37a": "19",
    "b37b": "20",
}
HC02C5_SMALL_MARKERS = {
    "b353": "a",
    "b44e": "a",
    "b354": "b",
    "b44f": "b",
    "b355": "c",
    "b450": "c",
    "b356": "d",
    "b451": "d",
    "b357": "e",
    "b452": "e",
    "b358": "f",
    "b453": "f",
    "b37c": "g",
    "b37d": "h",
    "b454": "h",
    "b37e": "i",
    "b455": "i",
    "b421": "j",
    "b422": "k",
    "b423": "l",
}
HC0151_NONPRINTING_CONTROL_OPS = {0x6D}
HC0151_SECTION_DIV_CLASSES = {
    "0011": "indent17",
    "0013": "indent19",
    "0017": "indent23",
    "0019": "indent25",
    "001b": "indent27",
    "001d": "indent29",
    "001e": "indent30",
    "001f": "indent31",
    "0021": "indent33",
    "0023": "indent35",
    "0024": "indent36",
    "0025": "indent37",
    "0026": "indent38",
    "0027": "indent39",
    "0029": "indent41",
    "002b": "indent43",
    "002d": "indent45",
    "0032": "indent50",
    "0088": "indent136",
}
HC0151_NOOP_SECTION_CODES = {"0001", "270f"}
HC0151_TABLE_HEADER_SECTION = "0020"
HC0151_TABLE_ROW_SECTION = "0022"
HC0151_TABLE_CLOSE_SECTION = "0087"
HC0151_SMALL_OPEN_MARKER = "b156"
HC0151_SMALL_CLOSE_MARKER = "b157"
HC0151_TABLE_CELL_MARKER = "b159"
HC0142_NOOP_SECTION_CODES = {"270f", "9999"}
HC0142_PLAIN_TEXT_MARKERS = {"b157", "b16a", "b16b", "b16c", "b16d", "b16e", "b16f", "b170"}
HC0142_NOOP_MARKERS = {"b13e"}
HC0142_DIRECT_HALF_IMAGE_MARKERS = {"a13f", "a162", "a163", "b169"}
HC0142_NONPRINTING_CONTROL_OPS = {0x61}
HC013D_NONPRINTING_CONTROL_OPS = {0x6D}
HC013D_MED_SECTION_CLASSES = {
    "0004": ("div", ' class="title3"'),
    "0006": ("span", ' class="med"'),
    "0008": ("div", ' class="medblk"'),
    "0010": ("div", ' class="medprice"'),
    "0011": ("div", ' class="medimage"'),
    "0014": ("div", ' class="mednamelist1"'),
    "0015": ("div", ' class="mednamelist2"'),
    "0016": ("div", ' class="mednamelist3"'),
}
HC013D_INDENT_SECTION_CODES = {"0040", "0041", "0050", "0051", "0052", "0060", "0061"}
HC013D_TITLE_TRIGGER_SEQUENCES = {
    ("4a2c", "4e60"),
    ("3d68", "4a7d"),
    ("3272", "4062"),
}
HC013D_SUBTITLE_IMAGE_SEQUENCES = {
    ("403d", "3a5e"): ("seizaijouhou", "SubTitle", "midashi1"),
    ("3335", "4d57"): ("gaiyou", "SubTitle_gaiyou", "midashi2"),
    ("452c", "317e"): ("gaiyou", "SubTitle_gaiyou", "midashi4"),
    ("3b48", "4d51"): ("shiyoujounocyuui", "SubTitle_caution", "midashi3"),
    ("436d", "3055"): ("shiyoujounocyuui", "SubTitle_caution", "midashi5"),
}
HC013D_SHOW_TITLE_SEQUENCES = {
    ("2433", "244e", "3e4f", "2447"): ("gTitle1", 0x12),
    ("3c2b", "384a", "436d", "3c4d"): ("gTitle2", 0x12),
    ("497b", "3a6e", "4d51", "2126"): ("nTitle1", 0x1e),
    ("3a5e", "3741", "242b", "2469"): ("nTitle2", 0x16),
    ("3330", "4d51", "4c74", "2447"): ("nTitle1", 0x1e),
    ("3441", "4a7d", "4c74", "2447"): ("nTitle3", 0x1a),
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


def _media_placeholder_html(control: str, target: dict[str, Any] | None) -> str:
    kind = "colscr" if target else "media"
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
    return f"<span {' '.join(attrs)}></span>"


def _renderer_code(options: HcRenderOptions) -> str:
    return (options.renderer_code or "").upper()


def _is_hc_gen_year_renderer(options: HcRenderOptions) -> bool:
    return _renderer_code(options) in HC_GEN_YEAR_RENDERERS


def _is_hc_hkdksr_medical_renderer(options: HcRenderOptions) -> bool:
    return _renderer_code(options) in HC_HKDKSR_MEDICAL_RENDERERS


def _link_css_class(options: HcRenderOptions, start_op: int | None) -> str:
    if _renderer_code(options) == "009B" and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _is_hc_hkdksr_medical_renderer(options):
        if start_op == 0x42:
            return "lv-hc-link lineLink2"
        if start_op in {0x43, 0x44}:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "00A6" and start_op in {0x3B, 0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0065" and start_op in {0x42, 0x43, 0x44}:
        return "lv-hc-link lLink"
    if _renderer_code(options) == "02C5" and start_op in {0x42, 0x43}:
        return "lv-hc-link lLink"
    if _renderer_code(options) == "0151":
        if start_op == 0x42:
            return "lv-hc-link Link"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if (
        _renderer_code(options)
        in {"0048", "00B3", "012F", "0131", "0136", "013C", "0142", "02BF", "02C0", "02C1", "02CA"}
        or _is_hc_gen_year_renderer(options)
    ) and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) in {"009C", "009D", "012D", "013D", "0141", "0144", "0145", "02C2", "03E8"} and start_op in {
        0x42,
        0x43,
    }:
        return "lv-hc-link lineLink"
    return "lv-hc-link"


def _style_start_spec(op: int, options: HcRenderOptions) -> tuple[str, str] | None:
    if _is_hc_hkdksr_medical_renderer(options) and op == 0x04:
        return ("span", ' class="hankaku"')
    if _is_hc_hkdksr_medical_renderer(options) and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "00A6" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00A6" and op == 0x06:
        return ("sup", "")
    if _renderer_code(options) == "00A6" and op == 0x41:
        return None
    if _renderer_code(options) == "0048" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0048" and op == 0x41:
        return None
    if _renderer_code(options) == "0157" and op == 0x12:
        return None
    if _renderer_code(options) == "0158" and op == 0x12:
        return ("b", "")
    if _renderer_code(options) == "009C" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "009B" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "009B" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "02C5" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "02C1" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "02BF" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) in {"00B3", "0136", "013C", "02C0", "02CA"} and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "012F" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "012F" and op == 0x06:
        return ("span", ' class="sizedown"')
    if _renderer_code(options) == "0131" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _is_hc_gen_year_renderer(options) and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0151" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0142" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "02C5" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "0151" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "0142" and op == 0x41:
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "0065":
        return None
    if op in {0x41, 0xE0, 0xE1} and _renderer_code(options) == "009C":
        return None
    if op in {0x41, 0x4C} and _renderer_code(options) in {"012F", "02BF", "02C1", "02C2"}:
        return None
    if op == 0x41 and _renderer_code(options) == "012D":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "012E":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "009D":
        return None
    if op == 0x41 and _renderer_code(options) in {"00B3", "0136", "013C", "02C0", "02CA"}:
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "013D":
        return ("div", ' class="midashi"')
    if op == 0x41 and _is_hc_gen_year_renderer(options):
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "0144":
        return None
    if op == 0x41 and _renderer_code(options) == "0145":
        return None
    if op == 0x41 and _renderer_code(options) == "03E8":
        return None
    if op == 0x41 and _renderer_code(options) == "0141":
        return None
    if op == 0x41 and _renderer_code(options) in {"00C6", "0146", "0157", "0158"}:
        return ("span", ' class="lv-hc-heading midashi"')
    return STYLE_START_TAGS.get(op)


def _style_close_tag(start_op: int, options: HcRenderOptions) -> str | None:
    if _renderer_code(options) == "0142" and start_op == 0x10:
        return "label"
    if _renderer_code(options) == "0131" and start_op == 0x41:
        return "div"
    spec = _style_start_spec(start_op, options)
    return spec[0] if spec else None


def _decode_next_jis_text(data: bytes, offset: int) -> str:
    if offset + 1 >= len(data):
        return ""
    first = data[offset]
    second = data[offset + 1]
    if not (0x21 <= first <= 0x7E and 0x21 <= second <= 0x7E):
        return ""
    return decode_jis_pair(data[offset : offset + 2]) or ""


def _jis_key_at(data: bytes, offset: int) -> str | None:
    if offset + 1 >= len(data):
        return None
    first = data[offset]
    second = data[offset + 1]
    if not (0x21 <= first <= 0x7E and 0x21 <= second <= 0x7E):
        return None
    return f"{first:02x}{second:02x}"


def _two_byte_key_at(data: bytes, offset: int) -> str | None:
    if offset + 1 >= len(data):
        return None
    return f"{data[offset]:02x}{data[offset + 1]:02x}"


def _hc0158_conditional_waku(next_text: str) -> str:
    if next_text == "訳":
        return '<br><span class="waku_red red">'
    if next_text == "慣":
        return '<span class="waku_red red">'
    if next_text == "図":
        return '<span class="back_red white">'
    return '<span class="waku">'


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


def _hc0190_close_section(contexts: list[_Context], sections: dict[int, str], stats: Counter[str]) -> bool:
    if not contexts or contexts[-1].kind != "hc0190_section":
        return False
    ctx = contexts.pop()
    section = ctx.start_offset
    if section in HC0190_BREAK_SECTIONS:
        ctx.parts.append("<br>")
    sections[section] = "".join(ctx.parts)
    stats["hc0190_sections_captured"] += 1
    return True


def _hc0190_apply_template(template: str, sections: dict[int, str], stats: Counter[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        section = int(match.group(1), 10)
        if section in sections:
            stats["hc0190_template_placeholders_filled"] += 1
            return sections[section]
        stats["hc0190_template_placeholders_empty"] += 1
        return ""

    return re.sub(r"<!--&IND(\d{4});-->", replace, template)


def _hc009c_section_parts(code: str, options: HcRenderOptions) -> tuple[list[str], str | None]:
    try:
        value = int(code, 16)
    except ValueError:
        return [], None
    if value == 1:
        return ['<div class="midashi">'], "</div>"
    margin_axis = "margin-top" if options.vertical else "margin-left"
    return [f'<div class="honbun" style="{margin_axis}:{value}em">'], "</div>"


def _hc009c_close_section(parts: list[str], marker_stack: list[str], section_close: str | None) -> None:
    while marker_stack:
        parts.append(marker_stack.pop())
    if section_close is not None:
        parts.append(section_close)


def _hc02c5_section_parts(code: str, *, hr_seen: bool) -> tuple[list[str], str | None, bool]:
    """Return the HC02C5 section wrapper recovered from the body-loop ladder.

    The native renderer has additional stateful lookahead branches for select
    menus and grammar panels. This helper intentionally covers only section
    outputs whose destination HTML/CSS was directly recovered.
    """

    if code == "000b":
        return ['<h1 class="indent11"><span class="hankakuMidashi">'], "</span></h1>", hr_seen
    if code == "000c":
        return ['<h1 class="indent12">'], "</h1>", hr_seen
    if code in {"000a", "000f"}:
        return ["<div>"], "</div>", hr_seen
    if code in {"0019", "001b"}:
        return ['<div style="margin-left:1em;font-size:0.95em;">'], "</div>", hr_seen
    if code == "002a":
        return ['<div style="font-size:1.1em;">'], "</div>", hr_seen
    if code in {"002d", "0050"}:
        return ['<div style="background-color:#FFFFCC;">'], "</div>", hr_seen
    if code == "002e":
        return ['<br><div class="Seiku">'], "</div>", hr_seen
    if code in {"003a"}:
        return ['<h1 class="indent58">'], "</h1>", hr_seen
    if code in {"003b"}:
        return ['<h1 class="indent59">'], "</h1>", hr_seen
    if code == "003c":
        return ['<div style="margin-left:3em;text-indent:-1em;">'], "</div>", hr_seen
    if code in {"003d", "003e", "0022"}:
        return ['<div class="contents">'], "</div>", hr_seen
    if code in {"0040", "0042"}:
        return ['<br><div style="margin-left:1em;font-size:0.95em;">'], "</div>", hr_seen
    if code in {"0041", "0043", "0048"}:
        return ['<div style="margin-left:2em;">'], "</div>", hr_seen
    if code == "0047":
        return ['<div style="margin-left:1em;">'], "</div>", hr_seen
    if code == "0049":
        return (["<br>"] if hr_seen else ['<hr size="1">']), None, True
    if code == "004a":
        return ['<div style="margin-left:3.5em;text-indent:-1.5em;">'], "</div>", hr_seen
    if code == "004b":
        return ['<div style="margin-left:1.5em;">'], "</div>", hr_seen
    return [], None, hr_seen


def _hc0151_section_parts(code: str, *, table_open: bool) -> tuple[list[str], str | None, bool]:
    """Return the HC0151 section wrapper recovered from the body-loop ladder.

    HC0151 uses numeric 1f09 payloads mostly as CSS-indent selectors. Two
    section codes create table rows and a later code closes the table. The
    function returns the opening fragments, the matching close fragment for
    1f0a, and the updated table-open state.
    """

    if code in HC0151_NOOP_SECTION_CODES:
        return [], None, table_open
    css_class = HC0151_SECTION_DIV_CLASSES.get(code)
    if css_class is not None:
        return [f'<div class="{css_class}">'], "</div>", table_open
    if code == HC0151_TABLE_HEADER_SECTION:
        return ["<table><tr><th>"], "</th></tr>", True
    if code == HC0151_TABLE_ROW_SECTION:
        return ["<tr><td>"], "</td></tr>", table_open
    if code == HC0151_TABLE_CLOSE_SECTION:
        return (["</table><br>"] if table_open else ["<br>"]), None, False
    return [], None, table_open


def _hc009c_marker_image_src(key: str, options: HcRenderOptions, *, midashi: bool = False) -> str | None:
    if midashi:
        return _image_source_for_key(f"{key}m", options) or _image_source_for_key(key, options)
    return _image_source_for_key(key, options)


def _hc009c_table_marker_html(key: str, table_class: str, options: HcRenderOptions) -> tuple[str, str]:
    src = _hc009c_marker_image_src(key, options)
    image_html = ""
    if src is not None:
        image_html = f'<img src="{_escape_attr(src)}" class="img_mark2" alt="{_escape_attr(key)}">'
    open_html = f'<table class="{table_class}"><tr><td class="subtitle">{image_html}</td><td class="honbun">'
    return open_html, "</td></tr></table>"


def _hc009c_private_image_key(text: str) -> tuple[str, str] | None:
    normalized = normalize_fullwidth_ascii(text).upper()
    match = re.search(r"IMG:\s*I([0-9]{8})\.(?:PNG|JPG|JPEG|GIF)", normalized)
    if match is None:
        return None
    raw_key = match.group(1).lower()
    if len(raw_key) != 8:
        return None
    image_key = ("50" + raw_key[2:]).lower()
    return raw_key, image_key


def _hc009c_private_image_html(text: str, options: HcRenderOptions, stats: Counter[str]) -> str | None:
    keys = _hc009c_private_image_key(text)
    if keys is None:
        return None
    raw_key, image_key = keys
    thumb = options.image_sources.get(f"hc009c_thumb_{image_key}")
    full = options.image_sources.get(image_key)
    icon = options.image_sources.get(f"hc009c_icon_{raw_key}")
    src = thumb or icon or full
    if src is None:
        stats["hc009c_private_image_missing"] += 1
        return None
    href = full or src
    stats["hc009c_private_images"] += 1
    return (
        f'<a class="hc009c-image-link" href="{_escape_attr(href)}">'
        f'<img src="{_escape_attr(src)}" class="img_button" alt="{_escape_attr(image_key)}">'
        "</a>"
    )


def _renderer_section_rules(options: HcRenderOptions) -> dict[str, _SectionImageRule]:
    code = (options.renderer_code or "").upper()
    return HC_SECTION_IMAGE_RULES.get(code, {})


def _section_image_src(rule: _SectionImageRule, image_sources: dict[str, str]) -> str | None:
    return image_sources.get(rule.image_key.lower()) or image_sources.get(f"{rule.image_key.lower()}.png")


def _sound_image_src(image_sources: dict[str, str]) -> str | None:
    return image_sources.get("sound") or image_sources.get("sound.png") or image_sources.get("sound.gif")


def _image_source_for_key(key: str, options: HcRenderOptions) -> str | None:
    lower = key.lower()
    if options.vertical:
        return options.image_sources.get(f"{lower}_v") or options.image_sources.get(lower)
    return options.image_sources.get(lower)


def _image_or_named_template(key: str, options: HcRenderOptions) -> str:
    return _image_source_for_key(key, options) or f"{key.upper()}.png"


def _append_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src:
        stats["gaiji_image"] += 1
        css_class = "lv-hc-gaiji lv-hc-gaiji-image"
        if _renderer_code(options) in {
            "0065",
            "0048",
            "009B",
            "00A6",
            "00B3",
            "009C",
            "009D",
            "012E",
            "012F",
            "0131",
            "0136",
            "013C",
            "013D",
            "0141",
            "0144",
            "0145",
            "0151",
            "02BF",
            "02C0",
            "02C1",
            "02C2",
            "02CA",
            "03E8",
        }:
            css_class += " img_gaiji"
        if _is_hc_hkdksr_medical_renderer(options):
            css_class += " img_gaiji"
        if _is_hc_gen_year_renderer(options):
            css_class += " img_gaiji"
        if _renderer_code(options) == "012D":
            css_class += " gaiji"
        if _renderer_code(options) == "0158":
            css_class += " gaiji"
        if _renderer_code(options) == "0142":
            css_class += " gaiji_half" if int(key, 16) < 0xB121 else " gaiji_full"
        parts.append(
            f'<img class="{css_class}" '
            f'src="{_escape_attr(image_src)}" alt="{_escape_attr(key)}" '
            f'data-gaiji-code="{_escape_attr(key)}">'
        )
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_renderer_image_gaiji(
    parts: list[str],
    key: str,
    image_src: str,
    css_class: str,
    stats: Counter[str],
) -> None:
    stats["gaiji_image"] += 1
    parts.append(
        f'<img class="lv-hc-gaiji {_escape_attr(css_class)}" '
        f'src="{_escape_attr(image_src)}" alt="{_escape_attr(key)}" '
        f'data-gaiji-code="{_escape_attr(key)}">'
    )


def _hc00c6_image_class(key: str, *, in_heading: bool) -> str:
    if key in {"a246", "a247", "a248", "a249"}:
        return "img_mark5"
    if in_heading:
        return "img_gaiji_midashi"
    return "img_mark5"


def _hc02be_accent_html(rule: tuple[str, str, str, str], image_sources: dict[str, str]) -> str:
    base_html, wrapper_class, image_class, image_key = rule
    src = image_sources.get(image_key) or image_sources.get(f"{image_key}.png") or f"{image_key}.png"
    return (
        f'<span class="{_escape_attr(wrapper_class)}">{base_html}'
        f'<img class="{_escape_attr(image_class)}" src="{_escape_attr(src)}"></span>'
    )


def _hc02bc_section_parts(code: str, image_sources: dict[str, str]) -> list[str]:
    if code == "270f":
        return []
    if code == "0001":
        return ['<div class="midashi">']
    if code == "0002":
        parts: list[str] = []
        src = image_sources.get("fukumidashi") or image_sources.get("fukumidashi.png")
        if src:
            parts.append(f'<img src="{_escape_attr(src)}" class="img_mark2">')
        parts.append('<div class="komidashi"  style="margin-left:1.000000em;">')
        return parts
    try:
        digit = int(code, 16) % 10
    except ValueError:
        return []
    if digit in {1, 6}:
        margin = "1.000000" if digit == 1 else "0.000000"
        return [f'<div class="komidashi"  style="margin-left:{margin}em;">']
    if digit == 2:
        return ['<div class="honbun" style="margin-left:0.000000em;">']
    if digit in {3, 4, 5, 7}:
        return ['<div class="honbun" style="margin-left:1.000000em;">']
    if digit == 8:
        return ['<div class="contents" style="text-indent:0em;">']
    if digit == 9:
        return ["&nbsp;"]
    return []


def _hc012e_honbun_div(*, indented: bool = False) -> str:
    if indented:
        return '<div class="honbun" style="margin-left:1.000000em;text-indent:-1.000000em;">'
    return '<div class="honbun" style="margin-left:0.000000em;">'


def _hc012e_section_parts(code: str, options: HcRenderOptions) -> list[str]:
    if code == "0007":
        return ['<table class="table_oyaji"><tr><td><div class="Oyaji">']
    if code == "001f":
        return ['<table class="table_kyuuji"><tr><td><div class="Kyuuji">']
    if code == "003f":
        return ['<table class="table_itaiji_2"><tr><td><div class="Itaiji">']
    if code == "0008":
        parts = ["<!-- hitsujun start -->", _hc012e_honbun_div(), "<br>"]
        src = options.image_sources.get("hitsujun_v" if options.vertical else "hitsujun") or options.image_sources.get("hitsujun")
        if src:
            parts.append(f'<img src="{_escape_attr(src)}" class="img_gaiji">')
        return parts
    if code == "000c":
        return ['<div class="bushu">']
    if code == "001e":
        return ['<div class="kaku_midashi" style="margin-left:0.000000em;">']
    if code in HC012E_INDENTED_SECTION_CODES:
        return [_hc012e_honbun_div(indented=True)]
    if code in HC012E_HONBUN_SECTION_CODES:
        return [_hc012e_honbun_div()]
    if code == "0050":
        return ['<hr size="1">', _hc012e_honbun_div()]
    if code == "0051":
        return ['<div class="exam" style="margin-left:0.000000em; margin-top:0.5em; margin-bottom:0.5em; margin-right:0.5em;">']
    if code == "0053":
        return ['<div class="column_Tsukaiwake">']
    if code == "0054":
        return ["</div>"]
    if code == "005a":
        return ["<br>", _hc012e_honbun_div()]
    return []


def _hc012e_section_close_for_parts(parts: list[str]) -> str | None:
    if any(part.startswith("<table") for part in parts):
        return "</div></td></tr></table>"
    if any(part.startswith("<div") for part in parts):
        return "</div>"
    return None


def _hc012f_image_tag(image_name: str, css_class: str, options: HcRenderOptions) -> str:
    stem = image_name.rsplit(".", 1)[0].lower()
    src = options.image_sources.get(stem) or options.image_sources.get(image_name.lower()) or image_name
    return f'<img src="{_escape_attr(src)}" class="{_escape_attr(css_class)}">'


def _hc012f_section_parts(code: str, options: HcRenderOptions) -> tuple[list[str], str | None]:
    if code in HC012F_NOOP_SECTION_CODES:
        return [], None
    if code == "0001":
        return ['<div class="midashi">'], "</div>"
    if code == "0002":
        return ['<div class="honbun">'], "</div>"
    if code == "0003":
        return ['<div class="bunnya">'], "</div>"
    if code == "0004":
        return [
            '<div class="honbun">',
            _hc012f_image_tag("link_1_V.png" if options.vertical else "link_1.png", "img_gaiji", options),
        ], "</div>"
    if code == "0005":
        return [
            '<div class="honbun">',
            _hc012f_image_tag("link_2_V.png" if options.vertical else "link_2.png", "img_gaiji", options),
        ], "</div>"
    if code == "0007":
        return ['<div class="menu">'], "</div>"
    if code == "0008":
        return ['<div class="menu_midashi">'], "</div>"
    return ['<div class="honbun">'], "</div>"


def _hc012f_bunnya_key_from_html(value: str) -> str | None:
    plain = normalize_fullwidth_ascii(_plain_from_html(value))
    digits = "".join(ch for ch in plain if ch.isdigit())
    return digits or None


def _hc012f_bunnya_image_html(key: str, options: HcRenderOptions) -> str:
    stem = f"bunnya_{key}{'_V' if options.vertical else ''}"
    fallback = f"{stem}.png"
    src = options.image_sources.get(stem.lower()) or options.image_sources.get(fallback.lower()) or fallback
    css_class = "img_bunnya_V" if options.vertical else "img_bunnya"
    return f'<img src="{_escape_attr(src)}" class="{css_class}">'


def _hc0131_image_tag(image_name: str, css_class: str, options: HcRenderOptions) -> str:
    stem = image_name.rsplit(".", 1)[0].lower()
    src = options.image_sources.get(stem) or options.image_sources.get(image_name.lower()) or image_name
    return f'<img src="{_escape_attr(src)}" class="{_escape_attr(css_class)}">'


def _hc0131_section_parts(code: str, data: bytes, offset: int, options: HcRenderOptions) -> tuple[list[str], str | None]:
    css_class = HC0131_SECTION_CLASSES.get(code)
    if css_class is None:
        return [], None
    parts = [f'<div class="{css_class}">']
    if code in HC0131_SECTION_HR_CODES:
        parts.append('<HR style="border-style: dotted;">')
    if code == "0012" and _jis_key_at(data, offset + 4) != "b132":
        parts.append(_hc0131_image_tag("b132_V.png" if options.vertical else "b132.png", "img_gaiji", options))
    return parts, "</div>"


def _hc012d_honbun_starts_with_marker(data: bytes, control_offset: int) -> bool:
    first_key = _two_byte_key_at(data, control_offset + 4)
    if first_key in HC012D_HONBUN_START_MARKERS:
        return True
    second_key = _jis_key_at(data, control_offset + 6)
    if second_key is None:
        return False
    try:
        value = int(second_key, 16)
    except ValueError:
        return False
    return 0x2331 <= value <= 0x2339


def _hc012d_section_parts(code: str, data: bytes, control_offset: int) -> list[str]:
    if code == "0002":
        css_class = "honbun_start" if _hc012d_honbun_starts_with_marker(data, control_offset) else "honbun"
        return [f'<div class="{css_class}">']
    if code == "0003":
        return ['<div class="column">']
    if code == "0004":
        return ['<div class="yindex_midashi">']
    if code == "0005":
        return ['<div class="yindex_menu">']
    if code == "0006":
        return ['<div class="yorei">']
    if code == "0008":
        return ['<div class="honbun_bold">']
    if code == "0009":
        return ['<div class="kanren">']
    if code == "000b":
        return ['<div class="bunrui">']
    if code == "000c":
        return ['<div class="image">']
    if code == "000f":
        return ['<div class="hinshi_midashi">']
    if code == "0010":
        return ['<div class="hinshi">']
    if code == "0011":
        return ['<div class="kaisetsu_b">']
    if code == "0012":
        return ['<div class="kaisetsu_m">']
    if code == "0013":
        return ['<div class="kaisetsu">']
    if code == "0014":
        return ['<div class="ruigo_box"><div class="ruigo_midashi">']
    return []


def _hc012d_section_close_for_parts(parts: list[str]) -> str | None:
    if any('class="ruigo_box"' in part for part in parts):
        return "</div></div>"
    if any(part.startswith("<div") for part in parts):
        return "</div>"
    return None


def _hc009d_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc009d_kakomi_image_src(image_name: str | None, options: HcRenderOptions) -> str | None:
    if image_name is None:
        return None
    key = image_name.rsplit(".", 1)[0].lower()
    return options.image_sources.get(key) or options.image_sources.get(image_name.lower()) or image_name


def _hc009d_section_parts(
    code: str,
    data: bytes,
    control_offset: int,
    options: HcRenderOptions,
) -> tuple[list[str], str | None, int | None, tuple[str, str] | None]:
    value = _hc009d_section_value(code)
    if value is None:
        return [], None, None, None
    if value in {13, 14}:
        src = _image_source_for_key("b124", options) or "b124.gif"
        return [f'<img src="{_escape_attr(src)}" class="img_gaiji">'], None, value, None
    parts: list[str] = []
    wrapper_stack: tuple[str, str] | None = None
    next_key = _two_byte_key_at(data, control_offset + 4)
    if value == 8 and next_key == HC009D_TABLE_OPEN_MARKER:
        parts.append('<table border="1" cellspacing="0" cellpadding="5" bordercolor="#d8e093" class="midashiKakomi">')
        wrapper_stack = (HC009D_TABLE_CLOSE_MARKER, "</td></tr></tbody></table>")
    elif value == 8 and next_key in HC009D_KAKOMI_OPEN_MARKERS:
        open_html, close_code, close_html, image_name = HC009D_KAKOMI_OPEN_MARKERS[next_key]
        parts.append(open_html)
        src = _hc009d_kakomi_image_src(image_name, options)
        if src is not None:
            parts.append(f'<img src="{_escape_attr(src)}" class="img_kakomi">')
        wrapper_stack = (close_code, close_html)
    parts.append(f'<div class="lineinfo{value}">')
    return parts, "</div>", value, wrapper_stack


def _hc0145_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc0145_section_parts(code: str) -> list[str]:
    value = _hc0145_section_value(code)
    if value is None:
        return []
    if value == 1:
        return ['<div class="midashi">']
    if value in {10, 20, 30, 110}:
        return ['<div class="honbun" style="text-indent:-1.0em;margin-left:2.000000em;">']
    digit = value % 10
    if digit in {1, 6}:
        return ['<div class="komidashi"  style="margin-left:1.000000em;">']
    if digit in {2, 3, 7}:
        return ['<div class="honbun" style="margin-left:2.000000em;">']
    if digit in {4, 5}:
        return ['<div class="honbun" style="text-indent:-1.0em;margin-left:2.000000em;">']
    if digit == 8:
        return ['<div class="contents" style="text-indent:0em;">']
    if digit == 9:
        return ["&nbsp;", '<div class="contents" style="text-indent:0em;">']
    return []


def _hc0145_section_close_for_parts(parts: list[str]) -> str | None:
    if any(part.startswith("<div") for part in parts):
        return "</div>"
    return None


def _hc0144_section_parts(code: str) -> list[str]:
    return _hc0145_section_parts(code)


def _hc0144_section_close_for_parts(parts: list[str]) -> str | None:
    return _hc0145_section_close_for_parts(parts)


def _hc03e8_section_parts(code: str) -> list[str]:
    return _hc0145_section_parts(code)


def _hc03e8_section_close_for_parts(parts: list[str]) -> str | None:
    return _hc0145_section_close_for_parts(parts)


def _hc0141_section_parts(code: str) -> list[str]:
    return _hc0145_section_parts(code)


def _hc0141_section_close_for_parts(parts: list[str]) -> str | None:
    return _hc0145_section_close_for_parts(parts)


def _hc013d_section_parts(code: str) -> tuple[list[str], str | None]:
    tag_spec = HC013D_MED_SECTION_CLASSES.get(code)
    if tag_spec is not None:
        tag, attrs = tag_spec
        return [f"<{tag}{attrs}>"], f"</{tag}>"
    if code in HC013D_INDENT_SECTION_CODES:
        return [f'<div class="indent{code[-2:]}">'], "</div>"
    try:
        value = int(code, 10)
    except ValueError:
        return [], None
    if value in {2, 3, 5, 7, 9, 12, 13}:
        return [f'<div style="margin-left:{value * 4}px;">'], "</div>"
    return [], None


def _image_tag_for_key(
    key: str,
    options: HcRenderOptions,
    css_class: str,
    *,
    fallback_ext: str = ".png",
) -> str:
    src = _image_source_for_key(key, options) or f"{key}{fallback_ext}"
    return f'<img src="{_escape_attr(src)}" class="{_escape_attr(css_class)}">'


def _hc013d_subtitle_html(
    outer_class: str,
    subtitle_class: str,
    image_key: str,
    options: HcRenderOptions,
) -> str:
    return (
        '<div class="clearLeft"></div>'
        f'<div class="{_escape_attr(outer_class)}">'
        f'<div class="{_escape_attr(subtitle_class)}">'
        f'{_image_tag_for_key(image_key, options, "img_midashi")}'
        "</div></div>"
    )


def _hc013d_jis_sequence_html(data: bytes, offset: int, options: HcRenderOptions) -> tuple[str | None, int]:
    key = _jis_key_at(data, offset)
    next1 = _jis_key_at(data, offset + 2)
    next2 = _jis_key_at(data, offset + 4)
    if key == "215a" and next1 == "3d69" and next2 == "215b":
        return _image_tag_for_key("syohatsu", options, "img_gaiji"), 6
    if key == "215a" and next1 is not None and next2 is not None:
        subtitle = HC013D_SUBTITLE_IMAGE_SEQUENCES.get((next1, next2))
        if subtitle is not None:
            return _hc013d_subtitle_html(*subtitle, options), 6
        if (next1, next2) == ("3e26", "494a"):
            return '<span class="SubTitle_syouhin"></span>', 6
        if (next1, next2) in HC013D_TITLE_TRIGGER_SEQUENCES:
            return '<div class="clearLeft"></div><span class="title"></span>', 6
    if key == "2223":
        seq = tuple(_jis_key_at(data, offset + 2 + step * 2) or "" for step in range(4))
        show_title = HC013D_SHOW_TITLE_SEQUENCES.get(seq)
        if show_title is not None:
            image_key, consumed = show_title
            return _image_tag_for_key(image_key, options, "img_showtitle"), consumed + 2
        if next1 is not None and next2 is not None and (next1, next2) in {
            ("3759", "3970"),
            ("3470", "4b5c"),
            ("3d45", "4267"),
            ("243d", "244e"),
            ("4a3b", "4d51"),
            ("436d", "3055"),
        }:
            return '<span class="title2"></span>', 6
    if key == "236d" and next1 == "234c":
        return "m&#x2113;", 4
    if key == "2364" and next1 == "234c":
        return "d&#x2113;", 4
    previous = _jis_key_at(data, offset - 2) if offset >= 2 else None
    if key == "234c" and previous is not None:
        try:
            previous_value = int(previous, 16)
        except ValueError:
            previous_value = 0
        if 0x2330 < previous_value < 0x233A:
            return "&#x2113;", 2
    if key == "217b" and data[offset + 2 : offset + 6] == b"\x1f\x04\x23\x32" and _jis_key_at(data, offset + 6) == "2331":
        return "&#x3251;", 8
    if key == "2175":
        return "&amp;", 2
    if key == "216f":
        return "&yen;", 2
    if key == "2163":
        return "&lt;", 2
    if key == "2164":
        return "&gt;", 2
    return None, 0


def _hc02c2_honbun_div() -> str:
    return '<div class="honbun" style="margin-left:0.000000em;">'


def _hc02c1_honbun_div() -> str:
    return '<div class="honbun" style="margin-left:0.000000em;">'


def _hc02c1_icon_section_parts(code: str) -> list[str]:
    image_name = HC02C1_ICON_SECTION_IMAGES.get(code)
    if image_name is None:
        return []
    return [
        _hc02c1_honbun_div(),
        f'<img src="{image_name}" class="img_icon"/><br>',
        "</div>",
    ]


def _hc02c1_section_parts(code: str, previous_code: str | None) -> tuple[list[str], str | None]:
    if code in HC02C1_NOOP_SECTION_CODES:
        return [], None
    if code == "0001":
        return ['<div class="midashi">'], "</div>"

    parts: list[str] = []
    if code in HC02C1_ICON_SECTION_IMAGES and code != previous_code:
        parts.extend(_hc02c1_icon_section_parts(code))
    parts.append(_hc02c1_honbun_div())
    return parts, "</div>"


def _hc02bf_honbun_div() -> str:
    return '<div class="honbun" style="margin-left:0.000000em;">'


def _hc02bf_icon_section_parts(code: str) -> list[str]:
    image_name = HC02BF_ICON_SECTION_IMAGES.get(code)
    if image_name is None:
        return []
    return [
        _hc02bf_honbun_div(),
        f'<img src="{image_name}" class="img_icon"/><br>',
        "</div>",
    ]


def _hc02bf_section_parts(code: str, previous_code: str | None) -> tuple[list[str], str | None]:
    if code in HC02BF_NOOP_SECTION_CODES:
        return [], None
    if code == "0001":
        return ['<div class="midashi">'], "</div>"

    parts: list[str] = []
    if code in HC02BF_ICON_SECTION_IMAGES and code != previous_code:
        parts.extend(_hc02bf_icon_section_parts(code))
    parts.append(_hc02bf_honbun_div())
    return parts, "</div>"


def _hc02c0_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        return int(code, 16)
    except ValueError:
        return None


def _hc02c0_honbun_div(value: int, *, vertical: bool) -> str:
    # HC02C0's body loop formats the section value as pixels with a left
    # margin for horizontal output and a top margin for vertical output.
    margin_prop = "margin-top" if vertical else "margin-left"
    return f'<div class="honbun" style="{margin_prop}:{value << 2}px">'


def _hc02c0_section_parts(code: str, *, vertical: bool) -> tuple[list[str], str | None, str | None]:
    if code in HC02C0_NOOP_SECTION_CODES:
        return [], None, "noop"
    value = _hc02c0_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        # The following 1f41/1f61 range carries the visible heading.
        return [], None, "state"
    if value == 0x0C:
        return ['<div class="footer">'], "</div>", "footer"
    return [_hc02c0_honbun_div(value, vertical=vertical)], "</div>", "honbun"


def _hc00b3_section_parts(code: str, *, vertical: bool) -> tuple[list[str], str | None, str | None]:
    value = _hc02c0_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return [], None, "state"
    if value == 0x0C:
        return ['<div class="header">'], "</div>", "header"
    return [_hc02c0_honbun_div(value, vertical=vertical)], "</div>", "honbun"


def _hc_gen_year_honbun_div() -> str:
    return '<div class="honbun" style="margin-left:0.000000em;">'


def _hc_gen_year_section_parts(code: str) -> tuple[list[str], str | None]:
    if code in HC_GEN_YEAR_NOOP_SECTION_CODES:
        return [], None
    if code == "0001":
        # HC02C4/HC02C7 use the following 1f41/1f61 span for the visible heading.
        return [], None
    if code == "000c":
        return ['<div class="footer">'], "</div>"
    return [_hc_gen_year_honbun_div()], "</div>"


def _hc02c2_icon_section_parts(code: str) -> list[str]:
    image_name = HC02C2_ICON_SECTION_IMAGES.get(code)
    if image_name is None:
        return []
    return [
        _hc02c2_honbun_div(),
        f'<img src="{image_name}" class="img_icon"/><br>',
        "</div>",
    ]


def _hc02c2_section_parts(code: str, previous_code: str | None) -> tuple[list[str], bool]:
    if code == "0001":
        return ['<div class="midashi">'], False

    parts: list[str] = []
    if code in HC02C2_ICON_SECTION_IMAGES and code != previous_code:
        parts.extend(_hc02c2_icon_section_parts(code))
    parts.append(_hc02c2_honbun_div())
    moji_down = code == "0007"
    if moji_down:
        parts.append('<p class="moji-down">')
    return parts, moji_down


def _safe_relative_path(value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _resolve_package_relative(source: DictionarySource, value: str) -> Path | None:
    relative = _safe_relative_path(value)
    if relative is None:
        return None
    for root in candidate_package_roots(source.idx):
        found = resolve_case_insensitive_path(root, relative)
        if found is not None and found.is_file():
            return found
    return None


def _resolve_package_relative_dir(source: DictionarySource, value: str) -> Path | None:
    relative = _safe_relative_path(value)
    if relative is None:
        return None
    for root in candidate_package_roots(source.idx):
        found = resolve_case_insensitive_path(root, relative)
        if found is not None and found.is_dir():
            return found
    return None


def _copy_package_asset(source: DictionarySource, value: str, out_dir: Path) -> bool:
    relative = _safe_relative_path(value)
    if relative is None:
        return False
    source_path = _resolve_package_relative(source, value)
    if source_path is None:
        return False
    target_path = out_dir / relative
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source_stat = source_path.stat()
    if not target_path.exists():
        shutil.copy2(source_path, target_path)
    else:
        target_stat = target_path.stat()
        if source_stat.st_size != target_stat.st_size or source_stat.st_mtime_ns != target_stat.st_mtime_ns:
            shutil.copy2(source_path, target_path)
    return True


def _renderer_css_name(renderer: HcRendererClassification | None) -> str | None:
    if renderer is None or renderer.code is None:
        return None
    try:
        value = int(renderer.code, 16)
    except ValueError:
        return None
    return f"Templates/{value:08X}.css"


def _normalise_hc_css(css: str) -> str:
    for placeholder, replacement in HC_CSS_DEFAULTS.items():
        css = css.replace(placeholder, replacement)
    return css


def _html_body_inner(text: str) -> str:
    match = re.search(r"<body\b[^>]*>(?P<body>.*)</body>", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group("body")
    return text


def _load_hc0190_templates(source: DictionarySource) -> dict[str, str]:
    templates: dict[str, str] = {}
    for key in ("b121", "b122", "b123", "b124"):
        rel = f"HTMLs/{key}.html"
        path = _resolve_package_relative(source, rel)
        if path is None:
            continue
        text = path.read_bytes().decode("cp932", errors="replace")
        templates[key] = _html_body_inner(text).replace("&cssPath;", "hc-renderer.css")
    return templates


def _load_hc00a0_templates(source: DictionarySource) -> dict[str, str]:
    templates: dict[str, str] = {}
    for key in ("Header", "Detail"):
        path = _resolve_package_relative(source, f"HTMLs/{key}.html")
        if path is None:
            continue
        templates[key] = path.read_bytes().decode("cp932", errors="replace")
    return templates


def _add_hc009c_image_sources(source: DictionarySource, dict_out: Path, output_sources: dict[str, str]) -> int:
    copied = 0
    for dirname, prefix in (
        ("images_thumb", "hc009c_thumb"),
        ("images_icon", "hc009c_icon"),
        ("images_icon_hanrei", "hc009c_hanrei"),
    ):
        directory = _resolve_package_relative_dir(source, dirname)
        if directory is None:
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file():
                continue
            rel = f"{dirname}/{path.name}"
            output_sources[f"{prefix}_{path.stem.lower()}"] = rel
            if _copy_package_asset(source, rel, dict_out):
                copied += 1
    return copied


def _prepare_hc_render_assets(
    source: DictionarySource,
    dict_out: Path,
    renderer: HcRendererClassification | None,
) -> tuple[dict[str, str], dict[str, str], str | None, int]:
    """Copy package assets needed by the generated standalone HC HTML."""

    copied = 0
    output_sources: dict[str, str] = {}
    for key, value in sorted((source.image_sources or {}).items()):
        output_sources[key] = value
        if _copy_package_asset(source, value, dict_out):
            copied += 1

    stylesheet_rel = _renderer_css_name(renderer)
    stylesheet_output = None
    css_parts = [HC_RENDER_BASE_CSS]
    if stylesheet_rel is not None:
        stylesheet_source = _resolve_package_relative(source, stylesheet_rel)
        if stylesheet_source is not None:
            css_parts.append(_normalise_hc_css(stylesheet_source.read_text(encoding="utf-8", errors="replace")))
    if css_parts:
        stylesheet_output = "hc-renderer.css"
        (dict_out / stylesheet_output).write_text("\n\n".join(css_parts) + "\n", encoding="utf-8")
        copied += 1
    html_templates: dict[str, str] = {}
    if renderer is not None and renderer.code.upper() == "0190":
        html_templates = _load_hc0190_templates(source)
    if renderer is not None and renderer.code.upper() == "00A0":
        html_templates = _load_hc00a0_templates(source)
    if renderer is not None and renderer.code.upper() == "009C":
        copied += _add_hc009c_image_sources(source, dict_out, output_sources)
    return output_sources, html_templates, stylesheet_output, copied


def _write_hc_html_header(html_out: Any, *, stylesheet: str | None, vertical: bool) -> None:
    body_class = "v" if vertical else "h"
    html_out.write("<!doctype html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n")
    if stylesheet:
        html_out.write(f'<link rel="stylesheet" href="{_escape_attr(stylesheet)}">\n')
    html_out.write(f"</head>\n<body class=\"{body_class}\">\n")


def _write_hc_html_footer(html_out: Any) -> None:
    html_out.write("</body>\n</html>\n")


def _replace_template_values(template: str, replacements: dict[str, str]) -> str:
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _hc00a0_rewrite_asset_sources(fragment: str, options: HcRenderOptions) -> str:
    def replace(match: re.Match[str]) -> str:
        quote = match.group(1)
        src = html.unescape(match.group(2))
        if "/" in src or "\\" in src or ":" in src:
            return match.group(0)
        key = Path(src).stem.lower()
        mapped = options.image_sources.get(key)
        if mapped is None:
            return match.group(0)
        return f'src={quote}{_escape_attr(mapped)}{quote}'

    return re.sub(r'src=(["\'])([^"\']+)\1', replace, fragment)


def _hc00a0_extract_private_directive(text: str, stats: Counter[str]) -> str | None:
    normalized = normalize_fullwidth_ascii(text).replace("：", ":")
    match = re.search(r"<PlaySound>(?P<name>[^<]+)</PlaySound>", normalized, flags=re.IGNORECASE)
    if not match:
        return None
    stats["hc00a0_play_sound_directives"] += 1
    return match.group("name").strip()


def _hc00a0_append_text(parts: list[str], text_parts: list[str], text: str) -> None:
    parts.append(_escape_text(text))
    text_parts.append(text)


def _render_hc00a0_body(data: bytes, options: HcRenderOptions) -> HcRenderResult:
    """Render HC00A0's phrase-detail template subset.

    The decompiled DLL collects 1f09 sections into string slots and feeds
    section 0001/0002 through HTMLs/Detail.html.  A private renderer directive
    supplies the MP3 filename with a fullwidth ``<PlaySound>`` tag.
    """

    sections: dict[str, list[str]] = {}
    section_text: dict[str, list[str]] = {}
    current_section: str | None = None
    style_stack: list[int] = []
    halfwidth_depth = 0
    stats: Counter[str] = Counter()
    gaps: set[str] = set()
    private_directives: list[dict[str, Any]] = []
    play_sound: str | None = None

    def parts_for(code: str | None) -> list[str]:
        return sections.setdefault(code or "0000", [])

    def text_for(code: str | None) -> list[str]:
        return section_text.setdefault(code or "0000", [])

    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0:
            i += 1
            continue
        if byte == 0x0A:
            parts_for(current_section).append("<br>")
            stats["line_breaks"] += 1
            i += 1
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
                current_section = payload.hex() if payload else "0000"
                parts_for(current_section)
                text_for(current_section)
                stats["section_markers"] += 1
                stats["hc00a0_sections_captured"] += 1
                i += 2 + arg_len
                continue
            if op == 0x0A:
                current_section = None
                i += 2 + arg_len
                continue
            if op == 0x41 or op == 0x61:
                stats["hc00a0_heading_controls"] += 1
                i += 2 + arg_len
                continue
            if op == 0x04:
                halfwidth_depth += 1
                i += 2 + arg_len
                continue
            if op == 0x05:
                halfwidth_depth = max(0, halfwidth_depth - 1)
                i += 2 + arg_len
                continue
            if op in {0x06, 0x0E, 0x12, 0xE0}:
                tag = {0x06: "sub", 0x0E: "sup", 0x12: "b", 0xE0: "b"}[op]
                parts_for(current_section).append(f"<{tag}>")
                style_stack.append(op)
                i += 2 + arg_len
                continue
            if op in {0x07, 0x0F, 0x13, 0xE1}:
                start_op = {0x07: 0x06, 0x0F: 0x0E, 0x13: 0x12, 0xE1: 0xE0}[op]
                tag = {0x06: "sub", 0x0E: "sup", 0x12: "b", 0xE0: "b"}[start_op]
                if start_op in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        popped_tag = {0x06: "sub", 0x0E: "sup", 0x12: "b", 0xE0: "b"}.get(popped)
                        if popped_tag:
                            parts_for(current_section).append(f"</{popped_tag}>")
                        if popped == start_op:
                            break
                else:
                    parts_for(current_section).append(f"</{tag}>")
                i += 2 + arg_len
                continue
            if op == 0xE2:
                end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                if end == -1:
                    stats["private_directives"] += 1
                    gaps.add("unterminated_private_directive")
                    break
                directive_bytes = data[i + 2 + arg_len : end]
                chars: list[str] = []
                j = 0
                while j + 1 < len(directive_bytes):
                    text = decode_jis_pair(directive_bytes[j : j + 2])
                    if text:
                        chars.append(text)
                    j += 2
                directive_text = "".join(chars)
                play = _hc00a0_extract_private_directive(directive_text, stats)
                if play:
                    play_sound = play
                private_directives.append(
                    {
                        "start_control": "1fe2",
                        "end_control": "1fe3",
                        "text_length": len(directive_text),
                        "kind": "play_sound" if play else "renderer_private",
                    }
                )
                stats["private_directives"] += 1
                i = end + 2 + control_arg_length(data, end)
                continue
            if op in LINK_START_OPS | LINK_END_OPS | AUDIO_START_OPS | AUDIO_END_OPS | MEDIA_OPS:
                stats["hc00a0_unsupported_inline_controls"] += 1
                gaps.add(f"hc00a0_unimplemented_control_1f{op:02x}")
                i += 2 + arg_len
                continue
            if op in VERTICAL_HINT_OPS | PRIVATE_RENDERER_DIRECTIVE_OPS | KNOWN_NONPRINTING_CONTROLS:
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
                _hc00a0_append_text(parts_for(current_section), text_for(current_section), value)
            else:
                stats["invalid_jis_pairs"] += 1
                gaps.add("invalid_jis_pair")
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= byte <= 0xFE:
            key = f"{byte:02x}{data[i + 1]:02x}"
            before = len(parts_for(current_section))
            _append_gaiji_value(parts_for(current_section), text_for(current_section), key, options, stats)
            if len(parts_for(current_section)) == before:
                gaps.add(f"hc00a0_unresolved_gaiji_{key}")
            i += 2
            continue

        stats["unknown_bytes"] += 1
        i += 1

    english_html = "".join(sections.get("0001", []))
    japanese_html = "".join(sections.get("0002", []))
    english_text = "".join(section_text.get("0001", []))
    japanese_text = "".join(section_text.get("0002", []))
    detail_no = Path(play_sound or "0000.mp3").stem
    if not re.fullmatch(r"\d{4}", detail_no):
        detail_no = "0000"
    if play_sound is None:
        gaps.add("hc00a0_missing_play_sound_directive")

    replacements = {
        "$$DISPLAY_MIDASHI$$": "none",
        "$$MIDASHI$$": "",
        "$$SUBMIDASHI$$": "",
        "$$MODE_EJ$$": "1",
        "$$MODE_E$$": "2",
        "$$MODE_J$$": "2",
        "$$MODE_ALL$$": "1",
        "$$MODE_OK$$": "2",
        "$$MODE_QU$$": "2",
        "$$MODE_EX$$": "2",
        "$$DISPLAY_DETAIL$$": "inline",
        "$$DISPLAY_ENGLISH$$": "inline",
        "$$DISPLAY_JAPANESE$$": "inline",
        "$$DETAIL_PLAY$$": play_sound or "",
        "$$DETAIL_PLAYNUM$$": "0",
        "$$DETAIL_EX$$": "2",
        "$$DETAIL_OK$$": "2",
        "$$JAPANESE$$": japanese_html,
        "$$ENGLISH$$": english_html,
        "$$LINE_NO$$": detail_no,
        "$$DETAIL_NO$$": detail_no,
    }
    header = _replace_template_values(options.html_templates.get("Header", HC00A0_HEADER_FALLBACK), replacements)
    detail = _replace_template_values(options.html_templates.get("Detail", HC00A0_DETAIL_FALLBACK), replacements)
    html_body = _hc00a0_rewrite_asset_sources(header + "\n" + detail, options)
    stats["hc00a0_detail_rows"] += 1
    if play_sound:
        stats["hc00a0_audio_links"] += 1
    plain = "\n".join(part for part in (english_text, japanese_text) if part)
    return HcRenderResult(
        html=f'<div class="lv-hc-render hc00a0">{html_body}</div>',
        plain=plain,
        stats=dict(stats),
        audio=(
            {
                "target": {
                    "component": "mp3",
                    "resource_id": f"file:mp3/{play_sound}",
                    "path": f"mp3/{play_sound}",
                },
                "status": "file_reference",
            },
        )
        if play_sound
        else (),
        private_directives=tuple(private_directives),
        named_behavior_gaps=tuple(sorted(gaps)),
    )


def render_hc_body(data: bytes, options: HcRenderOptions | None = None) -> HcRenderResult:
    """Render one expanded HONMON body slice with common HC semantics."""

    options = options or HcRenderOptions()
    if _renderer_code(options) == "00A0":
        return _render_hc00a0_body(data, options)
    section_rules = _renderer_section_rules(options)
    active_section_image_rules: set[str] = set()
    root_parts: list[str] = []
    contexts: list[_Context] = []
    style_stack: list[int] = []
    hc0158_marker_stack: list[tuple[str, str]] = []
    hc0157_marker_stack: list[tuple[str, str]] = []
    hc0146_marker_stack: list[tuple[str, str]] = []
    hc00c6_marker_stack: list[tuple[str, str]] = []
    hc009d_marker_stack: list[tuple[str, str]] = []
    hc0141_marker_stack: list[tuple[str, str]] = []
    hc0144_marker_stack: list[tuple[str, str]] = []
    hc0145_marker_stack: list[tuple[str, str]] = []
    hc03e8_marker_stack: list[tuple[str, str]] = []
    hc02be_marker_stack: list[tuple[str, str]] = []
    hc02bc_marker_stack: list[tuple[str, str]] = []
    hc012e_marker_stack: list[tuple[str, str]] = []
    stats: Counter[str] = Counter()
    links: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    private_directives: list[dict[str, Any]] = []
    gaps: set[str] = set()
    halfwidth_depth = 0
    hc00c6_section_open = False
    hc00c6_example_block_active = False
    hc00c6_supab_pending = False
    hc009d_section_close: str | None = None
    hc009d_current_section_value: int | None = None
    hc009b_section_close: str | None = None
    hc009c_section_close: str | None = None
    hc009c_marker_stack: list[str] = []
    hc02c5_section_close: str | None = None
    hc02c5_current_section: str | None = None
    hc02c5_hr_seen = False
    hc0151_section_close: str | None = None
    hc0151_current_section: str | None = None
    hc0151_table_open = False
    hc0151_contents_open = False
    hc0151_small_depth = 0
    hc0142_honbun_open = False
    hc0142_current_section: str | None = None
    hc0142_marker_stack: list[tuple[str, str]] = []
    hc02bc_section_open = False
    hc02be_section_open = False
    hc0190_sections: dict[int, str] = {}
    hc0190_template_key: str | None = None
    hc012d_section_close: str | None = None
    hc012d_pending_honbun_user = False
    hc013d_section_close: str | None = None
    hc0141_section_close: str | None = None
    hc0144_section_close: str | None = None
    hc0145_section_close: str | None = None
    hc_hkdksr_medical_section_close: str | None = None
    hc_hkdksr_medical_table_open = False
    hc03e8_section_close: str | None = None
    hc00a6_section_close: str | None = None
    hc00a6_ruby_readings: list[str] = []
    hc012f_section_close: str | None = None
    hc012f_current_section: str | None = None
    hc0131_section_close: str | None = None
    hc012e_section_close: str | None = None
    hc012e_current_section: str | None = None
    hc02c1_section_close: str | None = None
    hc02c1_current_section: str | None = None
    hc02c1_moji_down_open = False
    hc02c1_section_just_opened = False
    hc02bf_section_close: str | None = None
    hc02bf_current_section: str | None = None
    hc02bf_moji_down_open = False
    hc02bf_section_just_opened = False
    hc02c0_section_close: str | None = None
    hc013c_section_close: str | None = None
    hc00b3_section_close: str | None = None
    hc_gen_year_section_close: str | None = None
    hc02c2_section_open = False
    hc02c2_moji_down_open = False
    hc02c2_current_section: str | None = None
    hc0065_midashi_open = False
    hc0065_body_open = False
    hc0048_section_close: str | None = None
    hc0048_midashi_open = False
    hc0048_honbun_open = False
    hc0048_media_div_open = False
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
                    if _renderer_code(options) == "0190":
                        _hc0190_close_section(contexts, hc0190_sections, stats)
                        section = int.from_bytes(payload, "big")
                        contexts.append(
                            _Context(
                                kind="hc0190_section",
                                start_op=op,
                                payload=payload,
                                parent=root_parts,
                                start_offset=section,
                            )
                        )
                        stats["hc0190_section_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    root = _current_parts(root_parts, contexts)
                    if _renderer_code(options) == "009B":
                        if hc009b_section_close is not None:
                            root.append(hc009b_section_close)
                            hc009b_section_close = None
                        section_parts, hc009b_section_close, state = _hc009b_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc009b_section_blocks"] += 1
                        if state:
                            stats[f"hc009b_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "009C":
                        _hc009c_close_section(root, hc009c_marker_stack, hc009c_section_close)
                        section_parts, hc009c_section_close = _hc009c_section_parts(code, options)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc009c_section_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02C5":
                        if hc02c5_section_close is not None:
                            root.append(hc02c5_section_close)
                            hc02c5_section_close = None
                        hc02c5_current_section = code
                        section_parts, hc02c5_section_close, hc02c5_hr_seen = _hc02c5_section_parts(
                            code, hr_seen=hc02c5_hr_seen
                        )
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc02c5_section_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0048":
                        if hc0048_midashi_open:
                            root.append("</div>")
                            hc0048_midashi_open = False
                        if hc0048_honbun_open:
                            root.append("</div>")
                            hc0048_honbun_open = False
                        if hc0048_section_close is not None:
                            root.append(hc0048_section_close)
                            hc0048_section_close = None
                        value = _hc02c0_section_value(code)
                        if value is not None:
                            root.append(f'<div style="margin: {value * 3}px">')
                            hc0048_section_close = "</div>"
                            stats["hc0048_margin_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0151":
                        if hc0151_section_close is not None:
                            root.append(hc0151_section_close)
                            hc0151_section_close = None
                        hc0151_current_section = code
                        section_parts, hc0151_section_close, hc0151_table_open = _hc0151_section_parts(
                            code, table_open=hc0151_table_open
                        )
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0151_section_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0142":
                        hc0142_current_section = code
                        if code in HC0142_NOOP_SECTION_CODES:
                            stats["hc0142_noop_sections"] += 1
                        else:
                            stats["hc0142_section_states"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "009D":
                        if hc009d_section_close is not None:
                            root.append(hc009d_section_close)
                            hc009d_section_close = None
                        if hc009d_current_section_value == 1 and _hc009d_section_value(code) != 1:
                            root.append('<div class="contents_body">')
                            hc009d_marker_stack.append(("__contents_body__", "</div>"))
                        section_parts, section_close, section_value, wrapper_stack = _hc009d_section_parts(code, data, i, options)
                        root.extend(section_parts)
                        hc009d_section_close = section_close
                        hc009d_current_section_value = section_value
                        if wrapper_stack is not None and (not hc009d_marker_stack or hc009d_marker_stack[-1] != wrapper_stack):
                            hc009d_marker_stack.append(wrapper_stack)
                        if section_parts:
                            stats["hc009d_section_blocks"] += 1
                    if _renderer_code(options) == "00C6":
                        if hc00c6_section_open:
                            root.append("</div>")
                            hc00c6_section_open = False
                        if code not in {"0007", "0008"}:
                            hc00c6_example_block_active = False
                        if code == "0007" and not hc00c6_example_block_active:
                            image_src = _section_image_src(
                                _SectionImageRule(
                                    image_key="exam",
                                    css_class="ex_img",
                                    group_codes=frozenset({"0007", "0008"}),
                                ),
                                options.image_sources,
                            )
                            if image_src is None:
                                gaps.add("missing_section_image_exam")
                            else:
                                root.append(f'<img src="{_escape_attr(image_src)}" class="ex_img"><br>')
                                stats["section_images"] += 1
                            hc00c6_example_block_active = True
                        div_html = HC00C6_SECTION_DIVS.get(code)
                        if div_html is not None:
                            root.append(div_html)
                            hc00c6_section_open = True
                            stats["hc00c6_section_divs"] += 1
                    if _renderer_code(options) == "00A6":
                        if hc00a6_section_close is not None:
                            root.append(hc00a6_section_close)
                            hc00a6_section_close = None
                        section_parts, hc00a6_section_close = _hc00a6_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00a6_section_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if _is_hc_hkdksr_medical_renderer(options):
                        if hc_hkdksr_medical_section_close is not None:
                            root.append(hc_hkdksr_medical_section_close)
                            hc_hkdksr_medical_section_close = None
                        section_parts, section_close, state = _hc_hkdksr_medical_section_parts(code)
                        if state not in {"table_cell"} and hc_hkdksr_medical_table_open:
                            root.append("</td></tr></table>")
                            hc_hkdksr_medical_table_open = False
                        root.extend(section_parts)
                        if state == "table_open":
                            hc_hkdksr_medical_table_open = True
                        elif state == "table_cell":
                            hc_hkdksr_medical_table_open = False
                            hc_hkdksr_medical_section_close = section_close
                        else:
                            hc_hkdksr_medical_section_close = section_close
                        if section_parts:
                            stats["hc_hkdksr_medical_section_blocks"] += 1
                        if state:
                            stats[f"hc_hkdksr_medical_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02BE":
                        if hc02be_section_open:
                            root.append("</div>")
                            hc02be_section_open = False
                        root.append(f'<div class="ind_{_escape_attr(code)}">')
                        hc02be_section_open = True
                        stats["hc02be_section_divs"] += 1
                    if _renderer_code(options) == "02BC":
                        if hc02bc_section_open:
                            root.append("</div>")
                            hc02bc_section_open = False
                        section_parts = _hc02bc_section_parts(code, options.image_sources)
                        root.extend(section_parts)
                        if section_parts and section_parts[-1].startswith("<div"):
                            hc02bc_section_open = True
                        if section_parts:
                            stats["hc02bc_section_divs"] += 1
                    if _renderer_code(options) == "012D":
                        hc012d_pending_honbun_user = False
                        if hc012d_section_close is not None:
                            root.append(hc012d_section_close)
                            hc012d_section_close = None
                        section_parts = _hc012d_section_parts(code, data, i)
                        root.extend(section_parts)
                        hc012d_section_close = _hc012d_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc012d_section_blocks"] += 1
                    if _renderer_code(options) == "013D":
                        if hc013d_section_close is not None:
                            root.append(hc013d_section_close)
                            hc013d_section_close = None
                        section_parts, section_close = _hc013d_section_parts(code)
                        root.extend(section_parts)
                        hc013d_section_close = section_close
                        if section_parts:
                            stats["hc013d_section_blocks"] += 1
                    if _renderer_code(options) == "0144":
                        if hc0144_section_close is not None:
                            root.append(hc0144_section_close)
                            hc0144_section_close = None
                        section_parts = _hc0144_section_parts(code)
                        root.extend(section_parts)
                        hc0144_section_close = _hc0144_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc0144_section_blocks"] += 1
                    if _renderer_code(options) == "0145":
                        if hc0145_section_close is not None:
                            root.append(hc0145_section_close)
                            hc0145_section_close = None
                        section_parts = _hc0145_section_parts(code)
                        root.extend(section_parts)
                        hc0145_section_close = _hc0145_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc0145_section_blocks"] += 1
                    if _renderer_code(options) == "03E8":
                        if hc03e8_section_close is not None:
                            root.append(hc03e8_section_close)
                            hc03e8_section_close = None
                        section_parts = _hc03e8_section_parts(code)
                        root.extend(section_parts)
                        hc03e8_section_close = _hc03e8_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc03e8_section_blocks"] += 1
                    if _renderer_code(options) == "0141":
                        if hc0141_section_close is not None:
                            root.append(hc0141_section_close)
                            hc0141_section_close = None
                        section_parts = _hc0141_section_parts(code)
                        root.extend(section_parts)
                        hc0141_section_close = _hc0141_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc0141_section_blocks"] += 1
                    if _renderer_code(options) == "012E":
                        if hc012e_section_close is not None:
                            root.append(hc012e_section_close)
                            hc012e_section_close = None
                        hc012e_current_section = code
                        section_parts = _hc012e_section_parts(code, options)
                        root.extend(section_parts)
                        hc012e_section_close = _hc012e_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc012e_section_blocks"] += 1
                            if "hitsujun start" in "".join(section_parts):
                                stats["hc012e_hitsujun_sections"] += 1
                    if _renderer_code(options) == "012F":
                        if hc012f_section_close is not None:
                            root.append(hc012f_section_close)
                            hc012f_section_close = None
                        hc012f_current_section = code
                        section_parts, hc012f_section_close = _hc012f_section_parts(code, options)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc012f_section_blocks"] += 1
                        if code in HC012F_NOOP_SECTION_CODES:
                            stats["hc012f_noop_sections"] += 1
                        if code == "0003":
                            stats["hc012f_bunnya_sections"] += 1
                        if code in {"0004", "0005"}:
                            stats["hc012f_link_icon_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0131":
                        if hc0131_section_close is not None:
                            root.append(hc0131_section_close)
                            hc0131_section_close = None
                        section_parts, hc0131_section_close = _hc0131_section_parts(code, data, i, options)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0131_section_blocks"] += 1
                        else:
                            stats["hc0131_state_sections"] += 1
                        if code == "0012":
                            stats["hc0131_content_ind18_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02C1":
                        if hc02c1_moji_down_open:
                            root.append("</p>")
                            hc02c1_moji_down_open = False
                        if hc02c1_section_close is not None:
                            root.append(hc02c1_section_close)
                            hc02c1_section_close = None
                        icon_emitted = code in HC02C1_ICON_SECTION_IMAGES and code != hc02c1_current_section
                        section_parts, hc02c1_section_close = _hc02c1_section_parts(code, hc02c1_current_section)
                        root.extend(section_parts)
                        hc02c1_current_section = code
                        hc02c1_section_just_opened = bool(section_parts)
                        if section_parts:
                            stats["hc02c1_section_blocks"] += 1
                        if code in HC02C1_NOOP_SECTION_CODES:
                            stats["hc02c1_noop_sections"] += 1
                        if icon_emitted:
                            stats["hc02c1_section_icons"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02BF":
                        if hc02bf_moji_down_open:
                            root.append("</p>")
                            hc02bf_moji_down_open = False
                        if hc02bf_section_close is not None:
                            root.append(hc02bf_section_close)
                            hc02bf_section_close = None
                        icon_emitted = code in HC02BF_ICON_SECTION_IMAGES and code != hc02bf_current_section
                        section_parts, hc02bf_section_close = _hc02bf_section_parts(code, hc02bf_current_section)
                        root.extend(section_parts)
                        hc02bf_current_section = code
                        hc02bf_section_just_opened = bool(section_parts)
                        if section_parts:
                            stats["hc02bf_section_blocks"] += 1
                        if code in HC02BF_NOOP_SECTION_CODES:
                            stats["hc02bf_noop_sections"] += 1
                        if icon_emitted:
                            stats["hc02bf_section_icons"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02C0":
                        if hc02c0_section_close is not None:
                            root.append(hc02c0_section_close)
                            hc02c0_section_close = None
                        section_parts, hc02c0_section_close, state = _hc02c0_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc02c0_section_blocks"] += 1
                        if state:
                            stats[f"hc02c0_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02CA":
                        if hc02c0_section_close is not None:
                            root.append(hc02c0_section_close)
                            hc02c0_section_close = None
                        section_parts, hc02c0_section_close, state = _hc02c0_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc02ca_section_blocks"] += 1
                        if state:
                            stats[f"hc02ca_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0136":
                        if hc02c0_section_close is not None:
                            root.append(hc02c0_section_close)
                            hc02c0_section_close = None
                        section_parts, hc02c0_section_close, state = _hc02c0_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0136_section_blocks"] += 1
                        if state:
                            stats[f"hc0136_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "013C":
                        if hc013c_section_close is not None:
                            root.append(hc013c_section_close)
                            hc013c_section_close = None
                        section_parts, hc013c_section_close, state = _hc02c0_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc013c_section_blocks"] += 1
                        if state:
                            stats[f"hc013c_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00B3":
                        if hc00b3_section_close is not None:
                            root.append(hc00b3_section_close)
                            hc00b3_section_close = None
                        section_parts, hc00b3_section_close, state = _hc00b3_section_parts(code, vertical=options.vertical)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00b3_section_blocks"] += 1
                        if state:
                            stats[f"hc00b3_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _is_hc_gen_year_renderer(options):
                        if hc_gen_year_section_close is not None:
                            root.append(hc_gen_year_section_close)
                            hc_gen_year_section_close = None
                        section_parts, hc_gen_year_section_close = _hc_gen_year_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc_gen_year_section_blocks"] += 1
                        if code in HC_GEN_YEAR_NOOP_SECTION_CODES:
                            stats["hc_gen_year_noop_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02C2":
                        if hc02c2_section_open:
                            if hc02c2_moji_down_open:
                                root.append("</p>")
                                hc02c2_moji_down_open = False
                            root.append("</div>")
                            hc02c2_section_open = False
                        icon_emitted = code in HC02C2_ICON_SECTION_IMAGES and code != hc02c2_current_section
                        section_parts, moji_down_open = _hc02c2_section_parts(code, hc02c2_current_section)
                        root.extend(section_parts)
                        hc02c2_current_section = code
                        hc02c2_section_open = bool(section_parts)
                        hc02c2_moji_down_open = moji_down_open
                        if section_parts:
                            stats["hc02c2_section_blocks"] += 1
                        if icon_emitted:
                            stats["hc02c2_section_icons"] += 1
                    if _renderer_code(options) == "0065" and code == "0001" and not hc0065_midashi_open and not hc0065_body_open:
                        root.append('<div class="midashi">')
                        hc0065_midashi_open = True
                        stats["hc0065_midashi_blocks"] += 1
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
                if _renderer_code(options) == "0190" and _hc0190_close_section(contexts, hc0190_sections, stats):
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "009C":
                    _hc009c_close_section(_current_parts(root_parts, contexts), hc009c_marker_stack, hc009c_section_close)
                    hc009c_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0048":
                    parts = _current_parts(root_parts, contexts)
                    if hc0048_midashi_open:
                        parts.append("</div>")
                        hc0048_midashi_open = False
                        if not hc0048_honbun_open:
                            parts.append('<div class="honbun">')
                            hc0048_honbun_open = True
                            stats["hc0048_honbun_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "02C5" and hc02c5_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc02c5_section_close)
                    hc02c5_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0151":
                    if hc0151_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc0151_section_close)
                        hc0151_section_close = None
                    elif hc0151_current_section in HC0151_NOOP_SECTION_CODES:
                        i += 2 + arg_len
                        continue
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0142":
                    root = _current_parts(root_parts, contexts)
                    if 0x41 in style_stack:
                        while style_stack:
                            popped = style_stack.pop()
                            close_tag = _style_close_tag(popped, options)
                            if close_tag:
                                root.append(f"</{close_tag}>")
                            if popped == 0x41:
                                break
                        if not hc0142_honbun_open:
                            root.append('<div class="honbun" style="margin-left:0.000000em;">')
                            hc0142_honbun_open = True
                            stats["hc0142_honbun_blocks"] += 1
                    else:
                        root.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "009D" and hc009d_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc009d_section_close)
                    hc009d_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "009B" and hc009b_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc009b_section_close)
                    hc009b_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "00C6" and hc00c6_section_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00c6_section_open = False
                if _renderer_code(options) == "00A6" and hc00a6_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc00a6_section_close)
                    hc00a6_section_close = None
                    i += 2 + arg_len
                    continue
                if _is_hc_hkdksr_medical_renderer(options):
                    if hc_hkdksr_medical_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc_hkdksr_medical_section_close)
                        hc_hkdksr_medical_section_close = None
                        i += 2 + arg_len
                        continue
                    if hc_hkdksr_medical_table_open:
                        # These table rows are encoded as a 0042 label section
                        # followed by a 0043 body section, often with a 1f0a
                        # separator between them. Keep the first cell open until
                        # the next section decides whether to transition to
                        # td_pc2 or close the row.
                        i += 2 + arg_len
                        continue
                if _renderer_code(options) == "02BE" and hc02be_section_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc02be_section_open = False
                if _renderer_code(options) == "02BC" and hc02bc_section_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc02bc_section_open = False
                if _renderer_code(options) == "012D" and hc012d_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc012d_section_close)
                    hc012d_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "012D" and hc012d_pending_honbun_user:
                    hc012d_pending_honbun_user = False
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "013D" and hc013d_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc013d_section_close)
                    hc013d_section_close = None
                if _renderer_code(options) == "0144" and hc0144_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc0144_section_close)
                    hc0144_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0145" and hc0145_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc0145_section_close)
                    hc0145_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "03E8" and hc03e8_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc03e8_section_close)
                    hc03e8_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0141" and hc0141_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc0141_section_close)
                    hc0141_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "012E" and hc012e_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc012e_section_close)
                    hc012e_section_close = None
                if _renderer_code(options) == "012F":
                    if hc012f_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc012f_section_close)
                        hc012f_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0131":
                    if hc0131_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc0131_section_close)
                        hc0131_section_close = None
                    else:
                        _current_parts(root_parts, contexts).append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "02C1":
                    if hc02c1_moji_down_open:
                        _current_parts(root_parts, contexts).append("</p>")
                        hc02c1_moji_down_open = False
                    if hc02c1_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc02c1_section_close)
                        hc02c1_section_close = None
                        hc02c1_section_just_opened = False
                        i += 2 + arg_len
                        continue
                if _renderer_code(options) == "02BF":
                    if hc02bf_moji_down_open:
                        _current_parts(root_parts, contexts).append("</p>")
                        hc02bf_moji_down_open = False
                    if hc02bf_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc02bf_section_close)
                        hc02bf_section_close = None
                        hc02bf_section_just_opened = False
                        i += 2 + arg_len
                        continue
                if _renderer_code(options) == "02C0" and hc02c0_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc02c0_section_close)
                    hc02c0_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "013C" and hc013c_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc013c_section_close)
                    hc013c_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "00B3" and hc00b3_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc00b3_section_close)
                    hc00b3_section_close = None
                    i += 2 + arg_len
                    continue
                if _is_hc_gen_year_renderer(options) and hc_gen_year_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc_gen_year_section_close)
                    hc_gen_year_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "02C2" and hc02c2_section_open:
                    if hc02c2_moji_down_open:
                        _current_parts(root_parts, contexts).append("</p>")
                        hc02c2_moji_down_open = False
                    _current_parts(root_parts, contexts).append("</div>")
                    hc02c2_section_open = False
                _current_parts(root_parts, contexts).append("<br>")
                stats["line_breaks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0157" and op in {0x12, 0x13}:
                stats["hc0157_noop_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0142" and op in HC0142_NONPRINTING_CONTROL_OPS:
                stats["hc0142_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0142" and op == 0x10:
                if _jis_key_at(data, i + 2) == "2372" and _jis_key_at(data, i + 4) == "2375":
                    src = _image_source_for_key("rubar", options) or "rubar.png"
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_mark">'
                    )
                    stats["hc0142_rubar_markers"] += 1
                    i += 2 + arg_len + 4
                else:
                    _current_parts(root_parts, contexts).append('<label class="overline">')
                    style_stack.append(op)
                    stats["hc0142_overline_markers"] += 1
                    i += 2 + arg_len
                continue

            if _renderer_code(options) == "0142" and op == 0x11:
                if 0x10 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        if popped == 0x10:
                            _current_parts(root_parts, contexts).append("</label>")
                            break
                        close_tag = _style_close_tag(popped, options)
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if (
                _renderer_code(options) == "00C6"
                and op == 0x04
                and i + 5 < len(data)
                and data[i + 4 : i + 6] == b"\x1f\x05"
            ):
                close_key = f"{data[i + 2]:02x}{data[i + 3]:02x}"
                if close_key in HC00C6_CLOSE_MARKERS or close_key in HC00C6_NOOP_MARKERS:
                    stats["hc00c6_noop_markers"] += 1
                    i += 6
                    continue

            if _renderer_code(options) == "012E" and op in HC012E_NONPRINTING_CONTROL_OPS:
                stats["hc012e_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C2" and op in HC02C2_NONPRINTING_CONTROL_OPS:
                stats["hc02c2_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C1" and op in HC02C1_NONPRINTING_CONTROL_OPS:
                stats["hc02c1_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02BF" and op in HC02BF_NONPRINTING_CONTROL_OPS:
                stats["hc02bf_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C0" and op in HC02C0_NONPRINTING_CONTROL_OPS:
                stats["hc02c0_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue
            if _renderer_code(options) == "02CA" and op in HC02CA_NONPRINTING_CONTROL_OPS:
                stats["hc02ca_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue
            if _renderer_code(options) == "0136" and op in HC0136_NONPRINTING_CONTROL_OPS:
                stats["hc0136_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "013C" and op in HC013C_NONPRINTING_CONTROL_OPS:
                stats["hc013c_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00B3" and op in HC00B3_NONPRINTING_CONTROL_OPS:
                stats["hc00b3_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "012F" and op in HC012F_NONPRINTING_CONTROL_OPS:
                stats["hc012f_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0131" and op in HC0131_NONPRINTING_CONTROL_OPS:
                stats["hc0131_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _is_hc_gen_year_renderer(options) and op in HC_GEN_YEAR_NONPRINTING_CONTROL_OPS:
                stats["hc_gen_year_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A6" and op in HC00A6_NONPRINTING_CONTROL_OPS:
                stats["hc00a6_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _is_hc_hkdksr_medical_renderer(options) and op in HC_HKDKSR_MEDICAL_NONPRINTING_CONTROL_OPS:
                stats["hc_hkdksr_medical_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "012D" and op in HC012D_NONPRINTING_CONTROL_OPS:
                stats["hc012d_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "009D" and op in HC009D_NONPRINTING_CONTROL_OPS:
                stats["hc009d_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "009B" and op in HC009B_NONPRINTING_CONTROL_OPS:
                stats["hc009b_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "009C" and op in HC009C_NONPRINTING_CONTROL_OPS:
                stats["hc009c_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C5" and op in HC02C5_NONPRINTING_CONTROL_OPS:
                stats["hc02c5_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0151" and op in HC0151_NONPRINTING_CONTROL_OPS:
                stats["hc0151_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "013D" and op in HC013D_NONPRINTING_CONTROL_OPS:
                stats["hc013d_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0145" and op == 0x41:
                stats["hc0145_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0144" and op == 0x41:
                stats["hc0144_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "03E8" and op == 0x41:
                stats["hc03e8_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0141" and op == 0x41:
                stats["hc0141_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "009D" and op == 0x41:
                stats["hc009d_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0065" and op == 0x41:
                if hc0065_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc0065_midashi_open = False
                if not hc0065_body_open:
                    _current_parts(root_parts, contexts).append('<div class="contents_body">')
                    hc0065_body_open = True
                    stats["hc0065_contents_body_blocks"] += 1
                stats["hc0065_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0065" and op in HC0065_NONPRINTING_CONTROL_OPS:
                stats["hc0065_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0048" and op in {0x5C, 0x6D}:
                if hc0048_media_div_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc0048_media_div_open = False
                    stats["hc0048_media_div_closures"] += 1
                stats["hc0048_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0048" and op in HC0048_NONPRINTING_CONTROL_OPS:
                stats["hc0048_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0131" and op == 0x41:
                _current_parts(root_parts, contexts).append('<div class="midashi">')
                style_stack.append(op)
                stats["headings"] += 1
                stats["hc0131_heading_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0131" and op == 0x61:
                if 0x41 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        if popped == 0x41:
                            _current_parts(root_parts, contexts).append("</div>")
                            break
                        close_tag = _style_close_tag(popped, options)
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                stats["hc0131_heading_closures"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0131" and op == 0x06:
                _current_parts(root_parts, contexts).append('<span class="sizedown"><sub>')
                style_stack.append(op)
                stats["hc0131_sizedown_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0131" and op == 0x07:
                if 0x06 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        if popped == 0x06:
                            _current_parts(root_parts, contexts).append("</sub></span>")
                            break
                        close_tag = _style_close_tag(popped, options)
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C5" and op == 0x41:
                if hc02c5_current_section == "0046":
                    _current_parts(root_parts, contexts).append('<div class="CB_Title">')
                    cb_src = _image_source_for_key("cb_w", options)
                    if cb_src is not None:
                        _current_parts(root_parts, contexts).append(
                            f'<img src="{_escape_attr(cb_src)}" class="page">'
                        )
                else:
                    _current_parts(root_parts, contexts).append('<div class="midashi"><!-- INDEX_MENU -->')
                style_stack.append(op)
                stats["headings"] += 1
                stats["hc02c5_heading_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0151" and op == 0x41:
                _current_parts(root_parts, contexts).append('<div class="midashi">')
                style_stack.append(op)
                stats["headings"] += 1
                stats["hc0151_heading_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0151" and op == 0x61:
                if 0x41 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x41:
                            _current_parts(root_parts, contexts).append("</div>")
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                if not hc0151_contents_open:
                    _current_parts(root_parts, contexts).append('<div class="contents">')
                    hc0151_contents_open = True
                    stats["hc0151_contents_blocks"] += 1
                i += 2 + arg_len
                continue

            style_spec = _style_start_spec(op, options)
            if style_spec is not None:
                if _renderer_code(options) == "012D" and hc012d_pending_honbun_user:
                    _current_parts(root_parts, contexts).append('<div class="honbun_user">')
                    hc012d_section_close = "</div>"
                    hc012d_pending_honbun_user = False
                tag, attrs = style_spec
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
                close_tag = _style_close_tag(start_op, options)
                closed_requested_style = False
                if close_tag and start_op in style_stack:
                    if _renderer_code(options) == "00C6" and op == 0x05:
                        while hc00c6_marker_stack and hc00c6_marker_stack[-1][0] in HC00C6_CLOSE_MARKERS:
                            _current_parts(root_parts, contexts).append(hc00c6_marker_stack.pop()[1])
                            stats["hc00c6_style_markers"] += 1
                    while style_stack:
                        popped = style_stack.pop()
                        popped_tag = _style_close_tag(popped, options)
                        if popped_tag:
                            _current_parts(root_parts, contexts).append(f"</{popped_tag}>")
                        if popped == start_op:
                            closed_requested_style = True
                            break
                if _renderer_code(options) == "012E" and op == 0x61 and closed_requested_style:
                    _current_parts(root_parts, contexts).append('<div class="honbun_user">')
                    hc012e_section_close = "</div>"
                if _renderer_code(options) == "012D" and op == 0x61 and closed_requested_style:
                    hc012d_pending_honbun_user = True
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

            if _renderer_code(options) == "012F" and op == 0x62:
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
                if hc012f_current_section == "0003":
                    bunnya_key = _hc012f_bunnya_key_from_html(label)
                    if bunnya_key:
                        label = _hc012f_bunnya_image_html(bunnya_key, options)
                        stats["hc012f_bunnya_images"] += 1
                    elif not label:
                        label = "link"
                elif not label:
                    label = "link"
                attrs = [
                    f'class="{_escape_attr(_link_css_class(options, ctx.start_op if ctx else None))}"',
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
                    f'class="{_escape_attr(_link_css_class(options, ctx.start_op if ctx else None))}"',
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
                sound_src = _sound_image_src(options.image_sources)
                audio_class = "lv-hc-audio"
                image_class = "img_mark2"
                if _renderer_code(options) == "02C5":
                    sound_src = _image_source_for_key("dummy", options) or sound_src
                    audio_class = "lv-hc-audio lLink"
                    image_class = "im"
                if sound_src:
                    label = f'<img src="{_escape_attr(sound_src)}" class="{_escape_attr(image_class)}">'
                    stats["audio_images"] += 1
                attrs = [
                    f'class="{_escape_attr(audio_class)}"',
                    f'href="{_escape_attr(_audio_href(target))}"',
                    f'data-lv-audio-status="{_escape_attr("resolved_range" if target else "unresolved_range")}"',
                ]
                if target:
                    attrs.append(f'data-lv-resource="{_escape_attr(target["resource_id"])}"')
                parent = ctx.parent if ctx else _current_parts(root_parts, contexts)
                parent.append(f"<a {' '.join(attrs)}>{label}</a>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0048" and op == 0x4D:
                pointer = parse_media_pointer(payload) if len(payload) == 18 else None
                target = _media_target(pointer)
                control = f"1f{op:02x}"
                media.append(
                    {
                        "control": control,
                        "target": target,
                        "status": "resolved_address" if target else "unresolved_payload",
                    }
                )
                if hc0048_media_div_open:
                    _current_parts(root_parts, contexts).append("</div>")
                _current_parts(root_parts, contexts).append(f"<div>{_media_placeholder_html(control, target)}")
                hc0048_media_div_open = True
                stats["media_placeholders"] += 1
                stats["hc0048_media_divs"] += 1
                i += 2 + arg_len
                continue

            if op in MEDIA_OPS:
                pointer = parse_media_pointer(payload) if len(payload) == 18 else None
                target = _media_target(pointer)
                control = f"1f{op:02x}"
                media.append(
                    {
                        "control": control,
                        "target": target,
                        "status": "resolved_address" if target else "unresolved_payload",
                    }
                )
                _current_parts(root_parts, contexts).append(_media_placeholder_html(control, target))
                stats["media_placeholders"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C0" and op in PRIVATE_START_OPS:
                directive = payload.hex()
                icon_name = HC02C0_ICON_DIRECTIVES.get(directive)
                if icon_name is not None:
                    src = _image_or_named_template(icon_name.removesuffix(".png"), options)
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_icon"/><br>'
                    )
                    stats["hc02c0_private_icons"] += 1
                    private_directives.append(
                        {
                            "start_control": "1fe2",
                            "end_control": "1fe3",
                            "directive": directive,
                            "status": "rendered_icon",
                        }
                    )
                    end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                    i = (end + 2) if end != -1 else (i + 2 + arg_len)
                    continue

            if _renderer_code(options) == "02CA" and op in PRIVATE_START_OPS:
                directive = payload.hex()
                icon_name = HC02CA_ICON_DIRECTIVES.get(directive)
                if icon_name is not None:
                    src = _image_or_named_template(icon_name.removesuffix(".png"), options)
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_icon"/><br>'
                    )
                    stats["hc02ca_private_icons"] += 1
                    private_directives.append(
                        {
                            "start_control": "1fe2",
                            "end_control": "1fe3",
                            "directive": directive,
                            "status": "rendered_icon",
                        }
                    )
                    end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                    i = (end + 2) if end != -1 else (i + 2 + arg_len)
                    continue

            if _renderer_code(options) == "0136" and op in PRIVATE_START_OPS:
                directive = payload.hex()
                icon_name = HC0136_ICON_DIRECTIVES.get(directive)
                if icon_name is not None:
                    src = _image_or_named_template(icon_name.removesuffix(".png"), options)
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_icon"/><br>'
                    )
                    stats["hc0136_private_icons"] += 1
                    private_directives.append(
                        {
                            "start_control": "1fe2",
                            "end_control": "1fe3",
                            "directive": directive,
                            "status": "rendered_icon",
                        }
                    )
                    end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                    i = (end + 2) if end != -1 else (i + 2 + arg_len)
                    continue

            if _renderer_code(options) == "013C" and op in PRIVATE_START_OPS:
                directive = payload.hex()
                icon_name = HC013C_ICON_DIRECTIVES.get(directive)
                if icon_name is not None:
                    src = _image_or_named_template(icon_name.removesuffix(".png"), options)
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_icon"/><br>'
                    )
                    stats["hc013c_private_icons"] += 1
                    private_directives.append(
                        {
                            "start_control": "1fe2",
                            "end_control": "1fe3",
                            "directive": directive,
                            "status": "rendered_icon",
                        }
                    )
                    end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                    i = (end + 2) if end != -1 else (i + 2 + arg_len)
                    continue

            if _is_hc_gen_year_renderer(options) and op in PRIVATE_START_OPS:
                directive = payload.hex()
                icon_name = HC_GEN_YEAR_ICON_DIRECTIVES.get(directive)
                if icon_name is not None:
                    src = _image_or_named_template(icon_name.removesuffix(".png"), options)
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_icon"/><br>'
                    )
                    stats["hc_gen_year_private_icons"] += 1
                    private_directives.append(
                        {
                            "start_control": "1fe2",
                            "end_control": "1fe3",
                            "directive": directive,
                            "status": "rendered_icon",
                        }
                    )
                    end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                    i = (end + 2) if end != -1 else (i + 2 + arg_len)
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
                    if _renderer_code(options) == "00A6":
                        directive_text = _private_directive_text("".join(ctx.text_parts))
                        if directive_text.startswith("RUB:S"):
                            ruby_text = directive_text[5:]
                            ctx.parent.append('<ruby class="ruby7"><rb class="rb7">')
                            hc00a6_ruby_readings.append(ruby_text)
                            stats["hc00a6_ruby_starts"] += 1
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "ruby_start",
                                    "text_length": len(directive_text),
                                    "status": "rendered",
                                }
                            )
                            i += 2 + arg_len
                            continue
                        if directive_text.startswith("RUB:E"):
                            if not hc00a6_ruby_readings:
                                gaps.add("hc00a6_unmatched_ruby_end")
                                private_directives.append(
                                    {
                                        "start_control": f"1f{ctx.start_op:02x}",
                                        "end_control": f"1f{op:02x}",
                                        "kind": "ruby_end",
                                        "text_length": len(directive_text),
                                        "status": "unmatched",
                                    }
                                )
                                i += 2 + arg_len
                                continue
                            ruby_text = hc00a6_ruby_readings.pop()
                            ctx.parent.append(
                                '</rb><rp class="rp7">(</rp>'
                                f'<rt class="rt7">{_escape_text(ruby_text)}</rt>'
                                '<rp class="rp7">)</rp></ruby>'
                            )
                            stats["hc00a6_ruby_ends"] += 1
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "ruby_end",
                                    "text_length": len(directive_text),
                                    "status": "rendered" if ruby_text else "unmatched",
                                }
                            )
                            i += 2 + arg_len
                            continue
                    if _renderer_code(options) == "009C":
                        image_html = _hc009c_private_image_html("".join(ctx.text_parts), options, stats)
                        if image_html is not None:
                            ctx.parent.append(image_html)
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
            if _renderer_code(options) == "012D" and hc012d_pending_honbun_user:
                _current_parts(root_parts, contexts).append('<div class="honbun_user">')
                hc012d_section_close = "</div>"
                hc012d_pending_honbun_user = False
            if _renderer_code(options) == "013D":
                html_fragment, consumed = _hc013d_jis_sequence_html(data, i, options)
                if html_fragment is not None and consumed:
                    _current_parts(root_parts, contexts).append(html_fragment)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(html_fragment))
                    stats["hc013d_jis_template_markers"] += 1
                    i += consumed
                    continue
            key = f"{byte:02x}{data[i + 1]:02x}"
            if _renderer_code(options) == "012D":
                image_key = HC012D_INLINE_IMAGE_JIS.get(key)
                if key == "222a" and data[i + 2 : i + 4] == b"\x1f\x42":
                    image_key = "link_k"
                if image_key is not None:
                    image_src = _image_source_for_key(image_key, options) or _image_source_for_key(f"{image_key}.png", options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "gaiji", stats)
                    else:
                        _append_text(_current_parts(root_parts, contexts), decode_jis_pair(data[i : i + 2]) or "")
                    stats["hc012d_inline_image_markers"] += 1
                    i += 2
                    continue
            text = decode_jis_pair(data[i : i + 2])
            if text:
                stats["jis_pairs"] += 1
                value = normalize_fullwidth_ascii(text) if halfwidth_depth else text
                if _renderer_code(options) == "0048" and key in HC0048_MIDASHI_MARKERS and not hc0048_midashi_open:
                    parts = _current_parts(root_parts, contexts)
                    if hc0048_section_close is not None:
                        parts.append(hc0048_section_close)
                        hc0048_section_close = None
                    if hc0048_honbun_open:
                        parts.append("</div>")
                        hc0048_honbun_open = False
                    parts.append('<div class="midashi">')
                    hc0048_midashi_open = True
                    stats["hc0048_midashi_blocks"] += 1
                if _renderer_code(options) == "00C6" and hc00c6_supab_pending and value in {"A", "B", "Ａ", "Ｂ"}:
                    _current_parts(root_parts, contexts).append(f'<sup class="supAB">{_escape_text(value)}</sup>')
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(value)
                    stats["hc00c6_supab_markers"] += 1
                    hc00c6_supab_pending = False
                    i += 2
                    continue
                if _renderer_code(options) == "00C6" and hc00c6_supab_pending:
                    hc00c6_supab_pending = False
                _append_text(_current_parts(root_parts, contexts), value)
                text_parts = _current_text_parts(contexts)
                if text_parts is not None:
                    text_parts.append(value)
            else:
                stats["invalid_jis_pairs"] += 1
                gaps.add("invalid_jis_pair")
            if _renderer_code(options) == "02C1":
                hc02c1_section_just_opened = False
            if _renderer_code(options) == "02BF":
                hc02bf_section_just_opened = False
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= byte <= 0xFE:
            if _renderer_code(options) == "012D" and hc012d_pending_honbun_user:
                _current_parts(root_parts, contexts).append('<div class="honbun_user">')
                hc012d_section_close = "</div>"
                hc012d_pending_honbun_user = False
            key = f"{byte:02x}{data[i + 1]:02x}"
            if _renderer_code(options) == "0190" and key in HC0190_TEMPLATE_MARKERS:
                hc0190_template_key = key
                stats["hc0190_template_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "009C":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                direct_class = HC009C_DIRECT_IMAGE_MARKERS.get(key)
                if direct_class is not None:
                    image_src = _hc009c_marker_image_src(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, direct_class, stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc009c_direct_image_markers"] += 1
                    i += 2
                    continue
                if key in HC009C_SEASON_IMAGE_MARKERS:
                    image_src = _hc009c_marker_image_src(key, options, midashi=True)
                    if image_src is not None:
                        parts.append(f'<img src="{_escape_attr(image_src)}" class="img_season" alt="{_escape_attr(key)}">')
                        stats["hc009c_season_image_markers"] += 1
                    else:
                        stats["hc009c_missing_season_images"] += 1
                    i += 2
                    continue
                if key in HC009C_KO_MIDASHI_MARKERS:
                    parts.append('<span class="ko-midashi">')
                    hc009c_marker_stack.append("</span>")
                    stats["hc009c_ko_midashi_markers"] += 1
                    i += 2
                    continue
                if key == "b13a":
                    parts.append('<div class="page_comment">')
                    hc009c_marker_stack.append("</div>")
                    stats["hc009c_page_comment_markers"] += 1
                    i += 2
                    continue
                table_class = None
                if key in HC009C_FEATURE_TABLE_MARKERS:
                    table_class = "feature-table"
                elif key in HC009C_DATA_TABLE_MARKERS:
                    table_class = "data-table"
                elif key in HC009C_MEMO_TABLE_MARKERS:
                    table_class = "memo-table"
                if table_class is not None:
                    open_html, close_html = _hc009c_table_marker_html(key, table_class, options)
                    parts.append(open_html)
                    hc009c_marker_stack.append(close_html)
                    stats["hc009c_table_markers"] += 1
                    i += 2
                    continue
                if key in HC009C_NOOP_MARKERS:
                    stats["hc009c_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "02C5":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                if key in HC02C5_IMG_HIN_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, "img_hin", stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc02c5_img_hin_markers"] += 1
                    i += 2
                    continue
                strong = HC02C5_STRONG_MARKERS.get(key)
                if strong is not None:
                    parts.append(f"<strong>{_escape_text(strong)}</strong>")
                    if text_parts is not None:
                        text_parts.append(strong)
                    stats["hc02c5_strong_markers"] += 1
                    i += 2
                    continue
                small = HC02C5_SMALL_MARKERS.get(key)
                if small is not None:
                    parts.append(f"<small>{_escape_text(small)}</small>")
                    if text_parts is not None:
                        text_parts.append(small)
                    stats["hc02c5_small_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0151":
                parts = _current_parts(root_parts, contexts)
                if key == HC0151_SMALL_OPEN_MARKER:
                    parts.append("<small><small><small>")
                    hc0151_small_depth += 1
                    stats["hc0151_small_markers"] += 1
                    i += 2
                    continue
                if key == HC0151_SMALL_CLOSE_MARKER:
                    if hc0151_small_depth:
                        hc0151_small_depth -= 1
                    else:
                        stats["hc0151_unmatched_small_markers"] += 1
                    parts.append("</small></small></small>")
                    stats["hc0151_small_markers"] += 1
                    i += 2
                    continue
                if key == HC0151_TABLE_CELL_MARKER:
                    if hc0151_current_section == HC0151_TABLE_HEADER_SECTION:
                        parts.append("</th><th>")
                        stats["hc0151_table_cell_markers"] += 1
                        i += 2
                        continue
                    if hc0151_current_section == HC0151_TABLE_ROW_SECTION:
                        parts.append("</td><td>")
                        stats["hc0151_table_cell_markers"] += 1
                        i += 2
                        continue
            if _renderer_code(options) == "02C1":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                if key in HC02C1_MOJI_DOWN_MARKERS:
                    if hc02c1_section_just_opened and not hc02c1_moji_down_open:
                        parts.append('<p class="moji-down">')
                        hc02c1_moji_down_open = True
                        stats["hc02c1_moji_down_blocks"] += 1
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc02c1_moji_down_markers"] += 1
                    hc02c1_section_just_opened = False
                    i += 2
                    continue
                if key in HC02C1_TEMPLATE_IMAGE_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, "img_mark4", stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc02c1_template_image_markers"] += 1
                    hc02c1_section_just_opened = False
                    i += 2
                    continue
            if _renderer_code(options) == "02BF":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                if key in HC02BF_MOJI_DOWN_MARKERS:
                    if hc02bf_section_just_opened and not hc02bf_moji_down_open:
                        parts.append('<p class="moji-down">')
                        hc02bf_moji_down_open = True
                        stats["hc02bf_moji_down_blocks"] += 1
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc02bf_moji_down_markers"] += 1
                    hc02bf_section_just_opened = False
                    i += 2
                    continue
            if _renderer_code(options) == "02C0" and key in HC02C0_NOOP_MARKERS:
                stats["hc02c0_noop_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "02CA":
                if key in HC02CA_NOOP_MARKERS:
                    stats["hc02ca_noop_markers"] += 1
                    i += 2
                    continue
                if key in HC02CA_IMG_MARK_MARKERS:
                    image_src = _image_or_named_template(key, options)
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_mark", stats)
                    stats["hc02ca_img_mark_markers"] += 1
                    i += 2
                    continue
                literal = HC02CA_LITERAL_MARKERS.get(key)
                if literal is not None:
                    _append_text(_current_parts(root_parts, contexts), literal)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(literal)
                    stats["hc02ca_literal_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "013C" and key in HC013C_NOOP_MARKERS:
                stats["hc013c_noop_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0142":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                if key == "a164":
                    parts.append('<span class="margin"></span>')
                    stats["hc0142_margin_markers"] += 1
                    i += 2
                    continue
                if key == "b13f":
                    image_src = _image_source_for_key(key, options) or "b13f.png"
                    _append_renderer_image_gaiji(parts, key, image_src, "icotype_1", stats)
                    stats["hc0142_icotype_markers"] += 1
                    i += 2
                    continue
                if key == "b177":
                    parts.append('<span class="math">')
                    hc0142_marker_stack.append(("b178", "</span>"))
                    stats["hc0142_math_markers"] += 1
                    i += 2
                    continue
                if key == "b178":
                    if hc0142_marker_stack and hc0142_marker_stack[-1][0] == key:
                        parts.append(hc0142_marker_stack.pop()[1])
                    else:
                        parts.append("</span>")
                        stats["hc0142_unmatched_math_markers"] += 1
                    stats["hc0142_math_markers"] += 1
                    i += 2
                    continue
                if key in HC0142_NOOP_MARKERS:
                    stats["hc0142_noop_markers"] += 1
                    i += 2
                    continue
                if key in HC0142_PLAIN_TEXT_MARKERS:
                    parts.append('<span class="plain_text">')
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    parts.append("</span>")
                    stats["hc0142_plain_text_markers"] += 1
                    i += 2
                    continue
                if key in HC0142_DIRECT_HALF_IMAGE_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, "gaiji_half", stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc0142_half_image_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "012E":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC012E_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc012e_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc012e_style_markers"] += 1
                    i += 2
                    continue
                if key in HC012E_CLOSE_MARKERS:
                    if hc012e_marker_stack and hc012e_marker_stack[-1][0] == key:
                        parts.append(hc012e_marker_stack.pop()[1])
                    else:
                        parts.append("</span>")
                        stats["hc012e_unmatched_style_markers"] += 1
                    stats["hc012e_style_markers"] += 1
                    i += 2
                    continue
                if key == "b236":
                    if hc012e_current_section == "003f":
                        parts.append('</div></td><td><div class="honbun">')
                        stats["hc012e_table_cell_transitions"] += 1
                    else:
                        stats["hc012e_noop_markers"] += 1
                    i += 2
                    continue
                if key == "b237":
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    if hc012e_current_section == "003f":
                        parts.append("</div></td></tr></table><br>")
                        stats["hc012e_table_closures"] += 1
                        hc012e_section_close = None
                    i += 2
                    continue
                literal = HC012E_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc012e_literal_markers"] += 1
                    i += 2
                    continue
                if key in HC012E_DIRECT_IMAGE_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, "hatsuon", stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc012e_direct_image_markers"] += 1
                    i += 2
                    continue
                if key in HC012E_NOOP_MARKERS:
                    stats["hc012e_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "012F" and key in HC012F_TEMPLATE_GAIJI_MARKERS:
                image_src = _image_source_for_key(key, options)
                if image_src is not None:
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                else:
                    _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                stats["hc012f_template_gaiji"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0131":
                image_src = _image_source_for_key(key, options)
                if image_src is not None:
                    css_class = "img_gaiji_V" if options.vertical else "img_gaiji"
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, css_class, stats)
                    stats["hc0131_template_gaiji"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "02C2" and key in HC02C2_TEMPLATE_IMAGE_MARKERS:
                image_src = _image_source_for_key(key, options)
                if image_src is not None:
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                else:
                    _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                stats["hc02c2_template_image_markers"] += 1
                i += 2
                continue
            if _is_hc_gen_year_renderer(options):
                if key in HC_GEN_YEAR_NOOP_MARKERS:
                    stats["hc_gen_year_noop_markers"] += 1
                    i += 2
                    continue
                if key in HC_GEN_YEAR_IMG_MARK_MARKERS:
                    image_src = _image_or_named_template(key, options)
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_mark", stats)
                    stats["hc_gen_year_img_mark_markers"] += 1
                    i += 2
                    continue
                if key in HC_GEN_YEAR_IMG_MARK2_MARKERS:
                    image_src = _image_or_named_template(key, options)
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_mark2", stats)
                    stats["hc_gen_year_img_mark2_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0065":
                literal = HC0065_LITERAL_MARKERS.get(key)
                if literal is not None:
                    _append_text(_current_parts(root_parts, contexts), literal)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(literal)
                    stats["hc0065_literal_markers"] += 1
                    i += 2
                    continue
                image_class = HC0065_TEMPLATE_IMAGE_MARKERS.get(key)
                if image_class is not None:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, image_class, stats)
                    else:
                        _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                    stats["hc0065_template_image_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "009D":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                literal = HC009D_LITERAL_MARKERS.get(key)
                if literal is not None:
                    _append_text(parts, literal)
                    if text_parts is not None:
                        text_parts.append(literal)
                    stats["hc009d_literal_markers"] += 1
                    i += 2
                    continue
                html_marker = HC009D_HTML_MARKERS.get(key)
                if html_marker is not None:
                    parts.append(html_marker)
                    stats["hc009d_html_markers"] += 1
                    i += 2
                    continue
                if key in HC009D_BREAK_MARKERS:
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    stats["hc009d_break_markers"] += 1
                    i += 2
                    continue
                if key == HC009D_TABLE_HEADER_MARKER and hc009d_marker_stack and hc009d_marker_stack[-1][0] == HC009D_TABLE_CLOSE_MARKER:
                    parts.append("<thead><tr><th>")
                    stats["hc009d_table_markers"] += 1
                    i += 2
                    continue
                if key == HC009D_TABLE_CLOSE_MARKER:
                    if hc009d_marker_stack and hc009d_marker_stack[-1][0] == key:
                        parts.append(hc009d_marker_stack.pop()[1])
                        stats["hc009d_kakomi_markers"] += 1
                    else:
                        stats["hc009d_unmatched_kakomi_markers"] += 1
                    i += 2
                    continue
                if key in HC009D_KAKOMI_CLOSE_MARKERS:
                    if hc009d_marker_stack and hc009d_marker_stack[-1][0] == key:
                        parts.append(hc009d_marker_stack.pop()[1])
                        stats["hc009d_kakomi_markers"] += 1
                    else:
                        stats["hc009d_unmatched_kakomi_markers"] += 1
                    i += 2
                    continue
                if key in HC009D_NOOP_MARKERS:
                    stats["hc009d_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "012D":
                literal = HC012D_LITERAL_MARKERS.get(key)
                if literal is not None:
                    _append_text(_current_parts(root_parts, contexts), literal)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(literal)
                    stats["hc012d_literal_markers"] += 1
                    i += 2
                    continue
                inline_html = HC012D_INLINE_HTML_MARKERS.get(key)
                if inline_html is not None:
                    _current_parts(root_parts, contexts).append(inline_html)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(inline_html))
                    stats["hc012d_literal_markers"] += 1
                    i += 2
                    continue
                if key in HC012D_NOOP_MARKERS:
                    stats["hc012d_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0144":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC0144_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc0144_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc0144_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0144_CLOSE_MARKERS:
                    if hc0144_marker_stack and hc0144_marker_stack[-1][0] == key:
                        parts.append(hc0144_marker_stack.pop()[1])
                        stats["hc0144_style_markers"] += 1
                    else:
                        stats["hc0144_unmatched_style_markers"] += 1
                    i += 2
                    continue
                literal = HC0144_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc0144_literal_markers"] += 1
                    i += 2
                    continue
                if key in HC0144_NOOP_MARKERS:
                    stats["hc0144_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0145":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC0145_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc0145_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc0145_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0145_CLOSE_MARKERS:
                    if hc0145_marker_stack and hc0145_marker_stack[-1][0] == key:
                        parts.append(hc0145_marker_stack.pop()[1])
                        stats["hc0145_style_markers"] += 1
                    else:
                        stats["hc0145_unmatched_style_markers"] += 1
                    i += 2
                    continue
                literal = HC0145_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc0145_literal_markers"] += 1
                    i += 2
                    continue
                if key in HC0145_NOOP_MARKERS:
                    stats["hc0145_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "03E8":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC03E8_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc03e8_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc03e8_style_markers"] += 1
                    i += 2
                    continue
                if key in HC03E8_CLOSE_MARKERS:
                    if hc03e8_marker_stack and hc03e8_marker_stack[-1][0] == key:
                        parts.append(hc03e8_marker_stack.pop()[1])
                        stats["hc03e8_style_markers"] += 1
                    else:
                        stats["hc03e8_unmatched_style_markers"] += 1
                    i += 2
                    continue
                literal = HC03E8_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc03e8_literal_markers"] += 1
                    i += 2
                    continue
                if key in HC03E8_NOOP_MARKERS:
                    stats["hc03e8_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0141":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC0141_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc0141_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc0141_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0141_CLOSE_MARKERS:
                    if hc0141_marker_stack and hc0141_marker_stack[-1][0] == key:
                        parts.append(hc0141_marker_stack.pop()[1])
                        stats["hc0141_style_markers"] += 1
                    else:
                        stats["hc0141_unmatched_style_markers"] += 1
                    i += 2
                    continue
                literal = HC0141_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc0141_literal_markers"] += 1
                    i += 2
                    continue
                if key in HC0141_NOOP_MARKERS:
                    stats["hc0141_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "00C6":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC00C6_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc00c6_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc00c6_style_markers"] += 1
                    i += 2
                    continue
                if key in HC00C6_CLOSE_MARKERS:
                    if hc00c6_marker_stack and hc00c6_marker_stack[-1][0] == key:
                        parts.append(hc00c6_marker_stack.pop()[1])
                        stats["hc00c6_style_markers"] += 1
                    else:
                        stats["hc00c6_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key == "a244":
                    hc00c6_supab_pending = True
                    stats["hc00c6_supab_markers"] += 1
                    i += 2
                    continue
                if key == "b126":
                    parts.append('<br><hr class="line">')
                    stats["hc00c6_rule_lines"] += 1
                    i += 2
                    continue
                if key in HC00C6_NOOP_MARKERS:
                    stats["hc00c6_noop_markers"] += 1
                    i += 2
                    continue
                if key in HC00C6_IMAGE_MARKERS:
                    image_src = options.image_sources.get(key.lower())
                    if image_src is not None:
                        _append_renderer_image_gaiji(
                            parts,
                            key,
                            image_src,
                            _hc00c6_image_class(key, in_heading=0x41 in style_stack),
                            stats,
                        )
                        stats["hc00c6_image_markers"] += 1
                        i += 2
                        continue
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    i += 2
                    continue
            if _renderer_code(options) == "02BE":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                accent = HC02BE_ACCENT_MARKERS.get(key)
                if accent is not None:
                    parts.append(_hc02be_accent_html(accent, options.image_sources))
                    stats["hc02be_accent_markers"] += 1
                    i += 2
                    continue
                literal = HC02BE_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc02be_literal_markers"] += 1
                    i += 2
                    continue
                marker = HC02BE_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc02be_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc02be_style_markers"] += 1
                    i += 2
                    continue
                if key in HC02BE_CLOSE_MARKERS:
                    if hc02be_marker_stack and hc02be_marker_stack[-1][0] == key:
                        parts.append(hc02be_marker_stack.pop()[1])
                        stats["hc02be_style_markers"] += 1
                    else:
                        stats["hc02be_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key in HC02BE_NOOP_MARKERS:
                    stats["hc02be_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "02BC":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                composite = HC02BC_COMPOSITE_MARKERS.get(key)
                if composite is not None:
                    parts.append(composite)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(composite))
                    stats["hc02bc_composite_markers"] += 1
                    i += 2
                    continue
                literal = HC02BC_LITERAL_MARKERS.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc02bc_literal_markers"] += 1
                    if key == "b13c":
                        stats["line_breaks"] += 1
                    i += 2
                    continue
                marker = HC02BC_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc02bc_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc02bc_style_markers"] += 1
                    i += 2
                    continue
                if key in HC02BC_CLOSE_MARKERS:
                    if hc02bc_marker_stack and hc02bc_marker_stack[-1][0] == key:
                        parts.append(hc02bc_marker_stack.pop()[1])
                        stats["hc02bc_style_markers"] += 1
                    else:
                        stats["hc02bc_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key in HC02BC_NOOP_MARKERS:
                    stats["hc02bc_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0146":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                marker = HC0146_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc0146_marker_stack.append((marker.close_code, marker.close_html))
                    if marker.render_self:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc0146_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0146_CLOSE_MARKERS:
                    if hc0146_marker_stack and hc0146_marker_stack[-1][0] == key:
                        parts.append(hc0146_marker_stack.pop()[1])
                        stats["hc0146_style_markers"] += 1
                    else:
                        stats["hc0146_unmatched_style_markers"] += 1
                    i += 2
                    continue
                literal = HC0146_LITERAL_MARKERS.get(key)
                if literal is not None:
                    _append_text(parts, literal)
                    if text_parts is not None:
                        text_parts.append(literal)
                    stats["hc0146_literal_markers"] += 1
                    i += 2
                    continue
                image_rule = HC0146_IMAGE_MARKERS.get(key)
                if image_rule is not None:
                    image_src = options.image_sources.get(key.lower())
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, image_rule.css_class, stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc0146_image_markers"] += 1
                    i += 2
                    continue
                if key in HC0146_NOOP_MARKERS:
                    stats["hc0146_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0157":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                standalone = HC0157_STANDALONE_MARKERS.get(key)
                if standalone is not None:
                    parts.append(standalone)
                    stats["hc0157_style_markers"] += 1
                    i += 2
                    continue
                marker = HC0157_OPEN_MARKERS.get(key)
                if marker is not None:
                    parts.append(marker.html)
                    if marker.close_code is not None:
                        hc0157_marker_stack.append((marker.close_code, marker.close_html))
                    if marker.render_self:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc0157_style_markers"] += 1
                    i += 2
                    continue
                if int(key, 16) in HC0157_RED_GAIJI_RANGE:
                    parts.append('<span class="red">')
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    parts.append("</span>")
                    stats["hc0157_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0157_SELF_RENDERING_CLOSE_MARKERS:
                    _append_gaiji_value(parts, text_parts, key, options, stats)
                    if hc0157_marker_stack and hc0157_marker_stack[-1][0] == key:
                        parts.append(hc0157_marker_stack.pop()[1])
                        stats["hc0157_style_markers"] += 1
                    else:
                        stats["hc0157_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0157_CLOSE_MARKERS:
                    if hc0157_marker_stack and hc0157_marker_stack[-1][0] == key:
                        parts.append(hc0157_marker_stack.pop()[1])
                        stats["hc0157_style_markers"] += 1
                    else:
                        stats["hc0157_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0157_NOOP_MARKERS:
                    stats["hc0157_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0158":
                marker = HC0158_OPEN_MARKERS.get(key)
                if marker is not None:
                    html_fragment, close_code, close_html = marker
                    _current_parts(root_parts, contexts).append(html_fragment)
                    hc0158_marker_stack.append((close_code, close_html))
                    stats["hc0158_style_markers"] += 1
                    i += 2
                    continue
                if key == "b374":
                    _current_parts(root_parts, contexts).append("<br>")
                    stats["line_breaks"] += 1
                    stats["hc0158_style_markers"] += 1
                    i += 2
                    continue
                if key == "b377":
                    _current_parts(root_parts, contexts).append("<ruby>")
                    hc0158_marker_stack.append(("b378", "</ruby>"))
                    stats["hc0158_style_markers"] += 1
                    i += 2
                    continue
                if key == "b378":
                    if hc0158_marker_stack and hc0158_marker_stack[-1][0] == "b378":
                        hc0158_marker_stack.pop()
                    _current_parts(root_parts, contexts).append("<rt>&#x3001;</rt></ruby>")
                    stats["hc0158_style_markers"] += 1
                    i += 2
                    continue
                if key == "b379":
                    _current_parts(root_parts, contexts).append(_hc0158_conditional_waku(_decode_next_jis_text(data, i + 2)))
                    hc0158_marker_stack.append(("b37a", "</span>"))
                    stats["hc0158_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0158_CLOSE_MARKERS:
                    if hc0158_marker_stack and hc0158_marker_stack[-1][0] == key:
                        _current_parts(root_parts, contexts).append(hc0158_marker_stack.pop()[1])
                        stats["hc0158_style_markers"] += 1
                    else:
                        stats["hc0158_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0158_NOOP_MARKERS:
                    stats["hc0158_noop_markers"] += 1
                    i += 2
                    continue
            _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
            if _renderer_code(options) == "02C1":
                hc02c1_section_just_opened = False
            if _renderer_code(options) == "02BF":
                hc02bf_section_just_opened = False
            i += 2
            continue

        stats["unknown_bytes"] += 1
        i += 1

    while style_stack:
        popped = style_stack.pop()
        if _renderer_code(options) == "0131" and popped == 0x06:
            _current_parts(root_parts, contexts).append("</sub></span>")
            continue
        close_tag = _style_close_tag(popped, options)
        if close_tag:
            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
    while hc0158_marker_stack:
        close_code, close_html = hc0158_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0158_marker_{close_code}")
    while hc0157_marker_stack:
        close_code, close_html = hc0157_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0157_marker_{close_code}")
    while hc0146_marker_stack:
        close_code, close_html = hc0146_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0146_marker_{close_code}")
    while hc0142_marker_stack:
        close_code, close_html = hc0142_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0142_marker_{close_code}")
    while hc009d_marker_stack:
        close_code, close_html = hc009d_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        if not close_code.startswith("__"):
            gaps.add(f"unterminated_hc009d_marker_{close_code}")
    while hc00c6_marker_stack:
        close_code, close_html = hc00c6_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc00c6_marker_{close_code}")
    while hc0141_marker_stack:
        close_code, close_html = hc0141_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0141_marker_{close_code}")
    while hc0144_marker_stack:
        close_code, close_html = hc0144_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0144_marker_{close_code}")
    while hc0145_marker_stack:
        close_code, close_html = hc0145_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0145_marker_{close_code}")
    while hc03e8_marker_stack:
        close_code, close_html = hc03e8_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc03e8_marker_{close_code}")
    while hc02be_marker_stack:
        close_code, close_html = hc02be_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc02be_marker_{close_code}")
    while hc02bc_marker_stack:
        close_code, close_html = hc02bc_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc02bc_marker_{close_code}")
    while hc012e_marker_stack:
        close_code, close_html = hc012e_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc012e_marker_{close_code}")
    if hc00c6_section_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc02bc_section_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc02be_section_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc009d_section_close is not None:
        _current_parts(root_parts, contexts).append(hc009d_section_close)
    if hc009b_section_close is not None:
        _current_parts(root_parts, contexts).append(hc009b_section_close)
    if hc009c_marker_stack or hc009c_section_close is not None:
        _hc009c_close_section(_current_parts(root_parts, contexts), hc009c_marker_stack, hc009c_section_close)
    if hc02c5_section_close is not None:
        _current_parts(root_parts, contexts).append(hc02c5_section_close)
    while hc0151_small_depth:
        _current_parts(root_parts, contexts).append("</small></small></small>")
        hc0151_small_depth -= 1
        gaps.add("unterminated_hc0151_small_marker")
    if hc0151_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0151_section_close)
    if hc0151_table_open:
        _current_parts(root_parts, contexts).append("</table>")
    if hc0151_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0142_honbun_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc012d_section_close is not None:
        _current_parts(root_parts, contexts).append(hc012d_section_close)
    if hc013d_section_close is not None:
        _current_parts(root_parts, contexts).append(hc013d_section_close)
    if hc0141_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0141_section_close)
    if hc0144_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0144_section_close)
    if hc0145_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0145_section_close)
    if hc03e8_section_close is not None:
        _current_parts(root_parts, contexts).append(hc03e8_section_close)
    if hc00a6_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00a6_section_close)
    while hc00a6_ruby_readings:
        ruby_text = hc00a6_ruby_readings.pop()
        _current_parts(root_parts, contexts).append(
            '</rb><rp class="rp7">(</rp>'
            f'<rt class="rt7">{_escape_text(ruby_text)}</rt>'
            '<rp class="rp7">)</rp></ruby>'
        )
        gaps.add("hc00a6_unterminated_ruby")
    if hc012e_section_close is not None:
        _current_parts(root_parts, contexts).append(hc012e_section_close)
    if hc012f_section_close is not None:
        _current_parts(root_parts, contexts).append(hc012f_section_close)
    if hc0131_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0131_section_close)
    if hc02c1_moji_down_open:
        _current_parts(root_parts, contexts).append("</p>")
    if hc02c1_section_close is not None:
        _current_parts(root_parts, contexts).append(hc02c1_section_close)
    if hc02bf_moji_down_open:
        _current_parts(root_parts, contexts).append("</p>")
    if hc02bf_section_close is not None:
        _current_parts(root_parts, contexts).append(hc02bf_section_close)
    if hc02c0_section_close is not None:
        _current_parts(root_parts, contexts).append(hc02c0_section_close)
    if hc013c_section_close is not None:
        _current_parts(root_parts, contexts).append(hc013c_section_close)
    if hc00b3_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00b3_section_close)
    if hc_gen_year_section_close is not None:
        _current_parts(root_parts, contexts).append(hc_gen_year_section_close)
    if hc_hkdksr_medical_section_close is not None:
        _current_parts(root_parts, contexts).append(hc_hkdksr_medical_section_close)
    if hc_hkdksr_medical_table_open:
        _current_parts(root_parts, contexts).append("</td></tr></table>")
    if hc02c2_section_open:
        if hc02c2_moji_down_open:
            _current_parts(root_parts, contexts).append("</p>")
        _current_parts(root_parts, contexts).append("</div>")
    if hc0048_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0048_honbun_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0048_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0048_section_close)
    if hc0048_media_div_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0065_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0065_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    _hc0190_close_section(contexts, hc0190_sections, stats)
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
    if _renderer_code(options) == "0190" and hc0190_template_key is not None:
        template = options.html_templates.get(hc0190_template_key)
        if template is not None:
            body = _hc0190_apply_template(template, hc0190_sections, stats)
            stats["hc0190_templates_applied"] += 1
        else:
            gaps.add(f"missing_hc0190_template_{hc0190_template_key}")
    elif _renderer_code(options) == "0190" and hc0190_sections:
        body = "".join(value for _, value in sorted(hc0190_sections.items()))
        gaps.add("missing_hc0190_template_marker")
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
    image_sources, html_templates, stylesheet, copied_assets = _prepare_hc_render_assets(source, dict_out, renderer)
    options = HcRenderOptions(
        gaiji_map=source.gaiji_map,
        image_sources=image_sources,
        html_templates=html_templates,
        renderer_code=renderer.code if renderer is not None else None,
        vertical=bool(getattr(args, "vertical", False)),
    )
    totals: Counter[str] = Counter()
    emitted = 0
    named_gaps: Counter[str] = Counter()
    with entries_path.open("w", encoding="utf-8") as jsonl, html_path.open("w", encoding="utf-8") as html_out:
        _write_hc_html_header(html_out, stylesheet=stylesheet, vertical=bool(getattr(args, "vertical", False)))
        for entry_index, (start, end) in enumerate(iter_entry_slices_reader(reader), start=1):
            if args.limit is not None and emitted >= args.limit:
                break
            body = reader.read(start, end - start)
            result = render_hc_body(body, options)
            totals.update(result.stats)
            totals["rendered_links"] += len(result.links)
            totals["rendered_media_references"] += len(result.media)
            totals["rendered_audio_references"] += len(result.audio)
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
        _write_hc_html_footer(html_out)
    return {
        "raw_honmon_entries_emitted": emitted,
        "raw_honmon_entries_path": str(entries_path),
        "raw_honmon_html_path": str(html_path),
        "raw_honmon_stylesheet": stylesheet,
        "raw_honmon_assets_copied": copied_assets,
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
        media_count = int(stats.get("rendered_media_references", stats.get("media_placeholders", 0)) or 0)
        audio_count = int(stats.get("rendered_audio_references", stats.get("audio_links", 0)) or 0)
        link_count = int(stats.get("rendered_links", stats.get("links", 0)) or 0)
        print(
            f"{row['dict_id']:12s} hc_entries={row.get('raw_honmon_entries_emitted', 0):5d} "
            f"links={link_count:5d} media={media_count:5d} audio={audio_count:5d} "
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
