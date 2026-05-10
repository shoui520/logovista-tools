"""Structural verifier for writer-generated plain SSED packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .entries import iter_entry_slices_with_boundaries
from .gaiji import (
    Ga16Resource,
    UniResource,
    ga16_section_for_path,
    iter_ga16_code_sources,
    parse_ga16_resource,
    parse_uni_resource,
)
from .indexes import (
    IndexPointer,
    scan_index_component,
)
from .ssed import (
    BLOCK_SIZE,
    SSEDINFO_MAGIC,
    expand_sseddata_file,
    find_case_insensitive,
    parse_sseddata_header,
    parse_ssedinfo,
)


WRITER_INDEX_TYPES = {0x70, 0x71, 0x90, 0x91}
TAGGED_INDEX_TYPES = {0x70, 0x90}
SIMPLE_INDEX_TYPES = {0x71, 0x91}
TITLE_TYPES = {0x04, 0x05, 0x06, 0x07}


@dataclass(frozen=True)
class VerifyIssue:
    severity: str
    code: str
    message: str
    component: str | None = None
    page_index: int | None = None
    row_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComponentBytes:
    filename: str
    type: int
    start: int
    end: int
    path: Path
    expanded: bytes


@dataclass(frozen=True)
class BranchRow:
    row_index: int
    key: bytes
    child_block: int
    child_page_index: int


@dataclass
class PageInfo:
    page_index: int
    logical_block: int
    word: int
    leaf: bool
    high_key: bytes = b""
    branch_rows: list[BranchRow] = field(default_factory=list)
    lookup_keys: list[bytes] = field(default_factory=list)


@dataclass
class WriterVerifyReport:
    package_path: str
    idx_path: str
    title: str
    ok: bool
    errors: int
    warnings: int
    metrics: dict[str, Any]
    issues: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _is_leaf(word: int) -> bool:
    return bool(word & 0x8000)


def _padded_key(key: bytes, width: int) -> bytes:
    return key[:width].ljust(width, b"\x00")


def _is_ff_sentinel(key: bytes) -> bool:
    return bool(key) and all(value == 0xFF for value in key)


def find_ssedinfo_path(path: Path) -> Path:
    if path.is_file():
        if path.read_bytes()[:8] != SSEDINFO_MAGIC:
            raise ValueError(f"not SSEDINFO: {path}")
        return path

    candidates = sorted([*path.glob("*.IDX"), *path.glob("*.idx")], key=lambda p: (p.name.startswith("00000"), p.name.lower()))
    for candidate in candidates:
        try:
            if candidate.read_bytes()[:8] == SSEDINFO_MAGIC:
                return candidate
        except OSError:
            continue
    raise FileNotFoundError(f"no SSEDINFO .IDX found under {path}")


def _simple_leaf_keys(page: bytes) -> list[bytes]:
    count = _be16(page, 2)
    pos = 4
    keys: list[bytes] = []
    for _row_index in range(count):
        if pos >= len(page):
            break
        key_len = page[pos]
        if key_len == 0:
            break
        pos += 1
        if pos + key_len + 12 > len(page):
            break
        keys.append(page[pos : pos + key_len])
        pos += key_len + 12
    return keys


def _tagged_leaf_keys(page: bytes, current_key: bytes | None) -> tuple[list[bytes], bytes | None]:
    count = _be16(page, 2)
    pos = 4
    keys: list[bytes] = []
    subrecord = 0
    while subrecord < count and pos + 2 <= len(page):
        tag = page[pos]
        key_len = page[pos + 1]
        if tag == 0 and key_len == 0:
            break
        pos += 2

        if tag == 0x00:
            if pos + key_len + 12 > len(page):
                break
            key = page[pos : pos + key_len]
            keys.append(key)
            pos += key_len + 12
            subrecord += 1
            continue

        if tag == 0x80:
            if pos + 2 + key_len > len(page):
                break
            pos += 2
            current_key = page[pos : pos + key_len]
            pos += key_len
            subrecord += 1
            continue

        if tag == 0xC0:
            if pos + key_len + 12 > len(page):
                break
            target_key = page[pos : pos + key_len]
            key = current_key or target_key
            keys.append(key)
            pos += key_len + 12
            subrecord += 1
            continue

        break
    return keys, current_key


def _branch_rows(page: bytes, component_start: int) -> list[BranchRow]:
    word = _be16(page, 0)
    count = _be16(page, 2)
    slot = (word & 0xFF) + 4
    rows: list[BranchRow] = []
    pos = 4
    for row_index in range(1, count + 1):
        if pos + slot > len(page):
            break
        key = page[pos : pos + slot - 4]
        child_block = _be32(page, pos + slot - 4)
        rows.append(
            BranchRow(
                row_index=row_index,
                key=key,
                child_block=child_block,
                child_page_index=child_block - component_start,
            )
        )
        pos += slot
    return rows


def index_page_infos(data: bytes, component_type: int, component_start: int) -> dict[int, PageInfo]:
    pages: dict[int, PageInfo] = {}
    current_tagged_key: bytes | None = None
    for page_index in range(len(data) // BLOCK_SIZE):
        page = data[page_index * BLOCK_SIZE : (page_index + 1) * BLOCK_SIZE]
        word = _be16(page, 0)
        logical_block = component_start + page_index
        if not _is_leaf(word):
            rows = _branch_rows(page, component_start)
            pages[page_index] = PageInfo(
                page_index=page_index,
                logical_block=logical_block,
                word=word,
                leaf=False,
                high_key=rows[-1].key if rows else b"",
                branch_rows=rows,
            )
            continue

        if component_type in TAGGED_INDEX_TYPES:
            keys, current_tagged_key = _tagged_leaf_keys(page, current_tagged_key)
        elif component_type in SIMPLE_INDEX_TYPES:
            keys = _simple_leaf_keys(page)
        else:
            keys = []
        pages[page_index] = PageInfo(
            page_index=page_index,
            logical_block=logical_block,
            word=word,
            leaf=True,
            high_key=keys[-1] if keys else b"",
            lookup_keys=keys,
        )
    return pages


def traverse_index_key(data: bytes, component_start: int, key: bytes) -> int | None:
    page_index = 0
    seen: set[int] = set()
    page_count = len(data) // BLOCK_SIZE
    while 0 <= page_index < page_count and page_index not in seen:
        seen.add(page_index)
        page = data[page_index * BLOCK_SIZE : (page_index + 1) * BLOCK_SIZE]
        word = _be16(page, 0)
        if _is_leaf(word):
            return page_index
        rows = _branch_rows(page, component_start)
        for row in rows:
            if _is_ff_sentinel(row.key) or _padded_key(key, len(row.key)) <= row.key:
                page_index = row.child_page_index
                break
        else:
            return None
    return None


def _title_row_offsets(data: bytes) -> set[int]:
    starts = {0}
    last_nonzero = -1
    for index in range(len(data) - 1, -1, -1):
        if data[index]:
            last_nonzero = index
            break
    pos = 0
    while True:
        found = data.find(b"\x1f\x0a", pos)
        if found < 0:
            break
        next_pos = found + 2
        if next_pos <= last_nonzero:
            starts.add(next_pos)
        pos = next_pos
    return starts


def _element_for_pointer(elements: list[Any], pointer: IndexPointer) -> Any | None:
    for element in elements:
        if element.start and element.start <= pointer.block <= element.end:
            return element
    return None


def _component_offset(element: Any, pointer: IndexPointer) -> int:
    return (pointer.block - element.start) * BLOCK_SIZE + pointer.offset


def _load_component_bytes(idx_dir: Path, elements: list[Any], issues: list[VerifyIssue]) -> dict[str, ComponentBytes]:
    components: dict[str, ComponentBytes] = {}
    occupied: list[tuple[int, int, str]] = []
    for element in elements:
        if element.start and element.end < element.start:
            issues.append(VerifyIssue("error", "invalid_block_range", "component end block is before start block", element.filename))
            continue
        if element.start:
            occupied.append((element.start, element.end, element.filename))

        path = find_case_insensitive(idx_dir, element.filename)
        if path is None:
            if element.type not in {0xF1, 0xF2}:
                issues.append(VerifyIssue("error", "missing_component_file", "declared component file is missing", element.filename))
            continue
        if element.type in {0xF1, 0xF2}:
            continue
        try:
            header = parse_sseddata_header(path)
            expanded = expand_sseddata_file(path)
        except Exception as exc:
            issues.append(VerifyIssue("error", "sseddata_expand_failed", str(exc), element.filename))
            continue
        if header["kind"] != element.type:
            issues.append(VerifyIssue("error", "sseddata_kind_mismatch", f"header kind {header['kind']!r} != SSEDINFO type {element.type:02x}", element.filename))
        if header["start_block"] != element.start or header["end_block"] != element.end:
            issues.append(
                VerifyIssue(
                    "error",
                    "sseddata_range_mismatch",
                    f"header range {header['start_block']}..{header['end_block']} != SSEDINFO range {element.start}..{element.end}",
                    element.filename,
                )
            )
        expected_size = element.block_count * BLOCK_SIZE
        if len(expanded) != expected_size:
            issues.append(VerifyIssue("error", "expanded_size_mismatch", f"expanded size {len(expanded)} != declared {expected_size}", element.filename))
        components[element.filename.upper()] = ComponentBytes(element.filename, element.type, element.start, element.end, path, expanded)

    for previous, current in zip(sorted(occupied), sorted(occupied)[1:]):
        if current[0] <= previous[1]:
            issues.append(
                VerifyIssue(
                    "error",
                    "component_block_overlap",
                    f"{previous[2]} {previous[0]}..{previous[1]} overlaps {current[2]} {current[0]}..{current[1]}",
                )
            )
    return components


def _verify_index_structure(component: ComponentBytes, issues: list[VerifyIssue]) -> dict[str, int]:
    data = component.expanded
    pages = index_page_infos(data, component.type, component.start)
    metrics = {
        "pages": len(pages),
        "branch_pages": 0,
        "leaf_pages": 0,
        "leaf_keys": 0,
        "duplicate_keys": 0,
        "traversal_checks": 0,
    }
    leaf_sequence: list[tuple[bytes, int]] = []

    for page in pages.values():
        if page.leaf:
            metrics["leaf_pages"] += 1
            metrics["leaf_keys"] += len(page.lookup_keys)
            leaf_sequence.extend((key, page.page_index) for key in page.lookup_keys)
            continue

        metrics["branch_pages"] += 1
        if page.word & 0x2000:
            if not page.branch_rows or not _is_ff_sentinel(page.branch_rows[-1].key):
                issues.append(VerifyIssue("error", "missing_ff_sentinel", "final branch sibling does not end with an ff sentinel row", component.filename, page.page_index))
        for row in page.branch_rows:
            child = pages.get(row.child_page_index)
            if child is None:
                issues.append(VerifyIssue("error", "invalid_child_block", f"child block {row.child_block} is outside component", component.filename, page.page_index, row.row_index))
                continue
            if _is_ff_sentinel(row.key):
                if row.row_index != len(page.branch_rows) or not (page.word & 0x2000):
                    issues.append(VerifyIssue("error", "unexpected_ff_sentinel", "ff sentinel appears outside the final row of a final sibling branch page", component.filename, page.page_index, row.row_index))
                continue
            expected = _padded_key(child.high_key, len(row.key))
            if row.key != expected:
                issues.append(
                    VerifyIssue(
                        "error",
                        "branch_upper_bound_mismatch",
                        f"branch row key {row.key.hex()} does not match child high key {expected.hex()}",
                        component.filename,
                        page.page_index,
                        row.row_index,
                    )
                )

    positions: dict[bytes, list[int]] = {}
    for index, (key, _page_index) in enumerate(leaf_sequence):
        positions.setdefault(key, []).append(index)
    for key, rows in positions.items():
        if len(rows) <= 1:
            continue
        metrics["duplicate_keys"] += 1
        if rows != list(range(rows[0], rows[-1] + 1)):
            issues.append(VerifyIssue("error", "non_contiguous_duplicate_key", f"duplicate key {key.hex()} is not contiguous in leaf order", component.filename))

    for key, rows in positions.items():
        first_expected_page = leaf_sequence[rows[0]][1]
        found_page = traverse_index_key(data, component.start, key)
        metrics["traversal_checks"] += 1
        if found_page != first_expected_page:
            issues.append(
                VerifyIssue(
                    "error",
                    "index_traversal_mismatch",
                    f"lookup for {key.hex()} reached page {found_page}, expected first matching leaf page {first_expected_page}",
                    component.filename,
                )
            )
    return metrics


def _verify_index_rows(
    component: ComponentBytes,
    elements: list[Any],
    components: dict[str, ComponentBytes],
    entry_offsets: set[int],
    title_offsets: dict[str, set[int]],
    issues: list[VerifyIssue],
) -> dict[str, int]:
    rows: list[dict[str, Any]] = []
    result = scan_index_component(
        component.filename,
        component.type,
        component.expanded,
        component.start,
        gaiji="placeholder",
        gaiji_map={},
        emit_row=rows.append,
    )
    if result.unknown_leaf_bytes:
        issues.append(VerifyIssue("error", "index_unknown_leaf_bytes", f"{result.unknown_leaf_bytes} leaf subrecords could not be parsed", component.filename))

    pointer_checks = 0
    for row in rows:
        if row.get("kind") != "leaf":
            continue
        for target_name in ("body", "title"):
            pointer_data = row.get(target_name)
            if not isinstance(pointer_data, dict):
                continue
            pointer = IndexPointer(block=int(pointer_data["block"]), offset=int(pointer_data["offset"]))
            pointer_checks += 1
            element = _element_for_pointer(elements, pointer)
            if element is None:
                issues.append(VerifyIssue("error", "pointer_outside_components", f"{target_name} pointer {pointer.block}:{pointer.offset} points outside declared components", component.filename, row.get("page_index"), row.get("row_index")))
                continue
            target_component = components.get(element.filename.upper())
            if target_component is None:
                issues.append(VerifyIssue("error", "pointer_component_unloaded", f"{target_name} pointer targets unloaded component {element.filename}", component.filename, row.get("page_index"), row.get("row_index")))
                continue
            offset = _component_offset(element, pointer)
            if pointer.offset >= BLOCK_SIZE or offset >= len(target_component.expanded):
                issues.append(VerifyIssue("error", "pointer_offset_out_of_range", f"{target_name} pointer {pointer.block}:{pointer.offset} is outside {element.filename}", component.filename, row.get("page_index"), row.get("row_index")))
                continue
            if target_name == "body" and element.type == 0x00 and offset not in entry_offsets:
                issues.append(VerifyIssue("error", "body_pointer_not_entry_boundary", f"body pointer offset {offset} is not a HONMON entry boundary", component.filename, row.get("page_index"), row.get("row_index")))
            if target_name == "title" and element.type in TITLE_TYPES:
                starts = title_offsets.get(element.filename.upper(), set())
                if offset not in starts:
                    issues.append(VerifyIssue("error", "title_pointer_not_row_boundary", f"title pointer offset {offset} is not a title row boundary in {element.filename}", component.filename, row.get("page_index"), row.get("row_index")))
    return {"leaf_rows": result.leaf_rows, "search_groups": result.search_groups, "pointer_checks": pointer_checks}


def _verify_gaiji(idx_path: Path, elements: list[Any], issues: list[VerifyIssue]) -> dict[str, Any]:
    idx_dir = idx_path.parent
    uni_resources: list[UniResource] = []
    for path in sorted(idx_dir.glob("*.uni")) + sorted(idx_dir.glob("*.UNI")):
        parsed = parse_uni_resource(path)
        if parsed is None:
            issues.append(VerifyIssue("error", "uni_parse_failed", "could not parse .uni resource", path.name))
            continue
        if parsed.trailing_bytes:
            issues.append(VerifyIssue("warning", "uni_trailing_bytes", f".uni has {parsed.trailing_bytes} trailing bytes", path.name))
        uni_resources.append(parsed)
    uni = uni_resources[0] if uni_resources else None

    ga16_paths: list[Path] = []
    for element in elements:
        if element.type in {0xF1, 0xF2}:
            path = find_case_insensitive(idx_dir, element.filename)
            if path is not None:
                ga16_paths.append(path)
    for name in ("GA16HALF", "GA16FULL"):
        path = find_case_insensitive(idx_dir, name)
        if path is not None and path not in ga16_paths:
            ga16_paths.append(path)

    coverage: dict[str, set[str]] = {"half": set(), "full": set()}
    resources: list[Ga16Resource] = []
    for path in ga16_paths:
        parsed = parse_ga16_resource(path)
        if parsed is None:
            issues.append(VerifyIssue("error", "ga16_parse_failed", "could not parse GA16 resource", path.name))
            continue
        data = path.read_bytes()
        required = parsed.data_offset + parsed.count * parsed.glyph_bytes
        if len(data) < required:
            issues.append(VerifyIssue("error", "ga16_truncated", f"GA16 file has {len(data)} bytes, expected at least {required}", path.name))
        section = ga16_section_for_path(path)
        if section is not None:
            for code, _index, _source in iter_ga16_code_sources(parsed, uni):
                coverage[section].add(code.lower())
        resources.append(parsed)

    uni_counts = {"half": 0, "full": 0}
    missing_bitmap = 0
    if uni is not None:
        for record in uni.records:
            if record.section in uni_counts:
                uni_counts[record.section] += 1
                if record.code.lower() not in coverage.get(record.section, set()):
                    missing_bitmap += 1
                    issues.append(VerifyIssue("error", "uni_code_without_ga16_glyph", f"{record.section} gaiji {record.code} has no matching GA16 glyph slot", uni.path.name))

    return {
        "uni_files": len(uni_resources),
        "uni_records": sum(len(resource.records) for resource in uni_resources),
        "uni_half_records": uni_counts["half"],
        "uni_full_records": uni_counts["full"],
        "ga16_files": len(resources),
        "ga16_half_codes": len(coverage["half"]),
        "ga16_full_codes": len(coverage["full"]),
        "uni_codes_missing_ga16": missing_bitmap,
    }


def verify_written_package(path: Path) -> WriterVerifyReport:
    idx_path = find_ssedinfo_path(path)
    idx_dir = idx_path.parent
    title, elements = parse_ssedinfo(idx_path)
    issues: list[VerifyIssue] = []
    components = _load_component_bytes(idx_dir, elements, issues)

    honmon = next((component for component in components.values() if component.type == 0x00), None)
    entry_offsets: set[int] = set()
    if honmon is None:
        issues.append(VerifyIssue("error", "missing_honmon", "package has no loaded HONMON.DIC component"))
    else:
        entry_offsets = {start for start, _end in iter_entry_slices_with_boundaries(honmon.expanded)}

    title_offsets = {
        component.filename.upper(): _title_row_offsets(component.expanded)
        for component in components.values()
        if component.type in TITLE_TYPES
    }

    index_metrics: dict[str, Any] = {}
    for component in components.values():
        if component.type not in WRITER_INDEX_TYPES:
            continue
        structure = _verify_index_structure(component, issues)
        rows = _verify_index_rows(component, elements, components, entry_offsets, title_offsets, issues)
        index_metrics[component.filename] = {**structure, **rows}

    gaiji_metrics = _verify_gaiji(idx_path, elements, issues)
    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    metrics = {
        "components": len(elements),
        "loaded_components": len(components),
        "entries": len(entry_offsets),
        "titles": {name: len(offsets) for name, offsets in sorted(title_offsets.items())},
        "indexes": index_metrics,
        "gaiji": gaiji_metrics,
    }
    return WriterVerifyReport(
        package_path=str(idx_dir),
        idx_path=str(idx_path),
        title=title,
        ok=error_count == 0,
        errors=error_count,
        warnings=warning_count,
        metrics=metrics,
        issues=[issue.as_dict() for issue in issues],
    )
