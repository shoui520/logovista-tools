from pathlib import Path

from logovista_tools.windows import (
    classify_hc_renderer_file,
    expected_numeric_index_for_hc_code,
    hc_code_from_name,
    parse_pe_summary,
)


def test_hc_code_and_expected_numeric_index() -> None:
    assert hc_code_from_name(Path("HC014F.dll")) == "014F"
    assert hc_code_from_name(Path("hc02d0.DLL")) == "02D0"
    assert hc_code_from_name(Path("KENCOLLO.IDX")) is None
    assert expected_numeric_index_for_hc_code("014F") == "0000014F.idx"


def test_hc_classification_uses_exinfo_and_numeric_sidecar(tmp_path) -> None:
    hc = tmp_path / "HC014F.dll"
    hc.write_bytes(b"not a PE file, but it mentions epwing2HtmlBodydata and vlpljblF")
    (tmp_path / "EXINFO.INI").write_text("[GENERAL]\nHTMLDLL=HC014F.dll\n", encoding="cp932")
    (tmp_path / "0000014F.idx").write_text("00000000\t00000000\troot\n", encoding="cp932")
    (tmp_path / "vlpljblF").write_bytes(b"sidecar")

    row = classify_hc_renderer_file(hc, compute_hash=False)

    assert row.code == "014F"
    assert row.exinfo_declares_this is True
    assert row.expected_numeric_index_present is True
    assert row.vlpljbl_siblings == ("vlpljblF",)
    assert row.pe.kind == "unknown"
    assert row.sha256 is None


def test_parse_pe_summary_reports_non_pe_kind(tmp_path) -> None:
    path = tmp_path / "HC0001.dll"
    path.write_bytes(b"SSEDDATA")

    summary = parse_pe_summary(path)

    assert summary.kind == "sseddata"
    assert summary.exports == ()
