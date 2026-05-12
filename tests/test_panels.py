from pathlib import Path

import pytest

from logovista_tools.panels import (
    PanelBinParseError,
    decode_panel_text,
    iter_panel_data_references,
    parse_panel_bin,
)


def test_parse_panel_bin_fixed_records() -> None:
    data = (
        (2).to_bytes(4, "little")
        + (6).to_bytes(4, "little")
        + (0x1234).to_bytes(4, "little")
        + (0x0056).to_bytes(4, "little")
        + bytes.fromhex("242224240000")
        + (0x1235).to_bytes(4, "little")
        + (0x0078).to_bytes(4, "little")
        + bytes.fromhex("2469246a0000")
    )

    panel = parse_panel_bin(data)

    assert panel.record_count == 2
    assert panel.text_width == 6
    assert panel.records[0].block == 0x1234
    assert panel.records[0].offset == 0x56
    assert panel.records[0].text == "あい"
    assert panel.records[1].text == "らり"


def test_parse_panel_bin_rejects_size_mismatch() -> None:
    data = (2).to_bytes(4, "little") + (4).to_bytes(4, "little") + b"\x00" * 8

    with pytest.raises(PanelBinParseError, match="size mismatch"):
        parse_panel_bin(data)


def test_decode_panel_text_preserves_non_jis_pairs_as_placeholders() -> None:
    assert decode_panel_text(bytes.fromhex("b15824220000")) == "<b158>あ"


def test_iter_panel_data_references(tmp_path: Path) -> None:
    xml = tmp_path / "Panels.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<panels version="1.0">
  <information><dictionaryName>x</dictionaryName><creationDate>y</creationDate></information>
  <panel index="01010000" paneltype="contents" datatype="external">
    <title>A</title>
    <data type="bin" filename="Panel\\goju\\All-A.bin" />
  </panel>
</panels>
""",
        encoding="utf-8",
    )

    refs = list(iter_panel_data_references(xml))

    assert len(refs) == 1
    assert refs[0].panel_index == "01010000"
    assert refs[0].panel_type == "contents"
    assert refs[0].data_type == "bin"
    assert refs[0].filename == "Panel\\goju\\All-A.bin"

