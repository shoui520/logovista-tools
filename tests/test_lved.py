import json
from pathlib import Path

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
