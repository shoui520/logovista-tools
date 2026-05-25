"""Entry slicing and sidecar-backed entry bodies."""

from __future__ import annotations

import sqlite3
from typing import Callable, Iterable

from .body_source import (
    BodyPointerInspection,
    BodySourceInfo,
    BodySourceSupport,
    SidecarBody,
    SidecarInfo,
    SsedBodySourceKind,
    quote_sql_identifier,
    select_body_sidecar,
)
from .body_source_classifier import (
    classify_body_source,
    decode_anchor_at,
    dense_anchor_evidence,
    dense_anchor_marker_sample,
    inspect_body_pointer as inspect_body_pointer_for_package,
)
from .body_provider import BODY_PROVIDERS, BodyResolveContext
from .diagnostics import Diagnostic, DiagnosticArea, Severity
from .entry_views import body_stream_entry_view, semantic_entry_view, sidecar_body_entry_view, unsupported_entry_view
from .errors import FormatError
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry, PackageInfo, SearchProfile, Span
from .package_services import EntryStoreDeps
from .package_utils import ENTRY_MARKER
from .reader import EntryTarget, EntryTargetKind, EntryView
from .render import HtmlProfile
from .scan import ScanBudget
from .search import SearchHit, SearchHitDebug
from .sidecar_body import AnchorQueryValue, anchor_query_values, make_sidecar_body_entry, sidecar_body_from_row, sidecar_debug_details
from .ssed import BLOCK_SIZE, CHUNK_SIZE, SsedData


ENTRY_STOP_OPS = {0x09, 0x41}
ENTRY_STOP_MARKER_LENGTH = 4


