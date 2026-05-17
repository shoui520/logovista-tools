from pathlib import Path
import argparse
import sqlite3

from logovista_tools.entries import DictionarySource
from logovista_tools.hcrender import (
    HcRenderOptions,
    _has_entry_body_sidecar,
    _prepare_hc_render_assets,
    _renderer_behavior_gaps,
    _rendererdb_args,
    _schema_backed_sidecars,
    render_hc_body,
)
from logovista_tools.hcprofiles import build_hc_behavior_profile
from logovista_tools.resources import load_image_resource_profile
from logovista_tools.windows import HcRendererClassification, PeSummary


def jis_ascii(letter: str) -> bytes:
    return b"\x1f\x04" + bytes((0x23, ord(letter))) + b"\x1f\x05"


def jis_fullwidth_ascii(text: str) -> bytes:
    return b"".join(bytes((0x23, ord(ch))) for ch in text)


def jis_text(text: str) -> bytes:
    encoded = text.encode("iso2022_jp")
    return encoded.replace(b"\x1b$B", b"").replace(b"\x1b(B", b"")


def test_hc_render_internal_link_uses_end_payload_target() -> None:
    body = b"\x1f\x42" + jis_ascii("A") + b"\x1f\x62\x00\x00\x00\x01\x00\x20"

    rendered = render_hc_body(body)

    assert 'class="lv-hc-link"' in rendered.html
    assert 'href="lvaddr://00000001/0032"' in rendered.html
    assert rendered.links[0]["target"] == {"block": 1, "offset": 32}
    assert rendered.stats["links"] == 1


def test_hc_render_colscr_media_placeholder_decodes_bcd_target() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")
    rendered = render_hc_body(b"\x1f\x4d" + payload)

    assert 'class="lv-hc-media"' in rendered.html
    assert 'data-lv-resource="colscr:00000123:0045"' in rendered.html
    assert rendered.media[0]["target"]["component"] == "COLSCR.DIC"
    assert rendered.media[0]["target"]["block"] == 123
    assert rendered.media[0]["target"]["offset"] == 45


def test_hc_render_pcmdata_audio_range_decodes_bcd_range() -> None:
    payload = bytes.fromhex("00010000000001230045000001230067")
    body = b"\x1f\x4a" + payload + jis_ascii("P") + b"\x1f\x6a"

    rendered = render_hc_body(body)

    assert 'class="lv-hc-audio"' in rendered.html
    assert "pcmdata:00000123:0045-00000123:0067" in rendered.html
    assert rendered.audio[0]["target"]["component"] == "PCMDATA.DIC"
    assert rendered.audio[0]["target"]["start_block"] == 123
    assert rendered.audio[0]["target"]["end_offset"] == 67


def test_hc_render_pcmdata_audio_uses_sound_icon_when_asset_is_available() -> None:
    payload = bytes.fromhex("00010000000001230045000001230067")
    body = b"\x1f\x4a" + payload + jis_ascii("P") + b"\x1f\x6a"

    rendered = render_hc_body(
        body,
        HcRenderOptions(image_sources={"sound": "Templates/sound.gif"}),
    )

    assert '<img src="Templates/sound.gif" class="img_mark2">' in rendered.html
    assert "pcmdata:00000123:0045-00000123:0067" in rendered.html
    assert rendered.stats["audio_images"] == 1


def test_hc_render_private_directive_suppresses_visible_text() -> None:
    body = jis_ascii("A") + b"\x1f\xe2\x00\x00" + jis_ascii("B") + b"\x1f\xe3\x00\x00" + jis_ascii("C")

    rendered = render_hc_body(body)

    assert "A" in rendered.plain
    assert "C" in rendered.plain
    assert "B" not in rendered.plain
    assert rendered.private_directives[0]["text_length"] == 1
    assert rendered.stats["private_directives"] == 1


def test_hc_render_gaiji_prefers_unicode_then_image_then_placeholder() -> None:
    rendered = render_hc_body(
        b"\xa1\xa1\xa1\xa2\xa1\xa3",
        HcRenderOptions(
            gaiji_map={"a1a1": "〓"},
            image_sources={"a1a2": "Templates/a1a2.png"},
        ),
    )

    assert "〓" in rendered.plain
    assert 'src="Templates/a1a2.png"' in rendered.html
    assert 'data-gaiji-code="a1a3"' in rendered.html
    assert rendered.stats["gaiji_unicode"] == 1
    assert rendered.stats["gaiji_image"] == 1
    assert rendered.stats["gaiji_placeholder"] == 1


def test_hc013a_inserts_exam_badge_once_per_example_block() -> None:
    body = (
        b"\x1f\x09\x00\x11"
        + jis_ascii("A")
        + b"\x1f\x09\x00\x12"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x11"
        + jis_ascii("C")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("D")
        + b"\x1f\x09\x00\x11"
        + jis_ascii("E")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="013A",
            image_sources={"exam": "Templates/exam.png"},
        ),
    )

    assert rendered.html.count('src="Templates/exam.png" class="ex_img"') == 2
    assert rendered.html.count('data-lv-section="0011"') == 3
    assert rendered.stats["section_images"] == 2


def test_hc013a_reports_missing_exam_asset_without_faking_image() -> None:
    rendered = render_hc_body(b"\x1f\x09\x00\x11" + jis_ascii("A"), HcRenderOptions(renderer_code="013A"))

    assert "exam.png" not in rendered.html
    assert "missing_section_image_exam" in rendered.named_behavior_gaps


def test_hc00c6_maps_sections_to_product_divs_and_example_badge() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x06"
        + jis_ascii("D")
        + b"\x1f\x09\x00\x07"
        + jis_ascii("E")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("J")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00C6", image_sources={"exam": "templates/exam.png"}),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<div class="yakugo" style="margin-left:2.000000em;">' in rendered.html
    assert '<img src="templates/exam.png" class="ex_img"><br>' in rendered.html
    assert '<div class="contents">' in rendered.html
    assert '<div class="exampleyakugo">' in rendered.html
    assert rendered.html.count('src="templates/exam.png" class="ex_img"') == 1
    assert rendered.stats["hc00c6_section_divs"] == 4
    assert rendered.stats["section_images"] == 1


def test_hc00c6_renders_partwaku_supab_and_template_image_markers() -> None:
    rendered = render_hc_body(
        b"\xa2\x4c" + jis_ascii("P") + b"\xa2\x4d"
        + b"\xa2\x44" + jis_ascii("B") + b"\xa2\x45"
        + b"\xa1\x22"
        + b"\xb1\x26",
        HcRenderOptions(renderer_code="00C6", image_sources={"a122": "templates/A122.png"}),
    )

    assert '<span class="partwaku"><span class="lv-hc-halfwidth">P</span></span>' in rendered.html
    assert '<sup class="supAB"><span class="lv-hc-halfwidth">B</span></sup>' not in rendered.html
    assert '<sup class="supAB">B</sup>' in rendered.html
    assert 'src="templates/A122.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_mark5"' in rendered.html
    assert '<br><hr class="line">' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc00c6_style_markers"] == 2
    assert rendered.stats["hc00c6_supab_markers"] == 2
    assert rendered.stats["hc00c6_image_markers"] == 1
    assert rendered.stats["hc00c6_rule_lines"] == 1


