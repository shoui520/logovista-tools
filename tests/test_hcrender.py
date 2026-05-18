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


def jis_ascii_text(text: str) -> bytes:
    punctuation = {":": (0x21, 0x27), "/": (0x21, 0x3F), ".": (0x21, 0x25)}
    pairs = [bytes(punctuation[ch]) if ch in punctuation else bytes((0x23, ord(ch))) for ch in text]
    return b"\x1f\x04" + b"".join(pairs) + b"\x1f\x05"


def jis_fullwidth_ascii(text: str) -> bytes:
    punctuation = {":": (0x21, 0x27), "/": (0x21, 0x3F), ".": (0x21, 0x25)}
    return b"".join(bytes(punctuation[ch]) if ch in punctuation else bytes((0x23, ord(ch))) for ch in text)


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


def test_hc013a_maps_layout_markers_without_generic_placeholders() -> None:
    body = (
        b"\x1f\x09\x00\x03"
        + b"\xb2\x64"
        + b"".join(jis_ascii(ch) for ch in "abacero")
        + b"\xb2\x6a"
        + b"\xb2\x6b"
        + b"\x1f\x6d"
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013A"))

    assert '<div class="honbun2"><strong><span class="lv-hc-halfwidth">a</span>' in rendered.html
    assert '<span class="lv-hc-halfwidth">o</span></strong></div>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'data-gaiji-code="b26a"' not in rendered.html
    assert 'data-gaiji-code="b26b"' not in rendered.html
    assert rendered.stats["hc013a_honbun2_markers"] == 1
    assert rendered.stats["hc013a_honbun2_closures"] == 1
    assert rendered.stats["hc013a_suppressed_gaiji_markers"] == 2
    assert rendered.stats["hc013a_nonprinting_controls"] == 1
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps


def test_hc013a_missing_b263_uses_named_custom_bitmap_gap() -> None:
    rendered = render_hc_body(b"\xb2\x63" + b"".join(jis_ascii(ch) for ch in "abandonar"), HcRenderOptions(renderer_code="013A"))

    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-custom-dib-missing img_gaiji"' in rendered.html
    assert 'data-gaiji-code="b263"' in rendered.html
    assert rendered.stats["hc013a_custom_dib_gaiji"] == 1
    assert "hc013a_custom_gaiji_bitmap_unresolved" in rendered.named_behavior_gaps


def test_hc013f_consumes_renderer_state_control() -> None:
    rendered = render_hc_body(b"\x1f\x41\x00\x00" + jis_text("見出し") + b"\x1f\x61\x1f\x6d", HcRenderOptions(renderer_code="013F"))

    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc013f_nonprinting_controls"] == 1


def test_hc00c6_maps_sections_to_product_divs_and_example_badge() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
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
    assert rendered.stats["hc00c6_nonprinting_controls"] == 1
    assert rendered.stats["section_images"] == 1
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps


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
    assert '<span class="ind_0010">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc02be_section_div"] == 1
    assert rendered.stats["hc02be_section_span"] == 1


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
        + b"\x1f\x09\x00\x13" + jis_ascii("B")
        + b"\x1f\x09\x00\x08" + jis_ascii("C"),
        HcRenderOptions(renderer_code="02BC", image_sources={"fukumidashi": "Templates/fukumidashi.png"}),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<img src="Templates/fukumidashi.png" class="img_mark2">' in rendered.html
    assert '<div class="komidashi"  style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="honbun" style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc02bc_section_divs"] == 4


def test_hc02bc_consumes_state_controls_and_uses_line_link() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x99\x99"
        + b"\x1f\x41\x00\x00"
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30",
        HcRenderOptions(renderer_code="02BC"),
    )

    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc02bc_noop_sections"] == 1
    assert rendered.stats["hc02bc_nonprinting_controls"] == 2


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
    assert "lv-hc-section" not in rendered.html


def test_hc012e_keeps_common_kun_section_normal_sized() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x27" + jis_ascii("K"),
        HcRenderOptions(renderer_code="012E"),
    )

    assert '<div class="honbun" style="margin-left:0.000000em;">' in rendered.html
    assert "table_itaiji" not in rendered.html
    assert 'class="Itaiji"' not in rendered.html
    assert "lv-hc-section" not in rendered.html


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


def test_hc012e_ignores_unmatched_style_close_marker() -> None:
    rendered = render_hc_body(
        b"\xb2\x42" + jis_ascii("T"),
        HcRenderOptions(renderer_code="012E"),
    )

    assert "</span><span" not in rendered.html
    assert rendered.html.count("<span") == rendered.html.count("</span>")
    assert rendered.stats["hc012e_unmatched_style_markers"] == 1


def test_hc012e_explicit_column_close_does_not_double_close_section() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x53" + jis_ascii("C") + b"\x1f\x09\x00\x54" + jis_ascii("T"),
        HcRenderOptions(renderer_code="012E"),
    )

    assert '<div class="column_Tsukaiwake">' in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc012e_explicit_section_closures"] == 1


def test_hc012e_treats_1f6d_as_nonprinting_renderer_control() -> None:
    rendered = render_hc_body(jis_ascii("A") + b"\x1f\x6d" + jis_ascii("B"), HcRenderOptions(renderer_code="012E"))

    assert rendered.plain == "AB"
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc012e_nonprinting_controls"] == 1


def test_hc00b6_maps_sections_to_genius_blocks() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + jis_ascii("M")
        + b"\x1f\x09\x00\x0a"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x14"
        + jis_ascii("C")
        + b"\x1f\x09\x00\x46"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x20"
        + jis_ascii("S"),
        HcRenderOptions(renderer_code="00B6", image_sources={"cb_w": "templates/CB_w.png"}),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<h1 class="indent10">' in rendered.html
    assert '<div class="contents">' in rendered.html
    assert '<div class="CB_Title"><img src="templates/CB_w.png" class="img_mark4"></div>' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc00b6_section_midashi"] == 1
    assert rendered.stats["hc00b6_section_indent10"] == 1
    assert rendered.stats["hc00b6_section_contents"] == 1
    assert rendered.stats["hc00b6_section_cb"] == 1
    assert rendered.stats["hc00b6_section_state_only"] == 1


def test_hc00b6_renders_template_and_strong_markers() -> None:
    rendered = render_hc_body(
        b"\xb3\x47" + b"\xb3\x53" + b"\xb2\x3d" + b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x01\x00\x02",
        HcRenderOptions(renderer_code="00B6", image_sources={"b347": "templates/b347.png"}),
    )

    assert '<img class="lv-hc-gaiji img_hinshi" src="templates/b347.png" alt="b347" data-gaiji-code="b347">' in rendered.html
    assert "<strong>a</strong>" in rendered.html
    assert 'data-gaiji-code="b23d"' not in rendered.html
    assert 'class="lv-hc-link lLink"' in rendered.html
    assert rendered.stats["hc00b6_image_markers"] == 1
    assert rendered.stats["hc00b6_strong_markers"] == 1
    assert rendered.stats["hc00b6_noop_markers"] == 1


def test_hc012f_maps_sections_and_bunnya_link_images() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_fullwidth_ascii("M")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\x42"
        + jis_fullwidth_ascii("18")
        + b"\x1f\x62\x00\x00\x00\x12\x00\x34"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x05"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + b"\x1f\x0a",
        HcRenderOptions(
            renderer_code="012F",
            image_sources={
                "bunnya_18": "Templates/bunnya_18.png",
                "link_1": "Templates/link_1.png",
                "link_2": "Templates/link_2.png",
            },
        ),
    )

    assert '<div class="midashi">Ｍ</div>' in rendered.html
    assert '<div class="bunnya"><a class="lv-hc-link lineLink"' in rendered.html
    assert '<img src="Templates/bunnya_18.png" class="img_bunnya">' in rendered.html
    assert '<img src="Templates/link_1.png" class="img_gaiji">' in rendered.html
    assert '<img src="Templates/link_2.png" class="img_gaiji">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc012f_section_blocks"] == 4
    assert rendered.stats["hc012f_noop_sections"] == 1
    assert rendered.stats["hc012f_bunnya_images"] == 1
    assert rendered.stats["hc012f_link_icon_sections"] == 2


def test_hc012f_sizedown_and_template_gaiji_use_product_classes() -> None:
    rendered = render_hc_body(
        b"\x1f\x06" + jis_fullwidth_ascii("S") + b"\x1f\x07" + b"\xb1\x22",
        HcRenderOptions(renderer_code="012F", image_sources={"b122": "Templates/b122.png"}),
    )

    assert '<span class="sizedown">Ｓ</span>' in rendered.html
    assert "<sub>" not in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert 'src="Templates/b122.png"' in rendered.html
    assert rendered.stats["hc012f_template_gaiji"] == 1


def test_hc0131_maps_midashi_sections_and_template_gaiji() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_fullwidth_ascii("A")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x04"
        + jis_fullwidth_ascii("1")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + jis_fullwidth_ascii("E")
        + b"\x1f\x0a"
        + b"\xb1\x32",
        HcRenderOptions(renderer_code="0131", image_sources={"b132": "Templates/b132.png"}),
    )

    assert '<div class="midashi">Ａ</div>' in rendered.html
    assert '<div class="content_IND4"><HR style="border-style: dotted;">' in rendered.html
    assert '<div class="content_IND18"><img src="Templates/b132.png" class="img_gaiji">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/b132.png"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0131_heading_blocks"] == 1
    assert rendered.stats["hc0131_section_blocks"] == 2
    assert rendered.stats["hc0131_content_ind18_sections"] == 1
    assert rendered.stats["hc0131_template_gaiji"] == 1


def test_hc0131_sizedown_and_link_class() -> None:
    rendered = render_hc_body(
        b"\x1f\x06"
        + jis_fullwidth_ascii("S")
        + b"\x1f\x07"
        + b"\x1f\x42"
        + jis_fullwidth_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x12\x00\x34",
        HcRenderOptions(renderer_code="0131"),
    )

    assert '<span class="sizedown"><sub>Ｓ</sub></span>' in rendered.html
    assert '<a class="lv-hc-link lineLink"' in rendered.html
    assert rendered.stats["hc0131_sizedown_spans"] == 1


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
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc012d_section_blocks"] == 2
    assert rendered.stats["hc012d_midashi_state_sections"] == 1
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
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc009d_section_blocks"] == 1
    assert rendered.stats["hc009d_noop_markers"] == 1
    assert rendered.stats["hc009d_kakomi_markers"] == 1


