"""Panel XML/plist/BIN helpers for observed LogoVista Panel assets."""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .entries import decode_tokens, tokens_to_text
from .navigation import NavigationTarget as PanelTarget
from .navigation import parse_lvaddr_href, resolve_component_target
from .ssed import SsedInfoElement


@dataclass(frozen=True)
class PanelBinRecord:
    """One fixed-width Panel BIN cell record."""

    index: int
    record_id: int | None
    block: int
    offset: int
    text_bytes: bytes
    text: str


@dataclass(frozen=True)
class PanelBin:
    """Decoded fixed-record Panel BIN payload."""

    declared_record_count: int
    actual_record_count: int
    text_width: int
    format: str
    record_stride: int
    text_encoding: str
    records: tuple[PanelBinRecord, ...]

    @property
    def record_count(self) -> int:
        """Backward-compatible alias for callers that expect the header count."""

        return self.declared_record_count


@dataclass(frozen=True)
class PanelDataReference:
    """One external ``<data type="bin" filename="...">`` reference."""

    panel_index: str
    panel_type: str
    data_type: str
    filename: str
    title: str
    source_format: str = "xml"


@dataclass(frozen=True)
class PanelCellReference:
    """One inline Panel cell that points to another panel or action."""

    panel_index: str
    panel_type: str
    title: str
    cell_index: int
    row: int | None
    column: int | None
    label: str
    ref: str
    action_verb: str
    source_format: str = "xml"
    attributes: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "panel_index": self.panel_index,
            "panel_type": self.panel_type,
            "title": self.title,
            "cell_index": self.cell_index,
            "row": self.row,
            "column": self.column,
            "label": self.label,
            "ref": self.ref,
            "action_verb": self.action_verb,
            "source_format": self.source_format,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class PanelNavigationRow:
    """One app-facing Panel navigation item.

    External BIN records point at SSED block/offset destinations. Inline cells
    are menu-like nodes that point to another panel id.
    """

    source: str
    panel_index: str
    panel_type: str
    panel_title: str
    label: str
    source_format: str
    row_index: int | None = None
    cell_index: int | None = None
    record_id: int | None = None
    data_type: str = ""
    filename: str = ""
    resolved_path: str | None = None
    block: int | None = None
    offset: int | None = None
    target: PanelTarget | None = None
    ref: str = ""
    action_verb: str = ""
    row: int | None = None
    column: int | None = None

    @property
    def target_kind(self) -> str:
        if self.target is not None:
            return self.target.kind
        if self.ref:
            return "panel"
        if self.source == "cell" and self.label and not self.action_verb:
            return "label"
        if self.block == 0 and self.offset == 0:
            return "null"
        if self.block is not None and self.offset is not None:
            return "unresolved-address"
        return "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_format": self.source_format,
            "panel_index": self.panel_index,
            "panel_type": self.panel_type,
            "panel_title": self.panel_title,
            "label": self.label,
            "target_kind": self.target_kind,
            "row_index": self.row_index,
            "cell_index": self.cell_index,
            "row": self.row,
            "column": self.column,
            "record_id": self.record_id,
            "data_type": self.data_type,
            "filename": self.filename,
            "resolved_path": self.resolved_path,
            "block": self.block,
            "offset": self.offset,
            "target": self.target.as_dict() if self.target else None,
            "ref": self.ref,
            "action_verb": self.action_verb,
        }


class PanelBinParseError(ValueError):
    """Raised when a Panel BIN payload does not match the fixed-record grammar."""


_XML_ENCODING_RE = re.compile(br"<\?xml[^>]*encoding=[\"']([^\"']+)[\"']", re.IGNORECASE)
_LVED_ADDR_RE = re.compile(r"^lved\.addr(?P<block>[0-9A-Fa-f]{8}):(?P<offset>[0-9A-Fa-f]{4})$")
_LVED_PANEL_RE = re.compile(r"^lved\.panel:(?P<panel>[0-9A-Fa-f]+)$")


def decode_panel_text(data: bytes) -> str:
    """Decode fixed-width Panel cell text bytes.

    Observed Panel text fields use the same JIS-pair text stream conventions as
    body/title text: NUL padding, 7-bit JIS pairs, gaiji pairs, and lightweight
    0x1f display controls such as halfwidth/superscript spans.
    """

    tokens, _stats = decode_tokens(data.rstrip(b"\x00"), gaiji="placeholder")
    return tokens_to_text(tokens)