class EntryStore:
    """Entry slicing, dense-HONMON inspection, and body resolution service."""

    def __init__(self, deps: EntryStoreDeps) -> None:
        self._deps = deps
        self._marker_cache: dict[str, list[int]] = {}
        self._body_pointer_cache: dict[str, list[int]] = {}
        self._body_source_cache: dict[bool, BodySourceInfo] = {}

    def clear(self) -> None:
        self._marker_cache.clear()
        self._body_pointer_cache.clear()
        self._body_source_cache.clear()

    @property
    def info(self) -> PackageInfo:
        return self._deps.info

    @property
    def gaiji_mapping(self) -> dict[str, str]:
        return self._deps.gaiji_mapping()

    def components_by_role(self, role: ComponentRole) -> tuple[Component, ...]:
        return self._deps.components_by_role(role)

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None:
        return self._deps.component_for_address(address, role)

    def data(self, component: str | Component) -> SsedData:
        return self._deps.data(component)

    def _relative_offset(self, component: Component, address: Address) -> int:
        return self._deps.relative_offset(component, address)

    def _qualified_address(self, address: Address, *, role: ComponentRole | None = None) -> Address:
        return self._deps.qualified_address(address, role)

    def _location_for_address(self, address: Address, *, role: ComponentRole | None = None):
        return self._deps.location_for_address(address, role)

    def honmon_component(self) -> Component | None:
        return self._deps.honmon_component()

    def _decode_text_stream(self, data: bytes, *, renderer_entry_backed: bool = False):
        return self._deps.decode_text_stream(data, renderer_entry_backed)

    def _sidecar_file_candidates(self) -> list:
        return self._deps.sidecar_file_candidates()

    def _body_sidecars(self, *, stop_after_body_resolver: bool = False, allow_expensive: bool = True) -> tuple[SidecarInfo, ...]:
        return self._deps.body_sidecars(stop_after_body_resolver, allow_expensive)

    def _sqlite_connection_for_sidecar(self, path, storage: str):
        return self._deps.sqlite_connection_for_sidecar(path, storage)

    def indexes(self, component: str | Component | None = None):
        return self._deps.indexes(component)

    def _iter_index_rows_fast(
        self,
        component: Component,
        *,
        query: str | None = None,
        profile: SearchProfile | None = None,
        budget: ScanBudget | None = None,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ):
        return self._deps.iter_index_rows_fast(component, query, profile, budget, max_bytes, cancel)

    def _make_hit(
        self,
        *,
        hit_id: int,
        query: str,
        normalized_query: str,
        profile: SearchProfile,
        component_name: str,
        row,
        matched_key: str,
        debug: bool = False,
    ) -> SearchHit:
        return self._deps.make_hit(hit_id, query, normalized_query, profile, component_name, row, matched_key, debug)

    def _row_display_key(self, row, *, backward: bool = False) -> str:
        return self._deps.row_display_key(row, backward)

    def _row_dedupe_key(self, row) -> tuple[int, int, int, int]:
        return self._deps.row_dedupe_key(row)

    def _is_backward_index(self, component_name: str) -> bool:
        return self._deps.is_backward_index(component_name)

    def render_entry_html(
        self,
        entry: Entry,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self._deps.render_entry_html(entry, profile, include_diagnostics)

    def render_entry_text(self, entry: Entry) -> str:
        return self._deps.render_entry_text(entry)

    @staticmethod
    def _learn_entry_stop_markers(reader: SsedData, *, max_scan_bytes: int = CHUNK_SIZE) -> tuple[bytes, ...]:
        data = reader.read(0, min(reader.expanded_size, max_scan_bytes))
        for pos in range(0, max(0, len(data) - ENTRY_STOP_MARKER_LENGTH + 1)):
            if data[pos] == 0x1F and data[pos + 1] in ENTRY_STOP_OPS:
                return (bytes(data[pos : pos + ENTRY_STOP_MARKER_LENGTH]),)
        return (ENTRY_MARKER,)

    @staticmethod
    def _marker_offsets(reader: SsedData, *, limit: int | None = None, budget: ScanBudget | None = None) -> list[int]:
        offsets: list[int] = []
        markers = EntryStore._learn_entry_stop_markers(reader)
        tail = b""
        tail_base = 0
        budget = budget or ScanBudget()
        for chunk_index in range(len(reader.offsets)):
            if not budget.allow(CHUNK_SIZE):
                return offsets
            chunk = reader.chunk(chunk_index)
            if not chunk:
                continue
            chunk_base = chunk_index * CHUNK_SIZE
            data = tail + chunk
            data_base = tail_base
            search = 0
            while True:
                found = -1
                marker = b""
                for candidate in markers:
                    candidate_found = data.find(candidate, search)
                    if candidate_found >= 0 and (found < 0 or candidate_found < found):
                        found = candidate_found
                        marker = candidate
                if found < 0:
                    break
                absolute = data_base + found
                if found >= 2 and data[found - 2 : found] == b"\x1f\x02":
                    absolute -= 2
                elif absolute >= 2 and reader.read(absolute - 2, 2) == b"\x1f\x02":
                    absolute -= 2
                if not offsets or absolute != offsets[-1]:
                    offsets.append(absolute)
                search = found + max(1, len(marker))
                if limit is not None and len(offsets) >= limit + 1:
                    return offsets
            keep = max(0, ENTRY_STOP_MARKER_LENGTH + 2 - 1)
            tail = data[-keep:] if keep else b""
            tail_base = chunk_base + len(chunk) - len(tail)
        return offsets

    def _markers_for_component(self, component: Component, *, limit: int | None = None, budget: ScanBudget | None = None) -> list[int]:
        key = component.name.casefold()
        if limit is not None and key not in self._marker_cache:
            return self._marker_offsets(self.data(component), limit=limit, budget=budget)
        if key not in self._marker_cache:
            self._marker_cache[key] = self._marker_offsets(self.data(component), budget=budget)
        return self._marker_cache[key]

    def iter_entry_slices(
        self,
        *,
        limit: int | None = None,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Iterable[tuple[int, int]]:
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            return
        reader = self.data(honmon)
        budget = ScanBudget(max_bytes=max_bytes, cancel=cancel)
        starts = self._markers_for_component(honmon, limit=limit, budget=budget)
        pointer_offsets: list[int] = []
        if limit is not None:
            pointer_offsets = self._body_pointer_offsets_fast(honmon, limit=limit + 1, budget=budget)
        else:
            pointer_offsets = self._body_pointer_offsets(honmon)
        if pointer_offsets:
            starts = sorted(set(starts).union(pointer_offsets))
        if not starts:
            if not budget.allow(min(reader.expanded_size, BLOCK_SIZE)):
                return
            sample = reader.read(0, min(reader.expanded_size, BLOCK_SIZE))
            if sample.strip(b"\x00"):
                yield 0, reader.expanded_size
            return
        for index, start in enumerate(starts[:limit]):
            end = starts[index + 1] if index + 1 < len(starts) else reader.expanded_size
            yield start, end

    def iter_entries(
        self,
        *,
        limit: int | None = None,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Iterable[Entry]:
        honmon = self.honmon_component()
        if honmon is None:
            return
        reader = self.data(honmon)
        budget = ScanBudget(max_bytes=max_bytes, cancel=cancel)
        if limit is not None:
            source = self.body_source()
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
                sidecar = select_body_sidecar(self._body_sidecars(stop_after_body_resolver=True))
                if sidecar is not None:
                    yielded = False
                    for entry in self._iter_body_sidecar_entries_fast(sidecar, limit=limit):
                        yielded = True
                        yield entry
                    if yielded:
                        return
                yield from self._iter_dense_sidecar_entries_fast(limit=limit)
                return
            sample = reader.read(0, min(reader.expanded_size, BLOCK_SIZE))
            if ENTRY_MARKER not in sample:
                pointer_offsets = self._body_pointer_offsets_fast(honmon, limit=limit, preserve_order=True, budget=budget)
                if pointer_offsets:
                    for start in pointer_offsets[:limit]:
                        address = Address(honmon.start_block + start // BLOCK_SIZE, start % BLOCK_SIZE, honmon.name)
                        yield self.entry_at(address, max_bytes=64 * 1024)
                    return
        count = 0
        for start, end in self.iter_entry_slices(limit=limit, max_bytes=max_bytes, cancel=cancel):
            if not budget.allow(end - start):
                break
            decoded = self._decode_text_stream(reader.read(start, end - start))
            text = decoded.text.strip("\x00")
            entry = Entry(
                address=Address(honmon.start_block + start // 2048, start % 2048, honmon.name),
                end_address=Address(honmon.start_block + end // 2048, end % 2048, honmon.name),
                headword=None,
                text=text,
                spans=decoded.spans,
                decode_unknown_controls=decoded.unknown_controls,
                decode_unknown_bytes=decoded.unknown_bytes,
                decode_invalid_jis_pairs=decoded.invalid_jis_pairs,
            )
            yield entry
            count += 1
            if limit is not None and count >= limit:
                break

    def _index_components_for_entry_boundaries(self) -> list[Component]:
        components = [component for component in self.components_by_role(ComponentRole.INDEX) if component.path is not None]

        def priority(component: Component) -> tuple[int, str]:
            name = component.name.upper()
            if name.startswith(("FK", "FH", "KW")) or name == "INDEX.DIC":
                return 0, name
            if name.startswith(("BK", "BH")):
                return 2, name
            return 1, name

        return sorted(components, key=priority)

    def _body_pointer_offsets_fast(
        self,
        component: Component,
        *,
        limit: int,
        preserve_order: bool = False,
        budget: ScanBudget | None = None,
    ) -> list[int]:
        if limit <= 0:
            return []
        reader = self.data(component)
        offsets: list[int] = []
        seen: set[int] = set()
        for index_component in self._index_components_for_entry_boundaries():
            for row in self._iter_index_rows_fast(index_component, budget=budget):
                target = self.component_for_address(row.body, role=ComponentRole.HONMON)
                if target is None or target.name.casefold() != component.name.casefold():
                    continue
                offset = self._relative_offset(target, row.body)
                if offset < 0 or offset >= reader.expanded_size or offset in seen:
                    continue
                seen.add(offset)
                offsets.append(offset)
                if len(offsets) >= limit:
                    return offsets if preserve_order else sorted(offsets)
        return offsets if preserve_order else sorted(offsets)

    def _iter_entry_hits_fast(self, *, limit: int) -> Iterable[SearchHit]:
        seen: set[tuple[int, int, int, int]] = set()
        count = 0
        for component in self._index_components_for_entry_boundaries():
            backward = self._is_backward_index(component.name)
            for row in self._iter_index_rows_fast(component):
                dedupe_key = self._row_dedupe_key(row)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                display = self._row_display_key(row, backward=backward)
                yield self._make_hit(
                    hit_id=count + 1,
                    query="",
                    normalized_query="",
                    profile=SearchProfile.NATIVE,
                    component_name=component.name,
                    row=row,
                    matched_key=display or row.target_key or row.key,
                    debug=False,
                )
                count += 1
                if count >= limit:
                    return

    def _iter_dense_sidecar_entries_fast(self, *, limit: int) -> Iterable[Entry]:
        hits = list(self._iter_entry_hits_fast(limit=limit))
        if not hits:
            return
        sidecar = select_body_sidecar(self._body_sidecars(stop_after_body_resolver=True))
        if sidecar is None:
            for hit in hits:
                yield self.entry_for_hit(hit)
            return

        anchor_by_index: dict[int, str] = {}
        for index, hit in enumerate(hits):
            inspection = self.inspect_body_pointer(hit.debug_info.body)
            if inspection.anchor_id:
                anchor_by_index[index] = inspection.anchor_id
        bodies = self._fetch_sidecar_bodies(sidecar, tuple(anchor_by_index.values()))
        for index, hit in enumerate(hits):
            anchor_id = anchor_by_index.get(index)
            if not anchor_id:
                yield self._placeholder_entry(
                    hit.debug_info.body,
                    headword=hit.heading,
                    code="dense_anchor_missing_id",
                    message="dense HONMON record did not expose a numeric anchor id",
                    severity=Severity.ERROR,
                )
                continue
            body = bodies.get(anchor_id)
            if body is None:
                yield self._placeholder_entry(
                    hit.debug_info.body,
                    headword=hit.heading,
                    code="sidecar_body_not_found",
                    message="body sidecar did not contain a row for the dense HONMON anchor",
                    severity=Severity.ERROR,
                    details=self._sidecar_debug_details(sidecar, anchor_id),
                )
                continue
            yield self._entry_from_sidecar_body(
                hit,
                body,
                sidecar=sidecar,
                anchor_id=anchor_id,
            )

    def _iter_body_sidecar_entries_fast(self, sidecar: SidecarInfo, *, limit: int) -> Iterable[Entry]:
        if limit <= 0 or not sidecar.table or not sidecar.id_column:
            return
        try:
            con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
        except sqlite3.DatabaseError:
            return
        select_columns = [sidecar.id_column]
        for column in (sidecar.title_column, sidecar.html_column, sidecar.plain_column):
            if column and column not in select_columns:
                select_columns.append(column)
        quoted = ", ".join(quote_sql_identifier(column) for column in select_columns)
        sql = (
            f"select {quoted} from {quote_sql_identifier(sidecar.table)} "
            f"order by {quote_sql_identifier(sidecar.id_column)} limit ?"
        )
        try:
            rows = con.execute(sql, (limit,))
        except sqlite3.DatabaseError:
            return
        for row_index, row in enumerate(rows, start=1):
            raw_id = row[sidecar.id_column]
            anchor_id = str(raw_id) if raw_id is not None else str(row_index)
            body = self._sidecar_body_from_row(sidecar, row)
            if body is None:
                continue
            try:
                pseudo_offset = int(anchor_id.lstrip("0") or "0")
            except ValueError:
                pseudo_offset = row_index
            address = Address(0, pseudo_offset, sidecar.path.name)
            yield self._make_sidecar_body_entry(
                address,
                body,
                sidecar=sidecar,
                anchor_id=anchor_id,
                headword_hint=anchor_id,
            )

    def _body_pointer_offsets(self, component: Component) -> list[int]:
        key = component.name.casefold()
        if key in self._body_pointer_cache:
            return self._body_pointer_cache[key]
        offsets: set[int] = set()
        for parsed in self.indexes().values():
            for row in parsed.rows:
                for address in (row.body,):
                    target = self.component_for_address(address, role=ComponentRole.HONMON)
                    if target is None or target.name.casefold() != key:
                        continue
                    offset = self._relative_offset(target, address)
                    if 0 <= offset < self.data(target).expanded_size:
                        offsets.add(offset)
        self._body_pointer_cache[key] = sorted(offsets)
        return self._body_pointer_cache[key]

    def _decode_anchor_at(self, component: Component, start: int, *, max_bytes: int = 96) -> tuple[str, int]:
        return decode_anchor_at(self, component, start, max_bytes=max_bytes)

    def _dense_anchor_evidence(self, component: Component, *, use_index_pointers: bool = True) -> JsonObject:
        return dense_anchor_evidence(self, component, use_index_pointers=use_index_pointers)

    def _dense_anchor_marker_sample(self, component: Component, marker_offsets: list[int]) -> list[int]:
        return dense_anchor_marker_sample(self, component, marker_offsets)

    def body_source(self, *, debug: bool = False) -> BodySourceInfo:
        if debug in self._body_source_cache:
            return self._body_source_cache[debug]
        self._body_source_cache[debug] = classify_body_source(self, debug=debug)
        return self._body_source_cache[debug]

    def validate_body_source(self) -> BodySourceInfo:
        return self.body_source(debug=True)

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

    def _next_marker_after(self, component: Component, start: int, *, max_scan_bytes: int | None = None) -> int | None:
        reader = self.data(component)
        markers = self._learn_entry_stop_markers(reader)
        offset = max(start + 1, 0)
        scan_end = reader.expanded_size if max_scan_bytes is None else min(reader.expanded_size, start + max_scan_bytes)
        tail = b""
        tail_base = offset
        keep = ENTRY_STOP_MARKER_LENGTH + 2 - 1
        while offset < scan_end:
            chunk = reader.read(offset, min(CHUNK_SIZE, scan_end - offset))
            if not chunk:
                break
            data = tail + chunk
            base = tail_base
            found = -1
            for marker in markers:
                marker_found = data.find(marker)
                if marker_found >= 0 and (found < 0 or marker_found < found):
                    found = marker_found
            if found >= 0:
                absolute = base + found
                if absolute > start:
                    if found >= 2 and data[found - 2 : found] == b"\x1f\x02":
                        return absolute - 2
                    if absolute >= 2 and reader.read(absolute - 2, 2) == b"\x1f\x02":
                        return absolute - 2
                    return absolute
            if len(data) >= keep:
                tail = data[-keep:]
                tail_base = base + len(data) - keep
            else:
                tail = data
                tail_base = base
            offset += len(chunk)
        return None

    def _entry_range_for_address(
        self,
        address: Address,
        *,
        max_bytes: int = 64 * 1024,
        use_index_boundaries: bool = False,
    ) -> tuple[Component, int, int, tuple[Diagnostic, ...]]:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None:
            raise KeyError(f"no HONMON component contains address {address}")
        reader = self.data(honmon)
        start = self._relative_offset(honmon, address)
        if start < 0 or start >= reader.expanded_size:
            raise ValueError(f"entry address outside HONMON bounds: {address}")

        candidates: list[tuple[int, str]] = []
        if use_index_boundaries:
            next_pointer = self._next_after(self._body_pointer_offsets(honmon), start)
            if next_pointer is not None:
                candidates.append((next_pointer, "next_body_pointer"))
        next_marker = self._next_marker_after(honmon, start, max_scan_bytes=max_bytes)
        if next_marker is not None:
            candidates.append((next_marker, "next_marker"))
        elif reader.expanded_size <= 8 * 1024 * 1024:
            next_pointer = self._next_after(self._body_pointer_offsets(honmon), start)
            if next_pointer is not None:
                candidates.append((next_pointer, "small_component_body_pointer_fallback"))
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

    def entry_at(self, address: Address, *, max_bytes: int = 64 * 1024) -> Entry:
        honmon, start, end_offset, diagnostics = self._entry_range_for_address(address, max_bytes=max_bytes)
        reader = self.data(honmon)
        decoded = self._decode_text_stream(reader.read(start, end_offset - start))
        entry = Entry(
            address=Address(address.block, address.offset, honmon.name),
            end_address=Address(honmon.start_block + end_offset // BLOCK_SIZE, end_offset % BLOCK_SIZE, honmon.name),
            headword=None,
            text=decoded.text.strip("\x00"),
            spans=decoded.spans,
            entry_diagnostics=diagnostics,
            decode_unknown_controls=decoded.unknown_controls,
            decode_unknown_bytes=decoded.unknown_bytes,
            decode_invalid_jis_pairs=decoded.invalid_jis_pairs,
        )
        return entry

    def entry_view_at(self, address: Address, *, max_bytes: int = 64 * 1024) -> EntryView:
        return self.entry_view_for_target(
            EntryTarget.ssed_address(package_id=self.info.dict_id, address=self._qualified_address(address, role=ComponentRole.HONMON)),
            max_bytes=max_bytes,
        )

    def _hit_from_target(self, target: EntryTarget) -> SearchHit:
        body = target.address
        if body is None:
            body = Address(0, 0, None)
        title = target.title_address or body
        heading = target.label or ""
        return SearchHit(
            id=0,
            query="",
            normalized_query="",
            search_profile=SearchProfile.NATIVE,
            package_id=target.package_id or self.info.dict_id,
            display_key=heading,
            matched_key=heading,
            target_key=None,
            heading=heading,
            heading_source="target_label" if heading else "unknown",
            title_status="target",
            tagged=False,
            target=target,
            debug_info=SearchHitDebug(
                index_component=target.index_component or "",
                body=body,
                title=title,
                page=target.index_page,
                row=target.index_row,
            ),
        )

    def entry_view_for_target(self, target: EntryTarget, *, max_bytes: int = 64 * 1024) -> EntryView:
        if target.kind in {EntryTargetKind.SSED_ADDRESS, EntryTargetKind.NATIVE_INDEX_HIT} and target.address is not None:
            return self.entry_view_for_hit(self._hit_from_target(target))
        if target.kind == EntryTargetKind.DENSE_SIDECAR_ROW and target.address is not None:
            hit = self._hit_from_target(target)
            sidecar = next(
                (
                    item
                    for item in self._body_sidecars(stop_after_body_resolver=False)
                    if item.path.name == target.sidecar_name and (target.sidecar_table is None or item.table == target.sidecar_table)
                ),
                None,
            )
            if sidecar is not None and target.sidecar_row_id is not None:
                body = self.fetch_sidecar_body(sidecar, target.sidecar_row_id)
                if body is not None:
                    return self.view_for_sidecar_body(hit, body, sidecar=sidecar, anchor_id=target.sidecar_row_id)
                entry = self.placeholder_entry(
                    target.address,
                    headword=target.label or "",
                    code="sidecar_body_not_found",
                    message="sidecar target row could not be resolved",
                    severity=Severity.ERROR,
                    details=self.sidecar_debug_details(sidecar, target.sidecar_row_id),
                )
                return self.view_for_unsupported_entry(target, entry, source=sidecar.kind)
        address = target.address or Address(0, 0, None)
        entry = self.placeholder_entry(
            address,
            headword=target.label or "",
            code="unsupported_body_source",
            message="entry target is not resolvable as an entry body",
            severity=Severity.ERROR,
            details={"target_kind": target.kind.value},
        )
        return self.view_for_unsupported_entry(target, entry, source=target.kind.value)

    def inspect_body_pointer(self, address: Address) -> BodyPointerInspection:
        return inspect_body_pointer_for_package(self, address)

    @staticmethod
    def _anchor_query_values(anchor_id: str, sidecar: SidecarInfo) -> tuple[AnchorQueryValue, ...]:
        return anchor_query_values(anchor_id)

    def sidecar_debug_details(self, sidecar: SidecarInfo, anchor_id: str) -> JsonObject:
        return sidecar_debug_details(sidecar, anchor_id)

    def _sidecar_debug_details(self, sidecar: SidecarInfo, anchor_id: str) -> JsonObject:
        return self.sidecar_debug_details(sidecar, anchor_id)

    def fetch_sidecar_body(self, sidecar: SidecarInfo, anchor_id: str) -> SidecarBody | None:
        if not sidecar.table or not sidecar.id_column:
            return None
        try:
            con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
        except sqlite3.DatabaseError:
            return None
        try:
            select_columns = [sidecar.id_column]
            for column in (sidecar.title_column, sidecar.html_column, sidecar.plain_column):
                if column and column not in select_columns:
                    select_columns.append(column)
            quoted = ", ".join(quote_sql_identifier(column) for column in select_columns)
            sql = (
                f"select {quoted} from {quote_sql_identifier(sidecar.table)} "
                f"where {quote_sql_identifier(sidecar.id_column)}=? limit 1"
            )
            row = None
            for value in self._anchor_query_values(anchor_id, sidecar):
                row = con.execute(sql, (value,)).fetchone()
                if row is not None:
                    break
            if row is None:
                return None
            return self._sidecar_body_from_row(sidecar, row)
        except sqlite3.DatabaseError:
            return None

    def _fetch_sidecar_body(self, sidecar: SidecarInfo, anchor_id: str) -> SidecarBody | None:
        return self.fetch_sidecar_body(sidecar, anchor_id)

    @staticmethod
    def _sidecar_body_from_row(sidecar: SidecarInfo, row: sqlite3.Row) -> SidecarBody | None:
        return sidecar_body_from_row(sidecar, row)

    def _fetch_sidecar_bodies(self, sidecar: SidecarInfo, anchor_ids: tuple[str, ...]) -> dict[str, SidecarBody]:
        if not sidecar.table or not sidecar.id_column or not anchor_ids:
            return {}
        try:
            con = self._sqlite_connection_for_sidecar(sidecar.path, sidecar.storage)
        except sqlite3.DatabaseError:
            return {}

        value_to_anchor: dict[object, str] = {}
        query_values: list[object] = []
        seen_values: set[tuple[str, object]] = set()
        for anchor_id in anchor_ids:
            for value in self._anchor_query_values(anchor_id, sidecar):
                marker = (type(value).__name__, value)
                if marker not in seen_values:
                    seen_values.add(marker)
                    query_values.append(value)
                value_to_anchor.setdefault(value, anchor_id)
                value_to_anchor.setdefault(str(value), anchor_id)

        select_columns = [sidecar.id_column]
        for column in (sidecar.title_column, sidecar.html_column, sidecar.plain_column):
            if column and column not in select_columns:
                select_columns.append(column)
        quoted = ", ".join(quote_sql_identifier(column) for column in select_columns)
        base_sql = (
            f"select {quoted} from {quote_sql_identifier(sidecar.table)} "
            f"where {quote_sql_identifier(sidecar.id_column)} in "
        )

        bodies: dict[str, SidecarBody] = {}
        try:
            for start in range(0, len(query_values), 900):
                batch = query_values[start : start + 900]
                placeholders = ",".join("?" for _ in batch)
                for row in con.execute(f"{base_sql}({placeholders})", batch):
                    raw_id = row[sidecar.id_column]
                    anchor_id = value_to_anchor.get(raw_id) or value_to_anchor.get(str(raw_id))
                    if not anchor_id or anchor_id in bodies:
                        continue
                    body = self._sidecar_body_from_row(sidecar, row)
                    if body is not None:
                        bodies[anchor_id] = body
        except sqlite3.DatabaseError:
            return bodies
        return bodies

    def _make_sidecar_body_entry(
        self,
        address: Address,
        body: SidecarBody,
        *,
        sidecar: SidecarInfo,
        anchor_id: str,
        headword_hint: str,
    ) -> Entry:
        return make_sidecar_body_entry(
            address,
            body,
            sidecar=sidecar,
            anchor_id=anchor_id,
            headword_hint=headword_hint,
            location=self._location_for_address(address, role=ComponentRole.HONMON),
        )

    def _entry_from_sidecar_body(
        self,
        hit: SearchHit,
        body: SidecarBody,
        *,
        sidecar: SidecarInfo,
        anchor_id: str,
    ) -> Entry:
        return self._make_sidecar_body_entry(
            hit.debug_info.body,
            body,
            sidecar=sidecar,
            anchor_id=anchor_id,
            headword_hint=hit.heading,
        )

    def placeholder_entry(
        self,
        address: Address,
        *,
        headword: str,
        code: str,
        message: str,
        severity: Severity = Severity.WARNING,
        details: JsonObject | None = None,
        placeholder_text: str = "Entry body is not yet supported for this LogoVista body source.",
    ) -> Entry:
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
            text=placeholder_text,
            spans=(Span(kind="text", text=placeholder_text),),
            entry_diagnostics=(diagnostic,),
        )

    def _placeholder_entry(
        self,
        address: Address,
        *,
        headword: str,
        code: str,
        message: str,
        severity: Severity = Severity.WARNING,
        details: JsonObject | None = None,
        placeholder_text: str = "Entry body is not yet supported for this LogoVista body source.",
    ) -> Entry:
        return self.placeholder_entry(
            address,
            headword=headword,
            code=code,
            message=message,
            severity=severity,
            details=details,
            placeholder_text=placeholder_text,
        )

    def _entry_or_empty_body_placeholder(self, entry: Entry, hit: SearchHit) -> Entry:
        if entry.text.strip() or any(span.text for span in entry.spans if span.kind == "text"):
            return entry
        return self._placeholder_entry(
            hit.debug_info.body,
            headword=hit.heading,
            code="empty_body_at_pointer",
            message="entry body pointer decoded to no displayable text",
            details={"body": hit.debug_info.body.to_dict(), "heading": hit.heading},
            placeholder_text="Entry body pointer decoded to no displayable text.",
        )

    def sidecar_entry_for_hit(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        anchor_id = inspection.anchor_id
        if not anchor_id:
            return self.placeholder_entry(
                hit.debug_info.body,
                headword=hit.heading,
                code="dense_anchor_missing_id",
                message="dense HONMON record did not expose a numeric anchor id",
                severity=Severity.ERROR,
            )
        body = self.fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            return self.placeholder_entry(
                hit.debug_info.body,
                headword=hit.heading,
                code="sidecar_body_not_found",
                message="body sidecar did not contain a row for the dense HONMON anchor",
                severity=Severity.ERROR,
                details=self.sidecar_debug_details(sidecar, anchor_id),
            )
        return self._entry_from_sidecar_body(hit, body, sidecar=sidecar, anchor_id=anchor_id)

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        return self.sidecar_entry_for_hit(hit, sidecar, inspection)

    @staticmethod
    def _entry_with_hit_headword(entry: Entry, hit: SearchHit) -> Entry:
        from dataclasses import replace

        return replace(entry, headword=hit.heading or hit.display_key or entry.headword)

    def body_stream_entry_for_hit(self, hit: SearchHit) -> Entry:
        try:
            return self._entry_or_empty_body_placeholder(
                self._entry_with_hit_headword(self.entry_at(hit.debug_info.body), hit),
                hit,
            )
        except (FormatError, KeyError, ValueError, OSError) as exc:
            return self.placeholder_entry(
                hit.debug_info.body,
                headword=hit.heading,
                code="body_pointer_unresolved",
                message="body pointer could not be resolved to a readable HONMON entry",
                severity=Severity.ERROR,
                details={
                    "body": hit.debug_info.body.to_dict(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    def _entry_from_body_stream_pointer(self, hit: SearchHit) -> Entry:
        return self.body_stream_entry_for_hit(hit)

    def view_for_semantic_entry(self, target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView:
        return semantic_entry_view(target, entry, source=source)

    def _entry_view_from_semantic_entry(self, target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView:
        return self.view_for_semantic_entry(target, entry, source=source)

    def view_for_body_stream_entry(self, target: EntryTarget, entry: Entry) -> EntryView:
        return body_stream_entry_view(
            target,
            entry,
            location=self._location_for_address(entry.address, role=ComponentRole.HONMON),
        )

    def _entry_view_from_body_stream(self, target: EntryTarget, entry: Entry) -> EntryView:
        return self.view_for_body_stream_entry(target, entry)

    def view_for_unsupported_entry(self, target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView:
        return unsupported_entry_view(target, entry, source=source)

    def _entry_view_from_unsupported_entry(self, target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView:
        return self.view_for_unsupported_entry(target, entry, source=source)

    def view_for_sidecar_body(self, hit: SearchHit, body: SidecarBody, *, sidecar: SidecarInfo, anchor_id: str) -> EntryView:
        entry = self._entry_from_sidecar_body(hit, body, sidecar=sidecar, anchor_id=anchor_id)
        return sidecar_body_entry_view(hit, entry, body, sidecar=sidecar, anchor_id=anchor_id)

    def _entry_view_from_sidecar_body(self, hit: SearchHit, body: SidecarBody, *, sidecar: SidecarInfo, anchor_id: str) -> EntryView:
        return self.view_for_sidecar_body(hit, body, sidecar=sidecar, anchor_id=anchor_id)

    def _body_resolve_context(self, hit: SearchHit) -> BodyResolveContext:
        inspection = self.inspect_body_pointer(hit.debug_info.body)
        source_cache = None

        def source_getter() -> BodySourceInfo:
            nonlocal source_cache
            if source_cache is None:
                source_cache = self.body_source()
            return source_cache

        return BodyResolveContext(hit=hit, inspection=inspection, source_getter=source_getter)

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        context = self._body_resolve_context(hit)

        for provider in BODY_PROVIDERS:
            entry = provider.resolve(self, context)
            if entry is not None:
                return entry
        raise AssertionError("body provider registry did not return an entry")

    def entry_view_for_hit(self, hit: SearchHit) -> EntryView:
        context = self._body_resolve_context(hit)

        for provider in BODY_PROVIDERS:
            view = provider.resolve_view(self, context)
            if view is not None:
                return view
        raise AssertionError("body provider registry did not return an entry view")

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.render_entry_html(
            self.entry_for_hit(hit),
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.render_entry_text(self.entry_for_hit(hit))