def test_hc009d_table_header_linebreak_starts_balanced_tbody() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x08" + b"\xb1\x40" + b"\xb1\x4f" + jis_ascii("H") + b"\x1f\x0a" + jis_ascii("B") + b"\xb1\x41",
        HcRenderOptions(renderer_code="009D"),
    )

    assert "<thead><tr><th>" in rendered.html
    assert "</th></tr></thead><tbody><tr><td>" in rendered.html
    assert "</td></tr></tbody></table>" in rendered.html
    assert rendered.html.count("<td") == rendered.html.count("</td>")
    assert rendered.html.count("<tr") == rendered.html.count("</tr>")
    assert rendered.html.count("<table") == rendered.html.count("</table>")
    assert rendered.stats["hc009d_table_body_starts"] == 1


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
    assert "lv-hc-section" not in rendered.html
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
        + b"\x1f\x09\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0144"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="honbun" style="text-indent:-1.0em;margin-left:2.000000em;">' in rendered.html
    assert '<div class="komidashi"  style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0144_state_sections"] == 1
    assert rendered.stats["hc0144_nonprinting_controls"] == 1
    assert "hc0144_unmapped_section_0040" not in rendered.named_behavior_gaps


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
        + b"\x1f\x41\x01\x00"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("P")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("C")
        + b"\x1f\x09\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="03E8"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert rendered.stats["hc03e8_section_blocks"] == 3
    assert rendered.stats["hc03e8_state_sections"] == 1
    assert rendered.stats["hc03e8_nonprinting_controls"] == 1
    assert "hc03e8_unmapped_section_0040" not in rendered.named_behavior_gaps


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
        + b"\x1f\x41\x01\x00"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x02"
        + jis_ascii("P")
        + b"\x1f\x09\x00\x08"
        + jis_ascii("C")
        + b"\x1f\x09\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0141"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:2.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0141_section_blocks"] == 3
    assert rendered.stats["hc0141_state_sections"] == 1
    assert rendered.stats["hc0141_nonprinting_controls"] == 1
    assert "hc0141_unmapped_section_0040" not in rendered.named_behavior_gaps


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


def test_hc009b_maps_header_honbun_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x0c"
        + b"\x1f\x41"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_ascii("B")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\xb1\x21"
        + b"\x1f\x6d"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="009B", image_sources={"b121": "b121.gif"}),
    )

    assert '<div class="header"><div class="midashi">Ｈ</div></div>' in rendered.html
    assert '<div class="honbun" style="margin-top:12px">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc009b_section_header"] == 1
    assert rendered.stats["hc009b_section_honbun"] == 1
    assert rendered.stats["hc009b_nonprinting_controls"] == 1


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
        + b"\x1f\x09\x00\x42"
        + jis_ascii("T")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="title3">' in rendered.html
    assert '<div class="medblk">' in rendered.html
    assert '<span class="med">' in rendered.html
    assert '<div class="medprice">' in rendered.html
    assert '<div class="medimage">' in rendered.html
    assert '<div class="indent41">' in rendered.html
    assert '<div class="indent42">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc013d_section_blocks"] == 7
    assert rendered.stats["hc013d_midashi_state_sections"] == 1


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
    assert "lv-hc-section" not in rendered.html
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


def test_hc02c8_maps_zukaiho_sections_tables_and_links() -> None:
    body = (
        b"\x1f\x41"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x03"
        + jis_text("三")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("二")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + jis_text("頭")
        + b"\x1f\x09\x00\x31"
        + jis_text("三一")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x33"
        + jis_text("三三")
        + b"\x1f\x0a"
        + b"\x1f\x09\x01\x00"
        + jis_text("本文")
        + b"\xb1\x36"
        + b"\x1f\x09\x00\x50"
        + b"\x1f\x09\x00\x60"
        + b"\x1f\x09\x00\x70"
        + jis_text("表")
        + b"\x1f\x09\x00\x71"
        + b"\x1f\x09\x00\x61"
        + b"\x1f\x09\x00\x51"
        + b"\x1f\x42"
        + jis_ascii("A")
        + b"\x1f\x62\x00\x00\x00\x01\x00\x20"
        + b"\x1f\x43"
        + jis_ascii("B")
        + b"\x1f\x63\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02C8", image_sources={"b136": "Templates/B136.png"}))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="indent3">' in rendered.html
    assert '<div class="midashi_2nd">' in rendered.html
    assert '<div class="header" >' in rendered.html
    assert '<div class="indent31">' in rendered.html
    assert '<div class="indent33">' in rendered.html
    assert '<div class="contents">' in rendered.html
    assert "<table>" in rendered.html and "</table>" in rendered.html
    assert "<tr>" in rendered.html and "</tr>" in rendered.html
    assert "<td>" in rendered.html and "</td>" in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert 'class="lv-hc-link Link"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc02c8_section_indent3"] == 1
    assert rendered.stats["hc02c8_section_midashi_2nd"] == 1
    assert rendered.stats["hc02c8_section_header"] == 1
    assert rendered.stats["hc02c8_section_table_open"] == 1


def test_hc02c8_private_sections_do_not_leak_visible_close_state() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\xe2\x00\x07"
        + b"\x1f\x09\x00\x12"
        + jis_text("hidden")
        + b"\x1f\xe3"
        + b"\x1f\x09\x00\x03"
        + jis_text("visible")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02C8"))

    assert '<div class="midashi">' in rendered.html
    assert '<br/></div><div class="indent3">' not in rendered.html
    assert '<br/><div class="indent3">' in rendered.html
    assert rendered.stats["hc02c8_private_section_controls"] == 1


def test_hc008c_maps_midashi_contents_body_medical_sections_and_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("薬")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x0c"
        + jis_text("注")
        + b"\x1f\x0a"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\xb1\x70"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="008C", image_sources={"b170": "Templates/b170.gif"}))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div style="margin-left:12px;">' in rendered.html
    assert '<div class="medblk">' in rendered.html
    assert '<div class="medblkcaution">' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc008c_contents_body_blocks"] == 1
    assert rendered.stats["hc008c_section_medblk"] == 1
    assert rendered.stats["hc008c_nonprinting_controls"] == 1


def test_hc0147_maps_bcd_sections_bunken_blocks_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii("H")
        + b"\x1f\x09\x01\x00"
        + jis_text("題")
        + b"\x1f\x09\x02\x00"
        + jis_text("本文")
        + b"\x1f\x09\x00\x05"
        + jis_text("文献")
        + b"\x1f\x09\x00\x16"
        + jis_text("項目")
        + b"\x1f\x09\x00\x07"
        + jis_text("著者")
        + b"\x1f\x09\x99\x99"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0147"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="contents_title">' in rendered.html
    assert '<div class="contents">' in rendered.html
    assert '<div class="bunken"><div class="bunken_title">' in rendered.html
    assert '<div class="bunken_contents">' in rendered.html
    assert '<div class="cyosha">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0147_section_midashi"] == 1
    assert rendered.stats["hc0147_section_contents_title"] == 1
    assert rendered.stats["hc0147_section_contents"] == 1
    assert rendered.stats["hc0147_section_bunken_title"] == 1
    assert rendered.stats["hc0147_section_bunken_contents"] == 1
    assert rendered.stats["hc0147_section_cyosha"] == 1
    assert rendered.stats["hc0147_section_close"] == 1


def test_hc0147_renders_template_gaiji_and_rubar_marker() -> None:
    rendered = render_hc_body(
        b"\xa1\x2e\xb1\x41\x1f\x10\x23\x72\x23\x75",
        HcRenderOptions(
            renderer_code="0147",
            image_sources={"a12e": "Templates/a12e.png", "b141": "Templates/b141.png", "rubar": "Templates/rubar.png"},
        ),
    )

    assert 'src="Templates/a12e.png"' in rendered.html
    assert 'src="Templates/b141.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert '<img src="Templates/rubar.png" class="img_mark">' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0147_template_image_markers"] == 2
    assert rendered.stats["hc0147_rubar_markers"] == 1


def test_hc0147_renders_url_and_padding_markers_without_gaiji_placeholders() -> None:
    body = (
        b"\xb1\x60"
        + jis_text("行")
        + b"\xb1\x61"
        + b"\xb1\x5c"
        + jis_ascii_text("http://example.test")
        + jis_text("資料")
        + b"\xb1\x5d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0147"))

    assert '<span style="padding-left:0em;"></span>' in rendered.html
    assert '<span style="padding-left:1em;"></span>' in rendered.html
    assert 'href="http://example.test"' in rendered.html
    assert 'target="_blank"' in rendered.html
    assert "http://example.test" in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0147_padding_markers"] == 2
    assert rendered.stats["hc0147_url_link_starts"] == 1
    assert rendered.stats["hc0147_url_links"] == 1


def test_hc0094_maps_sections_color_blocks_template_gaiji_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii("H")
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\xb1\x21"
        + b"\xb1\x3e"
        + jis_text("赤")
        + b"\x1f\x09\x00\x09"
        + jis_text("行")
        + b"\x1f\x09\x00\x12"
        + jis_text("脚注")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0094", image_sources={"b121": "Templates/B121.gif"}),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo">' in rendered.html
    assert '<div class="footer">' in rendered.html
    assert '<div class="aka">' in rendered.html
    assert 'src="Templates/B121.gif"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0094_section_midashi"] == 1
    assert rendered.stats["hc0094_section_contents_body"] == 1
    assert rendered.stats["hc0094_section_lineinfo"] == 1
    assert rendered.stats["hc0094_section_footer"] == 1
    assert rendered.stats["hc0094_template_image_markers"] == 1
    assert rendered.stats["hc0094_color_div_markers"] == 1


def test_hc0094_maps_class_arrow_and_consumes_state_markers() -> None:
    body = (
        jis_text("前")
        + b"\xb1\x48"
        + jis_text("後")
        + b"\xb1\x50"
        + b"\xb1\x51"
        + b"\xb1\x59"
        + b"\xb1\x39"
        + b"\xb1\x40"
        + b"\xb1\x3d"
        + jis_text("終")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0094", image_sources={"class_arrow": "Templates/class_arrow.gif"}),
    )

    assert 'src="Templates/class_arrow.gif"' in rendered.html
    assert 'data-gaiji-code="b148"' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'data-gaiji-code="b150"' not in rendered.html
    assert 'data-gaiji-code="b151"' not in rendered.html
    assert 'data-gaiji-code="b159"' not in rendered.html
    assert 'data-gaiji-code="b139"' not in rendered.html
    assert 'data-gaiji-code="b140"' not in rendered.html
    assert 'data-gaiji-code="b13d"' not in rendered.html
    assert rendered.stats["hc0094_class_arrow_markers"] == 1
    assert rendered.stats["hc0094_state_markers"] == 3
    assert rendered.stats["hc0094_suppressed_markers"] == 3


