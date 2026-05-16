from pathlib import Path
import sqlite3

from logovista_tools.entries import DictionarySource
from logovista_tools.hcrender import HcRenderOptions, _renderer_behavior_gaps, _schema_backed_sidecars, render_hc_body
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
