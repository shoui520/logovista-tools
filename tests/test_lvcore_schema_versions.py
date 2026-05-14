from __future__ import annotations

from pathlib import Path

from test_lvcore_experimental import ga16_file, make_gaiji_package

from lvcore import Diagnostic, DiagnosticArea, Severity, open_package


def _assert_schema(value: dict[str, object], schema: str) -> None:
    assert value["schema"] == schema
    assert value["model_version"] == 1


def test_reader_public_dicts_include_schema_versions(tmp_path: Path) -> None:
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("a130"),
        uni_records=[("a130", "", "")],
        ga16_name="GA16HALF",
        ga16_payload=ga16_file(width=8, height=16, start_code=0xA121, glyphs=[bytes([0x80] * 16)]),
    )
    package = open_package(tmp_path)
    results = package.search("gaiji", limit=1)
    hit = results.hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()
    resource = document.resources[0]

    _assert_schema(entry.to_dict(), "lvcore.entry.v1")
    _assert_schema(document.to_dict(), "lvcore.entry_document.v1")
    _assert_schema(results.to_dict(), "lvcore.search_results.v1")
    _assert_schema(hit.to_dict(), "lvcore.search_hit.v1")
    _assert_schema(package.resource_info(resource).to_dict(), "lvcore.resource_location.v1")
    _assert_schema(package.gaiji_info(resource).to_dict(), "lvcore.gaiji_resolution.v1")
    _assert_schema(package.body_source().to_dict(), "lvcore.body_source.v1")


def test_diagnostic_dict_includes_schema_version() -> None:
    diagnostic = Diagnostic(
        severity=Severity.WARNING,
        area=DiagnosticArea.BODY,
        code="entry_range_fallback",
        message="synthetic diagnostic",
    )
    _assert_schema(diagnostic.to_dict(), "lvcore.diagnostic.v1")