def test_hc008c_selects_recovered_line_link_variants() -> None:
    target = b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    default_link = render_hc_body(b"\x1f\x42" + jis_ascii("L") + target, HcRenderOptions(renderer_code="008C"))
    forced_plain = render_hc_body(
        b"\x1f\x42\xb1\x2d" + jis_ascii("L") + target,
        HcRenderOptions(renderer_code="008C", image_sources={"b12d": "Templates/B12D.gif"}),
    )
    midashi_link = render_hc_body(
        b"\x1f\x41\x00\x00" + b"\x1f\x42" + jis_ascii("L") + target,
        HcRenderOptions(renderer_code="008C"),
    )

    assert 'class="lv-hc-link lineLink2"' in default_link.html
    assert 'class="lv-hc-link lineLink"' in forced_plain.html
    assert 'class="lv-hc-link lineLink3"' in midashi_link.html


def test_hc0137_maps_iwanami_sections_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii("H")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x05"
        + jis_text("副")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + jis_text("隠")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_text("角")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + jis_text("丸")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x10"
        + jis_text("太")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + jis_text("本")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x21"
        + jis_text("山")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x1e"
        + jis_text("文")
        + b"\xb1\x21"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0137", image_sources={"b121": "Templates/b121.png"}))

    assert '<div class="midashi">' in rendered.html
    assert '<font class="font_midashi_sub">' in rendered.html
    assert '<div style="display:none">' in rendered.html
    assert "［" in rendered.html and "］" in rendered.html
    assert "（" in rendered.html and "）" in rendered.html
    assert '<div style="margin-left: 10.000000em" class="honbunB">' in rendered.html
    assert '<div style="margin-left: 12.000000em" class="honbun">' in rendered.html
    assert "〈" in rendered.html and "〉" in rendered.html
    assert '<div class="honbun">' in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0137_section_midashi"] == 1
    assert rendered.stats["hc0137_section_midashi_sub"] == 1
    assert rendered.stats["hc0137_section_hidden"] == 1
    assert rendered.stats["hc0137_section_honbunB"] == 1
    assert rendered.stats["hc0137_section_honbun"] >= 2


def test_hc00a6_maps_sections_ruby_and_line_links() -> None:
    ruby_start = b"\x1f\xe2\x00\x05" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3\x00\x00"
    ruby_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:S") + jis_text("よ") + b"\x1f\xe3\x00\x00"
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x07"
        + ruby_start
        + jis_text("本")
        + ruby_end
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00A6"))

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:7.000000em;">' in rendered.html
    assert '<ruby class="ruby7"><rb class="rb7">本</rb>' in rendered.html
    assert '<rt class="rt7">よ</rt>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert rendered.stats["hc00a6_section_blocks"] == 2
    assert rendered.stats["hc00a6_ruby_starts"] == 1
    assert rendered.stats["hc00a6_ruby_ends"] == 1


def test_hc00a4_maps_sections_ruby_marker_images_and_line_links() -> None:
    ruby_start = b"\x1f\xe2\x00\x05" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3\x00\x00"
    ruby_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:S") + jis_text("よ") + b"\x1f\xe3\x00\x00"
    image_directive = b"\x1f\xe2\x00\x10" + jis_fullwidth_ascii("IMG:Fhon0007.png") + b"\x1f\xe3\x00\x00"
    html_directive = b"\x1f\xe2\x00\x14" + jis_fullwidth_ascii("HTM:FTaijiHaiiku.htm") + b"\x1f\xe3\x00\x00"
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\xb1\x2c"
        + b"\x1f\x09\x00\x10"
        + ruby_start
        + jis_text("本")
        + ruby_end
        + b"\xb1\x2f"
        + image_directive
        + html_directive
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00A4",
            image_sources={"b12fh": "Templates/b12fH.gif", "hon0007": "images/hon0007.png"},
            html_templates={"taijihaiiku.htm": '<div><img src="hon0007.png">T</div>'},
        ),
    )

    assert '<div class="midashi"><span class="hankakuMidashi">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:1.000000em;">' in rendered.html
    assert '<ruby class="ruby7"><rb class="rb7">本</rb>' in rendered.html
    assert '<rt class="rt7">よ</rt>' in rendered.html
    assert '<img src="Templates/b12fH.gif" class="img_mark2">' in rendered.html
    assert '<img src="images/hon0007.png" class="img_inline">' in rendered.html
    assert '<div><img src="images/hon0007.png">T</div>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert 'data-gaiji-code="b12c"' not in rendered.html
    assert rendered.stats["hc00a4_section_blocks"] == 1
    assert rendered.stats["hc00a4_midashi_blocks"] == 1
    assert rendered.stats["hc00a4_ruby_starts"] == 1
    assert rendered.stats["hc00a4_ruby_ends"] == 1
    assert rendered.stats["hc00a4_suppressed_gaiji_markers"] == 1
    assert rendered.stats["hc00a4_b12f_markers"] == 1
    assert rendered.stats["hc00a4_private_inline_images"] == 1
    assert rendered.stats["hc00a4_private_html_includes"] == 1


def test_hkdksr_medical_renderers_map_sections_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("一般名")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + b"\xb1\x2a"
        + jis_text("商品")
        + b"\x1f\x09\x00\x10"
        + b"".join(jis_ascii(ch) for ch in "100")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x11"
        + b"\x1f\x4d"
        + bytes.fromhex("000000000000000000000000000001230045")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x42"
        + b"".join(jis_ascii(ch) for ch in "PC")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x43"
        + jis_text("説明")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x40"
        + jis_text("見出し")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x6d"
    )

    for code in ("014A", "02C3", "02C6"):
        rendered = render_hc_body(
            body,
            HcRenderOptions(renderer_code=code, image_sources={"b12a": "Templates/b12a.png"}),
        )

        assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
        assert '<div class="title3">一般名</div>' in rendered.html
        assert '<span class="med"><img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
        assert '<div class="medprice"><span class="hankaku">1</span><span class="hankaku">0</span><span class="hankaku">0</span></div>' in rendered.html
        assert '<div class="medimage"><span class="lv-hc-media"' in rendered.html
        assert '<table class="table_pc"><tr class="tr_pc"><td class="td_pc1"><span class="hankaku">P</span><span class="hankaku">C</span></td><td class="td_pc2">説明</td></tr></table>' in rendered.html
        assert '<div class="indent40">見出し' in rendered.html
        assert 'class="lv-hc-link lineLink2"' in rendered.html
        assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
        assert "data-lv-section" not in rendered.html
        assert rendered.stats["hc_hkdksr_medical_section_title3"] == 1
        assert rendered.stats["hc_hkdksr_medical_section_med"] == 1
        assert rendered.stats["hc_hkdksr_medical_section_medprice"] == 1
        assert rendered.stats["hc_hkdksr_medical_section_medimage"] == 1
        assert rendered.stats["hc_hkdksr_medical_section_table_open"] == 1
        assert rendered.stats["hc_hkdksr_medical_section_table_cell"] == 1
        assert rendered.stats["hc_hkdksr_medical_section_indent"] == 1
        assert rendered.stats["hc_hkdksr_medical_nonprinting_controls"] == 1


def test_hc_gen_year_maps_sections_icons_links_and_template_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe2\x23\x31\x1f\xe3"
        + b"\xb1\x32"
        + jis_text("本文")
        + b"\xb1\x33"
        + b"\xb1\x30\xb1\x31\xb1\x38"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    for code in ("02C4", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"):
        rendered = render_hc_body(
            body,
            HcRenderOptions(
                renderer_code=code,
                image_sources={"b132": "Templates/B132.png", "b133": "Templates/B133.png"},
            ),
        )

        assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
        assert '<div class="honbun" style="margin-left:0.000000em;">' in rendered.html
        assert '<img src="1.png" class="img_icon"/><br>' in rendered.html
        assert 'src="Templates/B132.png"' in rendered.html
        assert 'src="Templates/B133.png"' in rendered.html
        assert 'class="lv-hc-gaiji img_mark2"' in rendered.html
        assert 'class="lv-hc-link lineLink"' in rendered.html
        assert 'href="lvaddr://00000002/0048"' in rendered.html
        assert 'data-lv-section' not in rendered.html
        assert 'data-gaiji-code="b130"' not in rendered.html
        assert rendered.stats["hc_gen_year_section_blocks"] == 1
        assert rendered.stats["hc_gen_year_private_icons"] == 1
        assert rendered.stats["hc_gen_year_img_mark2_markers"] == 2
        assert rendered.stats["hc_gen_year_noop_markers"] == 3


def test_hc_gen_year_consumes_1f6d_as_renderer_state() -> None:
    rendered = render_hc_body(b"\x1f\x6d", HcRenderOptions(renderer_code="02D1"))

    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc_gen_year_nonprinting_controls"] == 1


def test_hc_gen_year_distinguishes_marker_image_classes() -> None:
    for code in ("02C4", "02C7", "02C9", "02D1"):
        rendered = render_hc_body(
            b"\xb1\x2d\xb1\x2e\xb1\x2f\xb1\x32",
            HcRenderOptions(renderer_code=code, image_sources={"b132": "Templates/B132.png"}),
        )

        assert rendered.html.count('class="lv-hc-gaiji img_mark"') == 3
        assert 'src="B12D.png"' in rendered.html
        assert 'src="B12E.png"' in rendered.html
        assert 'src="B12F.png"' in rendered.html
        assert 'class="lv-hc-gaiji img_mark2"' in rendered.html
        assert rendered.stats["hc_gen_year_img_mark_markers"] == 3
        assert rendered.stats["hc_gen_year_img_mark2_markers"] == 1


def test_hc_gen_year_late_variants_special_case_b135_literal() -> None:
    for code in ("02CB", "02CC", "02CD"):
        rendered = render_hc_body(b"\xb1\x35", HcRenderOptions(renderer_code=code))

        assert "\U00020bb7" in rendered.html
        assert 'data-gaiji-code="b135"' not in rendered.html
        assert rendered.stats["hc_gen_year_literal_markers"] == 1
        assert "hc_gen_year_img_mark2_markers" not in rendered.stats


def test_hc00c4_maps_sections_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii("H")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\xb1\x26"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("別")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x0a"
        + b"\x1f\x09\x00\x0f"
        + jis_text("小")
        + b"\x1f\x09\x00\x10"
        + b"\x1f\x09\x00\x15"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00C4", image_sources={"b126": "Templates/B126.png"}))

    assert '<div class="midashi"><span class="zenkakuMidashi">' in rendered.html
    assert '<div class="block"><div class="honbun_number">' in rendered.html
    assert '<div class="honbun_icon"><img src="betsumei.png" class="icon">' in rendered.html
    assert '<img src="arrow1.png" class="icon_s">' in rendered.html
    assert '<font class="font_down">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'src="Templates/B126.png"' in rendered.html
    assert 'class="lv-hc-gaiji gaiji"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc00c4_section_midashi"] == 1
    assert rendered.stats["hc00c4_section_honbun_number"] == 1
    assert rendered.stats["hc00c4_section_honbun_icon"] == 1
    assert rendered.stats["hc00c4_section_inline_icon"] == 1
    assert rendered.stats["hc00c4_section_state_only"] == 1
    assert rendered.stats["hc00c4_template_gaiji"] == 1


def test_hc00c4_maps_heading_user_body_waku_and_narrow_gaiji() -> None:
    body = (
        b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x21\x4e"
        + jis_text("本文")
        + b"\x21\x4f"
        + b"\xb1\x37\xb1\x38\xb1\x3c"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00C4",
            image_sources={
                "waku_l": "Templates/waku_l.png",
                "waku_r": "Templates/waku_r.png",
                "b137": "Templates/B137.png",
                "b138": "Templates/B138.png",
                "b13c": "Templates/B13C.png",
            },
        ),
    )

    assert '<div class="midashi"><span class="zenkakuMidashi">' in rendered.html
    assert '<div class="honbun_user">' in rendered.html
    assert 'src="Templates/waku_l.png"' in rendered.html
    assert 'class="lv-hc-gaiji waku_l"' in rendered.html
    assert 'src="Templates/waku_r.png"' in rendered.html
    assert 'class="lv-hc-gaiji waku_r"' in rendered.html
    assert rendered.html.count('class="lv-hc-gaiji gaiji_k"') == 2
    assert 'class="lv-hc-gaiji gaiji_b"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc00c4_heading_blocks"] == 1
    assert rendered.stats["hc00c4_honbun_user_blocks"] == 1
    assert rendered.stats["hc00c4_waku_markers"] == 2
    assert rendered.stats["hc00c4_template_gaiji"] == 3
    assert rendered.stats["hc00c4_nonprinting_controls"] == 1


