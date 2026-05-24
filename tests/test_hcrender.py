import argparse
import io
import json
import re
import sqlite3
from pathlib import Path

from logovista_tools.body_boundaries import (
    target_containing_slice_from_index_boundaries,
    target_needs_index_boundaries,
    target_slice_from_visible_heading_span,
    target_slice_from_reader_and_index_boundaries,
    target_slice_from_index_boundaries,
    target_starts_on_visible_heading_span,
    target_starts_on_section_marker,
)
from logovista_tools.entries import DictionarySource
from logovista_tools.hcrender import (
    HC_RENDER_BASE_CSS,
    HC_RENDER_FINAL_CSS,
    HC_RENDER_BASE_SCRIPT,
    HcIndexHit,
    HcRenderOptions,
    PACKAGE_READER_STYLE,
    _hc_entry_slice_containing_reader,
    _hc_target_slice_reader,
    _has_entry_body_sidecar,
    _hc00aa_entry_slices,
    _normalize_exact_body_fragment_html,
    _normalise_hc_css,
    _prepare_hc_render_assets,
    _renderer_behavior_gaps,
    _rendererdb_args,
    _score_index_row,
    _rewrite_exact_body_asset_refs,
    _rewrite_lved_addr_hrefs,
    _schema_backed_sidecars,
    _effective_renderer_code_for_source,
    _hc_entry_slices_reader,
    _hc0137_entry_slices,
    _source_prefers_vertical,
    _strip_viewer_navigation_images,
    _write_exact_hc_entries_html_from_rendererdb,
    _write_rendererdb_fragment_media,
    _write_hc_html_header,
    render_hc_body,
)
from logovista_tools.colscr import MediaPointer
from logovista_tools.hcprofiles import build_hc_behavior_profile
from logovista_tools.resources import load_image_resource_profile, relative_image_source
from logovista_tools.ssed import SsedRandomReader
from logovista_tools.windows import (
    DICTIONARY_BRIDGE_DLL,
    HcRendererClassification,
    PeImport,
    PeImportDll,
    PeSummary,
    hc_renderer_effects,
)
from logovista_tools.writer import encode_sseddata_literal


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


def test_hc_index_hit_serializes_resolved_navigation_targets() -> None:
    hit = HcIndexHit(
        query="head",
        score=120,
        reason="key:exact",
        component="FKINDEX.DIC",
        key="head",
        target_key="head",
        body_block=10,
        body_offset=2,
        title_block=20,
        title_offset=4,
        page_index=1,
        row_index=2,
        body_target={"component": "HONMON.DIC", "kind": "body", "address_key": "00000010:0002"},
        title_target={"component": "FHTITLE.DIC", "kind": "title", "address_key": "00000020:0004"},
    )

    row = hit.as_dict()

    assert row["body"] == {"block": 10, "offset": 2}
    assert row["body_target"]["kind"] == "body"
    assert row["body_target"]["address_key"] == "00000010:0002"
    assert row["title_target"]["kind"] == "title"


def test_hc_render_colscr_media_placeholder_decodes_bcd_target() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")
    rendered = render_hc_body(b"\x1f\x4d" + payload)

    assert 'class="lv-hc-media"' in rendered.html
    assert 'data-lv-resource="colscr:00000123:0045"' in rendered.html


def test_viewer_navigation_images_are_suppressed_from_renderer_fragments(tmp_path: Path) -> None:
    fragment = (
        '<a href="lved.addr00001287:027a"><img src="Templates/backH.gif" class="img_mark4"></a>'
        '<span>body</span>'
        '<img src="forwardV.gif" class="img_mark4">'
    )

    stripped, removed = _strip_viewer_navigation_images(fragment)
    rewritten = _rewrite_exact_body_asset_refs(fragment, tmp_path)

    assert removed == 2
    assert "backH" not in stripped
    assert "forwardV" not in stripped
    assert "backH" not in rewritten
    assert "forwardV" not in rewritten
    assert "<span>body</span>" in rewritten


def test_hc_render_colscr_media_can_resolve_to_image() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")
    seen: list[MediaPointer] = []

    def resolve(pointer: MediaPointer) -> str:
        seen.append(pointer)
        return "media/colscr/00000123_0045.png"

    rendered = render_hc_body(b"\x1f\x4d" + payload, HcRenderOptions(media_image_resolver=resolve))

    assert '<img class="lv-hc-media"' in rendered.html
    assert 'src="media/colscr/00000123_0045.png"' in rendered.html
    assert 'data-lv-media-status="resolved_image"' in rendered.html
    assert seen == [MediaPointer(payload=payload, block=123, offset=45)]
    assert rendered.stats["media_images"] == 1
    assert rendered.media[0]["target"]["component"] == "COLSCR.DIC"
    assert rendered.media[0]["target"]["block"] == 123
    assert rendered.media[0]["target"]["offset"] == 45


def test_hc_render_resolved_media_suppresses_following_filename_label() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")

    def resolve(pointer: MediaPointer) -> str:
        return "media/colscr/00000123_0045.bmp"

    rendered = render_hc_body(
        b"\x1f\x4d" + payload + jis_fullwidth_ascii("29") + jis_text("費用関数") + jis_fullwidth_ascii(".bmp"),
        HcRenderOptions(media_image_resolver=resolve),
    )

    assert '<img class="lv-hc-media"' in rendered.html
    assert "２９費用関数．ｂｍｐ" not in rendered.html
    assert "２９費用関数．ｂｍｐ" not in rendered.plain
    assert rendered.stats["suppressed_media_filename_label_chars"] == len("２９費用関数．ｂｍｐ")


def test_hc_render_resolved_media_keeps_non_filename_caption() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")

    def resolve(pointer: MediaPointer) -> str:
        return "media/colscr/00000123_0045.bmp"

    rendered = render_hc_body(b"\x1f\x4d" + payload + jis_text("図"), HcRenderOptions(media_image_resolver=resolve))

    assert '<img class="lv-hc-media"' in rendered.html
    assert "図" in rendered.html
    assert "図" in rendered.plain


def test_archsic3_part_of_speech_style_keeps_only_brackets_bold() -> None:
    body = (
        b"\x1f\x10"
        + b"\x1f\xe0\x00\x03"
        + jis_text("（")
        + b"\x1f\xe1"
        + jis_text("自ハ四")
        + b"\x1f\xe0\x00\x03"
        + jis_text("）")
        + b"\x1f\xe1"
        + b"\x1f\x11"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="ARCHSIC3"))

    assert '<span class="archsic3-hinshi"><b>（</b>自ハ四<b>）</b></span>' in rendered.html
    assert "<i>" not in rendered.html


def test_archsic3_uses_legacy_renderer_profile_without_hc_dll() -> None:
    source = DictionarySource("ARCHSIC3", Path("ARCHSIC3.IDX"), "ARCHSIC3", Path("HONMON.DIC"), 2, {})

    assert _effective_renderer_code_for_source(source, None) == "ARCHSIC3"


def test_hc0088_uses_legacy_stylesheet_renderer_without_hc_dll(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_NGYK300K"
    templates = package / "Templates"
    templates.mkdir(parents=True)
    (templates / "00000088.css").write_text(".midashi{background:#97BB71}", encoding="utf-8")
    source = DictionarySource("NGYK300K", package / "NGYK300K.IDX", "NGYK300K", package / "HONMON.DIC", 2, {})

    assert _effective_renderer_code_for_source(source, None) == "0088"


def test_hc0088_renders_mm_category_midashi_and_body_fields() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x00"
        + b"\x1f\xe0\x00\x03"
        + jis_text("壬生寺")
        + b"\x1f\xe1"
        + b"\x1f\x04"
        + jis_text("　　")
        + b"\x1f\x05"
        + jis_text("ミブデラ（")
        + b"\x1f\x10"
        + jis_fullwidth_ascii("mibudera")
        + b"\x1f\x11"
        + jis_text("）〔寺院〕")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("［別名］壬生の地蔵さん")
        + b"\x1f\x04"
        + jis_text("　　")
        + b"\x1f\x05"
        + jis_text("［宗派］律宗")
        + b"\x1f\x04"
        + jis_text("　　")
        + b"\x1f\x05"
        + jis_text("［所在］京都府京都市中京区")
        + b"\x1f\x04"
        + jis_text("　　")
        + b"\x1f\x05"
        + jis_text("［本尊］地蔵菩薩")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0088", image_sources={"mm08": "Templates/MM08.png"}),
    )

    assert '<div class="lv-hc-render hc0088">' in rendered.html
    assert '<div class="midashi"><img src="Templates/MM08.png" class="img_mark" alt="寺院"> 壬生寺</div>' in rendered.html
    assert '<div class="midashi_yomi">ミブデラ(<i>mibudera</i>)</div>' in rendered.html
    assert ".lv-hc-render.hc0088 .midashi {\n  margin-bottom: 0 !important;" in HC_RENDER_FINAL_CSS
    assert ".lv-hc-render.hc0088 .midashi_yomi {\n  margin-top: 0 !important;" in HC_RENDER_FINAL_CSS
    assert "〔寺院〕" not in rendered.html
    assert rendered.html.count('class="hc0088-field"') == 4
    assert '<div class="hc0088-field">［別名］壬生の地蔵さん</div>' in rendered.html
    assert '<div class="hc0088-field">［本尊］地蔵菩薩</div>' in rendered.html
    assert rendered.plain.splitlines() == [
        "壬生寺",
        "ミブデラ(mibudera)",
        "［別名］壬生の地蔵さん",
        "［宗派］律宗",
        "［所在］京都府京都市中京区",
        "［本尊］地蔵菩薩",
    ]
    assert rendered.stats["hc0088_category_images"] == 1
    assert rendered.stats["hc0088_body_fields"] == 4


def test_hc_render_pcmdata_audio_range_decodes_bcd_range() -> None:
    payload = bytes.fromhex("00010000000001230045000001230067")
    body = b"\x1f\x4a" + payload + jis_ascii("P") + b"\x1f\x6a"

    rendered = render_hc_body(body)

    assert 'class="lv-hc-audio"' in rendered.html
    assert 'href="#lv-audio-pcmdata:00000123:0045-00000123:0067"' in rendered.html
    assert 'data-lv-original-href="lved.sond:pcmdata:00000123:0045-00000123:0067"' in rendered.html
    assert re.search(r'(?<![\w-])href="lved\.sond:', rendered.html) is None
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


def test_hc_render_common_media_close_state_is_not_unknown() -> None:
    rendered = render_hc_body(jis_ascii("A") + b"\x1f\x6d" + jis_ascii("B"))

    assert "AB" in rendered.plain
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["common_renderer_state_controls"] == 1


def test_hc_render_unterminated_link_preserves_visible_label_without_gap() -> None:
    rendered = render_hc_body(jis_text("前") + b"\x1f\x42" + jis_text("未終端リンク") + jis_text("後"))

    assert "前未終端リンク後" in rendered.plain
    assert "未終端リンク" in rendered.html
    assert "unterminated_link" not in rendered.named_behavior_gaps
    assert rendered.stats["unterminated_links_recovered"] == 1


def test_hc_render_private_directive_suppresses_visible_text() -> None:
    body = jis_ascii("A") + b"\x1f\xe2\x00\x00" + jis_ascii("B") + b"\x1f\xe3\x00\x00" + jis_ascii("C")

    rendered = render_hc_body(body)

    assert "A" in rendered.plain
    assert "C" in rendered.plain
    assert "B" not in rendered.plain
    assert rendered.private_directives[0]["text_length"] == 1
    assert rendered.stats["private_directives"] == 1


def test_hc_render_private_directive_style_controls_do_not_leak() -> None:
    body = jis_text("前") + b"\x1f\xe2\x00\x00\x1f\x04" + jis_text("中") + b"\x1f\xe3" + jis_text("後")

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00B6"))

    assert rendered.plain == "前後"
    assert rendered.html.count("<span") == rendered.html.count("</span>")
    assert "</span>後" not in rendered.html
    assert rendered.private_directives[0]["text_length"] == 1
    assert rendered.stats["private_style_controls"] == 1
    assert rendered.stats["private_directives"] == 1


def test_hc_render_audio_label_style_controls_do_not_leak_when_replaced_by_icon() -> None:
    body = b"\x1f\x4a" + bytes(18) + b"\x1f\x04" + jis_text("音") + b"\x1f\x6a" + jis_text("後")

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00B6", image_sources={"sound.png": "templates/sound.png"}),
    )

    assert rendered.html.count("<span") == rendered.html.count("</span>")
    assert "</span>後" not in rendered.html
    assert '<img src="templates/sound.png" class="img_mark2">' in rendered.html
    assert "音" not in rendered.html
    assert "後" in rendered.plain


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


def test_hc_render_decodes_fullwidth_html_numeric_charrefs() -> None:
    rendered = render_hc_body(jis_text("＆＃ｘ４Ｅ００；＆＃６５；"))

    assert rendered.plain == "一A"
    assert "一A" in rendered.html
    assert "＆＃" not in rendered.html


def test_hc0131_prefers_unicode_gaiji_mapping_over_bitmap_fallback() -> None:
    rendered = render_hc_body(
        b"\xa1\x31",
        HcRenderOptions(
            renderer_code="0131",
            gaiji_map={"a131": "é"},
            image_sources={"a131": "gaiji/GA16HALF/A131_uni_record_order.bmp"},
        ),
    )

    assert rendered.plain == "é"
    assert "é" in rendered.html
    assert "GA16HALF" not in rendered.html
    assert rendered.stats["gaiji_unicode"] == 1
    assert rendered.stats["hc0131_unicode_gaiji"] == 1


def test_hc00de_suppresses_britannica_state_gaiji_markers() -> None:
    rendered = render_hc_body(b"\xb4\x21\xb4\x22" + jis_text("本文"), HcRenderOptions(renderer_code="00DE"))

    assert "本文" in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'data-gaiji-code="b421"' not in rendered.html
    assert 'data-gaiji-code="b422"' not in rendered.html
    assert rendered.stats["hc_britannica_state_markers"] == 2
    assert rendered.stats["hc_britannica_custom_body_state_markers"] == 1


def test_hc00de_maps_britannica_heading_sections_and_links_without_generic_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\x42"
        + jis_text("リンク")
        + b"\x1f\x62\x00\x00\x00\x01\x00\x20"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00DE"))

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert 'data-lv-section=' not in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert rendered.stats["hc_britannica_state_sections"] == 2
    assert rendered.stats["links"] == 1


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
    assert 'class="lv-hc-section"' not in rendered.html
    assert '<div class="contents">' in rendered.html
    assert rendered.stats["hc013a_contents_blocks"] == 2
    assert rendered.stats["hc013a_example_translation_sections"] == 1
    assert rendered.stats["section_images"] == 2


def test_hc013a_does_not_double_break_before_example_badge() -> None:
    body = (
        b"\x1f\x09\x00\x02"
        + jis_text("揺れ動く")
        + jis_ascii_text(":")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x11"
        + jis_ascii_text("Las hojas")
        + b"\x1f\x09\x00\x12"
        + jis_text("落ち葉")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="013A",
            image_sources={"exam": "Templates/exam.png"},
        ),
    )

    assert "揺れ動く<span class=\"hankaku\">:</span><br><img src=\"Templates/exam.png\" class=\"ex_img\">" in rendered.html
    assert "<br><br><img" not in rendered.html
    assert rendered.stats["hc013a_duplicate_example_lead_breaks_suppressed"] == 1


def test_hc013a_reports_missing_exam_asset_without_faking_image() -> None:
    rendered = render_hc_body(b"\x1f\x09\x00\x11" + jis_ascii("A"), HcRenderOptions(renderer_code="013A"))

    assert "exam.png" not in rendered.html
    assert '<div class="contents">' in rendered.html
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

    assert '<div class="honbun2"><strong><span class="hankaku">a</span>' in rendered.html
    assert '<span class="hankaku">o</span></strong>本文</div>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'data-gaiji-code="b26a"' not in rendered.html
    assert 'data-gaiji-code="b26b"' not in rendered.html
    assert rendered.stats["hc013a_honbun2_markers"] == 1
    assert rendered.stats["hc013a_honbun2_closures"] == 1
    assert rendered.stats["hc013a_suppressed_gaiji_markers"] == 2
    assert rendered.stats["hc013a_nonprinting_controls"] == 1
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert 'class="lv-hc-section"' not in rendered.html


def test_hc013a_maps_heading_marker_classes_and_halfwidth() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + b"\xb2\x62"
        + jis_ascii("a")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("body")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013A"))

    assert '<div class="midashi2 hc013a-midashi">' in rendered.html
    assert '<span class="hankakuMidashi">a</span>' in rendered.html
    assert '<span class="hankaku">body</span>' in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc013a_midashi2_blocks"] == 1
    assert rendered.stats["hc013a_midashi_state_sections"] == 1
    assert rendered.stats["hc013a_body_state_sections"] == 1
    assert rendered.stats["hc013a_hankaku_midashi_spans"] == 1


def test_hc013a_missing_b263_uses_dll_custom_bitmap_template() -> None:
    rendered = render_hc_body(
        b"\x1f\x41\x01\x00" + b"\xb2\x63" + b"".join(jis_ascii(ch) for ch in "abandonar") + b"\x1f\x61",
        HcRenderOptions(renderer_code="013A"),
    )

    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert '<img class="img_dummy" src="dummy.gif"><img src="b263_M.png" style="height:1.0em;" class="img_gaiji_midashi">' in rendered.html
    assert rendered.stats["hc013a_custom_dib_gaiji"] == 1
    assert rendered.stats["hc013a_custom_dib_missing_resource"] == 1
    assert "hc013a_custom_gaiji_bitmap_unresolved" not in rendered.named_behavior_gaps


def test_hc013a_haespjpn_spanish_entry_markers_and_example_lifecycle() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x00"
        + b"\xb2\x61"
        + jis_ascii_text("danzar")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("[dan")
        + b"\xa1\x39"
        + jis_ascii_text("ar]")
        + b"\x1f\xe2\x00\x07"
        + bytes.fromhex("23532351234c2127b148")
        + b"\x1f\xe3"
        + b"\xb1\x3e"
        + b"\xb1\x21"
        + b"\x1f\x09\x00\x11"
        + jis_ascii_text("Las ")
        + b"\x1f\xe0\x00\x01"
        + jis_ascii_text("danzan")
        + b"\x1f\xe1"
        + b"\x1f\x09\x00\x12"
        + jis_text("落ち葉")
        + b"\xb1\x23"
        + jis_text("おせっかい")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="013A",
            gaiji_map={"a139": "θ", "b121": "①", "b123": "③"},
            image_sources={"b148": "Templates/b148.png", "b13e": "Templates/b13e.png", "exam": "Templates/exam.png"},
        ),
    )

    assert '<div class="midashi hc013a-midashi">' in rendered.html
    assert 'data-gaiji-code="b261"' not in rendered.html
    assert '<i class="hc013a-phonetic">θ</i>' in rendered.html
    assert 'src="Templates/b148.png"' in rendered.html
    assert 'src="Templates/b13e.png" alt="b13e" data-gaiji-code="b13e"><br>' in rendered.html
    assert '<i><span class="hankaku">danzan</span></i>' in rendered.html
    assert rendered.html.index("</div>③") < rendered.html.index("おせっかい")
    assert rendered.stats["hc013a_private_marker_images"] == 1
    assert rendered.stats["hc013a_phonetic_italic_gaiji"] == 1


def test_hc013a_haespjpn_japanese_entry_markers_and_gothic_decoration() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x00"
        + b"\xb2\x61"
        + jis_text("再開")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + b"\xb2\x65"
        + jis_text("再開")
        + b"\xb2\x65"
        + jis_ascii_text(" reanudacion ")
        + b"\x1f\xe0\x00\x04"
        + jis_ascii_text("f")
        + b"\x1f\xe1"
        + b"\x1f\x09\x00\x03"
        + b"\xb2\x64"
        + jis_text("〜する")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("［・・・を］")
        + jis_ascii_text(" ")
        + b"\x1f\xe0\x00\x04"
        + jis_ascii_text("algo")
        + b"\x1f\xe1"
        + b"\x1f\x0a"
        + jis_text("次の行")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013A"))

    assert '<div class="midashi4 hc013a-midashi">' in rendered.html
    assert 'data-gaiji-code="b261"' not in rendered.html
    assert 'data-gaiji-code="b265"' not in rendered.html
    assert '<span class="gothic"><b><i><span class="hankaku">f</span></i></b></span>' in rendered.html
    assert '<span class="gothic"><b><i><span class="hankaku">algo</span></i></b></span>' in rendered.html
    assert "〜する</strong>［・・・を］" in rendered.html
    assert "algo</span></i></b></span><br>次の行" in rendered.html


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


def test_hc00c6_closes_partwaku_before_trailing_halfwidth_text() -> None:
    body = b"\xa2\x3c" + jis_ascii_text("ad") + b"\x1f\x04\xa2\x3d" + bytes.fromhex("212121242121") + b"\x1f\x05"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00C6"))

    assert '<span class="partwaku"><span class="lv-hc-halfwidth">ad</span></span>' in rendered.html
    assert '<span class="lv-hc-halfwidth"> , </span>' in rendered.html
    assert '<span class="lv-hc-halfwidth"></span>' not in rendered.html
    assert rendered.stats["hc00c6_style_markers"] == 2
    assert rendered.stats.get("hc00c6_unmatched_style_markers", 0) == 0


def test_hc00c6_handles_supab_marker_when_halfwidth_wrapped() -> None:
    body = b"\x1f\x04\xa2\x44\x1f\x05" + jis_ascii("B")

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00C6"))

    assert rendered.html == '<div class="lv-hc-render"><sup class="supAB">B</sup></div>'
    assert '<span class="lv-hc-halfwidth"></span>' not in rendered.html
    assert rendered.stats["hc00c6_supab_markers"] == 2
    assert rendered.stats["hc00c6_supab_halfwidth_markers"] == 1
    assert rendered.stats["hc00c6_supab_halfwidth_labels"] == 1


def test_hc00c6_suppresses_empty_halfwidth_pair() -> None:
    body = b"\x1f\x04\x1f\x05" + b"\xb1\x26"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00C6"))

    assert rendered.html == '<div class="lv-hc-render"><br><hr class="line"></div>'
    assert '<span class="lv-hc-halfwidth"></span>' not in rendered.html
    assert rendered.stats["hc00c6_rule_lines"] == 1
    assert rendered.stats["hc00c6_empty_halfwidth_spans_suppressed"] == 1


def test_hc00c6_prefers_unicode_for_text_gaiji_and_expands_headword_macro() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_ascii_text("hand")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x07"
        + jis_ascii_text("a horse 14 ")
        + b"\xa2\x26"
        + jis_ascii_text("s high")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_text("肩の高さ")
        + b"\x1f\x09\x00\x06"
        + b"\xa1\x37\xa1\x7e\xa2\x21"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00C6",
            gaiji_map={"a137": "æ", "a17e": "《", "a221": "》", "a226": "〜"},
            image_sources={
                "a137": "gaiji/A137.bmp",
                "a17e": "gaiji/A17E.bmp",
                "a221": "gaiji/A221.bmp",
                "a226": "gaiji/A226.bmp",
            },
        ),
    )

    assert "ahorse14" in rendered.html
    assert "hand" in rendered.html
    assert "shigh" in rendered.html
    assert "æ《》" in rendered.html
    assert 'data-gaiji-code="a137"' not in rendered.html
    assert 'data-gaiji-code="a226"' not in rendered.html
    assert '<div class="contents"><span class="lv-hc-halfwidth">ahorse14</span>hand<span class="lv-hc-halfwidth">shigh</span><br><span class="exampleyakugo">肩の高さ</span></div>' in rendered.html
    assert rendered.stats["hc00c6_headword_macros"] == 1
    assert rendered.stats["hc00c6_unicode_preferred_markers"] == 3
    assert rendered.stats["hc00c6_example_translation_pairs_joined"] == 1


def test_hc00c6_splits_pronunciation_and_partwaku_from_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + b"\x1f\xe0\x00\x00"
        + jis_ascii_text("hand")
        + b"\x1f\xe1"
        + b"\x1f\x61"
        + jis_ascii_text(" /hand/ ")
        + b"\xa2\x3c"
        + jis_ascii_text("n")
        + b"\xa2\x3d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00C6"))

    assert '<div class="midashi"><b><span class="lv-hc-halfwidth">hand</span></b></div>' in rendered.html
    assert '<div class="hc00c6-pronunciation"><span class="lv-hc-halfwidth">/hand/</span></div>' in rendered.html
    assert '<div class="hc00c6-partwaku-line"><span class="partwaku"><span class="lv-hc-halfwidth">n</span></span></div>' in rendered.html
    assert '<div class="midashi"><b><span class="lv-hc-halfwidth">hand</span></b><span class="lv-hc-halfwidth"> /hand/ </span>' not in rendered.html
    assert rendered.stats["hc00c6_midashi_pronunciation_rows"] == 1


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


def test_hc02be_heading_anchor_control_is_nonprinting() -> None:
    rendered = render_hc_body(b"\x1f\x41\x00\x00" + jis_ascii("H"), HcRenderOptions(renderer_code="02BE"))

    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert "lv-hc-unknown-control" not in rendered.html
    assert rendered.plain == "H"
    assert rendered.stats["hc02be_nonprinting_controls"] == 1


def test_hc02be_internal_links_use_product_line_link_class() -> None:
    body = b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x03\x00\x40"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02BE"))

    assert 'class="lv-hc-link lineLink"' in rendered.html


def test_hc02be_mode_one_decoration_is_italic_part_of_speech() -> None:
    body = b"\x1f\xe0\x00\x01" + jis_ascii("n") + b"\x1f\xe1"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02BE"))

    assert '<i><span class="lv-hc-halfwidth">n</span></i>' in rendered.html
    assert '<b><span class="lv-hc-halfwidth">n</span></b>' not in rendered.html
    assert rendered.stats["hc02be_italic_decoration_spans"] == 1
    assert rendered.stats["hc02be_decoration_closures"] == 1


def test_hc02be_parent_slice_keeps_child_entry_sections() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii_text("denticle")
        + b"\x1f\x09\x01\x01"
        + jis_ascii_text("attached denticle")
        + b"\x1f\x09\x01\x03"
        + jis_text("付着性")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02BE"))

    assert '<div class="ind_0001">' in rendered.html
    assert '<span class="ind_0101">' in rendered.html
    assert '<span class="ind_0103">' in rendered.html
    assert "denticle" in rendered.html
    assert "attacheddenticle" in rendered.html
    assert "付着性" in rendered.html


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


def test_hc02be_renders_fullwidth_wave_accent_composite_markers() -> None:
    rendered = render_hc_body(
        b"\xa1\x29\xa1\x2a",
        HcRenderOptions(
            renderer_code="02BE",
            image_sources={"grave": "Templates/grave.png", "aigu": "Templates/aigu.png"},
        ),
    )

    assert (
        '<span class="nowrap_full">&#xFF5E;<img class="grave_full" src="Templates/grave.png"></span>'
        in rendered.html
    )
    assert (
        '<span class="nowrap_full">&#xFF5E;<img class="aigu_full" src="Templates/aigu.png"></span>'
        in rendered.html
    )
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

    assert '<div class="midashi hc02bc-midashi">' in rendered.html
    assert '<img src="Templates/fukumidashi.png" class="img_mark2">' in rendered.html
    assert '<div class="komidashi hc02bc-komidashi"  style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="honbun" style="margin-left:1.000000em;">' in rendered.html
    assert '<div class="contents" style="text-indent:0em;">' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc02bc_section_divs"] == 4


def test_hc02bc_promotes_first_child_section_to_midashi() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x02" + b"\x1f\x12" + jis_ascii_text("child") + b"\x1f\x13",
        HcRenderOptions(renderer_code="02BC", image_sources={"fukumidashi": "Templates/fukumidashi.png"}),
    )

    assert '<div class="midashi hc02bc-midashi"><b><span class="lv-hc-halfwidth">child</span></b></div>' in rendered.html
    assert "fukumidashi.png" not in rendered.html
    assert "hc02bc" in rendered.html


def test_hc02bc_uses_stedman_decoration_semantics_and_suppresses_section_delimiters() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + b"\x1f\x12" + jis_ascii_text("bold") + b"\x1f\x13"
        + b"\x1f\x0a"
        + b"\x1f\xe0\x00\x01" + jis_ascii_text("italic") + b"\x1f\xe1",
        HcRenderOptions(renderer_code="02BC"),
    )

    assert "<b><span class=\"lv-hc-halfwidth\">bold</span></b>" in rendered.html
    assert "<i><span class=\"lv-hc-halfwidth\">italic</span></i>" in rendered.html
    assert "<em>" not in rendered.html
    assert '<div class="midashi hc02bc-midashi"><b>' in rendered.html
    assert "</div><br>" not in rendered.html
    assert rendered.stats["hc02bc_section_delimiters_suppressed"] == 1


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


def test_hc02bc_b122_can_close_blue_span_without_b125() -> None:
    rendered = render_hc_body(
        b"\xb1\x21" + jis_text("カーシノ") + b"\xb1\x22" + jis_text("、")
        + b"\xb1\x21" + jis_text("癌血病") + b"\xb1\x22",
        HcRenderOptions(renderer_code="02BC"),
    )

    assert '<span class="blue">カーシノ</span>、<span class="blue">癌血病</span>' in rendered.html
    assert rendered.stats["hc02bc_b122_implicit_closures"] == 2
    assert "unterminated_hc02bc_marker_b125" not in rendered.named_behavior_gaps


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


