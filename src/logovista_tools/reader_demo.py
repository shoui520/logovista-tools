"""Generate static reader proof-of-concept pages."""

from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from .body_boundaries import index_body_marker_boundaries_for_source, target_needs_index_boundaries
from .ccaltstr import parse_ccaltstr_file
from .colorsamples import load_color_sample_map
from .entries import DictionarySource, decode_tokens, discover_dictionaries, tokens_to_text
from .gaiji import load_gaiji_profile
from .hcrender import (
    HC_RENDER_BASE_CSS,
    HC_RENDER_FINAL_CSS,
    HcRenderOptions,
    _effective_renderer_code_for_source,
    _find_index_hits_for_source,
    _make_colscr_image_resolver,
    _prepare_hc_render_assets,
    _renderer_for_source,
    render_hc_target_body,
)
from .indexes import scan_index_component_reader
from .menus import MENU_LIKE_NAMES, MENU_LIKE_TYPES, build_menu_tree, parse_menu_stream, resolve_menu_record_destinations
from .multi import parse_multi_descriptor
from .movies import movie_ids_for_idx
from .navigation import NavigationResolver, NavigationTarget, address_key
from .panels import (
    PanelBinParseError,
    iter_panel_cells,
    iter_panel_references,
    panel_bin_navigation_rows,
    panel_cell_navigation_row,
    parse_panel_bin_file,
)
from .pcmdata import detect_shared_wave_stream, find_pcmdata_path, parse_pcmdata_record, portable_audio_bytes
from .pcmu import load_pcmu_index, read_pcmu_record
from .sounddata import load_sounddata_index, portable_sounddata_audio_bytes, read_sounddata_record
from .ssed import (
    BLOCK_SIZE,
    SsedRandomReader,
    find_case_insensitive,
    is_metadata_noise_path,
    parse_ssedinfo,
    resolve_case_insensitive_path,
)


PANEL_METADATA_CANDIDATES = (
    "Panels.xml",
    "Panels.plist",
    "menu_.plist",
    "Panel/Panels.xml",
    "Panel/Panels.plist",
)


@dataclass(frozen=True)
class _QueuedAddress:
    block: int
    offset: int
    label: str
    source: str


@dataclass(frozen=True)
class _RenderedEntry:
    key: str
    block: int
    offset: int
    label: str
    source: str
    title: str
    html: str
    plain: str
    links: tuple[dict[str, Any], ...]
    audio: tuple[dict[str, Any], ...]
    body_source: str = "raw_honmon_controls"

    def as_manifest(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "block": self.block,
            "offset": self.offset,
            "label": self.label,
            "source": self.source,
            "title": self.title,
            "body_source": self.body_source,
            "links": list(self.links),
            "audio": list(self.audio),
        }


def add_reader_demo_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, help="Dictionary package directory, collection root, or direct .IDX path.")
    parser.add_argument("--dict", action="append", help="Dictionary id to render.")
    parser.add_argument("--query", action="append", default=[], help="Seed query to render through native indexes.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-reader-demo"))
    parser.add_argument("--panel-row-limit", type=int, default=80, help="Maximum external Panel body rows to include.")
    parser.add_argument("--menu-row-limit", type=int, default=120, help="Maximum MENU.DIC rows to include.")
    parser.add_argument("--menu-preview-limit", type=int, default=80, help="Rows to show when a MENU row targets another MENU region.")
    parser.add_argument("--index-row-limit", type=int, default=120, help="Rows to show from KW/MUL auxiliary indexes.")
    parser.add_argument("--hanrei-page-limit", type=int, default=40, help="Maximum HANREI pages/links to include.")
    parser.add_argument("--ccaltstr-row-limit", type=int, default=120, help="Maximum CCALTSTR alternate-string rows to include.")
    parser.add_argument("--link-depth", type=int, default=1, help="How many rounds of entry-internal links to pre-render.")
    parser.add_argument("--max-entries", type=int, default=80, help="Maximum entry bodies to embed in the page.")
    parser.add_argument("--no-audio", action="store_true", help="Do not extract PCMDATA/SoundData audio files.")
    parser.add_argument("--vertical", action="store_true", help="Apply vertical-rendering hints.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary JSON.")


def _safe_id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.:-]+", "-", value)


def _address_key(block: int, offset: int) -> str:
    return address_key(block, offset)


def _target_key(target: dict[str, Any] | None) -> str | None:
    if not target:
        return None
    try:
        return _address_key(int(target["block"]), int(target["offset"]))
    except (KeyError, TypeError, ValueError):
        return None


def _nav_row_key(component: str, line_index: int) -> str:
    return f"{component}:{line_index}"


def _record_containing_component_offset(records: list[Any], offset: int) -> Any | None:
    for record in records:
        if int(record.byte_start) <= offset < int(record.byte_end):
            return record
    return None


def _is_honmon_address(
    source: DictionarySource,
    block: int,
    offset: int,
    *,
    reader: SsedRandomReader | None = None,
) -> bool:
    if block < source.honmon_start_block or offset < 0:
        return False
    if reader is None:
        try:
            reader = SsedRandomReader(source.honmon)
        except Exception:
            return False
    absolute = (block - reader.start_block) * BLOCK_SIZE + offset
    return 0 <= absolute < reader.expanded_size


def _first_plain_line(value: str, fallback: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return fallback


def _panel_metadata_paths(root: Path) -> list[Path]:
    found: list[Path] = []
    for base in (root, root.parent):
        for candidate in PANEL_METADATA_CANDIDATES:
            path = resolve_case_insensitive_path(base, candidate)
            if path is not None and path.is_file() and path not in found:
                found.append(path)
    return found


def _panel_bin_roots(root: Path) -> list[Path]:
    candidates = [root]
    for base, relative in (
        (root, "Panel"),
        (root, "bin"),
        (root.parent, f"{root.name}_Panel"),
        (root.parent, "bin"),
    ):
        resolved = resolve_case_insensitive_path(base, relative)
        if resolved is not None:
            candidates.append(resolved)
    found: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)
    return found


def _resolve_panel_bin(root: Path, filename: str, data_type: str) -> Path | None:
    normalized = filename.replace("\\", "/")
    names = [normalized]
    if data_type.lower() == "bin" and Path(normalized).suffix.lower() != ".bin":
        names.append(f"{normalized}.bin")
    rel_without_panel = normalized.removeprefix("Panel/")
    if rel_without_panel != normalized:
        names.append(rel_without_panel)
        if data_type.lower() == "bin" and Path(rel_without_panel).suffix.lower() != ".bin":
            names.append(f"{rel_without_panel}.bin")
    for base in _panel_bin_roots(root):
        for name in names:
            path = resolve_case_insensitive_path(base, name)
            if path is not None and path.is_file():
                return path
    return None


def _audio_target_key(target: dict[str, Any]) -> str | None:
    if str(target.get("component") or "") == "SoundData":
        try:
            return f"sounddata:{int(target['sound_id']):08x}"
        except (KeyError, TypeError, ValueError):
            return None
    try:
        return (
            f"{int(target['start_block']):08d}:{int(target['start_offset']):04d}-"
            f"{int(target['end_block']):08d}:{int(target['end_offset']):04d}"
        )
    except (KeyError, TypeError, ValueError):
        return None


_LOCAL_SRC_RE = re.compile(r'(\bsrc\s*=\s*)(["\'])([^"\']+)(["\'])', re.IGNORECASE)


def _rewrite_local_asset_refs(fragment: str, out_dir: Path) -> str:
    """Rewrite bare template image references to copied demo assets."""

    templates_dir = out_dir / "Templates"

    def replace_src(match: re.Match[str]) -> str:
        prefix, quote, value, suffix_quote = match.groups()
        source = html.unescape(value).replace("\\", "/")
        lower = source.lower()
        if (
            not source
            or "/" in source
            or ":" in source
            or lower.startswith(("data:", "javascript:", "mailto:"))
            or (out_dir / source).exists()
        ):
            return match.group(0)
        if (templates_dir / source).is_file():
            return f"{prefix}{quote}Templates/{html.escape(source, quote=True)}{suffix_quote}"
        return match.group(0)

    return _LOCAL_SRC_RE.sub(replace_src, fragment)