def test_hc02c0_maps_sections_icons_links_and_state_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe2\x23\x31\x1f\xe3"
        + b"\xb1\x21"
        + jis_text("本文")
        + b"\xb1\x38\xb1\x4c\xb1\x4d"
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="02C0", image_sources={"1": "Templates/1.png", "b121": "Templates/B121.png"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:12px">' in rendered.html
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/B121.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'data-gaiji-code="b138"' not in rendered.html
    assert 'data-gaiji-code="b14c"' not in rendered.html
    assert 'data-gaiji-code="b14d"' not in rendered.html
    assert rendered.stats["hc02c0_section_blocks"] == 1
    assert rendered.stats["hc02c0_section_state"] == 1
    assert rendered.stats["hc02c0_private_icons"] == 1
    assert rendered.stats["hc02c0_noop_markers"] == 3
    assert rendered.stats["hc02c0_nonprinting_controls"] == 1


def test_hc02c0_uses_vertical_margin_axis() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x03" + jis_text("本文"),
        HcRenderOptions(renderer_code="02C0", vertical=True),
    )

    assert '<div class="honbun" style="margin-top:12px">' in rendered.html


def test_hc02ca_maps_sections_icons_template_markers_and_literal_marker() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe2\x23\x31\x1f\xe3"
        + b"\xb1\x2d\xb1\x2e\xb1\x2f"
        + b"\xb1\x30"
        + jis_text("本文")
        + b"\xb1\x35"
        + b"\xb1\x31"
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="02CA", image_sources={"1": "Templates/1.png", "b12d": "Templates/B12D.png"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:12px">' in rendered.html
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark" src="Templates/B12D.png"' in rendered.html
    assert 'src="B12E.png"' in rendered.html
    assert 'src="B12F.png"' in rendered.html
    assert "\U00020bb7" in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'data-gaiji-code="b130"' not in rendered.html
    assert 'data-gaiji-code="b131"' not in rendered.html
    assert rendered.stats["hc02ca_section_blocks"] == 1
    assert rendered.stats["hc02ca_section_state"] == 1
    assert rendered.stats["hc02ca_private_icons"] == 1
    assert rendered.stats["hc02ca_img_mark_markers"] == 3
    assert rendered.stats["hc02ca_noop_markers"] == 2
    assert rendered.stats["hc02ca_literal_markers"] == 1
    assert rendered.stats["hc02ca_nonprinting_controls"] == 1


def test_hc02ca_treats_unknown_private_directives_as_state_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\xe2\x00\x07"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\xe3"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02CA"))

    assert "本文" in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc02ca_private_state_markers"] == 2


def test_hc0136_maps_sections_icons_links_and_state_controls() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe2\x23\x31\x1f\xe3"
        + b"\xb1\x21"
        + jis_text("本文")
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0136", image_sources={"1": "Templates/1.png", "b121": "Templates/B121.png"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:12px">' in rendered.html
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/B121.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert rendered.stats["hc0136_section_blocks"] == 1
    assert rendered.stats["hc0136_section_state"] == 1
    assert rendered.stats["hc0136_private_icons"] == 1
    assert rendered.stats["hc0136_nonprinting_controls"] == 1


def test_hc0136_suppresses_private_state_block_without_leaking_section_close() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\xe2\x00\x07"
        + b"\x1f\x09\x00\x12"
        + b"\x1f\x42"
        + jis_text("秘")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x0a"
        + b"\x1f\xe3"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0136"))

    assert "秘" not in rendered.html
    assert "秘" not in rendered.plain
    assert "本文" in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert len(rendered.links) == 0
    assert rendered.stats["hc0136_private_state_blocks"] == 1
    assert rendered.stats["hc0136_section_blocks"] == 1


def test_hc0063_maps_heading_contents_sections_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
        + bytes.fromhex("b667")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x0a"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0063", image_sources={"roman-alphabet": "Templates/roman-alphabet.gif"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div style="margin-left: 6px">' in rendered.html
    assert '<div style="margin-left: 12px">' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/roman-alphabet.gif"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0063_heading_blocks"] == 1
    assert rendered.stats["hc0063_contents_body_blocks"] == 1
    assert rendered.stats["hc0063_margin_sections"] == 2
    assert rendered.stats["hc0063_template_image_markers"] == 1
    assert rendered.stats["hc0063_nonprinting_controls"] == 1


def test_hc0093_maps_lineinfo_sections_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
        + bytes.fromhex("b140")
        + bytes.fromhex("b14c")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x09\x00\x05"
        + jis_text("用例")
        + bytes.fromhex("b151")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0093",
            image_sources={
                "arrow": "Templates/arrow.gif",
                "class_arrow": "Templates/class_arrow.gif",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert '<div class="lineinfo1"><span class="hankakuMidashi">H</span>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo2">' in rendered.html
    assert '<div class="youreihan"><div class="lineinfo5">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert '<img src="Templates/dummy.GIF" class="img_dummy">' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark" src="Templates/arrow.gif"' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark2" src="Templates/class_arrow.gif"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0093_lineinfo_sections"] == 3
    assert rendered.stats["hc0093_contents_body_blocks"] == 1
    assert rendered.stats["hc0093_youreihan_sections"] == 1
    assert rendered.stats["hc0093_template_image_markers"] == 2
    assert rendered.stats["hc0093_noop_markers"] == 1


def test_hc0096_maps_lineinfo_sections_links_template_gaiji_and_state_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + bytes.fromhex("b150")
        + b"\x1f\x09\x00\x12"
        + bytes.fromhex("b124")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + bytes.fromhex("214c252f214d")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0096",
            image_sources={
                "b124": "Templates/B124.gif",
                "b250": "Templates/B250.gif",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert '<div class="lineinfo0-1"><span class="hankakuMidashi">H</span>' in rendered.html
    assert '<div class="lineinfo0-12">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo0-3">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark2" src="Templates/B124.gif"' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark4" src="Templates/B250.gif"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert "B150" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0096_lineinfo_sections"] == 3
    assert rendered.stats["hc0096_contents_body_blocks"] == 1
    assert rendered.stats["hc0096_template_image_markers"] == 1
    assert rendered.stats["hc0096_inline_mark_images"] == 1
    assert rendered.stats["hc0096_reflow_state_markers"] == 1


def test_hc0090_maps_lineinfo_sections_links_and_gaiji_classes() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + bytes.fromhex("b555")
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
        + bytes.fromhex("b556")
        + b"\x1f\x09\x00\x05"
        + jis_text("用例")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0090",
            image_sources={
                "b555": "Templates/B555.gif",
                "b556": "Templates/B556.gif",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert '<div class="lineinfo1"><span class="hankakuMidashi">H</span>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo2">' in rendered.html
    assert '<div class="yourei"><div class="lineinfo5">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert '<img src="Templates/dummy.GIF" class="img_dummy">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/B555.gif"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/B556.gif"' in rendered.html
    assert "data-lv-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0090_lineinfo_sections"] == 3
    assert rendered.stats["hc0090_contents_body_blocks"] == 1
    assert rendered.stats["hc0090_yourei_sections"] == 1
    assert rendered.stats["hc0090_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0090_img_gaiji_images"] == 1
    assert rendered.stats["hc0090_hankakuMidashi_spans"] == 1


def test_hc014f_maps_midashi_contents_links_decoration_and_gaiji_classes() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + bytes.fromhex("b555")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
        + bytes.fromhex("b556")
        + b"\x1f\xe0\x00\x04"
        + jis_text("強調")
        + b"\x1f\xe1"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x0a"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="014F",
            image_sources={
                "b555": "Templates/b555.png",
                "b556": "Templates/b556.png",
                "dummy": "Templates/dummy.gif",
            },
        ),
    )

    assert '<div class="midashi"><span class="hankaku">H</span>' in rendered.html
    assert '</div><div class="contents">' in rendered.html
    assert '<img src="Templates/dummy.gif" class="img_dummy">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/b555.png"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/b556.png"' in rendered.html
    assert "<b><i>強調</i></b>" in rendered.html
    assert 'class="lv-hc-link Link"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "data-lv-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc014f_midashi_blocks"] == 1
    assert rendered.stats["hc014f_contents_blocks"] == 1
    assert rendered.stats["hc014f_suppressed_heading_breaks"] == 1
    assert rendered.stats["hc014f_bold_italic_spans"] == 1
    assert rendered.stats["hc014f_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc014f_img_gaiji_images"] == 1