def test_hc00c6_closes_partwaku_when_close_marker_is_halfwidth_wrapped() -> None:
    rendered = render_hc_body(
        b"\xa2\x3c" + jis_ascii("N") + b"\x1f\x04\xa2\x3d\x1f\x05",
        HcRenderOptions(renderer_code="00C6"),
    )

    assert '<span class="partwaku"><span class="lv-hc-halfwidth">N</span></span>' in rendered.html
    assert '<span class="lv-hc-halfwidth"></span>' not in rendered.html
    assert rendered.stats["hc00c6_style_markers"] == 2
    assert rendered.stats["hc00c6_noop_markers"] == 1


def test_hc02be_maps_sections_to_ind_blocks() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01" + jis_ascii("H") + b"\x1f\x09\x00\x10" + jis_ascii("D"),
        HcRenderOptions(renderer_code="02BE"),
    )

    assert '<div class="ind_0001">' in rendered.html
    assert '<div class="ind_0010">' in rendered.html
    assert rendered.stats["hc02be_section_divs"] == 2


def test_hc02be_renders_phonetic_accent_composite_markers() -> None:
    rendered = render_hc_body(
        b"\xa2\x4f\xb1\x4f",
        HcRenderOptions(
            renderer_code="02BE",
            image_sources={"grave": "Templates/grave.png"},
        ),
    )

    assert '<span class="nowrap_half">&#x251;<img class="grave_half" src="Templates/grave.png"></span>' in rendered.html
    assert '<span class="nowrap_full">&#xe6;<img class="grave_full" src="Templates/grave.png"></span>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc02be_accent_markers"] == 2


def test_hc02be_renders_pronunciation_and_yomigana_markers() -> None:
    rendered = render_hc_body(
        b"\xb9\x28" + jis_ascii("P") + b"\xb9\x29"
        + b"\xb9\x2c" + jis_ascii("Y") + b"\xb9\x2d"
        + b"\xb9\x24\xb9\x25"
        + b"\xb9\x26" + b"\xb9\x27",
        HcRenderOptions(renderer_code="02BE"),
    )

    assert '<font class="hatsuon"><span class="lv-hc-halfwidth">P</span></font>' in rendered.html
    assert '<span class="yomigana">（<span class="lv-hc-halfwidth">Y</span>）</span>' in rendered.html
    assert "（" in rendered.html
    assert "）" in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc02be_style_markers"] == 4
    assert rendered.stats["hc02be_noop_markers"] == 2
    assert rendered.stats["hc02be_literal_markers"] == 2


def test_hc02bc_maps_sections_to_stedman_blocks() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01" + jis_ascii("H")
        + b"\x1f\x09\x00\x02" + jis_ascii("K")
        + b"\x1f\x09\x00\x08" + jis_ascii("C"),
        HcRenderOptions(renderer_code="02BC", image_sources={"fukumidashi": "Templates/fukumidashi.png"}),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<img src="Templates/fukumidashi.png" class="img_mark2">' in rendered.html
    assert '<div class="komidashi"  style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert rendered.stats["hc02bc_section_divs"] == 3


def test_hc02bc_renders_color_and_indent_marker_pairs() -> None:
    rendered = render_hc_body(
        b"\xb1\x21" + jis_ascii("B") + b"\xb1\x25"
        + b"\xb1\x34" + jis_ascii("R") + b"\xb1\x35"
        + b"\xb1\x36" + jis_ascii("D") + b"\xb1\x37"
        + b"\xb1\x3d" + jis_ascii("I") + b"\xb1\x3e"
        + b"\xb1\x3a\xb1\x3c",
        HcRenderOptions(renderer_code="02BC"),
    )

    assert '<span class="blue"><span class="lv-hc-halfwidth">B</span></span>' in rendered.html
    assert '<span style="color:#800000;"><span class="lv-hc-halfwidth">R</span></span>' in rendered.html
    assert '<span style="color:#990000;"><b><span class="lv-hc-halfwidth">D</span></b></span>' in rendered.html
    assert '<div style="margin-left:1em;"><span class="lv-hc-halfwidth">I</span></div>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc02bc_style_markers"] == 8
    assert rendered.stats["hc02bc_noop_markers"] == 1
    assert rendered.stats["hc02bc_literal_markers"] == 1


def test_hc02bc_renders_medical_composite_markers() -> None:
    rendered = render_hc_body(
        b"\xa1\x45\xa1\x47\xa1\x59\xb1\x26\xb1\x2c\xb1\x31",
        HcRenderOptions(renderer_code="02BC"),
    )

    assert "<span>Q</span>" in rendered.html
    assert "<span>V</span>" in rendered.html
    assert "<span>c</span>" in rendered.html
    assert "o<small><small>2</small></small>" in rendered.html
    assert "&#x2571;" in rendered.html
    assert "<small><small><small>N</small></small></small>" in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc02bc_composite_markers"] == 6


def test_hc012e_maps_kanji_sections_and_hitsujun_image() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x08"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x0c"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x3f"
        + jis_ascii("I")
        + b"\xb2\x36"
        + jis_ascii("J")
        + b"\xb2\x37",
        HcRenderOptions(
            renderer_code="012E",
            image_sources={"hitsujun": "templates/hitsujun.png", "b237": "Gaijitemp/B237.png"},
        ),
    )

    assert "<!-- hitsujun start -->" in rendered.html
    assert '<img src="templates/hitsujun.png" class="img_gaiji">' in rendered.html
    assert '<div class="bushu">' in rendered.html
    assert '<table class="table_itaiji_2"><tr><td><div class="Itaiji">' in rendered.html
    assert '</div></td><td><div class="honbun">' in rendered.html
    assert '</div></td></tr></table><br>' in rendered.html
    assert rendered.stats["hc012e_hitsujun_sections"] == 1
    assert rendered.stats["hc012e_table_cell_transitions"] == 1
    assert rendered.stats["hc012e_table_closures"] == 1


def test_hc012e_closes_table_sections_on_section_transition() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x07"
        + jis_ascii("O")
        + b"\x1f\x09\x00\x3f"
        + jis_ascii("I"),
        HcRenderOptions(renderer_code="012E"),
    )

    assert rendered.html.count("<table") == 2
    assert rendered.html.count("</table>") == 2
    assert rendered.html.count("<tr>") == 2
    assert rendered.html.count("</tr>") == 2
    assert rendered.html.count("<td>") == 2
    assert rendered.html.count("</td>") == 2
    assert '<table class="table_oyaji"><tr><td><div class="Oyaji">' in rendered.html
    assert '</div></td></tr></table><table class="table_itaiji_2"><tr><td><div class="Itaiji">' in rendered.html


def test_hc012e_keeps_common_kun_section_normal_sized() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x27" + jis_ascii("K"),
        HcRenderOptions(renderer_code="012E"),
    )

    assert '<div class="honbun" style="margin-left:0.000000em;">' in rendered.html
    assert "table_itaiji" not in rendered.html
    assert 'class="Itaiji"' not in rendered.html


