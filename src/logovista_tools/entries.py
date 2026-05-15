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

from .cli_args import add_entries_args
from .cli_ux import status
from .controls import CONTROL_ARG_LENGTHS, KNOWN_NONPRINTING_CONTROLS, control_arg_length
from .gaiji import load_gaiji_map, load_gaiji_profile
from .parallel import parallel_map_ordered
from .resources import load_image_resource_profile, relative_image_source
from .ssed import (
    BLOCK_SIZE,
    CHUNK_SIZE,
    SsedRandomReader,
    expand_sseddata_file_with_storage,
    find_case_insensitive,
    honmon_component,
    iter_files_with_suffix,
    parse_ssedinfo,
    sseddata_storage_for_file,
)


ENTRY_MARKER = b"\x1f\x09\x00\x01"
SPACE_RE = re.compile(r"[ \t\r\f\v]+")


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
    honmon_storage: str = "unknown"
    gaiji_loaded: bool = True
    gaiji_uni_entries: int = 0
    gaiji_plist_entries: int = 0
    image_resources_loaded: bool = True
    image_resource_entries: int = 0
    image_gaiji_keys: frozenset[str] = frozenset()
    image_sources: dict[str, str] | None = None
    image_dirs: tuple[Path, ...] = ()


def decode_jis_pair(pair: bytes) -> str:
    try:
        return (b"\x1b$B" + pair + b"\x1b(B").decode("iso2022_jp")
    except UnicodeDecodeError:
        pass
    try:
        sjis = jis_pair_to_sjis(pair)
        try:
            return sjis.decode("cp932")
        except UnicodeDecodeError:
            return sjis.decode("shift_jis_2004")
    except UnicodeDecodeError:
        return ""


def jis_pair_to_sjis(pair: bytes) -> bytes:
    """Convert a 7-bit JIS cell pair to Shift_JIS for CP932 extension rows."""

    row = pair[0] - 0x21
    cell = pair[1] - 0x21
    lead = (row >> 1) + 0x81
    if lead > 0x9F:
        lead += 0x40
    if row & 1:
        trail = cell + 0x9F
    else:
        trail = cell + 0x40
        if trail >= 0x7F:
            trail += 1
    return bytes((lead, trail))


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
        0x04: "halfwidth",
        0x06: "sub",
        0x0B: "literal",
        0x0E: "sup",
        0x10: "italic",
        0x12: "em",
        0x3B: "url",
        0x3C: "media",
        0x41: "head",
        0x42: "link",
        0x43: "link",
        0x44: "link",
        0x49: "link",
        0x4A: "link",
        0x4D: "media",
        0xE0: "bold",
        0xE2: "private",
    }.get(op)