def test_hc0135_maps_sinmei_sections_private_images_links_and_gaiji_classes() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + bytes.fromhex("b555")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x0b"
        + b"\x1f\x06"
        + jis_text("小")
        + b"\x1f\x07"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x1e"
        + jis_text("例")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x26"
        + bytes.fromhex("214c214c214d214d22652175216f")
        + b"\x1f\xe2"
        + jis_fullwidth_ascii("shikakuha")
        + b"\x1f\xe3"
        + bytes.fromhex("b556")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x5c"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0135",
            image_sources={
                "b122": "Templates/b122.png",
                "b123": "Templates/b123.png",
                "b555": "Templates/B555.png",
                "b556": "Templates/B556.png",
                "dummy": "Templates/dummy.gif",
                "exam": "Templates/exam.png",
                "jyokon": "Templates/jyokon.png",
                "shikakuha": "Templates/shikakuha.png",
            },
        ),
    )

    assert '<div class="midashi"><span class="hankaku">H</span>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="content_IND0">本文</div>' in rendered.html
    assert '<div class="content_IND1"><span class="sizedown"><sub>小</sub></span></div>' in rendered.html
    assert '<p class="contents_yourei">例</p>' in rendered.html
    assert '<img src="Templates/exam.png" class="img_icon">' in rendered.html
    assert '<img src="Templates/b122.png" class="img_gaiji">' in rendered.html
    assert '<img src="Templates/b123.png" class="img_gaiji">' in rendered.html
    assert '<img src="Templates/jyokon.png" class="img_icon">' in rendered.html
    assert '<img src="Templates/shikakuha.png" class="img_icon">' in rendered.html
    assert '<img src="Templates/dummy.gif" class="img_dummy">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/B555.png"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/B556.png"' in rendered.html
    assert "&amp;&yen;" in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "data-lv-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.html.count("<p") == rendered.html.count("</p>")
    assert rendered.stats["hc0135_midashi_blocks"] == 1
    assert rendered.stats["hc0135_contents_body_blocks"] == 1
    assert rendered.stats["hc0135_section_content_IND0"] == 1
    assert rendered.stats["hc0135_section_content_IND1"] == 1
    assert rendered.stats["hc0135_section_contents_yourei"] == 1
    assert rendered.stats["hc0135_private_directive_images"] == 1
    assert rendered.stats["hc0135_jis_image_markers"] == 3
    assert rendered.stats["hc0135_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0135_img_gaiji_images"] == 1
    assert "unknown_control_1f5c" not in rendered.named_behavior_gaps
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps


def test_hc0091_maps_midashi_contents_marker_images_links_and_gaiji_classes() -> None:
    body = (
        b"\x1f\x41\x00\x00"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + bytes.fromhex("b555")
        + b"\x1f\x0a"
        + jis_text("本文")
        + bytes.fromhex("215a4e63215b")
        + bytes.fromhex("4356212134392121212121612121")
        + bytes.fromhex("b556")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0091",
            image_sources={
                "b555": "Templates/B555.gif",
                "b556": "Templates/B556.gif",
                "dummy": "Templates/dummy.GIF",
                "rei": "Templates/rei.gif",
                "chikan": "Templates/chikan.gif",
            },
        ),
    )

    assert '<div class="midashi"><span class="hankakuMidashi">H</span>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<img src="Templates/dummy.GIF" class="img_dummy">' in rendered.html
    assert '<img src="Templates/rei.gif" class="img_mark">' in rendered.html
    assert '<img src="Templates/chikan.gif" class="img_mark">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/B555.gif"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/B556.gif"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "data-lv-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0091_midashi_blocks"] == 1
    assert rendered.stats["hc0091_contents_body_blocks"] == 1
    assert rendered.stats["hc0091_mark_images"] == 2
    assert rendered.stats["hc0091_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0091_img_gaiji_images"] == 1
    assert rendered.stats["hc0091_hankakuMidashi_spans"] == 1


def test_hc0048_maps_margin_sections_and_symbol_triggered_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + bytes.fromhex("2223")
        + jis_text("見出し")
        + b"\x1f\x0a"
        + jis_text("本文")
        + b"\x1f\x6d"
        + b"\x1f\x4d"
        + bytes.fromhex("000000000000000000000000000001230456")
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0048", image_sources={"sound": "Templates/sound.png"}),
    )

    assert '<div style="margin: 3px"></div><div class="midashi">■見出し</div><div class="honbun">' in rendered.html
    assert "本文" in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert '<div><span class="lv-hc-media"' in rendered.html
    assert 'data-lv-resource="colscr:00000123:0456"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0048_margin_sections"] == 1
    assert rendered.stats["hc0048_midashi_blocks"] == 1
    assert rendered.stats["hc0048_honbun_blocks"] == 1
    assert rendered.stats["hc0048_nonprinting_controls"] == 2
    assert rendered.stats["hc0048_media_divs"] == 1
    assert rendered.stats["hc0048_media_div_closures"] == 1


def test_hc00ac_maps_honbun_sections_links_and_marker_suppression() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + b"\xb1\x39"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00AC", image_sources={"b121": "Templates/B121.gif"}),
    )

    assert '<div class="honbun" style="margin-left:1em;">' in rendered.html
    assert '<div class="honbun" style="margin-left:7em;text-indent:-7em;">' in rendered.html
    assert "見出し" in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert 'data-gaiji-code="b139"' not in rendered.html
    assert rendered.stats["hc00ac_honbun_sections"] == 2
    assert rendered.stats["hc00ac_nonprinting_controls"] == 1
    assert rendered.stats["hc00ac_suppressed_markers"] == 1


def test_hc013c_maps_sections_icons_links_and_state_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe2\x23\x31\x1f\xe3"
        + b"\xb1\x21"
        + jis_text("本文")
        + b"\xa4\x35\xa4\x36"
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="013C", image_sources={"1": "Templates/1.png", "b121": "Templates/B121.png"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:12px">' in rendered.html
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/B121.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'data-gaiji-code="a435"' not in rendered.html
    assert 'data-gaiji-code="a436"' not in rendered.html
    assert rendered.stats["hc013c_section_blocks"] == 1
    assert rendered.stats["hc013c_section_state"] == 1
    assert rendered.stats["hc013c_private_icons"] == 1
    assert rendered.stats["hc013c_noop_markers"] == 2
    assert rendered.stats["hc013c_nonprinting_controls"] == 1


def test_hc013c_missing_b121_uses_named_custom_bitmap_gap() -> None:
    rendered = render_hc_body(b"\x1f\x41\x00\x00\xb1\x21" + jis_text("見出し") + b"\x1f\x61", HcRenderOptions(renderer_code="013C"))

    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-custom-dib-missing img_gaiji"' in rendered.html
    assert 'data-gaiji-code="b121"' in rendered.html
    assert rendered.stats["hc013c_custom_dib_gaiji"] == 1
    assert "hc013c_custom_gaiji_bitmap_unresolved" in rendered.named_behavior_gaps


def test_hc013c_uses_vertical_margin_axis() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x03" + jis_text("本文"),
        HcRenderOptions(renderer_code="013C", vertical=True),
    )

    assert '<div class="honbun" style="margin-top:12px">' in rendered.html


def test_hc00b3_maps_sections_links_and_state_controls() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\xb1\x21"
        + jis_text("本文")
        + b"\x1f\x6d"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00B3", image_sources={"b121": "Templates/B121.png"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="honbun" style="margin-left:12px">' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/B121.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert rendered.stats["hc00b3_section_blocks"] == 1
    assert rendered.stats["hc00b3_section_state"] == 1
    assert rendered.stats["hc00b3_nonprinting_controls"] == 1


def test_hc00b3_maps_section_000c_to_header_container() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x0c" + jis_text("本文") + b"\x1f\x0a",
        HcRenderOptions(renderer_code="00B3"),
    )

    assert '<div class="header">' in rendered.html
    assert rendered.stats["hc00b3_section_header"] == 1


def test_hc00a0_applies_detail_template_and_play_sound_directive() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("HELLO")
        + b"\x1f\x05"
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("こんにちは")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x10"
        + b"\x1f\xe2\x00\x07"
        + jis_text("＜ＰｌａｙＳｏｕｎｄ＞０００１．ｍｐ３＜／ＰｌａｙＳｏｕｎｄ＞")
        + b"\x1f\xe3"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00A0",
            image_sources={
                "sound": "Templates/sound.png",
                "eng": "Templates/eng.png",
                "jpn": "Templates/jpn.png",
                "play": "Templates/play.png",
                "ok-2": "Templates/ok-2.png",
                "ex-2": "Templates/ex-2.png",
            },
            html_templates={
                "Header": '<div class="header"><img src="play.png">$$DISPLAY_MIDASHI$$$$MODE_EJ$$</div>',
                "Detail": (
                    '<table class="main" id="detail"><tr>'
                    '<td><img src="sound.png" onclick="lvedPlay(\'$$DETAIL_NO$$\',\'$$DETAIL_PLAY$$\');"></td>'
                    '<td class="english">$$ENGLISH$$</td>'
                    '<td class="japanese">$$JAPANESE$$</td>'
                    '<td><img src="ok-$$DETAIL_OK$$.png"><img src="ex-$$DETAIL_EX$$.png"></td>'
                    "</tr></table>"
                ),
            },
        ),
    )

    assert '<div class="header"><img src="Templates/play.png">none1</div>' in rendered.html
    assert '<td class="english">HELLO</td>' in rendered.html
    assert '<td class="japanese">こんにちは</td>' in rendered.html
    assert 'src="Templates/sound.png"' in rendered.html
    assert "lvedPlay('0001','0001.mp3')" in rendered.html
    assert 'src="Templates/ok-2.png"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.plain == "HELLO\nこんにちは"
    assert rendered.audio[0]["target"]["path"] == "mp3/0001.mp3"
    assert rendered.private_directives[0]["kind"] == "play_sound"
    assert rendered.stats["hc00a0_detail_rows"] == 1
    assert rendered.stats["hc00a0_play_sound_directives"] == 1


def test_hc02c1_maps_sections_icons_and_moji_down_markers() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\xb1\x21"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + b"\xb1\x22"
        + jis_text("続き"),
        HcRenderOptions(renderer_code="02C1", gaiji_map={"b121": "❶", "b122": "❷"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<img src="1.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="2.png" class="img_icon"/><br>' in rendered.html
    assert '<p class="moji-down">❶' in rendered.html
    assert '<p class="moji-down">❷' in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc02c1_section_blocks"] == 3
    assert rendered.stats["hc02c1_section_icons"] == 2
    assert rendered.stats["hc02c1_moji_down_blocks"] == 2
    assert rendered.stats["hc02c1_nonprinting_controls"] == 2


def test_hc02c1_uses_line_links_and_template_image_gaiji() -> None:
    body = (
        b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\xb1\x4c"
        + b"\xb1\x35"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="02C1",
            image_sources={"b14c": "Templates/B14C.png", "b135": "Templates/b135.png"},
        ),
    )

    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert 'src="Templates/B14C.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_mark4"' in rendered.html
    assert 'src="Templates/b135.png"' in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert rendered.stats["hc02c1_template_image_markers"] == 1
    assert rendered.stats["hc02c1_moji_down_markers"] == 1


def test_hc02bf_maps_sections_hasei_icon_and_moji_down_markers() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x05"
        + b"\xb1\x28"
        + jis_text("派生")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + b"\xb1\x29"
        + jis_text("続き"),
        HcRenderOptions(renderer_code="02BF", gaiji_map={"b128": "①", "b129": "②"}),
    )

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<img src="hasei.png" class="img_icon"/><br>' in rendered.html
    assert '<p class="moji-down">①' in rendered.html
    assert '<p class="moji-down">②' in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc02bf_section_blocks"] == 3
    assert rendered.stats["hc02bf_section_icons"] == 1
    assert rendered.stats["hc02bf_moji_down_blocks"] == 2
    assert rendered.stats["hc02bf_nonprinting_controls"] == 2


