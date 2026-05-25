"""Reader-facing dictionary handle.

SSED packages currently contain one logical dictionary.  Keeping the handle
explicit makes the Python API line up with the future Rust/C ABI shape without
requiring callers to learn package internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, TYPE_CHECKING

from .index import IndexParse
from .model import Component, Entry, SearchProfile
from .reader import EntryView, HomeSurface
from .render import HtmlProfile
from .search import SearchHit, SearchResults

if TYPE_CHECKING:
    from .package_entries import EntryStore
    from .package_search import IndexStore
    from .surfaces import SurfaceStore


@dataclass(frozen=True)
class Dictionary:
    """Single logical dictionary inside a LogoVista package."""

    dictionary_id: str
    title: str
    entry_store: "EntryStore"
    index_store: "IndexStore"
    surface_store: "SurfaceStore"

    def iter_entries(
        self,
        *,
        limit: int | None = None,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Iterable[Entry]:
        return self.entry_store.iter_entries(limit=limit, max_bytes=max_bytes, cancel=cancel)

    def entry_at(self, address, *, max_bytes: int = 64 * 1024) -> Entry:
        return self.entry_store.entry_at(address, max_bytes=max_bytes)

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        return self.index_store.titles(component=component, limit=limit)

    def indexes(self, component: str | Component | None = None) -> dict[str, IndexParse]:
        return self.index_store.indexes(component=component)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
        debug: bool = False,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> SearchResults:
        return self.index_store.search(
            query,
            limit=limit,
            profile=profile,
            debug=debug,
            max_bytes=max_bytes,
            cancel=cancel,
        )

    def search_entries(
        self,
        term: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
    ) -> list[Entry]:
        return [self.entry_store.entry_for_hit(hit) for hit in self.search(term, limit=limit, profile=profile).hits]

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        return self.entry_store.entry_for_hit(hit)

    def entry_view_for_hit(self, hit: SearchHit) -> EntryView:
        return self.entry_store.entry_view_for_hit(hit)

    def entry_view_at(self, address, *, max_bytes: int = 64 * 1024) -> EntryView:
        return self.entry_store.entry_view_at(address, max_bytes=max_bytes)

    def home_surfaces(self) -> tuple[HomeSurface, ...]:
        return self.surface_store.home_surfaces()

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.entry_store.render_hit_html(
            hit,
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.entry_store.render_hit_text(hit)