def test_hc02bc_target_slice_expands_child_title_pointer(tmp_path: Path) -> None:
    child_a = b"\x1f\x09\x00\x02" + jis_ascii_text("child A") + b"\x1f\x09\x00\x13" + jis_ascii_text("body A")
    child_b = b"\x1f\x09\x00\x02" + jis_ascii_text("child B") + b"\x1f\x09\x00\x13" + jis_ascii_text("body B")
    parent = b"\x1f\x09\x00\x01" + jis_ascii("P") + child_a + child_b
    expanded = parent + b"\x1f\x09\x00\x01" + jis_ascii("N")
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)
    target = expanded.index(jis_ascii_text("body B")) + 4

    assert _hc_entry_slice_containing_reader(reader, "02BC", target) == (0, len(parent))
    assert _hc_target_slice_reader(reader, "02BC", target) == (len(parent) - len(child_b), len(parent))

    start, end = _hc_target_slice_reader(reader, "02BC", target) or (0, 0)
    rendered = render_hc_body(reader.read(start, end - start), HcRenderOptions(renderer_code="02BC", entry_start_offset=start))
    assert "childB" in rendered.plain
    assert "bodyB" in rendered.plain
    assert "childA" not in rendered.plain


def test_hc0137_slices_iwanami_midashi_sections_as_entries(tmp_path: Path) -> None:
    entry_prefix = b"\x1f\x09\x00\x04" + jis_text("亜") + b"\x1f\x09\x00\x12" + jis_text("つぐ")
    entry_a = b"\x1f\x09\x00\x01" + jis_text("アート") + b"\x1f\x09\x00\x12" + jis_text("芸術")
    entry_b = b"\x1f\x09\x00\x02" + jis_text("アート紙") + b"\x1f\x09\x00\x12" + jis_text("厚紙")
    entry_c = b"\x1f\x09\x00\x03" + jis_text("アーカイブ") + b"\x1f\x09\x00\x12" + jis_text("記録")
    entry_d = b"\x1f\x09\x00\x04" + jis_text("アーベント") + b"\x1f\x09\x00\x12" + jis_text("夜")
    expanded = entry_prefix + entry_a + entry_b + entry_c + entry_d
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)

    assert _hc0137_entry_slices(expanded) == (
        (0, len(entry_prefix)),
        (len(entry_prefix), len(entry_prefix) + len(entry_a)),
        (len(entry_prefix) + len(entry_a), len(entry_prefix) + len(entry_a) + len(entry_b)),
        (
            len(entry_prefix) + len(entry_a) + len(entry_b),
            len(entry_prefix) + len(entry_a) + len(entry_b) + len(entry_c),
        ),
        (len(entry_prefix) + len(entry_a) + len(entry_b) + len(entry_c), len(expanded)),
    )
    reader_slices = list(_hc_entry_slices_reader(reader, "0137"))
    assert reader_slices[:4] == list(_hc0137_entry_slices(expanded))[:4]
    assert reader_slices[4] == (len(entry_prefix) + len(entry_a) + len(entry_b) + len(entry_c), reader.expanded_size)

    prefix_target = entry_prefix.index(jis_text("つぐ"))
    assert _hc_target_slice_reader(reader, "0137", prefix_target) == (0, len(entry_prefix))

    target = len(entry_prefix) + len(entry_a) + entry_b.index(jis_text("厚紙"))
    assert _hc_entry_slice_containing_reader(reader, "0137", target) == (
        len(entry_prefix) + len(entry_a),
        len(entry_prefix) + len(entry_a) + len(entry_b),
    )
    assert _hc_target_slice_reader(reader, "0137", target) == (
        len(entry_prefix) + len(entry_a),
        len(entry_prefix) + len(entry_a) + len(entry_b),
    )

    start, end = _hc_target_slice_reader(reader, "0137", target) or (0, 0)
    rendered = render_hc_body(reader.read(start, end - start), HcRenderOptions(renderer_code="0137", entry_start_offset=start))
    assert "アート紙" in rendered.plain
    assert "厚紙" in rendered.plain
    assert "アート芸術" not in rendered.plain
    assert "アーベント" not in rendered.plain


def test_index_derived_body_boundaries_are_honmon_level(tmp_path: Path) -> None:
    expanded = (
        b"\x1f\x09\x00\x01"
        + jis_text("親")
        + b"\x1f\x09\x00\x12"
        + jis_text("親本文")
        + b"\x1f\x09\x00\x02"
        + jis_text("子")
        + b"\x1f\x09\x00\x12"
        + jis_text("子本文")
    )
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)
    child_start = expanded.index(b"\x1f\x09\x00\x02")

    assert target_starts_on_section_marker(reader, child_start)
    assert not target_needs_index_boundaries(reader, 0)
    assert target_needs_index_boundaries(reader, child_start)
    assert target_slice_from_index_boundaries((0, child_start), child_start, fallback_end=len(expanded)) == (
        child_start,
        len(expanded),
    )
    assert target_slice_from_index_boundaries((0, child_start), child_start + 4, fallback_end=len(expanded)) is None
    assert _hc_target_slice_reader(reader, None, 0, boundary_offsets=(0, child_start)) == (0, reader.expanded_size)
    assert target_slice_from_reader_and_index_boundaries(reader, child_start, (0, child_start)) == (
        child_start,
        reader.expanded_size,
    )
    assert _hc_target_slice_reader(reader, None, child_start, boundary_offsets=(0, child_start)) == (child_start, reader.expanded_size)


def test_markerless_stream_uses_index_boundary_containing_text_target(tmp_path: Path) -> None:
    first = b"\x1f\x09\x00\x02" + jis_text("最初") + b"\x1f\x0a"
    second_start = len(first)
    second = b"\x1f\x09\x00\x02" + jis_text("別々") + b"\x1f\x0a" + jis_text("例文")
    expanded = first + second
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)
    text_target = second_start + second.index(jis_text("例文"))

    assert target_needs_index_boundaries(reader, text_target)
    assert target_containing_slice_from_index_boundaries(
        (0, second_start),
        text_target,
        containing_start=0,
        fallback_end=reader.expanded_size,
    ) == (second_start, reader.expanded_size)
    assert target_slice_from_reader_and_index_boundaries(reader, text_target, (0, second_start)) == (
        second_start,
        reader.expanded_size,
    )
    assert _hc_target_slice_reader(reader, None, text_target, boundary_offsets=(0, second_start)) == (
        second_start,
        reader.expanded_size,
    )


def test_visible_heading_span_target_slices_to_next_peer_heading(tmp_path: Path) -> None:
    intro = b"\x1f\x09\x00\x01" + jis_text("親")
    first_heading = b"\x1f\x41\x01\x00" + jis_text("見出し一") + b"\x1f\x61"
    first_body = b"\x1f\x0a" + jis_text("本文一")
    second_heading = b"\x1f\x41\x01\x00" + jis_text("見出し二") + b"\x1f\x61"
    second_body = b"\x1f\x0a" + jis_text("本文二")
    expanded = intro + first_heading + first_body + second_heading + second_body
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)
    target = len(intro)
    next_target = len(intro) + len(first_heading) + len(first_body)

    assert target_starts_on_visible_heading_span(reader, target)
    assert target_needs_index_boundaries(reader, target)
    assert target_slice_from_visible_heading_span(
        reader,
        target,
        containing_start=0,
        fallback_end=reader.expanded_size,
    ) == (target, next_target)
    assert target_slice_from_reader_and_index_boundaries(reader, target, None) == (target, next_target)
    assert _hc_target_slice_reader(reader, None, target, boundary_offsets=None) == (target, next_target)


def test_empty_heading_span_target_is_not_generic_boundary(tmp_path: Path) -> None:
    anchor_stub = b"\x1f\x09\x00\x01\x1f\x41\x01\x00\x1f\x61"
    body = b"\x1f\x09\x00\x01" + jis_text("本文")
    expanded = anchor_stub + body
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)
    empty_heading = 4

    assert not target_starts_on_visible_heading_span(reader, empty_heading)
    assert not target_needs_index_boundaries(reader, empty_heading)
    assert target_slice_from_reader_and_index_boundaries(reader, empty_heading, None) is None


def test_hc0065_target_slice_skips_anchor_stub(tmp_path: Path) -> None:
    anchor_stub = b"\x1f\x09\x00\x01\x1f\x41\x01\x00\x1f\x61"
    body = b"\x1f\x09\x00\x01" + jis_ascii_text("oh") + b"\x1f\x09\x00\x02" + jis_ascii_text("definition")
    next_entry = b"\x1f\x09\x00\x01" + jis_ascii_text("next")
    expanded = anchor_stub + body + next_entry
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(expanded, start_block=0, kind=0))
    reader = SsedRandomReader(path)

    assert _hc_entry_slice_containing_reader(reader, "0065", 4) == (0, len(anchor_stub))
    assert _hc_target_slice_reader(reader, "0065", 4) == (len(anchor_stub), len(anchor_stub) + len(body))


def test_hc02bc_resolved_media_suppresses_internal_image_label() -> None:
    payload = b"\x00" * 12 + bytes.fromhex("00000001 0002")

    def resolve(pointer: MediaPointer) -> str | None:
        assert pointer.block == 1
        assert pointer.offset == 2
        return "media/colscr/00000001_0002.bmp"

    rendered = render_hc_body(
        b"\x1f\x09\x00\x05\x1f\x4d" + payload + jis_text("画像") + b"\x1f\x6d",
        HcRenderOptions(renderer_code="02BC", media_image_resolver=resolve),
    )

    assert 'src="media/colscr/00000001_0002.bmp"' in rendered.html
    assert "画像" not in rendered.html
    assert "画像" not in rendered.plain
    assert rendered.stats["hc02bc_suppressed_media_label_chars"] == 2


def test_hc012e_maps_kanji_sections_and_hitsujun_image() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x08"
        + jis_text("筆順")
        + b"\x1f\x4d"
        + bytes.fromhex("000000000000000000000000000001230045")
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
    assert '<img src="templates/hitsujun.png" class="img_gaiji"><br><span class="hc012e-hitsujun-animation"' in rendered.html
    assert '<span class="hc012e-hitsujun-animation" data-lv-role="stroke-order">' in rendered.html
    assert "筆順" not in rendered.html
    assert '<div class="bushu">' in rendered.html
    assert '<table class="table_itaiji_2"><tr><td><div class="Itaiji">' in rendered.html
    assert '</div></td><td><div class="honbun">' in rendered.html
    assert '</div></td></tr></table><br>' in rendered.html
    assert rendered.stats["hc012e_hitsujun_sections"] == 1
    assert rendered.stats["hc012e_suppressed_hitsujun_label_chars"] == 2
    assert rendered.stats["hc012e_table_cell_transitions"] == 1
    assert rendered.stats["hc012e_table_closures"] == 1


def test_hc012e_suppresses_internal_counter_section_and_groups_kanji_summary() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x07"
        + jis_text("長")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x20"
        + jis_ascii_text("12955")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x14"
        + jis_text("部首")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x26"
        + jis_text("常用音"),
        HcRenderOptions(renderer_code="012E"),
    )

    assert "12955" not in rendered.html
    assert '<div class="hc012e-kanji-summary">' in rendered.html
    assert '<div class="hc012e-kanji-main">' in rendered.html
    assert '<div class="hc012e-kanji-meta">' in rendered.html
    assert rendered.html.index('class="hc012e-kanji-main"') < rendered.html.index('class="hc012e-kanji-meta"')
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc012e_suppressed_sections"] == 1
    assert rendered.stats["hc012e_suppressed_section_controls"] >= 1
    assert rendered.stats["hc012e_suppressed_section_text"] == 5


def test_hc013f_decodes_fullwidth_conjugation_table_as_collapsible_html() -> None:
    table = (
        "＜ｔａｂｌｅ　ｂｏｒｄｅｒ＝”２”　ｓｔｙｌｅ＝”ｗｒｉｔｉｎｇ−ｍｏｄｅ：ｔｂ−ｒｌ”＞"
        "＜ｔｒ＞＜ｔｄ＞基本形＜／ｔｄ＞＜ｔｄ＞未然形＜／ｔｄ＞＜／ｔｒ＞"
        "＜ｔｒ＞＜ｔｄ＞たいぎなり"
    )
    tail = "（たいぎに）＜／ｔｄ＞＜ｔｄ＞たいぎなら＜／ｔｄ＞＜／ｔｒ＞＜／ｔａｂｌｅ＞"
    rendered = render_hc_body(
        jis_text(table) + b"\x1f\x0a" + jis_text(tail),
        HcRenderOptions(renderer_code="013F", image_sources={"katu": "templates/katu.png"}),
    )

    assert '<details class="hc013f-katu">' in rendered.html
    assert '<img src="templates/katu.png" class="img_hinshi" alt="活用表">' in rendered.html
    assert '<table class="hc013f-katu-table" border="2" style="writing-mode:tb-rl">' in rendered.html
    assert "</tr><br><tr>" not in rendered.html
    assert "たいぎなり<br>（たいぎに）" in rendered.html
    assert "＜ｔａｂｌｅ" not in rendered.html
    assert rendered.stats["hc013f_katu_tables"] == 1


def test_hc013f_treats_zakukog_marker_gaiji_as_renderer_controls() -> None:
    rendered = render_hc_body(
        bytes.fromhex("b15b")
        + jis_text("たいぎ")
        + bytes.fromhex("b232")
        + jis_text("（・なり）")
        + bytes.fromhex("b233")
        + bytes.fromhex("b162")
        + jis_text("〔名〕")
        + bytes.fromhex("b163")
        + bytes.fromhex("b121")
        + jis_text("重大な事柄。")
        + bytes.fromhex("b122")
        + jis_text("面倒なこと。")
        + jis_text("餅舟")
        + bytes.fromhex("b15c")
        + jis_text("モチブネ")
        + bytes.fromhex("b15e")
        + jis_text("（もちぶね）"),
        HcRenderOptions(renderer_code="013F"),
    )

    assert "たいぎ<sub>（・なり）</sub>" in rendered.html
    assert "〔名〕" in rendered.html
    assert "❶重大な事柄。❷面倒なこと。" in rendered.html
    assert "餅舟モチブネ（もちぶね）" in rendered.html
    for code in ("b121", "b122", "b15b", "b15c", "b162", "b163", "b15e", "b232", "b233"):
        assert f'data-gaiji-code="{code}"' not in rendered.html
    assert rendered.stats["hc013f_sense_number_markers"] == 2
    assert rendered.stats["hc013f_suppressed_gaiji_markers"] == 5
    assert rendered.stats["hc013f_subscript_markers"] == 2


def test_hc013f_suppresses_post_colscr_alt_label_before_reference_link() -> None:
    media_payload = bytes.fromhex("000000000000000000000000000442531574")
    link_target = bytes.fromhex("000090761808")
    rendered = render_hc_body(
        b"\x1f\x09\x00\x11"
        + jis_text("⇒")
        + b"\x1f\x4d"
        + media_payload
        + jis_text("かはたけ【河竹】")
        + b"\x1f\x42"
        + bytes.fromhex("b229")
        + jis_text("せいりゃうでん")
        + b"\x1f\x62"
        + link_target,
        HcRenderOptions(
            renderer_code="013F",
            image_sources={"b229": "templates/b229.png"},
            media_image_resolver=lambda pointer: "media/colscr/00044253_1574.bmp",
        ),
    )

    assert "かはたけ" not in rendered.html
    assert "河竹" not in rendered.html
    assert 'src="media/colscr/00044253_1574.bmp" alt=""><br><a class="lv-hc-link"' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image" src="templates/b229.png" alt="b229" data-gaiji-code="b229">せいりゃうでん' in rendered.html
    assert rendered.stats["hc013f_media_reference_breaks"] == 1
    assert rendered.stats["hc013f_media_alt_labels_suppressed"] == 1
    assert rendered.stats["hc013f_suppressed_media_alt_label_chars"] == len("かはたけ【河竹】")


def test_hc_render_final_css_keeps_links_green_after_package_css() -> None:
    assert "#5da24c" in HC_RENDER_FINAL_CSS
    assert "!important" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry a[href]" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry .cref" in HC_RENDER_FINAL_CSS


def test_hc_css_normalization_and_final_css_keep_dconci_red_classes() -> None:
    assert "color:#FF0000;" in _normalise_hc_css(".red { color:FF0000; }")
    assert ":where(.lv-hc-render .red)" in HC_RENDER_FINAL_CSS
    assert ":where(.lv-hc-render .midashi_word.red)" in HC_RENDER_FINAL_CSS
    assert ":where(.lv-hc-render .jerank1)" in HC_RENDER_FINAL_CSS
    assert "color: #ff0000 !important;" in HC_RENDER_FINAL_CSS


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
    assert '</div></td></tr></table></div></div><table class="table_itaiji_2"><tr><td><div class="Itaiji">' in rendered.html
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


def test_hc012e_maps_decoration_to_bold_and_indents_onkun_children() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x26"
        + b"\x1f\x12"
        + jis_text("チョウ")
        + b"\x1f\x13"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x25"
        + jis_text("《音訓》")
        + b"\x1f\x12"
        + jis_text("チョウ")
        + b"\x1f\x13",
        HcRenderOptions(renderer_code="012E"),
    )

    assert "<b>チョウ</b>" in rendered.html
    assert "<em>チョウ</em>" not in rendered.html
    assert '<div class="honbun" style="margin-left:1.000000em;text-indent:-1.000000em;">《音訓》<b>チョウ</b></div>' in rendered.html
    assert "</div><br>" not in rendered.html


def test_hc012e_keeps_tsukaiwake_column_open_until_next_column_or_close() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x53"
        + b"\xb2\x29"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x67"
        + jis_text("チョウカン")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x66"
        + jis_text("＝長間。")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x53"
        + b"\xb2\x2a"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x67"
        + jis_text("のどか"),
        HcRenderOptions(renderer_code="012E", image_sources={"b229": "Gaijitemp/B229.png", "b22a": "Gaijitemp/B22A.png"}),
    )

    assert '<div class="column_Tsukaiwake">' in rendered.html
    assert rendered.html.count('<div class="column_Tsukaiwake">') == 2
    assert 'Gaijitemp/B229.png' in rendered.html
    assert 'Gaijitemp/B22A.png' in rendered.html
    assert re.search(r'<div class="column_Tsukaiwake">.*チョウカン<br>＝長間。.*</div>', rendered.html) is not None
    assert rendered.stats["hc012e_column_inner_sections"] == 3
    assert rendered.stats["hc012e_column_linebreaks"] >= 3


def test_hc012e_closes_tsukaiwake_column_before_next_entry_section() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x53"
        + b"\xb2\x2a"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x67"
        + jis_text("のどか")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x66"
        + jis_text("＝長間。")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\x41\x01\x60"
        + jis_text("［長跪］")
        + b"\x1f\x61",
        HcRenderOptions(renderer_code="012E", image_sources={"b22a": "Gaijitemp/B22A.png"}),
    )

    assert re.search(r'<div class="column_Tsukaiwake">.*＝長間。<br></div><div class="midashi">［長跪］</div>', rendered.html) is not None


def test_hc012e_renders_b236_b237_as_bracket_side_images_outside_itaiji_table() -> None:
    rendered = render_hc_body(
        b"\xb2\x36" + jis_text("同字") + b"\xb2\x37",
        HcRenderOptions(renderer_code="012E", image_sources={"b236": "Gaijitemp/B236.png", "b237": "Gaijitemp/B237.png"}),
    )

    assert 'src="Gaijitemp/B236.png"' in rendered.html
    assert 'src="Gaijitemp/B237.png"' in rendered.html
    assert rendered.html.index('Gaijitemp/B236.png') < rendered.html.index("同字") < rendered.html.index('Gaijitemp/B237.png')
    assert rendered.stats["hc012e_bracket_side_markers"] == 1


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


def test_hc012f_keeps_related_link_punctuation_inline() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x05"
        + b"\x1f\x42"
        + jis_text("家督相続")
        + b"\x1f\x62\x00\x00\x02\x65\x01\x34"
        + b"\x1f\x09\x00\x02"
        + jis_text("、")
        + b"\x1f\x09\x00\x05"
        + b"\x1f\x42"
        + jis_text("祭祀相続")
        + b"\x1f\x62\x00\x00\x08\x80\x16\x96"
        + b"\x1f\x09\x00\x02"
        + jis_text("、")
        + b"\x1f\x09\x00\x05"
        + b"\x1f\x42"
        + jis_text("身分相続")
        + b"\x1f\x62\x00\x00\x21\x93\x01\x12"
        + b"\x1f\x0a",
        HcRenderOptions(
            renderer_code="012F",
            image_sources={"link_2": "Templates/link_2.png"},
        ),
    )

    assert '<div class="honbun hc012f-related-links">' in rendered.html
    assert "</a></div><div class=\"honbun\">、</div>" not in rendered.html
    assert "家督相続</a>、" in rendered.html
    assert "祭祀相続</a>、" in rendered.html
    assert "身分相続</a></div>" in rendered.html
    assert rendered.html.count('<img src="Templates/link_2.png" class="img_gaiji">') == 3
    assert rendered.stats["hc012f_related_link_separators"] == 2
    assert rendered.stats["hc012f_related_link_groups"] == 1


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


def test_hc0131_groups_example_sections_and_suppresses_hidden_state_break() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x02"
        + jis_fullwidth_ascii("/pron/")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x30"
        + b"\x1f\xe2\x00\x07"
        + jis_fullwidth_ascii("L")
        + b"\x1f\xe3"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("━")
        + b"\x1f\xe0\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("n")
        + b"\x1f\x05"
        + b"\x1f\xe1"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x17"
        + jis_text("・")
        + jis_fullwidth_ascii("example one.")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x17"
        + jis_text("・")
        + jis_fullwidth_ascii("example two.")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_fullwidth_ascii("1"),
        HcRenderOptions(renderer_code="0131"),
    )

    assert "／ｐｒｏｎ／<br>━" in rendered.html
    assert "<br><br>━" not in rendered.html
    assert '<i><span class="hankaku">n</span></i>' in rendered.html
    assert rendered.html.count('<div class="contents">') == 1
    assert "・ｅｘａｍｐｌｅｏｎｅ．" in rendered.html
    assert "・ｅｘａｍｐｌｅｔｗｏ．" in rendered.html
    assert rendered.html.index("・ｅｘａｍｐｌｅｏｎｅ．") < rendered.html.index("・ｅｘａｍｐｌｅｔｗｏ．")
    assert rendered.html.index("・ｅｘａｍｐｌｅｔｗｏ．") < rendered.html.index('<div class="content_IND4"')
    assert rendered.stats["hc0131_example_blocks"] == 1
    assert rendered.stats["hc0131_example_sections"] == 2
    assert rendered.stats["hc0131_state_line_breaks_suppressed"] == 1
    assert rendered.stats["hc0131_italic_decoration_spans"] == 1


def test_hc0131_splits_first_subsense_from_parent_number() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x04"
        + b"\x1f\xe0\x00\x00"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("1")
        + b"\x1f\x05"
        + b"\x1f\xe1"
        + b"\x1f\x04"
        + b"\x21\x21"
        + b"\x1f\x05"
        + b"\x1f\xe0\x00\x00"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("a")
        + b"\x1f\x05"
        + b"\x1f\xe1"
        + b"\x1f\x04"
        + b"\x21\x21"
        + jis_fullwidth_ascii("body")
        + b"\x1f\x05"
        + b"\x1f\x0a",
        HcRenderOptions(renderer_code="0131"),
    )

    assert '<span class="hc0131-sense-number">1</span></div><div class="content_IND5">' in rendered.html
    assert '<div class="content_IND5"><b><span class="hankaku">a</span></b>' in rendered.html
    assert rendered.stats["hc0131_first_subsense_splits"] == 1


def test_hc0131_keeps_example_decoration_bold_outside_pos_sections() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x17"
        + jis_text("・")
        + b"\x1f\xe0\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("loan")
        + b"\x1f\x05"
        + b"\x1f\xe1",
        HcRenderOptions(renderer_code="0131"),
    )

    assert '<div class="contents">・<b><span class="hankaku">loan</span></b></div>' in rendered.html
    assert rendered.stats["hc0131_bold_decoration_spans"] == 1


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


def test_hc009d_merges_gakken_toc_sections_into_one_line() -> None:
    body = (
        b"\x1f\x09\x00\x12"
        + b"\xb1\x24"
        + jis_text("目次")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x13"
        + b"\xb1\x2e"
        + jis_text("参列する")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x14"
        + b"\xb1\x2f"
        + jis_text("会葬当日")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="009D", image_sources={"b124": "Templates/b124.gif"}))

    assert '<div class="lineinfo12 hc009d-toc-line">' in rendered.html
    assert "目次" in rendered.html
    assert "参列する" in rendered.html
    assert "会葬当日" in rendered.html
    assert rendered.html.index("目次") < rendered.html.index("参列する") < rendered.html.index("会葬当日")
    toc_html = rendered.html.split('<div class="lineinfo12 hc009d-toc-line">', 1)[1].split("</div>", 1)[0]
    assert "<br>" not in toc_html
    assert rendered.stats["hc009d_toc_lines"] == 1
    assert rendered.stats["hc009d_toc_linebreaks_suppressed"] == 3


def test_hc009d_private_box_directive_renders_black_label() -> None:
    box_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("BOX:2") + b"\x1f\xe3"
    box_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("BOX:S") + b"\x1f\xe3"

    rendered = render_hc_body(box_start + jis_text("順番") + box_end + jis_text("一般参列者"), HcRenderOptions(renderer_code="009D"))

    assert '<span class="kuroKakomiShiroNuki hc009d-box-label">順番</span>' in rendered.html
    assert "一般参列者" in rendered.html
    assert rendered.stats["hc009d_box_label_starts"] == 1
    assert rendered.stats["hc009d_box_label_ends"] == 1


def test_hc009d_private_box_directive_renders_gray_topic_label() -> None:
    box_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("BOX:1") + b"\x1f\xe3"
    box_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("BOX:S") + b"\x1f\xe3"

    rendered = render_hc_body(
        b"\x1f\x09\x00\x03" + box_start + jis_text("●焼香はしきたりに従う") + box_end,
        HcRenderOptions(renderer_code="009D"),
    )

    assert '<span class="kuroKakomi hc009d-gray-box-label">●焼香はしきたりに従う</span>' in rendered.html
    assert rendered.stats["hc009d_gray_box_label_starts"] == 1
    assert rendered.stats["hc009d_box_label_ends"] == 1


def test_hc009d_private_ruby_directive_renders_furigana() -> None:
    ruby_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3"
    ruby_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:S") + jis_text("まっこう") + b"\x1f\xe3"

    rendered = render_hc_body(ruby_start + jis_text("抹香") + ruby_end, HcRenderOptions(renderer_code="009D"))

    assert '<ruby class="ruby7"><rb class="rb7">抹香</rb>' in rendered.html
    assert '<rt class="rt7">まっこう</rt>' in rendered.html
    assert rendered.stats["hc009d_ruby_starts"] == 1
    assert rendered.stats["hc009d_ruby_ends"] == 1


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


def test_hc012d_yindex_toggle_suppresses_label_and_wraps_field() -> None:
    body = (
        b"\x1f\x09\x00\x07"
        + jis_text("用例引き")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_ascii("A")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x05"
        + jis_text("Menu")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("Body")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="012D",
            image_sources={"youreioff": "Templates/youreioff.png", "youreion": "Templates/youreion.png"},
            entry_start_offset=0x1000,
        ),
    )

    assert "用例引き" not in rendered.html
    assert '<a name="icon1000"></a><a href="#icon1000">' in rendered.html
    assert 'src="Templates/youreioff.png" id="1000" class="yindex_icon" onclick="showIndex(id);"' in rendered.html
    assert 'data-lv-on-src="Templates/youreion.png"' in rendered.html
    assert '<div class="yindex_field" id="field1000" style="display:none"><div class="yindex_midashi">' in rendered.html
    assert '</div><div class="honbun">' in rendered.html
    assert rendered.stats["hc012d_yindex_toggles"] == 1
    assert rendered.stats["hc012d_yindex_fields"] == 1
    assert rendered.stats["hc012d_yindex_field_closures"] == 1
    assert rendered.stats["hc012d_yindex_label_suppressed"] == 1


def test_hc012d_uses_line_link_and_link_k_marker() -> None:
    body = b"\x22\x2a" + b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="012D", image_sources={"link_k": "Templates/link_k.png"}))

    assert '<img class="lv-hc-gaiji gaiji" src="Templates/link_k.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0030"' in rendered.html
    assert rendered.stats["hc012d_inline_image_markers"] == 1


def test_hc012d_uses_kaisetsu_m_image_for_diamond_marker() -> None:
    rendered = render_hc_body(
        b"\x22\x21" + jis_text("「音連") + b"\x1f\x06" + jis_text("つ") + b"\x1f\x07" + jis_text("れる」"),
        HcRenderOptions(renderer_code="012D", image_sources={"kaisetsu_m": "Templates/kaisetsu_m.png"}),
    )

    assert '<img class="lv-hc-gaiji gaiji" src="Templates/kaisetsu_m.png" alt="◆" data-gaiji-code="2221">' in rendered.html
    assert ">◆" not in rendered.html
    assert "「音連" in rendered.html
    assert rendered.stats["hc012d_inline_image_markers"] == 1


def test_hc_render_asset_preparation_registers_template_image_names(tmp_path: Path) -> None:
    package = tmp_path / "DICT"
    templates = package / "Templates"
    templates.mkdir(parents=True)
    (package / "DICT.IDX").write_bytes(b"")
    (package / "HONMON.DIC").write_bytes(b"")
    (templates / "kaisetsu_m.png").write_bytes(b"png")
    (templates / "backH.gif").write_bytes(b"viewer chrome")

    source = DictionarySource("DICT", package / "DICT.IDX", "DICT", package / "HONMON.DIC", 1, {})
    output_sources, _templates, _stylesheet, _copied, _assets = _prepare_hc_render_assets(source, tmp_path / "out", None)

    assert output_sources["kaisetsu_m"] == "Templates/kaisetsu_m.png"
    assert output_sources["kaisetsu_m.png"] == "Templates/kaisetsu_m.png"
    assert output_sources["templates/kaisetsu_m.png"] == "Templates/kaisetsu_m.png"
    assert "backh" not in output_sources
    assert "backh.gif" not in output_sources
    assert not (tmp_path / "out" / "Templates" / "backH.gif").exists()


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


