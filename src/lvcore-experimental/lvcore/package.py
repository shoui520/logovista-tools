"""High-level package API."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .detect import detect_family
from .errors import UnsupportedPackageError
from .gaiji import Ga16Resource, GaijiMap, load_gaiji_map, parse_ga16
from .index import IndexParse, parse_index
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile
from .render import HtmlProfile, render_html, render_text
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

    @property
    def title(self) -> str:
        return self.catalog.title

    @property
    def dict_id(self) -> str:
        return self.catalog.dict_id

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

    def honmon_component(self) -> Component | None:
        candidates = self.components_by_role(ComponentRole.HONMON)
        return candidates[0] if candidates else None

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

    def iter_entry_slices(self, *, limit: int | None = None) -> Iterable[tuple[int, int]]:
        honmon = self.honmon_component()
        if honmon is None or honmon.path is None:
            return
        reader = self.data(honmon)
        starts = self._marker_offsets(reader, limit=limit)
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

    def entry_at(self, address: Address, *, max_bytes: int = 512 * 1024) -> Entry:
        honmon = self.component_for_address(address, role=ComponentRole.HONMON)
        if honmon is None:
            raise KeyError(f"no HONMON component contains address {address}")
        reader = self.data(honmon)
        start = (address.block - honmon.start_block) * BLOCK_SIZE + address.offset
        data = reader.read(start, max_bytes)
        next_marker = data.find(ENTRY_MARKER, 1)
        end_offset = start + (next_marker if next_marker > 0 else len(data))
        decoded = decode_text_stream(data[: end_offset - start], self.gaiji.mapping)
        lines = [line.strip() for line in decoded.text.splitlines() if line.strip()]
        headword = lines[0] if lines else ""
        return Entry(
            address=Address(address.block, address.offset, honmon.name),
            end_address=Address(honmon.start_block + end_offset // BLOCK_SIZE, end_offset % BLOCK_SIZE, honmon.name),
            headword=headword,
            text=decoded.text.strip("\x00"),
            spans=decoded.spans,
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

    def indexes(self, component: str | Component | None = None) -> dict[str, IndexParse]:
        comps = [component] if component is not None else list(self.components_by_role(ComponentRole.INDEX))
        out: dict[str, IndexParse] = {}
        for comp in comps:
            item = self.component(comp) if isinstance(comp, str) else comp
            if item is None or item.path is None:
                continue
            out[item.name] = parse_index(self.expanded(item), item.start_block, item.type, self.gaiji.mapping)
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

    def search_index(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[dict[str, object]]:
        if isinstance(profile, str):
            profile = SearchProfile(profile)
        hits: list[dict[str, object]] = []
        for name, parsed in self.indexes().items():
            component = self.component(name)
            if component is not None and not self._index_component_matches_profile(component, profile):
                continue
            for row in parsed.rows:
                if row.key == term or row.target_key == term:
                    hits.append({"component": name, **row.to_dict()})
                    if len(hits) >= limit:
                        return hits
        return hits

    def search_entries(self, term: str, *, limit: int = 20, profile: SearchProfile | str = SearchProfile.NATIVE) -> list[Entry]:
        if isinstance(profile, str):
            profile = SearchProfile(profile)
        out: list[Entry] = []
        seen: set[tuple[int, int]] = set()
        for hit in self.search_index(term, limit=limit * 4, profile=profile):
            body = hit.get("body")
            if not isinstance(body, dict):
                continue
            address = Address(int(body["block"]), int(body["offset"]))
            key = (address.block, address.offset)
            if key in seen:
                continue
            seen.add(key)
            try:
                out.append(self.entry_at(address))
            except Exception:
                continue
            if len(out) >= limit:
                break
        return out

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
            profile = HtmlProfile(profile)
        return render_html(entry.document(), profile=profile, include_diagnostics=include_diagnostics)

    def render_entry_text(self, entry: Entry) -> str:
        return render_text(entry.document())

    def entry_diagnostics(self, entry: Entry) -> tuple[object, ...]:
        return entry.document().diagnostics

    def validate(self, *, sample_entries: int = 3) -> dict[str, object]:
        diagnostics_by_severity = {"info": 0, "warning": 0, "error": 0}
        diagnostics_by_area: dict[str, int] = {}
        entries_checked = 0
        render_ok = 0
        entry_errors: list[str] = []

        for entry in self.iter_entries(limit=sample_entries):
            entries_checked += 1
            try:
                document = entry.document()
                render_html(document)
                render_text(document)
                render_ok += 1
                for diagnostic in document.diagnostics:
                    diagnostics_by_severity[diagnostic.severity.value] = diagnostics_by_severity.get(diagnostic.severity.value, 0) + 1
                    diagnostics_by_area[diagnostic.area.value] = diagnostics_by_area.get(diagnostic.area.value, 0) + 1
            except Exception as exc:  # pragma: no cover - defensive validation report path
                diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                entry_errors.append(f"{entry.address.block}:{entry.address.offset}: {exc}")

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
        return {
            "package": self.info.to_dict(),
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
            "sample_entries_checked": entries_checked,
            "sample_entries_rendered": render_ok,
            "diagnostics": {
                "by_severity": diagnostics_by_severity,
                "by_area": diagnostics_by_area,
                "entry_errors": entry_errors,
            },
            "ok": diagnostics_by_severity.get("error", 0) == 0 and not entry_errors,
        }

    def summary(self) -> dict[str, object]:
        return {
            "package": self.info.to_dict(),
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
