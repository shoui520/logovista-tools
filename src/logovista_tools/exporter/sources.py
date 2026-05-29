"""LogoVista source adapters for the exporter IR."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from ..entries import (
    Break,
    EndTag,
    Image,
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
from ..gaiji import load_gaiji_profile
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
from ..rendererdb import (
    iter_honmon_id_records,
    parse_decimal_int,
    quote_identifier,
    t_contents_columns,
    table_exists,
)
from ..ssed import BLOCK_SIZE, SsedRandomReader, find_case_insensitive, is_metadata_noise_path, parse_ssedinfo, resolve_case_insensitive_path
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


def _token_events(tokens: list[object]) -> tuple[ExportBodyEvent, ...]:
    events: list[ExportBodyEvent] = []
    for token in tokens:
        if isinstance(token, Text):
            events.append(ExportBodyEvent.text_event(token.value))
        elif isinstance(token, Break):
            events.append(ExportBodyEvent.line_break())
        elif isinstance(token, StartTag):
            if token.tag in {"bold", "italic", "sub", "sup", "em"}:
                events.append(ExportBodyEvent(kind="style_start", value=token.tag))
        elif isinstance(token, EndTag):
            if token.tag in {"bold", "italic", "sub", "sup", "em"}:
                events.append(ExportBodyEvent(kind="style_end", value=token.tag))
        elif isinstance(token, Image):
            events.append(ExportBodyEvent(kind="gaiji_ref", value=token.key))
        elif isinstance(token, Media):
            events.append(ExportBodyEvent(kind="media_ref", value=token.payload, text="media"))
        elif isinstance(token, Section):
            events.append(ExportBodyEvent(kind="section", value=token.code))
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
    rows: list[ExportGaiji] = []
    for code, value in sorted(profile.map.items()):
        first = int(code[:2], 16)
        rows.append(
            ExportGaiji(
                code=code.upper(),
                space="half" if first < 0xB0 else "full",
                unicode=value,
            )
        )
    return tuple(rows)


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
                    body = html_to_export_body(html_value, source_kind="rendererdb_t_contents_html")
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
    _progress(
        options,
        f"export: SSED source {source.dict_id}: title={title!r} honmon={source.honmon.name} expanded={reader.expanded_size:,} bytes",
    )
    diagnostics: list[ExportDiagnostic] = []
    rendererdb_result = _rendererdb_export_entries(source, reader, options)
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
                image_gaiji_keys=source.image_gaiji_keys,
                preserve_image_gaiji=True,
                preserve_media=True,
                preserve_sections=True,
            )
            body_text = tokens_to_text(tokens)
            heading = extract_heading(tokens, body_text) or f"entry {entry_index}"
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
                    body=ExportBody(events=_token_events(tokens), source_kind="ssed_honmon_slice", diagnostics=tuple(entry_diags)),
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

    gaiji_rows = _gaiji_rows(source.idx)
    copyright_rows, copyright_diagnostics = _ssed_copyright_surfaces(source.idx.parent, options)
    diagnostics.extend(copyright_diagnostics)
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
                        body = html_to_export_body(body_html, source_kind="lved_content_html")
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
                        copyright_rows.append(
                            ExportNavigation(
                                kind="copyright",
                                label=name or f"info {row['id']}",
                                target=f"lved:info:{row['id']}",
                                body=html_to_export_body(body_html, source_kind="lved_info_html"),
                            )
                        )
            diagnostics = []
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