def test_hc0145_groups_reader_example_lines_until_next_definition() -> None:
    body = (
        b"\x1f\x09\x00\x04"
        + jis_ascii("a")
        + jis_text("語義")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_ascii_text("sourceone")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + jis_text("訳一")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_ascii_text("sourcetwo")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + jis_text("訳二")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x05"
        + jis_ascii("b")
        + jis_text("次の語義")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_ascii_text("sourcethree")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x09"
        + jis_text("訳三")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0145"))

    assert rendered.html.count('<div class="contents" style="text-indent:0em;">') == 2
    assert (
        '<div class="contents" style="text-indent:0em;">'
        '<span class="lv-hc-halfwidth">sourceone</span> 訳一'
        '<br><span class="lv-hc-halfwidth">sourcetwo</span> 訳二</div>'
    ) in rendered.html
    assert (
        '<div class="contents" style="text-indent:0em;">'
        '<span class="lv-hc-halfwidth">sourcethree</span> 訳三</div>'
    ) in rendered.html
    assert "</div>&nbsp;" not in rendered.html
    assert rendered.stats["hc0145_example_blocks"] == 2
    assert rendered.stats["hc0145_example_pair_joins"] == 3
    assert rendered.stats["hc0145_example_pair_breaks"] == 1


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
    assert '<i><span class="lv-hc-halfwidth">B</span></i>' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0145_literal_markers"] == 8
    assert rendered.stats["hc0145_style_markers"] == 2
    assert rendered.stats["hc0145_noop_markers"] == 2


def test_hc0145_uses_line_links_and_img_gaiji_class() -> None:
    body = b"\xb9\x21" + b"\x1f\x42" + jis_ascii("L") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0145", image_sources={"b921": "Templates/b921.png"}))

    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/b921.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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


def test_hc0144_joins_source_translation_example_pairs() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x08"
        + jis_ascii_text("source")
        + b"\x1f\x09\x00\x09"
        + jis_text("訳")
        + b"\x1f\x09\x00\x02"
        + jis_text("本文"),
        HcRenderOptions(renderer_code="0144"),
    )

    assert '<div class="contents" style="text-indent:0em;"><span class="lv-hc-halfwidth">source</span> <span class="lv-hc-example-translation-inline">訳</span></div>' in rendered.html
    assert "</div>&nbsp;<div" not in rendered.html
    assert rendered.stats["hc0144_example_translation_pairs_joined"] == 1


def test_hc0144_italicizes_short_bracketed_grammar_labels() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x03"
        + jis_text("［")
        + b"\x1f\xe0\x00\x01"
        + jis_ascii_text("pl.")
        + b"\x1f\xe1"
        + jis_text("］")
        + jis_text(" ")
        + b"\x1f\xe0\x00\x01"
        + jis_ascii_text("dog")
        + b"\x1f\xe1",
        HcRenderOptions(renderer_code="0144"),
    )

    assert '［<i><span class="lv-hc-halfwidth">pl.</span></i>］' in rendered.html
    assert '<b><span class="lv-hc-halfwidth">dog</span></b>' in rendered.html
    assert rendered.stats["hc0144_grammar_labels_italicized"] == 1


def test_hc0145_italicizes_short_angle_grammar_labels() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x03"
        + jis_text("〈")
        + b"\x1f\xe0\x00\x01"
        + jis_ascii_text("that")
        + b"\x1f\xe1"
        + jis_text("〉"),
        HcRenderOptions(renderer_code="0145"),
    )

    assert '〈<i><span class="lv-hc-halfwidth">that</span></i>〉' in rendered.html
    assert '〈<b><span class="lv-hc-halfwidth">that</span></b>〉' not in rendered.html
    assert rendered.stats["hc0145_grammar_labels_italicized"] == 1


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
    assert '<i><span class="lv-hc-halfwidth">B</span></i>' in rendered.html
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html


def test_hc0144_uses_arrow_image_for_part_of_speech_marker() -> None:
    body = b"\xb9\x39" + jis_text("━") + b"\xb9\x24" + jis_ascii_text("n.") + b"\xb9\x25"

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0144", image_sources={"b939": "Templates/b939.png"}),
    )

    assert (
        '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" '
        'src="Templates/b939.png" alt="▶" data-gaiji-code="b939">━'
    ) in rendered.html
    assert '<i><span class="lv-hc-halfwidth">n.</span></i>' in rendered.html
    assert rendered.stats["hc0144_arrow_markers"] == 1


def test_hc0144_prefers_unicode_for_pronunciation_gaiji_over_bitmaps() -> None:
    body = (
        b"\x1f\x04"
        + b"\x23\x64\xa2\x42\x21\x4a\xa2\x7a\x21\x4b\x23\x67\x21\x21\x23\x64\xa2\x46\x23\x67"
        + b"\x1f\x05"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0144",
            gaiji_map={"a27a": "ː"},
            image_sources={"a242": "gaiji/A242.bmp", "a246": "gaiji/A246.bmp"},
        ),
    )

    assert "dɔ́(ː)g dɑ́g" in rendered.html
    assert "gaiji/A242.bmp" not in rendered.html
    assert "gaiji/A246.bmp" not in rendered.html
    assert rendered.stats["hc0144_unicode_preferred_markers"] == 2


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
    assert '<i><span class="lv-hc-halfwidth">B</span></i>' in rendered.html
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
    assert '<i><span class="lv-hc-halfwidth">B</span></i>' in rendered.html
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html


def test_hc0141_inserts_cross_reference_arrow_after_double_pipe_link_marker() -> None:
    body = b"\xa9\x24" + b"\x1f\x42" + jis_ascii_text("intervale") + b"\x1f\x62\x00\x00\x00\x02\x00\x30"

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0141", image_sources={"b939": "Templates/b939.png"}),
    )

    assert (
        '<sup>||</sup><img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" '
        'src="Templates/b939.png" alt="▶" data-gaiji-code="b939"><a class="lv-hc-link lineLink"'
    ) in rendered.html
    assert '<span class="lv-hc-halfwidth">intervale</span></a>' in rendered.html
    assert rendered.stats["hc0141_cross_reference_arrows"] == 1


def test_hc0141_does_not_insert_double_pipe_arrow_after_visible_text() -> None:
    body = (
        b"\xa9\x24"
        + jis_text("《")
        + b"\x1f\x42"
        + jis_ascii_text("intervale")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0141", image_sources={"b939": "Templates/b939.png"}),
    )

    assert "Templates/b939.png" not in rendered.html
    assert "hc0141_cross_reference_arrows" not in rendered.stats


def test_hc0141_groups_example_source_translation_pairs() -> None:
    body = (
        b"\x1f\x09\x00\x28"
        + b"\x1f\x04"
        + jis_text("ａ　ｎｅｗ　ｅｘｐｅｒｉｍｅｎｔ　ｉｎ　ｅｄｕｃａｔｉｏｎ")
        + b"\x1f\x05"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x29"
        + jis_text("教育上の新しい試み")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x28"
        + b"\x1f\x04"
        + jis_text("ｌｅａｒｎ　ｂｙ　ｅｘｐｅｒｉｍｅｎｔ")
        + b"\x1f\x05"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x29"
        + jis_text("実験して知る")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x20"
        + jis_text("次の語義")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0141"))

    assert rendered.html.count('<div class="contents" style="text-indent:0em;">') == 1
    assert (
        '<span class="lv-hc-halfwidth">a new experiment in education</span> '
        '教育上の新しい試み<br><span class="lv-hc-halfwidth">learn by experiment</span> 実験して知る</div>'
    ) in rendered.html
    assert "</div>&nbsp;" not in rendered.html
    assert rendered.stats["hc0141_example_blocks"] == 1
    assert rendered.stats["hc0141_example_pair_joins"] == 2


def test_hc0141_normalizes_visible_fullwidth_ascii_and_italic_mode() -> None:
    body = (
        b"\x1f\x09\x00\x02"
        + jis_fullwidth_ascii("/")
        + b"\x1f\xe0\x00\x01"
        + jis_fullwidth_ascii("r")
        + b"\x1f\xe1"
        + jis_fullwidth_ascii("/")
        + b"\x1f\x09\x00\x24"
        + jis_text("実験する〈")
        + b"\x1f\xe0\x00\x01"
        + b"\x1f\x04"
        + jis_text("ｗｉｔｈ")
        + b"\x1f\xe1"
        + jis_text("　ａ　ｎｅｗ　ｍｅｔｈｏｄ；　")
        + b"\x1f\xe0\x00\x01"
        + jis_text("ｉｎ")
        + b"\x1f\xe1"
        + jis_text("　ｐａｉｎｔｉｎｇ")
        + jis_text("〉")
        + b"\x1f\x0a"
        + b"\x1f\x05"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0141"))

    assert "/<i>r</i>/" in rendered.html
    assert '実験する〈<span class="lv-hc-halfwidth"><i>with</i> a new method; <i>in</i> painting〉</span></div>' in rendered.html
    assert "</div></span>" not in rendered.html
    assert "ｗｉｔｈ" not in rendered.html
    assert rendered.stats["hc0141_italic_decoration_spans"] == 3
    assert rendered.stats["hc0141_halfwidth_closed_before_linebreak"] == 1
    assert rendered.stats["hc0141_fullwidth_ascii_normalized"] > 0


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


def test_hc0190_uses_bcd_sections_and_link_anchor_template_slots() -> None:
    body = (
        b"\x1f\x09\x00\x10"
        + b"\x1f\x42"
        + jis_ascii_text("link")
        + b"\x1f\x62\x00\x00\x00\x02\x08\x08"
        + b"\x1f\x0a"
        + b"\xb1\x21"
    )
    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0190",
            html_templates={"b121": '<p><!--&IND0010;--><img src="tyosya.png"></a></p>'},
        ),
    )

    assert '<a class="lv-hc-link" href="lvaddr://00000002/0808"' in rendered.html
    assert '<img src="tyosya.png"></a>' in rendered.html
    assert "link</a><img" not in rendered.html
    assert rendered.html.count("<a ") == rendered.html.count("</a>")
    assert rendered.stats["hc0190_template_link_prefixes"] == 1
    assert rendered.stats["hc0190_template_placeholders_filled"] == 1


def test_hc0190_playback_template_gets_browser_audio_player() -> None:
    body = b"\x1f\x09\x00\x04" + jis_ascii("T") + b"\xb1\x24"
    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0190",
            html_templates={
                "b124": '<img id="cmdPlay" src="Templates/play.gif" onclick="lvedPlay();">'
                '<iframe src="Templates/honbun.html#0"></iframe><!--&IND0004;-->'
            },
            renderer_assets={
                "hc0190_audio_src": "shizuku.mp3",
                "hc0190_text_sidecar": "shizuku_honbun.txt",
                "hc0190_time_sidecar": "shizuku_time.txt",
                "hc0190_transcript_src": "Templates/honbun.html#0",
            },
        ),
    )

    assert '<audio controls preload="metadata" src="shizuku.mp3"></audio>' in rendered.html
    assert 'class="hc0190-transcript-link" href="Templates/honbun.html#0"' in rendered.html
    assert re.search(r'(?<!original-)onclick="lvedPlay\(\);"', rendered.html) is None
    assert 'data-lv-original-onclick="lvedPlay();"' in rendered.html
    assert rendered.audio[0]["target"]["path"] == "shizuku.mp3"
    assert rendered.audio[0]["role"] == "readaloud_audio"
    assert rendered.stats["hc0190_readaloud_audio_players"] == 1
    assert rendered.stats["hc0190_mci_handlers_preserved_as_metadata"] == 1


def test_hc0190_asset_preparation_rewrites_template_assets_and_copies_readaloud_files(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_SIZK0101"
    (package / "HTMLs").mkdir(parents=True)
    (package / "Templates").mkdir()
    idx = package / "SIZK0101.IDX"
    honmon = package / "HONMON.DIC"
    idx.write_bytes(b"")
    honmon.write_bytes(b"")
    (package / "EXINFO.INI").write_text("[GENERAL]\nHTMLDLL=HC0190.dll\nMP3NAME=shizuku.mp3\n", encoding="cp932")
    (package / "HTMLs" / "b124.html").write_text(
        '<html><body><link href="&cssPath;"><script src="main.js"></script>'
        '<img src="play.gif" onclick="lvedPlay();"><iframe src="honbun.html#0"></iframe></body></html>',
        encoding="cp932",
    )
    for name in ("main.js", "play.gif", "honbun.html"):
        (package / "Templates" / name).write_bytes(name.encode("ascii"))
    (package / "shizuku.mp3").write_bytes(b"ID3")
    (package / "shizuku_honbun.txt").write_bytes(b"\xff\xfeH\x00")
    (package / "shizuku_time.txt").write_bytes(b"\xff\xfe0\x00")
    source = DictionarySource(
        dict_id="SIZK0101",
        idx=idx,
        title="test",
        honmon=honmon,
        honmon_start_block=0,
        gaiji_map={},
        image_sources={},
    )
    renderer = HcRendererClassification(
        path=package / "HC0190.dll",
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
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={},
    )

    _image_sources, html_templates, _stylesheet, copied, renderer_assets = _prepare_hc_render_assets(source, tmp_path / "out", renderer)

    assert 'href="hc-renderer.css"' in html_templates["b124"]
    assert 'src="Templates/main.js"' in html_templates["b124"]
    assert 'src="Templates/play.gif"' in html_templates["b124"]
    assert 'src="Templates/honbun.html#0"' in html_templates["b124"]
    assert renderer_assets["hc0190_audio_src"] == "shizuku.mp3"
    assert renderer_assets["hc0190_text_sidecar"] == "shizuku_honbun.txt"
    assert renderer_assets["hc0190_time_sidecar"] == "shizuku_time.txt"
    assert renderer_assets["hc0190_transcript_src"] == "Templates/honbun.html#0"
    assert (tmp_path / "out" / "shizuku.mp3").read_bytes() == b"ID3"
    assert copied >= 5


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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
    assert '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc009b_section_header"] == 1
    assert rendered.stats["hc009b_section_honbun"] == 1
    assert rendered.stats["hc009b_nonprinting_controls"] == 1


def test_hc009b_ignores_outlier_section_payloads_instead_of_huge_margins() -> None:
    body = b"\x1f\x09\x91\x9e" + jis_text("国基本データ")

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="009B"))

    assert "国基本データ" in rendered.plain
    assert "margin-top:149112px" not in rendered.html
    assert "margin-left:149112px" not in rendered.html
    assert rendered.stats["hc009b_section_ignored_outlier"] == 1


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

    image_sources, _, _, copied, _ = _prepare_hc_render_assets(source, out_dir, renderer)

    assert image_sources["hc009c_thumb_50042100"] == "images_thumb/50042100.png"
    assert image_sources["hc009c_icon_71042100"] == "images_icon/71042100.png"
    assert image_sources["hc009c_hanrei_71042100"] == "images_icon_hanrei/71042100.png"
    assert (out_dir / "images_thumb" / "50042100.png").read_bytes() == b"images_thumb"
    assert (out_dir / "images_icon" / "71042100.png").read_bytes() == b"images_icon"
    assert (out_dir / "images_icon_hanrei" / "71042100.png").read_bytes() == b"images_icon_hanrei"
    assert copied >= 3


def test_hc00a0_asset_preparation_copies_mp3_assets(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_GKBUSINE"
    (package / "HTMLs").mkdir(parents=True)
    (package / "Templates").mkdir()
    (package / "mp3").mkdir()
    idx = package / "GKBUSINE.IDX"
    honmon = package / "HONMON.DIC"
    idx.write_bytes(b"")
    honmon.write_bytes(b"")
    (package / "HTMLs" / "Header.html").write_text("header", encoding="cp932")
    (package / "HTMLs" / "Detail.html").write_text("detail", encoding="cp932")
    (package / "Templates" / "sound.png").write_bytes(b"PNG")
    (package / "Templates" / "000000A0.css").write_text(".x{}", encoding="cp932")
    (package / "mp3" / "0001.mp3").write_bytes(b"ID3")
    source = DictionarySource(
        dict_id="GKBUSINE",
        idx=idx,
        title="test",
        honmon=honmon,
        honmon_start_block=0,
        gaiji_map={},
        image_sources={"sound": "Templates/sound.png"},
    )
    renderer = HcRendererClassification(
        path=package / "HC00A0.dll",
        code="00A0",
        expected_numeric_index="000000A0.idx",
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

    image_sources, html_templates, stylesheet, copied, _ = _prepare_hc_render_assets(source, tmp_path / "out", renderer)

    assert image_sources["sound"] == "Templates/sound.png"
    assert html_templates == {"Header": "header", "Detail": "detail"}
    assert stylesheet == "hc-renderer.css"
    assert (tmp_path / "out" / "mp3" / "0001.mp3").read_bytes() == b"ID3"
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
        + b"\x1f\x09\x00\x50"
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
    assert '<div class="indent50">' in rendered.html
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


def test_hc013d_maps_clickmenu_section_triplet() -> None:
    body = (
        b"\x1f\x09\x00\x70"
        + jis_text("見出し")
        + b"\x1f\x09\x00\x71"
        + jis_text("詳細")
        + b"\x1f\x09\x00\x72"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert 'class="clickmenu"' in rendered.html
    assert 'src="menuoff.png"' in rendered.html
    assert 'style="display:none;"' in rendered.html
    assert "詳細" in rendered.html
    assert "hc013d_unmapped_section_0070" not in rendered.named_behavior_gaps
    assert "hc013d_unmapped_section_0071" not in rendered.named_behavior_gaps
    assert "hc013d_unmapped_section_0072" not in rendered.named_behavior_gaps
    assert rendered.stats["hc013d_clickmenu_titles"] == 1
    assert rendered.stats["hc013d_clickmenu_fields"] == 1
    assert rendered.stats["hc013d_clickmenu_field_closures"] == 1


def test_hc013d_uses_bcd_values_for_medname_list_sections() -> None:
    body = (
        b"\x1f\x09\x00\x20"
        + jis_ascii("A")
        + b"\x1f\x09\x00\x21"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x22"
        + jis_ascii("C")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert '<div class="mednamelist1">' in rendered.html
    assert '<div class="mednamelist2">' in rendered.html
    assert '<div class="mednamelist3">' in rendered.html
    assert "hc013d_unmapped_section_0020" not in rendered.named_behavior_gaps
    assert "hc013d_unmapped_section_0021" not in rendered.named_behavior_gaps
    assert "hc013d_unmapped_section_0022" not in rendered.named_behavior_gaps


def test_hc013d_maps_gray_table_bcd_section_sequence() -> None:
    body = (
        b"\x1f\x09\x00\x31"
        + jis_ascii("A")
        + b"\x1f\x09\x00\x32"
        + jis_ascii("B")
        + b"\x1f\x09\x00\x33"
        + jis_ascii("C")
        + b"\x1f\x09\x00\x34"
        + jis_ascii("D")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert '<table cellpadding="3" cellspacing="1" bgcolor="#666666" style="margin:8px;">' in rendered.html
    assert rendered.html.count("<table") == rendered.html.count("</table>")
    assert rendered.html.count("<tr") == rendered.html.count("</tr>")
    assert rendered.html.count("<td") == rendered.html.count("</td>")
    assert "hc013d_unmapped_section_0031" not in rendered.named_behavior_gaps
    assert rendered.stats["hc013d_gray_tables"] == 1
    assert rendered.stats["hc013d_gray_table_cells"] == 3
    assert rendered.stats["hc013d_gray_table_closures"] == 1


def test_hc013d_maps_product_code_table_bcd_sections() -> None:
    body = b"\x1f\x09\x00\x42" + jis_ascii("A") + b"\x1f\x09\x00\x43" + jis_ascii("B")

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert '<table class="table_pc"><tr class="tr_pc"><td class="td_pc1">' in rendered.html
    assert '</td><td class="td_pc2">' in rendered.html
    assert rendered.html.count("<table") == rendered.html.count("</table>")
    assert "hc013d_unmapped_section_0042" not in rendered.named_behavior_gaps
    assert "hc013d_unmapped_section_0043" not in rendered.named_behavior_gaps
    assert rendered.stats["hc013d_pc_tables"] == 1
    assert rendered.stats["hc013d_pc_table_second_cells"] == 1


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
        + bytes.fromhex("215a403d3a5e3e704a73215b")
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


def test_hc013d_consumes_full_jis_label_macros_without_suffix_leaks() -> None:
    body = (
        bytes.fromhex("215a403d3a5e3e704a73215b")
        + bytes.fromhex("215a3e26494a215b")
        + bytes.fromhex("215a4a2c4e60215b")
        + bytes.fromhex("215a3d684a7d4c5c452a215b")
        + bytes.fromhex("215a32724062215b")
        + bytes.fromhex("215a3b484d513e65244e436d3055215b")
        + bytes.fromhex("2223")
        + jis_text("基本的注意")
        + bytes.fromhex("2223")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="013D",
            image_sources={"midashi1": "Templates/midashi1.png", "midashi3": "Templates/midashi3.png"},
        ),
    )

    assert '<img src="Templates/midashi1.png" class="img_midashi">' in rendered.html
    assert '<img src="Templates/midashi3.png" class="img_midashi">' in rendered.html
    assert '<div class="SubTitle_syouhin">商品</div>' in rendered.html
    assert '<span class="title">分類</span>' in rendered.html
    assert '<span class="title">処方目的</span>' in rendered.html
    assert '<span class="title">解説</span>' in rendered.html
    assert '<span class="title2">基本的注意</span>' in rendered.html
    assert "情報】" not in rendered.html
    assert "】" not in rendered.html
    assert "的注意■" not in rendered.html


def test_hc013d_keeps_drug_category_metadata_inside_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("薬品名")
        + b"\x1f\x61"
        + jis_text("［内服薬５‐１‐２］")
        + b"\x1f\x0a"
        + jis_text("本文")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert '<div class="midashi">薬品名［内服薬５‐１‐２］</div>' in rendered.html
    assert rendered.stats["hc013d_deferred_midashi_ends"] == 1
    assert rendered.stats["hc013d_deferred_midashi_closures"] == 1


def test_hc013d_groups_product_name_price_and_image_sections_into_cards() -> None:
    body = (
        b"\x1f\x09\x00\x06"
        + jis_text("商品一")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x10"
        + jis_text("十円")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x11"
        + jis_text("写真一")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + jis_text("商品二")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("次章")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert rendered.html.count('class="hc013d-product-card"') == 2
    assert '<div class="hc013d-product-card"><span class="med">商品一</span><div class="medprice">十円</div><div class="medimage">写真一</div></div>' in rendered.html
    assert '<div class="hc013d-product-card"><span class="med">商品二</span></div><div class="title3">次章</div>' in rendered.html
    assert rendered.stats["hc013d_product_cards"] == 2
    assert rendered.stats["hc013d_product_card_closures"] == 2
    assert rendered.stats["hc013d_product_linebreaks_suppressed"] == 4
    assert rendered.stats["hc013d_section_linebreaks_suppressed"] == 4


def test_hc013d_suppresses_redundant_block_section_linebreaks() -> None:
    body = (
        b"\x1f\x09\x00\x04"
        + jis_text("一般名")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_text("服用量")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x50"
        + jis_text("本文")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="013D"))

    assert rendered.html == (
        '<div class="lv-hc-render">'
        '<div class="title3">一般名</div>'
        '<div class="medblk">服用量</div>'
        '<div class="indent50">本文</div>'
        "</div>"
    )
    assert rendered.stats["hc013d_section_linebreaks_suppressed"] == 3
    assert rendered.stats.get("line_breaks", 0) == 0


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
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="Templates/2.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="Templates/3.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="Templates/4.png" class="img_icon"/><br>' in rendered.html
    assert '<p class="moji-down">' in rendered.html
    assert '<span class="lv-hc-halfwidth">A</span></p>' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc02c2_section_blocks"] == 5
    assert rendered.stats["hc02c2_section_icons"] == 4


def test_hc_base_css_keeps_renderer_marker_images_small_without_package_css() -> None:
    assert ":where(.lv-hc-render .img_icon)" in HC_RENDER_BASE_CSS
    assert "height: 1em;" in HC_RENDER_BASE_CSS
    assert ":where(.lv-hc-render .img_icon_Small)" in HC_RENDER_BASE_CSS
    assert ":where(.lv-hc-render .img_icon_Big)" in HC_RENDER_BASE_CSS


def test_hc_html_header_links_stylesheet_instead_of_inlining_name() -> None:
    out = io.StringIO()

    _write_hc_html_header(out, stylesheet="hc-renderer.css", vertical=False)

    html = out.getvalue()
    assert '<link rel="stylesheet" href="hc-renderer.css">' in html
    assert "<style>" not in html
    assert "\nhc-renderer.css\n" not in html


def test_hc02c2_preserves_numbered_text_and_section_marker_icon_classes() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x01"
        + jis_text("たらーん")
        + b"\x1f\x09\x00\x02"
        + b"\xb1\x21"
        + jis_text("流れている。")
        + b"\x1f\x09\x00\x07"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("A")
        + b"\x1f\x05"
        + jis_text("：例文"),
        HcRenderOptions(renderer_code="02C2", gaiji_map={"b121": "①"}),
    )

    assert '<div class="midashi">たらーん</div>' in rendered.html
    assert "①" in rendered.html
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<p class="moji-down"><span class="lv-hc-halfwidth">A</span>：例文' in rendered.html


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
    assert 'href="lvaddr://00000002/0030"' in rendered.html


def test_hc02c2_private_ruby_directives() -> None:
    body = (
        b"\x1f\xe2\x00\x01"
        + jis_ascii_text("RUB:")
        + jis_text("始")
        + b"\x1f\xe3"
        + jis_text("垂")
        + b"\x1f\xe2\x00\x01"
        + jis_ascii_text("RUB:")
        + jis_text("終た")
        + b"\x1f\xe3"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02C2"))

    assert '<ruby class="ruby7"><rb class="rb7">垂</rb>' in rendered.html
    assert '<rt class="rt7">た</rt>' in rendered.html
    assert rendered.stats["hc02c2_ruby_starts"] == 1
    assert rendered.stats["hc02c2_ruby_ends"] == 1
    assert rendered.stats["hc02c2_private_directives_handled"] == 2


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


def test_hc02c8_consumes_1f6d_image_anchor_close() -> None:
    rendered = render_hc_body(
        b"\x1f\x4d\x00\x00\x00\x02\x00\x30\x00\x00\x00\x02\x00\x40\x00\x00\x00\x00\x00\x00\x1f\x6d",
        HcRenderOptions(renderer_code="02C8"),
    )

    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["media_placeholders"] == 1
    assert rendered.stats["hc02c8_nonprinting_controls"] == 1
    assert 'data-lv-control="1f4d"' in rendered.html


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


def test_hc004d_maps_midashi_honbun_and_line_links() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("AB")
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("M")
        + b"\x1f\x63\x00\x00\x00\x02\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="004D"))

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert '<div class="honbun">' in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert rendered.stats["hc004d_heading_section_state"] == 1
    assert rendered.stats["hc004d_midashi_blocks"] == 1
    assert rendered.stats["hc004d_honbun_blocks"] == 1
    assert rendered.stats["hc004d_hankaku_spans"] == 3


def test_hc0076_maps_medical_body_sections_links_and_template_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("見出")
        + b"\x1f\x42"
        + jis_ascii("H")
        + b"\x1f\x62\x00\x00\x00\x01\x00\x10"
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("AB")
        + jis_text("本文")
        + bytes.fromhex("2179")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("M")
        + b"\x1f\x63\x00\x00\x00\x02\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0076",
            image_sources={"dummy": "Templates/dummy.gif", "2179": "Templates/2179.gif"},
        ),
    )

    assert '<div class="midashi">見出' in rendered.html
    assert '<div style="margin-left:6px;">' in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert 'class="lv-hc-link lineLink3"' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert '<img src="Templates/dummy.gif" class="img_dummy">' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0076_heading_section_state"] == 1
    assert rendered.stats["hc0076_midashi_blocks"] == 1
    assert rendered.stats["hc0076_margin_sections"] == 2
    assert rendered.stats["hc0076_hankaku_spans"] == 4
    assert rendered.stats["hc0076_template_image_markers"] == 1
    assert rendered.stats["hc0076_nonprinting_controls"] == 1


def test_hc0076_closes_halfwidth_span_when_bold_ends_first() -> None:
    rendered = render_hc_body(
        b"\x1f\xe0\x00\x00\x1f\x04\x23\x3f\x1f\xe1\x1f\x05",
        HcRenderOptions(renderer_code="0076"),
    )

    assert '<b><span class="hankaku">' in rendered.html
    assert "</span></b>" in rendered.html
    assert rendered.html.count("<span") == rendered.html.count("</span>")


def test_hc00c7_maps_lineinfo_sections_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_text("見")
        + b"\x1f\x09\x00\x04"
        + b"\xb1\x21"
        + b"\xb1\x2d"
        + b"\x1f\x09\x00\x16"
        + jis_text("注")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00C7",
            image_sources={
                "dummy": "Templates/dummy.GIF",
                "b121": "Templates/B121.png",
                "b12d": "Templates/B12D.png",
            },
        ),
    )

    assert '<div class="lineinfo1">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo4">' in rendered.html
    assert '<font class="lineinfo22">' in rendered.html
    assert 'src="Templates/B121.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_mark4"' in rendered.html
    assert '<img src="Templates/dummy.GIF" class="img_dummy">' in rendered.html
    assert 'src="Templates/B12D.png"' in rendered.html
    assert 'class="lv-hc-gaiji img_mark"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.stats["hc00c7_lineinfo_sections"] == 2
    assert rendered.stats["hc00c7_contents_body_blocks"] == 1
    assert rendered.stats["hc00c7_img_mark4_images"] == 1
    assert rendered.stats["hc00c7_img_mark_images"] == 1


