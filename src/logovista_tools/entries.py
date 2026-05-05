#!/usr/bin/env python3
"""Extract readable raw entries from LogoVista/SSED HONMON.DIC files.

This deliberately uses the raw .IDX/.DIC layer. SQLite cache files are not read.
Gaiji are ignored or emitted as placeholders; full gaiji resolution belongs in a
separate pass.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .ssed import BLOCK_SIZE, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


ENTRY_MARKER = b"\x1f\x09\x00\x01"
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
CONTROL_ARG_LENGTHS = {
    0x09: 2,
    0x41: 2,
    0x42: 0,
    0x4A: 15,
    0x4D: 16,
    0x62: 6,
    0xE0: 2,
    0xE2: 2,
}


@dataclass(frozen=True)
class Text:
    value: str


@dataclass(frozen=True)
class StartTag:
    tag: str


@dataclass(frozen=True)
class EndTag:
    tag: str


@dataclass(frozen=True)
class Break:
    pass


Token = Text | StartTag | EndTag | Break


@dataclass(frozen=True)
class DictionarySource:
    dict_id: str
    idx: Path
    title: str
    honmon: Path
    honmon_start_block: int
    gaiji_map: dict[str, str]


def decode_jis_pair(pair: bytes) -> str:
    try:
        return (b"\x1b$B" + pair + b"\x1b(B").decode("iso2022_jp")
    except UnicodeDecodeError:
        return ""


def normalize_fullwidth_ascii(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch == "\u3000":
            chars.append(" ")
        elif ch == "\u2212":
            chars.append("-")
        elif 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(ch)
    return "".join(chars)


def gaiji_text(first: int, second: int, mode: str, gaiji_map: dict[str, str]) -> str:
    key = f"{first:02x}{second:02x}"
    if key in gaiji_map:
        return gaiji_map[key]
    if mode == "drop":
        return ""
    prefix = "h" if first < 0xB0 else "z"
    if mode == "h-placeholder" and prefix != "h":
        return ""
    return f"<{prefix}{first:02X}{second:02X}>"


def control_tag_for_start(op: int) -> str | None:
    return {
        0x06: "sub",
        0x0E: "sup",
        0x10: "italic",
        0x12: "em",
        0x41: "head",
        0x42: "link",
        0x4A: "link",
        0x4D: "media",
        0xE0: "bold",
        0xE2: "color",
    }.get(op)


def control_tag_for_end(op: int) -> str | None:
    return {
        0x07: "sub",
        0x0F: "sup",
        0x11: "italic",
        0x13: "em",
        0x61: "head",
        0x62: "link",
        0x6A: "link",
        0x6D: "media",
        0xE1: "bold",
        0xE3: "color",
    }.get(op)


def decode_tokens(
    data: bytes, *, gaiji: str = "drop", gaiji_map: dict[str, str] | None = None
) -> tuple[list[Token], dict[str, int]]:
    gaiji_map = gaiji_map or {}
    tokens: list[Token] = []
    stats = {"controls": 0, "unknown_controls": 0, "gaiji": 0, "jis_pairs": 0}
    i = 0
    while i < len(data):
        b = data[i]

        if b == 0:
            i += 1
            continue

        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            stats["controls"] += 1
            if op == 0x0A:
                tokens.append(Break())
                i += 2
                continue
            start_tag = control_tag_for_start(op)
            end_tag = control_tag_for_end(op)
            if start_tag is not None:
                tokens.append(StartTag(start_tag))
            elif end_tag is not None:
                tokens.append(EndTag(end_tag))
            elif op not in (0x02, 0x03, 0x04, 0x05, 0x00):
                stats["unknown_controls"] += 1
            i += 2 + CONTROL_ARG_LENGTHS.get(op, 0)
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            text = decode_jis_pair(data[i : i + 2])
            if text:
                tokens.append(Text(normalize_fullwidth_ascii(text)))
                stats["jis_pairs"] += 1
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            value = gaiji_text(b, data[i + 1], gaiji, gaiji_map)
            if value:
                tokens.append(Text(value))
            stats["gaiji"] += 1
            i += 2
            continue

        i += 1

    return tokens, stats


def tokens_to_text(tokens: list[Token]) -> str:
    lines = [""]
    for token in tokens:
        if isinstance(token, Text):
            lines[-1] += token.value
        elif isinstance(token, Break):
            if lines[-1] or (lines and lines[-1] != ""):
                lines.append("")
        elif isinstance(token, StartTag):
            if token.tag in {"link", "media"}:
                continue
        elif isinstance(token, EndTag):
            continue

    cleaned: list[str] = []
    previous_blank = False
    for line in lines:
        line = SPACE_RE.sub(" ", line).strip()
        if not line:
            if not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        cleaned.append(line)
        previous_blank = False
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


def extract_heading(tokens: list[Token], body: str) -> str:
    in_head = False
    parts: list[str] = []
    for token in tokens:
        if isinstance(token, StartTag) and token.tag == "head":
            in_head = True
        elif isinstance(token, EndTag) and token.tag == "head":
            if parts:
                break
            in_head = False
        elif in_head and isinstance(token, Text):
            parts.append(token.value)

    heading = SPACE_RE.sub(" ", "".join(parts)).strip()
    if heading:
        return heading[:240]

    for line in body.splitlines():
        line = line.strip()
        if line:
            return line[:240]
    return ""


def is_useless_body(body: str) -> bool:
    compact = body.strip()
    if not compact:
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{8,16}", compact):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{6,16}(?:\n[0-9A-Fa-f]{6,16})+", compact):
        return True
    return False


def iter_entry_slices(data: bytes) -> Iterable[tuple[int, int]]:
    positions: list[int] = []
    pos = data.find(ENTRY_MARKER)
    while pos != -1:
        start = pos - 2 if pos >= 2 and data[pos - 2 : pos] == b"\x1f\x02" else pos
        positions.append(start)
        pos = data.find(ENTRY_MARKER, pos + 1)

    if not positions:
        stripped = data.strip(b"\x00")
        if stripped:
            yield 0, len(data)
        return

    deduped: list[int] = []
    for start in positions:
        if not deduped or start != deduped[-1]:
            deduped.append(start)
    for index, start in enumerate(deduped):
        end = deduped[index + 1] if index + 1 < len(deduped) else len(data)
        if end > start:
            yield start, end


def load_plist_gaiji_map(idx: Path) -> dict[str, str]:
    gaiji_map: dict[str, str] = {}
    candidates = [
        idx.parent / "GaijiS.plist",
        idx.parent / "Gaiji.plist",
        idx.parent.parent / "GaijiS.plist",
        idx.parent.parent / "Gaiji.plist",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = plistlib.load(path.open("rb"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Fa-f0-9]{4}", key):
                continue
            if isinstance(value, str):
                gaiji_map.setdefault(key.lower(), value)
    return gaiji_map


def discover_dictionaries(roots: list[Path]) -> list[DictionarySource]:
    found: list[DictionarySource] = []
    seen: set[Path] = set()
    for root in roots:
        candidates: list[Path] = []
        if root.is_file() and root.suffix.upper() == ".IDX":
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(root.rglob("*.IDX"))
            candidates.extend(root.rglob("*.idx"))

        for idx in sorted(candidates):
            idx = idx.resolve()
            if idx in seen:
                continue
            seen.add(idx)
            try:
                title, elements = parse_ssedinfo(idx)
            except Exception:
                continue
            honmon_element = next((e for e in elements if e.filename.upper() == "HONMON.DIC"), None)
            if honmon_element is None:
                continue
            honmon = find_case_insensitive(idx.parent, honmon_element.filename)
            if honmon is None:
                continue
            dict_id = idx.parent.parent.name if idx.parent.name == idx.parent.parent.name else idx.stem
            found.append(
                DictionarySource(
                    dict_id=dict_id,
                    idx=idx,
                    title=title,
                    honmon=honmon,
                    honmon_start_block=honmon_element.start,
                    gaiji_map=load_plist_gaiji_map(idx),
                )
            )
    return found


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_dictionary(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    entries_path = dict_out / "raw_entries.jsonl"

    expanded = expand_sseddata_file(source.honmon)
    marker_count = expanded.count(ENTRY_MARKER)
    emitted = 0
    skipped_empty = 0
    aggregate_stats = {"controls": 0, "unknown_controls": 0, "gaiji": 0, "jis_pairs": 0}
    warnings: list[str] = []

    dense_marker_honmon = marker_count > 0 and marker_count * 64 > len(expanded)
    if dense_marker_honmon and args.skip_dense_marker_honmon:
        warnings.append(
            "HONMON has a dense 32-byte-ish entry-marker pattern; it appears to "
            "be an anchor/id table rather than body text. Skipped HONMON body extraction."
        )
        entries_path.write_text("", encoding="utf-8")
        summary = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "idx": str(source.idx),
            "honmon": str(source.honmon),
            "honmon_start_block": source.honmon_start_block,
            "expanded_bytes": len(expanded),
            "entry_markers": marker_count,
            "entries_emitted": 0,
            "entries_skipped_empty": 0,
            "stats": aggregate_stats,
            "warnings": warnings,
            "gaiji_map_entries": len(source.gaiji_map),
            "entries_path": str(entries_path),
        }
        write_json(dict_out / "summary.json", summary)
        return summary

    with entries_path.open("w", encoding="utf-8") as out:
        for entry_index, (start, end) in enumerate(iter_entry_slices(expanded), start=1):
            if args.limit and emitted >= args.limit:
                break
            segment = expanded[start:end]
            tokens, stats = decode_tokens(segment, gaiji=args.gaiji, gaiji_map=source.gaiji_map)
            body = tokens_to_text(tokens)
            if is_useless_body(body) or len(body.strip()) < args.min_chars:
                skipped_empty += 1
                continue
            for key, value in stats.items():
                aggregate_stats[key] += value
            block = source.honmon_start_block + start // BLOCK_SIZE
            offset = start % BLOCK_SIZE
            heading = extract_heading(tokens, body)
            item = {
                "dict_id": source.dict_id,
                "dict_title": source.title,
                "entry_index": entry_index,
                "block": block,
                "offset": offset,
                "length": end - start,
                "heading": heading,
                "body": body,
            }
            out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            out.write("\n")
            emitted += 1

    summary = {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "honmon_start_block": source.honmon_start_block,
        "expanded_bytes": len(expanded),
        "entry_markers": marker_count,
        "entries_emitted": emitted,
        "entries_skipped_empty": skipped_empty,
        "stats": aggregate_stats,
        "warnings": warnings,
        "gaiji_map_entries": len(source.gaiji_map),
        "entries_path": str(entries_path),
    }
    write_json(dict_out / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        help="Dictionary collection directory or a direct .IDX path. Can repeat.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-extract"))
    parser.add_argument("--limit", type=int, help="Limit entries per dictionary, for testing.")
    parser.add_argument("--min-chars", type=int, default=1)
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument(
        "--no-skip-dense-marker-honmon",
        dest="skip_dense_marker_honmon",
        action="store_false",
        help="Attempt extraction even when HONMON looks like an anchor/id table.",
    )
    parser.set_defaults(skip_dense_marker_honmon=True)
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
        print(f"extracting {source.dict_id}: {source.title}", file=sys.stderr)
        summary = extract_dictionary(source, args.out_dir, args)
        summaries.append(summary)
        print(
            f"  entries={summary['entries_emitted']} markers={summary['entry_markers']} "
            f"bytes={summary['expanded_bytes']}",
            file=sys.stderr,
        )

    write_json(args.out_dir / "summary.json", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