def test_hc012e_renders_color_size_direct_image_and_literal_markers() -> None:
    rendered = render_hc_body(
        b"\xb2\x39" + jis_ascii("R") + b"\xb2\x42"
        + b"\xb2\x41" + jis_ascii("S") + b"\xb2\x42"
        + b"\xb1\x36"
        + b"\xb1\x2b"
        + b"\xa1\x49",
        HcRenderOptions(renderer_code="012E", image_sources={"b136": "Gaijitemp/B136.png", "b12b": "Gaijitemp/B12B.png"}),
    )

    assert '<span style="color:#FF0000;"><span class="lv-hc-halfwidth">R</span></span>' in rendered.html
    assert '<span class="sizedown"><span class="lv-hc-halfwidth">S</span></span>' in rendered.html
    assert 'src="Gaijitemp/B136.png"' in rendered.html
    assert 'class="lv-hc-gaiji hatsuon"' in rendered.html
    assert "Gaijitemp/B12B.png" not in rendered.html
    assert "&nbsp;&nbsp;" in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc012e_direct_image_markers"] == 1
    assert rendered.stats["hc012e_noop_markers"] == 1
    assert rendered.stats["hc012e_literal_markers"] == 1


def test_hc012e_treats_1f6d_as_nonprinting_renderer_control() -> None:
    rendered = render_hc_body(jis_ascii("A") + b"\x1f\x6d" + jis_ascii("B"), HcRenderOptions(renderer_code="012E"))

    assert rendered.plain == "AB"
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc012e_nonprinting_controls"] == 1


def test_hc012d_maps_midashi_honbun_and_yorei_sections() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + b"\xb8\x7c"
        + jis_ascii("B")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + jis_ascii("Y")
        + b"\x1f\x0a",
        HcRenderOptions(renderer_code="012D"),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<span class="lv-hc-halfwidth">H</span></div>' in rendered.html
    assert '<div class="honbun_start">' in rendered.html
    assert '<div class="yorei">' in rendered.html
    assert '<span class="lv-hc-halfwidth">B</span></div>' in rendered.html
    assert '<span class="lv-hc-halfwidth">Y</span></div>' in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert rendered.stats["hc012d_section_blocks"] == 2
    assert rendered.stats["hc012d_noop_markers"] == 1


def test_hc009d_maps_lineinfo_sections_and_suppresses_generic_heading() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_ascii("B")
        + b"\x1f\x0a",
        HcRenderOptions(renderer_code="009D"),
    )

    assert '<div class="lineinfo1">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo3">' in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc009d_section_blocks"] == 2
    assert rendered.stats["hc009d_nonprinting_controls"] == 1


def test_hc009d_maps_kakomi_section_markers() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x08"
        + b"\xb1\x44"
        + b"\x1f\x0a"
        + jis_ascii("K")
        + b"\xb1\x45",
        HcRenderOptions(renderer_code="009D", image_sources={"b144": "Templates/B144.gif"}),
    )

    assert '<div class="komattaKakomi">' in rendered.html
    assert '<img src="Templates/B144.gif" class="img_kakomi">' in rendered.html
    assert '<div class="lineinfo8">' in rendered.html
    assert '<span class="lv-hc-halfwidth">K</span></div>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc009d_section_blocks"] == 1
    assert rendered.stats["hc009d_noop_markers"] == 1
    assert rendered.stats["hc009d_kakomi_markers"] == 1


def test_hc009d_maps_literals_breaks_checkbox_and_line_links() -> None:
    body = (
        b"\xb1\x21\xb1\x25\xb1\x30"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="009D"))

    assert "☞" in rendered.html
    assert '<input type="checkbox" name="chk" value="chk">' in rendered.html
    assert "<br>" in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc009d_literal_markers"] == 1
    assert rendered.stats["hc009d_html_markers"] == 1
    assert rendered.stats["hc009d_break_markers"] == 1


def test_hc012d_honbun_user_transition_does_not_emit_empty_block_before_section() -> None:
    section_follows = render_hc_body(
        b"\x1f\x41\x01\x00" + jis_ascii("H") + b"\x1f\x61" + b"\x1f\x09\x00\x02" + jis_ascii("B"),
        HcRenderOptions(renderer_code="012D"),
    )
    text_follows = render_hc_body(
        b"\x1f\x41\x01\x00" + jis_ascii("H") + b"\x1f\x61" + jis_ascii("B"),
        HcRenderOptions(renderer_code="012D"),
    )

    assert '<div class="honbun_user"></div>' not in section_follows.html
    assert '<div class="honbun_user"><span class="lv-hc-halfwidth">B</span></div>' in text_follows.html


def test_hc012d_uses_line_link_and_link_k_marker() -> None:
    body = b"\x22\x2a" + b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="012D", image_sources={"link_k": "Templates/link_k.png"}))

    assert '<img class="lv-hc-gaiji gaiji" src="Templates/link_k.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert rendered.stats["hc012d_inline_image_markers"] == 1


def test_hc012d_maps_spacing_markers_and_gaiji_class() -> None:
    rendered = render_hc_body(
        b"\xa1\x34\xa1\x37\xb1\x21",
        HcRenderOptions(renderer_code="012D", image_sources={"b121": "Templates/b121.png"}),
    )

    assert '<span class="mini_space"> </span>' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image gaiji" src="Templates/b121.png"' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.plain == ""
    assert rendered.stats["hc012d_literal_markers"] == 2


def test_hc0145_maps_decimal_sections_without_generic_heading() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_ascii("B")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_ascii("C")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + jis_ascii("D")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x20"
        + jis_ascii("E")
        + b"\x1f\x0a",
        HcRenderOptions(renderer_code="0145"),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<span class="lv-hc-halfwidth">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert '<div class="honbun" style="text-indent:-1.0em;margin-left:2.000000em;">' in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0145_section_blocks"] == 5
    assert rendered.stats["hc0145_nonprinting_controls"] == 1


def test_hc0145_renders_literal_style_and_noop_markers() -> None:
    rendered = render_hc_body(
        b"\xa9\x21\xa9\x22\xa9\x23\xa9\x24\xb9\x2a\xb9\x2b\xb9\x34\xb9\x36"
        + b"\xb9\x24"
        + jis_ascii("B")
        + b"\xb9\x25"
        + b"\xb9\x32\xb9\x2d",
        HcRenderOptions(renderer_code="0145"),
    )

    assert "≪" in rendered.html
    assert "≫" in rendered.html
    assert "<sup>*</sup>" in rendered.html
    assert "<sup>||</sup>" in rendered.html
    assert "（" in rendered.html
    assert "）" in rendered.html
    assert "[" in rendered.html
    assert "&nbsp;" in rendered.html
    assert '<b><i><span class="lv-hc-halfwidth">B</span></i></b>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0145_literal_markers"] == 8
    assert rendered.stats["hc0145_style_markers"] == 2
    assert rendered.stats["hc0145_noop_markers"] == 2


def test_hc0145_uses_line_links_and_img_gaiji_class() -> None:
    body = b"\xb9\x21" + b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0145", image_sources={"b921": "Templates/b921.png"}))

    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/b921.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert rendered.stats["gaiji_image"] == 1


def test_hc0144_maps_sections_and_consumes_heading_state() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("P")
        + b"\x1f\x09\x00\x04"
        + jis_ascii("D")
        + b"\x1f\x09\x01\x06"
        + jis_ascii("S")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("C")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0144"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="honbun" style="text-indent:-1.0em;margin-left:2.000000em;">' in rendered.html
    assert '<div class="komidashi"  style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0144_nonprinting_controls"] == 1


