from pathlib import Path

from logovista_tools.ssed import expand_sseddata_file, parse_sseddata_header, parse_ssedinfo
from logovista_tools.writer import IndexPointer, IndexTarget, WriterEntry, build_plain_honmon_package, encode_simple_index_pages, encode_sseddata, encode_tagged_index_pages, rows_to_ga16_glyph, write_plain_package
from logovista_tools.writer_verify import _is_ff_sentinel, _padded_key, index_page_infos, verify_written_package


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


def test_tagged_leaf_high_key_comes_from_group_header_not_target_key() -> None:
    body = IndexPointer(100, 0)
    title = IndexPointer(200, 0)
    targets = [
        IndexTarget(key=f"key{i:04d}", target_key=f"zz-target-{i:04d}", body=body, title=title)
        for i in range(180)
    ]
    data = encode_tagged_index_pages(targets, start_block=5000)
    pages = index_page_infos(data, 0x90, 5000)

    branch_pages = [page for page in pages.values() if not page.leaf]
    assert branch_pages
    for page in branch_pages:
        for row in page.branch_rows:
            if _is_ff_sentinel(row.key):
                continue
            child = pages[row.child_page_index]
            assert row.key == _padded_key(child.high_key, len(row.key))


def test_index_writer_keeps_branch_prefix_groups_searchable() -> None:
    body = IndexPointer(100, 0)
    title = IndexPointer(200, 0)
    prefix = "M" * 18
    targets = [
        *(IndexTarget(key=f"B{i:04d}{'B' * 17}", body=body, title=title) for i in range(33)),
        *(IndexTarget(key=f"{prefix}{i:04d}", body=body, title=title) for i in range(8)),
        *(IndexTarget(key=f"Z{i:04d}{'Z' * 17}", body=body, title=title) for i in range(33)),
    ]

    for data, component_type in (
        (encode_simple_index_pages(targets, start_block=5000), 0x91),
        (
            encode_tagged_index_pages(
                [IndexTarget(key=row.key, target_key=row.key, body=row.body, title=row.title) for row in targets],
                start_block=6000,
            ),
            0x90,
        ),
    ):
        pages = index_page_infos(data, component_type, 5000 if component_type == 0x91 else 6000)
        seen: dict[bytes, int] = {}
        for page in pages.values():
            if page.leaf:
                for key in page.lookup_keys:
                    seen.setdefault(key, page.page_index)
        for key, expected_page in seen.items():
            start_block = 5000 if component_type == 0x91 else 6000
            # Import lazily to keep this assertion near the fixture.
            from logovista_tools.writer_verify import traverse_index_key

            assert traverse_index_key(data, start_block, key, pages) == expected_page


def test_index_writer_keeps_parent_branch_prefix_groups_searchable() -> None:
    body = IndexPointer(100, 0)
    title = IndexPointer(200, 0)
    targets = [
        *(IndexTarget(key=f"B{i:04d}{'B' * 20}", body=body, title=title) for i in range(2100)),
        *(IndexTarget(key=f"{'M' * 18}{i:04d}", body=body, title=title) for i in range(120)),
        *(IndexTarget(key=f"Z{i:04d}{'Z' * 20}", body=body, title=title) for i in range(2100)),
    ]

    data = encode_simple_index_pages(targets, start_block=7000)
    pages = index_page_infos(data, 0x91, 7000)
    assert sum(1 for page in pages.values() if not page.leaf) > 1

    seen: dict[bytes, int] = {}
    for page in pages.values():
        if page.leaf:
            for key in page.lookup_keys:
                seen.setdefault(key, page.page_index)

    from logovista_tools.writer_verify import traverse_index_key

    for key, expected_page in seen.items():
        assert traverse_index_key(data, 7000, key, pages) == expected_page