def test_hc02bf_line_links_use_product_class() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02BF"))

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


def test_hc0142_maps_midashi_honbun_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x99\x99"
        + b"\x1f\xe2\x00\x07"
        + jis_fullwidth_ascii("1000100")
        + b"\x1f\xe3"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x42"
        + jis_text("参照")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0142"))

    assert '<div class="midashi">見出し</div><div class="honbun" style="margin-left:0.000000em;"><br>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc0142_honbun_blocks"] == 1
    assert rendered.stats["hc0142_noop_sections"] == 1
    assert rendered.stats["hc0142_section_states"] == 2
    assert rendered.stats["hc0142_nonprinting_controls"] == 1


def test_hc0142_math_plain_text_image_and_overline_markers() -> None:
    body = (
        b"\xb1\x77"
        + b"\xb1\x2f"
        + b"\xb1\x78"
        + b"\xa1\x64"
        + b"\xb1\x3f"
        + b"\xb1\x57"
        + b"\x1f\x10"
        + jis_text("上線")
        + b"\x1f\x11"
        + b"\x1f\x10"
        + jis_fullwidth_ascii("ru")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0142",
            gaiji_map={"b12f": "①"},
            image_sources={"b13f": "Templates/b13f.png", "b157": "Templates/b157.png", "rubar": "Templates/rubar.png"},
        ),
    )

    assert '<span class="math">①</span>' in rendered.html
    assert '<span class="margin"></span>' in rendered.html
    assert 'class="lv-hc-gaiji icotype_1"' in rendered.html
    assert '<span class="plain_text"><img class="lv-hc-gaiji lv-hc-gaiji-image gaiji_full"' in rendered.html
    assert '<label class="overline">上線</label>' in rendered.html
    assert '<img src="Templates/rubar.png" class="img_mark">' in rendered.html
    assert rendered.stats["hc0142_math_markers"] == 2
    assert rendered.stats["hc0142_margin_markers"] == 1
    assert rendered.stats["hc0142_icotype_markers"] == 1
    assert rendered.stats["hc0142_plain_text_markers"] == 1
    assert rendered.stats["hc0142_rubar_markers"] == 1


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
    assert "lv-hc-section" not in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert rendered.stats["hc0065_midashi_blocks"] == 1
    assert rendered.stats["hc0065_contents_body_blocks"] == 1
    assert rendered.stats["hc0065_state_sections"] == 2


def test_hc0067_wraps_midashi_contents_margin_sections_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0067"))

    assert '<div class="midashi"><span class="hankaku">H</span></div><div class="contents_body">' in rendered.html
    assert '<div style="margin-left: 9px">' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0067_midashi_blocks"] == 1
    assert rendered.stats["hc0067_contents_body_blocks"] == 1
    assert rendered.stats["hc0067_margin_sections"] == 1
    assert rendered.stats["hc0067_nonprinting_controls"] == 2


def test_hc0068_wraps_midashi_contents_margin_sections_links_and_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\xb6\x55"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("本文")
        + b"\xb6\x55"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0068",
            image_sources={"b655": "Templates/b655.gif", "dummy": "Templates/dummy.gif"},
        ),
    )

    assert '<div class="midashi"><span class="hankaku">H</span><img src="Templates/dummy.gif" class="img_dummy"><img class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert '</div><div class="contents_body">' in rendered.html
    assert '<div style="margin-left: 12px">' in rendered.html
    assert '<img src="Templates/dummy.gif" class="img_dummy"><img class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0068_midashi_blocks"] == 1
    assert rendered.stats["hc0068_contents_body_blocks"] == 1
    assert rendered.stats["hc0068_margin_sections"] == 1
    assert rendered.stats["hc0068_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0068_img_gaiji_images"] == 1
    assert rendered.stats["hc0068_nonprinting_controls"] == 1


def test_hc0020_maps_midashi_contents_definition_markers_links_and_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + bytes.fromhex("b12d")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + bytes.fromhex("215a")
        + jis_text("注")
        + b"\x1f\x0a"
        + bytes.fromhex("2221")
        + jis_text("一")
        + bytes.fromhex("2126")
        + jis_text("二")
        + b"\x1f\x0a"
        + bytes.fromhex("222a")
        + bytes.fromhex("224d")
        + bytes.fromhex("b12e")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0020",
            image_sources={
                "b12d": "Templates/b12d.png",
                "b12e": "Templates/b12e.png",
                "confer": "Templates/confer.png",
                "diamond": "Templates/diamond.png",
                "dummy": "Templates/dummy.GIF",
                "nakaguro": "Templates/nakaguro.png",
            },
        ),
    )

    assert '<div class="midashi"><span class="hankaku">H</span>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div style="margin-left: 9px">' in rendered.html
    assert '<div class="hr_div2"></div><div class="div_215a">' in rendered.html
    assert '<dl><dt><img src="Templates/diamond.png" class="img_diamond"></dt><dd>' in rendered.html
    assert '</dd><dt><img src="Templates/nakaguro.png" class="img_diamond"></dt><dd>' in rendered.html
    assert '<img src="Templates/confer.png" class="img_confer">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/b12d.png"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/b12e.png"' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert 'data-gaiji-code="224d"' not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.html.count("<dl") == rendered.html.count("</dl>")
    assert rendered.stats["hc0020_midashi_blocks"] == 1
    assert rendered.stats["hc0020_contents_body_blocks"] == 1
    assert rendered.stats["hc0020_margin_sections"] == 1
    assert rendered.stats["hc0020_definition_lists"] == 1
    assert rendered.stats["hc0020_definition_terms"] == 1
    assert rendered.stats["hc0020_confer_markers"] == 1
    assert rendered.stats["hc0020_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0020_img_gaiji_images"] == 1
    assert rendered.stats["hc0020_nonprinting_controls"] == 3


def test_hc0069_wraps_midashi_contents_margin_sections_links_and_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\xb5\x55"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\xb5\x55"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0069",
            image_sources={"b555": "gaiji/B555.bmp", "dummy": "Templates/dummy.gif"},
        ),
    )

    assert '<div class="midashi"><span class="hankaku">H</span><img src="Templates/dummy.gif" class="img_dummy"><img class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert '</div><div class="contents_body">' in rendered.html
    assert '<div style="margin-left: 24px">' in rendered.html
    assert '<img src="Templates/dummy.gif" class="img_dummy"><img class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0069_midashi_blocks"] == 1
    assert rendered.stats["hc0069_contents_body_blocks"] == 1
    assert rendered.stats["hc0069_margin_sections"] == 1
    assert rendered.stats["hc0069_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0069_img_gaiji_images"] == 1
    assert rendered.stats["hc0069_nonprinting_controls"] == 1


def test_hc008b_wraps_midashi_contents_kaisou_sections_and_line_links() -> None:
    body = (
        b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x09\x00\x02"
        + jis_text("階層")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("R")
        + b"\x1f\x63\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="008B"))

    assert '<div class="midashi"><span class="hankaku">H</span></div>' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="kaisou">' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc008b_midashi_blocks"] == 1
    assert rendered.stats["hc008b_midashi_closures"] == 1
    assert rendered.stats["hc008b_contents_body_blocks"] == 1
    assert rendered.stats["hc008b_kaisou_sections"] == 1
    assert rendered.stats["hc008b_nonprinting_controls"] == 1


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


def test_hc0158_maps_archsic4_section_codes_to_renderer_classes() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x37"
        + jis_text("用例")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x39"
        + jis_text("音声")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x23"
        + jis_text("欄")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x24"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0158"))

    assert 'class="lv-hc-section"' not in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun_normal">' in rendered.html
    assert '<div class="honbun_yourei">' in rendered.html
    assert '<div class="honbun_sound">' in rendered.html
    assert '<div class="column_waka_haiku"><div>' in rendered.html
    assert rendered.stats["hc0158_section_blocks"] == 4
    assert rendered.stats["hc0158_section_state_1"] == 1
    assert rendered.stats["hc0158_section_state_24"] == 1


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


def test_hc0157_uses_line_link_for_internal_addresses() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x03\x00\x40"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0157"))

    assert 'class="lv-hc-link lineLink"' in rendered.html


def test_hc0157_treats_1f12_1f13_as_noop_controls() -> None:
    rendered = render_hc_body(b"\x1f\x12" + jis_ascii("A") + b"\x1f\x13", HcRenderOptions(renderer_code="0157"))

    assert "<em>" not in rendered.html
    assert rendered.plain == "A"
    assert rendered.stats["hc0157_noop_controls"] == 2
    assert "unknown_control_1f12" not in rendered.named_behavior_gaps


def test_hc0157_maps_dconci98_section_layout() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + jis_ascii("K")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_ascii("G")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x10"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + jis_ascii("M")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x13"
        + jis_ascii("S")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x11"
        + b"\x1f\x09\x00\x30"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x32"
        + jis_ascii("D")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x33"
        + jis_ascii("E")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x31"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0157"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="komidashi">' in rendered.html
    assert '<div class="textline gogi_ej">' in rendered.html
    assert '<div class="yourei_ej"><div>' in rendered.html
    assert '<div class="textline yourei_ej_main">' in rendered.html
    assert '<div class="textline yourei_ej_sub">' in rendered.html
    assert '<div class="haseigo"><div>' in rendered.html
    assert '<div class="textline haseigo_main">' in rendered.html
    assert '<div class="textline haseigo_sub">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0157_group_sections"] == 2
    assert rendered.stats["hc0157_section_blocks"] == 6
    assert rendered.stats["hc0157_group_closures"] == 2


def test_hc0146_renders_color_font_markers_without_gaiji_placeholders() -> None:
    rendered = render_hc_body(
        b"\xb2\x32" + jis_ascii("A") + b"\xb2\x33",
        HcRenderOptions(renderer_code="0146"),
    )

    assert '<font class="color_font"><span class="lv-hc-halfwidth">A</span></font>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0146_style_markers"] == 2


