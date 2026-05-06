#!/usr/bin/env python3
"""Extract readable raw entries from LogoVista/SSED HONMON.DIC files.

This deliberately uses the raw .IDX/.DIC layer. SQLite cache files are not read.
Gaiji are resolved through dictionary-specific .uni files first, then
Gaiji.plist/GaijiS.plist fallback mappings, or emitted as placeholders.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .gaiji import load_gaiji_map, load_gaiji_profile
from .resources import load_image_resource_profile, relative_image_source
from .ssed import BLOCK_SIZE, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


ENTRY_MARKER = b"\x1f\x09\x00\x01"
SPACE_RE = re.compile(r"[ \t\r\f\v]+")
CONTROL_ARG_LENGTHS = {
    0x09: 2,
    0x41: 2,
    0x42: 0,
    0x43: 0,
    0x4A: 16,
    0x4D: 18,
    0x62: 6,
    0x63: 6,
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


@dataclass(frozen=True)
class Image:
    key: str
    kind: str = "gaiji"


@dataclass(frozen=True)
class Media:
    payload: str


@dataclass(frozen=True)
class Section:
    code: str


Token = Text | StartTag | EndTag | Break | Image | Media | Section


@dataclass(frozen=True)
class DictionarySource:
    dict_id: str
    idx: Path
    title: str
    honmon: Path
    honmon_start_block: int
    gaiji_map: dict[str, str]
    gaiji_uni_entries: int = 0
    gaiji_plist_entries: int = 0
    image_resource_entries: int = 0
    image_gaiji_keys: frozenset[str] = frozenset()
    image_sources: dict[str, str] | None = None
    image_dirs: tuple[Path, ...] = ()


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
        0x43: "link",
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
        0x63: "link",
        0x6A: "link",
        0x6D: "media",
        0xE1: "bold",
        0xE3: "color",
    }.get(op)


def decode_tokens(
    data: bytes,
    *,
    gaiji: str = "drop",
    gaiji_map: dict[str, str] | None = None,
    image_gaiji_keys: frozenset[str] | set[str] | None = None,
    preserve_image_gaiji: bool = False,
    preserve_media: bool = False,
    preserve_sections: bool = False,
) -> tuple[list[Token], dict[str, int]]:
    gaiji_map = gaiji_map or {}
    image_gaiji_keys = image_gaiji_keys or frozenset()
    tokens: list[Token] = []
    stats = {
        "controls": 0,
        "unknown_controls": 0,
        "gaiji": 0,
        "image_gaiji": 0,
        "media": 0,
        "sections": 0,
        "links": 0,
        "jis_pairs": 0,
    }
    i = 0
    while i < len(data):
        b = data[i]

        if b == 0:
            i += 1
            continue

        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            stats["controls"] += 1
            if op == 0x09:
                payload = data[i + 2 : i + 4]
                stats["sections"] += 1
                if preserve_sections and len(payload) == 2:
                    tokens.append(Section(payload.hex()))
                i += 2 + CONTROL_ARG_LENGTHS[op]
                continue
            if op == 0x0A:
                tokens.append(Break())
                i += 2
                continue
            if op == 0x4D:
                arg_len = CONTROL_ARG_LENGTHS[op]
                payload = data[i + 2 : i + 2 + arg_len]
                stats["media"] += 1
                if preserve_media:
                    tokens.append(Media(payload.hex()))
                else:
                    tokens.append(StartTag("media"))
                i += 2 + arg_len
                continue
            start_tag = control_tag_for_start(op)
            end_tag = control_tag_for_end(op)
            if start_tag is not None:
                tokens.append(StartTag(start_tag))
                if start_tag == "link":
                    stats["links"] += 1
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
            key = f"{b:02x}{data[i + 1]:02x}"
            if key in gaiji_map:
                tokens.append(Text(gaiji_map[key]))
            elif preserve_image_gaiji and key in image_gaiji_keys:
                tokens.append(Image(key))
                stats["image_gaiji"] += 1
            else:
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
        elif isinstance(token, Image):
            lines[-1] += f"<img:{token.key}>"
        elif isinstance(token, Media):
            lines[-1] += f"<media:{token.payload}>"
        elif isinstance(token, Section):
            lines[-1] += f"<section:{token.code}>"
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


def html_image_src(key: str, image_sources: dict[str, str]) -> str | None:
    return image_sources.get(key.lower())


def tokens_to_html(
    tokens: list[Token],
    *,
    image_sources: dict[str, str] | None = None,
    section_image_sources: dict[str, str] | None = None,
) -> str:
    """Render decoded tokens as conservative inline HTML.

    The resulting HTML is intended as an interchange/debug representation for
    targets that support inline HTML. Exporters should copy the referenced image
    files into the target package and rewrite paths if needed.
    """

    image_sources = image_sources or {}
    section_image_sources = section_image_sources or {}
    html_parts: list[str] = []
    tag_stack: list[str] = []
    tag_map = {
        "bold": "b",
        "italic": "i",
        "sub": "sub",
        "sup": "sup",
        "em": "em",
        "head": "span",
        "color": "span",
    }
    attrs = {
        "head": ' class="lv-head"',
        "color": ' class="lv-color"',
    }

    for token in tokens:
        if isinstance(token, Text):
            html_parts.append(html.escape(token.value, quote=False))
        elif isinstance(token, Image):
            src = html_image_src(token.key, image_sources)
            if src is None:
                html_parts.append(html.escape(f"<img:{token.key}>", quote=False))
                continue
            escaped_src = html.escape(src, quote=True)
            escaped_key = html.escape(token.key, quote=True)
            html_parts.append(f'<img src="{escaped_src}" alt="{escaped_key}" class="lv-gaiji lv-gaiji-{escaped_key}">')
        elif isinstance(token, Media):
            escaped_payload = html.escape(token.payload, quote=True)
            html_parts.append(f'<span class="lv-media" data-lv-media="{escaped_payload}"></span>')
        elif isinstance(token, Section):
            escaped_code = html.escape(token.code, quote=True)
            section_src = section_image_sources.get(token.code.lower())
            if section_src is not None:
                escaped_src = html.escape(section_src, quote=True)
                html_parts.append(
                    f'<img src="{escaped_src}" alt="{escaped_code}" '
                    f'class="lv-section-image lv-section-image-{escaped_code}">'
                )
            html_parts.append(f'<span class="lv-section" data-lv-section="{escaped_code}"></span>')
        elif isinstance(token, Break):
            html_parts.append("<br>")
        elif isinstance(token, StartTag):
            html_tag = tag_map.get(token.tag)
            if html_tag is None:
                continue
            html_parts.append(f"<{html_tag}{attrs.get(token.tag, '')}>")
            tag_stack.append(html_tag)
        elif isinstance(token, EndTag):
            html_tag = tag_map.get(token.tag)
            if html_tag is None:
                continue
            if html_tag in tag_stack:
                while tag_stack:
                    closing = tag_stack.pop()
                    html_parts.append(f"</{closing}>")
                    if closing == html_tag:
                        break

    while tag_stack:
        html_parts.append(f"</{tag_stack.pop()}>")
    return "".join(html_parts)


def resolve_section_image_sources(specs: list[str] | None, image_sources: dict[str, str] | None) -> dict[str, str]:
    """Resolve CODE=KEY section-image specs into CODE=img/path mappings."""

    resolved: dict[str, str] = {}
    if not specs:
        return resolved
    image_sources = image_sources or {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"invalid --section-image value {spec!r}; expected CODE=IMAGE_KEY")
        code, image_key = spec.split("=", 1)
        code = code.strip().lower()
        image_key = image_key.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{4}", code):
            raise ValueError(f"invalid section code {code!r}; expected four hex digits")
        image_src = image_sources.get(image_key)
        if image_src is None:
            image_src = f"img/{image_key}.png"
        resolved[code] = image_src
    return resolved


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
    return iter_entry_slices_with_boundaries(data)


def iter_entry_slices_with_boundaries(
    data: bytes,
    boundary_offsets: Iterable[int] | None = None,
) -> Iterable[tuple[int, int]]:
    positions: list[int] = []
    pos = data.find(ENTRY_MARKER)
    while pos != -1:
        start = pos - 2 if pos >= 2 and data[pos - 2 : pos] == b"\x1f\x02" else pos
        positions.append(start)
        pos = data.find(ENTRY_MARKER, pos + 1)

    if boundary_offsets is not None:
        for offset in boundary_offsets:
            if 0 <= offset < len(data):
                if offset >= 2 and data[offset - 2 : offset] == b"\x1f\x02" and data[offset : offset + 4] == ENTRY_MARKER:
                    positions.append(offset - 2)
                else:
                    positions.append(offset)

    if not positions:
        stripped = data.strip(b"\x00")
        if stripped:
            yield 0, len(data)
        return

    deduped = sorted(set(positions))
    for index, start in enumerate(deduped):
        end = deduped[index + 1] if index + 1 < len(deduped) else len(data)
        if end > start:
            yield start, end


def load_plist_gaiji_map(idx: Path) -> dict[str, str]:
    """Compatibility wrapper for callers that used the old plist-only name."""

    return load_gaiji_map(idx)


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
            gaiji_profile = load_gaiji_profile(idx)
            image_profile = load_image_resource_profile(idx)
            image_sources = {}
            for key, resource in image_profile.resources.items():
                selected = resource.normal or resource.default or resource.white
                if selected is not None:
                    image_sources[key] = relative_image_source(selected, idx)
            found.append(
                DictionarySource(
                    dict_id=dict_id,
                    idx=idx,
                    title=title,
                    honmon=honmon,
                    honmon_start_block=honmon_element.start,
                    gaiji_map=gaiji_profile.map,
                    gaiji_uni_entries=gaiji_profile.uni_entries,
                    gaiji_plist_entries=gaiji_profile.plist_entries,
                    image_resource_entries=len(image_profile.resources),
                    image_gaiji_keys=image_profile.gaiji_image_keys,
                    image_sources=image_sources,
                    image_dirs=image_profile.image_dirs,
                )
            )
    return found


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_dictionary(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    entries_path = dict_out / "raw_entries.jsonl"
    section_image_sources = resolve_section_image_sources(getattr(args, "section_image", None), source.image_sources)

    expanded = expand_sseddata_file(source.honmon)
    marker_count = expanded.count(ENTRY_MARKER)
    emitted = 0
    skipped_empty = 0
    aggregate_stats = {
        "controls": 0,
        "unknown_controls": 0,
        "gaiji": 0,
        "image_gaiji": 0,
        "media": 0,
        "sections": 0,
        "links": 0,
        "jis_pairs": 0,
    }
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
            "gaiji_uni_entries": source.gaiji_uni_entries,
            "gaiji_plist_entries": source.gaiji_plist_entries,
            "image_resource_entries": source.image_resource_entries,
            "image_gaiji_entries": len(source.image_gaiji_keys),
            "image_dirs": [str(path) for path in source.image_dirs],
            "entries_path": str(entries_path),
        }
        write_json(dict_out / "summary.json", summary)
        return summary

    index_boundary_offsets: set[int] = set()
    if getattr(args, "index_boundaries", True):
        try:
            from .indexes import collect_index_body_offsets_for_idx

            index_boundary_offsets = collect_index_body_offsets_for_idx(
                source.idx,
                honmon_start_block=source.honmon_start_block,
                expanded_size=len(expanded),
            )
        except Exception as exc:
            warnings.append(f"Could not collect index-derived entry boundaries: {exc}")

    with entries_path.open("w", encoding="utf-8") as out:
        slices = iter_entry_slices_with_boundaries(expanded, index_boundary_offsets)
        for entry_index, (start, end) in enumerate(slices, start=1):
            if args.limit and emitted >= args.limit:
                break
            segment = expanded[start:end]
            tokens, stats = decode_tokens(
                segment,
                gaiji=args.gaiji,
                gaiji_map=source.gaiji_map,
                image_gaiji_keys=source.image_gaiji_keys,
                preserve_image_gaiji=args.image_gaiji,
                preserve_media=args.media_placeholder,
                preserve_sections=args.section_markers,
            )
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
            if args.html:
                item["body_html"] = tokens_to_html(
                    tokens,
                    image_sources=source.image_sources,
                    section_image_sources=section_image_sources,
                )
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
        "index_entry_boundaries": len(index_boundary_offsets),
        "entries_emitted": emitted,
        "entries_skipped_empty": skipped_empty,
        "stats": aggregate_stats,
        "warnings": warnings,
        "gaiji_map_entries": len(source.gaiji_map),
        "gaiji_uni_entries": source.gaiji_uni_entries,
        "gaiji_plist_entries": source.gaiji_plist_entries,
        "image_resource_entries": source.image_resource_entries,
        "image_gaiji_entries": len(source.image_gaiji_keys),
        "image_dirs": [str(path) for path in source.image_dirs],
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
        "--image-gaiji",
        action="store_true",
        help="Preserve unresolved gaiji that have PNG assets as <img:code> placeholders.",
    )
    parser.add_argument(
        "--media-placeholder",
        action="store_true",
        help="Preserve 1f4d media controls as <media:payload-hex> placeholders.",
    )
    parser.add_argument(
        "--section-markers",
        action="store_true",
        help="Preserve 1f09 section markers as <section:xxxx> placeholders.",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Also emit body_html with conservative inline HTML and img tags for image gaiji.",
    )
    parser.add_argument(
        "--section-image",
        action="append",
        help="For HTML output, insert an image at a section marker. Format: CODE=IMAGE_KEY, e.g. 0011=exam.",
    )
    parser.add_argument(
        "--no-skip-dense-marker-honmon",
        dest="skip_dense_marker_honmon",
        action="store_false",
        help="Attempt extraction even when HONMON looks like an anchor/id table.",
    )
    parser.add_argument(
        "--no-index-boundaries",
        dest="index_boundaries",
        action="store_false",
        help="Do not add raw index body pointers as extra entry boundaries.",
    )
    parser.set_defaults(skip_dense_marker_honmon=True)
    parser.set_defaults(index_boundaries=True)
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
