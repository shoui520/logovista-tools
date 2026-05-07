import pytest

from logovista_tools.spans import LosslessDecodeError, decode_lossless_spans


def test_lossless_spans_preserve_offsets_and_raw_bytes() -> None:
    data = b"\x1f\x09\x00\x01" + b"\x23\x41" + b"\xa1\x26" + b"\x1f\x0a"
    decoded = decode_lossless_spans(data, gaiji_map={"a126": "é"})

    assert [span.kind for span in decoded.spans] == ["section", "text", "gaiji", "break"]
    assert decoded.spans[0].start == 0
    assert decoded.spans[0].end == 4
    assert decoded.spans[0].payload_hex == "0001"
    assert decoded.spans[1].raw_hex == "2341"
    assert decoded.spans[2].code == "a126"
    assert decoded.spans[2].resolved == "é"
    assert decoded.stats["bytes_covered"] == len(data)
    assert decoded.control_ops == {"09": 1, "0a": 1}


def test_lossless_spans_measure_unknown_controls() -> None:
    decoded = decode_lossless_spans(b"\x1f\x99ABC")

    assert decoded.stats["unknown_controls"] == 1
    assert decoded.control_ops == {"99": 1}
    assert decoded.unknown_control_ops == {"99": 1}
    assert decoded.issue_counts == {"unknown_control": 1}
    assert decoded.spans[0].kind == "unknown_control"


def test_observed_literal_and_url_controls_are_known_spans() -> None:
    decoded = decode_lossless_spans(b"\x1f\x0bA\x1f\x0c\x1f\x3bB\x1f\x5b")

    assert decoded.stats["unknown_controls"] == 0
    assert decoded.control_ops == {"0b": 1, "0c": 1, "3b": 1, "5b": 1}
    assert [span.tag for span in decoded.spans if span.kind == "control"] == [
        "literal",
        "literal",
        "url",
        "url",
    ]
    assert decoded.stats["links"] == 1


def test_lossless_spans_strict_mode_raises() -> None:
    with pytest.raises(LosslessDecodeError):
        decode_lossless_spans(b"\x1f\x99", mode="strict")


def test_lossless_spans_can_count_padding_without_emitting_it() -> None:
    decoded = decode_lossless_spans(b"\x00\x00A", include_padding=False)

    assert [span.kind for span in decoded.spans] == ["ascii"]
    assert decoded.stats["padding_bytes"] == 2
    assert decoded.stats["bytes_covered"] == 3
