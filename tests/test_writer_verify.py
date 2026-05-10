from pathlib import Path

from logovista_tools.ssed import expand_sseddata_file, parse_sseddata_header, parse_ssedinfo
from logovista_tools.writer import WriterEntry, build_plain_honmon_package, encode_sseddata, rows_to_ga16_glyph, write_plain_package
from logovista_tools.writer_verify import verify_written_package


def _write_branchy_package(path: Path) -> Path:
    entries = [
        WriterEntry(
            headword=f"alpha{i:04d}",
            body=f"definition {i}",
            search_keys=(f"alpha{i:04d}", f"alias{i:04d}"),
        )
        for i in range(80)
    ]
    entries.append(WriterEntry("biang", "rare 𰻞", search_keys=("biang", "𰻞")))
    package = build_plain_honmon_package(
        dict_id="VERIFY",
        title="Verifier Test",
        entries=entries,
        glyph_renderer=lambda _text, width, height, _space: rows_to_ga16_glyph(
            ["#" * width if y in {0, height - 1} else "#" + "." * (width - 2) + "#" for y in range(height)],
            width=width,
            height=height,
        ),
    )
    out = path / "VERIFY"
    write_plain_package(package, out)
    return out


def test_verify_written_package_accepts_writer_output(tmp_path: Path) -> None:
    package_dir = _write_branchy_package(tmp_path)

    report = verify_written_package(package_dir)

    assert report.ok
    assert report.errors == 0
    assert report.metrics["entries"] == 81
    assert report.metrics["gaiji"]["uni_records"] >= 1
    assert report.metrics["gaiji"]["uni_codes_missing_ga16"] == 0
    assert report.metrics["indexes"]["FHINDEX.DIC"]["traversal_checks"] > 0


def test_verify_written_package_rejects_wrong_branch_upper_bound(tmp_path: Path) -> None:
    package_dir = _write_branchy_package(tmp_path)
    title, elements = parse_ssedinfo(package_dir / "VERIFY.IDX")
    fh = next(element for element in elements if element.filename == "FHINDEX.DIC")
    header = parse_sseddata_header(package_dir / "FHINDEX.DIC")
    expanded = bytearray(expand_sseddata_file(package_dir / "FHINDEX.DIC"))

    # Replace the first root upper-bound key with zeros. The page remains
    # syntactically parseable, but traversal no longer matches LogoVista's
    # upper-bound branch semantics.
    word = int.from_bytes(expanded[:2], "big")
    slot = (word & 0xFF) + 4
    expanded[4 : 4 + slot - 4] = bytes(slot - 4)
    (package_dir / "FHINDEX.DIC").write_bytes(encode_sseddata(bytes(expanded), start_block=int(header["start_block"]), kind=fh.type))

    report = verify_written_package(package_dir)

    assert not report.ok
    assert any(issue["code"] == "branch_upper_bound_mismatch" for issue in report.issues)
    assert any(issue["code"] == "index_traversal_mismatch" for issue in report.issues)


def test_verify_written_package_rejects_missing_final_sentinel(tmp_path: Path) -> None:
    package_dir = _write_branchy_package(tmp_path)
    title, elements = parse_ssedinfo(package_dir / "VERIFY.IDX")
    fh = next(element for element in elements if element.filename == "FHINDEX.DIC")
    header = parse_sseddata_header(package_dir / "FHINDEX.DIC")
    expanded = bytearray(expand_sseddata_file(package_dir / "FHINDEX.DIC"))

    word = int.from_bytes(expanded[:2], "big")
    count = int.from_bytes(expanded[2:4], "big")
    slot = (word & 0xFF) + 4
    last_key_start = 4 + (count - 1) * slot
    expanded[last_key_start] = 0
    (package_dir / "FHINDEX.DIC").write_bytes(encode_sseddata(bytes(expanded), start_block=int(header["start_block"]), kind=fh.type))

    report = verify_written_package(package_dir)

    assert not report.ok
    assert any(issue["code"] == "missing_ff_sentinel" for issue in report.issues)