def test_hc0144_marker_subset_matches_decompiled_branch_table() -> None:
    body = (
        b"\xb9\x21"
        + b"\xb9\x24"
        + jis_ascii("B")
        + b"\xb9\x25"
        + b"\xa9\x21"
        + b"\xa9\x22"
        + b"\xb9\x2a"
        + b"\xb9\x2b"
        + b"\xb9\x34"
        + b"\xb9\x36"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0144", image_sources={"b921": "Templates/b921.png"}))

    assert "Templates/b921.png" not in rendered.html
    assert '<b><i><span class="lv-hc-halfwidth">B</span></i></b>' in rendered.html
    assert "≪" in rendered.html
    assert "≫" in rendered.html
    assert "（" in rendered.html
    assert "）" in rendered.html
    assert "[" in rendered.html
    assert "&nbsp;" in rendered.html
    assert rendered.stats["hc0144_style_markers"] == 2
    assert rendered.stats["hc0144_literal_markers"] == 6
    assert rendered.stats["hc0144_noop_markers"] == 1


def test_hc0144_internal_links_use_line_link_class() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0144"))

    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html


def test_hc03e8_maps_sections_and_consumes_heading_state() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("P")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("C")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="03E8"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert rendered.stats["hc03e8_section_blocks"] == 3
    assert rendered.stats["hc03e8_nonprinting_controls"] == 1


def test_hc03e8_marker_subset_matches_decompiled_branch_table() -> None:
    body = (
        b"\xb9\x21"
        + b"\xb9\x39"
        + b"\xb9\x24"
        + jis_ascii("B")
        + b"\xb9\x25"
        + b"\xa9\x21"
        + b"\xa9\x22"
        + b"\xb9\x2a"
        + b"\xb9\x2b"
        + b"\xb9\x34"
        + b"\xb9\x36"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="03E8", image_sources={"b921": "Templates/b921.png"}))

    assert "Templates/b921.png" not in rendered.html
    assert '<b><i><span class="lv-hc-halfwidth">B</span></i></b>' in rendered.html
    assert "≪" in rendered.html
    assert "≫" in rendered.html
    assert "（" in rendered.html
    assert "）" in rendered.html
    assert "[]" in rendered.html
    assert "]&nbsp;" in rendered.html
    assert rendered.stats["hc03e8_style_markers"] == 2
    assert rendered.stats["hc03e8_literal_markers"] == 6
    assert rendered.stats["hc03e8_noop_markers"] == 2


def test_hc03e8_internal_links_use_line_link_class() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="03E8"))

    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html


def test_hc0141_maps_sections_and_consumes_heading_state() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("P")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("C")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0141"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0141_section_blocks"] == 3
    assert rendered.stats["hc0141_nonprinting_controls"] == 1


def test_hc0141_marker_subset_matches_decompiled_branch_table() -> None:
    body = (
        b"\xb9\x24"
        + jis_ascii("B")
        + b"\xb9\x25"
        + b"\xa9\x21"
        + b"\xa9\x22"
        + b"\xb9\x2a"
        + b"\xb9\x2b"
        + b"\xb9\x34"
        + b"\xb9\x36"
        + b"\xb9\x26"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0141", image_sources={"b926": "Templates/b926.png"}))

    assert "Templates/b926.png" not in rendered.html
    assert '<b><i><span class="lv-hc-halfwidth">B</span></i></b>' in rendered.html
    assert "≪" in rendered.html
    assert "≫" in rendered.html
    assert "（" in rendered.html
    assert "）" in rendered.html
    assert "[]" in rendered.html
    assert "]&nbsp;" in rendered.html
    assert rendered.stats["hc0141_style_markers"] == 2
    assert rendered.stats["hc0141_literal_markers"] == 6
    assert rendered.stats["hc0141_noop_markers"] == 1


def test_hc0141_internal_links_use_line_link_class_and_img_gaiji() -> None:
    body = b"\xa9\x25" + b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0141", image_sources={"a925": "Templates/a925.png"}))

    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html


def test_hc0190_applies_html_template_section_placeholders() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii("T")
        + b"\xb1\x21"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_ascii("B")
        + b"\x1f\x0a"
    )
    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0190",
            html_templates={"b121": "<body><h1><!--&IND0001;--></h1><p><!--&IND0004;--></p><!--&IND0099;--></body>"},
        ),
    )

    assert '<h1><span class="lv-hc-halfwidth">T</span></h1>' in rendered.html
    assert '<p><span class="lv-hc-halfwidth">B</span></p>' in rendered.html
    assert "IND0001" not in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0190_template_markers"] == 1
    assert rendered.stats["hc0190_templates_applied"] == 1
    assert rendered.stats["hc0190_template_placeholders_filled"] == 2
    assert rendered.stats["hc0190_template_placeholders_empty"] == 1


def test_hc009c_maps_sections_links_private_images_and_marker_images() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x01"
        + jis_fullwidth_ascii("TITLE")
        + b"\x1f\x61"
        + b"\xb1\x48"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\xb1\x39"
        + b"\x1f\xe2\x00\x07"
        + jis_text("ＩＭＧ：Ｉ７１０４２１００．ＰＮＧ")
        + b"\x1f\xe3"
        + b"\x1f\x42"
        + jis_fullwidth_ascii("A")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="009C",
            image_sources={
                "b148m": "Templates/B148m.gif",
                "50042100": "images/50042100.jpg",
                "hc009c_thumb_50042100": "images_thumb/50042100.png",
            },
        ),
    )

    assert '<div class="midashi">ＴＩＴＬＥ<img src="Templates/B148m.gif" class="img_season" alt="b148"></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:3em">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert '<a class="hc009c-image-link" href="images/50042100.jpg">' in rendered.html
    assert '<img src="images_thumb/50042100.png" class="img_button" alt="50042100">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert 'data-gaiji-code="b139"' not in rendered.html
    assert rendered.stats["hc009c_section_blocks"] == 2
    assert rendered.stats["hc009c_private_images"] == 1
    assert rendered.stats["hc009c_noop_markers"] == 1
    assert rendered.stats["hc009c_season_image_markers"] == 1


def test_hc009c_table_marker_outputs_balanced_product_table() -> None:
    body = b"\x1f\x09\x00\x04" + b"\xb1\x2a" + jis_fullwidth_ascii("TEXT") + b"\x1f\x0a"

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="009C", image_sources={"b12a": "Templates/B12A.gif"}),
    )

    assert '<table class="feature-table">' in rendered.html
    assert '<td class="subtitle"><img src="Templates/B12A.gif" class="img_mark2" alt="b12a"></td>' in rendered.html
    assert '<td class="honbun">ＴＥＸＴ</td>' in rendered.html
    assert rendered.html.count("<table") == rendered.html.count("</table>")
    assert rendered.html.count("<td") == rendered.html.count("</td>")
    assert rendered.stats["hc009c_table_markers"] == 1


