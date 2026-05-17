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

HC02C2_ICON_SECTION_IMAGES = {
    "0007": "1.png",
    "0008": "2.png",
    "0009": "3.png",
    "000a": "4.png",
}
HC02C2_TEMPLATE_IMAGE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x13E, 0x15E))
HC02C2_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}

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


def _renderer_code(options: HcRenderOptions) -> str:
    return (options.renderer_code or "").upper()


def _link_css_class(options: HcRenderOptions, start_op: int | None) -> str:
    if _renderer_code(options) == "0065" and start_op in {0x42, 0x43, 0x44}:
        return "lv-hc-link lLink"
    if _renderer_code(options) in {"009D", "012D", "013D", "0141", "0144", "0145", "02C2", "03E8"} and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    return "lv-hc-link"


def _style_start_spec(op: int, options: HcRenderOptions) -> tuple[str, str] | None:
    if _renderer_code(options) == "0157" and op == 0x12:
        return None
    if _renderer_code(options) == "0158" and op == 0x12:
        return ("b", "")
    if op == 0x41 and _renderer_code(options) == "0065":
        return None
    if op in {0x41, 0x4C} and _renderer_code(options) == "02C2":
        return None
    if op == 0x41 and _renderer_code(options) == "012D":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "012E":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "009D":
        return None
    if op == 0x41 and _renderer_code(options) == "013D":
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
        if _renderer_code(options) in {"0065", "009D", "012E", "013D", "0141", "0144", "0145", "02C2", "03E8"}:
            css_class += " img_gaiji"
        if _renderer_code(options) == "012D":
            css_class += " gaiji"
        if _renderer_code(options) == "0158":
            css_class += " gaiji"
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
    return output_sources, html_templates, stylesheet_output, copied


def _write_hc_html_header(html_out: Any, *, stylesheet: str | None, vertical: bool) -> None:
    body_class = "v" if vertical else "h"
    html_out.write("<!doctype html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n")
    if stylesheet:
        html_out.write(f'<link rel="stylesheet" href="{_escape_attr(stylesheet)}">\n')
    html_out.write(f"</head>\n<body class=\"{body_class}\">\n")


def _write_hc_html_footer(html_out: Any) -> None:
    html_out.write("</body>\n</html>\n")


def render_hc_body(data: bytes, options: HcRenderOptions | None = None) -> HcRenderResult:
    """Render one expanded HONMON body slice with common HC semantics."""

    options = options or HcRenderOptions()
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
    hc03e8_section_close: str | None = None
    hc012e_section_close: str | None = None
    hc012e_current_section: str | None = None
    hc02c2_section_open = False
    hc02c2_moji_down_open = False
    hc02c2_current_section: str | None = None
    hc0065_midashi_open = False
    hc0065_body_open = False
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
                if _renderer_code(options) == "009D" and hc009d_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc009d_section_close)
                    hc009d_section_close = None
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "00C6" and hc00c6_section_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00c6_section_open = False
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

            if _renderer_code(options) == "012D" and op in HC012D_NONPRINTING_CONTROL_OPS:
                stats["hc012d_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "009D" and op in HC009D_NONPRINTING_CONTROL_OPS:
                stats["hc009d_nonprinting_controls"] += 1
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
                if sound_src:
                    label = f'<img src="{_escape_attr(sound_src)}" class="img_mark2">'
                    stats["audio_images"] += 1
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
            if _renderer_code(options) == "02C2" and key in HC02C2_TEMPLATE_IMAGE_MARKERS:
                image_src = _image_source_for_key(key, options)
                if image_src is not None:
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                else:
                    _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                stats["hc02c2_template_image_markers"] += 1
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
            i += 2
            continue

        stats["unknown_bytes"] += 1
        i += 1

    while style_stack:
        close_tag = _style_close_tag(style_stack.pop(), options)
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
    if hc012e_section_close is not None:
        _current_parts(root_parts, contexts).append(hc012e_section_close)
    if hc02c2_section_open:
        if hc02c2_moji_down_open:
            _current_parts(root_parts, contexts).append("</p>")
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
