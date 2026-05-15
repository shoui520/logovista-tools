"""Dictionary-local gaiji resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import plistlib
import re
from typing import Any, Iterable

from .ssed import CaseFoldedDirectory, is_metadata_noise_path


UNI_MAGIC = b"Ver2  "
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
PLIST_GAIJI_NAMES = {"gaiji.plist", "gaijis.plist", "resourcescopy.plist", "gaijiicon.plist"}
CODE_RE = re.compile(r"(?i)([ab][0-9a-f]{3})")
KNOWN_GAIJI_IMAGE_DIRS = (
    "Templates",
    "img",
    "res",
    "OTHER",
    "html",
    "HANREI/img",
    "HANREI/contents/hanrei/img",
    "HANREI/contents/hanrei/img/gai",
    "resource/kmkimages",
    "resource/kmkimges",
    "appendix/img",
    "manual/contents/img",
)


class GaijiDisplayStatus(str, Enum):
    UNICODE_MAPPED = "unicode_mapped"
    BITMAP_BACKED = "bitmap_backed"
    IMAGE_BACKED = "image_backed"
    FORMATTING_HELPER = "formatting_helper"
    RENDERER_ENTRY_BACKED = "renderer_entry_backed"
    UNRESOLVED = "unresolved"


class GaijiResolutionReason(str, Enum):
    UNICODE_MAPPING = "unicode_mapping"
    UNICODE_FALLBACK_MAPPING = "unicode_fallback_mapping"
    UNI_RECORD_ORDER_GA16 = "uni_record_order_ga16"
    JIS_GRID_GA16 = "jis_grid_ga16"
    IMAGE_ASSET = "image_asset"
    PLIST_MAPPING = "plist_mapping"
    BLANK_BITMAP_FORMATTING_HELPER = "blank_bitmap_formatting_helper"
    FULLWIDTH_FORMATTING_HELPER_CANDIDATE = "fullwidth_formatting_helper_candidate"
    RENDERER_CONTEXTUAL_REQUIRED = "renderer_contextual_required"
    MISSING_UNICODE_MAPPING = "missing_unicode_mapping"
    MISSING_BITMAP_RESOURCE = "missing_bitmap_resource"
    MISSING_IMAGE_RESOURCE = "missing_image_resource"
    MISSING_GAIJI_TABLE = "missing_gaiji_table"
    UNSUPPORTED_GAIJI_PLANE_RANGE = "unsupported_gaiji_plane_range"
    MALFORMED_GAIJI_CODE = "malformed_gaiji_code"
    IMAGE_RESOURCE_AMBIGUOUS = "image_resource_ambiguous"
    PLIST_MAPPING_AMBIGUOUS = "plist_mapping_ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UniRecord:
    section: str
    index: int
    code: str
    display: str
    fallback: str
    legacy: str
    raw: bytes
    source: str = "uni"


@dataclass(frozen=True)
class GaijiMap:
    records: tuple[UniRecord, ...]
    mapping: dict[str, str]
    paths: tuple[Path, ...]
    records_by_code: dict[str, UniRecord]
    plist_unicode_mappings: int = 0
    plist_mapping_ambiguous: int = 0
    plist_parse_failures: int = 0

    def resolve(self, code: str) -> str | None:
        return self.mapping.get(code.lower())

    def record_for_code(self, code: str) -> UniRecord | None:
        return self.records_by_code.get(code.lower())


@dataclass(frozen=True)
class Ga16Resource:
    path: Path
    width: int
    height: int
    start_code: int
    count: int
    glyph_bytes: int
    section: str = "unknown"
    data_offset: int = 2048

    def code_for_index(self, index: int) -> int:
        return gaiji_grid_code_for_index(self.start_code, index)

    def index_for_code(self, code: int) -> int:
        return gaiji_grid_index_for_code(self.start_code, code)

    def glyph(self, code: int) -> bytes | None:
        index = self.index_for_code(code)
        return self.glyph_by_index(index)

    def glyph_by_index(self, index: int) -> bytes | None:
        if index < 0 or index >= self.count:
            return None
        start = self.data_offset + index * self.glyph_bytes
        end = start + self.glyph_bytes
        try:
            if end > self.path.stat().st_size:
                return None
            with self.path.open("rb") as fh:
                fh.seek(start)
                data = fh.read(self.glyph_bytes)
        except OSError:
            return None
        return data if len(data) == self.glyph_bytes else None


@dataclass(frozen=True)
class ImageGaijiResource:
    code: str
    path: Path
    source: str
    key: str | None = None


@dataclass(frozen=True)
class GaijiSources:
    uni_files: tuple[Path, ...]
    ga16_files: tuple[Path, ...]
    plist_files: tuple[Path, ...]
    image_directories: tuple[Path, ...]


def ga16_row_size(width: int) -> int:
    return (width + 7) // 8


def ga16_glyph_size(width: int, height: int) -> int:
    return ga16_row_size(width) * height


def gaiji_grid_code_for_index(start_code: int, index: int) -> int:
    row = (start_code >> 8) & 0xFF
    cell = start_code & 0xFF
    if index < 0:
        raise ValueError("gaiji index must be non-negative")
    if not 0x21 <= cell <= 0x7E:
        return start_code + index
    cell_index = (cell - 0x21) + index
    row += cell_index // 0x5E
    cell = 0x21 + (cell_index % 0x5E)
    return (row << 8) | cell


def gaiji_grid_index_for_code(start_code: int, code: int) -> int:
    start_row = (start_code >> 8) & 0xFF
    start_cell = start_code & 0xFF
    row = (code >> 8) & 0xFF
    cell = code & 0xFF
    if not (0x21 <= start_cell <= 0x7E and 0x21 <= cell <= 0x7E):
        return code - start_code
    return (row - start_row) * 0x5E + (cell - start_cell)


def parse_ga16(path: Path) -> Ga16Resource | None:
    try:
        with path.open("rb") as fh:
            data = fh.read(16)
    except OSError:
        return None
    if len(data) < 16:
        return None
    width = data[8]
    height = data[9]
    if width <= 0 or height <= 0:
        return None
    count = int.from_bytes(data[12:14], "big")
    return Ga16Resource(
        path=path,
        width=width,
        height=height,
        start_code=int.from_bytes(data[10:12], "big"),
        count=count,
        glyph_bytes=ga16_glyph_size(width, height),
        section=ga16_section_for_name(path.name, width=width),
    )


def ga16_section_for_name(name: str, *, width: int | None = None) -> str:
    upper = name.upper()
    if "HALF" in upper or upper.startswith("GAI16H"):
        return "half"
    if "FULL" in upper or upper.startswith("GAI16F"):
        return "full"
    if width == 8:
        return "half"
    if width == 16:
        return "full"
    return "unknown"


def decode_utf16_units(values: tuple[int, ...]) -> str:
    data = b"".join(value.to_bytes(2, "big") for value in values if value)
    if not data:
        return ""
    return data.decode("utf-16-be", errors="ignore")


def _parse_records(data: bytes, offset: int, count: int, record_size: int, section: str) -> tuple[list[UniRecord], int]:
    records: list[UniRecord] = []
    for index in range(count):
        start = offset + index * record_size
        end = start + record_size
        if end > len(data):
            break
        raw = data[start:end]
        code = raw[:2].hex().lower()
        fields = tuple(int.from_bytes(raw[pos : pos + 2], "big") for pos in range(0, len(raw), 2))
        display_units = fields[2:4]
        fallback_units = fields[4:6]
        legacy_units = fields[6:8] if len(fields) >= 8 else ()
        records.append(
            UniRecord(
                section=section,
                index=index,
                code=code,
                display=decode_utf16_units(display_units),
                fallback=decode_utf16_units(fallback_units),
                legacy=decode_utf16_units(legacy_units),
                raw=raw,
            )
        )
    return records, offset + count * record_size


def parse_uni(path: Path) -> tuple[UniRecord, ...]:
    data = path.read_bytes()
    if len(data) < 4:
        return ()
    if data.startswith(UNI_MAGIC):
        half_count = int.from_bytes(data[6:10], "big")
        half, pos = _parse_records(data, 10, half_count, 16, "half")
        if pos + 4 > len(data):
            return tuple(half)
        full_count = int.from_bytes(data[pos : pos + 4], "big")
        full, _pos = _parse_records(data, pos + 4, full_count, 16, "full")
        return tuple(half + full)

    half_count = int.from_bytes(data[:4], "big")
    half, pos = _parse_records(data, 4, half_count, 12, "half")
    if pos == len(data):
        return tuple(half)
    if pos + 4 > len(data):
        return tuple(half)
    full_count = int.from_bytes(data[pos : pos + 4], "big")
    full, _pos = _parse_records(data, pos + 4, full_count, 12, "full")
    return tuple(half + full)


def _case_insensitive_child(parent: Path, name: str) -> Path | None:
    return CaseFoldedDirectory.from_path(parent).find(name)


def _case_insensitive_path(root: Path, relative: str) -> Path | None:
    current = root
    for part in Path(relative.replace("\\", "/")).parts:
        child = _case_insensitive_child(current, part)
        if child is None:
            return None
        current = child
    return current


def _dedupe_existing(paths: Iterable[Path]) -> tuple[Path, ...]:
    out: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return tuple(sorted(out, key=lambda item: str(item).lower()))


def _iter_files_bounded(path: Path, *, max_depth: int = 4) -> Iterable[Path]:
    if max_depth < 0:
        return
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return
    for child in children:
        if is_metadata_noise_path(child):
            continue
        if child.is_file():
            yield child
        elif child.is_dir() and max_depth > 0:
            yield from _iter_files_bounded(child, max_depth=max_depth - 1)


def _exinfo_uni_names(root: Path) -> list[Path]:
    paths: list[Path] = []
    exinfo = _case_insensitive_child(root, "EXINFO.INI")
    if exinfo is None or not exinfo.exists():
        return paths
    for raw_line in exinfo.read_text(encoding="cp932", errors="ignore").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        if not key.strip().upper().startswith("GAIJI"):
            continue
        value = value.strip().strip('"').replace("\\", "/")
        if value.lower().endswith(".uni"):
            candidate = Path(value)
            if candidate.is_absolute():
                paths.append(candidate)
                continue
            resolved = _case_insensitive_path(root, value)
            paths.append(resolved if resolved is not None else root / candidate)
    return paths


def resolve_gaiji_sources(
    root: Path,
    dict_id: str,
    *,
    component_paths: Iterable[Path] = (),
) -> GaijiSources:
    uni_candidates = [
        *(
            candidate
            for name in (f"{dict_id}.uni", f"{dict_id}.UNI", f"{dict_id.upper()}.uni", f"{dict_id.upper()}.UNI")
            if (candidate := _case_insensitive_child(root, name)) is not None
        ),
        *_exinfo_uni_names(root),
        *CaseFoldedDirectory.from_path(root).files_with_suffix(".uni"),
    ]

    known_dirs: list[Path] = [root]
    root_key = root.name.lower()
    dict_key = dict_id.lower()
    bare_root_key = root_key.removeprefix("_dct_")
    sibling_keys = {key for key in (root_key, dict_key, bare_root_key) if key}
    try:
        for sibling in sorted(root.parent.iterdir(), key=lambda item: item.name.lower()):
            sibling_name = sibling.name.lower()
            if (
                sibling.is_dir()
                and sibling_name.endswith("_gaiji")
                and any(sibling_name in {f"{key}_gaiji", f"_{key}_gaiji"} or sibling_name.startswith(f"{key}_") for key in sibling_keys)
            ):
                known_dirs.append(sibling)
    except OSError:
        pass
    for relative in KNOWN_GAIJI_IMAGE_DIRS:
        resolved = _case_insensitive_path(root, relative)
        if resolved is not None and resolved.is_dir():
            known_dirs.append(resolved)

    plist_candidates: list[Path] = []
    for directory in known_dirs:
        for name in sorted(PLIST_GAIJI_NAMES):
            candidate = _case_insensitive_child(directory, name)
            if candidate is not None and candidate.is_file():
                plist_candidates.append(candidate)

    image_dirs = [path for path in known_dirs if path.is_dir()]
    for plist in plist_candidates:
        image_dirs.append(plist.parent)

    ga16_candidates: list[Path] = list(component_paths)
    for directory in known_dirs:
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children:
            if is_metadata_noise_path(child):
                continue
            upper = child.name.upper()
            if child.is_file() and upper.startswith(("GA16", "GAI16")):
                ga16_candidates.append(child)

    return GaijiSources(
        uni_files=_dedupe_existing(uni_candidates),
        ga16_files=_dedupe_existing(ga16_candidates),
        plist_files=_dedupe_existing(plist_candidates),
        image_directories=_dedupe_existing(image_dirs),
    )


def _plist_candidates(root: Path, sources: GaijiSources | None = None) -> tuple[Path, ...]:
    return sources.plist_files if sources is not None else resolve_gaiji_sources(root, "").plist_files


def _looks_like_code(value: str) -> str | None:
    text = value.strip().lower()
    if len(text) == 4 and CODE_RE.fullmatch(text):
        return text
    match = CODE_RE.search(text)
    return match.group(1).lower() if match else None


def _looks_like_image_path(value: str) -> bool:
    return Path(value.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES


def _walk_plist(value: Any, *, key_path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    rows: list[tuple[tuple[str, ...], Any]] = [(key_path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            rows.extend(_walk_plist(item, key_path=(*key_path, str(key))))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(_walk_plist(item, key_path=(*key_path, str(index))))
    return rows


def _load_plist(path: Path) -> Any | None:
    try:
        with path.open("rb") as fh:
            return plistlib.load(fh)
    except Exception:
        return None


def plist_unicode_mappings(root: Path, sources: GaijiSources | None = None) -> tuple[dict[str, str], int, int, int]:
    mappings: dict[str, str] = {}
    mapped = 0
    ambiguous = 0
    parse_failures = 0
    for path in _plist_candidates(root, sources):
        data = _load_plist(path)
        if data is None:
            parse_failures += 1
            continue
        for key_path, value in _walk_plist(data):
            if not isinstance(value, str) or _looks_like_image_path(value):
                continue
            key_code = next((_looks_like_code(part) for part in reversed(key_path) if _looks_like_code(part)), None)
            if key_code is None:
                continue
            text = value.strip()
            if not text:
                continue
            if _looks_like_code(text) or len(text) > 8:
                ambiguous += 1
                continue
            if key_code not in mappings:
                mappings[key_code] = text
                mapped += 1
    return mappings, mapped, ambiguous, parse_failures


def _find_unique_named_file(sources: GaijiSources, name: str) -> Path | None:
    matches = [
        path
        for directory in sources.image_directories
        for path in _iter_files_bounded(directory, max_depth=4)
        if path.name.lower() == name.lower()
    ]
    matches = [match for match in matches if match.suffix.lower() in IMAGE_SUFFIXES]
    unique = _dedupe_existing(matches)
    return unique[0] if len(unique) == 1 else None


def load_image_gaiji_resources(root: Path, sources: GaijiSources | None = None) -> tuple[ImageGaijiResource, ...]:
    sources = sources or resolve_gaiji_sources(root, "")
    resources: dict[str, ImageGaijiResource] = {}
    for path in _plist_candidates(root, sources):
        data = _load_plist(path)
        if data is None:
            continue
        for key_path, value in _walk_plist(data):
            if not isinstance(value, str) or not _looks_like_image_path(value):
                continue
            key_code = next((_looks_like_code(part) for part in reversed(key_path) if _looks_like_code(part)), None)
            value_code = _looks_like_code(value)
            code = key_code or value_code
            if code is None:
                continue
            candidate = Path(value.replace("\\", "/"))
            full_path = candidate if candidate.is_absolute() else root / candidate
            if not full_path.exists():
                resolved = _case_insensitive_path(root, value)
                full_path = resolved if resolved is not None else full_path
            if not full_path.exists():
                found = _find_unique_named_file(sources, candidate.name)
                if found is None:
                    continue
                full_path = found
            resources.setdefault(code, ImageGaijiResource(code=code, path=full_path, source=path.name, key="/".join(key_path)))
    image_paths = [
        path
        for directory in sources.image_directories
        for path in _iter_files_bounded(directory, max_depth=4)
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    for path in sorted(image_paths, key=lambda item: str(item).lower()):
        code = _looks_like_code(path.stem)
        if code is None:
            continue
        resources.setdefault(code, ImageGaijiResource(code=code, path=path, source="code_shaped_filename"))
    return tuple(resources[code] for code in sorted(resources))


def load_gaiji_map(root: Path, dict_id: str, sources: GaijiSources | None = None) -> GaijiMap:
    sources = sources or resolve_gaiji_sources(root, dict_id)
    candidates = list(sources.uni_files)
    records: list[UniRecord] = []
    paths: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        parsed = parse_uni(path)
        if parsed:
            records.extend(parsed)
            paths.append(path)
    mapping = {record.code: (record.display or record.fallback) for record in records if record.display or record.fallback}
    plist_mappings, plist_count, plist_ambiguous, plist_failures = plist_unicode_mappings(root, sources)
    for code, display in plist_mappings.items():
        if code in mapping:
            continue
        records.append(
            UniRecord(
                section="unknown",
                index=-1,
                code=code,
                display=display,
                fallback="",
                legacy="",
                raw=b"",
                source="plist",
            )
        )
        mapping[code] = display
    records_by_code: dict[str, UniRecord] = {}
    for record in records:
        records_by_code.setdefault(record.code, record)
    return GaijiMap(
        records=tuple(records),
        mapping=mapping,
        paths=tuple(paths),
        records_by_code=records_by_code,
        plist_unicode_mappings=plist_count,
        plist_mapping_ambiguous=plist_ambiguous,
        plist_parse_failures=plist_failures,
    )
