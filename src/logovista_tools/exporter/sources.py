"""LogoVista source adapters for the exporter IR."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import mimetypes
import re
from urllib.parse import unquote, urlparse
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from ..colscr import decode_bcd_decimal, find_colscr_path, parse_media_pointer, read_colscr_record
from ..entries import (
    Break,
    EndTag,
    Image,
    LinkEnd,
    LinkStart,
    Media,
    Section,
    StartTag,
    Text,
    decode_tokens,
    discover_dictionaries,
    extract_heading,
    iter_entry_slices_reader,
    tokens_to_text,
)
from ..gaiji import (
    candidate_gaiji_paths,
    file_identity,
    ga16_preferred_code_for_index,
    ga16_section_for_path,
    is_bitmap_gaiji_resource_name,
    load_gaiji_profile,
    parse_ga16_resource,
    parse_uni_resource,
)
from ..indexes import INDEX_TYPES, scan_index_component_reader
from ..lved import (
    classify_lved_payload,
    decrypt_lved_sqlcipher4_to_path,
    discover_lved_key_file,
    lved_payload_title,
    read_lved_key_file,
)
from ..lvcrypto import decrypt_logofont_cipher_file_to_path
from ..menus import MENU_LIKE_NAMES, MENU_LIKE_TYPES, parse_menu_stream, resolve_menu_record_destinations
from ..pcmdata import (
    PCMDATA_DIRECTORY_BYTES,
    detect_shared_wave_stream,
    find_pcmdata_path,
    parse_pcm_pointer,
    parse_pcmdata_record,
    pointer_relative_range,
    wave_chunks_for_record,
)
from ..rendererdb import (
    iter_honmon_id_records,
    parse_decimal_int,
    quote_identifier,
    t_contents_columns,
    table_exists,
)
from ..ssed import BLOCK_SIZE, SsedRandomReader, find_case_insensitive, is_metadata_noise_path, parse_ssedinfo, resolve_case_insensitive_path
from ..resources import candidate_package_roots, load_image_resource_profile, relative_image_source
from ..windows import discover_renderer_sidecars, load_exinfo_for_idx
from .html_flatten import html_to_export_body
from .model import (
    ExportBody,
    ExportBodyEvent,
    ExportBook,
    ExportDiagnostic,
    ExportEntry,
    ExportGaiji,
    ExportIndex,
    ExportNavigation,
    ExportResource,
    SourceFamily,
)


@dataclass(frozen=True)
class ExportSourceOptions:
    source_family: SourceFamily = SourceFamily.AUTO
    limit: int | None = None
    progress: Callable[[str], None] | None = field(default=None, compare=False, repr=False)
    progress_every: int = 1000


def _progress(options: ExportSourceOptions, message: str) -> None:
    if options.progress is not None:
        options.progress(message)


def _progress_interval(options: ExportSourceOptions) -> int:
    return max(1, int(options.progress_every or 1000))


_IMAGE_MIME_BY_SUFFIX = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

_ENTRY_HTML_RE = re.compile(r"(?:^|/)entry_(?P<id>\d+)\.html(?:$|[#?])", re.IGNORECASE)
_LVED_DATAID_RE = re.compile(r"^lved\.dataid(?:\.result)?:(?P<id>\d+)", re.IGNORECASE)
_LVADDR_RE = re.compile(r"^lvaddr://(?P<block>[0-9A-Fa-f]+)/(?P<offset>[0-9A-Fa-f]+)$", re.IGNORECASE)


def _mime_for_path(path: Path) -> str:
    return _IMAGE_MIME_BY_SUFFIX.get(path.suffix.casefold()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _clean_resource_ref(value: str) -> str:
    parsed = urlparse(value.replace("\\", "/"))
    path = unquote(parsed.path or value).strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def _parse_int_auto(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        return int(text, 16 if any(ch in "abcdefABCDEF" for ch in text) else 10)
    except ValueError:
        return None


def _ssed_html_link_target(href: str, idx_name: str) -> str | None:
    clean = unquote(str(href or "")).strip()
    match = _LVADDR_RE.match(clean)
    if match is None:
        return None
    block = _parse_int_auto(match.group("block"))
    offset = _parse_int_auto(match.group("offset"))
    if block is None or offset is None:
        return None
    return f"ssed:{idx_name}:{block}:{offset}"


def _lved_html_link_target(href: str) -> str | None:
    clean = unquote(str(href or "")).strip()
    if not clean:
        return None
    parsed = urlparse(clean)
    path = parsed.path or clean
    if path.isdigit():
        return f"lved:content:{int(path)}"
    match = _ENTRY_HTML_RE.search(path)
    if match is not None:
        return f"lved:content:{int(match.group('id'))}"
    match = _LVED_DATAID_RE.match(clean)
    if match is not None:
        return f"lved:content:{int(match.group('id'))}"
    return None


@dataclass
class _FileResourceResolver:
    package_hint: Path
    resources: dict[str, ExportResource] = field(default_factory=dict)
    diagnostics: list[ExportDiagnostic] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._image_profile = load_image_resource_profile(self.package_hint)
        self._roots = candidate_package_roots(self.package_hint)

    def _resolve_direct_path(self, ref: str) -> Path | None:
        if not ref or ref.startswith(("data:", "http:", "https:", "javascript:")):
            return None
        clean = _clean_resource_ref(ref)
        if not clean:
            return None
        raw = Path(clean)
        if raw.is_absolute() and raw.is_file():
            return raw
        for root in self._roots:
            direct = resolve_case_insensitive_path(root, clean)
            if direct is not None and direct.is_file():
                return direct
        basename = Path(clean).name
        for image_dir in self._image_profile.image_dirs:
            candidate = find_case_insensitive(image_dir, basename)
            if candidate is not None and candidate.is_file():
                return candidate
        key = Path(clean).stem.casefold()
        resource = self._image_profile.resources.get(key)
        if resource is not None:
            selected = resource.normal or resource.default or resource.white
            if selected is not None and selected.is_file():
                return selected
        return None

    def add_image_file(self, ref: str, *, role: str = "html_image") -> str | None:
        path = self._resolve_direct_path(ref)
        if path is None:
            return None
        resource_id = f"file:{relative_image_source(path, self.package_hint)}"
        if resource_id not in self.resources:
            try:
                payload = path.read_bytes()
            except OSError as exc:
                self.diagnostics.append(
                    ExportDiagnostic("warning", "export_image_read_failed", f"Could not read image resource: {exc}", source=str(path))
                )
                return None
            self.resources[resource_id] = ExportResource(
                resource_id=resource_id,
                kind="image",
                source_path=str(path),
                payload=payload,
                mime_type=_mime_for_path(path),
                role=role,
            )
        return resource_id

    def rewrite_body_media(self, body: ExportBody, *, role: str = "html_image") -> ExportBody:
        events: list[ExportBodyEvent] = []
        changed = False
        for event in body.events:
            if event.kind == "media_ref" and event.value and not event.value.startswith(("ssed-", "file:")):
                resource_id = self.add_image_file(event.value, role=role)
                if resource_id is not None:
                    attrs = dict(event.attrs)
                    attrs.setdefault("epwing_media", "image")
                    events.append(ExportBodyEvent(kind="media_ref", text=event.text, value=resource_id, attrs=attrs))
                    changed = True
                    continue
            events.append(event)
        if not changed:
            return body
        return ExportBody(events=tuple(events), source_kind=body.source_kind, diagnostics=body.diagnostics)


class _SsedMediaResolver(_FileResourceResolver):
    def __init__(self, source, options: ExportSourceOptions) -> None:
        super().__init__(source.idx)
        self.source = source
        self.options = options
        self._colscr_reader: SsedRandomReader | None | bool = False
        self._pcm_reader: SsedRandomReader | None | bool = False
        self._shared_wave = None

    def _colscr(self) -> SsedRandomReader | None:
        if self._colscr_reader is False:
            path = find_colscr_path(self.source.idx)
            self._colscr_reader = SsedRandomReader(path) if path is not None else None
        return self._colscr_reader if isinstance(self._colscr_reader, SsedRandomReader) else None

    def _pcm(self) -> SsedRandomReader | None:
        if self._pcm_reader is False:
            path = find_pcmdata_path(self.source.idx)
            self._pcm_reader = SsedRandomReader(path) if path is not None else None
            if self._pcm_reader is not None:
                prefix = self._pcm_reader.read(0, min(self._pcm_reader.expanded_size, PCMDATA_DIRECTORY_BYTES))
                self._shared_wave = detect_shared_wave_stream(prefix)
        return self._pcm_reader if isinstance(self._pcm_reader, SsedRandomReader) else None

    def resolve_control_payload(self, payload_hex: str) -> ExportBodyEvent | None:
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError:
            return None
        if len(payload) == 18:
            return self._resolve_colscr_payload(payload)
        if len(payload) == 16:
            return self._resolve_pcm_payload(payload)
        return None

    def _resolve_colscr_payload(self, payload: bytes) -> ExportBodyEvent | None:
        pointer = parse_media_pointer(payload)
        reader = self._colscr()
        if pointer is None or reader is None:
            return None
        resource_id = f"ssed-colscr:{pointer.block:08d}:{pointer.offset:04d}"
        if resource_id not in self.resources:
            try:
                parsed = read_colscr_record(reader, pointer)
            except Exception as exc:
                self.diagnostics.append(
                    ExportDiagnostic("warning", "ssed_colscr_read_failed", f"Could not read COLSCR image: {exc}", source=resource_id)
                )
                return None
            if parsed is None:
                return None
            record, image = parsed
            wrapped = b"data" + len(image).to_bytes(4, "little") + image
            self.resources[resource_id] = ExportResource(
                resource_id=resource_id,
                kind="image",
                payload=wrapped,
                mime_type=f"image/{'jpeg' if record.media_type == 'jpeg' else record.media_type}",
                role="colscr",
            )
        return ExportBodyEvent(kind="media_ref", text="image", value=resource_id, attrs={"epwing_media": "image", "epwing_display": "block"})

    def _resolve_pcm_payload(self, payload: bytes) -> ExportBodyEvent | None:
        pointer = parse_pcm_pointer(payload)
        reader = self._pcm()
        if pointer is None or reader is None:
            return None
        resource_id = f"ssed-pcmdata:{pointer.start_block:08d}:{pointer.start_offset:04d}-{pointer.end_block:08d}:{pointer.end_offset:04d}"
        if resource_id not in self.resources:
            start, end, size = pointer_relative_range(pointer, reader)
            if start < 0 or end >= reader.expanded_size or size <= 0:
                return None
            raw = reader.read(start, size)
            parsed = parse_pcmdata_record(raw, start, shared_wave=self._shared_wave)
            if parsed is None:
                return None
            record, _chunks = parsed
            mime_type = "audio/wav"
            if record.media_type == "wave_chunks":
                chunks = raw[: record.content_size]
            elif record.media_type == "mp3":
                chunks = raw[: record.content_size]
                mime_type = "audio/mpeg"
            elif record.media_type == "mp3_data_slice":
                chunks = raw[: record.content_size]
                mime_type = "audio/mpeg"
            else:
                chunks = wave_chunks_for_record(record, raw[: record.content_size])
            self.resources[resource_id] = ExportResource(
                resource_id=resource_id,
                kind="audio",
                payload=chunks,
                mime_type=mime_type,
                role="pcmdata",
            )
        return ExportBodyEvent(kind="media_ref", text="sound", value=resource_id, attrs={"epwing_media": "audio"})


def _media_blob_for_ref(con: sqlite3.Connection, ref: str) -> tuple[str, bytes, str] | None:
    if not _sqlite_table_exists(con, "media"):
        return None
    columns = {column.casefold(): column for column in _sqlite_columns(con, "media")}
    name_col = columns.get("f_name") or columns.get("name")
    blob_col = columns.get("f_main") or columns.get("body") or columns.get("data") or columns.get("blob")
    if name_col is None or blob_col is None:
        return None
    clean = _clean_resource_ref(ref)
    candidates = [clean]
    basename = Path(clean).name
    if basename and basename not in candidates:
        candidates.append(basename)
    stem = Path(basename or clean).stem
    if stem and stem not in candidates:
        candidates.append(stem)
    for candidate in candidates:
        if not candidate:
            continue
        row = con.execute(
            f"select {quote_identifier(name_col)} as name, {quote_identifier(blob_col)} as payload "
            f"from {quote_identifier('media')} where lower({quote_identifier(name_col)}) = lower(?) limit 1",
            (candidate,),
        ).fetchone()
        if row is None:
            continue
        payload = row["payload"]
        if payload is None:
            continue
        row_name = str(row["name"] or candidate)
        mime_path = Path(row_name if Path(row_name).suffix else (basename or clean or row_name))
        return row_name, bytes(payload), _mime_for_path(mime_path)
    return None


def _rewrite_body_with_sqlite_media(body: ExportBody, con: sqlite3.Connection, resolver: _FileResourceResolver, *, role: str) -> ExportBody:
    events: list[ExportBodyEvent] = []
    changed = False
    for event in body.events:
        if event.kind == "media_ref" and event.value and not event.value.startswith(("ssed-", "file:", "rendererdb-media:")):
            media = _media_blob_for_ref(con, event.value)
            if media is not None:
                name, payload, mime_type = media
                resource_id = f"rendererdb-media:{name}"
                if resource_id not in resolver.resources:
                    resolver.resources[resource_id] = ExportResource(
                        resource_id=resource_id,
                        kind="image",
                        payload=payload,
                        mime_type=mime_type,
                        role=role,
                    )
                attrs = dict(event.attrs)
                attrs.setdefault("epwing_media", "image")
                events.append(ExportBodyEvent(kind="media_ref", text=event.text, value=resource_id, attrs=attrs))
                changed = True
                continue
        events.append(event)
    if not changed:
        return body
    return ExportBody(events=tuple(events), source_kind=body.source_kind, diagnostics=body.diagnostics)


def _pointer_target_from_payload(payload_hex: str, idx_name: str) -> str | None:
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        return None
    if len(payload) < 6:
        return None
    payload = payload[-6:]
    block = decode_bcd_decimal(payload[:4])
    offset = decode_bcd_decimal(payload[4:6])
    if block is None or offset is None:
        block = int.from_bytes(payload[:4], "big")
        offset = int.from_bytes(payload[4:6], "big")
    if block <= 0:
        return None
    return f"ssed:{idx_name}:{block}:{offset}"


def _event_plain_label(events: list[ExportBodyEvent], fallback: str = "link") -> str:
    parts: list[str] = []
    for event in events:
        if event.kind == "text":
            parts.append(event.text)
        elif event.kind == "line_break":
            parts.append(" ")
        elif event.kind in {"gaiji_ref", "media_ref"}:
            parts.append(event.text or event.value)
    label = " ".join("".join(parts).split()).strip()
    return label or fallback


_HEADING_GAIJI_TAG_RE = re.compile(r"<(?:img:|[hz])[0-9A-Fa-f]{4}>")


def _clean_export_heading(heading: str) -> str:
    cleaned = _HEADING_GAIJI_TAG_RE.sub("", heading).strip()
    return cleaned or heading


def _token_events(
    tokens: list[object],
    media_resolver: _SsedMediaResolver | None = None,
    *,
    idx_name: str = "",
) -> tuple[ExportBodyEvent, ...]:
    events: list[ExportBodyEvent] = []
    link_stack: list[dict[str, Any]] = []

    def append_event(event: ExportBodyEvent) -> None:
        if link_stack:
            link_stack[-1]["events"].append(event)
        else:
            events.append(event)

    for token in tokens:
        if isinstance(token, Text):
            append_event(ExportBodyEvent.text_event(token.value))
        elif isinstance(token, Break):
            append_event(ExportBodyEvent.line_break())
        elif isinstance(token, StartTag):
            if token.tag in {"bold", "italic", "sub", "sup", "em"}:
                append_event(ExportBodyEvent(kind="style_start", value=token.tag))
        elif isinstance(token, EndTag):
            if token.tag in {"bold", "italic", "sub", "sup", "em"}:
                append_event(ExportBodyEvent(kind="style_end", value=token.tag))
        elif isinstance(token, LinkStart):
            link_stack.append({"op": token.op, "payload": token.payload, "events": []})
        elif isinstance(token, LinkEnd):
            if not link_stack:
                continue
            ctx = link_stack.pop()
            target_payload = token.payload or str(ctx.get("payload") or "")[-12:]
            target = _pointer_target_from_payload(target_payload, idx_name) if idx_name else None
            append_event(
                ExportBodyEvent(
                    kind="link",
                    text=_event_plain_label(ctx["events"]),
                    value=target or "",
                    attrs={"start_op": f"{int(ctx.get('op') or 0):02x}", "end_op": f"{token.op:02x}"},
                )
            )
        elif isinstance(token, Image):
            append_event(ExportBodyEvent(kind="gaiji_ref", value=token.key))
        elif isinstance(token, Media):
            resolved = media_resolver.resolve_control_payload(token.payload) if media_resolver is not None else None
            append_event(resolved or ExportBodyEvent(kind="media_ref", value=token.payload, text="media"))
        elif isinstance(token, Section):
            append_event(ExportBodyEvent(kind="section", value=token.code))
    while link_stack:
        ctx = link_stack.pop(0)
        events.extend(ctx["events"])
    return tuple(events)


def _mode_for_index_type(component_type: int) -> str:
    if component_type in {0x90, 0x91, 0x92}:
        return "forward"
    if component_type in {0x70, 0x71, 0x72}:
        return "backward"
    if component_type == 0x80:
        return "keyword"
    if component_type == 0x81:
        return "cross-reference"
    if component_type in {0x30, 0x60}:
        return "body-only"
    return "unknown"


def _gaiji_rows(idx: Path) -> tuple[ExportGaiji, ...]:
    profile = load_gaiji_profile(idx)
    rows_by_key: dict[tuple[str, str], ExportGaiji] = {}
    uni_candidates, _plist_candidates = candidate_gaiji_paths(idx)
    uni_resources = []
    seen_uni: set[Any] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_uni:
            continue
        seen_uni.add(identity)
        resource = parse_uni_resource(path)
        if resource is not None:
            uni_resources.append(resource)
            for record in resource.records:
                if record.display:
                    rows_by_key[(record.section, record.code.upper())] = ExportGaiji(
                        code=record.code.upper(),
                        space=record.section,
                        unicode=record.display,
                    )
    for code, value in sorted(profile.map.items()):
        first = int(code[:2], 16)
        inferred_space = "half" if first < 0xB0 else "full"
        rows_by_key.setdefault(
            (inferred_space, code.upper()),
            ExportGaiji(
                code=code.upper(),
                space=inferred_space,
                unicode=value,
            )
        )

    bitmap_paths: list[Path] = []
    seen_bitmap: set[Any] = set()
    for root in (idx.parent, idx.parent.parent):
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_file() or not is_bitmap_gaiji_resource_name(child):
                continue
            identity = file_identity(child)
            if identity in seen_bitmap:
                continue
            seen_bitmap.add(identity)
            bitmap_paths.append(child)

    for bitmap_path in sorted(bitmap_paths, key=lambda item: item.name.casefold()):
        resource = parse_ga16_resource(bitmap_path)
        section = ga16_section_for_path(bitmap_path)
        if resource is None or section is None:
            continue
        data = bitmap_path.read_bytes()
        preferred_uni = next((uni for uni in uni_resources if any(record.section == section for record in uni.records)), None)
        for index in range(resource.count):
            code, _source = ga16_preferred_code_for_index(resource, index, preferred_uni)
            glyph = resource.glyph_for_index(data, index)
            key = (section, code.upper())
            previous = rows_by_key.get(key)
            rows_by_key[key] = ExportGaiji(
                code=code.upper(),
                space=section,
                unicode=previous.unicode if previous is not None else None,
                template_resource=previous.template_resource if previous is not None else None,
                bitmap=glyph,
                bitmap_width=resource.width,
                bitmap_height=resource.height,
                bitmap_source=bitmap_path.name,
                bitmap_backed=True,
                nonliteral_marker=previous.nonliteral_marker if previous is not None else False,
            )

    return tuple(sorted(rows_by_key.values(), key=lambda row: (row.space, int(row.code, 16))))


def _renderer_sidecar_sqlite_path(sidecar_path: Path, storage: str, callback) -> Any:
    if storage == "plain":
        return callback(sidecar_path)
    with TemporaryDirectory() as temp_dir:
        decrypted = Path(temp_dir) / f"{sidecar_path.name}.sqlite"
        decrypt_logofont_cipher_file_to_path(sidecar_path, decrypted)
        return callback(decrypted)


def _rendererdb_export_entries(
    source,
    reader: SsedRandomReader,
    options: ExportSourceOptions,
    media_resolver: _SsedMediaResolver | None = None,
) -> tuple[list[ExportEntry], list[ExportDiagnostic]] | None:
    """Return renderer-sidecar entries for dense HONMON packages when available."""

    exinfo = load_exinfo_for_idx(source.idx)
    sidecars = discover_renderer_sidecars(source.idx, exinfo)
    if not sidecars:
        return None

    expanded = reader.read(0, reader.expanded_size)
    raw_ids = list(iter_honmon_id_records(expanded, honmon_start_block=source.honmon_start_block))
    if not raw_ids:
        return None
    ids_by_data_id = {record.data_id: record for record in raw_ids}
    diagnostics: list[ExportDiagnostic] = [
        ExportDiagnostic(
            "info",
            "rendererdb_dense_honmon_source",
            f"HONMON contains {len(raw_ids)} dense ID anchor records; exporting renderer sidecar bodies instead of raw anchors.",
        )
    ]
    interval = _progress_interval(options)

    def build(db_path: Path) -> tuple[list[ExportEntry], list[ExportDiagnostic]] | None:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            if not table_exists(con, "t_contents"):
                return None
            columns = t_contents_columns(con)
            data_id_col = columns.get("f_DataId")
            html_col = columns.get("f_Html")
            plain_col = columns.get("f_Plane")
            title_col = columns.get("f_Title")
            title_search_col = columns.get("f_Title_SS")
            keyword_col = columns.get("f_Keyword")
            if data_id_col is None or (html_col is None and plain_col is None):
                return None
            select_parts = [f"{quote_identifier(data_id_col)} as {quote_identifier('f_DataId')}"]
            optional_cols = {
                "f_Html": html_col,
                "f_Plane": plain_col,
                "f_Title": title_col,
                "f_Title_SS": title_search_col,
                "f_Keyword": keyword_col,
            }
            for canonical, actual in optional_cols.items():
                if actual is not None:
                    select_parts.append(f"{quote_identifier(actual)} as {quote_identifier(canonical)}")
            total_row = con.execute(f"select count(*) from {quote_identifier('t_contents')}").fetchone()
            total_rows = int(total_row[0]) if total_row else 0
            _progress(
                options,
                f"export: using renderer sidecar {db_path.name} t_contents rows={total_rows:,} raw_ids={len(raw_ids):,}",
            )
            query = (
                f"select {', '.join(select_parts)} from {quote_identifier('t_contents')} "
                f"order by {quote_identifier(data_id_col)}"
            )
            entries: list[ExportEntry] = []
            skipped_without_anchor = 0
            for row_index, row in enumerate(con.execute(query), start=1):
                if options.limit is not None and options.limit > 0 and len(entries) >= options.limit:
                    diagnostics.append(ExportDiagnostic("info", "entry_limit", f"stopped after {options.limit} renderer sidecar entries"))
                    break
                data_id = parse_decimal_int(row["f_DataId"])
                if data_id is None:
                    continue
                raw = ids_by_data_id.get(data_id)
                if raw is None:
                    skipped_without_anchor += 1
                    continue
                title_value = _sqlite_text(row["f_Title"]) if "f_Title" in row.keys() else ""
                html_value = _sqlite_text(row["f_Html"]) if "f_Html" in row.keys() else ""
                plain_value = _sqlite_text(row["f_Plane"]) if "f_Plane" in row.keys() else ""
                title_plain = _html_to_plain(title_value) if title_value else ""
                if not title_plain:
                    title_plain = (plain_value or _html_to_plain(html_value)).strip().split("\n", 1)[0][:80]
                if not title_plain:
                    title_plain = f"data {data_id}"
                if html_value:
                    body = html_to_export_body(
                        html_value,
                        source_kind="rendererdb_t_contents_html",
                        link_resolver=lambda href, idx_name=source.idx.name: _ssed_html_link_target(href, idx_name),
                    )
                    if media_resolver is not None:
                        body = _rewrite_body_with_sqlite_media(body, con, media_resolver, role="rendererdb_media_blob")
                        body = media_resolver.rewrite_body_media(body, role="rendererdb_html_image")
                else:
                    body = ExportBody.text(plain_value, source_kind="rendererdb_t_contents_plain")
                target = f"ssed:{source.idx.name}:{raw.marker_block}:{raw.marker_offset}"
                keys = []
                for value in (
                    title_plain,
                    _sqlite_text(row["f_Title_SS"]) if "f_Title_SS" in row.keys() else "",
                    _sqlite_text(row["f_Keyword"]) if "f_Keyword" in row.keys() else "",
                ):
                    clean = " ".join(value.split()).strip()
                    if clean and clean not in keys:
                        keys.append(clean)
                entries.append(
                    ExportEntry(
                        target=target,
                        headword=title_plain,
                        body=body,
                        search_keys=tuple(keys),
                    )
                )
                if len(entries) == 1 or len(entries) % interval == 0:
                    _progress(options, f"export: decoded {len(entries):,} renderer sidecar entries ({row_index:,}/{total_rows:,} rows)")
            if skipped_without_anchor:
                diagnostics.append(
                    ExportDiagnostic(
                        "info",
                        "rendererdb_rows_without_honmon_anchor",
                        f"{skipped_without_anchor} t_contents rows did not have matching dense HONMON anchors.",
                    )
                )
            if entries:
                return entries, diagnostics
            return None
        finally:
            con.close()

    for sidecar in sidecars:
        result = _renderer_sidecar_sqlite_path(sidecar.path, sidecar.storage, build)
        if result is not None:
            return result
    return None


_HTML_SUFFIXES = {".html", ".htm"}
_COPYRIGHT_NEEDLES = ("copyright", "著作権")
_ABOUT_NEEDLES = ("about", "について")


def _decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _copyright_candidate_score(path: Path, text: str) -> int | None:
    haystack = f"{path.as_posix().casefold()}\n{text[:2000].casefold()}"
    if any(needle in haystack for needle in _COPYRIGHT_NEEDLES):
        return 0
    if any(needle in haystack for needle in _ABOUT_NEEDLES):
        return 10
    return None


def _iter_html_files(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.casefold() in _HTML_SUFFIXES and not is_metadata_noise_path(root):
        return [root]
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold())
        if path.is_file() and path.suffix.casefold() in _HTML_SUFFIXES and not is_metadata_noise_path(path)
    ]


def _copyright_from_html_roots(roots: list[Path]) -> list[tuple[int, Path, str]]:
    candidates: list[tuple[int, Path, str]] = []
    for root in roots:
        for path in _iter_html_files(root):
            try:
                text = _decode_text_bytes(path.read_bytes())
            except OSError:
                continue
            score = _copyright_candidate_score(path, text)
            if score is not None:
                candidates.append((score, path, text))
    return candidates


def _ssed_copyright_surfaces(root: Path, options: ExportSourceOptions) -> tuple[list[ExportNavigation], list[ExportDiagnostic]]:
    """Extract copyright/about surfaces from SSED HANREI sources."""

    diagnostics: list[ExportDiagnostic] = []
    html_roots: list[Path] = []
    hanrei_dir = resolve_case_insensitive_path(root, "HANREI")
    if hanrei_dir is not None and hanrei_dir.is_dir():
        html_roots.append(hanrei_dir)
    root_hanrei = resolve_case_insensitive_path(root, "hanrei.html")
    if root_hanrei is not None and root_hanrei.is_file():
        html_roots.append(root_hanrei)
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name.casefold().endswith("_help.localized") and not is_metadata_noise_path(child):
                html_roots.append(child)
    except OSError:
        pass

    candidates = _copyright_from_html_roots(html_roots)
    chm_path = resolve_case_insensitive_path(root, "HANREI.chm")
    if chm_path is not None and chm_path.is_file():
        seven_zip = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
        if seven_zip is None:
            diagnostics.append(
                ExportDiagnostic(
                    "warning",
                    "ssed_hanrei_chm_extract_unavailable",
                    "HANREI.chm is present but no 7z/7zz/7za executable was found for extraction.",
                    source=chm_path.name,
                )
            )
        else:
            with TemporaryDirectory() as temp_dir:
                extracted = Path(temp_dir) / "hanrei-chm"
                try:
                    subprocess.run(
                        [seven_zip, "x", "-y", f"-o{extracted}", str(chm_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )
                    candidates.extend(_copyright_from_html_roots([extracted]))
                except (OSError, subprocess.CalledProcessError) as exc:
                    diagnostics.append(
                        ExportDiagnostic(
                            "warning",
                            "ssed_hanrei_chm_extract_failed",
                            f"HANREI.chm extraction failed: {exc}",
                            source=chm_path.name,
                        )
                    )

    if not candidates:
        return [], diagnostics
    candidates.sort(key=lambda item: (item[0], item[1].as_posix().casefold()))
    score, path, html = candidates[0]
    label = path.name
    plain_title = _html_to_plain(html)
    if score == 0:
        for line in plain_title.splitlines():
            if "著作権" in line or "copyright" in line.casefold():
                label = line.strip()[:80] or label
                break
    body = html_to_export_body(html, source_kind="ssed_hanrei_copyright_html")
    _progress(options, f"export: extracted SSED copyright surface from {path.name}")
    return [
        ExportNavigation(
            kind="copyright",
            label=label,
            target=f"ssed-info:{path.name}",
            body=body,
        )
    ], diagnostics


def _load_ssed_export_book(path: Path, options: ExportSourceOptions) -> ExportBook:
    _progress(options, f"export: discovering SSED package under {path}")
    sources = discover_dictionaries([path], jobs=1, include_gaiji=True, include_images=True)
    if not sources:
        raise ValueError(f"no SSED dictionary found under {path}")
    if len(sources) > 1:
        raise ValueError(f"multiple SSED dictionaries found under {path}; pass a direct .IDX or filtered directory")
    source = sources[0]
    title, elements = parse_ssedinfo(source.idx)
    reader = SsedRandomReader(source.honmon)
    gaiji_rows = _gaiji_rows(source.idx)
    source_image_gaiji_keys = set(source.image_gaiji_keys)
    source_image_gaiji_keys.update(
        row.code.lower()
        for row in gaiji_rows
        if row.bitmap_backed or row.template_resource
    )
    _progress(
        options,
        f"export: SSED source {source.dict_id}: title={title!r} honmon={source.honmon.name} expanded={reader.expanded_size:,} bytes",
    )
    diagnostics: list[ExportDiagnostic] = []
    media_resolver = _SsedMediaResolver(source, options)
    rendererdb_result = _rendererdb_export_entries(source, reader, options, media_resolver)
    if rendererdb_result is not None:
        entries, rendererdb_diagnostics = rendererdb_result
        diagnostics.extend(rendererdb_diagnostics)
        _progress(options, f"export: decoded {len(entries):,} renderer sidecar entries total")
    else:
        entries = []
        interval = _progress_interval(options)
        _progress(options, "export: decoding SSED HONMON entries")
        for entry_index, (start, end) in enumerate(iter_entry_slices_reader(reader), start=1):
            if options.limit is not None and options.limit > 0 and len(entries) >= options.limit:
                diagnostics.append(ExportDiagnostic("info", "entry_limit", f"stopped after {options.limit} entries"))
                break
            raw = reader.read(start, end - start)
            tokens, stats = decode_tokens(
                raw,
                gaiji="placeholder",
                gaiji_map=source.gaiji_map,
                image_gaiji_keys=source_image_gaiji_keys,
                preserve_image_gaiji=True,
                preserve_media=True,
                preserve_sections=True,
            )
            body_text = tokens_to_text(tokens)
            heading = _clean_export_heading(extract_heading(tokens, body_text)) or f"entry {entry_index}"
            block = source.honmon_start_block + start // BLOCK_SIZE
            offset = start % BLOCK_SIZE
            target = f"ssed:{source.idx.name}:{block}:{offset}"
            entry_diags = []
            if stats.get("unknown_controls"):
                entry_diags.append(ExportDiagnostic("warning", "unknown_controls", f"{stats['unknown_controls']} unknown controls", source=target))
            entries.append(
                ExportEntry(
                    target=target,
                    headword=heading,
                    body=ExportBody(
                        events=_token_events(tokens, media_resolver, idx_name=source.idx.name),
                        source_kind="ssed_honmon_slice",
                        diagnostics=tuple(entry_diags),
                    ),
                    search_keys=(heading,),
                    diagnostics=tuple(entry_diags),
                )
            )
            if len(entries) == 1 or len(entries) % interval == 0:
                _progress(options, f"export: decoded {len(entries):,} SSED entries ({end:,}/{reader.expanded_size:,} bytes)")
        _progress(options, f"export: decoded {len(entries):,} SSED entries total")

    indexes: list[ExportIndex] = []
    index_elements = [element for element in elements if element.type in INDEX_TYPES and element.start]
    if index_elements:
        _progress(options, f"export: scanning {len(index_elements):,} SSED index component(s)")
    for element in elements:
        if element.type not in INDEX_TYPES or not element.start:
            continue
        component = find_case_insensitive(source.idx.parent, element.filename)
        if component is None:
            continue
        before_rows = len(indexes)
        _progress(options, f"export: scanning index {element.filename} type=0x{element.type:02x}")

        def add_row(row: dict[str, Any]) -> None:
            if row.get("kind") != "leaf":
                return
            body = row.get("body") if isinstance(row.get("body"), dict) else {}
            block = body.get("block")
            offset = body.get("offset")
            if not isinstance(block, int) or not isinstance(offset, int):
                return
            indexes.append(
                ExportIndex(
                    mode=_mode_for_index_type(element.type),
                    key=str(row.get("key") or ""),
                    label=str(row.get("target_key") or row.get("key") or ""),
                    target=f"ssed:{source.idx.name}:{block}:{offset}",
                    source_component=element.filename,
                )
            )

        scan_index_component_reader(
            element.filename,
            element.type,
            component,
            element.start,
            gaiji="placeholder",
            gaiji_map=source.gaiji_map,
            emit_internal=False,
            emit_row=add_row,
        )
        _progress(options, f"export: scanned index {element.filename}: +{len(indexes) - before_rows:,} row(s)")

    navigation: list[ExportNavigation] = []
    menu_elements = [
        element
        for element in elements
        if (element.type in MENU_LIKE_TYPES or element.filename.upper() in MENU_LIKE_NAMES) and element.start
    ]
    if menu_elements:
        _progress(options, f"export: scanning {len(menu_elements):,} SSED menu/navigation component(s)")
    for element in elements:
        if (element.type not in MENU_LIKE_TYPES and element.filename.upper() not in MENU_LIKE_NAMES) or not element.start:
            continue
        component = find_case_insensitive(source.idx.parent, element.filename)
        if component is None:
            continue
        before_rows = len(navigation)
        _progress(options, f"export: scanning navigation {element.filename}")
        menu_reader = SsedRandomReader(component)
        parsed = parse_menu_stream(menu_reader.read(0, menu_reader.expanded_size), gaiji="unicode", gaiji_map=source.gaiji_map)
        resolve_menu_record_destinations(parsed.records, elements)
        for record in parsed.records:
            label = record.label().strip()
            if not label:
                continue
            target = None
            if record.destination and record.destination.target:
                resolved = record.destination.target
                if resolved.kind == "body":
                    target = f"ssed:{source.idx.name}:{resolved.block}:{resolved.offset}"
            navigation.append(ExportNavigation(kind="menu", label=label, target=target))
        _progress(options, f"export: scanned navigation {element.filename}: +{len(navigation) - before_rows:,} row(s)")

    copyright_rows, copyright_diagnostics = _ssed_copyright_surfaces(source.idx.parent, options)
    copyright_rows = [
        ExportNavigation(
            kind=row.kind,
            label=row.label,
            target=row.target,
            children=row.children,
            body=media_resolver.rewrite_body_media(row.body, role="hanrei_html_image") if row.body is not None else None,
            diagnostics=row.diagnostics,
        )
        for row in copyright_rows
    ]
    diagnostics.extend(copyright_diagnostics)
    diagnostics.extend(media_resolver.diagnostics)
    _progress(
        options,
        f"export: built SSED export model entries={len(entries):,} indexes={len(indexes):,} navigation={len(navigation):,} copyright={len(copyright_rows):,} gaiji={len(gaiji_rows):,}",
    )

    return ExportBook(
        title=title,
        source_family=SourceFamily.SSED,
        source_id=source.dict_id,
        metadata={"idx": str(source.idx), "honmon": str(source.honmon), "honmon_storage": source.honmon_storage},
        entries=tuple(entries),
        indexes=tuple(indexes),
        navigation=tuple(navigation),
        resources=tuple(media_resolver.resources.values()),
        copyright=tuple(copyright_rows),
        gaiji=gaiji_rows,
        diagnostics=tuple(diagnostics),
    )


def _sqlite_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("cp932", errors="replace")
    return str(value)


def _lved_payload_path(path: Path) -> Path:
    if path.is_file():
        return path
    for name in ("main.data", "main.dbc"):
        candidate = path / name
        if candidate.is_file():
            return candidate
    dbc = sorted(path.glob("*.dbc"), key=lambda item: item.name.casefold())
    if dbc:
        return dbc[0]
    raise ValueError(f"no LVED_SQLITE3 payload found under {path}")


def _with_lved_sqlite_path(payload: Path, callback, options: ExportSourceOptions):
    classification = classify_lved_payload(payload)
    _progress(options, f"export: LVED_SQLITE3 payload {payload.name}: class={classification}")
    if classification == "plaintext_sqlite":
        return callback(payload, "plaintext_sqlite")
    if classification != "sqlcipher_lved_candidate":
        raise ValueError(f"not an LVED_SQLITE3 payload: {payload} ({classification})")
    key_file = discover_lved_key_file(payload)
    if key_file is None:
        raise ValueError(f"encrypted LVED_SQLITE3 payload has no local .key file: {payload}")
    key = read_lved_key_file(key_file.path)
    _progress(options, f"export: decrypting LVED_SQLITE3 payload using {key_file.path.name} ({key_file.match})")
    with TemporaryDirectory() as temp_dir:
        decrypted = Path(temp_dir) / "lved.sqlite3"
        decrypt_lved_sqlcipher4_to_path(payload, decrypted, key)
        _progress(options, f"export: decrypted LVED_SQLITE3 payload to temporary SQLite database ({decrypted.stat().st_size:,} bytes)")
        return callback(decrypted, f"sqlcipher:{key_file.match}")


def _load_lved_export_book(path: Path, options: ExportSourceOptions) -> ExportBook:
    _progress(options, f"export: locating LVED_SQLITE3 payload under {path}")
    payload = _lved_payload_path(path)

    def build(sqlite_path: Path, storage: str) -> ExportBook:
        title, title_source = lved_payload_title(payload, full_decode_sqlcipher=False)
        _progress(options, f"export: opening LVED_SQLITE3 database storage={storage} title_source={title_source}")
        resource_resolver = _FileResourceResolver(payload.parent)
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            entries: list[ExportEntry] = []
            if _sqlite_table_exists(con, "content"):
                columns = _sqlite_columns(con, "content")
                if {"id", "body"} <= {column.casefold() for column in columns}:
                    total_rows = _sqlite_count(con, "content")
                    if total_rows is None:
                        _progress(options, "export: reading LVED content rows")
                    else:
                        limit_text = f" limit={options.limit:,}" if options.limit is not None and options.limit > 0 else ""
                        _progress(options, f"export: reading LVED content rows total={total_rows:,}{limit_text}")
                    limit_sql = "" if options.limit is None or options.limit <= 0 else f" limit {int(options.limit)}"
                    interval = _progress_interval(options)
                    for row_index, row in enumerate(con.execute(f"select id, body from content order by id{limit_sql}"), start=1):
                        content_id = int(row["id"])
                        body_html = _sqlite_text(row["body"])
                        body = html_to_export_body(body_html, source_kind="lved_content_html", link_resolver=_lved_html_link_target)
                        body = _rewrite_body_with_sqlite_media(body, con, resource_resolver, role="lved_media_blob")
                        body = resource_resolver.rewrite_body_media(body, role="lved_html_image")
                        heading = _lved_title_for_content(con, content_id) or f"content {content_id}"
                        entries.append(
                            ExportEntry(
                                target=f"lved:content:{content_id}",
                                headword=heading,
                                body=body,
                                search_keys=(heading,),
                            )
                        )
                        if len(entries) == 1 or len(entries) % interval == 0:
                            total_suffix = f"/{total_rows:,}" if total_rows is not None else ""
                            _progress(options, f"export: decoded {len(entries):,}{total_suffix} LVED content row(s)")
            indexes = [
                ExportIndex(mode="forward", key=entry.headword, target=entry.target, label=entry.headword)
                for entry in entries
                if entry.headword
            ]
            copyright_rows: list[ExportNavigation] = []
            if _sqlite_table_exists(con, "info"):
                _progress(options, "export: scanning LVED info rows for copyright/about surfaces")
                for row in con.execute("select id, name, body from info order by id"):
                    name = _sqlite_text(row["name"])
                    if "copyright" in name.casefold() or "著作権" in name or "about" in name.casefold() or "について" in name:
                        body_html = _sqlite_text(row["body"])
                        body = html_to_export_body(body_html, source_kind="lved_info_html", link_resolver=_lved_html_link_target)
                        body = _rewrite_body_with_sqlite_media(body, con, resource_resolver, role="lved_info_media_blob")
                        body = resource_resolver.rewrite_body_media(body, role="lved_info_image")
                        copyright_rows.append(
                            ExportNavigation(
                                kind="copyright",
                                label=name or f"info {row['id']}",
                                target=f"lved:info:{row['id']}",
                                body=body,
                            )
                        )
            diagnostics = list(resource_resolver.diagnostics)
            if not entries:
                diagnostics.append(ExportDiagnostic("warning", "lved_no_content_entries", "No exportable content table rows were found."))
            _progress(
                options,
                f"export: built LVED export model entries={len(entries):,} indexes={len(indexes):,} copyright={len(copyright_rows):,}",
            )
            return ExportBook(
                title=title or payload.parent.name.removeprefix("_DCT_"),
                source_family=SourceFamily.LVED_SQLITE3,
                source_id=payload.parent.name.removeprefix("_DCT_"),
                metadata={"payload": str(payload), "storage": storage, "title_source": title_source},
                entries=tuple(entries),
                indexes=tuple(indexes),
                resources=tuple(resource_resolver.resources.values()),
                copyright=tuple(copyright_rows),
                diagnostics=tuple(diagnostics),
            )
        finally:
            con.close()

    return _with_lved_sqlite_path(payload, build, options)


def _sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"pragma table_info({_quote_sql_identifier(table)})")]


def _sqlite_table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("select 1 from sqlite_master where type='table' and name=? limit 1", (table,)).fetchone() is not None


def _sqlite_count(con: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(con.execute(f"select count(*) from {_quote_sql_identifier(table)}").fetchone()[0])
    except sqlite3.Error:
        return None


def _quote_sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lved_title_for_content(con: sqlite3.Connection, content_id: int) -> str | None:
    if not _sqlite_table_exists(con, "list"):
        return None
    columns = {column.casefold() for column in _sqlite_columns(con, "list")}
    if not {"content_id", "title"} <= columns:
        return None
    row = con.execute("select title from list where content_id = ? order by id limit 1", (content_id,)).fetchone()
    if row is None:
        return None
    title = _sqlite_text(row["title"]).strip()
    if not title:
        return None
    return _html_to_plain(title)


def _html_to_plain(fragment: str) -> str:
    body = html_to_export_body(fragment, source_kind="html_label")
    out: list[str] = []
    for event in body.events:
        if event.kind == "text":
            out.append(event.text)
        elif event.kind == "line_break":
            out.append(" ")
    return " ".join("".join(out).split()).strip()


def _detect_source_family(path: Path) -> SourceFamily:
    if path.is_file() and path.suffix.lower() == ".idx":
        return SourceFamily.SSED
    candidate = path if path.is_file() else None
    if candidate is None:
        for name in ("main.data", "main.dbc"):
            if (path / name).is_file():
                candidate = path / name
                break
        if candidate is None:
            dbc = sorted(path.glob("*.dbc"), key=lambda item: item.name.casefold()) if path.is_dir() else []
            if dbc:
                candidate = dbc[0]
    if candidate is not None:
        try:
            if classify_lved_payload(candidate) in {"plaintext_sqlite", "sqlcipher_lved_candidate"}:
                return SourceFamily.LVED_SQLITE3
        except OSError:
            pass
    return SourceFamily.SSED


def load_export_book(path: Path, options: ExportSourceOptions | None = None) -> ExportBook:
    options = options or ExportSourceOptions()
    family = options.source_family
    if family == SourceFamily.AUTO:
        _progress(options, f"export: detecting source family for {path}")
        family = _detect_source_family(path)
    _progress(options, f"export: source family={family.value}")
    if family == SourceFamily.SSED:
        return _load_ssed_export_book(path, options)
    if family == SourceFamily.LVED_SQLITE3:
        return _load_lved_export_book(path, options)
    raise ValueError(f"unsupported export source family: {family}")
