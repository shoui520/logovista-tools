"""Reader-facing dictionary handle.

SSED packages currently contain one logical dictionary.  Keeping the handle
explicit makes the Python API line up with the future Rust/C ABI shape without
requiring callers to learn package internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, TYPE_CHECKING

from .json_types import JsonObject
from .model import Component, Entry, SearchProfile
from .render import HtmlProfile
from .search import SearchHit, SearchResults

if TYPE_CHECKING:
    from .index import IndexParse
    from .package import LogoVistaPackage


@dataclass(frozen=True)
class Dictionary:
    """Single logical dictionary inside a LogoVista package."""

    package: "LogoVistaPackage"
    dictionary_id: str
    title: str

    def iter_entries(
        self,
        *,
        limit: int | None = None,
        max_bytes: int | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Iterable[Entry]:
        return self.package._entry_store.iter_entries(limit=limit, max_bytes=max_bytes, cancel=cancel)

    def entry_at(self, address, *, max_bytes: int = 64 * 1024) -> Entry:
        return self.package._entry_store.entry_at(address, max_bytes=max_bytes)

    def titles(self, component: str | Component | None = None, *, limit: int | None = None) -> list[str]:
        return self.package._index_store.titles(component=component, limit=limit)

    def indexes(self, component: str | Component | None = None) -> dict[str, "IndexParse"]:
        return self.package._index_store.indexes(component=component)

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
        return self.package._index_store.search(
            query,
            limit=limit,
            profile=profile,
            debug=debug,
            max_bytes=max_bytes,
            cancel=cancel,
        )

    def search_index(
        self,
        term: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
    ) -> list[JsonObject]:
        return self.package._index_store.search_index(term, limit=limit, profile=profile)

    def search_entries(
        self,
        term: str,
        *,
        limit: int = 20,
        profile: SearchProfile | str = SearchProfile.NATIVE,
    ) -> list[Entry]:
        return self.package._index_store.search_entries(term, limit=limit, profile=profile)

    def entry_for_hit(self, hit: SearchHit) -> Entry:
        return self.package._index_store.entry_for_hit(hit)

    def render_hit_html(
        self,
        hit: SearchHit,
        *,
        profile: HtmlProfile | str = HtmlProfile.FRIENDLY,
        include_diagnostics: bool = False,
    ) -> str:
        return self.package._index_store.render_hit_html(
            hit,
            profile=profile,
            include_diagnostics=include_diagnostics,
        )

    def render_hit_text(self, hit: SearchHit) -> str:
        return self.package._index_store.render_hit_text(hit)
