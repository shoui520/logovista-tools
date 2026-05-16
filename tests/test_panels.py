from pathlib import Path

import pytest

from logovista_tools.panels import (
    PanelBinParseError,
    decode_panel_text,
    iter_panel_plist_data_references,
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
    assert panel.actual_record_count == 2
    assert panel.format == "address_label"
    assert panel.text_width == 6
    assert panel.records[0].record_id is None
    assert panel.records[0].block == 0x1234
    assert panel.records[0].offset == 0x56
    assert panel.records[0].text == "あい"
    assert panel.records[1].text == "らり"


def test_parse_panel_bin_rejects_size_mismatch() -> None:
    data = (2).to_bytes(4, "little") + (4).to_bytes(4, "little") + b"\xff" * 7

    with pytest.raises(PanelBinParseError, match="size mismatch"):
        parse_panel_bin(data)


def test_parse_panel_bin_id_prefixed_records() -> None:
    data = (
        (2).to_bytes(4, "little")
        + (4).to_bytes(4, "little")
        + (7).to_bytes(4, "little")
        + (0x1111).to_bytes(4, "little")
        + (0x22).to_bytes(4, "little")
        + bytes.fromhex("24220000")
        + (8).to_bytes(4, "little")
        + (0x1112).to_bytes(4, "little")
        + (0x44).to_bytes(4, "little")
        + bytes.fromhex("24240000")
    )

    panel = parse_panel_bin(data)

    assert panel.format == "id_address_label"
    assert panel.record_stride == 16
    assert panel.records[0].record_id == 7
    assert panel.records[0].text == "あ"
    assert panel.records[1].record_id == 8
    assert panel.records[1].text == "い"


def test_parse_panel_bin_declared_count_mismatch() -> None:
    data = (
        (4).to_bytes(4, "little")
        + (4).to_bytes(4, "little")
        + (0x1200).to_bytes(4, "little")
        + (0x10).to_bytes(4, "little")
        + bytes.fromhex("24220000")
        + (0x1201).to_bytes(4, "little")
        + (0x20).to_bytes(4, "little")
        + bytes.fromhex("24240000")
    )

    panel = parse_panel_bin(data)

    assert panel.format == "address_label_declared_count_mismatch"
    assert panel.declared_record_count == 4
    assert panel.actual_record_count == 2


def test_parse_panel_bin_empty_and_zero_width_records() -> None:
    empty = parse_panel_bin((0).to_bytes(4, "little") + (0).to_bytes(4, "little"))
    assert empty.format == "empty"
    assert empty.records == ()

    address_only = parse_panel_bin(
        (1).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0x33).to_bytes(4, "little")
        + (0x44).to_bytes(4, "little")
    )
    assert address_only.format == "address_empty_label"
    assert address_only.records[0].block == 0x33
    assert address_only.records[0].text == ""


def test_parse_panel_bin_big_endian_utf8_records_without_header() -> None:
    width = 12

    def row(block: int, offset: int, text: str) -> bytes:
        return block.to_bytes(4, "big") + offset.to_bytes(4, "big") + text.encode("utf-8").ljust(width, b"\x00")

    panel = parse_panel_bin(row(2, 0x92, "亜") + row(3, 0x180, "ア"))

    assert panel.format == "big_endian_address_utf8_label_no_header"
    assert panel.text_encoding == "utf-8"
    assert panel.record_stride == 20
    assert panel.records[0].block == 2
    assert panel.records[0].offset == 0x92
    assert panel.records[0].text == "亜"


def test_decode_panel_text_uses_text_stream_controls() -> None:
    assert decode_panel_text(bytes.fromhex("1f0423421f0524220000")) == "Bあ"


def test_decode_panel_text_preserves_gaiji_as_placeholders() -> None:
    assert decode_panel_text(bytes.fromhex("b15824220000")) == "<zB158>あ"


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


def test_iter_panel_data_references_shift_jis_xml(tmp_path: Path) -> None:
    xml = tmp_path / "Panels.xml"
    text = """<?xml version="1.0" encoding="Shift_JIS"?>
<panels version="1.0">
  <information><dictionaryName>x</dictionaryName><creationDate>y</creationDate></information>
  <panel index="01010000" paneltype="contents" datatype="external">
    <title>A</title>
    <data type="bin" filename="Panel\\goju\\All-A.bin" />
  </panel>
</panels>
"""
    xml.write_bytes(text.encode("cp932"))

    refs = list(iter_panel_data_references(xml))

    assert len(refs) == 1
    assert refs[0].filename == "Panel\\goju\\All-A.bin"


def test_iter_panel_plist_data_references(tmp_path: Path) -> None:
    plist = tmp_path / "Panels.plist"
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>panel</key>
  <dict>
    <key>10100000</key>
    <dict>
      <key>paneltype</key><string>contents</string>
      <key>datatype</key><string>external</string>
      <key>title</key><string>A</string>
      <key>data</key>
      <array>
        <dict>
          <key>type</key><string>bin</string>
          <key>filename</key><string>Panel/A.bin</string>
        </dict>
      </array>
    </dict>
  </dict>
</dict>
</plist>
""",
        encoding="utf-8",
    )

    refs = list(iter_panel_plist_data_references(plist))

    assert len(refs) == 1
    assert refs[0].source_format == "plist"
    assert refs[0].panel_index == "10100000"
    assert refs[0].filename == "Panel/A.bin"


def test_iter_mobile_panel_plist_path_references(tmp_path: Path) -> None:
    plist = tmp_path / "menu_.plist"
    plist.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
  <dict>
    <key>item</key><string>top</string>
    <key>child</key>
    <array>
      <dict>
        <key>item</key><string>goju</string>
        <key>path</key><string>goju/All-A</string>
      </dict>
    </array>
  </dict>
</array>
</plist>
""",
        encoding="utf-8",
    )

    refs = list(iter_panel_plist_data_references(plist))

    assert len(refs) == 1
    assert refs[0].source_format == "plist"
    assert refs[0].data_type == "bin"
    assert refs[0].filename == "goju/All-A"