def test_hc0146_renders_recovered_style_marker_pairs() -> None:
    body = (
        b"\xb2\x30"
        + jis_ascii("A")
        + b"\xb2\x31"
        + b"\xb2\x34"
        + jis_ascii("B")
        + b"\xb2\x35"
        + b"\xb2\x38"
        + jis_ascii("C")
        + b"\xb2\x39"
        + b"\xb2\x44"
        + jis_ascii("D")
        + b"\xb2\x45"
        + b"\xb3\x54"
        + jis_ascii("E")
        + b"\xb3\x55"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<span class="plain_font"><span class="lv-hc-halfwidth">A</span></span>' in rendered.html
    assert '<span class="not_italic_font"><span class="lv-hc-halfwidth">B</span></span>' in rendered.html
    assert '<span class="under_line"><span class="lv-hc-halfwidth">C</span></span>' in rendered.html
    assert '<span class="under_line"><span class="lv-hc-halfwidth">D</span></span>' in rendered.html
    assert '<small><span class="lv-hc-halfwidth">E</span></small>' in rendered.html
    assert rendered.stats["hc0146_style_markers"] == 10


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
    assert rendered.html.count('class="lv-hc-gaiji img_mark4"') == 2
    assert 'src="Templates/b357.png"' in rendered.html
    assert 'class="lv-hc-gaiji gaiji_icon"' in rendered.html
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


def test_hc0146_maps_common_body_sections_and_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x99\x99"
        + b"\x1f\x09\x01\x80"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x03\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<div class="midashi">' in rendered.html
    assert '<span class="indent_minus">本文</span>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0146_state_sections"] == 2
    assert rendered.stats["hc0146_honbun_sections"] == 1
    assert rendered.stats["hc0146_template_sections"] == 1


def test_hc0146_maps_recovered_example_and_translation_sections() -> None:
    body = (
        b"\x1f\x09\x02\x10"
        + jis_text("例")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x20"
        + jis_text("訳")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x50"
        + jis_text("成句")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<div class="column_frame exam_frame"><span class="exam_text">例</span></div>' in rendered.html
    assert '<span class="exam_translate">訳</span>' in rendered.html
    assert '<span class="idiom_text_color">成句</span>' in rendered.html
    assert "hc0146_unmapped_section_branch" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0146_section_exam_text"] == 1
    assert rendered.stats["hc0146_section_exam_translate"] == 1
    assert rendered.stats["hc0146_section_idiom_text_color"] == 1


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
    row.features["plugin_hooks"] = False
    row.features["user_data_hooks"] = False
    assert _renderer_behavior_gaps(row) == ["panel_hooks", "sql_search_or_helper_hooks"]


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


def test_hc0147_profile_records_contents_bunken_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0147.dll"),
        code="0147",
        expected_numeric_index="00000147.idx",
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
        html_templates=("Templates/00000147.css",),
        sql_snippets=(),
        image_templates=("a12e.png", "b141.png", "rubar.png"),
        features={"panel_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0147_contents_bunken_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "panel_lifecycle" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0094_profile_records_keigo_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0094.dll"),
        code="0094",
        expected_numeric_index="00000094.idx",
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
        html_templates=("Templates/00000094.css",),
        sql_snippets=(),
        image_templates=("B121.gif", "B13E.gif", "class_arrow.gif"),
        features={},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0094_sections_color_blocks_and_template_gaiji" in data["implemented_semantics"]
    assert "HC0094_class_arrow_state_and_bitmap_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0137_profile_records_iwanami_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0137.dll"),
        code="0137",
        expected_numeric_index="00000137.idx",
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
        html_templates=("Templates/00000137.css",),
        sql_snippets=("initializeSQL",),
        image_templates=("b1.png", "g1.png"),
        features={"panel_hooks": True, "headword_modifier": True, "sql_hooks": True, "custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0137_iwanami_section_margin_and_line_links" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "panel_lifecycle" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc02c8_profile_records_zukaiho_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02C8.dll"),
        code="02C8",
        expected_numeric_index="000002C8.idx",
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
        html_templates=("Templates/000002C8.css",),
        sql_snippets=("initializeSQL",),
        image_templates=("B136.png", "B146.png", "mlink.gif"),
        features={"headword_modifier": True, "sql_hooks": True, "custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C8_zukaiho_section_table_and_indent_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc008c_profile_records_hkdk_2010_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC008C.dll"),
        code="008C",
        expected_numeric_index="0000008C.idx",
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
        html_templates=("0000008C.css",),
        sql_snippets=(),
        image_templates=("b170.gif", "sound.png"),
        features={"custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC008C_medical_section_layout" in data["implemented_semantics"]
    assert "HC008C_conditional_link_classes" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


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


def test_hc0067_profile_records_contents_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0067.dll"),
        code="0067",
        expected_numeric_index="00000067.idx",
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
        html_templates=("Templates/00000067.css",),
        sql_snippets=(),
        image_templates=("sound.png", "image.png"),
        features={"vertical_renderer": False},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0067_midashi_contents_and_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0068_profile_records_contents_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0068.dll"),
        code="0068",
        expected_numeric_index="00000068.idx",
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
        html_templates=("Templates/00000068.css",),
        sql_snippets=(),
        image_templates=("sound.png", "image.png", "dummy.gif", "b655.gif"),
        features={"custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0068_midashi_contents_and_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0069_profile_records_contents_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0069.dll"),
        code="0069",
        expected_numeric_index="00000069.idx",
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
        html_templates=("Templates/00000069.css",),
        sql_snippets=(),
        image_templates=("sound.png", "image.png", "dummy.gif"),
        features={"custom_gaiji_dib": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0069_midashi_contents_and_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc008b_profile_records_medical_expert_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC008B.dll"),
        code="008B",
        expected_numeric_index="0000008B.idx",
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
        html_templates=("Templates/0000008B.css",),
        sql_snippets=(),
        image_templates=("sound.png", "image.png", "a.gif", "chiryou.gif"),
        features={"vertical_renderer": False},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC008B_kaisou_contents_and_midashi_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc009b_profile_records_honbun_margin_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC009B.dll"),
        code="009B",
        expected_numeric_index="0000009B.idx",
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
        html_templates=("%sHTMLs\\%d-%d.html", "%s\\body%d.html"),
        sql_snippets=(),
        image_templates=("<img src=\"%4x.gif\" class=\"img_gaiji\">",),
        features={"uses_picture_api": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC009B_honbun_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "block_offset_htmls_template" in data["named_gaps"]


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


def test_hc0142_profile_records_panel_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0142.dll"),
        code="0142",
        expected_numeric_index="00000142.idx",
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
        html_templates=("Templates/00000142.css",),
        sql_snippets=(),
        image_templates=("Templates/b13f.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "panel_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0142_panel_body_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "panel_lifecycle" in data["named_gaps"]


def test_hc02c1_profile_records_panel_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02C1.dll"),
        code="02C1",
        expected_numeric_index="000002C1.idx",
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
        html_templates=("Templates/000002C1.css",),
        sql_snippets=(),
        image_templates=("Templates/B14C.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "panel_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C1_section_icons_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "panel_lifecycle" in data["named_gaps"]


def test_hc02bf_profile_records_panel_layout_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02BF.dll"),
        code="02BF",
        expected_numeric_index="000002BF.idx",
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
        html_templates=("Templates/000002BF.css",),
        sql_snippets=("SELECT ...",),
        image_templates=("Templates/hasei.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "panel_hooks": True, "sql_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02BF_section_icon_and_moji_down_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "panel_lifecycle" in data["named_gaps"]


def test_hc012f_profile_records_bunnya_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC012F.dll"),
        code="012F",
        expected_numeric_index="0000012F.idx",
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
        html_templates=("Templates/0000012F.css",),
        sql_snippets=(),
        image_templates=("Templates/bunnya_18.png", "Templates/b122.png"),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC012F_bunnya_section_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0131_profile_records_kqebhou_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0131.dll"),
        code="0131",
        expected_numeric_index="00000131.idx",
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
        html_templates=("Templates/00000131.css",),
        sql_snippets=("SELECT ...",),
        image_templates=("Templates/b132.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "sql_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0131_kqebhou_section_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]


def test_hc00a6_profile_records_hkkigak6_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00A6.dll"),
        code="00A6",
        expected_numeric_index="000000A6.idx",
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
        html_templates=("Templates/000000A6.css",),
        sql_snippets=(),
        image_templates=("Templates/b12fH.gif",),
        features={"headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00A6_sections_and_ruby_directives" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]


def test_hkdksr_medical_profiles_record_subset_without_claiming_parity() -> None:
    for code, css_name in (("014A", "0000014a.css"), ("02C3", "000002c3.css"), ("02C6", "000002c6.css")):
        row = HcRendererClassification(
            path=Path(f"HC{code}.dll"),
            code=code,
            expected_numeric_index=f"0000{code}.idx",
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
            html_templates=(f"Templates/{css_name}",),
            sql_snippets=("SELECT ...",),
            image_templates=("Templates/b12a.png", "Templates/midashi1.png"),
            features={"custom_gaiji_dib": True, "headword_modifier": True, "panel_hooks": True, "sql_hooks": True, "vertical_renderer": True},
        )

        data = build_hc_behavior_profile(row).as_dict()

        assert "HC_HKDKSR_medical_section_layout" in data["implemented_semantics"]
        assert data["exact_hc_parity"] is False
        assert "custom_gaiji_dib_hook" in data["named_gaps"]
        assert "modify_headword_hook" in data["named_gaps"]
        assert "panel_lifecycle_hook" in data["named_gaps"]
        assert "sql_hook" in data["named_gaps"]


def test_hc_gen_year_profile_records_subset_without_claiming_parity() -> None:
    for code in ("02C4", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"):
        row = HcRendererClassification(
            path=Path(f"HC{code}.dll"),
            code=code,
            expected_numeric_index=f"0000{code}.idx",
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
            html_templates=(f"Templates/0000{code}.css",),
            sql_snippets=(),
            image_templates=("Templates/B132.png",),
            features={"custom_gaiji_dib": True, "headword_modifier": True, "vertical_renderer": True},
        )

        data = build_hc_behavior_profile(row).as_dict()

        assert "HC_GEN_YEAR_section_icons_and_template_markers" in data["implemented_semantics"]
        assert data["exact_hc_parity"] is False
        assert "custom_gaiji_dib_hook" in data["named_gaps"]
        assert "modify_headword_hook" in data["named_gaps"]


def test_hc02ca_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02CA.dll"),
        code="02CA",
        expected_numeric_index="000002CA.idx",
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
        html_templates=("Templates/000002CA.css",),
        sql_snippets=(),
        image_templates=("Templates/B12D.png", "Templates/B135.png"),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02CA_honbun_margin_sections" in data["implemented_semantics"]
    assert "HC02CA_private_state_and_bitmap_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0136_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0136.dll"),
        code="0136",
        expected_numeric_index="00000136.idx",
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
        html_templates=("Templates/00000136.css",),
        sql_snippets=(),
        image_templates=("Templates/1.png",),
        features={"vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0136_honbun_margin_sections" in data["implemented_semantics"]
    assert "HC0136_private_state_block_suppression" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert data["named_gaps"] == ["visual_parity_unverified"]


def test_hc0063_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0063.dll"),
        code="0063",
        expected_numeric_index="00000063.idx",
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
        html_templates=("Templates/00000063.css",),
        sql_snippets=(),
        image_templates=("Templates/roman-alphabet.gif",),
        features={"custom_gaiji_dib": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0063_contents_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc0093_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0093.dll"),
        code="0093",
        expected_numeric_index="00000093.idx",
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
        html_templates=("Templates/00000093.css",),
        sql_snippets=(),
        image_templates=("Templates/arrow.gif", "Templates/meaning.gif", "Templates/etymology.gif", "Templates/class_arrow.gif"),
        features={"custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0093_lineinfo_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "private_ruby_directive_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0096_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0096.dll"),
        code="0096",
        expected_numeric_index="00000096.idx",
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
        html_templates=("Templates/00000096.css",),
        sql_snippets=(),
        image_templates=("Templates/B124.gif", "Templates/B250.gif"),
        features={"custom_gaiji_dib": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0096_lineinfo_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "private_ruby_directive_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc014f_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC014F.dll"),
        code="014F",
        expected_numeric_index="0000014F.idx",
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
        html_templates=("Templates/0000014F.css",),
        sql_snippets=("SELECT f_midasi,f_block,f_offset FROM t_Search_1 WHERE f_midasi LIKE '%'",),
        image_templates=("Templates/back.gif", "Templates/forward.gif"),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "plugin_hooks": True, "sql_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC014F_midashi_contents_and_decoration_modes" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0135_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0135.dll"),
        code="0135",
        expected_numeric_index="00000135.idx",
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
        html_templates=("Templates/00000135.css",),
        sql_snippets=("SELECT f_word FROM t_originalsearch WHERE f_word LIKE '%'",),
        image_templates=("Templates/exam.png", "Templates/shikakuha.png"),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "plugin_hooks": True, "sql_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0135_sinmei_sections_and_private_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "sql_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0048_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0048.dll"),
        code="0048",
        expected_numeric_index="00000048.idx",
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
        html_templates=("Templates/00000049.css",),
        sql_snippets=(),
        image_templates=("Templates/sound.png",),
        features={"vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0048_margin_heading_sections" in data["implemented_semantics"]
    assert "HC0048_media_div_placeholders" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00a4_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00A4.dll"),
        code="00A4",
        expected_numeric_index="000000A4.idx",
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
        html_templates=("Templates/000000A4.css",),
        sql_snippets=(),
        image_templates=("Templates/b12fH.gif", "Templates/URL-icon.gif"),
        features={"vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00A4_sections_ruby_and_resource_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "fixed_html_fallback_loading" in data["named_gaps"]
    assert "private_image_or_html_directive_hook" not in data["named_gaps"]
    assert "previous_next_navigation_footer" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00ac_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00AC.dll"),
        code="00AC",
        expected_numeric_index="000000AC.idx",
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
        html_templates=("Templates/000000AC.css",),
        sql_snippets=(),
        image_templates=("Templates/b139H.gif", "Templates/b13aH.gif"),
        features={"vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00AC_honbun_margin_sections" in data["implemented_semantics"]
    assert "HC00AC_marker_suppression" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc02c0_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02C0.dll"),
        code="02C0",
        expected_numeric_index="000002C0.idx",
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
        html_templates=("Templates/000002C0.css",),
        sql_snippets=(),
        image_templates=("Templates/B121.png",),
        features={"custom_gaiji_dib": True, "headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C0_honbun_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc013c_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC013C.dll"),
        code="013C",
        expected_numeric_index="0000013C.idx",
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
        html_templates=("Templates/0000013C.css",),
        sql_snippets=(),
        image_templates=("Templates/B121.png",),
        features={"headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC013C_honbun_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc00b3_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00B3.dll"),
        code="00B3",
        expected_numeric_index="000000B3.idx",
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
        html_templates=("Templates/000000B3.css",),
        sql_snippets=(),
        image_templates=("Templates/B121.gif",),
        features={"vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00B3_honbun_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False


def test_hc00a0_profile_records_sql_detail_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00A0.dll"),
        code="00A0",
        expected_numeric_index="000000A0.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=("HTMLs/Header.html", "HTMLs/Detail.html", "Templates/000000A0.css"),
        sql_snippets=("SDicSQLSearchAndHtml",),
        image_templates=("Templates/sound.png",),
        features={"sql_hooks": True, "plugin_hooks": True, "user_data_hooks": True, "uses_body_api": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00A0_phrase_detail_renderer" in data["implemented_semantics"]
    assert data["family"] == "sql_hook_renderer"
    assert data["exact_hc_parity"] is False
    assert "sql_hook" in data["named_gaps"]


def test_hc0159_profile_records_exact_rendererdb_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0159.dll"),
        code="0159",
        expected_numeric_index="00000159.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=("Templates/00000159.css",),
        sql_snippets=("SELECT f_Html FROM t_contents WHERE f_DataId = ?", "SELECT * FROM kisoku;"),
        image_templates=("back.gif", "forward.gif"),
        features={
            "custom_gaiji_dib": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "sql_hooks": True,
            "user_data_hooks": True,
            "vertical_renderer": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={"status": "ok", "t_contents_rows": 10, "entries_matched_to_raw_honmon": 10},
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC0159_t_contents_exact_body_html" in data["implemented_semantics"]
    assert "schema_backed_exact_entry_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["habgespa_t_contents_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert hooks["habgespa_sql_search_helpers"]["status"] == "classified_not_emulated"
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc013f_profile_records_block_offset_rendererdb_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC013F.dll"),
        code="013F",
        expected_numeric_index="0000013F.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("vlpljblF",),
        html_templates=("%sHTMLs\\%d-%d.html", "%sHTMLs\\%d-%d_v.html"),
        sql_snippets=("SELECT Body FROM",),
        image_templates=("b16c.png", "forwardV.gif"),
        features={
            "custom_gaiji_dib": True,
            "headword_modifier": True,
            "panel_hooks": True,
            "sql_hooks": True,
            "uses_body_api": True,
            "vertical_renderer": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={"status": "ok_block_offset_body", "block_offset_body_rows": 2},
        raw_gaps={"unknown_control_1f6d": 1},
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC013F_block_offset_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["block_offset_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert hooks["panel_lifecycle"]["status"] == "classified_not_emulated"
    assert data["exact_hc_parity"] is False
    assert "custom_gaiji_dib_hook" in data["named_gaps"]
    assert "unknown_control_1f6d" not in data["named_gaps"]


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


def test_hc005c_maps_heading_sections_links_and_custom_gaiji_templates() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("A")
        + b"\xa1\x34"
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x03"
        + b"\xb1\x25"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x01\x00\x20"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="005C", image_sources={"dummy": "Templates/dummy.GIF"}),
    )

    assert '<div class="eMidashi">' in rendered.html
    assert '<span class="hankakuMidashi">A</span>' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert 'src="a134.gif"' in rendered.html
    assert '<div style="margin: 9px">' in rendered.html
    assert 'src="b125.gif"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc005c_section_blocks"] == 1
    assert rendered.stats["hc005c_custom_gaiji"] == 2


def test_hc005c_renders_label_marker_images_and_image_links() -> None:
    body = (
        b"\x21\x5a"
        + jis_text("語法")
        + b"\x1f\x44"
        + b"\x00\x00\x00\x00\x00\x00\x00\x02\x00\x40"
        + b"\x1f\x64\x00\x00\x00\x02\x00\x40"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="005C",
            image_sources={
                "dummy": "Templates/dummy.GIF",
                "gohou": "Templates/gohou.gif",
                "image": "Templates/image.png",
            },
        ),
    )

    assert 'src="Templates/gohou.gif" class="img_mark"' in rendered.html
    assert "語法" not in rendered.html
    assert '<img src="Templates/image.png" class="img_mark"></a>' in rendered.html
    assert "lvaddr://00000002/0064" in rendered.html
    assert rendered.stats["hc005c_mark_images"] == 1
    assert rendered.stats["hc005c_image_links"] == 1


def test_hc005c_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC005C.dll"),
        code="005C",
        expected_numeric_index="0000005C.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata",)),
        exinfo_html_dll="HC005C.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("sound.gif", "image.png", "gohou.gif"),
        features={"html_body_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC005C_heading_section_marker_and_gaiji_layout" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "kene7j5_heading_section_and_marker_layout" for hook in row["hook_behaviors"])


def test_hc0132_maps_finance_sections_and_heading_layout() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + jis_ascii("G")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + jis_ascii("S")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x13"
        + jis_ascii("K")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x14"
        + jis_ascii("Y")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x15"
        + jis_ascii("R")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x16"
        + jis_ascii("E")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x17"
        + jis_ascii("J")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x18"
        + jis_ascii("C")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0132"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun">' in rendered.html
    assert '<div class="gogi">' in rendered.html
    assert '<div class="sansho">' in rendered.html
    assert '<div class="kanren_h">' in rendered.html
    assert '<div class="kanren_y">' in rendered.html
    assert '<div class="kanren_sansho">' in rendered.html
    assert '<div class="example_h">' in rendered.html
    assert '<div class="example_y">' in rendered.html
    assert '<div class="kaisetsu">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc0132_honbun_blocks"] == 1
    assert rendered.stats["hc0132_section_blocks"] == 8


def test_hc0132_maps_hankaku_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x02"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("ABC")
        + b"\x1f\x05"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x03\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0132"))

    assert '<span class="hankaku">ABC</span>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html


def test_hc0132_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC0132.dll"),
        code="0132",
        expected_numeric_index="00000132.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata",)),
        exinfo_html_dll="HC0132.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("sound.png", "image.png"),
        features={"html_body_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC0132_finance_section_layout" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "ngfinanc_section_layout" for hook in row["hook_behaviors"])


def test_hc00a9_maps_midashi_honbun_header_links_and_marker_image() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("見出し")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x03\x00\x40"
        + b"\x1f\x09\x00\x0c"
        + b"\x22\x2a"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("URL")
        + b"\x1f\x05"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00A9", image_sources={"mlink": "Templates/mlink.gif"}),
    )

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert '<div class="honbun" style="margin-left:12px">' in rendered.html
    assert '<div class="header">' in rendered.html
    assert 'src="Templates/mlink.gif" class="img_mark2"' in rendered.html
    assert '<span class="hankakuLink">URL</span>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00a9_section_honbun"] == 1
    assert rendered.stats["hc00a9_section_header"] == 1
    assert rendered.stats["hc00a9_mlink_markers"] == 1


def test_hc00a9_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC00A9.dll"),
        code="00A9",
        expected_numeric_index="000000A9.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata", "epwing2HtmlBodydataVertical")),
        exinfo_html_dll="HC00A9.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("mlink.gif", "URL-icon.gif"),
        features={"html_body_renderer": True, "vertical_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC00A9_header_honbun_link_layout" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "gen2011_header_honbun_link_layout" for hook in row["hook_behaviors"])