def test_hc00c7_maps_links_hankaku_fontbold_and_state_controls() -> None:
    body = (
        b"\x1f\x04"
        + jis_fullwidth_ascii("AB")
        + b"\x1f\x05"
        + b"\x1f\x12"
        + jis_text("太")
        + b"\x1f\x13"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x41\x00\x00"
        + b"\x1f\x61"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00C7"))

    assert '<span class="hankaku">AB</span>' in rendered.html
    assert '<font class="fontbold">太</font>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "unknown_control_1f41" not in rendered.named_behavior_gaps
    assert "unknown_control_1f61" not in rendered.named_behavior_gaps
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00c7_hankaku_spans"] == 2
    assert rendered.stats["hc00c7_fontbold_spans"] == 1
    assert rendered.stats["hc00c7_nonprinting_controls"] == 3


def test_hc00c7_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC00C7.dll"),
        code="00C7",
        expected_numeric_index="000000C7.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata", "epwing2HtmlBodydataVertical")),
        exinfo_html_dll="HC00C7.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("B121.png", "B12D.png", "dummy.GIF"),
        features={"html_body_renderer": True, "vertical_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC00C7_lineinfo_template_gaiji_renderer" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "gkknjpzl_lineinfo_template_gaiji_renderer" for hook in row["hook_behaviors"])


def test_hc007d_maps_midashi_margin_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("見出")
        + b"\xb1\x21"
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("AB")
        + jis_text("本文")
        + b"\xb1\x23"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x43"
        + jis_ascii("M")
        + b"\x1f\x63\x00\x00\x00\x02\x00\x40"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="007D",
            image_sources={
                "dummy.gif": "Templates/dummy.GIF",
                "b121": "Templates/B121.gif",
                "b123": "Templates/B123.gif",
            },
        ),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div style="margin-left:6px;">' in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc007d_heading_section_state"] == 1
    assert rendered.stats["hc007d_margin_sections"] == 1
    assert rendered.stats["hc007d_contents_body_blocks"] == 1
    assert rendered.stats["hc007d_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc007d_img_gaiji_images"] == 1
    assert rendered.stats["hc007d_nonprinting_controls"] == 1


def test_hc007d_decoration_state_does_not_leak_random_italics() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x41\x00\x00"
        + b"\x1f\x61"
        + b"\x1f\x10"
        + b"\x1f\xe0\x11\x01"
        + jis_ascii_text("n.")
        + b"\x1f\xe1"
        + b"\x1f\x11"
        + b"\x1f\x0a"
        + b"\x1f\x10"
        + jis_text("《英口語》")
        + b"\x1f\x11"
        + b"\x1f\x10"
        + jis_ascii_text("Nile")
        + b"\x1f\x11"
        + b"\x1f\x10"
        + b"\x1f\xe0\x11\x01"
        + jis_ascii_text("Crocodylus")
        + b"\x1f\xe1"
        + b"\x1f\x11"
        + b"\x1f\x10"
        + b"\x1f\xe0\x11\x03"
        + jis_ascii_text("1")
        + b"\x1f\xe1"
        + b"\x1f\x11"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="007D"))

    assert '<i><span class="hankaku">n.</span></i>' in rendered.html
    assert '<i><span class="hankaku">Crocodylus</span></i>' in rendered.html
    assert '<b><span class="hankaku">1</span></b>' in rendered.html
    assert '<i><b><span class="hankaku">n.</span></b></i>' not in rendered.html
    assert '<i>《英口語》</i>' not in rendered.html
    assert '<i><span class="hankaku">Nile</span></i>' not in rendered.html
    assert '<b><span class="hankaku">Crocodylus</span></b>' not in rendered.html
    assert rendered.stats["hc007d_italic_spans"] == 2
    assert rendered.stats["hc007d_bold_spans"] == 1
    assert rendered.stats["hc007d_state_controls"] >= 8


def test_hc007d_groups_bullet_examples_in_exsample_box() -> None:
    section_prefix = b"\x1f\x09\x00\x02" + b"\x1f\x41\x02\x91" + b"\x1f\x61"
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x02\x91"
        + jis_text("crocodile")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + section_prefix
        + jis_text("2")
        + jis_text("定義")
        + b"\x1f\x0a"
        + section_prefix
        + b"\x1f\x04"
        + jis_text("・")
        + jis_fullwidth_ascii("a crocodile (of school children).")
        + b"\x1f\x05"
        + b"\x1f\x0a"
        + section_prefix
        + b"\x1f\x04"
        + jis_text("・")
        + jis_fullwidth_ascii("proceed in a crocodile.")
        + b"\x1f\x05"
        + b"\x1f\x0a"
        + section_prefix
        + jis_text("3")
        + jis_text("次")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="007D"))

    assert rendered.html.count('<div class="exsample">') == 1
    assert '<span class="hankaku"><div class="exsample">' not in rendered.html
    assert '<div class="exsample"><span class="hankaku">・acrocodile' in rendered.html
    assert '<br><span class="hankaku">・proceedinacrocodile.</span></div>' in rendered.html
    assert rendered.stats["hc007d_example_boxes"] == 1
    assert rendered.stats["hc007d_example_linebreaks_inside_box"] == 1
    assert rendered.stats["hc007d_example_box_closures"] == 1


def test_hc007d_media_image_breaks_before_caption() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")

    def resolve(_pointer: MediaPointer) -> str:
        return "media/colscr/00000123_0045.bmp"

    rendered = render_hc_body(
        b"\x1f\x4d" + payload + jis_ascii_text("crocodile ") + jis_text("【挿絵】"),
        HcRenderOptions(renderer_code="007D", media_image_resolver=resolve),
    )

    assert 'src="media/colscr/00000123_0045.bmp" alt=""><br><span class="hankaku">crocodile</span>【挿絵】' in rendered.html
    assert rendered.stats["media_images"] == 1
    assert rendered.stats["hc007d_media_trailing_breaks"] == 1


def test_hc007d_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC007D.dll"),
        code="007D",
        expected_numeric_index="0000007D.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata", "epwing2HtmlBodydataVertical")),
        exinfo_html_dll="HC007D.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("B121.gif", "B123.gif", "dummy.GIF"),
        features={"html_body_renderer": True, "vertical_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC007D_midashi_margin_renderer" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "kqnewej6_midashi_margin_renderer" for hook in row["hook_behaviors"])


def test_hc008f_maps_jmidashi_margin_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x41\x00\x00"
        + jis_text("見")
        + b"\x1f\x04"
        + jis_fullwidth_ascii("AB")
        + b"\x1f\x05"
        + jis_text("出")
        + b"\xb1\x21"
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\xb1\x23"
        + b"\x1f\x0a"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x5c\x00\x00"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="008F",
            image_sources={
                "dummy.gif": "Templates/dummy.GIF",
                "b121": "Templates/B121.gif",
                "b123": "Templates/B123.gif",
            },
        ),
    )

    assert '<div class="jMidashi">' in rendered.html
    assert '<span class="hankakuMidashi">AB</span>' in rendered.html
    assert '<span class="eMidashi_Japanese">' in rendered.html
    assert '<div style="margin: 30px">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f5c" not in rendered.named_behavior_gaps
    assert rendered.stats["hc008f_jmidashi_blocks"] == 1
    assert rendered.stats["hc008f_margin_sections"] == 1
    assert rendered.stats["hc008f_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc008f_img_gaiji_images"] == 1
    assert rendered.stats["hc008f_nonprinting_controls"] == 1


def test_hc008f_prefers_unicode_gaiji_mapping_over_bitmap_fallback() -> None:
    body = (
        b"\x1f\x41\x00\x00"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("m")
        + b"\xa1\x3e"
        + jis_fullwidth_ascii("ney")
        + b"\x1f\x05"
        + b"\x1f\x61"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="008F",
            gaiji_map={"a13e": "ó"},
            image_sources={
                "dummy.gif": "Templates/dummy.GIF",
                "a13e": "gaiji/GA16HALF/A13E_uni_record_order.bmp",
            },
        ),
    )

    assert "móney" in rendered.html
    assert "GA16HALF" not in rendered.html
    assert "img_gaiji_midashi" not in rendered.html
    assert rendered.stats["gaiji_unicode"] == 1
    assert rendered.stats.get("gaiji_image", 0) == 0


def test_hc008f_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC008F.dll"),
        code="008F",
        expected_numeric_index="0000008F.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata",)),
        exinfo_html_dll="HC008F.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("B121.gif", "B123.gif", "dummy.GIF"),
        features={"html_body_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC008F_jmidashi_margin_renderer" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "kqbizej_jmidashi_margin_renderer" for hook in row["hook_behaviors"])


def test_hc0073_maps_midashi_margin_links_and_template_gaiji() -> None:
    body = (
        b"\x1f\x41\x00\x00"
        + jis_text("見出")
        + b"\xb1\x21"
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_ascii_text("AB")
        + jis_text("本文")
        + b"\xb1\x23"
        + b"\x1f\x0a"
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0073",
            image_sources={
                "dummy.gif": "Templates/dummy.GIF",
                "b121": "Templates/B121.gif",
                "b123": "Templates/B123.gif",
            },
        ),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<div class="contents">' in rendered.html
    assert '<div style="margin-left:9px;">' in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert 'class="lv-hc-link lineLink2"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert 'class="lv-hc-gaiji img_gaiji"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0073_midashi_blocks"] == 1
    assert rendered.stats["hc0073_contents_blocks"] == 1
    assert rendered.stats["hc0073_margin_sections"] == 1
    assert rendered.stats["hc0073_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0073_img_gaiji_images"] == 1
    assert rendered.stats["hc0073_nonprinting_controls"] == 1


def test_hc0073_profile_records_branch_subset_without_full_parity_claim() -> None:
    renderer = HcRendererClassification(
        path=Path("HC0073.dll"),
        code="0073",
        expected_numeric_index="00000073.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe", exports=("epwing2HtmlBodydata",)),
        exinfo_html_dll="HC0073.dll",
        exinfo_declares_this=True,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=("B121.gif", "B123.gif", "dummy.GIF"),
        features={"html_body_renderer": True},
    )

    profile = build_hc_behavior_profile(renderer)
    row = profile.as_dict()

    assert "HC0073_hkkigaku_body_renderer" in row["implemented_semantics"]
    assert row["exact_hc_parity"] is False
    assert any(hook["name"] == "hkkigaku_midashi_margin_renderer" for hook in row["hook_behaviors"])


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

    assert '<div class="lineinfo-1-1">' in rendered.html
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


def test_hc0094_gakken_footer_hidden_nav_and_merged_answer_box() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_text("見出し")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + b"\xb1\x48"
        + jis_text("目次")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + b"\xb1\x48"
        + jis_text("第３章")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + b"\xb1\x30"
        + jis_text("敬語の解説へ")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x13"
        + b"\xb1\x49"
        + b"\xb1\x29"
        + b"\x1f\x42"
        + jis_text("隠")
        + b"\x1f\x62\x00\x00\x00\x01\x00\x02"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\xb1\x3e"
        + b"\xb1\x2c"
        + jis_text("誤り")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\xb1\x3e"
        + b"\xb1\x2d"
        + jis_text("正解")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0094",
            image_sources={
                "class_arrow": "Templates/class_arrow.gif",
                "b130": "Templates/B130.gif",
                "b12c": "Templates/B12C.gif",
                "b12d": "Templates/B12D.gif",
            },
        ),
    )

    assert '<div class="lineinfo-1-1">見出し<br></div>' in rendered.html
    assert rendered.html.count('<div class="footer">') == 1
    assert "目次<br>" not in rendered.html
    assert "第３章<br>" not in rendered.html
    assert "目次" in rendered.html
    assert "第３章" in rendered.html
    assert "敬語の解説へ" in rendered.html
    assert "隠" not in rendered.html
    assert 'data-gaiji-code="b149"' not in rendered.html
    assert 'data-gaiji-code="b129"' not in rendered.html
    assert 'class="lv-hc-link lineLink"' not in rendered.html
    assert rendered.html.count('<div class="aka">') == 1
    answer_box = rendered.html.split('<div class="aka">', 1)[1].split("</div>", 1)[0]
    assert "誤り" in answer_box
    assert "正解" in answer_box
    assert 'src="Templates/B12C.gif"' in answer_box
    assert 'src="Templates/B12D.gif"' in answer_box
    assert rendered.stats["hc0094_suppressed_sections"] == 1
    assert rendered.stats["hc0094_suppressed_section_markers"] == 2
    assert rendered.stats["hc0094_suppressed_section_controls"] == 2
    assert rendered.stats["hc0094_suppressed_section_text"] == 1
    assert rendered.stats["hc0094_toc_linebreaks_suppressed"] >= 2
    assert rendered.stats["hc0094_merged_color_box_sections"] == 1
    assert rendered.stats["hc0094_merged_color_box_markers"] == 1


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
    assert '<div class="honbunB hc0137-indent">' in rendered.html
    assert '<div class="honbun hc0137-body-main">' in rendered.html
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


def test_hc0137_applies_iwanami_private_body_markers() -> None:
    def private(text: str) -> bytes:
        return b"\x1f\xe2\x00\x07" + jis_text(text) + b"\x1f\xe3"

    target = b"\x1f\x62\x00\x00\x00\x02\x00\x30"
    body = (
        b"\x1f\x09\x00\x04"
        + jis_text("あたらしい")
        + jis_text("◆新しい§アタラシイ")
        + b"\x1f\x0a"
        + private("＜語種＞和語＜／語種＞")
        + b"\x1f\x0a"
        + private("＜分野＞一般＜／分野＞")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + private("品詞：Ｅ")
        + jis_text("形")
        + private("品詞：Ｓ")
        + jis_text("現れる。")
        + private("対義：Ｅ")
        + jis_text("古い")
        + private("対義：Ｓ")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x20"
        + jis_text("注記")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x20"
        + b"\xb9\x22"
        + jis_text("は現代的")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x17"
        + b"\xb7\x61"
        + private("関連：Ｅ")
        + b"\x1f\x42"
        + jis_text("真新しい")
        + target
        + private("関連：Ｓ")
        + private("関連：Ｅ")
        + b"\x1f\x42"
        + private("ＲＵＢ：Ｅ")
        + jis_text("瑞瑞")
        + private("ＲＵＢ：Ｓみずみず")
        + jis_text("しい")
        + target
        + private("関連：Ｓ")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0137",
            image_sources={
                "b761": "Templates/B761.png",
                "b922": "Templates/B922.png",
                "g2": "Templates/g2.png",
                "b1": "Templates/b1.png",
                "dummy": "Templates/dummy.gif",
            },
        ),
    )

    assert "◆新しい" not in rendered.html
    assert "アタラシイ" not in rendered.html
    assert 'src="Templates/g2.png"' in rendered.html
    assert 'src="Templates/b1.png"' in rendered.html
    assert '<span class="hc0137-pos">〘形〙</span>' in rendered.html
    assert '<span class="hc0137-antonym">↔古い</span>' in rendered.html
    assert rendered.html.count('src="Templates/B922.png"') == 1
    assert '<span class="hc0137-note-marker">▽</span>注記' in rendered.html
    assert '<span class="hc0137-note-marker">▽</span><img' in rendered.html
    assert 'src="Templates/B761.png"' in rendered.html
    assert 'src="Templates/B761.png" alt="b761" data-gaiji-code="b761"><br><span' in rendered.html
    assert "真新しい</a></span><br><span" in rendered.html
    assert "<ruby><rb>瑞瑞</rb><rp>（</rp><rt>みずみず</rt><rp>）</rp></ruby>しい" in rendered.html
    assert rendered.stats["hc0137_suppressed_search_tail_markers"] == 1
    assert rendered.stats["hc0137_note_markers"] == 2
    assert rendered.stats["hc0137_private_directives_handled"] >= 10


def test_iwanami_companion_templates_are_logical_templates(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_SAMPLE"
    package.mkdir()
    idx = package / "SAMPLE.IDX"
    idx.write_bytes(b"")
    templates = tmp_path / "_DCT_SAMPLE_Templates"
    templates.mkdir()
    image = templates / "B922.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    profile = load_image_resource_profile(idx)

    assert profile.image_dirs == (templates,)
    assert profile.resources["b922"].default == image
    assert relative_image_source(image, idx) == "Templates/B922.png"


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


def test_hc00a6_decodes_decimal_header_section_and_template_arrow() -> None:
    body = b"\x1f\x09\x00\x20" + jis_text("体の仕組みとはたらき：目次")

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00A6",
            image_sources={"b12fh": "Templates/b12fH.gif"},
        ),
    )

    assert '<div class="header"><img src="Templates/b12fH.gif" class="img_mark2">' in rendered.html
    assert "体の仕組みとはたらき：目次" in rendered.html
    assert "margin-left:32.000000em" not in rendered.html
    assert rendered.stats["hc00a6_section_blocks"] == 1


def test_hc00a6_image_caption_section_uses_css_class_not_numeric_indent() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")

    def resolve(pointer: MediaPointer) -> str:
        return "media/colscr/00000123_0045.bmp"

    body = (
        b"\x1f\x09\x00\x11"
        + jis_text("■　咽頭・喉頭の正中断面")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + b"\x1f\x4d"
        + payload
        + jis_text("■　咽頭・喉頭の正中断面")
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00A6", media_image_resolver=resolve))

    assert '<div class="image_caption">■　咽頭・喉頭の正中断面</div>' in rendered.html
    assert rendered.html.count("咽頭・喉頭の正中断面") == 1
    assert 'src="media/colscr/00000123_0045.bmp"' in rendered.html
    assert "margin-left:11.000000em" not in rendered.html
    assert '<div class="honbun" style="margin-left:12.000000em;"><img' in rendered.html
    assert rendered.stats["hc00a6_section_blocks"] == 2
    assert rendered.stats["hc00a6_suppressed_media_caption_chars"] == len("■　咽頭・喉頭の正中断面")


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

    assert '<div class="midashi" style="background-color:#33bf6b;"><span class="hankakuMidashi">H</span></div>' in rendered.html
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


def test_hc00a4_heading_color_marker_and_internal_headings_are_renderer_state() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("おんぶのしかた")
        + b"\x1f\x61"
        + b"\xb1\x2c"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x41\x01\x60"
        + jis_text("おんぶ：")
        + b"\x1f\x04"
        + jis_ascii_text("1")
        + b"\x1f\x05"
        + jis_text("カ月")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00A4"))

    assert '<div class="midashi" style="background-color:#33bf6b;">おんぶのしかた</div>' in rendered.html
    assert "おんぶ：" not in rendered.html
    assert "1カ月" not in rendered.html
    assert "b12c" not in rendered.html.lower()
    assert rendered.stats["hc00a4_midashi_color_markers"] == 1
    assert rendered.stats["hc00a4_suppressed_gaiji_markers"] == 1
    assert rendered.stats["hc00a4_suppressed_internal_headings"] == 1
    assert rendered.stats["hc00a4_suppressed_internal_heading_chars"] == len("おんぶ：1カ月")
    assert rendered.stats["hc00a4_suppressed_internal_heading_breaks"] == 1


def test_hc00a4_private_inline_images_are_not_icon_scaled() -> None:
    assert ":where(.lv-hc-render .img_inline)" not in HC_RENDER_BASE_CSS.split("max-height: 1.4em", 1)[0]
    assert ":where(.lv-hc-render .img_inline),\n:where(.lv-hc-render .lv-hc-media.img_inline)" in HC_RENDER_BASE_CSS


def test_hc00a4_repairs_omitted_tfoot_row_close_in_fixed_html_include() -> None:
    html_directive = b"\x1f\xe2\x00\x0e" + jis_fullwidth_ascii("HTM:FTable.htm") + b"\x1f\xe3\x00\x00"
    body = b"\x1f\x09\x00\x10" + html_directive

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00A4",
            html_templates={
                "table.htm": '<table><tfoot><tr><td>note</td></tfoot><tbody><tr><td>body</td></tr></tbody></table>',
            },
        ),
    )

    assert "<tfoot><tr><td>note</td></tr></tfoot>" in rendered.html
    assert rendered.html.count("<tr") == rendered.html.count("</tr>")
    assert rendered.stats["hc00a4_private_html_includes"] == 1
    assert rendered.stats["hc00a4_private_html_table_rows_repaired"] == 1


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


def test_hkdksr_medical_heading_keeps_category_metadata_in_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("薬剤")
        + b"\x1f\x61"
        + jis_text("［内服薬］")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="014A"))

    assert '<div class="midashi">薬剤［内服薬］</div>' in rendered.html
    assert rendered.stats["hc_hkdksr_medical_deferred_midashi_ends"] == 1
    assert rendered.stats["hc_hkdksr_medical_deferred_midashi_closures"] == 1


def test_hkdksr_medical_renders_title_image_and_product_subtitle() -> None:
    body = (
        b"\x1f\x09\x00\x03"
        + jis_text("【製剤情報】")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("一般名：アテノロール")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x08"
        + jis_text("【商品】")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="014A"))

    assert '<img src="Templates/midashi1.png" class="img_midashi">' in rendered.html
    assert "【製剤情報】" not in rendered.html
    assert '<div class="title3">一般名：アテノロール</div>' in rendered.html
    assert '<div class="SubTitle_syouhin">商品</div>' in rendered.html
    assert "【商品】" not in rendered.html
    assert rendered.stats["hc_hkdksr_medical_seizaijouhou_titles"] == 1
    assert rendered.stats["hc_hkdksr_medical_product_subtitles"] == 1


def test_hkdksr_medical_groups_ranked_products_in_gradient_cards() -> None:
    body = (
        b"\x1f\x09\x00\x06"
        + b"\xb1\x2a"
        + jis_text("先発品")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x10"
        + jis_text("十円")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x11"
        + jis_text("写真")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + b"\xb1\x2b"
        + jis_text("後発品")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("一般名")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="014A",
            image_sources={"b12a": "Templates/b12a.png", "b12b": "Templates/b12b.png"},
        ),
    )

    assert rendered.html.count('class="hc-hkdksr-product-card"') == 2
    assert (
        '<div class="hc-hkdksr-product-card"><span class="med">'
        '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/b12a.png"'
    ) in rendered.html
    assert '<div class="medprice">十円</div><div class="medimage">写真</div></div>' in rendered.html
    assert (
        '<div class="hc-hkdksr-product-card"><span class="med">'
        '<img class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji" src="Templates/b12b.png"'
    ) in rendered.html
    assert '</div><div class="title3">一般名</div>' in rendered.html
    assert 'url("Templates/back3.png")' in HC_RENDER_FINAL_CSS
    assert rendered.stats["hc_hkdksr_product_cards"] == 2
    assert rendered.stats["hc_hkdksr_product_card_closures"] == 2


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
        assert 'href="lvaddr://00000002/0030"' in rendered.html
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

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00C4",
            image_sources={
                "b126": "Templates/B126.png",
                "betsumei": "Templates/betsumei.png",
                "arrow1": "Templates/arrow1.png",
            },
        ),
    )

    assert '<div class="midashi"><span class="zenkakuMidashi">' in rendered.html
    assert '<div class="block"><div class="honbun_number">' in rendered.html
    assert '<div class="honbun_icon"><img src="Templates/betsumei.png" class="icon">' in rendered.html
    assert '<img src="Templates/arrow1.png" class="icon_s">' in rendered.html
    assert '<font class="font_down">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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


def test_hc0063_keeps_real_heading_state_open_until_newline() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x91"
        + b"\x1f\x61"
        + jis_text("だしん")
        + b"\x1f\x10"
        + jis_text("【打診】")
        + b"\x1f\x11"
        + bytes.fromhex("b667")
        + jis_ascii_text("dashin")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("本文")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0063", image_sources={"roman-alphabet": "Templates/roman-alphabet.gif"}),
    )

    assert '<div class="midashi"></div>' not in rendered.html
    assert '<div class="midashi">' in rendered.html
    heading = rendered.html.split('<div class="midashi">', 1)[1].split("</div>", 1)[0]
    assert "だしん" in heading
    assert "打" in heading
    assert "診" in heading
    assert "roman-alphabet.gif" in heading
    assert "dashin" in heading
    assert '<div class="contents_body">' in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0063_heading_blocks"] == 1
    assert rendered.stats["hc0063_contents_body_blocks"] == 1


def test_hc0063_b65e_opens_exsample_across_bullet_continuations() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x91"
        + b"\x1f\x61"
        + jis_text("見出し")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x41\x02\x91"
        + b"\x1f\x61"
        + bytes.fromhex("b65e")
        + jis_text("例一")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x41\x02\x91"
        + b"\x1f\x61"
        + b"\x1f\x04\x21\x26\x1f\x05"
        + jis_text("例二")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x41\x02\x91"
        + b"\x1f\x61"
        + jis_text("次")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0063", image_sources={"circle-triangle": "Templates/circle-triangle.gif"}),
    )

    assert 'src="Templates/circle-triangle.gif"' in rendered.html
    assert rendered.html.count('<div class="exsample">') == 1
    exsample = rendered.html.split('<div class="exsample">', 1)[1].split("</div>", 1)[0]
    assert "例一" in exsample
    assert "例二" in exsample
    assert "次" not in exsample
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0063_exsample_blocks"] == 1
    assert rendered.stats["hc0063_exsample_blocks_closed"] == 1


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
    assert '<div class="youreihan contents"><div class="lineinfo5">' in rendered.html
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