def decode_panel_utf8_text(data: bytes) -> str:
    """Decode null-padded UTF-8 Panel cell text bytes."""

    return data.rstrip(b"\x00").decode("utf-8")


def _parse_panel_records(
    data: bytes,
    *,
    declared_record_count: int,
    actual_record_count: int,
    text_width: int,
    format_name: str,
    has_record_id: bool,
    text_encoding: str = "jis_control_stream",
) -> PanelBin:
    record_stride = (12 if has_record_id else 8) + text_width
    records: list[PanelBinRecord] = []
    pos = 8
    for index in range(actual_record_count):
        if has_record_id:
            record_id = int.from_bytes(data[pos : pos + 4], "little")
            block_pos = pos + 4
        else:
            record_id = None
            block_pos = pos
        block = int.from_bytes(data[block_pos : block_pos + 4], "little")
        offset = int.from_bytes(data[block_pos + 4 : block_pos + 8], "little")
        text_start = block_pos + 8
        text_bytes = data[text_start : text_start + text_width]
        text = (
            decode_panel_utf8_text(text_bytes)
            if text_encoding == "utf-8"
            else decode_panel_text(text_bytes)
        )
        records.append(
            PanelBinRecord(
                index=index,
                record_id=record_id,
                block=block,
                offset=offset,
                text_bytes=text_bytes.rstrip(b"\x00"),
                text=text,
            )
        )
        pos += record_stride
    return PanelBin(
        declared_record_count=declared_record_count,
        actual_record_count=actual_record_count,
        text_width=text_width,
        format=format_name,
        record_stride=record_stride,
        text_encoding=text_encoding,
        records=tuple(records),
    )