def test_hc009c_asset_preparation_copies_thumbnail_and_icon_dirs(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_SESGRASS"
    package.mkdir()
    idx = package / "SESGRASS.IDX"
    idx.write_bytes(b"")
    honmon = package / "HONMON.DIC"
    honmon.write_bytes(b"")
    for dirname, filename in (
        ("images_thumb", "50042100.png"),
        ("images_icon", "71042100.png"),
        ("images_icon_hanrei", "71042100.png"),
    ):
        directory = package / dirname
        directory.mkdir()
        (directory / filename).write_bytes(dirname.encode("ascii"))
    source = DictionarySource(
        dict_id="SESGRASS",
        idx=idx,
        title="SESGRASS",
        honmon=honmon,
        honmon_start_block=0,
        gaiji_map={},
        image_sources={"50042100": "images/50042100.jpg"},
    )
    renderer = HcRendererClassification(
        path=package / "HC009C.dll",
        code="009C",
        expected_numeric_index="0000009C.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={},
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    image_sources, _, _, copied = _prepare_hc_render_assets(source, out_dir, renderer)

    assert image_sources["hc009c_thumb_50042100"] == "images_thumb/50042100.png"
    assert image_sources["hc009c_icon_71042100"] == "images_icon/71042100.png"
    assert image_sources["hc009c_hanrei_71042100"] == "images_icon_hanrei/71042100.png"
    assert (out_dir / "images_thumb" / "50042100.png").read_bytes() == b"images_thumb"
    assert (out_dir / "images_icon" / "71042100.png").read_bytes() == b"images_icon"
    assert (out_dir / "images_icon_hanrei" / "71042100.png").read_bytes() == b"images_icon_hanrei"
    assert copied >= 3


def test_hc013d_maps_sections_to_drug_layout_classes() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x04"
        + jis_ascii("G")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x06"
        + jis_ascii("M")
        + b"\x1f\x09\x00\x10"
        + jis_ascii("P")
        + b"\x1f\x09\x00\x11"
        + jis_ascii("I")
        + b"\x1f\x09\x00\x41"
        + jis_ascii("S")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="title3">' in rendered.html
    assert '<div class="medblk">' in rendered.html
    assert '<span class="med">' in rendered.html
    assert '<div class="medprice">' in rendered.html
    assert '<div class="medimage">' in rendered.html
    assert '<div class="indent41">' in rendered.html
    assert rendered.stats["hc013d_section_blocks"] == 6


def test_hc013d_internal_links_use_line_link_class_and_1f6d_is_nonprinting() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30" + b"\x1f\x6d\x00\x00"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc013d_nonprinting_controls"] == 1


def test_hc013d_renders_template_gaiji_with_product_image_class() -> None:
    rendered = render_hc_body(
        b"\xb1\x29",
        HcRenderOptions(renderer_code="013D", image_sources={"b129": "Templates/b129.png"}),
    )

    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert 'src="Templates/b129.png"' in rendered.html


def test_hc013d_decodes_jis_product_template_sequences() -> None:
    body = (
        bytes.fromhex("215a3d69215b")
        + bytes.fromhex("215a403d3a5e")
        + bytes.fromhex("236d234c")
        + bytes.fromhex("2364234c")
        + bytes.fromhex("2331234c")
        + bytes.fromhex("2175216f21632164")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="013D",
            image_sources={"syohatsu": "Templates/syohatsu.png", "midashi1": "Templates/midashi1.png"},
        ),
    )

    assert '<img src="Templates/syohatsu.png" class="img_gaiji">' in rendered.html
    assert '<div class="seizaijouhou"><div class="SubTitle">' in rendered.html
    assert '<img src="Templates/midashi1.png" class="img_midashi">' in rendered.html
    assert "m&#x2113;" in rendered.html
    assert "d&#x2113;" in rendered.html
    assert "１ℓ" in rendered.plain
    assert "&amp;" in rendered.html
    assert "&yen;" in rendered.html
    assert "&lt;" in rendered.html
    assert "&gt;" in rendered.html
    assert rendered.stats["hc013d_jis_template_markers"] == 9


def test_hc02c2_maps_section_icons_and_moji_down_blocks() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x07"
        + jis_ascii("A")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x09"
        + jis_ascii("C")
        + b"\x1f\x09\x00\x0a"
        + jis_ascii("D"),
        HcRenderOptions(renderer_code="02C2"),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<img src="1.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="2.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="3.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="4.png" class="img_icon"/><br>' in rendered.html
    assert '<p class="moji-down">' in rendered.html
    assert '<span class="lv-hc-halfwidth">A</span></p>' in rendered.html
    assert rendered.stats["hc02c2_section_blocks"] == 5
    assert rendered.stats["hc02c2_section_icons"] == 4


def test_hc02c2_uses_template_gaiji_images_and_suppresses_heading_control() -> None:
    rendered = render_hc_body(
        b"\x1f\x41" + jis_ascii("H") + b"\x1f\x61" + b"\xb1\x3e",
        HcRenderOptions(renderer_code="02C2", image_sources={"b13e": "Templates/B13E.png"}),
    )

    assert 'class="lv-hc-heading"' not in rendered.html
    assert 'src="Templates/B13E.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc02c2_nonprinting_controls"] == 1
    assert rendered.stats["hc02c2_template_image_markers"] == 1


def test_hc02c2_line_links_use_product_class() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02C2"))

    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html


def test_hc02c5_wraps_headings_and_decoded_sections() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x0b"
        + jis_ascii("I")
        + b"\x1f\x09\x00\x2e"
        + jis_ascii("S")
        + b"\x1f\x09\x00\x3a"
        + jis_ascii("R"),
        HcRenderOptions(renderer_code="02C5"),
    )

    assert '<div class="midashi"><!-- INDEX_MENU -->' in rendered.html
    assert '<span class="hankaku">H</span></div>' in rendered.html
    assert '<h1 class="indent11"><span class="hankakuMidashi">' in rendered.html
    assert '<br><div class="Seiku">' in rendered.html
    assert '<h1 class="indent58">' in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc02c5_heading_blocks"] == 1
    assert rendered.stats["hc02c5_section_blocks"] == 3


def test_hc02c5_uses_product_link_and_audio_templates() -> None:
    payload = bytes.fromhex("00010000000001230045000001230067")
    body = (
        b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x4a"
        + payload
        + jis_ascii("P")
        + b"\x1f\x6a"
        + b"\x1f\x6d\x00\x00"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="02C5", image_sources={"dummy": "Templates/dummy.GIF", "sound": "Templates/sound.png"}),
    )

    assert 'class="lv-hc-link lLink"' in rendered.html
    assert 'class="lv-hc-audio lLink"' in rendered.html
    assert '<img src="Templates/dummy.GIF" class="im">' in rendered.html
    assert "pcmdata:00000123:0045-00000123:0067" in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc02c5_nonprinting_controls"] == 1


def test_hc02c5_renders_marker_numerals_letters_and_hin_images() -> None:
    rendered = render_hc_body(
        bytes.fromhex("b146 b44d b353 b423 b347 b273"),
        HcRenderOptions(
            renderer_code="02C5",
            image_sources={"b347": "HANREI/img/b347.png", "b273": "Templates/B273.png"},
        ),
    )

    assert "<strong>1</strong>" in rendered.html
    assert "<strong>18</strong>" in rendered.html
    assert "<small>a</small>" in rendered.html
    assert "<small>l</small>" in rendered.html
    assert 'class="lv-hc-gaiji img_hin"' in rendered.html
    assert 'src="HANREI/img/b347.png"' in rendered.html
    assert 'src="Templates/B273.png"' in rendered.html
    assert rendered.stats["hc02c5_strong_markers"] == 2
    assert rendered.stats["hc02c5_small_markers"] == 2
    assert rendered.stats["hc02c5_img_hin_markers"] == 2


