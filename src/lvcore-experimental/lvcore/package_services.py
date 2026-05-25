"""Typed service dependency records for the SSED reader core."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Callable

from .body_source import BodySourceInfo, SidecarInfo
from .diagnostics import Location
from .index import IndexParse, IndexRow
from .model import Address, Component, ComponentRole, Entry, PackageInfo, SearchProfile
from .render import HtmlProfile
from .scan import ScanBudget
from .search import SearchHit
from .ssed import SsedData
from .text import DecodeResult


@dataclass(frozen=True)
class EntryStoreDeps:
    """Dependencies required by entry slicing and dense-body resolution."""

    info: PackageInfo
    components_by_role: Callable[[ComponentRole], tuple[Component, ...]]
    component_for_address: Callable[[Address, ComponentRole | None], Component | None]
    data: Callable[[str | Component], SsedData]
    relative_offset: Callable[[Component, Address], int]
    qualified_address: Callable[[Address, ComponentRole | None], Address]
    location_for_address: Callable[[Address, ComponentRole | None], Location]
    honmon_component: Callable[[], Component | None]
    decode_text_stream: Callable[[bytes, bool], DecodeResult]
    gaiji_mapping: Callable[[], dict[str, str]]
    sidecar_file_candidates: Callable[[], list[Path]]
    body_sidecars: Callable[[bool, bool], tuple[SidecarInfo, ...]]
    sqlite_connection_for_sidecar: Callable[[Path, str], sqlite3.Connection]
    indexes: Callable[[str | Component | None], dict[str, IndexParse]]
    iter_index_rows_fast: Callable[
        [Component, str | None, SearchProfile | None, ScanBudget | None, int | None, Callable[[], bool] | None],
        Iterable[IndexRow],
    ]
    make_hit: Callable[[int, str, str, SearchProfile, str, IndexRow, str, bool], SearchHit]
    row_display_key: Callable[[IndexRow, bool], str]
    row_dedupe_key: Callable[[IndexRow], tuple[int, int, int, int]]
    is_backward_index: Callable[[str], bool]
    render_entry_html: Callable[[Entry, HtmlProfile | str, bool], str]
    render_entry_text: Callable[[Entry], str]


@dataclass(frozen=True)
class IndexStoreDeps:
    """Dependencies required by title/index/search traversal."""

    info: PackageInfo
    component: Callable[[str], Component | None]
    components_by_role: Callable[[ComponentRole], tuple[Component, ...]]
    component_for_address: Callable[[Address, ComponentRole | None], Component | None]
    data: Callable[[str | Component], SsedData]
    expanded: Callable[[str | Component], bytes]
    relative_offset: Callable[[Component, Address], int]
    qualified_address: Callable[[Address, ComponentRole | None], Address]
    location_for_address: Callable[[Address, ComponentRole | None], Location]
    gaiji_mapping: Callable[[], dict[str, str]]
    decode_component: Callable[[str | Component], DecodeResult]
    decode_text_stream: Callable[[bytes, bool], DecodeResult]
    body_source: Callable[[bool], BodySourceInfo]