def control_tag_for_end(op: int) -> str | None:
    return {
        0x05: "halfwidth",
        0x07: "sub",
        0x0C: "literal",
        0x0F: "sup",
        0x11: "italic",
        0x13: "em",
        0x5B: "url",
        0x5C: "media",
        0x61: "head",
        0x62: "link",
        0x63: "link",
        0x64: "link",
        0x69: "link",
        0x6A: "link",
        0x6D: "media",
        0xE1: "bold",
        0xE3: "private",
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
        "legacy_controls": 0,
    }
    i = 0
    halfwidth_depth = 0
    private_depth = 0
    while i < len(data):
        b = data[i]

        if b == 0:
            i += 1
            continue

        if b == 0x0A:
            if not private_depth:
                tokens.append(Break())
            i += 1
            continue

        if i + 1 < len(data) and data[i : i + 2] == b"\x11\x03":
            stats["legacy_controls"] += 1
            i += 2
            continue

        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            arg_len = control_arg_length(data, i)
            stats["controls"] += 1
            if op == 0x09:
                payload = data[i + 2 : i + 4]
                stats["sections"] += 1
                if preserve_sections and len(payload) == 2 and not private_depth:
                    tokens.append(Section(payload.hex()))
                i += 2 + arg_len
                continue
            if op == 0x0A:
                if not private_depth:
                    tokens.append(Break())
                i += 2
                continue
            if op in {0x3C, 0x4D}:
                payload = data[i + 2 : i + 2 + arg_len]
                stats["media"] += 1
                if private_depth:
                    pass
                elif preserve_media:
                    tokens.append(Media(payload.hex()))
                else:
                    tokens.append(StartTag("media"))
                i += 2 + arg_len
                continue
            start_tag = control_tag_for_start(op)
            end_tag = control_tag_for_end(op)
            if start_tag is not None:
                if op == 0x04:
                    halfwidth_depth += 1
                if op == 0xE2:
                    private_depth += 1
                if not private_depth or op == 0xE2:
                    if start_tag != "private":
                        tokens.append(StartTag(start_tag))
                if start_tag in {"link", "url"}:
                    stats["links"] += 1
            elif end_tag is not None:
                if op == 0x05 and halfwidth_depth:
                    halfwidth_depth -= 1
                if op == 0xE3:
                    if private_depth:
                        private_depth -= 1
                elif not private_depth:
                    tokens.append(EndTag(end_tag))
            elif op not in (0x04, 0x05) and op not in KNOWN_NONPRINTING_CONTROLS:
                stats["unknown_controls"] += 1
            i += 2 + arg_len
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            text = decode_jis_pair(data[i : i + 2])
            if text:
                stats["jis_pairs"] += 1
                if not private_depth:
                    tokens.append(Text(normalize_fullwidth_ascii(text) if halfwidth_depth else text))
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            key = f"{b:02x}{data[i + 1]:02x}"
            if not private_depth:
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
        "halfwidth": "span",
        "literal": "span",
        "url": "span",
    }
    attrs = {
        "head": ' class="lv-head"',
        "color": ' class="lv-color"',
        "halfwidth": ' class="lv-halfwidth"',
        "literal": ' class="lv-literal"',
        "url": ' class="lv-url"',
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
    content = re.sub(r"<section:[0-9A-Fa-f]{4}>", "", compact).strip()
    content = re.sub(r"<media:[0-9A-Fa-f]+>", "", content).strip()
    if not content:
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{8,16}", compact):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{6,16}(?:\n[0-9A-Fa-f]{6,16})+", compact):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{8,16}", content):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{6,16}(?:\n[0-9A-Fa-f]{6,16})+", content):
        return True
    if re.fullmatch(r"[A-Za-z0-9+/]{6,24}={0,2}", content):
        return True
    return False


def iter_entry_slices(data: bytes) -> Iterable[tuple[int, int]]:
    return iter_entry_slices_with_boundaries(data)


def iter_entry_marker_offsets_reader(reader: SsedRandomReader) -> Iterable[int]:
    """Yield HONMON entry marker offsets without expanding the whole component."""

    carry = b""
    carry_base = 0
    emitted: set[int] = set()
    tail_size = len(ENTRY_MARKER) + 2 - 1
    for offset in range(0, reader.expanded_size, CHUNK_SIZE):
        chunk = reader.read(offset, min(CHUNK_SIZE, reader.expanded_size - offset))
        if not chunk:
            break
        buffer = carry + chunk
        base = carry_base
        pos = buffer.find(ENTRY_MARKER)
        while pos != -1:
            absolute = base + pos
            start = absolute - 2 if pos >= 2 and buffer[pos - 2 : pos] == b"\x1f\x02" else absolute
            if start not in emitted:
                emitted.add(start)
                yield start
            pos = buffer.find(ENTRY_MARKER, pos + 1)
        if len(buffer) >= tail_size:
            carry = buffer[-tail_size:]
            carry_base = base + len(buffer) - tail_size
        else:
            carry = buffer
            carry_base = base


def iter_entry_slices_reader(reader: SsedRandomReader) -> Iterable[tuple[int, int]]:
    offsets = iter(iter_entry_marker_offsets_reader(reader))
    try:
        previous = next(offsets)
    except StopIteration:
        if reader.expanded_size:
            yield 0, reader.expanded_size
        return
    for current in offsets:
        if current > previous:
            yield previous, current
        previous = current
    if previous < reader.expanded_size:
        yield previous, reader.expanded_size


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


