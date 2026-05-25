"""JMdict-to-SSED writer integration.

This module keeps JMdict-specific structure outside the generic writer while
using normal SSED components for the emitted package: HONMON, title/index
streams, KWTITLE/KWINDEX, MENU.DIC, Panel metadata/BIN files, dictionary-local
Unicode gaiji maps, GA16 bitmap resources, CCALTSTR alternate strings, and
template gaiji redundancy.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import shutil
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .indexes import IndexPointer
from .gaiji import is_bitmap_gaiji_resource_name
from .panels import parse_panel_bin
from .writer import (
    BodyControl,
    BodyMarkup,
    CompressionMode,
    EmbeddedBitmapFontRenderer,
    FULL_GAIJI_START,
    HALF_GAIJI_START,
    FontFallbackGlyphRenderer,
    GaijiAssignment,
    GaijiAllocator,
    IndexTarget,
    PlainPackage,
    SsedInfoComponent,
    WriterEntry,
    WriterMenuItem,
    build_plain_honmon_package,
    blank_glyph,
    component_offset_to_pointer,
    default_catalog_data,
    encode_ccaltstr_table,
    encode_keyword_index_pages,
    encode_menu_stream,
    encode_panel_bin,
    encode_search_key,
    encode_sseddata,
    encode_sseddata_literal,
    encode_ssedinfo,
    encode_title_stream,
    logical_block_count,
    write_plain_package,
)


BOLD_START = BodyControl(b"\x1f\xe0\x00\x04")
BOLD_END = BodyControl(b"\x1f\xe1")
ITALIC_START = BodyControl(b"\x1f\x10")
ITALIC_END = BodyControl(b"\x1f\x11")

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

VECTOR_FONT_PREFERENCES = (
    "japanese/vector/kaikoku PM-M9N.otf",
    "japanese/vector/NotoSerifJP-Regular.ttf",
    "chinese/vector/PMingLiU.ttf",
    "japanese/vector/NotoSansJP-Regular.ttf",
    "chinese/vector/NotoSansSC-Regular.ttf",
    "english/vector/NotoSans-Regular.ttf",
    "emoji/NotoColorEmoji-Regular.ttf",
    "chinese/vector/HanaMinA.ttf",
    "chinese/vector/HanaMinB.ttf",
)
BITMAP_FONT_PREFERENCES = (
    "japanese/msgothic.ttc",
    "japanese/msmincho.ttc",
    "chinese/simsun.ttc",
    "chinese/simsunb.ttf",
    "chinese/SimsunExtG.ttf",
)

SEARCH_IGNORABLE = {
    " ",
    "\u3000",
    "\t",
    "\n",
    "\r",
    "-",
    "\u2010",
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
    "\u2015",
    "\u2212",
    "\uff0d",
    "・",
    "\u30fb",
    ".",
    "\u3002",
    ",",
    "\u3001",
    "/",
    "\\",
    ":",
    ";",
    "(",
    ")",
    "\uff08",
    "\uff09",
    "[",
    "]",
    "［",
    "］",
    "「",
    "」",
    "『",
    "』",
    "【",
    "】",
    "〈",
    "〉",
    "《",
    "》",
}


@dataclass(frozen=True)
class JmdictSense:
    glosses: tuple[str, ...]
    pos: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    misc: tuple[str, ...] = ()
    dialects: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    xrefs: tuple[str, ...] = ()
    antonyms: tuple[str, ...] = ()
    examples: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class JmdictRecord:
    ent_seq: str
    headword: str
    kanji: tuple[str, ...]
    readings: tuple[str, ...]
    priorities: tuple[str, ...]
    senses: tuple[JmdictSense, ...]
    search_keys: tuple[str, ...]
    kind_tags: tuple[str, ...]
    body_text: str
    synthetic: bool = False

    @property
    def first_reading(self) -> str:
        return self.readings[0] if self.readings else ""

    @property
    def has_examples(self) -> bool:
        return any(sense.examples for sense in self.senses)


@dataclass
class JmdictImportResult:
    entries: list[WriterEntry]
    records: list[JmdictRecord]
    keyword_keys: list[tuple[str, ...]]
    menu_labels: list[tuple[str, str]]
    stats: dict[str, Any]
    xml_entry_count: int = 0


@dataclass(frozen=True)
class JmdictPanel:
    index: str
    title: str
    panel_type: str
    filename: str
    rows: tuple[int, ...]


@dataclass
class JmdictExtraReport:
    components: dict[str, int]
    panel_bins: dict[str, int]
    panels_xml_bytes: int
    template_gaiji: int
    ccaltstr: dict[str, int]
    kwindex_targets: int
    menu_items: int


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "tr", "br"}:
            self._newline()
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "tr"}:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data.replace("\ufeff", ""))
        if text.strip():
            self.parts.append(text)

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _path_list(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(item) for item in value]


def _preferred_fonts(root: Path, preferences: Iterable[str]) -> list[Path]:
    return [root / rel for rel in preferences if (root / rel).is_file()]


@dataclass
class _BitmapOnlyGa16Renderer:
    renderers: tuple[Any, ...]
    fallback_misses: Counter[str]

    def __call__(self, text: str, width: int, height: int, space: str) -> bytes:
        for renderer in self.renderers:
            try:
                glyph = renderer(text, width, height, space)
            except Exception:
                continue
            if glyph and any(glyph):
                return glyph
        self.fallback_misses[text] += 1
        return blank_glyph(width, height)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "bitmap-only",
            "sources": [
                list(getattr(renderer, "sources", ()))
                for renderer in self.renderers
            ],
            "fallback_misses": sum(self.fallback_misses.values()),
            "fallback_miss_samples": list(self.fallback_misses.keys())[:20],
        }


def jmdict_vector_fonts(args: Any) -> list[Path]:
    explicit = _path_list(getattr(args, "gaiji_vector_font", None)) + _path_list(getattr(args, "gaiji_font", None))
    if explicit:
        return explicit
    configured = getattr(args, "font_root", None)
    if configured is None:
        return []
    root = Path(configured)
    return _preferred_fonts(root, VECTOR_FONT_PREFERENCES)


def jmdict_bitmap_fonts(args: Any) -> list[Path]:
    explicit = _path_list(getattr(args, "gaiji_bitmap_font", None))
    if explicit:
        return explicit
    configured = getattr(args, "font_root", None)
    if configured is None:
        return []
    root = Path(configured)
    return _preferred_fonts(root, BITMAP_FONT_PREFERENCES)


def jmdict_ga16_glyph_renderer(args: Any, bitmap_fonts: list[Path]) -> Any | None:
    """Build the GA16 renderer from the bitmap-preference font list only.

    The preference list may contain LogoVista GA16 resources/directories or
    Windows-style TTC/TTF fonts with embedded bitmap strikes.  It intentionally
    does not use outline/vector rendering; missing bitmap glyphs become blank
    GA16 cells because Unicode and Template resources are the higher-quality
    display paths.
    """

    if not bitmap_fonts:
        return None
    logo_bitmap_paths: list[Path] = []
    embedded_bitmap_paths: list[Path] = []
    for path in bitmap_fonts:
        if path.is_dir() or path.suffix.lower() == ".uni" or is_bitmap_gaiji_resource_name(path):
            logo_bitmap_paths.append(path)
        else:
            embedded_bitmap_paths.append(path)
    renderers: list[Any] = []
    if logo_bitmap_paths:
        try:
            renderers.append(FontFallbackGlyphRenderer.from_sources(bitmap_paths=logo_bitmap_paths, vector_paths=()))
        except Exception:
            pass
    if embedded_bitmap_paths:
        try:
            renderers.append(EmbeddedBitmapFontRenderer.from_paths(embedded_bitmap_paths))
        except Exception:
            pass
    if not renderers:
        return None
    return _BitmapOnlyGa16Renderer(tuple(renderers), Counter())


@lru_cache(maxsize=64)
def _font_face_coverages(path_text: str) -> tuple[tuple[int, frozenset[int]], ...]:
    try:
        from fontTools.ttLib import TTCollection, TTFont
    except ImportError:
        return ()
    path = Path(path_text)
    faces: list[tuple[int, frozenset[int]]] = []
    try:
        if path.suffix.lower() in {".ttc", ".otc"}:
            collection = TTCollection(str(path), lazy=True)
            try:
                for index, font in enumerate(collection.fonts):
                    faces.append((index, _font_coverage(font)))
            finally:
                for font in collection.fonts:
                    close = getattr(font, "close", None)
                    if close is not None:
                        close()
            return tuple(faces)
        font = TTFont(str(path), lazy=True)
        try:
            return ((0, _font_coverage(font)),)
        finally:
            close = getattr(font, "close", None)
            if close is not None:
                close()
    except Exception:
        return ()


def _font_coverage(font: Any) -> frozenset[int]:
    cmap = font.get("cmap")
    if cmap is None:
        return frozenset()
    codepoints: set[int] = set()
    for table in cmap.tables:
        if not table.isUnicode():
            continue
        for codepoint, glyph_name in table.cmap.items():
            if str(glyph_name).lower() in {".notdef", "glyph0", "glyph00000"}:
                continue
            codepoints.add(int(codepoint))
    return frozenset(codepoints)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u0000", "").replace("\ufeff", "")).strip()


def _katakana_to_hiragana(value: str) -> str:
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "ヴ":
            out.append("う")
        elif 0x30A1 <= code <= 0x30FA:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _normalize_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", _clean(value))
    text = _katakana_to_hiragana(text)
    return "".join(ch for ch in text if ch not in SEARCH_IGNORABLE and not unicodedata.category(ch).startswith("Z"))


def _lookup_aliases(value: str) -> tuple[str, ...]:
    text = _clean(value)
    normalized = _normalize_key(text)
    aliases = [text] if text else []
    if normalized and normalized != text:
        aliases.append(normalized)
    return tuple(dict.fromkeys(aliases))


def _children_text(parent: ET.Element, tag: str) -> tuple[str, ...]:
    return tuple(_clean(child.text or "") for child in parent.findall(tag) if _clean(child.text or ""))


def _entry_forms(elem: ET.Element) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    kanji: list[str] = []
    readings: list[str] = []
    priorities: list[str] = []
    for k_ele in elem.findall("k_ele"):
        keb = _clean(k_ele.findtext("keb") or "")
        if keb:
            kanji.append(keb)
        priorities.extend(_children_text(k_ele, "ke_pri"))
    for r_ele in elem.findall("r_ele"):
        reb = _clean(r_ele.findtext("reb") or "")
        if reb:
            readings.append(reb)
        priorities.extend(_children_text(r_ele, "re_pri"))
    return (
        tuple(dict.fromkeys(kanji)),
        tuple(dict.fromkeys(readings)),
        tuple(dict.fromkeys(priorities)),
    )


def _parse_sense(elem: ET.Element) -> JmdictSense:
    examples: list[tuple[str, str]] = []
    for example in elem.findall("example"):
        jpn = ""
        eng = ""
        for sent in example.findall("ex_sent"):
            lang = sent.attrib.get(XML_LANG, "")
            if lang == "jpn":
                jpn = _clean(sent.text or "")
            elif lang == "eng":
                eng = _clean(sent.text or "")
        if jpn or eng:
            examples.append((jpn, eng))
    sources = []
    for source in elem.findall("lsource"):
        text = _clean(source.text or "")
        lang = source.attrib.get(XML_LANG, "")
        if text and lang:
            sources.append(f"{lang}: {text}")
        elif text:
            sources.append(text)
        elif lang:
            sources.append(lang)
    return JmdictSense(
        glosses=_children_text(elem, "gloss"),
        pos=_children_text(elem, "pos"),
        fields=_children_text(elem, "field"),
        misc=_children_text(elem, "misc"),
        dialects=_children_text(elem, "dial"),
        sources=tuple(dict.fromkeys(sources)),
        xrefs=_children_text(elem, "xref"),
        antonyms=_children_text(elem, "ant"),
        examples=tuple(examples),
        notes=_children_text(elem, "s_inf"),
    )


def _body_markup(record: JmdictRecord) -> BodyMarkup:
    parts: list[str | BodyControl] = []

    def line(text: str = "") -> None:
        parts.append(text + "\n")

    if record.kanji:
        line("Written forms: " + "、".join(record.kanji))
    if record.readings:
        line("Readings: " + "、".join(record.readings))
    if record.priorities:
        line("Priority: " + ", ".join(record.priorities))
    if record.kind_tags:
        line("Tags: " + ", ".join(record.kind_tags))
    if record.kanji or record.readings or record.priorities or record.kind_tags:
        line()

    for index, sense in enumerate(record.senses, start=1):
        parts.extend((BOLD_START, f"{index}. ", BOLD_END))
        if sense.glosses:
            parts.append("; ".join(sense.glosses) + "\n")
        else:
            parts.append("\n")
        details: list[str] = []
        if sense.pos:
            details.append("Part of speech: " + ", ".join(sense.pos))
        if sense.fields:
            details.append("Field: " + ", ".join(sense.fields))
        if sense.misc:
            details.append("Misc: " + ", ".join(sense.misc))
        if sense.dialects:
            details.append("Dialect: " + ", ".join(sense.dialects))
        if sense.sources:
            details.append("Source: " + ", ".join(sense.sources))
        if sense.xrefs:
            details.append("See also: " + ", ".join(sense.xrefs))
        if sense.antonyms:
            details.append("Antonym: " + ", ".join(sense.antonyms))
        if sense.notes:
            details.append("Notes: " + " / ".join(sense.notes))
        for item in details:
            line(item)
        if sense.examples:
            parts.extend((ITALIC_START, "Examples", ITALIC_END, "\n"))
            for jpn, eng in sense.examples:
                if jpn:
                    line("  " + jpn)
                if eng:
                    line("  " + eng)
        if index != len(record.senses):
            line()
    return BodyMarkup(tuple(parts))


def _record_body_text(record: JmdictRecord) -> str:
    lines: list[str] = []
    if record.kanji:
        lines.append("Written forms: " + "、".join(record.kanji))
    if record.readings:
        lines.append("Readings: " + "、".join(record.readings))
    for index, sense in enumerate(record.senses, 1):
        lines.append(f"{index}. " + "; ".join(sense.glosses))
        for jpn, eng in sense.examples[:2]:
            if jpn:
                lines.append("  " + jpn)
            if eng:
                lines.append("  " + eng)
    return "\n".join(lines)


def _english_keywords(glosses: Iterable[str], *, limit: int) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    stop = {
        "a",
        "an",
        "and",
        "be",
        "by",
        "for",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "one",
        "something",
        "someone",
    }
    seen: list[str] = []
    for gloss in glosses:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+'-]{1,}", gloss):
            key = token.strip("-'").lower()
            if len(key) < 3 or key in stop:
                continue
            if key not in seen:
                seen.append(key)
            if len(seen) >= limit:
                return tuple(seen)
    return tuple(seen)


def _ascii_fallback(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if ord(ch) < 128 and not unicodedata.combining(ch))[:60]


def _keywords_for_record(record: JmdictRecord, *, english_limit: int) -> tuple[str, ...]:
    keys: list[str] = []
    if record.has_examples:
        keys.append("entries with examples")
    if record.priorities:
        keys.append("common words")
        keys.extend(record.priorities[:4])
    for sense in record.senses:
        keys.extend(sense.pos)
        keys.extend(sense.fields)
        keys.extend(sense.misc)
        keys.extend(sense.dialects)
        for source in sense.sources:
            keys.append("source languages")
            keys.append(source.split(":", 1)[0])
        keys.extend(_english_keywords(sense.glosses, limit=english_limit))
    return tuple(dict.fromkeys(_clean(key) for key in keys if _clean(key)))


def _parse_entry(elem: ET.Element, *, english_keyword_limit: int) -> tuple[WriterEntry, JmdictRecord, tuple[str, ...]]:
    ent_seq = _clean(elem.findtext("ent_seq") or "")
    kanji, readings, priorities = _entry_forms(elem)
    senses = tuple(_parse_sense(sense) for sense in elem.findall("sense"))
    headword = kanji[0] if kanji else readings[0] if readings else ent_seq
    forms = tuple(dict.fromkeys((*kanji, *readings)))
    aliases: list[str] = []
    for form in forms:
        aliases.extend(_lookup_aliases(form))
    if ent_seq:
        aliases.append(ent_seq)
    tags: list[str] = []
    if priorities:
        tags.append("common/priority")
    if any(sense.examples for sense in senses):
        tags.append("examples")
    if any(sense.fields for sense in senses):
        tags.append("field/domain")
    if any(sense.dialects for sense in senses):
        tags.append("dialect")
    if any(sense.sources for sense in senses):
        tags.append("source-language")
    record = JmdictRecord(
        ent_seq=ent_seq,
        headword=headword,
        kanji=kanji,
        readings=readings,
        priorities=priorities,
        senses=senses,
        search_keys=tuple(dict.fromkeys(aliases)),
        kind_tags=tuple(tags),
        body_text="",
    )
    record = JmdictRecord(
        ent_seq=record.ent_seq,
        headword=record.headword,
        kanji=record.kanji,
        readings=record.readings,
        priorities=record.priorities,
        senses=record.senses,
        search_keys=record.search_keys,
        kind_tags=record.kind_tags,
        body_text=_record_body_text(record),
    )
    entry = WriterEntry(headword=headword, body=_body_markup(record), search_keys=record.search_keys)
    return entry, record, _keywords_for_record(record, english_limit=english_keyword_limit)


def _synthetic_record(headword: str, body: str, *, tags: Iterable[str] = ()) -> tuple[WriterEntry, JmdictRecord, tuple[str, ...]]:
    sense = JmdictSense(glosses=(body.splitlines()[0] if body.splitlines() else body[:60],))
    record = JmdictRecord(
        ent_seq=headword,
        headword=headword,
        kanji=(headword,),
        readings=(),
        priorities=(),
        senses=(sense,),
        search_keys=_lookup_aliases(headword),
        kind_tags=tuple(tags),
        body_text=body,
        synthetic=True,
    )
    return WriterEntry(headword=headword, body=BodyMarkup.text(body), search_keys=record.search_keys), record, tuple(tags)


def _html_file_to_text(path: Path) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    parser.close()
    return parser.text()


def _category_summary(title: str, values: Iterable[str], *, max_items: int = 80) -> str:
    items = [value for value, _count in Counter(values).most_common(max_items)]
    return title + "\n\n" + "\n".join(f"- {item}" for item in items)


def import_jmdict(
    path: Path,
    *,
    limit: int | None = None,
    progress_every: int = 10000,
    license_html: Path | None = None,
    project_html: Path | None = None,
    english_keyword_limit: int = 4,
) -> JmdictImportResult:
    entries: list[WriterEntry] = []
    records: list[JmdictRecord] = []
    keyword_keys: list[tuple[str, ...]] = []
    pos_values: list[str] = []
    field_values: list[str] = []
    dialect_values: list[str] = []
    misc_values: list[str] = []
    priorities: list[str] = []
    source_values: list[str] = []
    rows_read = 0

    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rb") as handle:
        for event, elem in ET.iterparse(handle, events=("end",)):
            if elem.tag != "entry":
                continue
            rows_read += 1
            entry, record, keywords = _parse_entry(elem, english_keyword_limit=english_keyword_limit)
            entries.append(entry)
            records.append(record)
            keyword_keys.append(keywords)
            priorities.extend(record.priorities)
            for sense in record.senses:
                pos_values.extend(sense.pos)
                field_values.extend(sense.fields)
                dialect_values.extend(sense.dialects)
                misc_values.extend(sense.misc)
                source_values.extend(source.split(":", 1)[0] for source in sense.sources)
            if progress_every and rows_read % progress_every == 0:
                print(f"import jmdict entries={rows_read}", file=sys.stderr, flush=True)
            elem.clear()
            if limit and rows_read >= limit:
                break

    menu_labels: list[tuple[str, str]] = [
        ("About JMdict", "ＪＭｄｉｃｔ"),
        ("Kana browse", "JMdict Kana Browse"),
        ("Parts of speech", "JMdict Parts of Speech"),
        ("Fields and domains", "JMdict Fields and Domains"),
        ("Dialects", "JMdict Dialects"),
        ("Common and priority words", "JMdict Priority Words"),
        ("Entries with examples", "JMdict Entries with Examples"),
        ("Source languages", "JMdict Source Languages"),
    ]

    synthetic_specs = [
        ("JMdict Kana Browse", "Kana browse panels group entries by reading initial.", ("panel", "kana")),
        ("JMdict Parts of Speech", _category_summary("Parts of speech", pos_values), ("panel", "pos")),
        ("JMdict Fields and Domains", _category_summary("Fields and domains", field_values), ("panel", "field")),
        ("JMdict Dialects", _category_summary("Dialects", dialect_values), ("panel", "dialect")),
        ("JMdict Priority Words", _category_summary("Common/priority tags", priorities), ("panel", "priority")),
        ("JMdict Entries with Examples", "Entries with examples are indexed and exposed as a Panel group.", ("panel", "examples")),
        ("JMdict Source Languages", _category_summary("Source languages", source_values), ("panel", "source")),
    ]
    if license_html and license_html.is_file():
        synthetic_specs.append(("JMdict licence", _html_file_to_text(license_html), ("license",)))
        menu_labels.append(("JMdict licence", "JMdict licence"))
    if project_html and project_html.is_file():
        synthetic_specs.append(("JMdict-EDICT Dictionary Project", _html_file_to_text(project_html), ("project",)))
        menu_labels.append(("JMdict-EDICT Project", "JMdict-EDICT Dictionary Project"))

    for headword, body, tags in synthetic_specs:
        entry, record, keywords = _synthetic_record(headword, body, tags=tags)
        entries.append(entry)
        records.append(record)
        keyword_keys.append(keywords)

    stats = {
        "input": str(path),
        "input_format": "jmdict",
        "rows_read": rows_read,
        "entries": len(entries),
        "synthetic_entries": len(synthetic_specs),
        "pos": dict(sorted(Counter(pos_values).items())),
        "fields": dict(sorted(Counter(field_values).items())),
        "dialects": dict(sorted(Counter(dialect_values).items())),
        "misc": dict(sorted(Counter(misc_values).items())),
        "priorities": dict(sorted(Counter(priorities).items())),
        "sources": dict(sorted(Counter(source_values).items())),
        "entries_with_examples": sum(1 for record in records if record.has_examples),
    }
    return JmdictImportResult(
        entries=entries,
        records=records,
        keyword_keys=keyword_keys,
        menu_labels=menu_labels,
        stats=stats,
        xml_entry_count=rows_read,
    )


def _writer_catalog_components(data: bytes) -> list[SsedInfoComponent]:
    count = data[0x4D]
    out: list[SsedInfoComponent] = []
    for index in range(count):
        pos = 0x80 + index * 0x30
        rec = data[pos : pos + 0x30]
        name_len = rec[16]
        filename = rec[17 : 17 + name_len].decode("ascii")
        out.append(
            SsedInfoComponent(
                filename=filename,
                type=rec[3],
                start_block=int.from_bytes(rec[4:8], "big"),
                end_block=int.from_bytes(rec[8:12], "big"),
                data=bytes(rec[12:16]),
                multi=rec[2],
            )
        )
    return out


def _encode_component(
    expanded: bytes,
    *,
    start_block: int,
    kind: int,
    compression: CompressionMode,
    jobs: int | None,
) -> bytes:
    if compression == "literal":
        return encode_sseddata_literal(expanded, start_block=start_block, kind=kind)
    if compression == "compressed":
        return encode_sseddata(expanded, start_block=start_block, kind=kind, jobs=jobs)
    raise ValueError(f"unknown compression mode: {compression}")


def _make_keyword_targets(
    result: JmdictImportResult,
    encoded_entries: tuple[Any, ...],
    *,
    kwtitle_start: int,
    kwtitle_offsets: list[int],
) -> list[IndexTarget]:
    targets: list[IndexTarget] = []
    for index, keywords in enumerate(result.keyword_keys):
        if index >= len(encoded_entries):
            break
        encoded = encoded_entries[index]
        title = component_offset_to_pointer(kwtitle_start, kwtitle_offsets[index])
        for key in keywords:
            targets.append(IndexTarget(key=key, body=encoded.body_pointer, title=title))
    return targets


def _pointer_by_headword(package: PlainPackage) -> dict[str, IndexPointer]:
    mapping: dict[str, IndexPointer] = {}
    for encoded in package.encoded_entries:
        mapping.setdefault(encoded.entry.headword, encoded.body_pointer)
    return mapping


def _menu_items(result: JmdictImportResult, package: PlainPackage) -> list[WriterMenuItem]:
    pointers = _pointer_by_headword(package)
    items: list[WriterMenuItem] = []
    for label, headword in result.menu_labels:
        pointer = pointers.get(headword)
        if pointer is not None:
            items.append(WriterMenuItem(label=label, body=pointer))
    return items


def _kana_bucket(text: str) -> str:
    base = _normalize_key(text)
    if not base:
        return "#"
    first = base[0]
    table = [
        ("あ", "あいうえお"),
        ("か", "かきくけこがぎぐげご"),
        ("さ", "さしすせそざじずぜぞ"),
        ("た", "たちつてとだぢづでど"),
        ("な", "なにぬねの"),
        ("は", "はひふへほばびぶべぼぱぴぷぺぽ"),
        ("ま", "まみむめも"),
        ("や", "やゆよ"),
        ("ら", "らりるれろ"),
        ("わ", "わをん"),
    ]
    for bucket, chars in table:
        if first in chars:
            return bucket
    return "#"


def _panel_specs(result: JmdictImportResult) -> tuple[JmdictPanel, ...]:
    buckets: dict[str, list[int]] = defaultdict(list)
    pos: dict[str, list[int]] = defaultdict(list)
    fields: dict[str, list[int]] = defaultdict(list)
    dialects: dict[str, list[int]] = defaultdict(list)
    misc: dict[str, list[int]] = defaultdict(list)
    priority: list[int] = []
    examples: list[int] = []
    sources: dict[str, list[int]] = defaultdict(list)

    for index, record in enumerate(result.records[: result.xml_entry_count]):
        buckets[_kana_bucket(record.first_reading or record.headword)].append(index)
        if record.priorities:
            priority.append(index)
        if record.has_examples:
            examples.append(index)
        for sense in record.senses:
            for value in sense.pos:
                pos[value].append(index)
            for value in sense.fields:
                fields[value].append(index)
            for value in sense.dialects:
                dialects[value].append(index)
            for value in sense.misc:
                misc[value].append(index)
            for source in sense.sources:
                sources[source.split(":", 1)[0]].append(index)

    panels: list[JmdictPanel] = []
    panel_id = 0x01010000

    def add_panel(title: str, filename: str, rows: Iterable[int], panel_type: str = "contents") -> None:
        nonlocal panel_id
        unique = tuple(dict.fromkeys(rows))
        if not unique:
            return
        panels.append(JmdictPanel(f"{panel_id:08X}", title, panel_type, filename, unique))
        panel_id += 0x00010000

    for key in ("あ", "か", "さ", "た", "な", "は", "ま", "や", "ら", "わ", "#"):
        add_panel(f"Kana {key}", f"Panel/kana_{key}.bin", buckets.get(key, ()))
    for label, rows in Counter({k: len(v) for k, v in pos.items()}).most_common(40):
        add_panel(f"POS {label}", f"Panel/pos_{len(panels):03d}.bin", pos[label])
    for label, rows in Counter({k: len(v) for k, v in fields.items()}).most_common(40):
        add_panel(f"Field {label}", f"Panel/field_{len(panels):03d}.bin", fields[label])
    for label, rows in Counter({k: len(v) for k, v in dialects.items()}).most_common(20):
        add_panel(f"Dialect {label}", f"Panel/dialect_{len(panels):03d}.bin", dialects[label])
    for label, rows in Counter({k: len(v) for k, v in misc.items()}).most_common(30):
        add_panel(f"Misc {label}", f"Panel/misc_{len(panels):03d}.bin", misc[label])
    for label, rows in Counter({k: len(v) for k, v in sources.items()}).most_common(20):
        add_panel(f"Source {label}", f"Panel/source_{len(panels):03d}.bin", sources[label])
    add_panel("Common and priority words", "Panel/priority.bin", priority)
    add_panel("Entries with examples", "Panel/examples.bin", examples)
    return tuple(panels)


def _panel_root_xml(panels: tuple[JmdictPanel, ...]) -> bytes:
    menu_cells = "\n".join(
        f'      <cell action_verb="lved.panel:{panel.index}" ref="{panel.index}">{html.escape(panel.title)}</cell>'
        for panel in panels
    )
    external = "\n".join(
        f'''  <panel index="{panel.index}" paneltype="{panel.panel_type}" datatype="external">
    <title>{html.escape(panel.title)}</title>
    <data type="bin" filename="{html.escape(panel.filename)}" />
  </panel>'''
        for panel in panels
    )
    text = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE panels SYSTEM "Panels.dtd">
<panels version="1.0">
  <information>
    <dictionaryName>JMdict</dictionaryName>
    <creationDate>generated by logovista-tools</creationDate>
  </information>
  <panel index="01000000" paneltype="menu" count_x="3">
    <title>JMdict Panels</title>
    <data>
{menu_cells}
    </data>
  </panel>
{external}
</panels>
'''
    return text.encode("utf-8")


def _panel_dtd() -> bytes:
    return b"""<!ELEMENT panels (information?, panel*)>