def test_hc0151_maps_heading_contents_sections_and_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x17"
        + jis_ascii("B")
        + b"\x1f\x0a"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0151"))

    assert '<div class="midashi"><span class="hankaku">H</span></div><div class="contents">' in rendered.html
    assert '<div class="indent23"><span class="hankaku">B</span></div>' in rendered.html
    assert 'class="lv-hc-link Link"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0151_heading_blocks"] == 1
    assert rendered.stats["hc0151_contents_blocks"] == 1
    assert rendered.stats["hc0151_section_blocks"] == 1
    assert rendered.stats["hc0151_nonprinting_controls"] == 1


def test_hc0151_table_sections_and_small_markers_are_balanced() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x20"
        + jis_ascii("A")
        + b"\xb1\x59"
        + jis_ascii("B")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x22"
        + jis_ascii("C")
        + b"\xb1\x59"
        + jis_ascii("D")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x87"
        + b"\xb1\x56"
        + jis_ascii("S")
        + b"\xb1\x57",
        HcRenderOptions(renderer_code="0151"),
    )

    assert "<table><tr><th>" in rendered.html
    assert "</th><th>" in rendered.html
    assert "</th></tr>" in rendered.html
    assert "<tr><td>" in rendered.html
    assert "</td><td>" in rendered.html
    assert "</td></tr>" in rendered.html
    assert "</table><br>" in rendered.html
    assert "<small><small><small>" in rendered.html
    assert "</small></small></small>" in rendered.html
    assert rendered.html.count("<table") == rendered.html.count("</table>")
    assert rendered.html.count("<th") == rendered.html.count("</th>")
    assert rendered.html.count("<td") == rendered.html.count("</td>")
    assert rendered.stats["hc0151_section_blocks"] == 3
    assert rendered.stats["hc0151_table_cell_markers"] == 2
    assert rendered.stats["hc0151_small_markers"] == 2


def test_hc0065_wraps_midashi_and_contents_body() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + jis_ascii("A")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("B")
        + b"\x1f\x41\x01\x00"
        + jis_ascii("C")
        + b"\x1f\x61",
        HcRenderOptions(renderer_code="0065"),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<span class="lv-hc-halfwidth">A</span>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<span class="lv-hc-halfwidth">C</span></div>' in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert rendered.stats["hc0065_midashi_blocks"] == 1
    assert rendered.stats["hc0065_contents_body_blocks"] == 1


def test_hc0065_renders_grammar_label_and_template_image_markers() -> None:
    rendered = render_hc_body(
        b"\xa1\x74\xa4\x30\xa4\x31\xa4\x32\xa4\x33\xa2\x51\xa2\x53",
        HcRenderOptions(
            renderer_code="0065",
            image_sources={"a251": "Templates/a251.png", "a253": "Templates/a253.png"},
        ),
    )

    assert "BcuSD" in rendered.plain
    assert 'src="Templates/a251.png"' in rendered.html
    assert 'src="Templates/a253.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0065_literal_markers"] == 5
    assert rendered.stats["hc0065_template_image_markers"] == 2


def test_hc0065_internal_links_use_product_class() -> None:
    body = b"\x1f\x42" + jis_ascii("G") + b"\x1f\x62\x00\x00\x00\x03\x00\x40"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0065"))

    assert 'class="lv-hc-link lLink"' in rendered.html
    assert 'href="lvaddr://00000003/0064"' in rendered.html


def test_hc0158_renders_rank_marker_stars_without_gaiji_placeholder() -> None:
    rendered = render_hc_body(
        b"\xb3\x55" + jis_ascii("A") + b"\xb3\x54",
        HcRenderOptions(renderer_code="0158"),
    )

    assert '<span class="rank1"><sup>&#x2605;&#x2605;</sup>' in rendered.html
    assert "★★A" in rendered.plain
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0158_style_markers"] == 2


def test_hc0158_renders_part_of_speech_and_conjugation_markers() -> None:
    rendered = render_hc_body(
        b"\xb3\x68" + jis_ascii("H") + b"\xb3\x69" + b"\xb3\x6c" + jis_ascii("K") + b"\xb3\x6d",
        HcRenderOptions(renderer_code="0158"),
    )

    assert '<span class="hinshi"><span class="lv-hc-halfwidth">H</span></span>' in rendered.html
    assert '<span class="katsuyou"><span class="lv-hc-halfwidth">K</span></span>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html


def test_hc0158_renders_label_and_red_text_markers() -> None:
    rendered = render_hc_body(
        b"\xb3\x79\x4c\x75\xb3\x7a" + b"\xb3\x75" + jis_ascii("R") + b"\xb3\x76",
        HcRenderOptions(renderer_code="0158"),
    )

    assert '<br><span class="waku_red red">訳</span>' in rendered.html
    assert '<span class="red"><span class="lv-hc-halfwidth">R</span></span>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html


def test_hc0158_keeps_numbered_svg_gaiji_as_image_resource() -> None:
    rendered = render_hc_body(
        b"\xb2\x53",
        HcRenderOptions(renderer_code="0158", image_sources={"b253": "Templates/B253.svg"}),
    )

    assert 'src="Templates/B253.svg"' in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-gaiji-image gaiji"' in rendered.html
    assert rendered.stats["gaiji_image"] == 1


def test_hc0158_renders_pcm_audio_as_sound_icon_link() -> None:
    payload = bytes.fromhex("00010000000001230045000001230067")
    body = b"\x1f\x4a" + payload + jis_ascii("P") + b"\x1f\x6a"

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0158", image_sources={"sound": "Templates/sound.png"}),
    )

    assert '<img src="Templates/sound.png" class="img_mark2">' in rendered.html
    assert "pcmdata:00000123:0045-00000123:0067" in rendered.html
    assert rendered.stats["audio_images"] == 1


def test_hc0157_renders_dll_backed_inline_style_markers() -> None:
    rendered = render_hc_body(
        b"\xb1\x5c" + jis_ascii("P") + b"\xb1\x5d"
        + b"\xb1\x78" + jis_ascii("N") + b"\xb1\x79"
        + b"\xb2\x40" + jis_ascii("S") + b"\xb2\x41",
        HcRenderOptions(renderer_code="0157"),
    )

    assert '<span class="hinshi_ej"><span class="lv-hc-halfwidth">P</span></span>' in rendered.html
    assert '<span class="bunshi"><sup><span class="lv-hc-halfwidth">N</span></sup></span>' in rendered.html
    assert '<sup><span class="jousu"><span class="lv-hc-halfwidth">S</span></span></sup>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0157_style_markers"] == 6


def test_hc0157_renders_self_glyph_markers_inside_profile_spans() -> None:
    rendered = render_hc_body(
        b"\xb1\x57" + jis_ascii("A") + b"\xb2\x2a"
        + b"\xb1\x72" + jis_ascii("B") + b"\xb1\x73",
        HcRenderOptions(
            renderer_code="0157",
            gaiji_map={"b172": "｟", "b173": "｠"},
            image_sources={"b157": "Templates/B157.png"},
        ),
    )

    assert '<span class="midashi_word red"><img class="lv-hc-gaiji lv-hc-gaiji-image" src="Templates/B157.png"' in rendered.html
    assert '<span class="lv-hc-halfwidth">A</span></span>' in rendered.html
    assert '<span class="shiyougroup">｟<span class="lv-hc-halfwidth">B</span>｠</span>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html


