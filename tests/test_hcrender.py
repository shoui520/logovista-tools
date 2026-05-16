from pathlib import Path
import argparse
import sqlite3

from logovista_tools.entries import DictionarySource
from logovista_tools.hcrender import (
    HcRenderOptions,
    _has_entry_body_sidecar,
    _renderer_behavior_gaps,
    _rendererdb_args,
    _schema_backed_sidecars,
    render_hc_body,
)
from logovista_tools.hcprofiles import build_hc_behavior_profile
from logovista_tools.windows import HcRendererClassification, PeSummary


def jis_ascii(letter: str) -> bytes:
    return b"\x1f\x04" + bytes((0x23, ord(letter))) + b"\x1f\x05"


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
    assert "schema_backed_exact_entry_html" in row["implemented_semantics"]
    assert "ziptomedia_reference_extraction" in row["implemented_semantics"]
    assert "royal_example_search_helpers" in row["named_gaps"]