def test_hc0093_gakken_toc_metadata_ruby_and_note_box() -> None:
    private = lambda directive: b"\x1f\xe2\x56\x64" + jis_fullwidth_ascii(directive) + b"\x1f\xe3"
    body = (
        b"\x1f\x09\x00\x01"
        + jis_text("いし【意志】")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + bytes.fromhex("b14c")
        + jis_text("目次")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + bytes.fromhex("b14d")
        + jis_text("５　西洋")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x10"
        + jis_text("項５−１−３")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + bytes.fromhex("b148b149")
        + jis_text("意味")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + bytes.fromhex("b14ab14b")
        + jis_text("『")
        + private("B:E")
        + jis_text("哲")
        + private("B:D")
        + jis_text("（てつ")
        + private("B:S")
        + jis_text("）学』")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x05"
        + jis_text("◆現在は")
        + b"\x1f\x0a"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0093",
            image_sources={
                "class_arrow": "Templates/class_arrow.gif",
                "meaning": "Templates/meaning.gif",
                "etymology": "Templates/etymology.gif",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert "目次<br>" not in rendered.html
    assert "項５−１−３" not in rendered.html
    assert 'src="Templates/meaning.gif"' in rendered.html
    assert 'alt="b149"' not in rendered.html
    assert 'src="Templates/etymology.gif"' in rendered.html
    assert 'alt="b14b"' not in rendered.html
    assert '<ruby class="ruby7"><rb class="rb7">哲</rb><rp class="rp7">(</rp><rt class="rt7">てつ</rt>' in rendered.html
    assert "哲（てつ）" not in rendered.html
    assert '<div class="youreihan contents"><div class="lineinfo5">◆現在は' in rendered.html
    assert "◆現在は<br></div></div>" not in rendered.html
    assert "◆現在は</div></div>" in rendered.html
    assert rendered.stats["hc0093_toc_linebreaks_suppressed"] == 2
    assert rendered.stats["hc0093_suppressed_sections"] == 1
    assert rendered.stats["hc0093_suppressed_section_text"] == 6
    assert rendered.stats["hc0093_ruby_starts"] == 1
    assert rendered.stats["hc0093_ruby_reading_starts"] == 1
    assert rendered.stats["hc0093_ruby_ends"] == 1
    assert rendered.stats["hc0093_ruby_parentheses_suppressed"] == 2
    assert rendered.stats["hc0093_trailing_yourei_breaks_suppressed"] == 2


def test_hc0095_maps_page_kind_lineinfo_sections_and_template_markers() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x05"
        + bytes.fromhex("b13b")
        + b"\x1f\x09\x00\x12"
        + bytes.fromhex("b12f")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x09\x00\x06"
        + bytes.fromhex("b128")
        + jis_text("例文")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0095",
            image_sources={
                "b13b": "gaiji/GA16FULL/B13B.bmp",
                "b12f": "Templates/b12f.png",
                "b128": "Templates/B128.png",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert '<div class="lineinfo3-1"><span class="hankakuMidashi">H</span>' in rendered.html
    assert 'data-gaiji-code="b13b"' not in rendered.html
    assert '<div class="lineinfo3-12">' in rendered.html
    assert '<div class="contents_body">' in rendered.html
    assert '<div class="lineinfo3-3">' in rendered.html
    assert '<div class="lineinfo3-6">' in rendered.html
    assert '<img src="Templates/dummy.GIF" class="img_dummy">' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark2" src="Templates/b12f.png"' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark" src="Templates/B128.png"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0095_lineinfo_sections"] == 4
    assert rendered.stats["hc0095_contents_body_blocks"] == 1
    assert rendered.stats["hc0095_page_kind_markers"] == 1
    assert rendered.stats["hc0095_template_image_markers"] == 2


def test_hc0095_suppresses_page_kind_markers_merges_footer_and_styles_emphasis() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_text("見出し")
        + bytes.fromhex("b13c")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + bytes.fromhex("b12f")
        + b"\x1f\x42"
        + jis_text("目次")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x02"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x12"
        + bytes.fromhex("b12f")
        + b"\x1f\x42"
        + jis_text("第３章")
        + b"\x1f\x62\x00\x00\x03\x36\x05\x52"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\xe0\x00\x00"
        + jis_text("課長が、「")
        + b"\x1f\x12"
        + jis_text("おまえ")
        + b"\x1f\x13"
        + jis_text("」")
        + b"\x1f\xe1"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0095",
            image_sources={"b12f": "Templates/b12f.png", "b13c": "gaiji/GA16FULL/B13C.bmp"},
        ),
    )

    assert 'data-gaiji-code="b13c"' not in rendered.html
    assert rendered.html.count('<div class="lineinfo4-12">') == 1
    assert "目次<br>" not in rendered.html
    assert "第３章<br>" not in rendered.html
    assert '<b class="hc0095-emphasis">課長が、「<span class="hc0095-underline">おまえ</span>」</b>' in rendered.html
    assert rendered.stats["hc0095_page_kind_markers"] == 1
    assert rendered.stats["hc0095_page_kind_markers_suppressed"] == 1
    assert rendered.stats["hc0095_toc_linebreaks_suppressed"] == 2
    assert rendered.stats["hc0095_repeated_footer_sections_suppressed"] == 1


def test_hc0095_suppresses_internal_b122_marker_before_ga16_fallback() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_text("見出し")
        + bytes.fromhex("b13b")
        + b"\x1f\x09\x00\x06"
        + bytes.fromhex("b121b122b131")
        + jis_text("注目")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0095",
            image_sources={
                "b121": "Templates/B121.png",
                "b122": "gaiji/GA16FULL/B122.bmp",
                "b131": "Templates/B131.png",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert 'src="Templates/B121.png"' in rendered.html
    assert 'src="Templates/B131.png"' in rendered.html
    assert 'data-gaiji-code="b122"' not in rendered.html
    assert "GA16FULL/B122.bmp" not in rendered.html
    assert rendered.stats["hc0095_suppressed_markers"] == 1


def test_hc0095_emphasis_is_plain_for_page_kind_three_and_navy_for_page_kind_four() -> None:
    plain = render_hc_body(
        b"\x1f\x09\x00\x01"
        + bytes.fromhex("b13b")
        + b"\x1f\x09\x00\x04"
        + b"\x1f\xe0\x00\x00"
        + jis_text("怒り心頭")
        + b"\x1f\xe1",
        HcRenderOptions(renderer_code="0095", image_sources={"b13b": "gaiji/GA16FULL/B13B.bmp"}),
    )
    navy = render_hc_body(
        b"\x1f\x09\x00\x01"
        + bytes.fromhex("b13c")
        + b"\x1f\x09\x00\x02"
        + b"\x1f\xe0\x00\x00"
        + jis_text("課長")
        + b"\x1f\xe1",
        HcRenderOptions(renderer_code="0095", image_sources={"b13c": "gaiji/GA16FULL/B13C.bmp"}),
    )

    assert "<b>怒り心頭</b>" in plain.html
    assert "hc0095-emphasis\">怒り心頭" not in plain.html
    assert '<b class="hc0095-emphasis">課長</b>' in navy.html
    assert plain.stats["hc0095_plain_emphasis_spans"] == 1
    assert navy.stats["hc0095_navy_emphasis_spans"] == 1


def test_hc0095_renders_rub_private_directives_as_ruby() -> None:
    ruby_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3"
    ruby_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:S") + jis_text("しわざ") + b"\x1f\xe3"
    body = (
        b"\x1f\x09\x00\x01"
        + bytes.fromhex("b13b")
        + b"\x1f\x09\x00\x06"
        + jis_text("彼らの")
        + ruby_start
        + jis_text("仕業")
        + ruby_end
        + jis_text("とわかる")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0095", image_sources={"b13b": "gaiji/GA16FULL/B13B.bmp"}),
    )

    assert '<ruby class="ruby7"><rb class="rb7">仕業</rb>' in rendered.html
    assert '<rt class="rt7">しわざ</rt>' in rendered.html
    assert rendered.stats["hc0095_ruby_starts"] == 1
    assert rendered.stats["hc0095_ruby_ends"] == 1


def test_hc0096_maps_lineinfo_sections_links_template_gaiji_and_state_markers() -> None:
    ruby_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3\x00\x00"
    ruby_end = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:S") + jis_text("ふじわら") + b"\x1f\xe3\x00\x00"
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
        + ruby_start
        + jis_text("藤原")
        + ruby_end
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
    assert '<ruby class="ruby7"><rb class="rb7">藤原</rb><rp class="rp7">(</rp><rt class="rt7">ふじわら</rt>' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert "B150" not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0096_lineinfo_sections"] == 3
    assert rendered.stats["hc0096_contents_body_blocks"] == 1
    assert rendered.stats["hc0096_template_image_markers"] == 1
    assert rendered.stats["hc0096_inline_mark_images"] == 1
    assert rendered.stats["hc0096_reflow_state_markers"] == 1
    assert rendered.stats["hc0096_ruby_starts"] == 1
    assert rendered.stats["hc0096_ruby_ends"] == 1


def test_hc009f_maps_season_category_sections_and_template_gaiji() -> None:
    body = (
        b"\x1f\x41\x01\x00"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x06"
        + jis_text("春")
        + b"\x1f\x09\x00\x07"
        + jis_text("時候")
        + b"\x1f\x09\x00\x04"
        + bytes.fromhex("b121")
        + bytes.fromhex("b123")
        + bytes.fromhex("b15a")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x30"
        + jis_text("本文")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="009F",
            image_sources={
                "hsp": "Templates/Hsp.png",
                "hsp1": "Templates/Hsp1.png",
                "b121h": "Templates/b121H.gif",
                "b15a": "Templates/b15a.gif",
                "dummy": "Templates/dummy.GIF",
            },
        ),
    )

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert '<img src="Templates/Hsp.png" class="img_mark hc009f-marker">' in rendered.html
    assert '<img src="Templates/Hsp1.png" class="img_mark hc009f-marker">' in rendered.html
    assert '<img src="Templates/Hsp.png" class="img_mark hc009f-marker"><img src="Templates/Hsp1.png" class="img_mark hc009f-marker">' in rendered.html
    assert "春" not in rendered.html
    assert "時" not in rendered.html
    assert "候" not in rendered.html
    assert 'style="background-color:#fad9ea;"></div>' not in rendered.html
    assert '<div class="honbun" style="margin-left:1em;">' in rendered.html
    assert '<img class="lv-hc-gaiji img_mark" src="Templates/b121H.gif"' in rendered.html
    assert "b123" not in rendered.html
    assert '<img src="Templates/dummy.GIF" class="img_dummy">' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/b15a.gif"' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'lv-hc-heading' not in rendered.html
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc009f_season_markers"] == 1
    assert rendered.stats["hc009f_category_markers"] == 1
    assert rendered.stats["hc009f_oriented_markers"] == 1
    assert rendered.stats["hc009f_suppressed_markers"] == 1
    assert rendered.stats["hc009f_template_image_markers"] == 1
    assert rendered.stats["hc009f_section_label_pairs_suppressed"] == 3
    assert rendered.stats["hc009f_label_linebreaks_suppressed"] == 1


def test_hc009f_section_six_does_not_consume_heading_when_label_is_absent() -> None:
    body = b"\x1f\x09\x00\x06" + b"\x1f\x41\x01\x00" + jis_text("見出し") + b"\x1f\x61"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="009F"))

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert rendered.stats["hc009f_season_markers_without_label"] == 1
    assert "hc009f_unmapped_season_marker_1f41" not in rendered.named_behavior_gaps


def test_hc009f_groups_midashi_aliases_and_haiku_separators_vertically() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("【春】")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("陽春")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("芳春")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + jis_text("春")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x07"
        + jis_text("時候")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x04"
        + jis_text("説明")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("一句")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("二句")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="009F",
            vertical=True,
            image_sources={
                "vsp": "Templates/Vsp.png",
                "vsp1": "Templates/Vsp1.png",
            },
        ),
    )

    assert '<div class="lv-hc-render lv-hc-vertical">' in rendered.html
    assert rendered.html.count('<div class="midashi">') == 1
    assert '<div class="midashi">【春】<span class="hc009f-midashi-alias">陽春</span><span class="hc009f-midashi-alias">芳春</span></div>' in rendered.html
    assert '<img src="Templates/Vsp.png" class="img_mark hc009f-marker"><img src="Templates/Vsp1.png" class="img_mark hc009f-marker">' in rendered.html
    assert ".lv-hc-render.lv-hc-vertical .hc009f-marker" in HC_RENDER_FINAL_CSS
    assert "Templates/Hsp.png" not in rendered.html
    assert rendered.html.count('class="hc009f-haiku-separator"') == 2
    assert '<div class="honbun" style="margin-top:1em;">説明</div>' in rendered.html
    assert '<div class="honbun" style="margin-top:0.5em;">一句</div>' in rendered.html
    assert rendered.stats["hc009f_midashi_aliases"] == 2
    assert rendered.stats["hc009f_haiku_separators"] == 2
    assert rendered.stats["hc009f_label_linebreaks_suppressed"] == 3


def test_hc_render_vertical_shell_uses_modern_writing_mode() -> None:
    assert ":where(.lv-hc-render.lv-hc-vertical)" in HC_RENDER_FINAL_CSS
    assert "writing-mode: vertical-rl;" in HC_RENDER_FINAL_CSS
    assert "text-orientation: mixed;" in HC_RENDER_FINAL_CSS
    assert ":where(html.v)" in HC_RENDER_FINAL_CSS
    assert "overflow-y: hidden !important;" in HC_RENDER_FINAL_CSS
    assert ":where(body.v > .exact-rendererdb-entry .midashi)" in HC_RENDER_FINAL_CSS
    assert "height: auto !important;" in HC_RENDER_FINAL_CSS
    assert "lvInitVerticalPageScroll" in HC_RENDER_BASE_SCRIPT


def test_hc009f_entry_slices_group_season_aliases_with_body(tmp_path: Path) -> None:
    grouped = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("【春】")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("陽春")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x06"
        + jis_text("春")
        + b"\x1f\x09\x00\x07"
        + jis_text("時候")
        + b"\x1f\x09\x00\x04"
        + jis_text("説明")
        + b"\x1f\x09\x00\x03"
        + jis_text("一句")
    )
    next_entry = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("夏")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x04"
        + jis_text("次")
    )
    third_entry = b"\x1f\x09\x00\x01" + b"\x1f\x41\x01\x60" + jis_text("秋") + b"\x1f\x61"
    path = tmp_path / "HONMON.DIC"
    path.write_bytes(encode_sseddata_literal(grouped + next_entry + third_entry, start_block=0, kind=0))
    reader = SsedRandomReader(path)

    assert list(_hc_entry_slices_reader(reader, "009F"))[:2] == [
        (0, len(grouped)),
        (len(grouped), len(grouped) + len(next_entry)),
    ]


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


def test_hc0090_renders_a255_a256_as_linebreak_markers() -> None:
    rendered = render_hc_body(bytes.fromhex("a255a256"), HcRenderOptions(renderer_code="0090"))

    assert rendered.html == '<div class="lv-hc-render"><br><br></div>'
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0090_linebreak_markers"] == 2


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

    assert '<div class="midashi lv-hc-windows-blue-midashi"><span class="hankaku">H</span>' in rendered.html
    assert ".lv-hc-render .lv-hc-windows-blue-midashi" in HC_RENDER_FINAL_CSS
    assert "color: #3021f9;" in HC_RENDER_FINAL_CSS
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


def test_hc014f_package_reader_style_keeps_package_midashi_colour() -> None:
    body = b"\x1f\x09\x00\x01\x1f\x41\x01\x60" + jis_text("見出し") + b"\x1f\x61"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="014F", reader_style=PACKAGE_READER_STYLE))

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert "lv-hc-windows-blue-midashi" not in rendered.html


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


def test_hc0135_maps_sinmei_state_sections_from_dll_branch_table() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_fullwidth_ascii("H")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x07"
        + jis_text("状態")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x23"
        + jis_text("参照")
        + b"\x1f\x04"
        + jis_text("小")
        + b"\x1f\x05"
        + b"\x1f\x09\x00\x17"
        + jis_text("改行")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0135"))

    assert "hc0135_unmapped_section_0007" not in rendered.named_behavior_gaps
    assert "hc0135_unmapped_section_0017" not in rendered.named_behavior_gaps
    assert "hc0135_unmapped_section_0023" not in rendered.named_behavior_gaps
    assert rendered.html.count("<div") == rendered.html.count("</div>")
    assert rendered.stats["hc0135_section_state_only"] == 2
    assert rendered.stats["hc0135_section_conditional_break"] == 1
    assert "<br>改行" in rendered.html


def test_hc0135_renders_sinmei_private_state_markers_and_example_box() -> None:
    def private_marker(text: str) -> bytes:
        return b"\x1f\xe2\x00\x07" + jis_text(text) + b"\x1f\xe3"

    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("えん")
        + private_marker("上付き文字：Ｓオ")
        + jis_text("【")
        + private_marker("教育漢字開始")
        + jis_text("延")
        + private_marker("教育漢字終了")
        + jis_text("】■えん§延長")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x18"
        + jis_text("−チヤウ")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x31"
        + jis_text("（自他サ）")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x27"
        + jis_text("（なに")
        + b"\x1f\xe0\x00\x00"
        + jis_text("ヲ")
        + b"\x1f\xe1"
        + jis_text("−する）")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x29"
        + b"\x1f\x4a"
        + bytes.fromhex("00010000000474510076000474522035")
        + jis_text("→")
        + private_marker("画像：ａｃｃｅｎｔ００")
        + b"\x1f\x6a"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x30"
        + jis_text("「")
        + b"\x1f\x09\x00\x38"
        + jis_text("例一")
        + b"\x1f\x0a"
        + jis_text("／")
        + b"\x1f\x09\x00\x38"
        + jis_text("例二")
        + jis_text("」")
        + b"\x1f\x09\x00\x11"
        + private_marker("画像：ｍａｒｕ０１")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="0135",
            image_sources={
                "accent00": "Templates/accent00.png",
                "exam": "Templates/exam.png",
                "maru01": "Templates/maru01.png",
                "sound": "Templates/sound.png",
            },
        ),
    )

    assert "■" not in rendered.html
    assert "§" not in rendered.html
    assert "えん§延長" not in rendered.html
    assert '<span class="hc0135-sup-note">(オ)</span>' in rendered.html
    assert '<span class="hc0135-kyouiku-kanji">延</span>' in rendered.html
    assert '<span class="hc0135-history-kana">−チヤウ</span>' in rendered.html
    assert '<span class="hc0135-part-of-speech">（自他サ）</span>' in rendered.html
    assert '<span class="hc0135-construction-pattern">（なに<b class="hc0135-red-emphasis">ヲ</b>−する）</span>' in rendered.html
    assert '<span class="hc0135-accent-line"><img src="Templates/accent00.png" class="img_mark2"><a class="lv-hc-audio"' in rendered.html
    assert '<img src="Templates/accent00.png" class="img_mark2"><a class="lv-hc-audio"' in rendered.html
    assert '<img src="Templates/sound.png" class="img_mark2"></a></span><br>' in rendered.html
    assert '<div class="contents_yourei"><img src="Templates/exam.png" class="img_icon_Big"><br>' in rendered.html
    assert "「" not in rendered.html
    assert "」" not in rendered.html
    assert "／" not in rendered.html
    assert "例一<br>" in rendered.html
    assert ">例二" in rendered.html
    assert '<img src="Templates/maru01.png" class="img_icon">' in rendered.html
    assert rendered.stats["hc0135_suppressed_search_tail_markers"] == 1
    assert rendered.stats["hc0135_example_blocks"] == 1
    assert rendered.stats["hc0135_example_icons"] == 1
    assert rendered.stats["hc0135_audio_label_extras"] == 1
    assert rendered.stats["hc0135_suppressed_example_punctuation"] == 3


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
    def resolve(pointer: MediaPointer) -> str:
        return "media/colscr/example.bmp"

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
        HcRenderOptions(
            renderer_code="0048",
            image_sources={"sound": "Templates/sound.png"},
            media_image_resolver=resolve,
        ),
    )

    assert (
        '<div style="margin: 3px"></div><div class="midashi hc0048-midashi">■見出し</div><div class="honbun">'
        in rendered.html
    )
    assert "本文" in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0048"' in rendered.html
    assert '<div><img class="lv-hc-media img_inline"' in rendered.html
    assert 'src="media/colscr/example.bmp"' in rendered.html
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
    ruby_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3"
    ruby_end = b"\x1f\xe2\x00\x09" + jis_fullwidth_ascii("RUB:S") + jis_text("しぎ") + b"\x1f\xe3"
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
        + b"\x1f\x09\x00\x10"
        + jis_text("［用例］")
        + ruby_start
        + jis_text("鷸")
        + ruby_end
        + b"\x1f\xe0\x00\x04"
        + jis_text("漁夫の利")
        + b"\x1f\xe1"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00AC", image_sources={"b121": "Templates/B121.gif"}),
    )

    assert '<div class="midashi">見出し</div>' in rendered.html
    assert '<div class="honbun" style="margin-left:7em;text-indent:-7em;">' in rendered.html
    assert '<div class="honbun" style="margin-left:6em;text-indent:-6em;">' in rendered.html
    assert "見出し" in rendered.html
    assert '<ruby class="ruby7"><rb class="rb7">鷸</rb><rp class="rp7">(</rp><rt class="rt7">しぎ</rt>' in rendered.html
    assert '<span class="hc00ac-red-emphasis">漁夫の利</span>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert 'href="lvaddr://00000002/0030"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert 'data-gaiji-code="b139"' not in rendered.html
    assert rendered.stats["hc00ac_honbun_sections"] == 3
    assert rendered.stats["hc00ac_midashi_sections"] == 1
    assert rendered.stats["hc00ac_nonprinting_controls"] == 1
    assert rendered.stats["hc00ac_suppressed_markers"] == 1
    assert rendered.stats["hc00ac_ruby_starts"] == 1
    assert rendered.stats["hc00ac_ruby_ends"] == 1


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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
    assert 'data-lv-section' not in rendered.html
    assert 'data-gaiji-code="a435"' not in rendered.html
    assert 'data-gaiji-code="a436"' not in rendered.html
    assert rendered.stats["hc013c_section_blocks"] == 1
    assert rendered.stats["hc013c_section_state"] == 1
    assert rendered.stats["hc013c_private_icons"] == 1
    assert rendered.stats["hc013c_noop_markers"] == 2
    assert rendered.stats["hc013c_nonprinting_controls"] == 1


def test_hc013c_missing_b121_uses_dll_no_output_bitmap_fallback() -> None:
    rendered = render_hc_body(b"\x1f\x41\x00\x00\xb1\x21" + jis_text("見出し") + b"\x1f\x61", HcRenderOptions(renderer_code="013C"))

    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert 'data-gaiji-code="b121"' not in rendered.html
    assert rendered.stats["hc013c_custom_bitmap_fallback_suppressed"] == 1
    assert "hc013c_custom_gaiji_bitmap_unresolved" not in rendered.named_behavior_gaps


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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
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
            template_heading="行動別：テスト",
        ),
    )

    assert '<div class="header"><img src="Templates/play.png">table-row1</div>' in rendered.html
    assert rendered.stats["hc00a0_detail_rows"] == 1
    assert '<td class="english">HELLO</td>' in rendered.html
    assert '<td class="japanese">こんにちは</td>' in rendered.html
    assert 'src="Templates/sound.png"' in rendered.html
    assert 'class="lv-hc-audio hc00a0-audio" href="mp3/0001.mp3"' in rendered.html
    assert 'data-lv-original-onclick="lvedPlay(&#x27;0001&#x27;,&#x27;0001.mp3&#x27;)"' in rendered.html
    assert "onclick=\"lvedPlay('0001','0001.mp3')" not in rendered.html
    assert 'src="Templates/ok-2.png"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert rendered.plain == "HELLO\nこんにちは"
    assert rendered.audio[0]["target"]["path"] == "mp3/0001.mp3"


def test_hc00a0_preserves_header_play_gif_when_play_png_shares_stem() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("PLAY")
        + b"\x1f\x05"
        + b"\x1f\x09\x00\x02"
        + jis_text("再生")
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
                "play": "Templates/play.png",
                "play.gif": "Templates/play.gif",
                "play.png": "Templates/play.png",
                "sound": "Templates/sound.png",
                "sound.png": "Templates/sound.png",
            },
            html_templates={
                "Header": '<img id="cmdPlay" src="play.gif" title="すべてを再生" onclick="lvedPlayAll();">',
                "Detail": (
                    '<div class="detail"><img src="play.png">'
                    '<img src="sound.png" onclick="lvedPlay(\'$$DETAIL_NO$$\',\'$$DETAIL_PLAY$$\');">'
                    "$$ENGLISH$$$$JAPANESE$$</div>"
                ),
            },
            template_heading="行動別：テスト",
        ),
    )

    assert 'class="lv-hc-audio hc00a0-audio hc00a0-play-all"' in rendered.html
    assert 'data-lv-audio-action="play-all"' in rendered.html
    assert 'data-lv-original-onclick="lvedPlayAll();"' in rendered.html
    assert re.search(r"<img\b[^>]*\sonclick=", rendered.html) is None
    assert '<img id="cmdPlay" src="Templates/play.gif" title="すべてを再生">' in rendered.html
    assert '<div class="detail"><img src="Templates/play.png">' in rendered.html
    assert 'class="lv-hc-audio hc00a0-audio" href="mp3/0001.mp3"' in rendered.html


def test_hc00a0_generated_script_supports_clickable_play_all() -> None:
    assert 'data-lv-audio-action="play-all"' in HC_RENDER_BASE_SCRIPT
    assert 'querySelectorAll("[data-lv-audio-src]")' in HC_RENDER_BASE_SCRIPT
    assert "new Audio()" in HC_RENDER_BASE_SCRIPT


def test_hc00a0_applies_one_header_to_multiple_template_detail_rows() -> None:
    row1 = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("ONE")
        + b"\x1f\x05"
        + b"\x1f\x09\x00\x02"
        + jis_text("一")
        + b"\x1f\xe2\x00\x07"
        + jis_text("＜ＰｌａｙＳｏｕｎｄ＞０００１．ｍｐ３＜／ＰｌａｙＳｏｕｎｄ＞")
        + b"\x1f\xe3"
    )
    row2 = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("TWO")
        + b"\x1f\x05"
        + b"\x1f\x09\x00\x02"
        + jis_text("二")
        + b"\x1f\xe2\x00\x07"
        + jis_text("＜ＰｌａｙＳｏｕｎｄ＞０００２．ｍｐ３＜／ＰｌａｙＳｏｕｎｄ＞")
        + b"\x1f\xe3"
    )

    rendered = render_hc_body(
        row1 + row2,
        HcRenderOptions(
            renderer_code="00A0",
            html_templates={
                "Header": '<header>$$DISPLAY_MIDASHI$$:<b>$$MIDASHI$$</b></header>',
                "Detail": '<p data-no="$$DETAIL_NO$$">$$ENGLISH$$/$$JAPANESE$$/$$DETAIL_PLAY$$</p>',
            },
            template_heading="行動別：サービス(商用)：説明・返事",
        ),
    )

    assert rendered.html.count("<header>") == 1
    assert "行動別：サービス(商用)：説明・返事" in rendered.html
    assert '<p data-no="0001">ONE/一/0001.mp3</p>' in rendered.html
    assert '<p data-no="0002">TWO/二/0002.mp3</p>' in rendered.html
    assert rendered.plain == "ONE\n一\nTWO\n二"
    assert [row["target"]["path"] for row in rendered.audio] == ["mp3/0001.mp3", "mp3/0002.mp3"]
    assert rendered.stats["hc00a0_detail_rows"] == 2
    assert rendered.stats["hc00a0_audio_links"] == 2
    assert rendered.private_directives[0]["kind"] == "play_sound"
    assert rendered.stats["hc00a0_play_sound_directives"] == 2


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
    assert '<img src="Templates/1.png" class="img_icon"/><br>' in rendered.html
    assert '<img src="Templates/2.png" class="img_icon"/><br>' in rendered.html
    assert '<p class="moji-down">❶' in rendered.html
    assert '<p class="moji-down">❷' in rendered.html
    assert 'class="lv-hc-section"' not in rendered.html
    assert rendered.stats["hc02c1_section_blocks"] == 3
    assert rendered.stats["hc02c1_section_icons"] == 2
    assert rendered.stats["hc02c1_moji_down_blocks"] == 2
    assert rendered.stats["hc02c1_nonprinting_controls"] == 2


def test_hc02c1_uses_line_links_and_suppresses_unused_template_markers() -> None:
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
    assert 'href="lvaddr://00000002/0030"' in rendered.html
    assert 'src="Templates/B14C.png"' not in rendered.html
    assert 'data-gaiji-code="b14c"' not in rendered.html
    assert 'src="Templates/b135.png"' in rendered.html
    assert 'class="lv-hc-gaiji lv-hc-gaiji-image img_gaiji"' in rendered.html
    assert rendered.stats["hc02c1_suppressed_gaiji_markers"] == 1
    assert rendered.stats["hc02c1_moji_down_markers"] == 1


