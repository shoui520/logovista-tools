"""LogoVista source adapters for the exporter IR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
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
from ..menus import MENU_LIKE_NAMES, MENU_LIKE_TYPES, parse_menu_stream
from ..ssed import BLOCK_SIZE, SsedRandomReader, find_case_insensitive, parse_ssedinfo
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


def _load_ssed_export_book(path: Path, options: ExportSourceOptions) -> ExportBook:
    sources = discover_dictionaries([path], jobs=1, include_gaiji=True, include_images=True)
    if not sources:
        raise ValueError(f"no SSED dictionary found under {path}")
    if len(sources) > 1:
        raise ValueError(f"multiple SSED dictionaries found under {path}; pass a direct .IDX or filtered directory")
    source = sources[0]
    title, elements = parse_ssedinfo(source.idx)
    reader = SsedRandomReader(source.honmon)
    entries: list[ExportEntry] = []
    diagnostics: list[ExportDiagnostic] = []
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

    indexes: list[ExportIndex] = []
    for element in elements:
        if element.type not in INDEX_TYPES or not element.start:
            continue
        component = find_case_insensitive(source.idx.parent, element.filename)
        if component is None:
            continue

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

    navigation: list[ExportNavigation] = []
    for element in elements:
        if (element.type not in MENU_LIKE_TYPES and element.filename.upper() not in MENU_LIKE_NAMES) or not element.start:
            continue
        component = find_case_insensitive(source.idx.parent, element.filename)
        if component is None:
            continue
        menu_reader = SsedRandomReader(component)
        parsed = parse_menu_stream(menu_reader.read(0, menu_reader.expanded_size), gaiji="unicode", gaiji_map=source.gaiji_map)
        for record in parsed.records:
            label = record.label().strip()
            if not label:
                continue
            target = None
            if record.destination and record.destination.target:
                target = f"ssed-menu:{element.filename}:{record.destination.block}:{record.destination.offset}"
            navigation.append(ExportNavigation(kind="menu", label=label, target=target))

    return ExportBook(
        title=title,
        source_family=SourceFamily.SSED,
        source_id=source.dict_id,
        metadata={"idx": str(source.idx), "honmon": str(source.honmon), "honmon_storage": source.honmon_storage},
        entries=tuple(entries),
        indexes=tuple(indexes),
        navigation=tuple(navigation),
        gaiji=_gaiji_rows(source.idx),
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


def _with_lved_sqlite_path(payload: Path, callback):
    classification = classify_lved_payload(payload)
    if classification == "plaintext_sqlite":
        return callback(payload, "plaintext_sqlite")
    if classification != "sqlcipher_lved_candidate":
        raise ValueError(f"not an LVED_SQLITE3 payload: {payload} ({classification})")
    key_file = discover_lved_key_file(payload)
    if key_file is None:
        raise ValueError(f"encrypted LVED_SQLITE3 payload has no local .key file: {payload}")
    key = read_lved_key_file(key_file.path)
    with TemporaryDirectory() as temp_dir:
        decrypted = Path(temp_dir) / "lved.sqlite3"
        decrypt_lved_sqlcipher4_to_path(payload, decrypted, key)
        return callback(decrypted, f"sqlcipher:{key_file.match}")


def _load_lved_export_book(path: Path, options: ExportSourceOptions) -> ExportBook:
    payload = _lved_payload_path(path)

    def build(sqlite_path: Path, storage: str) -> ExportBook:
        title, title_source = lved_payload_title(payload, full_decode_sqlcipher=False)
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            entries: list[ExportEntry] = []
            if _sqlite_table_exists(con, "content"):
                columns = _sqlite_columns(con, "content")
                if {"id", "body"} <= {column.casefold() for column in columns}:
                    limit_sql = "" if options.limit is None or options.limit <= 0 else f" limit {int(options.limit)}"
                    for row in con.execute(f"select id, body from content order by id{limit_sql}"):
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
            indexes = [
                ExportIndex(mode="forward", key=entry.headword, target=entry.target, label=entry.headword)
                for entry in entries
                if entry.headword
            ]
            copyright_rows: list[ExportNavigation] = []
            if _sqlite_table_exists(con, "info"):
                for row in con.execute("select id, name, body from info order by id"):
                    name = _sqlite_text(row["name"])
                    if "copyright" in name.casefold() or "著作権" in name or "about" in name.casefold() or "について" in name:
                        copyright_rows.append(ExportNavigation(kind="copyright", label=name or f"info {row['id']}", target=f"lved:info:{row['id']}"))
            diagnostics = []
            if not entries:
                diagnostics.append(ExportDiagnostic("warning", "lved_no_content_entries", "No exportable content table rows were found."))
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

    return _with_lved_sqlite_path(payload, build)


def _sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f"pragma table_info({_quote_sql_identifier(table)})")]


def _sqlite_table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("select 1 from sqlite_master where type='table' and name=? limit 1", (table,)).fetchone() is not None


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
        family = _detect_source_family(path)
    if family == SourceFamily.SSED:
        return _load_ssed_export_book(path, options)
    if family == SourceFamily.LVED_SQLITE3:
        return _load_lved_export_book(path, options)
    raise ValueError(f"unsupported export source family: {family}")
