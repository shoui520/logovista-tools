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


def test_observed_extended_link_controls_are_known_spans() -> None:
    decoded = decode_lossless_spans(
        b"\x1f\x44\x00\x01\x00\x00\x00\x71\x00\x00\x04\x88"
        b"\x1f\x64\x00\x00\x11\x00\x00\x00"
    )

    assert decoded.stats["unknown_controls"] == 0
    assert decoded.control_ops == {"44": 1, "64": 1}
    assert [span.tag for span in decoded.spans] == ["link", "link"]
    assert decoded.stats["links"] == 1


def test_observed_layout_controls_keep_semantic_tags() -> None:
    decoded = decode_lossless_spans(b"\x1f\x1a\x00\x06\x1f\x1c\x20\x00")

    assert decoded.stats["unknown_controls"] == 0
    assert decoded.control_ops == {"1a": 1, "1c": 1}
    assert [(span.op, span.tag, span.payload_hex) for span in decoded.spans] == [
        ("1a", "tab_column", "0006"),
        ("1c", "media_layout", "2000"),
    ]


def test_hc_renderer_controls_are_known_spans() -> None:
    decoded = decode_lossless_spans(
        b"\x1f\x3c" + bytes(range(18))
        + b"\x1f\x36" + bytes(range(12))
        + b"\x1f\x4b" + bytes(range(6))
        + b"\x1f\x4c\x00\x01"
    )

    assert decoded.stats["unknown_controls"] == 0
    assert decoded.stats["media"] == 1
    assert decoded.control_ops == {"3c": 1, "36": 1, "4b": 1, "4c": 1}
    assert [(span.kind, span.op, span.tag) for span in decoded.spans] == [
        ("media_ref", "3c", None),
        ("control", "36", "renderer_skip"),
        ("control", "4b", "renderer_skip"),
        ("control", "4c", "renderer_skip"),
    ]


def test_cp932_extension_jis_cells_decode_as_text() -> None:
    decoded = decode_lossless_spans(b"\x2d\x21\x2d\x54\x2c\x29\x23\x3f")

    assert decoded.stats["invalid_jis_pairs"] == 0
    assert [span.normalized for span in decoded.spans] == ["①", "㎏", "❾", "◦"]


def test_bare_lf_is_a_legacy_break_byte() -> None:
    decoded = decode_lossless_spans(b"A\x0aB")

    assert [span.kind for span in decoded.spans] == ["ascii", "break", "ascii"]
    assert decoded.stats["unknown_bytes"] == 0
    assert decoded.stats["breaks"] == 1


def test_lossless_spans_strict_mode_raises() -> None:
    with pytest.raises(LosslessDecodeError):
        decode_lossless_spans(b"\x1f\x99", mode="strict")


def test_lossless_spans_can_count_padding_without_emitting_it() -> None:
    decoded = decode_lossless_spans(b"\x00\x00A", include_padding=False)

    assert [span.kind for span in decoded.spans] == ["ascii"]
    assert decoded.stats["padding_bytes"] == 2
    assert decoded.stats["bytes_covered"] == 3


def test_lossless_spans_can_collect_stats_without_spans() -> None:
    decoded = decode_lossless_spans(
        b"\x1f\x09\x00\x01\x00A\x1f\x99",
        collect_spans=False,
        max_issues=0,
    )

    assert decoded.spans == []
    assert decoded.issues == []
    assert decoded.issue_counts == {"unknown_control": 1}
    assert decoded.stats["bytes_covered"] == decoded.stats["bytes_total"]
    assert decoded.stats["unknown_controls"] == 1
