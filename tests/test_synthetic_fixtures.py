import json
import sqlite3
from pathlib import Path

from logovista_tools.colscr import parse_colscr_image_header
from logovista_tools.gaiji import parse_ga16_resource, parse_uni_resource
from logovista_tools.indexes import scan_index_component
from logovista_tools.menus import parse_menu_stream
from logovista_tools.panels import iter_panel_cells, iter_panel_data_references, parse_panel_bin
from logovista_tools.pcmdata import iter_pcm_references, parse_pcmdata_record
from logovista_tools.repacker import repack_dictionary_source
from logovista_tools.ssed import expand_sseddata_file, find_case_insensitive, parse_ssedinfo
from logovista_tools.entries import DictionarySource


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "synthetic"

EXPECTED_FIXTURES = {
    "catalog_basic",
    "catalog_inactive_missing_component",
    "sseddata_chunk_roundtrip",
    "case_insensitive_component_lookup",
    "basic_bidirectional_title_index",
    "standard_four_title_index",
    "kw_cr_title_index",
    "multi_selector_components",
    "honmon_common_controls",
    "honmon_entry_marker_variants",
    "honmon_index_boundary_slicing",
    "honmon_links",
    "title_pointer_resolution",
    "simple_index_leaf",
    "tagged_index_leaf",
    "normalized_index_keys",
    "menu_body_targets",
    "menu_menu_targets",
    "toc_tree_targets",
    "uni_gaiji_mapping",
    "ga16_half_full",
    "gai16_name_variants",
    "template_gaiji_fallback",
    "nonliteral_gaiji_marker",
    "colscr_image_records",
    "pcmdata_audio_records",
    "dense_honmon_t_contents",
    "dense_honmon_honbun",
    "dense_honmon_main_table",
    "block_offset_rendererdb",
    "sidecar_media_blob",
    "ziptomedia_links",
    "panel_xml_cells",
    "panel_bin_rows",
    "panel_cell_to_entry_address",
    "malformed_catalog_offsets",
    "truncated_sseddata_chunk",
    "lossless_repack_representative",
}


def fixture(name: str) -> Path:
    return FIXTURE_ROOT / name


def test_synthetic_fixture_manifest_is_complete() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    names = {row["fixture"] for row in manifest["fixtures"]}

    assert names == EXPECTED_FIXTURES
    assert {path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()} == EXPECTED_FIXTURES
    for name in names:
        local = json.loads((fixture(name) / "manifest.json").read_text(encoding="utf-8"))
        assert local["fixture"] == name
        assert local["files"]


def test_synthetic_catalog_fixtures_parse() -> None:
    for name in sorted(EXPECTED_FIXTURES):
        idxs = sorted(fixture(name).glob("*.IDX"))
        for idx in idxs:
            title, elements = parse_ssedinfo(idx)
            assert title
            assert elements


def test_sseddata_roundtrip_fixture_expands_to_expected_payload() -> None:
    root = fixture("sseddata_chunk_roundtrip")
    expected = (root / "expanded.bin").read_bytes()
    expanded = expand_sseddata_file(root / "SAMPLE.DIC")

    assert expanded.startswith(expected)
    assert expanded[len(expected) :] == bytes(len(expanded) - len(expected))


def test_case_insensitive_component_lookup_fixture_uses_preserved_casing() -> None:
    root = fixture("case_insensitive_component_lookup")
    _title, elements = parse_ssedinfo(root / "CASE.IDX")
    names = {element.filename for element in elements}

    assert "honmon.dic" in names
    assert find_case_insensitive(root, "HONMON.DIC") == root / "honmon.dic"


def test_kw_cr_multi_index_fixtures_have_decodable_rows() -> None:
    cases = [
        ("kw_cr_title_index", "KWINDEX.DIC", 0x80, "keyword"),
        ("kw_cr_title_index", "CRINDEX.DIC", 0x81, "cross"),
        ("multi_selector_components", "MUL1_1_2.DIC", 0xA1, "selector"),
    ]
    for fixture_name, component, component_type, expected_key in cases:
        root = fixture(fixture_name)
        _title, elements = parse_ssedinfo(next(root.glob("*.IDX")))
        element = next(row for row in elements if row.filename == component)
        expanded = expand_sseddata_file(root / component)
        rows = []
        result = scan_index_component(component, component_type, expanded, element.start, gaiji="drop", gaiji_map={}, emit_row=rows.append)
        assert result.unknown_leaf_bytes == 0
        assert any(str(row["key"]).casefold() == expected_key for row in rows)


