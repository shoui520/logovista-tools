# Synthetic LogoVista/SSED Fixtures

These fixtures are generated, deterministic, and committed on purpose. They contain no private corpus text.

Compatibility source of truth:

- The real LogoVista corpus remains the compatibility target.
- These fixtures are regression inputs for format structures already observed or intentionally modeled.
- Passing fixture tests is not evidence that a feature is corpus-complete.
- New reader/repacker behavior must still be validated against relevant real packages before it is treated as supported.
- Fixtures should stay minimal and explicit; do not encode product-specific guesses here before they are understood from corpus evidence.

Regenerate from the repository root with:

```bash
PYTHONPATH=src python3 tools/generate_synthetic_fixtures.py
```

Fixtures:

- `catalog_basic`
- `catalog_inactive_missing_component`
- `sseddata_chunk_roundtrip`
- `case_insensitive_component_lookup`
- `basic_bidirectional_title_index`
- `standard_four_title_index`
- `kw_cr_title_index`
- `multi_selector_components`
- `honmon_common_controls`
- `honmon_entry_marker_variants`
- `honmon_index_boundary_slicing`
- `honmon_links`
- `title_pointer_resolution`
- `simple_index_leaf`
- `tagged_index_leaf`
- `normalized_index_keys`
- `menu_body_targets`
- `menu_menu_targets`
- `toc_tree_targets`
- `uni_gaiji_mapping`
- `ga16_half_full`
- `gai16_name_variants`
- `template_gaiji_fallback`
- `nonliteral_gaiji_marker`
- `colscr_image_records`
- `pcmdata_audio_records`
- `dense_honmon_t_contents`
- `dense_honmon_honbun`
- `dense_honmon_main_table`
- `block_offset_rendererdb`
- `sidecar_media_blob`
- `ziptomedia_links`
- `panel_xml_cells`
- `panel_bin_rows`
- `panel_cell_to_entry_address`
- `malformed_catalog_offsets`
- `truncated_sseddata_chunk`
- `lossless_repack_representative`