def test_hc02c1_private_ruby_and_bold_emphasis() -> None:
    body = (
        b"\x1f\xe2\x00\x01"
        + jis_ascii_text("RUB:")
        + jis_text("始")
        + b"\x1f\xe3"
        + jis_text("懐中")
        + b"\x1f\xe2\x00\x01"
        + jis_ascii_text("RUB:")
        + jis_text("終かいちゅう")
        + b"\x1f\xe3"
        + b"\x1f\x12"
        + jis_text("向けて")
        + b"\x1f\x13"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02C1"))

    assert '<ruby class="ruby7"><rb class="rb7">懐中</rb>' in rendered.html
    assert '<rt class="rt7">かいちゅう</rt>' in rendered.html
    assert "<b>向けて</b>" in rendered.html
    assert "<em>向けて</em>" not in rendered.html
    assert rendered.stats["hc02c1_ruby_starts"] == 1
    assert rendered.stats["hc02c1_ruby_ends"] == 1
    assert rendered.stats["hc02c1_private_directives_handled"] == 2


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
    assert 'href="lvaddr://00000002/0030"' in rendered.html


def test_hc02bf_decoration_mode_one_is_contextual() -> None:
    body = (
        b"\x1f\x09\x00\x04"
        + jis_text("［")
        + b"\x1f\xe0\x00\x01"
        + jis_ascii_text("cf. ")
        + b"\x1f\xe1"
        + jis_text("］")
        + b"\x1f\x09\x00\x02"
        + jis_text("（＋")
        + b"\x1f\xe0\x00\x01"
        + jis_ascii_text("ind")
        + b"\x1f\xe1"
        + jis_text("）")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02BF"))

    assert '<i><span class="hankaku">cf.</span></i>' in rendered.html
    assert '<b><span class="hankaku">ind</span></b>' in rendered.html
    assert rendered.stats["hc02bf_italic_decoration_spans"] == 1
    assert rendered.stats["hc02bf_bold_decoration_spans"] == 1
    assert rendered.stats["hc02bf_decoration_closures"] == 2


def test_hc02bf_decoration_mode_four_is_small_author_tail() -> None:
    body = (
        b"\x1f\x09\x00\x09"
        + jis_ascii_text("(C")
        + b"\x1f\xe0\x00\x04"
        + jis_ascii_text("IC")
        + b"\x1f\xe1"
        + jis_ascii_text(")")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="02BF"))

    assert '<span class="hc02bf-smallcap"><span class="hankaku">IC</span></span>' in rendered.html
    assert '<b><span class="hankaku">IC</span></b>' not in rendered.html
    assert rendered.stats["hc02bf_smallcap_decoration_spans"] == 1
    assert rendered.stats["hc02bf_decoration_closures"] == 1


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

    assert '<div class="midashi hc0151-midashi"><span class="hankaku">H</span></div><div class="contents">' in rendered.html
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


def test_hc0151_media_is_a_block_and_suppresses_renderer_padding() -> None:
    payload = bytes.fromhex("000000000000000000000000000001230045")
    seen: list[MediaPointer] = []

    def resolve(pointer: MediaPointer) -> str:
        seen.append(pointer)
        return "media/colscr/00000123_0045.jpg"

    rendered = render_hc_body(
        b"\x1f\x09\x00\x17"
        + jis_text("略．")
        + b"\x1f\x4d"
        + payload
        + b"\x1f\x04"
        + (b"\x21\x21" * 6)
        + b"\x1f\x05"
        + jis_text("本文"),
        HcRenderOptions(renderer_code="0151", media_image_resolver=resolve),
    )

    assert '<br><div class="hc0151-media-block"><img class="lv-hc-media"' in rendered.html
    assert 'src="media/colscr/00000123_0045.jpg"' in rendered.html
    assert '<span class="hankaku">      </span>' not in rendered.html
    assert "本文" in rendered.html
    assert seen == [MediaPointer(payload=payload, block=123, offset=45)]
    assert rendered.stats["hc0151_media_blocks"] == 1
    assert rendered.stats["hc0151_suppressed_media_padding_chars"] == 6


def test_hc0151_suppresses_unused_template_markers_and_preserves_black_midashi() -> None:
    rendered = render_hc_body(
        b"\x1f\x41\x01\x60" + jis_text("見出し") + b"\x1f\x61" + b"\xb1\x4e" + jis_ascii_text("abc") + b"\xb1\x4f",
        HcRenderOptions(
            renderer_code="0151",
            image_sources={"b14e": "Templates/B14E.png", "b14f": "Templates/B14F.png"},
        ),
    )

    assert '<div class="midashi hc0151-midashi">見出し</div>' in rendered.html
    assert "Templates/B14E.png" not in rendered.html
    assert "Templates/B14F.png" not in rendered.html
    assert 'data-gaiji-code="b14e"' not in rendered.html
    assert 'data-gaiji-code="b14f"' not in rendered.html
    assert '<span class="hankaku">abc</span>' in rendered.html
    assert rendered.stats["hc0151_suppressed_internal_markers"] == 2


def test_hc0151_adds_sentence_breaks_for_long_definition_lines() -> None:
    rendered = render_hc_body(
        b"\x1f\x09\x00\x17" + jis_text("あ" * 41 + "．") + jis_text("次"),
        HcRenderOptions(renderer_code="0151"),
    )

    assert "あ．<br>次" in rendered.html
    assert rendered.stats["hc0151_auto_sentence_breaks"] == 1


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


def test_hc0142_consumes_media_close_renderer_state_controls() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + jis_text("本文")
        + b"\x1f\x4d"
        + b"\x00" * 12
        + b"\x00\x01\x65\x51\x17\x30"
        + b"\x3f\x74\x3c\x30"
        + b"\x1f\x6d"
        + b"\x1f\x5c"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0142"))

    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert "unknown_control_1f5c" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0142_nonprinting_controls"] == 3
    assert rendered.stats["media_placeholders"] == 1


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
    assert '<span class="lv-hc-halfwidth">B</span>' in rendered.html
    assert '<div class="hr_div2"></div><span class="lv-hc-halfwidth">C</span></div>' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert 'class="lv-hc-heading"' not in rendered.html
    assert rendered.stats["hc0065_midashi_blocks"] == 1
    assert rendered.stats.get("hc0065_headword_prefixes", 0) == 0
    assert rendered.stats["hc0065_contents_body_blocks"] == 1
    assert rendered.stats["hc0065_state_sections"] == 2


def test_hc0065_suppresses_italic_controls_for_headword_pronunciation_and_crossrefs() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\xe0\x11\x03"
        + b"\x1f\x10"
        + jis_ascii_text("oh")
        + b"\x1f\x0e"
        + jis_text("１")
        + b"\x1f\x0f"
        + b"\x1f\x11"
        + b"\x1f\xe1"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text(" **")
        + b"\x1f\x10"
        + jis_ascii_text("/o")
        + b"\x1f\x11"
        + jis_ascii_text("u/")
        + jis_text("⇔")
        + b"\x1f\x10"
        + jis_ascii_text("unofficially")
        + b"\x1f\x11"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0065"))

    assert "<i>" not in rendered.html
    assert "</i>" not in rendered.html
    assert '<div class="midashi"><b><span class="lv-hc-halfwidth">oh</span><sup>１</sup>' in rendered.html
    assert '<span class="lv-hc-halfwidth">/o</span><span class="lv-hc-halfwidth">u/</span>' in rendered.html
    assert '⇔<span class="lv-hc-halfwidth">unofficially</span>' in rendered.html
    assert rendered.stats["hc0065_suppressed_italic_controls"] == 6


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

    assert '<div class="midashi hc0067-plain-midashi"><span class="hankaku">H</span></div><div class="contents_body hc0067-contents-body">' in rendered.html
    assert ".hc0067-plain-midashi" in HC_RENDER_FINAL_CSS
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
        + jis_text("［英")
        + jis_ascii_text("cleavagetype")
        + jis_text("］")
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

    assert '<div class="midashi hc0068-plain-midashi"><span class="hankaku">H</span><img src="Templates/dummy.gif" class="img_dummy"><img class="lv-hc-gaiji img_gaiji_midashi"' in rendered.html
    assert '</div><div class="contents_body hc0068-contents-body">' in rendered.html
    assert '<span class="hc0068-source-line">［英<span class="hankaku">cleavagetype</span>］</span><br>' in rendered.html
    assert ".hc0068-source-line" in HC_RENDER_FINAL_CSS
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
    assert rendered.stats["hc0068_source_lines"] == 1
    assert rendered.stats["hc0068_img_gaiji_midashi_images"] == 1
    assert rendered.stats["hc0068_img_gaiji_images"] == 1
    assert rendered.stats["hc0068_nonprinting_controls"] == 1


def test_hc0020_maps_midashi_contents_definition_markers_links_and_gaiji() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii("H")
        + bytes.fromhex("222a")
        + bytes.fromhex("b12d")
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + bytes.fromhex("b12e")
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
    assert rendered.html.count('<img src="Templates/confer.png" class="img_confer">') == 1
    assert '<img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/b12d.png"' in rendered.html
    assert '<img class="lv-hc-gaiji img_gaiji" src="Templates/b12e.png"' in rendered.html
    assert '<img src="Templates/confer.png" class="img_confer"><img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/b12d.png"' not in rendered.html
    assert '<img src="Templates/confer.png" class="img_confer"><img class="lv-hc-gaiji img_gaiji_midashi" src="Templates/b12e.png"' not in rendered.html
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
    assert rendered.stats["hc0020_midashi_confer_markers_suppressed"] == 1
    assert rendered.stats["hc0020_confer_following_markers_suppressed"] == 1
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


def test_hc008b_recovers_unterminated_line_link_as_plain_text() -> None:
    body = b"\x1f\x42" + jis_text("未閉リンク")

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="008B"))

    assert "未閉リンク" in rendered.html
    assert "lv-hc-link" not in rendered.html
    assert "lvaddr://" not in rendered.html
    assert "unterminated_link" not in rendered.named_behavior_gaps
    assert rendered.stats["hc008b_unterminated_link_recovered"] == 1


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


def test_hc0065_part_of_speech_marker_uses_following_label_image() -> None:
    rendered = render_hc_body(
        b"\xa4\x3a" + jis_text("【間】") + b"\xa4\x3a" + jis_text("副】"),
        HcRenderOptions(
            renderer_code="0065",
            image_sources={"interjective": "Templates/interjective.png", "adverb": "Templates/adverb.png"},
        ),
    )

    assert 'src="Templates/interjective.png"' in rendered.html
    assert 'src="Templates/adverb.png"' in rendered.html
    assert '<span class="hc0065-hinshi-prefix">━</span><img class="lv-hc-gaiji img_hinshi" src="Templates/interjective.png"' in rendered.html
    assert '<span class="hc0065-hinshi-prefix">━</span><img class="lv-hc-gaiji img_hinshi" src="Templates/adverb.png"' in rendered.html
    assert "【間】" not in rendered.html
    assert "副】" not in rendered.html
    assert rendered.stats["hc0065_part_of_speech_images"] == 2


def test_hc0065_example_marker_opens_exam_box_and_suppresses_delimiters() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + jis_ascii_text("oh")
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("XX")
        + bytes((0x21, 0x42))
        + jis_ascii_text("Oh")
        + b"\x21\x21"
        + jis_ascii_text("dear")
        + bytes((0x21, 0x3F))
        + jis_ascii_text("Oh")
        + b"\x21\x21"
        + jis_ascii_text("great")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0065", image_sources={"exam": "Templates/exam.png"}),
    )

    assert '<img src="Templates/exam.png" class="ex_img">' in rendered.html
    assert '<div class="contents">' in rendered.html
    assert "‖" not in rendered.html
    assert "/Oh" not in rendered.html
    assert "Oh\u3000dear" in rendered.plain
    assert "Oh\u3000great" in rendered.plain
    assert rendered.stats["hc0065_example_blocks"] == 1
    assert rendered.stats["hc0065_suppressed_example_start_markers"] == 1
    assert rendered.stats["hc0065_suppressed_example_continuation_markers"] == 1


def test_hc0065_internal_links_use_product_class() -> None:
    body = b"\x1f\x42" + jis_ascii("G") + b"\x1f\x62\x00\x00\x00\x03\x00\x40"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0065"))

    assert 'class="lv-hc-link lLink"' in rendered.html
    assert 'href="lvaddr://00000003/0064"' in rendered.html


def test_hc0065_consumes_media_close_state_control() -> None:
    body = b"\x1f\x4d\x00\x09\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x06\x48\x38\x05\x06" + b"\x1f\x6d"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0065"))

    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0065_nonprinting_controls"] == 1


def test_hc0158_renders_rank_marker_stars_without_gaiji_placeholder() -> None:
    rendered = render_hc_body(
        b"\xb3\x55" + jis_ascii("A") + b"\xb3\x54",
        HcRenderOptions(renderer_code="0158"),
    )

    assert '<span class="rank1"><sup>&#x2605;&#x2605;</sup>' in rendered.html
    assert "★★A" in rendered.plain
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc0158_style_markers"] == 2


def test_hc0158_rank1_marker_uses_second_close_for_full_midashi() -> None:
    rendered = render_hc_body(
        b"\xb3\x55" + jis_text("たか") + b"\xb3\x54" + jis_text("【高し】") + b"\xb3\x54",
        HcRenderOptions(renderer_code="0158"),
    )

    assert '<span class="rank1"><sup>&#x2605;&#x2605;</sup>たか【高し】</span>' in rendered.html
    assert rendered.stats["hc0158_style_markers"] == 3
    assert rendered.stats["hc0158_rank1_midashi_delimiters"] == 1
    assert rendered.stats.get("hc0158_unmatched_style_markers", 0) == 0


def test_hc0158_rank4_guide_marker_closes_at_heading_end() -> None:
    rendered = render_hc_body(
        b"\x1f\x41\x01\x60" + b"\xb3\x53\xb3\x5e" + jis_text("識別ガイド") + b"\x1f\x61",
        HcRenderOptions(renderer_code="0158"),
    )

    assert '<div class="midashi"><span class="rank4">識別ガイド</span></div>' in rendered.html
    assert rendered.stats["hc0158_section_end_style_markers"] == 1
    assert "unterminated_hc0158_marker" not in rendered.named_behavior_gaps


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
    assert '<div class="column_waka_haiku">' in rendered.html
    assert rendered.stats["hc0158_section_blocks"] == 4
    assert rendered.stats["hc0158_section_state_1"] == 1
    assert rendered.stats["hc0158_section_state_24"] == 1


def test_hc0158_keeps_panel_column_content_inside_container() -> None:
    body = (
        b"\x1f\x09\x00\x27"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x21"
        + jis_text("語義パネル")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("語義本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x28"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0158"))

    assert '<div class="column_gogi_panel"><div class="honbun_columntitle">語義パネル</div><div class="honbun_normal">語義本文</div></div>' in rendered.html
    assert '<div class="column_gogi_panel"></div>' not in rendered.html
    assert rendered.stats["hc0158_column_panels_closed"] == 1


def test_hc0158_tableizes_ruigo_panel_rows_and_suppresses_title_waku() -> None:
    body = (
        b"\x1f\x09\x00\x33"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x21"
        + b"\xb3\x79"
        + jis_text("類語パネル")
        + b"\xb3\x7a"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("あし‖本質的に悪い。")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x02"
        + jis_text("よし‖本質的によい。")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x34"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0158"))

    assert '<div class="honbun_columntitle">類語パネル</div>' in rendered.html
    assert '<span class="waku">類語パネル</span>' not in rendered.html
    assert '<table class="hc0158-panel-table"><tbody>' in rendered.html
    assert '<td class="column_ruigo_panel_headercell">あし</td><td class="column_ruigo_panel_bodycell">本質的に悪い。</td>' in rendered.html
    assert '<td class="column_ruigo_panel_headercell">よし</td><td class="column_ruigo_panel_bodycell">本質的によい。</td>' in rendered.html
    assert rendered.stats["hc0158_ruigo_panel_rows"] == 2
    assert rendered.stats["hc0158_ruigo_panel_delimiters"] == 2


def test_hc0158_bolds_archsic4_grammar_labels() -> None:
    body = b"\x1f\x09\x00\x13" + jis_text("［文法］本文") + b"\x1f\x0a"

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0158"))

    assert '<div class="honbun_bunpou2"><b class="hc0158-grammar-label">［文法］</b>本文</div>' in rendered.html
    assert rendered.stats["hc0158_grammar_labels_bolded"] == 1


def test_hc0158_renders_img_private_directive_from_media_source() -> None:
    body = b"\x1f\xe2\x00\x0c" + jis_fullwidth_ascii("IMG:b0010.bmp") + b"\x1f\xe3\x00\x00"

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="0158", image_sources={"b0010": "HTMLs/b0010.bmp"}),
    )

    assert '<img class="lv-hc-media column_picture_img" src="HTMLs/b0010.bmp"' in rendered.html
    assert rendered.stats["hc0158_private_directive_images"] == 1


def test_prepare_hc_render_assets_extracts_vlpljbl_media_store(tmp_path: Path) -> None:
    root = tmp_path / "ARCHSIC4"
    root.mkdir()
    idx = root / "ARCHSIC4.IDX"
    idx.write_bytes(b"")
    honmon = root / "HONMON.DIC"
    honmon.write_bytes(b"")
    con = sqlite3.connect(root / "vlpljblM")
    con.execute("create table t_media(f_name text primary key, f_blob blob)")
    con.execute("insert into t_media values(?, ?)", ("c0001", b"\xff\xd8\xff\xdbjpeg"))
    con.execute("insert into t_media values(?, ?)", ("b0010", b"BMbmp"))
    con.commit()
    con.close()
    source = DictionarySource("ARCHSIC4", idx, "ARCHSIC4", honmon, 1, {})
    (tmp_path / "out").mkdir()

    image_sources, _templates, _stylesheet, copied, _assets = _prepare_hc_render_assets(source, tmp_path / "out", None)

    assert copied >= 2
    assert image_sources["c0001"] == "HTMLs/c0001.jpg"
    assert image_sources["b0010"] == "HTMLs/b0010.bmp"
    assert (tmp_path / "out" / "HTMLs" / "c0001.jpg").read_bytes() == b"\xff\xd8\xff\xdbjpeg"
    assert (tmp_path / "out" / "HTMLs" / "b0010.bmp").read_bytes() == b"BMbmp"


def test_prepare_hc_render_assets_extracts_gai16_suffix_bitmap_gaiji(tmp_path: Path) -> None:
    root = tmp_path / "ARCHSIC3"
    root.mkdir()
    idx = root / "ARCHSIC3.IDX"
    idx.write_bytes(b"")
    honmon = root / "HONMON.DIC"
    honmon.write_bytes(b"")
    header = bytearray(2048)
    header[0] = 1
    header[8] = 16
    header[9] = 16
    header[10:12] = bytes.fromhex("a121")
    header[12:14] = (1).to_bytes(2, "big")
    (root / "GAI16F00").write_bytes(bytes(header) + (b"\xff\x00" * 16))
    source = DictionarySource("ARCHSIC3", idx, "ARCHSIC3", honmon, 1, {})
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    image_sources, _templates, _stylesheet, copied, _assets = _prepare_hc_render_assets(source, out_dir, None)
    rendered = render_hc_body(b"\xa1\x21", HcRenderOptions(renderer_code="ARCHSIC3", image_sources=image_sources))

    assert copied >= 1
    assert image_sources["a121"] == "gaiji/GAI16F00/A121_jis_grid.bmp"
    assert (out_dir / image_sources["a121"]).is_file()
    assert 'src="gaiji/GAI16F00/A121_jis_grid.bmp"' in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["gaiji_image"] == 1


def test_prepare_hc_render_assets_extracts_numbered_media_store(tmp_path: Path) -> None:
    root = tmp_path / "KOJIEN7"
    root.mkdir()
    idx = root / "KOJIEN7.IDX"
    idx.write_bytes(b"")
    honmon = root / "HONMON.DIC"
    honmon.write_bytes(b"")
    con = sqlite3.connect(root / "vlpljblM")
    con.execute("create table media(No integer primary key, f_name text, f_type integer, f_main blob)")
    con.execute("insert into media values(?, ?, ?, ?)", (4186, "50029810", 1, b"\xff\xd8\xff\xe0jpeg"))
    con.execute(
        "insert into media values(?, ?, ?, ?)",
        (4187, "P0072_04", 4, b'<?xml version="1.0" encoding="UTF-8"?><svg id="x"/>'),
    )
    con.execute("insert into media values(?, ?, ?, ?)", (4188, "011_3", 5, b"II*\x00\x08\x00\x00\x00"))
    con.commit()
    con.close()
    source = DictionarySource("KOJIEN7", idx, "KOJIEN7", honmon, 1, {})
    (tmp_path / "out").mkdir()

    image_sources, _templates, _stylesheet, copied, _assets = _prepare_hc_render_assets(source, tmp_path / "out", None)

    assert copied >= 1
    assert image_sources["50029810"] == "HTMLs/50029810.jpg"
    assert image_sources["50029810.jpg"] == "HTMLs/50029810.jpg"
    assert (tmp_path / "out" / "HTMLs" / "50029810.jpg").read_bytes() == b"\xff\xd8\xff\xe0jpeg"
    assert image_sources["p0072_04"] == "HTMLs/P0072_04.svg"
    assert image_sources["011_3"] == "HTMLs/011_3.tif"
    assert (tmp_path / "out" / "HTMLs" / "P0072_04.svg").read_text(encoding="utf-8").startswith("<?xml")
    assert (tmp_path / "out" / "HTMLs" / "011_3.tif").read_bytes().startswith(b"II*\x00")


def test_prepare_hc_render_assets_decodes_shift_jis_stylesheet(tmp_path: Path) -> None:
    root = tmp_path / "JSSAURU2"
    (root / "Templates").mkdir(parents=True)
    idx = root / "JSSAURU2.IDX"
    idx.write_bytes(b"")
    honmon = root / "HONMON.DIC"
    honmon.write_bytes(b"")
    (root / "Templates" / "0000015C.css").write_bytes(
        '@charset "Shift_JIS";\n.ico_shoubunrui:after{content:"小語群";}\n'
        '.ico_sakuin:after{content:"索引";}\n'
        '.wo_goi_del_bu:before{content:"・";}\n'
        '.image_box:before{content:"画像をクリックして拡大";}\n'
        '.author:before{content:"◆ ";}\n'.encode("cp932")
    )
    source = DictionarySource("JSSAURU2", idx, "JSSAURU2", honmon, 1, {})
    renderer = HcRendererClassification(
        path=root / "HC015C.dll",
        code="015C",
        expected_numeric_index="0000015C.IDX",
        size=1,
        sha256=None,
        pe=PeSummary(kind="pe"),
        exinfo_html_dll="HC015C.dll",
        exinfo_declares_this=True,
        numeric_indexes=("0000015C.IDX",),
        expected_numeric_index_present=True,
        vlpljbl_siblings=(),
        dic_tokens=(),
        vlpljbl_tokens=(),
        html_templates=(),
        sql_snippets=(),
        image_templates=(),
        features={},
    )
    (tmp_path / "out").mkdir()

    _image_sources, _templates, stylesheet, _copied, _assets = _prepare_hc_render_assets(source, tmp_path / "out", renderer)

    assert stylesheet == "hc-renderer.css"
    css = (tmp_path / "out" / "hc-renderer.css").read_text(encoding="utf-8")
    assert '@charset "Shift_JIS"' not in css
    assert "小語群" in css
    assert "索引" in css
    assert 'content:"・"' in css
    assert "画像をクリックして拡大" in css
    assert 'content:"◆ "' in css
    assert "�" not in css


def test_rewrite_exact_body_asset_refs_uses_case_insensitive_lookup(tmp_path: Path) -> None:
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "dummy.GIF").write_bytes(b"gif")
    (tmp_path / "Templates" / "b152.svg").write_text("<svg/>", encoding="utf-8")

    rewritten = _rewrite_exact_body_asset_refs(
        '<img src="dummy.gif"><object data="dummy.gif"></object><object data="B152.svg" class="gaiji"></object>',
        tmp_path,
    )

    assert rewritten == (
        '<img src="Templates/dummy.GIF"><object data="Templates/dummy.GIF"></object>'
        '<object data="Templates/b152.svg" class="gaiji"></object>'
    )


def test_hc_render_entry_lookup_prefers_exact_short_query_over_noise() -> None:
    assert _score_index_row("et", {"key": "ET", "target_key": "ET"}) == (120, "target_key:exact")
    assert _score_index_row("et", {"key": "ABÊTIR", "target_key": "ABÊTIR"}) == (0, "")
    assert _score_index_row("crocodile", {"key": "CROCODILE", "target_key": "CROCODILE"}) == (
        120,
        "target_key:exact",
    )


def test_rewrite_exact_body_asset_refs_can_prefer_horizontal_template_assets(tmp_path: Path) -> None:
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "b174.png").write_bytes(b"png")
    (tmp_path / "Templates" / "b174_v.png").write_bytes(b"vertical")
    (tmp_path / "Templates" / "b225.png").write_bytes(b"png")
    (tmp_path / "Templates" / "b225_v.png").write_bytes(b"vertical")

    rewritten = _rewrite_exact_body_asset_refs(
        '<img src="Templates/b174_v.png"><object data="b225_v.png"></object>',
        tmp_path,
        prefer_horizontal_assets=True,
    )

    assert rewritten == '<img src="Templates/b174.png"><object data="Templates/b225.png"></object>'