def test_menu_and_toc_navigation_fixtures_decode() -> None:
    menu_root = fixture("menu_body_targets")
    _title, elements = parse_ssedinfo(menu_root / "MENUBODY.IDX")
    menu = next(row for row in elements if row.filename == "MENU.DIC")
    parsed = parse_menu_stream(expand_sseddata_file(menu_root / "MENU.DIC"))
    assert parsed.stats["destinations"] == 1
    assert parsed.records[0].links[0].destination.block == 2

    toc_root = fixture("toc_tree_targets")
    toc = expand_sseddata_file(toc_root / "TOC.DIC")
    assert b"\x1f\x49" in toc
    assert b"\x1f\x69" in toc


def test_gaiji_fixture_families_parse() -> None:
    uni = parse_uni_resource(fixture("uni_gaiji_mapping") / "GAIJI.uni")
    assert uni is not None
    assert {record.display for record in uni.records} == {"Ω", "𰻞"}

    assert parse_ga16_resource(fixture("ga16_half_full") / "GA16HALF") is not None
    assert parse_ga16_resource(fixture("ga16_half_full") / "GA16FULL") is not None
    assert parse_ga16_resource(fixture("gai16_name_variants") / "GAI16H00") is not None
    assert parse_ga16_resource(fixture("gai16_name_variants") / "GAI16F00") is not None
    assert (fixture("template_gaiji_fallback") / "Templates" / "b121.png").read_bytes().startswith(b"\x89PNG")


def test_media_fixtures_decode() -> None:
    colscr = expand_sseddata_file(fixture("colscr_image_records") / "COLSCR.DIC")
    assert parse_colscr_image_header(colscr) is not None

    honmon = expand_sseddata_file(fixture("pcmdata_audio_records") / "HONMON.DIC")
    refs = list(iter_pcm_references(honmon))
    assert len(refs) == 1
    pcm = expand_sseddata_file(fixture("pcmdata_audio_records") / "PCMDATA.DIC")
    start = (refs[0].pointer.start_block - 2) * 2048 + refs[0].pointer.start_offset
    end = (refs[0].pointer.end_block - 2) * 2048 + refs[0].pointer.end_offset + 1
    parsed = parse_pcmdata_record(pcm[start:end], start)
    assert parsed is not None
    assert parsed[0].codec == "pcm"


def test_panel_fixtures_decode() -> None:
    cells = list(iter_panel_cells(fixture("panel_xml_cells") / "Panels.xml"))
    assert cells and cells[0].label == "あ"

    refs = list(iter_panel_data_references(fixture("panel_cell_to_entry_address") / "Panels.xml"))
    assert refs and refs[0].filename == "Panel\\kana.bin"

    panel = parse_panel_bin((fixture("panel_bin_rows") / "Panel" / "kana.bin").read_bytes())
    assert panel.format == "address_label"
    assert [record.text for record in panel.records] == ["あ", "い"]


def test_dense_and_sidecar_sqlite_fixtures_have_expected_tables() -> None:
    cases = {
        "dense_honmon_t_contents": ("vlpljblF", "t_contents"),
        "dense_honmon_honbun": ("body.db", "HONBUN"),
        "dense_honmon_main_table": ("DENSEMAIN", "DENSEMAIN"),
        "block_offset_rendererdb": ("renderer.db", "body"),
        "sidecar_media_blob": ("media.db", "media"),
    }
    for fixture_name, (db_name, table) in cases.items():
        con = sqlite3.connect(f"file:{(fixture(fixture_name) / db_name).as_posix()}?mode=ro", uri=True)
        try:
            names = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        finally:
            con.close()
        assert table in names


def test_lossless_repack_representative_fixture_roundtrips(tmp_path: Path) -> None:
    root = fixture("lossless_repack_representative")
    source = DictionarySource(
        dict_id="REPACK",
        idx=root / "REPACK.IDX",
        title="REPACK",
        honmon=root / "HONMON.DIC",
        honmon_start_block=2,
        gaiji_map={},
    )

    report = repack_dictionary_source(source, tmp_path / "repacked")

    assert report.ok
    assert all(row.byte_identical for row in report.files)
