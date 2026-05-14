"""Entry slicing, body-source classification, dense HONMON, and sidecar bodies."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Iterable

from .body_source import (
    BodyPointerInspection,
    BodySourceInfo,
    BodySourceSupport,
    Confidence,
    SidecarBody,
    SidecarInfo,
    SsedBodySourceKind,
    quote_sql_identifier,
    strip_html,
)
from .diagnostics import Diagnostic, DiagnosticArea, Severity
from .model import Address, Component, ComponentRole, Entry, SearchProfile, Span
from .package_utils import ENTRY_MARKER
from .search import SearchHit
from .ssed import BLOCK_SIZE, CHUNK_SIZE, SsedData
from .text import decode_text_stream


class PackageEntryMixin:
    """Entry/body-source methods for LogoVistaPackage."""

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

    def iter_entries(self, *, limit: int | None = None, include_supplements: bool = False) -> Iterable[Entry]:
        honmon = self.honmon_component()
        if honmon is None:
            return
        reader = self.data(honmon)
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
                for hit in self._iter_entry_hits_fast(limit=limit):
                    yield self.entry_for_hit(hit, include_supplements=include_supplements)
                return
            sample = reader.read(0, min(reader.expanded_size, BLOCK_SIZE))
            if ENTRY_MARKER not in sample:
                pointer_offsets = self._body_pointer_offsets_fast(honmon, limit=limit, preserve_order=True)
                if pointer_offsets:
                    for start in pointer_offsets[:limit]:
                        address = Address(honmon.start_block + start // BLOCK_SIZE, start % BLOCK_SIZE, honmon.name)
                        yield self.entry_at(address, max_bytes=64 * 1024, include_supplements=include_supplements)
                    return
        count = 0
        for start, end in self.iter_entry_slices(limit=limit):
            decoded = self._decode_text_stream(reader.read(start, end - start))
            text = decoded.text.strip("\x00")
            head = ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                head = lines[0]
            entry = Entry(
                address=Address(honmon.start_block + start // 2048, start % 2048, honmon.name),
                end_address=Address(honmon.start_block + end // 2048, end % 2048, honmon.name),
                headword=head,
                text=text,
                spans=decoded.spans,
                decode_unknown_controls=decoded.unknown_controls,
                decode_unknown_bytes=decoded.unknown_bytes,
            )
            yield self._attach_sidecar_supplements(entry) if include_supplements else entry
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

    def _body_pointer_offsets_fast(self, component: Component, *, limit: int, preserve_order: bool = False) -> list[int]:
        if limit <= 0:
            return []
        reader = self.data(component)
        offsets: list[int] = []
        seen: set[int] = set()
        for index_component in self._index_components_for_entry_boundaries():
            for row in self._iter_index_rows_fast(index_component):
                target = self.component_for_address(row.body, role=ComponentRole.HONMON)
                if target is None or target.name.lower() != component.name.lower():
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
        # Observed dense HONMON sidecar packages expose short decimal anchor
        # identifiers in the raw HONMON record. Non-numeric records are left
        # unresolved instead of guessed; a future provider can add another
        # anchor scheme when corpus evidence is strong enough.
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

    def _dense_anchor_evidence(self, component: Component, *, use_index_pointers: bool = True) -> dict[str, object]:
        if use_index_pointers:
            pointer_offsets = self._body_pointer_offsets(component)
        else:
            pointer_offsets = self._body_pointer_offsets_fast(component, limit=4, preserve_order=True)
        sample_offsets = pointer_offsets[:64]
        if not sample_offsets:
            marker_offsets = self._markers_for_component(component, limit=256)[:256]
            sample_offsets = self._dense_anchor_marker_sample(component, marker_offsets)
        ids: list[str] = []
        lengths: list[int] = []
        for offset in sample_offsets:
            anchor_id, size = self._decode_anchor_at(component, offset)
            if anchor_id:
                ids.append(anchor_id)
            lengths.append(size)
        numeric_ratio = len(ids) / len(sample_offsets) if sample_offsets else 0.0
        common_gap = None
        unique_offsets = sorted(set((pointer_offsets or sample_offsets)[:4096]))
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

    def _dense_anchor_marker_sample(self, component: Component, marker_offsets: list[int]) -> list[int]:
        if not marker_offsets:
            return []
        best_offsets = marker_offsets[:64]
        best_ratio = -1.0
        best_count = 0
        max_stride = min(8, len(marker_offsets))
        for stride in range(1, max_stride + 1):
            for phase in range(stride):
                candidates = marker_offsets[phase::stride][:64]
                if len(candidates) < 4:
                    continue
                numeric = 0
                for offset in candidates:
                    anchor_id, _size = self._decode_anchor_at(component, offset)
                    if anchor_id:
                        numeric += 1
                ratio = numeric / len(candidates)
                if (ratio, numeric) > (best_ratio, best_count):
                    best_ratio = ratio
                    best_count = numeric
                    best_offsets = candidates
        return best_offsets

    def _choose_body_sidecar(self, sidecars: tuple[SidecarInfo, ...]) -> SidecarInfo | None:
        renderable = [sidecar for sidecar in sidecars if sidecar.table and sidecar.id_column and (sidecar.html_column or sidecar.plain_column)]
        if not renderable:
            return None
        for sidecar in renderable:
            lower = sidecar.path.name.lower()
            if lower.startswith("vlpljbl") and sidecar.kind in {"t_contents", "honbun", "sqlite_body"}:
                return sidecar
        return renderable[0]

    def body_source(self, *, debug: bool = False) -> BodySourceInfo:
        if debug in self._body_source_cache:
            return self._body_source_cache[debug]
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            self._body_source_cache[debug] = BodySourceInfo(
                package_family=self.info.family,
                ssed_kind=SsedBodySourceKind.MISSING_BODY_COMPONENT,
                support=BodySourceSupport.UNSUPPORTED,
                confidence=Confidence.PROVEN,
                notes=("missing HONMON component",),
            )
            return self._body_source_cache[debug]
        reader = self.data(honmon)
        marker_limit = 2000000 if debug else 16
        marker_count = len(self._markers_for_component(honmon, limit=marker_limit))
        marker_density = marker_count / max(reader.expanded_size, 1)
        sidecar_paths = tuple(self._sidecar_file_candidates())
        evidence = self._dense_anchor_evidence(honmon, use_index_pointers=debug)
        numeric_ratio = float(evidence["numeric_ratio"])
        is_dense = numeric_ratio >= 0.6 and int(evidence["sample_count"]) >= 4
        sidecars = self._body_sidecars(stop_after_body_resolver=not debug) if is_dense else ()
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

        if not debug:
            notes = (*notes, "fast body-source classification; run with --debug for index-pointer evidence")

        self._body_source_cache[debug] = BodySourceInfo(
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
        offset = max(start + 1, 0)
        scan_end = reader.expanded_size if max_scan_bytes is None else min(reader.expanded_size, start + max_scan_bytes)
        tail = b""
        tail_base = offset
        keep = len(ENTRY_MARKER) + 2 - 1
        while offset < scan_end:
            chunk = reader.read(offset, min(CHUNK_SIZE, scan_end - offset))
            if not chunk:
                break
            data = tail + chunk
            base = tail_base
            found = data.find(ENTRY_MARKER)
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

    def entry_at(self, address: Address, *, max_bytes: int = 64 * 1024, include_supplements: bool = False) -> Entry:
        honmon, start, end_offset, diagnostics = self._entry_range_for_address(address, max_bytes=max_bytes)
        reader = self.data(honmon)
        decoded = self._decode_text_stream(reader.read(start, end_offset - start))
        lines = [line.strip() for line in decoded.text.splitlines() if line.strip()]
        headword = lines[0] if lines else ""
        entry = Entry(
            address=Address(address.block, address.offset, honmon.name),
            end_address=Address(honmon.start_block + end_offset // BLOCK_SIZE, end_offset % BLOCK_SIZE, honmon.name),
            headword=headword,
            text=decoded.text.strip("\x00"),
            spans=decoded.spans,
            entry_diagnostics=diagnostics,
            decode_unknown_controls=decoded.unknown_controls,
            decode_unknown_bytes=decoded.unknown_bytes,
        )
        return self._attach_sidecar_supplements(entry, include=include_supplements)

    def inspect_body_pointer(self, address: Address) -> BodyPointerInspection:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None or honmon.path is None:
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

    def _sidecar_debug_details(self, sidecar: SidecarInfo, anchor_id: str) -> dict[str, object]:
        query_values = [str(value) for value in self._anchor_query_values(anchor_id, sidecar)]
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

    def _entry_or_empty_body_placeholder(self, entry: Entry, hit: SearchHit) -> Entry:
        if entry.text.strip() or any(span.text for span in entry.spans if span.kind == "text"):
            return entry
        return self._placeholder_entry(
            hit.body,
            headword=hit.heading,
            code="empty_body_at_pointer",
            message="entry body pointer decoded to no displayable text",
            details={"body": hit.body.to_dict(), "heading": hit.heading},
            placeholder_text="Entry body pointer decoded to no displayable text.",
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
        spans = (
            (Span(kind="sidecar_html", text=body.html, attrs={"plain_text": text}),)
            if body.html
            else (Span(kind="text", text=text),)
        )
        entry = Entry(
            address=hit.body,
            end_address=hit.body,
            headword=body.title or hit.heading,
            text=text,
            spans=spans,
            entry_diagnostics=(note,),
        )
        return entry

    def _try_entry_from_dense_sidecar(
        self,
        hit: SearchHit,
        inspection: BodyPointerInspection,
        *,
        include_supplements: bool = False,
    ) -> Entry | None:
        anchor_id = inspection.anchor_id
        if not anchor_id or not self._sidecar_file_candidates():
            return None
        sidecar = self._choose_body_sidecar(self._body_sidecars(stop_after_body_resolver=True))
        if sidecar is None:
            return None
        body = self._fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            return None
        note = Diagnostic(
            severity=Severity.INFO,
            area=DiagnosticArea.BODY,
            code="sidecar_body_resolved",
            message="entry body resolved from SSED sidecar database",
            location=self._location_for_address(hit.body, role=ComponentRole.HONMON),
            details=self._sidecar_debug_details(sidecar, anchor_id),
        )
        text = body.text or body.title or hit.heading
        spans = (
            (Span(kind="sidecar_html", text=body.html, attrs={"plain_text": text}),)
            if body.html
            else (Span(kind="text", text=text),)
        )
        entry = Entry(
            address=hit.body,
            end_address=hit.body,
            headword=body.title or hit.heading,
            text=text,
            spans=spans,
            entry_diagnostics=(note,),
        )
        return self._attach_sidecar_supplements(entry, include=include_supplements)
