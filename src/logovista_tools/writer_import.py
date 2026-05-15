"""Import common dictionary source formats into writer-v0 plain SSED packages."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
import zipfile
from io import BytesIO
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .writer import (
    BodyControl,
    BodyMarkup,
    BodyMedia,
    CompressionMode,
    FULL_GAIJI_START,
    FontFallbackGlyphRenderer,
    GaijiAllocator,
    HALF_GAIJI_START,
    VectorGlyphRenderer,
    WriterEntry,
    build_plain_honmon_package,
    encode_search_key,
    write_plain_package,
)


BOLD_START = BodyControl(b"\x1f\xe0\x00\x04")
BOLD_END = BodyControl(b"\x1f\xe1")
SUB_START = BodyControl(b"\x1f\x06")
SUB_END = BodyControl(b"\x1f\x07")
SUP_START = BodyControl(b"\x1f\x0e")
SUP_END = BodyControl(b"\x1f\x0f")
ITALIC_START = BodyControl(b"\x1f\x10")
ITALIC_END = BodyControl(b"\x1f\x11")
EM_START = BodyControl(b"\x1f\x12")
EM_END = BodyControl(b"\x1f\x13")
LITERAL_START = BodyControl(b"\x1f\x0b")
LITERAL_END = BodyControl(b"\x1f\x0c")

BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "tfoot",
    "thead",
    "tr",
    "ul",
}
INLINE_SUPPORTED_TAGS = {
    "a",
    "abbr",
    "b",
    "br",
    "cite",
    "code",
    "data",
    "dfn",
    "em",
    "i",
    "img",
    "kbd",
    "mark",
    "object",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "td",
    "th",
    "time",
    "u",
    "var",
    "wbr",
    "rn",
}


@dataclass
class MarkupStats:
    html_tags: Counter[str] = field(default_factory=Counter)
    structured_tags: Counter[str] = field(default_factory=Counter)
    unsupported_tags: Counter[str] = field(default_factory=Counter)
    flattened_images: int = 0
    embedded_images: int = 0
    missing_images: int = 0
    converted_images: int = 0
    flattened_links: int = 0
    controls: Counter[str] = field(default_factory=Counter)

    def merge(self, other: "MarkupStats") -> None:
        self.html_tags.update(other.html_tags)
        self.structured_tags.update(other.structured_tags)
        self.unsupported_tags.update(other.unsupported_tags)
        self.flattened_images += other.flattened_images
        self.embedded_images += other.embedded_images
        self.missing_images += other.missing_images
        self.converted_images += other.converted_images
        self.flattened_links += other.flattened_links
        self.controls.update(other.controls)

    def as_dict(self) -> dict[str, Any]:
        return {
            "html_tags": dict(sorted(self.html_tags.items())),
            "structured_tags": dict(sorted(self.structured_tags.items())),
            "unsupported_tags": dict(sorted(self.unsupported_tags.items())),
            "flattened_images": self.flattened_images,
            "embedded_images": self.embedded_images,
            "missing_images": self.missing_images,
            "converted_images": self.converted_images,
            "flattened_links": self.flattened_links,
            "controls": dict(sorted(self.controls.items())),
        }


class BodyBuilder:
    def __init__(self, stats: MarkupStats) -> None:
        self.parts: list[str | BodyControl] = []
        self.stats = stats

    def text(self, value: Any) -> None:
        if value is None:
            return
        text = html.unescape(str(value)).replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        if self.parts and isinstance(self.parts[-1], str):
            self.parts[-1] += text
        else:
            self.parts.append(text)

    def newline(self) -> None:
        if not self.parts:
            return
        if isinstance(self.parts[-1], str):
            if self.parts[-1].endswith("\n"):
                return
            self.parts[-1] += "\n"
        else:
            self.parts.append("\n")

    def control(self, control: BodyControl, label: str) -> None:
        self.parts.append(control)
        self.stats.controls[label] += 1

    def media(self, media: BodyMedia) -> None:
        self.parts.append(media)
        self.stats.embedded_images += 1

    def markup(self) -> BodyMarkup:
        collapsed: list[str | BodyControl] = []
        for part in self.parts:
            if isinstance(part, str):
                text = re.sub(r"[ \t\f\v]+\n", "\n", part)
                text = re.sub(r"\n{3,}", "\n\n", text)
                if not text:
                    continue
                if collapsed and isinstance(collapsed[-1], str):
                    collapsed[-1] += text
                else:
                    collapsed.append(text)
            else:
                collapsed.append(part)
        while collapsed and isinstance(collapsed[0], str) and not collapsed[0].strip():
            collapsed.pop(0)
        while collapsed and isinstance(collapsed[-1], str) and not collapsed[-1].strip():
            collapsed.pop()
        if not collapsed:
            collapsed.append("")
        return BodyMarkup(tuple(collapsed))


def _style_controls(tag: str, attrs: dict[str, str], stats: MarkupStats) -> list[tuple[BodyControl, BodyControl, str]]:
    controls: list[tuple[BodyControl, BodyControl, str]] = []
    style = attrs.get("style", "").replace(" ", "").lower()
    cls = attrs.get("class", "").lower()

    if tag in {"b", "strong"} or "font-weight:bold" in style or "fontweight:bold" in style:
        controls.append((BOLD_START, BOLD_END, "bold"))
    if tag == "sub" or "vertical-align:sub" in style:
        controls.append((SUB_START, SUB_END, "sub"))
    if tag == "sup" or tag == "rt" or "vertical-align:super" in style or "vertical-align:top" in style:
        controls.append((SUP_START, SUP_END, "sup"))
    if tag == "i":
        controls.append((ITALIC_START, ITALIC_END, "italic"))
    if tag == "em":
        controls.append((EM_START, EM_END, "em"))
    if tag in {"code", "kbd", "pre", "samp"}:
        controls.append((LITERAL_START, LITERAL_END, "literal"))
    if "rubi" in cls and tag in {"span", "sub"} and all(label not in {"sub", "sup"} for _start, _end, label in controls):
        controls.append((SUB_START, SUB_END, "sub"))
    return controls


def _mime_from_bytes(path: str, data: bytes) -> str:
    lower = path.lower()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".bmp"):
        return "image/bmp"
    if lower.endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"


def _image_has_alpha(image: Any) -> bool:
    if image.mode in {"RGBA", "LA"}:
        return True
    if image.mode == "P" and "transparency" in image.info:
        return True
    return False


def _convert_webp_image_bytes(data: bytes) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional local writer tooling
        raise RuntimeError("Pillow is required to convert non-SSED image payloads") from exc
    with Image.open(BytesIO(data)) as image:
        out = BytesIO()
        if _image_has_alpha(image):
            if image.mode not in {"RGBA", "LA"}:
                image = image.convert("RGBA")
            image.save(out, format="PNG")
            return out.getvalue(), "image/png"
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue(), "image/jpeg"


class YomitanMediaResolver:
    def __init__(self, archive: zipfile.ZipFile, stats: MarkupStats) -> None:
        self.archive = archive
        self.stats = stats
        self._names = set(archive.namelist())
        self._cache: dict[str, BodyMedia | None] = {}

    def _resolve_name(self, path: str) -> str | None:
        normalized = path.strip().replace("\\", "/").lstrip("./")
        if normalized in self._names:
            return normalized
        lowered = normalized.casefold()
        for name in self._names:
            if name.casefold() == lowered:
                return name
        return None

    def __call__(self, path: str, label: str = "") -> BodyMedia | None:
        if not path:
            return None
        name = self._resolve_name(path)
        if name is None:
            self.stats.missing_images += 1
            return None
        cached = self._cache.get(name)
        if cached is not None or name in self._cache:
            return cached
        data = self.archive.read(name)
        mime = _mime_from_bytes(name, data)
        converted_from: str | None = None
        if mime == "image/webp":
            data, mime = _convert_webp_image_bytes(data)
            converted_from = "image/webp"
            self.stats.converted_images += 1
        digest = hashlib.sha1(data).hexdigest()[:16]
        media = BodyMedia(
            resource_key=f"{name}:{digest}",
            payload=data,
            mime_type=mime,
            label=clean_headword(label) or Path(name).name,
            source_path=name,
            converted_from=converted_from,
        )
        self._cache[name] = media
        return media


class SsedHtmlParser(HTMLParser):
    def __init__(self, stats: MarkupStats, media_resolver: Any | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.stats = stats
        self.media_resolver = media_resolver
        self.builder = BodyBuilder(stats)
        self.close_stack: list[list[tuple[BodyControl, str]]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs = {key.lower(): value or "" for key, value in attrs_list}
        self.stats.html_tags[tag] += 1
        if tag not in BLOCK_TAGS and tag not in INLINE_SUPPORTED_TAGS:
            self.stats.unsupported_tags[tag] += 1

        if tag in BLOCK_TAGS:
            self.builder.newline()
        if tag == "li":
            self.builder.text("・")
        if tag in {"td", "th"}:
            self.builder.text("　")
        if tag in {"br", "rn", "wbr", "hr"}:
            self.builder.newline()
            self.close_stack.append([])
            return
        if tag in {"img", "object"}:
            src = attrs.get("src") or attrs.get("data") or ""
            alt = attrs.get("alt") or attrs.get("title") or src
            media = self.media_resolver(src, alt) if self.media_resolver and src else None
            if media is not None:
                self.builder.media(media)
            else:
                if alt:
                    self.builder.text(f"［{alt}］")
                self.stats.flattened_images += 1
            self.close_stack.append([])
            return
        if tag == "a" and attrs.get("href"):
            self.stats.flattened_links += 1

        close_controls: list[tuple[BodyControl, str]] = []
        for start, end, label in _style_controls(tag, attrs, self.stats):
            self.builder.control(start, f"{label}_start")
            close_controls.append((end, f"{label}_end"))
        self.close_stack.append(close_controls)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        close_controls = self.close_stack.pop() if self.close_stack else []
        for control, label in reversed(close_controls):
            self.builder.control(control, label)
        if tag in BLOCK_TAGS:
            self.builder.newline()

    def handle_data(self, data: str) -> None:
        self.builder.text(data)


class HeadwordHtmlParser(HTMLParser):
    """Extract search/title text from source headword markup.

    KOUJIEN-style CSV exports can place renderer HTML directly in the title
    column, including ``object`` nodes for inline gaiji/icons. Those bytes are
    useful body evidence, but they are not valid LogoVista lookup keys. For the
    writer v0 importer we keep textual child content such as ruby/superscript
    and drop image/object placeholders from headword/search keys.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.html_tags: Counter[str] = Counter()
        self.dropped_images = 0

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.html_tags[tag] += 1
        if tag in {"br", "rn", "wbr", "hr"}:
            self.parts.append(" ")
        if tag in {"img", "object"}:
            self.dropped_images += 1

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return clean_headword("".join(self.parts))


