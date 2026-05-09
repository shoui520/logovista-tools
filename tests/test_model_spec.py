import json
from pathlib import Path

from logovista_tools.model_readiness import build_model_readiness
from logovista_tools.model_types import (
    AddressKind,
    BodySource,
    ComponentRole,
    ControlConfidence,
    HonmonShape,
    PackageFamily,
    PlatformWrapper,
    ReadinessStatus,
    SpanKind,
    WriterStatus,
)


SPEC = Path(__file__).resolve().parents[1] / "spec" / "decoded-model-v0.md"


def read_spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def fenced_block_after(label: str, *, language: str = "text") -> str:
    text = read_spec()
    start = text.index(label)
    fence = f"```{language}"
    block_start = text.index(fence, start) + len(fence)
    block_end = text.index("```", block_start)
    return text[block_start:block_end].strip()


def enum_values(enum_type) -> list[str]:
    return [item.value for item in enum_type]


def documented_values(label: str) -> list[str]:
    values = []
    for line in fenced_block_after(label).splitlines():
        line = line.strip()
        if not line:
            continue
        values.append(line.split()[0])
    return values


def test_decoded_model_spec_enum_blocks_match_model_types() -> None:
    assert documented_values("`package_family` values:") == enum_values(PackageFamily)
    assert documented_values("`platform` values:") == enum_values(PlatformWrapper)
    assert documented_values("`honmon_shape` values observed so far:") == enum_values(HonmonShape)
    assert documented_values("`body_source` values:") == enum_values(BodySource)
    assert documented_values("Address `kind` values:") == enum_values(AddressKind)
    assert documented_values("`role` values:") == enum_values(ComponentRole)
    assert documented_values("Span `kind` values:") == enum_values(SpanKind)
    assert documented_values("Control `confidence` values:") == enum_values(ControlConfidence)
    assert documented_values("Capability status values:") == enum_values(ReadinessStatus)
    assert documented_values("Writer status values:") == enum_values(WriterStatus)


def test_writer_readiness_example_matches_emitted_keys() -> None:
    block = fenced_block_after("The top-level `writer_readiness`", language="json")
    example = json.loads(block)["writer_readiness"]
    emitted = build_model_readiness({"classification": {}})["writer_readiness"]
    assert list(example) == list(emitted)