def _dictionary_source_from_idx(payload: Path | tuple[Path, bool, bool]) -> DictionarySource | None:
    if isinstance(payload, tuple):
        idx, include_gaiji, include_images = payload
    else:
        idx = payload
        include_gaiji = True
        include_images = True
    try:
        title, elements = parse_ssedinfo(idx)
    except Exception:
        return None
    honmon_element = honmon_component(elements)
    if honmon_element is None:
        return None
    honmon = find_case_insensitive(idx.parent, honmon_element.filename)
    if honmon is None:
        return None
    dict_id = idx.parent.parent.name if idx.parent.name == idx.parent.parent.name else idx.stem
    gaiji_profile = load_gaiji_profile(idx) if include_gaiji else None
    image_profile = load_image_resource_profile(idx) if include_images else None
    image_sources = {}
    if image_profile is not None:
        for key, resource in image_profile.resources.items():
            selected = resource.normal or resource.default or resource.white
            if selected is not None:
                image_sources[key] = relative_image_source(selected, idx)
    return DictionarySource(
        dict_id=dict_id,
        idx=idx,
        title=title,
        honmon=honmon,
        honmon_start_block=honmon_element.start,
        honmon_storage=sseddata_storage_for_file(honmon),
        gaiji_loaded=gaiji_profile is not None,
        gaiji_map=gaiji_profile.map if gaiji_profile is not None else {},
        gaiji_uni_entries=gaiji_profile.uni_entries if gaiji_profile is not None else 0,
        gaiji_plist_entries=gaiji_profile.plist_entries if gaiji_profile is not None else 0,
        image_resources_loaded=image_profile is not None,
        image_resource_entries=len(image_profile.resources) if image_profile is not None else 0,
        image_gaiji_keys=image_profile.gaiji_image_keys if image_profile is not None else frozenset(),
        image_sources=image_sources,
        image_dirs=image_profile.image_dirs if image_profile is not None else (),
    )


def _candidate_dict_keys(idx: Path) -> set[str]:
    keys = {idx.stem, idx.parent.name, idx.parent.name.removeprefix("_DCT_")}
    if idx.parent.name == idx.parent.parent.name:
        keys.add(idx.parent.parent.name)
        keys.add(idx.parent.parent.name.removeprefix("_DCT_"))
    return {key for key in keys if key}


def discover_dictionaries(
    roots: list[Path],
    *,
    jobs: int | None = 1,
    dict_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    include_gaiji: bool = True,
    include_images: bool = True,
) -> list[DictionarySource]:
    selected = {value.lower() for value in dict_ids or []}
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidates.extend(iter_files_with_suffix(root, ".idx", recursive=root.is_dir()))

    unique_candidates: list[Path] = []
    for idx in sorted(candidates):
        if selected and not ({key.lower() for key in _candidate_dict_keys(idx)} & selected):
            continue
        resolved = idx.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_candidates.append(resolved)

    rows = parallel_map_ordered(
        _dictionary_source_from_idx,
        [(candidate, include_gaiji, include_images) for candidate in unique_candidates],
        jobs=jobs,
    )
    return [row for row in rows if row is not None]


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _entry_row_display(row: dict[str, Any], fmt: str) -> str:
    if fmt == "jsonl":
        return json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    heading = str(row.get("heading") or "").strip()
    label = f"{row.get('dict_id', '')} #{row.get('entry_index', '')}".strip()
    if heading:
        label = f"{label} {heading}".strip()
    if fmt == "html":
        body = str(row.get("body_html") or row.get("body") or "")
        return f"<!-- {label} -->\n{body}".rstrip()
    body = str(row.get("body") or row.get("body_html") or "")
    return f"## {label}\n{body}".rstrip()


def print_entries_to_terminal(summary: dict[str, Any], fmt: str = "text") -> int:
    """Print emitted entry rows from a summary JSONL path to stdout."""

    entries_path = Path(str(summary.get("entries_path") or ""))
    if not entries_path.is_file():
        print(f"entries: no entry JSONL file found for {summary.get('dict_id', '<unknown>')}: {entries_path}", file=sys.stderr)
        return 0
    emitted = 0
    with entries_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            if fmt == "jsonl":
                print(line)
            else:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    print(line)
                else:
                    if emitted:
                        print()
                    print(_entry_row_display(row, fmt))
            emitted += 1
    sys.stdout.flush()
    return emitted


def entry_marker_status_text(summary: dict[str, Any]) -> str:
    if summary.get("entry_markers_complete"):
        return str(summary.get("entry_markers") or 0)
    seen = summary.get("entry_markers_seen")
    if seen is None:
        return "unknown"
    return f"seen={seen}"


