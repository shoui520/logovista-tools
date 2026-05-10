"""Corpus-wide atlas for LogoVista 0x1f text-stream controls."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .component_forensics import component_role
from .entries import CONTROL_ARG_LENGTHS, control_tag_for_end, control_tag_for_start, decode_jis_pair, gaiji_text
from .gaiji import load_gaiji_profile
from .parallel import parallel_map_ordered, worker_args
from .profiles import ProfileTarget, discover_profile_targets, safe_relative
from .ssed import expand_sseddata_file, find_case_insensitive


TEXT_STREAM_ROLES = {"honmon", "menu", "title", "text", "text_index"}


@dataclass(frozen=True)
class ControlSpec:
    op: str
    arg_len: int
    family: str
    label: str
    pair_role: str = "none"
    pair: str | None = None
    structural_confidence: str = "high"
    semantic_confidence: str = "medium"
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "arg_len": self.arg_len,
            "family": self.family,
            "label": self.label,
            "pair_role": self.pair_role,
            "pair": self.pair,
            "structural_confidence": self.structural_confidence,
            "semantic_confidence": self.semantic_confidence,
            "notes": self.notes,
        }


CONTROL_SPECS: dict[int, ControlSpec] = {
    0x00: ControlSpec("00", 0, "neutral", "neutral/no-op control", semantic_confidence="low"),
    0x02: ControlSpec("02", 0, "wrapper", "entry/wrapper start", pair_role="start", pair="03"),
    0x03: ControlSpec("03", 0, "wrapper", "entry/wrapper end", pair_role="end", pair="02"),
    0x04: ControlSpec(
        "04",
        0,
        "text-mode",
        "halfwidth conversion start",
        pair_role="start",
        pair="05",
        semantic_confidence="high",
        notes="JIS row-3 fullwidth ASCII cells inside this span are rendered/exported as halfwidth ASCII.",
    ),
    0x05: ControlSpec(
        "05",
        0,
        "text-mode",
        "halfwidth conversion end",
        pair_role="end",
        pair="04",
        semantic_confidence="high",
    ),
    0x06: ControlSpec("06", 0, "style", "subscript start", pair_role="start", pair="07", semantic_confidence="high"),
    0x07: ControlSpec("07", 0, "style", "subscript end", pair_role="end", pair="06", semantic_confidence="high"),
    0x09: ControlSpec("09", 2, "structure", "section/entry marker", semantic_confidence="high"),
    0x0A: ControlSpec("0a", 0, "structure", "line break", semantic_confidence="high"),
    0x0B: ControlSpec("0b", 0, "style", "literal/preformatted start", pair_role="start", pair="0c"),
    0x0C: ControlSpec("0c", 0, "style", "literal/preformatted end", pair_role="end", pair="0b"),
    0x0E: ControlSpec("0e", 0, "style", "superscript start", pair_role="start", pair="0f", semantic_confidence="high"),
    0x0F: ControlSpec("0f", 0, "style", "superscript end", pair_role="end", pair="0e", semantic_confidence="high"),
    0x10: ControlSpec("10", 0, "style", "italic-ish start", pair_role="start", pair="11"),
    0x11: ControlSpec("11", 0, "style", "italic-ish end", pair_role="end", pair="10"),
    0x12: ControlSpec("12", 0, "style", "emphasis-ish start", pair_role="start", pair="13"),
    0x13: ControlSpec("13", 0, "style", "emphasis-ish end", pair_role="end", pair="12"),
    0x1A: ControlSpec(
        "1a",
        2,
        "layout",
        "fixed two-byte layout/style control",
        semantic_confidence="low",
        notes="Payload is structurally fixed; renderer semantics remain neutral.",
    ),
    0x1C: ControlSpec(
        "1c",
        2,
        "layout",
        "fixed two-byte layout/style control",
        semantic_confidence="low",
        notes="Often clusters near media references; renderer semantics remain neutral.",
    ),
    0x3B: ControlSpec("3b", 0, "link", "URL span start", pair_role="start", pair="5b"),
    0x5B: ControlSpec("5b", 0, "link", "URL span end", pair_role="end", pair="3b"),
    0x41: ControlSpec("41", 2, "heading", "headword/title span start", pair_role="start", pair="61", semantic_confidence="high"),
    0x61: ControlSpec("61", 0, "heading", "headword/title span end", pair_role="end", pair="41", semantic_confidence="high"),
    0x42: ControlSpec("42", 0, "link", "link-ish start", pair_role="start", pair="62"),
    0x62: ControlSpec("62", 6, "link", "link-ish end with pointer payload", pair_role="end", pair="42"),
    0x43: ControlSpec("43", 0, "link", "link-ish start", pair_role="start", pair="63"),
    0x63: ControlSpec("63", 6, "link", "link-ish end with pointer payload", pair_role="end", pair="43"),
    0x44: ControlSpec("44", 10, "link", "extended link start", pair_role="start", pair="64"),
    0x64: ControlSpec("64", 6, "link", "extended link end with pointer payload", pair_role="end", pair="44"),
    0x49: ControlSpec(
        "49",
        10,
        "link",
        "TOC/internal link start",
        pair_role="start",
        pair="69",
        notes="Payload ends with a big-endian block/offset-like target in observed TOC streams.",
    ),
    0x69: ControlSpec("69", 0, "link", "TOC/internal link end", pair_role="end", pair="49"),
    0x4A: ControlSpec(
        "4a",
        16,
        "link-media",
        "jump/link/media range start",
        pair_role="start",
        pair="6a",
        notes="Used for visible links and PCMDATA-style sound/media ranges.",
    ),
    0x6A: ControlSpec("6a", 0, "link-media", "jump/link/media range end", pair_role="end", pair="4a"),
    0x4D: ControlSpec("4d", 18, "media", "media/reference start", pair_role="start", pair="6d"),
    0x6D: ControlSpec("6d", 0, "media", "media/reference end", pair_role="end", pair="4d"),
    0xE0: ControlSpec("e0", 2, "style", "bold-ish start", pair_role="start", pair="e1"),
    0xE1: ControlSpec("e1", 0, "style", "bold-ish end", pair_role="end", pair="e0"),
    0xE2: ControlSpec(
        "e2",
        2,
        "renderer-directive",
        "private renderer directive start",
        pair_role="start",
        pair="e3",
        semantic_confidence="high",
        notes="Wraps directive text such as IMG:, RUB:, SMC:, IDX:, HTM:, SQL:, and PlaySound; renderer output should not expose the directive text literally.",
    ),
    0xE3: ControlSpec(
        "e3",
        0,
        "renderer-directive",
        "private renderer directive end",
        pair_role="end",
        pair="e2",
        semantic_confidence="high",
    ),
}


def spec_for_op(op: int) -> ControlSpec | None:
    spec = CONTROL_SPECS.get(op)
    if spec is not None:
        return spec
    start_tag = control_tag_for_start(op)
    end_tag = control_tag_for_end(op)
    if start_tag is None and end_tag is None:
        return None
    role = "start" if start_tag is not None else "end"
    return ControlSpec(
        f"{op:02x}",
        CONTROL_ARG_LENGTHS.get(op, 0),
        start_tag or end_tag or "control",
        f"{start_tag or end_tag} {role}",
        pair_role=role,
        semantic_confidence="medium",
    )


def decode_context(data: bytes, *, gaiji_map: dict[str, str], max_chars: int = 80) -> str:
    parts: list[str] = []
    i = 0
    while i < len(data) and len("".join(parts)) < max_chars:
        b = data[i]
        if b == 0:
            i += 1
            continue
        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            arg_len = CONTROL_ARG_LENGTHS.get(op, 0)
            raw = data[i : min(len(data), i + 2 + arg_len)]
            payload = raw[2:].hex()
            parts.append(f"<1f{op:02x}{':' + payload if payload else ''}>")
            i += max(2, len(raw))
            continue
        if i + 1 < len(data) and data[i : i + 2] == b"\x11\x03":
            parts.append("<1103>")
            i += 2
            continue
        if b == 0x0A:
            parts.append("\\n")
            i += 1
            continue
        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            parts.append(decode_jis_pair(data[i : i + 2]))
            i += 2
            continue
        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            parts.append(gaiji_map.get(data[i : i + 2].hex()) or gaiji_text(b, data[i + 1], "placeholder", gaiji_map))
            i += 2
            continue
        if 0x20 <= b <= 0x7E:
            parts.append(chr(b))
            i += 1
            continue
        parts.append(f"<{b:02x}>")
        i += 1
    rendered = "".join(parts)
    return rendered[:max_chars]


def next_control_op(data: bytes, offset: int, *, limit: int = 256) -> str | None:
    end = min(len(data), offset + limit)
    pos = data.find(b"\x1f", offset, end)
    if pos < 0 or pos + 1 >= len(data):
        return None
    return f"{data[pos + 1]:02x}"


def preceding_text_context(data: bytes, start: int, *, gaiji_map: dict[str, str], radius: int) -> str:
    begin = max(0, start - radius)
    return decode_context(data[begin:start], gaiji_map=gaiji_map)


def following_text_context(data: bytes, end: int, *, gaiji_map: dict[str, str], radius: int) -> str:
    finish = min(len(data), end + radius)
    return decode_context(data[end:finish], gaiji_map=gaiji_map)


@dataclass
class OpcodeAccumulator:
    count: int = 0
    payload_lengths: Counter[int] = field(default_factory=Counter)
    payload_values: Counter[str] = field(default_factory=Counter)
    payload_prefixes: Counter[str] = field(default_factory=Counter)
    roles: Counter[str] = field(default_factory=Counter)
    component_types: Counter[str] = field(default_factory=Counter)
    filenames: Counter[str] = field(default_factory=Counter)
    dictionaries: Counter[str] = field(default_factory=Counter)
    previous_ops: Counter[str] = field(default_factory=Counter)
    next_ops: Counter[str] = field(default_factory=Counter)
    examples: list[dict[str, Any]] = field(default_factory=list)


def scan_text_stream(
    *,
    target: ProfileTarget,
    roots: list[Path],
    element: Any,
    data: bytes,
    role: str,
    gaiji_map: dict[str, str],
    max_examples_per_opcode: int,
    context_bytes: int,
) -> dict[str, Any]:
    opcodes: dict[str, OpcodeAccumulator] = defaultdict(OpcodeAccumulator)
    unknowns: Counter[str] = Counter()
    truncated: list[dict[str, Any]] = []
    previous_op: str | None = None
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x1F:
            start = i
            if i + 1 >= len(data):
                truncated.append(
                    {
                        "dict_id": target.dict_id,
                        "component": element.filename,
                        "role": role,
                        "offset": start,
                        "raw_hex": data[start:].hex(),
                        "message": "0x1f control introducer has no opcode byte",
                    }
                )
                break
            op = data[i + 1]
            op_hex = f"{op:02x}"
            spec = spec_for_op(op)
            arg_len = CONTROL_ARG_LENGTHS.get(op, 0)
            length = 2 + arg_len
            raw = data[start : min(len(data), start + length)]
            payload = raw[2:]
            acc = opcodes[op_hex]
            acc.count += 1
            acc.payload_lengths[len(payload)] += 1
            if payload:
                if len(acc.payload_values) < 32 or payload.hex() in acc.payload_values:
                    acc.payload_values[payload.hex()] += 1
                acc.payload_prefixes[payload[:4].hex()] += 1
            acc.roles[role] += 1
            acc.component_types[f"{element.type:02x}"] += 1
            acc.filenames[element.filename] += 1
            acc.dictionaries[target.dict_id] += 1
            if previous_op is not None:
                acc.previous_ops[previous_op] += 1
            if spec is None:
                unknowns[op_hex] += 1

            if len(raw) < length:
                truncated.append(
                    {
                        "dict_id": target.dict_id,
                        "component": element.filename,
                        "role": role,
                        "offset": start,
                        "op": op_hex,
                        "raw_hex": raw.hex(),
                        "expected_length": length,
                        "actual_length": len(raw),
                    }
                )
                break

            if len(acc.examples) < max_examples_per_opcode:
                next_op = next_control_op(data, start + length)
                if next_op is not None:
                    acc.next_ops[next_op] += 1
                acc.examples.append(
                    {
                        "dict_id": target.dict_id,
                        "dict_title": target.title,
                        "component": element.filename,
                        "role": role,
                        "component_type": f"{element.type:02x}",
                        "offset": start,
                        "raw_hex": raw.hex(),
                        "payload_hex": payload.hex(),
                        "previous_op": previous_op,
                        "next_op": next_op,
                        "before": preceding_text_context(data, start, gaiji_map=gaiji_map, radius=context_bytes),
                        "after": following_text_context(data, start + length, gaiji_map=gaiji_map, radius=context_bytes),
                    }
                )

            previous_op = op_hex
            i += length
            continue
        i += 1

    return {
        "opcodes": {
            op: {
                "count": acc.count,
                "payload_lengths": dict(acc.payload_lengths),
                "payload_values": dict(acc.payload_values.most_common(32)),
                "payload_prefixes": dict(acc.payload_prefixes.most_common(32)),
                "roles": dict(acc.roles),
                "component_types": dict(acc.component_types),
                "filenames": dict(acc.filenames.most_common(32)),
                "dictionaries": dict(acc.dictionaries),
                "previous_ops": dict(acc.previous_ops.most_common(32)),
                "next_ops": dict(acc.next_ops.most_common(32)),
                "examples": acc.examples,
            }
            for op, acc in opcodes.items()
        },
        "unknowns": dict(unknowns),
        "truncated": truncated,
    }


def merge_opcode_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, OpcodeAccumulator] = defaultdict(OpcodeAccumulator)
    unknowns: Counter[str] = Counter()
    truncated: list[dict[str, Any]] = []
    component_count = 0
    bytes_scanned = 0
    for row in rows:
        component_count += int(row.get("components_scanned", 0))
        bytes_scanned += int(row.get("bytes_scanned", 0))
        unknowns.update(row.get("unknowns", {}))
        truncated.extend(row.get("truncated", []))
        for op, data in row.get("opcodes", {}).items():
            acc = merged[op]
            acc.count += int(data.get("count", 0))
            acc.payload_lengths.update({int(k): int(v) for k, v in data.get("payload_lengths", {}).items()})
            acc.payload_values.update(data.get("payload_values", {}))
            acc.payload_prefixes.update(data.get("payload_prefixes", {}))
            acc.roles.update(data.get("roles", {}))
            acc.component_types.update(data.get("component_types", {}))
            acc.filenames.update(data.get("filenames", {}))
            acc.dictionaries.update(data.get("dictionaries", {}))
            acc.previous_ops.update(data.get("previous_ops", {}))
            acc.next_ops.update(data.get("next_ops", {}))
            remaining = max(0, 12 - len(acc.examples))
            if remaining:
                acc.examples.extend(data.get("examples", [])[:remaining])

    opcode_rows: list[dict[str, Any]] = []
    for op in sorted(merged):
        acc = merged[op]
        op_int = int(op, 16)
        spec = spec_for_op(op_int)
        row = {
            "op": op,
            "count": acc.count,
            "payload_lengths": dict(sorted(acc.payload_lengths.items())),
            "roles": dict(acc.roles.most_common()),
            "component_types": dict(acc.component_types.most_common()),
            "filenames": dict(acc.filenames.most_common(32)),
            "dictionary_count": len(acc.dictionaries),
            "dictionaries": sorted(acc.dictionaries),
            "payload_values": dict(acc.payload_values.most_common(32)),
            "payload_prefixes": dict(acc.payload_prefixes.most_common(32)),
            "previous_ops": dict(acc.previous_ops.most_common(32)),
            "next_ops": dict(acc.next_ops.most_common(32)),
            "examples": acc.examples,
            "classification": spec.as_dict()
            if spec is not None
            else {
                "op": op,
                "arg_len": None,
                "family": "unknown",
                "label": "unclassified 0x1f control",
                "pair_role": "unknown",
                "pair": None,
                "structural_confidence": "unknown",
                "semantic_confidence": "unknown",
                "notes": "Argument length and renderer semantics are not classified.",
            },
        }
        opcode_rows.append(row)

    return {
        "schema": "logovista-opcode-atlas-v1",
        "components_scanned": component_count,
        "bytes_scanned": bytes_scanned,
        "opcode_count": len(opcode_rows),
        "total_controls": sum(row["count"] for row in opcode_rows),
        "unknown_control_ops": dict(sorted(unknowns.items())),
        "truncated_controls": truncated,
        "opcodes": opcode_rows,
    }


def inspect_target_for_opcodes(payload: tuple[ProfileTarget, list[Path], argparse.Namespace]) -> dict[str, Any]:
    target, roots, args = payload
    gaiji_profile = load_gaiji_profile(target.idx)
    row: dict[str, Any] = {
        "dict_id": target.dict_id,
        "dict_title": target.title,
        "idx": safe_relative(target.idx, roots),
        "components_scanned": 0,
        "bytes_scanned": 0,
        "opcodes": {},
        "unknowns": {},
        "truncated": [],
        "warnings": [],
    }
    opcodes: dict[str, Any] = {}
    unknowns: Counter[str] = Counter()
    truncated: list[dict[str, Any]] = []
    for element in target.elements:
        role = "honmon" if element.filename.upper() == "HONMON.DIC" else component_role(element.filename, element.type)
        if role not in TEXT_STREAM_ROLES or not element.start:
            continue
        source = find_case_insensitive(target.idx.parent, element.filename)
        if source is None:
            row["warnings"].append(f"missing component {element.filename}")
            continue
        try:
            expanded = expand_sseddata_file(source)
        except Exception as exc:
            row["warnings"].append(f"could not expand {element.filename}: {exc}")
            continue
        report = scan_text_stream(
            target=target,
            roots=roots,
            element=element,
            data=expanded,
            role=role or "text",
            gaiji_map=gaiji_profile.map,
            max_examples_per_opcode=args.max_examples_per_opcode,
            context_bytes=args.context_bytes,
        )
        row["components_scanned"] += 1
        row["bytes_scanned"] += len(expanded)
        unknowns.update(report["unknowns"])
        truncated.extend(report["truncated"])
        for op, data in report["opcodes"].items():
            existing = opcodes.setdefault(
                op,
                {
                    "count": 0,
                    "payload_lengths": Counter(),
                    "payload_values": Counter(),
                    "payload_prefixes": Counter(),
                    "roles": Counter(),
                    "component_types": Counter(),
                    "filenames": Counter(),
                    "dictionaries": Counter(),
                    "previous_ops": Counter(),
                    "next_ops": Counter(),
                    "examples": [],
                },
            )
            existing["count"] += data["count"]
            existing["payload_lengths"].update({int(k): int(v) for k, v in data["payload_lengths"].items()})
            existing["payload_values"].update(data["payload_values"])
            existing["payload_prefixes"].update(data["payload_prefixes"])
            existing["roles"].update(data["roles"])
            existing["component_types"].update(data["component_types"])
            existing["filenames"].update(data["filenames"])
            existing["dictionaries"].update(data["dictionaries"])
            existing["previous_ops"].update(data["previous_ops"])
            existing["next_ops"].update(data["next_ops"])
            remaining = max(0, args.max_examples_per_opcode - len(existing["examples"]))
            if remaining:
                existing["examples"].extend(data["examples"][:remaining])

    row["opcodes"] = {
        op: {
            "count": data["count"],
            "payload_lengths": dict(data["payload_lengths"]),
            "payload_values": dict(data["payload_values"].most_common(32)),
            "payload_prefixes": dict(data["payload_prefixes"].most_common(32)),
            "roles": dict(data["roles"]),
            "component_types": dict(data["component_types"]),
            "filenames": dict(data["filenames"].most_common(32)),
            "dictionaries": dict(data["dictionaries"]),
            "previous_ops": dict(data["previous_ops"].most_common(32)),
            "next_ops": dict(data["next_ops"].most_common(32)),
            "examples": data["examples"],
        }
        for op, data in opcodes.items()
    }
    row["unknowns"] = dict(unknowns)
    row["truncated"] = truncated
    return row


def write_markdown_atlas(path: Path, atlas: dict[str, Any]) -> None:
    lines = [
        "# LogoVista 0x1f Opcode Atlas",
        "",
        f"Components scanned: {atlas['components_scanned']}",
        f"Bytes scanned: {atlas['bytes_scanned']:,}",
        f"Total controls: {atlas['total_controls']:,}",
        f"Distinct opcodes: {atlas['opcode_count']}",
        "",
        "## Summary",
        "",
        "| Opcode | Count | Payload lengths | Roles | Classification | Confidence |",
        "|---|---:|---|---|---|---|",
    ]
    for row in atlas["opcodes"]:
        classification = row["classification"]
        payload_lengths = ", ".join(f"{key}:{value}" for key, value in row["payload_lengths"].items()) or "0"
        roles = ", ".join(f"{key}:{value}" for key, value in row["roles"].items())
        confidence = f"{classification['structural_confidence']}/{classification['semantic_confidence']}"
        lines.append(
            f"| `1f{row['op']}` | {row['count']:,} | {payload_lengths} | {roles} | "
            f"{classification['label']} | {confidence} |"
        )

    if atlas["unknown_control_ops"]:
        lines.extend(["", "## Unclassified Opcodes", ""])
        for op, count in atlas["unknown_control_ops"].items():
            match = next((row for row in atlas["opcodes"] if row["op"] == op), None)
            dictionaries = ", ".join(match["dictionaries"]) if match else ""
            lines.append(f"- `1f{op}`: {count:,} occurrence(s). Dictionaries: {dictionaries}")
    else:
        lines.extend(["", "## Unclassified Opcodes", "", "None in scanned text-stream components."])

    if atlas["truncated_controls"]:
        lines.extend(["", "## Truncated Controls", ""])
        for item in atlas["truncated_controls"][:50]:
            lines.append(
                f"- {item.get('dict_id')} {item.get('component')} offset {item.get('offset')}: "
                f"{item.get('message', item.get('raw_hex'))}"
            )

    lines.extend(["", "## Opcode Details", ""])
    for row in atlas["opcodes"]:
        classification = row["classification"]
        lines.extend(
            [
                f"### `1f{row['op']}`",
                "",
                f"- Count: {row['count']:,}",
                f"- Payload lengths: {row['payload_lengths']}",
                f"- Roles: {row['roles']}",
                f"- Component types: {row['component_types']}",
                f"- Dictionaries: {row['dictionary_count']}",
                f"- Classification: {classification['label']}",
                f"- Family: {classification['family']}",
                f"- Pair behavior: {classification['pair_role']} -> {classification['pair']}",
                f"- Confidence: structural={classification['structural_confidence']}, semantic={classification['semantic_confidence']}",
            ]
        )
        if classification.get("notes"):
            lines.append(f"- Notes: {classification['notes']}")
        if row["payload_values"]:
            lines.append(f"- Common payloads: {row['payload_values']}")
        if row["previous_ops"]:
            lines.append(f"- Common previous controls: {row['previous_ops']}")
        if row["next_ops"]:
            lines.append(f"- Sampled next controls: {row['next_ops']}")
        lines.extend(["", "Examples:", ""])
        for example in row["examples"][:6]:
            lines.extend(
                [
                    f"- {example['dict_id']} `{example['component']}` offset {example['offset']}",
                    f"  - raw: `{example['raw_hex']}`",
                    f"  - before: `{example['before']}`",
                    f"  - after: `{example['after']}`",
                ]
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def extract_opcode_atlas_for_args(args: argparse.Namespace) -> dict[str, Any]:
    roots = args.root or [Path(".")]
    targets = discover_profile_targets(roots, jobs=args.jobs)
    if args.dict:
        selected = set(args.dict)
        targets = [target for target in targets if target.dict_id in selected or target.idx.stem in selected]
    payloads = [(target, roots, worker_args(args)) for target in targets]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    def log_result(row: dict[str, Any]) -> None:
        print(
            f"opcode-atlas progress: {row.get('dict_id', '?')} "
            f"components={row.get('components_scanned', 0)} bytes={row.get('bytes_scanned', 0)} "
            f"unknowns={sum(int(v) for v in row.get('unknowns', {}).values())}",
            file=sys.stderr,
        )

    rows = parallel_map_ordered(inspect_target_for_opcodes, payloads, jobs=args.jobs, on_result=log_result)
    atlas = merge_opcode_rows(rows)
    atlas["packages_scanned"] = len(rows)
    atlas["package_warnings"] = [
        {"dict_id": row["dict_id"], "warnings": row["warnings"]}
        for row in rows
        if row.get("warnings")
    ][:200]
    (args.out_dir / "per_dictionary").mkdir(parents=True, exist_ok=True)
    for row in rows:
        (args.out_dir / "per_dictionary" / f"{row['dict_id']}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (args.out_dir / "opcode_atlas.json").write_text(
        json.dumps(atlas, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown_atlas(args.out_dir / "opcode_atlas.md", atlas)
    return atlas
