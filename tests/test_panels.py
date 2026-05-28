from pathlib import Path

import pytest

from logovista_tools.cli import _panel_metadata_paths
from logovista_tools.panels import (
    PanelBinParseError,
    PanelCellReference,
    PanelDataReference,
    decode_panel_text,
    iter_panel_cells,
    iter_panel_plist_data_references,
    iter_panel_data_references,
    panel_bin_navigation_rows,
    panel_cell_navigation_row,
    parse_panel_bin,
    resolve_panel_target,
)
from logovista_tools.ssed import SsedInfoElement


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


def test_panel_metadata_paths_honor_exinfo_panelxml(tmp_path: Path) -> None:
    idx = tmp_path / "DICT.IDX"
    idx.write_bytes(b"SSEDINFO")
    (tmp_path / "EXINFO.INI").write_text(
        "[GENERAL]\nPANELXML=BritannicaPanels_win.xml\n",
        encoding="cp932",
    )
    declared = tmp_path / "BritannicaPanels_win.xml"
    declared.write_text("<panels />", encoding="utf-8")

    assert _panel_metadata_paths(tmp_path, idx=idx) == [declared]


def test_iter_panel_cells_extracts_inline_navigation_grid(tmp_path: Path) -> None:
    xml = tmp_path / "Panels.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<panels version="1.0">
  <panel index="01000000" paneltype="menu" count_x="2">
    <title>五十音</title>
    <data>
      <cell action_verb="lved.panel:01010000" ref="01010000">あ</cell>
      <cell></cell>
      <cell action_verb="lved.panel:01020000" ref="01020000">い</cell>
    </data>
  </panel>
</panels>
""",
        encoding="utf-8",
    )

    cells = list(iter_panel_cells(xml))

    assert [cell.label for cell in cells] == ["あ", "い"]
    assert cells[0].panel_index == "01000000"
    assert cells[0].panel_type == "menu"
    assert cells[0].row == 0
    assert cells[0].column == 0
    assert cells[1].cell_index == 2
    assert cells[1].row == 1
    assert cells[1].column == 0


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


def test_resolve_panel_target_classifies_sed_components() -> None:
    elements = [
        SsedInfoElement(0, 0, 0x00, 2, 5, b"", "HONMON.DIC"),
        SsedInfoElement(1, 0, 0x01, 6, 6, b"", "MENU.DIC"),
    ]

    body = resolve_panel_target(3, 0x20, elements)
    menu = resolve_panel_target(6, 0x40, elements)

    assert body is not None
    assert body.component == "HONMON.DIC"
    assert body.kind == "body"
    assert body.relative_offset == 0x820
    assert menu is not None
    assert menu.component == "MENU.DIC"
    assert menu.kind == "menu"
    assert resolve_panel_target(0, 0, elements) is None


def test_panel_bin_navigation_rows_resolve_record_targets(tmp_path: Path) -> None:
    data = (
        (1).to_bytes(4, "little")
        + (4).to_bytes(4, "little")
        + (3).to_bytes(4, "little")
        + (0x20).to_bytes(4, "little")
        + bytes.fromhex("24220000")
    )
    panel = parse_panel_bin(data)
    elements = [SsedInfoElement(0, 0, 0x00, 2, 5, b"", "HONMON.DIC")]
    ref = PanelDataReference(
        panel_index="01010000",
        panel_type="contents",
        data_type="bin",
        filename="Panel/All-A.bin",
        title="あ",
    )

    rows = panel_bin_navigation_rows(ref, panel, resolved_path=tmp_path / "All-A.bin", elements=elements)

    assert len(rows) == 1
    assert rows[0].source == "bin"
    assert rows[0].target_kind == "body"
    assert rows[0].label == "あ"
    assert rows[0].target is not None
    assert rows[0].target.relative_offset == 0x820


def test_panel_cell_navigation_row_targets_panel_ref() -> None:
    row = panel_cell_navigation_row(
        PanelCellReference(
            panel_index="01000000",
            panel_type="menu",
            title="五十音",
            cell_index=0,
            row=0,
            column=0,
            label="あ",
            ref="01010000",
            action_verb="lved.panel:01010000",
        )
    )

    assert row.source == "cell"
    assert row.target_kind == "panel"
    assert row.ref == "01010000"


def test_panel_cell_navigation_row_resolves_lved_addr_action() -> None:
    elements = [SsedInfoElement(1, 0, 0x01, 0x1400, 0x1410, b"", "MENU.DIC")]

    row = panel_cell_navigation_row(
        PanelCellReference(
            panel_index="04000000",
            panel_type="menu",
            title="百人一首",
            cell_index=0,
            row=0,
            column=0,
            label="上の句",
            ref="",
            action_verb="lved.addr0000140b:059c",
        ),
        elements=elements,
    )

    assert row.target_kind == "menu"
    assert row.block == 0x140B
    assert row.offset == 0x059C
    assert row.target is not None
    assert row.target.component == "MENU.DIC"


def test_panel_cell_navigation_row_classifies_plain_label_cell() -> None:
    row = panel_cell_navigation_row(
        PanelCellReference(
            panel_index="04040000",
            panel_type="menu",
            title="最初の文字",
            cell_index=0,
            row=0,
            column=0,
            label="あ",
            ref="",
            action_verb="",
        )
    )

    assert row.target_kind == "label"
