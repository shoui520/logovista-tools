from __future__ import annotations

import json
from pathlib import Path

from logovista_tools.resource_taxonomy import (
    analyze_bin_file,
    analyze_ccaltstr,
    analyze_figure_dic,
    analyze_panel_package,
    build_taxonomy_report,
    dicprof_report_for_package,
    idx_uni_associations_for_package,
    resolve_case_insensitive,
    structural_references_from_text,
)


def test_case_insensitive_lookup_and_collision_detection(tmp_path: Path) -> None:
    (tmp_path / "Kwindex.dic").write_bytes(b"SSEDDATA")

    match = resolve_case_insensitive(tmp_path, "KWINDEX.DIC")

    assert match.status == "case_insensitive"
    assert match.matched == "Kwindex.dic"

    collision = tmp_path / "collision"
    collision.mkdir()
    (collision / "Panel.XML").write_text("<root/>", encoding="utf-8")
    (collision / "panel.xml").write_text("<root/>", encoding="utf-8")

    ambiguous = resolve_case_insensitive(collision, "PANEL.XML")

    assert ambiguous.status == "ambiguous"
    assert ambiguous.candidates == ("Panel.XML", "panel.xml")


def test_dicprof_parsing_and_declared_resource_reference(tmp_path: Path) -> None:
    package = tmp_path / "_DCT_BMANNER"
    package.mkdir()
    (package / "BMANNAR.IDX").write_bytes(b"SSEDINFO")
    (package / "DICPROF.INI").write_text(
        "[GENERAL]\nDICTID=BMANNAR\nGAIJI=000000A4.uni\n",
        encoding="cp932",
    )
    (package / "000000A4.uni").write_text("a121\tX\n", encoding="cp932")

    report = dicprof_report_for_package(package)
    assert report is not None

    refs = {(row["key"], row["lookup"]["status"], row["lookup"]["matched"]) for row in report["references"]}
    assert ("GAIJI", "exact", "000000A4.uni") in refs

    associations = idx_uni_associations_for_package(package)
    by_name = {row["name"]: row for row in associations["files"]}
    assert by_name["BMANNAR.IDX"]["association"]["rule"] == "dicprof_declared_basename"
    assert by_name["000000A4.uni"]["eight_hex_stem"] is True


def test_panel_xml_html_dtd_reference_extraction_and_role(tmp_path: Path) -> None:
    package = tmp_path / "PANELPKG"
    panel = package / "Panel"
    panel.mkdir(parents=True)
    (panel / "panel.xml").write_text(
        '<panel><item href="detail.html"/><image src="icon.bin"/></panel>',
        encoding="utf-8",
    )
    (panel / "detail.html").write_text('<html><body><img src="icon.png"></body></html>', encoding="utf-8")
    (panel / "panel.dtd").write_text("<!ELEMENT panel (item*)>\n<!ENTITY icon SYSTEM \"icon.png\">", encoding="utf-8")
    (panel / "icon.bin").write_bytes(b"\x89PNG\r\n\x1a\npayload")

    refs = structural_references_from_text(panel / "panel.xml", (panel / "panel.xml").read_text(encoding="utf-8"))
    assert any(row.get("value") == "detail.html" for row in refs)

    report = analyze_panel_package(package, tmp_path)
    assert report is not None
    assert report["file_count"] == 4
    assert report["classification"]["role"] == "panel_ui"
    assert report["classification"]["evidence"]["has_bin"] is True


def test_panel_bin_fingerprint_is_structural_only(tmp_path: Path) -> None:
    package = tmp_path / "ZYAKUKOG"
    package.mkdir()
    blob = package / "Panel" / "asset.bin"
    blob.parent.mkdir()
    blob.write_bytes(b"BM" + b"\x00" * 20 + b"image.png\x00")

    row = analyze_bin_file(blob, package)

    assert row["classification"]["role"] == "media_resource"
    assert row["structural_string_refs"] == ["image.png"]
    assert "sha256" in row


def test_ccaltstr_and_figure_are_fingerprinted_without_semantic_overclaim(tmp_path: Path) -> None:
    package = tmp_path / "KQSYNONM"
    package.mkdir()
    (package / "CCALTSTR.HA").write_bytes(b"\x00\x01resource.idx\x00")
    (package / "FIGURE.DIC").write_bytes(b"SSEDDATA" + b"\x00" * 32)

    ccaltstr = analyze_ccaltstr(package / "CCALTSTR.HA", package)
    figure = analyze_figure_dic(package)

    assert ccaltstr["role"] == "unknown_alt_string_or_renderer_helper"
    assert figure is not None
    assert figure["role"] == "media_resource"
    assert figure["confidence"] == "low"


def test_resource_taxonomy_report_shape(tmp_path: Path) -> None:
    package = tmp_path / "HAESPJPN"
    panel = package / "Panel"
    panel.mkdir(parents=True)
    (panel / "panel.xml").write_text('<panel><item href="panel.bin"/></panel>', encoding="utf-8")
    (panel / "panel.bin").write_bytes(b"PK\x03\x04")
    (package / "DICPROF.INI").write_text("[GENERAL]\nINDEX=HAESPJPN.IDX\n", encoding="cp932")
    (package / "HAESPJPN.IDX").write_bytes(b"SSEDINFO")
    out_dir = tmp_path / "out"

    summary = build_taxonomy_report([tmp_path], out_dir, jobs=1)

    assert summary["schema"] == "logovista.resource_sidecar_taxonomy.v2"
    assert summary["panel_package_count"] == 1
    assert (out_dir / "lvcore-handoff.json").is_file()
    handoff = json.loads((out_dir / "lvcore-handoff.json").read_text(encoding="utf-8"))
    assert handoff["schema"] == "lvcore.resource_sidecar_handoff.v1"
    assert any(row["family"] == "Panel" for row in handoff["file_families"])