<!ATTLIST panels version CDATA #IMPLIED>
<!ELEMENT information (dictionaryName?, creationDate?)>
<!ELEMENT dictionaryName (#PCDATA)>
<!ELEMENT creationDate (#PCDATA)>
<!ELEMENT panel (title?, data?)>
<!ATTLIST panel index CDATA #REQUIRED paneltype CDATA #IMPLIED datatype CDATA #IMPLIED count_x CDATA #IMPLIED count_y CDATA #IMPLIED>
<!ELEMENT title (#PCDATA)>
<!ELEMENT data (cell*)>
<!ATTLIST data type CDATA #IMPLIED filename CDATA #IMPLIED>
<!ELEMENT cell (#PCDATA)>
<!ATTLIST cell ref CDATA #IMPLIED action_verb CDATA #IMPLIED>
"""


def _panel_html() -> bytes:
    return b"""<!doctype html>
<html><head><meta charset="utf-8"><title>JMdict Panel</title></head>
<body><div id="panel"></div></body></html>
"""


def _cell_html() -> bytes:
    return b"""<!doctype html>
<html><head><meta charset="utf-8"><title>JMdict Panel Cell</title></head>
<body><span id="cell"></span></body></html>
"""


def _template_gaiji_dimensions(space: str) -> tuple[int, int]:
    if space == "half":
        return 75, 150
    return 150, 150


def _template_codepoints(text: str) -> frozenset[int]:
    return frozenset(
        ord(ch)
        for ch in text
        if not unicodedata.combining(ch) and unicodedata.category(ch) != "Cf"
    )


def _font_faces_covering_text(text: str, font_paths: list[Path]) -> Iterable[tuple[Path, int]]:
    required_codepoints = _template_codepoints(text)
    for path in font_paths:
        coverages = _font_face_coverages(str(path))
        if not coverages:
            continue
        for face_index, coverage in coverages:
            if required_codepoints and coverage and not required_codepoints.issubset(coverage):
                continue
            yield path, face_index


def _render_template_gaiji_png(text: str, font_paths: list[Path], *, width: int, height: int) -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    for path, face_index in _font_faces_covering_text(text, font_paths):
        for font_size in range(max(height - 8, 1), 3, -2):
            try:
                font = ImageFont.truetype(str(path), size=font_size, index=face_index)
            except Exception:
                break
            image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
            draw = ImageDraw.Draw(image)
            bbox = draw.textbbox((0, 0), text, font=font)
            glyph_width = max(1, bbox[2] - bbox[0])
            glyph_height = max(1, bbox[3] - bbox[1])
            if glyph_width > width - 8 or glyph_height > height - 8:
                continue
            draw.text(
                ((width - glyph_width) / 2 - bbox[0], (height - glyph_height) / 2 - bbox[1]),
                text,
                font=font,
                fill=(0, 0, 0, 255),
            )
            alpha = image.getchannel("A")
            if alpha.getbbox():
                out = BytesIO()
                image.save(out, format="PNG")
                return out.getvalue()
    return b""


def _svg_path_for_font_text(text: str, path: Path, face_index: int, *, width: int, height: int) -> str | None:
    try:
        from fontTools.misc.transform import Transform
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen
        from fontTools.ttLib import TTFont
    except ImportError:
        return None

    font = None
    try:
        font = TTFont(str(path), fontNumber=face_index, lazy=False)
        glyph_set = font.getGlyphSet()
        cmap = font.getBestCmap() or {}
        glyph_names: list[str] = []
        for ch in text:
            if unicodedata.combining(ch) or unicodedata.category(ch) == "Cf":
                continue
            glyph_name = cmap.get(ord(ch))
            if glyph_name is None or str(glyph_name).lower() in {".notdef", "glyph0", "glyph00000"}:
                return None
            glyph_names.append(str(glyph_name))
        if not glyph_names:
            return None

        hmtx = font.get("hmtx")
        advances = [
            int(hmtx.metrics.get(name, (0, 0))[0]) if hmtx is not None else 0
            for name in glyph_names
        ]
        x_cursor = 0
        bounds_pen = BoundsPen(glyph_set)
        for name, advance in zip(glyph_names, advances):
            glyph_set[name].draw(TransformPen(bounds_pen, Transform(1, 0, 0, 1, x_cursor, 0)))
            x_cursor += advance or 1000
        if bounds_pen.bounds is None:
            return None

        x_min, y_min, x_max, y_max = bounds_pen.bounds
        glyph_width = max(1.0, x_max - x_min)
        glyph_height = max(1.0, y_max - y_min)
        margin_x = max(width * 0.06, 2.0)
        margin_y = max(height * 0.06, 2.0)
        scale = min((width - margin_x * 2) / glyph_width, (height - margin_y * 2) / glyph_height)
        if scale <= 0:
            return None
        extra_x = (width - glyph_width * scale) / 2
        extra_y = (height - glyph_height * scale) / 2
        translate_x = extra_x - x_min * scale
        translate_y = extra_y + y_max * scale

        svg_pen = SVGPathPen(glyph_set)
        x_cursor = 0
        for name, advance in zip(glyph_names, advances):
            transform = Transform(scale, 0, 0, -scale, translate_x + x_cursor * scale, translate_y)
            glyph_set[name].draw(TransformPen(svg_pen, transform))
            x_cursor += advance or 1000
        path_data = svg_pen.getCommands()
        return path_data or None
    except Exception:
        return None
    finally:
        if font is not None:
            close = getattr(font, "close", None)
            if close is not None:
                close()


def _template_svg(text: str, font_paths: list[Path], *, space: str) -> bytes:
    width, height = _template_gaiji_dimensions(space)
    for path, face_index in _font_faces_covering_text(text, font_paths):
        path_data = _svg_path_for_font_text(text, path, face_index, width=width, height=height)
        if path_data:
            title = html.escape(text)
            return (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
                f"<title>{title}</title>"
                f'<path d="{path_data}"/>'
                "</svg>\n"
            ).encode("utf-8")
    return b""


def _ccaltstr_values(assignments: Iterable[GaijiAssignment]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in assignments:
        fallback = _ascii_fallback(row.text)
        if fallback and fallback != row.text:
            values[row.key] = fallback
    return values


def augment_jmdict_package(
    package: PlainPackage,
    result: JmdictImportResult,
    *,
    compression: CompressionMode,
    compression_jobs: int | None,
    vector_fonts: list[Path],
    progress: Any | None = None,
) -> tuple[PlainPackage, JmdictExtraReport]:
    files = dict(package.files)
    catalog = _writer_catalog_components(files[f"{package.dict_id}.IDX"])
    cursor = max(row.end_block for row in catalog if row.start_block or row.end_block) + 1

    kwtitle_start = cursor
    kwtitle_expanded, kwtitle_offsets = encode_title_stream([row.entry.headword for row in package.encoded_entries], package.gaiji_allocator)
    kwtitle_blocks = logical_block_count(kwtitle_expanded)
    cursor += kwtitle_blocks

    kwindex_start = cursor
    keyword_targets = _make_keyword_targets(result, package.encoded_entries, kwtitle_start=kwtitle_start, kwtitle_offsets=kwtitle_offsets)
    kwindex_expanded = encode_keyword_index_pages(keyword_targets, start_block=kwindex_start, gaiji=package.gaiji_allocator)
    kwindex_blocks = logical_block_count(kwindex_expanded)
    cursor += kwindex_blocks

    menu_start = cursor
    menu_items = _menu_items(result, package)
    menu_expanded = encode_menu_stream(menu_items, package.gaiji_allocator)
    menu_blocks = logical_block_count(menu_expanded)
    cursor += menu_blocks

    extras = [
        ("KWTITLE.DIC", 0x03, kwtitle_start, kwtitle_blocks, 0x03, kwtitle_expanded),
        ("KWINDEX.DIC", 0x80, kwindex_start, kwindex_blocks, 0x80, kwindex_expanded),
        ("MENU.DIC", 0x01, menu_start, menu_blocks, 0x01, menu_expanded),
    ]
    for filename, component_type, start, blocks, kind, expanded in extras:
        catalog.append(
            SsedInfoComponent(
                filename,
                component_type,
                start,
                start + blocks - 1,
                default_catalog_data(component_type, filename),
            )
        )
        files[filename] = _encode_component(
            expanded,
            start_block=start,
            kind=kind,
            compression=compression,
            jobs=compression_jobs,
        )
        if progress:
            progress(f"encoded {filename}: expanded={len(expanded)} bytes")

    panels = _panel_specs(result)
    panel_bins: dict[str, int] = {}
    for panel in panels:
        rows = []
        for row_index in panel.rows:
            if row_index >= len(package.encoded_entries):
                continue
            encoded = package.encoded_entries[row_index]
            rows.append((encoded.entry.headword, encoded.body_pointer))
        payload = encode_panel_bin(rows, gaiji=package.gaiji_allocator)
        parse_panel_bin(payload)
        files[panel.filename] = payload
        panel_bins[panel.filename] = len(rows)
    files["Panels.dtd"] = _panel_dtd()
    files["Panels.xml"] = _panel_root_xml(panels)
    files["Panel.html"] = _panel_html()
    files["Cell.html"] = _cell_html()

    template_count = 0
    for assignment in package.gaiji_allocator.assignments.values():
        width, height = _template_gaiji_dimensions(assignment.space)
        png = _render_template_gaiji_png(assignment.text, vector_fonts, width=width, height=height)
        base = f"Templates/{assignment.key.lower()}"
        if png:
            files[base + ".png"] = png
            template_count += 1
        svg = _template_svg(assignment.text, vector_fonts, space=assignment.space)
        if svg:
            files[base + ".svg"] = svg

    ccaltstr_counts: dict[str, int] = {}
    half_values = _ccaltstr_values(package.gaiji_allocator.half_assignments)
    full_values = _ccaltstr_values(package.gaiji_allocator.full_assignments)
    if half_values:
        files["CCALTSTR.HA"] = encode_ccaltstr_table(package.gaiji_allocator.half_assignments, kind="half", values=half_values)
        ccaltstr_counts["CCALTSTR.HA"] = len(half_values)
    if full_values:
        files["CCALTSTR.FU"] = encode_ccaltstr_table(package.gaiji_allocator.full_assignments, kind="full", values=full_values)
        ccaltstr_counts["CCALTSTR.FU"] = len(full_values)

    files[f"{package.dict_id}.IDX"] = encode_ssedinfo(package.title, catalog)
    updated = PlainPackage(
        dict_id=package.dict_id,
        title=package.title,
        files=files,
        encoded_entries=package.encoded_entries,
        gaiji_allocator=package.gaiji_allocator,
        media_allocator=package.media_allocator,
    )
    report = JmdictExtraReport(
        components={name: len(files[name]) for name, *_rest in extras},
        panel_bins=panel_bins,
        panels_xml_bytes=len(files["Panels.xml"]),
        template_gaiji=template_count,
        ccaltstr=ccaltstr_counts,
        kwindex_targets=len(keyword_targets),
        menu_items=len(menu_items),
    )
    return updated, report


def _showcase_sample_indices(records: list[JmdictRecord], limit: int) -> list[int]:
    selected: list[int] = []
    for index, record in enumerate(records):
        if len(selected) >= limit:
            return selected
        if record.synthetic or record.headword == "ＪＭｄｉｃｔ":
            selected.append(index)
    predicates = [
        lambda r: r.headword == "ＪＭｄｉｃｔ",
        lambda r: r.has_examples,
        lambda r: bool(r.priorities),
        lambda r: any(s.fields for s in r.senses),
        lambda r: any(s.dialects for s in r.senses),
        lambda r: any(s.sources for s in r.senses),
        lambda r: any(s.xrefs for s in r.senses),
        lambda r: any(any(ord(ch) > 0xFFFF for ch in r.body_text + r.headword) for _ in (0,)),
    ]
    for pred in predicates:
        for index, record in enumerate(records):
            if len(selected) >= limit:
                break
            if index not in selected and pred(record):
                selected.append(index)
        if len(selected) >= limit:
            break
    for index in range(len(records)):
        if len(selected) >= limit:
            break
        if index not in selected:
            selected.append(index)
    return selected


def write_jmdict_showcase(result: JmdictImportResult, out_dir: Path, *, limit: int = 1000) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    indices = _showcase_sample_indices(result.records, limit)
    sample_set = set(indices)
    entries = []
    for index in indices:
        record = result.records[index]
        entries.append(
            {
                "id": f"e{index}",
                "headword": record.headword,
                "reading": record.first_reading,
                "tags": list(record.kind_tags),
                "body": record.body_text,
                "examples": sum(len(sense.examples) for sense in record.senses),
            }
        )

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for index in indices:
        record = result.records[index]
        groups["Kana " + _kana_bucket(record.first_reading or record.headword)].append({"id": f"e{index}", "label": record.headword})
        if record.has_examples:
            groups["Entries with examples"].append({"id": f"e{index}", "label": record.headword})
        if record.priorities:
            groups["Common and priority words"].append({"id": f"e{index}", "label": record.headword})
        for sense in record.senses:
            for field in sense.fields[:2]:
                groups["Field: " + field].append({"id": f"e{index}", "label": record.headword})
            for pos in sense.pos[:2]:
                groups["POS: " + pos].append({"id": f"e{index}", "label": record.headword})
            for dialect in sense.dialects[:2]:
                groups["Dialect: " + dialect].append({"id": f"e{index}", "label": record.headword})

    data = {
        "entries": entries,
        "groups": {key: value for key, value in sorted(groups.items()) if value},
        "menu": [
            {"label": label, "target": next((f"e{i}" for i in sample_set if result.records[i].headword == target), None)}
            for label, target in result.menu_labels
        ],
    }
    data["menu"] = [row for row in data["menu"] if row["target"]]
    json_data = json.dumps(data, ensure_ascii=False, sort_keys=True)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>JMdict SSED Showcase</title>
<style>
body {{ margin: 0; font-family: system-ui, sans-serif; display: grid; grid-template-columns: 280px 1fr; height: 100vh; }}
nav {{ overflow: auto; border-right: 1px solid #ccc; padding: 12px; background: #f7f7f7; }}
main {{ overflow: auto; padding: 20px; }}
button {{ display: block; width: 100%; text-align: left; margin: 4px 0; padding: 5px 7px; border: 1px solid #ccc; background: white; cursor: pointer; }}
button:hover {{ border-color: #5da24c; }}
.group {{ margin: 12px 0; }}
.entry-head {{ color: #3021f9; font-weight: 700; font-size: 1.35rem; }}
.reading {{ color: #555; margin-left: .5rem; }}
.tag {{ display: inline-block; border: 1px solid #ddd; padding: 1px 5px; margin: 2px; font-size: .8rem; }}
pre {{ white-space: pre-wrap; font-family: inherit; line-height: 1.55; }}
</style>
</head>
<body>
<nav>
<h1>JMdict</h1>
<section id="menu"></section>
<h2>Panels</h2>
<section id="groups"></section>
</nav>
<main>
<div id="entry"></div>
</main>
<script>
const DATA = {json_data};
const byId = new Map(DATA.entries.map(e => [e.id, e]));
function renderEntry(id) {{
  const e = byId.get(id);
  if (!e) return;
  document.getElementById('entry').innerHTML =
    `<div class="entry-head">${{escapeHtml(e.headword)}}<span class="reading">${{escapeHtml(e.reading || '')}}</span></div>` +
    `<div>${{e.tags.map(t => `<span class="tag">${{escapeHtml(t)}}</span>`).join('')}}</div>` +
    `<pre>${{escapeHtml(e.body)}}</pre>`;
}}
function escapeHtml(s) {{
  return String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function button(label, fn) {{
  const b = document.createElement('button');
  b.textContent = label;
  b.addEventListener('click', fn);
  return b;
}}
const menu = document.getElementById('menu');
DATA.menu.forEach(row => menu.appendChild(button(row.label, () => renderEntry(row.target))));
const groups = document.getElementById('groups');
Object.entries(DATA.groups).forEach(([name, rows]) => {{
  const box = document.createElement('div');
  box.className = 'group';
  const h = document.createElement('h3');
  h.textContent = `${{name}} (${{rows.length}})`;
  box.appendChild(h);
  rows.slice(0, 120).forEach(row => box.appendChild(button(row.label, () => renderEntry(row.id))));
  groups.appendChild(box);
}});
renderEntry(DATA.entries[0].id);
</script>
</body>
</html>
"""
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")
    (out_dir / "showcase-data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"output": str(out_dir / "index.html"), "entries": len(entries), "groups": len(data["groups"]), "menu": len(data["menu"])}


def build_jmdict_writer_package(args: Any) -> dict[str, Any]:
    result = import_jmdict(
        args.input,
        limit=args.limit,
        progress_every=args.progress_every,
        license_html=getattr(args, "jmdict_license_html", None),
        project_html=getattr(args, "jmdict_project_html", None),
        english_keyword_limit=getattr(args, "jmdict_english_keywords_per_entry", 4),
    )
    if not result.entries:
        raise ValueError("no JMdict entries were imported")

    dict_id = args.dict_id or "JMDICT"
    title = args.title or "JMdict Japanese-English Dictionary"
    bitmap_fonts = jmdict_bitmap_fonts(args)
    vector_fonts = jmdict_vector_fonts(args)
    glyph_renderer = jmdict_ga16_glyph_renderer(args, bitmap_fonts)

    package_dir = args.out_dir / dict_id
    if package_dir.exists() and not args.force:
        raise FileExistsError(f"output package already exists: {package_dir}; pass --force to overwrite")
    if package_dir.exists():
        shutil.rmtree(package_dir)

    progress = (lambda message: print(f"build {dict_id}: {message}", flush=True)) if args.progress_every else None
    package = build_plain_honmon_package(
        dict_id=dict_id,
        title=title,
        entries=result.entries,
        include_tagged_indexes=not args.simple_only,
        glyph_renderer=glyph_renderer,
        compression=args.compression,
        progress=progress,
        gaiji_half_start=HALF_GAIJI_START,
        gaiji_full_start=FULL_GAIJI_START,
        force_full_gaiji=False,
        compression_jobs=getattr(args, "jobs", 1),
    )
    package, extra_report = augment_jmdict_package(
        package,
        result,
        compression=args.compression,
        compression_jobs=getattr(args, "jobs", 1),
        vector_fonts=vector_fonts,
        progress=progress,
    )
    write_plain_package(package, package_dir)

    showcase_report = None
    showcase_out = getattr(args, "showcase_out_dir", None)
    if showcase_out:
        showcase_report = write_jmdict_showcase(result, Path(showcase_out), limit=getattr(args, "showcase_limit", 1000))

    file_sizes = {name: len(data) for name, data in sorted(package.files.items())}
    report = dict(result.stats)
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
                "index_key_drops": package.gaiji_allocator.index_key_drops,
                "bitmap_fonts": [str(path) for path in bitmap_fonts],
                "vector_fonts": [str(path) for path in vector_fonts],
                "layout": "split",
                "ga16_renderer": glyph_renderer.as_dict() if hasattr(glyph_renderer, "as_dict") else None,
            },
            "jmdict_features": {
                "kwindex_targets": extra_report.kwindex_targets,
                "menu_items": extra_report.menu_items,
                "panel_bins": extra_report.panel_bins,
                "template_gaiji": extra_report.template_gaiji,
                "ccaltstr": extra_report.ccaltstr,
                "numeric_sidecar_trees": "not emitted: MENU.DIC and Panels cover the browse/navigation use case without a platform sidecar tree",
            },
            "showcase": showcase_report,
            "files": file_sizes,
        }
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{dict_id}_writer_import_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report