def test_exact_rendererdb_media_placeholders_use_raw_honmon_media_sources(tmp_path: Path) -> None:
    dict_out = tmp_path / "ZYAKUKOG"
    (dict_out / "media" / "colscr").mkdir(parents=True)
    (dict_out / "Templates").mkdir(parents=True)
    (dict_out / "Templates" / "katu.png").write_bytes(b"png")
    (dict_out / "Templates" / "katu_v.png").write_bytes(b"vertical")
    (dict_out / "media" / "colscr" / "00044253_1574.bmp").write_bytes(b"BM")
    raw_entries = dict_out / "hc_entries.jsonl"
    raw_entries.write_text(
        json.dumps(
            {
                "entry_index": 3977,
                "html": '<img class="lv-hc-media" src="media/colscr/00044253_1574.bmp">',
                "media": [
                    {
                        "target": {
                            "component": "COLSCR.DIC",
                            "block": 44253,
                            "offset": 1574,
                        }
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rendererdb_entries = dict_out / "rendererdb_entries.jsonl"
    rendererdb_entries.write_text(
        json.dumps(
            {
                "dict_id": "ZYAKUKOG",
                "raw_honmon": {"entry_index": 3977},
                "html": '<div><img src="00001163-1166-00000001.gif"><img src="katu_v.png"></div>',
                "plain": "川竹",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(rendererdb_entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="013F",
        raw_honmon_entries_path=raw_entries,
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert '<html class="h">' in rendered
    assert 'src="media/colscr/00044253_1574.bmp"' in rendered
    assert 'src="Templates/katu.png"' in rendered
    assert "00001163-1166-00000001.gif" not in rendered
    assert "_v.png" not in rendered


def test_exact_rendererdb_fragment_media_writes_referenced_blobs(tmp_path: Path) -> None:
    con = sqlite3.connect(":memory:")
    try:
        con.execute("create table media(No integer primary key, f_name text, f_type integer, f_main blob)")
        con.execute("insert into media values(?, ?, ?, ?)", (3539, "T03983_KK010", 2, b"\x89PNG\r\n\x1a\npng"))
        con.execute("insert into media values(?, ?, ?, ?)", (3540, "unused", 2, b"\x89PNG\r\n\x1a\nunused"))
        con.commit()

        media_dir = tmp_path / "SINJIGEN" / "rendererdb" / "SINJIGEN" / "media"
        written = _write_rendererdb_fragment_media(
            con,
            media_dir,
            '<div><img src="T03983_KK010.png"><img src="missing.png"></div>',
        )
    finally:
        con.close()

    assert written == 1
    assert (media_dir / "T03983_KK010.png").is_file()
    assert not (media_dir / "unused.png").exists()
    rewritten = _rewrite_exact_body_asset_refs('<img src="T03983_KK010.png">', tmp_path / "SINJIGEN")
    assert 'src="rendererdb/SINJIGEN/media/T03983_KK010.png"' in rewritten


def test_hc_render_vertical_requires_explicit_flag(tmp_path: Path) -> None:
    root = tmp_path / "DAIJIRN4"
    root.mkdir()
    idx = root / "DAIJIRN4.IDX"
    idx.write_bytes(b"")
    honmon = root / "HONMON.DIC"
    honmon.write_bytes(b"")
    (root / "EXINFO.INI").write_text("[GENERAL]\nVERTICAL=1\n", encoding="cp932")
    source = DictionarySource("DAIJIRN4", idx, "DAIJIRN4", honmon, 1, {})

    assert _source_prefers_vertical(source, argparse.Namespace(vertical=False)) is False
    assert _source_prefers_vertical(source, argparse.Namespace(vertical=True)) is True


def test_exact_rendererdb_entries_get_windows_headword_style(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"DICT","data_id":5,"html":"<div class=\\"jr_midashi\\"><span class=\\"jr_hw_midashi\\">head</span></div><object data=\\"B152.svg\\" class=\\"gaiji\\"></object>","plain":"head"}\n',
        encoding="utf-8",
    )
    (dict_out / "Templates").mkdir()
    (dict_out / "Templates" / "b152.svg").write_text("<svg/>", encoding="utf-8")

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="015B",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert '<html class="h">' in rendered
    assert '<body class="h">' in rendered
    assert 'class="rendererdb-entry exact-rendererdb-entry hc015b lv-hc-windows-reader lv-hc-blue-headword"' in rendered
    assert '<span class="jr_hw_midashi">head</span>' in rendered
    assert 'data="Templates/b152.svg"' in rendered
    assert ".rendererdb-entry.lv-hc-windows-reader.lv-hc-blue-headword .jr_midashi" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry object.gaiji" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry.hc015a" in HC_RENDER_FINAL_CSS
    assert "Yu Mincho" in HC_RENDER_FINAL_CSS


def test_hc0161_exact_rendererdb_figure_rl_svg_is_font_sized(tmp_path: Path) -> None:
    dict_out = tmp_path / "YUPSYCHO"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"YUPSYCHO","data_id":29,'
        '"html":"<span>明るさ</span><img src=\\"P0005_06.svg\\" width=\\"117\\" height=\\"94\\" class=\\"figureRL\\">",'
        '"plain":"明るさ"}\n',
        encoding="utf-8",
    )
    (dict_out / "P0005_06.svg").write_text("<svg/>", encoding="utf-8")

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet="hc-renderer.css",
        vertical=False,
        renderer_code="0161",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'class="rendererdb-entry exact-rendererdb-entry hc0161 lv-hc-windows-reader"' in rendered
    assert 'src="P0005_06.svg"' in rendered
    assert 'class="figureRL"' in rendered
    assert ".rendererdb-entry.hc0161 img.figureRL" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry.hc0162 img.figureRL" in HC_RENDER_FINAL_CSS
    assert "height: 1em !important;" in HC_RENDER_FINAL_CSS
    assert "width: auto !important;" in HC_RENDER_FINAL_CSS


def test_hc015d_exact_rendererdb_entries_force_vertical_shell_and_rewrite_media(tmp_path: Path) -> None:
    dict_out = tmp_path / "SINJIGEN"
    (dict_out / "HTMLs").mkdir(parents=True)
    (dict_out / "HTMLs" / "T03983_KK010.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"SINJIGEN","data_id":112380,'
        '"html":"<div class=\\"midashi\\">新</div><div class=\\"honbun\\">'
        '<div class=\\"kodaimoji_box\\"><img src=\\"T03983_KK010.png\\" class=\\"img_kodaimoji\\"></div>'
        '<span class=\\"imi_top\\"></span></div>",'
        '"plain":"新"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet="hc-renderer.css",
        vertical=False,
        renderer_code="015D",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert '<html class="v">' in rendered
    assert '<body class="v">' in rendered
    assert 'class="rendererdb-entry exact-rendererdb-entry hc015d lv-hc-windows-reader lv-hc-blue-headword"' in rendered
    assert 'src="HTMLs/T03983_KK010.png"' in rendered


def test_hc015d_exact_rendererdb_resolves_dictionary_font_pua(tmp_path: Path) -> None:
    dict_out = tmp_path / "SINJIGEN"
    dict_out.mkdir()
    sqlite_path = dict_out / "vlpljblb.sqlite"
    con = sqlite3.connect(sqlite_path)
    try:
        con.execute("CREATE TABLE t_kanji (f_DataId INTEGER, f_Title TEXT, f_UniCode TEXT)")
        con.execute("INSERT INTO t_kanji VALUES (1, ?, '200A2')", ("\ue209",))
        con.execute("INSERT INTO t_kanji VALUES (2, '薪', '85AA')")
        con.execute("INSERT INTO t_kanji VALUES (2, ?, '―')", ("\ue5c9",))
        con.commit()
    finally:
        con.close()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"SINJIGEN","data_id":112380,'
        '"html":"<div class=\\"midashi\\">新</div><div class=\\"honbun\\">'
        '<span class=\\"imi\\">\\ue0b0\\ue130\\ue029\\ue84b\\ue5c9\\ue209\\ue1ad</span>'
        '<span class=\\"wari\\"><span class=\\"wari_mae\\">シ</span><br><span class=\\"wari_ato\\">ン</span></span>'
        '<span class=\\"wari\\"><span class=\\"wari_mae\\">あた</span><br><span class=\\"wari_ato\\">らし</span></span>'
        '</div>",'
        '"plain":"新"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries), "sqlite_path": str(sqlite_path)},
        dict_out,
        stylesheet="hc-renderer.css",
        vertical=False,
        renderer_code="015D",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert "➊" in rendered
    assert "㋐" in rendered
    assert "亲" in rendered
    assert "薪" in rendered
    assert chr(0x200A2) in rendered
    assert "㆑" in rendered
    assert 'src="HANREI/image/967.png"' in rendered
    assert 'class="hc015d-marker"' in rendered
    assert "\ue0b0" not in rendered
    assert "\ue130" not in rendered
    assert "\ue029" not in rendered
    assert "\ue84b" not in rendered
    assert "\ue5c9" not in rendered
    assert "\ue209" not in rendered
    assert "\ue1ad" not in rendered
    assert ".rendererdb-entry.hc015d .wari br" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry.hc015d .oyaji_data_main_jou" in HC_RENDER_FINAL_CSS
    assert "html.v .rendererdb-entry.hc015d .oyaji_data_main_jou" in HC_RENDER_FINAL_CSS
    assert "html.v .rendererdb-entry.hc015d .oyaji_data_no" in HC_RENDER_FINAL_CSS
    assert "html.v .rendererdb-entry.hc015d .wari" in HC_RENDER_FINAL_CSS
    assert "hc015d-wari-chars" in rendered
    assert "hc015d-wari-left" in rendered
    assert "hc015d-wari-right" in rendered
    assert '<span class="hc015d-wari-cell hc015d-wari-left" style="grid-row:1">ら</span>' in rendered
    assert '<span class="hc015d-wari-cell hc015d-wari-right" style="grid-row:1">あ</span>' in rendered
    assert '<span class="hc015d-wari-cell hc015d-wari-left" style="grid-row:2">し</span>' in rendered
    assert '<span class="hc015d-wari-cell hc015d-wari-right" style="grid-row:2">た</span>' in rendered
    assert "display: inline-grid !important;" in HC_RENDER_FINAL_CSS
    assert "grid-template-columns: max-content max-content !important;" in HC_RENDER_FINAL_CSS
    assert "column-gap: 0.12em !important;" in HC_RENDER_FINAL_CSS
    assert "row-gap: 0.06em !important;" in HC_RENDER_FINAL_CSS
    assert "width: 0.74em !important;" in HC_RENDER_FINAL_CSS
    assert "direction: ltr !important;" in HC_RENDER_FINAL_CSS
    assert "grid-column: 1 !important;" in HC_RENDER_FINAL_CSS
    assert "grid-column: 2 !important;" in HC_RENDER_FINAL_CSS


def test_hc015d_known_dictionary_font_pua_resolves_without_kanji_table(tmp_path: Path) -> None:
    dict_out = tmp_path / "SINJIGEN"
    dict_out.mkdir()
    sqlite_path = dict_out / "vlpljblb.sqlite"
    con = sqlite3.connect(sqlite_path)
    try:
        con.execute("CREATE TABLE t_data (id INTEGER PRIMARY KEY)")
        con.commit()
    finally:
        con.close()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"SINJIGEN","data_id":112380,'
        '"html":"<div class=\\"honbun\\">\\ue5c9</div>",'
        '"plain":"新"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries), "sqlite_path": str(sqlite_path)},
        dict_out,
        stylesheet="hc-renderer.css",
        vertical=False,
        renderer_code="015D",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert "薪" in rendered
    assert "\ue5c9" not in rendered


def test_hc03e8_exact_rendererdb_plain_rows_get_midashi_honbun_layout(tmp_path: Path) -> None:
    dict_out = tmp_path / "GENKANA5"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"GENKANA5","id":4049,'
        '"html":"エクストリーム出社【 Extreme― 】 エクストリームは，極限。",'
        '"plain":"エクストリーム出社【 Extreme― 】 エクストリームは，極限。"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="03E8",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'class="rendererdb-entry exact-rendererdb-entry hc03e8 lv-hc-windows-reader lv-hc-blue-headword"' in rendered
    assert '<div class="midashi">エクストリーム出社【 Extreme― 】</div>' in rendered
    assert '<div class="honbun" style="margin-left:2.000000em;">エクストリームは，極限。</div>' in rendered
    assert ".rendererdb-entry.lv-hc-windows-reader.lv-hc-blue-headword .midashi" in HC_RENDER_FINAL_CSS
    assert "font-size: 1.15em;" in HC_RENDER_FINAL_CSS


def test_exact_rendererdb_headword_blue_style_is_renderer_scoped(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"DICT","data_id":5,"html":"<div class=\\"midashi\\">head</div>","plain":"head"}\n',
        encoding="utf-8",
    )

    blue_html = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="015A",
    ).read_text(encoding="utf-8")
    black_html = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="015E",
    ).read_text(encoding="utf-8")

    assert "lv-hc-blue-headword" in blue_html
    assert "hc015e lv-hc-windows-reader" in black_html
    assert "lv-hc-blue-headword" not in black_html
    assert ".rendererdb-entry.hc015e .akusentnum" in HC_RENDER_FINAL_CSS
    assert "text-combine-upright: all" in HC_RENDER_FINAL_CSS
    assert "html.v .exact-rendererdb-entry + .exact-rendererdb-entry" in HC_RENDER_FINAL_CSS


def test_hc015f_exact_rendererdb_japanese_caption_uses_royal_colours(tmp_path: Path) -> None:
    dict_out = tmp_path / "PROYAL53"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"PROYAL53","data_id":330815,'
        '"html":"<div class=\\"caption\\"><span class=\\"large_gothic_font\\">'
        '<font class=\\"color_font\\">はこぶ</font></span>【運ぶ】</div>",'
        '"plain":"はこぶ【運ぶ】"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="015F",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'class="rendererdb-entry exact-rendererdb-entry hc015f lv-hc-windows-reader"' in rendered
    assert '<font class="color_font">はこぶ</font></span>【運ぶ】' in rendered
    assert ".rendererdb-entry.hc015f .caption" in HC_RENDER_FINAL_CSS
    assert "color: #113f08 !important;" in HC_RENDER_FINAL_CSS
    assert ".rendererdb-entry.hc015f .caption .color_font" in HC_RENDER_FINAL_CSS
    assert "color: #ba2636 !important;" in HC_RENDER_FINAL_CSS


def test_hc0153_exact_rendererdb_section_labels_use_heading_css(tmp_path: Path) -> None:
    dict_out = tmp_path / "HOUGAKU5"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"HOUGAKU5","data_id":"K0j6WEa5",'
        '"html":"<div class=\\"midashi\\">司法的統制</div><p><span class=\\"009QG\\">1 意義</span>本文'
        '<br/><br/><span class=\\"009QG\\">2 特徴</span>本文</p>",'
        '"plain":"司法的統制"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
        renderer_code="0153",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'class="rendererdb-entry exact-rendererdb-entry hc0153 lv-hc-windows-reader"' in rendered
    assert '<span class="009QG">1 意義</span>' in rendered
    assert '<span class="009QG">2 特徴</span>' in rendered
    assert '.rendererdb-entry.hc0153 [class~="009QG"]' in HC_RENDER_FINAL_CSS
    assert "display: block;" in HC_RENDER_FINAL_CSS


def test_exact_rendererdb_vertical_shell_enables_package_vertical_css(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    dict_out.mkdir()
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        "\n".join(
            [
                '{"dict_id":"DICT","data_id":1,"html":"<div>one</div>","plain":"one"}',
                '{"dict_id":"DICT","data_id":2,"html":"<div>two</div>","plain":"two"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=True,
        renderer_code="015E",
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert '<html class="v">' in rendered
    assert '<body class="v">' in rendered
    assert rendered.count("exact-rendererdb-entry") == 2


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
    assert 'href="lvaddr://00000003/0040"' in rendered.html
    assert 'data-lv-offset="40"' in rendered.html


def test_hc0157_closes_style_markers_inside_links() -> None:
    body = (
        b"\x1f\x42"
        + b"\xb2\x21"
        + jis_fullwidth_ascii("A")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x03"
        + b"\xb2\x22"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0157"))

    assert '<a class="lv-hc-link lineLink"' in rendered.html
    assert '<span class="smallcap">Ａ</span></a>' in rendered.html
    assert "</a></span>" not in rendered.html
    assert rendered.stats["hc0157_link_scoped_style_closures"] == 1
    assert rendered.stats["hc0157_link_scoped_close_markers"] == 1
    assert "unterminated_hc0157_marker_b222" not in rendered.named_behavior_gaps


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
    assert '<div class="yourei_ej"><div></div>' not in rendered.html
    assert '<div class="yourei_ej"><div class="textline yourei_ej_main">' in rendered.html
    assert '<div class="textline yourei_ej_main">' in rendered.html
    assert '<div class="textline yourei_ej_sub">' in rendered.html
    assert '<div class="haseigo"><div></div>' not in rendered.html
    assert '<div class="haseigo"><div class="textline haseigo_main">' in rendered.html
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


def test_hc0146_repairs_color_font_inside_bold_style() -> None:
    body = (
        b"\x1f\xe0\x00\x00"
        + b"\xb2\x32"
        + jis_ascii("A")
        + b"\x1f\xe1\x00\x00"
        + b"\x1f\xe0\x00\x00"
        + jis_ascii("B")
        + b"\xb2\x33"
        + b"\x1f\xe1\x00\x00"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<font class="color_font"><b><span class="lv-hc-halfwidth">A</span></b>' in rendered.html
    assert '<b><span class="lv-hc-halfwidth">B</span></b></font>' in rendered.html
    assert rendered.html.count("<b>") == rendered.html.count("</b>")
    assert rendered.html.count("<font") == rendered.html.count("</font>")
    assert rendered.stats["hc0146_color_font_bold_nesting_repairs"] == 2


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

    assert (
        '<div class="column_frame exam_frame"><span class="exam_text">'
        '<span class="small_font">¶</span>例</span><span class="exam_translate">訳</span></div>'
    ) in rendered.html
    assert '<span class="idiom_text_color">成句</span>' in rendered.html
    assert "hc0146_unmapped_section_branch" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0146_section_exam_text"] == 1
    assert rendered.stats["hc0146_section_exam_translate"] == 1
    assert rendered.stats["hc0146_example_source_linebreaks_suppressed"] == 1
    assert rendered.stats["hc0146_example_frame_closures"] == 1
    assert rendered.stats["hc0146_section_idiom_text_color"] == 1


def test_hc0146_suppresses_raw_paragraph_marker_in_example_sources() -> None:
    body = (
        b"\x1f\x09\x02\x11"
        + b"\xb1\x67"
        + jis_text("氏名を問う")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x23"
        + jis_text("訳")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x12"
        + jis_text("賛否を問う")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x20"
        + jis_text("訳二")
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert rendered.html.count("¶") == 2
    assert (
        '<span class="small_font">¶</span>氏名を問う</span>'
        '<span class="exam_translate">訳</span></div>'
    ) in rendered.html
    assert (
        '<span class="small_font">¶</span>賛否を問う</span>'
        '<span class="exam_translate">訳二</span></div>'
    ) in rendered.html
    assert rendered.stats["hc0146_example_raw_paragraph_markers_suppressed"] == 1


def test_hc0146_keeps_point_column_body_inside_frame() -> None:
    body = (
        b"\x1f\x09\x00\x40"
        + jis_text("ポイント")
        + b"\x1f\x0a"
        + b"\x1f\x09\x04\x31"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x50"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<div class="column_frame cm_f_point">ポイント<br>本文<br></div>' in rendered.html
    assert rendered.stats["hc0146_section_point_column"] == 1
    assert rendered.stats["hc0146_column_frame_closures"] == 1


def test_hc0146_decoration_mode_one_is_italic_not_bold() -> None:
    body = (
        b"\x1f\xe0\x00\x01"
        + jis_ascii_text("et")
        + b"\x1f\xe1\x00\x00"
        + b"\x1f\xe0\x00\x00"
        + jis_ascii_text("A")
        + b"\x1f\xe1\x00\x00"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<i><span class="lv-hc-halfwidth">et</span></i>' in rendered.html
    assert '<b><span class="lv-hc-halfwidth">A</span></b>' in rendered.html
    assert '<b><span class="lv-hc-halfwidth">et</span></b>' not in rendered.html
    assert rendered.stats["hc0146_italic_decoration_spans"] == 1
    assert rendered.stats["hc0146_bold_decoration_spans"] == 1


def test_hc0146_maps_synonym_column_title_and_frame_state() -> None:
    body = (
        b"\x1f\x09\x00\x70"
        + b"\xb2\x32\xb2\x27\xb2\x33"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x71"
        + jis_text("類語")
        + b"\x1f\x0a"
        + jis_text("本文")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x80"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<div class="column_frame cm_f_synonym">' in rendered.html
    assert '<p class="column_title cm_t_synonym"><img src="b227.png" class="column_icon">類語</p>' in rendered.html
    assert "本文<br>" in rendered.html
    assert "hc0146_unmapped_section_branch" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0146_synonym_icon_selectors"] == 1
    assert rendered.stats["hc0146_section_synonym_column_title"] == 1
    assert rendered.stats["hc0146_column_frame_closures"] == 1


def test_hc0146_maps_relation_column_title_and_frame_state() -> None:
    body = (
        b"\x1f\x09\x01\x50"
        + b"\xb2\x32\xb2\x29\xb2\x33"
        + b"\x1f\x0a"
        + b"\x1f\x09\x01\x53"
        + jis_text("関連")
        + b"\x1f\x0a"
        + b"\x1f\x09\x01\x60"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert '<div class="column_frame cm_f_relation">' in rendered.html
    assert '<p class="column_title cm_t_relation"><img src="b229.png" class="column_icon"></p>' in rendered.html
    assert "関連<br>" in rendered.html
    assert "hc0146_unmapped_section_branch" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0146_section_relation_column_title"] == 1
    assert rendered.stats["hc0146_column_frame_closures"] == 1


def test_hc0146_maps_residual_state_and_indent_sections() -> None:
    body = (
        b"\x1f\x09\x02\x00"
        + jis_text("通常")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x92"
        + jis_text("流し")
        + b"\x1f\x0a"
        + b"\x1f\x09\x02\x30"
        + jis_text("追加")
        + b"\x1f\x0a"
        + b"\x1f\x09\x03\x40"
        + jis_text("補足")
        + b"\x1f\x0a"
        + b"\x1f\x09\x03\x51"
        + jis_text("字下げ")
        + b"\x1f\x0a"
        + b"\x1f\x09\x01\x40"
        + b"\x1f\x0a"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0146"))

    assert "通常<br>" in rendered.html
    assert "流し<br>" in rendered.html
    assert "追加<br>" in rendered.html
    assert "補足<br>" in rendered.html
    assert '<span class="indent_minus">字下げ</span>' in rendered.html
    assert "hc0146_unmapped_section_branch" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0146_state_sections"] == 5
    assert rendered.stats["hc0146_section_indent_minus"] == 1


def test_hc0146_consumes_renderer_state_close_control() -> None:
    rendered = render_hc_body(
        jis_text("前") + b"\x1f\x6d" + b"\x1f\x0a" + jis_text("後"),
        HcRenderOptions(renderer_code="0146"),
    )

    assert "前<br>後" in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0146_nonprinting_controls"] == 1


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

    image_sources, html_templates, stylesheet, copied, _ = _prepare_hc_render_assets(source, tmp_path / "out", renderer)

    assert image_sources["b157"] == "Templates/b157_M.png"
    assert image_sources["b157_m"] == "Templates/b157_M.png"
    assert image_sources["b157_m.png"] == "Templates/b157_M.png"
    assert "00000146.css" not in image_sources
    assert html_templates == {}
    assert stylesheet == "hc-renderer.css"
    assert (tmp_path / "out" / "Templates" / "b157_M.png").read_bytes() == b"PNGDATA"
    assert (tmp_path / "out" / "Templates" / "00000146.css").is_file()
    css = (tmp_path / "out" / "hc-renderer.css").read_text(encoding="utf-8")
    assert ".lv-hc-render" in css
    assert ":where(.lv-hc-render .midashi)" in css
    assert ".lv-hc-render .midashi {" not in css
    assert ":where(.lv-hc-render img)" in css
    assert "$midashi-font-size$" not in css
    assert "1.35em" in css
    assert "#111111" in css
    assert copied == 3


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


def test_hc_custom_dib_export_is_capability_not_renderer_gap() -> None:
    row = HcRendererClassification(
        path=Path("HC013A.dll"),
        code="013A",
        expected_numeric_index="0000013A.idx",
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
        features={"custom_dib_export": True, "bridge_bitmap_import": True},
    )

    gaps = _renderer_behavior_gaps(row)
    data = build_hc_behavior_profile(row).as_dict()
    hook_names = {hook["name"] for hook in data["hook_behaviors"]}

    assert "custom_gaiji_dib_hook" not in gaps
    assert "custom_dib_export" not in data["named_gaps"]
    assert "bridge_bitmap_import" not in data["named_gaps"]
    assert {"custom_dib_export", "bridge_bitmap_import"} <= hook_names


def test_hc_renderer_effects_split_dib_export_from_bridge_bitmap_import() -> None:
    row = HcRendererClassification(
        path=Path("HC0001.dll"),
        code="0001",
        expected_numeric_index="00000001.idx",
        size=1,
        sha256=None,
        pe=PeSummary(
            kind="pe",
            exports=("epwing2HtmlBodydata", "getCustomCharacterDIB"),
            imports=(PeImportDll(DICTIONARY_BRIDGE_DLL, (PeImport("SDicGetCustomCharacterBitmap"),)),),
        ),
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
        features={"custom_dib_export": True, "bridge_bitmap_import": True},
    )

    effects = hc_renderer_effects(row)

    assert effects["custom_dib_strategy"]["export"] == "getCustomCharacterDIB"
    assert effects["custom_dib_strategy"]["bridge_bitmap_import"] == "SDicGetCustomCharacterBitmap"
    assert "visible custom-DIB impact" in effects["custom_dib_strategy"]["parity_note"]


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
    assert "HC02C2_private_ruby_directives" in data["implemented_semantics"]
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


def test_hc004d_profile_records_bmanner_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC004D.dll"),
        code="004D",
        expected_numeric_index="0000004D.idx",
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
        html_templates=("Templates/0000004d.css",),
        sql_snippets=(),
        image_templates=("sound.png", "image.png", "movie.png"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC004D_midashi_honbun_renderer" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


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


def test_hc00aa_maps_hkbyoin_sections_links_and_close_state() -> None:
    media_payload = bytes.fromhex("000000000000000000000000000001230045")
    body = (
        b"\x1f\x09\x00\x01"
        + jis_text("見出し")
        + b"\x1f\x09\x01\x01"
        + jis_text("囲み")
        + b"\x1f\x09\x01\x02"
        + jis_text("看護")
        + b"\x1f\x09\x01\x14"
        + jis_text("手順")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x48"
        + b"\x1f\x4d"
        + media_payload
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00AA",
            image_sources={"nurse": "Templates/Nurse.png", "tejyun": "Templates/tejyun.png"},
        ),
    )

    assert '<div class="midashi">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "<table" in rendered.html
    assert 'src="Templates/Nurse.png"' in rendered.html
    assert 'src="Templates/tejyun.png"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00aa_section_midashi"] == 1
    assert rendered.stats["hc00aa_section_boxed_table"] == 1
    assert rendered.stats["hc00aa_section_nurse_box"] == 1
    assert rendered.stats["hc00aa_section_tejyun"] == 1
    assert rendered.stats["hc00aa_nonprinting_controls"] == 1
    assert rendered.media[0]["target"]["resource_id"] == "colscr:00000123:0045"


def test_hc00aa_entry_slices_use_section_headings_for_monolithic_honmon() -> None:
    data = (
        b"\x00\x00"
        + b"\x1f\x09\x00\x01"
        + jis_text("Root")
        + b"\x1f\x09\x00\x66"
        + jis_text("Menu")
        + b"\x1f\x09\x01\x16"
        + jis_text("Entry")
        + b"\x1f\x09\x01\x14"
        + jis_text("Body")
        + b"\x1f\x09\x01\x16"
        + jis_text("Next")
    )

    slices = _hc00aa_entry_slices(data)

    assert len(slices) == 4
    assert data[slices[1][0] : slices[1][0] + 4] == b"\x1f\x09\x00\x66"
    assert data[slices[2][0] : slices[2][0] + 4] == b"\x1f\x09\x01\x16"
    assert b"Body" not in data[slices[3][0] : slices[3][1]]


def test_hc00aa_profile_records_hkbyoin_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00AA.dll"),
        code="00AA",
        expected_numeric_index="000000AA.idx",
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
        html_templates=("Templates/000000aa.css",),
        sql_snippets=(),
        image_templates=("Nurse.png", "tejyun.png"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00AA_hkbyoin_section_media_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00a3_maps_viku_quiz_answer_sections_and_state_controls() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x09\x00\x08"
        + b"\x1f\x09\x00\x03"
        + jis_text("問題")
        + b"\x1f\x09\x00\x08"
        + b"\x1f\x09\x00\x04"
        + jis_text("解答")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x48"
        + b"\x1f\x41"
        + b"\x1f\x4c"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00A3"))

    assert '<div class="honbun"' in rendered.html
    assert '<div class="quiz" id="quiz1" style="display:none;">' in rendered.html
    assert '<div class="answer" id="ans1" style="display:none;">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00a3_section_honbun_nobr"] == 1
    assert rendered.stats["hc00a3_section_quiz"] == 1
    assert rendered.stats["hc00a3_section_answer"] == 1
    assert rendered.stats["hc00a3_nonprinting_controls"] == 2


def test_hc00a3_aliases_b261_to_b167_gaiji_image() -> None:
    rendered = render_hc_body(
        bytes.fromhex("b261"),
        HcRenderOptions(renderer_code="00A3", image_sources={"b167": "gaiji/b167.bmp"}),
    )

    assert 'data-gaiji-code="b167"' in rendered.html
    assert 'src="gaiji/b167.bmp"' in rendered.html
    assert 'data-gaiji-code="b261"' not in rendered.html
    assert "lv-hc-gaiji-placeholder" not in rendered.html
    assert rendered.stats["hc00a3_b261_alias_markers"] == 1


def test_hc00a3_profile_records_viku_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00A3.dll"),
        code="00A3",
        expected_numeric_index="000000A3.idx",
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
        html_templates=("Templates/000000A3.css", "Templates/000000A3.js"),
        sql_snippets=(),
        image_templates=("answerH.gif", "kaisetsuV.gif"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00A3_quiz_answer_section_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00c5_maps_section_images_midashi_and_honbun_user() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x08"
        + jis_text("意味")
        + b"\x1f\x09\x00\x03"
        + jis_text("本文")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x48"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(renderer_code="00C5", image_sources={"imi": "Templates/imi.png"}),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun_user">' in rendered.html
    assert '<img src="Templates/imi.png" class="img_mark">' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert rendered.stats["hc00c5_midashi_blocks"] == 1
    assert rendered.stats["hc00c5_honbun_user_blocks"] == 1
    assert rendered.stats["hc00c5_section_imi"] == 1
    assert rendered.stats["hc00c5_section_honbun"] == 1


def test_hc00c5_profile_records_gkkanyok_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00C5.dll"),
        code="00C5",
        expected_numeric_index="000000C5.idx",
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
        html_templates=("Templates/000000C5.css",),
        sql_snippets=(),
        image_templates=("imi.png", "yourei_V.png"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00C5_section_image_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00ad_maps_large_character_sections_and_state_close() -> None:
    media_payload = bytes.fromhex("000000000000000000000000000001230045")

    def resolve_media(_pointer: MediaPointer) -> str:
        return "media/colscr/00000123_0045.bmp"

    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41"
        + jis_text("親")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x11"
        + jis_text("大")
        + b"\x1f\x09\x00\x12"
        + jis_text("説明")
        + b"\x1f\x09\x00\x17"
        + jis_text("コード")
        + b"\x1f\x09\x00\x40"
        + jis_text("《常用音訓》")
        + b"\x1f\x12"
        + jis_text("リ")
        + b"\x1f\x13"
        + b"\x1f\x09\x00\x41"
        + jis_text("《音読み》５ＢＤ８")
        + b"\x1f\x09\x00\x44"
        + jis_text("［意味］")
        + b"\x1f\x12"
        + b"\xb2\x71"
        + jis_text("語")
        + b"\xb2\x72"
        + jis_text("姓")
        + b"\x1f\x13"
        + b"\x1f\x09\x00\x46"
        + jis_text("［解字］")
        + b"\x1f\x4d"
        + media_payload
        + jis_text("会意")
        + b"\x1f\x09\x00\x72"
        + jis_text("本文")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x48"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="00AD",
            image_sources={
                "dummy": "Templates/dummy.GIF",
                "jyouyou": "Templates/jyouyou.png",
                "imi": "Templates/imi.png",
                "onyomi": "Templates/onyomi.png",
                "kaiji": "Templates/kaiji.png",
            },
            gaiji_map={"b271": "❶", "b272": "❷"},
            media_image_resolver=resolve_media,
        ),
    )

    assert '<div class="midashi">' in rendered.html
    assert '<div class="hc00ad-kanji-summary"><div class="hc00ad-kanji-main">' in rendered.html
    assert '<div class="hc00ad-kanji-meta">' in rendered.html
    assert "font-size:6em" in rendered.html
    assert '<div class="honbun hc00ad-label-section" style="margin-left:0.250000em;text-indent:0.000000em;">' in rendered.html
    assert "<hr" in rendered.html
    assert rendered.html.count("<hr") == 1
    assert "Templates/jyouyou.png" in rendered.html
    assert "Templates/onyomi.png" in rendered.html
    assert "Templates/imi.png" in rendered.html
    assert "Templates/kaiji.png" in rendered.html
    assert "《常用音訓》" not in rendered.html
    assert "《音読み》" not in rendered.html
    assert "［意味］" not in rendered.html
    assert "［解字］" not in rendered.html
    assert "５ＢＤ８" not in rendered.html
    assert "寘" in rendered.html
    assert "<strong>リ</strong>" in rendered.html
    assert "<strong>❶語<br>❷姓</strong>" in rendered.html
    assert "<em>リ</em>" not in rendered.html
    assert "<em>語</em>" not in rendered.html
    assert '<img src="Templates/kaiji.png" class="img_mark"><span class="hc00ad-media-block"><img class="lv-hc-media"' in rendered.html
    assert '</span><br>会意' in rendered.html
    assert 'src="media/colscr/00000123_0045.bmp"' in rendered.html
    assert "00000123_0045.bmp" in rendered.html
    assert "00000123_0045.bmp" in rendered.html[: rendered.html.index("会意")]
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00ad_midashi_blocks"] == 1
    assert rendered.stats["hc00ad_kanji_summary_blocks"] == 1
    assert rendered.stats["hc00ad_section_large_character"] == 1
    assert rendered.stats["hc00ad_section_paired_explanation"] == 2
    assert rendered.stats["hc00ad_section_label_images"] == 4
    assert rendered.stats["hc00ad_suppressed_label_chars"] == len("《常用音訓》") + len("《音読み》") + len("［意味］") + len("［解字］")
    assert rendered.stats["hc00ad_section_hr_body"] == 1
    assert rendered.stats["hc00ad_nonprinting_controls"] == 1
    assert rendered.stats["hc00ad_codepoint_literals_decoded"] == 1
    assert rendered.stats["hc00ad_media_blocks"] == 1
    assert rendered.stats["hc00ad_sense_number_linebreaks"] == 1


def test_hc00ad_profile_records_kanjigen_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00AD.dll"),
        code="00AD",
        expected_numeric_index="000000AD.idx",
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
        html_templates=("Templates/000000AD.css",),
        sql_snippets=(),
        image_templates=("imi.png", "v_imi.png"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00AD_large_character_section_layout" in data["implemented_semantics"]
    assert "HC00AD_summary_label_images_and_bold_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00bb_maps_gen2000_honbun_sections_and_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41"
        + jis_text("見出し")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x05"
        + jis_ascii_text("ABC")
        + b"\x1f\x09\x00\x0c"
        + jis_text("参照")
        + jis_text("＊")
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00BB"))

    assert '<div class="midashi">' in rendered.html
    assert '<div class="honbun" style="margin-left:4px">' in rendered.html
    assert '<div class="honbun" style="margin-left:20px">' in rendered.html
    assert '<div class="footer">' in rendered.html
    assert '<span class="hankaku">' in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00bb_midashi_blocks"] == 1
    assert rendered.stats["hc00bb_section_honbun"] == 2
    assert rendered.stats["hc00bb_section_footer"] == 1
    assert rendered.stats["hc00bb_hankaku_spans"] == 1
    assert rendered.stats["hc00bb_nonprinting_controls"] == 1


def test_hc00bb_profile_records_gen2000_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00BB.dll"),
        code="00BB",
        expected_numeric_index="000000BB.idx",
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
        html_templates=("Templates/00000134.css",),
        sql_snippets=(),
        image_templates=("mlink.gif", "forward.gif"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00BB_honbun_section_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00ab_maps_gkyojijk_honbun_sections_and_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_text("親")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x04"
        + jis_ascii_text("AB")
        + jis_text("本文")
        + b"\x1f\x09\x00\x0c"
        + jis_text("参照")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x48"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00AB"))

    assert '<div class="midashi">親</div>' in rendered.html
    assert '<div class="honbun" style="margin-left:6.000000em;text-indent:-6.000000em;">' in rendered.html
    assert '<div class="honbun" style="margin-left:3.000000em;">' in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc00ab_midashi_blocks"] == 1
    assert rendered.stats["hc00ab_section_hanging_honbun"] == 1
    assert rendered.stats["hc00ab_section_honbun_special"] == 1
    assert rendered.stats["hc00ab_hankaku_spans"] == 2
    assert rendered.stats["hc00ab_nonprinting_controls"] == 1


def test_hc00ab_maps_yourei_ruby_and_red_emphasis() -> None:
    ruby_start = b"\x1f\xe2\x00\x07" + jis_fullwidth_ascii("RUB:E") + b"\x1f\xe3"
    ruby_end = b"\x1f\xe2\x00\x09" + jis_fullwidth_ascii("RUB:S") + jis_text("あ") + b"\x1f\xe3"
    body = (
        b"\x1f\x09\x00\x11"
        + jis_text("［用例］天に")
        + ruby_start
        + jis_text("在")
        + ruby_end
        + jis_text("っては")
        + b"\x1f\xe0\x00\x04"
        + jis_text("比翼")
        + b"\x1f\xe1"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="00AB"))

    assert '<div class="honbun" style="margin-left:6.000000em;text-indent:-6.000000em;">' in rendered.html
    assert "margin-left:17.000000em" not in rendered.html
    assert '<ruby class="ruby7"><rb class="rb7">在</rb><rp class="rp7">(</rp><rt class="rt7">あ</rt>' in rendered.html
    assert '<span class="hc00ab-red-emphasis">比翼</span>' in rendered.html
    assert rendered.stats["hc00ab_section_hanging_honbun"] == 1
    assert rendered.stats["hc00ab_ruby_starts"] == 1
    assert rendered.stats["hc00ab_ruby_ends"] == 1


def test_hc00ab_profile_records_gkyojijk_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00AB.dll"),
        code="00AB",
        expected_numeric_index="000000AB.idx",
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
        html_templates=("Templates/000000AB.css",),
        sql_snippets=(),
        image_templates=("b138.gif", "b13a.gif", "lineLink.gif"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC00AB_honbun_section_layout" in data["implemented_semantics"]
    assert "HC00AB_ruby_red_emphasis_and_yourei_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"panel_hooks": True, "headword_modifier": True, "sql_hooks": True, "custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0137_iwanami_section_margin_and_line_links" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "panel_lifecycle" in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"headword_modifier": True, "sql_hooks": True, "custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C8_zukaiho_section_table_and_indent_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC008C_medical_section_layout" in data["implemented_semantics"]
    assert "HC008C_conditional_link_classes" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0068_midashi_contents_and_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0069_midashi_contents_and_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
    assert "HC008B_unterminated_line_link_plaintext_recovery" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc00de_profile_records_britannica_state_marker_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC00DE.dll"),
        code="00DE",
        expected_numeric_index="000000DE.idx",
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
        html_templates=("Media/HTMLs/%d-%d.html",),
        sql_snippets=(),
        image_templates=(),
        features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC_BRITANNICA_state_gaiji_marker_suppression" in data["implemented_semantics"]
    assert "HC_BRITANNICA_midashi_state_sections_and_line_links" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "modify_headword_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]
    assert any(hook["name"] == "britannica_panel_media_html" for hook in data["hook_behaviors"])


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
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["block_offset_htmls_template"]["status"] == "classified_inactive_current_corpus"
    assert "block_offset_htmls_template" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


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
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC009D_section_and_kakomi_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]


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
        features={"custom_dib_export": True, "sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0145_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0144_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "sql_hooks": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC03E8_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "sql_hooks": True, "headword_modifier": True, "dictionary_original_search": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0141_section_and_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC009C_section_image_index_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]


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
        features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C5_section_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0151_section_table_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0142_panel_body_marker_layout" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C1_section_icons_and_template_gaiji" in data["implemented_semantics"]
    assert "HC02C1_private_ruby_bold_and_unused_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True, "sql_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02BF_section_icon_and_moji_down_layout" in data["implemented_semantics"]
    assert "HC02BF_decoration_modes" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC012F_bunnya_section_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "sql_hooks": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0131_kqebhou_section_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
            features={"custom_dib_export": True, "headword_modifier": True, "panel_hooks": True, "sql_hooks": True, "vertical_renderer": True},
        )

        data = build_hc_behavior_profile(row).as_dict()

        assert "HC_HKDKSR_medical_section_layout" in data["implemented_semantics"]
        assert data["exact_hc_parity"] is False
        assert "custom_dib_export" not in data["named_gaps"]
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
            features={"custom_dib_export": True, "headword_modifier": True, "vertical_renderer": True},
        )

        data = build_hc_behavior_profile(row).as_dict()

        assert "HC_GEN_YEAR_section_icons_and_template_markers" in data["implemented_semantics"]
        assert data["exact_hc_parity"] is False
        assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02CA_honbun_margin_sections" in data["implemented_semantics"]
    assert "HC02CA_private_state_and_bitmap_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0063_contents_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0093_lineinfo_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "private_ruby_directive_hook" in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc0095_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0095.dll"),
        code="0095",
        expected_numeric_index="00000095.idx",
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
        html_templates=("Templates/00000095.css",),
        sql_snippets=(),
        image_templates=("Templates/B128.png", "Templates/B12F.png"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0095_lineinfo_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0096_lineinfo_sections_and_template_gaiji" in data["implemented_semantics"]
    assert "HC0096_private_ruby_directives" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "private_ruby_directive_hook" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


def test_hc009f_profile_records_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC009F.dll"),
        code="009F",
        expected_numeric_index="0000009F.idx",
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
        html_templates=("Templates/0000009F.css",),
        sql_snippets=(),
        image_templates=("Templates/Hsp.png", "Templates/Hsp1.png", "Templates/b121H.gif"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC009F_season_category_sections_and_template_gaiji" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "plugin_hooks": True, "sql_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC014F_midashi_contents_and_decoration_modes" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "plugin_hooks": True, "sql_hooks": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0135_sinmei_sections_and_private_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
    assert "HC00AC_ruby_red_emphasis_and_yourei_layout" in data["implemented_semantics"]
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
        features={"custom_dib_export": True, "headword_modifier": True, "vertical_renderer": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC02C0_honbun_margin_sections" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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
    assert "HC00A0_playable_mp3_audio_links" in data["implemented_semantics"]
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
            "custom_dib_export": True,
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


def test_hc015c_profile_records_t_contents_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC015C.dll"),
        code="015C",
        expected_numeric_index="0000015C.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("t_contents", "f_DataId", "f_Html"),
        html_templates=("Templates/0000015C.css",),
        sql_snippets=("SELECT f_Html FROM t_contents WHERE f_DataId = ?",),
        image_templates=(),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "sql_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={"status": "ok", "t_contents_rows": 20, "entries_matched_to_raw_honmon": 20},
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC015x_t_contents_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["t_contents_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc015d_profile_records_t_contents_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC015D.dll"),
        code="015D",
        expected_numeric_index="0000015D.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblb",),
        dic_tokens=(),
        vlpljbl_tokens=("t_contents", "f_DataId", "f_Html"),
        html_templates=("Templates/0000015D.css",),
        sql_snippets=("SELECT f_Html FROM t_contents WHERE f_DataId = ?",),
        image_templates=(),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "sql_hooks": True,
            "plugin_hooks": True,
            "user_data_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={"status": "ok", "t_contents_rows": 20, "entries_matched_to_raw_honmon": 20},
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC015x_t_contents_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["t_contents_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "plugin_function_hook" in data["named_gaps"]


def test_hc0160_profile_records_unanchored_t_contents_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0160.dll"),
        code="0160",
        expected_numeric_index="00000160.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("t_contents", "f_data_id", "f_contents"),
        html_templates=("Templates/00000160.css", "select0.html", "select1.html"),
        sql_snippets=("SELECT f_contents FROM t_contents WHERE f_data_id = ?",),
        image_templates=("icon_bm.png", "icon_bm_on.png"),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "plugin_hooks": True,
            "sql_hooks": True,
            "user_data_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={
            "status": "ok_t_contents_unanchored_body",
            "t_contents_rows": 20,
            "entries_matched_to_raw_honmon": 0,
            "unanchored_t_contents_rows": 20,
        },
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC0160_unanchored_t_contents_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["unanchored_t_contents_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "plugin_function_hook" in data["named_gaps"]


def test_hc0c80_profile_records_array_t_contents_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0C80.dll"),
        code="0C80",
        expected_numeric_index="00000C80.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("t_contents", "f_array_no", "f_contents", "t_media"),
        html_templates=("Templates/00000C80.css",),
        sql_snippets=("SELECT f_contents FROM t_contents WHERE f_array_no = ?",),
        image_templates=(),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "headword_modifier": True,
            "plugin_hooks": True,
            "sql_hooks": True,
            "user_data_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={"status": "ok", "t_contents_rows": 20, "entries_matched_to_raw_honmon": 20},
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC0C80_array_no_t_contents_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["array_no_t_contents_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "plugin_function_hook" in data["named_gaps"]


def test_hc0156_profile_records_main_id_text_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0156.dll"),
        code="0156",
        expected_numeric_index="00000156.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("KJJK100",),
        dic_tokens=(),
        vlpljbl_tokens=("main", "K_text", "J_text"),
        html_templates=("Templates/00000156.css",),
        sql_snippets=("main(ID, Class, K_text, J_text)",),
        image_templates=(),
        features={"custom_dib_export": True, "headword_modifier": True, "sql_hooks": True},
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={
            "status": "ok_main_id_text_body",
            "entries_matched_to_raw_honmon": 20,
            "entries_missing_raw_honmon_id": 0,
        },
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC0156_main_id_text_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["hc0156_main_id_text_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "modify_headword_hook" in data["named_gaps"]


def test_hc016x_profile_records_order_id_contents_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0161.dll"),
        code="0161",
        expected_numeric_index="00000161.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblb",),
        dic_tokens=(),
        vlpljbl_tokens=("t_contents", "f_order_id", "f_contents"),
        html_templates=("Templates/00000161.css",),
        sql_snippets=("SELECT f_contents FROM t_contents WHERE f_order_id = ?",),
        image_templates=(),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "sql_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={
            "status": "ok",
            "t_contents_rows": 20,
            "entries_matched_to_raw_honmon": 20,
            "entries_missing_raw_honmon_id": 0,
        },
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC016x_order_id_t_contents_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["order_id_t_contents_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "dictionary_original_search_hook" in data["named_gaps"]
    assert "fulltext_search_hook" in data["named_gaps"]


def test_hc0153_profile_records_honbun_array_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0153.dll"),
        code="0153",
        expected_numeric_index="00000153.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("HONBUN", "ANCHOR", "f_array_no", "f_data_id"),
        html_templates=("Templates/00000153.css",),
        sql_snippets=("SELECT f_contents FROM HONBUN WHERE f_array_no = ? ;",),
        image_templates=("b16c.png",),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "sql_hooks": True,
            "user_data_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={
            "status": "ok_honbun_array_body",
            "honbun_rows": 2,
            "entries_matched_to_raw_honmon": 2,
        },
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert data["body_strategy_status"] == "exact_entry_body_html"
    assert "HC0153_honbun_array_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["honbun_array_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False
    assert "user_data_hook" in data["named_gaps"]


def test_hc014d_profile_records_ordered_honbun_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC014D.dll"),
        code="014D",
        expected_numeric_index="0000014D.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblF",),
        dic_tokens=(),
        vlpljbl_tokens=("HONBUN", "Contents_HTML_box"),
        html_templates=("Templates/0000014D.css",),
        sql_snippets=("SELECT Contents_HTML_box FROM HONBUN",),
        image_templates=(),
        features={
            "custom_dib_export": True,
            "headword_modifier": True,
            "sql_hooks": True,
            "plugin_hooks": True,
            "user_data_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={
            "status": "ok_honbun_ordered",
            "honbun_rows": 20,
            "entries_matched_to_raw_honmon": 20,
        },
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert "HC014D_honbun_ordered_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["honbun_ordered_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False


def test_hc02d0_profile_records_t_contents_body_without_claiming_hook_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC02D0.dll"),
        code="02D0",
        expected_numeric_index="000002D0.idx",
        size=1,
        sha256=None,
        pe=PeSummary(kind="unknown"),
        exinfo_html_dll=None,
        exinfo_declares_this=None,
        numeric_indexes=(),
        expected_numeric_index_present=False,
        vlpljbl_siblings=("vlpljblh",),
        dic_tokens=(),
        vlpljbl_tokens=("t_contents", "f_DataId", "f_Html"),
        html_templates=("Templates/000002D0.css",),
        sql_snippets=("SELECT f_Html FROM t_contents WHERE f_DataId = ?",),
        image_templates=(),
        features={
            "custom_dib_export": True,
            "dictionary_original_search": True,
            "fulltext_search": True,
            "headword_modifier": True,
            "sql_hooks": True,
            "plugin_hooks": True,
            "user_data_hooks": True,
        },
    )

    data = build_hc_behavior_profile(
        row,
        rendererdb_summary={"status": "ok", "t_contents_rows": 20, "entries_matched_to_raw_honmon": 20},
    ).as_dict()

    assert data["body_strategy"] == "rendererdb_html"
    assert "HC02D0_t_contents_exact_body_html" in data["implemented_semantics"]
    hooks = {row["name"]: row for row in data["hook_behaviors"]}
    assert hooks["hc02d0_t_contents_body_lookup"]["status"] == "implemented_when_sidecar_present"
    assert data["exact_hc_parity"] is False


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
            "custom_dib_export": True,
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
    assert "custom_dib_export" not in data["named_gaps"]
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
        features={"custom_dib_export": True, "headword_modifier": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC013D_hkdksr13_drug_layout_and_template_markers" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
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


def test_hc_render_exact_rendererdb_html_becomes_visual_entry_html(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    (dict_out / "Templates").mkdir(parents=True)
    (dict_out / "Templates" / "sound.png").write_bytes(b"png")
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"DICT","data_id":5,"html":"<div class=\\"midashi\\">A</div><a href=\\"lved.ziptomedia:000010.wav\\"><img src=\\"sound.png\\"></a>","plain":"A"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet="hc-renderer.css",
        vertical=False,
    )

    assert html_path == dict_out / "hc_entries.html"
    rendered = html_path.read_text(encoding="utf-8")
    assert 'href="hc-renderer.css"' in rendered
    assert '<div class="midashi">A</div>' in rendered
    assert 'src="Templates/sound.png"' in rendered
    assert 'href="#lv-ziptomedia-000010.wav"' in rendered
    assert 'data-lv-original-href="lved.ziptomedia:000010.wav"' in rendered
    assert 'id="lv-dataid-5" data-lv-dataid="5"' in rendered


def test_hc_render_exact_rendererdb_html_rewrites_extracted_renderer_links(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    (dict_out / "rendererdb" / "DICT" / "ziptomedia").mkdir(parents=True)
    audio = dict_out / "rendererdb" / "DICT" / "ziptomedia" / "000010.wav"
    audio.write_bytes(b"RIFFxxxxWAVE")
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        "\n".join(
            [
                '{"dict_id":"DICT","data_id":"000005","html":"<a href=\\"lved.ziptomedia:000010.wav\\"><img src=\\"sound.png\\"></a><a href=\\"lved.dataid:000006#000006_00A0100\\">next</a>","plain":"A"}',
                '{"dict_id":"DICT","data_id":6,"html":"<div>target</div><a href=\\"lved.bookmark:C001_00000000\\">bookmark</a>","plain":"target"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {
            "entries_path": str(entries),
            "ziptomedia_written_files": [{"reference": "000010.wav", "path": str(audio)}],
        },
        dict_out,
        stylesheet=None,
        vertical=False,
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'href="rendererdb/DICT/ziptomedia/000010.wav"' in rendered
    assert 'data-lv-original-href="lved.ziptomedia:000010.wav"' in rendered
    assert 'data-lv-ziptomedia="000010.wav"' in rendered
    assert 'href="#000006_00A0100"' in rendered
    assert 'data-lv-original-href="lved.dataid:000006#000006_00A0100"' in rendered
    assert 'data-lv-dataid="6"' in rendered
    assert 'data-lv-target-anchor="000006_00A0100"' in rendered
    assert 'data-lv-missing-target' not in rendered
    assert 'href="#lv-bookmark-C001_00000000"' in rendered
    assert 'data-lv-original-href="lved.bookmark:C001_00000000"' in rendered
    assert 'data-lv-bookmark="C001_00000000"' in rendered
    assert re.search(r'(?<![\w-])href="lved\.', rendered) is None


def test_hc_render_exact_rendererdb_html_rewrites_string_dataid_links(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.parent.mkdir()
    entries.write_text(
        "\n".join(
            [
                '{"dict_id":"DICT","data_id":"Kalpha","html":"<div>alpha</div>","plain":"alpha"}',
                '{"dict_id":"DICT","data_id":"Kbeta","html":"<a href=\\"lved.dataid:Kalpha\\">alpha</a>","plain":"beta"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'id="lv-dataid-Kalpha" data-lv-dataid="Kalpha"' in rendered
    assert 'href="#lv-dataid-Kalpha"' in rendered
    assert 'data-lv-original-href="lved.dataid:Kalpha"' in rendered
    assert 'data-lv-dataid="Kalpha"' in rendered
    assert 'data-lv-missing-target' not in rendered


def test_hc_render_exact_rendererdb_html_rewrites_internal_addr_and_image_links(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    (dict_out / "media").mkdir(parents=True)
    (dict_out / "media" / "0000006801.jpg").write_bytes(b"\xff\xd8\xff")
    (dict_out / "rendererdb" / "DICT" / "media").mkdir(parents=True)
    (dict_out / "rendererdb" / "DICT" / "media" / "50029810.jpg").write_bytes(b"\xff\xd8\xff")
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"DICT","data_id":5,'
        '"html":"<a href=\\"lved.addr00000109:0226\\">jump</a>'
        '<a href=\\"lved.image:0000006801.jpg\\"><img src=\\"0000006801.jpg\\"></a>'
        '<img class=\\"FIGc\\" src=\\"50029810.jpg\\">",'
        '"plain":"A"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert 'href="lvaddr://00000265/0550"' in rendered
    assert 'data-lv-original-href="lved.addr00000109:0226"' in rendered
    assert 'data-lv-address-block="00000109"' in rendered
    assert 'data-lv-address-offset="0226"' in rendered
    assert 'href="media/0000006801.jpg"' in rendered
    assert 'src="rendererdb/DICT/media/50029810.jpg"' in rendered
    assert 'data-lv-original-href="lved.image:0000006801.jpg"' in rendered
    assert re.search(r'(?<![\w-])href="lved\.', rendered) is None


def test_hc_render_rewrites_embedded_fixed_html_lved_addr_links() -> None:
    rendered = _rewrite_lved_addr_hrefs('<a href="lved.addr00000133:0208">(jump)</a>')

    assert 'href="lvaddr://00000307/0520"' in rendered
    assert 'data-lv-original-href="lved.addr00000133:0208"' in rendered
    assert re.search(r'(?<![\w-])href="lved\.', rendered) is None


def test_hc_render_exact_rendererdb_html_closes_self_closing_anchors(tmp_path: Path) -> None:
    dict_out = tmp_path / "DICT"
    dict_out.mkdir(parents=True)
    entries = dict_out / "rendererdb_entries.jsonl"
    entries.write_text(
        '{"dict_id":"DICT","data_id":5,'
        '"html":"<a name=\\"Idx1000\\" /><a href=\\"lved.dataid:000005\\">self</a>",'
        '"plain":"A"}\n',
        encoding="utf-8",
    )

    html_path = _write_exact_hc_entries_html_from_rendererdb(
        {"entries_path": str(entries)},
        dict_out,
        stylesheet=None,
        vertical=False,
    )

    rendered = html_path.read_text(encoding="utf-8")
    assert '<a name="Idx1000"></a>' in rendered
    assert '<a name="Idx1000" />' not in rendered
    assert rendered.count("<a ") == rendered.count("</a>")


def test_exact_rendererdb_fragment_normalizer_fixes_known_fragment_leaks() -> None:
    typo = '<span class="hin">*</sapn>'
    open_div = '<div class="midashi">A</div><div class="honbun"><div>body</div>'
    extra_close = '<div class="midashi">A</div></div>'

    assert _normalize_exact_body_fragment_html(typo) == '<span class="hin">*</span>'
    assert _normalize_exact_body_fragment_html(open_div).endswith("</div>")
    assert _normalize_exact_body_fragment_html(open_div).count("<div") == 3
    assert _normalize_exact_body_fragment_html(open_div).count("</div>") == 3
    assert _normalize_exact_body_fragment_html(extra_close) == '<div class="midashi">A</div>'


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


def test_hc005c_examples_use_exam_box_and_suppress_structural_markers() -> None:
    body = (
        b"\x1f\x09\x00\x03"
        + jis_text("¶")
        + jis_text("手や顔を洗う")
        + b"\x1f\x04"
        + jis_fullwidth_ascii(".")
        + b"\x1f\x05"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("wash")
        + b"\x1f\x05"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("/")
        + jis_fullwidth_ascii("AB")
        + b"\x1f\x05"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("‖")
        + jis_text("この色")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="005C",
            image_sources={
                "dummy": "Templates/dummy.GIF",
                "exam": "Templates/exam.gif",
                "bunrei": "Templates/bunrei.gif",
            },
        ),
    )

    assert rendered.html.count('<div class="contents">') == 1
    assert 'src="Templates/bunrei.gif" class="img_mark"><br><div class="contents">' in rendered.html
    assert "exam.gif" not in rendered.html
    assert '<div class="contents"><div class="hr_div2"></div>' not in rendered.html
    assert '<div class="hr_div2"></div>' in rendered.html
    assert "¶" not in rendered.html
    assert "‖" not in rendered.html
    assert '<span class="hankaku">/' not in rendered.html
    assert "手や顔を洗う" in rendered.html
    assert '手や顔を洗う<span class="hankaku">.</span><br><span class="hankaku">wash</span><br>' in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert rendered.stats["hc005c_example_blocks"] == 1
    assert rendered.stats["hc005c_example_bunrei_icons"] == 1
    assert rendered.stats["hc005c_example_translation_linebreaks"] == 1
    assert rendered.stats["hc005c_example_linebreaks_inside_box"] == 1
    assert rendered.stats["hc005c_example_linebreaks_suppressed"] == 1
    assert rendered.stats["hc005c_example_separators_in_box"] == 1


def test_hc005c_colon_example_keeps_following_text_visible() -> None:
    body = (
        b"\x1f\x09\x00\x03"
        + b"\x1f\x04"
        + jis_fullwidth_ascii(":AB")
        + b"\x1f\x05"
        + jis_text("彼女の言葉")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + jis_text("通常本文")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="005C",
            image_sources={"dummy": "Templates/dummy.GIF", "exam": "Templates/exam.gif"},
        ),
    )

    assert '<div class="contents">' in rendered.html
    assert 'src="Templates/exam.gif" class="img_mark"><br><div class="contents">' in rendered.html
    assert '<div class="contents"><img src="Templates/dummy.GIF" class="img_dummy"><img src="Templates/exam.gif"' not in rendered.html
    assert '<span class="hankaku">:AB' not in rendered.html
    assert '<span class="hankaku">AB</span>' in rendered.html
    assert "彼女の言葉" in rendered.html
    assert '<div style="margin: 9px">通常本文</div>' in rendered.html
    assert rendered.stats["hc005c_suppressed_example_marker_colon"] == 1


def test_hc005c_skips_heading_gaiji_uses_numeric_separators_and_italic_examples() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + bytes.fromhex("a133")
        + b"\x1f\xe0\x00\x00"
        + jis_fullwidth_ascii("doc")
        + b"\x1f\xe1"
        + b"\x1f\x61"
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe0\x00\x00"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("1")
        + b"\x1f\xe1"
        + b"\x1f\x05"
        + jis_text("語義")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\x04"
        + jis_fullwidth_ascii(":He was ")
        + b"\x1f\xe0\x00\x01"
        + jis_fullwidth_ascii("animated")
        + b"\x1f\xe1"
        + jis_fullwidth_ascii(" by her words.")
        + b"\x1f\x05"
        + jis_text("　")
        + b"\x1f\x04"
        + jis_text("　")
        + b"\x1f\x05"
        + jis_text("彼女の言葉で彼は元気づけられた。")
        + b"\x1f\x0a"
        + b"\x1f\x09\x00\x03"
        + b"\x1f\xe0\x00\x00"
        + b"\x1f\x04"
        + jis_fullwidth_ascii("2")
        + b"\x1f\xe1"
        + b"\x1f\x05"
        + jis_text("次義")
    )

    rendered = render_hc_body(
        body,
        HcRenderOptions(
            renderer_code="005C",
            image_sources={"dummy": "Templates/dummy.GIF", "exam": "Templates/exam.gif"},
        ),
    )

    assert '<div class="eMidashi">' in rendered.html
    assert '<div class="jMidashi">' not in rendered.html
    assert rendered.html.count('<div class="hr_div2"></div>') == 2
    assert '<img src="Templates/exam.gif" class="img_mark"><br><div class="contents">' in rendered.html
    assert "<i>animated</i>" in rendered.html
    assert "<b>animated</b>" not in rendered.html
    assert (
        '<span class="hankaku">Hewas<i>animated</i>byherwords.</span>'
        "<br>彼女の言葉で彼は元気づけられた。"
    ) in rendered.html
    assert "words.</span>　" not in rendered.html
    assert "words.</span><span" not in rendered.html
    assert rendered.stats["hc005c_definition_separators"] == 2
    assert rendered.stats["hc005c_decoration_bold"] == 3
    assert rendered.stats["hc005c_decoration_italic"] == 1
    assert rendered.stats["hc005c_example_translation_linebreaks"] == 1
    assert rendered.stats["hc005c_suppressed_example_translation_padding_chars"] == 1
    assert rendered.stats["hc005c_suppressed_example_translation_padding_spans"] == 1


def test_hc0092_maps_lineinfo_sections_and_hankaku_midashi() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + jis_ascii_text("AB")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + jis_ascii_text("CD")
        + b"\x1f\x42"
        + jis_ascii("L")
        + b"\x1f\x62\x00\x00\x00\x02\x00\x48"
        + b"\x1f\x6d"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0092"))

    assert '<div class="lineinfo1">' in rendered.html
    assert '<span class="hankakuMidashi">AB</span>' in rendered.html
    assert '<div class="lineinfo2">' in rendered.html
    assert '<span class="hankaku">CD</span>' in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "lv-hc-section" not in rendered.html
    assert "lv-hc-heading" not in rendered.html
    assert "unknown_control_1f6d" not in rendered.named_behavior_gaps
    assert rendered.stats["hc0092_lineinfo_sections"] == 2
    assert rendered.stats["hc0092_hankakuMidashi_spans"] == 1
    assert rendered.stats["hc0092_hankaku_spans"] == 2
    assert rendered.stats["hc0092_nonprinting_controls"] == 1


def test_hc0092_decoration_state_and_example_boxes() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x00\x00"
        + b"\x1f\x10"
        + b"\x1f\xe0\x11\x03"
        + jis_ascii_text("license")
        + b"\x1f\xe1"
        + b"\x1f\x11"
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x10"
        + jis_text("は")
        + b"\x1f\x11"
        + b"\x1f\x10"
        + jis_text("《英》")
        + b"\x1f\x11"
        + b"\x1f\x10"
        + b"\x1f\xe0\x11\x01"
        + jis_ascii_text("licence")
        + b"\x1f\xe1"
        + b"\x1f\x11"
        + b"\x1f\x09\x00\x02"
        + b"\x1f\x04"
        + jis_text("・")
        + b"\x1f\x05"
        + b"\x1f\x10"
        + jis_ascii_text("a software ")
        + b"\x1f\xe0\x11\x01"
        + jis_ascii_text("license")
        + b"\x1f\xe1"
        + b"\x1f\x11"
        + jis_text("例文")
        + b"\x1f\x09\x00\x02"
        + jis_text("・")
        + jis_ascii_text("second")
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0092"))

    assert '<b><span class="hankakuMidashi">license</span></b>' in rendered.html
    assert '<i><span class="hankaku">licence</span></i>' in rendered.html
    assert '<i>は</i>' not in rendered.html
    assert '<i>《英》</i>' not in rendered.html
    assert rendered.html.count('<div class="contents">') == 2
    assert '<div class="contents"><span class="hankaku">・</span>' in rendered.html
    assert '<span class="hankaku"><div class="contents">' not in rendered.html
    assert '<i><b><span class="hankakuMidashi">license</span></b></i>' not in rendered.html
    assert rendered.stats["hc0092_bold_spans"] == 1
    assert rendered.stats["hc0092_italic_spans"] == 2
    assert rendered.stats["hc0092_example_boxes"] == 2


def test_hc0092_profile_records_kcompej_subset_without_claiming_parity() -> None:
    row = HcRendererClassification(
        path=Path("HC0092.dll"),
        code="0092",
        expected_numeric_index="00000092.idx",
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
        html_templates=("Templates/00000092.css",),
        sql_snippets=(),
        image_templates=("b128.gif", "hosetu.png"),
        features={"custom_dib_export": True},
    )

    data = build_hc_behavior_profile(row).as_dict()

    assert "HC0092_lineinfo_sections_and_gaiji_classes" in data["implemented_semantics"]
    assert data["exact_hc_parity"] is False
    assert "custom_dib_export" not in data["named_gaps"]
    assert "visual_parity_unverified" in data["named_gaps"]


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


def test_hc0132_suppresses_midashi_search_tail_metadata() -> None:
    body = (
        b"\x1f\x09\x00\x01"
        + b"\x1f\x41\x01\x60"
        + jis_ascii_text("confidence")
        + jis_text("◆信頼§信用§信認")
        + b"\x1f\x61"
        + b"\x1f\x09\x00\x12"
        + jis_text("⇒")
        + b"\x1f\x42"
        + jis_ascii_text("consumer confidence index")
        + b"\x1f\x62\x00\x00\x00\x03\x00\x40"
    )

    rendered = render_hc_body(body, HcRenderOptions(renderer_code="0132"))

    assert '<div class="midashi"><span class="hankaku">confidence</span></div>' in rendered.html
    assert "◆" not in rendered.html
    assert "§" not in rendered.html
    assert "信頼§信用" not in rendered.html
    assert 'class="lv-hc-link lineLink"' in rendered.html
    assert "consumerconfidenceindex" in rendered.html
    assert rendered.stats["hc0132_suppressed_search_tail_markers"] == 1
    assert rendered.stats["hc0132_suppressed_search_tail_text"] == 8


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
