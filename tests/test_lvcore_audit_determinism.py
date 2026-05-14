from __future__ import annotations

from pathlib import Path

from test_lvcore_experimental import make_synthetic_package

from lvcore import open_package
from lvcore_audit.determinism import canonical_json
from lvcore_audit.package_validation import validate_package


def _audit_package(path: Path) -> str:
    report = validate_package(open_package(path), sample_entries=1, sample_search_hits=1)
    return canonical_json(report)


def test_package_audit_is_byte_identical_across_runs(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)

    assert _audit_package(tmp_path) == _audit_package(tmp_path)


def test_package_audit_is_stable_across_case_only_renames(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    make_synthetic_package(first)
    make_synthetic_package(second)
    (second / "HONMON.DIC").rename(second / "honmon.dic")
    (second / "FHTITLE.DIC").rename(second / "fhtitle.dic")
    (second / "FHINDEX.DIC").rename(second / "fhindex.dic")

    first_report = validate_package(open_package(first), sample_entries=1, sample_search_hits=1)
    second_report = validate_package(open_package(second), sample_entries=1, sample_search_hits=1)
    for report in (first_report, second_report):
        report["package"]["root"] = "<package>"
        report["package"]["idx_path"] = "<package>/TEST.IDX"

    assert canonical_json(first_report) == canonical_json(second_report)


def test_package_audit_sorts_filesystem_order(tmp_path: Path, monkeypatch) -> None:
    make_synthetic_package(tmp_path)
    original_iterdir = Path.iterdir

    def reversed_iterdir(path: Path):
        return iter(sorted(original_iterdir(path), key=lambda item: item.name, reverse=True))

    normal = _audit_package(tmp_path)
    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reversed_order = _audit_package(tmp_path)

    assert normal == reversed_order
