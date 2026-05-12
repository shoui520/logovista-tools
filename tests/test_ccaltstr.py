import pytest

from logovista_tools.ccaltstr import CcAltStrParseError, parse_ccaltstr
from logovista_tools.gaiji import gaiji_grid_code_for_index


def _record(code: int, value: bytes = b"") -> bytes:
    return code.to_bytes(2, "big") + value + b"\x00" * (60 - len(value))


def test_parse_ccaltstr_half_table() -> None:
    data = (
        b"SDICALTH"
        + (1).to_bytes(2, "little")
        + bytes.fromhex("a121")
        + (2).to_bytes(2, "big")
        + b"\x00\x00"
        + _record(0xA121, b"A")
        + _record(0xA122, b"ae")
    )

    table = parse_ccaltstr(data)

    assert table.kind == "half"
    assert table.version == 1
    assert table.start_code_hex == "a121"
    assert table.record_count == 2
    assert table.records[0].code_hex == "a121"
    assert table.records[0].value == "A"
    assert table.records[1].value_bytes == b"ae"
    assert table.records[1].value == "ae"


def test_parse_ccaltstr_full_table() -> None:
    data = (
        b"SDICALTF"
        + (1).to_bytes(2, "little")
        + bytes.fromhex("b121")
        + (1).to_bytes(2, "big")
        + b"\x00\x00"
        + _record(0xB121, b"(C)")
    )

    table = parse_ccaltstr(data)

    assert table.kind == "full"
    assert table.records[0].code_hex == "b121"
    assert table.records[0].value == "(C)"


def test_parse_ccaltstr_uses_jis_grid_sequence() -> None:
    start = 0xA17E
    data = (
        b"SDICALTH"
        + (1).to_bytes(2, "little")
        + start.to_bytes(2, "big")
        + (2).to_bytes(2, "big")
        + b"\x00\x00"
        + _record(gaiji_grid_code_for_index(start, 0), b"x")
        + _record(gaiji_grid_code_for_index(start, 1), b"y")
    )

    table = parse_ccaltstr(data)

    assert [record.code_hex for record in table.records] == ["a17e", "a221"]


def test_parse_ccaltstr_rejects_size_mismatch() -> None:
    data = (
        b"SDICALTH"
        + (1).to_bytes(2, "little")
        + bytes.fromhex("a121")
        + (2).to_bytes(2, "big")
        + b"\x00\x00"
        + _record(0xA121)
    )

    with pytest.raises(CcAltStrParseError, match="size mismatch"):
        parse_ccaltstr(data)


def test_parse_ccaltstr_rejects_sequence_mismatch() -> None:
    data = (
        b"SDICALTH"
        + (1).to_bytes(2, "little")
        + bytes.fromhex("a121")
        + (2).to_bytes(2, "big")
        + b"\x00\x00"
        + _record(0xA121)
        + _record(0xA123)
    )

    with pytest.raises(CcAltStrParseError, match="sequence mismatch"):
        parse_ccaltstr(data)
