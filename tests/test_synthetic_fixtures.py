import argparse
import json
import sqlite3
from pathlib import Path

from logovista_tools.colscr import parse_colscr_image_header
from logovista_tools.entries import (
    DictionarySource,
    decode_tokens,
    discover_dictionaries,
    iter_entry_slices_with_boundaries,
    normalize_fullwidth_ascii,
    tokens_to_text,
)
from logovista_tools.gaiji import parse_ga16_resource, parse_uni_resource
from logovista_tools.indexes import scan_index_component
from logovista_tools.menus import parse_menu_stream, resolve_menu_record_destinations
from logovista_tools.panels import (
    iter_panel_cells,
    iter_panel_data_references,
    iter_panel_references,
    panel_bin_navigation_rows,
    panel_cell_navigation_row,
    parse_panel_bin,
)
from logovista_tools.pcmdata import iter_pcm_references, parse_pcmdata_record
from logovista_tools.repacker import repack_dictionary_source
from logovista_tools.rendererdb import (
    extract_rendererdb_dictionary,
    iter_honmon_id_records,
    media_column_names,
    media_table_name,
    summarize_ziptomedia_refs,
    write_media_records,
)
from logovista_tools.resources import load_image_resource_profile, relative_image_source
from logovista_tools.ssed import expand_sseddata_file, find_case_insensitive, parse_sseddata_header, parse_ssedinfo
from logovista_tools.windows import discover_renderer_sidecars, load_exinfo_for_idx, sqlite_role_for_tables, sqlite_table_schema_summaries


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


def fixture_source(name: str) -> DictionarySource:
    sources = discover_dictionaries([fixture(name)], jobs=1, include_gaiji=True, include_images=True)
    assert len(sources) == 1
    return sources[0]


