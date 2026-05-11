"""High-level package API."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterable

from .body_source import (
    BodyPointerInspection,
    BodySourceInfo,
    BodySourceSupport,
    Confidence,
    SQLITE_MAGIC,
    SidecarBody,
    SidecarInfo,
    SsedBodySourceKind,
    find_column,
    sqlite_columns,
    strip_html,
)
from .crypto import decrypt_logofont, decrypt_logofont_prefix
from .detect import detect_family
from .diagnostics import Diagnostic, DiagnosticArea, Location, Severity
from .errors import UnsupportedPackageError
from .gaiji import Ga16Resource, GaijiMap, load_gaiji_map, parse_ga16
from .index import IndexRow
from .index import IndexParse, parse_index
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile, Span
from .render import HtmlProfile, render_html, render_text
from .search import SearchHit, SearchResults, natural_backward_key, normalize_query, query_candidates
from .ssed import BLOCK_SIZE, CHUNK_SIZE, Catalog, SsedData, find_file_case_insensitive, parse_catalog
from .text import DecodeResult, decode_text_stream


ENTRY_MARKER = b"\x1f\x09\x00\x01"


def open_package(path: str | Path) -> "LogoVistaPackage":
    info = detect_family(Path(path))
    if info.family != PackageFamily.SSED:
        raise UnsupportedPackageError(f"{info.family.value} package support is deferred")
    if info.idx_path is None:
        raise UnsupportedPackageError("SSED package did not expose an IDX path")
    return LogoVistaPackage(info.idx_path)


class LogoVistaPackage:
    """Reader/parser API for one SSED package."""

    def __init__(self, idx_path: Path):
        self.catalog: Catalog = parse_catalog(idx_path)
        self.info = PackageInfo(
            family=PackageFamily.SSED,
            root=idx_path.parent,
            idx_path=idx_path,
            dict_id=idx_path.stem,
            title=self.catalog.title,
        )
        self.gaiji: GaijiMap = load_gaiji_map(idx_path.parent, idx_path.stem)
        self.components: tuple[Component, ...] = tuple(
            replace(component, path=find_file_case_insensitive(idx_path.parent, component.name))
            for component in self.catalog.components
        )
        self.ga16: tuple[Ga16Resource, ...] = tuple(
            resource
            for component in self.components
            if component.role == ComponentRole.GAIJI and component.path is not None
            for resource in [parse_ga16(component.path)]
            if resource is not None
        )
        self._component_by_name = {component.name.lower(): component for component in self.components}
        self._data_cache: dict[str, SsedData] = {}
        self._index_cache: dict[str, IndexParse] = {}
        self._marker_cache: dict[str, list[int]] = {}
        self._body_pointer_cache: dict[str, list[int]] = {}
        self._body_source_cache: BodySourceInfo | None = None
        self._sqlite_sidecar_cache: dict[str, Path] = {}
        self._sqlite_schema_cache: dict[str, SidecarInfo | None] = {}
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        tempdir = getattr(self, "_tempdir", None)
        if tempdir is not None:
            try:
                tempdir.cleanup()
            except Exception:
                pass

    @property
    def title(self) -> str:
        return self.catalog.title

    @property
    def dict_id(self) -> str:
        return self.catalog.dict_id

    def package_family(self) -> PackageFamily:
        return self.info.family

    def component(self, name: str) -> Component | None:
        return self._component_by_name.get(name.lower())

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
        return tuple(component for component in self.components if component.role == role)

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None:
        if address.component:
            component = self.component(address.component)
            if component is not None:
                return component
        candidates = self.components_by_role(role) if role is not None else self.components
        for component in candidates:
            if component.start_block <= address.block <= component.end_block:
                return component
        return None

    def data(self, component: str | Component) -> SsedData:
        name = component.name if isinstance(component, Component) else component
        key = name.lower()
        if key not in self._data_cache:
            item = self.component(name)
            if item is None:
                raise KeyError(name)
            if item.path is None:
                raise FileNotFoundError(name)
            self._data_cache[key] = SsedData(item.path)
        return self._data_cache[key]

    def expanded(self, component: str | Component) -> bytes:
        return self.data(component).expand()

    def decode_component(self, component: str | Component) -> DecodeResult:
        return decode_text_stream(self.expanded(component), self.gaiji.mapping)

    def read_address(self, address: Address, size: int, *, role: ComponentRole | None = None) -> bytes:
        component = self.component_for_address(address, role=role)
        if component is None:
            raise KeyError(f"no component contains address {address}")
        offset = (address.block - component.start_block) * BLOCK_SIZE + address.offset
        return self.data(component).read(offset, size)

    @staticmethod
    def _relative_offset(component: Component, address: Address) -> int:
        return (address.block - component.start_block) * BLOCK_SIZE + address.offset

    def _qualified_address(self, address: Address, *, role: ComponentRole | None = None) -> Address:
        if address.component:
            return address
        component = self.component_for_address(address, role=role)
        if component is None:
            return address
        return Address(address.block, address.offset, component.name)

    def _location_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Location:
        qualified = self._qualified_address(address, role=role)
        return Location(component=qualified.component, block=qualified.block, offset=qualified.offset)

    def honmon_component(self) -> Component | None:
        candidates = self.components_by_role(ComponentRole.HONMON)
        return candidates[0] if candidates else None

    def _sidecar_file_candidates(self) -> list[Path]:
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

    @staticmethod
    def _sqlite_storage(path: Path) -> str | None:
        try:
            raw = path.read_bytes()[:2048]
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
        decrypted.write_bytes(decrypt_logofont(path.read_bytes()))
        self._sqlite_sidecar_cache[key] = decrypted
        return decrypted

    @staticmethod
    def _row_count(con: sqlite3.Connection, table: str) -> int | None:
        try:
            return int(con.execute(f'select count(*) from "{table}"').fetchone()[0])
        except sqlite3.DatabaseError:
            return None

    def _inspect_sqlite_sidecar(self, path: Path) -> SidecarInfo | None:
        key = str(path)
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
            for table in ("t_contents", "HONBUN", "main"):
                if table not in tables:
                    continue
                columns = sqlite_columns(con, table)
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
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=html_col,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table),
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
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=None,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table),
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
                            table=table,
                            id_column=id_col,
                            title_column=title_col,
                            html_column=html_col,
                            plain_column=plain_col,
                            row_count=self._row_count(con, table),
                        )
                        self._sqlite_schema_cache[key] = info
                        return info
            self._sqlite_schema_cache[key] = SidecarInfo(path=path, kind="sqlite_unmapped", storage=storage, notes=tuple(tables[:8]))
            return self._sqlite_schema_cache[key]
        finally:
            con.close()

    def _body_sidecars(self) -> tuple[SidecarInfo, ...]:
        rows: list[SidecarInfo] = []
        for path in self._sidecar_file_candidates():
            sidecar = self._inspect_sqlite_sidecar(path)
            if sidecar is not None:
                rows.append(sidecar)
        return tuple(rows)

    @staticmethod
    def _marker_offsets(reader: SsedData, *, limit: int | None = None) -> list[int]:
        offsets: list[int] = []
        tail = b""
        tail_base = 0
        for chunk_index in range(len(reader.offsets)):
            chunk = reader.chunk(chunk_index)
            if not chunk:
                continue
            chunk_base = chunk_index * CHUNK_SIZE
            data = tail + chunk
            data_base = tail_base
            search = 0
            while True:
                found = data.find(ENTRY_MARKER, search)
                if found < 0:
                    break
                absolute = data_base + found
                if not offsets or absolute != offsets[-1]:
                    offsets.append(absolute)
                search = found + 1
                if limit is not None and len(offsets) >= limit + 1:
                    return offsets
            keep = max(0, len(ENTRY_MARKER) - 1)
            tail = data[-keep:] if keep else b""
            tail_base = chunk_base + len(chunk) - len(tail)
        return offsets

    def _markers_for_component(self, component: Component, *, limit: int | None = None) -> list[int]:
        key = component.name.lower()
        if limit is not None and key not in self._marker_cache:
            return self._marker_offsets(self.data(component), limit=limit)
        if key not in self._marker_cache:
            self._marker_cache[key] = self._marker_offsets(self.data(component))
        return self._marker_cache[key]

    def iter_entry_slices(self, *, limit: int | None = None) -> Iterable[tuple[int, int]]:
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            return
        reader = self.data(honmon)
        starts = self._markers_for_component(honmon, limit=limit)
        if not starts:
            sample = reader.read(0, min(reader.expanded_size, BLOCK_SIZE))
            if sample.strip(b"\x00"):
                yield 0, reader.expanded_size
            return
        for index, start in enumerate(starts[:limit]):
            end = starts[index + 1] if index + 1 < len(starts) else reader.expanded_size
            yield start, end

    def iter_entries(self, *, limit: int | None = None) -> Iterable[Entry]:
        honmon = self.honmon_component()
        if honmon is None:
            return
        reader = self.data(honmon)
        count = 0
        for start, end in self.iter_entry_slices(limit=limit):
            decoded = decode_text_stream(reader.read(start, end - start), self.gaiji.mapping)
            text = decoded.text.strip("\x00")
            head = ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                head = lines[0]
            yield Entry(
                address=Address(honmon.start_block + start // 2048, start % 2048, honmon.name),
                end_address=Address(honmon.start_block + end // 2048, end % 2048, honmon.name),
                headword=head,
                text=text,
                spans=decoded.spans,
            )
            count += 1
            if limit is not None and count >= limit:
                break

    def _body_pointer_offsets(self, component: Component) -> list[int]:
        key = component.name.lower()
        if key in self._body_pointer_cache:
            return self._body_pointer_cache[key]
        offsets: set[int] = set()
        for parsed in self.indexes().values():
            for row in parsed.rows:
                for address in (row.body,):
                    target = self.component_for_address(address, role=ComponentRole.HONMON)
                    if target is None or target.name.lower() != key:
                        continue
                    offset = self._relative_offset(target, address)
                    if 0 <= offset < self.data(target).expanded_size:
                        offsets.add(offset)
        self._body_pointer_cache[key] = sorted(offsets)
        return self._body_pointer_cache[key]

    def _decode_anchor_at(self, component: Component, start: int, *, max_bytes: int = 96) -> tuple[str, int]:
        reader = self.data(component)
        data = reader.read(start, min(max_bytes, max(reader.expanded_size - start, 0)))
        next_marker = data.find(ENTRY_MARKER, 1)
        if next_marker > 0:
            data = data[:next_marker]
        decoded = decode_text_stream(data, self.gaiji.mapping)
        compact = "".join(ch for ch in decoded.text if not ch.isspace() and ch != "\x00")
        if compact.isdigit() and 4 <= len(compact) <= 16:
            return compact, len(data)
        return "", len(data)

    def _dense_anchor_evidence(self, component: Component) -> dict[str, object]:
        pointer_offsets = self._body_pointer_offsets(component)
        sample_offsets = pointer_offsets[:64]
        if not sample_offsets:
            sample_offsets = self._markers_for_component(component, limit=64)[:64]
        ids: list[str] = []
        lengths: list[int] = []
        for offset in sample_offsets:
            anchor_id, size = self._decode_anchor_at(component, offset)
            if anchor_id:
                ids.append(anchor_id)
            lengths.append(size)
        numeric_ratio = len(ids) / len(sample_offsets) if sample_offsets else 0.0
        common_gap = None
        unique_offsets = sorted(set(pointer_offsets[:4096]))
        gaps = [b - a for a, b in zip(unique_offsets, unique_offsets[1:]) if b > a]
        if gaps:
            counts: dict[int, int] = {}
            for gap in gaps:
                counts[gap] = counts.get(gap, 0) + 1
            common_gap = max(counts.items(), key=lambda item: (item[1], -item[0]))[0]
        dense_record_size = common_gap if common_gap in {16, 32, 40, 48, 64, 80, 96, 112, 128, 160, 320} else None
        if dense_record_size is None and lengths:
            median = int(sorted(lengths)[len(lengths) // 2])
            if median <= 160:
                dense_record_size = median
        return {
            "anchor_ids": ids,
            "numeric_ratio": numeric_ratio,
            "sample_count": len(sample_offsets),
            "common_gap": common_gap,
            "dense_record_size": dense_record_size,
        }

    def _choose_body_sidecar(self, sidecars: tuple[SidecarInfo, ...]) -> SidecarInfo | None:
        renderable = [sidecar for sidecar in sidecars if sidecar.table and sidecar.id_column and (sidecar.html_column or sidecar.plain_column)]
        if not renderable:
            return None
        for sidecar in renderable:
            lower = sidecar.path.name.lower()
            if lower.startswith("vlpljbl") and sidecar.kind in {"t_contents", "honbun"}:
                return sidecar
        return renderable[0]

    def body_source(self) -> BodySourceInfo:
        if self._body_source_cache is not None:
            return self._body_source_cache
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            self._body_source_cache = BodySourceInfo(
                package_family=self.info.family,
                support=BodySourceSupport.UNSUPPORTED,
                confidence=Confidence.PROVEN,
                notes=("missing HONMON component",),
            )
            return self._body_source_cache
        reader = self.data(honmon)
        marker_count = len(self._markers_for_component(honmon, limit=2000000))
        marker_density = marker_count / max(reader.expanded_size, 1)
        sidecar_paths = tuple(self._sidecar_file_candidates())
        evidence = self._dense_anchor_evidence(honmon)
        numeric_ratio = float(evidence["numeric_ratio"])
        is_dense = numeric_ratio >= 0.6 and int(evidence["sample_count"]) >= 4
        sidecars = self._body_sidecars() if is_dense else ()
        chosen_sidecar = self._choose_body_sidecar(sidecars)

        if is_dense:
            if chosen_sidecar is not None:
                if chosen_sidecar.kind == "honbun":
                    kind = SsedBodySourceKind.HONBUN_SIDECAR
                elif chosen_sidecar.path.name.lower().startswith("vlpljbl"):
                    kind = SsedBodySourceKind.RENDERER_SQLITE_SIDECAR
                else:
                    kind = SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR
                support = BodySourceSupport.PARTIALLY_RENDERABLE
                provider = "sqlite_sidecar"
                sidecar_kind = chosen_sidecar.kind
                notes = ("HONMON body pointers resolve to short numeric anchor records; selected SQLite sidecar body provider",)
            elif sidecars:
                kind = SsedBodySourceKind.SIDECAR_UNKNOWN
                support = BodySourceSupport.DEFERRED
                provider = "sqlite_sidecar_deferred"
                sidecar_kind = ",".join(dict.fromkeys(sidecar.kind for sidecar in sidecars))
                notes = ("HONMON body pointers resolve to short numeric anchor records; sidecar files were found but no supported body table schema was identified",)
            else:
                kind = SsedBodySourceKind.DENSE_ANCHOR_TABLE
                support = BodySourceSupport.DEFERRED
                provider = "dense_anchor_deferred"
                sidecar_kind = None
                notes = ("HONMON body pointers resolve to short numeric anchor records; no supported body sidecar found",)
        else:
            kind = SsedBodySourceKind.BODY_STREAM
            support = BodySourceSupport.RENDERABLE
            provider = "honmon_body_stream"
            sidecar_kind = None
            notes = ("HONMON body pointers resolve directly into readable body-stream data",)

        self._body_source_cache = BodySourceInfo(
            package_family=self.info.family,
            ssed_kind=kind,
            support=support,
            confidence=Confidence.INFERRED if is_dense else Confidence.PROVEN,
            honmon_component=honmon.name,
            expanded_size=reader.expanded_size,
            marker_count=marker_count,
            marker_density=round(marker_density, 8),
            dense_record_size=evidence.get("dense_record_size") if is_dense else None,
            anchor_count=len(evidence.get("anchor_ids") or ()) if is_dense else None,
            sidecar_paths=sidecar_paths,
            sidecar_kind=sidecar_kind,
            render_provider=provider,
            notes=notes,
            sidecars=sidecars,
        )
        return self._body_source_cache

    def validate_body_source(self) -> BodySourceInfo:
        return self.body_source()

    def supports_entry_rendering(self) -> bool:
        return self.body_source().support in {BodySourceSupport.RENDERABLE, BodySourceSupport.PARTIALLY_RENDERABLE}

    @staticmethod
    def _next_after(sorted_offsets: list[int], start: int) -> int | None:
        lo = 0
        hi = len(sorted_offsets)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_offsets[mid] <= start:
                lo = mid + 1
            else:
                hi = mid
        return sorted_offsets[lo] if lo < len(sorted_offsets) else None

    def _entry_range_for_address(self, address: Address, *, max_bytes: int = 512 * 1024) -> tuple[Component, int, int, tuple[Diagnostic, ...]]:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None:
            raise KeyError(f"no HONMON component contains address {address}")
        reader = self.data(honmon)
        start = self._relative_offset(honmon, address)
        if start < 0 or start >= reader.expanded_size:
            raise ValueError(f"entry address outside HONMON bounds: {address}")

        candidates: list[tuple[int, str]] = []
        next_pointer = self._next_after(self._body_pointer_offsets(honmon), start)
        if next_pointer is not None:
            candidates.append((next_pointer, "next_body_pointer"))
        if next_pointer is None:
            next_marker = self._next_after(self._markers_for_component(honmon), start)
            if next_marker is not None:
                candidates.append((next_marker, "next_marker"))
        candidates.append((reader.expanded_size, "component_end"))

        fallback_end = min(reader.expanded_size, start + max_bytes)
        if fallback_end < reader.expanded_size:
            candidates.append((fallback_end, "max_bytes_fallback"))

        end, source = min((candidate for candidate in candidates if candidate[0] > start), key=lambda item: item[0])
        diagnostics: list[Diagnostic] = []
        if source == "max_bytes_fallback":
            diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    area=DiagnosticArea.BODY,
                    code="entry_range_fallback",
                    message="entry end was limited by max_bytes fallback",
                    location=self._location_for_address(address, role=ComponentRole.HONMON),
                    details={"max_bytes": max_bytes},
                )
            )
        return honmon, start, end, tuple(diagnostics)

    def entry_at(self, address: Address, *, max_bytes: int = 512 * 1024) -> Entry:
        honmon, start, end_offset, diagnostics = self._entry_range_for_address(address, max_bytes=max_bytes)
        reader = self.data(honmon)
        decoded = decode_text_stream(reader.read(start, end_offset - start), self.gaiji.mapping)
        lines = [line.strip() for line in decoded.text.splitlines() if line.strip()]
        headword = lines[0] if lines else ""
        return Entry(
            address=Address(address.block, address.offset, honmon.name),
            end_address=Address(honmon.start_block + end_offset // BLOCK_SIZE, end_offset % BLOCK_SIZE, honmon.name),
            headword=headword,
            text=decoded.text.strip("\x00"),
            spans=decoded.spans,
            entry_diagnostics=diagnostics,
        )

    def inspect_body_pointer(self, address: Address) -> BodyPointerInspection:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None:
            return BodyPointerInspection(
                diagnostics=(
                    Diagnostic(
                        severity=Severity.ERROR,
                        area=DiagnosticArea.BODY,
                        code="body_pointer_outside_honmon",
                        message="body pointer does not resolve to a HONMON component",
                        location=self._location_for_address(address, role=ComponentRole.HONMON),
                    ),
                )
            )
        start = self._relative_offset(honmon, address)
        anchor_id, length = self._decode_anchor_at(honmon, start)
        raw_text_hash = hashlib.sha256(anchor_id.encode("utf-8")).hexdigest()[:16] if anchor_id else None
        return BodyPointerInspection(anchor_id=anchor_id or None, raw_text_hash=raw_text_hash, raw_text_length=len(anchor_id), record_offset=start, record_length=length)

    @staticmethod
    def _anchor_query_values(anchor_id: str, sidecar: SidecarInfo) -> tuple[object, ...]:
        values: list[object] = [anchor_id]
        stripped = anchor_id.lstrip("0") or "0"
        if stripped != anchor_id:
            values.append(stripped)
        if sidecar.kind != "honbun":
            try:
                values.append(int(stripped))
            except ValueError:
                pass
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _sidecar_debug_details(sidecar: SidecarInfo, anchor_id: str) -> dict[str, object]:
        query_values = [str(value) for value in LogoVistaPackage._anchor_query_values(anchor_id, sidecar)]
        return {
            "anchor_id": anchor_id,
            "query_values": query_values,
            "sidecar": sidecar.path.name,
            "sidecar_kind": sidecar.kind,
            "storage": sidecar.storage,
            "table": sidecar.table,
            "id_column": sidecar.id_column,
            "title_column": sidecar.title_column,
            "html_column": sidecar.html_column,
            "plain_column": sidecar.plain_column,
        }

    def _fetch_sidecar_body(self, sidecar: SidecarInfo, anchor_id: str) -> SidecarBody | None:
        if not sidecar.table or not sidecar.id_column:
            return None
        sqlite_path = self._sqlite_path_for_sidecar(sidecar.path, sidecar.storage)
        try:
            con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
        except sqlite3.DatabaseError:
            return None
        try:
            select_columns = [sidecar.id_column]
            for column in (sidecar.title_column, sidecar.html_column, sidecar.plain_column):
                if column and column not in select_columns:
                    select_columns.append(column)
            quoted = ", ".join(f'"{column}"' for column in select_columns)
            sql = f'select {quoted} from "{sidecar.table}" where "{sidecar.id_column}"=? limit 1'
            row = None
            for value in self._anchor_query_values(anchor_id, sidecar):
                row = con.execute(sql, (value,)).fetchone()
                if row is not None:
                    break
            if row is None:
                return None
            title = str(row[sidecar.title_column]) if sidecar.title_column and row[sidecar.title_column] is not None else ""
            html_value = str(row[sidecar.html_column]) if sidecar.html_column and row[sidecar.html_column] is not None else ""
            plain_value = str(row[sidecar.plain_column]) if sidecar.plain_column and row[sidecar.plain_column] is not None else ""
            text = plain_value.strip() or strip_html(html_value) or title.strip()
            return SidecarBody(title=strip_html(title), text=text, html=html_value or None, source=sidecar)
        finally:
            con.close()

    def _placeholder_entry(
        self,
        address: Address,
        *,
        headword: str,
        code: str,
        message: str,
        severity: Severity = Severity.WARNING,
        details: dict[str, object] | None = None,
    ) -> Entry:
        placeholder = "Entry body is not yet supported for this LogoVista body source."
        diagnostic = Diagnostic(
            severity=severity,
            area=DiagnosticArea.BODY,
            code=code,
            message=message,
            location=self._location_for_address(address, role=ComponentRole.HONMON),
            details=details or {},
        )
        qualified = self._qualified_address(address, role=ComponentRole.HONMON)
        return Entry(
            address=qualified,
            end_address=qualified,
            headword=headword,
            text=placeholder,
            spans=(Span(kind="text", text=placeholder),),
            entry_diagnostics=(diagnostic,),
        )

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        anchor_id = inspection.anchor_id
        if not anchor_id:
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="dense_anchor_missing_id",
                message="dense HONMON record did not expose a numeric anchor id",
                severity=Severity.ERROR,
            )
        body = self._fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="sidecar_body_not_found",
                message="body sidecar did not contain a row for the dense HONMON anchor",
                severity=Severity.ERROR,
                details=self._sidecar_debug_details(sidecar, anchor_id),
            )
        note = Diagnostic(
            severity=Severity.INFO,
            area=DiagnosticArea.BODY,
            code="sidecar_body_resolved",
            message="entry body resolved from SSED sidecar database",
            location=self._location_for_address(hit.body, role=ComponentRole.HONMON),
            details=self._sidecar_debug_details(sidecar, anchor_id),
        )
        text = body.text or body.title or hit.heading
        return Entry(
            address=hit.body,
            end_address=hit.body,
            headword=body.title or hit.heading,
            text=text,
            spans=(Span(kind="text", text=text),),
            entry_diagnostics=(note,),
        )

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        comps = [component] if component is not None else list(self.components_by_role(ComponentRole.TITLE))
        out: list[str] = []
        for comp in comps:
            decoded = self.decode_component(comp)
            for line in decoded.text.splitlines():
                line = line.strip()
                if line:
                    out.append(line)
                    if limit is not None and len(out) >= limit:
                        return out
        return out

    def resolve_title(self, address: Address, *, max_bytes: int = 4096) -> tuple[str, tuple[Diagnostic, ...]]:
        component = self.component_for_address(address, role=ComponentRole.TITLE)
        if component is None:
            return "", (
                Diagnostic(
                    severity=Severity.WARNING,
                    area=DiagnosticArea.INDEX,
                    code="title_dereference_failed",
                    message="no title component contains the title pointer",
                    location=self._location_for_address(address, role=ComponentRole.TITLE),
                ),
            )
        reader = self.data(component)
        start = self._relative_offset(component, address)
        if start < 0 or start >= reader.expanded_size:
            return "", (
                Diagnostic(
                    severity=Severity.WARNING,
                    area=DiagnosticArea.INDEX,
                    code="title_dereference_failed",
                    message="title pointer is outside component bounds",
                    location=self._location_for_address(address, role=ComponentRole.TITLE),
                ),
            )
        data = reader.read(start, min(max_bytes, reader.expanded_size - start))
        if not data:
            return "", ()
        end_candidates = [
            pos
            for marker in (b"\x1f\x0a", b"\x0a")
            for pos in [data.find(marker)]
            if pos >= 0
        ]
        end = min(end_candidates) if end_candidates else len(data)
        decoded = decode_text_stream(data[:end], self.gaiji.mapping)
        title = " ".join(line.strip() for line in decoded.text.splitlines() if line.strip()).strip("\x00 ")
        if title:
            return title, ()
        return "", (
            Diagnostic(
                severity=Severity.WARNING,
                area=DiagnosticArea.INDEX,
                code="title_dereference_empty",
                message="title pointer decoded to an empty title",
                location=self._location_for_address(address, role=ComponentRole.TITLE),
            ),
        )

    def indexes(self, component: str | Component | None = None) -> dict[str, IndexParse]:
        comps = [component] if component is not None else list(self.components_by_role(ComponentRole.INDEX))
        out: dict[str, IndexParse] = {}
        for comp in comps:
            item = self.component(comp) if isinstance(comp, str) else comp
            if item is None or item.path is None:
                continue
            key = item.name.lower()
            if key not in self._index_cache:
                self._index_cache[key] = parse_index(self.expanded(item), item.start_block, item.type, self.gaiji.mapping)
            out[item.name] = self._index_cache[key]
        return out

    @staticmethod
    def _index_component_matches_profile(component: Component, profile: SearchProfile) -> bool:
        name = component.name.upper()
        if profile in {SearchProfile.NATIVE, SearchProfile.EXACT}:
            return True
        if profile == SearchProfile.FORWARD:
            return name.startswith(("FK", "FH", "KW"))
        if profile == SearchProfile.BACKWARD:
            return name.startswith(("BK", "BH"))
        return True

    @staticmethod
    def _is_backward_index(component_name: str) -> bool:
        return component_name.upper().startswith(("BK", "BH"))

    @staticmethod
    def _row_display_key(row: IndexRow, *, backward: bool = False) -> str:
        value = row.target_key or row.key
        return natural_backward_key(value) if backward else value

    @staticmethod
    def _row_dedupe_key(row: IndexRow) -> tuple[int, int, int, int]:
        return (row.body.block, row.body.offset, row.title.block, row.title.offset)

    def _row_matches(self, row: IndexRow, query: str, candidates: tuple[str, ...], profile: SearchProfile, *, backward: bool) -> str | None:
        row_values = [row.key]
        if row.target_key and row.target_key != row.key:
            row_values.append(row.target_key)
        normalized_query = normalize_query(query)
        normalized_candidates = {normalize_query(candidate) for candidate in candidates if candidate}

        if profile == SearchProfile.EXACT:
            for value in row_values:
                if value in candidates or normalize_query(value) in normalized_candidates:
                    return value
            return None

        if profile == SearchProfile.FORWARD:
            for value in row_values:
                normalized_value = normalize_query(value)
                if normalized_value.startswith(normalized_query) or any(value.startswith(candidate) for candidate in candidates):
                    return value
            return None

        if profile == SearchProfile.BACKWARD:
            reversed_query = normalized_query[::-1]
            for value in row_values:
                normalized_value = normalize_query(value)
                natural_value = normalize_query(natural_backward_key(value)) if backward else normalized_value
                if normalized_value.startswith(reversed_query) or natural_value.endswith(normalized_query):
                    return value
            return None

        return None

    def _iter_matching_rows(
        self,
        query: str,
        profile: SearchProfile,
    ) -> Iterable[tuple[str, Component, IndexRow, str]]:
        candidates = query_candidates(query)
        for name, parsed in self.indexes().items():
            component = self.component(name)
            if component is None or not self._index_component_matches_profile(component, profile):
                continue
            backward = self._is_backward_index(name)
            for row in parsed.rows:
                matched = self._row_matches(row, query, candidates, profile, backward=backward)
                if matched is not None:
                    yield name, component, row, matched

    def _make_hit(
        self,
        *,
        hit_id: int,
        query: str,
        normalized_query: str,
        profile: SearchProfile,
        component_name: str,
        row: IndexRow,
        matched_key: str,
    ) -> SearchHit:
        backward = self._is_backward_index(component_name)
        display_key = self._row_display_key(row, backward=backward)
        body = self._qualified_address(row.body, role=ComponentRole.HONMON)
        title = self._qualified_address(row.title, role=ComponentRole.TITLE)
        title_text, diagnostics = self.resolve_title(title)
        heading = title_text or display_key or matched_key
        return SearchHit(
            id=hit_id,
            query=query,
            normalized_query=normalized_query,
            search_profile=profile,
            package_id=self.info.dict_id,
            index_component=component_name,
            display_key=display_key,
            matched_key=matched_key,
            target_key=row.target_key,
            heading=heading,
            body=body,
            title=title,
            tagged=row.tagged,
            diagnostics=diagnostics,
            page=row.page,
            row=row.row,
            raw_row=row,
            body_source=self.body_source().to_dict(debug=False),
            _package=self,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
        debug: bool = False,
    ) -> SearchResults:
        if isinstance(profile, str):
            profile = SearchProfile(profile)
        normalized_query = normalize_query(query)
        if not normalized_query and not str(query or "").strip():
            return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=())

        profiles = (SearchProfile.EXACT, SearchProfile.FORWARD, SearchProfile.BACKWARD) if profile == SearchProfile.NATIVE else (profile,)
        hits: list[SearchHit] = []
        seen: set[tuple[int, int, int, int]] = set()
        for effective_profile in profiles:
            for name, _component, row, matched_key in self._iter_matching_rows(query, effective_profile):
                dedupe_key = self._row_dedupe_key(row)
                if not debug and dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                hits.append(
                    self._make_hit(
                        hit_id=len(hits) + 1,
                        query=query,
                        normalized_query=normalized_query,
                        profile=effective_profile if profile == SearchProfile.NATIVE else profile,
                        component_name=name,
                        row=row,
                        matched_key=matched_key,
                    )
                )
                if len(hits) >= limit:
                    return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))
        return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))

    def search_index(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[dict[str, object]]:
        return [
            {
                "component": hit.index_component,
                **(hit.raw_row.to_dict() if hit.raw_row is not None else {}),
                "heading": hit.heading,
                "display_key": hit.display_key,
            }
            for hit in self.search(term, limit=limit, profile=profile).hits
        ]

    def search_entries(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[Entry]:
        out: list[Entry] = []
        for hit in self.search(term, limit=limit, profile=profile).hits:
            try:
                out.append(self.entry_for_hit(hit))
            except Exception:
                continue
        return out

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        source = self.body_source()
        if source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            return self.entry_at(hit.body)
        if source.ssed_kind in {
            SsedBodySourceKind.DENSE_ANCHOR_TABLE,
            SsedBodySourceKind.DENSE_MARKER_TABLE,
            SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR,
            SsedBodySourceKind.RENDERER_SQLITE_SIDECAR,
            SsedBodySourceKind.DICTFULLDB_SIDECAR,
            SsedBodySourceKind.HONBUN_SIDECAR,
            SsedBodySourceKind.VLPLJBL_SIDECAR,
            SsedBodySourceKind.SIDECAR_UNKNOWN,
        }:
            inspection = self.inspect_body_pointer(hit.body)
            sidecar = self._choose_body_sidecar(source.sidecars)
            if sidecar is not None:
                return self._entry_from_sidecar(hit, sidecar, inspection)
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="unsupported_body_source",
                message="SSED dense HONMON body source is not renderable without a supported sidecar",
                severity=Severity.ERROR,
                details={"body_source": source.ssed_kind.value},
            )
        return self._placeholder_entry(
            hit.body,
            headword=hit.heading,
            code="unsupported_body_source",
            message="entry body source is not supported by lvcore",
            severity=Severity.ERROR,
            details={"body_source": source.ssed_kind.value},
        )

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.render_entry_html(self.entry_for_hit(hit), profile=profile, include_diagnostics=include_diagnostics)

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.render_entry_text(self.entry_for_hit(hit))

    def entry_document(self, entry: Entry):
        return entry.document()

    def render_entry_html(
        self,
        entry: Entry,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        if isinstance(profile, str):
            profile = HtmlProfile(profile.replace("-", "_"))
        return render_html(entry.document(), profile=profile, include_diagnostics=include_diagnostics)

    def render_entry_text(self, entry: Entry) -> str:
        return render_text(entry.document())

    def entry_diagnostics(self, entry: Entry) -> tuple[object, ...]:
        return entry.document().diagnostics

    @staticmethod
    def _count_diagnostics(target: tuple[Diagnostic, ...], by_severity: dict[str, int], by_area: dict[str, int], by_code: dict[str, int]) -> None:
        for diagnostic in target:
            by_severity[diagnostic.severity.value] = by_severity.get(diagnostic.severity.value, 0) + 1
            by_area[diagnostic.area.value] = by_area.get(diagnostic.area.value, 0) + 1
            by_code[diagnostic.code] = by_code.get(diagnostic.code, 0) + 1

    def validate(self, *, sample_entries: int = 3, sample_search_hits: int = 5) -> dict[str, object]:
        body_source = self.body_source()
        diagnostics_by_severity = {"info": 0, "warning": 0, "error": 0}
        diagnostics_by_area: dict[str, int] = {}
        diagnostics_by_code: dict[str, int] = {}
        entries_checked = 0
        render_ok = 0
        entry_errors: list[str] = []

        if body_source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            for entry in self.iter_entries(limit=sample_entries):
                entries_checked += 1
                try:
                    document = entry.document()
                    render_html(document)
                    render_text(document)
                    render_ok += 1
                    self._count_diagnostics(document.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                except Exception as exc:  # pragma: no cover - defensive validation report path
                    diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                    entry_errors.append(f"{entry.address.block}:{entry.address.offset}: {exc}")

        index_rows_sampled = 0
        search_hits_dereferenced = 0
        search_hits_rendered_html = 0
        search_hits_rendered_text = 0
        search_errors: list[str] = []
        sampled_rows: list[IndexRow] = []
        for parsed in self.indexes().values():
            for row in parsed.rows:
                sampled_rows.append(row)
                if len(sampled_rows) >= sample_search_hits:
                    break
            if len(sampled_rows) >= sample_search_hits:
                break
        for row in sampled_rows:
            index_rows_sampled += 1
            query = row.target_key or row.key
            try:
                results = self.search(query, profile=SearchProfile.EXACT, limit=1)
                self._count_diagnostics(results.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                if not results.hits:
                    search_errors.append(f"no hit for sampled index key on page {row.page} row {row.row}")
                    diagnostics_by_severity["warning"] = diagnostics_by_severity.get("warning", 0) + 1
                    diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                    diagnostics_by_code["sample_search_miss"] = diagnostics_by_code.get("sample_search_miss", 0) + 1
                    continue
                hit = results.hits[0]
                self._count_diagnostics(hit.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                entry = self.entry_for_hit(hit)
                search_hits_dereferenced += 1
                document = entry.document()
                self._count_diagnostics(document.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                render_html(document)
                search_hits_rendered_html += 1
                render_text(document)
                search_hits_rendered_text += 1
            except Exception as exc:  # pragma: no cover - defensive validation report path
                diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                search_errors.append(f"{row.page}:{row.row}: {exc}")

        index_stats = {
            name: {
                "rows": len(parsed.rows),
                "internal_rows": len(parsed.internal_rows),
                "leaf_pages": parsed.leaf_pages,
                "internal_pages": parsed.internal_pages,
                "unknown_leaf_bytes": parsed.unknown_leaf_bytes,
            }
            for name, parsed in self.indexes().items()
        }
        sidecar_resolution = {
            "resolved": diagnostics_by_code.get("sidecar_body_resolved", 0),
            "missing_anchor_id": diagnostics_by_code.get("dense_anchor_missing_id", 0),
            "missing_row": diagnostics_by_code.get("sidecar_body_not_found", 0),
            "unsupported_body_source": diagnostics_by_code.get("unsupported_body_source", 0),
        }
        resource_resolution = {
            "unresolved_gaiji": diagnostics_by_code.get("unresolved_gaiji", 0),
            "unresolved_media": diagnostics_by_code.get("unresolved_media_ref", 0),
            "unresolved_link": diagnostics_by_code.get("unresolved_link_target", 0),
        }
        return {
            "package": self.info.to_dict(),
            "body_source": body_source.to_dict(debug=True),
            "sidecar_resolution": sidecar_resolution,
            "resource_resolution": resource_resolution,
            "component_count": len(self.components),
            "components": [
                {
                    "name": component.name,
                    "role": component.role.value,
                    "type": f"{component.type:02x}",
                    "present": component.path is not None,
                }
                for component in self.components
            ],
            "gaiji": {
                "uni_records": len(self.gaiji.records),
                "unicode_mappings": len(self.gaiji.mapping),
                "ga16_resources": len(self.ga16),
            },
            "indexes": index_stats,
            "title_components": len(self.components_by_role(ComponentRole.TITLE)),
            "sample_entries_checked": entries_checked,
            "sample_entries_rendered": render_ok,
            "sample_index_rows_checked": index_rows_sampled,
            "sample_search_hits_dereferenced": search_hits_dereferenced,
            "sample_search_hits_rendered_html": search_hits_rendered_html,
            "sample_search_hits_rendered_text": search_hits_rendered_text,
            "diagnostics": {
                "by_severity": diagnostics_by_severity,
                "by_area": diagnostics_by_area,
                "by_code": diagnostics_by_code,
                "entry_errors": entry_errors,
                "search_errors": search_errors,
            },
            "ok": diagnostics_by_severity.get("error", 0) == 0 and not entry_errors,
        }

    def summary(self) -> dict[str, object]:
        return {
            "package": self.info.to_dict(),
            "body_source": self.body_source().to_dict(debug=False),
            "components": [component.to_dict() for component in self.components],
            "gaiji": {
                "records": len(self.gaiji.records),
                "mapped": len(self.gaiji.mapping),
                "paths": [str(path) for path in self.gaiji.paths],
                "ga16": [
                    {
                        "path": str(resource.path),
                        "width": resource.width,
                        "height": resource.height,
                        "start_code": f"{resource.start_code:04x}",
                        "count": resource.count,
                        "glyph_bytes": resource.glyph_bytes,
                    }
                    for resource in self.ga16
                ],
            },
        }