def _empty_stats() -> dict[str, int]:
    return {
        "controls": 0,
        "unknown_controls": 0,
        "gaiji": 0,
        "image_gaiji": 0,
        "media": 0,
        "sections": 0,
        "links": 0,
        "jis_pairs": 0,
        "legacy_controls": 0,
    }


def _summary(
    source: DictionarySource,
    entries_path: Path,
    *,
    honmon_storage: str,
    expanded_bytes: int,
    entry_markers: int | None,
    entry_markers_seen: int,
    entry_markers_complete: bool,
    emitted: int,
    skipped_empty: int,
    stats: dict[str, int],
    warnings: list[str],
    index_boundary_offsets: int = 0,
) -> dict[str, Any]:
    return {
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "idx": str(source.idx),
        "honmon": str(source.honmon),
        "honmon_start_block": source.honmon_start_block,
        "honmon_storage": honmon_storage,
        "expanded_bytes": expanded_bytes,
        "entry_markers": entry_markers,
        "entry_markers_seen": entry_markers_seen,
        "entry_markers_complete": entry_markers_complete,
        "index_entry_boundaries": index_boundary_offsets,
        "entries_emitted": emitted,
        "entries_skipped_empty": skipped_empty,
        "stats": stats,
        "warnings": warnings,
        "gaiji_loaded": source.gaiji_loaded,
        "gaiji_map_entries": len(source.gaiji_map),
        "gaiji_uni_entries": source.gaiji_uni_entries,
        "gaiji_plist_entries": source.gaiji_plist_entries,
        "image_resources_loaded": source.image_resources_loaded,
        "image_resource_entries": source.image_resource_entries,
        "image_gaiji_entries": len(source.image_gaiji_keys),
        "image_dirs": [str(path) for path in source.image_dirs],
        "entries_path": str(entries_path),
    }