def test_hc0157_wraps_red_circled_number_gaiji_without_losing_unicode() -> None:
    rendered = render_hc_body(
        b"\xb2\x2d",
        HcRenderOptions(renderer_code="0157", gaiji_map={"b22d": "①"}),
    )

    assert '<span class="red">①</span>' in rendered.html
    assert rendered.plain == "①"
    assert rendered.stats["gaiji_unicode"] == 1


def test_hc0157_renders_accent_and_sound_icon_templates() -> None:
    payload = bytes.fromhex("00010000000001230045000001230067")
    body = b"\xa1\x4e" + b"\x1f\x4a" + payload + jis_ascii("P") + b"\x1f\x6a"

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0157", image_sources={"sound": "Templates/sound.png"}),
    )

    assert '<span class="accent">&#x0301;</span>' in rendered.html
    assert '<img src="Templates/sound.png" class="img_mark2">' in rendered.html
    assert "pcmdata:00000123:0045-00000123:0067" in rendered.html
    assert rendered.stats["audio_images"] == 1


def test_hc0157_treats_1f12_1f13_as_noop_controls() -> None:
    rendered = render_hc_body(b"\x1f\x12" + jis_ascii("A") + b"\x1f\x13", HcRenderOptions(renderer_code="0157"))

    assert "<em>" not in rendered.html
    assert rendered.plain == "A"
    assert rendered.stats["hc0157_noop_controls"] == 2
    assert "unknown_control_1f12" not in rendered.named_behavior_gaps


def test_hc0146_renders_color_font_markers_without_gaiji_placeholders() -> None:
    rendered = render_hc_body(
        b"\xb2\x32" + jis_ascii("A") + b"\xb2\x33",
        HcRenderOptions(renderer_code="0146"),
    )

    assert '<font class="color_font"><span class="lv-hc-halfwidth">A</span></font>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0146_style_markers"] == 2


def test_hc0146_renders_dll_backed_image_marker_classes() -> None:
    rendered = render_hc_body(
        b"\xb1\x57\xb2\x5a\xb3\x57",
        HcRenderOptions(
            renderer_code="0146",
            image_sources={
                "b157": "Templates/b157_M.png",
                "b25a": "Templates/b25a.png",
                "b357": "Templates/b357.png",
            },
        ),
    )

    assert 'src="Templates/b157_M.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_mark4"' in rendered.html
    assert 'src="Templates/b25a.png"' in rendered.html
    assert 'class="lv-hc-gaiji gaiji_icon"' in rendered.html
    assert 'src="Templates/b357.png"' in rendered.html
    assert 'class="lv-hc-gaiji gaiji_full"' in rendered.html
    assert rendered.stats["hc0146_image_markers"] == 3
    assert rendered.stats["gaiji_image"] == 3


def test_hc0146_treats_template_selector_markers_as_nonprinting() -> None:
    rendered = render_hc_body(
        b"\xb4\x4f" + jis_ascii("A") + b"\xb4\x51",
        HcRenderOptions(renderer_code="0146", image_sources={"b44f": "Templates/b44f.png"}),
    )

    assert "Templates/b44f.png" not in rendered.html
    assert 'data-gaiji-code="b451"' not in rendered.html
    assert rendered.plain == "A"
    assert rendered.stats["hc0146_noop_markers"] == 2


def test_hc0146_renders_abbreviation_marker_as_literal_text() -> None:
    rendered = render_hc_body(b"\xb2\x40" + jis_ascii("A"), HcRenderOptions(renderer_code="0146"))

    assert "略：" in rendered.html
    assert rendered.plain == "略：A"
    assert rendered.stats["hc0146_literal_markers"] == 1


def test_hc_render_assets_copy_images_and_normalise_product_css(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_TEST"
    templates = package / "Templates"
    templates.mkdir(parents=True)
    idx = package / "TEST.IDX"
    honmon = package / "HONMON.DIC"
    idx.write_bytes(b"")
    honmon.write_bytes(b"")
    (templates / "00000146.css").write_text(".midashi{font-size:$midashi-font-size$}.x{color:$body-color$}", encoding="utf-8")
    (templates / "b157_M.png").write_bytes(b"PNGDATA")
    source = DictionarySource(
        dict_id="TEST",
        idx=idx,
        title="test",
        honmon=honmon,
        honmon_start_block=1,
        gaiji_map={},
        image_sources={"b157": "Templates/b157_M.png"},
    )
    renderer = HcRendererClassification(
        path=package / "HC0146.dll",
        code="0146",
        expected_numeric_index="00000146.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={},
    )

    image_sources, html_templates, stylesheet, copied = _prepare_hc_render_assets(source, tmp_path / "out", renderer)

    assert image_sources == {"b157": "Templates/b157_M.png"}
    assert html_templates == {}
    assert stylesheet == "hc-renderer.css"
    assert (tmp_path / "out" / "Templates" / "b157_M.png").read_bytes() == b"PNGDATA"
    css = (tmp_path / "out" / "hc-renderer.css").read_text(encoding="utf-8")
    assert ".lv-hc-render" in css
    assert "$midashi-font-size$" not in css
    assert "1.35em" in css
    assert "#111111" in css
    assert copied == 2


def test_image_resource_profile_discovers_gaijitemp_assets(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_TEST"
    gaijitemp = package / "Gaijitemp"
    gaijitemp.mkdir(parents=True)
    idx = package / "TEST.IDX"
    idx.write_bytes(b"")
    (gaijitemp / "B123.png").write_bytes(b"PNG")
    (gaijitemp / "B123_V.png").write_bytes(b"PNGV")

    profile = load_image_resource_profile(idx)

    assert "b123" in profile.resources
    assert "b123_v" in profile.resources
    assert "b123" in profile.gaiji_image_keys


def test_hc_renderer_product_hooks_are_named_gaps() -> None:
    row = HcRendererClassification(
        path=Path("HC0001.dll"),
        code="0001",
        expected_numeric_index="00000001.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"panel_hooks": True, "sql_hooks": True, "plugin_hooks": True},
    )

    assert _renderer_behavior_gaps(row) == ["panel_hooks", "plugin_hooks", "sql_search_or_helper_hooks"]


def test_branch_subset_profiles_do_not_claim_visual_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0146.dll"),
        code="0146",
        expected_numeric_index="00000146.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={},
    )

    profile = build_hc_behavior_profile(row)

    assert profile.exact_hc_parity is False
    assert "visual_parity_unverified" in profile.named_gaps
    assert any("visual parity" in note for note in profile.notes)
    assert any(hook.status == "branch_subset_implemented" for hook in profile.hook_behaviors)


def test_hc02c2_profile_records_section_icon_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02C2.dll"),
        code="02C2",
        expected_numeric_index="000002C2.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"panel_hooks": True, "headword_modifier": True},
    )

    profile = build_hc_behavior_profile(row)
    data = profile.as_dict()

    assert "HC02C2_section_icons_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "panel_lifecycle" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0065_profile_records_midashi_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0065.dll"),
        code="0065",
        expected_numeric_index="00000065.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0065_midashi_contents_and_grammar_labels" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "sql_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc009d_profile_records_kakomi_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC009D.dll"),
        code="009D",
        expected_numeric_index="0000009D.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC009D_section_and_kakomi_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]


