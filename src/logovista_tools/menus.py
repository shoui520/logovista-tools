"""Extract structured MENU.DIC trees and destination pointers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .colscr import decode_bcd_decimal
from .entries import (
    CONTROL_ARG_LENGTHS,
    SPACE_RE,
    decode_jis_pair,
    discover_dictionaries,
    gaiji_text,
    normalize_fullwidth_ascii,
)
from .gaiji import load_gaiji_profile
from .ssed import BLOCK_SIZE, SsedInfoElement, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


MENU_TYPE = 0x01


@dataclass(frozen=True)
class MenuTarget:
    component: str
    component_type: str
    kind: str
    start_block: int
    end_block: int
    relative_offset: int
    offset_in_block: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "component_type": self.component_type,
            "kind": self.kind,
            "start_block": self.start_block,
            "end_block": self.end_block,
            "relative_offset": self.relative_offset,
            "offset_in_block": self.offset_in_block,
        }


@dataclass(frozen=True)
class MenuDestination:
    payload: bytes
    block: int
    offset: int
    encoding: str
    target: MenuTarget | None = None

    @property
    def is_null(self) -> bool:
        return self.block == 0 and self.offset == 0

    @property
    def absolute_offset(self) -> int:
        return (self.block - 1) * BLOCK_SIZE + self.offset

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload.hex(),
            "encoding": self.encoding,
            "block": self.block,
            "offset": self.offset,
            "is_null": self.is_null,
            "absolute_offset": self.absolute_offset,
            "target": self.target.as_dict() if self.target else None,
        }


@dataclass
class MenuLink:
    label: str
    destination: MenuDestination | None
    start_offset: int
    end_offset: int
    control: str
    start_payload: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "destination": self.destination.as_dict() if self.destination else None,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "control": self.control,
            "start_payload": self.start_payload,
        }


@dataclass
class MenuRecord:
    line_index: int
    byte_start: int
    byte_end: int
    section_codes: list[str] = field(default_factory=list)
    text: str = ""
    links: list[MenuLink] = field(default_factory=list)
    depth: int = 1
    path: list[str] = field(default_factory=list)

    @property
    def section_code(self) -> str | None:
        return self.section_codes[0] if self.section_codes else None

    @property
    def destination(self) -> MenuDestination | None:
        if len(self.links) == 1:
            return self.links[0].destination
        return None

    def label(self) -> str:
        if self.text:
            return self.text
        if self.links:
            return self.links[0].label
        return f"line {self.line_index}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_index": self.line_index,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "section_code": self.section_code,
            "section_codes": self.section_codes,
            "depth": self.depth,
            "path": self.path,
            "text": self.text,
            "links": [link.as_dict() for link in self.links],
            "destination": self.destination.as_dict() if self.destination else None,
        }


@dataclass
class MenuParseResult:
    records: list[MenuRecord]
    stats: dict[str, int]


@dataclass
class _ActiveLink:
    control: str
    start_offset: int
    parts: list[str] = field(default_factory=list)
    start_payload: str | None = None


@dataclass
class _LineBuilder:
    line_index: int
    byte_start: int
    parts: list[str] = field(default_factory=list)
    section_codes: list[str] = field(default_factory=list)
    links: list[MenuLink] = field(default_factory=list)
    active_link: _ActiveLink | None = None

    def add_text(self, value: str) -> None:
        self.parts.append(value)
        if self.active_link is not None:
            self.active_link.parts.append(value)


def clean_menu_text(value: str) -> str:
    return SPACE_RE.sub(" ", value).strip()


def parse_menu_destination(payload: bytes) -> MenuDestination | None:
    if len(payload) != 6:
        return None
    block = decode_bcd_decimal(payload[:4])
    offset = decode_bcd_decimal(payload[4:6])
    if block is not None and offset is not None:
        return MenuDestination(payload=payload, block=block, offset=offset, encoding="bcd")
    return MenuDestination(
        payload=payload,
        block=int.from_bytes(payload[:4], "big"),
        offset=int.from_bytes(payload[4:6], "big"),
        encoding="big-endian",
    )


def component_kind(component_type: int) -> str:
    if component_type == 0x00:
        return "body"
    if component_type == 0x01:
        return "menu"
    if component_type in {0x03, 0x04, 0x05, 0x06, 0x07, 0x0A}:
        return "title"
    if component_type in {0x70, 0x71, 0x80, 0x81, 0x90, 0x91}:
        return "index"
    if component_type == 0xD2:
        return "media-image"
    if component_type == 0xD8:
        return "media-audio"
    if component_type in {0xF1, 0xF2}:
        return "gaiji-resource"
    return "component"


def resolve_menu_destination(
    destination: MenuDestination,
    elements: list[SsedInfoElement],
) -> MenuDestination:
    for element in elements:
        if not element.start:
            continue
        if element.start <= destination.block <= element.end:
            relative_offset = (destination.block - element.start) * BLOCK_SIZE + destination.offset
            target = MenuTarget(
                component=element.filename,
                component_type=f"{element.type:02x}",
                kind=component_kind(element.type),
                start_block=element.start,
                end_block=element.end,
                relative_offset=relative_offset,
                offset_in_block=destination.offset,
            )
            return replace(destination, target=target)
    return destination


def resolve_menu_record_destinations(records: list[MenuRecord], elements: list[SsedInfoElement]) -> int:
    resolved = 0
    for record in records:
        for link in record.links:
            if link.destination is None:
                continue
            if link.destination.is_null:
                continue
            destination = resolve_menu_destination(link.destination, elements)
            if destination.target is not None:
                resolved += 1
            link.destination = destination
    return resolved


def menu_destination_resolution_counts(records: list[MenuRecord]) -> dict[str, int]:
    destinations = 0
    null_destinations = 0
    resolved_destinations = 0
    unresolved_destinations = 0
    for record in records:
        for link in record.links:
            destination = link.destination
            if destination is None:
                continue
            destinations += 1
            if destination.is_null:
                null_destinations += 1
            elif destination.target is not None:
                resolved_destinations += 1
            else:
                unresolved_destinations += 1
    return {
        "destinations": destinations,
        "null_destinations": null_destinations,
        "resolved_destinations": resolved_destinations,
        "unresolved_destinations": unresolved_destinations,
    }


def finish_active_link(line: _LineBuilder, *, end_offset: int, destination: MenuDestination | None) -> None:
    if line.active_link is None:
        return
    label = clean_menu_text("".join(line.active_link.parts))
    line.links.append(
        MenuLink(
            label=label,
            destination=destination,
            start_offset=line.active_link.start_offset,
            end_offset=end_offset,
            control=line.active_link.control,
            start_payload=line.active_link.start_payload,
        )
    )
    line.active_link = None


def flush_line(line: _LineBuilder, *, byte_end: int) -> MenuRecord | None:
    if line.active_link is not None:
        finish_active_link(line, end_offset=byte_end, destination=None)
    text = clean_menu_text("".join(line.parts))
    if not text and not line.section_codes and not line.links:
        return None
    return MenuRecord(
        line_index=line.line_index,
        byte_start=line.byte_start,
        byte_end=byte_end,
        section_codes=list(line.section_codes),
        text=text,
        links=list(line.links),
    )


def parse_menu_stream(
    data: bytes,
    *,
    gaiji: str = "h-placeholder",
    gaiji_map: dict[str, str] | None = None,
) -> MenuParseResult:
    gaiji_map = gaiji_map or {}
    records: list[MenuRecord] = []
    stats = {
        "controls": 0,
        "unknown_controls": 0,
        "sections": 0,
        "links": 0,
        "destinations": 0,
        "null_destinations": 0,
        "jis_pairs": 0,
        "gaiji": 0,
        "lines": 0,
    }

    line = _LineBuilder(line_index=1, byte_start=0)
    i = 0
    halfwidth_depth = 0
    private_depth = 0
    while i < len(data):
        b = data[i]
        if b == 0:
            i += 1
            continue

        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            stats["controls"] += 1

            if op == 0x09:
                if i + 4 <= len(data) and not private_depth:
                    line.section_codes.append(data[i + 2 : i + 4].hex())
                    stats["sections"] += 1
                i += 4
                continue

            if op == 0x0A:
                if private_depth:
                    i += 2
                    continue
                record = flush_line(line, byte_end=i + 2)
                if record is not None:
                    records.append(record)
                    stats["lines"] += 1
                line = _LineBuilder(line_index=line.line_index + 1, byte_start=i + 2)
                i += 2
                continue

            if op in (0x42, 0x43):
                if private_depth:
                    i += 2
                    continue
                line.active_link = _ActiveLink(control=f"1f{op:02x}", start_offset=i)
                i += 2
                continue

            if op == 0x4A:
                if private_depth:
                    i += 18
                    continue
                payload = data[i + 2 : i + 18]
                line.active_link = _ActiveLink(
                    control="1f4a",
                    start_offset=i,
                    start_payload=payload.hex() if len(payload) == 16 else None,
                )
                i += 18
                continue

            if op in (0x62, 0x63):
                payload = data[i + 2 : i + 8]
                destination = parse_menu_destination(payload) if len(payload) == 6 else None
                if destination is not None and not private_depth:
                    stats["destinations"] += 1
                    if destination.is_null:
                        stats["null_destinations"] += 1
                if not private_depth:
                    finish_active_link(line, end_offset=i + 8, destination=destination)
                    stats["links"] += 1
                i += 8
                continue

            if op == 0x6A:
                if not private_depth:
                    finish_active_link(line, end_offset=i + 2, destination=None)
                    stats["links"] += 1
                i += 2
                continue

            if op in (0x41, 0xE0, 0xE2):
                if op == 0xE2:
                    private_depth += 1
                i += 4
                continue

            if op == 0x04:
                halfwidth_depth += 1
                i += 2
                continue

            if op == 0x05:
                if halfwidth_depth:
                    halfwidth_depth -= 1
                i += 2
                continue

            if op in (0x00, 0x02, 0x03, 0x61, 0xE1, 0xE3):
                if op == 0xE3 and private_depth:
                    private_depth -= 1
                i += 2
                continue

            stats["unknown_controls"] += 1
            i += 2 + CONTROL_ARG_LENGTHS.get(op, 0)
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            text = decode_jis_pair(data[i : i + 2])
            if text:
                if not private_depth:
                    line.add_text(normalize_fullwidth_ascii(text) if halfwidth_depth else text)
                stats["jis_pairs"] += 1
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            if not private_depth:
                value = gaiji_text(b, data[i + 1], gaiji, gaiji_map)
                if value:
                    line.add_text(value)
            stats["gaiji"] += 1
            i += 2
            continue

        i += 1

    record = flush_line(line, byte_end=len(data))
    if record is not None:
        records.append(record)
        stats["lines"] += 1

    annotate_menu_records(records)
    return MenuParseResult(records=records, stats=stats)


def annotate_menu_records(records: list[MenuRecord]) -> None:
    section_codes = sorted(
        {record.section_code for record in records if record.section_code is not None},
        key=lambda value: int(value, 16),
    )
    section_depths = {code: index + 1 for index, code in enumerate(section_codes)}
    label_stack: list[str] = []

    for record in records:
        record.depth = section_depths.get(record.section_code, 1)
        label = record.label()
        while len(label_stack) >= record.depth:
            label_stack.pop()
        record.path = [*label_stack, label]
        label_stack.append(label)


def menu_record_tree_node(record: MenuRecord) -> dict[str, Any]:
    node = record.as_dict()
    node["children"] = []
    return node


def build_menu_tree(records: list[MenuRecord]) -> list[dict[str, Any]]:
    tree: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for record in records:
        node = menu_record_tree_node(record)
        while len(stack) >= record.depth:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            tree.append(node)
        stack.append(node)
    return tree


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_menus_for_idx(idx: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    title, elements = parse_ssedinfo(idx)
    dict_id = idx.parent.parent.name if idx.parent.name == idx.parent.parent.name else idx.stem
    dict_out = out_dir / dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    menus_path = dict_out / "raw_menus.jsonl"
    tree_path = dict_out / "menu_tree.json"
    gaiji_profile = load_gaiji_profile(idx)

    summaries: list[dict[str, Any]] = []
    emitted = 0
    tree_components: list[dict[str, Any]] = []

    with menus_path.open("w", encoding="utf-8") as out:
        for element in elements:
            if element.type != MENU_TYPE or not element.start:
                continue
            source = find_case_insensitive(idx.parent, element.filename)
            if source is None:
                summaries.append(
                    {
                        "component": element.filename,
                        "type": element.type,
                        "missing": True,
                        "lines_emitted": 0,
                    }
                )
                continue

            expanded = expand_sseddata_file(source)
            parsed = parse_menu_stream(expanded, gaiji=args.gaiji, gaiji_map=gaiji_profile.map)
            resolve_menu_record_destinations(parsed.records, elements)
            destination_counts = menu_destination_resolution_counts(parsed.records)
            target_kinds: dict[str, int] = {}
            for record in parsed.records:
                for link in record.links:
                    if link.destination is None or link.destination.target is None:
                        continue
                    target_kinds[link.destination.target.kind] = target_kinds.get(link.destination.target.kind, 0) + 1
            component_emitted = 0
            for record in parsed.records:
                if args.limit and component_emitted >= args.limit:
                    break
                item = {
                    "dict_id": dict_id,
                    "dict_title": title,
                    "component": element.filename,
                    **record.as_dict(),
                }
                out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")
                component_emitted += 1
                emitted += 1

            tree_records = parsed.records[: args.limit] if args.limit else parsed.records
            tree_components.append(
                {
                    "component": element.filename,
                    "type": element.type,
                    "tree": build_menu_tree(tree_records),
                }
            )
            summaries.append(
                {
                    "component": element.filename,
                    "type": element.type,
                    "data_flags": element.data.hex(),
                    "expanded_bytes": len(expanded),
                    "lines_total": len(parsed.records),
                    "lines_emitted": component_emitted,
                    "links": parsed.stats["links"],
                    **destination_counts,
                    "target_kinds": target_kinds,
                    "sections": parsed.stats["sections"],
                    "stats": parsed.stats,
                    "stub": len(parsed.records) == 0,
                }
            )

    write_json(
        tree_path,
        {
            "dict_id": dict_id,
            "dict_title": title,
            "idx": str(idx),
            "components": tree_components,
        },
    )

    summary = {
        "dict_id": dict_id,
        "dict_title": title,
        "idx": str(idx),
        "menu_components": summaries,
        "lines_emitted": emitted,
        "gaiji_map_entries": len(gaiji_profile.map),
        "gaiji_uni_entries": gaiji_profile.uni_entries,
        "gaiji_plist_entries": gaiji_profile.plist_entries,
        "menus_path": str(menus_path),
        "tree_path": str(tree_path),
    }
    write_json(dict_out / "menus_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        help="Dictionary collection directory or a direct .IDX path. Can repeat.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-menus"))
    parser.add_argument("--limit", type=int, help="Limit emitted menu lines per component.")
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    args = parser.parse_args()

    roots = args.root or [Path(".")]
    sources = discover_dictionaries(roots)
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in sources:
        print(f"extracting menus {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_menus_for_idx(source.idx, args.out_dir, args)
        summaries.append(summary)
        print(f"  menu_lines={summary['lines_emitted']}", file=sys.stderr)
    write_json(args.out_dir / "summary.json", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