def html_to_body_markup(source: str, stats: MarkupStats | None = None, media_resolver: Any | None = None) -> BodyMarkup:
    stats = stats or MarkupStats()
    parser = SsedHtmlParser(stats, media_resolver=media_resolver)
    parser.feed(source)
    parser.close()
    return parser.builder.markup()


def structured_content_to_body_markup(value: Any, stats: MarkupStats | None = None, media_resolver: Any | None = None) -> BodyMarkup:
    stats = stats or MarkupStats()
    builder = BodyBuilder(stats)

    def visit(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            if "<" in node and ">" in node:
                nested = html_to_body_markup(node, stats, media_resolver=media_resolver)
                for part in nested.parts:
                    if isinstance(part, BodyControl):
                        builder.control(part, "raw_control")
                    elif isinstance(part, BodyMedia):
                        builder.media(part)
                    else:
                        builder.text(part)
            else:
                builder.text(node)
            return
        if isinstance(node, (int, float, bool)):
            builder.text(str(node))
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            builder.text(str(node))
            return

        if node.get("type") == "structured-content":
            visit(node.get("content"))
            return
        if node.get("type") == "image":
            path = str(node.get("path") or node.get("src") or "")
            alt = str(node.get("title") or node.get("description") or path or "")
            media = media_resolver(path, alt) if media_resolver and path else None
            if media is not None:
                builder.media(media)
            else:
                if alt:
                    builder.text(f"［{alt}］")
                stats.flattened_images += 1
            return

        tag = str(node.get("tag") or "").lower()
        content = node.get("content")
        attrs: dict[str, str] = {}
        if isinstance(node.get("style"), dict):
            style = node["style"]
            attrs["style"] = ";".join(f"{k}:{v}" for k, v in style.items())
        if isinstance(node.get("data"), dict):
            name = node["data"].get("name")
            if name:
                attrs["class"] = str(name)

        if tag:
            stats.structured_tags[tag] += 1
        if tag and tag not in BLOCK_TAGS and tag not in INLINE_SUPPORTED_TAGS:
            stats.unsupported_tags[tag] += 1
        if tag in BLOCK_TAGS:
            builder.newline()
        if tag == "li":
            builder.text("・")
        if tag in {"td", "th"}:
            builder.text("　")
        if tag in {"br", "rn", "wbr", "hr"}:
            builder.newline()
            return
        if tag in {"img", "object"}:
            data = node.get("data")
            path = ""
            if isinstance(data, dict):
                path = str(data.get("path") or data.get("src") or data.get("name") or "")
            elif isinstance(data, str):
                path = data
            path = str(node.get("src") or node.get("path") or path or "")
            alt = str(node.get("alt") or node.get("title") or path or "")
            media = media_resolver(path, alt) if media_resolver and path else None
            if media is not None:
                builder.media(media)
            else:
                if alt:
                    builder.text(f"［{alt}］")
                stats.flattened_images += 1
            return
        if tag == "a":
            stats.flattened_links += 1

        close_controls: list[tuple[BodyControl, str]] = []
        for start, end, label in _style_controls(tag, attrs, stats):
            builder.control(start, f"{label}_start")
            close_controls.append((end, f"{label}_end"))
        visit(content)
        for control, label in reversed(close_controls):
            builder.control(control, label)
        if tag in BLOCK_TAGS:
            builder.newline()

    visit(value)
    return builder.markup()


@dataclass
class ImportAccumulator:
    merge_duplicates: bool = True
    rows_read: int = 0
    rows_skipped: int = 0
    long_key_drops: int = 0
    duplicate_rows_merged: int = 0
    search_keys_emitted: int = 0
    search_aliases_emitted: int = 0
    headword_html_tags: Counter[str] = field(default_factory=Counter)
    headword_images_dropped: int = 0
    markup_stats: MarkupStats = field(default_factory=MarkupStats)
    bank_rows: Counter[str] = field(default_factory=Counter)
    _entries: dict[str, WriterEntry] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def add(self, headword: str, body: BodyMarkup, search_keys: Iterable[str] = (), *, include_headword_key: bool = True) -> None:
        headword = clean_headword(headword)
        if not headword:
            self.rows_skipped += 1
            return
        expanded_keys: list[str] = []
        for key in (search_keys or (headword,)):
            expanded_keys.extend(lookup_key_aliases(key))
        keys = tuple(dict.fromkeys(key for key in expanded_keys if key))
        if include_headword_key and headword not in keys:
            keys = (headword, *keys)
        valid_keys: list[str] = []
        probe = GaijiAllocator()
        for key in keys:
            try:
                encode_search_key(key, probe)
            except ValueError:
                self.long_key_drops += 1
                continue
            valid_keys.append(key)
        if not valid_keys:
            self.rows_skipped += 1
            return
        self.search_keys_emitted += len(valid_keys)
        self.search_aliases_emitted += len(valid_keys) - (1 if include_headword_key and headword in valid_keys else 0)
        if self.merge_duplicates and headword in self._entries:
            existing = self._entries[headword]
            merged_keys = tuple(dict.fromkeys((*existing.keys, *valid_keys)))
            merged_body = merge_bodies(existing.body, body)
            self._entries[headword] = WriterEntry(headword=headword, body=merged_body, search_keys=merged_keys)
            self.duplicate_rows_merged += 1
            return
        key = headword
        if key in self._entries:
            suffix = 2
            while f"{headword}\u0000{suffix}" in self._entries:
                suffix += 1
            key = f"{headword}\u0000{suffix}"
        self._order.append(key)
        self._entries[key] = WriterEntry(headword=headword, body=body, search_keys=tuple(valid_keys))

    def entries(self) -> list[WriterEntry]:
        return [self._entries[key] for key in self._order]

    def clean_title(self, value: str) -> str:
        text = str(value or "")
        if "<" not in text or ">" not in text:
            return clean_headword(text)
        parser = HeadwordHtmlParser()
        parser.feed(text)
        parser.close()
        self.headword_html_tags.update(parser.html_tags)
        self.headword_images_dropped += parser.dropped_images
        return parser.text()


def katakana_to_hiragana(value: str) -> str:
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "ヴ":
            # Historic Japanese indexes commonly avoid U+3094, which is not
            # encodable in the JIS cell set used by SSED indexes.
            out.append("う")
        elif 0x30A1 <= code <= 0x30FA:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


SEARCH_IGNORABLE_CHARS = {
    " ",
    "\u3000",
    "\t",
    "\n",
    "\r",
    "-",
    "\u2010",  # hyphen
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
    "\u2015",
    "\u2212",
    "\uff0d",
    "\u30fb",
    "\uff65",
    "・",
    "_",
    "\uff3f",
    ".",
    "\uff0e",
    "\u3002",
    ",",
    "\uff0c",
    "\u3001",
    "/",
    "\uff0f",
    "\\",
    "\uff3c",
    ":",
    "\uff1a",
    ";",
    "\uff1b",
    "'",
    "\u2018",
    "\u2019",
    "\u02bc",
    '"',
    "\u201c",
    "\u201d",
    "(",
    ")",
    "\uff08",
    "\uff09",
    "[",
    "]",
    "\uff3b",
    "\uff3d",
    "{",
    "}",
    "\uff5b",
    "\uff5d",
    "「",
    "」",
    "『",
    "』",
    "【",
    "】",
    "［",
    "］",
    "〈",
    "〉",
    "《",
    "》",
}


def is_lookup_ignorable(ch: str) -> bool:
    if ch in SEARCH_IGNORABLE_CHARS:
        return True
    category = unicodedata.category(ch)
    return category.startswith("Z") or category.startswith("P") or category == "Cf"


def normalize_lookup_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", clean_headword(value))
    text = katakana_to_hiragana(text)
    return "".join(ch for ch in text if not is_lookup_ignorable(ch))


def lookup_key_aliases(value: str, *, include_raw: bool = True) -> tuple[str, ...]:
    text = clean_headword(value)
    normalized = normalize_lookup_key(text)
    aliases: list[str] = []
    if include_raw and text:
        aliases.append(text)
    if normalized and (normalized != text or not include_raw):
        aliases.append(normalized)
    return tuple(dict.fromkeys(aliases))


def strip_koujien_title_labels(value: str) -> str:
    text = clean_headword(value)
    text = re.sub(r"【.*?】", "", text)
    text = re.sub(r"［.*?］", "", text)
    text = re.sub(r"\\[.*?\\]", "", text)
    text = re.sub(r"（[^（）]*詞）$", "", text)
    text = re.sub(r"（音節）$", "", text)
    return clean_headword(text)


def koujien_title_search_keys(title: str) -> tuple[str, ...]:
    base = strip_koujien_title_labels(title)
    keys: list[str] = []

    keys.extend(lookup_key_aliases(base, include_raw=False))

    # If the title has a bracketed kanji/variant spelling, preserve that as an
    # additional lookup form.  We intentionally do not split on Japanese middle
    # dots inside this bracket, because those often express orthographic
    # alternatives whose exact policy varies by source.
    for match in re.finditer(r"【(.*?)】", title):
        bracket = clean_headword(match.group(1))
        if bracket:
            keys.extend(lookup_key_aliases(bracket, include_raw=False))

    return tuple(dict.fromkeys(key for key in keys if key))


def clean_headword(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u0000", "")).strip()


def merge_bodies(left: Any, right: BodyMarkup) -> BodyMarkup:
    left_markup = left if isinstance(left, BodyMarkup) else BodyMarkup.text(str(left))
    return BodyMarkup((*left_markup.parts, "\n---\n", *right.parts))


def iter_koujien_csv(path: Path, accumulator: ImportAccumulator, *, limit: int | None = None, progress_every: int = 10000) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=",", quotechar='"')
        for row in reader:
            accumulator.rows_read += 1
            title = row.get("Title") or row.get("title") or ""
            html_body = row.get("Html") or row.get("HTML") or row.get("html") or ""
            stats = MarkupStats()
            body = html_to_body_markup(html_body, stats)
            accumulator.markup_stats.merge(stats)
            clean_title = accumulator.clean_title(title)
            accumulator.add(clean_title, body, koujien_title_search_keys(clean_title), include_headword_key=False)
            if progress_every and accumulator.rows_read % progress_every == 0:
                print(f"import csv rows={accumulator.rows_read} entries={len(accumulator._entries)}", file=sys.stderr)
            if limit and accumulator.rows_read >= limit:
                break


YOMITAN_ENTRY_BANK_RE = re.compile(r"(^|/)(term|term_meta|kanji|kanji_meta)_bank_(\d+)\.json$")


def sorted_yomitan_entry_banks(names: Iterable[str], *, kinds: set[str] | None = None) -> list[tuple[str, str]]:
    def key(name: str) -> tuple[int, str]:
        match = YOMITAN_ENTRY_BANK_RE.search(name)
        return (int(match.group(3)) if match else 10**9, name)

    banks: list[tuple[str, str]] = []
    for name in names:
        match = YOMITAN_ENTRY_BANK_RE.search(name)
        if not match:
            continue
        kind = match.group(2)
        if kinds is not None and kind not in kinds:
            continue
        banks.append((kind, name))
    return sorted(banks, key=lambda item: key(item[1]))


def sorted_term_banks(names: Iterable[str]) -> list[str]:
    return [name for _kind, name in sorted_yomitan_entry_banks(names, kinds={"term"})]


def metadata_value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_headword(value)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(part for part in (metadata_value_to_text(item) for item in value) if part)
    if isinstance(value, dict):
        if "displayValue" in value:
            display = metadata_value_to_text(value.get("displayValue"))
            if display:
                return display
        if "value" in value:
            display = metadata_value_to_text(value.get("value"))
            if display:
                return display
        compact = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return clean_headword(compact)
    return clean_headword(str(value))


def metadata_lines(mode: str, data: Any) -> list[str]:
    mode = clean_headword(mode)
    lines = [f"［{mode}］"] if mode else []
    if isinstance(data, dict):
        if mode == "freq":
            reading = metadata_value_to_text(data.get("reading"))
            if reading:
                lines.append(f"Reading: {reading}")
            frequency = data.get("frequency", data)
            value = metadata_value_to_text(frequency)
            if value:
                lines.append(f"Frequency: {value}")
            return lines
        if mode == "pitch":
            reading = metadata_value_to_text(data.get("reading"))
            if reading:
                lines.append(f"Reading: {reading}")
            pitches = data.get("pitches")
            if isinstance(pitches, list):
                parts: list[str] = []
                for pitch in pitches:
                    if isinstance(pitch, dict):
                        position = pitch.get("position")
                        tags = metadata_value_to_text(pitch.get("tags") or pitch.get("nasal") or pitch.get("devoice"))
                        part = f"position={position}" if position is not None else metadata_value_to_text(pitch)
                        if tags:
                            part = f"{part} {tags}"
                        parts.append(part)
                    else:
                        parts.append(metadata_value_to_text(pitch))
                if parts:
                    lines.append("Pitch: " + "; ".join(part for part in parts if part))
            else:
                value = metadata_value_to_text(data)
                if value:
                    lines.append(value)
            return lines
        for key in sorted(data):
            value = metadata_value_to_text(data[key])
            if value:
                lines.append(f"{key}: {value}")
        return lines
    value = metadata_value_to_text(data)
    if value:
        lines.append(value)
    return lines


def split_lookup_values(value: Any) -> tuple[str, ...]:
    text = metadata_value_to_text(value)
    if not text:
        return ()
    parts = re.split(r"[\s,;、，・/／]+", text)
    return tuple(dict.fromkeys(clean_headword(part) for part in parts if clean_headword(part)))


def simple_lines_to_body(lines: list[str], stats: MarkupStats) -> BodyMarkup:
    return glossary_to_body("\n".join(line for line in lines if line), stats)


def glossary_to_body(
    glossary: Any,
    stats: MarkupStats,
    *,
    prefix_lines: list[str] | None = None,
    media_resolver: Any | None = None,
) -> BodyMarkup:
    parts: list[str | BodyControl | BodyMedia] = []
    if prefix_lines:
        parts.append("\n".join(line for line in prefix_lines if line))
        parts.append("\n")
    items = glossary if isinstance(glossary, list) else [glossary]
    for index, item in enumerate(items):
        if index:
            parts.append("\n")
        body = structured_content_to_body_markup(item, stats, media_resolver=media_resolver)
        parts.extend(body.parts)
    return BodyMarkup(tuple(parts))


def iter_yomitan_zip(
    path: Path,
    accumulator: ImportAccumulator,
    *,
    limit: int | None = None,
    progress_every: int = 10000,
    skip_forms: bool = True,
) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        index = json.loads(archive.read("index.json")) if "index.json" in archive.namelist() else {}
        archive_media_stats = accumulator.markup_stats
        media_resolver = YomitanMediaResolver(archive, archive_media_stats)
        for bank_kind, bank_name in sorted_yomitan_entry_banks(archive.namelist()):
            rows = json.loads(archive.read(bank_name))
            for row in rows:
                accumulator.rows_read += 1
                accumulator.bank_rows[bank_kind] += 1
                if not isinstance(row, list):
                    accumulator.rows_skipped += 1
                    continue
                if bank_kind == "term":
                    if len(row) < 6:
                        accumulator.rows_skipped += 1
                        continue
                    expression = str(row[0] or "")
                    reading = str(row[1] or "")
                    definition_tags = str(row[2] or "")
                    rules = str(row[3] or "")
                    glossary = row[5]
                    if skip_forms and definition_tags == "forms":
                        accumulator.rows_skipped += 1
                        continue
                    prefix: list[str] = []
                    if reading and reading != expression:
                        prefix.append(f"［{reading}］")
                    tags = " ".join(part for part in (definition_tags, rules, str(row[7] if len(row) > 7 else "")) if part)
                    if tags:
                        prefix.append(f"［{tags}］")
                    stats = MarkupStats()
                    body = glossary_to_body(glossary, stats, prefix_lines=prefix, media_resolver=media_resolver)
                    accumulator.markup_stats.merge(stats)
                    keys = [expression]
                    if reading and reading != expression:
                        keys.append(reading)
                    accumulator.add(expression, body, keys)
                elif bank_kind == "term_meta":
                    if len(row) < 3:
                        accumulator.rows_skipped += 1
                        continue
                    expression = str(row[0] or "")
                    mode = str(row[1] or "")
                    data = row[2]
                    lines = metadata_lines(mode, data)
                    stats = MarkupStats()
                    body = simple_lines_to_body(lines, stats)
                    accumulator.markup_stats.merge(stats)
                    keys = [expression]
                    if isinstance(data, dict) and data.get("reading"):
                        keys.append(str(data["reading"]))
                    accumulator.add(expression, body, keys)
                elif bank_kind == "kanji":
                    if len(row) < 5:
                        accumulator.rows_skipped += 1
                        continue
                    character = str(row[0] or "")
                    onyomi = row[1] if len(row) > 1 else ""
                    kunyomi = row[2] if len(row) > 2 else ""
                    tags = row[3] if len(row) > 3 else ""
                    meanings = row[4] if len(row) > 4 else ""
                    stats_value = row[5] if len(row) > 5 else None
                    lines = ["［kanji］"]
                    for label, value in (
                        ("On", onyomi),
                        ("Kun", kunyomi),
                        ("Tags", tags),
                        ("Meanings", meanings),
                        ("Stats", stats_value),
                    ):
                        text = metadata_value_to_text(value)
                        if text:
                            lines.append(f"{label}: {text}")
                    stats = MarkupStats()
                    body = simple_lines_to_body(lines, stats)
                    accumulator.markup_stats.merge(stats)
                    keys = [character, *split_lookup_values(onyomi), *split_lookup_values(kunyomi)]
                    accumulator.add(character, body, keys)
                elif bank_kind == "kanji_meta":
                    if len(row) < 3:
                        accumulator.rows_skipped += 1
                        continue
                    character = str(row[0] or "")
                    mode = str(row[1] or "")
                    data = row[2]
                    lines = metadata_lines(mode, data)
                    stats = MarkupStats()
                    body = simple_lines_to_body(lines, stats)
                    accumulator.markup_stats.merge(stats)
                    accumulator.add(character, body, [character])
                if progress_every and accumulator.rows_read % progress_every == 0:
                    print(f"import yomitan rows={accumulator.rows_read} entries={len(accumulator._entries)} bank={bank_name}", file=sys.stderr)
                if limit and accumulator.rows_read >= limit:
                    return index
    return index


def detect_input_format(path: Path, requested: str = "auto") -> str:
    if requested != "auto":
        return requested
    if path.suffix.lower() == ".zip":
        return "yomitan"
    if path.suffix.lower() == ".csv":
        return "koujien-csv"
    raise ValueError(f"cannot detect writer input format for {path}")


def import_entries(path: Path, *, input_format: str, limit: int | None, merge_duplicates: bool, skip_forms: bool, progress_every: int) -> tuple[list[WriterEntry], dict[str, Any]]:
    accumulator = ImportAccumulator(merge_duplicates=merge_duplicates)
    metadata: dict[str, Any] = {}
    if input_format == "koujien-csv":
        iter_koujien_csv(path, accumulator, limit=limit, progress_every=progress_every)
        metadata["source_title"] = path.stem
    elif input_format == "yomitan":
        metadata = iter_yomitan_zip(path, accumulator, limit=limit, progress_every=progress_every, skip_forms=skip_forms)
    else:
        raise ValueError(f"unsupported writer input format: {input_format}")
    entries = accumulator.entries()
    report = {
        "input": str(path),
        "input_format": input_format,
        "source_metadata": metadata,
        "rows_read": accumulator.rows_read,
        "rows_skipped": accumulator.rows_skipped,
        "bank_rows": dict(sorted(accumulator.bank_rows.items())),
        "duplicate_rows_merged": accumulator.duplicate_rows_merged,
        "long_key_drops": accumulator.long_key_drops,
        "search_keys_emitted": accumulator.search_keys_emitted,
        "search_aliases_emitted": accumulator.search_aliases_emitted,
        "headword_html_tags": dict(sorted(accumulator.headword_html_tags.items())),
        "headword_images_dropped": accumulator.headword_images_dropped,
        "entries": len(entries),
        "markup": accumulator.markup_stats.as_dict(),
    }
    return entries, report


def default_writer_dict_id(path: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "", path.stem.upper())
    digest = hashlib.sha1(path.stem.encode("utf-8", "surrogatepass")).hexdigest()[:5].upper()
    if not base:
        return f"YOMI_{digest}"
    if len(base) <= 10:
        return f"{base}_{digest}"[:16]
    return f"{base[:10]}_{digest}"[:16]


def _path_list_arg(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(item) for item in value]


def build_writer_import_package(args: Any) -> dict[str, Any]:
    input_format = detect_input_format(args.input, args.input_format)
    entries, report = import_entries(
        args.input,
        input_format=input_format,
        limit=args.limit,
        merge_duplicates=not args.no_merge_duplicates,
        skip_forms=not args.include_yomitan_forms,
        progress_every=args.progress_every,
    )
    if not entries:
        raise ValueError("no writer entries were imported")

    dict_id = args.dict_id or default_writer_dict_id(args.input)
    title = args.title or report["source_metadata"].get("title") or args.input.stem
    glyph_renderer = None
    bitmap_fonts = _path_list_arg(getattr(args, "gaiji_bitmap_font", None))
    vector_fonts = _path_list_arg(getattr(args, "gaiji_vector_font", None))
    legacy_fonts = _path_list_arg(getattr(args, "gaiji_font", None))
    vector_fonts.extend(legacy_fonts)
    if bitmap_fonts or vector_fonts:
        if bitmap_fonts or len(vector_fonts) > 1:
            glyph_renderer = FontFallbackGlyphRenderer.from_sources(
                bitmap_paths=bitmap_fonts,
                vector_paths=vector_fonts,
                vector_face_index=None if len(vector_fonts) > 1 else args.font_face_index,
                threshold=args.font_threshold,
            )
        else:
            glyph_renderer = VectorGlyphRenderer(vector_fonts[0], face_index=args.font_face_index, threshold=args.font_threshold)
    if args.gaiji_layout == "split":
        gaiji_half_start = HALF_GAIJI_START
        gaiji_full_start = FULL_GAIJI_START
        force_full_gaiji = False
    elif args.gaiji_layout == "full-a121":
        gaiji_half_start = HALF_GAIJI_START
        gaiji_full_start = HALF_GAIJI_START
        force_full_gaiji = True
    else:
        raise ValueError(f"unknown gaiji layout: {args.gaiji_layout}")

    package_dir = args.out_dir / dict_id
    if package_dir.exists() and not args.force:
        raise FileExistsError(f"output package already exists: {package_dir}; pass --force to overwrite")
    if package_dir.exists():
        import shutil

        shutil.rmtree(package_dir)

    print(f"building package dict_id={dict_id} entries={len(entries)} compression={args.compression}", file=sys.stderr)
    progress = (lambda message: print(f"build {dict_id}: {message}", file=sys.stderr)) if args.progress_every else None
    package = build_plain_honmon_package(
        dict_id=dict_id,
        title=title,
        entries=entries,
        include_tagged_indexes=not args.simple_only,
        glyph_renderer=glyph_renderer,
        compression=args.compression,
        progress=progress,
        gaiji_half_start=gaiji_half_start,
        gaiji_full_start=gaiji_full_start,
        force_full_gaiji=force_full_gaiji,
        compression_jobs=getattr(args, "jobs", 1),
    )
    write_plain_package(package, package_dir)

    file_sizes = {name: len(data) for name, data in sorted(package.files.items())}
    report.update(
        {
            "dict_id": dict_id,
            "title": title,
            "output": str(package_dir),
            "compression": args.compression,
            "compression_jobs": getattr(args, "jobs", 1),
            "include_tagged_indexes": not args.simple_only,
            "gaiji": {
                "half": len(package.gaiji_allocator.half_assignments),
                "full": len(package.gaiji_allocator.full_assignments),
                "total": len(package.gaiji_allocator.assignments),
                "external_media": len(package.gaiji_allocator.external_assignments),
                "index_key_drops": package.gaiji_allocator.index_key_drops,
                "font": str(vector_fonts[0]) if len(vector_fonts) == 1 and not bitmap_fonts else None,
                "bitmap_fonts": [str(path) for path in bitmap_fonts],
                "vector_fonts": [str(path) for path in vector_fonts],
                "font_fallback": glyph_renderer.as_dict() if hasattr(glyph_renderer, "as_dict") else None,
                "layout": args.gaiji_layout,
                "half_start": f"{gaiji_half_start:04x}",
                "full_start": f"{gaiji_full_start:04x}",
            },
            "media": {
                "colscr_records": len(package.media_allocator.assignments),
                "colscr_bytes": sum(row.payload_length or len(row.payload) for row in package.media_allocator.assignments.values()),
                "mime_types": dict(
                    sorted(Counter(row.mime_type for row in package.media_allocator.assignments.values()).items())
                ),
                "converted_from": dict(
                    sorted(Counter(row.converted_from for row in package.media_allocator.assignments.values() if row.converted_from).items())
                ),
            },
            "files": file_sizes,
        }
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{dict_id}_writer_import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