def extract_dictionary_streaming(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    entries_path = dict_out / "raw_entries.jsonl"
    section_image_sources = resolve_section_image_sources(getattr(args, "section_image", None), source.image_sources)

    reader = SsedRandomReader(source.honmon)
    status(
        args,
        f"entries: {source.dict_id}: streaming HONMON storage={reader.storage} expanded_bytes={reader.expanded_size}",
        verbose=True,
    )
    warnings: list[str] = []
    aggregate_stats = _empty_stats()
    sample_size = min(reader.expanded_size, 256 * 1024)
    sample = reader.read(0, sample_size)
    sample_markers = sample.count(ENTRY_MARKER)
    dense_marker_honmon = sample_markers > 0 and sample_markers * 64 > max(sample_size, 1)
    if dense_marker_honmon and args.skip_dense_marker_honmon:
        status(args, f"entries: {source.dict_id}: skipped dense marker-like HONMON sample", verbose=True)
        warnings.append(
            "HONMON sample has a dense 32-byte-ish entry-marker pattern; it appears to "
            "be an anchor/id table rather than body text. Skipped HONMON body extraction."
        )
        entries_path.write_text("", encoding="utf-8")
        summary = _summary(
            source,
            entries_path,
            honmon_storage=reader.storage,
            expanded_bytes=reader.expanded_size,
            entry_markers=None,
            entry_markers_seen=sample_markers,
            entry_markers_complete=False,
            emitted=0,
            skipped_empty=0,
            stats=aggregate_stats,
            warnings=warnings,
        )
        write_json(dict_out / "summary.json", summary)
        status(
            args,
            f"entries: {source.dict_id}: emitted=0 markers={entry_marker_status_text(summary)} entries_path={entries_path}",
            verbose=True,
        )
        return summary

    emitted = 0
    skipped_empty = 0
    marker_offsets_seen = 0
    markers_complete = False

    def emit_slice(out, entry_index: int, start: int, end: int) -> bool:
        nonlocal emitted, skipped_empty
        segment = reader.read(start, end - start)
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
            return False
        for key, value in stats.items():
            aggregate_stats[key] = aggregate_stats.get(key, 0) + value
        block = source.honmon_start_block + start // BLOCK_SIZE
        offset = start % BLOCK_SIZE
        item = {
            "dict_id": source.dict_id,
            "dict_title": source.title,
            "entry_index": entry_index,
            "block": block,
            "offset": offset,
            "length": end - start,
            "heading": extract_heading(tokens, body),
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
        return True

    with entries_path.open("w", encoding="utf-8") as out:
        marker_iter = iter(iter_entry_marker_offsets_reader(reader))
        try:
            previous = next(marker_iter)
            marker_offsets_seen = 1
        except StopIteration:
            markers_complete = True
            if reader.expanded_size:
                emit_slice(out, 1, 0, reader.expanded_size)
        else:
            entry_index = 1
            for current in marker_iter:
                marker_offsets_seen += 1
                if current > previous:
                    emit_slice(out, entry_index, previous, current)
                    entry_index += 1
                    if args.limit and emitted >= args.limit:
                        break
                previous = current
            else:
                markers_complete = True
                if previous < reader.expanded_size and (not args.limit or emitted < args.limit):
                    emit_slice(out, entry_index, previous, reader.expanded_size)

    summary = _summary(
        source,
        entries_path,
        honmon_storage=reader.storage,
        expanded_bytes=reader.expanded_size,
        entry_markers=marker_offsets_seen if markers_complete else None,
        entry_markers_seen=marker_offsets_seen,
        entry_markers_complete=markers_complete,
        emitted=emitted,
        skipped_empty=skipped_empty,
        stats=aggregate_stats,
        warnings=warnings,
    )
    write_json(dict_out / "summary.json", summary)
    status(
        args,
        f"entries: {source.dict_id}: emitted={emitted} markers={entry_marker_status_text(summary)} entries_path={entries_path}",
        verbose=True,
    )
    return summary


def extract_dictionary(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    full_scan = bool(getattr(args, "full_scan", False) or getattr(args, "debug", False))
    if not full_scan and not getattr(args, "index_boundaries", False):
        return extract_dictionary_streaming(source, out_dir, args)
    status(args, f"entries: {source.dict_id}: using full forensic HONMON scan", verbose=True)

    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    entries_path = dict_out / "raw_entries.jsonl"
    section_image_sources = resolve_section_image_sources(getattr(args, "section_image", None), source.image_sources)

    expanded, honmon_storage = expand_sseddata_file_with_storage(source.honmon)
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
        "legacy_controls": 0,
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
            "honmon_storage": honmon_storage,
            "expanded_bytes": len(expanded),
            "entry_markers": marker_count,
            "entry_markers_seen": marker_count,
            "entry_markers_complete": True,
            "index_entry_boundaries": 0,
            "entries_emitted": 0,
            "entries_skipped_empty": 0,
            "stats": aggregate_stats,
            "warnings": warnings,
            "gaiji_loaded": source.gaiji_loaded,
            "gaiji_map_entries": len(source.gaiji_map),
            "gaiji_uni_entries": source.gaiji_uni_entries,
            "gaiji_plist_entries": source.gaiji_plist_entries,
            "image_resources_loaded": source.image_resources_loaded,
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
                aggregate_stats[key] = aggregate_stats.get(key, 0) + value
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
        "honmon_storage": honmon_storage,
        "expanded_bytes": len(expanded),
        "entry_markers": marker_count,
        "entry_markers_seen": marker_count,
        "entry_markers_complete": True,
        "index_entry_boundaries": len(index_boundary_offsets),
        "entries_emitted": emitted,
        "entries_skipped_empty": skipped_empty,
        "stats": aggregate_stats,
        "warnings": warnings,
        "gaiji_loaded": source.gaiji_loaded,
        "gaiji_map_entries": len(source.gaiji_map),
        "gaiji_uni_entries": source.gaiji_uni_entries,
        "gaiji_plist_entries": source.gaiji_plist_entries,
        "image_resources_loaded": source.image_resources_loaded,
        "image_resource_entries": source.image_resource_entries,
        "image_gaiji_entries": len(source.image_gaiji_keys),
        "image_dirs": [str(path) for path in source.image_dirs],
        "entries_path": str(entries_path),
    }
    write_json(dict_out / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_entries_args(parser)
    args = parser.parse_args()

    roots = args.root or [Path(".")]
    sources = discover_dictionaries(
        roots,
        jobs=args.jobs,
        dict_ids=args.dict,
        include_images=bool(args.image_gaiji or args.html or args.section_image),
    )
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
            f"  entries={summary['entries_emitted']} markers={entry_marker_status_text(summary)} "
            f"bytes={summary['expanded_bytes']}",
            file=sys.stderr,
        )

    write_json(args.out_dir / "summary.json", summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