def test_hc012d_profile_records_section_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC012D.dll"),
        code="012D",
        expected_numeric_index="0000012D.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC012D_section_and_inline_image_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "sql_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0145_profile_records_section_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0145.dll"),
        code="0145",
        expected_numeric_index="00000145.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"custom_gaiji_dib": True, "sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0145_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0144_profile_records_section_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0144.dll"),
        code="0144",
        expected_numeric_index="00000144.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"custom_gaiji_dib": True, "sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0144_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc03e8_profile_records_section_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC03E8.dll"),
        code="03E8",
        expected_numeric_index="000003E8.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"custom_gaiji_dib": True, "sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC03E8_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0141_profile_records_section_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0141.dll"),
        code="0141",
        expected_numeric_index="00000141.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"custom_gaiji_dib": True, "sql_hooks": True, "headword_modifier": True, "dictionary_original_search": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0141_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "dictionary_original_search_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0190_profile_records_template_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0190.dll"),
        code="0190",
        expected_numeric_index="00000190.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=("HTMLs/b121.html", "HTMLs/b122.html"),
        sql_snippets=(),
        image_templates=(),
        features={},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0190_html_template_section_substitution" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False


def test_hc009c_profile_records_section_image_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC009C.dll"),
        code="009C",
        expected_numeric_index="0000009C.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("Templates/B122.gif",),
        features={"custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC009C_section_image_index_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]


def test_hc02c5_profile_records_section_marker_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02C5.dll"),
        code="02C5",
        expected_numeric_index="000002C5.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("Templates/B273.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "panel_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C5_section_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "panel_lifecycle" in data["named_gaps"]


def test_hc0151_profile_records_biology_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0151.dll"),
        code="0151",
        expected_numeric_index="00000151.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=("Templates/00000151.css",),
        sql_snippets=(),
        image_templates=("Templates/B156.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "panel_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0151_section_table_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "panel_lifecycle_hook" in data["named_gaps"]


def test_hc013d_profile_records_drug_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC013D.dll"),
        code="013D",
        expected_numeric_index="0000013D.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={"custom_gaiji_dib": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC013D_hkdksr13_drug_layout_and_template_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc_render_schema_sidecars_classify_search_helpers(tmp_path: Path) -> None:
    idx = tmp_path / "TEST.IDX"
    honmon = tmp_path / "HONMON.DIC"
    idx.write_bytes(b"")
    honmon.write_bytes(b"")
    con = sqlite3.connect(tmp_path / "search.db")
    con.execute("create table t_Search_word (f_type integer, f_midasi text, f_block integer, f_offset integer)")
    con.commit()
    con.close()
    source = DictionarySource(
        dict_id="TEST",
        idx=idx,
        title="test",
        honmon=honmon,
        honmon_start_block=1,
        gaiji_map={},
    )

    summary = _schema_backed_sidecars(source)

    assert summary["role_counts"] == {"sqlite_category_search_index": 1}
    assert summary["sidecars"][0]["hc_render_support"] == "classified_search_helper"
    assert _has_entry_body_sidecar(summary) is False


def test_hc_render_schema_sidecars_detect_exact_body_sidecar(tmp_path: Path) -> None:
    idx = tmp_path / "TEST.IDX"
    honmon = tmp_path / "HONMON.DIC"
    idx.write_bytes(b"")
    honmon.write_bytes(b"")
    con = sqlite3.connect(tmp_path / "body.db")
    con.execute("create table t_contents (f_DataId integer primary key, f_Html text)")
    con.commit()
    con.close()
    source = DictionarySource(
        dict_id="TEST",
        idx=idx,
        title="test",
        honmon=honmon,
        honmon_start_block=1,
        gaiji_map={},
    )

    summary = _schema_backed_sidecars(source)

    assert summary["role_counts"] == {"sqlite_renderer_body": 1}
    assert summary["sidecars"][0]["hc_render_support"] == "entry_body_or_media"
    assert _has_entry_body_sidecar(summary) is True


def test_hc_render_schema_sidecars_detect_extensionless_renderer_sidecar(tmp_path: Path) -> None:
    idx = tmp_path / "TEST.IDX"
    honmon = tmp_path / "HONMON.DIC"
    idx.write_bytes(b"")
    honmon.write_bytes(b"")
    con = sqlite3.connect(tmp_path / "vlpljblF")
    con.execute("create table t_contents (f_DataId integer primary key, f_Html text)")
    con.commit()
    con.close()
    source = DictionarySource(
        dict_id="TEST",
        idx=idx,
        title="test",
        honmon=honmon,
        honmon_start_block=1,
        gaiji_map={},
    )

    summary = _schema_backed_sidecars(source)

    assert summary["role_counts"] == {"sqlite_renderer_body": 1}
    assert summary["sidecars"][0]["name"] == "vlpljblF"
    assert summary["sidecars"][0]["hc_render_support"] == "entry_body_or_media"
    assert _has_entry_body_sidecar(summary) is True


def test_hc_render_rendererdb_comparison_uses_requested_entry_limit() -> None:
    args = argparse.Namespace(
        write_sidecar_media=False,
        media_limit=None,
        write_ziptomedia=True,
        ziptomedia_limit=3,
        limit=25,
    )

    derived = _rendererdb_args(args)

    assert derived.limit == 25
    assert derived.write_ziptomedia is True
    assert derived.ziptomedia_limit == 3


def test_hc_behavior_profile_names_exact_body_without_claiming_full_parity() -> None:
    renderer = HcRendererClassification(
        path=Path("HC015F.dll"),
        code="015F",
        expected_numeric_index="0000015F.idx",
        size=1,
        sha256=None,
        pe=PeSummary(
            kind="pe",
            exports=(
                "epwing2HtmlBodydata",
                "epwing2HtmlBodydataVertical",
                "createMediaFileFromZip",
                "pluginFunction2nd",
                "modifyHeadwordEx",
                "initializeSQL",
                "finalizeSQL",
            ),
        ),
        exinfo_html_dll="HC015F.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("vlpljblF",),
        html_templates=("%s\\body%d.html",),
        sql_snippets=("SELECT f_html FROM t_contents WHERE f_dataid = ? LIMIT 1 ;",),
        image_templates=(),
        features={
            "html_body_renderer": True,
            "vertical_renderer": True,
            "sql_hooks": True,
            "plugin_hooks": True,
            "headword_modifier": True,
            "zip_media_export": True,
        },
    )
    schema = {
        "role_counts": {"sqlite_renderer_body_with_media": 1},
        "sidecars": [{"role": "sqlite_renderer_body_with_media", "hc_render_support": "entry_body_or_media"}],
    }

    profile = build_hc_behavior_profile(
        renderer,
        schema_sidecars=schema,
        rendererdb_summary={"status": "ok", "ziptomedia_written": 2},
    )
    row = profile.as_dict()

    assert row["family"] == "modern_dense_t_contents_renderer"
    assert row["exact_body_html_available"] is True
    assert row["body_strategy_status"] == "exact_entry_body_html"
    assert row["exact_hc_parity"] is False
    assert "PCMDATA_sound_icon_when_asset_present" in row["implemented_semantics"]
    assert "schema_backed_exact_entry_html" in row["implemented_semantics"]
    assert "ziptomedia_reference_extraction" in row["implemented_semantics"]
    assert "royal_example_search_helpers" in row["named_gaps"]
