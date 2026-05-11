"""Reader-facing native search models for lvcore."""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata
from typing import Any

from .diagnostics import Diagnostic
from .index import IndexRow
from .model import Address, SearchProfile


def kana_to_hiragana(value: str) -> str:
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30FA:
            out.append(chr(code - 0x60))
        elif ch == "ヴ":
            out.append("う")
        else:
            out.append(ch)
    return "".join(out)


def normalize_query(value: str) -> str:
    """Conservative native-index query normalization.

    This is not fuzzy search. It mirrors the common decoded index-key shape:
    NFKC compatibility folding, ASCII case folding, Japanese katakana reading
    folding, and removal of whitespace / dash-like separators that are usually
    absent from native lookup keys.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    folded: list[str] = []
    for ch in kana_to_hiragana(text):
        category = unicodedata.category(ch)
        if category.startswith("Z") or category == "Pd" or ch in {"・", "･", "-", "‐", "‑", "‒", "–", "—", "―", "−"}:
            continue
        if "a" <= ch <= "z":
            folded.append(ch.upper())
        else:
            folded.append(ch)
    return "".join(folded)


def query_candidates(query: str) -> tuple[str, ...]:
    raw = str(query or "").strip()
    normalized = normalize_query(raw)
    return tuple(dict.fromkeys(candidate for candidate in (raw, normalized) if candidate))


def natural_backward_key(value: str) -> str:
    return value[::-1]


@dataclass(frozen=True)
class SearchHit:
    id: int
    query: str
    normalized_query: str
    search_profile: SearchProfile
    package_id: str | None
    index_component: str
    display_key: str
    matched_key: str
    target_key: str | None
    heading: str
    body: Address
    title: Address
    tagged: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    page: int | None = None
    row: int | None = None
    raw_row: IndexRow | None = field(default=None, repr=False, compare=False)
    _package: Any = field(default=None, repr=False, compare=False)

    def entry(self):
        if self._package is None:
            raise RuntimeError("SearchHit is detached from a package")
        return self._package.entry_for_hit(self)

    def inspect(self) -> dict[str, Any]:
        return self.to_dict(debug=True)

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "package_id": self.package_id,
            "search_profile": self.search_profile.value,
            "index_component": self.index_component,
            "heading": self.heading,
            "display_key": self.display_key,
            "matched_key": self.matched_key,
            "target_key": self.target_key,
            "tagged": self.tagged,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if debug:
            data.update(
                {
                    "query": self.query,
                    "normalized_query": self.normalized_query,
                    "body": self.body.to_dict(),
                    "title": self.title.to_dict(),
                    "page": self.page,
                    "row": self.row,
                    "raw_row": self.raw_row.to_dict() if self.raw_row else None,
                }
            )
        return data


@dataclass(frozen=True)
class SearchResults:
    query: str
    normalized_query: str
    profile: SearchProfile
    hits: tuple[SearchHit, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self, *, debug: bool = False) -> dict[str, Any]:
        return {
            "query": self.query,
            "normalized_query": self.normalized_query,
            "profile": self.profile.value,
            "hits": [hit.to_dict(debug=debug) for hit in self.hits],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
