"""EPWING v6 export backend."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from bisect import bisect_right
from io import BytesIO
from pathlib import Path
import json
import re
import shutil
import subprocess
from typing import Any

from ..gaiji import gaiji_grid_code_for_index, gaiji_grid_index_for_code, is_bitmap_gaiji_resource_name
from ..ssed import BLOCK_SIZE
from ..writer import (
    EmbeddedBitmapFontRenderer,
    FontFallbackGlyphRenderer,
    FULL_GAIJI_START,
    GaijiAllocator,
    GaijiAssignment,
    HALF_GAIJI_START,
    IndexPointer,
    IndexTarget,
    be16,
    be32,
    blank_glyph,
    encode_body_text,
    encode_bcd_decimal,
    encode_ga16_resource,
    encode_simple_index_pages,
)
from .model import ExportBodyEvent, ExportBook, ExportDiagnostic, ExportEntry, ExportGaiji


EPWING_WAVE_CONTROL_FLAGS = b"\x00\x01\x00\x12"
EPWING_SOUND_INDEX_DATA = b"\x01\x00\x00\x00"

BITMAP_FONT_PREFERENCES = (
    "japanese/msgothic.ttc",
    "japanese/msmincho.ttc",
    "chinese/simsun.ttc",
    "chinese/simsunb.ttf",
    "chinese/SimsunExtG.ttf",
)


@dataclass(frozen=True)
class _EpwingMediaLocation:
    kind: str
    page: int
    offset: int
    media_type: str
    end_page: int = 0
    end_offset: int = 0
    inline: bool = True


@dataclass(frozen=True)
class EpwingV6Options:
    include_media: bool = True
    drop_style_markers: bool = True
    gaiji_policy: str = "unicode-map,ga16"
    gaiji_bitmap_fonts: tuple[Path, ...] = ()
    gaiji_font_root: Path | None = None
    progress: Callable[[str], None] | None = None
    progress_every: int = 5000


@dataclass
class _BitmapOnlyGa16Renderer:
    renderers: tuple[Any, ...]
    fallback_misses: Counter[str] = field(default_factory=Counter)

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

    @property
    def sources(self) -> tuple[str, ...]:
        values: list[str] = []
        for renderer in self.renderers:
            values.extend(str(source) for source in getattr(renderer, "sources", ()))
        return tuple(values)


@dataclass
class _EpwingWriteStats:
    image_controls: int = 0
    audio_controls: int = 0
    media_placeholders: int = 0
    entry_links: int = 0
    unresolved_links: int = 0
    containing_target_links: int = 0


@dataclass(frozen=True)
class EpwingV6ExportReport:
    title: str
    source_family: str
    source_id: str
    out_dir: str
    subbook_dir: str
    entries: int
    indexes: int
    navigation: int
    copyright_pages: int
    graphic_resources: int
    sound_resources: int
    honmon2_bytes: int
    honmong_bytes: int
    honmons_bytes: int
    image_controls: int
    audio_controls: int
    media_placeholders: int
    entry_link_controls: int
    unresolved_link_placeholders: int
    containing_target_links: int
    gaiji_half_entries: int
    gaiji_full_entries: int
    gaiji_map_entries: int
    gaiji_bitmap_sources: tuple[str, ...]
    gaiji_bitmap_misses: int
    diagnostics: tuple[ExportDiagnostic, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source_family": self.source_family,
            "source_id": self.source_id,
            "out_dir": self.out_dir,
            "subbook_dir": self.subbook_dir,
            "entries": self.entries,
            "indexes": self.indexes,
            "navigation": self.navigation,
            "copyright_pages": self.copyright_pages,
            "graphic_resources": self.graphic_resources,
            "sound_resources": self.sound_resources,
            "honmon2_bytes": self.honmon2_bytes,
            "honmong_bytes": self.honmong_bytes,
            "honmons_bytes": self.honmons_bytes,
            "image_controls": self.image_controls,
            "audio_controls": self.audio_controls,
            "media_placeholders": self.media_placeholders,
            "entry_link_controls": self.entry_link_controls,
            "unresolved_link_placeholders": self.unresolved_link_placeholders,
            "containing_target_links": self.containing_target_links,
            "gaiji_half_entries": self.gaiji_half_entries,
            "gaiji_full_entries": self.gaiji_full_entries,
            "gaiji_map_entries": self.gaiji_map_entries,
            "gaiji_bitmap_sources": list(self.gaiji_bitmap_sources),
            "gaiji_bitmap_misses": self.gaiji_bitmap_misses,
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }


def _pad_to_page(data: bytes) -> bytes:
    remainder = len(data) % BLOCK_SIZE
    if remainder:
        return data + bytes(BLOCK_SIZE - remainder)
    return data


def _page_count(data: bytes) -> int:
    return max(1, (len(_pad_to_page(data)) // BLOCK_SIZE))


def _progress(options: EpwingV6Options, message: str) -> None:
    if options.progress is not None:
        options.progress(message)


def _progress_interval(options: EpwingV6Options) -> int:
    return max(1, int(options.progress_every or 5000))


def _write_bytes(path: Path, data: bytes, options: EpwingV6Options) -> None:
    path.write_bytes(data)
    _progress(options, f"export: wrote {path} ({len(data):,} bytes)")


def _write_text(path: Path, text: str, options: EpwingV6Options) -> None:
    path.write_text(text, encoding="utf-8")
    _progress(options, f"export: wrote {path} ({len(text.encode('utf-8')):,} bytes)")


def _preferred_fonts(root: Path, preferences: Iterable[str]) -> list[Path]:
    return [root / rel for rel in preferences if (root / rel).is_file()]


def _bitmap_font_paths(options: EpwingV6Options) -> list[Path]:
    if options.gaiji_bitmap_fonts:
        return [Path(path) for path in options.gaiji_bitmap_fonts]
    if options.gaiji_font_root is not None:
        return _preferred_fonts(Path(options.gaiji_font_root), BITMAP_FONT_PREFERENCES)
    return []


def _build_ga16_glyph_renderer(
    options: EpwingV6Options,
    diagnostics: list[ExportDiagnostic],
) -> _BitmapOnlyGa16Renderer | None:
    font_paths = _bitmap_font_paths(options)
    if not font_paths:
        return None

    logo_bitmap_paths: list[Path] = []
    embedded_bitmap_paths: list[Path] = []
    for path in font_paths:
        if path.is_dir() or path.suffix.lower() == ".uni" or is_bitmap_gaiji_resource_name(path):
            logo_bitmap_paths.append(path)
        else:
            embedded_bitmap_paths.append(path)

    renderers: list[Any] = []
    if logo_bitmap_paths:
        try:
            renderers.append(FontFallbackGlyphRenderer.from_sources(bitmap_paths=logo_bitmap_paths, vector_paths=()))
        except Exception as exc:
            diagnostics.append(
                ExportDiagnostic(
                    "warning",
                    "epwing_ga16_bitmap_font_unavailable",
                    f"LogoVista bitmap gaiji font source could not be loaded: {exc}",
                )
            )
    if embedded_bitmap_paths:
        try:
            renderers.append(EmbeddedBitmapFontRenderer.from_paths(embedded_bitmap_paths))
        except Exception as exc:
            diagnostics.append(
                ExportDiagnostic(
                    "warning",
                    "epwing_ga16_embedded_bitmap_font_unavailable",
                    f"Embedded bitmap font source could not be loaded: {exc}",
                )
            )
    if not renderers:
        diagnostics.append(
            ExportDiagnostic(
                "warning",
                "epwing_ga16_no_usable_bitmap_font",
                "No supplied GA16 bitmap font source could be used; generated GA16 fallback cells may be blank.",
            )
        )
        return None
    renderer = _BitmapOnlyGa16Renderer(tuple(renderers))
    _progress(options, f"export: GA16 bitmap renderer sources={len(renderer.sources):,}")
    return renderer


def _store_pointer(byte_offset: int) -> tuple[int, int]:
    return byte_offset // BLOCK_SIZE + 1, byte_offset % BLOCK_SIZE


def _ssed_target_address(target: str) -> tuple[str, int, int] | None:
    parts = str(target).split(":")
    if len(parts) != 4 or parts[0] != "ssed":
        return None
    try:
        return parts[1], int(parts[2]), int(parts[3])
    except ValueError:
        return None


@dataclass
class _TargetPointerResolver:
    exact: dict[str, IndexPointer]
    ssed_addresses: list[tuple[str, int, IndexPointer]]
    ssed_keys: list[tuple[str, int]]

    @classmethod
    def build(cls, exact: dict[str, IndexPointer]) -> "_TargetPointerResolver":
        rows: list[tuple[str, int, IndexPointer]] = []
        for target, pointer in exact.items():
            parsed = _ssed_target_address(target)
            if parsed is None:
                continue
            idx_name, block, offset = parsed
            rows.append((idx_name, block * BLOCK_SIZE + offset, pointer))
        rows.sort(key=lambda row: (row[0], row[1]))
        keys = [(idx_name, address) for idx_name, address, _pointer in rows]
        return cls(exact=exact, ssed_addresses=rows, ssed_keys=keys)

    def resolve(self, target: str | None) -> tuple[IndexPointer | None, str]:
        if not target:
            return None, "missing"
        pointer = self.exact.get(target)
        if pointer is not None:
            return pointer, "exact"
        parsed = _ssed_target_address(target)
        if parsed is None:
            return None, "unresolved"
        idx_name, block, offset = parsed
        address = block * BLOCK_SIZE + offset
        pos = bisect_right(self.ssed_keys, (idx_name, address)) - 1
        if pos < 0:
            return None, "unresolved"
        row_idx, row_address, row_pointer = self.ssed_addresses[pos]
        if row_idx != idx_name or row_address > address:
            return None, "unresolved"
        next_pos = pos + 1
        if next_pos >= len(self.ssed_addresses):
            return None, "unresolved"
        next_idx, next_address, _next_pointer = self.ssed_addresses[next_pos]
        if next_idx != idx_name or address >= next_address:
            return None, "unresolved"
        return row_pointer, "containing"


def _safe_subbook_dir(source_id: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "", source_id.upper())
    return (text or "LVBOOK")[:8]


def _jis_label(text: str, max_bytes: int) -> bytes:
    out = bytearray()
    for ch in text:
        if ch == " ":
            pair = b"\x21\x21"
        else:
            try:
                from ..writer import encode_jis_cell, fullwidth_ascii

                pair = encode_jis_cell(ch) or encode_jis_cell(fullwidth_ascii(ch))
            except Exception:
                pair = None
        if pair is None:
            continue
        if len(out) + len(pair) > max_bytes:
            break
        out.extend(pair)
    return bytes(out).ljust(max_bytes, b"\x00")


def _catalogs(book: ExportBook, subbook_dir: str) -> bytes:
    header = bytearray(16)
    header[0:2] = be16(1)
    header[2:4] = be16(6)
    record = bytearray(164)
    record[0:2] = be16(0x0005)
    record[2:82] = _jis_label(book.title, 80)
    record[82:90] = subbook_dir.encode("ascii").ljust(8, b"\x00")
    record[94:96] = be16(1)
    wide_fonts = [b"GA16FULL", b"GA24FULL", b"GA30FULL", b"GA48FULL"]
    narrow_fonts = [b"GA16HALF", b"GA24HALF", b"GA30HALF", b"GA48HALF"]
    for index, name in enumerate(wide_fonts):
        record[100 + index * 8 : 108 + index * 8] = name[:8].ljust(8, b"\x00")
    for index, name in enumerate(narrow_fonts):
        record[132 + index * 8 : 140 + index * 8] = name[:8].ljust(8, b"\x00")

    extra = bytearray(164)
    extra[4:12] = b"HONMON2\x00"
    extra[41:43] = be16(0x0102)
    extra[44:52] = b"HONMONG\x00"
    extra[56:64] = b"HONMONS\x00"
    extra[53] = 0
    extra[54] = 0
    extra[55] = 0
    return _pad_to_page(bytes(header + record + extra))


def _index_record(index_id: int, start_page: int, page_count: int, data: bytes = b"\x02\x01\x55\x40") -> bytes:
    row = bytearray(16)
    row[0] = index_id & 0xFF
    row[2:6] = be32(start_page)
    row[6:10] = be32(page_count)
    row[10:14] = data[:4].ljust(4, b"\x00")
    return bytes(row)


def _index_table(records: list[bytes]) -> bytes:
    if len(records) > 0x7E:
        raise ValueError("EPWING index table has too many records")
    page = bytearray(BLOCK_SIZE)
    page[1] = len(records)
    page[4] = 0
    pos = 16
    for row in records:
        page[pos : pos + 16] = row
        pos += 16
    return bytes(page)


def _convert_image_to_bmp_payload(data: bytes, source: str, diagnostics: list[ExportDiagnostic]) -> bytes | None:
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - depends on optional local Pillow install
        diagnostics.append(
            ExportDiagnostic(
                "warning",
                "epwing_image_conversion_unavailable",
                f"Image {source} requires BMP conversion, but Pillow could not be imported: {exc}",
                source=source,
            )
        )
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            converted = image.convert("RGB")
            out = BytesIO()
            converted.save(out, format="BMP")
            return out.getvalue()
    except Exception as exc:
        diagnostics.append(
            ExportDiagnostic(
                "warning",
                "epwing_image_conversion_failed",
                f"Image {source} could not be converted to BMP: {exc}",
                source=source,
            )
        )
        return None


def _epwing_graphic_payload(resource, diagnostics: list[ExportDiagnostic]) -> tuple[bytes, str] | None:
    if resource.payload is None:
        diagnostics.append(ExportDiagnostic("warning", "epwing_media_missing_payload", "Image resource has no payload.", source=resource.resource_id))
        return None
    data = bytes(resource.payload)
    if data.startswith(b"data") and len(data) >= 8:
        payload_size = int.from_bytes(data[4:8], "little")
        image = data[8 : 8 + payload_size]
        if len(image) != payload_size:
            diagnostics.append(
                ExportDiagnostic("warning", "epwing_image_wrapped_size_mismatch", "Wrapped image payload size is inconsistent.", source=resource.resource_id)
            )
            return None
    else:
        image = data

    if image.startswith(b"BM"):
        wrapped = data if data.startswith(b"data") else b"data" + len(image).to_bytes(4, "little") + image
        return wrapped, "bmp"
    if image.startswith(b"\xff\xd8\xff"):
        wrapped = data if data.startswith(b"data") else b"data" + len(image).to_bytes(4, "little") + image
        return wrapped, "jpeg"
    if image.startswith((b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF")):
        bmp = _convert_image_to_bmp_payload(image, resource.resource_id, diagnostics)
        if bmp is None:
            return None
        diagnostics.append(
            ExportDiagnostic(
                "info",
                "epwing_image_converted_to_bmp",
                "Image resource converted to BMP for EPWING HONMONG.",
                source=resource.resource_id,
            )
        )
        return b"data" + len(bmp).to_bytes(4, "little") + bmp, "bmp"

    diagnostics.append(
        ExportDiagnostic(
            "warning",
            "epwing_image_payload_unsupported",
            "Image resource is not BMP/JPEG and could not be converted for HONMONG.",
            source=resource.resource_id,
        )
    )
    return None


def _riff_wave_chunks(data: bytes) -> bytes | None:
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return data[12:]
    return None


def _convert_audio_to_wave_chunks(data: bytes, source: str, diagnostics: list[ExportDiagnostic]) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        diagnostics.append(
            ExportDiagnostic(
                "warning",
                "epwing_audio_conversion_unavailable",
                "Audio resource requires WAVE conversion, but ffmpeg was not found.",
                source=source,
            )
        )
        return None
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", "pipe:0", "-f", "wav", "pipe:1"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        diagnostics.append(
            ExportDiagnostic(
                "warning",
                "epwing_audio_conversion_failed",
                f"Audio resource could not be converted to WAVE chunks: {exc}",
                source=source,
            )
        )
        return None
    chunks = _riff_wave_chunks(result.stdout)
    if chunks is None or not chunks.startswith(b"fmt "):
        diagnostics.append(
            ExportDiagnostic(
                "warning",
                "epwing_audio_conversion_invalid",
                "ffmpeg conversion did not produce a RIFF/WAVE stream with fmt/data chunks.",
                source=source,
            )
        )
        return None
    diagnostics.append(
        ExportDiagnostic(
            "info",
            "epwing_audio_converted_to_wave",
            "Audio resource converted to WAVE chunks for EPWING HONMONS.",
            source=source,
        )
    )
    return chunks


def _epwing_sound_payload(resource, diagnostics: list[ExportDiagnostic]) -> bytes | None:
    if resource.payload is None:
        diagnostics.append(ExportDiagnostic("warning", "epwing_media_missing_payload", "Audio resource has no payload.", source=resource.resource_id))
        return None
    payload = bytes(resource.payload)
    if payload.startswith(b"fmt "):
        return payload
    chunks = _riff_wave_chunks(payload)
    if chunks is not None and chunks.startswith(b"fmt "):
        return chunks
    return _convert_audio_to_wave_chunks(payload, resource.resource_id, diagnostics)


def _build_media_stores(
    book: ExportBook,
    options: EpwingV6Options,
    diagnostics: list[ExportDiagnostic],
) -> tuple[bytes, bytes, dict[str, _EpwingMediaLocation]]:
    honmong = bytearray()
    honmons = bytearray()
    locations: dict[str, _EpwingMediaLocation] = {}
    if not options.include_media:
        return bytes(BLOCK_SIZE), bytes(BLOCK_SIZE), locations
    if not book.resources:
        return bytes(BLOCK_SIZE), bytes(BLOCK_SIZE), locations

    _progress(options, f"export: lowering {len(book.resources):,} resource payload(s) to HONMONG/HONMONS")
    for resource in book.resources:
        if resource.resource_id in locations:
            continue
        if resource.kind == "image":
            lowered = _epwing_graphic_payload(resource, diagnostics)
            if lowered is None:
                continue
            payload, media_type = lowered
            start = len(honmong)
            honmong.extend(payload)
            page, offset = _store_pointer(start)
            inline = resource.role not in {"colscr", "figure", "monoscr"}
            locations[resource.resource_id] = _EpwingMediaLocation(
                kind="image",
                page=page,
                offset=offset,
                media_type=media_type,
                inline=inline,
            )
        elif resource.kind == "audio":
            payload = _epwing_sound_payload(resource, diagnostics)
            if payload is None:
                continue
            start = len(honmons)
            honmons.extend(payload)
            end = start + len(payload) - 1
            page, offset = _store_pointer(start)
            end_page, end_offset = _store_pointer(end)
            locations[resource.resource_id] = _EpwingMediaLocation(
                kind="audio",
                page=page,
                offset=offset,
                end_page=end_page,
                end_offset=end_offset,
                media_type="wave",
            )
    graphic_count = sum(1 for value in locations.values() if value.kind == "image")
    sound_count = sum(1 for value in locations.values() if value.kind == "audio")
    padded_honmong = _pad_to_page(bytes(honmong) or bytes(BLOCK_SIZE))
    padded_honmons = _pad_to_page(bytes(honmons) or bytes(BLOCK_SIZE))
    _progress(
        options,
        (
            "export: media stores prepared "
            f"HONMONG graphics={graphic_count:,} bytes={len(padded_honmong):,}; "
            f"HONMONS sounds={sound_count:,} bytes={len(padded_honmons):,}"
        ),
    )
    return padded_honmong, padded_honmons, locations


def _graphic_control(location: _EpwingMediaLocation) -> bytes:
    format_word = b"\x01\x00" if location.media_type == "jpeg" else b"\x00\x00"
    payload = (
        format_word
        + bytes(10)
        + encode_bcd_decimal(location.page, 8)
        + encode_bcd_decimal(location.offset, 4)
    )
    start = b"\x1f\x3c" if location.inline else b"\x1f\x4d"
    return start + payload + b"\x1f\x6d"


def _audio_control(location: _EpwingMediaLocation, label: str, gaiji: GaijiAllocator) -> bytes:
    payload = (
        EPWING_WAVE_CONTROL_FLAGS
        + encode_bcd_decimal(location.page, 8)
        + encode_bcd_decimal(location.offset, 4)
        + encode_bcd_decimal(location.end_page, 8)
        + encode_bcd_decimal(location.end_offset, 4)
    )
    text = encode_body_text(label or "sound", gaiji)
    return b"\x1f\x4a" + payload + text + b"\x1f\x6a"


def _body_events_to_epwing(
    events: tuple[ExportBodyEvent, ...],
    gaiji: GaijiAllocator,
    diagnostics: list[ExportDiagnostic],
    source: str,
    media_locations: dict[str, _EpwingMediaLocation] | None = None,
    stats: _EpwingWriteStats | None = None,
    target_resolver: _TargetPointerResolver | None = None,
    source_gaiji_codes: set[str] | None = None,
) -> bytes:
    media_locations = media_locations or {}
    source_gaiji_codes = source_gaiji_codes or set()
    out = bytearray()
    for event in events:
        if event.kind == "text":
            out.extend(encode_body_text(event.text, gaiji))
        elif event.kind == "line_break":
            out.extend(b"\x1f\x0a")
        elif event.kind == "style_start":
            if event.value == "bold":
                out.extend(b"\x1f\xe0\x00\x04")
            elif event.value == "italic":
                out.extend(b"\x1f\x10")
            elif event.value == "sub":
                out.extend(b"\x1f\x06")
            elif event.value == "sup":
                out.extend(b"\x1f\x0e")
        elif event.kind == "style_end":
            if event.value == "bold":
                out.extend(b"\x1f\xe1")
            elif event.value == "italic":
                out.extend(b"\x1f\x11")
            elif event.value == "sub":
                out.extend(b"\x1f\x07")
            elif event.value == "sup":
                out.extend(b"\x1f\x0f")
        elif event.kind == "gaiji_ref":
            code = event.value.lower()
            if re.fullmatch(r"[0-9a-f]{4}", code) and code in source_gaiji_codes:
                out.extend(bytes.fromhex(code))
            else:
                out.extend(encode_body_text(f"[{event.value}]", gaiji))
                diagnostics.append(ExportDiagnostic("warning", "gaiji_ref_placeholder", f"gaiji ref {event.value} lowered as text placeholder", source=source))
        elif event.kind == "media_ref":
            location = media_locations.get(event.value)
            if location is None:
                out.extend(encode_body_text(f"［{event.text or 'media'}］", gaiji))
                if stats is not None:
                    stats.media_placeholders += 1
                diagnostics.append(
                    ExportDiagnostic(
                        "warning",
                        "media_ref_placeholder",
                        "media reference has no EPWING media payload and was lowered as a text placeholder.",
                        source=source,
                    )
                )
            elif location.kind == "image":
                out.extend(_graphic_control(location))
                if stats is not None:
                    stats.image_controls += 1
            elif location.kind == "audio":
                out.extend(_audio_control(location, event.text, gaiji))
                if stats is not None:
                    stats.audio_controls += 1
        elif event.kind == "link":
            pointer = None
            status = "unresolved"
            if target_resolver is not None:
                pointer, status = target_resolver.resolve(event.value)
            if pointer is not None:
                out.extend(_reference_control(event.text, pointer, gaiji))
                if stats is not None:
                    stats.entry_links += 1
                    if status == "containing":
                        stats.containing_target_links += 1
            else:
                out.extend(encode_body_text(event.text or event.attrs.get("href") or "link", gaiji))
                if stats is not None:
                    stats.unresolved_links += 1
                diagnostics.append(
                    ExportDiagnostic(
                        "warning",
                        "epwing_link_target_unresolved",
                        "Entry/body link target could not be resolved to an exported EPWING text position.",
                        source=source,
                    )
                )
        elif event.kind in {"section", "ruby"}:
            diagnostics.append(ExportDiagnostic("info", f"{event.kind}_not_lowered", f"{event.kind} event has no EPWING v6 lowering yet", source=source))
        elif event.kind == "unsupported":
            diagnostics.append(ExportDiagnostic("warning", "unsupported_body_event", event.text or event.value, source=source))
    return bytes(out)


def _candidate_bytes(label: str, pointer: IndexPointer, gaiji: GaijiAllocator) -> bytes:
    """Encode an EPWING menu/candidate item pointing to another text position."""

    out = bytearray()
    out.extend(b"\x1f\x43")
    out.extend(encode_body_text(label, gaiji))
    out.extend(b"\x1f\x63")
    out.extend(encode_bcd_decimal(pointer.block, 8))
    out.extend(encode_bcd_decimal(pointer.offset, 4))
    out.extend(b"\x1f\x0a")
    return bytes(out)


def _reference_control(label: str, pointer: IndexPointer, gaiji: GaijiAllocator) -> bytes:
    """Encode an inline EPWING body reference pointing to another text position."""

    out = bytearray()
    out.extend(b"\x1f\x42\x00\x00")
    out.extend(encode_body_text(label or "link", gaiji))
    out.extend(b"\x1f\x62")
    out.extend(encode_bcd_decimal(pointer.block, 8))
    out.extend(encode_bcd_decimal(pointer.offset, 4))
    return bytes(out)


def _entry_bytes(
    entry: ExportEntry,
    gaiji: GaijiAllocator,
    diagnostics: list[ExportDiagnostic],
    media_locations: dict[str, _EpwingMediaLocation],
    stats: _EpwingWriteStats,
    target_resolver: _TargetPointerResolver,
    source_gaiji_codes: set[str],
) -> bytes:
    out = bytearray()
    out.extend(b"\x1f\x09\x00\x01")
    out.extend(b"\x1f\x41\x00\x00")
    out.extend(encode_body_text(entry.headword, gaiji))
    out.extend(b"\x1f\x61")
    out.extend(b"\x1f\x0a")
    out.extend(_body_events_to_epwing(entry.body.events, gaiji, diagnostics, entry.target, media_locations, stats, target_resolver, source_gaiji_codes))
    out.extend(b"\x1f\x0a")
    return bytes(out)


def _append_index_target(targets: list[IndexTarget], seen: set[tuple[str, int, int]], key: str, pointer: IndexPointer) -> None:
    clean = " ".join(key.split()).strip()
    if not clean:
        return
    marker = (clean.casefold(), pointer.block, pointer.offset)
    if marker in seen:
        return
    seen.add(marker)
    targets.append(IndexTarget(key=clean, body=pointer, title=pointer))


def _map_lines(gaiji: GaijiAllocator, book: ExportBook) -> str:
    source_rows = {(row.space, row.code.upper()): row for row in book.gaiji if row.unicode}
    lines = [f"# {book.title} generated by logovista-tools exporter", ""]
    for row in sorted(gaiji.assignments.values(), key=lambda item: item.code):
        prefix = "h" if row.space == "half" else "z"
        code = row.key.upper()
        source_row = source_rows.get((row.space, code))
        value = source_row.unicode if source_row is not None and source_row.unicode else row.text
        units = ",".join(f"u{ord(ch):04X}" for ch in value)
        lines.append(f"{prefix}{code}\t{units}\t{value}")
    generated_keys = {(row.space, row.key.upper()) for row in gaiji.assignments.values()}
    for (space, code), source_row in sorted(source_rows.items(), key=lambda item: (item[0][0], int(item[0][1], 16))):
        if (space, code) in generated_keys or not source_row.unicode:
            continue
        prefix = "h" if space == "half" else "z"
        units = ",".join(f"u{ord(ch):04X}" for ch in source_row.unicode)
        lines.append(f"{prefix}{code}\t{units}\t{source_row.unicode}")
    return "\n".join(lines).rstrip() + "\n"


def _source_gaiji_codes(book: ExportBook) -> set[str]:
    return {row.code.lower() for row in book.gaiji if row.bitmap_backed or row.unicode or row.template_resource}


def _source_gaiji_bitmap_sources(book: ExportBook) -> tuple[str, ...]:
    return tuple(sorted({row.bitmap_source for row in book.gaiji if row.bitmap_source}))


def _next_gaiji_code(rows: Iterable[ExportGaiji], default_start: int) -> int:
    codes = [int(row.code, 16) for row in rows if row.bitmap_backed and row.bitmap_width in {8, 16} and row.bitmap_height == 16]
    if not codes:
        return default_start
    start = min(codes)
    max_index = max(gaiji_grid_index_for_code(start, code) for code in codes)
    return gaiji_grid_code_for_index(start, max_index + 1)


def _ga16_output_assignments(
    *,
    book: ExportBook,
    generated: Iterable[GaijiAssignment],
    space: str,
    default_start: int,
    width: int,
) -> tuple[int, list[GaijiAssignment]]:
    rows: dict[int, GaijiAssignment] = {}
    for row in book.gaiji:
        if row.space != space or row.bitmap is None or row.bitmap_width != width or row.bitmap_height != 16:
            continue
        rows[int(row.code, 16)] = GaijiAssignment(text=row.unicode or row.code, code=int(row.code, 16), space=space, glyph=row.bitmap)
    for row in generated:
        rows.setdefault(row.code, row)
    if not rows:
        return default_start, []
    start_code = min(rows)
    max_index = max(gaiji_grid_index_for_code(start_code, code) for code in rows)
    assignments: list[GaijiAssignment] = []
    for index in range(max_index + 1):
        code = gaiji_grid_code_for_index(start_code, index)
        assignments.append(rows.get(code) or GaijiAssignment(text="", code=code, space=space, glyph=blank_glyph(width, 16)))
    return start_code, assignments


def _encode_epwing_ga16_resource(
    assignments: Iterable[GaijiAssignment],
    *,
    width: int,
    start_code: int,
) -> bytes:
    return _pad_to_page(encode_ga16_resource(assignments, width=width, start_code=start_code))


class EpwingV6Backend:
    """Write a conservative EPWING v6 package from an export book."""

    def __init__(self, options: EpwingV6Options | None = None) -> None:
        self.options = options or EpwingV6Options()

    def write(self, book: ExportBook, out_dir: Path) -> EpwingV6ExportReport:
        _progress(self.options, f"export: EPWING v6 backend preparing {out_dir}")
        out_dir.mkdir(parents=True, exist_ok=True)
        subbook_dir = _safe_subbook_dir(book.source_id)
        for stale in (out_dir / "CATALOGS", out_dir / "LANGUAGE", out_dir / f"{subbook_dir}.map", out_dir / subbook_dir):
            if stale.is_dir():
                shutil.rmtree(stale)
            elif stale.exists():
                stale.unlink()
        data_dir = out_dir / subbook_dir / "DATA"
        gaiji_dir = out_dir / subbook_dir / "GAIJI"
        data_dir.mkdir(parents=True, exist_ok=True)
        gaiji_dir.mkdir(parents=True, exist_ok=True)
        _progress(self.options, f"export: EPWING subbook={subbook_dir} entries={len(book.entries):,}")

        diagnostics: list[ExportDiagnostic] = list(book.diagnostics)
        honmong, honmons, media_locations = _build_media_stores(book, self.options, diagnostics)
        gaiji_renderer = _build_ga16_glyph_renderer(self.options, diagnostics)
        source_gaiji_bitmap_sources = _source_gaiji_bitmap_sources(book)
        half_source_rows = [row for row in book.gaiji if row.space == "half"]
        full_source_rows = [row for row in book.gaiji if row.space == "full"]
        gaiji = GaijiAllocator(
            half_start=_next_gaiji_code(half_source_rows, HALF_GAIJI_START),
            full_start=_next_gaiji_code(full_source_rows, FULL_GAIJI_START),
            glyph_renderer=gaiji_renderer,
        )
        source_gaiji_codes = _source_gaiji_codes(book)
        stats = _EpwingWriteStats()
        body = bytearray(bytes(BLOCK_SIZE))
        word_targets: list[IndexTarget] = []
        endword_targets: list[IndexTarget] = []
        seen_word_targets: set[tuple[str, int, int]] = set()
        seen_endword_targets: set[tuple[str, int, int]] = set()
        target_positions: dict[str, IndexPointer] = {}

        interval = _progress_interval(self.options)
        all_entry_targets = {entry.target for entry in book.entries}
        sizing_resolver = _TargetPointerResolver.build({target: IndexPointer(block=0, offset=0) for target in all_entry_targets})
        cursor = len(body)
        sizing_stats = _EpwingWriteStats()
        sizing_diagnostics: list[ExportDiagnostic] = []
        for entry in book.entries:
            pointer = IndexPointer(block=(cursor // BLOCK_SIZE) + 1, offset=cursor % BLOCK_SIZE)
            target_positions[entry.target] = pointer
            cursor += len(_entry_bytes(entry, gaiji, sizing_diagnostics, media_locations, sizing_stats, sizing_resolver, source_gaiji_codes))
        target_resolver = _TargetPointerResolver.build(target_positions)

        _progress(self.options, "export: encoding entries into HONMON2 body stream")
        for entry_index, entry in enumerate(book.entries, start=1):
            pointer = target_positions[entry.target]
            body.extend(_entry_bytes(entry, gaiji, diagnostics, media_locations, stats, target_resolver, source_gaiji_codes))
            for key in entry.keys:
                _append_index_target(word_targets, seen_word_targets, key, pointer)
                _append_index_target(endword_targets, seen_endword_targets, key, pointer)
            if entry_index == 1 or entry_index % interval == 0 or entry_index == len(book.entries):
                _progress(self.options, f"export: encoded {entry_index:,}/{len(book.entries):,} entries ({len(body):,} body bytes)")
        entry_body_page_count = max(1, (len(_pad_to_page(bytes(body))) - BLOCK_SIZE) // BLOCK_SIZE)

        unmatched_index_rows = 0
        _progress(self.options, f"export: applying {len(book.indexes):,} source index row(s)")
        for index in book.indexes:
            pointer = target_positions.get(index.target)
            if pointer is None:
                unmatched_index_rows += 1
                continue
            if index.mode == "backward":
                _append_index_target(endword_targets, seen_endword_targets, index.key, pointer)
            elif index.mode in {"forward", "exact", "keyword", "cross-reference", "body-only", "unknown"}:
                _append_index_target(word_targets, seen_word_targets, index.key, pointer)
        if unmatched_index_rows:
            diagnostics.append(
                ExportDiagnostic(
                    "info",
                    "source_index_targets_unmatched",
                    f"{unmatched_index_rows} source index rows pointed outside the exported entry set.",
                )
            )
        _progress(
            self.options,
            f"export: prepared index targets word={len(word_targets):,} endword={len(endword_targets):,} unmatched={unmatched_index_rows:,}",
        )

        menu_start = 0
        menu_pages = 0
        if book.navigation:
            _progress(self.options, f"export: encoding {len(book.navigation):,} navigation row(s)")
            body = bytearray(_pad_to_page(bytes(body)))
            menu_start = len(body) // BLOCK_SIZE + 1
            for node in book.navigation:
                pointer, status = target_resolver.resolve(node.target)
                if pointer is not None:
                    body.extend(_candidate_bytes(node.label, pointer, gaiji))
                    if status == "containing":
                        stats.containing_target_links += 1
                else:
                    body.extend(encode_body_text(node.label, gaiji))
                    body.extend(b"\x1f\x0a")
                    diagnostics.append(
                        ExportDiagnostic(
                            "warning",
                            "epwing_menu_target_unmatched",
                            f"Navigation item {node.label!r} does not point to an exported entry; emitted as non-clickable text.",
                            source=node.target,
                        )
                    )
            menu_pages = _page_count(bytes(body[(menu_start - 1) * BLOCK_SIZE :]))

        copyright_start = 0
        copyright_pages = 0
        if book.copyright:
            _progress(self.options, f"export: encoding {len(book.copyright):,} copyright/info row(s)")
            body = bytearray(_pad_to_page(bytes(body)))
            copyright_start = len(body) // BLOCK_SIZE + 1
            for node in book.copyright:
                body.extend(encode_body_text(node.label, gaiji))
                body.extend(b"\x1f\x0a")
                if node.body is not None:
                    body.extend(
                        _body_events_to_epwing(
                            node.body.events,
                            gaiji,
                            diagnostics,
                            node.target or "copyright",
                            media_locations,
                            stats,
                            target_resolver,
                            source_gaiji_codes,
                        )
                    )
                    body.extend(b"\x1f\x0a")
            copyright_pages = _page_count(bytes(body[(copyright_start - 1) * BLOCK_SIZE :]))

        graphic_resources = sum(1 for value in media_locations.values() if value.kind == "image")
        sound_resources = sum(1 for value in media_locations.values() if value.kind == "audio")
        _progress(
            self.options,
            (
                "export: media controls "
                f"images={stats.image_controls:,} audio={stats.audio_controls:,} placeholders={stats.media_placeholders:,}; "
                f"entry_links={stats.entry_links:,} unresolved_links={stats.unresolved_links:,} containing_links={stats.containing_target_links:,}"
            ),
        )

        body = bytearray(_pad_to_page(bytes(body)))
        index_records: list[bytes] = [_index_record(0x00, 2, entry_body_page_count, b"\x00\x00\x00\x00")]
        if word_targets:
            _progress(self.options, f"export: writing forward index with {len(word_targets):,} target(s)")
            word_start = len(body) // BLOCK_SIZE + 1
            word_index = encode_simple_index_pages(word_targets, start_block=word_start, gaiji=gaiji)
            body.extend(word_index)
            index_records.append(_index_record(0x91, word_start, _page_count(word_index)))
        if endword_targets:
            _progress(self.options, f"export: writing backward index with {len(endword_targets):,} target(s)")
            endword_start = len(body) // BLOCK_SIZE + 1
            endword_index = encode_simple_index_pages(endword_targets, start_block=endword_start, gaiji=gaiji, reverse_keys=True)
            body.extend(endword_index)
            index_records.append(_index_record(0x71, endword_start, _page_count(endword_index)))
        if menu_start:
            index_records.append(_index_record(0x01, menu_start, max(1, menu_pages), b"\x00\x00\x00\x00"))
        if copyright_start:
            index_records.append(_index_record(0x02, copyright_start, max(1, copyright_pages), b"\x00\x00\x00\x00"))
        if sound_resources:
            index_records.append(_index_record(0xD8, 1, _page_count(honmons), EPWING_SOUND_INDEX_DATA))
        body[0:BLOCK_SIZE] = _index_table(index_records)
        _progress(self.options, f"export: assembled HONMON2 with {len(index_records):,} index record(s)")

        honmon2 = _pad_to_page(bytes(body))
        if gaiji.assignments and gaiji_renderer is None:
            diagnostics.append(
                ExportDiagnostic(
                    "warning",
                    "epwing_ga16_blank_fallback_no_renderer",
                    "Generated GA16 fallback cells are blank because no bitmap gaiji font source was configured.",
                )
            )
        if isinstance(gaiji_renderer, _BitmapOnlyGa16Renderer) and gaiji_renderer.fallback_misses:
            diagnostics.append(
                ExportDiagnostic(
                    "warning",
                    "epwing_ga16_bitmap_fallback_misses",
                    f"{sum(gaiji_renderer.fallback_misses.values())} generated GA16 fallback glyph(s) were blank after bitmap font lookup misses.",
                )
            )
        _progress(
            self.options,
            (
                "export: GA16 assignments "
                f"half={len(gaiji.half_assignments):,} full={len(gaiji.full_assignments):,} "
                f"bitmap_misses={sum(gaiji_renderer.fallback_misses.values()) if isinstance(gaiji_renderer, _BitmapOnlyGa16Renderer) else 0:,}"
            ),
        )
        half_start, half_rows = _ga16_output_assignments(
            book=book,
            generated=gaiji.half_assignments,
            space="half",
            default_start=HALF_GAIJI_START,
            width=8,
        )
        full_start, full_rows = _ga16_output_assignments(
            book=book,
            generated=gaiji.full_assignments,
            space="full",
            default_start=FULL_GAIJI_START,
            width=16,
        )
        _progress(
            self.options,
            (
                "export: GA16 output rows "
                f"half={len(half_rows):,} start=0x{half_start:04X}; "
                f"full={len(full_rows):,} start=0x{full_start:04X}"
            ),
        )

        _write_bytes(data_dir / "HONMON2", honmon2, self.options)
        _write_bytes(data_dir / "HONMONG", honmong, self.options)
        _write_bytes(data_dir / "HONMONS", honmons, self.options)
        _write_bytes(out_dir / "CATALOGS", _catalogs(book, subbook_dir), self.options)

        _write_bytes(gaiji_dir / "GA16HALF", _encode_epwing_ga16_resource(half_rows, width=8, start_code=half_start), self.options)
        _write_bytes(gaiji_dir / "GA16FULL", _encode_epwing_ga16_resource(full_rows, width=16, start_code=full_start), self.options)
        map_text = _map_lines(gaiji, book)
        _write_text(out_dir / f"{subbook_dir}.map", map_text, self.options)

        report = EpwingV6ExportReport(
            title=book.title,
            source_family=book.source_family.value,
            source_id=book.source_id,
            out_dir=str(out_dir),
            subbook_dir=subbook_dir,
            entries=len(book.entries),
            indexes=len(index_records),
            navigation=len(book.navigation),
            copyright_pages=len(book.copyright),
            graphic_resources=graphic_resources,
            sound_resources=sound_resources,
            honmon2_bytes=len(honmon2),
            honmong_bytes=len(honmong),
            honmons_bytes=len(honmons),
            image_controls=stats.image_controls,
            audio_controls=stats.audio_controls,
            media_placeholders=stats.media_placeholders,
            entry_link_controls=stats.entry_links,
            unresolved_link_placeholders=stats.unresolved_links,
            containing_target_links=stats.containing_target_links,
            gaiji_half_entries=len(half_rows),
            gaiji_full_entries=len(full_rows),
            gaiji_map_entries=sum(1 for line in map_text.splitlines() if line and not line.startswith("#")),
            gaiji_bitmap_sources=(
                *source_gaiji_bitmap_sources,
                *(gaiji_renderer.sources if isinstance(gaiji_renderer, _BitmapOnlyGa16Renderer) else ()),
            ),
            gaiji_bitmap_misses=sum(gaiji_renderer.fallback_misses.values()) if isinstance(gaiji_renderer, _BitmapOnlyGa16Renderer) else 0,
            diagnostics=tuple(diagnostics),
        )
        _write_text(out_dir / "logovista-export-report.json", json.dumps(report.as_dict(), ensure_ascii=False, indent=2), self.options)
        _progress(
            self.options,
            f"export: finished EPWING v6 export entries={report.entries:,} indexes={report.indexes:,} diagnostics={len(report.diagnostics):,}",
        )
        return report