def _looks_like_utf8_panel_records(data: bytes, stride: int, *, sample_limit: int = 256) -> bool:
    if stride <= 8 or len(data) % stride:
        return False
    records = len(data) // stride
    if records == 0:
        return False
    non_empty = 0
    checked = 0
    for index in range(min(records, sample_limit)):
        pos = index * stride
        block = int.from_bytes(data[pos : pos + 4], "big")
        offset = int.from_bytes(data[pos + 4 : pos + 8], "big")
        text_bytes = data[pos + 8 : pos + stride].rstrip(b"\x00")
        if block > 0x100000 or offset > 0x100000:
            return False
        if not text_bytes:
            continue
        try:
            text = text_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if "\x00" in text:
            return False
        checked += 1
        non_empty += 1
    return checked > 0 and non_empty >= max(1, min(records, sample_limit) // 2)


def _parse_big_endian_utf8_panel_records(data: bytes) -> PanelBin | None:
    candidates: list[int] = []
    for stride in range(12, min(512, len(data)) + 1):
        if len(data) % stride == 0 and _looks_like_utf8_panel_records(data, stride):
            candidates.append(stride)
    if not candidates:
        return None
    stride = min(candidates)
    text_width = stride - 8
    records: list[PanelBinRecord] = []
    for index in range(len(data) // stride):
        pos = index * stride
        block = int.from_bytes(data[pos : pos + 4], "big")
        offset = int.from_bytes(data[pos + 4 : pos + 8], "big")
        text_bytes = data[pos + 8 : pos + stride]
        records.append(
            PanelBinRecord(
                index=index,
                record_id=None,
                block=block,
                offset=offset,
                text_bytes=text_bytes.rstrip(b"\x00"),
                text=decode_panel_utf8_text(text_bytes),
            )
        )
    return PanelBin(
        declared_record_count=len(records),
        actual_record_count=len(records),
        text_width=text_width,
        format="big_endian_address_utf8_label_no_header",
        record_stride=stride,
        text_encoding="utf-8",
        records=tuple(records),
    )


def parse_panel_bin(data: bytes) -> PanelBin:
    """Parse observed fixed-record Panel BIN payloads.

    Observed Panel tables are small address-label lookup files.  The common
    Windows layout is an 8-byte header followed by fixed records:

    ``u32 count, u32 text_width, repeated(u32 block, u32 offset, bytes label)``.

    Mobile/recovered packages add compatible variants:
    id-prefixed rows, where each row starts with an extra ``u32 record_id``;
    nominal-count tables where the header count is greater than the physically
    stored rows; empty zero-width placeholders; and headerless big-endian UTF-8
    row tables.
    """

    if len(data) < 8:
        raise PanelBinParseError("panel BIN is shorter than the 8-byte header")
    record_count = int.from_bytes(data[0:4], "little")
    text_width = int.from_bytes(data[4:8], "little")

    if text_width == 0:
        if len(data) == 8 and record_count == 0:
            return _parse_panel_records(
                data,
                declared_record_count=0,
                actual_record_count=0,
                text_width=0,
                format_name="empty",
                has_record_id=False,
            )
        expected = 8 + record_count * 8
        if len(data) == expected:
            return _parse_panel_records(
                data,
                declared_record_count=record_count,
                actual_record_count=record_count,
                text_width=0,
                format_name="address_empty_label",
                has_record_id=False,
            )
        raise PanelBinParseError(
            f"panel BIN zero-width size mismatch: count={record_count} "
            f"expected={expected} actual={len(data)}"
        )

    if text_width < 0:
        raise PanelBinParseError("panel BIN text width must not be negative")

    candidates = (
        ("address_label", False, record_count),
        ("id_address_label", True, record_count),
        ("address_label_declared_count_plus_one", False, max(record_count - 1, 0)),
        ("id_address_label_declared_count_plus_one", True, max(record_count - 1, 0)),
    )
    for format_name, has_record_id, actual_count in candidates:
        if actual_count < 0:
            continue
        stride = (12 if has_record_id else 8) + text_width
        if len(data) == 8 + actual_count * stride:
            return _parse_panel_records(
                data,
                declared_record_count=record_count,
                actual_record_count=actual_count,
                text_width=text_width,
                format_name=format_name,
                has_record_id=has_record_id,
            )

    if text_width > 0:
        for format_name, has_record_id in (
            ("address_label_declared_count_mismatch", False),
            ("id_address_label_declared_count_mismatch", True),
        ):
            stride = (12 if has_record_id else 8) + text_width
            rem = len(data) - 8
            if rem >= 0 and rem % stride == 0:
                actual_count = rem // stride
                if 0 <= actual_count <= record_count:
                    return _parse_panel_records(
                        data,
                        declared_record_count=record_count,
                        actual_record_count=actual_count,
                        text_width=text_width,
                        format_name=format_name,
                        has_record_id=has_record_id,
                    )

    utf8_panel = _parse_big_endian_utf8_panel_records(data)
    if utf8_panel is not None:
        return utf8_panel

    expected = 8 + record_count * (8 + text_width)
    expected_id = 8 + record_count * (12 + text_width)
    expected_minus_one = 8 + max(record_count - 1, 0) * (8 + text_width)
    expected_id_minus_one = 8 + max(record_count - 1, 0) * (12 + text_width)
    raise PanelBinParseError(
        f"panel BIN size mismatch: count={record_count} text_width={text_width} "
        f"expected={expected} expected_id={expected_id} "
        f"expected_minus_one={expected_minus_one} expected_id_minus_one={expected_id_minus_one} "
        f"actual={len(data)}"
    )


def parse_panel_bin_file(path: Path) -> PanelBin:
    return parse_panel_bin(path.read_bytes())


def _parse_panel_xml(path: Path) -> ElementTree.Element:
    data = path.read_bytes()
    try:
        return ElementTree.fromstring(data)
    except ValueError as exc:
        if "multi-byte encodings are not supported" not in str(exc):
            raise
    match = _XML_ENCODING_RE.search(data[:256])
    encoding = match.group(1).decode("ascii", errors="replace") if match else "cp932"
    text = data.decode(encoding)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    return ElementTree.fromstring(text)


def iter_panel_data_references(path: Path) -> Iterable[PanelDataReference]:
    """Yield external Panel data references from a ``Panels.xml`` file."""

    root = _parse_panel_xml(path)
    for panel in root.findall("panel"):
        title = panel.findtext("title") or ""
        panel_index = panel.attrib.get("index", "")
        panel_type = panel.attrib.get("paneltype", "")
        for data in panel.findall("data"):
            filename = data.attrib.get("filename")
            if not filename:
                continue
            yield PanelDataReference(
                panel_index=panel_index,
                panel_type=panel_type,
                data_type=data.attrib.get("type", ""),
                filename=filename,
                title=title,
                source_format="xml",
            )


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    return int(value)


def iter_panel_cell_references(path: Path) -> Iterable[PanelCellReference]:
    """Yield inline cells from a ``Panels.xml`` file.

    The observed Windows Panel grammar uses an internal ``menu`` panel whose
    cells carry ``ref`` and ``action_verb=lved.panel:<id>`` pointers to external
    data panels. These cells are the first navigation level and must be exposed
    separately from external BIN rows.
    """

    root = _parse_panel_xml(path)
    for panel in root.findall("panel"):
        title = panel.findtext("title") or ""
        panel_index = panel.attrib.get("index", "")
        panel_type = panel.attrib.get("paneltype", "")
        columns = _optional_int(panel.attrib.get("count_x"))
        cell_index = 0
        for data in panel.findall("data"):
            for cell in data.findall("cell"):
                label = "".join(cell.itertext()).strip()
                ref = cell.attrib.get("ref", "")
                action_verb = cell.attrib.get("action_verb", "")
                row = cell_index // columns if columns else None
                column = cell_index % columns if columns else None
                if label or ref or action_verb:
                    yield PanelCellReference(
                        panel_index=panel_index,
                        panel_type=panel_type,
                        title=title,
                        cell_index=cell_index,
                        row=row,
                        column=column,
                        label=label,
                        ref=ref,
                        action_verb=action_verb,
                        source_format="xml",
                        attributes=tuple(sorted(cell.attrib.items())),
                    )
                cell_index += 1


def iter_panel_plist_data_references(path: Path) -> Iterable[PanelDataReference]:
    """Yield external Panel data references from a ``Panels.plist``/menu plist.

    Mac and mobile Panel metadata preserves the same high-level model as
    Windows ``Panels.xml``: a panel id maps to either internal cells or an
    external data object.  External objects are dictionaries with ``type`` and
    ``filename`` keys, or mobile dictionaries whose ``path`` key names a BIN
    file under the package's ``bin/`` directory.
    """

    root = plistlib.loads(path.read_bytes())
    if isinstance(root, dict) and isinstance(root.get("panel"), dict):
        panels = root["panel"].items()
    elif isinstance(root, list):
        panels = _iter_mobile_menu_panels(root)
    else:
        return

    for panel_index, panel in panels:
        if not isinstance(panel, dict):
            continue
        panel_type = str(panel.get("paneltype", panel.get("type", "")))
        title = str(panel.get("title", panel.get("item", "")))
        data_items = panel.get("data")
        if data_items is None and "path" in panel:
            data_items = [{"type": "bin", "filename": str(panel["path"])}]
        if isinstance(data_items, dict):
            data_items = [data_items]
        if not isinstance(data_items, list):
            continue
        for data in data_items:
            if not isinstance(data, dict):
                continue
            filename = data.get("filename") or data.get("path")
            if not filename:
                continue
            yield PanelDataReference(
                panel_index=str(panel_index),
                panel_type=panel_type,
                data_type=str(data.get("type", "bin")),
                filename=str(filename),
                title=title,
                source_format="plist",
            )


def iter_panel_plist_cell_references(path: Path) -> Iterable[PanelCellReference]:
    """Yield inline/menu-like cells from ``Panels.plist`` or mobile menu plists."""

    root = plistlib.loads(path.read_bytes())
    if isinstance(root, dict) and isinstance(root.get("panel"), dict):
        panels = root["panel"].items()
    elif isinstance(root, list):
        panels = _iter_mobile_menu_panels(root)
    else:
        return

    for panel_index, panel in panels:
        if not isinstance(panel, dict):
            continue
        panel_type = str(panel.get("paneltype", panel.get("type", "")))
        title = str(panel.get("title", panel.get("item", "")))
        data_items = panel.get("data")
        if isinstance(data_items, dict):
            data_items = [data_items]
        if not isinstance(data_items, list):
            continue
        cell_index = 0
        for data in data_items:
            if not isinstance(data, dict):
                continue
            if data.get("filename") or data.get("path"):
                continue
            label = str(data.get("title", data.get("item", data.get("label", ""))))
            ref = str(data.get("ref", ""))
            action_verb = str(data.get("action_verb", data.get("action", "")))
            if label or ref or action_verb:
                attrs = tuple(sorted((str(key), str(value)) for key, value in data.items()))
                yield PanelCellReference(
                    panel_index=str(panel_index),
                    panel_type=panel_type,
                    title=title,
                    cell_index=cell_index,
                    row=None,
                    column=None,
                    label=label,
                    ref=ref,
                    action_verb=action_verb,
                    source_format="plist",
                    attributes=attrs,
                )
            cell_index += 1


def _iter_mobile_menu_panels(items: list[object], prefix: str = "") -> Iterable[tuple[str, dict[str, object]]]:
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        panel_index = f"{prefix}{index:04d}"
        if "path" in item:
            yield panel_index, item
        children = item.get("child")
        if isinstance(children, list):
            yield from _iter_mobile_menu_panels(children, prefix=f"{panel_index}.")


def iter_panel_references(path: Path) -> Iterable[PanelDataReference]:
    """Yield external Panel data references from XML or plist metadata."""

    lower = path.name.lower()
    if lower.endswith(".plist"):
        yield from iter_panel_plist_data_references(path)
    else:
        yield from iter_panel_data_references(path)


def iter_panel_cells(path: Path) -> Iterable[PanelCellReference]:
    """Yield inline Panel cells from XML or plist metadata."""

    lower = path.name.lower()
    if lower.endswith(".plist"):
        yield from iter_panel_plist_cell_references(path)
    else:
        yield from iter_panel_cell_references(path)


def resolve_panel_target(block: int, offset: int, elements: list[SsedInfoElement]) -> PanelTarget | None:
    """Resolve an absolute SSED block/offset address to its catalog component."""

    return resolve_component_target(block, offset, elements)


def panel_bin_navigation_rows(
    reference: PanelDataReference,
    panel: PanelBin,
    *,
    resolved_path: Path | None,
    elements: list[SsedInfoElement],
) -> tuple[PanelNavigationRow, ...]:
    """Convert decoded Panel BIN records into app-facing navigation rows."""

    return tuple(
        PanelNavigationRow(
            source="bin",
            panel_index=reference.panel_index,
            panel_type=reference.panel_type,
            panel_title=reference.title,
            label=record.text,
            source_format=reference.source_format,
            row_index=record.index,
            record_id=record.record_id,
            data_type=reference.data_type,
            filename=reference.filename,
            resolved_path=str(resolved_path) if resolved_path else None,
            block=record.block,
            offset=record.offset,
            target=resolve_panel_target(record.block, record.offset, elements),
        )
        for record in panel.records
    )


def _parse_panel_action_address(action_verb: str) -> tuple[int, int] | None:
    match = _LVED_ADDR_RE.match(action_verb)
    if match:
        return int(match.group("block"), 16), int(match.group("offset"), 16)
    return parse_lvaddr_href(action_verb)


def _panel_ref_from_action(action_verb: str) -> str:
    match = _LVED_PANEL_RE.match(action_verb)
    return match.group("panel") if match else ""


def panel_cell_navigation_row(
    cell: PanelCellReference,
    *,
    elements: list[SsedInfoElement] | None = None,
) -> PanelNavigationRow:
    """Convert an inline Panel cell to an app-facing navigation row."""

    address = _parse_panel_action_address(cell.action_verb)
    block = address[0] if address else None
    offset = address[1] if address else None
    ref = cell.ref or _panel_ref_from_action(cell.action_verb)
    return PanelNavigationRow(
        source="cell",
        panel_index=cell.panel_index,
        panel_type=cell.panel_type,
        panel_title=cell.title,
        label=cell.label,
        source_format=cell.source_format,
        cell_index=cell.cell_index,
        block=block,
        offset=offset,
        target=resolve_panel_target(block, offset, elements or []) if address else None,
        ref=ref,
        action_verb=cell.action_verb,
        row=cell.row,
        column=cell.column,
    )
