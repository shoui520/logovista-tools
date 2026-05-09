import json
from argparse import Namespace
from pathlib import Path

from logovista_tools.decoded_model import dump_package_model_for_path
from logovista_tools.lved import (
    derive_lved_sqlcipher_key,
    infer_lved_dict_code,
    inspect_lved_roots,
    is_lved_payload_name,
    shannon_entropy,
)


def test_lved_payload_name_detection() -> None:
    assert is_lved_payload_name(Path("main.data"))
    assert is_lved_payload_name(Path("OXFPEU4.dbc"))
    assert not is_lved_payload_name(Path("main.data:Zone.Identifier"))
    assert not is_lved_payload_name(Path("HONMON.DIC"))


def test_infer_lved_dict_code() -> None:
    assert infer_lved_dict_code(Path("_DCT_OXFPEU4/main.data")) == "OXFPEU4"
    assert infer_lved_dict_code(Path("DIC/OXFPEU4/main.data")) == "OXFPEU4"
    assert infer_lved_dict_code(Path("OXFPEU4.dbc")) == "OXFPEU4"


def test_lved_key_derivation_metadata_shape() -> None:
    key = derive_lved_sqlcipher_key(750, "OXFPEU4")

    assert key.startswith("jlasgoiahoiampvsjhosDHfopj")
    assert key.endswith("o4" + str(750 * 19286))


def test_shannon_entropy_bounds() -> None:
    assert shannon_entropy(b"") == 0.0
    assert shannon_entropy(b"\x00" * 256) == 0.0
    assert shannon_entropy(bytes(range(256))) == 8.0


def test_memory_dump_candidates_are_not_emitted(tmp_path) -> None:
    dump = tmp_path / "viewer.dmp"
    candidate = "jlasgoiahoiampvsjhosDHfopjxx123456"
    dump.write_text(f"noise {candidate} noise", encoding="utf-16le")

    report = inspect_lved_roots([], memory_dump=dump)
    rendered = json.dumps(report)

    assert report["memory_dump"]["candidate_keys"] == 1
    assert candidate not in rendered


def test_dump_package_model_classifies_lved_as_deferred_family(tmp_path) -> None:
    package = tmp_path / "_DCT_TESTLVED"
    package.mkdir()
    # 4096 bytes with high entropy but no SQLite header is enough for LVED
    # classification; validation/decryption remains a separate command path.
    (package / "main.data").write_bytes(bytes(range(256)) * 16)
    (package / "main.data:Zone.Identifier").write_text("noise", encoding="utf-8")
    (package / "res").mkdir()
    (package / "res" / "CID00001_t.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (package / "res" / "CID00001_t.png:Zone.Identifier").write_text("noise", encoding="utf-8")

    model = dump_package_model_for_path(package, Namespace(dict=None, sample_limit=20))

    assert model["classification"]["package_family"] == "lved_sqlcipher"
    assert model["classification"]["status"] == "deferred"
    assert model["classification"]["body_source_hint"] == "lved_sqlcipher"
    assert model["readiness"]["requirements"]["deferred_package_family"] is True
    assert model["writer_readiness"]["read_existing"] == "gray"
    assert model["writer_readiness"]["export_existing"] == "gray"
    assert model["writer_readiness"]["author_core_ssed_v0"] == "gray"
    assert model["writer_readiness"]["lossless_repack_existing"] == "gray"
    assert model["writer_readiness"]["legacy_ssed_subset"] == "gray"
    assert model["writer_readiness"]["legacy_ssed_subset_blockers"] == ["non_ssed_package_family"]
    assert model["families"]["lved"]["payloads"][0]["classification"] == "sqlcipher_lved_candidate"
    assert model["resources"]["static_sidecars"]["extension_counts"].get(".identifier") is None


def test_dump_package_model_classifies_multiview_as_deferred_family(tmp_path) -> None:
    package = tmp_path / "_DCT_TESTMULTI"
    package.mkdir()
    (package / "menuData.xml").write_text("<menu><item href=\"about\" label=\"About\" /></menu>", encoding="utf-8")
    (package / "blvbat").write_bytes(b"not sqlite")
    (package / "blvbat:Zone.Identifier").write_text("noise", encoding="utf-8")

    model = dump_package_model_for_path(package, Namespace(dict=None, sample_limit=20))

    assert model["classification"]["package_family"] == "multiview_sqlite"
    assert model["classification"]["status"] == "deferred"
    assert model["classification"]["body_source_hint"] == "multiview_sqlite"
    assert model["readiness"]["requirements"]["deferred_package_family"] is True
    assert model["writer_readiness"]["lossless_repack_existing"] == "gray"
    assert model["writer_readiness"]["lossless_repacker"] == "gray"
    assert model["families"]["multiview"]["payloads"][0]["name"] == "blvbat"
    assert len(model["families"]["multiview"]["payloads"]) == 1
