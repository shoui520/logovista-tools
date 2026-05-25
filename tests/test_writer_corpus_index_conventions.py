import os
from pathlib import Path

import pytest

from logovista_tools.gaiji import load_gaiji_profile
from logovista_tools.indexes import scan_index_component_reader
from logovista_tools.ssed import SsedRandomReader, find_case_insensitive, parse_ssedinfo
from logovista_tools.writer import WriterEntry, build_plain_honmon_package, write_plain_package
from logovista_tools.writer_verify import verify_written_package


def _corpus_root() -> Path:
    root_text = os.environ.get("LV_CORPUS_PATH")
    if not root_text:
        pytest.skip("LV_CORPUS_PATH is not set")
    root = Path(root_text)
    if not root.is_dir():
        pytest.skip(f"LV_CORPUS_PATH does not exist: {root}")
    return root


def _idx(root: Path, folder: str, name: str) -> Path:
    path = root / folder / name
    if not path.is_file():
        pytest.skip(f"corpus sample is unavailable: {folder}/{name}")
    return path


def _element(idx: Path, filename: str):
    _title, elements = parse_ssedinfo(idx)
    wanted = filename.upper()
    for element in elements:
        if element.filename.upper() == wanted:
            return element
    pytest.skip(f"{idx.name} has no {filename}")


def _scan_sample(idx: Path, filename: str):
    element = _element(idx, filename)
    source = find_case_insensitive(idx.parent, element.filename)
    if source is None:
        pytest.skip(f"{idx.name} declares missing sample component {filename}")
    profile = load_gaiji_profile(idx)
    return scan_index_component_reader(
        element.filename,
        element.type,
        source,
        element.start,
        gaiji="h-placeholder",
        gaiji_map=profile.map,
        row_limit=50,
    )


def _expanded_title_size(idx: Path, filename: str) -> int:
    element = _element(idx, filename)
    source = find_case_insensitive(idx.parent, element.filename)
    if source is None:
        pytest.skip(f"{idx.name} declares missing sample component {filename}")
    return SsedRandomReader(source).expanded_size


def test_real_corpus_samples_exercise_writer_index_conventions() -> None:
    root = _corpus_root()

    kqnewje5 = _idx(root, "_DCT_KQNEWJE5", "KQNEWJE5.IDX")
    kwindex = _scan_sample(kqnewje5, "KWINDEX.DIC")
    assert kwindex.unknown_leaf_bytes == 0
    assert kwindex.search_groups > 0
    assert kwindex.leaf_rows > kwindex.search_groups
    assert _expanded_title_size(kqnewje5, "KWTITLE.DIC") > 0

    ejje100 = _idx(root, "_DCT_EJJE100", "EJJE100.IDX")
    fhindex = _scan_sample(ejje100, "FHINDEX.DIC")
    fkindex = _scan_sample(ejje100, "FKINDEX.DIC")
    assert fhindex.unknown_leaf_bytes == 0
    assert fkindex.unknown_leaf_bytes == 0
    assert fhindex.search_groups == 0
    assert fkindex.search_groups > 0
    assert _expanded_title_size(ejje100, "FHTITLE.DIC") > _expanded_title_size(ejje100, "FKTITLE.DIC")


def test_writer_alias_heavy_package_keeps_exact_and_broad_indexes_separate(tmp_path: Path) -> None:
    package = build_plain_honmon_package(
        dict_id="IDXCONV",
        title="Index Convention Fixture",
        entries=[
            WriterEntry("alpha", "first", search_keys=("alpha", "alpha alias", "alpha synonym")),
            WriterEntry("beta", "second", search_keys=("beta", "beta alias")),
            WriterEntry("gamma", "third", search_keys=("gamma",)),
        ],
        compression="literal",
    )
    out = tmp_path / "IDXCONV"
    write_plain_package(package, out)

    report = verify_written_package(out)

    assert report.ok
    assert report.metrics["indexes"]["FHINDEX.DIC"]["search_groups"] == 0
    assert report.metrics["indexes"]["BHINDEX.DIC"]["search_groups"] == 0
    assert report.metrics["indexes"]["FKINDEX.DIC"]["search_groups"] == 3
    assert report.metrics["indexes"]["BKINDEX.DIC"]["search_groups"] == 3
    assert report.metrics["indexes"]["FHINDEX.DIC"]["leaf_rows"] == 6
    assert report.metrics["indexes"]["BHINDEX.DIC"]["leaf_rows"] == 6
    assert report.metrics["indexes"]["FKINDEX.DIC"]["leaf_rows"] == 3
    assert report.metrics["indexes"]["BKINDEX.DIC"]["leaf_rows"] == 3
