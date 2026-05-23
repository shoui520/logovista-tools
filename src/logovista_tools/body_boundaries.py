"""HONMON body-boundary helpers derived from native index targets."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable

from .controls import control_arg_length
from .entries import DictionarySource, iter_entry_slices_reader
from .indexes import INDEX_TYPES, scan_index_component_reader
from .ssed import BLOCK_SIZE, SsedRandomReader, find_case_insensitive, parse_ssedinfo


def target_starts_on_section_marker(reader: SsedRandomReader, target_offset: int) -> bool:
    """Return true if an expanded HONMON offset starts at a section control."""

    if target_offset < 0 or target_offset >= reader.expanded_size:
        return False
    prefix = reader.read(target_offset, min(6, reader.expanded_size - target_offset))
    return prefix.startswith(b"\x1f\x09") or prefix.startswith(b"\x1f\x02\x1f\x09")


def target_starts_on_visible_heading_span(reader: SsedRandomReader, target_offset: int) -> bool:
    """Return true if an expanded HONMON offset starts a non-empty heading span."""

    if target_offset < 0 or target_offset >= reader.expanded_size:
        return False
    prefix = reader.read(target_offset, min(512, reader.expanded_size - target_offset))
    if not prefix.startswith(b"\x1f\x41"):
        return False
    payload_start = 2 + control_arg_length(prefix, 0)
    close = prefix.find(b"\x1f\x61", payload_start)
    return close > payload_start


def index_body_marker_boundaries_for_source(
    source: DictionarySource,
    reader: SsedRandomReader | None = None,
    *,
    components: Iterable[str] | None = None,
) -> tuple[int, ...]:
    """Collect HONMON-relative native-index targets that are section starts.

    Generic SSED entry slicing uses ``1f09 0001`` markers, but several real
    dictionaries have native index rows that target other ``1f09`` sections
    inside those generic groups.  Those targets are addressable body boundaries
    for focused rendering and reader navigation.
    """

    active_reader = reader or SsedRandomReader(source.honmon)
    _title, elements = parse_ssedinfo(source.idx)
    selected_components = {component.casefold() for component in components or ()}
    offsets: set[int] = set()
    for element in elements:
        if element.type not in INDEX_TYPES or not element.start:
            continue
        if selected_components and element.filename.casefold() not in selected_components:
            continue
        index_path = find_case_insensitive(source.idx.parent, element.filename)
        if index_path is None:
            continue

        def collect(row: dict) -> None:
            if row.get("kind") != "leaf":
                return
            body = row.get("body")
            if not isinstance(body, dict):
                return
            try:
                block = int(body["block"])
                offset = int(body["offset"])
            except (KeyError, TypeError, ValueError):
                return
            relative = (block - source.honmon_start_block) * BLOCK_SIZE + offset
            if not (0 <= relative < active_reader.expanded_size):
                return
            if target_starts_on_section_marker(active_reader, relative):
                offsets.add(relative)

        scan_index_component_reader(
            element.filename,
            element.type,
            index_path,
            element.start,
            gaiji="drop",
            gaiji_map={},
            emit_internal=False,
            emit_row=collect,
        )
    return tuple(sorted(offsets))


def generic_entry_slice_containing_reader(
    reader: SsedRandomReader,
    target_offset: int,
) -> tuple[int, int] | None:
    """Return the generic ``1f09 0001`` entry slice containing an offset."""

    if target_offset < 0:
        return None
    for start, end in iter_entry_slices_reader(reader):
        if start <= target_offset < end:
            return start, end
        if start > target_offset:
            break
    return None


def target_needs_index_boundaries(reader: SsedRandomReader, target_offset: int) -> bool:
    """Return true when a focused target needs native-index sub-boundaries.

    A target at the beginning of the generic ``1f09 0001`` slice should keep
    that full slice because examples and child sections commonly follow it.
    A target inside a broader generic slice can be narrowed when it lands on a
    recognizable HONMON section marker or a visible heading span.  If the
    generic marker scan sees the whole stream as one slice, native-index
    boundaries are also needed because the package may use a different section
    value for entry starts.
    """

    generic = generic_entry_slice_containing_reader(reader, target_offset)
    target_starts_generic_slice = (
        generic is not None
        and (
            generic[0] == target_offset
            or (target_offset >= 2 and generic[0] == target_offset - 2)
        )
    )
    markerless_whole_stream = generic == (0, reader.expanded_size) and target_offset != 0
    return not target_starts_generic_slice and (
        target_starts_on_section_marker(reader, target_offset)
        or target_starts_on_visible_heading_span(reader, target_offset)
        or markerless_whole_stream
    )


def target_slice_from_index_boundaries(
    boundary_offsets: Iterable[int] | None,
    target_offset: int,
    *,
    fallback_end: int,
) -> tuple[int, int] | None:
    """Return an exact target slice when *target_offset* is an index boundary."""

    if boundary_offsets is None:
        return None
    boundaries = tuple(boundary_offsets)
    if not boundaries:
        return None
    index = bisect_left(boundaries, target_offset)
    if index >= len(boundaries) or boundaries[index] != target_offset:
        return None
    next_end = boundaries[index + 1] if index + 1 < len(boundaries) else fallback_end
    end = min(next_end, fallback_end) if fallback_end > target_offset else next_end
    if end <= target_offset:
        return None
    return target_offset, end


def target_containing_slice_from_index_boundaries(
    boundary_offsets: Iterable[int] | None,
    target_offset: int,
    *,
    containing_start: int,
    fallback_end: int,
) -> tuple[int, int] | None:
    """Return the native-index section interval containing *target_offset*.

    Markerless body streams can have index rows that target visible text inside
    a section rather than the section marker itself.  In that case the closest
    preceding index-derived section boundary inside the current generic slice is
    the renderer entry start.
    """

    if boundary_offsets is None:
        return None
    boundaries = tuple(boundary_offsets)
    if not boundaries:
        return None
    index = bisect_left(boundaries, target_offset)
    if index < len(boundaries) and boundaries[index] == target_offset:
        return target_slice_from_index_boundaries(boundaries, target_offset, fallback_end=fallback_end)
    previous_index = index - 1
    if previous_index < 0:
        return None
    start = boundaries[previous_index]
    if start < containing_start or start >= target_offset:
        return None
    next_end = boundaries[index] if index < len(boundaries) else fallback_end
    end = min(next_end, fallback_end) if fallback_end > target_offset else next_end
    if end <= target_offset:
        return None
    return start, end


def target_slice_from_visible_heading_span(
    reader: SsedRandomReader,
    target_offset: int,
    *,
    containing_start: int,
    fallback_end: int,
) -> tuple[int, int] | None:
    """Return a local heading-span interval for native targets inside TOC-like bodies."""

    if target_offset < containing_start or target_offset >= fallback_end:
        return None
    if not target_starts_on_visible_heading_span(reader, target_offset):
        return None
    data = reader.read(target_offset, fallback_end - target_offset)
    payload_start = 2 + control_arg_length(data, 0)
    close = data.find(b"\x1f\x61", payload_start)
    scan = close + 2 + control_arg_length(data, close) if close >= 0 else payload_start
    while scan < len(data):
        next_heading = data.find(b"\x1f\x41", scan)
        if next_heading < 0:
            return target_offset, fallback_end
        candidate = target_offset + next_heading
        if target_starts_on_visible_heading_span(reader, candidate):
            return target_offset, candidate
        scan = next_heading + 2
    return target_offset, fallback_end


def target_slice_from_reader_and_index_boundaries(
    reader: SsedRandomReader,
    target_offset: int,
    boundary_offsets: Iterable[int] | None,
) -> tuple[int, int] | None:
    """Return the generic HONMON/index slice for a native index target.

    This is the renderer-independent rule that focused lookup should apply
    before profile-specific formatting or HC-specific compatibility handling:

    - exact native-index section targets slice to the next native-index section;
    - markerless whole-stream targets can land inside visible text, in which
      case the closest preceding native-index section is the entry start;
    - normal generic marker starts keep the whole generic entry slice.
    """

    generic_containing = generic_entry_slice_containing_reader(reader, target_offset)
    if generic_containing is None:
        return None
    target_starts_generic_slice = (
        generic_containing[0] == target_offset
        or (target_offset >= 2 and generic_containing[0] == target_offset - 2)
    )
    if target_starts_generic_slice:
        return None

    boundary_slice = target_slice_from_index_boundaries(
        boundary_offsets,
        target_offset,
        fallback_end=generic_containing[1],
    )
    if boundary_slice is not None:
        return boundary_slice

    if generic_containing == (0, reader.expanded_size):
        containing_slice = target_containing_slice_from_index_boundaries(
            boundary_offsets,
            target_offset,
            containing_start=generic_containing[0],
            fallback_end=generic_containing[1],
        )
        if containing_slice is not None:
            return containing_slice
    return target_slice_from_visible_heading_span(
        reader,
        target_offset,
        containing_start=generic_containing[0],
        fallback_end=generic_containing[1],
    )