class _ReaderDemoBuilder:
    def __init__(self, source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> None:
        self.source = source
        self.out_dir = out_dir
        self.args = args
        self.catalog_title, self.elements = parse_ssedinfo(source.idx)
        self.navigation = NavigationResolver(self.elements)
        self.renderer = _renderer_for_source(source, compute_hash=False)
        self.renderer_code = _effective_renderer_code_for_source(source, self.renderer)
        self.reader = SsedRandomReader(source.honmon)
        self.boundary_offsets: tuple[int, ...] | None = None
        self.image_sources, self.html_templates, self.stylesheet, self.copied_assets, self.renderer_assets = _prepare_hc_render_assets(
            source,
            out_dir,
            self.renderer,
        )
        self.options = HcRenderOptions(
            gaiji_map=source.gaiji_map,
            image_sources=self.image_sources,
            html_templates=self.html_templates,
            renderer_assets=self.renderer_assets,
            renderer_code=self.renderer_code,
            vertical=bool(args.vertical),
            media_image_resolver=_make_colscr_image_resolver(source, out_dir),
            movie_ids=movie_ids_for_idx(source.idx),
            color_samples=load_color_sample_map(source.idx),
        )
        self.queue: deque[tuple[_QueuedAddress, int]] = deque()
        self.queued: set[str] = set()
        self.entries: dict[str, _RenderedEntry] = {}
        self.audio_files: dict[str, str] = {}
        self.audio_dir = out_dir / "audio"
        self.pcmdata_path = find_pcmdata_path(source.idx)
        self.pcm_reader = SsedRandomReader(self.pcmdata_path) if self.pcmdata_path is not None else None
        self.pcmu_index = load_pcmu_index(source.idx)
        self.sounddata_index = load_sounddata_index(source.idx)
        if self.sounddata_index is not None:
            self.options = replace(self.options, sounddata_ids=frozenset(self.sounddata_index.records))
        if self.pcm_reader is not None:
            prefix = self.pcm_reader.read(0, min(2048, self.pcm_reader.expanded_size))
            self.shared_wave = detect_shared_wave_stream(prefix)
        else:
            self.shared_wave = None
        self.failures: list[dict[str, Any]] = []

    def enqueue(self, block: int, offset: int, *, label: str, source: str, depth: int = 0) -> None:
        resolution = self.navigation.resolve_body(block, offset, expanded_size=self.reader.expanded_size)
        if resolution.target is None:
            return
        self.enqueue_target(resolution.target, label=label, source=source, depth=depth)

    def enqueue_target(self, target: NavigationTarget, *, label: str, source: str, depth: int = 0) -> None:
        if not target.is_body or not (0 <= target.relative_offset < self.reader.expanded_size):
            return
        key = target.address_key
        if key in self.queued or key in self.entries:
            return
        if len(self.queued) + len(self.entries) >= int(self.args.max_entries):
            return
        self.queued.add(key)
        self.queue.append((_QueuedAddress(block=target.block, offset=target.offset, label=label, source=source), depth))

    def render_queued(self) -> None:
        while self.queue and len(self.entries) < int(self.args.max_entries):
            address, depth = self.queue.popleft()
            key = _address_key(address.block, address.offset)
            if key in self.entries:
                continue
            entry = self._render_address(address)
            if entry is None:
                continue
            self.entries[key] = entry
            if depth >= int(self.args.link_depth):
                continue
            for link in entry.links:
                target_key = _target_key(link.get("target") if isinstance(link, dict) else None)
                target = link.get("target") if isinstance(link, dict) else None
                if target_key and isinstance(target, dict):
                    try:
                        self.enqueue(
                            int(target["block"]),
                            int(target["offset"]),
                            label=f"link from {entry.title}",
                            source="entry-link",
                            depth=depth + 1,
                        )
                    except (KeyError, TypeError, ValueError):
                        continue

    def _render_address(self, address: _QueuedAddress) -> _RenderedEntry | None:
        target_offset = (address.block - self.source.honmon_start_block) * BLOCK_SIZE + address.offset
        if self.boundary_offsets is None and target_needs_index_boundaries(self.reader, target_offset):
            self.boundary_offsets = index_body_marker_boundaries_for_source(self.source, self.reader)
        result = render_hc_target_body(
            self.source,
            self.out_dir,
            self.reader,
            self.renderer_code,
            self.options,
            target_offset,
            boundary_offsets=self.boundary_offsets,
            vertical=bool(self.args.vertical),
        )
        if result is None:
            self.failures.append(
                {
                    "kind": "entry_slice_unresolved",
                    "block": address.block,
                    "offset": address.offset,
                    "label": address.label,
                    "source": address.source,
                }
            )
            return None
        entry_html = _rewrite_local_asset_refs(result.html, self.out_dir)
        if not self.args.no_audio:
            entry_html = self._rewrite_audio(entry_html, result.audio)
            entry_html = _rewrite_local_asset_refs(entry_html, self.out_dir)
        title = _first_plain_line(result.plain, address.label)
        return _RenderedEntry(
            key=_address_key(address.block, address.offset),
            block=address.block,
            offset=address.offset,
            label=address.label,
            source=address.source,
            title=title,
            html=entry_html,
            plain=result.plain,
            links=tuple(result.links),
            audio=tuple(result.audio),
            body_source=result.body_source,
        )

    def _rewrite_audio(self, fragment: str, audio_rows: Iterable[dict[str, Any]]) -> str:
        for audio_row in audio_rows:
            target = audio_row.get("target") if isinstance(audio_row, dict) else None
            if not isinstance(target, dict):
                continue
            resource_id = str(target.get("resource_id") or "")
            if not resource_id:
                continue
            rel = self._write_audio_file(target)
            if rel is None:
                continue
            token = _safe_id(resource_id)
            fragment = fragment.replace(
                f'href="#lv-audio-{token}"',
                f'href="{html.escape(rel, quote=True)}" data-lv-audio-file="{html.escape(rel, quote=True)}"',
            )
        return fragment

    def _write_audio_file(self, target: dict[str, Any]) -> str | None:
        resource_id = str(target.get("resource_id") or "")
        if resource_id in self.audio_files:
            return self.audio_files[resource_id]
        if str(target.get("component") or "") == "SoundData":
            if self.sounddata_index is None:
                return None
            try:
                sound_id = int(target["sound_id"])
            except (KeyError, TypeError, ValueError):
                return None
            decoded = read_sounddata_record(self.sounddata_index, sound_id)
            if decoded is None:
                return None
            _map_record, raw, record, _chunks = decoded
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            name = f"{_safe_id(_audio_target_key(target) or resource_id).replace(':', '_')}.{record.extension}"
            (self.audio_dir / name).write_bytes(portable_sounddata_audio_bytes(record, raw))
            rel = f"audio/{name}"
            self.audio_files[resource_id] = rel
            return rel
        if self.pcm_reader is None:
            if self.pcmu_index is not None:
                pointer = None
                try:
                    from .pcmdata import PcmPointer

                    pointer = PcmPointer(
                        payload=b"",
                        kind=int(target.get("kind") or 0),
                        flags=int(target.get("flags") or 0),
                        start_block=int(target["start_block"]),
                        start_offset=int(target["start_offset"]),
                        end_block=int(target["end_block"]),
                        end_offset=int(target["end_offset"]),
                    )
                except (KeyError, TypeError, ValueError):
                    pointer = None
                if pointer is not None:
                    decoded = read_pcmu_record(self.pcmu_index, pointer)
                    if decoded is not None:
                        map_record, raw, record, _chunks = decoded
                        self.audio_dir.mkdir(parents=True, exist_ok=True)
                        name = f"{_safe_id(_audio_target_key(target) or resource_id).replace(':', '_')}_{map_record.stem}.{record.extension}"
                        (self.audio_dir / name).write_bytes(portable_audio_bytes(record, raw))
                        rel = f"audio/{name}"
                        self.audio_files[resource_id] = rel
                        return rel
            return None
        try:
            start_block = int(target["start_block"])
            start_offset = int(target["start_offset"])
            end_block = int(target["end_block"])
            end_offset = int(target["end_offset"])
        except (KeyError, TypeError, ValueError):
            return None
        start = (start_block - self.pcm_reader.start_block) * BLOCK_SIZE + start_offset
        end = (end_block - self.pcm_reader.start_block) * BLOCK_SIZE + end_offset
        size = end - start + 1
        if start < 0 or size <= 0 or start + size > self.pcm_reader.expanded_size:
            return None
        raw = self.pcm_reader.read(start, size)
        parsed = parse_pcmdata_record(raw, start, shared_wave=self.shared_wave)
        if parsed is None:
            return None
        record, _chunks = parsed
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        name = f"{_safe_id(_audio_target_key(target) or resource_id).replace(':', '_')}.{record.extension}"
        (self.audio_dir / name).write_bytes(portable_audio_bytes(record, raw))
        rel = f"audio/{name}"
        self.audio_files[resource_id] = rel
        return rel


def _query_seeds(builder: _ReaderDemoBuilder, queries: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query in queries:
        hits = _find_index_hits_for_source(builder.source, query, limit=3)
        rendered = 0
        for hit in hits:
            builder.enqueue(hit.body_block, hit.body_offset, label=hit.target_key or hit.key or query, source=f"query:{query}")
            rendered += 1
            if rendered >= 1:
                break
        rows.append({"query": query, "hits": [hit.as_dict() for hit in hits], "seeded": rendered})
    return rows


def _menu_target_for_record(record: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    item = record.as_dict()
    destination = item.get("destination")
    target = destination.get("target") if isinstance(destination, dict) else None
    if isinstance(destination, dict) and isinstance(target, dict):
        return destination, target
    return None


def _menu_record_item(
    builder: _ReaderDemoBuilder,
    element_filename: str,
    record: Any,
    *,
    all_records: list[Any],
    selected_line_indexes: set[int],
) -> tuple[dict[str, Any], int]:
    item = record.as_dict()
    item["component"] = element_filename
    item["navigation_key"] = _nav_row_key(element_filename, record.line_index)
    item["target_key"] = None
    item["navigation_target_key"] = None
    item["navigation_preview_key"] = None
    seeded = 0
    target_pair = _menu_target_for_record(record)
    if target_pair is None:
        return item, seeded
    destination, target = target_pair
    if target.get("kind") == "body":
        try:
            block = int(destination["block"])
            offset = int(destination["offset"])
            key = str(target.get("address_key") or _address_key(block, offset))
            item["target_key"] = key
            builder.enqueue(block, offset, label=record.label(), source="menu")
            seeded += 1
        except (KeyError, TypeError, ValueError):
            pass
    elif target.get("component") == element_filename:
        try:
            relative_offset = int(target["relative_offset"])
        except (KeyError, TypeError, ValueError):
            relative_offset = -1
        target_record = _record_containing_component_offset(all_records, relative_offset)
        if target_record is not None:
            target_key = _nav_row_key(element_filename, target_record.line_index)
            item["navigation_preview_key"] = target_key
            if target_record.line_index in selected_line_indexes:
                item["navigation_target_key"] = target_key
    return item, seeded


def _menu_data(builder: _ReaderDemoBuilder) -> dict[str, Any]:
    gaiji_profile = load_gaiji_profile(builder.source.idx)
    components: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    seeded = 0
    for element in builder.elements:
        if element.type not in MENU_LIKE_TYPES and element.filename.upper() not in MENU_LIKE_NAMES:
            continue
        path = find_case_insensitive(builder.source.idx.parent, element.filename)
        if path is None:
            components.append({"component": element.filename, "status": "missing_file"})
            continue
        reader = SsedRandomReader(path)
        parsed = parse_menu_stream(reader.read(0, reader.expanded_size), gaiji="unicode", gaiji_map=gaiji_profile.map)
        resolve_menu_record_destinations(parsed.records, builder.elements)
        limited_records = parsed.records[: builder.args.menu_row_limit]
        limited_line_indexes = {record.line_index for record in limited_records}
        record_indexes = {record.line_index: index for index, record in enumerate(parsed.records)}
        preview_targets: dict[str, Any] = {}
        for record in limited_records:
            item, row_seeded = _menu_record_item(
                builder,
                element.filename,
                record,
                all_records=parsed.records,
                selected_line_indexes=limited_line_indexes,
            )
            seeded += row_seeded
            if item.get("navigation_preview_key"):
                target_record = next(
                    (
                        candidate
                        for candidate in parsed.records
                        if _nav_row_key(element.filename, candidate.line_index) == item["navigation_preview_key"]
                    ),
                    None,
                )
                if target_record is not None:
                    preview_targets.setdefault(str(item["navigation_preview_key"]), target_record)
            rows.append(item)
        for preview_key, target_record in preview_targets.items():
            start_index = record_indexes.get(target_record.line_index)
            if start_index is None:
                continue
            preview_records = parsed.records[start_index : start_index + int(builder.args.menu_preview_limit)]
            preview_rows: list[dict[str, Any]] = []
            preview_line_indexes = {record.line_index for record in preview_records}
            for preview_record in preview_records:
                item, row_seeded = _menu_record_item(
                    builder,
                    element.filename,
                    preview_record,
                    all_records=parsed.records,
                    selected_line_indexes=preview_line_indexes | limited_line_indexes,
                )
                seeded += row_seeded
                preview_rows.append(item)
            previews.append(
                {
                    "component": element.filename,
                    "navigation_key": preview_key,
                    "title": target_record.label(),
                    "rows": preview_rows,
                }
            )
        components.append(
            {
                "component": element.filename,
                "status": "ok",
                "records_total": len(parsed.records),
                "records_in_demo": min(len(parsed.records), builder.args.menu_row_limit),
                "tree_roots": len(build_menu_tree(limited_records)),
            }
        )
    return {
        "title": builder.catalog_title,
        "components": components,
        "rows": rows,
        "previews": previews,
        "seeded_entries": seeded,
    }


def _panel_data(builder: _ReaderDemoBuilder) -> dict[str, Any]:
    metadata_paths = _panel_metadata_paths(builder.source.idx.parent)
    inline_cells: list[dict[str, Any]] = []
    panel_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seeded = 0
    body_rows_seen = 0
    for metadata_path in metadata_paths:
        metadata_cells = []
        try:
            metadata_cells = list(iter_panel_cells(metadata_path))
            for cell in metadata_cells:
                row = panel_cell_navigation_row(cell, elements=builder.elements).as_dict()
                row["metadata"] = str(metadata_path)
                if row.get("target_kind") == "body":
                    try:
                        block = int(row["block"])
                        offset = int(row["offset"])
                        key = str(((row.get("target") or {}) if isinstance(row.get("target"), dict) else {}).get("address_key") or _address_key(block, offset))
                        row["target_key"] = key
                        builder.enqueue(block, offset, label=str(row.get("label") or cell.title), source="panel-cell")
                        seeded += 1
                    except (TypeError, ValueError):
                        row["target_key"] = None
                inline_cells.append(row)
        except Exception as exc:
            failures.append({"path": str(metadata_path), "reason": f"cell_parse_failed: {exc}"})
        try:
            refs = list(iter_panel_references(metadata_path))
        except Exception as exc:
            failures.append({"path": str(metadata_path), "reason": f"metadata_parse_failed: {exc}"})
            continue
        cell_refs_by_parent: dict[str, list[str]] = {}
        referenced_panels: set[str] = set()
        for cell in metadata_cells:
            ref = cell.ref
            if not ref:
                continue
            cell_refs_by_parent.setdefault(cell.panel_index, []).append(ref)
            referenced_panels.add(ref)
        roots = [
            panel_id
            for panel_id in cell_refs_by_parent
            if panel_id == "00000000" or panel_id not in referenced_panels
        ]
        if not roots and cell_refs_by_parent:
            roots = [next(iter(cell_refs_by_parent))]
        ordered_panel_ids: list[str] = []
        seen_panels: set[str] = set()

        def visit_panel(panel_id: str) -> None:
            if panel_id in seen_panels:
                return
            seen_panels.add(panel_id)
            for ref in cell_refs_by_parent.get(panel_id, []):
                if ref not in ordered_panel_ids:
                    ordered_panel_ids.append(ref)
                if ref in cell_refs_by_parent and ref not in seen_panels:
                    visit_panel(ref)

        for root_panel_id in roots:
            visit_panel(root_panel_id)
        panel_order = {panel_id: index for index, panel_id in enumerate(ordered_panel_ids)}
        refs.sort(key=lambda ref: (panel_order.get(ref.panel_index, len(panel_order)), ref.panel_index, ref.filename))
        for ref in refs:
            resolved = _resolve_panel_bin(builder.source.idx.parent, ref.filename, ref.data_type)
            if resolved is None or ref.data_type.lower() != "bin":
                continue
            try:
                panel = parse_panel_bin_file(resolved)
            except PanelBinParseError as exc:
                failures.append({"path": str(resolved), "reason": str(exc)})
                continue
            for row_obj in panel_bin_navigation_rows(ref, panel, resolved_path=resolved, elements=builder.elements):
                row = row_obj.as_dict()
                row["metadata"] = str(metadata_path)
                if row.get("target_kind") == "body":
                    body_rows_seen += 1
                    if body_rows_seen <= int(builder.args.panel_row_limit):
                        try:
                            block = int(row["block"])
                            offset = int(row["offset"])
                            row["target_key"] = str(((row.get("target") or {}) if isinstance(row.get("target"), dict) else {}).get("address_key") or _address_key(block, offset))
                            builder.enqueue(block, offset, label=str(row.get("label") or ref.title), source="panel")
                            seeded += 1
                        except (TypeError, ValueError):
                            row["target_key"] = None
                    else:
                        continue
                panel_rows.append(row)
                if len(panel_rows) >= int(builder.args.panel_row_limit) + len(inline_cells):
                    break
            if body_rows_seen >= int(builder.args.panel_row_limit):
                break
    return {
        "metadata_files": [str(path) for path in metadata_paths],
        "inline_cells": inline_cells[:500],
        "rows": panel_rows,
        "seeded_entries": seeded,
        "failures": failures,
    }


def _decode_text_component_lines(path: Path, gaiji_map: dict[str, str], limit: int) -> list[str]:
    if limit <= 0:
        return []
    reader = SsedRandomReader(path)
    read_size = min(reader.expanded_size, 128 * 1024)
    max_preview_bytes = min(reader.expanded_size, 1024 * 1024)
    while read_size <= max_preview_bytes:
        data = reader.read(0, read_size)
        tokens, _stats = decode_tokens(data, gaiji="unicode", gaiji_map=gaiji_map)
        lines = [line.strip() for line in tokens_to_text(tokens).splitlines() if line.strip()]
        if len(lines) >= limit or read_size == max_preview_bytes:
            return lines[:limit]
        read_size = min(max_preview_bytes, read_size * 2)
    return []


def _auxiliary_data(builder: _ReaderDemoBuilder) -> dict[str, Any]:
    index_rows: list[dict[str, Any]] = []
    title_streams: list[dict[str, Any]] = []
    text_streams: list[dict[str, Any]] = []
    multi_descriptors: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen_text_components: set[str] = set()
    for element in builder.elements:
        upper = element.filename.upper()
        path = find_case_insensitive(builder.source.idx.parent, element.filename)
        if path is None or not path.is_file():
            continue
        if element.type in {0x80, 0xA1}:
            leaf_rows: list[dict[str, Any]] = []

            def collect(row: dict[str, Any]) -> None:
                if row.get("kind") != "leaf":
                    return
                item = dict(row)
                body = row.get("body")
                if isinstance(body, dict):
                    try:
                        block = int(body["block"])
                        offset = int(body["offset"])
                    except (KeyError, TypeError, ValueError):
                        block = offset = -1
                    if block >= 0 and offset >= 0:
                        resolved = builder.navigation.resolve_body(block, offset)
                        if resolved.target is not None and resolved.target.is_body:
                            item["target_key"] = resolved.target.address_key
                            builder.enqueue(block, offset, label=str(row.get("target_key") or row.get("key") or upper), source=upper.lower())
                leaf_rows.append(item)

            try:
                result = scan_index_component_reader(
                    element.filename,
                    element.type,
                    path,
                    element.start,
                    gaiji="unicode",
                    gaiji_map=builder.source.gaiji_map,
                    emit_internal=False,
                    emit_row=collect,
                    row_limit=int(builder.args.index_row_limit),
                )
                index_rows.append(
                    {
                        "component": element.filename,
                        "type": f"{element.type:02x}",
                        "rows": leaf_rows[: int(builder.args.index_row_limit)],
                        "summary": {
                            "leaf_rows": result.leaf_rows,
                            "search_groups": result.search_groups,
                            "unknown_leaf_bytes": result.unknown_leaf_bytes,
                        },
                    }
                )
            except Exception as exc:
                failures.append({"component": element.filename, "reason": f"index_parse_failed: {exc}"})
        elif upper.startswith("MULTI") or element.type == 0xFF:
            try:
                reader = SsedRandomReader(path)
                descriptor = parse_multi_descriptor(
                    reader.read(0, reader.expanded_size),
                    gaiji="unicode",
                    gaiji_map=builder.source.gaiji_map,
                    parent_start_block=element.start,
                )
                multi_descriptors.append({"component": element.filename, **descriptor.as_dict()})
            except Exception as exc:
                failures.append({"component": element.filename, "reason": f"multi_parse_failed: {exc}"})
        elif upper == "KWTITLE.DIC" or (upper.startswith("MUL") and element.type == 0x0D):
            try:
                title_streams.append(
                    {
                        "component": element.filename,
                        "type": f"{element.type:02x}",
                        "lines": _decode_text_component_lines(path, builder.source.gaiji_map, 80),
                    }
                )
            except Exception as exc:
                failures.append({"component": element.filename, "reason": f"title_decode_failed: {exc}"})
        elif upper in {"RIGHT.DIC", "IDXJUMP.DIC"} or element.type in {0x02, 0x28}:
            try:
                text_streams.append(
                    {
                        "component": element.filename,
                        "type": f"{element.type:02x}",
                        "lines": _decode_text_component_lines(path, builder.source.gaiji_map, 80),
                    }
                )
                seen_text_components.add(upper)
            except Exception as exc:
                failures.append({"component": element.filename, "reason": f"text_decode_failed: {exc}"})
    for name in ("RIGHT.DIC", "IDXJUMP.DIC"):
        if name in seen_text_components:
            continue
        path = resolve_case_insensitive_path(builder.source.idx.parent, name)
        if path is None or not path.is_file():
            continue
        try:
            text_streams.append(
                {
                    "component": path.name,
                    "type": "loose",
                    "lines": _decode_text_component_lines(path, builder.source.gaiji_map, 80),
                }
            )
            seen_text_components.add(name)
        except Exception as exc:
            failures.append({"component": path.name, "reason": f"text_decode_failed: {exc}"})
    return {
        "index_rows": index_rows,
        "title_streams": title_streams,
        "text_streams": text_streams,
        "multi_descriptors": multi_descriptors,
        "failures": failures,
    }


class _HanreiLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs}
        if tag_lower in {"frame", "iframe"}:
            src = attrs_dict.get("src")
            if src:
                label = attrs_dict.get("name") or attrs_dict.get("title") or src
                self.links.append({"href": src, "label": label})
            return
        if tag_lower != "a":
            return
        href = attrs_dict.get("href")
        if href:
            self._href = href
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        label = " ".join("".join(self._parts).split())
        self.links.append({"href": self._href, "label": label or self._href})
        self._href = None
        self._parts = []


class _HanreiSitemapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs}
        if tag_lower == "object" and (attrs_dict.get("type") or "").lower() == "text/sitemap":
            self._current = {}
            return
        if tag_lower != "param" or self._current is None:
            return
        name = (attrs_dict.get("name") or "").lower()
        value = attrs_dict.get("value") or ""
        if name in {"name", "local"}:
            self._current[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "object" or self._current is None:
            return
        local = self._current.get("local", "").strip()
        label = " ".join(self._current.get("name", "").split()) or local
        if local:
            self.links.append({"href": local, "label": label})
        self._current = None


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp932", "shift_jis", "euc_jp", "latin1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _hanrei_local_href(rel_base: str, href: str) -> str | None:
    href = html.unescape(href).replace("\\", "/").strip()
    if not href:
        return None
    lower = href.lower()
    if lower.startswith(("http:", "https:", "mailto:", "javascript:")):
        return None
    target, sep, fragment = href.partition("#")
    normalized = posixpath.normpath(target.lstrip("/")) if target else ""
    if normalized == ".":
        normalized = ""
    if normalized.startswith("../") or normalized == "..":
        return None
    joined = posixpath.normpath(f"{rel_base.rstrip('/')}/{normalized}") if normalized else rel_base.rstrip("/")
    if fragment:
        joined = f"{joined}{sep}{fragment}"
    return joined


def _hanrei_links_from_file(path: Path, rel_base: str, limit: int) -> list[dict[str, str]]:
    text = _read_text_best_effort(path)
    sitemap_parser = _HanreiSitemapParser()
    sitemap_parser.feed(text)
    parser = _HanreiLinkParser()
    parser.feed(text)
    rows: list[dict[str, str]] = []
    for row in [*sitemap_parser.links, *parser.links]:
        href = _hanrei_local_href(rel_base, row["href"])
        if href is None:
            continue
        rows.append({"label": row["label"], "href": href})
        if len(rows) >= limit:
            break
    return rows


def _append_hanrei_links(links: list[dict[str, str]], incoming: list[dict[str, str]], limit: int) -> None:
    seen = {row["href"] for row in links}
    for row in incoming:
        href = row["href"]
        if href in seen:
            continue
        links.append(row)
        seen.add(href)
        if len(links) >= limit:
            break


def _hanrei_chm_navigation_files(extracted_dir: Path) -> list[Path]:
    files: list[Path] = []
    for suffix in (".hhc", ".hhk"):
        files.extend(path for path in extracted_dir.rglob(f"*{suffix}") if path.is_file())
    if files:
        return sorted(files, key=lambda path: path.relative_to(extracted_dir).as_posix().lower())
    fallbacks = [extracted_dir / "index.html", extracted_dir / "Source/top.html"]
    return [path for path in fallbacks if path.is_file()]


def _copy_hanrei_tree(source: Path, target: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name.startswith("._") or name.endswith(":Zone.Identifier") or name == "__MACOSX"}

    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)


def _hanrei_html_files(root: Path, limit: int) -> list[Path]:
    preferred = []
    for name in ("menu.html", "menu.htm", "index.html", "index.htm", "top.html", "top.htm", "contents/hanrei.html"):
        path = resolve_case_insensitive_path(root, name)
        if path is not None and path.is_file() and not is_metadata_noise_path(path):
            preferred.append(path)
    seen = set(preferred)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        if len(preferred) >= limit:
            break
        if path in seen or not path.is_file() or is_metadata_noise_path(path):
            continue
        if path.suffix.casefold() in {".html", ".htm"}:
            preferred.append(path)
            seen.add(path)
    return preferred[:limit]


def _mac_hanrei_bundle_paths(root: Path) -> list[Path]:
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    bundles = [
        child
        for child in children
        if child.is_dir() and child.name.casefold().endswith("_help.localized") and not is_metadata_noise_path(child)
    ]
    return sorted(bundles, key=lambda path: path.name.casefold())


def _hanrei_data(builder: _ReaderDemoBuilder) -> dict[str, Any]:
    root = builder.source.idx.parent
    out = builder.out_dir / "hanrei"
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    limit = int(builder.args.hanrei_page_limit)
    hanrei_dir = resolve_case_insensitive_path(root, "HANREI")
    if hanrei_dir is not None and hanrei_dir.is_dir():
        target = out / "HANREI"
        _copy_hanrei_tree(hanrei_dir, target)
        candidates = [target / "menu.html", target / "index.html", target / "top.html"]
        links: list[dict[str, str]] = []
        for candidate in candidates:
            if candidate.is_file():
                _append_hanrei_links(links, _hanrei_links_from_file(candidate, "hanrei/HANREI", limit - len(links)), limit)
            if len(links) >= limit:
                break
        if not links:
            links = [
                {"label": path.relative_to(target).as_posix(), "href": f"hanrei/HANREI/{path.relative_to(target).as_posix()}"}
                for path in sorted(target.rglob("*.html"))[:limit]
            ]
        entry = links[0]["href"] if links else "hanrei/HANREI/index.html"
        rows.append({"kind": "html_directory", "source": "HANREI", "entry": entry, "links": links[:limit]})
    mac_root_hanrei = resolve_case_insensitive_path(root, "hanrei.html")
    mac_bundles = _mac_hanrei_bundle_paths(root)
    if mac_root_hanrei is not None and mac_root_hanrei.is_file():
        out.mkdir(parents=True, exist_ok=True)
        copied_root = out / "hanrei.html"
        shutil.copy2(mac_root_hanrei, copied_root)
        links: list[dict[str, str]] = []
        _append_hanrei_links(links, _hanrei_links_from_file(copied_root, "hanrei", limit), limit)
        rows.append(
            {
                "kind": "mac_html_frameset",
                "source": mac_root_hanrei.name,
                "entry": "hanrei/hanrei.html",
                "links": links[:limit],
            }
        )
    for bundle in mac_bundles:
        target = out / bundle.name
        _copy_hanrei_tree(bundle, target)
        rel_base = f"hanrei/{bundle.name}"
        links: list[dict[str, str]] = []
        for candidate in _hanrei_html_files(target, limit):
            candidate_base = rel_base
            if candidate.parent != target:
                candidate_base = f"{rel_base}/{candidate.parent.relative_to(target).as_posix()}"
            _append_hanrei_links(links, _hanrei_links_from_file(candidate, candidate_base, limit - len(links)), limit)
            if len(links) >= limit:
                break
        if not links:
            links = [
                {"label": path.relative_to(target).as_posix(), "href": f"{rel_base}/{path.relative_to(target).as_posix()}"}
                for path in _hanrei_html_files(target, limit)
            ]
        entry = links[0]["href"] if links else f"{rel_base}/index.htm"
        rows.append({"kind": "mac_help_localized", "source": bundle.name, "entry": entry, "links": links[:limit]})
    chm_path = resolve_case_insensitive_path(root, "HANREI.chm")
    if chm_path is not None and chm_path.is_file():
        out.mkdir(parents=True, exist_ok=True)
        copied = out / "HANREI.chm"
        shutil.copy2(chm_path, copied)
        links: list[dict[str, str]] = []
        extracted_dir = out / "chm"
        seven_zip = shutil.which("7z") or shutil.which("7zz")
        if seven_zip:
            try:
                subprocess.run(
                    [seven_zip, "x", "-y", f"-o{extracted_dir}", str(chm_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                for candidate in _hanrei_chm_navigation_files(extracted_dir):
                    _append_hanrei_links(links, _hanrei_links_from_file(candidate, "hanrei/chm", limit - len(links)), limit)
                    if len(links) >= limit:
                        break
                if not links:
                    links = [
                        {"label": path.relative_to(extracted_dir).as_posix(), "href": f"hanrei/chm/{path.relative_to(extracted_dir).as_posix()}"}
                        for path in sorted(extracted_dir.rglob("*.html"))[:limit]
                    ]
            except Exception as exc:
                failures.append({"source": "HANREI.chm", "reason": f"chm_extract_failed: {exc}"})
        entry = links[0]["href"] if links else "hanrei/HANREI.chm"
        rows.append({"kind": "chm", "source": "HANREI.chm", "entry": entry, "links": links[:limit]})
    return {"sources": rows, "failures": failures}


def _ccaltstr_data(builder: _ReaderDemoBuilder) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for name in ("CCALTSTR.HA", "CCALTSTR.FU"):
        path = resolve_case_insensitive_path(builder.source.idx.parent, name)
        if path is None or not path.is_file():
            continue
        try:
            table = parse_ccaltstr_file(path)
        except Exception as exc:
            failures.append({"component": name, "reason": str(exc)})
            continue
        records = [
            {"code": record.code_hex, "value": record.value}
            for record in table.records
            if record.value
        ][: int(builder.args.ccaltstr_row_limit)]
        tables.append(
            {
                "component": path.name,
                "kind": table.kind,
                "start_code": table.start_code_hex,
                "records_total": table.record_count,
                "records_with_values": sum(1 for record in table.records if record.value),
                "records": records,
            }
        )
    return {"tables": tables, "failures": failures}


def _entry_templates(entries: dict[str, _RenderedEntry]) -> str:
    parts = ['<div id="entry-store" hidden>']
    for key, entry in sorted(entries.items()):
        parts.append(f'<template data-entry-key="{html.escape(key, quote=True)}">')
        parts.append(entry.html)
        parts.append("</template>")
    parts.append("</div>")
    return "\n".join(parts)


def _button(label: str, key: str | None, css: str = "") -> str:
    escaped = html.escape(label or "(blank)")
    if not key:
        return f'<button class="{css}" disabled>{escaped}</button>'
    return f'<button class="{css}" data-entry-key="{html.escape(key, quote=True)}">{escaped}</button>'


def _navigation_button(label: str, target_key: str | None, row_key: str | None, css: str = "") -> str:
    escaped = html.escape(label or "(blank)")
    row_attr = f' data-nav-row-key="{html.escape(row_key, quote=True)}"' if row_key else ""
    if not target_key:
        return f'<button class="{css}"{row_attr} disabled>{escaped}</button>'
    return (
        f'<button class="{css}"{row_attr} '
        f'data-nav-target-key="{html.escape(target_key, quote=True)}">{escaped}</button>'
    )


def _menu_row_button(row: dict[str, Any], entries: dict[str, _RenderedEntry], css: str = "menu-link") -> str:
    key = row.get("target_key")
    nav_target_key = row.get("navigation_target_key")
    nav_preview_key = row.get("navigation_preview_key")
    nav_row_key = row.get("navigation_key")
    label = " / ".join(row.get("path") or []) or row.get("text") or f"line {row.get('line_index')}"
    if key:
        return _button(str(label), str(key) if key in entries else None, css)
    if nav_preview_key:
        escaped = html.escape(str(label) or "(blank)")
        row_attr = f' data-nav-row-key="{html.escape(str(nav_row_key), quote=True)}"' if nav_row_key else ""
        target_attr = f' data-nav-target-key="{html.escape(str(nav_target_key), quote=True)}"' if nav_target_key else ""
        return (
            f'<button class="{css}"{row_attr}{target_attr} '
            f'data-menu-preview-target="{html.escape(str(nav_preview_key), quote=True)}">{escaped}</button>'
        )
    return _navigation_button(
        str(label),
        str(nav_target_key) if nav_target_key else None,
        str(nav_row_key) if nav_row_key else None,
        css,
    )


def _menu_html(menu: dict[str, Any], entries: dict[str, _RenderedEntry]) -> str:
    parts = ["<section><h2>MENU.DIC</h2>"]
    components = menu.get("components") or []
    if not components:
        parts.append('<p class="muted">No MENU.DIC component declared.</p>')
    for component in components:
        status = component.get("status")
        parts.append(f'<h3>{html.escape(str(component.get("component", "MENU.DIC")))}: {html.escape(str(status))}</h3>')
        if status == "missing_file":
            parts.append('<p class="muted">Catalog declares MENU.DIC, but the file is absent from this package copy.</p>')
    rows = menu.get("rows") or []
    if rows:
        parts.append('<div class="nav-list">')
        for row in rows:
            parts.append(_menu_row_button(row, entries))
        parts.append("</div>")
    previews = menu.get("previews") or []
    if previews:
        parts.append('<h3>MENU target previews</h3><p id="menu-status" class="muted">Choose a MENU item.</p>')
        for preview in previews:
            preview_key = str(preview.get("navigation_key") or "")
            title = str(preview.get("title") or preview_key)
            parts.append(
                f'<div class="menu-preview" data-menu-preview-id="{html.escape(preview_key, quote=True)}">'
            )
            parts.append(f"<h4>{html.escape(title)}</h4><div class=\"nav-list\">")
            for row in preview.get("rows") or []:
                parts.append(_menu_row_button(row, entries, "menu-preview-link"))
            parts.append("</div></div>")
    parts.append("</section>")
    return "\n".join(parts)


def _panels_html(panels: dict[str, Any], entries: dict[str, _RenderedEntry]) -> str:
    parts = ["<section><h2>Panels</h2>"]
    metadata = panels.get("metadata_files") or []
    if not metadata:
        parts.append('<p class="muted">No Panels.xml/Panels.plist metadata found.</p>')
    else:
        parts.append("<p>Metadata: " + ", ".join(html.escape(Path(path).name) for path in metadata) + "</p>")
    parts.append('<p id="panel-status" class="muted"></p>')
    cells = panels.get("inline_cells") or []
    if cells:
        refs = {str(cell.get("ref")) for cell in cells if cell.get("ref")}
        parents: dict[str, list[dict[str, Any]]] = {}
        for cell in cells[:500]:
            parents.setdefault(str(cell.get("panel_index") or "panel"), []).append(cell)
        root_parents = {panel_id for panel_id in parents if panel_id == "00000000" or panel_id not in refs}
        if not root_parents and parents:
            root_parents = {next(iter(parents))}
        parts.append("<h3>Panel cells</h3>")
        for parent_id, group_cells in parents.items():
            root_attr = ' data-panel-root="1"' if parent_id in root_parents else ""
            parts.append(
                f'<div class="panel-cell-group" data-cell-panel-id="{html.escape(parent_id, quote=True)}"{root_attr}>'
            )
            parts.append(f'<h4>Panel {html.escape(parent_id)}</h4><div class="nav-list">')
            for cell in group_cells:
                label = cell.get("label") or cell.get("panel_title") or cell.get("ref") or "(blank)"
                key = cell.get("target_key")
                ref = cell.get("ref")
                if key:
                    parts.append(_button(str(label), str(key) if key in entries else None, "panel-cell"))
                elif ref:
                    parts.append(
                        f'<button class="panel-cell" data-panel-ref="{html.escape(str(ref), quote=True)}" '
                        f'data-panel-parent="{html.escape(parent_id, quote=True)}">{html.escape(str(label))}</button>'
                    )
                else:
                    parts.append(_button(str(label), str(key) if key in entries else None, "panel-cell"))
            parts.append("</div></div>")
    rows = panels.get("rows") or []
    if rows:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get("panel_index") or "panel"), []).append(row)
        parts.append("<h3>Panel rows</h3>")
        for panel_id, panel_rows in list(grouped.items())[:80]:
            title = panel_rows[0].get("panel_title") or panel_id
            parts.append(f'<div class="panel-group" data-panel-id="{html.escape(panel_id, quote=True)}">')
            parts.append(f"<h4>{html.escape(str(title))}</h4><div class=\"nav-list\">")
            for row in panel_rows[:80]:
                key = row.get("target_key")
                label = row.get("label") or row.get("panel_title") or row.get("filename") or "(blank)"
                parts.append(_button(str(label), str(key) if key in entries else None, "panel-row"))
            parts.append("</div></div>")
    parts.append("</section>")
    return "\n".join(parts)


def _entries_html(entries: dict[str, _RenderedEntry]) -> str:
    parts = ["<section><h2>Rendered entries</h2><div class=\"nav-list\">"]
    for key, entry in sorted(entries.items()):
        parts.append(_button(entry.title, key, "entry-shortcut"))
    parts.append("</div></section>")
    return "\n".join(parts)


def _auxiliary_html(auxiliary: dict[str, Any], entries: dict[str, _RenderedEntry]) -> str:
    parts = ["<section><h2>Auxiliary indexes/resources</h2>"]
    for component in auxiliary.get("index_rows") or []:
        rows = component.get("rows") or []
        parts.append(
            f'<h3>{html.escape(str(component.get("component")))} '
            f'(type {html.escape(str(component.get("type")))})</h3>'
        )
        if component.get("summary"):
            summary = component["summary"]
            parts.append(
                '<p class="muted">'
                + ", ".join(f"{html.escape(str(k))}={html.escape(str(v))}" for k, v in summary.items())
                + "</p>"
            )
        parts.append('<div class="nav-list">')
        for row in rows:
            label = str(row.get("target_key") or row.get("key") or "(blank)")
            key = row.get("target_key")
            parts.append(_button(label, str(key) if key in entries else None, "aux-index-link"))
        parts.append("</div>")
    for component in auxiliary.get("multi_descriptors") or []:
        parts.append(f'<h3>{html.escape(str(component.get("component")))} selector</h3><div class="nav-list">')
        for record in component.get("records") or []:
            label = record.get("label") or f"record {record.get('index')}"
            refs = ", ".join(ref.get("component_type", "") for ref in record.get("refs") or [])
            parts.append(f'<button class="aux-static" disabled>{html.escape(str(label))} [{html.escape(refs)}]</button>')
        parts.append("</div>")
    for component in auxiliary.get("title_streams") or []:
        parts.append(f'<h3>{html.escape(str(component.get("component")))} titles</h3><div class="nav-list">')
        for line in component.get("lines") or []:
            parts.append(f'<button class="aux-static" disabled>{html.escape(str(line))}</button>')
        parts.append("</div>")
    for component in auxiliary.get("text_streams") or []:
        parts.append(f'<h3>{html.escape(str(component.get("component")))} text</h3>')
        parts.append("<pre>" + html.escape("\n".join(component.get("lines") or [])) + "</pre>")
    failures = auxiliary.get("failures") or []
    if failures:
        parts.append("<h3>Auxiliary parse failures</h3><pre>" + html.escape(json.dumps(failures, ensure_ascii=False, indent=2)) + "</pre>")
    if len(parts) == 1:
        parts.append('<p class="muted">No KW/MUL/RIGHT auxiliary components selected for this package.</p>')
    parts.append("</section>")
    return "\n".join(parts)


def _hanrei_html(hanrei: dict[str, Any]) -> str:
    parts = ["<section><h2>HANREI / 凡例</h2>"]
    sources = hanrei.get("sources") or []
    initial_src = ""
    if not sources:
        parts.append('<p class="muted">No HANREI directory or HANREI.chm found.</p>')
    for source in sources:
        parts.append(f'<h3>{html.escape(str(source.get("source")))}: {html.escape(str(source.get("kind")))}</h3>')
        links = source.get("links") or []
        if not initial_src:
            if links:
                initial_src = str(links[0].get("href") or "")
            elif source.get("entry"):
                initial_src = str(source.get("entry"))
        if links:
            parts.append('<div class="nav-list">')
            for row in links:
                href = str(row.get("href") or "")
                label = str(row.get("label") or href)
                parts.append(
                    f'<button class="hanrei-link" data-hanrei-href="{html.escape(href, quote=True)}">'
                    f"{html.escape(label)}</button>"
                )
            parts.append("</div>")
        elif source.get("entry"):
            href = str(source.get("entry"))
            parts.append(f'<p><a href="{html.escape(href, quote=True)}">{html.escape(href)}</a></p>')
    if sources:
        src_attr = f' src="{html.escape(initial_src, quote=True)}"' if initial_src else ""
        parts.append(f'<iframe id="hanrei-frame" title="HANREI"{src_attr} style="width:100%;height:360px;border:1px solid #ccc;background:#fff"></iframe>')
    failures = hanrei.get("failures") or []
    if failures:
        parts.append("<pre>" + html.escape(json.dumps(failures, ensure_ascii=False, indent=2)) + "</pre>")
    parts.append("</section>")
    return "\n".join(parts)


def _ccaltstr_html(ccaltstr: dict[str, Any]) -> str:
    parts = ["<section><h2>CCALTSTR alternate strings</h2>"]
    tables = ccaltstr.get("tables") or []
    if not tables:
        parts.append('<p class="muted">No CCALTSTR.HA/FU table found.</p>')
    for table in tables:
        parts.append(
            f'<h3>{html.escape(str(table.get("component")))} '
            f'{html.escape(str(table.get("kind")))}</h3>'
        )
        parts.append(
            f'<p class="muted">start={html.escape(str(table.get("start_code")))} '
            f'total={html.escape(str(table.get("records_total")))} '
            f'nonempty={html.escape(str(table.get("records_with_values")))}</p>'
        )
        parts.append('<div class="nav-list">')
        for record in table.get("records") or []:
            parts.append(
                f'<button class="aux-static" disabled>{html.escape(str(record.get("code")))} '
                f'→ {html.escape(str(record.get("value")))}</button>'
            )
        parts.append("</div>")
    failures = ccaltstr.get("failures") or []
    if failures:
        parts.append("<pre>" + html.escape(json.dumps(failures, ensure_ascii=False, indent=2)) + "</pre>")
    parts.append("</section>")
    return "\n".join(parts)


def _write_demo_page(
    source: DictionarySource,
    out_dir: Path,
    *,
    builder: _ReaderDemoBuilder,
    query_rows: list[dict[str, Any]],
    menu: dict[str, Any],
    panels: dict[str, Any],
    auxiliary: dict[str, Any],
    hanrei: dict[str, Any],
    ccaltstr: dict[str, Any],
) -> None:
    stylesheet = f'<link rel="stylesheet" href="{html.escape(builder.stylesheet, quote=True)}">' if builder.stylesheet else ""
    entries_json = json.dumps([entry.as_manifest() for entry in builder.entries.values()], ensure_ascii=False, sort_keys=True)
    entries_json_script = entries_json.replace("<", "\\u003c")
    summary_bits = {
        "entries": len(builder.entries),
        "audio_files": len(builder.audio_files),
        "menu_rows": len(menu.get("rows") or []),
        "menu_previews": len(menu.get("previews") or []),
        "panel_rows": len(panels.get("rows") or []),
        "panel_cells": len(panels.get("inline_cells") or []),
        "aux_indexes": len(auxiliary.get("index_rows") or []),
        "hanrei": len(hanrei.get("sources") or []),
        "ccaltstr": len(ccaltstr.get("tables") or []),
        "failures": (
            len(builder.failures)
            + len(panels.get("failures") or [])
            + len(auxiliary.get("failures") or [])
            + len(hanrei.get("failures") or [])
            + len(ccaltstr.get("failures") or [])
        ),
    }
    html_text = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>{html.escape(source.dict_id)} reader demo</title>
{stylesheet}
<style>
{HC_RENDER_BASE_CSS}
{HC_RENDER_FINAL_CSS}
body {{ margin: 0; font-family: system-ui, sans-serif; background: #f7f7f7; color: #1a1a1a; }}
.app {{ display: grid; grid-template-columns: minmax(280px, 34vw) 1fr; min-height: 100vh; }}
.side {{ overflow: auto; max-height: 100vh; padding: 16px; border-right: 1px solid #d0d0d0; background: #fff; }}
.main {{ overflow: auto; max-height: 100vh; padding: 22px; }}
h1 {{ margin: 0 0 10px; font-size: 22px; }}
h2 {{ margin: 22px 0 8px; font-size: 16px; border-bottom: 1px solid #ddd; }}
h3 {{ margin: 14px 0 6px; font-size: 14px; }}
h4 {{ margin: 12px 0 4px; font-size: 13px; }}
.muted {{ color: #666; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 14px; }}
.stats span {{ border: 1px solid #ccc; border-radius: 4px; padding: 3px 6px; background: #fafafa; font-size: 12px; }}
.nav-list {{ display: flex; flex-wrap: wrap; gap: 5px; }}
button {{ border: 1px solid #bbb; border-radius: 4px; padding: 4px 7px; background: #fff; cursor: pointer; text-align: left; }}
button:hover {{ border-color: #777; }}
button:disabled {{ color: #999; cursor: not-allowed; background: #f1f1f1; }}
button.is-active {{ border-color: #5da24c; background: #edf7eb; }}
button.is-nav-target {{ border-color: #5da24c; background: #edf7eb; }}
.entry-box {{ background: #fff; border: 1px solid #d0d0d0; padding: 18px; min-height: 60vh; }}
.entry-meta {{ color: #666; font-size: 13px; margin-bottom: 10px; }}
#audio-player {{ width: min(520px, 100%); margin: 12px 0; }}
.panel-cell-group, .panel-group, .menu-preview {{ display: none; }}
.panel-cell-group.is-active, .panel-group.is-active, .menu-preview.is-active {{ display: block; }}
.panel-group, .menu-preview {{ border-left: 3px solid #ddd; padding-left: 8px; margin: 8px 0; }}
.panel-group.is-active, .menu-preview.is-active {{ border-left-color: #5da24c; }}
</style>
</head>
<body>
<div class="app">
<aside class="side">
<h1>{html.escape(source.dict_id)} reader demo</h1>
<p>{html.escape(source.title)}</p>
<div class="stats">{"".join(f"<span>{html.escape(k)}={v}</span>" for k, v in summary_bits.items())}</div>
<audio id="audio-player" controls preload="metadata"></audio>
{_entries_html(builder.entries)}
{_menu_html(menu, builder.entries)}
{_panels_html(panels, builder.entries)}
{_auxiliary_html(auxiliary, builder.entries)}
{_hanrei_html(hanrei)}
{_ccaltstr_html(ccaltstr)}
</aside>
<main class="main">
<div class="entry-box">
<div id="entry-meta" class="entry-meta"></div>
<div id="entry-view">Choose an entry, MENU row, Panel row, or in-entry link.</div>
</div>
</main>
</div>
{_entry_templates(builder.entries)}
<script type="application/json" id="entry-manifest">{entries_json_script}</script>
<script>
const entryManifest = JSON.parse(document.getElementById('entry-manifest').textContent);
const entryTitles = Object.fromEntries(entryManifest.map(row => [row.key, row.title]));
function normalizeAddress(block, offset) {{
  const base = /[a-f]/i.test(String(block)) || /[a-f]/i.test(String(offset)) ? 16 : 10;
  const b = String(parseInt(block, base)).padStart(8, '0');
  const o = String(parseInt(offset, base)).padStart(4, '0');
  return `${{b}}:${{o}}`;
}}
function keyFromHref(href) {{
  const m = href.match(/^lvaddr:\\/\\/([0-9A-Fa-f]+)\\/([0-9A-Fa-f]+)$/);
  if (!m) return null;
  return normalizeAddress(m[1], m[2]);
}}
function bindEntryLinks() {{
  document.querySelectorAll('#entry-view a[href^="lvaddr://"]').forEach(a => {{
    a.addEventListener('click', ev => {{
      const key = keyFromHref(a.getAttribute('href') || '');
      if (key && loadEntry(key)) ev.preventDefault();
    }});
  }});
  document.querySelectorAll('#entry-view a[data-lv-audio-file]').forEach(a => {{
    a.addEventListener('click', ev => {{
      ev.preventDefault();
      const src = a.getAttribute('data-lv-audio-file');
      const player = document.getElementById('audio-player');
      player.src = src;
      player.play();
    }});
  }});
}}
function loadEntry(key) {{
  const tpl = document.querySelector(`template[data-entry-key="${{CSS.escape(key)}}"]`);
  if (!tpl) {{
    document.getElementById('entry-meta').textContent = `Address ${{key}} is not embedded in this sample page.`;
    return false;
  }}
  document.getElementById('entry-view').innerHTML = tpl.innerHTML;
  document.getElementById('entry-meta').textContent = `${{key}} — ${{entryTitles[key] || ''}}`;
  bindEntryLinks();
  return true;
}}
function activateMenuPreview(key) {{
  const targetPreview = document.querySelector(`.menu-preview[data-menu-preview-id="${{CSS.escape(key)}}"]`);
  document.querySelectorAll('.menu-preview').forEach(group => {{
    group.classList.toggle('is-active', group === targetPreview);
  }});
  const status = document.getElementById('menu-status');
  if (targetPreview) {{
    if (status) status.textContent = targetPreview.querySelector('h4')?.textContent || key;
    targetPreview.scrollIntoView({{block: 'nearest'}});
    return true;
  }}
  if (status) status.textContent = `MENU target ${{key}} is referenced, but no preview is embedded.`;
  return false;
}}
function setPanelStatus(text) {{
  const status = document.getElementById('panel-status');
  if (status) status.textContent = text || '';
}}
function activatePanel(ref) {{
  const targetCellGroup = document.querySelector(`.panel-cell-group[data-cell-panel-id="${{CSS.escape(ref)}}"]`);
  const targetRowGroup = document.querySelector(`.panel-group[data-panel-id="${{CSS.escape(ref)}}"]`);
  document.querySelectorAll('.panel-cell-group').forEach(group => {{
    group.classList.toggle('is-active', group.getAttribute('data-panel-root') === '1' || group === targetCellGroup);
  }});
  document.querySelectorAll('.panel-group').forEach(group => {{
    group.classList.toggle('is-active', group === targetRowGroup);
  }});
  document.querySelectorAll('[data-panel-ref]').forEach(btn => {{
    btn.classList.toggle('is-active', btn.getAttribute('data-panel-ref') === ref);
  }});
  if (targetRowGroup) {{
    const title = targetRowGroup.querySelector('h4')?.textContent || ref;
    setPanelStatus(`Showing rows for ${{title}}`);
    targetRowGroup.scrollIntoView({{block: 'nearest'}});
    return true;
  }}
  if (targetCellGroup) {{
    const title = targetCellGroup.querySelector('h4')?.textContent || ref;
    setPanelStatus(`Showing child cells for ${{title}}`);
    targetCellGroup.scrollIntoView({{block: 'nearest'}});
    return true;
  }}
  setPanelStatus(`Panel ${{ref}} is referenced, but no decoded rows/cells are embedded in this sample.`);
  return false;
}}
function activateInitialPanel() {{
  const firstRoot = document.querySelector('.panel-cell-group[data-panel-root="1"]');
  if (firstRoot) firstRoot.classList.add('is-active');
  const firstRows = document.querySelector('.panel-group');
  if (!firstRoot && firstRows) firstRows.classList.add('is-active');
  setPanelStatus(firstRoot ? 'Choose a Panel cell.' : firstRows ? 'Showing first decoded Panel row group.' : '');
}}
document.querySelectorAll('[data-entry-key]').forEach(btn => {{
  btn.addEventListener('click', () => loadEntry(btn.getAttribute('data-entry-key')));
}});
document.querySelectorAll('[data-nav-target-key]').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const key = btn.getAttribute('data-nav-target-key');
    const target = key ? document.querySelector(`[data-nav-row-key="${{CSS.escape(key)}}"]`) : null;
    document.querySelectorAll('[data-nav-row-key]').forEach(row => row.classList.remove('is-nav-target'));
    if (target) {{
      target.classList.add('is-nav-target');
      target.scrollIntoView({{block: 'nearest'}});
    }}
  }});
}});
document.querySelectorAll('[data-menu-preview-target]').forEach(btn => {{
  btn.addEventListener('click', () => activateMenuPreview(btn.getAttribute('data-menu-preview-target')));
}});
document.querySelectorAll('[data-panel-ref]').forEach(btn => {{
  btn.addEventListener('click', () => activatePanel(btn.getAttribute('data-panel-ref')));
}});
document.querySelectorAll('[data-hanrei-href]').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const frame = document.getElementById('hanrei-frame');
    if (frame) frame.src = btn.getAttribute('data-hanrei-href') || '';
  }});
}});
activateInitialPanel();
if (entryManifest.length) loadEntry(entryManifest[0].key);
</script>
</body>
</html>
"""
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")


def generate_reader_demo_for_source(source: DictionarySource, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    dict_out = out_dir / source.dict_id
    dict_out.mkdir(parents=True, exist_ok=True)
    builder = _ReaderDemoBuilder(source, dict_out, args)
    query_rows = _query_seeds(builder, list(args.query or []))
    menu = _menu_data(builder)
    panels = _panel_data(builder)
    auxiliary = _auxiliary_data(builder)
    hanrei = _hanrei_data(builder)
    ccaltstr = _ccaltstr_data(builder)
    builder.render_queued()
    _write_demo_page(
        source,
        dict_out,
        builder=builder,
        query_rows=query_rows,
        menu=menu,
        panels=panels,
        auxiliary=auxiliary,
        hanrei=hanrei,
        ccaltstr=ccaltstr,
    )
    summary = {
        "schema": "logovista-reader-demo-summary-v1",
        "dict_id": source.dict_id,
        "dict_title": source.title,
        "html_path": str(dict_out / "index.html"),
        "queries": query_rows,
        "menu": {
            "components": menu.get("components", []),
            "rows": len(menu.get("rows") or []),
            "previews": len(menu.get("previews") or []),
            "seeded_entries": menu.get("seeded_entries", 0),
        },
        "panels": {
            "metadata_files": panels.get("metadata_files", []),
            "inline_cells": len(panels.get("inline_cells") or []),
            "rows": len(panels.get("rows") or []),
            "seeded_entries": panels.get("seeded_entries", 0),
            "failures": panels.get("failures", []),
        },
        "auxiliary": {
            "index_components": len(auxiliary.get("index_rows") or []),
            "title_streams": len(auxiliary.get("title_streams") or []),
            "text_streams": len(auxiliary.get("text_streams") or []),
            "multi_descriptors": len(auxiliary.get("multi_descriptors") or []),
            "failures": auxiliary.get("failures", []),
        },
        "hanrei": {
            "sources": len(hanrei.get("sources") or []),
            "failures": hanrei.get("failures", []),
        },
        "ccaltstr": {
            "tables": len(ccaltstr.get("tables") or []),
            "failures": ccaltstr.get("failures", []),
        },
        "entries": [entry.as_manifest() for entry in builder.entries.values()],
        "entry_count": len(builder.entries),
        "audio_files": dict(sorted(builder.audio_files.items())),
        "audio_file_count": len(builder.audio_files),
        "failures": builder.failures,
        "renderer_code": builder.renderer_code,
        "copied_assets": builder.copied_assets,
    }
    (dict_out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def generate_reader_demo(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources = discover_dictionaries(
        [args.root],
        jobs=getattr(args, "jobs", 1),
        dict_ids=args.dict,
        include_gaiji=True,
        include_images=True,
    )
    if not sources:
        raise ValueError("reader-demo: no SSED dictionaries found")
    if len(sources) > 1 and not args.dict:
        raise ValueError("reader-demo: multiple dictionaries found; pass --dict")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = [generate_reader_demo_for_source(source, args.out_dir, args) for source in sources]
    (args.out_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows
