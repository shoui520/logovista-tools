from pathlib import Path

from logovista_tools.gaiji import (
    Ga16Resource,
    UniRecord,
    UniResource,
    ga16_preferred_code_for_index,
    iter_ga16_code_sources,
)
from logovista_tools.gaiji_readiness import MappingEvidence, code_row, summarize_rows


def test_gaiji_readiness_buckets_and_flags():
    rows = [
        code_row(
            "a126",
            raw_count=3,
            component_counts={"HONMON.DIC": 3},
            mapping=MappingEvidence(code="a126", display="é", fallback="e", source="uni"),
            bitmap=None,
            image=False,
        ),
        code_row(
            "a127",
            raw_count=2,
            component_counts={"HONMON.DIC": 2},
            mapping=MappingEvidence(code="a127", display="ɑ́", source="uni"),
            bitmap=None,
            image=False,
        ),
        code_row(
            "b13d",
            raw_count=4,
            component_counts={"HONMON.DIC": 4},
            mapping=None,
            bitmap=None,
            image=True,
        ),
        code_row(
            "a430",
            raw_count=6,
            component_counts={"HONMON.DIC": 6},
            mapping=None,
            bitmap={"resource": "GAI16H", "glyph_index": 297, "blank": True},
            image=False,
        ),
        code_row(
            "b200",
            raw_count=5,
            component_counts={"FHTITLE.DIC": 5},
            mapping=None,
            bitmap=None,
            image=False,
        ),
        code_row(
            "a1aa",
            raw_count=1,
            component_counts={"HONMON.DIC": 1},
            mapping=None,
            bitmap=None,
            image=False,
        ),
        code_row(
            "a1bb",
            raw_count=0,
            component_counts={},
            mapping=MappingEvidence(code="a1bb", display="ø", source="uni"),
            bitmap=None,
            image=False,
        ),
    ]

    buckets = {row["code"]: row["bucket"] for row in rows}
    assert buckets["a126"] == "unicode_mapped"
    assert buckets["a127"] == "unicode_mapped"
    assert buckets["b13d"] == "image_backed"
    assert buckets["a430"] == "formatting_helper"
    assert buckets["b200"] == "formatting_helper"
    assert buckets["a1aa"] == "display_unresolved"
    assert buckets["a1bb"] == "unused_mapping"
    assert "search_fallback_missing" in rows[1]["flags"]
    assert "formatting_helper_candidate" in rows[3]["flags"]

    summary = summarize_rows(rows)
    assert summary["readiness_status"] == "partial"
    assert summary["display_unresolved_occurrences"] == 1
    assert summary["formatting_helper_candidate_occurrences"] == 11


def test_ga16_can_use_uni_record_order_for_sparse_codes():
    resource = Ga16Resource(
        path=Path("GAI16H"),
        width=8,
        height=16,
        start_code=0xA121,
        count=3,
        glyph_bytes=16,
    )
    uni = UniResource(
        path=Path("DICT.uni"),
        format="simple12",
        half_count=3,
        full_count=0,
        records=(
            UniRecord("half", 0, "a121", 0, (), "", (), "", (), "", ()),
            UniRecord("half", 1, "a430", 0, (), "", (), "", (), "", ()),
            UniRecord("half", 2, "a431", 0, (), "", (), "", (), "", ()),
        ),
        expected_size=0,
        trailing_bytes=0,
    )

    assert ga16_preferred_code_for_index(resource, 1, uni) == ("a430", "uni_record_order")
    sources = set(iter_ga16_code_sources(resource, uni))
    assert ("a122", 1, "sequential") in sources
    assert ("a430", 1, "uni_record_order") in sources
