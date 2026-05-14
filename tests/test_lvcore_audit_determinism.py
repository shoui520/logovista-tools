from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path

from test_lvcore_experimental import make_synthetic_package

from lvcore_audit.cli import main as audit_main


def _audit_package(path: Path) -> str:
    output = io.StringIO()
    with redirect_stdout(output):
        code = audit_main(["package", str(path), "--sample-entries", "1", "--sample-search-hits", "1"])
    assert code == 0
    text = output.getvalue()
    assert str(path) not in text
    return text


def test_package_audit_is_byte_identical_across_runs(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)

    assert _audit_package(tmp_path) == _audit_package(tmp_path)


def test_package_audit_is_stable_across_case_only_renames(tmp_path: Path) -> None:
    make_synthetic_package(tmp_path)
    first_report = _audit_package(tmp_path)
    (tmp_path / "HONMON.DIC").rename(tmp_path / "honmon.dic")
    (tmp_path / "FHTITLE.DIC").rename(tmp_path / "fhtitle.dic")
    (tmp_path / "FHINDEX.DIC").rename(tmp_path / "fhindex.dic")

    assert first_report == _audit_package(tmp_path)


def test_package_audit_sorts_filesystem_order(tmp_path: Path, monkeypatch) -> None:
    make_synthetic_package(tmp_path)
    original_iterdir = Path.iterdir

    def reversed_iterdir(path: Path):
        return iter(sorted(original_iterdir(path), key=lambda item: item.name, reverse=True))

    normal = _audit_package(tmp_path)
    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)
    reversed_order = _audit_package(tmp_path)

    assert normal == reversed_order
