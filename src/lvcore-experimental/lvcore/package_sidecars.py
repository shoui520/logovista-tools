"""SQLite sidecar discovery, role classification, supplements, and sidecar media."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from .body_source import (
    SQLITE_MAGIC,
    SidecarInfo,
    SidecarRole,
    SidecarSupportStatus,
    SidecarTableInfo,
    classify_sqlite_sidecar_role,
    compatibility_significant_sidecar_role,
    find_column,
    quote_sql_identifier,
    sqlite_columns,
    strip_html,
)
from .crypto import decrypt_logofont_file_to_path, decrypt_logofont_prefix
from .document import ResourceKind, ResourceRef
from .model import Address, Entry
from .package_utils import EXPENSIVE_SIDECAR_BYTES, _media_mime_and_format
from .ssed import read_file_prefix


class PackageSidecarMixin:
    """Sidecar discovery and supplemental resource methods for LogoVistaPackage."""

    def _sidecar_file_candidates(self) -> list[Path]:
        self._ensure_open()
        candidates: list[Path] = []
        try:
            children = sorted(self.info.root.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            return candidates
        dict_id = (self.info.dict_id or "").lower()
        for child in children:
            if not child.is_file():
                continue
            lower = child.name.lower()
            if lower == "vlpljbl.bin":
                continue
            is_dict_id_payload = bool(dict_id and child.suffix == "" and lower == dict_id)
            if lower.startswith("vlpljbl") or child.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".sql"} or is_dict_id_payload:
                candidates.append(child)
        return candidates

    def _body_sidecar_file_candidates(self) -> list[Path]:
        candidates = self._sidecar_file_candidates()

        def priority(path: Path) -> tuple[int, str]:
            lower = path.name.lower()
            if self.info.dict_id and lower == self.info.dict_id.lower():
                return 0, lower
            if lower.startswith("vlpljbl"):
                suffix = lower.removeprefix("vlpljbl")
                if suffix in {"f", "b", "h"}:
                    return 1, lower
                if suffix in {"m", "n", "s"}:
                    return 4, lower
                return 2, lower
            if lower.endswith((".db", ".sqlite", ".sqlite3", ".sql")):
                return 3, lower
            return 5, lower

        return sorted(candidates, key=priority)

    def _is_expensive_sidecar_candidate(self, path: Path) -> bool:
        try:
            if path.stat().st_size <= EXPENSIVE_SIDECAR_BYTES:
                return False
        except OSError:
            return False
        return self._sqlite_storage(path) == "logofont_cipher"

    @staticmethod
    def _sqlite_storage(path: Path) -> str | None:
        try:
            raw = read_file_prefix(path, 2048)
        except OSError:
            return None
        if raw.startswith(SQLITE_MAGIC):
            return "plain"
        try:
            prefix = decrypt_logofont_prefix(raw, size=64)
        except Exception:
            return None
        if prefix.startswith(SQLITE_MAGIC):
            return "logofont_cipher"
        return None

    def _sqlite_path_for_sidecar(self, path: Path, storage: str) -> Path:
        key = str(path)
        if storage == "plain":
            return path
        if key in self._sqlite_sidecar_cache:
            return self._sqlite_sidecar_cache[key]
        if self._tempdir is None:
            self._tempdir = tempfile.TemporaryDirectory(prefix="lvcore-sidecar-")
        decrypted = Path(self._tempdir.name) / f"{path.name}.sqlite"
        decrypt_logofont_file_to_path(path, decrypted)
        self._sqlite_sidecar_cache[key] = decrypted
        return decrypted

    @staticmethod
    def _row_count(con: sqlite3.Connection, table: str) -> int | None:
        try:
            return int(con.execute(f"select count(*) from {quote_sql_identifier(table)}").fetchone()[0])
        except sqlite3.DatabaseError:
            return None

    def _sidecar_table_info(self, con: sqlite3.Connection, table: str, *, include_row_count: bool = True) -> SidecarTableInfo:
        columns = sqlite_columns(con, table)
        lower = {column.lower(): column for column in columns}

        def first(*names: str) -> str | None:
            for name in names:
                found = lower.get(name.lower())
                if found is not None:
                    return found
            return None

        block_col = first("Block", "Block_s", "f_block")
        offset_col = first("Offset", "Offset_s", "f_offset")
        role = classify_sqlite_sidecar_role("sqlite_unmapped", (table,), {table: columns})
        return SidecarTableInfo(
            table=table,
            columns=tuple(columns),
            row_count=self._row_count(con, table) if include_row_count else None,
            role=role,
            id_column=first("ID", "No", "ItemID", "f_DataId", "f_data_id", "f_array_no", "f_contents_id", "f_order_id", "id", "index"),
            title_column=first(
                "Title",
                "TitleJIS",
                "JIS_Title",
                "Title_UTF8",
                "Title_SJIS",
                "f_Title",
                "f_title",
                "Keyword",
                "Midashi",
                "MidashiJ",
                "f_midasi",
                "f_midashi_hyoki",
                "f_midashi_key",
            ),
            html_column=first("f_Html", "f_html_text", "Contents_HTML_box", "Contents_HTML_list", "f_contents"),
            plain_column=first("Body", "f_body", "f_Plane", "f_plane", "f_plane_text", "h_text", "Value", "data"),
            blob_column=first("f_blob", "f_main"),
            name_column=first("f_name", "name"),
            block_column=block_col,
            offset_column=offset_col,
            end_block_column=first("Block_e"),
            end_offset_column=first("Offset_e"),
        )

    @staticmethod
    def _sidecar_support_status(role: SidecarRole | str, tables: tuple[SidecarTableInfo, ...]) -> SidecarSupportStatus:
        role_value = role.value if isinstance(role, SidecarRole) else str(role)
        if role_value == SidecarRole.MEDIA_RESOURCE.value:
            if any(table.blob_column and (table.name_column or table.id_column) for table in tables):
                return SidecarSupportStatus.RESOURCE_RESOLVER
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        if role_value in {
            SidecarRole.EXAMPLES_IDIOMS.value,
            SidecarRole.LINK_REFERENCE.value,
            SidecarRole.SUPPLEMENTAL.value,
        }:
            if any(table.block_column and table.offset_column and (table.title_column or table.plain_column or table.html_column) for table in tables):
                return SidecarSupportStatus.SUPPLEMENT_RESOLVER
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        if role_value == SidecarRole.SEARCH.value:
            if any(table.block_column and table.offset_column and (table.title_column or table.plain_column) for table in tables):
                return SidecarSupportStatus.SEARCH_METADATA
            if any(table.id_column and table.title_column for table in tables):
                return SidecarSupportStatus.SEARCH_METADATA
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        if role_value in {SidecarRole.ANCILLARY.value, SidecarRole.KANJI_SUPPORT.value}:
            return SidecarSupportStatus.SCHEMA_CLASSIFIED
        return SidecarSupportStatus.UNSUPPORTED_SCHEMA

    def _inspect_sqlite_sidecar(self, path: Path, *, include_row_counts: bool = True) -> SidecarInfo | None:
        key = f"{path}\0rows={int(include_row_counts)}"
        if key in self._sqlite_schema_cache:
            return self._sqlite_schema_cache[key]
        storage = self._sqlite_storage(path)
        if storage is None:
            self._sqlite_schema_cache[key] = None
            return None
        sqlite_path = self._sqlite_path_for_sidecar(path, storage)
        try:
            con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        except sqlite3.DatabaseError:
            self._sqlite_schema_cache[key] = None
            return None
        try:
            tables = [row[0] for row in con.execute("select name from sqlite_master where type='table' order by name")]
            table_infos = tuple(self._sidecar_table_info(con, table, include_row_count=include_row_counts) for table in tables)
            columns_by_table = {info.table: list(info.columns) for info in table_infos}
            for table in ("t_contents", "HONBUN", "main"):
                if table not in tables:
                    continue
                columns = sqlite_columns(con, table)
                table_info = next((info for info in table_infos if info.table == table), None)
                if table_info is not None:
                    table_info = replace(table_info, role=SidecarRole.BODY_CRITICAL)
                if table == "HONBUN":
                    id_col = find_column(columns, "ID", "f_DataId", "f_data_id")
                    title_col = find_column(columns, "Title_UTF8", "Title_SJIS", "Title", "f_Title")
                    html_col = find_column(columns, "Contents_HTML_box", "Contents_HTML_list", "f_Html", "f_contents")
                    plain_col = find_column(columns, "f_Plane", "f_body", "Body")
                    if id_col and (html_col or plain_col or title_col):
                        info = SidecarInfo(
                            path=path,
                            kind="honbun",
                            storage=storage,
                            role=classify_sqlite_sidecar_role("honbun", tables),
                            support_status=SidecarSupportStatus.BODY_RESOLVER,
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=html_col,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table) if include_row_counts else None,
                            tables=(table_info,) if table_info is not None else (),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
                elif table == "main":
                    id_col = find_column(columns, "ID")
                    title_col = find_column(columns, "C_text", "K_text", "J_text")
                    plain_col = find_column(columns, "J_text", "C_text", "K_text")
                    if id_col and (title_col or plain_col):
                        info = SidecarInfo(
                            path=path,
                            kind="main_wordlist",
                            storage=storage,
                            role=classify_sqlite_sidecar_role("main_wordlist", tables),
                            support_status=SidecarSupportStatus.BODY_RESOLVER,
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=None,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table) if include_row_counts else None,
                            tables=(table_info,) if table_info is not None else (),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
                else:
                    id_col = find_column(columns, "f_DataId", "f_data_id", "f_array_no", "f_contents_id", "f_order_id")
                    title_col = find_column(columns, "f_Title", "f_title", "f_midashi", "f_midashi_hyoki", "f_midashi_key", "f_abbr", "f_fullname")
                    html_col = find_column(columns, "f_Html", "f_html_text", "f_contents", "f_body")
                    plain_col = find_column(columns, "f_Plane", "f_plane", "f_plane_text", "f_body")
                    if id_col and (html_col or plain_col):
                        info = SidecarInfo(
                            path=path,
                            kind="t_contents",
                            storage=storage,
                            role=classify_sqlite_sidecar_role("t_contents", tables),
                            support_status=SidecarSupportStatus.BODY_RESOLVER,
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=html_col,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table) if include_row_counts else None,
                            tables=(table_info,) if table_info is not None else (),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
            role = classify_sqlite_sidecar_role("sqlite_unmapped", tables, columns_by_table)
            support_status = self._sidecar_support_status(role, table_infos)
            self._sqlite_schema_cache[key] = SidecarInfo(
                path=path,
                kind="sqlite_unmapped",
                storage=storage,
                role=role,
                support_status=support_status,
                tables=table_infos,
                notes=tuple(tables[:8]),
            )
            return self._sqlite_schema_cache[key]
        finally:
            con.close()

    def _body_sidecars(self, *, stop_after_body_resolver: bool = False, allow_expensive: bool = True) -> tuple[SidecarInfo, ...]:
        cache_key = (stop_after_body_resolver, allow_expensive)
        if cache_key in self._body_sidecars_cache:
            return self._body_sidecars_cache[cache_key]
        rows: list[SidecarInfo] = []
        candidates = self._body_sidecar_file_candidates() if stop_after_body_resolver else self._sidecar_file_candidates()
        for path in candidates:
            if not allow_expensive and self._is_expensive_sidecar_candidate(path):
                continue
            sidecar = self._inspect_sqlite_sidecar(path, include_row_counts=not stop_after_body_resolver)
            if sidecar is not None:
                rows.append(sidecar)
                if stop_after_body_resolver and sidecar.support_status == SidecarSupportStatus.BODY_RESOLVER:
                    break
        self._body_sidecars_cache[cache_key] = tuple(rows)
        return self._body_sidecars_cache[cache_key]

    def sidecar_role_summary(self) -> dict[str, object]:
        role_counts: dict[str, int] = {}
        unsupported_role_counts: dict[str, int] = {}
        supported_role_counts: dict[str, int] = {}
        compatibility_significant_unsupported_counts: dict[str, int] = {}
        support_status_counts: dict[str, int] = {}
        unsupported_sidecars: list[dict[str, object]] = []
        sqlite_count = 0
        non_sqlite_count = 0
        candidates = self._sidecar_file_candidates()
        for path in candidates:
            sidecar = self._inspect_sqlite_sidecar(path)
            if sidecar is None:
                non_sqlite_count += 1
                role = SidecarRole.NON_SQLITE_OR_UNKNOWN.value
                role_counts[role] = role_counts.get(role, 0) + 1
                status = SidecarSupportStatus.NON_SQLITE_OR_UNKNOWN.value
                support_status_counts[status] = support_status_counts.get(status, 0) + 1
                continue
            sqlite_count += 1
            role = sidecar.role.value if isinstance(sidecar.role, SidecarRole) else str(sidecar.role)
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            role_counts[role] = role_counts.get(role, 0) + 1
            support_status_counts[status] = support_status_counts.get(status, 0) + 1
            if status in {
                SidecarSupportStatus.BODY_RESOLVER.value,
                SidecarSupportStatus.SUPPLEMENT_RESOLVER.value,
                SidecarSupportStatus.RESOURCE_RESOLVER.value,
                SidecarSupportStatus.SEARCH_METADATA.value,
            }:
                supported_role_counts[role] = supported_role_counts.get(role, 0) + 1
            else:
                unsupported_role_counts[role] = unsupported_role_counts.get(role, 0) + 1
                significant = compatibility_significant_sidecar_role(role)
                if significant:
                    compatibility_significant_unsupported_counts[role] = compatibility_significant_unsupported_counts.get(role, 0) + 1
                unsupported_sidecars.append(
                    {
                        "name": sidecar.path.name,
                        "kind": sidecar.kind,
                        "role": role,
                        "support_status": status,
                        "compatibility_significant": significant,
                        "tables": [table.table for table in sidecar.tables] or list(sidecar.notes),
                    }
                )
        return {
            "candidate_count": len(candidates),
            "sqlite_count": sqlite_count,
            "non_sqlite_or_unknown_count": non_sqlite_count,
            "role_counts": role_counts,
            "supported_role_counts": supported_role_counts,
            "unsupported_role_counts": unsupported_role_counts,
            "compatibility_significant_unsupported_counts": compatibility_significant_unsupported_counts,
            "support_status_counts": support_status_counts,
            "unsupported_sidecars": unsupported_sidecars,
        }

    @staticmethod
    def _safe_sidecar_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            for encoding in ("utf-8", "cp932"):
                try:
                    return value.decode(encoding).strip()
                except UnicodeDecodeError:
                    continue
            return ""
        return str(value).strip()

    @staticmethod
    def _sidecar_supplement_kind(role: SidecarRole | str, table: str) -> str:
        role_value = role.value if isinstance(role, SidecarRole) else str(role)
        table_lower = table.lower()
        if role_value == SidecarRole.EXAMPLES_IDIOMS.value:
            if "idiom" in table_lower:
                return "idiom"
            if any(token in table_lower for token in ("goyo", "keigo", "kininaru")):
                return "usage_note"
            return "example"
        if role_value == SidecarRole.LINK_REFERENCE.value:
            return "link_reference"
        if role_value == SidecarRole.SEARCH.value:
            return "sidecar_search"
        return "supplemental"

    @staticmethod
    def _sidecar_table_text_columns(table: SidecarTableInfo) -> tuple[str, ...]:
        candidates = [
            table.title_column,
            table.plain_column,
            table.html_column,
            table.name_column,
            "Keyword",
            "Midashi",
            "MidashiJ",
            "Title",
            "TitleJIS",
            "JIS_Title",
            "Body",
            "h_text",
        ]
        columns = set(table.columns)
        out: list[str] = []
        for column in candidates:
            if column and column in columns and column not in out:
                out.append(column)
        return tuple(out)

    def sidecar_supplements(self, address: Address, *, limit: int = 32, debug: bool = False) -> list[dict[str, object]]:
        """Return readable supplemental sidecar rows for one entry address."""

        supplements: list[dict[str, object]] = []
        for sidecar in self._body_sidecars(allow_expensive=False):
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            if status not in {SidecarSupportStatus.SUPPLEMENT_RESOLVER.value, SidecarSupportStatus.SEARCH_METADATA.value}:
                continue
            candidate_tables = [table for table in sidecar.tables if table.block_column and table.offset_column]
            if not candidate_tables:
                continue
            sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
            try:
                con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
            except sqlite3.DatabaseError:
                continue
            try:
                for table in candidate_tables:
                    text_columns = self._sidecar_table_text_columns(table)
                    select_columns: list[str] = []
                    for column in (table.id_column, table.block_column, table.offset_column, *text_columns):
                        if column and column in table.columns and column not in select_columns:
                            select_columns.append(column)
                    quoted_columns = [quote_sql_identifier(column) for column in select_columns]
                    quoted = ", ".join(["rowid as __rowid", *quoted_columns])
                    sql = (
                        f"select {quoted} from {quote_sql_identifier(table.table)} "
                        f"where {quote_sql_identifier(table.block_column or '')}=? "
                        f"and {quote_sql_identifier(table.offset_column or '')}=? "
                        f"order by rowid limit ?"
                    )
                    try:
                        rows = con.execute(sql, (address.block, address.offset, max(1, limit - len(supplements)))).fetchall()
                    except sqlite3.DatabaseError:
                        continue
                    role = table.role.value if isinstance(table.role, SidecarRole) else str(table.role)
                    for row in rows:
                        values = {column: self._safe_sidecar_text(row[column]) for column in text_columns if column in row.keys()}
                        heading = values.get(table.title_column or "") or values.get("Title") or values.get("Midashi") or values.get("Keyword") or ""
                        text = (
                            values.get(table.plain_column or "")
                            or values.get(table.html_column or "")
                            or values.get("Body")
                            or values.get("h_text")
                            or heading
                        )
                        kind = self._sidecar_supplement_kind(table.role, table.table)
                        row_id = int(row["__rowid"])
                        supplement: dict[str, object] = {
                            "id": f"sidecar-{kind}-{len(supplements) + 1}",
                            "kind": kind,
                            "role": role,
                            "status": "address_matched",
                            "sidecar": sidecar.path.name,
                            "table": table.table,
                            "row_id": row_id,
                            "address": address.to_dict(),
                            "heading": strip_html(heading),
                            "text": strip_html(text),
                            "keyword": values.get("Keyword") or "",
                        }
                        if kind in {"link_reference", "sidecar_search"}:
                            label = str(supplement.get("heading") or supplement.get("text") or "reference")
                            supplement["link_target"] = {
                                "kind": "internal_address" if kind == "link_reference" else "sidecar_search",
                                "href": f"lvcore-entry://{address.block}/{address.offset}",
                                "status": "resolved",
                                "label": label,
                                "address": address.to_dict(),
                                "details": {
                                    "status": "address_matched",
                                    "source_sidecar": sidecar.path.name,
                                    "source_table": table.table,
                                    "row_id": row_id,
                                },
                            }
                        if debug:
                            supplement["debug"] = {
                                "storage": sidecar.storage,
                                "block_column": table.block_column,
                                "offset_column": table.offset_column,
                                "id_column": table.id_column,
                                "text_columns": list(text_columns),
                            }
                        supplements.append(supplement)
                        if len(supplements) >= limit:
                            return supplements
            finally:
                con.close()
        return supplements

    def _attach_sidecar_supplements(self, entry: Entry, *, include: bool = True) -> Entry:
        if not include:
            return entry
        supplements = tuple(self.sidecar_supplements(entry.address, debug=True))
        if not supplements:
            return entry
        return replace(entry, supplements=supplements)

    @staticmethod
    def _resource_kind_from_container(container_kind: str) -> ResourceKind:
        if container_kind == "image" or container_kind == "bitmap":
            return ResourceKind.IMAGE
        if container_kind == "audio":
            return ResourceKind.AUDIO
        return ResourceKind.MEDIA

    def sidecar_media_resources(self, *, limit: int | None = None) -> tuple[ResourceRef, ...]:
        """List package-level sidecar BLOB media resources with exact byte access."""

        resources: list[ResourceRef] = []
        for sidecar in self._body_sidecars():
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            if status != SidecarSupportStatus.RESOURCE_RESOLVER.value:
                continue
            candidate_tables = [table for table in sidecar.tables if table.blob_column]
            if not candidate_tables:
                continue
            sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
            try:
                con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
            except sqlite3.DatabaseError:
                continue
            try:
                for table in candidate_tables:
                    name_column = table.name_column or table.id_column
                    select_columns: list[str] = []
                    for column in (name_column, table.id_column):
                        if column and column in table.columns and column not in select_columns:
                            select_columns.append(column)
                    quoted_columns = [quote_sql_identifier(column) for column in select_columns]
                    quoted = ", ".join(["rowid as __rowid", *quoted_columns])
                    sql = (
                        f"select {quoted}, "
                        f"length({quote_sql_identifier(table.blob_column)}) as __blob_length, "
                        f"substr({quote_sql_identifier(table.blob_column)}, 1, 1024) as __blob_prefix "
                        f"from {quote_sql_identifier(table.table)} order by rowid"
                    )
                    if limit is not None:
                        sql += " limit ?"
                        params: tuple[object, ...] = (max(0, limit - len(resources)),)
                    else:
                        params = ()
                    try:
                        rows = con.execute(sql, params).fetchall()
                    except sqlite3.DatabaseError:
                        continue
                    for row in rows:
                        row_id = int(row["__rowid"])
                        name = self._safe_sidecar_text(row[name_column]) if name_column and name_column in row.keys() else ""
                        prefix = bytes(row["__blob_prefix"] or b"")
                        mime_type, format_hint, container_kind = _media_mime_and_format(prefix, store_kind="sidecar_media")
                        digest = hashlib.sha1(f"{sidecar.path.name}:{table.table}:{row_id}:{name}".encode("utf-8")).hexdigest()[:12]
                        resources.append(
                            ResourceRef(
                                id=f"sidecar-media-{digest}",
                                kind=self._resource_kind_from_container(container_kind),
                                label=name or f"{table.table}#{row_id}",
                                status="resolved",
                                mime_type=mime_type,
                                source_path=str(sidecar.path),
                                details={
                                    "reason": "sidecar_media_blob",
                                    "resolved": True,
                                    "sidecar_media": True,
                                    "store_kind": "sidecar_media",
                                    "sidecar": sidecar.path.name,
                                    "storage": sidecar.storage,
                                    "table": table.table,
                                    "row_id": row_id,
                                    "name": name,
                                    "id_column": table.id_column,
                                    "name_column": name_column,
                                    "blob_column": table.blob_column,
                                    "byte_length": int(row["__blob_length"] or 0),
                                    "format_hint": format_hint,
                                    "container_kind": container_kind,
                                },
                            )
                        )
                        if limit is not None and len(resources) >= limit:
                            return tuple(resources)
            finally:
                con.close()
        return tuple(resources)

    def sidecar_supplement_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "examples_idioms_rows_seen": 0,
            "examples_idioms_rows_attached": 0,
            "entry_supplements_attached": 0,
            "link_reference_rows_seen": 0,
            "link_reference_rows_matched": 0,
            "link_reference_targets_resolved": 0,
            "sidecar_search_rows_seen": 0,
            "sidecar_search_rows_supported": 0,
            "sidecar_search_rows_deferred": 0,
            "sidecar_media_rows_seen": 0,
            "sidecar_media_rows_resolved": 0,
            "sidecar_media_bytes_available": 0,
            "sidecar_media_mime_counts": {},
        }
        for sidecar in self._body_sidecars():
            role = sidecar.role.value if isinstance(sidecar.role, SidecarRole) else str(sidecar.role)
            status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
            for table in sidecar.tables:
                rows = int(table.row_count or 0)
                if role == SidecarRole.EXAMPLES_IDIOMS.value:
                    summary["examples_idioms_rows_seen"] = int(summary["examples_idioms_rows_seen"]) + rows
                elif role == SidecarRole.LINK_REFERENCE.value:
                    summary["link_reference_rows_seen"] = int(summary["link_reference_rows_seen"]) + rows
                elif role == SidecarRole.SEARCH.value:
                    summary["sidecar_search_rows_seen"] = int(summary["sidecar_search_rows_seen"]) + rows
                    if status == SidecarSupportStatus.SEARCH_METADATA.value:
                        summary["sidecar_search_rows_supported"] = int(summary["sidecar_search_rows_supported"]) + rows
                    else:
                        summary["sidecar_search_rows_deferred"] = int(summary["sidecar_search_rows_deferred"]) + rows
                elif role == SidecarRole.MEDIA_RESOURCE.value and table.blob_column:
                    summary["sidecar_media_rows_seen"] = int(summary["sidecar_media_rows_seen"]) + rows
        media_mime_counts = summary["sidecar_media_mime_counts"]
        for resource in self.sidecar_media_resources():
            info = self.resource_info(resource)
            if info.get("status") == "resolved":
                summary["sidecar_media_rows_resolved"] = int(summary["sidecar_media_rows_resolved"]) + 1
                summary["sidecar_media_bytes_available"] = int(summary["sidecar_media_bytes_available"]) + 1
                if isinstance(media_mime_counts, dict):
                    self._increment_reason(media_mime_counts, info.get("mime_type"))
        return summary

    def sidecar_references(self, address: Address, *, limit: int = 32, debug: bool = False) -> list[dict[str, object]]:
        """Return structural sidecar rows that point at an entry address.

        This is a read-only metadata resolver for supplemental sidecars such as
        example/idiom/search/navigation tables. It reports table relationships
        without returning dictionary text.
        """

        matches: list[dict[str, object]] = []
        for sidecar in self._body_sidecars(allow_expensive=False):
            candidate_tables = [table for table in sidecar.tables if table.block_column and table.offset_column]
            if not candidate_tables:
                continue
            sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
            try:
                con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
            except sqlite3.DatabaseError:
                continue
            try:
                for table in candidate_tables:
                    sql = (
                        f"select count(*) from {quote_sql_identifier(table.table)} "
                        f"where {quote_sql_identifier(table.block_column or '')}=? "
                        f"and {quote_sql_identifier(table.offset_column or '')}=?"
                    )
                    try:
                        count = int(con.execute(sql, (address.block, address.offset)).fetchone()[0])
                    except sqlite3.DatabaseError:
                        continue
                    if count <= 0:
                        continue
                    role = table.role.value if isinstance(table.role, SidecarRole) else str(table.role)
                    support_status = sidecar.support_status.value if isinstance(sidecar.support_status, SidecarSupportStatus) else str(sidecar.support_status)
                    row: dict[str, object] = {
                        "sidecar": sidecar.path.name,
                        "kind": sidecar.kind,
                        "role": role,
                        "support_status": support_status,
                        "table": table.table,
                        "match_count": count,
                        "status": "matched",
                    }
                    if debug:
                        row["block_column"] = table.block_column
                        row["offset_column"] = table.offset_column
                        row["title_column"] = table.title_column
                        row["plain_column"] = table.plain_column
                    matches.append(row)
                    if len(matches) >= limit:
                        return matches
            finally:
                con.close()
        return matches
