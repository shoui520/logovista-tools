"""Title, index, and native search behavior for SSED packages."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .body_source import (
    SsedBodySourceKind,
)
from .diagnostics import Diagnostic, DiagnosticArea, Severity
from .errors import FormatError
from .index import (
    BODY_ONLY_SIMPLE_TYPES,
    BODY_ONLY_TAGGED_TYPES,
    CROSS_REFERENCE_TYPES,
    GroupContext,
    IndexParse,
    IndexRow,
    KEYWORD_TYPES,
    MULTI_SELECTOR_TYPES,
    SIMPLE_TYPES,
    TAGGED_TYPES,
    is_leaf,
    parse_index,
    parse_internal_page,
    parse_simple_leaf,
    parse_tagged_leaf,
)
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, Entry, SearchProfile, Span
from .package_utils import (
    EXACT_INDEX_PROBE_PAGES,
    SearchValueRow,
    _fold_small_kana_for_index_seek,
    _index_ascii_passthrough_query_bytes,
    _jis_symbol_index_query_bytes,
    _title_surface_query_bytes,
)
from .render import HtmlProfile
from .search import SearchHit, SearchHitDebug, SearchResults, TitleResolution, natural_backward_key, normalize_query, query_candidates
from .ssed import BLOCK_SIZE, CHUNK_SIZE


class PackageSearchMixin:
    """Title/index/search methods for LogoVistaPackage."""

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        comps = [component] if component is not None else list(self.components_by_role(ComponentRole.TITLE))
        out: list[str] = []
        for comp in comps:
            item = self.component(comp) if isinstance(comp, str) else comp
            if item is None:
                continue
            lines = self._limited_title_lines(item, limit=None if limit is None else max(limit - len(out), 0))
            for line in lines:
                out.append(line)
                if limit is not None and len(out) >= limit:
                    return out
        return out

    def _limited_title_lines(self, component: Component, *, limit: int | None = None) -> Iterable[str]:
        if limit is not None and limit <= 0:
            return
        reader = self.data(component)
        if limit is None:
            decoded = self.decode_component(component)
            for line in decoded.text.splitlines():
                line = line.strip()
                if line:
                    yield line
            return

        buffer = bytearray()
        emitted = 0
        offset = 0
        separators = (b"\x1f\x0a", b"\x0a")
        while offset < reader.expanded_size and emitted < limit:
            chunk = reader.read(offset, min(CHUNK_SIZE, reader.expanded_size - offset))
            if not chunk:
                break
            offset += len(chunk)
            buffer.extend(chunk)
            while emitted < limit:
                found: tuple[int, int] | None = None
                for separator in separators:
                    pos = buffer.find(separator)
                    if pos >= 0 and (found is None or pos < found[0]):
                        found = (pos, len(separator))
                if found is None:
                    break
                pos, separator_size = found
                raw = bytes(buffer[:pos])
                del buffer[: pos + separator_size]
                decoded = self._decode_text_stream(raw)
                line = " ".join(part.strip() for part in decoded.text.splitlines() if part.strip()).strip("\x00 ")
                if not line:
                    continue
                yield line
                emitted += 1
        if emitted < limit and buffer.strip(b"\x00"):
            decoded = self._decode_text_stream(bytes(buffer))
            line = " ".join(part.strip() for part in decoded.text.splitlines() if part.strip()).strip("\x00 ")
            if line:
                yield line

    def _iter_title_records(self, component: Component) -> Iterable[tuple[int, str]]:
        reader = self.data(component)
        buffer = bytearray()
        record_start = 0
        offset = 0
        separators = (b"\x1f\x0a", b"\x0a")
        while offset < reader.expanded_size:
            chunk = reader.read(offset, min(CHUNK_SIZE, reader.expanded_size - offset))
            if not chunk:
                break
            offset += len(chunk)
            buffer.extend(chunk)
            while True:
                found: tuple[int, int] | None = None
                for separator in separators:
                    pos = buffer.find(separator)
                    if pos >= 0 and (found is None or pos < found[0]):
                        found = (pos, len(separator))
                if found is None:
                    break
                pos, separator_size = found
                raw = bytes(buffer[:pos])
                del buffer[: pos + separator_size]
                current_start = record_start
                record_start += pos + separator_size
                decoded = self._decode_text_stream(raw)
                line = " ".join(part.strip() for part in decoded.text.splitlines() if part.strip()).strip("\x00 ")
                if line:
                    yield current_start, line
        if buffer.strip(b"\x00"):
            decoded = self._decode_text_stream(bytes(buffer))
            line = " ".join(part.strip() for part in decoded.text.splitlines() if part.strip()).strip("\x00 ")
            if line:
                yield record_start, line

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
                    details={"reason": "missing_title_component"},
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
                    details={"reason": "pointer_outside_component"},
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
        decoded = self._decode_text_stream(data[:end])
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
                details={"reason": "empty_title"},
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

    @staticmethod
    def _row_key_values(row: IndexRow) -> tuple[str, ...]:
        values = [row.key]
        if row.target_key and row.target_key != row.key:
            values.append(row.target_key)
        return tuple(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _heading_fallback(
        *,
        display_key: str,
        matched_key: str,
        row: IndexRow,
    ) -> tuple[str, str]:
        if display_key:
            return display_key, "display_key"
        if row.target_key:
            return row.target_key, "target_key"
        if row.key:
            return row.key, "row_key"
        return matched_key, "fallback"

    def _cached_search_values(
        self,
        component_name: str,
        parsed: IndexParse,
    ) -> tuple[SearchValueRow, ...]:
        key = component_name.lower()
        if key in self._search_value_cache:
            return self._search_value_cache[key]
        backward = self._is_backward_index(component_name)
        cached = []
        for row in parsed.rows:
            stored_values = self._row_key_values(row)
            natural_values = tuple(dict.fromkeys(natural_backward_key(value) if backward else value for value in stored_values))
            stored_normalized = tuple(dict.fromkeys(normalize_query(value) for value in stored_values if value))
            natural_normalized = tuple(dict.fromkeys(normalize_query(value) for value in natural_values if value))
            cached.append((row, stored_values, natural_values, stored_normalized, natural_normalized))
        self._search_value_cache[key] = tuple(cached)
        return self._search_value_cache[key]

    def _cached_exact_values(self, component_name: str, parsed: IndexParse) -> dict[str, tuple[tuple[IndexRow, str], ...]]:
        key = component_name.lower()
        if key in self._exact_search_cache:
            return self._exact_search_cache[key]
        backward = self._is_backward_index(component_name)
        rows_by_key: dict[str, list[tuple[IndexRow, str]]] = {}
        for row, stored_values, natural_values, stored_normalized, natural_normalized in self._cached_search_values(component_name, parsed):
            values: list[tuple[str, str]] = []
            values.extend(zip(natural_values, natural_normalized))
            if backward:
                values.extend((natural_backward_key(value), normalized) for value, normalized in zip(stored_values, stored_normalized))
            seen: set[tuple[int, int, int, int, str]] = set()
            for display_value, normalized_value in values:
                if not normalized_value:
                    continue
                dedupe_key = (*self._row_dedupe_key(row), display_value)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows_by_key.setdefault(normalized_value, []).append((row, display_value))
        self._exact_search_cache[key] = {normalized: tuple(rows) for normalized, rows in rows_by_key.items()}
        return self._exact_search_cache[key]

    def _row_matches(
        self,
        *,
        stored_values: tuple[str, ...],
        natural_values: tuple[str, ...],
        stored_normalized: tuple[str, ...],
        natural_normalized: tuple[str, ...],
        query: str,
        candidates: tuple[str, ...],
        profile: SearchProfile,
        backward: bool,
    ) -> str | None:
        normalized_query = normalize_query(query)
        normalized_candidates = {normalize_query(candidate) for candidate in candidates if candidate}
        return self._row_matches_prepared(
            stored_values=stored_values,
            natural_values=natural_values,
            stored_normalized=stored_normalized,
            natural_normalized=natural_normalized,
            normalized_query=normalized_query,
            normalized_candidates=normalized_candidates,
            raw_candidates=candidates,
            profile=profile,
            backward=backward,
        )

    def _row_matches_prepared(
        self,
        *,
        stored_values: tuple[str, ...],
        natural_values: tuple[str, ...],
        stored_normalized: tuple[str, ...],
        natural_normalized: tuple[str, ...],
        normalized_query: str,
        normalized_candidates: set[str],
        raw_candidates: tuple[str, ...],
        profile: SearchProfile,
        backward: bool,
    ) -> str | None:
        if profile == SearchProfile.EXACT:
            for value, normalized_value in zip(natural_values, natural_normalized):
                if value in raw_candidates or normalized_value in normalized_candidates:
                    return value
            for value, normalized_value in zip(stored_values, stored_normalized):
                if value in raw_candidates or normalized_value in normalized_candidates:
                    return natural_backward_key(value) if backward else value
            return None

        if profile == SearchProfile.FORWARD:
            for value, normalized_value in zip(natural_values, natural_normalized):
                if normalized_value.startswith(normalized_query) or any(value.startswith(candidate) for candidate in raw_candidates):
                    return value
            return None

        if profile == SearchProfile.BACKWARD:
            reversed_query = normalized_query[::-1]
            for stored_value, stored_norm, natural_value, natural_norm in zip(
                stored_values,
                stored_normalized,
                natural_values,
                natural_normalized,
            ):
                if stored_norm.startswith(reversed_query) or natural_norm.endswith(normalized_query) or stored_norm.endswith(normalized_query):
                    return natural_value if backward else stored_value
            return None

        return None

    @staticmethod
    def _search_range_passed(
        *,
        stored_normalized: tuple[str, ...],
        natural_normalized: tuple[str, ...],
        normalized_query: str,
        normalized_candidates: set[str] | None = None,
        profile: SearchProfile,
        backward: bool,
        multi_page_index: bool = False,
    ) -> bool:
        if not normalized_query:
            return False
        if profile == SearchProfile.EXACT:
            candidates = tuple(sorted(value for value in (normalized_candidates or {normalized_query}) if value))
            if not candidates:
                return False
            ceiling = candidates[-1]
            values = tuple(value for value in ((stored_normalized if backward else natural_normalized) or stored_normalized) if value)
            return bool(values) and all(value not in candidates for value in values) and any(value > ceiling for value in values)
        if profile == SearchProfile.FORWARD:
            values = tuple(value for value in natural_normalized if value)
            return bool(values) and all(not value.startswith(normalized_query) for value in values) and any(
                value > normalized_query for value in values
            )
        if profile == SearchProfile.BACKWARD and backward and multi_page_index:
            reversed_query = normalized_query[::-1]
            values = tuple(value for value in stored_normalized if value)
            return bool(values) and all(not value.startswith(reversed_query) for value in values) and any(
                value > reversed_query for value in values
            )
        return False

    def _index_tree_query_key(self, query: str, *, backward: bool = False) -> str:
        value = natural_backward_key(query) if backward else query
        return normalize_query(value)

    def _index_tree_query_bytes(self, query: str, *, backward: bool = False) -> bytes | None:
        candidates = self._index_tree_query_byte_candidates(query, backward=backward)
        return candidates[0] if candidates else None

    def _index_tree_query_byte_candidates(self, query: str, *, backward: bool = False) -> tuple[bytes, ...]:
        raw = str(query or "").strip()
        normalized = normalize_query(query)
        values = [raw, normalized, _fold_small_kana_for_index_seek(raw), _fold_small_kana_for_index_seek(normalized)]
        out: list[bytes] = []
        for value in values:
            if not value:
                continue
            candidate = natural_backward_key(value) if backward else value
            for encoded in (
                _index_ascii_passthrough_query_bytes(candidate),
                _jis_symbol_index_query_bytes(candidate),
                _title_surface_query_bytes(candidate),
            ):
                if encoded:
                    out.append(encoded)
        return tuple(dict.fromkeys(out))

    def _seek_index_leaf_page_for_key(self, component: Component, query_key_bytes: bytes) -> int:
        reader = self.data(component)
        page_index = 0
        seen: set[int] = set()
        while 0 <= page_index < max(1, (reader.expanded_size + BLOCK_SIZE - 1) // BLOCK_SIZE):
            if page_index in seen:
                return page_index
            seen.add(page_index)
            page = reader.read(page_index * BLOCK_SIZE, BLOCK_SIZE)
            if len(page) < BLOCK_SIZE:
                return page_index
            word = int.from_bytes(page[:2], "big")
            if is_leaf(word):
                return page_index
            rows = parse_internal_page(page, page_index, self.gaiji.mapping)
            if not rows:
                return page_index
            chosen = rows[-1]
            for row in rows:
                if not row.raw_key:
                    continue
                if row.raw_key >= query_key_bytes:
                    chosen = row
                    break
            next_page = chosen.child_block - component.start_block
            if next_page == page_index:
                return page_index
            page_index = next_page
        return 0

    def _seek_index_leaf_page_lower_for_key(self, component: Component, query_key_bytes: bytes) -> int:
        reader = self.data(component)
        page_index = 0
        seen: set[int] = set()
        while 0 <= page_index < max(1, (reader.expanded_size + BLOCK_SIZE - 1) // BLOCK_SIZE):
            if page_index in seen:
                return page_index
            seen.add(page_index)
            page = reader.read(page_index * BLOCK_SIZE, BLOCK_SIZE)
            if len(page) < BLOCK_SIZE:
                return page_index
            word = int.from_bytes(page[:2], "big")
            if is_leaf(word):
                return page_index
            rows = parse_internal_page(page, page_index, self.gaiji.mapping)
            if not rows:
                return page_index
            chosen = rows[0]
            for row in rows:
                if not row.raw_key:
                    chosen = row
                    continue
                if row.raw_key <= query_key_bytes:
                    chosen = row
                    continue
                break
            next_page = chosen.child_block - component.start_block
            if next_page == page_index:
                return page_index
            page_index = next_page
        return 0

    def _seek_index_leaf_page(self, component: Component, query: str, *, backward: bool = False) -> int:
        query_key_bytes = self._index_tree_query_bytes(query, backward=backward)
        if query_key_bytes:
            return self._seek_index_leaf_page_for_key(component, query_key_bytes)
        reader = self.data(component)
        query_key = self._index_tree_query_key(query, backward=backward)
        page_index = 0
        seen: set[int] = set()
        while 0 <= page_index < max(1, (reader.expanded_size + BLOCK_SIZE - 1) // BLOCK_SIZE):
            if page_index in seen:
                return page_index
            seen.add(page_index)
            page = reader.read(page_index * BLOCK_SIZE, BLOCK_SIZE)
            if len(page) < BLOCK_SIZE:
                return page_index
            word = int.from_bytes(page[:2], "big")
            if is_leaf(word):
                return page_index
            rows = parse_internal_page(page, page_index, self.gaiji.mapping)
            if not rows:
                return page_index
            chosen = rows[-1]
            for row in rows:
                row_key = normalize_query(row.key)
                if not row_key:
                    continue
                if row_key >= query_key:
                    chosen = row
                    break
            next_page = chosen.child_block - component.start_block
            if next_page == page_index:
                return page_index
            page_index = next_page
        return 0

    def _iter_index_rows_fast(
        self,
        component: Component,
        *,
        query: str | None = None,
        profile: SearchProfile | None = None,
    ) -> Iterable[IndexRow]:
        """Yield leaf rows page-by-page without materializing the whole index.

        The full parser remains available through :meth:`indexes` for debug and
        validation. Reader-facing search only needs matching leaf rows and can
        stop as soon as enough hits are found.
        """

        reader = self.data(component)
        component_type = component.type
        total_pages = (reader.expanded_size + BLOCK_SIZE - 1) // BLOCK_SIZE
        backward = self._is_backward_index(component.name)
        query_key_byte_candidates = self._index_tree_query_byte_candidates(query, backward=backward) if query else ()
        normalized_query = normalize_query(query or "")
        exact_requires_full_scan = bool(
            query
            and profile == SearchProfile.EXACT
            and (not query_key_byte_candidates or component_type in BODY_ONLY_TAGGED_TYPES | BODY_ONLY_SIMPLE_TYPES)
        )
        if exact_requires_full_scan or not query:
            start_pages = (0,)
        elif query_key_byte_candidates:
            pages: list[int] = []
            for candidate in query_key_byte_candidates:
                upper_page = self._seek_index_leaf_page_for_key(component, candidate)
                lower_page = self._seek_index_leaf_page_lower_for_key(component, candidate)
                if profile == SearchProfile.EXACT:
                    pages.extend((lower_page, upper_page))
                else:
                    pages.extend((upper_page, lower_page))
            start_pages = tuple(dict.fromkeys(pages))
        else:
            start_pages = (self._seek_index_leaf_page(component, query, backward=backward),)
        max_pages = total_pages if exact_requires_full_scan else (EXACT_INDEX_PROBE_PAGES if query and profile == SearchProfile.EXACT else total_pages)
        seen_rows: set[tuple[int, int, int, int, int, int, str, str | None]] = set()
        tagged_component = component_type in TAGGED_TYPES | BODY_ONLY_TAGGED_TYPES | KEYWORD_TYPES | CROSS_REFERENCE_TYPES | MULTI_SELECTOR_TYPES
        for start_page in start_pages:
            context: GroupContext | None = None
            if query and tagged_component and start_page > 0:
                previous = reader.read((start_page - 1) * BLOCK_SIZE, BLOCK_SIZE)
                if len(previous) == BLOCK_SIZE and is_leaf(int.from_bytes(previous[:2], "big")):
                    seeded = parse_tagged_leaf(previous, start_page - 1, self.gaiji.mapping, component_type=component_type, context=None)
                    context = seeded.context
            for page_index in range(start_page, min(total_pages, start_page + max_pages)):
                pos = page_index * BLOCK_SIZE
                page = reader.read(pos, BLOCK_SIZE)
                if len(page) < BLOCK_SIZE or not is_leaf(int.from_bytes(page[:2], "big")):
                    if query and not exact_requires_full_scan:
                        break
                    continue
                if component_type in SIMPLE_TYPES:
                    parsed = parse_simple_leaf(page, page_index, self.gaiji.mapping)
                elif component_type in BODY_ONLY_SIMPLE_TYPES:
                    parsed = parse_simple_leaf(page, page_index, self.gaiji.mapping, body_only=True)
                elif tagged_component:
                    parsed = parse_tagged_leaf(page, page_index, self.gaiji.mapping, component_type=component_type, context=context)
                    context = parsed.context
                else:
                    continue
                for row in parsed.rows:
                    row_key = (
                        row.body.block,
                        row.body.offset,
                        row.title.block,
                        row.title.offset,
                        row.page,
                        row.row,
                        row.key,
                        row.target_key,
                    )
                    if row_key in seen_rows:
                        continue
                    seen_rows.add(row_key)
                    yield row

    def _iter_matching_rows_fast(
        self,
        query: str,
        profile: SearchProfile,
        *,
        include_backward_exact: bool = True,
    ) -> Iterable[tuple[str, Component, IndexRow, str]]:
        candidates = query_candidates(query)
        normalized_query = normalize_query(query)
        normalized_candidates = {normalize_query(candidate) for candidate in candidates if candidate}
        for component in self.components_by_role(ComponentRole.INDEX):
            if component.path is None or not self._index_component_matches_profile(component, profile):
                continue
            backward = self._is_backward_index(component.name)
            reader = self.data(component)
            multi_page_index = reader.expanded_size > BLOCK_SIZE
            if profile == SearchProfile.EXACT and backward and not include_backward_exact:
                continue
            for row in self._iter_index_rows_fast(component, query=query, profile=profile):
                stored_values = self._row_key_values(row)
                natural_values = tuple(dict.fromkeys(natural_backward_key(value) if backward else value for value in stored_values))
                stored_normalized = tuple(dict.fromkeys(normalize_query(value) for value in stored_values if value))
                natural_normalized = tuple(dict.fromkeys(normalize_query(value) for value in natural_values if value))
                matched = self._row_matches_prepared(
                    stored_values=stored_values,
                    natural_values=natural_values,
                    stored_normalized=stored_normalized,
                    natural_normalized=natural_normalized,
                    normalized_query=normalized_query,
                    normalized_candidates=normalized_candidates,
                    raw_candidates=candidates,
                    profile=profile,
                    backward=backward,
                )
                if matched is not None:
                    yield component.name, component, row, matched
                elif self._search_range_passed(
                    stored_normalized=stored_normalized,
                    natural_normalized=natural_normalized,
                    normalized_query=normalized_query,
                    normalized_candidates=normalized_candidates,
                    profile=profile,
                    backward=backward,
                    multi_page_index=multi_page_index,
                ):
                    break

    def _iter_matching_rows(
        self,
        query: str,
        profile: SearchProfile,
        *,
        include_backward_exact: bool = True,
        fast: bool = False,
    ) -> Iterable[tuple[str, Component, IndexRow, str]]:
        if fast:
            yield from self._iter_matching_rows_fast(query, profile, include_backward_exact=include_backward_exact)
            return
        candidates = query_candidates(query)
        for name, parsed in self.indexes().items():
            component = self.component(name)
            if component is None or not self._index_component_matches_profile(component, profile):
                continue
            backward = self._is_backward_index(name)
            if profile == SearchProfile.EXACT and backward and not include_backward_exact:
                continue
            if profile == SearchProfile.EXACT:
                yielded: set[tuple[int, int, int, int, str]] = set()
                for candidate in candidates:
                    normalized_candidate = normalize_query(candidate)
                    if not normalized_candidate:
                        continue
                    for row, matched in self._cached_exact_values(name, parsed).get(normalized_candidate, ()):
                        dedupe_key = (*self._row_dedupe_key(row), matched)
                        if dedupe_key in yielded:
                            continue
                        yielded.add(dedupe_key)
                        yield name, component, row, matched
                continue
            for row, stored_values, natural_values, stored_normalized, natural_normalized in self._cached_search_values(name, parsed):
                matched = self._row_matches(
                    stored_values=stored_values,
                    natural_values=natural_values,
                    stored_normalized=stored_normalized,
                    natural_normalized=natural_normalized,
                    query=query,
                    candidates=candidates,
                    profile=profile,
                    backward=backward,
                )
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
        debug: bool = False,
    ) -> SearchHit:
        backward = self._is_backward_index(component_name)
        display_key = self._row_display_key(row, backward=backward)
        body = self._qualified_address(row.body, role=ComponentRole.HONMON)
        title = self._qualified_address(row.title, role=ComponentRole.TITLE)
        fallback_heading, fallback_source = self._heading_fallback(display_key=display_key, matched_key=matched_key, row=row)
        diagnostics: tuple[Diagnostic, ...] = ()
        title_text = ""
        heading = fallback_heading
        heading_source = fallback_source
        title_status = "fallback"
        title_diagnostic_code: str | None = None
        title_reason: str | None = None
        raw_title_component = self.component_for_address(row.title)
        title_components = self.components_by_role(ComponentRole.TITLE)
        raw_title_component_name: str | None = None
        raw_title_component_role: str | None = None
        raw_title_component_type: str | None = None
        if raw_title_component is not None:
            raw_title_component_name = raw_title_component.name
            raw_title_component_role = raw_title_component.role.value
            raw_title_component_type = f"{raw_title_component.type:02x}"
        if row.title == row.body and raw_title_component is not None and raw_title_component.role == ComponentRole.HONMON:
            title_reason = "title_pointer_is_body_pointer"
        elif not title_components and raw_title_component is not None and raw_title_component.role == ComponentRole.HONMON:
            title_reason = "title_pointer_hits_honmon_without_title_components"
        else:
            title_text, diagnostics = self.resolve_title(title)
            if title_text:
                heading = title_text
                heading_source = "title"
                title_status = "resolved"
            elif diagnostics:
                first = diagnostics[0]
                title_status = "failed" if first.code == "title_dereference_failed" else "missing"
                title_diagnostic_code = first.code.value
                title_reason = str(first.details.get("reason") or first.code.value)
            else:
                title_status = "missing"
                title_reason = "empty_title_data"
        title_resolution = TitleResolution(
            status=title_status,
            heading=heading,
            heading_source=heading_source,
            title=title,
            body=body,
            raw_title=row.title,
            raw_body=row.body,
            row_title_equals_body=row.title == row.body,
            fallback_heading_source=fallback_source,
            reason=title_reason,
            title_diagnostic_code=title_diagnostic_code,
            raw_title_component=raw_title_component_name,
            raw_title_component_role=raw_title_component_role,
            raw_title_component_type=raw_title_component_type,
        )
        debug_info = SearchHitDebug(
            index_component=component_name,
            body=body,
            title=title,
            page=row.page,
            row=row.row,
            raw_row=row,
            body_source=self.body_source(debug=True) if debug else None,
            title_resolution=title_resolution,
        )
        return SearchHit(
            id=hit_id,
            query=query,
            normalized_query=normalized_query,
            search_profile=profile,
            package_id=self.info.dict_id,
            display_key=display_key,
            matched_key=matched_key,
            target_key=row.target_key,
            heading=heading,
            heading_source=heading_source,
            title_status=title_status,
            tagged=row.tagged,
            debug_info=debug_info,
            title_diagnostic_code=title_diagnostic_code,
            title_reason=title_reason,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _entry_with_hit_headword(entry: Entry, hit: SearchHit) -> Entry:
        return replace(entry, headword=hit.heading or hit.display_key or entry.headword)

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
        def add_row_matches(row_matches: Iterable[tuple[str, Component, IndexRow, str]], effective_profile: SearchProfile) -> bool:
            added = False
            for name, _component, row, matched_key in row_matches:
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
                        debug=debug,
                    )
                )
                added = True
                if len(hits) >= limit:
                    return added
            return added

        for effective_profile in profiles:
            before_profile = len(hits)
            if effective_profile == SearchProfile.EXACT:
                found_primary = add_row_matches(
                    self._iter_matching_rows(
                        query,
                        effective_profile,
                        include_backward_exact=False,
                        fast=not debug,
                    ),
                    effective_profile,
                )
                if len(hits) >= limit:
                    return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))
                if not found_primary:
                    add_row_matches(
                        self._iter_matching_rows(
                            query,
                            effective_profile,
                            include_backward_exact=True,
                            fast=not debug,
                        ),
                        effective_profile,
                    )
            else:
                add_row_matches(self._iter_matching_rows(query, effective_profile, fast=not debug), effective_profile)
            if len(hits) >= limit:
                return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))
            if profile == SearchProfile.NATIVE and len(hits) > before_profile:
                return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))
        return SearchResults(query=query, normalized_query=normalized_query, profile=profile, hits=tuple(hits))

    def search_index(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[JsonObject]:
        return [
            {
                "component": hit.index_component,
                **(hit.raw_row.to_dict() if hit.raw_row is not None else {}),
                "heading": hit.heading,
                "display_key": hit.display_key,
            }
            for hit in self.search(term, limit=limit, profile=profile).hits
        ]

    def search_entries(
        self,
        term: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
    ) -> list[Entry]:
        return [self.entry_for_hit(hit) for hit in self.search(term, limit=limit, profile=profile).hits]

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        inspection = self.inspect_body_pointer(hit.body)
        sidecar_entry = self._try_entry_from_dense_sidecar(hit, inspection)
        if sidecar_entry is not None:
            return sidecar_entry
        honmon = self.honmon_component()
        if not inspection.anchor_id and honmon is not None and honmon.path is not None:
            try:
                return self._entry_or_empty_body_placeholder(
                    self._entry_with_hit_headword(self.entry_at(hit.body), hit),
                    hit,
                )
            except (FormatError, KeyError, ValueError, OSError) as exc:
                return self._placeholder_entry(
                    hit.body,
                    headword=hit.heading,
                    code="body_pointer_unresolved",
                    message="body pointer could not be resolved to a readable HONMON entry",
                    severity=Severity.ERROR,
                    details={
                        "body": hit.body.to_dict(),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
        source = self.body_source()
        if source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            try:
                return self._entry_or_empty_body_placeholder(
                    self._entry_with_hit_headword(self.entry_at(hit.body), hit),
                    hit,
                )
            except (FormatError, KeyError, ValueError, OSError) as exc:
                return self._placeholder_entry(
                    hit.body,
                    headword=hit.heading,
                    code="body_pointer_unresolved",
                    message="body pointer could not be resolved to a readable HONMON entry",
                    severity=Severity.ERROR,
                    details={
                        "body": hit.body.to_dict(),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
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
        if source.ssed_kind == SsedBodySourceKind.MISSING_BODY_COMPONENT:
            return self._placeholder_entry(
                hit.body,
                headword=hit.heading,
                code="missing_body_component",
                message="local SSED package declares no readable HONMON component for entry bodies",
                severity=Severity.WARNING,
                details={"body_source": source.ssed_kind.value, "missing_component": "HONMON.DIC"},
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
        return self.render_entry_html(
            self.entry_for_hit(hit),
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.render_entry_text(self.entry_for_hit(hit))
