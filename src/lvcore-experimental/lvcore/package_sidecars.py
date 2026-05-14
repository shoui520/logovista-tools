"""SQLite sidecar discovery, role classification, and sidecar media."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from .body_source import (
    SQLITE_MAGIC,
    SidecarAddressMatch,
    SidecarInfo,
    SidecarRole,
    SidecarSupportStatus,
    SidecarTableInfo,
    classify_sqlite_table_role,
    classify_sqlite_sidecar_role,
    quote_sql_identifier,
    resolve_sqlite_sidecar_columns,
    sqlite_columns,
)
from .crypto import decrypt_logofont_file, decrypt_logofont_file_to_path, decrypt_logofont_prefix
from .document import ResourceKind, ResourceRef
from .model import Address
from .package_utils import _media_mime_and_format
from .ssed import read_file_prefix


class PackageSidecarMixin:
    """Sidecar discovery and supplemental resource methods for LogoVistaPackage."""

    def _sidecar_file_candidates(self) -> list[Path]:
        self._ensure_open()
        if self._sidecar_file_candidates_cache is not None:
            return list(self._sidecar_file_candidates_cache)
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
        self._sidecar_file_candidates_cache = candidates
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

    def _sqlite_connection_for_sidecar(self, path: Path, storage: str) -> sqlite3.Connection:
        key = (str(path), storage)
        cached = self._sqlite_connection_cache.get(key)
        if isinstance(cached, sqlite3.Connection):
            return cached
        if storage == "logofont_cipher" and hasattr(sqlite3.Connection, "deserialize"):
            data = decrypt_logofont_file(path)
            con = sqlite3.connect(":memory:")
            con.deserialize(data)
        else:
            sqlite_path = self._sqlite_path_for_sidecar(path, storage)
            con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        self._sqlite_connection_cache[key] = con
        return con

    @staticmethod
    def _row_count(con: sqlite3.Connection, table: str) -> int | None:
        try:
            return int(con.execute(f"select count(*) from {quote_sql_identifier(table)}").fetchone()[0])
        except sqlite3.DatabaseError:
            return None

    def _sidecar_table_info(self, con: sqlite3.Connection, table: str, *, include_row_count: bool = True) -> SidecarTableInfo:
        columns = sqlite_columns(con, table)
        resolved = resolve_sqlite_sidecar_columns(columns)
        role = classify_sqlite_table_role(table, columns)
        return SidecarTableInfo(
            table=table,
            columns=tuple(columns),
            row_count=self._row_count(con, table) if include_row_count else None,
            role=role,
            id_column=resolved["id"],
            title_column=resolved["title"],
            html_column=resolved["html"],
            plain_column=resolved["plain"],
            blob_column=resolved["blob"],
            name_column=resolved["name"],
            block_column=resolved["block"],
            offset_column=resolved["offset"],
            end_block_column=resolved["end_block"],
            end_offset_column=resolved["end_offset"],
        )

    @staticmethod
    def _body_sidecar_kind(table: SidecarTableInfo) -> str:
        table_lower = table.table.lower()
        if table_lower == "honbun":
            return "honbun"
        if table_lower == "main":
            return "main_wordlist"
        if table_lower == "t_contents":
            return "t_contents"
        return "sqlite_body"

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
        try:
            con = self._sqlite_connection_for_sidecar(path, storage)
        except sqlite3.DatabaseError:
            self._sqlite_schema_cache[key] = None
            return None
        tables = [row[0] for row in con.execute("select name from sqlite_master where type='table' order by name")]
        table_infos = tuple(self._sidecar_table_info(con, table, include_row_count=include_row_counts) for table in tables)
        columns_by_table = {info.table: list(info.columns) for info in table_infos}
        for table_info in table_infos:
            table_role = table_info.role.value if isinstance(table_info.role, SidecarRole) else str(table_info.role)
            if table_role != SidecarRole.BODY_CRITICAL.value:
                continue
            if not table_info.id_column or not (table_info.html_column or table_info.plain_column or table_info.title_column):
                continue
            kind = self._body_sidecar_kind(table_info)
            info = SidecarInfo(
                path=path,
                kind=kind,
                storage=storage,
                role=classify_sqlite_sidecar_role(kind, tables, columns_by_table),
                support_status=SidecarSupportStatus.BODY_RESOLVER,
                table=table_info.table,
                id_column=table_info.id_column,
                title_column=table_info.title_column,
                html_column=table_info.html_column,
                plain_column=table_info.plain_column,
                row_count=table_info.row_count,
                tables=tuple(
                    replace(item, role=SidecarRole.BODY_CRITICAL) if item.table == table_info.table else item
                    for item in table_infos
                ),
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
            try:
                con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
            except sqlite3.DatabaseError:
                continue
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
        return tuple(resources)

    def sidecar_address_matches(self, address: Address, *, limit: int = 32) -> tuple[SidecarAddressMatch, ...]:
        """Return structural sidecar rows that point at an entry address."""

        matches: list[SidecarAddressMatch] = []
        for sidecar in self._body_sidecars(allow_expensive=False):
            candidate_tables = [table for table in sidecar.tables if table.block_column and table.offset_column]
            if not candidate_tables:
                continue
            try:
                con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
            except sqlite3.DatabaseError:
                continue
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
                matches.append(
                    SidecarAddressMatch(
                        sidecar_name=sidecar.path.name,
                        kind=sidecar.kind,
                        role=role,
                        support_status=support_status,
                        table=table.table,
                        match_count=count,
                        block_column=table.block_column,
                        offset_column=table.offset_column,
                        title_column=table.title_column,
                        plain_column=table.plain_column,
                    )
                )
                if len(matches) >= limit:
                    return tuple(matches)
        return tuple(matches)
