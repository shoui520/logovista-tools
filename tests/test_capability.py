import json

from logovista_tools.capability import build_capability_matrix


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_capability_matrix_classifies_green_raw_body(tmp_path):
    profile_dir = tmp_path / "profiles"
    honmon_dir = tmp_path / "honmon"
    component_dir = tmp_path / "components"
    write_json(
        profile_dir / "summary.json",
        {
            "profiles": [
                {
                    "dict_id": "GOOD",
                    "title": "Good",
                    "status": "ok",
                    "body_source_hint": "honmon",
                    "honmon_shape": "body_stream_indexed",
                    "index_unknown_leaf_bytes": 0,
                    "index_boundary_offsets": 1,
                }
            ]
        },
    )
    write_json(profile_dir / "GOOD" / "profile.json", {"classification": {"status": "ok", "missing_components": []}})
    write_json(
        honmon_dir / "summary.json",
        {
            "profiles": [
                {
                    "dict_id": "GOOD",
                    "title": "Good",
                    "status": "ok",
                    "byte_shape": "marker_rich_text_stream",
                    "unknown_controls": 0,
                    "unknown_bytes": 0,
                    "gaiji_unresolved": 0,
                }
            ]
        },
    )
    write_json(
        honmon_dir / "GOOD" / "honmon_bytes.json",
        {"decode": {"stats": {"gaiji": 0, "gaiji_unresolved": 0, "unknown_controls": 0, "unknown_bytes": 0}}},
    )
    write_json(
        component_dir / "summary.json",
        {
            "profiles": [
                {
                    "dict_id": "GOOD",
                    "title": "Good",
                    "component_counts": {"index": 1, "title": 1, "menu": 1},
                    "totals": {
                        "index_nonzero_residual_bytes": 0,
                        "index_trailing_component_nonzero": 0,
                    },
                }
            ]
        },
    )
    write_json(
        component_dir / "GOOD" / "component_forensics.json",
        {
            "components": [
                {"role": "index", "status": "ok"},
                {
                    "role": "title",
                    "status": "ok",
                    "coverage": {},
                    "decode": {"stats": {"gaiji": 0, "gaiji_unresolved": 0}},
                },
                {
                    "role": "menu",
                    "status": "ok",
                    "coverage": {},
                    "decode": {"stats": {"gaiji": 0, "gaiji_unresolved": 0}},
                    "menu": {"destinations": 2, "resolved_destinations": 2},
                },
            ],
            "uni_files": [],
        },
    )

    report = build_capability_matrix(
        profile_dir=profile_dir,
        honmon_bytes_dir=honmon_dir,
        component_forensics_dir=component_dir,
    )

    row = report["rows"][0]
    assert row["raw_honmon_body"] == "yes"
    assert row["indexes_fully_parsed"] == "yes"
    assert row["titles_fully_parsed"] == "yes"
    assert row["menu_pointers_resolved"] == "yes"
    assert row["writer_repacker_status"] == "green"


def test_capability_matrix_names_writer_blockers(tmp_path):
    profile_dir = tmp_path / "profiles"
    honmon_dir = tmp_path / "honmon"
    component_dir = tmp_path / "components"
    write_json(
        profile_dir / "summary.json",
        {
            "profiles": [
                {
                    "dict_id": "ROUGH",
                    "title": "Rough",
                    "status": "incomplete",
                    "body_source_hint": "honmon_anchor_dereference",
                    "honmon_shape": "dense_marker_table",
                    "index_unknown_leaf_bytes": 0,
                    "index_boundary_offsets": 1,
                }
            ]
        },
    )
    write_json(profile_dir / "ROUGH" / "profile.json", {"classification": {"status": "incomplete", "missing_components": ["MENU.DIC"]}})
    write_json(honmon_dir / "summary.json", {"profiles": [{"dict_id": "ROUGH", "title": "Rough", "status": "ok"}]})
    write_json(
        honmon_dir / "ROUGH" / "honmon_bytes.json",
        {
            "decode": {
                "stats": {
                    "gaiji": 3,
                    "gaiji_unresolved": 2,
                    "unknown_controls": 1,
                    "unknown_bytes": 0,
                    "truncated_controls": 0,
                    "truncated_gaiji": 0,
                    "invalid_jis_pairs": 0,
                }
            }
        },
    )
    write_json(
        component_dir / "summary.json",
        {
            "profiles": [
                {
                    "dict_id": "ROUGH",
                    "title": "Rough",
                    "component_counts": {"index": 1, "title": 1, "menu": 1, "pcmdata": 1},
                    "totals": {
                        "index_nonzero_residual_bytes": 3,
                        "index_trailing_component_nonzero": 3,
                        "pcmdata_invalid_referenced_records": 2,
                    },
                }
            ]
        },
    )
    write_json(
        component_dir / "ROUGH" / "component_forensics.json",
        {
            "components": [
                {"role": "index", "status": "ok", "nonzero_residual_bytes": 3},
                {
                    "role": "title",
                    "status": "ok",
                    "coverage": {"unknown_controls": 1},
                    "decode": {"stats": {"unknown_controls": 1, "gaiji": 0, "gaiji_unresolved": 0}},
                },
                {"role": "menu", "status": "missing_file"},
                {"role": "pcmdata", "status": "ok", "invalid_referenced_records": 2, "nonzero_unparsed_bytes": 0},
            ],
            "uni_files": [],
        },
    )

    report = build_capability_matrix(
        profile_dir=profile_dir,
        honmon_bytes_dir=honmon_dir,
        component_forensics_dir=component_dir,
    )

    row = report["rows"][0]
    blockers = set(row["writer_repacker_blockers"].split(";"))
    assert row["raw_honmon_body"] == "no"
    assert row["indexes_fully_parsed"] == "partial"
    assert row["titles_fully_parsed"] == "partial"
    assert row["gaiji_fully_resolved"] == "partial"
    assert row["media_refs_resolved"] == "partial"
    assert row["menu_pointers_resolved"] == "no"
    assert row["writer_repacker_status"] == "red"
    assert "body_requires_sidecar_or_is_missing" in blockers
    assert "unknown_or_structural_text_issues" in blockers
