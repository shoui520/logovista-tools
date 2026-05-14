"""Reader-facing native search models for lvcore."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .diagnostics import Diagnostic
from .index import IndexRow
from .json_types import JsonObject
from .model import Address, SearchProfile

SEARCH_HIT_SCHEMA = "lvcore.search_hit.v1"
SEARCH_HIT_MODEL_VERSION = 1
SEARCH_RESULTS_SCHEMA = "lvcore.search_results.v1"
SEARCH_RESULTS_MODEL_VERSION = 1
TITLE_RESOLUTION_SCHEMA = "lvcore.title_resolution.v1"
TITLE_RESOLUTION_MODEL_VERSION = 1


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
class TitleResolution:
    status: str
    heading: str
    heading_source: str
    title: Address
    body: Address
    raw_title: Address
    raw_body: Address
    row_title_equals_body: bool
    fallback_heading_source: str
    reason: str | None = None
    title_diagnostic_code: str | None = None
    raw_title_component: str | None = None
    raw_title_component_role: str | None = None
    raw_title_component_type: str | None = None

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": TITLE_RESOLUTION_SCHEMA,
            "model_version": TITLE_RESOLUTION_MODEL_VERSION,
            "status": self.status,
            "heading": self.heading,
            "heading_source": self.heading_source,
            "reason": self.reason,
            "diagnostic_code": self.title_diagnostic_code,
        }
        if debug:
            data.update(
                {
                    "title": self.title.to_dict(),
                    "body": self.body.to_dict(),
                    "raw_title": self.raw_title.to_dict(),
                    "raw_body": self.raw_body.to_dict(),
                    "row_title_equals_body": self.row_title_equals_body,
                    "fallback_heading_source": self.fallback_heading_source,
                    "raw_title_component": self.raw_title_component,
                    "raw_title_component_role": self.raw_title_component_role,
                    "raw_title_component_type": self.raw_title_component_type,
                }
            )  # type: ignore[arg-type]
        return data


@dataclass(frozen=True)
class SearchHitDebug:
    index_component: str
    body: Address
    title: Address
    page: int | None = None
    row: int | None = None
    raw_row: IndexRow | None = None
    body_source: object | None = None
    title_resolution: TitleResolution | None = None

    def to_dict(self) -> JsonObject:
        return {
            "index_component": self.index_component,
            "body": self.body.to_dict(),
            "title": self.title.to_dict(),
            "page": self.page,
            "row": self.row,
            "raw_row": self.raw_row.to_dict() if self.raw_row else None,
            "body_source": self.body_source.to_dict(debug=False) if hasattr(self.body_source, "to_dict") else None,
            "title_resolution": self.title_resolution.to_dict(debug=True) if self.title_resolution else None,
        }


@dataclass(frozen=True)
class SearchHit:
    id: int
    query: str
    normalized_query: str
    search_profile: SearchProfile
    package_id: str | None
    display_key: str
    matched_key: str
    target_key: str | None
    heading: str
    heading_source: str
    title_status: str
    tagged: bool
    debug_info: SearchHitDebug
    title_diagnostic_code: str | None = None
    title_reason: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def index_component(self) -> str:
        return self.debug_info.index_component

    @property
    def body(self) -> Address:
        return self.debug_info.body

    @property
    def title(self) -> Address:
        return self.debug_info.title

    @property
    def page(self) -> int | None:
        return self.debug_info.page

    @property
    def row(self) -> int | None:
        return self.debug_info.row

    @property
    def raw_row(self) -> IndexRow | None:
        return self.debug_info.raw_row

    def inspect(self) -> JsonObject:
        return self.to_dict(debug=True)

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        data: JsonObject = {
            "schema": SEARCH_HIT_SCHEMA,
            "model_version": SEARCH_HIT_MODEL_VERSION,
            "id": self.id,
            "package_id": self.package_id,
            "search_profile": self.search_profile.value,
            "heading": self.heading,
            "heading_source": self.heading_source,
            "title_status": self.title_status,
            "title_diagnostic_code": self.title_diagnostic_code,
            "display_key": self.display_key,
            "matched_key": self.matched_key,
            "target_key": self.target_key,
            "tagged": self.tagged,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if debug:
            data["query"] = self.query
            data["normalized_query"] = self.normalized_query
            data["title_reason"] = self.title_reason
            data.update(self.debug_info.to_dict())  # type: ignore[arg-type]
        return data


@dataclass(frozen=True)
class SearchResults:
    query: str
    normalized_query: str
    profile: SearchProfile
    hits: tuple[SearchHit, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self, *, debug: bool = False) -> JsonObject:
        return {
            "schema": SEARCH_RESULTS_SCHEMA,
            "model_version": SEARCH_RESULTS_MODEL_VERSION,
            "query": self.query,
            "normalized_query": self.normalized_query,
            "profile": self.profile.value,
            "hits": [hit.to_dict(debug=debug) for hit in self.hits],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
