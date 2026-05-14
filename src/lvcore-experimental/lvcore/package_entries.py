"""Entry slicing, body-source classification, dense HONMON, and sidecar bodies."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import sqlite3
from typing import Callable, Iterable

from .body_source import (
    BodyPointerInspection,
    BodySourceInfo,
    BodySourceSupport,
    Confidence,
    SidecarBody,
    SidecarInfo,
    SsedBodySourceKind,
    body_source_kind_for_sidecar,
    quote_sql_identifier,
    select_body_sidecar,
    strip_html,
)
from .diagnostics import Diagnostic, DiagnosticArea, Severity
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry, SearchProfile, Span
from .package_utils import ENTRY_MARKER
from .scan import ScanBudget
from .search import SearchHit
from .ssed import BLOCK_SIZE, CHUNK_SIZE, SsedData
from .text import decode_text_stream


_SIDECAR_BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}

_SIDECAR_STYLE_TAGS = {
    "b": ("bold", 0x06, 0x07),
    "strong": ("bold", 0x06, 0x07),
    "i": ("italic", 0x0E, 0x0F),
    "em": ("em", 0x10, 0x11),
}


class _SidecarHtmlSpanParser(HTMLParser):
    """Convert supported sidecar HTML into normal text/control spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.spans: list[Span] = []
        self._offset = 0

    def _append(self, span: Span) -> None:
        self.spans.append(span)

    def _break(self) -> None:
        if not self.spans or self.spans[-1].kind == "break":
            return
        self._append(Span(kind="break", offset=self._offset, length=0))

    def _control(self, tag: str, op: int) -> None:
        self._append(Span(kind="control", offset=self._offset, length=0, op=op, attrs={"tag": tag}))

    def handle_starttag(self, tag: str, attrs) -> None:
        lower = tag.lower()
        if lower == "br":
            self._break()
            return
        if lower in _SIDECAR_BLOCK_TAGS:
            self._break()
        style = _SIDECAR_STYLE_TAGS.get(lower)
        if style is not None:
            logical_tag, start_op, _end_op = style
            self._control(logical_tag, start_op)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        style = _SIDECAR_STYLE_TAGS.get(lower)
        if style is not None:
            logical_tag, _start_op, end_op = style
            self._control(logical_tag, end_op)
        if lower in _SIDECAR_BLOCK_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._append(Span(kind="text", text=data, offset=self._offset, length=len(data)))
        self._offset += len(data)


def _sidecar_html_to_spans(value: str, *, fallback_text: str) -> tuple[Span, ...]:
    parser = _SidecarHtmlSpanParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return (Span(kind="text", text=fallback_text),)
    spans = tuple(span for span in parser.spans if not (span.kind == "break" and span is parser.spans[-1]))
    if any(span.kind == "text" and span.text for span in spans):
        return spans
    return (Span(kind="text", text=fallback_text),)


class PackageEntryMixin:
    """Entry/body-source methods for LogoVistaPackage."""

    @staticmethod
    def _marker_offsets(reader: SsedData, *, limit: int | None = None, budget: ScanBudget | None = None) -> list[int]:
        offsets: list[int] = []
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

    def _markers_for_component(self, component: Component, *, limit: int | None = None, budget: ScanBudget | None = None) -> list[int]:
        key = component.name.lower()
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

    def _dense_anchor_evidence(self, component: Component, *, use_index_pointers: bool = True) -> JsonObject:
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
        chosen_sidecar = select_body_sidecar(sidecars)

        if is_dense:
            if chosen_sidecar is not None:
                kind = body_source_kind_for_sidecar(chosen_sidecar)
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
        )
        return entry

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

    def _sidecar_debug_details(self, sidecar: SidecarInfo, anchor_id: str) -> JsonObject:
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

    @staticmethod
    def _sidecar_body_from_row(sidecar: SidecarInfo, row: sqlite3.Row) -> SidecarBody | None:
        title = str(row[sidecar.title_column]) if sidecar.title_column and row[sidecar.title_column] is not None else ""
        html_value = str(row[sidecar.html_column]) if sidecar.html_column and row[sidecar.html_column] is not None else ""
        plain_value = str(row[sidecar.plain_column]) if sidecar.plain_column and row[sidecar.plain_column] is not None else ""
        text = plain_value.strip() or strip_html(html_value) or title.strip()
        if not (text or html_value or title):
            return None
        return SidecarBody(title=strip_html(title), text=text, html=html_value or None, source=sidecar)

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
        note = Diagnostic(
            severity=Severity.INFO,
            area=DiagnosticArea.BODY,
            code="sidecar_body_resolved",
            message="entry body resolved from SSED sidecar database",
            location=self._location_for_address(address, role=ComponentRole.HONMON),
            details=self._sidecar_debug_details(sidecar, anchor_id),
        )
        text = body.text or body.title or headword_hint
        html_fallback_text = strip_html(body.html) if body.html else ""
        spans = (
            _sidecar_html_to_spans(body.html, fallback_text=text)
            if body.html and (not body.text or body.text == html_fallback_text)
            else (Span(kind="text", text=text),)
        )
        return Entry(
            address=address,
            end_address=address,
            headword=body.title or headword_hint,
            text=text,
            spans=spans,
            entry_diagnostics=(note,),
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
            hit.debug_info.body,
            headword=hit.heading,
            code="empty_body_at_pointer",
            message="entry body pointer decoded to no displayable text",
            details={"body": hit.debug_info.body.to_dict(), "heading": hit.heading},
            placeholder_text="Entry body pointer decoded to no displayable text.",
        )

    def _entry_from_sidecar(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry:
        anchor_id = inspection.anchor_id
        if not anchor_id:
            return self._placeholder_entry(
                hit.debug_info.body,
                headword=hit.heading,
                code="dense_anchor_missing_id",
                message="dense HONMON record did not expose a numeric anchor id",
                severity=Severity.ERROR,
            )
        body = self._fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            return self._placeholder_entry(
                hit.debug_info.body,
                headword=hit.heading,
                code="sidecar_body_not_found",
                message="body sidecar did not contain a row for the dense HONMON anchor",
                severity=Severity.ERROR,
                details=self._sidecar_debug_details(sidecar, anchor_id),
            )
        return self._entry_from_sidecar_body(hit, body, sidecar=sidecar, anchor_id=anchor_id)

    def _try_entry_from_dense_sidecar(
        self,
        hit: SearchHit,
        inspection: BodyPointerInspection,
    ) -> Entry | None:
        anchor_id = inspection.anchor_id
        if not anchor_id:
            return None
        sidecar = select_body_sidecar(self._body_sidecars(stop_after_body_resolver=True))
        if sidecar is None:
            return None
        body = self._fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            return None
        return self._entry_from_sidecar_body(
            hit,
            body,
            sidecar=sidecar,
            anchor_id=anchor_id,
        )