def rendererdb_args(**overrides) -> argparse.Namespace:
    values = {
        "decrypted_db": None,
        "include_html": True,
        "limit": None,
        "media_limit": None,
        "vertical": False,
        "write_media": False,
        "write_ziptomedia": False,
        "ziptomedia_limit": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def scan_fixture_index_component(fixture_name: str, component: str):
    root = fixture(fixture_name)
    idx = next(root.glob("*.IDX"))
    _title, elements = parse_ssedinfo(idx)
    element = next(row for row in elements if row.filename == component)
    rows = []
    result = scan_index_component(
        component,
        element.type,
        expand_sseddata_file(root / component),
        element.start,
        gaiji="drop",
        gaiji_map={},
        emit_row=rows.append,
    )
    return result, rows, element


def test_synthetic_fixture_manifest_is_complete() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    names = {row["fixture"] for row in manifest["fixtures"]}

    assert names == EXPECTED_FIXTURES
    assert {path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()} == EXPECTED_FIXTURES
    root_rows = {row["fixture"]: row for row in manifest["fixtures"]}
    for name in names:
        local = json.loads((fixture(name) / "manifest.json").read_text(encoding="utf-8"))
        actual_files = sorted(
            item.relative_to(fixture(name)).as_posix()
            for item in fixture(name).rglob("*")
            if item.is_file() and item.name != "manifest.json"
        )
        assert local["fixture"] == name
        assert local["files"] == actual_files
        assert root_rows[name]["files"] == actual_files
        assert root_rows[name]["features"] == local["features"]


def test_synthetic_fixture_readme_keeps_corpus_as_source_of_truth() -> None:
    readme = (FIXTURE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "The real LogoVista corpus remains the compatibility target." in readme
    assert "Passing fixture tests is not evidence that a feature is corpus-complete." in readme


def test_synthetic_catalog_fixtures_parse() -> None:
    for name in sorted(EXPECTED_FIXTURES):
        idxs = sorted(fixture(name).glob("*.IDX"))
        for idx in idxs:
            title, elements = parse_ssedinfo(idx)
            assert title
            assert elements


def test_synthetic_catalog_components_match_sseddata_headers() -> None:
    excluded = {"catalog_inactive_missing_component", "malformed_catalog_offsets"}
    for name in sorted(EXPECTED_FIXTURES - excluded):
        root = fixture(name)
        for idx in sorted(root.glob("*.IDX")):
            _title, elements = parse_ssedinfo(idx)
            for element in elements:
                path = find_case_insensitive(root, element.filename)
                assert path is not None, f"{name}: missing catalog component {element.filename}"
                header = parse_sseddata_header(path)
                assert header["start_block"] == element.start
                assert header["end_block"] == element.end
                assert header["kind"] == element.type
                expanded = expand_sseddata_file(path)
                assert len(expanded) == (element.end - element.start + 1) * 2048


def test_intentionally_broken_synthetic_fixtures_are_actually_broken() -> None:
    inactive_root = fixture("catalog_inactive_missing_component")
    _title, inactive_elements = parse_ssedinfo(inactive_root / "INACTIVE.IDX")
    assert {element.filename for element in inactive_elements} == {"HONMON.DIC", "MISSING.DIC"}
    assert find_case_insensitive(inactive_root, "HONMON.DIC") is not None
    assert find_case_insensitive(inactive_root, "MISSING.DIC") is None

    bad_root = fixture("malformed_catalog_offsets")
    _title, elements = parse_ssedinfo(bad_root / "BAD.IDX")
    assert len(elements) == 1
    assert elements[0].filename == "HONMON.DIC"
    assert elements[0].start > elements[0].end

    truncated = expand_sseddata_file(fixture("truncated_sseddata_chunk") / "TRUNC.DIC")
    assert len(truncated) % 2048 != 0


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


def test_title_and_index_fixtures_resolve_body_and_title_pointers() -> None:
    cases = {
        "basic_bidirectional_title_index": ("FHINDEX.DIC", "alpha"),
        "standard_four_title_index": ("FKINDEX.DIC", "alpha"),
        "kw_cr_title_index": ("KWINDEX.DIC", "keyword"),
        "title_pointer_resolution": ("FHINDEX.DIC", "alpha"),
        "normalized_index_keys": ("FHINDEX.DIC", "mixed case"),
    }
    for fixture_name, (component, expected_key) in cases.items():
        root = fixture(fixture_name)
        idx = next(root.glob("*.IDX"))
        _title, elements = parse_ssedinfo(idx)
        element = next(row for row in elements if row.filename == component)
        rows = []
        result = scan_index_component(
            component,
            element.type,
            expand_sseddata_file(root / component),
            element.start,
            gaiji="drop",
            gaiji_map={},
            emit_row=rows.append,
        )
        leaf = next(row for row in rows if row.get("kind") == "leaf")
        assert result.unknown_leaf_bytes == 0
        assert str(leaf["key"]).casefold() == expected_key.casefold()
        if fixture_name == "normalized_index_keys":
            assert normalize_fullwidth_ascii("Ｍｉｘｅｄ Ｃａｓｅ").casefold() == str(leaf["key"]).casefold()
        assert leaf["body"]["block"] >= 2
        assert leaf["title"]["block"] >= 2


def test_simple_and_tagged_index_leaf_fixtures_decode_expected_row_shapes() -> None:
    simple_result, simple_rows, _simple_element = scan_fixture_index_component("simple_index_leaf", "FHINDEX.DIC")
    simple_leaves = [row for row in simple_rows if row.get("kind") == "leaf"]
    assert simple_result.leaf_rows == 2
    assert simple_result.search_groups == 0
    assert simple_result.unknown_leaf_bytes == 0
    assert [row["key"] for row in simple_leaves] == ["ALPHA", "BETA"]
    assert all(row["tagged"] is False for row in simple_leaves)
    assert [row["body"]["offset"] for row in simple_leaves] == [0, 52]
    assert [row["title"]["offset"] for row in simple_leaves] == [0, 16]

    tagged_result, tagged_rows, _tagged_element = scan_fixture_index_component("tagged_index_leaf", "FKINDEX.DIC")
    tagged_leaves = [row for row in tagged_rows if row.get("kind") == "leaf"]
    assert tagged_result.leaf_rows == 2
    assert tagged_result.search_groups == 2
    assert tagged_result.unknown_leaf_bytes == 0
    assert [row["key"] for row in tagged_leaves] == ["ALPHA", "BETA"]
    assert all(row["tagged"] is True for row in tagged_leaves)
    assert [row["target_count_hint"] for row in tagged_leaves] == [1, 1]

    backward_result, backward_rows, _backward_element = scan_fixture_index_component("tagged_index_leaf", "BKINDEX.DIC")
    backward_leaves = [row for row in backward_rows if row.get("kind") == "leaf"]
    assert backward_result.search_groups == 2
    assert [row["key"] for row in backward_leaves] == ["AHPLA", "ATEB"]
    assert all(row["tagged"] is True for row in backward_leaves)


def test_honmon_control_link_and_section_fixtures_decode_through_body_model() -> None:
    controls = expand_sseddata_file(fixture("honmon_common_controls") / "HONMON.DIC")
    tokens, stats = decode_tokens(controls, preserve_sections=True)
    text = tokens_to_text(tokens)
    assert "fullwidth ABC ruby overbase" in text
    assert stats["controls"] >= 6

    markers = expand_sseddata_file(fixture("honmon_entry_marker_variants") / "HONMON.DIC")
    marker_tokens, marker_stats = decode_tokens(markers, preserve_sections=True)
    marker_text = tokens_to_text(marker_tokens)
    assert "<section:0008>" in marker_text
    assert "<section:1001>" in marker_text
    assert marker_stats["sections"] == 2

    links = expand_sseddata_file(fixture("honmon_links") / "HONMON.DIC")
    _link_tokens, link_stats = decode_tokens(links)
    assert link_stats["links"] == 1


def test_honmon_index_boundary_fixture_keeps_non_entry_sections_inside_parent() -> None:
    root = fixture("honmon_index_boundary_slicing")
    honmon = expand_sseddata_file(root / "HONMON.DIC")
    _result, rows, _element = scan_fixture_index_component("honmon_index_boundary_slicing", "FHINDEX.DIC")
    boundary_offsets = [
        (row["body"]["block"] - 2) * 2048 + row["body"]["offset"]
        for row in rows
        if row.get("kind") == "leaf"
    ]

    slices = list(iter_entry_slices_with_boundaries(honmon, boundary_offsets))
    rendered = []
    for start, end in slices:
        tokens, _stats = decode_tokens(honmon[start:end], preserve_sections=True)
        rendered.append(tokens_to_text(tokens))

    assert len(slices) == 2
    assert rendered[0].startswith("<section:0001>parent")
    assert "<section:0002>child section" in rendered[0]
    assert rendered[1].startswith("<section:0001>next")


def test_nonliteral_gaiji_marker_fixture_is_available_as_marker_not_text() -> None:
    data = expand_sseddata_file(fixture("nonliteral_gaiji_marker") / "HONMON.DIC")

    placeholder_tokens, placeholder_stats = decode_tokens(data, gaiji="placeholder", preserve_sections=True)
    assert placeholder_stats["gaiji"] == 1
    assert "<zB162>" in tokens_to_text(placeholder_tokens)

    user_tokens, user_stats = decode_tokens(data, gaiji="h-placeholder", preserve_sections=True)
    assert user_stats["gaiji"] == 1
    assert tokens_to_text(user_tokens) == "<section:0001>marker\nbeforeafter"


def test_menu_and_toc_navigation_fixtures_decode() -> None:
    menu_root = fixture("menu_body_targets")
    _title, elements = parse_ssedinfo(menu_root / "MENUBODY.IDX")
    parsed = parse_menu_stream(expand_sseddata_file(menu_root / "MENU.DIC"))
    assert parsed.stats["destinations"] == 1
    assert parsed.records[0].links[0].destination.block == 2
    assert resolve_menu_record_destinations(parsed.records, elements) == 1
    assert parsed.records[0].destination.target.kind == "body"

    menu_menu_root = fixture("menu_menu_targets")
    _menu_title, menu_elements = parse_ssedinfo(menu_menu_root / "MENUMENU.IDX")
    menu_to_menu = parse_menu_stream(expand_sseddata_file(menu_menu_root / "MENU.DIC"))
    assert menu_to_menu.records[0].label() == "submenu"
    assert resolve_menu_record_destinations(menu_to_menu.records, menu_elements) == 1
    assert menu_to_menu.records[0].destination.target.kind == "menu"
    assert menu_to_menu.records[0].destination.target.component == "MENU.DIC"

    toc_root = fixture("toc_tree_targets")
    _toc_title, toc_elements = parse_ssedinfo(toc_root / "TOCTREE.IDX")
    toc = parse_menu_stream(expand_sseddata_file(toc_root / "TOC.DIC"))
    assert toc.records[0].links[0].control == "1f49"
    assert toc.records[0].links[0].destination.encoding == "toc-big-endian"
    assert resolve_menu_record_destinations(toc.records, toc_elements) == 1
    assert toc.records[0].destination.target.kind == "body"


def test_gaiji_fixture_families_parse() -> None:
    uni = parse_uni_resource(fixture("uni_gaiji_mapping") / "GAIJI.uni")
    assert uni is not None
    assert {record.display for record in uni.records} == {"Ω", "𰻞"}

    assert parse_ga16_resource(fixture("ga16_half_full") / "GA16HALF") is not None
    assert parse_ga16_resource(fixture("ga16_half_full") / "GA16FULL") is not None
    assert parse_ga16_resource(fixture("gai16_name_variants") / "GAI16H00") is not None
    assert parse_ga16_resource(fixture("gai16_name_variants") / "GAI16F00") is not None

    template_root = fixture("template_gaiji_fallback")
    profile = load_image_resource_profile(template_root)
    assert "b121" in profile.gaiji_image_keys
    png = next(path for path in profile.resources["b121"].files if path.suffix == ".png")
    assert relative_image_source(png, template_root) == "Templates/b121.png"


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
    cell_row = panel_cell_navigation_row(cells[0])
    assert cell_row.target_kind == "panel"
    assert cell_row.ref == "01010000"

    refs = list(iter_panel_data_references(fixture("panel_cell_to_entry_address") / "Panels.xml"))
    assert refs and refs[0].filename == "Panel\\kana.bin"

    panel = parse_panel_bin((fixture("panel_bin_rows") / "Panel" / "kana.bin").read_bytes())
    assert panel.format == "address_label"
    assert [record.text for record in panel.records] == ["あ", "い"]

    panel_root = fixture("panel_cell_to_entry_address")
    _title, elements = parse_ssedinfo(panel_root / "PANELADR.IDX")
    inline_cells = list(iter_panel_cells(panel_root / "Panels.xml"))
    inline_row = panel_cell_navigation_row(inline_cells[0], elements=elements)
    assert inline_row.label == "alpha"
    assert inline_row.target_kind == "body"
    assert inline_row.target.component == "HONMON.DIC"

    external_refs = list(iter_panel_references(panel_root / "Panels.xml"))
    external_panel = parse_panel_bin((panel_root / "Panel" / "kana.bin").read_bytes())
    navigation_rows = panel_bin_navigation_rows(
        external_refs[0],
        external_panel,
        resolved_path=panel_root / "Panel" / "kana.bin",
        elements=elements,
    )
    assert navigation_rows[0].label == "alpha"
    assert navigation_rows[0].target_kind == "body"
    assert navigation_rows[0].target.component == "HONMON.DIC"


def test_dense_and_sidecar_sqlite_fixtures_have_expected_tables() -> None:
    cases = {
        "dense_honmon_t_contents": ("vlpljblF", "t_contents"),
        "dense_honmon_honbun": ("body.db", "HONBUN"),
        "dense_honmon_main_table": ("DENSEMAIN", "main"),
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


def test_dense_honmon_fixtures_use_real_fixed_width_anchor_records() -> None:
    for fixture_name, expected_ids in {
        "dense_honmon_t_contents": [1, 2],
        "dense_honmon_honbun": [1, 2],
        "dense_honmon_main_table": [1, 2],
        "block_offset_rendererdb": [1],
        "ziptomedia_links": [1],
    }.items():
        source = fixture_source(fixture_name)
        expanded = expand_sseddata_file(source.honmon)
        records = list(iter_honmon_id_records(expanded, honmon_start_block=source.honmon_start_block))
        assert [record.data_id for record in records] == expected_ids
        assert all(record.record_offset % 32 == 0 for record in records)


def test_rendererdb_dense_sidecar_fixtures_resolve_to_body_rows(tmp_path: Path) -> None:
    expected = {
        "dense_honmon_t_contents": ("ok", "alpha body"),
        "dense_honmon_honbun": ("ok_honbun_ordered", "alpha box"),
        "dense_honmon_main_table": ("ok_main_id_text_body", "main table body"),
        "block_offset_rendererdb": ("ok_block_offset_body", "block body"),
    }
    for fixture_name, (status, expected_text) in expected.items():
        summary = extract_rendererdb_dictionary(fixture_source(fixture_name), tmp_path / fixture_name, rendererdb_args())
        assert summary["status"] == status
        assert summary["entries_matched_to_raw_honmon"] == 1
        assert summary["entries_emitted"] == 1
        first = json.loads(Path(summary["entries_path"]).read_text(encoding="utf-8").splitlines()[0])
        assert expected_text in first["plain"]
        assert first["raw_honmon"]


def test_dense_main_table_fixture_is_discovered_through_exinfo_rosqlname() -> None:
    source = fixture_source("dense_honmon_main_table")
    exinfo = load_exinfo_for_idx(source.idx)
    assert exinfo is not None
    assert exinfo.general["ROSQLNAME"] == "DENSEMAIN"
    sidecars = discover_renderer_sidecars(source.idx, exinfo)
    assert [sidecar.path.name for sidecar in sidecars] == ["DENSEMAIN"]


def test_sidecar_media_blob_fixture_uses_supported_media_schema(tmp_path: Path) -> None:
    db = fixture("sidecar_media_blob") / "media.db"
    role = sqlite_role_for_tables(sqlite_table_schema_summaries(db))
    assert role == "sqlite_media_store"
    con = sqlite3.connect(db)
    try:
        assert media_table_name(con) == "media"
        assert media_column_names(con) is not None
        summary = write_media_records(con, tmp_path / "media")
    finally:
        con.close()
    assert summary["media_rows"] == 1
    assert summary["media_written"] == 1
    assert (tmp_path / "media" / "pixel.png").read_bytes().startswith(b"\x89PNG")


def test_ziptomedia_fixture_resolves_referenced_sound_file(tmp_path: Path) -> None:
    source = fixture_source("ziptomedia_links")
    summary = extract_rendererdb_dictionary(source, tmp_path / "zipmedia", rendererdb_args())
    assert summary["status"] == "ok"
    assert summary["ziptomedia_distinct_references"] == 1
    assert summary["ziptomedia_files_available"] == 1
    assert summary["ziptomedia_missing_references"] == 0

    refs = summarize_ziptomedia_refs(source.idx, {"sound001.wav": 1})
    assert refs["ziptomedia_files_available"] == 1


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
