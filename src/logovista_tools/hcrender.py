"""Common HC HTML renderer semantics for SSED body streams.

This module intentionally implements the shared renderer behavior visible
across HC????.dll plugins. Product-specific hooks remain classified metadata;
they are not treated as exact renderer parity.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
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
from .gaiji import (
    ga16_preferred_code_for_index,
    is_bitmap_gaiji_resource_name,
    parse_ga16_resource,
    parse_uni_resource,
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
COMMON_RENDERER_STATE_OPS = {0x6D}

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
:where(.lv-hc-render .lv-hc-heading) {
  display: block;
}
:where(.lv-hc-render .lv-hc-section) {
  display: none;
}
:where(.lv-hc-render img) {
  max-height: 1.4em;
  vertical-align: middle;
}
:where(.lv-hc-render .lv-hc-gaiji-placeholder) {
  display: inline-block;
  min-width: 1em;
  min-height: 1em;
  border: 1px solid #999;
  vertical-align: -0.15em;
}
:where(.lv-hc-render .lv-hc-link),
:where(.lv-hc-render .lv-hc-audio) {
  text-decoration: none;
}
:where(.lv-hc-render .midashi) {
  font-weight: 700;
  font-size: 1.15em;
  margin: 0.2em 0 0.35em;
}
:where(.lv-hc-render .contents_body) {
  margin-top: 0.25em;
}
:where(.lv-hc-render .medblk),
:where(.lv-hc-render .medblkcaution),
:where(.lv-hc-render .medprice),
:where(.lv-hc-render .medimage),
:where(.lv-hc-render .medcaution),
:where(.lv-hc-render .notmedblock) {
  margin: 0.25em 0;
}
:where(.lv-hc-render .med) {
  font-weight: 600;
}
""".strip()

