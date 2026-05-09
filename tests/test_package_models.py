from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from logovista_tools.cli import cmd_dump_package_models
from logovista_tools.decoded_model import discover_package_model_targets


def make_corpus_args(root: Path, out_dir: Path, **overrides) -> Namespace:
    values = {
        "root": [root],
        "out_dir": out_dir,
        "dict": None,
        "family": None,
        "resume": False,
        "progress": False,
        "allow_failures": False,
        "parse_mode": "forensic",
        "entry_limit": 2,
        "profile_max_slices": 2,
        "title_limit": 5,
        "index_limit": 5,
        "menu_limit": 5,
        "media_limit": 5,
        "sample_limit": 5,
        "sidecar_sample_limit": 5,
        "max_issue_samples": 10,
        "include_spans": True,
        "include_raw": False,
        "include_padding_spans": False,
        "include_internal_indexes": False,
        "deep_sidecars": False,
        "full_profile_indexes": False,
        "full_entry_boundaries": False,
        "skip_row_models": False,
        "gaiji_readiness": False,
        "renderer_sidecar_gaiji": False,
        "renderer_inference_limit": None,
        "include_playback_rows": False,
        "no_hash": True,
        "json": False,
        "jobs": 2,
    }
    values.update(overrides)
    return Namespace(**values)


def make_lved_package(root: Path, name: str) -> Path:
    package = root / f"_DCT_{name}"
    package.mkdir()
    (package / "main.data").write_bytes(bytes(range(256)) * 16)
    (package / "main.data:Zone.Identifier").write_text("noise", encoding="utf-8")
    return package


def make_multiview_package(root: Path, name: str) -> Path:
    package = root / f"_DCT_{name}"
    package.mkdir()
    (package / "menuData.xml").write_text("<menu><item href=\"about\" label=\"About\" /></menu>", encoding="utf-8")
    (package / "blvbat").write_bytes(b"not sqlite")
    return package


def test_discover_package_model_targets_classifies_deferred_families(tmp_path: Path) -> None:
    make_lved_package(tmp_path, "TESTLVED")
    make_multiview_package(tmp_path, "TESTMULTI")

    targets = discover_package_model_targets([tmp_path])
    by_id = {target.dict_id: target for target in targets}

    assert by_id["TESTLVED"].family_hint == "lved_sqlcipher"
    assert by_id["TESTMULTI"].family_hint == "multiview_sqlite"


def test_dump_package_models_writes_summary_failures_and_resumes(tmp_path: Path) -> None:
    make_lved_package(tmp_path, "TESTLVED")
    make_multiview_package(tmp_path, "TESTMULTI")
    out_dir = tmp_path / "reports"

    result = cmd_dump_package_models(make_corpus_args(tmp_path, out_dir))

    assert result == 0
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    failures = json.loads((out_dir / "failures.json").read_text(encoding="utf-8"))
    assert summary["total"] == 2
    assert summary["status_counts"] == {"ok": 2}
    assert summary["family_counts"] == {"lved_sqlcipher": 1, "multiview_sqlite": 1}
    assert failures["failures"] == []
    model_paths = [Path(row["model_path"]) for row in summary["rows"]]
    assert all(path.exists() for path in model_paths)

    resumed = cmd_dump_package_models(make_corpus_args(tmp_path, out_dir, resume=True))

    assert resumed == 0
    resumed_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert resumed_summary["status_counts"] == {"skipped": 2}
