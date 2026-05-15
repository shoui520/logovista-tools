from pathlib import Path

from logovista_tools.opcode_atlas import merge_opcode_rows, scan_text_stream
from logovista_tools.profiles import ProfileTarget
from logovista_tools.ssed import SsedInfoElement


def test_opcode_atlas_records_payload_lengths_and_unknowns() -> None:
    target = ProfileTarget(
        dict_id="TEST",
        idx=Path("TEST.IDX"),
        title="Test",
        elements=[],
    )
    element = SsedInfoElement(
        index=0,
        multi=0,
        type=0,
        start=1,
        end=1,
        data=b"\x00\x00\x00\x00",
        filename="HONMON.DIC",
    )
    payload = bytes.fromhex("00010000000231930000000231991579")
    data = b"\x1f\x09\x00\x01\x24\x22\x1f\x4a" + payload + b"\x24\x24\x1f\x6a\x1f\x99"

    report = scan_text_stream(
        target=target,
        roots=[Path(".")],
        element=element,
        data=data,
        role="honmon",
        gaiji_map={},
        max_examples_per_opcode=4,
        context_bytes=16,
    )

    assert report["opcodes"]["09"]["payload_lengths"] == {2: 1}
    assert report["opcodes"]["4a"]["payload_lengths"] == {16: 1}
    assert report["opcodes"]["6a"]["payload_lengths"] == {0: 1}
    assert report["unknowns"] == {"99": 1}


def test_opcode_atlas_uses_hc_variable_control_lengths() -> None:
    target = ProfileTarget(
        dict_id="TEST",
        idx=Path("TEST.IDX"),
        title="Test",
        elements=[],
    )
    element = SsedInfoElement(
        index=0,
        multi=0,
        type=0,
        start=1,
        end=1,
        data=b"\x00\x00\x00\x00",
        filename="HONMON.DIC",
    )
    data = b"\x1f\x4a\x00\x00" + (b"\xaa" * 12) + b"\x1f\x4f\x1f\x6f" + (b"\xbb" * 46)

    report = scan_text_stream(
        target=target,
        roots=[Path(".")],
        element=element,
        data=data,
        role="honmon",
        gaiji_map={},
        max_examples_per_opcode=4,
        context_bytes=16,
    )

    assert report["opcodes"]["4a"]["payload_lengths"] == {14: 1}
    assert report["opcodes"]["4f"]["payload_lengths"] == {48: 1}
    assert report["unknowns"] == {}


def test_opcode_atlas_merge_classifies_known_and_unknown_controls() -> None:
    merged = merge_opcode_rows(
        [
            {
                "components_scanned": 1,
                "bytes_scanned": 4,
                "unknowns": {"99": 1},
                "truncated": [],
                "opcodes": {
                    "09": {
                        "count": 1,
                        "payload_lengths": {2: 1},
                        "payload_values": {"0001": 1},
                        "payload_prefixes": {"0001": 1},
                        "roles": {"honmon": 1},
                        "component_types": {"00": 1},
                        "filenames": {"HONMON.DIC": 1},
                        "dictionaries": {"TEST": 1},
                        "previous_ops": {},
                        "next_ops": {},
                        "examples": [],
                    },
                    "99": {
                        "count": 1,
                        "payload_lengths": {0: 1},
                        "payload_values": {},
                        "payload_prefixes": {},
                        "roles": {"honmon": 1},
                        "component_types": {"00": 1},
                        "filenames": {"HONMON.DIC": 1},
                        "dictionaries": {"TEST": 1},
                        "previous_ops": {},
                        "next_ops": {},
                        "examples": [],
                    },
                },
            }
        ]
    )

    by_op = {row["op"]: row for row in merged["opcodes"]}
    assert by_op["09"]["classification"]["label"] == "section/entry marker"
    assert by_op["99"]["classification"]["family"] == "unknown"
    assert merged["unknown_control_ops"] == {"99": 1}