HC_RENDER_BASE_SCRIPT = """
function showIndex(id) {
  var field = document.getElementById("field" + id.toString());
  var icon = document.getElementById(id.toString());
  if (!field || !icon) {
    return;
  }
  var hidden = field.style.display === "none" || window.getComputedStyle(field).display === "none";
  if (hidden) {
    field.style.display = "block";
    icon.src = icon.getAttribute("data-lv-on-src") || (icon.src.match(/_V/) ? "youreion_V.png" : "youreion.png");
  } else {
    field.style.display = "none";
    icon.src = icon.getAttribute("data-lv-off-src") || (icon.src.match(/_V/) ? "youreioff_V.png" : "youreioff.png");
  }
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
    entry_start_offset: int = 0


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
    style_stack: list[int] = field(default_factory=list)
    halfwidth_depth: int = 0
    start_offset: int = 0
    flags: frozenset[str] = field(default_factory=frozenset)


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
HC_PACKED_BCD_LINK_RENDERERS = frozenset(
    {
        "009B",
        "009C",
        "009F",
        "00A3",
        "00A4",
        "00A6",
        "00A9",
        "00AA",
        "00AB",
        "00AC",
        "00AD",
        "00B3",
        "00BB",
        "00C4",
        "00C5",
        "00C6",
        "012D",
        "012E",
        "012F",
        "0131",
        "0132",
        "0136",
        "0137",
        "013A",
        "013C",
        "0141",
        "0142",
        "0144",
        "0145",
        "0157",
        "0158",
        "0190",
        "02BC",
        "02BE",
        "02BF",
        "02C0",
        "02C1",
        "02C2",
        "02C4",
        "02C7",
        "02C9",
        "02CA",
        "02CB",
        "02CC",
        "02CD",
        "02D1",
    }
)
HC0157_GROUP_START_CLASSES = {
    10: "yourei_ej",
    20: "seiku",
    30: "haseigo",
    40: "yourei_je",
    50: "hukugou",
}
HC0157_GROUP_END_VALUES = {11, 21, 31, 41, 51}
HC0157_TEXTLINE_CLASSES = {
    12: "yourei_ej_main",
    13: "yourei_ej_sub",
    22: "seiku_main",
    23: "seiku_sub",
    32: "haseigo_main",
    33: "haseigo_sub",
    42: "yourei_je_main",
    43: "yourei_je_sub",
    52: "hukugou_main",
    53: "hukugou_sub",
}
HC0157_SIMPLE_SECTION_CLASSES = {
    2: "komidashi",
    3: "textline gogi_ej",
    4: "textline gogi_je",
}


def _hc0146_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None

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
HC0158_SECTION_CLASSES = {
    2: "honbun_normal",
    3: "honbun_level1",
    4: "honbun_level2",
    5: "honbun_level3",
    6: "honbun_reigo",
    7: "honbun_naritachi",
    8: "honbun_setsuzoku",
    9: "honbun_setsuzoku_inyou",
    10: "honbun_bunpou1",
    11: "honbun_bunpou1_inyou1",
    12: "honbun_bunpou1_inyou2",
    13: "honbun_bunpou2",
    14: "honbun_gohou",
    15: "honbun_chui",
    16: "honbun_syuzi",
    17: "honbun_sankou",
    18: "honbun_sankou_inyou",
    19: "honbun_boutoubun",
    20: "honbun_kanyouhyougen",
    21: "honbun_columntitle",
    22: "honbun_inyou",
    23: "column_waka_haiku",
    25: "column_imi_youhou",
    27: "column_gogi_panel",
    29: "column_hatten",
    31: "column_zukai_gakusyu",
    33: "column_ruigo_panel",
    35: "column_shikibetsu_board",
    37: "honbun_yourei",
    38: "honbun_picture",
    39: "honbun_sound",
    100: "sakuin",
    101: "sakuin_sound",
}
HC0158_COLUMN_SECTION_VALUES = {23, 25, 27, 29, 31, 33, 35}
HC0158_STATE_SECTION_VALUES = {1, 24, 26, 28, 30, 32, 34, 36}

HC0146_OPEN_MARKERS: dict[str, _RendererGaijiRule] = {
    # HC0146 uses C++ template-string globals for these paired marker
    # branches.  The names below are recovered from the branch destinations
    # rather than inferred from marker numbers.
    "b230": _RendererGaijiRule('<span class="plain_font">', "b231", "</span>"),
    # HC0146 maps this pair to a color-font span. The close side is an
    # explicit </font> branch in the renderer loop; the open pointer is set up
    # by the renderer's template globals and matches 00000146.css.
    "b232": _RendererGaijiRule('<font class="color_font">', "b233", "</font>"),
    "b234": _RendererGaijiRule('<span class="not_italic_font">', "b235", "</span>"),
    "b238": _RendererGaijiRule('<span class="under_line">', "b239", "</span>"),
    "b244": _RendererGaijiRule('<span class="under_line">', "b245", "</span>"),
    "b354": _RendererGaijiRule("<small>", "b355", "</small>"),
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
    **{f"b{value:03x}": _RendererImageGaijiRule("img_mark4") for value in range(0x25A, 0x352)},
    "b23b": _RendererImageGaijiRule("gaiji_icon"),
    **{f"b{value:03x}": _RendererImageGaijiRule("gaiji_icon") for value in range(0x357, 0x425)},
}
HC0146_NONPRINTING_CONTROL_OPS = {0x6D}

HC0146_STATE_SECTION_VALUES = {
    1,
    30,
    31,
    50,
    80,
    100,
    120,
    140,
    153,
    160,
    200,
    230,
    231,
    260,
    261,
    262,
    270,
    280,
    290,
    291,
    292,
    300,
    310,
    320,
    321,
    340,
    350,
    360,
    370,
    380,
    381,
    382,
    400,
    420,
    430,
    431,
    432,
    440,
    460,
    470,
    471,
    472,
    600,
    620,
    621,
    622,
    623,
    680,
    700,
    710,
    730,
    770,
    790,
    800,
    9999,
}

HC0146_FRAME_CLOSE_SECTION_VALUES = {
    50,
    80,
    100,
    120,
    140,
    160,
    600,
    650,
    670,
    700,
    760,
}

HC0146_SECTION_TEMPLATES: dict[int, tuple[str, str, str]] = {
    2: ('<span class="sub_caption">', "</span>", "sub_caption"),
    10: ('<span class="indent_minus">', "</span>", "indent_minus"),
    20: (
        '<div class="column_frame cm_f_resume"><p class="column_title cm_t_resume">'
        '<img src="b177.png" class="column_icon"></p><span class="indent_minus">',
        "</span></div>",
        "resume_column",
    ),
    21: (
        '<div class="column_frame cm_f_resume"><p class="column_title cm_t_resume">'
        '<img src="b177.png" class="column_icon"></p><span class="indent_minus">',
        "</span></div>",
        "resume_column",
    ),
    40: ('<div class="column_frame cm_f_point">', "</div>", "point_column"),
    90: (
        '<div class="column_frame cm_f_conversation_sentence">'
        '<p class="column_title cm_t_conversation_sentence">会話文成句</p>',
        "</div>",
        "conversation_sentence_column",
    ),
    110: (
        '<div class="column_frame cm_f_connect_conversation">'
        '<p class="column_title cm_t_connect_conversation">会話をつなぐ</p>',
        "</div>",
        "connect_conversation_column",
    ),
    130: (
        '<div class="column_frame cm_f_connection"><p class="column_title cm_t_connection">'
        '<img src="b243.png" class="column_icon"></p>',
        "</div>",
        "connection_column",
    ),
    170: ('<span class="indent_minus">', "</span>", "indent_minus"),
    171: ('<span class="indent_minus">', "</span>", "indent_minus"),
    172: ('<span class="indent_minus">', "</span>", "indent_minus"),
    173: ('<span class="indent_minus">', "</span>", "indent_minus"),
    180: ('<span class="indent_minus">', "</span>", "indent_minus"),
    181: ('<span class="indent_minus">', "</span>", "indent_minus"),
    190: ('<span class="indent_minus">', "</span>", "indent_minus"),
    351: ('<span class="indent_minus">', "</span>", "indent_minus"),
    210: ('<div class="column_frame exam_frame"><span class="exam_text">', "</span></div>", "exam_text"),
    211: ('<div class="column_frame exam_frame"><span class="exam_text">', "</span></div>", "exam_text"),
    212: ('<div class="column_frame exam_frame"><span class="exam_text">', "</span></div>", "exam_text"),
    213: ('<div class="column_frame exam_frame"><span class="exam_text">', "</span></div>", "exam_text"),
    214: (
        '<div class="column_frame exam_frame"><span class="exam_text"><span class="small_font">¶</span>',
        "</span></div>",
        "exam_text_small_marker",
    ),
    220: ('<span class="exam_translate">', "</span>", "exam_translate"),
    221: ('<span class="exam_translate">', "</span>", "exam_translate"),
    222: ('<span class="exam_translate">', "</span>", "exam_translate"),
    223: ('<span class="exam_translate">', "</span>", "exam_translate"),
    224: ('<span class="exam_translate">', "</span>", "exam_translate"),
    225: ('<span class="exam_translate">', "</span>", "exam_translate"),
    226: ('<span class="exam_translate">', "</span>", "exam_translate"),
    250: ('<span class="idiom_text_color">', "</span>", "idiom_text_color"),
    251: ('<span class="idiom_text_color">', "</span>", "idiom_text_color"),
    252: ('<span class="idiom_text_color">', "</span>", "idiom_text_color"),
}


def _hc0146_section_parts(code: str) -> tuple[list[str], str | None, str | None, bool]:
    value = _hc0146_section_value(code)
    if value is None:
        return [], None, None, False
    section = HC0146_SECTION_TEMPLATES.get(value)
    if section is not None:
        html, close, label = section
        return [html], close, label, False
    if value in HC0146_STATE_SECTION_VALUES:
        return [], None, f"state_{value}", False
    return ['<div class="honbun" style="margin-left:0.000000em;">'], "</div>", "fallback_honbun", True

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
HC00C6_NONPRINTING_CONTROL_OPS = {0x41}
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
HC00A4_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x5C, 0x6D}
HC00A4_SUPPRESSED_GAIJI_MARKERS = {"b12c", "b12d", "b12e", "b132", "b133"}
HC00A9_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC00AB_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC00BB_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC004D_SECTION_SUPPRESS_PREVIOUS_KEYS = {"217b", "217c"}
HC0076_TEMPLATE_IMAGE_MARKERS = {"2179", "217a"}
HC0073_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC0076_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC007D_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC008F_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C}
HC00C7_MARK4_GAIJI_MARKERS = frozenset(f"b{value:03x}" for value in range(0x121, 0x12D))
HC00C7_MARK_GAIJI_MARKERS = frozenset(
    [f"b{value:03x}" for value in range(0x12D, 0x131)]
    + [f"b{value:03x}" for value in range(0x135, 0x139)]
)
HC00C7_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x61, 0x6D}

HC013A_NONPRINTING_CONTROL_OPS = {0x6D}
HC013A_SUPPRESSED_GAIJI_MARKERS = {"a225", "a226", "b26a", "b26b"}
HC013A_CUSTOM_BITMAP_MARKERS = {"b263"}

HC013F_NONPRINTING_CONTROL_OPS = {0x6D}

HC009B_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC0092_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}

HC008C_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x61, 0x6D}

HC_HKDKSR_MEDICAL_RENDERERS = {"014A", "02C3", "02C6"}
HC_HKDKSR_MEDICAL_NONPRINTING_CONTROL_OPS = {0x6D}


def _hc00a6_honbun_div(indent: int) -> str:
    return f'<div class="honbun" style="margin-left:{indent:.6f}em;">'


def _hc00a4_section_value(code: str) -> int | None:
    try:
        return int(code, 16)
    except ValueError:
        return None


def _hc00c7_section_value(code: str) -> int | None:
    try:
        return int(code, 16)
    except ValueError:
        return None


def _hc00a4_honbun_div(margin_left: float, text_indent: float | None = None) -> str:
    if text_indent is None:
        return f'<div class="honbun" style="margin-left:{margin_left:.6f}em;">'
    return (
        '<div class="honbun" '
        f'style="margin-left:{margin_left:.6f}em;text-indent:{text_indent:.6f}em;">'
    )


def _hc00a4_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc00a4_section_value(code)
    if value is None:
        return [], None, None
    if value in {1, 2}:
        return [], None, "state_only"
    if value == 3:
        return [_hc00a4_honbun_div(1.0, 1.0)], "</div>", "honbun_indent"
    if 4 <= value <= 9:
        return [_hc00a4_honbun_div(float(value - 2), 0.0)], "</div>", "honbun_indent"
    if value in {10, 11, 15, 18, 19, 21, 24, 25, 27}:
        return [_hc00a4_honbun_div(1.0)], "</div>", "honbun"
    if value == 20:
        return ["<div>"], "</div>", "plain_div"
    if value == 22:
        return [_hc00a4_honbun_div(2.0)], "</div>", "honbun"
    if value == 23:
        return [_hc00a4_honbun_div(1.0)], "</div>", "url_icon_line"
    if value == 26:
        return [_hc00a4_honbun_div(2.0)], "</div>", "honbun"
    if value in {30, 31}:
        return [_hc00a4_honbun_div(0.0), "<hr>"], "</div>", "separator"
    if value == 32:
        return [_hc00a4_honbun_div(0.0)], "</div>", "honbun"
    if value == 33:
        return [_hc00a4_honbun_div(1.0)], "</div>", "honbun"
    if value == 40:
        return ['<div class="header">'], "</div>", "header"
    return [_hc00a4_honbun_div(1.0)], "</div>", "honbun_fallback"


def _hc00a9_section_parts(code: str, *, vertical: bool) -> tuple[list[str], str | None, str | None, int | None]:
    value = _hc02c0_section_value(code)
    if value is None:
        return [], None, None, None
    if value == 0x0C:
        return ['<div class="header">'], "</div>", "header", value
    margin_prop = "margin-top" if vertical else "margin-left"
    return [f'<div class="honbun" style="{margin_prop}:{value << 2}px">'], "</div>", "honbun", value


def _hc00bb_section_parts(code: str, *, vertical: bool) -> tuple[list[str], str | None, str | None, int | None]:
    value = _hc02c0_section_value(code)
    if value is None:
        return [], None, None, None
    if value == 0x0C:
        return ['<div class="footer">'], "</div>", "footer", value
    margin_prop = "margin-top" if vertical else "margin-left"
    return [f'<div class="honbun" style="{margin_prop}:{value << 2}px">'], "</div>", "honbun", value


def _hc00ab_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc02c0_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return [], None, "midashi_state"
    if 4 <= value <= 11 or value == 20:
        return ['<div class="honbun" style="margin-left:6.000000em;text-indent:-6.000000em;">'], "</div>", "hanging_honbun"
    if value == 12:
        return ['<div class="honbun" style="margin-left:3.000000em;">'], "</div>", "honbun_special"
    return [f'<div class="honbun" style="margin-left:{float(value):.6f}em;">'], "</div>", "honbun"


def _hc00ac_honbun_style(code: str) -> str:
    try:
        value = int(code, 16)
    except ValueError:
        value = 0
    if value == 4:
        return "margin-left:1em;"
    if value in {8, 9}:
        return "margin-left:7em;text-indent:-7em;"
    if 5 <= value <= 10:
        return "margin-left:6em;text-indent:-6em;"
    return f"margin-left:{value}em;"


def _hc00aa_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc00aa_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc00aa_section_value(code)
    if value is None:
        return [], None, None
    if value in HC00AA_MIDASHI_SECTION_VALUES:
        return ['<div class="midashi">'], "</div>", "midashi"
    if value == 20:
        return ['<div class="honbun" style="text-align:right;background-color:#DDDDDD;">'], "</div>", "right_honbun"
    if value == 101:
        return [
            '<div style="margin:10px 10px 10px 1.000000em;">'
            '<table border="0" cellpadding="4" cellspacing="1" bgcolor="#000000">'
            '<tr><td bgcolor="#FFFFFF">'
        ], "</td></tr></table></div>", "boxed_table"
    if value == 102:
        return ['<table border="0"><tr><td><img src="Nurse.png"></td><td><div class="indent102">'], "</div></td></tr></table>", "nurse_box"
    box_class = HC00AA_BOX_SECTION_CLASSES.get(value)
    if box_class is not None:
        return [f'<div class="{box_class}">'], "</div>", box_class
    if value == 106:
        return ['<hr size="1">', '<div class="honbun" style="margin-left:1.000000em;">'], "</div>", "honbun_hr"
    if value == 114:
        return [
            '<div class="honbun" style="margin-left:1.000000em;">',
            '<img src="tejyun.png" height="24">',
        ], "</div>", "tejyun"
    return ['<div class="honbun" style="margin-left:1.000000em;">'], "</div>", "honbun"


def _hc00a3_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc00a3_honbun_div(margin_left: float, text_indent: float | None = None) -> str:
    style = f"margin-left:{margin_left:.6f}em;"
    if text_indent is not None:
        style += f"text-indent:{text_indent:.6f}em;"
    return f'<div class="honbun" style="{style}">'


def _hc00a3_section_parts(
    code: str,
    previous_value: int | None,
    *,
    vertical: bool,
    answer_index: int,
    quiz_group_active: bool,
) -> tuple[list[str], str | None, str | None, int, bool]:
    value = _hc00a3_section_value(code)
    if value is None:
        return [], None, None, answer_index, quiz_group_active
    if value == 8:
        return [], None, "state_8", answer_index, True
    if value in {1, 6, 7, 9}:
        return [], None, f"state_{value}", answer_index, False
    if previous_value == 8 and value == 3:
        return [f'<div class="quiz" id="quiz{answer_index}" style="display:none;">'], "</div>", "quiz", answer_index, True
    if (previous_value == 8 or quiz_group_active) and value == 4:
        return [f'<div class="answer" id="ans{answer_index}" style="display:none;">'], "</div>", "answer", answer_index + 1, False
    if previous_value == 9 and value == 4:
        suffix = "V" if vertical else "H"
        return [
            _hc00a3_honbun_div(float(value), -1.0),
            '<img id="cmdAnswer" alt="" class="img_mark4" '
            f'src="kaisetsu{suffix}.gif" style="display:inline;cursor:hand;">',
            _hc00a3_honbun_div(float(value), -1.0).replace('">', 'display:none;">', 1),
        ], "</div></div>", "kaisetsu", answer_index, False
    if previous_value == 1 and value == 2:
        return [_hc00a3_honbun_div(1.0)], "</div>", "honbun_intro", answer_index, False
    if previous_value == 1 and value == 3:
        return [_hc00a3_honbun_div(1.0), "<nobr>"], "</nobr></div>", "honbun_nobr", answer_index, False
    if previous_value == 2 and value == 2:
        return [_hc00a3_honbun_div(float(value), -1.0)], "</div>", "honbun_hanging", answer_index, False
    if previous_value == 2 and value == 20:
        suffix = "V" if vertical else "H"
        return [
            _hc00a3_honbun_div(float(value), -1.0),
            '<img id="cmdAnswer" alt="" class="img_mark4" '
            f'src="kaisetsu{suffix}.gif" style="display:inline;cursor:hand;">',
            _hc00a3_honbun_div(float(value), -1.0).replace('">', 'display:none;">', 1),
        ], "</div></div>", "kaisetsu", answer_index, False
    if previous_value == 3 and value == 4:
        return [_hc00a3_honbun_div(1.0)], "</div>", "honbun", answer_index, False
    if previous_value == 4:
        return [_hc00a3_honbun_div(float(value))], "</div>", "honbun", answer_index, False
    if previous_value == 6 and value == 5:
        return [_hc00a3_honbun_div(float(value), -1.0)], "</div>", "honbun_hanging", answer_index, False
    if previous_value == 9 and value == 3:
        return [_hc00a3_honbun_div(float(value), -1.0)], "</div>", "honbun_hanging", answer_index, False
    return [_hc00a3_honbun_div(float(value))], "</div>", "honbun", answer_index, False


HC00C5_SECTION_IMAGES = {
    2: "arrow",
    4: "chui",
    5: "imi1",
    6: "imi2",
    7: "imi3",
    8: "imi",
    9: "ruiku",
    10: "sankou",
    11: "tsuiku",
    12: "yourei",
}


def _hc00c5_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc00c5_section_image_src(key: str, options: HcRenderOptions) -> str:
    found = _image_source_for_key(key, options)
    if found is not None:
        return found
    suffix = "_V" if options.vertical else ""
    return f"{key}{suffix}.png"


def _hc00c5_section_parts(code: str, options: HcRenderOptions) -> tuple[list[str], str | None, str | None]:
    value = _hc00c5_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return [], None, "midashi_state"
    if value == 3:
        return ['<div class="honbun">'], "</div>", "honbun"
    image_key = HC00C5_SECTION_IMAGES.get(value)
    if image_key is not None:
        src = _hc00c5_section_image_src(image_key, options)
        return [f'<div class="honbun"><img src="{_escape_attr(src)}" class="img_mark">'], "</div>", image_key
    return ['<div class="honbun">'], "</div>", "honbun_fallback"


def _hc00ad_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc00ad_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc00ad_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return [], None, "midashi_state"
    if value in {11, 21, 31}:
        return [
            '<div class="honbun" style="margin-left:0.000000em;font-size:6em;line-height:1.2em;">'
        ], "</div>", "large_character"
    if (12 <= value <= 20) or (22 <= value <= 30):
        return [
            '<div class="honbun" style="margin-left:2.000000em;text-indent:-1.000000em;">'
        ], "</div>", "paired_explanation"
    if 40 <= value <= 69:
        return [
            '<hr size="1">',
            '<div class="honbun" style="margin-left:2.000000em;text-indent:-1.000000em;">',
        ], "</div>", "hr_explanation"
    if 70 <= value <= 79:
        return [
            '<hr size="1">',
            '<div class="honbun" style="margin-left:1.000000em;">',
        ], "</div>", "hr_body"
    return [f'<div class="honbun" style="margin-left:{float(value):.6f}em;">'], "</div>", "honbun"


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

    HC014A, HC02C3, and HC02C6 use the section payload as a decimal-coded
    class/state value in several branches: for example body bytes ``00 40``
    map to CSS class ``indent40``. Non-decimal payloads such as ``002a`` still
    use their raw numeric value for table-state controls.
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


def _hc008c_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        return int(code, 16)
    except ValueError:
        return None


def _hc008c_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    """Return the decoded HC008C body-section wrapper for the understood branch.

    HC008C reads the raw two-byte 1f09 payload as an integer.  The DLL has
    stateful title/medicine branches, so this maps only code paths whose
    emitted tags are directly visible in the decompile; unknown states fall
    back to the renderer's documented margin-left rule.
    """

    value = _hc008c_section_value(code)
    if value is None:
        return [], None, None
    if value == 0:
        return [], None, "state_only"
    if value == 4:
        return ['<div class="medblk">'], "</div>", "medblk"
    if value == 6:
        return [f'<div style="margin-left:{value * 4}px;">'], "</div>", "indent"
    if value == 10:
        return ['<div class="medprice">'], "</div>", "medprice"
    if value == 11:
        return ['<div class="medimage">'], "</div>", "medimage"
    if value == 12:
        return ['<div class="medblkcaution">'], "</div>", "medblkcaution"
    if value == 13:
        return ['<div class="medcaution">'], "</div>", "medcaution"
    return [f'<div style="margin-left:{value * 4}px;">'], "</div>", "indent"


def _private_directive_text(text: str) -> str:
    cleaned = "".join(ch for ch in text if ord(ch) >= 0x20)
    return normalize_fullwidth_ascii(cleaned).replace("：", ":").replace("⦿", ":")


def _hc00a4_private_resource_name(text: str, prefix: str) -> str | None:
    directive = _private_directive_text(text).strip()
    if not directive.upper().startswith(prefix.upper() + ":"):
        return None
    name = directive.split(":", 1)[1].strip().replace("\\", "/")
    if name[:1].upper() == "F" and len(name) > 1:
        name = name[1:]
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.name


def _hc00a4_private_image_src(text: str, options: HcRenderOptions) -> str | None:
    name = _hc00a4_private_resource_name(text, "IMG")
    if name is None:
        return None
    return (
        options.image_sources.get(Path(name).stem.casefold())
        or options.image_sources.get(name.casefold())
        or options.image_sources.get(f"images/{name}".casefold())
    )


def _repair_hc00a4_private_html_fragment(fragment: str) -> tuple[str, int]:
    """Normalize HC00A4 fixed HTML fragments before inline insertion.

    IKUIKU fixed HTML includes contain table footers written as
    ``<tfoot><tr><td>...</td></tfoot>``. Browsers autoclose the row, but the
    generated proof HTML becomes source-unbalanced when multiple entries are
    concatenated. Insert the explicit row close without otherwise changing the
    fragment.
    """

    repairs = 0

    def close_tfoot_row(match: re.Match[str]) -> str:
        nonlocal repairs
        body = match.group(1)
        if re.search(r"</tr\s*>", body, flags=re.IGNORECASE):
            return match.group(0)
        repairs += 1
        return f"<tfoot>{body}</tr></tfoot>"

    repaired = re.sub(
        r"<tfoot\b[^>]*>(.*?)</tfoot\s*>",
        close_tfoot_row,
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return repaired, repairs


def _hc00a4_private_html_fragment(text: str, options: HcRenderOptions) -> tuple[str, int] | None:
    name = _hc00a4_private_resource_name(text, "HTM")
    if name is None:
        return None
    fragment = options.html_templates.get(name.casefold()) or options.html_templates.get(Path(name).stem.casefold())
    if fragment is None:
        return None
    return _repair_hc00a4_private_html_fragment(_hc00a0_rewrite_asset_sources(fragment, options))


def _extract_first_url(text: str) -> str | None:
    normalized = normalize_fullwidth_ascii(text)
    match = re.search(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+", normalized)
    return match.group(0) if match else None


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
HC02BE_NONPRINTING_CONTROL_OPS = {0x41}
HC02BE_ACCENT_MARKERS: dict[str, tuple[str, str, str, str]] = {
    # marker -> (visible base HTML, wrapper class, image class, image key)
    "a129": ("&#xFF5E;", "nowrap_full", "grave_full", "grave"),
    "a12a": ("&#xFF5E;", "nowrap_full", "aigu_full", "aigu"),
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
HC02BC_NOOP_SECTION_VALUES = {9999}
HC02BC_NONPRINTING_CONTROL_OPS = {0x41, 0x5C, 0x6D}
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

HC00B6_NOOP_MARKERS = frozenset(
    {
        "a278",
        "b23d",
        "b23e",
        "b23f",
        "b240",
        "b241",
        "b242",
        "b243",
        "b244",
        "b245",
        "b246",
        "b247",
        "b248",
        "b249",
        "b24a",
    }
)
HC00B6_IMAGE_MARKER_CLASSES = {
    "b347": "img_hinshi",
    "b348": "img_hinshi",
    "b25c": "img_hinshi",
    "b431": "img_hinshi",
}
HC00B6_STRONG_MARKERS: dict[str, str] = {
    **{f"b{value:03x}": str(index) for index, value in enumerate(range(0x146, 0x151), start=1)},
    **{f"b{value:03x}": str(index) for index, value in enumerate(range(0x15F, 0x16A), start=1)},
    **{f"b{value:03x}": str(index) for index, value in enumerate(range(0x16A, 0x173), start=12)},
    **{f"b{value:03x}": chr(ord("a") + index) for index, value in enumerate(range(0x151, 0x15F))},
    **{f"b{value:03x}": chr(ord("a") + index) for index, value in enumerate(range(0x173, 0x176))},
    "b353": "a",
    "b354": "b",
    "b355": "c",
    "b356": "d",
    "b357": "e",
    "b358": "f",
}
HC00B6_NONPRINTING_CONTROL_OPS = {0x6D}

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
HC02C8_NONPRINTING_CONTROL_OPS = {0x6D}
HC02C8_NOOP_SECTION_VALUES = {1, 8, 32, 34}
HC02C8_NO_BREAK_SECTION_VALUES = {12, 50, 51, 60, 61, 70, 71}
HC0147_TEMPLATE_IMAGE_MARKERS = frozenset(["a12e", *[f"b{value:03x}" for value in range(0x141, 0x146)]])
HC0147_PADDING_MARKERS = {f"b{value:03x}": value - 0x160 for value in range(0x160, 0x165)}
HC0147_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC0137_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
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
HC0063_NONPRINTING_CONTROL_OPS = {0x6D}
HC0063_DIRECT_IMAGE_MARKERS = {
    "a568": "kakko-left",
    "a569": "kakko-right",
    "b571": "right-triangle",
    "b65e": "circle-triangle",
    "b661": "white-triangle",
    "b667": "roman-alphabet",
}
HC0093_DIRECT_IMAGE_MARKERS = {
    "b140": ("arrow", "img_mark"),
    "b148": ("meaning", "img_mark"),
    "b14a": ("etymology", "img_mark"),
    "b14c": ("class_arrow", "img_mark2"),
    "b14d": ("class_arrow", "img_mark2"),
    "b14e": ("class_arrow", "img_mark2"),
}
HC0093_NOOP_MARKERS = {"b151", "b152", "b153", "b154", "b155"}
HC0093_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC0095_PAGE_KIND_MARKERS = {
    "b138": 0,
    "b139": 1,
    "b13a": 2,
    "b13b": 3,
    "b13c": 4,
}
HC0095_TEMPLATE_MARKERS = frozenset(
    {
        "b121",
        "b123",
        "b128",
        "b129",
        "b12a",
        "b12b",
        "b131",
        "b132",
        "b133",
        "b12e",
        "b12f",
    }
)
HC0095_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x61, 0x6D}
HC009F_SEASON_MARKERS = {
    "3d55": ("sp", "#fad9ea"),
    "3246": ("su", "#c2fac3"),
    "3d29": ("au", "#fbc8b8"),
    "455f": ("wi", "#cfdbff"),
    "3f37": ("ny", "#fefebe"),
}
HC009F_CATEGORY_MARKERS = {
    "3b7e": "1",
    "4537": "2",
    "434f": "3",
    "4038": "4",
    "3954": "5",
    "4630": "6",
    "3f22": "7",
}
HC009F_ORIENTED_MARKERS = {"b121", "b122"}
HC009F_SUPPRESSED_MARKERS = {"b123"}
HC0096_TEMPLATE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x121, 0x14A))
HC0096_REFLOW_STATE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x150, 0x153)) | frozenset(
    f"b{value:03x}" for value in range(0x155, 0x15B)
)
HC0096_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC0091_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC0091_MARK_IMAGE_PATTERNS = {
    # HC0091 compares the first, third, and sixth JIS pairs for these label
    # patterns, then replaces the full marker text with a template GIF.
    "215a": ("rei", "rei.gif", ("4e63",), ("215b",), 6),
    "4356": ("chikan", "chikan.gif", ("3439",), ("2161",), 14),
    "3272": ("kaisetsu", "kaisetsu.gif", ("4062",), ("2161",), 14),
    "4a64": ("hosoku", "hosoku.gif", ("422d",), ("2161",), 14),
}
HC0090_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC0090_LINEBREAK_MARKERS = {"a255", "a256"}
HC0135_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC0135_NOOP_SECTION_VALUES = {
    0x05,
    0x06,
    0x07,
    0x08,
    0x10,
    0x11,
    0x12,
    0x13,
    0x14,
    0x15,
    0x16,
    0x18,
    0x19,
    0x20,
    0x21,
    0x22,
    0x23,
    0x24,
    0x25,
    0x27,
    0x28,
    0x29,
    0x2B,
    0x2C,
    0x30,
    0x31,
    0x32,
    0x33,
    0x34,
    0x35,
    0x36,
    0x37,
    0x38,
    0x39,
}
HC0135_PRIVATE_IMAGE_DIRECTIVES = {
    "shikakuha": ("shikakuha", "img_icon"),
    "bunnpou": ("bunnpou", "img_icon"),
    "hyouki": ("hyouki", "img_icon"),
    "unnyou": ("unnyou", "img_icon"),
    "kazoekata": ("kazoekata", "img_icon"),
    "1652-1": ("1652-1", "img_icon"),
    "1653-abc": ("1653-abc", "img_icon"),
    "1653-def": ("1653-def", "img_icon"),
    "1653-ghij": ("1653-ghij", "img_icon"),
    "1654-kl": ("1654-kl", "img_icon"),
    "genngotaisyou": ("genngotaisyou", "img_icon"),
}
HC014F_NONPRINTING_CONTROL_OPS = {0x02, 0x5C, 0x6D}
HC013C_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x6D}
HC013C_ICON_DIRECTIVES = {
    "2331": "1.png",
    "2332": "2.png",
    "2333": "3.png",
    "2334": "4.png",
}
HC013C_NOOP_MARKERS = {"a435", "a436"}
HC00B3_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC_GEN_YEAR_RENDERERS = {"02C4", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"}
HC_GEN_YEAR_NOOP_SECTION_CODES = {"270f", "9999"}
HC_GEN_YEAR_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x6D}
HC_GEN_YEAR_ICON_DIRECTIVES = {
    "2331": "1.png",
    "2332": "2.png",
    "2333": "3.png",
    "2334": "4.png",
}
HC_GEN_YEAR_IMG_MARK_MARKERS = {"b12d", "b12e", "b12f"}
HC_GEN_YEAR_IMG_MARK2_MARKERS = frozenset(f"b{value:03x}" for value in range(0x132, 0x138))
HC_GEN_YEAR_NOOP_MARKERS = {"b130", "b131", "b138"}
HC_GEN_YEAR_LITERAL_MARKERS_BY_RENDERER = {
    # HC02CB/HC02CC/HC02CD special-case B135 before the B132-B137 image-marker range.
    "02CB": {"b135": "\U00020bb7"},
    "02CC": {"b135": "\U00020bb7"},
    "02CD": {"b135": "\U00020bb7"},
}
HC00C4_SECTION_ICON_IMAGES = {
    "0003": "betsumei.png",
    "0004": "yourei.png",
    "0005": "hosoku.png",
    "0006": "reibun.png",
}
HC00C4_INLINE_SECTION_IMAGES = {
    "000a": "arrow1.png",
    "000b": "arrow2.png",
    "000c": "arrow3.png",
    "0011": "gogen.png",
    "0012": "wasei.png",
}
HC00C4_NOOP_SECTION_CODES = {"0015", "0016", "0017", "0018"}
HC00C4_NUMBER_MARKERS = frozenset(f"b{value:03x}" for value in range(0x126, 0x130))
HC00C4_NONPRINTING_CONTROL_OPS = {0x02, 0x4C, 0x5C, 0x6D}
HC00C4_GAIJI_CLASS_OVERRIDES = {
    "b137": "gaiji_k",
    "b138": "gaiji_k",
    "b13c": "gaiji_b",
}
HC00C4_WAKU_INLINE_IMAGES = {
    "214e": ("waku_l.png", "waku_l"),
    "214f": ("waku_r.png", "waku_r"),
}

HC005C_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC005C_CUSTOM_GAIJI_EXCLUDED = {"b132", "b133", "b139", "b13b", "b143", "b573", "b576", "b578", "b57a"}
HC005C_MARK_IMAGE_LABELS = {
    ("386c", "4b21"): "gohou.gif",
    ("386c", "3741"): "gokei.gif",
    ("482f", "323b"): "hatsuon.gif",
    ("4a51", "3439"): "henkan.gif",
    ("4866", "3353"): "hikaku.gif",
    ("3272", "4062"): "kaisetu.gif",
    ("3458", "4f22"): "kanren.gif",
    ("4456", "246a"): "tsuzuri.gif",
    ("4649", "245f"): "yomikata.gif",
    ("4d51", "4b21"): "youhou.gif",
    ("4d33", "4d68"): "yurai.gif",
}

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
HC0065_NONPRINTING_CONTROL_OPS = {0x4C, 0x61, 0x6D}
HC0068_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC0069_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC0094_TEMPLATE_IMAGE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x121, 0x13E)) - {"b13d"}
HC0094_COLOR_DIV_MARKERS = {"b13e": "aka", "b13f": "beni"}
HC0094_CLASS_ARROW_MARKER = "b148"
HC0094_STATE_MARKERS = frozenset(f"b{value:03x}" for value in range(0x150, 0x15A))
HC0094_SUPPRESSED_MARKERS = {"b139", "b140", "b13d"}
HC0094_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC0067_NONPRINTING_CONTROL_OPS = {0x6D}
HC0020_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x61, 0x6D}
HC0020_SUPPRESSED_JIS_MARKERS = {"224d"}
HC008B_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC_BRITANNICA_PANEL_RENDERERS = {"00D3", "00D5", "00DE"}
HC_BRITANNICA_SUPPRESSED_GAIJI_MARKERS = {"b421", "b422"}
HC0048_NONPRINTING_CONTROL_OPS = {0x02, 0x41, 0x4C, 0x5C, 0x6D}
HC0048_MIDASHI_MARKERS = {"2178", "217a", "2221", "2223", "2227"}
HC00AC_NONPRINTING_CONTROL_OPS = {0x02, 0x41, 0x4C, 0x5C, 0x6D}
HC00AC_SUPPRESSED_GAIJI_MARKERS = {"b139", "b13a", "b13b"}
HC00AA_NONPRINTING_CONTROL_OPS = {0x5C, 0x6D}
HC00AA_MIDASHI_SECTION_VALUES = {1, 16, 66, 116, 300, 304, 1010, 1012, 2000, 2005, 3000}
HC00AA_BOX_SECTION_CLASSES = {
    101: "indent101",
    102: "indent102",
    105: "indent105",
    109: "indent109",
    110: "indent110",
}
HC00A3_NONPRINTING_CONTROL_OPS = {0x41, 0x4C, 0x5C, 0x6D}
HC00C5_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}
HC00AD_NONPRINTING_CONTROL_OPS = {0x4C, 0x5C, 0x6D}

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
HC0142_NONPRINTING_CONTROL_OPS = {0x5C, 0x61, 0x6D}
HC013D_NONPRINTING_CONTROL_OPS = {0x6D}
HC013D_MED_SECTION_CLASSES = {
    4: ("div", ' class="title3"'),
    6: ("span", ' class="med"'),
    8: ("div", ' class="medblk"'),
    10: ("div", ' class="medprice"'),
    11: ("div", ' class="medimage"'),
    20: ("div", ' class="mednamelist1"'),
    21: ("div", ' class="mednamelist2"'),
    22: ("div", ' class="mednamelist3"'),
}
HC013D_INDENT_SECTION_VALUES = frozenset({40, 41, *range(50, 70)})
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


def _packed_bcd_to_int(payload: bytes) -> int:
    value = 0
    for byte in payload:
        value = value * 10 + ((byte >> 4) & 0x0F)
        value = value * 10 + (byte & 0x0F)
    return value


def _decode_pointer_payload(payload: bytes, *, packed_bcd: bool = False) -> dict[str, int] | None:
    if len(payload) < 6:
        return None
    if packed_bcd:
        block = _packed_bcd_to_int(payload[:4])
        offset = _packed_bcd_to_int(payload[4:6])
    else:
        block = int.from_bytes(payload[:4], "big")
        offset = int.from_bytes(payload[4:6], "big")
    return {"block": block, "offset": offset}


def _link_payload_is_packed_bcd(options: HcRenderOptions) -> bool:
    return _renderer_code(options) in HC_PACKED_BCD_LINK_RENDERERS


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


def _safe_href_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.:-]+", "-", value)


def _audio_href(target: dict[str, Any] | None) -> str:
    if target is None:
        return "#lv-audio-unresolved"
    return f"#lv-audio-{_safe_href_token(str(target['resource_id']))}"


def _audio_original_href(target: dict[str, Any] | None) -> str:
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


def _is_hc00c4_renderer(options: HcRenderOptions) -> bool:
    return _renderer_code(options) == "00C4"


def _is_hc005c_renderer(options: HcRenderOptions) -> bool:
    return _renderer_code(options) == "005C"


def _is_hc_hkdksr_medical_renderer(options: HcRenderOptions) -> bool:
    return _renderer_code(options) in HC_HKDKSR_MEDICAL_RENDERERS


def _is_hc_britannica_panel_renderer(options: HcRenderOptions) -> bool:
    return _renderer_code(options) in HC_BRITANNICA_PANEL_RENDERERS


def _link_css_class(options: HcRenderOptions, start_op: int | None) -> str:
    if _is_hc005c_renderer(options) and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _is_hc00c4_renderer(options) and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "008C":
        if start_op == 0x42:
            return "lv-hc-link lineLink2"
        if start_op in {0x43, 0x44}:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "0137" and start_op in {0x3B, 0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0094" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0093" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "02BE" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) in {"0095", "0096", "009F"} and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0091" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0092" and start_op in {0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00AB" and start_op in {0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0090" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "0135" and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "014F":
        if start_op == 0x42:
            return "lv-hc-link Link"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "009B" and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _is_hc_hkdksr_medical_renderer(options):
        if start_op == 0x42:
            return "lv-hc-link lineLink2"
        if start_op in {0x43, 0x44}:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "00A6" and start_op in {0x3B, 0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00A4" and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00A9" and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00BB" and start_op in {0x3B, 0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00AA" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00A3" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00C7" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00C5" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "00AD" and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "004D":
        if start_op == 0x42:
            return "lv-hc-link lineLink2"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) in {"0065", "00B6"} and start_op in {0x42, 0x43, 0x44}:
        return "lv-hc-link lLink"
    if _renderer_code(options) in {"0067", "0068", "008B", "0069"}:
        if start_op == 0x42:
            return "lv-hc-link lineLink2"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "0020":
        if start_op == 0x42:
            return "lv-hc-link lineLink2"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "02C5" and start_op in {0x42, 0x43}:
        return "lv-hc-link lLink"
    if _renderer_code(options) == "02C8":
        if start_op == 0x42:
            return "lv-hc-link Link"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "0151":
        if start_op == 0x42:
            return "lv-hc-link Link"
        if start_op == 0x43:
            return "lv-hc-link lineLink"
    if (
        _renderer_code(options)
        in {
            "0048",
            "00AC",
            "00B3",
            "012F",
            "0131",
            "0132",
            "0136",
            "013C",
            "0142",
            "0146",
            "0147",
            "0157",
            "02BF",
            "02C0",
            "02C1",
            "02CA",
        }
        or _is_hc_gen_year_renderer(options)
    ) and start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) == "02BC" and start_op in {0x42, 0x43, 0x44}:
        return "lv-hc-link lineLink"
    if _renderer_code(options) in {"009C", "009D", "012D", "013D", "0141", "0144", "0145", "02C2", "03E8"} and start_op in {
        0x42,
        0x43,
    }:
        return "lv-hc-link lineLink"
    return "lv-hc-link"


def _hc008c_forces_plain_line_link(data: bytes, offset: int) -> bool:
    return (
        _two_byte_key_at(data, offset + 2) in {"b12d", "b12e"}
        or _two_byte_key_at(data, offset - 2) == "224d"
        or _two_byte_key_at(data, offset - 4) == "224d"
    )


def _hc0063_forces_plain_line_link(data: bytes, offset: int) -> bool:
    return (
        _two_byte_key_at(data, offset + 4) in {"b12d", "b12e"}
        or _two_byte_key_at(data, offset - 2) == "224d"
        or _two_byte_key_at(data, offset - 4) == "224d"
    )


def _hc0076_forces_plain_line_link(data: bytes, offset: int) -> bool:
    return _hc0063_forces_plain_line_link(data, offset)


def _hc007d_forces_plain_line_link(data: bytes, offset: int) -> bool:
    return _hc0063_forces_plain_line_link(data, offset)


def _hc0073_forces_plain_line_link(data: bytes, offset: int) -> bool:
    return _hc0063_forces_plain_line_link(data, offset)


def _link_css_class_for_context(options: HcRenderOptions, ctx: _Context | None, data: bytes) -> str:
    if _renderer_code(options) == "008C" and ctx is not None and ctx.start_op == 0x42:
        if _hc008c_forces_plain_line_link(data, ctx.start_offset):
            return "lv-hc-link lineLink"
        if "hc008c_midashi" in ctx.flags:
            return "lv-hc-link lineLink3"
        return "lv-hc-link lineLink2"
    if _renderer_code(options) == "0063" and ctx is not None:
        if ctx.start_op == 0x42:
            return "lv-hc-link lineLink" if _hc0063_forces_plain_line_link(data, ctx.start_offset) else "lv-hc-link lineLink2"
        if ctx.start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "004D" and ctx is not None and ctx.start_op == 0x42:
        if "hc004d_midashi" in ctx.flags:
            return "lv-hc-link lineLink3"
        return "lv-hc-link lineLink2"
    if _renderer_code(options) == "0076" and ctx is not None:
        if ctx.start_op == 0x42:
            if _hc0076_forces_plain_line_link(data, ctx.start_offset):
                return "lv-hc-link lineLink"
            if "hc0076_midashi" in ctx.flags:
                return "lv-hc-link lineLink3"
            return "lv-hc-link lineLink2"
        if ctx.start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "007D" and ctx is not None:
        if ctx.start_op == 0x42:
            return "lv-hc-link lineLink" if _hc007d_forces_plain_line_link(data, ctx.start_offset) else "lv-hc-link lineLink2"
        if ctx.start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "0073" and ctx is not None:
        if ctx.start_op == 0x42:
            if _hc0073_forces_plain_line_link(data, ctx.start_offset):
                return "lv-hc-link lineLink"
            if "hc0073_midashi" in ctx.flags:
                return "lv-hc-link lineLink3"
            return "lv-hc-link lineLink2"
        if ctx.start_op == 0x43:
            return "lv-hc-link lineLink"
    if _renderer_code(options) == "008F" and ctx is not None and ctx.start_op in {0x42, 0x43}:
        return "lv-hc-link lineLink"
    return _link_css_class(options, ctx.start_op if ctx else None)


def _style_start_spec(op: int, options: HcRenderOptions) -> tuple[str, str] | None:
    if _is_hc005c_renderer(options) and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0091" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0092" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "00AB" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0090" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0135" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0135" and op in {0x06, 0x41}:
        return None
    if _renderer_code(options) == "014F" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "014F" and op == 0x41:
        return None
    if _renderer_code(options) == "014F" and op == 0xE0:
        return None
    if _is_hc00c4_renderer(options) and op == 0x04:
        return ("span", ' class="hankaku"')
    if _is_hc00c4_renderer(options) and op == 0x41:
        return None
    if _renderer_code(options) == "008C" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "008C" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "00AA" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00AA" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "00A3" and op in {0x04, 0x41}:
        return ("span", ' class="hankaku"') if op == 0x04 else None
    if _renderer_code(options) == "00C5" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00C5" and op == 0x41:
        return None
    if _renderer_code(options) == "00AD" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00AD" and op == 0x41:
        return None
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
    if _renderer_code(options) == "00A4" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00A4" and op == 0x41:
        return None
    if _renderer_code(options) == "00A9" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0048" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0048" and op == 0x41:
        return None
    if _renderer_code(options) == "004D" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0076" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "0073" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "007D" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "008F" and op in {0x04, 0x41}:
        return None
    if _renderer_code(options) == "00C7" and op in {0x04, 0x12, 0x41}:
        return None
    if _renderer_code(options) == "0063" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0063" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) in {"0067", "0068", "008B", "0069"} and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) in {"0067", "0068", "008B", "0069"} and op == 0x41:
        return None
    if _renderer_code(options) == "0020" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0020" and op == 0x41:
        return None
    if _renderer_code(options) == "00B6" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00AC" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "00AC" and op == 0x41:
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
    if _renderer_code(options) == "02C8" and op == 0x04:
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
    if _renderer_code(options) == "0132" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _is_hc_gen_year_renderer(options) and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0151" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0142" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "02C5" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "02C8" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "0151" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "0142" and op == 0x41:
        return ("div", ' class="midashi"')
    if _renderer_code(options) == "0147" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0094" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0093" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0096" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0137" and op == 0x04:
        return ("span", ' class="hankaku"')
    if _renderer_code(options) == "0147" and op == 0x41:
        return None
    if _renderer_code(options) == "0094" and op == 0x41:
        return None
    if _renderer_code(options) == "0137" and op == 0x41:
        return None
    if op == 0x41 and _renderer_code(options) == "0065":
        return None
    if op == 0x41 and _renderer_code(options) == "00B6":
        return ("div", ' class="midashi"')
    if op in {0x41, 0xE0, 0xE1} and _renderer_code(options) == "009C":
        return None
    if op in {0x41, 0x4C} and _renderer_code(options) in {"012F", "02BF", "02C1", "02C2"}:
        return None
    if op == 0x41 and _renderer_code(options) == "012D":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "012E":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "0132":
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "009D":
        return None
    if op == 0x41 and _renderer_code(options) in {"00B3", "0136", "013C", "02C0", "02CA"}:
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "013D":
        return ("div", ' class="midashi"')
    if op == 0x41 and _is_hc_gen_year_renderer(options):
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "02BE":
        return None
    if op == 0x41 and _renderer_code(options) == "0144":
        return None
    if op == 0x41 and _renderer_code(options) == "0145":
        return None
    if op == 0x41 and _renderer_code(options) == "03E8":
        return None
    if op == 0x41 and _renderer_code(options) == "0141":
        return None
    if op == 0x41 and _renderer_code(options) == "00C6":
        return None
    if op == 0x41 and _renderer_code(options) in {"0146", "0157"}:
        return ("div", ' class="midashi"')
    if op == 0x41 and _renderer_code(options) == "0158":
        return ("div", ' class="midashi"')
    return STYLE_START_TAGS.get(op)


def _style_close_tag(start_op: int, options: HcRenderOptions) -> str | None:
    if _renderer_code(options) in {"0142", "0147"} and start_op == 0x10:
        return "label"
    if _renderer_code(options) == "0091" and start_op == 0x04:
        return "span"
    if _renderer_code(options) == "0090" and start_op == 0x04:
        return "span"
    if _renderer_code(options) == "014F" and start_op == 0x04:
        return "span"
    if _renderer_code(options) == "0131" and start_op == 0x41:
        return "div"
    spec = _style_start_spec(start_op, options)
    return spec[0] if spec else None


def _renderer_halfwidth_span_needs_explicit_close(start_op: int, options: HcRenderOptions) -> bool:
    return start_op == 0x04 and (
        _is_hc005c_renderer(options)
        or _renderer_code(options) in {"0073", "0076", "007D", "008F"}
    )


def _decode_next_jis_text(data: bytes, offset: int) -> str:
    if offset + 1 >= len(data):
        return ""
    first = data[offset]
    second = data[offset + 1]
    if not (0x21 <= first <= 0x7E and 0x21 <= second <= 0x7E):
        return ""
    return decode_jis_pair(data[offset : offset + 2]) or ""


def _decode_jis_text_bytes(data: bytes) -> str:
    parts: list[str] = []
    index = 0
    while index + 1 < len(data):
        if 0x21 <= data[index] <= 0x7E and 0x21 <= data[index + 1] <= 0x7E:
            text = decode_jis_pair(data[index : index + 2])
            if text:
                parts.append(text)
        index += 2
    return "".join(parts)


def _jis_key_at(data: bytes, offset: int) -> str | None:
    if offset + 1 >= len(data):
        return None
    first = data[offset]
    second = data[offset + 1]
    if not (0x21 <= first <= 0x7E and 0x21 <= second <= 0x7E):
        return None
    return f"{first:02x}{second:02x}"


def _two_byte_key_at(data: bytes, offset: int) -> str | None:
    if offset < 0 or offset + 1 >= len(data):
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


def _hc0158_has_following_rank_close_before_heading_end(data: bytes, offset: int) -> bool:
    while offset + 1 < len(data):
        if data[offset] == 0x1F and data[offset + 1] in {0x09, 0x0A, 0x61}:
            return False
        if data[offset] == 0xB3 and data[offset + 1] == 0x54:
            return True
        offset += 1
    return False


def _has_two_byte_key_before_section_end(data: bytes, offset: int, key: str) -> bool:
    wanted = bytes.fromhex(key)
    while offset + 1 < len(data):
        if data[offset] == 0x1F and data[offset + 1] in {0x09, 0x0A, 0x61}:
            return False
        if data[offset : offset + 2] == wanted:
            return True
        offset += 1
    return False


def _find_control_offset(data: bytes, offset: int, op: int) -> int | None:
    while offset + 1 < len(data):
        if data[offset] == 0x1F and data[offset + 1] == op:
            return offset
        offset += 1
    return None


def _hc0158_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc0158_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc0158_section_value(code)
    if value is None:
        return [], None, None
    css_class = HC0158_SECTION_CLASSES.get(value)
    if css_class is not None:
        if value in HC0158_COLUMN_SECTION_VALUES:
            return [f'<div class="{css_class}"><div>'], "</div></div>", css_class
        return [f'<div class="{css_class}">'], "</div>", css_class
    if value in HC0158_STATE_SECTION_VALUES:
        return [], None, f"state_{value}"
    return [], None, None


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


def _active_private_context(contexts: list[_Context]) -> _Context | None:
    for ctx in reversed(contexts):
        if ctx.kind == "private":
            return ctx
    return None


def _context_halfwidth_depth(contexts: list[_Context]) -> int:
    return contexts[-1].halfwidth_depth if contexts else 0


def _consume_private_style_control(ctx: _Context, op: int, options: HcRenderOptions) -> bool:
    if _style_start_spec(op, options) is not None:
        ctx.style_stack.append(op)
        if op == 0x04:
            ctx.halfwidth_depth += 1
        return True
    if op not in STYLE_END_OPS:
        return False
    start_op = STYLE_END_OPS[op]
    if start_op in ctx.style_stack:
        while ctx.style_stack:
            popped = ctx.style_stack.pop()
            if popped == 0x04 and ctx.halfwidth_depth:
                ctx.halfwidth_depth -= 1
            if popped == start_op:
                break
    return True


def _close_context_styles(ctx: _Context, options: HcRenderOptions) -> None:
    while ctx.style_stack:
        popped = ctx.style_stack.pop()
        close_tag = _style_close_tag(popped, options)
        if close_tag:
            ctx.parts.append(f"</{close_tag}>")
        elif _renderer_halfwidth_span_needs_explicit_close(popped, options):
            ctx.parts.append("</span>")
        if popped == 0x04 and ctx.halfwidth_depth:
            ctx.halfwidth_depth -= 1


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


def _hc0190_template_anchor_prefix(section_html: str) -> str | None:
    match = re.fullmatch(
        r'(<a\s+[^>]+>)(?P<label>.*?)</a>(?P<trailing><br\s*/?>)?',
        section_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    return match.group(1)


def _hc0190_apply_template(template: str, sections: dict[int, str], stats: Counter[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        section = int(match.group(1), 10)
        if section in sections:
            section_html = sections[section]
            tail = template[match.end() :]
            if re.match(r"\s*<img\b", tail, flags=re.IGNORECASE):
                anchor_prefix = _hc0190_template_anchor_prefix(section_html)
                if anchor_prefix is not None:
                    stats["hc0190_template_link_prefixes"] += 1
                    stats["hc0190_template_placeholders_filled"] += 1
                    return anchor_prefix
            stats["hc0190_template_placeholders_filled"] += 1
            return section_html
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


def _hc013a_custom_dib_html(key: str, options: HcRenderOptions, *, in_heading: bool) -> tuple[str, bool]:
    """Return the HC013A custom-DIB image HTML recovered from FUN_1000175e.

    HC013A asks imgctl to derive variant PNGs from a base ``%4x.png`` when it
    exists, but the body renderer emits an ``img_dummy`` spacer plus the derived
    filename even if the bitmap is absent.  Missing files are therefore a
    resource condition, not an unknown renderer branch.
    """

    stem = key.lower()
    if in_heading:
        suffix = "_M"
        css_class = "img_gaiji_midashi"
        style = ' style="height:1.0em;"'
    elif options.vertical:
        suffix = "_C"
        css_class = "img_gaiji_v"
        style = ""
    else:
        suffix = ""
        css_class = "img_gaiji"
        style = ' style="height:1em;"'
    vertical_suffix = "_V" if options.vertical else ""
    derived_stem = f"{stem}{suffix}{vertical_suffix}"
    derived_name = f"{derived_stem}.png"
    lookup_stem = derived_stem.lower()
    lookup_name = derived_name.lower()
    src = options.image_sources.get(lookup_stem) or options.image_sources.get(lookup_name) or derived_name
    dummy_src = _dummy_image_source(options) or "dummy.gif"
    resolved = lookup_stem in options.image_sources or lookup_name in options.image_sources
    return (
        f'<img class="img_dummy" src="{_escape_attr(dummy_src)}">'
        f'<img src="{_escape_attr(src)}"{style} class="{css_class}">',
        resolved,
    )


def _dummy_image_source(options: HcRenderOptions) -> str | None:
    return (
        _image_source_for_key("dummy", options)
        or _image_source_for_key("dummy.gif", options)
        or _image_source_for_key("dummy.GIF", options)
    )


def _append_hc0093_template_marker(
    parts: list[str],
    key: str,
    image_key: str,
    css_class: str,
    options: HcRenderOptions,
    stats: Counter[str],
) -> bool:
    image_src = _image_source_for_key(image_key, options)
    if image_src is None:
        return False
    if css_class == "img_mark":
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc0093_dummy_images"] += 1
    _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
    stats["hc0093_template_image_markers"] += 1
    return True


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
            "0069",
            "0063",
            "0067",
            "008B",
            "0076",
            "0048",
            "00AC",
            "0094",
            "009B",
            "00A6",
            "00B3",
            "008C",
            "0093",
            "0096",
            "009C",
            "009D",
            "00A4",
            "00A9",
            "0068",
            "012E",
            "012F",
            "0131",
            "0136",
            "013C",
            "013D",
            "0141",
            "0137",
            "0147",
            "0144",
            "0145",
            "0151",
            "02C8",
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


def _append_hc00c7_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
) -> None:
    image_src = _image_source_for_key(key, options)
    if key in HC00C7_MARK4_GAIJI_MARKERS and image_src is not None:
        _append_renderer_image_gaiji(parts, key, image_src, "img_mark4", stats)
        stats["hc00c7_img_mark4_images"] += 1
        return
    if key in HC00C7_MARK_GAIJI_MARKERS and image_src is not None:
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc00c7_dummy_images"] += 1
        _append_renderer_image_gaiji(parts, key, image_src, "img_mark", stats)
        stats["hc00c7_img_mark_images"] += 1
        return
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    if image_src is not None:
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc00c7_dummy_images"] += 1
        _append_renderer_image_gaiji(parts, key, image_src, "img_gaiji", stats)
        stats["hc00c7_img_gaiji_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc007d_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc007d_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc007d_{css_class}_images"] += 1
        return
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc008f_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc008f_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc008f_{css_class}_images"] += 1
        return
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0073_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc0073_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0073_{css_class}_images"] += 1
        return
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc00a4_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy_src = _dummy_image_source(options)
        if dummy_src is not None:
            parts.append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
            stats["hc00a4_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats["hc00a4_template_gaiji"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _hc005c_image_source(image_name: str, options: HcRenderOptions) -> str:
    stem = Path(image_name).stem.lower()
    return (
        options.image_sources.get(stem)
        or options.image_sources.get(image_name.lower())
        or options.image_sources.get(image_name)
        or image_name
    )


def _append_hc005c_image(parts: list[str], image_name: str, css_class: str, options: HcRenderOptions) -> None:
    dummy = _hc005c_image_source("dummy.gif", options)
    src = _hc005c_image_source(image_name, options)
    parts.append(
        f'<img src="{_escape_attr(dummy)}" class="img_dummy">'
        f'<img src="{_escape_attr(src)}" class="{_escape_attr(css_class)}">'
    )


def _append_hc005c_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options) or f"{key}.gif"
    css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
    dummy = _hc005c_image_source("dummy.gif", options)
    stats["gaiji_image"] += 1
    parts.append(
        f'<img src="{_escape_attr(dummy)}" class="img_dummy">'
        f'<img class="lv-hc-gaiji {_escape_attr(css_class)}" '
        f'src="{_escape_attr(image_src)}" alt="{_escape_attr(key)}" '
        f'data-gaiji-code="{_escape_attr(key)}">'
    )


def _append_hc0069_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc0069_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0069_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0068_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc0068_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0068_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0091_named_image(parts: list[str], image_key: str, image_name: str, options: HcRenderOptions, stats: Counter[str]) -> None:
    dummy = _dummy_image_source(options)
    if dummy is not None:
        parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
        stats["hc0091_dummy_images"] += 1
    src = _image_source_for_key(image_key, options) or _image_source_for_key(image_name, options) or image_name
    parts.append(f'<img src="{_escape_attr(src)}" class="img_mark">')
    stats["hc0091_mark_images"] += 1


def _hc0091_marker_image_match(data: bytes, offset: int) -> tuple[str, str, int] | None:
    key = _jis_key_at(data, offset)
    if key is None:
        return None
    pattern = HC0091_MARK_IMAGE_PATTERNS.get(key)
    if pattern is None:
        return None
    image_key, image_name, third_pair_options, sixth_pair_options, consumed = pattern
    if key == "215a":
        if _jis_key_at(data, offset + 2) in third_pair_options and _jis_key_at(data, offset + 4) in sixth_pair_options:
            return image_key, image_name, consumed
        return None
    if _jis_key_at(data, offset + 4) in third_pair_options and _jis_key_at(data, offset + 10) in sixth_pair_options:
        return image_key, image_name, consumed
    return None


def _append_hc0091_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc0091_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0091_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0090_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc0090_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0090_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc014f_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc014f_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc014f_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0135_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc0135_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0135_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0020_gaiji_value(
    parts: list[str],
    text_parts: list[str] | None,
    key: str,
    options: HcRenderOptions,
    stats: Counter[str],
    *,
    in_heading: bool,
) -> None:
    mapped = options.gaiji_map.get(key)
    if mapped:
        stats["gaiji_unicode"] += 1
        _append_text(parts, mapped)
        if text_parts is not None:
            text_parts.append(mapped)
        return
    image_src = _image_source_for_key(key, options)
    if image_src is not None:
        dummy = _dummy_image_source(options)
        if dummy is not None:
            parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
            stats["hc0020_dummy_images"] += 1
        css_class = "img_gaiji_midashi" if in_heading else "img_gaiji"
        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
        stats[f"hc0020_{css_class}_images"] += 1
        return
    stats["gaiji_placeholder"] += 1
    parts.append(
        f'<span class="lv-hc-gaiji lv-hc-gaiji-placeholder" '
        f'data-gaiji-code="{_escape_attr(key)}"></span>'
    )


def _append_hc0020_named_image(parts: list[str], name: str, css_class: str, options: HcRenderOptions) -> None:
    src = _image_source_for_key(name, options) or _image_source_for_key(f"{name}.png", options) or f"{name}.png"
    parts.append(f'<img src="{_escape_attr(src)}" class="{_escape_attr(css_class)}">')


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


def _hc02be_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    try:
        value = int(code, 16)
    except ValueError:
        return [], None, None
    if value in {0x67, 0x71, 0x7B, 0x270F}:
        return [], None, "state_only"
    if value == 1:
        return [f'<div class="ind_{_escape_attr(code)}">'], "</div>", "div"
    return [f'<span class="ind_{_escape_attr(code)}">'], "</span>", "span"


def _hc02bc_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        if all(ch in "0123456789" for ch in code):
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc02bc_section_parts(code: str, image_sources: dict[str, str]) -> list[str]:
    value = _hc02bc_section_value(code)
    if value is None or value in HC02BC_NOOP_SECTION_VALUES:
        return []
    if value == 1:
        return ['<div class="midashi">']
    if value == 2:
        parts: list[str] = []
        src = image_sources.get("fukumidashi") or image_sources.get("fukumidashi.png")
        if src:
            parts.append(f'<img src="{_escape_attr(src)}" class="img_mark2">')
        parts.append('<div class="komidashi"  style="margin-left:1.000000em;">')
        return parts
    digit = value % 10
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


def _hc005c_heading_class(data: bytes, offset: int) -> str:
    """Infer HC005C e/j heading branch from the first printable heading bytes.

    The DLL uses entry-address ranges for the English/Japanese half split.
    The standalone renderer does not receive the body address, so use the
    body-local signal that follows the same branch in observed English entries:
    halfwidth/fullwidth ASCII at the start of the heading selects eMidashi.
    """

    pos = offset
    while pos + 1 < len(data) and data[pos] == 0x1F:
        op = data[pos + 1]
        arg_len = control_arg_length(data, pos)
        if op in {0x04, 0x0E, 0x10, 0x12, 0xE0}:
            pos += 2 + arg_len
            continue
        break
    if pos + 1 < len(data):
        if data[pos] == 0x23:
            return "eMidashi"
        if 0x21 <= data[pos] <= 0x7E and 0x21 <= data[pos + 1] <= 0x7E:
            text = decode_jis_pair(data[pos : pos + 2])
            if text and normalize_fullwidth_ascii(text) != text:
                return "eMidashi"
    return "jMidashi"


def _hc005c_section_parts(code: str, data: bytes, offset: int) -> tuple[list[str], str | None, str | None]:
    if data[offset : offset + 2] == b"\x1f\x41":
        return [], None, "heading_state"
    try:
        value = int(code, 16)
    except ValueError:
        return [], None, None
    return [f'<div style="margin: {value * 3}px">'], "</div>", "margin"


def _hc012e_honbun_div(*, indented: bool = False) -> str:
    if indented:
        return '<div class="honbun" style="margin-left:1.000000em;text-indent:-1.000000em;">'
    return '<div class="honbun" style="margin-left:0.000000em;">'


def _hc00b6_section_parts(code: str, options: HcRenderOptions) -> tuple[list[str], str | None, str | None]:
    try:
        value = int(code, 16)
    except ValueError:
        return [], None, None
    if value in {1, 2, 3, 4, 5, 6, 0x33, 0x35}:
        return ['<div class="midashi">'], "</div>", "midashi"
    if value in {0x0A, 0x2A}:
        return ['<h1 class="indent10">'], "</h1>", "indent10"
    if value == 0x0B:
        return ['<h1 class="indent11">'], "</h1>", "indent11"
    if value == 0x0C:
        return ['<h1 class="indent12">'], "</h1>", "indent12"
    if value == 0x0D:
        return ['<h1 class="indent13">'], "</h1>", "indent13"
    if value in {0x39, 0x3A, 0x3B}:
        return [f'<h1 class="indent{value + 0x1E}">'], "</h1>", f"indent{value + 0x1E}"
    if value in {0x14, 0x15, 0x16, 0x17, 0x2B, 0x3D, 0x3E}:
        return ['<div class="contents">'], "</div>", "contents"
    if value == 0x46:
        cb_src = options.image_sources.get("cb_w") or options.image_sources.get("CB_w".lower()) or "templates/CB_w.png"
        return [
            f'<div class="CB_Title"><img src="{_escape_attr(cb_src)}" class="img_mark4"></div>',
            '<div class="CB_contents"><div class="CB70">',
        ], "</div></div>", "cb"
    if value == 0x49:
        return ['<hr size="1">'], None, "hr"
    if value in {0x0E, 0x10, 0x11, 0x19, 0x1A, 0x40, 0x42, 0x47, 0x48}:
        return ['<div style="margin-left:1em;">'], "</div>", "margin1"
    if value in {0x41, 0x43}:
        return ['<div style="margin-left:2em;">'], "</div>", "margin2"
    if value == 0x4A:
        return ['<div style="margin-left:3.5em;">'], "</div>", "margin35"
    if value in {0x4B, 0x5C}:
        return ['<div style="margin-left:1.5em;">'], "</div>", "margin15"
    if value in {7, 8, 9, 0x12, 0x18, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x30, 0x31, 0x34, 0x36, 0x37, 0x38, 0x3F, 0x45}:
        return [], None, "state_only"
    return [], None, None


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


def _hc0135_section_value(code: str) -> int | None:
    try:
        return int(code, 16)
    except ValueError:
        return None


def _hc0135_section_parts(
    code: str,
    options: HcRenderOptions,
    *,
    previous_pair: bytes = b"",
) -> tuple[list[str], str | None, str | None]:
    value = _hc0135_section_value(code)
    if value is None:
        return [], None, None
    if value == 0x09:
        return ['<div class="content_IND0">'], "</div>", "content_IND0"
    if value == 0x0B:
        return ['<div class="content_IND1">'], "</div>", "content_IND1"
    if value == 0x0C:
        return ['<div class="content_IND2">'], "</div>", "content_IND2"
    if value == 0x1E:
        return ['<p class="contents_yourei">'], "</p>", "contents_yourei"
    if value == 0x17:
        if previous_pair == b"\x1f\x05":
            return ["<br>"], None, "conditional_break"
        return [], None, "state_only"
    if value == 0x26:
        src = _image_source_for_key("exam", options) or _image_source_for_key("exam.png", options)
        if src is not None:
            return [f'<img src="{_escape_attr(src)}" class="img_icon">'], None, "exam_icon"
        return [], None, "missing_exam_icon"
    if value in HC0135_NOOP_SECTION_VALUES:
        return [], None, "state_only"
    return [], None, None


def _hc0135_image_tag(image_key: str, css_class: str, options: HcRenderOptions) -> str:
    src = _image_source_for_key(image_key, options) or _image_source_for_key(f"{image_key}.png", options) or f"{image_key}.png"
    return f'<img src="{_escape_attr(src)}" class="{_escape_attr(css_class)}">'


def _hc0135_private_directive_image(text: str, options: HcRenderOptions) -> str | None:
    directive = _private_directive_text(text).strip().lower()
    if directive.startswith("<") and directive.endswith(">"):
        directive = directive[1:-1].strip()
    image = HC0135_PRIVATE_IMAGE_DIRECTIVES.get(directive)
    if image is None:
        return None
    image_key, css_class = image
    return _hc0135_image_tag(image_key, css_class, options)


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


def _hc012d_image_src(key: str, options: HcRenderOptions) -> str:
    image_name = f"{key}.png"
    return options.image_sources.get(key) or options.image_sources.get(image_name.lower()) or image_name


def _hc012d_yindex_id(options: HcRenderOptions, control_offset: int) -> str:
    return f"{options.entry_start_offset + control_offset:x}"


def _hc012d_yindex_icon_html(yindex_id: str, options: HcRenderOptions) -> str:
    off_src = _hc012d_image_src("youreioff", options)
    on_src = _hc012d_image_src("youreion", options)
    css_class = "yindex_icon_V" if options.vertical else "yindex_icon"
    return (
        f'<a name="icon{_escape_attr(yindex_id)}"></a>'
        f'<a href="#icon{_escape_attr(yindex_id)}">'
        f'<img src="{_escape_attr(off_src)}" id="{_escape_attr(yindex_id)}" '
        f'class="{css_class}" onclick="showIndex(id);" '
        f'data-lv-on-src="{_escape_attr(on_src)}" data-lv-off-src="{_escape_attr(off_src)}">'
        "</a>"
    )


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


def _hc0145_section_is_known_state(code: str) -> bool:
    value = _hc0145_section_value(code)
    if value is None:
        return False
    if value == 9999:
        return True
    return value % 10 == 0


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


def _hc013d_bcd_value(code: str) -> int | None:
    if len(code) != 4:
        return None
    try:
        raw = bytes.fromhex(code)
    except ValueError:
        return None
    value = 0
    for byte in raw:
        high = byte >> 4
        low = byte & 0x0F
        if high > 9 or low > 9:
            return None
        value = value * 100 + high * 10 + low
    return value


def _hc013d_section_parts(code: str) -> tuple[list[str], str | None]:
    value = _hc013d_bcd_value(code)
    if value is None:
        return [], None
    tag_spec = HC013D_MED_SECTION_CLASSES.get(value)
    if tag_spec is not None:
        tag, attrs = tag_spec
        return [f"<{tag}{attrs}>"], f"</{tag}>"
    if value in HC013D_INDENT_SECTION_VALUES:
        return [f'<div class="indent{value:02d}">'], "</div>"
    if value in {2, 3, 5, 7, 9, 12, 13}:
        return [f'<div style="margin-left:{value * 4}px;">'], "</div>"
    return [], None


def _hc0132_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc0132_section_class(value: int, *, next_private_directive: bool = False) -> str | None:
    if value in {*range(2, 12), 20}:
        return "gogi_pos" if next_private_directive else "gogi"
    return {
        12: "sansho",
        13: "kanren_h",
        14: "kanren_y",
        15: "kanren_sansho",
        16: "example_h",
        17: "example_y",
        18: "kaisetsu",
        19: "kaisetsu",
    }.get(value)


def _hc0157_section_value(code: str) -> int | None:
    try:
        if code.isdigit():
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


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


def _hc0096_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        return int(code, 10) if code.isdecimal() else int(code, 16)
    except ValueError:
        return None


def _hc0095_page_kind(data: bytes) -> int:
    """Infer HC0095's first lineinfo axis from the entry-kind gaiji marker.

    The DLL emits ``lineinfo%d-%d`` wrappers.  In GKSAHOU the first axis is
    selected by the page-kind marker immediately following the entry title:
    B138 ancillary pages, B139 kana index, B13A category pages, B13B ordinary
    entry pages, and B13C keigo-column pages.
    """

    i = 0
    while i + 1 < len(data):
        if data[i] == 0x1F:
            arg_len = control_arg_length(data, i)
            i += 2 + arg_len
            continue
        if 0xA1 <= data[i] <= 0xFE:
            key = f"{data[i]:02x}{data[i + 1]:02x}"
            kind = HC0095_PAGE_KIND_MARKERS.get(key)
            if kind is not None:
                return kind
            i += 2
            continue
        i += 1
    return 0


def _hc009f_oriented_prefix(options: HcRenderOptions) -> str:
    return "v" if options.vertical else "h"


def _hc009f_image_src(stem: str, options: HcRenderOptions) -> str | None:
    return options.image_sources.get(stem.casefold()) or options.image_sources.get(f"{stem.casefold()}.png")


def _hc009f_marker_image(stem: str, css_class: str, options: HcRenderOptions) -> str | None:
    src = _hc009f_image_src(stem, options)
    if src is None:
        return None
    return f'<img src="{_escape_attr(src)}" class="{css_class}">'


def _hc009f_season_from_jis(code: str) -> tuple[str, str] | None:
    return HC009F_SEASON_MARKERS.get(code)


def _hc009f_category_from_jis(code: str) -> str | None:
    return HC009F_CATEGORY_MARKERS.get(code)


def _hc009f_honbun_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc02c0_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return [], None, "midashi_state"
    if value == 20:
        return ['<div class="honbun" style="margin-left:1em;text-align:right;">'], "</div>", "right_honbun"
    if value == 4:
        return ['<div class="honbun" style="margin-left:1em;">'], "</div>", "description"
    if value == 3:
        return ['<div class="honbun" style="margin-left:0.5em;">'], "</div>", "example"
    if value in {6, 7}:
        return [], None, "season_or_category_marker"
    return [f'<div class="honbun" style="margin-left:{value}em;">'], "</div>", f"honbun_{value}"


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
        # GEN year-family renderers use the following 1f41/1f61 span for the visible heading.
        return [], None
    if code == "000c":
        return ['<div class="footer">'], "</div>"
    return [_hc_gen_year_honbun_div()], "</div>"


def _hc00c4_section_parts(
    code: str,
    *,
    next_key: str | None,
) -> tuple[list[str], str | None, str | None]:
    if code == "0001":
        return ['<div class="midashi"><span class="zenkakuMidashi">'], None, "midashi"
    if code == "0002":
        if next_key in HC00C4_NUMBER_MARKERS:
            return ['<div class="block"><div class="honbun_number">'], "</div></div>", "honbun_number"
        return ['<div class="honbun">'], "</div>", "honbun"
    image_name = HC00C4_SECTION_ICON_IMAGES.get(code)
    if image_name is not None:
        return [
            '<div class="honbun_icon">',
            f'<img src="{_escape_attr(image_name)}" class="icon">',
        ], "</div>", "honbun_icon"
    image_name = HC00C4_INLINE_SECTION_IMAGES.get(code)
    if image_name is not None:
        return [f'<img src="{_escape_attr(image_name)}" class="icon_s">'], None, "inline_icon"
    if code == "000f":
        return ['<font class="font_down">'], None, "font_down"
    if code == "0010":
        return ["</font>"], None, "font_down_close"
    if code in HC00C4_NOOP_SECTION_CODES:
        return [], None, "state_only"
    return [], None, None


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


def _hc02c8_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        if all(ch in "0123456789" for ch in code):
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc02c8_section_parts(code: str) -> tuple[list[str], str | None, str | None, int | None]:
    value = _hc02c8_section_value(code)
    if value is None:
        return [], None, None, None
    if value in HC02C8_NOOP_SECTION_VALUES:
        return [], None, "noop", value
    if value == 3:
        return ['<div class="indent3">　 '], "</div>", "indent3", value
    if value == 4:
        return ['<div class="midashi_2nd">'], "</div>", "midashi_2nd", value
    if value in {5, 6, 30, 31}:
        return [f'<div class="indent{value}">'], "</div>", f"indent{value}", value
    if value == 7:
        return ['<div class="indent7">　 '], "</div>", "indent7", value
    if value == 12:
        return ['<div class="header" >'], "</div>", "header", value
    if value == 33:
        return ['<hr>', '<div class="indent33">'], "</div>", "indent33", value
    if value == 50:
        return ["<table>"], None, "table_open", value
    if value == 51:
        return ["</table>"], None, "table_close", value
    if value == 60:
        return ["<tr>"], None, "row_open", value
    if value == 61:
        return ["</tr>"], None, "row_close", value
    if value == 70:
        return ["<td>"], None, "cell_open", value
    if value == 71:
        return ["</td>"], None, "cell_close", value
    return ['<div class="contents">'], "</div>", "contents", value


def _hc0147_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        if all(ch in "0123456789" for ch in code):
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc0147_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc0147_section_value(code)
    if value is None:
        return [], None, None
    if value == 9999:
        return [], None, "close"
    if value == 1:
        return ['<div class="midashi">'], "</div>", "midashi"
    if value == 3 or value == 200:
        return ['<div class="contents">'], "</div>", "contents"
    if value == 5:
        return ['<div class="bunken_title">'], "</div>", "bunken_title"
    if value == 6 or value % 10 == 6:
        return ['<div class="bunken_contents">'], "</div>", "bunken_contents"
    if value == 7:
        return ['<div class="cyosha">'], "</div>", "cyosha"
    if value == 8 or value == 100:
        return ['<div class="contents_title">'], "</div>", "contents_title"
    return ['<div class="contents_body">'], "</div>", "contents_body"


def _hc0094_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        if all(ch in "0123456789" for ch in code):
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc0094_section_parts(code: str) -> tuple[list[str], str | None, str | None]:
    value = _hc0094_section_value(code)
    if value is None:
        return [], None, None
    if value == 1:
        return ['<div class="midashi">'], "</div>", "midashi"
    if value == 9:
        return ['<div class="lineinfo">'], "</div>", "lineinfo"
    if value == 12:
        return ['<div class="footer">'], "</div>", "footer"
    return ['<div class="contents_body">'], "</div>", "contents_body"


def _hc0137_section_value(code: str) -> int | None:
    if not code:
        return None
    try:
        if all(ch in "0123456789" for ch in code):
            return int(code, 10)
        return int(code, 16)
    except ValueError:
        return None


def _hc0137_section_parts(code: str) -> tuple[list[str], str | None, str | None, str | None]:
    """Return the recovered HC0137 section wrapper subset."""

    value = _hc0137_section_value(code)
    if value is None:
        return [], None, None, None
    if value in {1, 2, 3, 4}:
        return ['<div class="midashi">'], "</div>", "midashi", "</div>"
    if value == 5:
        return ['<font class="font_midashi_sub">'], "</font>", "midashi_sub", "</font>"
    if value in {6, 7}:
        return ['<div style="display:none">'], "</div>", "hidden", "</div>"
    if value == 8:
        return ["［"], "］", "bracket_square", "］"
    if value == 9:
        return ["（"], "）", "bracket_round", "）"
    if value in {10, 18}:
        return [f'<div style="margin-left: {value:f}em" class="honbunB">'], "</div>", "honbunB", "</div>"
    if 11 <= value <= 20:
        return [f'<div style="margin-left: {value:f}em" class="honbun">'], "</div>", "honbun", "</div>"
    if value == 21:
        return ["〈"], "〉", "bracket_angle", "〉"
    if value == 30:
        return ['<div class="honbun">'], "</div>", "honbun", None
    return [f'<div class="honbun" style="margin-left:{value:f}em;">'], "</div>", "honbun_fallback", "</div>"


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


def _copy_package_directory_assets(source: DictionarySource, dirname: str, out_dir: Path) -> int:
    directory = _resolve_package_relative_dir(source, dirname)
    if directory is None:
        return 0
    copied = 0
    for path in sorted((item for item in directory.rglob("*") if item.is_file()), key=lambda item: item.relative_to(directory).as_posix().casefold()):
        rel = Path(dirname) / path.relative_to(directory)
        target_path = out_dir / rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        source_stat = path.stat()
        if not target_path.exists():
            shutil.copy2(path, target_path)
            copied += 1
        else:
            target_stat = target_path.stat()
            if source_stat.st_size != target_stat.st_size or source_stat.st_mtime_ns != target_stat.st_mtime_ns:
                shutil.copy2(path, target_path)
                copied += 1
    return copied


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


def _load_hc00a4_templates(source: DictionarySource) -> dict[str, str]:
    templates: dict[str, str] = {}
    directory = _resolve_package_relative_dir(source, "HTMLs") or _resolve_package_relative_dir(source, "htmls")
    if directory is None:
        return templates
    for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in {".htm", ".html"}:
            continue
        text = path.read_bytes().decode("cp932", errors="replace")
        fragment = _html_body_inner(text)
        templates[path.name.casefold()] = fragment
        templates[path.stem.casefold()] = fragment
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


def _add_hc00a4_image_sources(source: DictionarySource, dict_out: Path, output_sources: dict[str, str]) -> int:
    copied = 0
    directory = _resolve_package_relative_dir(source, "images")
    if directory is None:
        return copied
    for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        rel = f"images/{path.name}"
        output_sources.setdefault(path.stem.casefold(), rel)
        output_sources.setdefault(path.name.casefold(), rel)
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
    copied += _copy_package_directory_assets(source, "Templates", dict_out)
    copied += _add_hc_bitmap_gaiji_sources(source, dict_out, output_sources)

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
    if renderer is not None and renderer.code.upper() == "00A4":
        html_templates = _load_hc00a4_templates(source)
        copied += _add_hc00a4_image_sources(source, dict_out, output_sources)
    return output_sources, html_templates, stylesheet_output, copied


def _write_hc_html_header(html_out: Any, *, stylesheet: str | None, vertical: bool) -> None:
    body_class = "v" if vertical else "h"
    html_out.write("<!doctype html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n")
    if stylesheet:
        html_out.write(f'<link rel="stylesheet" href="{_escape_attr(stylesheet)}">\n')
    html_out.write(f"<script>\n{HC_RENDER_BASE_SCRIPT}\n</script>\n")
    html_out.write(f"</head>\n<body class=\"{body_class}\">\n")


def _write_hc_html_footer(html_out: Any) -> None:
    html_out.write("</body>\n</html>\n")


def _relative_browser_path(path: Path, base: Path) -> str:
    try:
        relative = path.resolve().relative_to(base.resolve())
    except ValueError:
        relative = Path(os.path.relpath(path, base))
    return relative.as_posix()


def _decimal_data_id(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None


def _split_lved_dataid(value: str) -> tuple[int | None, str | None]:
    data_id_text, separator, anchor = value.partition("#")
    data_id = _decimal_data_id(data_id_text)
    if not separator or not anchor:
        return data_id, None
    return data_id, anchor


def _rewrite_lved_addr_hrefs(fragment: str) -> str:
    def replace_addr_href(match: re.Match[str]) -> str:
        prefix = match.group(1)
        quote = match.group(2)
        block = html.unescape(match.group(3))
        offset = html.unescape(match.group(4))
        suffix_quote = match.group(5)
        if not block or not offset:
            return match.group(0)
        original = f"lved.addr{block}:{offset}"
        target = f"lvaddr://{block}/{offset}"
        return (
            f'{prefix}{quote}{_escape_attr(target)}{suffix_quote} '
            f'data-lv-original-href="{_escape_attr(original)}" '
            f'data-lv-address-block="{_escape_attr(block)}" '
            f'data-lv-address-offset="{_escape_attr(offset)}"'
        )

    return re.sub(
        r'(\bhref\s*=\s*)(["\'])lved\.addr([0-9A-Za-z]+):([0-9A-Za-z]+)(["\'])',
        replace_addr_href,
        fragment,
        flags=re.IGNORECASE,
    )


def _ziptomedia_href_map(rendererdb_summary: dict[str, Any], dict_out: Path) -> dict[str, str]:
    refs: dict[str, str] = {}
    files = rendererdb_summary.get("ziptomedia_written_files")
    if not isinstance(files, list):
        return refs
    for row in files:
        if not isinstance(row, dict):
            continue
        reference = row.get("reference")
        path_value = row.get("path")
        if not isinstance(reference, str) or not isinstance(path_value, str):
            continue
        output_path = Path(path_value)
        if not output_path.is_file():
            continue
        refs[reference] = _relative_browser_path(output_path, dict_out)
    return refs


class _RendererFragmentNormalizer(HTMLParser):
    """Normalize one renderer-sidecar fragment for standalone browser output."""

    TRACKED_TAGS = frozenset(
        {
            "a",
            "b",
            "dd",
            "div",
            "dl",
            "dt",
            "em",
            "font",
            "i",
            "li",
            "ol",
            "p",
            "rb",
            "rt",
            "ruby",
            "small",
            "span",
            "strong",
            "sub",
            "sup",
            "table",
            "tbody",
            "td",
            "tfoot",
            "th",
            "thead",
            "tr",
            "u",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text() or f"<{tag}>")
        lower = tag.lower()
        if lower in self.TRACKED_TAGS:
            self.stack.append(lower)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or f"<{tag}/>"
        lower = tag.lower()
        if lower == "a":
            self.parts.append(re.sub(r"\s*/\s*>$", ">", raw) + "</a>")
            return
        self.parts.append(raw)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower not in self.TRACKED_TAGS:
            self.parts.append(f"</{tag}>")
            return
        if lower not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            if current != lower:
                self.parts.append(f"</{current}>")
                continue
            self.parts.append(f"</{lower}>")
            break

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def normalized(self) -> str:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts)


def _normalize_renderer_fragment_structure(fragment: str) -> str:
    normalizer = _RendererFragmentNormalizer()
    try:
        normalizer.feed(fragment)
        normalizer.close()
    except Exception:
        return fragment
    return normalizer.normalized()


def _normalize_exact_body_fragment_html(fragment: str) -> str:
    """Normalize renderer-sidecar fragments enough to keep entry DOMs isolated."""

    normalized = re.sub(r"</sapn\s*>", "</span>", fragment, flags=re.IGNORECASE)
    return _normalize_renderer_fragment_structure(normalized)


def _rewrite_exact_body_asset_refs(
    fragment: str,
    dict_out: Path,
    *,
    ziptomedia_hrefs: dict[str, str] | None = None,
    data_ids: set[int] | None = None,
) -> str:
    """Rewrite bare renderer-sidecar asset names to copied package assets."""

    def replace_src(match: re.Match[str]) -> str:
        quote = match.group(1)
        src = html.unescape(match.group(2))
        lower = src.lower()
        if (
            ":" in src
            or src.startswith("/")
            or "\\" in src
            or "/" in src
            or lower.startswith(("data:", "javascript:", "mailto:"))
        ):
            return match.group(0)
        if (dict_out / src).is_file():
            return match.group(0)
        for dirname in ("Templates", "HANREI/img", "HTMLs", "images", "img"):
            candidate = dict_out / dirname / src
            if candidate.is_file():
                return f"src={quote}{_escape_attr(str(Path(dirname) / src))}{quote}"
        return match.group(0)

    def replace_ziptomedia_href(match: re.Match[str]) -> str:
        prefix = match.group(1)
        quote = match.group(2)
        reference = html.unescape(match.group(3))
        suffix_quote = match.group(4)
        mapped = (ziptomedia_hrefs or {}).get(reference)
        original = f"lved.ziptomedia:{reference}"
        if mapped is None:
            mapped = f"#lv-ziptomedia-{_safe_href_token(reference)}"
        return (
            f'{prefix}{quote}{_escape_attr(mapped)}{suffix_quote} '
            f'data-lv-original-href="{_escape_attr(original)}" '
            f'data-lv-ziptomedia="{_escape_attr(reference)}"'
        )

    def replace_dataid_href(match: re.Match[str]) -> str:
        prefix = match.group(1)
        quote = match.group(2)
        raw_id = html.unescape(match.group(3))
        suffix_quote = match.group(4)
        data_id, anchor = _split_lved_dataid(raw_id)
        if data_id is None:
            return match.group(0)
        original = f"lved.dataid:{raw_id}"
        target = f"#{anchor}" if anchor else f"#lv-dataid-{data_id}"
        missing = "" if data_ids is None or data_id in data_ids else ' data-lv-missing-target="true"'
        anchor_attr = f' data-lv-target-anchor="{_escape_attr(anchor)}"' if anchor else ""
        return (
            f'{prefix}{quote}{_escape_attr(target)}{suffix_quote} '
            f'data-lv-original-href="{_escape_attr(original)}" '
            f'data-lv-dataid="{data_id}"{anchor_attr}{missing}'
        )

    def replace_image_href(match: re.Match[str]) -> str:
        prefix = match.group(1)
        quote = match.group(2)
        scheme = match.group(3)
        reference = html.unescape(match.group(4))
        suffix_quote = match.group(5)
        if not reference:
            return match.group(0)
        original = f"lved.{scheme}:{reference}"
        candidates = [
            dict_out / reference,
            dict_out / "media" / reference,
            dict_out / "rendererdb" / dict_out.name / "media" / reference,
        ]
        mapped = next((_relative_browser_path(path, dict_out) for path in candidates if path.is_file()), reference)
        return (
            f'{prefix}{quote}{_escape_attr(mapped)}{suffix_quote} '
            f'data-lv-original-href="{_escape_attr(original)}" '
            f'data-lv-image-ref="{_escape_attr(reference)}"'
        )

    rewritten = _normalize_exact_body_fragment_html(fragment)
    rewritten = re.sub(r'src=(["\'])([^"\']+)\1', replace_src, rewritten)
    rewritten = re.sub(
        r'(\bhref\s*=\s*)(["\'])lved\.ziptomedia:([^"\']+)(["\'])',
        replace_ziptomedia_href,
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = re.sub(
        r'(\bhref\s*=\s*)(["\'])lved\.dataid:([^"\']+)(["\'])',
        replace_dataid_href,
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = _rewrite_lved_addr_hrefs(rewritten)
    rewritten = re.sub(
        r'(\bhref\s*=\s*)(["\'])lved\.(imag|image):([^"\']+)(["\'])',
        replace_image_href,
        rewritten,
        flags=re.IGNORECASE,
    )
    return rewritten


def _write_exact_hc_entries_html_from_rendererdb(
    rendererdb_summary: dict[str, Any],
    dict_out: Path,
    *,
    stylesheet: str | None,
    vertical: bool,
) -> Path | None:
    entries_path_value = rendererdb_summary.get("entries_path")
    if not entries_path_value:
        return None
    entries_path = Path(str(entries_path_value))
    if not entries_path.is_file():
        return None
    records: list[dict[str, Any]] = []
    with entries_path.open("r", encoding="utf-8") as jsonl:
        for line in jsonl:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                records.append(row)
    data_ids = {
        data_id
        for data_id in (_decimal_data_id(record.get("data_id")) for record in records)
        if data_id is not None
    }
    ziptomedia_hrefs = _ziptomedia_href_map(rendererdb_summary, dict_out)
    html_path = dict_out / "hc_entries.html"
    with html_path.open("w", encoding="utf-8") as html_out:
        _write_hc_html_header(html_out, stylesheet=stylesheet, vertical=vertical)
        for record in records:
            data_id = _decimal_data_id(record.get("data_id"))
            label = html.escape(str(record.get("data_id") or record.get("entry_index") or ""))
            fragment = record.get("html")
            if isinstance(fragment, str) and fragment:
                body_html = _rewrite_exact_body_asset_refs(
                    fragment,
                    dict_out,
                    ziptomedia_hrefs=ziptomedia_hrefs,
                    data_ids=data_ids,
                )
            else:
                body_html = f"<pre>{html.escape(str(record.get('plain') or ''))}</pre>"
            entry_attrs = 'class="rendererdb-entry exact-rendererdb-entry"'
            if data_id is not None:
                entry_attrs += f' id="lv-dataid-{data_id}" data-lv-dataid="{data_id}"'
            html_out.write(
                f"<!-- {record.get('dict_id', '')} {label} rendererdb -->\n"
                f"<div {entry_attrs}>{body_html}</div>\n"
            )
        _write_hc_html_footer(html_out)
    return html_path


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


def _bmp_bytes_from_ga16_glyph(glyph: bytes, width: int, height: int) -> bytes:
    row_in = (width + 7) // 8
    row_out = ((width * 3 + 3) // 4) * 4
    pixels = bytearray()
    for y in range(height - 1, -1, -1):
        row = bytearray()
        base = y * row_in
        for x in range(width):
            byte = glyph[base + x // 8]
            bit = (byte >> (7 - (x % 8))) & 1
            row.extend(b"\x00\x00\x00" if bit else b"\xff\xff\xff")
        row.extend(b"\x00" * (row_out - len(row)))
        pixels.extend(row)
    header_size = 14 + 40
    file_size = header_size + len(pixels)
    file_header = b"BM" + file_size.to_bytes(4, "little") + b"\x00\x00\x00\x00" + header_size.to_bytes(4, "little")
    dib = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little", signed=True)
        + height.to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + len(pixels).to_bytes(4, "little")
        + (2835).to_bytes(4, "little", signed=True)
        + (2835).to_bytes(4, "little", signed=True)
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    return file_header + dib + bytes(pixels)


def _discover_hc_ga16_resources(source: DictionarySource) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_package_roots(source.idx):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or not is_bitmap_gaiji_resource_name(path):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return tuple(paths)


def _first_uni_resource_near(path: Path):
    for candidate in sorted(path.parent.iterdir(), key=lambda item: item.name.casefold()):
        if not candidate.is_file() or candidate.suffix.lower() != ".uni":
            continue
        parsed = parse_uni_resource(candidate)
        if parsed is not None:
            return parsed
    return None


def _add_hc_bitmap_gaiji_sources(source: DictionarySource, dict_out: Path, output_sources: dict[str, str]) -> int:
    copied = 0
    for path in _discover_hc_ga16_resources(source):
        resource = parse_ga16_resource(path)
        if resource is None:
            continue
        data = path.read_bytes()
        uni_resource = _first_uni_resource_near(path)
        target_dir = dict_out / "gaiji" / path.name
        target_dir.mkdir(parents=True, exist_ok=True)
        for index in range(resource.count):
            glyph = resource.glyph_for_index(data, index)
            if glyph is None or not any(glyph):
                continue
            code, code_source = ga16_preferred_code_for_index(resource, index, uni_resource)
            key = code.lower()
            if key in output_sources:
                continue
            rel = f"gaiji/{path.name}/{code.upper()}_{code_source}.bmp"
            (dict_out / rel).write_bytes(_bmp_bytes_from_ga16_glyph(glyph, resource.width, resource.height))
            output_sources[key] = rel
            copied += 1
    return copied


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
    hc0157_marker_stack: list[tuple[str, str, int]] = []
    hc0157_link_scoped_close_markers: Counter[str] = Counter()
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
    hc0094_section_close: str | None = None
    hc0094_color_div_close: str | None = None
    hc0093_section_close: str | None = None
    hc0093_current_section: int | None = 0
    hc0093_contents_body_open = False
    hc0095_page_kind = _hc0095_page_kind(data) if _renderer_code(options) == "0095" else 0
    hc0095_section_close: str | None = None
    hc0095_current_section: int | None = 0
    hc0095_contents_body_open = False
    hc009f_section_close: str | None = None
    hc009f_current_section: int | None = None
    hc009f_midashi_open = False
    hc009f_season: str | None = None
    hc009f_skip_next_jis = False
    hc0096_section_close: str | None = None
    hc0096_current_section: int | None = 0
    hc0096_contents_body_open = False
    hc0091_midashi_open = False
    hc0091_contents_open = False
    hc0090_section_close: str | None = None
    hc0090_current_section: int | None = 0
    hc0090_contents_body_open = False
    hc0090_yourei_open = False
    hc0135_section_close: str | None = None
    hc0135_current_section: int | None = None
    hc0135_midashi_open = False
    hc0135_contents_body_open = False
    hc0135_honbun_user_open = False
    hc014f_current_section: int | None = None
    hc014f_midashi_open = False
    hc014f_contents_open = False
    hc014f_decoration_stack: list[int] = []
    hc009d_section_close: str | None = None
    hc009d_current_section_value: int | None = None
    hc009d_table_header_open = False
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
    hc0146_section_close: str | None = None
    hc0146_column_frame_close: str | None = None
    hc0157_section_close: str | None = None
    hc0157_group_close: str | None = None
    hc0157_group_open_part_count: int | None = None
    hc0142_honbun_open = False
    hc0142_current_section: str | None = None
    hc0142_marker_stack: list[tuple[str, str]] = []
    hc02bc_section_open = False
    hc02be_section_open = False
    hc02be_section_close: str | None = None
    hc013d_clickmenu_close: str | None = None
    hc013d_clickmenu_field_close: str | None = None
    hc013d_gray_table_open = False
    hc013d_pc_table_open = False
    hc0190_sections: dict[int, str] = {}
    hc0190_template_key: str | None = None
    hc00b6_section_close: str | None = None
    hc012d_section_close: str | None = None
    hc012d_pending_honbun_user = False
    hc012d_pending_yindex_field_id: str | None = None
    hc012d_yindex_field_open = False
    hc013d_section_close: str | None = None
    hc0141_section_close: str | None = None
    hc0144_section_close: str | None = None
    hc0145_section_close: str | None = None
    hc0158_section_close: str | None = None
    hc_hkdksr_medical_section_close: str | None = None
    hc_hkdksr_medical_table_open = False
    hc008c_section_close: str | None = None
    hc008c_heading_phase = _renderer_code(options) == "008C"
    hc008c_contents_body_open = False
    hc03e8_section_close: str | None = None
    hc00a6_section_close: str | None = None
    hc00a6_ruby_readings: list[str] = []
    hc00a4_section_close: str | None = None
    hc00a4_midashi_open = False
    hc00a4_ruby_open = False
    hc00a9_section_close: str | None = None
    hc00a9_current_section: int | None = None
    hc00a9_heading_phase = _renderer_code(options) == "00A9"
    hc00a9_midashi_open = False
    hc00ab_section_close: str | None = None
    hc00ab_current_section: int | None = None
    hc00ab_midashi_open = False
    hc00bb_section_close: str | None = None
    hc00bb_current_section: int | None = None
    hc00bb_midashi_open = False
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
    hc00c4_section_close: str | None = None
    hc00c4_midashi_open = False
    hc00c4_font_down_open = False
    hc02c2_section_open = False
    hc02c2_moji_down_open = False
    hc02c2_current_section: str | None = None
    hc02c8_section_close: str | None = None
    hc02c8_current_value: int | None = None
    hc0147_section_close: str | None = None
    hc0147_bunken_open = False
    hc0137_section_close: str | None = None
    hc0137_line_close: str | None = None
    hc0065_midashi_open = False
    hc0065_body_open = False
    hc0063_section_close: str | None = None
    hc0063_heading_phase = _renderer_code(options) == "0063"
    hc0063_contents_body_open = False
    hc0048_section_close: str | None = None
    hc0048_midashi_open = False
    hc0048_honbun_open = False
    hc0048_media_div_open = False
    hc004d_midashi_open = False
    hc004d_honbun_open = False
    hc004d_heading_phase = _renderer_code(options) == "004D"
    hc0073_section_close: str | None = None
    hc0073_midashi_open = False
    hc0073_contents_open = False
    hc0076_section_open = False
    hc0076_midashi_open = False
    hc0076_contents_body_open = False
    hc0076_heading_phase = _renderer_code(options) == "0076"
    hc007d_section_open = False
    hc007d_midashi_open = False
    hc007d_contents_body_open = False
    hc007d_heading_phase = _renderer_code(options) == "007D"
    hc007d_pending_contents_transition = False
    hc008f_section_close: str | None = None
    hc008f_jmidashi_open = False
    hc008f_emidashi_japanese_open = False
    hc008f_halfwidth_mode = False
    hc008f_hankaku_open = False
    hc00c7_section_close: str | None = None
    hc00c7_current_section: int | None = None
    hc00c7_contents_body_open = False
    hc00ac_section_close: str | None = None
    hc00aa_section_close: str | None = None
    hc00a3_section_close: str | None = None
    hc00a3_previous_section: int | None = None
    hc00a3_answer_index = 1
    hc00a3_quiz_group_active = False
    hc00c5_section_close: str | None = None
    hc00c5_midashi_open = False
    hc00c5_honbun_user_open = False
    hc00ad_section_close: str | None = None
    hc00ad_midashi_open = False
    hc0020_section_close: str | None = None
    hc0020_midashi_open = False
    hc0020_contents_open = False
    hc0020_div_215a_open = False
    hc0020_definition_open = False
    hc0067_section_close: str | None = None
    hc0067_midashi_open = False
    hc0067_contents_open = False
    hc0068_section_close: str | None = None
    hc0068_midashi_open = False
    hc0068_contents_open = False
    hc0069_section_close: str | None = None
    hc0069_midashi_open = False
    hc0069_contents_open = False
    hc008b_section_close: str | None = None
    hc008b_midashi_open = False
    hc008b_contents_open = False
    hc005c_section_close: str | None = None
    hc005c_heading_open = False
    hc005c_heading_class: str | None = None
    hc005c_emidashi_japanese_open = False
    hc0092_lineinfo_open = False
    hc0092_current_section: int | None = None
    hc0092_contents_open = False
    hc0132_honbun_open = False
    hc0132_section_close: str | None = None
    hc013a_honbun2_open = False
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

            private_ctx = _active_private_context(contexts)
            if private_ctx is not None and op not in PRIVATE_END_OPS:
                if _renderer_code(options) == "02C8" and op == 0x09:
                    stats["hc02c8_private_section_controls"] += 1
                if _consume_private_style_control(private_ctx, op, options):
                    stats["private_style_controls"] += 1
                else:
                    stats["private_suppressed_controls"] += 1
                i += 2 + arg_len
                continue

            if op == 0x09:
                stats["section_markers"] += 1
                if payload:
                    code = payload.hex()
                    if _renderer_code(options) == "013A" and hc013a_honbun2_open:
                        _current_parts(root_parts, contexts).append("</strong></div>")
                        hc013a_honbun2_open = False
                        stats["hc013a_honbun2_closures"] += 1
                    if _renderer_code(options) == "0190":
                        _hc0190_close_section(contexts, hc0190_sections, stats)
                        section = _packed_bcd_to_int(payload)
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
                    if _renderer_code(options) == "0063":
                        next_control = data[i + 2 + arg_len : i + 4 + arg_len]
                        if next_control == b"\x1f\x41":
                            stats["hc0063_heading_section_state"] += 1
                            i += 2 + arg_len
                            continue
                        if hc0063_section_close is not None:
                            root.append(hc0063_section_close)
                            hc0063_section_close = None
                        value = _hc02c0_section_value(code)
                        if value is not None:
                            root.append(f'<div style="margin-left: {value * 3}px">')
                            hc0063_section_close = "</div>"
                            stats["hc0063_margin_sections"] += 1
                        else:
                            stats["hc0063_unmapped_sections"] += 1
                            gaps.add(f"hc0063_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0073":
                        if hc0073_section_close is not None:
                            root.append(hc0073_section_close)
                            hc0073_section_close = None
                            stats["hc0073_section_closures"] += 1
                        value = _hc02c0_section_value(code)
                        if value is not None:
                            root.append(f'<div style="margin-left:{value * 3}px;">')
                            hc0073_section_close = "</div>"
                            stats["hc0073_margin_sections"] += 1
                        else:
                            stats["hc0073_unmapped_sections"] += 1
                            gaps.add(f"hc0073_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "007D":
                        next_control = data[i + 2 + arg_len : i + 4 + arg_len]
                        if next_control == b"\x1f\x41":
                            stats["hc007d_heading_section_state"] += 1
                            i += 2 + arg_len
                            continue
                        if hc007d_section_open:
                            root.append("</div>")
                            hc007d_section_open = False
                            stats["hc007d_section_closures"] += 1
                        value = _hc02c0_section_value(code)
                        if value is not None:
                            root.append(f'<div style="margin-left:{value * 3}px;">')
                            hc007d_section_open = True
                            stats["hc007d_margin_sections"] += 1
                        else:
                            stats["hc007d_unmapped_sections"] += 1
                            gaps.add(f"hc007d_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "008F":
                        if hc008f_section_close is not None:
                            if hc008f_hankaku_open:
                                root.append("</span>")
                                hc008f_hankaku_open = False
                                halfwidth_depth = max(0, halfwidth_depth - 1)
                                stats["hc008f_hankaku_forced_closures"] += 1
                            root.append(hc008f_section_close)
                            hc008f_section_close = None
                            stats["hc008f_section_closures"] += 1
                        value = _hc02c0_section_value(code)
                        if value is not None:
                            root.append(f'<div style="margin: {value * 10}px">')
                            hc008f_section_close = "</div>"
                            stats["hc008f_margin_sections"] += 1
                        else:
                            stats["hc008f_unmapped_sections"] += 1
                            gaps.add(f"hc008f_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0092":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            gaps.add(f"hc0092_unmapped_section_{code}")
                            stats["hc0092_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        if hc0092_lineinfo_open:
                            root.append("</div>")
                            hc0092_lineinfo_open = False
                            stats["hc0092_lineinfo_closures"] += 1
                        if value == hc0092_current_section:
                            stats["hc0092_repeated_sections"] += 1
                        root.append(f'<div class="lineinfo{value}">')
                        hc0092_lineinfo_open = True
                        hc0092_current_section = value
                        stats["hc0092_lineinfo_sections"] += 1
                        stats[f"hc0092_lineinfo_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0093":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            gaps.add(f"hc0093_unmapped_section_{code}")
                            stats["hc0093_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        if (
                            hc0093_current_section
                            and not (value == hc0093_current_section == 2)
                            and hc0093_section_close is not None
                        ):
                            root.append(hc0093_section_close)
                            hc0093_section_close = None
                            stats["hc0093_lineinfo_closures"] += 1
                        if value != hc0093_current_section:
                            if hc0093_current_section == 5:
                                root.append("</div>")
                                stats["hc0093_yourei_closures"] += 1
                            elif hc0093_current_section == 1 and not hc0093_contents_body_open:
                                root.append('<div class="contents_body">')
                                hc0093_contents_body_open = True
                                stats["hc0093_contents_body_blocks"] += 1
                            if value == 5:
                                root.append('<div class="youreihan">')
                                stats["hc0093_youreihan_sections"] += 1
                        if value == hc0093_current_section == 2:
                            stats["hc0093_repeated_lineinfo2_suppressed"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<div class="lineinfo{value}">')
                        hc0093_section_close = "</div>"
                        hc0093_current_section = value
                        stats["hc0093_lineinfo_sections"] += 1
                        stats[f"hc0093_lineinfo_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0095":
                        value = _hc0096_section_value(code)
                        if value is None:
                            gaps.add(f"hc0095_unmapped_section_{code}")
                            stats["hc0095_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        if (
                            hc0095_current_section
                            and value != hc0095_current_section
                            and hc0095_section_close is not None
                        ):
                            root.append(hc0095_section_close)
                            hc0095_section_close = None
                            stats["hc0095_lineinfo_closures"] += 1
                        if hc0095_current_section == 12 and value != 12 and not hc0095_contents_body_open:
                            root.append('<div class="contents_body">')
                            hc0095_contents_body_open = True
                            stats["hc0095_contents_body_blocks"] += 1
                        if value == hc0095_current_section:
                            stats["hc0095_repeated_sections_suppressed"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<div class="lineinfo{hc0095_page_kind}-{value}">')
                        hc0095_section_close = "</div>"
                        hc0095_current_section = value
                        stats["hc0095_lineinfo_sections"] += 1
                        stats[f"hc0095_lineinfo{hc0095_page_kind}_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "009F":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            gaps.add(f"hc009f_unmapped_section_{code}")
                            stats["hc009f_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        next_word = data[i + 2 + arg_len : i + 4 + arg_len].hex()
                        if hc009f_section_close is not None:
                            root.append(hc009f_section_close)
                            hc009f_section_close = None
                            stats["hc009f_section_closures"] += 1
                        if value == 6:
                            season = _hc009f_season_from_jis(next_word)
                            if season is not None:
                                hc009f_season, season_color = season
                                marker = _hc009f_marker_image(
                                    f"{_hc009f_oriented_prefix(options)}{hc009f_season}",
                                    "img_mark",
                                    options,
                                )
                                if marker is not None:
                                    root.append(marker)
                                    stats["hc009f_season_markers"] += 1
                                root.append(f'<div class="midashi" style="background-color:{season_color};">')
                                hc009f_section_close = "</div>"
                                stats["hc009f_season_midashi_blocks"] += 1
                                stats[f"hc009f_season_{hc009f_season}"] += 1
                                hc009f_skip_next_jis = True
                            elif next_word.startswith("1f"):
                                stats["hc009f_season_markers_without_label"] += 1
                            else:
                                gaps.add(f"hc009f_unmapped_season_marker_{next_word}")
                                stats["hc009f_unmapped_season_markers"] += 1
                            hc009f_current_section = value
                            i += 2 + arg_len
                            continue
                        if value == 7:
                            category = _hc009f_category_from_jis(next_word)
                            if category is not None and hc009f_season is not None:
                                marker = _hc009f_marker_image(
                                    f"{_hc009f_oriented_prefix(options)}{hc009f_season}{category}",
                                    "img_mark",
                                    options,
                                )
                                if marker is not None:
                                    root.append(marker)
                                    stats["hc009f_category_markers"] += 1
                                stats[f"hc009f_category_{category}"] += 1
                                hc009f_skip_next_jis = True
                            elif category is not None:
                                stats["hc009f_category_markers_without_season"] += 1
                                hc009f_skip_next_jis = True
                            elif next_word.startswith("1f"):
                                stats["hc009f_category_markers_without_label"] += 1
                            else:
                                gaps.add(f"hc009f_unmapped_category_marker_{next_word}")
                                stats["hc009f_unmapped_category_markers"] += 1
                            hc009f_current_section = value
                            i += 2 + arg_len
                            continue
                        section_parts, next_close, state = _hc009f_honbun_section_parts(code)
                        root.extend(section_parts)
                        hc009f_section_close = next_close
                        hc009f_current_section = value
                        if state is not None:
                            stats["hc009f_section_blocks"] += 1
                            stats[f"hc009f_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0096":
                        value = _hc0096_section_value(code)
                        if value is None:
                            gaps.add(f"hc0096_unmapped_section_{code}")
                            stats["hc0096_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        if hc0096_current_section and value != hc0096_current_section and hc0096_section_close is not None:
                            root.append(hc0096_section_close)
                            hc0096_section_close = None
                            stats["hc0096_lineinfo_closures"] += 1
                        if hc0096_current_section == 12 and value != 12 and not hc0096_contents_body_open:
                            root.append('<div class="contents_body">')
                            hc0096_contents_body_open = True
                            stats["hc0096_contents_body_blocks"] += 1
                        if value == 10:
                            root.append('<div class="hr_div2"></div>')
                            hc0096_current_section = value
                            stats["hc0096_hr_sections"] += 1
                            i += 2 + arg_len
                            continue
                        if value == hc0096_current_section:
                            stats["hc0096_repeated_sections_suppressed"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<div class="lineinfo0-{value}">')
                        hc0096_section_close = "</div>"
                        hc0096_current_section = value
                        stats["hc0096_lineinfo_sections"] += 1
                        stats[f"hc0096_lineinfo0_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0091":
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        stats["hc0091_address_anchors"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0090":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            gaps.add(f"hc0090_unmapped_section_{code}")
                            stats["hc0090_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        stats["hc0090_address_anchors"] += 1
                        if hc0090_current_section and hc0090_section_close is not None:
                            root.append(hc0090_section_close)
                            hc0090_section_close = None
                            stats["hc0090_lineinfo_closures"] += 1
                        if value != hc0090_current_section:
                            if hc0090_current_section == 5 and hc0090_yourei_open:
                                root.append("</div>")
                                hc0090_yourei_open = False
                                stats["hc0090_yourei_closures"] += 1
                            elif hc0090_current_section == 1 and not hc0090_contents_body_open:
                                root.append('<div class="contents_body">')
                                hc0090_contents_body_open = True
                                stats["hc0090_contents_body_blocks"] += 1
                            if value == 5 and not hc0090_yourei_open:
                                root.append('<div class="yourei">')
                                hc0090_yourei_open = True
                                stats["hc0090_yourei_sections"] += 1
                        root.append(f'<div class="lineinfo{value}">')
                        hc0090_section_close = "</div>"
                        hc0090_current_section = value
                        stats["hc0090_lineinfo_sections"] += 1
                        stats[f"hc0090_lineinfo_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0135":
                        value = _hc0135_section_value(code)
                        if value is None:
                            gaps.add(f"hc0135_unmapped_section_{code}")
                            stats["hc0135_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        stats["hc0135_address_anchors"] += 1
                        if value == 1:
                            if hc0135_section_close is not None:
                                root.append(hc0135_section_close)
                                hc0135_section_close = None
                                stats["hc0135_section_closures"] += 1
                            if hc0135_contents_body_open:
                                root.append("</div>")
                                hc0135_contents_body_open = False
                                stats["hc0135_contents_body_closures"] += 1
                            if not hc0135_midashi_open:
                                root.append('<div class="midashi">')
                                hc0135_midashi_open = True
                                stats["headings"] += 1
                                stats["hc0135_midashi_blocks"] += 1
                            hc0135_current_section = value
                            stats[f"hc0135_section_{value}"] += 1
                            i += 2 + arg_len
                            continue
                        if hc0135_midashi_open:
                            root.append("</div>")
                            hc0135_midashi_open = False
                            stats["hc0135_midashi_closures"] += 1
                            if not hc0135_contents_body_open:
                                root.append('<div class="contents_body">')
                                hc0135_contents_body_open = True
                                stats["hc0135_contents_body_blocks"] += 1
                        section_parts, next_hc0135_section_close, state = _hc0135_section_parts(
                            code,
                            options,
                            previous_pair=data[i - 2 : i] if i >= 2 else b"",
                        )
                        if next_hc0135_section_close is not None and hc0135_section_close is not None:
                            root.append(hc0135_section_close)
                            stats["hc0135_section_closures"] += 1
                            hc0135_section_close = None
                        root.extend(section_parts)
                        if next_hc0135_section_close is not None:
                            hc0135_section_close = next_hc0135_section_close
                        if section_parts:
                            stats["hc0135_section_blocks"] += 1
                        if state == "missing_exam_icon":
                            gaps.add("missing_hc0135_template_image_exam")
                        elif state is None:
                            gaps.add(f"hc0135_unmapped_section_{code}")
                            stats["hc0135_unmapped_sections"] += 1
                        else:
                            stats[f"hc0135_section_{state}"] += 1
                        hc0135_current_section = value
                        stats[f"hc0135_section_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "014F":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            gaps.add(f"hc014f_unmapped_section_{code}")
                            stats["hc014f_unmapped_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        hc014f_current_section = value
                        stats["hc014f_address_anchors"] += 1
                        stats[f"hc014f_section_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00A4":
                        if hc00a4_midashi_open:
                            root.append("</div>")
                            hc00a4_midashi_open = False
                            stats["hc00a4_midashi_closures"] += 1
                        if hc00a4_section_close is not None:
                            root.append(hc00a4_section_close)
                            hc00a4_section_close = None
                            stats["hc00a4_section_closures"] += 1
                        root.append(f'<a name="section-{stats["section_markers"]}"></a>')
                        section_parts, hc00a4_section_close, state = _hc00a4_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00a4_section_blocks"] += 1
                        if state is None:
                            stats["hc00a4_unmapped_sections"] += 1
                            gaps.add(f"hc00a4_unmapped_section_{code}")
                        else:
                            stats[f"hc00a4_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00A9":
                        if hc00a9_heading_phase:
                            stats["hc00a9_heading_section_state"] += 1
                            i += 2 + arg_len
                            continue
                        value = _hc02c0_section_value(code)
                        if value is None:
                            stats["hc00a9_unmapped_sections"] += 1
                            gaps.add(f"hc00a9_unmapped_section_{code}")
                            i += 2 + arg_len
                            continue
                        if hc00a9_section_close is not None and value != hc00a9_current_section:
                            root.append(hc00a9_section_close)
                            hc00a9_section_close = None
                            stats["hc00a9_section_closures"] += 1
                        if value == hc00a9_current_section:
                            stats["hc00a9_repeated_sections_suppressed"] += 1
                            i += 2 + arg_len
                            continue
                        section_parts, hc00a9_section_close, state, hc00a9_current_section = _hc00a9_section_parts(
                            code, vertical=options.vertical
                        )
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00a9_section_blocks"] += 1
                        if state:
                            stats[f"hc00a9_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00AB":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            stats["hc00ab_unmapped_sections"] += 1
                            gaps.add(f"hc00ab_unmapped_section_{code}")
                            i += 2 + arg_len
                            continue
                        if hc00ab_midashi_open and value != 1:
                            root.append("</div>")
                            hc00ab_midashi_open = False
                            stats["hc00ab_midashi_closures"] += 1
                        if hc00ab_section_close is not None and value != hc00ab_current_section:
                            root.append(hc00ab_section_close)
                            hc00ab_section_close = None
                            stats["hc00ab_section_closures"] += 1
                        if value == hc00ab_current_section:
                            stats["hc00ab_repeated_sections_suppressed"] += 1
                            i += 2 + arg_len
                            continue
                        section_parts, hc00ab_section_close, state = _hc00ab_section_parts(code)
                        root.extend(section_parts)
                        hc00ab_current_section = value
                        if section_parts:
                            stats["hc00ab_section_blocks"] += 1
                        if state:
                            stats[f"hc00ab_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00BB":
                        value = _hc02c0_section_value(code)
                        if value is None:
                            stats["hc00bb_unmapped_sections"] += 1
                            gaps.add(f"hc00bb_unmapped_section_{code}")
                            i += 2 + arg_len
                            continue
                        if hc00bb_midashi_open:
                            root.append("</div>")
                            hc00bb_midashi_open = False
                            stats["hc00bb_midashi_closures"] += 1
                        if hc00bb_section_close is not None and value != hc00bb_current_section:
                            root.append(hc00bb_section_close)
                            hc00bb_section_close = None
                            stats["hc00bb_section_closures"] += 1
                        if value == hc00bb_current_section:
                            stats["hc00bb_repeated_sections_suppressed"] += 1
                            i += 2 + arg_len
                            continue
                        section_parts, hc00bb_section_close, state, hc00bb_current_section = _hc00bb_section_parts(
                            code, vertical=options.vertical
                        )
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00bb_section_blocks"] += 1
                        if state:
                            stats[f"hc00bb_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "004D":
                        previous_key = _two_byte_key_at(data, i - 2)
                        if previous_key in HC004D_SECTION_SUPPRESS_PREVIOUS_KEYS:
                            stats["hc004d_suppressed_sections_after_marker"] += 1
                            i += 2 + arg_len
                            continue
                        if hc004d_heading_phase:
                            stats["hc004d_heading_section_state"] += 1
                            i += 2 + arg_len
                            continue
                        if not hc004d_honbun_open:
                            root.append('<div class="honbun">')
                            hc004d_honbun_open = True
                            stats["hc004d_honbun_blocks"] += 1
                        else:
                            stats["hc004d_repeated_body_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0076":
                        if hc0076_section_open:
                            root.append("</div>")
                            hc0076_section_open = False
                            stats["hc0076_section_closures"] += 1
                        if hc0076_heading_phase:
                            indent = 0
                            stats["hc0076_heading_section_state"] += 1
                        else:
                            try:
                                indent = int(code, 16) * 3
                            except ValueError:
                                indent = 0
                                gaps.add(f"hc0076_unmapped_section_{code}")
                                stats["hc0076_unmapped_sections"] += 1
                        root.append(f'<div style="margin-left:{indent}px;">')
                        hc0076_section_open = True
                        stats["hc0076_margin_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00C7":
                        value = _hc00c7_section_value(code)
                        if value is None:
                            stats["hc00c7_unmapped_sections"] += 1
                            gaps.add(f"hc00c7_unmapped_section_{code}")
                            i += 2 + arg_len
                            continue
                        if hc00c7_current_section == 0x16:
                            root.append("</font>")
                            stats["hc00c7_lineinfo22_closures"] += 1
                            if hc00c7_section_close is not None:
                                root.append(hc00c7_section_close)
                                hc00c7_section_close = None
                                stats["hc00c7_lineinfo_closures"] += 1
                        elif (
                            hc00c7_section_close is not None
                            and hc00c7_current_section not in {0x0B, 0x0C, 0x15}
                        ):
                            root.append(hc00c7_section_close)
                            hc00c7_section_close = None
                            stats["hc00c7_lineinfo_closures"] += 1
                        elif hc00c7_current_section in {0x0B, 0x0C}:
                            hc00c7_section_close = None
                        if hc00c7_current_section in {1, 2, 3} and not hc00c7_contents_body_open:
                            root.append('<div class="contents_body">')
                            hc00c7_contents_body_open = True
                            stats["hc00c7_contents_body_blocks"] += 1
                        if value in {0x0B, 0x0C}:
                            if value == 0x0C:
                                root.append("&nbsp;")
                                stats["hc00c7_navigation_state_sections"] += 1
                            else:
                                stats["hc00c7_header_state_sections"] += 1
                            hc00c7_current_section = value
                            stats[f"hc00c7_section_{value}"] += 1
                            i += 2 + arg_len
                            continue
                        if value == 0x16:
                            root.append('&nbsp;<font class="lineinfo22">')
                            hc00c7_current_section = value
                            stats["hc00c7_lineinfo22_sections"] += 1
                            stats[f"hc00c7_section_{value}"] += 1
                            i += 2 + arg_len
                            continue
                        root.append(f'<div class="lineinfo{value}">')
                        hc00c7_section_close = "</div>"
                        hc00c7_current_section = value
                        stats["hc00c7_lineinfo_sections"] += 1
                        stats[f"hc00c7_lineinfo_{value}"] += 1
                        stats[f"hc00c7_section_{value}"] += 1
                        i += 2 + arg_len
                        continue
                    if _is_hc005c_renderer(options):
                        if hc005c_section_close is not None:
                            root.append(hc005c_section_close)
                            hc005c_section_close = None
                        section_parts, hc005c_section_close, state = _hc005c_section_parts(code, data, i + 2 + arg_len)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc005c_section_blocks"] += 1
                        if state:
                            stats[f"hc005c_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0094":
                        if hc0094_color_div_close is not None:
                            root.append(hc0094_color_div_close)
                            hc0094_color_div_close = None
                        if hc0094_section_close is not None:
                            root.append(hc0094_section_close)
                            hc0094_section_close = None
                        section_parts, hc0094_section_close, state = _hc0094_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0094_section_blocks"] += 1
                        if state:
                            stats[f"hc0094_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
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
                    if _renderer_code(options) == "0020":
                        next_is_heading = data[i + 2 + arg_len : i + 4 + arg_len] == b"\x1f\x41"
                        if not next_is_heading and not hc0020_definition_open:
                            if hc0020_section_close is not None:
                                root.append(hc0020_section_close)
                                hc0020_section_close = None
                            value = _hc02c0_section_value(code)
                            if value is not None:
                                root.append(f'<div style="margin-left: {value * 3}px">')
                                hc0020_section_close = "</div>"
                                stats["hc0020_margin_sections"] += 1
                        stats["hc0020_section_controls"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0067":
                        next_is_heading = data[i + 2 + arg_len : i + 4 + arg_len] == b"\x1f\x41"
                        if not next_is_heading:
                            if hc0067_section_close is not None:
                                root.append(hc0067_section_close)
                                hc0067_section_close = None
                            value = _hc02c0_section_value(code)
                            if value is not None:
                                root.append(f'<div style="margin-left: {value * 3}px">')
                                hc0067_section_close = "</div>"
                                stats["hc0067_margin_sections"] += 1
                        stats["hc0067_section_controls"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0068":
                        next_is_heading = data[i + 2 + arg_len : i + 4 + arg_len] == b"\x1f\x41"
                        if not next_is_heading:
                            if hc0068_section_close is not None:
                                root.append(hc0068_section_close)
                                hc0068_section_close = None
                            value = _hc02c0_section_value(code)
                            if value is not None:
                                root.append(f'<div style="margin-left: {value * 3}px">')
                                hc0068_section_close = "</div>"
                                stats["hc0068_margin_sections"] += 1
                        stats["hc0068_section_controls"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0069":
                        if hc0069_contents_open:
                            if hc0069_section_close is not None:
                                root.append(hc0069_section_close)
                                hc0069_section_close = None
                            value = _hc02c0_section_value(code)
                            if value is not None:
                                root.append(f'<div style="margin-left: {value * 8}px">')
                                hc0069_section_close = "</div>"
                                stats["hc0069_margin_sections"] += 1
                        stats["hc0069_section_controls"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "008B":
                        if hc008b_section_close is not None:
                            root.append(hc008b_section_close)
                            hc008b_section_close = None
                        value = _hc02c0_section_value(code)
                        if value == 2:
                            root.append('<div class="kaisou">')
                            hc008b_section_close = "</div>"
                            stats["hc008b_kaisou_sections"] += 1
                        elif value == 3:
                            if not hc008b_contents_open:
                                root.append('<div class="contents_body">')
                                hc008b_contents_open = True
                                stats["hc008b_contents_body_blocks"] += 1
                        elif value == 1:
                            stats["hc008b_noop_sections"] += 1
                        stats["hc008b_section_controls"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00AC":
                        if hc00ac_section_close is not None:
                            root.append(hc00ac_section_close)
                            hc00ac_section_close = None
                        style = _hc00ac_honbun_style(code)
                        root.append(f'<div class="honbun" style="{_escape_attr(style)}">')
                        hc00ac_section_close = "</div>"
                        stats["hc00ac_honbun_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00AA":
                        if hc00aa_section_close is not None:
                            root.append(hc00aa_section_close)
                            hc00aa_section_close = None
                        section_parts, hc00aa_section_close, state = _hc00aa_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00aa_section_blocks"] += 1
                        if state:
                            stats[f"hc00aa_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00A3":
                        if hc00a3_section_close is not None:
                            root.append(hc00a3_section_close)
                            hc00a3_section_close = None
                        (
                            section_parts,
                            hc00a3_section_close,
                            state,
                            hc00a3_answer_index,
                            hc00a3_quiz_group_active,
                        ) = _hc00a3_section_parts(
                            code,
                            hc00a3_previous_section,
                            vertical=options.vertical,
                            answer_index=hc00a3_answer_index,
                            quiz_group_active=hc00a3_quiz_group_active,
                        )
                        root.extend(section_parts)
                        value = _hc00a3_section_value(code)
                        if value is not None:
                            hc00a3_previous_section = value
                        if section_parts:
                            stats["hc00a3_section_blocks"] += 1
                        if state:
                            stats[f"hc00a3_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00C5":
                        if hc00c5_section_close is not None:
                            root.append(hc00c5_section_close)
                            hc00c5_section_close = None
                        section_parts, hc00c5_section_close, state = _hc00c5_section_parts(code, options)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00c5_section_blocks"] += 1
                        if state:
                            stats[f"hc00c5_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00AD":
                        if hc00ad_section_close is not None:
                            root.append(hc00ad_section_close)
                            hc00ad_section_close = None
                        section_parts, hc00ad_section_close, state = _hc00ad_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc00ad_section_blocks"] += 1
                        if state:
                            stats[f"hc00ad_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0146":
                        if hc0146_section_close is not None:
                            root.append(hc0146_section_close)
                            hc0146_section_close = None
                        section_value = _hc0146_section_value(code)
                        if section_value == 70:
                            section_end = _find_control_offset(data, i + 2 + arg_len, 0x0A)
                            if section_end is not None:
                                i = section_end
                            else:
                                i += 2 + arg_len
                            stats["hc0146_synonym_icon_selectors"] += 1
                            continue
                        if section_value == 71:
                            if hc0146_column_frame_close is not None:
                                root.append(hc0146_column_frame_close)
                                hc0146_column_frame_close = None
                                stats["hc0146_column_frame_closures"] += 1
                            root.append(
                                '<div class="column_frame cm_f_synonym"><p class="column_title cm_t_synonym">'
                                '<img src="b227.png" class="column_icon">'
                            )
                            hc0146_section_close = "</p>"
                            hc0146_column_frame_close = "</div>"
                            stats["hc0146_template_sections"] += 1
                            stats["hc0146_section_synonym_column_title"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value == 150:
                            if hc0146_column_frame_close is not None:
                                root.append(hc0146_column_frame_close)
                                hc0146_column_frame_close = None
                                stats["hc0146_column_frame_closures"] += 1
                            root.append(
                                '<div class="column_frame cm_f_relation"><p class="column_title cm_t_relation">'
                                '<img src="b229.png" class="column_icon">'
                            )
                            hc0146_section_close = "</p>"
                            hc0146_column_frame_close = "</div>"
                            stats["hc0146_template_sections"] += 1
                            stats["hc0146_section_relation_column_title"] += 1
                            section_end = _find_control_offset(data, i + 2 + arg_len, 0x0A)
                            if section_end is not None:
                                i = section_end
                            else:
                                i += 2 + arg_len
                            continue
                        if (
                            section_value in HC0146_FRAME_CLOSE_SECTION_VALUES
                            and hc0146_column_frame_close is not None
                        ):
                            root.append(hc0146_column_frame_close)
                            hc0146_column_frame_close = None
                            stats["hc0146_column_frame_closures"] += 1
                        section_parts, hc0146_section_close, section_label, section_is_fallback = _hc0146_section_parts(code)
                        if not section_parts:
                            stats["hc0146_state_sections"] += 1
                            i += 2 + arg_len
                            continue
                        root.extend(section_parts)
                        if section_is_fallback:
                            gaps.add("hc0146_unmapped_section_branch")
                            stats["hc0146_fallback_honbun_sections"] += 1
                        else:
                            stats["hc0146_template_sections"] += 1
                            if section_label is not None:
                                stats[f"hc0146_section_{section_label}"] += 1
                        stats["hc0146_honbun_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0157":
                        if hc0157_section_close is not None:
                            root.append(hc0157_section_close)
                            hc0157_section_close = None
                        value = _hc0157_section_value(code)
                        if value in HC0157_GROUP_END_VALUES:
                            if hc0157_group_close is not None:
                                root.append(hc0157_group_close)
                                hc0157_group_close = None
                                hc0157_group_open_part_count = None
                                stats["hc0157_group_closures"] += 1
                            stats["hc0157_state_sections"] += 1
                            i += 2 + arg_len
                            continue
                        group_class = HC0157_GROUP_START_CLASSES.get(value) if value is not None else None
                        if group_class is not None:
                            if hc0157_group_close is not None:
                                root.append(hc0157_group_close)
                            root.append(f'<div class="{_escape_attr(group_class)}">')
                            hc0157_section_close = None
                            hc0157_group_close = "</div>"
                            hc0157_group_open_part_count = len(root)
                            stats["hc0157_group_sections"] += 1
                            stats[f"hc0157_section_{group_class}"] += 1
                            i += 2 + arg_len
                            continue
                        css_class = None
                        if value is not None:
                            css_class = HC0157_SIMPLE_SECTION_CLASSES.get(value) or (
                                f"textline {HC0157_TEXTLINE_CLASSES[value]}" if value in HC0157_TEXTLINE_CLASSES else None
                            )
                        if css_class is not None:
                            if value in HC0157_SIMPLE_SECTION_CLASSES and hc0157_group_close is not None:
                                root.append(hc0157_group_close)
                                hc0157_group_close = None
                                hc0157_group_open_part_count = None
                            root.append(f'<div class="{_escape_attr(css_class)}">')
                            hc0157_section_close = "</div>"
                            hc0157_group_open_part_count = None
                            stats["hc0157_section_blocks"] += 1
                            stats[f"hc0157_section_{css_class.replace(' ', '_')}"] += 1
                            i += 2 + arg_len
                            continue
                        if value == 1:
                            stats["hc0157_midashi_section_state"] += 1
                            i += 2 + arg_len
                            continue
                        if value is not None:
                            stats["hc0157_unmapped_sections"] += 1
                            gaps.add(f"hc0157_unmapped_section_{code}")
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
                        else:
                            stats["hc009d_unmapped_sections"] += 1
                            gaps.add(f"hc009d_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00C6":
                        if hc00c6_section_open:
                            while hc00c6_marker_stack:
                                root.append(hc00c6_marker_stack.pop()[1])
                            while style_stack:
                                close_tag = _style_close_tag(style_stack.pop(), options)
                                if close_tag:
                                    root.append(f"</{close_tag}>")
                            halfwidth_depth = 0
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
                        i += 2 + arg_len
                        continue
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
                    if _renderer_code(options) == "008C":
                        if hc008c_heading_phase:
                            stats["hc008c_heading_section_controls"] += 1
                            i += 2 + arg_len
                            continue
                        if hc008c_section_close is not None:
                            root.append(hc008c_section_close)
                            hc008c_section_close = None
                        section_parts, section_close, state = _hc008c_section_parts(code)
                        root.extend(section_parts)
                        hc008c_section_close = section_close
                        if section_parts:
                            stats["hc008c_section_blocks"] += 1
                        if state:
                            stats[f"hc008c_section_{state}"] += 1
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
                        if hc02be_section_close is not None:
                            root.append(hc02be_section_close)
                            hc02be_section_close = None
                            hc02be_section_open = False
                        section_parts, section_close, state = _hc02be_section_parts(code)
                        root.extend(section_parts)
                        hc02be_section_close = section_close
                        hc02be_section_open = section_close is not None
                        if section_parts:
                            stats["hc02be_section_blocks"] += 1
                        if state:
                            stats[f"hc02be_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
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
                        if code in {"9999", "270f"}:
                            stats["hc02bc_noop_sections"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00B6":
                        if hc00b6_section_close is not None:
                            root.append(hc00b6_section_close)
                            hc00b6_section_close = None
                        section_parts, section_close, state = _hc00b6_section_parts(code, options)
                        root.extend(section_parts)
                        hc00b6_section_close = section_close
                        if section_parts:
                            stats["hc00b6_section_blocks"] += 1
                        if state:
                            stats[f"hc00b6_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "012D":
                        hc012d_pending_honbun_user = False
                        if hc012d_section_close is not None:
                            root.append(hc012d_section_close)
                            hc012d_section_close = None
                        if code == "0007":
                            if hc012d_yindex_field_open:
                                root.append("</div>")
                                hc012d_yindex_field_open = False
                                stats["hc012d_yindex_field_closures"] += 1
                            yindex_id = _hc012d_yindex_id(options, i)
                            root.append(_hc012d_yindex_icon_html(yindex_id, options))
                            hc012d_pending_yindex_field_id = yindex_id
                            stats["hc012d_yindex_toggles"] += 1
                            section_end = _find_control_offset(data, i + 2 + arg_len, 0x0A)
                            if section_end is not None:
                                stats["hc012d_yindex_label_suppressed"] += 1
                                i = section_end + 2
                            else:
                                stats["hc012d_yindex_label_missing_end"] += 1
                                i += 2 + arg_len
                            continue
                        if code == "0002" and hc012d_yindex_field_open:
                            root.append("</div>")
                            hc012d_yindex_field_open = False
                            stats["hc012d_yindex_field_closures"] += 1
                        if code == "0004" and hc012d_pending_yindex_field_id is not None:
                            field_id = hc012d_pending_yindex_field_id
                            root.append(f'<div class="yindex_field" id="field{_escape_attr(field_id)}" style="display:none">')
                            hc012d_yindex_field_open = True
                            hc012d_pending_yindex_field_id = None
                            stats["hc012d_yindex_fields"] += 1
                        section_parts = _hc012d_section_parts(code, data, i)
                        root.extend(section_parts)
                        hc012d_section_close = _hc012d_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc012d_section_blocks"] += 1
                        elif code == "0001":
                            stats["hc012d_midashi_state_sections"] += 1
                        else:
                            stats["hc012d_unmapped_sections"] += 1
                            gaps.add(f"hc012d_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "013D":
                        section_value = _hc013d_bcd_value(code)
                        if hc013d_pc_table_open and section_value not in {42, 43}:
                            root.append("</td></tr></table><br>")
                            hc013d_pc_table_open = False
                            stats["hc013d_pc_table_closures"] += 1
                        if hc013d_gray_table_open and section_value not in {31, 32, 33, 34}:
                            root.append("</td></tr></table>")
                            hc013d_gray_table_open = False
                            stats["hc013d_gray_table_closures"] += 1
                        if hc013d_section_close is not None:
                            root.append(hc013d_section_close)
                            hc013d_section_close = None
                        if hc013d_clickmenu_close is not None:
                            root.append(hc013d_clickmenu_close)
                            hc013d_clickmenu_close = None
                            stats["hc013d_clickmenu_title_closures"] += 1
                        if section_value == 31:
                            if hc013d_gray_table_open:
                                root.append('</td></tr><tr bgcolor="#FFFFFF"><td>')
                                stats["hc013d_gray_table_rows"] += 1
                            else:
                                root.append(
                                    '<table cellpadding="3" cellspacing="1" bgcolor="#666666" '
                                    'style="margin:8px;"><tr bgcolor="#FFFFFF"><td>'
                                )
                                hc013d_gray_table_open = True
                                stats["hc013d_gray_tables"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value in {32, 33, 34}:
                            if not hc013d_gray_table_open:
                                root.append(
                                    '<table cellpadding="3" cellspacing="1" bgcolor="#666666" '
                                    'style="margin:8px;"><tr bgcolor="#FFFFFF"><td>'
                                )
                                hc013d_gray_table_open = True
                                stats["hc013d_gray_table_implicit_opens"] += 1
                            root.append("</td><td>")
                            stats["hc013d_gray_table_cells"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value == 42:
                            if hc013d_pc_table_open:
                                root.append('</td></tr><tr class="tr_pc"><td class="td_pc1">')
                                stats["hc013d_pc_table_rows"] += 1
                            else:
                                root.append('<table class="table_pc"><tr class="tr_pc"><td class="td_pc1">')
                                hc013d_pc_table_open = True
                                stats["hc013d_pc_tables"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value == 43:
                            if not hc013d_pc_table_open:
                                root.append('<table class="table_pc"><tr class="tr_pc"><td class="td_pc1">')
                                hc013d_pc_table_open = True
                                stats["hc013d_pc_table_implicit_opens"] += 1
                            root.append('</td><td class="td_pc2">')
                            stats["hc013d_pc_table_second_cells"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value == 70:
                            menu_index = stats["hc013d_clickmenu_titles"]
                            root.append(
                                f'<div><span id="lv-hc013d-menu-{menu_index}" class="clickmenu" '
                                'style="cursor:hand;">'
                            )
                            hc013d_clickmenu_close = (
                                f'<img src="menuoff.png" id="lv-hc013d-img-{menu_index}" '
                                'class="img_gaiji"></span></div>'
                            )
                            stats["hc013d_clickmenu_titles"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value == 71:
                            if hc013d_clickmenu_field_close is not None:
                                root.append(hc013d_clickmenu_field_close)
                                stats["hc013d_clickmenu_field_closures"] += 1
                            field_index = stats["hc013d_clickmenu_fields"]
                            root.append(f'<div id="lv-hc013d-field-{field_index}" style="display:none;">')
                            hc013d_clickmenu_field_close = "</div>"
                            stats["hc013d_clickmenu_fields"] += 1
                            i += 2 + arg_len
                            continue
                        if section_value == 72:
                            if hc013d_clickmenu_field_close is not None:
                                root.append(hc013d_clickmenu_field_close)
                                hc013d_clickmenu_field_close = None
                                stats["hc013d_clickmenu_field_closures"] += 1
                            else:
                                stats["hc013d_unmatched_clickmenu_field_closures"] += 1
                            i += 2 + arg_len
                            continue
                        section_parts, section_close = _hc013d_section_parts(code)
                        root.extend(section_parts)
                        hc013d_section_close = section_close
                        if section_parts:
                            stats["hc013d_section_blocks"] += 1
                        elif code == "0001":
                            stats["hc013d_midashi_state_sections"] += 1
                        else:
                            stats["hc013d_unmapped_sections"] += 1
                            gaps.add(f"hc013d_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0144":
                        if hc0144_section_close is not None:
                            root.append(hc0144_section_close)
                            hc0144_section_close = None
                        section_parts = _hc0144_section_parts(code)
                        root.extend(section_parts)
                        hc0144_section_close = _hc0144_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc0144_section_blocks"] += 1
                        elif _hc0145_section_is_known_state(code):
                            stats["hc0144_state_sections"] += 1
                        else:
                            stats["hc0144_unmapped_sections"] += 1
                            gaps.add(f"hc0144_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0158":
                        if hc0158_section_close is not None:
                            root.append(hc0158_section_close)
                            hc0158_section_close = None
                        section_parts, hc0158_section_close, state = _hc0158_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0158_section_blocks"] += 1
                        if state:
                            stats[f"hc0158_section_{state}"] += 1
                        else:
                            stats["hc0158_unmapped_sections"] += 1
                            gaps.add(f"hc0158_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0145":
                        if hc0145_section_close is not None:
                            root.append(hc0145_section_close)
                            hc0145_section_close = None
                        section_parts = _hc0145_section_parts(code)
                        root.extend(section_parts)
                        hc0145_section_close = _hc0145_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc0145_section_blocks"] += 1
                        elif _hc0145_section_is_known_state(code):
                            stats["hc0145_state_sections"] += 1
                        else:
                            stats["hc0145_unmapped_sections"] += 1
                            gaps.add(f"hc0145_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "03E8":
                        if hc03e8_section_close is not None:
                            root.append(hc03e8_section_close)
                            hc03e8_section_close = None
                        section_parts = _hc03e8_section_parts(code)
                        root.extend(section_parts)
                        hc03e8_section_close = _hc03e8_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc03e8_section_blocks"] += 1
                        elif _hc0145_section_is_known_state(code):
                            stats["hc03e8_state_sections"] += 1
                        else:
                            stats["hc03e8_unmapped_sections"] += 1
                            gaps.add(f"hc03e8_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0141":
                        if hc0141_section_close is not None:
                            root.append(hc0141_section_close)
                            hc0141_section_close = None
                        section_parts = _hc0141_section_parts(code)
                        root.extend(section_parts)
                        hc0141_section_close = _hc0141_section_close_for_parts(section_parts)
                        if section_parts:
                            stats["hc0141_section_blocks"] += 1
                        elif _hc0145_section_is_known_state(code):
                            stats["hc0141_state_sections"] += 1
                        else:
                            stats["hc0141_unmapped_sections"] += 1
                            gaps.add(f"hc0141_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "012E":
                        if code == "0054":
                            if hc012e_section_close is not None:
                                root.append(hc012e_section_close)
                                hc012e_section_close = None
                                stats["hc012e_explicit_section_closures"] += 1
                            else:
                                stats["hc012e_unmatched_section_closures"] += 1
                            hc012e_current_section = code
                            i += 2 + arg_len
                            continue
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
                        i += 2 + arg_len
                        continue
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
                    if _renderer_code(options) == "0132":
                        if hc0132_section_close is not None:
                            root.append(hc0132_section_close)
                            hc0132_section_close = None
                        value = _hc0132_section_value(code)
                        section_class = None
                        if value is not None:
                            next_private = data[i + 2 + arg_len : i + 4 + arg_len] == b"\x1f\xe2"
                            section_class = _hc0132_section_class(value, next_private_directive=next_private)
                        if section_class is not None:
                            if value in {*range(2, 12), 20} and not hc0132_honbun_open:
                                root.append('<div class="honbun">')
                                hc0132_honbun_open = True
                                stats["hc0132_honbun_blocks"] += 1
                            root.append(f'<div class="{_escape_attr(section_class)}">')
                            hc0132_section_close = "</div>"
                            stats["hc0132_section_blocks"] += 1
                            stats[f"hc0132_section_{section_class}"] += 1
                        elif value == 1:
                            stats["hc0132_midashi_section_state"] += 1
                        elif value is not None:
                            stats["hc0132_unmapped_sections"] += 1
                            gaps.add(f"hc0132_unmapped_section_{code}")
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
                    if _is_hc00c4_renderer(options):
                        if hc00c4_midashi_open:
                            root.append("</span></div>")
                            hc00c4_midashi_open = False
                        if hc00c4_section_close is not None:
                            root.append(hc00c4_section_close)
                            hc00c4_section_close = None
                        next_pos = i + 2 + arg_len
                        next_key = None
                        if next_pos + 1 < len(data) and 0xA1 <= data[next_pos] <= 0xFE:
                            next_key = f"{data[next_pos]:02x}{data[next_pos + 1]:02x}"
                        section_parts, hc00c4_section_close, state = _hc00c4_section_parts(code, next_key=next_key)
                        if code == "000f":
                            hc00c4_font_down_open = True
                        elif code == "0010":
                            if not hc00c4_font_down_open:
                                section_parts = []
                                state = "font_down_state_only"
                            hc00c4_font_down_open = False
                        root.extend(section_parts)
                        if state == "midashi":
                            hc00c4_midashi_open = True
                        if section_parts:
                            stats["hc00c4_section_blocks"] += 1
                        if state:
                            stats[f"hc00c4_section_{state}"] += 1
                        else:
                            stats["hc00c4_unknown_sections"] += 1
                            gaps.add(f"hc00c4_unmapped_section_{code}")
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "02C8":
                        if contexts and contexts[-1].kind == "private":
                            stats["hc02c8_private_section_controls"] += 1
                            i += 2 + arg_len
                            continue
                        if hc02c8_section_close is not None:
                            root.append(hc02c8_section_close)
                            hc02c8_section_close = None
                        section_parts, hc02c8_section_close, state, hc02c8_current_value = _hc02c8_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc02c8_section_blocks"] += 1
                        if state:
                            stats[f"hc02c8_section_{state}"] += 1
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
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0147":
                        if hc0147_section_close is not None:
                            root.append(hc0147_section_close)
                            hc0147_section_close = None
                        section_parts, hc0147_section_close, state = _hc0147_section_parts(code)
                        bunken_state = state in {"bunken_title", "bunken_contents", "cyosha"}
                        if not bunken_state and hc0147_bunken_open:
                            root.append("</div>")
                            hc0147_bunken_open = False
                        if bunken_state and not hc0147_bunken_open:
                            root.append('<div class="bunken">')
                            hc0147_bunken_open = True
                            stats["hc0147_bunken_groups"] += 1
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0147_section_blocks"] += 1
                        if state:
                            stats[f"hc0147_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0137":
                        if hc0137_section_close is not None:
                            root.append(hc0137_section_close)
                            hc0137_section_close = None
                            hc0137_line_close = None
                        section_parts, hc0137_section_close, state, hc0137_line_close = _hc0137_section_parts(code)
                        root.extend(section_parts)
                        if section_parts:
                            stats["hc0137_section_blocks"] += 1
                        if state:
                            stats[f"hc0137_section_{state}"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "0065" and code in {"0001", "0002"}:
                        if code == "0001" and not hc0065_midashi_open and not hc0065_body_open:
                            root.append('<div class="midashi">')
                            hc0065_midashi_open = True
                            stats["hc0065_midashi_blocks"] += 1
                        stats["hc0065_state_sections"] += 1
                        i += 2 + arg_len
                        continue
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
                if _is_hc005c_renderer(options):
                    parts = _current_parts(root_parts, contexts)
                    if hc005c_section_close is not None:
                        parts.append(hc005c_section_close)
                        hc005c_section_close = None
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "009F":
                    parts = _current_parts(root_parts, contexts)
                    if hc009f_section_close is not None:
                        parts.append(hc009f_section_close)
                        hc009f_section_close = None
                        stats["hc009f_section_line_closures"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0132":
                    parts = _current_parts(root_parts, contexts)
                    if hc0132_section_close is not None:
                        parts.append(hc0132_section_close)
                        hc0132_section_close = None
                        stats["hc0132_section_closures"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0146":
                    parts = _current_parts(root_parts, contexts)
                    if hc0146_section_close is not None:
                        parts.append(hc0146_section_close)
                        hc0146_section_close = None
                        stats["hc0146_section_closures"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0157":
                    parts = _current_parts(root_parts, contexts)
                    if hc0157_section_close is not None:
                        parts.append(hc0157_section_close)
                        hc0157_section_close = None
                        stats["hc0157_section_closures"] += 1
                    elif hc0157_group_open_part_count is not None and len(parts) == hc0157_group_open_part_count:
                        hc0157_group_open_part_count = None
                        stats["hc0157_group_open_linebreaks_suppressed"] += 1
                    else:
                        hc0157_group_open_part_count = None
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0190" and _hc0190_close_section(contexts, hc0190_sections, stats):
                    i += 2 + arg_len
                    continue
                if _is_hc00c4_renderer(options):
                    parts = _current_parts(root_parts, contexts)
                    if hc00c4_midashi_open:
                        parts.append("</span></div>")
                        hc00c4_midashi_open = False
                    if hc00c4_section_close is not None:
                        parts.append(hc00c4_section_close)
                        hc00c4_section_close = None
                    stats["hc00c4_section_closures"] += 1
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
                if _renderer_code(options) == "004D":
                    parts = _current_parts(root_parts, contexts)
                    if hc004d_midashi_open:
                        parts.append("</div>")
                        hc004d_midashi_open = False
                        hc004d_heading_phase = False
                        stats["hc004d_midashi_closures"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0073":
                    parts = _current_parts(root_parts, contexts)
                    if hc0073_midashi_open:
                        parts.append("</div>")
                        hc0073_midashi_open = False
                        stats["hc0073_midashi_closures"] += 1
                        if not hc0073_contents_open:
                            parts.append('<div class="contents">')
                            hc0073_contents_open = True
                            stats["hc0073_contents_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if hc0073_section_close is not None:
                        parts.append(hc0073_section_close)
                        hc0073_section_close = None
                        stats["hc0073_section_closures"] += 1
                        if not hc0073_contents_open:
                            parts.append('<div class="contents">')
                            hc0073_contents_open = True
                            stats["hc0073_contents_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0076":
                    parts = _current_parts(root_parts, contexts)
                    if hc0076_midashi_open:
                        parts.append("</div>")
                        hc0076_midashi_open = False
                        stats["hc0076_midashi_closures"] += 1
                    if hc0076_heading_phase:
                        if hc0076_section_open:
                            parts.append("</div>")
                            hc0076_section_open = False
                            stats["hc0076_section_closures"] += 1
                        hc0076_heading_phase = False
                        i += 2 + arg_len
                        continue
                    if hc0076_contents_body_open:
                        parts.append("</div>")
                        hc0076_contents_body_open = False
                        stats["hc0076_contents_body_closures"] += 1
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "007D":
                    parts = _current_parts(root_parts, contexts)
                    if hc007d_midashi_open:
                        parts.append("</div>")
                        hc007d_midashi_open = False
                        stats["hc007d_midashi_closures"] += 1
                    if hc007d_heading_phase or hc007d_pending_contents_transition:
                        if hc007d_section_open:
                            parts.append("</div>")
                            hc007d_section_open = False
                            stats["hc007d_section_closures"] += 1
                        hc007d_heading_phase = False
                        hc007d_pending_contents_transition = False
                        if not hc007d_contents_body_open:
                            parts.append('<div class="contents_body">')
                            hc007d_contents_body_open = True
                            stats["hc007d_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if hc007d_section_open:
                        parts.append("</div>")
                        hc007d_section_open = False
                        stats["hc007d_section_closures"] += 1
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "008F":
                    parts = _current_parts(root_parts, contexts)
                    if hc008f_section_close is not None:
                        if hc008f_hankaku_open:
                            parts.append("</span>")
                            hc008f_hankaku_open = False
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            stats["hc008f_hankaku_forced_closures"] += 1
                        parts.append(hc008f_section_close)
                        hc008f_section_close = None
                        stats["hc008f_section_closures"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "00C7":
                    stats["hc00c7_section_end_controls"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0020":
                    parts = _current_parts(root_parts, contexts)
                    if hc0020_midashi_open:
                        parts.append("</div>")
                        hc0020_midashi_open = False
                        if not hc0020_contents_open:
                            parts.append('<div class="contents_body">')
                            hc0020_contents_open = True
                            stats["hc0020_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if hc0020_div_215a_open:
                        parts.append("</div>")
                        hc0020_div_215a_open = False
                        stats["hc0020_div_215a_closures"] += 1
                        i += 2 + arg_len
                        continue
                    if hc0020_definition_open:
                        parts.append("</dd></dl>")
                        hc0020_definition_open = False
                        stats["hc0020_definition_list_closures"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0067":
                    parts = _current_parts(root_parts, contexts)
                    if hc0067_midashi_open:
                        parts.append("</div>")
                        hc0067_midashi_open = False
                        if not hc0067_contents_open:
                            parts.append('<div class="contents_body">')
                            hc0067_contents_open = True
                            stats["hc0067_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0068":
                    parts = _current_parts(root_parts, contexts)
                    if hc0068_midashi_open:
                        parts.append("</div>")
                        hc0068_midashi_open = False
                        if not hc0068_contents_open:
                            parts.append('<div class="contents_body">')
                            hc0068_contents_open = True
                            stats["hc0068_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0069":
                    parts = _current_parts(root_parts, contexts)
                    if hc0069_midashi_open:
                        parts.append("</div>")
                        hc0069_midashi_open = False
                        if not hc0069_contents_open:
                            parts.append('<div class="contents_body">')
                            hc0069_contents_open = True
                            stats["hc0069_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    parts.append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0091":
                    parts = _current_parts(root_parts, contexts)
                    if hc0091_midashi_open:
                        parts.append("</div>")
                        hc0091_midashi_open = False
                        if not hc0091_contents_open:
                            parts.append('<div class="contents_body">')
                            hc0091_contents_open = True
                            stats["hc0091_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if not hc0091_contents_open:
                        parts.append('<div class="contents_body">')
                        hc0091_contents_open = True
                        stats["hc0091_contents_body_blocks"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0135":
                    parts = _current_parts(root_parts, contexts)
                    if hc0135_midashi_open:
                        parts.append("</div>")
                        hc0135_midashi_open = False
                        stats["hc0135_midashi_closures"] += 1
                        if not hc0135_contents_body_open:
                            parts.append('<div class="contents_body">')
                            hc0135_contents_body_open = True
                            stats["hc0135_contents_body_blocks"] += 1
                        i += 2 + arg_len
                        continue
                    if hc0135_section_close is not None:
                        parts.append(hc0135_section_close)
                        hc0135_section_close = None
                        stats["hc0135_section_closures"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "014F":
                    parts = _current_parts(root_parts, contexts)
                    if hc014f_current_section == 1:
                        stats["hc014f_suppressed_heading_breaks"] += 1
                    else:
                        parts.append("<br/>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "008B":
                    _current_parts(root_parts, contexts).append("<br>")
                    stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "00AC":
                    _current_parts(root_parts, contexts).append("<br>")
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
                if _renderer_code(options) == "0137":
                    if hc0137_line_close is not None:
                        _current_parts(root_parts, contexts).append(hc0137_line_close)
                        hc0137_section_close = None
                        hc0137_line_close = None
                    else:
                        _current_parts(root_parts, contexts).append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "009D" and hc009d_section_close is not None:
                    if hc009d_table_header_open:
                        _current_parts(root_parts, contexts).append("</th></tr></thead><tbody><tr><td>")
                        hc009d_table_header_open = False
                        stats["hc009d_table_body_starts"] += 1
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
                    parts = _current_parts(root_parts, contexts)
                    while hc00c6_marker_stack:
                        parts.append(hc00c6_marker_stack.pop()[1])
                    while style_stack:
                        close_tag = _style_close_tag(style_stack.pop(), options)
                        if close_tag:
                            parts.append(f"</{close_tag}>")
                    halfwidth_depth = 0
                    parts.append("</div>")
                    hc00c6_section_open = False
                if _renderer_code(options) == "00A9":
                    parts = _current_parts(root_parts, contexts)
                    if hc00a9_heading_phase:
                        if hc00a9_midashi_open:
                            parts.append("</div>")
                            hc00a9_midashi_open = False
                            stats["hc00a9_midashi_closures"] += 1
                        hc00a9_heading_phase = False
                        stats["hc00a9_heading_breaks"] += 1
                        i += 2 + arg_len
                        continue
                    if hc00a9_current_section == 0x0C:
                        stats["hc00a9_header_breaks_suppressed"] += 1
                        i += 2 + arg_len
                        continue
                    next_control = data[i + 2 + arg_len : i + 4 + arg_len]
                    if next_control != b"\x1f\x0a":
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    else:
                        stats["hc00a9_duplicate_breaks_suppressed"] += 1
                    i += 2 + arg_len
                    continue
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
                    if hc02be_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc02be_section_close)
                        hc02be_section_close = None
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
                if _renderer_code(options) == "0158" and hc0158_section_close is not None:
                    _current_parts(root_parts, contexts).append(hc0158_section_close)
                    hc0158_section_close = None
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
                if _renderer_code(options) == "008C":
                    parts = _current_parts(root_parts, contexts)
                    if hc008c_heading_phase:
                        while style_stack:
                            popped = style_stack.pop()
                            close_tag = _style_close_tag(popped, options)
                            if popped == 0x41:
                                parts.append("</div>")
                                break
                            if close_tag:
                                parts.append(f"</{close_tag}>")
                        hc008c_heading_phase = False
                        if not hc008c_contents_body_open:
                            parts.append('<div class="contents_body">')
                            hc008c_contents_body_open = True
                            stats["hc008c_contents_body_blocks"] += 1
                        stats["hc008c_heading_line_breaks"] += 1
                    elif hc008c_section_close is not None:
                        parts.append(hc008c_section_close)
                        hc008c_section_close = None
                        stats["hc008c_section_line_breaks"] += 1
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "0063":
                    parts = _current_parts(root_parts, contexts)
                    if hc0063_heading_phase:
                        if hc0063_section_close is not None:
                            parts.append(hc0063_section_close)
                            hc0063_section_close = None
                        if not hc0063_contents_body_open:
                            parts.append('<div class="contents_body">')
                            hc0063_contents_body_open = True
                            stats["hc0063_contents_body_blocks"] += 1
                        hc0063_heading_phase = False
                    else:
                        parts.append("<br>")
                        stats["line_breaks"] += 1
                    i += 2 + arg_len
                    continue
                if _renderer_code(options) == "02C8":
                    if hc02c8_current_value in HC02C8_NO_BREAK_SECTION_VALUES:
                        i += 2 + arg_len
                        continue
                    if hc02c8_current_value in {3, 4, 5, 6, 7, 30, 31, 33} and hc02c8_section_close is not None:
                        _current_parts(root_parts, contexts).append(hc02c8_section_close)
                        hc02c8_section_close = None
                    else:
                        _current_parts(root_parts, contexts).append("<br/>")
                        stats["line_breaks"] += 1
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

            if _renderer_code(options) == "0094" and op in HC0094_NONPRINTING_CONTROL_OPS:
                stats["hc0094_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0093" and op in HC0093_NONPRINTING_CONTROL_OPS:
                stats["hc0093_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0095" and op in HC0095_NONPRINTING_CONTROL_OPS:
                stats["hc0095_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0096" and op in HC0096_NONPRINTING_CONTROL_OPS:
                stats["hc0096_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0142" and op in HC0142_NONPRINTING_CONTROL_OPS:
                stats["hc0142_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0146" and op in HC0146_NONPRINTING_CONTROL_OPS:
                stats["hc0146_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0147" and op in HC0147_NONPRINTING_CONTROL_OPS:
                stats["hc0147_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0137" and op in HC0137_NONPRINTING_CONTROL_OPS:
                stats["hc0137_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008C" and op in HC008C_NONPRINTING_CONTROL_OPS:
                stats["hc008c_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) in {"0142", "0147"} and op == 0x10:
                if _jis_key_at(data, i + 2) == "2372" and _jis_key_at(data, i + 4) == "2375":
                    src = _image_source_for_key("rubar", options) or "rubar.png"
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_mark">'
                    )
                    stats[f"hc{_renderer_code(options).lower()}_rubar_markers"] += 1
                    i += 2 + arg_len + 4
                else:
                    _current_parts(root_parts, contexts).append('<label class="overline">')
                    style_stack.append(op)
                    stats[f"hc{_renderer_code(options).lower()}_overline_markers"] += 1
                    i += 2 + arg_len
                continue

            if _renderer_code(options) in {"0142", "0147"} and op == 0x11:
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
                and hc00c6_supab_pending
                and op == 0x04
                and i + 5 < len(data)
                and data[i + 4 : i + 6] == b"\x1f\x05"
            ):
                label = normalize_fullwidth_ascii(decode_jis_pair(data[i + 2 : i + 4]))
                if label in {"A", "B"}:
                    _current_parts(root_parts, contexts).append(f'<sup class="supAB">{_escape_text(label)}</sup>')
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(label)
                    stats["hc00c6_supab_markers"] += 1
                    stats["hc00c6_supab_halfwidth_labels"] += 1
                    hc00c6_supab_pending = False
                    i += 6
                    continue

            if _renderer_code(options) == "00C6" and op == 0x04 and i + 3 < len(data) and data[i + 2 : i + 4] == b"\x1f\x05":
                stats["hc00c6_empty_halfwidth_spans_suppressed"] += 1
                i += 4
                continue

            if (
                _renderer_code(options) == "00C6"
                and op == 0x04
                and i + 5 < len(data)
                and data[i + 4 : i + 6] == b"\x1f\x05"
            ):
                close_key = f"{data[i + 2]:02x}{data[i + 3]:02x}"
                if close_key == "a244":
                    hc00c6_supab_pending = True
                    stats["hc00c6_supab_markers"] += 1
                    stats["hc00c6_supab_halfwidth_markers"] += 1
                    i += 6
                    continue
                if close_key in HC00C6_CLOSE_MARKERS:
                    if hc00c6_marker_stack and hc00c6_marker_stack[-1][0] == close_key:
                        _current_parts(root_parts, contexts).append(hc00c6_marker_stack.pop()[1])
                        stats["hc00c6_style_markers"] += 1
                    else:
                        stats["hc00c6_unmatched_style_markers"] += 1
                    stats["hc00c6_noop_markers"] += 1
                    i += 6
                    continue
                if close_key in HC00C6_NOOP_MARKERS:
                    stats["hc00c6_noop_markers"] += 1
                    i += 6
                    continue
            if _renderer_code(options) == "00C6" and op == 0x04 and i + 3 < len(data):
                marker_key = f"{data[i + 2]:02x}{data[i + 3]:02x}"
                marker = HC00C6_OPEN_MARKERS.get(marker_key)
                if marker is not None:
                    _current_parts(root_parts, contexts).append(marker.html)
                    _current_parts(root_parts, contexts).append('<span class="lv-hc-halfwidth">')
                    style_stack.append(0x04)
                    halfwidth_depth += 1
                    if marker.close_code is not None:
                        hc00c6_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc00c6_style_markers"] += 1
                    i += 4
                    continue
                if marker_key in HC00C6_CLOSE_MARKERS:
                    if hc00c6_marker_stack and hc00c6_marker_stack[-1][0] == marker_key:
                        _current_parts(root_parts, contexts).append(hc00c6_marker_stack.pop()[1])
                        stats["hc00c6_style_markers"] += 1
                    else:
                        stats["hc00c6_unmatched_style_markers"] += 1
                    _current_parts(root_parts, contexts).append('<span class="lv-hc-halfwidth">')
                    style_stack.append(0x04)
                    halfwidth_depth += 1
                    i += 4
                    continue
                if marker_key in HC00C6_NOOP_MARKERS:
                    stats["hc00c6_noop_markers"] += 1
                    _current_parts(root_parts, contexts).append('<span class="lv-hc-halfwidth">')
                    style_stack.append(0x04)
                    halfwidth_depth += 1
                    i += 4
                    continue

            if _renderer_code(options) == "012E" and op in HC012E_NONPRINTING_CONTROL_OPS:
                stats["hc012e_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00B6" and op in HC00B6_NONPRINTING_CONTROL_OPS:
                stats["hc00b6_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C2" and op in HC02C2_NONPRINTING_CONTROL_OPS:
                stats["hc02c2_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02C8" and op in HC02C8_NONPRINTING_CONTROL_OPS:
                stats["hc02c8_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02BC" and op in HC02BC_NONPRINTING_CONTROL_OPS:
                stats["hc02bc_nonprinting_controls"] += 1
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

            if _is_hc00c4_renderer(options) and op in HC00C4_NONPRINTING_CONTROL_OPS:
                stats["hc00c4_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A6" and op in HC00A6_NONPRINTING_CONTROL_OPS:
                stats["hc00a6_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "013A" and op in HC013A_NONPRINTING_CONTROL_OPS:
                stats["hc013a_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "013F" and op in HC013F_NONPRINTING_CONTROL_OPS:
                stats["hc013f_nonprinting_controls"] += 1
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

            if _renderer_code(options) == "009F" and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if hc009f_section_close is not None:
                    parts.append(hc009f_section_close)
                    hc009f_section_close = None
                    stats["hc009f_section_forced_closures"] += 1
                if not hc009f_midashi_open:
                    parts.append('<div class="midashi">')
                    hc009f_midashi_open = True
                    stats["headings"] += 1
                    stats["hc009f_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "009F" and op == 0x61:
                if hc009f_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc009f_midashi_open = False
                    stats["hc009f_midashi_closures"] += 1
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

            if _renderer_code(options) == "00C6" and op in HC00C6_NONPRINTING_CONTROL_OPS:
                stats["hc00c6_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue
            if _renderer_code(options) == "02BE" and op in HC02BE_NONPRINTING_CONTROL_OPS:
                stats["hc02be_nonprinting_controls"] += 1
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

            if _renderer_code(options) == "0020" and op == 0x41:
                if not hc0020_midashi_open and not hc0020_contents_open and not hc0020_definition_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc0020_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0020_midashi_blocks"] += 1
                stats["hc0020_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0020" and op in HC0020_NONPRINTING_CONTROL_OPS:
                stats["hc0020_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0067" and op == 0x41:
                if not hc0067_midashi_open and not hc0067_contents_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc0067_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0067_midashi_blocks"] += 1
                stats["hc0067_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0067" and op in HC0067_NONPRINTING_CONTROL_OPS:
                stats["hc0067_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0068" and op == 0x41:
                if not hc0068_midashi_open and not hc0068_contents_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc0068_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0068_midashi_blocks"] += 1
                stats["hc0068_heading_anchor_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0068" and op in HC0068_NONPRINTING_CONTROL_OPS:
                stats["hc0068_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0069" and op == 0x41:
                if not hc0069_midashi_open and not hc0069_contents_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc0069_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0069_midashi_blocks"] += 1
                stats["hc0069_heading_anchor_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0069" and op in HC0069_NONPRINTING_CONTROL_OPS:
                stats["hc0069_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0091" and op == 0x41:
                if not hc0091_midashi_open and not hc0091_contents_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc0091_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0091_midashi_blocks"] += 1
                else:
                    stats["hc0091_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0091" and op == 0x61:
                if hc0091_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc0091_midashi_open = False
                    stats["hc0091_midashi_closures"] += 1
                else:
                    stats["hc0091_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0091" and op in HC0091_NONPRINTING_CONTROL_OPS:
                stats["hc0091_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0135" and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if not hc0135_midashi_open and not hc0135_contents_body_open and not hc0135_honbun_user_open:
                    parts.append('<div class="midashi">')
                    hc0135_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0135_midashi_blocks"] += 1
                else:
                    stats["hc0135_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0135" and op == 0x61:
                parts = _current_parts(root_parts, contexts)
                if hc0135_midashi_open:
                    parts.append("</div>")
                    hc0135_midashi_open = False
                    stats["hc0135_midashi_closures"] += 1
                if not hc0135_honbun_user_open:
                    if hc0135_contents_body_open:
                        parts.append("</div>")
                        hc0135_contents_body_open = False
                        stats["hc0135_contents_body_closures"] += 1
                    parts.append('<div class="honbun_user">')
                    hc0135_honbun_user_open = True
                    stats["hc0135_honbun_user_blocks"] += 1
                else:
                    stats["hc0135_honbun_user_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0135" and op == 0x06:
                _current_parts(root_parts, contexts).append('<span class="sizedown"><sub>')
                style_stack.append(op)
                stats["hc0135_sizedown_sub_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0135" and op == 0x07:
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

            if _renderer_code(options) == "0135" and op in HC0135_NONPRINTING_CONTROL_OPS:
                stats["hc0135_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "014F" and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if not hc014f_midashi_open and not hc014f_contents_open:
                    parts.append('<div class="midashi">')
                    hc014f_midashi_open = True
                    stats["headings"] += 1
                    stats["hc014f_midashi_blocks"] += 1
                else:
                    stats["hc014f_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "014F" and op == 0x61:
                parts = _current_parts(root_parts, contexts)
                if hc014f_midashi_open:
                    parts.append("</div>")
                    hc014f_midashi_open = False
                    stats["hc014f_midashi_closures"] += 1
                if not hc014f_contents_open:
                    parts.append('<div class="contents">')
                    hc014f_contents_open = True
                    stats["hc014f_contents_blocks"] += 1
                else:
                    stats["hc014f_contents_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "014F" and op in HC014F_NONPRINTING_CONTROL_OPS:
                stats["hc014f_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0090" and op in HC0090_NONPRINTING_CONTROL_OPS:
                stats["hc0090_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008B" and op == 0x41:
                _current_parts(root_parts, contexts).append('<div class="midashi">')
                hc008b_midashi_open = True
                stats["headings"] += 1
                stats["hc008b_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008B" and op == 0x61:
                if hc008b_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc008b_midashi_open = False
                    if data[i + 2 + arg_len : i + 4 + arg_len] == b"\x1f\x0a":
                        i += 2 + arg_len + 2 + control_arg_length(data, i + 2 + arg_len)
                    else:
                        i += 2 + arg_len
                    stats["hc008b_midashi_closures"] += 1
                    continue

            if _renderer_code(options) == "008B" and op in HC008B_NONPRINTING_CONTROL_OPS:
                stats["hc008b_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op in HC005C_NONPRINTING_CONTROL_OPS:
                stats["hc005c_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if hc005c_section_close is not None:
                    parts.append(hc005c_section_close)
                    hc005c_section_close = None
                hc005c_heading_class = _hc005c_heading_class(data, i + 2 + arg_len)
                parts.append(f'<div class="{_escape_attr(hc005c_heading_class)}">')
                hc005c_heading_open = True
                stats["headings"] += 1
                stats["hc005c_heading_blocks"] += 1
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op == 0x61:
                parts = _current_parts(root_parts, contexts)
                if hc005c_emidashi_japanese_open:
                    parts.append("</span>")
                    hc005c_emidashi_japanese_open = False
                if hc005c_heading_open:
                    parts.append("</div>")
                    hc005c_heading_open = False
                    hc005c_heading_class = None
                stats["hc005c_heading_closures"] += 1
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op == 0x04:
                css = "hankakuMidashi" if hc005c_heading_open else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css}">')
                style_stack.append(op)
                halfwidth_depth += 1
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                        else:
                            close_tag = _style_close_tag(popped, options)
                            if close_tag:
                                _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                        if popped == 0x04:
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                next_op = data[i + 2 + arg_len + 1] if i + 2 + arg_len + 1 < len(data) and data[i + 2 + arg_len] == 0x1F else None
                if (
                    hc005c_heading_open
                    and hc005c_heading_class == "eMidashi"
                    and next_op != 0x61
                    and not hc005c_emidashi_japanese_open
                ):
                    _current_parts(root_parts, contexts).append('<span class="eMidashi_Japanese">')
                    hc005c_emidashi_japanese_open = True
                    stats["hc005c_emidashi_japanese_spans"] += 1
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

            if _renderer_code(options) == "00A4" and op in HC00A4_NONPRINTING_CONTROL_OPS:
                stats["hc00a4_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A4" and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if hc00a4_section_close is not None:
                    parts.append(hc00a4_section_close)
                    hc00a4_section_close = None
                    stats["hc00a4_section_closures"] += 1
                if not hc00a4_midashi_open:
                    parts.append('<div class="midashi">')
                    hc00a4_midashi_open = True
                    stats["headings"] += 1
                    stats["hc00a4_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A4" and op == 0x61:
                if hc00a4_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00a4_midashi_open = False
                    stats["hc00a4_midashi_closures"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A4" and op == 0x04:
                css_class = "hankakuMidashi" if hc00a4_midashi_open else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc00a4_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A4" and op == 0x44:
                target = _decode_pointer_payload(payload[-6:] if len(payload) >= 6 else payload)
                link = {
                    "start_control": "1f44",
                    "end_control": None,
                    "target": target,
                    "status": "resolved_address" if target else "unresolved_target",
                }
                links.append(link)
                image_src = _image_source_for_key("image", options) or _image_source_for_key("image.png", options)
                label = "image"
                if image_src is not None:
                    label = f'<img src="{_escape_attr(image_src)}" class="img_mark2">'
                attrs = [
                    'class="lv-hc-link lineLink"',
                    f'href="{_escape_attr(_pointer_href(target))}"',
                    f'data-lv-link-status="{_escape_attr(link["status"])}"',
                ]
                if target:
                    attrs.append(f'data-lv-block="{target["block"]}"')
                    attrs.append(f'data-lv-offset="{target["offset"]}"')
                _current_parts(root_parts, contexts).append(f"<a {' '.join(attrs)}>{label}</a>")
                stats["links"] += 1
                stats["hc00a4_image_links"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op in HC00A9_NONPRINTING_CONTROL_OPS:
                stats["hc00a9_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op == 0x41:
                if hc00a9_heading_phase and not hc00a9_midashi_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc00a9_midashi_open = True
                    stats["headings"] += 1
                    stats["hc00a9_midashi_blocks"] += 1
                else:
                    stats["hc00a9_heading_anchor_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op == 0x61:
                if hc00a9_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00a9_midashi_open = False
                    stats["hc00a9_midashi_closures"] += 1
                else:
                    stats["hc00a9_heading_anchor_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op == 0x04:
                css_class = "hankakuLink" if hc00a9_current_section == 0x0C else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc00a9_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op == 0x44:
                target = _decode_pointer_payload(payload[-6:] if len(payload) >= 6 else payload)
                link = {
                    "start_control": "1f44",
                    "end_control": None,
                    "target": target,
                    "status": "resolved_address" if target else "unresolved_target",
                }
                links.append(link)
                image_src = _image_source_for_key("image", options) or _image_source_for_key("image.png", options)
                label = "image"
                if image_src is not None:
                    label = f'<img src="{_escape_attr(image_src)}" class="img_mark2">'
                attrs = [
                    'class="lv-hc-link lineLink"',
                    f'href="{_escape_attr(_pointer_href(target))}"',
                    f'data-lv-link-status="{_escape_attr(link["status"])}"',
                ]
                if target:
                    attrs.append(f'data-lv-block="{target["block"]}"')
                    attrs.append(f'data-lv-offset="{target["offset"]}"')
                _current_parts(root_parts, contexts).append(f"<a {' '.join(attrs)}>{label}</a>")
                stats["links"] += 1
                stats["hc00a9_image_links"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00BB" and op in HC00BB_NONPRINTING_CONTROL_OPS:
                stats["hc00bb_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00BB" and op == 0x41:
                if not hc00bb_midashi_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc00bb_midashi_open = True
                    stats["headings"] += 1
                    stats["hc00bb_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00BB" and op == 0x61:
                if hc00bb_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00bb_midashi_open = False
                    stats["hc00bb_midashi_closures"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00BB" and op == 0x04:
                css_class = "hankakuLink" if hc00bb_current_section == 0x0C else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc00bb_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00BB" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AB" and op in HC00AB_NONPRINTING_CONTROL_OPS:
                stats["hc00ab_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AB" and op == 0x41:
                if not hc00ab_midashi_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc00ab_midashi_open = True
                    stats["headings"] += 1
                    stats["hc00ab_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AB" and op == 0x61:
                if hc00ab_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00ab_midashi_open = False
                    stats["hc00ab_midashi_closures"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AB" and op == 0x04:
                _current_parts(root_parts, contexts).append('<span class="hankaku">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats["hc00ab_hankaku_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AB" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0092" and op in HC0092_NONPRINTING_CONTROL_OPS:
                stats["hc0092_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0092" and op in {0x41, 0x61}:
                stats["hc0092_heading_anchor_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0092" and op == 0x04:
                css_class = "hankakuMidashi" if hc0092_current_section == 1 else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc0092_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0092" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "004D" and op == 0x41:
                if hc004d_heading_phase and not hc004d_midashi_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc004d_midashi_open = True
                    stats["headings"] += 1
                    stats["hc004d_midashi_blocks"] += 1
                else:
                    stats["hc004d_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "004D" and op == 0x61:
                if hc004d_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc004d_midashi_open = False
                    stats["hc004d_midashi_closures"] += 1
                hc004d_heading_phase = False
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "004D" and op == 0x04:
                _current_parts(root_parts, contexts).append('<span class="hankaku">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats["hc004d_hankaku_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "004D" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0076" and op == 0x41:
                if hc0076_heading_phase and not hc0076_midashi_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc0076_midashi_open = True
                    stats["headings"] += 1
                    stats["hc0076_midashi_blocks"] += 1
                else:
                    stats["hc0076_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0076" and op == 0x61:
                if hc0076_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc0076_midashi_open = False
                    stats["hc0076_midashi_closures"] += 1
                hc0076_heading_phase = False
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0076" and op == 0x04:
                _current_parts(root_parts, contexts).append('<span class="hankaku">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats["hc0076_hankaku_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0076" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0076" and op in {0x07, 0x0F}:
                target_start = 0x06 if op == 0x07 else 0x0E
                target_close = "sub" if op == 0x07 else "sup"
                closed_target = False
                while style_stack:
                    popped = style_stack.pop()
                    if popped == 0x04:
                        _current_parts(root_parts, contexts).append("</span>")
                        halfwidth_depth = max(0, halfwidth_depth - 1)
                    elif popped == target_start:
                        _current_parts(root_parts, contexts).append(f"</{target_close}>")
                        closed_target = True
                        break
                    else:
                        close_tag = _style_close_tag(popped, options)
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                if not closed_target:
                    _current_parts(root_parts, contexts).append(f"</{target_close}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0073" and op == 0x41:
                _current_parts(root_parts, contexts).append('<div class="midashi">')
                hc0073_midashi_open = True
                stats["headings"] += 1
                stats["hc0073_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0073" and op == 0x61:
                if hc0073_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc0073_midashi_open = False
                    stats["hc0073_midashi_closures"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0073" and op == 0x04:
                _current_parts(root_parts, contexts).append('<span class="hankaku">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats["hc0073_hankaku_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0073" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0073" and op in HC0073_NONPRINTING_CONTROL_OPS:
                stats["hc0073_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0076" and op in HC0076_NONPRINTING_CONTROL_OPS:
                stats["hc0076_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "007D" and op == 0x41:
                if hc007d_heading_phase and not hc007d_midashi_open:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    hc007d_midashi_open = True
                    stats["headings"] += 1
                    stats["hc007d_midashi_blocks"] += 1
                else:
                    stats["hc007d_heading_state_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "007D" and op == 0x61:
                if hc007d_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc007d_midashi_open = False
                    stats["hc007d_midashi_closures"] += 1
                if hc007d_heading_phase:
                    hc007d_pending_contents_transition = True
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "007D" and op == 0x04:
                _current_parts(root_parts, contexts).append('<span class="hankaku">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats["hc007d_hankaku_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "007D" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "007D" and op in HC007D_NONPRINTING_CONTROL_OPS:
                stats["hc007d_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008F" and op == 0x41:
                _current_parts(root_parts, contexts).append('<div class="jMidashi">')
                hc008f_jmidashi_open = True
                stats["headings"] += 1
                stats["hc008f_jmidashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008F" and op == 0x61:
                if hc008f_hankaku_open:
                    _current_parts(root_parts, contexts).append("</span>")
                    hc008f_hankaku_open = False
                    halfwidth_depth = max(0, halfwidth_depth - 1)
                    stats["hc008f_hankaku_forced_closures"] += 1
                hc008f_halfwidth_mode = False
                if hc008f_emidashi_japanese_open:
                    _current_parts(root_parts, contexts).append("</span>")
                    hc008f_emidashi_japanese_open = False
                    stats["hc008f_emidashi_japanese_closures"] += 1
                if hc008f_jmidashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc008f_jmidashi_open = False
                    stats["hc008f_jmidashi_closures"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008F" and op == 0x04:
                if hc008f_emidashi_japanese_open:
                    _current_parts(root_parts, contexts).append("</span>")
                    hc008f_emidashi_japanese_open = False
                    stats["hc008f_emidashi_japanese_closures"] += 1
                hc008f_halfwidth_mode = True
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008F" and op == 0x05:
                if hc008f_hankaku_open:
                    _current_parts(root_parts, contexts).append("</span>")
                    hc008f_hankaku_open = False
                    halfwidth_depth = max(0, halfwidth_depth - 1)
                hc008f_halfwidth_mode = False
                next_control = data[i + 2 + arg_len : i + 4 + arg_len]
                if hc008f_jmidashi_open and next_control != b"\x1f\x61":
                    _current_parts(root_parts, contexts).append('<span class="eMidashi_Japanese">')
                    hc008f_emidashi_japanese_open = True
                    stats["hc008f_emidashi_japanese_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "008F" and op in HC008F_NONPRINTING_CONTROL_OPS:
                stats["hc008f_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C7" and op == 0x04:
                _current_parts(root_parts, contexts).append('<span class="hankaku">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats["hc00c7_hankaku_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C7" and op == 0x05:
                if 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                        if popped == 0x12:
                            _current_parts(root_parts, contexts).append("</font>")
                            continue
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C7" and op == 0x12:
                _current_parts(root_parts, contexts).append('<font class="fontbold">')
                style_stack.append(op)
                stats["hc00c7_fontbold_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C7" and op == 0x13:
                if 0x12 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        if popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                        elif popped == 0x12:
                            _current_parts(root_parts, contexts).append("</font>")
                            break
                        else:
                            close_tag = _style_close_tag(popped, options)
                            if close_tag:
                                _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C7" and op in HC00C7_NONPRINTING_CONTROL_OPS:
                stats["hc00c7_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AC" and op in HC00AC_NONPRINTING_CONTROL_OPS:
                stats["hc00ac_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AA" and op in HC00AA_NONPRINTING_CONTROL_OPS:
                stats["hc00aa_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A3" and op in HC00A3_NONPRINTING_CONTROL_OPS:
                stats["hc00a3_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C5" and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if hc00c5_section_close is not None:
                    parts.append(hc00c5_section_close)
                    hc00c5_section_close = None
                if hc00c5_midashi_open:
                    parts.append("</div>")
                parts.append('<div class="midashi">')
                hc00c5_midashi_open = True
                stats["headings"] += 1
                stats["hc00c5_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C5" and op == 0x61:
                parts = _current_parts(root_parts, contexts)
                if hc00c5_midashi_open:
                    parts.append("</div>")
                    hc00c5_midashi_open = False
                if not hc00c5_honbun_user_open:
                    parts.append('<div class="honbun_user">')
                    hc00c5_honbun_user_open = True
                    stats["hc00c5_honbun_user_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00C5" and op in HC00C5_NONPRINTING_CONTROL_OPS:
                stats["hc00c5_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AD" and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if hc00ad_section_close is not None:
                    parts.append(hc00ad_section_close)
                    hc00ad_section_close = None
                if hc00ad_midashi_open:
                    parts.append("</div>")
                parts.append('<div class="midashi">')
                hc00ad_midashi_open = True
                stats["headings"] += 1
                stats["hc00ad_midashi_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AD" and op == 0x61:
                if hc00ad_midashi_open:
                    _current_parts(root_parts, contexts).append("</div>")
                    hc00ad_midashi_open = False
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00AD" and op in HC00AD_NONPRINTING_CONTROL_OPS:
                stats["hc00ad_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0063" and op in HC0063_NONPRINTING_CONTROL_OPS:
                stats["hc0063_nonprinting_controls"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0063" and op == 0x41:
                if hc0063_heading_phase:
                    _current_parts(root_parts, contexts).append('<div class="midashi">')
                    style_stack.append(op)
                    stats["headings"] += 1
                    stats["hc0063_heading_blocks"] += 1
                else:
                    stats["hc0063_anchor_controls"] += 1
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

            if _is_hc00c4_renderer(options) and op == 0x41:
                parts = _current_parts(root_parts, contexts)
                if hc00c4_midashi_open:
                    stats["headings"] += 1
                    stats["hc00c4_heading_state_controls"] += 1
                    i += 2 + arg_len
                    continue
                if hc00c4_section_close is not None:
                    parts.append(hc00c4_section_close)
                    hc00c4_section_close = None
                parts.append('<div class="midashi"><span class="zenkakuMidashi">')
                hc00c4_midashi_open = True
                stats["headings"] += 1
                stats["hc00c4_heading_blocks"] += 1
                i += 2 + arg_len
                continue

            if _is_hc00c4_renderer(options) and op == 0x61:
                parts = _current_parts(root_parts, contexts)
                if hc00c4_midashi_open:
                    parts.append("</span></div>")
                    hc00c4_midashi_open = False
                if hc00c4_section_close is not None:
                    parts.append(hc00c4_section_close)
                parts.append('<div class="honbun_user">')
                hc00c4_section_close = "</div>"
                stats["hc00c4_honbun_user_blocks"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0093" and op == 0x04:
                css_class = "hankakuMidashi" if hc0093_current_section == 1 else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc0093_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0095" and op == 0x04:
                css_class = "hankakuMidashi" if hc0095_current_section == 1 else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc0095_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0096" and op == 0x04:
                css_class = "hankakuMidashi" if hc0096_current_section == 1 else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc0096_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0091" and op == 0x04:
                css_class = "hankakuMidashi" if hc0091_midashi_open else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc0091_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0090" and op == 0x04:
                css_class = "hankakuMidashi" if hc0090_current_section == 1 else "hankaku"
                _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                style_stack.append(op)
                halfwidth_depth += 1
                stats[f"hc0090_{css_class}_spans"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "014F" and op == 0xE0:
                mode = (payload[-1] & 0x0F) if payload else 0
                if mode == 0:
                    _current_parts(root_parts, contexts).append("<b>")
                    hc014f_decoration_stack.append(mode)
                    stats["hc014f_bold_spans"] += 1
                elif mode == 1:
                    _current_parts(root_parts, contexts).append("<i>")
                    hc014f_decoration_stack.append(mode)
                    stats["hc014f_italic_spans"] += 1
                elif mode == 4:
                    _current_parts(root_parts, contexts).append("<b><i>")
                    hc014f_decoration_stack.append(mode)
                    stats["hc014f_bold_italic_spans"] += 1
                else:
                    gaps.add(f"hc014f_unhandled_decoration_mode_{mode}")
                    stats["hc014f_unhandled_decoration_modes"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "014F" and op == 0xE1:
                if hc014f_decoration_stack:
                    mode = hc014f_decoration_stack.pop()
                    if mode == 0:
                        _current_parts(root_parts, contexts).append("</b>")
                    elif mode == 1:
                        _current_parts(root_parts, contexts).append("</i>")
                    elif mode == 4:
                        _current_parts(root_parts, contexts).append("</i></b>")
                    stats["hc014f_decoration_closures"] += 1
                else:
                    stats["hc014f_unmatched_decoration_end"] += 1
                i += 2 + arg_len
                continue

            if (
                _renderer_code(options) == "0158"
                and op == 0x61
                and hc0158_marker_stack
                and hc0158_marker_stack[-1][0] == "b354_section_end"
            ):
                _current_parts(root_parts, contexts).append(hc0158_marker_stack.pop()[1])
                stats["hc0158_section_end_style_markers"] += 1

            style_spec = _style_start_spec(op, options)
            if style_spec is not None:
                if _renderer_code(options) == "012D" and hc012d_pending_honbun_user:
                    _current_parts(root_parts, contexts).append('<div class="honbun_user">')
                    hc012d_section_close = "</div>"
                    hc012d_pending_honbun_user = False
                tag, attrs = style_spec
                _current_parts(root_parts, contexts).append(f"<{tag}{attrs}>")
                style_context = contexts[-1] if contexts else None
                if style_context is not None:
                    style_context.style_stack.append(op)
                else:
                    style_stack.append(op)
                if op == 0x04:
                    if style_context is not None:
                        style_context.halfwidth_depth += 1
                    else:
                        halfwidth_depth += 1
                if op == 0x41:
                    stats["headings"] += 1
                i += 2 + arg_len
                continue

            if op in STYLE_END_OPS:
                start_op = STYLE_END_OPS[op]
                close_tag = _style_close_tag(start_op, options)
                style_context = contexts[-1] if contexts and start_op in contexts[-1].style_stack else None
                active_style_stack = style_context.style_stack if style_context is not None else style_stack
                closed_requested_style = False
                can_close_style = bool(close_tag and start_op in active_style_stack) or (
                    _is_hc005c_renderer(options) and start_op == 0x04 and start_op in active_style_stack
                )
                if can_close_style:
                    while active_style_stack:
                        popped = active_style_stack.pop()
                        if _is_hc005c_renderer(options) and popped == 0x04:
                            _current_parts(root_parts, contexts).append("</span>")
                        else:
                            popped_tag = _style_close_tag(popped, options)
                            if popped_tag:
                                _current_parts(root_parts, contexts).append(f"</{popped_tag}>")
                            elif _renderer_halfwidth_span_needs_explicit_close(popped, options):
                                _current_parts(root_parts, contexts).append("</span>")
                        if popped == 0x04:
                            if style_context is not None:
                                style_context.halfwidth_depth = max(0, style_context.halfwidth_depth - 1)
                            else:
                                halfwidth_depth = max(0, halfwidth_depth - 1)
                        if popped == start_op:
                            closed_requested_style = True
                            break
                if _renderer_code(options) == "012E" and op == 0x61 and closed_requested_style:
                    _current_parts(root_parts, contexts).append('<div class="honbun_user">')
                    hc012e_section_close = "</div>"
                if _renderer_code(options) == "012D" and op == 0x61 and closed_requested_style:
                    hc012d_pending_honbun_user = True
                i += 2 + arg_len
                continue

            if op in URL_START_OPS:
                contexts.append(_Context(kind="url", start_op=op, payload=payload, parent=_current_parts(root_parts, contexts), start_offset=i))
                i += 2 + arg_len
                continue

            if op in URL_END_OPS:
                ctx = _pop_context(contexts, "url")
                if ctx is not None:
                    _close_context_styles(ctx, options)
                    label = "".join(ctx.parts)
                    ctx.parent.append(f'<span class="lv-hc-url">{label}</span>')
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op == 0x44:
                target = _decode_pointer_payload(payload[-6:] if len(payload) >= 6 else payload)
                link = {
                    "start_control": "1f44",
                    "end_control": None,
                    "target": target,
                    "status": "resolved_address" if target else "unresolved_target",
                }
                links.append(link)
                image_src = _hc005c_image_source("image.png", options)
                attrs = [
                    'class="lv-hc-link lineLink"',
                    f'href="{_escape_attr(_pointer_href(target))}"',
                    f'data-lv-link-status="{_escape_attr(link["status"])}"',
                ]
                if target:
                    attrs.append(f'data-lv-block="{target["block"]}"')
                    attrs.append(f'data-lv-offset="{target["offset"]}"')
                _current_parts(root_parts, contexts).append(
                    f'<a {" ".join(attrs)}>'
                    f'<img src="{_escape_attr(_hc005c_image_source("dummy.gif", options))}" class="img_dummy">'
                    f'<img src="{_escape_attr(image_src)}" class="img_mark"></a>'
                )
                stats["links"] += 1
                stats["hc005c_image_links"] += 1
                i += 2 + arg_len
                continue

            if _is_hc005c_renderer(options) and op == 0x64:
                stats["hc005c_image_link_closures"] += 1
                i += 2 + arg_len
                continue

            if op in LINK_START_OPS:
                if _renderer_code(options) == "008C" and 0x04 in style_stack:
                    while style_stack:
                        popped = style_stack.pop()
                        close_tag = _style_close_tag(popped, options)
                        if close_tag:
                            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
                        if popped == 0x04:
                            halfwidth_depth = max(0, halfwidth_depth - 1)
                            break
                link_flags: set[str] = set()
                if _renderer_code(options) == "008C" and 0x41 in style_stack:
                    link_flags.add("hc008c_midashi")
                if _renderer_code(options) == "004D" and hc004d_midashi_open:
                    link_flags.add("hc004d_midashi")
                if _renderer_code(options) == "0076" and (hc0076_heading_phase or hc0076_midashi_open):
                    link_flags.add("hc0076_midashi")
                if _renderer_code(options) == "0073" and hc0073_midashi_open:
                    link_flags.add("hc0073_midashi")
                contexts.append(
                    _Context(
                        kind="link",
                        start_op=op,
                        payload=payload,
                        parent=_current_parts(root_parts, contexts),
                        start_offset=i,
                        flags=frozenset(link_flags),
                    )
                )
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
                if ctx is not None:
                    _close_context_styles(ctx, options)
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
                    f'class="{_escape_attr(_link_css_class_for_context(options, ctx, data))}"',
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
                if _renderer_code(options) == "0157" and ctx is not None:
                    while hc0157_marker_stack and hc0157_marker_stack[-1][2] > ctx.start_offset:
                        close_code, close_html, _start = hc0157_marker_stack.pop()
                        ctx.parts.append(close_html)
                        hc0157_link_scoped_close_markers[close_code] += 1
                        stats["hc0157_link_scoped_style_closures"] += 1
                target_payload = payload or (ctx.payload[-6:] if ctx and len(ctx.payload) >= 6 else b"")
                target = _decode_pointer_payload(target_payload, packed_bcd=_link_payload_is_packed_bcd(options))
                link = {
                    "start_control": f"1f{ctx.start_op:02x}" if ctx else None,
                    "end_control": f"1f{op:02x}",
                    "target": target,
                    "status": "resolved_address" if target else "unresolved_target",
                }
                links.append(link)
                if ctx is not None:
                    _close_context_styles(ctx, options)
                label = "".join(ctx.parts) if ctx else ""
                if not label:
                    label = "link"
                attrs = [
                    f'class="{_escape_attr(_link_css_class_for_context(options, ctx, data))}"',
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
                if ctx is not None:
                    _close_context_styles(ctx, options)
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
                    f'data-lv-original-href="{_escape_attr(_audio_original_href(target))}"',
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

            if _renderer_code(options) == "00A4" and op in MEDIA_OPS:
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
                html = _media_placeholder_html(control, target).replace(
                    'class="lv-hc-media"', 'class="lv-hc-media img_inline"', 1
                )
                _current_parts(root_parts, contexts).append(html + "<br>")
                stats["media_placeholders"] += 1
                stats["hc00a4_inline_media_placeholders"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "00A9" and op in MEDIA_OPS:
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
                html = _media_placeholder_html(control, target).replace(
                    'class="lv-hc-media"', 'class="lv-hc-media img_inline"', 1
                )
                _current_parts(root_parts, contexts).append(html + "<br>")
                stats["media_placeholders"] += 1
                stats["hc00a9_inline_media_placeholders"] += 1
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
                stats["hc02ca_private_state_markers"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "02CA" and op in PRIVATE_END_OPS:
                stats["hc02ca_private_state_markers"] += 1
                i += 2 + arg_len
                continue

            if _renderer_code(options) == "0136" and op in PRIVATE_START_OPS:
                directive = payload.hex()
                if directive == "0007":
                    stats["hc0136_private_state_blocks"] += 1
                    private_directives.append(
                        {
                            "start_control": "1fe2",
                            "end_control": "1fe3" if data.find(b"\x1f\xe3", i + 2 + arg_len) != -1 else None,
                            "directive": directive,
                            "status": "suppressed_private_block",
                        }
                    )
                    end = data.find(b"\x1f\xe3", i + 2 + arg_len)
                    i = (end + 2) if end != -1 else (i + 2 + arg_len)
                    continue
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
                    if _renderer_code(options) == "0135":
                        directive_text = _decode_jis_text_bytes(ctx.payload) + "".join(ctx.text_parts)
                        image_html = _hc0135_private_directive_image(directive_text, options)
                        status = "suppressed"
                        if image_html is not None:
                            ctx.parent.append(image_html)
                            stats["hc0135_private_directive_images"] += 1
                            status = "rendered_image"
                        private_directives.append(
                            {
                                "start_control": f"1f{ctx.start_op:02x}",
                                "end_control": f"1f{op:02x}",
                                "kind": "hc0135_private_directive",
                                "text_length": len(directive_text),
                                "status": status,
                            }
                        )
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00A4":
                        directive_text = _private_directive_text(
                            _decode_jis_text_bytes(ctx.payload) + "".join(ctx.text_parts)
                        )
                        if directive_text == "RUB:E":
                            ctx.parent.append('<ruby class="ruby7"><rb class="rb7">')
                            hc00a4_ruby_open = True
                            stats["hc00a4_ruby_starts"] += 1
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
                        if directive_text.startswith("RUB:S"):
                            ruby_text = directive_text[5:]
                            if hc00a4_ruby_open:
                                ctx.parent.append(
                                    '</rb><rp class="rp7">(</rp>'
                                    f'<rt class="rt7">{_escape_text(ruby_text)}</rt>'
                                    '<rp class="rp7">)</rp></ruby>'
                                )
                                hc00a4_ruby_open = False
                                status = "rendered"
                            else:
                                status = "unmatched"
                                gaps.add("hc00a4_unmatched_ruby_end")
                            stats["hc00a4_ruby_ends"] += 1
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "ruby_end",
                                    "text_length": len(directive_text),
                                    "status": status,
                                }
                            )
                            i += 2 + arg_len
                            continue
                        image_src = _hc00a4_private_image_src(directive_text, options)
                        if image_src is not None:
                            ctx.parent.append(f'<img src="{_escape_attr(image_src)}" class="img_inline">')
                            stats["hc00a4_private_inline_images"] += 1
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "inline_image",
                                    "text_length": len(directive_text),
                                    "status": "rendered",
                                }
                            )
                            i += 2 + arg_len
                            continue
                        if _hc00a4_private_resource_name(directive_text, "IMG") is not None:
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "inline_image",
                                    "text_length": len(directive_text),
                                    "status": "missing_resource",
                                }
                            )
                            gaps.add("hc00a4_missing_private_image")
                            i += 2 + arg_len
                            continue
                        html_fragment = _hc00a4_private_html_fragment(directive_text, options)
                        if html_fragment is not None:
                            html_text, repair_count = html_fragment
                            ctx.parent.append(html_text)
                            stats["hc00a4_private_html_includes"] += 1
                            if repair_count:
                                stats["hc00a4_private_html_table_rows_repaired"] += repair_count
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "html_include",
                                    "text_length": len(directive_text),
                                    "status": "rendered",
                                }
                            )
                            i += 2 + arg_len
                            continue
                        if _hc00a4_private_resource_name(directive_text, "HTM") is not None:
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "html_include",
                                    "text_length": len(directive_text),
                                    "status": "missing_resource",
                                }
                            )
                            gaps.add("hc00a4_missing_private_html")
                            i += 2 + arg_len
                            continue
                        url = _extract_first_url(directive_text)
                        if url is not None:
                            icon_key = "URL-V" if options.vertical else "URL-icon"
                            icon = _image_source_for_key(icon_key, options) or _image_source_for_key(
                                f"{icon_key}.gif", options
                            )
                            label = _escape_text(url)
                            if icon is not None:
                                label = f'<img src="{_escape_attr(icon)}" class="img_mark2">'
                            ctx.parent.append(
                                f'<a class="lineLink" target="_blank" href="{_escape_attr(url)}">{label}</a>'
                            )
                            stats["hc00a4_url_directives"] += 1
                            private_directives.append(
                                {
                                    "start_control": f"1f{ctx.start_op:02x}",
                                    "end_control": f"1f{op:02x}",
                                    "kind": "url",
                                    "text_length": len(directive_text),
                                    "status": "rendered",
                                }
                            )
                            i += 2 + arg_len
                            continue
                        private_directives.append(
                            {
                                "start_control": f"1f{ctx.start_op:02x}",
                                "end_control": f"1f{op:02x}",
                                "kind": "hc00a4_private_directive",
                                "text_length": len(directive_text),
                                "status": "suppressed",
                            }
                        )
                        stats["hc00a4_private_directives_suppressed"] += 1
                        i += 2 + arg_len
                        continue
                    if _renderer_code(options) == "00A6":
                        directive_text = _private_directive_text("".join(ctx.text_parts))
                        if directive_text == "RUB:E":
                            ctx.parent.append('<ruby class="ruby7"><rb class="rb7">')
                            hc00a6_ruby_readings.append("")
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
                        if directive_text.startswith("RUB:S"):
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
                            hc00a6_ruby_readings.pop()
                            ruby_text = directive_text[5:]
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

            if op in COMMON_RENDERER_STATE_OPS:
                stats["common_renderer_state_controls"] += 1
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
            if _renderer_code(options) == "009F" and hc009f_skip_next_jis:
                hc009f_skip_next_jis = False
                stats["hc009f_section_label_pairs_suppressed"] += 1
                i += 2
                continue
            if _renderer_code(options) == "00A9" and hc00a9_current_section == 0x0C and key == "222a":
                image_key = "mlinkV" if options.vertical else "mlink"
                image_src = (
                    _image_source_for_key(image_key, options)
                    or _image_source_for_key(f"{image_key}.gif", options)
                    or f"{image_key}.gif"
                )
                _current_parts(root_parts, contexts).append(
                    f'<img src="{_escape_attr(image_src)}" class="img_mark2">'
                )
                stats["hc00a9_mlink_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "00BB" and hc00bb_current_section == 0x0C and key == "222a":
                image_key = "mlinkV" if options.vertical else "mlink"
                image_src = (
                    _image_source_for_key(image_key, options)
                    or _image_source_for_key(f"{image_key}.gif", options)
                    or f"{image_key}.gif"
                )
                _current_parts(root_parts, contexts).append(
                    f'<img src="{_escape_attr(image_src)}" class="img_mark2">'
                )
                stats["hc00bb_mlink_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0020":
                parts = _current_parts(root_parts, contexts)
                if key == "215a":
                    if _jis_key_at(data, i - 2) != "222a":
                        parts.append('<div class="hr_div2"></div><div class="div_215a">')
                        hc0020_div_215a_open = True
                        stats["hc0020_div_215a_blocks"] += 1
                    text = decode_jis_pair(data[i : i + 2])
                    if text:
                        _append_text(parts, text)
                        text_parts = _current_text_parts(contexts)
                        if text_parts is not None:
                            text_parts.append(text)
                    i += 2
                    continue
                if key == "2221":
                    if hc0020_definition_open:
                        parts.append("</dd></dl>")
                    parts.append('<dl><dt>')
                    _append_hc0020_named_image(parts, "diamond", "img_diamond", options)
                    parts.append("</dt><dd>")
                    hc0020_definition_open = True
                    stats["hc0020_definition_lists"] += 1
                    i += 2
                    continue
                if key == "2126" and hc0020_definition_open:
                    parts.append("</dd><dt>")
                    _append_hc0020_named_image(parts, "nakaguro", "img_diamond", options)
                    parts.append("</dt><dd>")
                    stats["hc0020_definition_terms"] += 1
                    i += 2
                    continue
                if key == "222a":
                    dummy = _dummy_image_source(options)
                    if dummy is not None:
                        parts.append(f'<img src="{_escape_attr(dummy)}" class="img_dummy">')
                    _append_hc0020_named_image(parts, "confer", "img_confer", options)
                    stats["hc0020_confer_markers"] += 1
                    i += 2
                    continue
                if key in HC0020_SUPPRESSED_JIS_MARKERS:
                    stats["hc0020_suppressed_jis_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0096" and key == "214c":
                j = i + 2
                marker_key: str | None = None
                while j + 1 < len(data):
                    lookahead = f"{data[j]:02x}{data[j + 1]:02x}"
                    if lookahead == "252f":
                        marker_key = "b250"
                    elif lookahead == "457a":
                        marker_key = "b252"
                    elif lookahead == "255f":
                        marker_key = "b254"
                    if lookahead == "214d":
                        break
                    j += 2
                if marker_key is not None and j + 1 < len(data):
                    image_src = _image_source_for_key(marker_key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(
                            _current_parts(root_parts, contexts),
                            marker_key,
                            image_src,
                            "img_mark4",
                            stats,
                        )
                        stats["hc0096_inline_mark_images"] += 1
                        i = j + 2
                        continue
            if _renderer_code(options) == "0135":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                if key == "214c" and _jis_key_at(data, i + 2) == "214c":
                    parts.append(_hc0135_image_tag("b122", "img_gaiji", options))
                    stats["hc0135_jis_image_markers"] += 1
                    i += 4
                    continue
                if key == "214d" and _jis_key_at(data, i + 2) == "214d":
                    parts.append(_hc0135_image_tag("b123", "img_gaiji", options))
                    stats["hc0135_jis_image_markers"] += 1
                    i += 4
                    continue
                if key == "2265":
                    parts.append(_hc0135_image_tag("jyokon", "img_icon", options))
                    stats["hc0135_jis_image_markers"] += 1
                    i += 2
                    continue
                literal = {"2175": "&amp;", "216f": "&yen;"}.get(key)
                if literal is not None:
                    parts.append(literal)
                    if text_parts is not None:
                        text_parts.append(_plain_from_html(literal))
                    stats["hc0135_literal_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0091":
                marker = _hc0091_marker_image_match(data, i)
                if marker is not None:
                    image_key, image_name, consumed = marker
                    _append_hc0091_named_image(_current_parts(root_parts, contexts), image_key, image_name, options, stats)
                    i += consumed
                    continue
            if _renderer_code(options) == "0069" and _image_source_for_key(key, options) is not None:
                _append_hc0069_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=not hc0069_contents_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "0068" and _image_source_for_key(key, options) is not None:
                _append_hc0068_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=not hc0068_contents_open,
                )
                i += 2
                continue
            if _is_hc005c_renderer(options) and key == "215a":
                marker_name = HC005C_MARK_IMAGE_LABELS.get(
                    (
                        _jis_key_at(data, i + 2) or "",
                        _jis_key_at(data, i + 4) or "",
                    )
                )
                if marker_name is not None:
                    _append_hc005c_image(_current_parts(root_parts, contexts), marker_name, "img_mark", options)
                    stats["hc005c_mark_images"] += 1
                    i += 6
                    continue
            if _renderer_code(options) == "0076" and key in HC0076_TEMPLATE_IMAGE_MARKERS:
                image_src = _image_source_for_key(key, options) or _image_source_for_key(f"{key}.gif", options)
                if image_src is not None:
                    dummy_src = _dummy_image_source(options)
                    if dummy_src is not None:
                        _current_parts(root_parts, contexts).append(f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">')
                        stats["hc0076_dummy_images"] += 1
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                    stats["hc0076_template_image_markers"] += 1
                    i += 2
                    continue
            if (
                _renderer_code(options) == "00C7"
                and (
                    key in HC00C7_MARK4_GAIJI_MARKERS
                    or key in HC00C7_MARK_GAIJI_MARKERS
                    or _image_source_for_key(key, options) is not None
                )
            ):
                _append_hc00c7_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                )
                i += 2
                continue
            if _is_hc00c4_renderer(options):
                waku = HC00C4_WAKU_INLINE_IMAGES.get(key)
                if waku is not None:
                    image_name, css_class = waku
                    image_key = image_name.removesuffix(".png")
                    image_src = _image_source_for_key(image_key, options) or image_name
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, css_class, stats)
                    stats["hc00c4_waku_markers"] += 1
                    i += 2
                    continue
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
                if _renderer_code(options) == "008F" and hc008f_halfwidth_mode and not hc008f_hankaku_open:
                    css_class = "hankakuMidashi" if hc008f_jmidashi_open else "hankaku"
                    _current_parts(root_parts, contexts).append(f'<span class="{css_class}">')
                    hc008f_hankaku_open = True
                    halfwidth_depth += 1
                    stats[f"hc008f_{css_class}_spans"] += 1
                value = normalize_fullwidth_ascii(text) if halfwidth_depth or _context_halfwidth_depth(contexts) else text
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
            if _is_hc_britannica_panel_renderer(options) and key in HC_BRITANNICA_SUPPRESSED_GAIJI_MARKERS:
                stats["hc_britannica_state_markers"] += 1
                if key == "b422":
                    stats["hc_britannica_custom_body_state_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "013A":
                if key in HC013A_SUPPRESSED_GAIJI_MARKERS:
                    stats["hc013a_suppressed_gaiji_markers"] += 1
                    i += 2
                    continue
                if key == "b264":
                    parts = _current_parts(root_parts, contexts)
                    if not hc013a_honbun2_open:
                        parts.append('<div class="honbun2"><strong>')
                        hc013a_honbun2_open = True
                    stats["hc013a_honbun2_markers"] += 1
                    i += 2
                    continue
                if (
                    key in HC013A_CUSTOM_BITMAP_MARKERS
                    and not options.gaiji_map.get(key)
                    and _image_source_for_key(key, options) is None
                ):
                    html, resolved = _hc013a_custom_dib_html(key, options, in_heading=0x41 in style_stack)
                    _current_parts(root_parts, contexts).append(html)
                    stats["hc013a_custom_dib_gaiji"] += 1
                    if not resolved:
                        stats["hc013a_custom_dib_missing_resource"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "00A4":
                if key in HC00A4_SUPPRESSED_GAIJI_MARKERS:
                    stats["hc00a4_suppressed_gaiji_markers"] += 1
                    i += 2
                    continue
                if key == "b12f":
                    suffix = "V" if options.vertical else "H"
                    src = (
                        _image_source_for_key(f"b12f{suffix}", options)
                        or _image_source_for_key("b12f", options)
                        or f"b12f{suffix}.gif"
                    )
                    _current_parts(root_parts, contexts).append(
                        f'<img src="{_escape_attr(src)}" class="img_mark2">'
                    )
                    stats["hc00a4_b12f_markers"] += 1
                    i += 2
                    continue
                _append_hc00a4_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc00a4_midashi_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "00A9":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                mapped = options.gaiji_map.get(key)
                if mapped:
                    stats["gaiji_unicode"] += 1
                    _append_text(parts, mapped)
                    if text_parts is not None:
                        text_parts.append(mapped)
                else:
                    image_src = _image_source_for_key(key, options)
                    css_class = "img_gaiji_midashi" if hc00a9_heading_phase or hc00a9_midashi_open else "img_gaiji"
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
                        stats["hc00a9_template_gaiji"] += 1
                    else:
                        parts.append(
                            '<span class="lv-hc-gaiji lv-hc-custom-dib-missing '
                            f'{_escape_attr(css_class)}" data-gaiji-code="{_escape_attr(key)}" '
                            'data-hc-behavior="custom-gaiji-bitmap"></span>'
                        )
                        stats["hc00a9_custom_dib_gaiji"] += 1
                        gaps.add("hc00a9_custom_gaiji_bitmap_unresolved")
                i += 2
                continue
            if _renderer_code(options) == "00BB":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                mapped = options.gaiji_map.get(key)
                if mapped:
                    stats["gaiji_unicode"] += 1
                    _append_text(parts, mapped)
                    if text_parts is not None:
                        text_parts.append(mapped)
                else:
                    image_src = _image_source_for_key(key, options)
                    css_class = "img_gaiji_midashi" if hc00bb_midashi_open else "img_gaiji"
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, css_class, stats)
                        stats["hc00bb_template_gaiji"] += 1
                    else:
                        parts.append(
                            '<span class="lv-hc-gaiji lv-hc-custom-dib-missing '
                            f'{_escape_attr(css_class)}" data-gaiji-code="{_escape_attr(key)}" '
                            'data-hc-behavior="custom-gaiji-bitmap"></span>'
                        )
                        stats["hc00bb_custom_dib_gaiji"] += 1
                        gaps.add("hc00bb_custom_gaiji_bitmap_unresolved")
                i += 2
                continue
            if _renderer_code(options) == "00AC" and key in HC00AC_SUPPRESSED_GAIJI_MARKERS:
                stats["hc00ac_suppressed_markers"] += 1
                i += 2
                continue
            if _is_hc005c_renderer(options):
                if key in HC005C_CUSTOM_GAIJI_EXCLUDED:
                    stats["hc005c_suppressed_gaiji_markers"] += 1
                    i += 2
                    continue
                _append_hc005c_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc005c_heading_open,
                )
                stats["hc005c_custom_gaiji"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0069" and _image_source_for_key(key, options) is not None:
                _append_hc0069_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=not hc0069_contents_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "0068" and _image_source_for_key(key, options) is not None:
                _append_hc0068_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=not hc0068_contents_open,
                )
                i += 2
                continue
            if _is_hc00c4_renderer(options):
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                image_src = _image_source_for_key(key, options)
                if image_src is not None or key.startswith("b"):
                    css_class = HC00C4_GAIJI_CLASS_OVERRIDES.get(key, "gaiji")
                    _append_renderer_image_gaiji(parts, key, image_src or f"{key.upper()}.png", css_class, stats)
                    stats["hc00c4_template_gaiji"] += 1
                    i += 2
                    continue
                _append_gaiji_value(parts, text_parts, key, options, stats)
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
            if (
                _renderer_code(options) == "013C"
                and int(key, 16) >= 0xB121
                and not options.gaiji_map.get(key)
                and _image_source_for_key(key, options) is None
            ):
                stats["hc013c_custom_bitmap_fallback_suppressed"] += 1
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
            if _renderer_code(options) == "00B6":
                parts = _current_parts(root_parts, contexts)
                text_parts = _current_text_parts(contexts)
                image_class = HC00B6_IMAGE_MARKER_CLASSES.get(key)
                if image_class is not None:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(parts, key, image_src, image_class, stats)
                    else:
                        _append_gaiji_value(parts, text_parts, key, options, stats)
                    stats["hc00b6_image_markers"] += 1
                    i += 2
                    continue
                strong_text = HC00B6_STRONG_MARKERS.get(key)
                if strong_text is not None:
                    html = f"<strong>{_escape_text(strong_text)}</strong>"
                    parts.append(html)
                    if text_parts is not None:
                        text_parts.append(strong_text)
                    stats["hc00b6_strong_markers"] += 1
                    i += 2
                    continue
                if key in HC00B6_NOOP_MARKERS:
                    stats["hc00b6_noop_markers"] += 1
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
            if _renderer_code(options) == "0094":
                color_class = HC0094_COLOR_DIV_MARKERS.get(key)
                if color_class is not None:
                    parts = _current_parts(root_parts, contexts)
                    if hc0094_color_div_close is not None:
                        parts.append(hc0094_color_div_close)
                    parts.append(f'<div class="{color_class}">')
                    hc0094_color_div_close = "</div>"
                    stats["hc0094_color_div_markers"] += 1
                    i += 2
                    continue
                if key == HC0094_CLASS_ARROW_MARKER:
                    image_src = _image_source_for_key("class_arrow", options) or "class_arrow.gif"
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                    stats["hc0094_class_arrow_markers"] += 1
                    i += 2
                    continue
                if key in HC0094_STATE_MARKERS:
                    stats["hc0094_state_markers"] += 1
                    i += 2
                    continue
                if key in HC0094_SUPPRESSED_MARKERS:
                    stats["hc0094_suppressed_markers"] += 1
                    i += 2
                    continue
                if key in HC0094_TEMPLATE_IMAGE_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                    else:
                        _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                    stats["hc0094_template_image_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0147" and key in HC0147_TEMPLATE_IMAGE_MARKERS:
                image_src = _image_source_for_key(key, options)
                if image_src is not None:
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_gaiji", stats)
                else:
                    _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                stats["hc0147_template_image_markers"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0147" and key == "b15c":
                contexts.append(
                    _Context(
                        kind="hc0147_url",
                        start_op=0,
                        payload=data[i : i + 2],
                        parent=_current_parts(root_parts, contexts),
                        start_offset=i,
                    )
                )
                stats["hc0147_url_link_starts"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0147" and key == "b15d":
                ctx = _pop_context(contexts, "hc0147_url")
                if ctx is None:
                    gaps.add("hc0147_unmatched_url_marker")
                    stats["hc0147_unmatched_url_markers"] += 1
                else:
                    label = "".join(ctx.parts)
                    text = "".join(ctx.text_parts)
                    href = _extract_first_url(text) or "#"
                    ctx.parent.append(
                        f'<a class="lineLink" target="_blank" href="{_escape_attr(href)}">{label}</a>'
                    )
                    stats["hc0147_url_links"] += 1
                i += 2
                continue
            if _renderer_code(options) == "0147" and key in HC0147_PADDING_MARKERS:
                em = HC0147_PADDING_MARKERS[key]
                _current_parts(root_parts, contexts).append(f'<span style="padding-left:{em}em;"></span>')
                stats["hc0147_padding_markers"] += 1
                i += 2
                continue
            if _is_hc_gen_year_renderer(options):
                if key in HC_GEN_YEAR_NOOP_MARKERS:
                    stats["hc_gen_year_noop_markers"] += 1
                    i += 2
                    continue
                literal = HC_GEN_YEAR_LITERAL_MARKERS_BY_RENDERER.get(_renderer_code(options), {}).get(key)
                if literal is not None:
                    _append_text(_current_parts(root_parts, contexts), literal)
                    text_parts = _current_text_parts(contexts)
                    if text_parts is not None:
                        text_parts.append(literal)
                    stats["hc_gen_year_literal_markers"] += 1
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
                    hc009d_table_header_open = True
                    stats["hc009d_table_markers"] += 1
                    i += 2
                    continue
                if key == HC009D_TABLE_CLOSE_MARKER:
                    if hc009d_marker_stack and hc009d_marker_stack[-1][0] == key:
                        if hc009d_table_header_open:
                            parts.append("</th></tr></thead><tbody><tr><td>")
                            hc009d_table_header_open = False
                            stats["hc009d_table_body_starts"] += 1
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
                    if 0x04 in style_stack:
                        parts.append("</span>")
                        parts.append(marker.html)
                        parts.append('<span class="lv-hc-halfwidth">')
                    else:
                        parts.append(marker.html)
                    if marker.close_code is not None:
                        hc00c6_marker_stack.append((marker.close_code, marker.close_html))
                    stats["hc00c6_style_markers"] += 1
                    i += 2
                    continue
                if key in HC00C6_CLOSE_MARKERS:
                    if hc00c6_marker_stack and hc00c6_marker_stack[-1][0] == key:
                        if 0x04 in style_stack:
                            parts.append("</span>")
                            parts.append(hc00c6_marker_stack.pop()[1])
                            parts.append('<span class="lv-hc-halfwidth">')
                        else:
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
                if (
                    key == "b122"
                    and hc02bc_marker_stack
                    and hc02bc_marker_stack[-1][0] == "b125"
                    and not _has_two_byte_key_before_section_end(data, i + 2, "b125")
                ):
                    parts.append(hc02bc_marker_stack.pop()[1])
                    stats["hc02bc_b122_implicit_closures"] += 1
                    stats["hc02bc_style_markers"] += 1
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
                    if key == "b232" and 0xE0 in style_stack:
                        parts.append('</b><font class="color_font"><b>')
                        stats["hc0146_color_font_bold_nesting_repairs"] += 1
                    else:
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
                        close_code, close_html = hc0146_marker_stack.pop()
                        if close_code == "b233" and 0xE0 in style_stack:
                            parts.append("</b></font><b>")
                            stats["hc0146_color_font_bold_nesting_repairs"] += 1
                        else:
                            parts.append(close_html)
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
                        hc0157_marker_stack.append((marker.close_code, marker.close_html, i))
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
                    elif hc0157_link_scoped_close_markers[key]:
                        hc0157_link_scoped_close_markers[key] -= 1
                        stats["hc0157_link_scoped_close_markers"] += 1
                    else:
                        stats["hc0157_unmatched_style_markers"] += 1
                    i += 2
                    continue
                if key in HC0157_CLOSE_MARKERS:
                    if hc0157_marker_stack and hc0157_marker_stack[-1][0] == key:
                        parts.append(hc0157_marker_stack.pop()[1])
                        stats["hc0157_style_markers"] += 1
                    elif hc0157_link_scoped_close_markers[key]:
                        hc0157_link_scoped_close_markers[key] -= 1
                        stats["hc0157_link_scoped_close_markers"] += 1
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
                    if key == "b355":
                        close_code = "b354_rank1"
                    elif key == "b353" and _two_byte_key_at(data, i + 2) == "b35e":
                        close_code = "b354_section_end"
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
                if key == "b354" and hc0158_marker_stack and hc0158_marker_stack[-1][0] == "b354_rank1":
                    if _hc0158_has_following_rank_close_before_heading_end(data, i + 2):
                        stats["hc0158_rank1_midashi_delimiters"] += 1
                    else:
                        _current_parts(root_parts, contexts).append(hc0158_marker_stack.pop()[1])
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
            if _renderer_code(options) == "007D" and _image_source_for_key(key, options) is not None:
                _append_hc007d_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc007d_heading_phase or hc007d_midashi_open or not hc007d_contents_body_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "0073" and _image_source_for_key(key, options) is not None:
                _append_hc0073_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc0073_midashi_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "008F" and _image_source_for_key(key, options) is not None:
                _append_hc008f_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc008f_jmidashi_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "0063":
                image_key = HC0063_DIRECT_IMAGE_MARKERS.get(key)
                if image_key is not None:
                    image_src = _image_source_for_key(image_key, options)
                    if image_src is None:
                        gaps.add(f"missing_hc0063_template_image_{image_key}")
                    else:
                        css_class = "img_gaiji_midashi" if 0x41 in style_stack else "img_gaiji"
                        _append_renderer_image_gaiji(
                            _current_parts(root_parts, contexts),
                            key,
                            image_src,
                            css_class,
                            stats,
                        )
                        stats["hc0063_template_image_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0093":
                marker = HC0093_DIRECT_IMAGE_MARKERS.get(key)
                if marker is not None:
                    image_key, css_class = marker
                    if not _append_hc0093_template_marker(
                        _current_parts(root_parts, contexts),
                        key,
                        image_key,
                        css_class,
                        options,
                        stats,
                    ):
                        gaps.add(f"missing_hc0093_template_image_{image_key}")
                    i += 2
                    continue
                if key in HC0093_NOOP_MARKERS:
                    stats["hc0093_noop_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0095":
                if key in HC0095_PAGE_KIND_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        css_class = "img_gaiji_midashi" if hc0095_current_section == 1 else "img_gaiji"
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, css_class, stats)
                        stats["hc0095_page_kind_markers"] += 1
                    else:
                        _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                    i += 2
                    continue
                if key in HC0095_TEMPLATE_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        css_class = "img_mark2" if hc0095_current_section == 12 else "img_mark"
                        if css_class == "img_mark":
                            dummy_src = _dummy_image_source(options)
                            if dummy_src is not None:
                                _current_parts(root_parts, contexts).append(
                                    f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">'
                                )
                                stats["hc0095_dummy_images"] += 1
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, css_class, stats)
                        stats["hc0095_template_image_markers"] += 1
                    else:
                        _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                    i += 2
                    continue
            if _renderer_code(options) == "009F":
                if key in HC009F_SUPPRESSED_MARKERS:
                    stats["hc009f_suppressed_markers"] += 1
                    i += 2
                    continue
                if key in HC009F_ORIENTED_MARKERS:
                    image_key = f"{key}{_hc009f_oriented_prefix(options)}"
                    image_src = _hc009f_image_src(image_key, options)
                    if image_src is not None:
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, "img_mark", stats)
                        stats["hc009f_oriented_markers"] += 1
                    else:
                        gaps.add(f"missing_hc009f_oriented_marker_{image_key}")
                        _append_gaiji_value(
                            _current_parts(root_parts, contexts),
                            _current_text_parts(contexts),
                            key,
                            options,
                            stats,
                        )
                    i += 2
                    continue
                lookup_key = "b167" if key == "b261" else key
                if key == "b261":
                    stats["hc009f_b261_alias_markers"] += 1
                image_src = _hc009f_image_src(lookup_key, options)
                if image_src is not None:
                    css_class = "img_gaiji_midashi" if hc009f_midashi_open or hc009f_current_section == 1 else "img_gaiji"
                    dummy_src = _dummy_image_source(options)
                    if dummy_src is not None:
                        _current_parts(root_parts, contexts).append(
                            f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">'
                        )
                        stats["hc009f_dummy_images"] += 1
                    _append_renderer_image_gaiji(_current_parts(root_parts, contexts), lookup_key, image_src, css_class, stats)
                    stats["hc009f_template_image_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "0096":
                if key in HC0096_TEMPLATE_MARKERS:
                    image_src = _image_source_for_key(key, options)
                    if image_src is not None:
                        css_class = "img_mark2" if hc0096_current_section == 12 else "img_mark"
                        if css_class == "img_mark":
                            dummy_src = _dummy_image_source(options)
                            if dummy_src is not None:
                                _current_parts(root_parts, contexts).append(
                                    f'<img src="{_escape_attr(dummy_src)}" class="img_dummy">'
                                )
                                stats["hc0096_dummy_images"] += 1
                        _append_renderer_image_gaiji(_current_parts(root_parts, contexts), key, image_src, css_class, stats)
                        stats["hc0096_template_image_markers"] += 1
                    else:
                        _append_gaiji_value(_current_parts(root_parts, contexts), _current_text_parts(contexts), key, options, stats)
                    i += 2
                    continue
                if key in HC0096_REFLOW_STATE_MARKERS:
                    stats["hc0096_reflow_state_markers"] += 1
                    i += 2
                    continue
            if _renderer_code(options) == "00C7":
                _append_hc00c7_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                )
                i += 2
                continue
            if _renderer_code(options) == "0020":
                _append_hc0020_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=not hc0020_contents_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "0091":
                _append_hc0091_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc0091_midashi_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "0090":
                if key in HC0090_LINEBREAK_MARKERS:
                    _current_parts(root_parts, contexts).append("<br>")
                    stats["hc0090_linebreak_markers"] += 1
                    i += 2
                    continue
                _append_hc0090_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc0090_current_section == 1,
                )
                i += 2
                continue
            if _renderer_code(options) == "0135":
                _append_hc0135_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=hc0135_midashi_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "014F":
                _append_hc014f_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    key,
                    options,
                    stats,
                    in_heading=not hc014f_contents_open,
                )
                i += 2
                continue
            if _renderer_code(options) == "00A3" and key == "b261":
                _append_gaiji_value(
                    _current_parts(root_parts, contexts),
                    _current_text_parts(contexts),
                    "b167",
                    options,
                    stats,
                )
                stats["hc00a3_b261_alias_markers"] += 1
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
        if _renderer_code(options) in {"0131", "0135"} and popped == 0x06:
            _current_parts(root_parts, contexts).append("</sub></span>")
            continue
        if _is_hc005c_renderer(options) and popped == 0x04:
            _current_parts(root_parts, contexts).append("</span>")
            continue
        if _renderer_code(options) == "0076" and popped == 0x04:
            _current_parts(root_parts, contexts).append("</span>")
            continue
        if _renderer_code(options) == "007D" and popped == 0x04:
            _current_parts(root_parts, contexts).append("</span>")
            continue
        if _renderer_code(options) == "008F" and popped == 0x04:
            _current_parts(root_parts, contexts).append("</span>")
            continue
        if _renderer_code(options) == "0073" and popped == 0x04:
            _current_parts(root_parts, contexts).append("</span>")
            continue
        if _renderer_code(options) == "00C7" and popped == 0x04:
            _current_parts(root_parts, contexts).append("</span>")
            continue
        if _renderer_code(options) == "00C7" and popped == 0x12:
            _current_parts(root_parts, contexts).append("</font>")
            continue
        close_tag = _style_close_tag(popped, options)
        if close_tag:
            _current_parts(root_parts, contexts).append(f"</{close_tag}>")
    while hc0158_marker_stack:
        close_code, close_html = hc0158_marker_stack.pop()
        _current_parts(root_parts, contexts).append(close_html)
        gaps.add(f"unterminated_hc0158_marker_{close_code}")
    while hc0157_marker_stack:
        close_code, close_html, _start = hc0157_marker_stack.pop()
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
    if hc009d_table_header_open:
        _current_parts(root_parts, contexts).append("</th></tr></thead><tbody><tr><td>")
        hc009d_table_header_open = False
        stats["hc009d_table_body_starts"] += 1
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
    if hc0094_color_div_close is not None:
        _current_parts(root_parts, contexts).append(hc0094_color_div_close)
    if hc0094_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0094_section_close)
    if hc0093_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0093_section_close)
    if hc0093_current_section == 5:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0093_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0095_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0095_section_close)
    if hc0095_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0096_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0096_section_close)
    if hc0096_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0092_lineinfo_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0092_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc02bc_section_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc009d_section_close is not None:
        _current_parts(root_parts, contexts).append(hc009d_section_close)
    if hc009b_section_close is not None:
        _current_parts(root_parts, contexts).append(hc009b_section_close)
    if hc013a_honbun2_open:
        _current_parts(root_parts, contexts).append("</strong></div>")
        hc013a_honbun2_open = False
        stats["hc013a_honbun2_closures"] += 1
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
    if hc00b6_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00b6_section_close)
    if hc012d_section_close is not None:
        _current_parts(root_parts, contexts).append(hc012d_section_close)
    if hc012d_yindex_field_open:
        _current_parts(root_parts, contexts).append("</div>")
        stats["hc012d_yindex_field_closures"] += 1
    if hc013d_section_close is not None:
        _current_parts(root_parts, contexts).append(hc013d_section_close)
    if hc013d_pc_table_open:
        _current_parts(root_parts, contexts).append("</td></tr></table><br>")
        stats["hc013d_pc_table_closures"] += 1
    if hc013d_gray_table_open:
        _current_parts(root_parts, contexts).append("</td></tr></table>")
        stats["hc013d_gray_table_closures"] += 1
    if hc013d_clickmenu_close is not None:
        _current_parts(root_parts, contexts).append(hc013d_clickmenu_close)
    if hc013d_clickmenu_field_close is not None:
        _current_parts(root_parts, contexts).append(hc013d_clickmenu_field_close)
    if hc0141_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0141_section_close)
    if hc0144_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0144_section_close)
    if hc0145_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0145_section_close)
    if hc0158_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0158_section_close)
    if hc03e8_section_close is not None:
        _current_parts(root_parts, contexts).append(hc03e8_section_close)
    if hc00a6_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00a6_section_close)
    if hc00a4_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00a4_section_close)
    if hc00a4_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc00a4_ruby_open:
        _current_parts(root_parts, contexts).append(
            '</rb><rp class="rp7">(</rp><rt class="rt7"></rt><rp class="rp7">)</rp></ruby>'
        )
        gaps.add("hc00a4_unterminated_ruby")
    if hc00a9_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00a9_section_close)
    if hc00a9_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc00ab_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00ab_section_close)
    if hc00ab_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc00bb_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00bb_section_close)
    if hc00bb_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
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
    if hc02be_section_close is not None:
        _current_parts(root_parts, contexts).append(hc02be_section_close)
    if hc013c_section_close is not None:
        _current_parts(root_parts, contexts).append(hc013c_section_close)
    if hc00b3_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00b3_section_close)
    if hc_gen_year_section_close is not None:
        _current_parts(root_parts, contexts).append(hc_gen_year_section_close)
    if hc00c4_midashi_open:
        _current_parts(root_parts, contexts).append("</span></div>")
    if hc00c4_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00c4_section_close)
    if hc00c4_font_down_open:
        _current_parts(root_parts, contexts).append("</font>")
    if hc_hkdksr_medical_section_close is not None:
        _current_parts(root_parts, contexts).append(hc_hkdksr_medical_section_close)
    if hc_hkdksr_medical_table_open:
        _current_parts(root_parts, contexts).append("</td></tr></table>")
    if hc008c_section_close is not None:
        _current_parts(root_parts, contexts).append(hc008c_section_close)
    if hc008c_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc02c8_section_close is not None:
        _current_parts(root_parts, contexts).append(hc02c8_section_close)
    if hc02c2_section_open:
        if hc02c2_moji_down_open:
            _current_parts(root_parts, contexts).append("</p>")
        _current_parts(root_parts, contexts).append("</div>")
    if hc0147_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0147_section_close)
    if hc0147_bunken_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0137_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0137_section_close)
    if hc0048_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0048_honbun_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0048_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0048_section_close)
    if hc0048_media_div_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc004d_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc004d_honbun_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0073_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0073_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0073_section_close)
    if hc0073_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0076_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0076_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0076_section_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc007d_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc007d_section_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc007d_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc008f_emidashi_japanese_open:
        _current_parts(root_parts, contexts).append("</span>")
    if hc008f_jmidashi_open:
        if hc008f_hankaku_open:
            _current_parts(root_parts, contexts).append("</span>")
            hc008f_hankaku_open = False
            halfwidth_depth = max(0, halfwidth_depth - 1)
        _current_parts(root_parts, contexts).append("</div>")
    if hc008f_section_close is not None:
        if hc008f_hankaku_open:
            _current_parts(root_parts, contexts).append("</span>")
            hc008f_hankaku_open = False
            halfwidth_depth = max(0, halfwidth_depth - 1)
        _current_parts(root_parts, contexts).append(hc008f_section_close)
    if hc00c7_current_section == 0x16:
        _current_parts(root_parts, contexts).append("</font>")
    if hc00c7_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00c7_section_close)
    if hc00c7_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc00ac_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00ac_section_close)
    if hc0020_div_215a_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0020_definition_open:
        _current_parts(root_parts, contexts).append("</dd></dl>")
    if hc0020_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0020_section_close)
    if hc0020_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0020_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0063_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0063_section_close)
    if hc0063_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0065_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0065_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0067_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0067_section_close)
    if hc0067_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0067_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0068_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0068_section_close)
    if hc0068_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0068_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0069_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0069_section_close)
    if hc0069_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0069_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0091_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0091_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc009f_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc009f_section_close is not None:
        _current_parts(root_parts, contexts).append(hc009f_section_close)
    if hc0135_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0135_section_close)
    if hc0135_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0135_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0135_honbun_user_open:
        _current_parts(root_parts, contexts).append("</div>")
    while hc014f_decoration_stack:
        mode = hc014f_decoration_stack.pop()
        if mode == 0:
            _current_parts(root_parts, contexts).append("</b>")
        elif mode == 1:
            _current_parts(root_parts, contexts).append("</i>")
        elif mode == 4:
            _current_parts(root_parts, contexts).append("</i></b>")
        gaps.add(f"unterminated_hc014f_decoration_{mode}")
    if hc014f_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc014f_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0090_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0090_section_close)
    if hc0090_yourei_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0090_contents_body_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc008b_section_close is not None:
        _current_parts(root_parts, contexts).append(hc008b_section_close)
    if hc008b_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc008b_contents_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc005c_emidashi_japanese_open:
        _current_parts(root_parts, contexts).append("</span>")
    if hc005c_heading_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc005c_section_close is not None:
        _current_parts(root_parts, contexts).append(hc005c_section_close)
    if hc0132_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0132_section_close)
    if hc0132_honbun_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc0146_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0146_section_close)
    if hc0146_column_frame_close is not None:
        _current_parts(root_parts, contexts).append(hc0146_column_frame_close)
    if hc0157_section_close is not None:
        _current_parts(root_parts, contexts).append(hc0157_section_close)
    if hc0157_group_close is not None:
        _current_parts(root_parts, contexts).append(hc0157_group_close)
    if hc00aa_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00aa_section_close)
    if hc00a3_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00a3_section_close)
    if hc00c5_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00c5_section_close)
    if hc00c5_midashi_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc00c5_honbun_user_open:
        _current_parts(root_parts, contexts).append("</div>")
    if hc00ad_section_close is not None:
        _current_parts(root_parts, contexts).append(hc00ad_section_close)
    if hc00ad_midashi_open:
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
        elif ctx.kind == "link" and ctx.start_op == 0x42 and _renderer_code(options) == "008B":
            ctx.parent.extend(ctx.parts)
            stats["hc008b_unterminated_link_recovered"] += 1
        elif ctx.kind == "link":
            ctx.parent.extend(ctx.parts)
            stats["unterminated_links_recovered"] += 1
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
    body = _rewrite_lved_addr_hrefs(body)
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
    gaps: list[str] = []
    for feature, gap in (
        ("panel_hooks", "panel_hooks"),
        ("plugin_hooks", "plugin_hooks"),
        ("user_data_hooks", "user_data_hooks"),
        ("sql_hooks", "sql_search_or_helper_hooks"),
        ("headword_modifier", "modify_headword_hook"),
        ("custom_gaiji_dib", "custom_gaiji_dib_hook"),
    ):
        if renderer.features.get(feature):
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
    html_path = dict_out / "raw_honmon_entries.html"
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
            result = render_hc_body(body, replace(options, entry_start_offset=start))
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
        vertical=bool(getattr(args, "vertical", False)),
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
    if not profile.exact_body_html_available:
        behavior_gaps.extend(raw_summary.get("raw_honmon_named_behavior_gaps", {}).keys())
    final_html_path: Path | None = None
    final_html_source = "raw_honmon_controls"
    if profile.exact_body_html_available and rendererdb_summary is not None:
        final_html_path = _write_exact_hc_entries_html_from_rendererdb(
            rendererdb_summary,
            dict_out,
            stylesheet=raw_summary.get("raw_honmon_stylesheet"),
            vertical=bool(getattr(args, "vertical", False)),
        )
        if final_html_path is not None:
            final_html_source = "rendererdb_html"
    if final_html_path is None:
        final_html_path = dict_out / "hc_entries.html"
        shutil.copy2(Path(str(raw_summary["raw_honmon_html_path"])), final_html_path)
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
        "entry_html_path": str(final_html_path),
        "entry_html_source": final_html_source,
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
