"""Body-source classification and dense HONMON pointer inspection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from .body_source import (
    BodyPointerInspection,
    BodySourceInfo,
    BodySourceSupport,
    Confidence,
    SidecarInfo,
    SsedBodySourceKind,
    body_source_kind_for_sidecar,
    select_body_sidecar,
)
from .diagnostics import Diagnostic, DiagnosticArea, DiagnosticCode, Location, Severity
from .json_types import JsonObject
from .model import Address, Component, ComponentRole, PackageInfo
from .package_utils import ENTRY_MARKER
from .scan import ScanBudget
from .ssed import SsedData
from .text import decode_text_stream


class BodySourceClassifierHost(Protocol):
    @property
    def info(self) -> PackageInfo: ...

    def data(self, component: Component) -> SsedData: ...

    def component_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Component | None: ...

    def honmon_component(self) -> Component | None: ...

    def _relative_offset(self, component: Component, address: Address) -> int: ...

    def _location_for_address(self, address: Address, *, role: ComponentRole | None = None) -> Location: ...

    def _markers_for_component(self, component: Component, *, limit: int | None = None, budget: ScanBudget | None = None) -> list[int]: ...

    def _body_pointer_offsets(self, component: Component) -> list[int]: ...

    def _body_pointer_offsets_fast(
        self,
        component: Component,
        *,
        limit: int,
        preserve_order: bool = False,
        budget: ScanBudget | None = None,
    ) -> list[int]: ...

    def _sidecar_file_candidates(self) -> list[Path]: ...

    def _body_sidecars(self, *, stop_after_body_resolver: bool = False, allow_expensive: bool = True) -> tuple[SidecarInfo, ...]: ...

    @property
    def gaiji_mapping(self) -> dict[str, str]: ...


def decode_anchor_at(host: BodySourceClassifierHost, component: Component, start: int, *, max_bytes: int = 96) -> tuple[str, int]:
    """Decode a dense-HONMON anchor record at ``start``.

    Dense sidecar packages observed in the corpus point native index rows at
    compact HONMON records that contain a short decimal id. Keep this function
    deliberately strict: non-numeric data is unresolved evidence, not an
    invitation to guess another scheme.
    """

    reader = host.data(component)
    data = reader.read(start, min(max_bytes, max(reader.expanded_size - start, 0)))
    next_marker = data.find(ENTRY_MARKER, 1)
    if next_marker > 0:
        data = data[:next_marker]
    decoded = decode_text_stream(data, host.gaiji_mapping)
    compact = "".join(ch for ch in decoded.text if not ch.isspace() and ch != "\x00")
    if compact.isdigit() and 4 <= len(compact) <= 16:
        return compact, len(data)
    return "", len(data)


def dense_anchor_marker_sample(
    host: BodySourceClassifierHost,
    component: Component,
    marker_offsets: list[int],
) -> list[int]:
    if not marker_offsets:
        return []
    best_offsets = marker_offsets[:64]
    best_ratio = -1.0
    best_count = 0
    max_stride = min(8, len(marker_offsets))
    for stride in range(1, max_stride + 1):
        for phase in range(stride):
            candidates = marker_offsets[phase::stride][:64]
            if len(candidates) < 4:
                continue
            numeric = 0
            for offset in candidates:
                anchor_id, _size = decode_anchor_at(host, component, offset)
                if anchor_id:
                    numeric += 1
            ratio = numeric / len(candidates)
            if (ratio, numeric) > (best_ratio, best_count):
                best_ratio = ratio
                best_count = numeric
                best_offsets = candidates
    return best_offsets


def dense_anchor_evidence(
    host: BodySourceClassifierHost,
    component: Component,
    *,
    use_index_pointers: bool = True,
) -> JsonObject:
    if use_index_pointers:
        pointer_offsets = host._body_pointer_offsets(component)
    else:
        pointer_offsets = host._body_pointer_offsets_fast(component, limit=4, preserve_order=True)
    sample_offsets = pointer_offsets[:64]
    if not sample_offsets:
        marker_offsets = host._markers_for_component(component, limit=256)[:256]
        sample_offsets = dense_anchor_marker_sample(host, component, marker_offsets)
    ids: list[str] = []
    lengths: list[int] = []
    for offset in sample_offsets:
        anchor_id, size = decode_anchor_at(host, component, offset)
        if anchor_id:
            ids.append(anchor_id)
        lengths.append(size)
    numeric_ratio = len(ids) / len(sample_offsets) if sample_offsets else 0.0
    common_gap = None
    unique_offsets = sorted(set((pointer_offsets or sample_offsets)[:4096]))
    gaps = [b - a for a, b in zip(unique_offsets, unique_offsets[1:]) if b > a]
    if gaps:
        counts: dict[int, int] = {}
        for gap in gaps:
            counts[gap] = counts.get(gap, 0) + 1
        common_gap = max(counts.items(), key=lambda item: (item[1], -item[0]))[0]
    dense_record_size = common_gap if common_gap in {16, 32, 40, 48, 64, 80, 96, 112, 128, 160, 320} else None
    if dense_record_size is None and lengths:
        median = int(sorted(lengths)[len(lengths) // 2])
        if median <= 160:
            dense_record_size = median
    return {
        "anchor_ids": ids,
        "numeric_ratio": numeric_ratio,
        "sample_count": len(sample_offsets),
        "common_gap": common_gap,
        "dense_record_size": dense_record_size,
    }


def classify_body_source(host: BodySourceClassifierHost, *, debug: bool = False) -> BodySourceInfo:
    honmon = host.honmon_component()
    if honmon is None or honmon.path is None:
        return BodySourceInfo(
            package_family=host.info.family,
            ssed_kind=SsedBodySourceKind.MISSING_BODY_COMPONENT,
            support=BodySourceSupport.UNSUPPORTED,
            confidence=Confidence.PROVEN,
            notes=("missing HONMON component",),
        )
    reader = host.data(honmon)
    marker_limit = 2000000 if debug else 16
    marker_count = len(host._markers_for_component(honmon, limit=marker_limit))
    marker_density = marker_count / max(reader.expanded_size, 1)
    sidecar_paths = tuple(host._sidecar_file_candidates())
    evidence = dense_anchor_evidence(host, honmon, use_index_pointers=debug)
    numeric_ratio = float(evidence["numeric_ratio"])
    is_dense = numeric_ratio >= 0.6 and int(evidence["sample_count"]) >= 4
    sidecars = host._body_sidecars(stop_after_body_resolver=not debug) if is_dense else ()
    chosen_sidecar = select_body_sidecar(sidecars)

    if is_dense:
        if chosen_sidecar is not None:
            kind = body_source_kind_for_sidecar(chosen_sidecar)
            support = BodySourceSupport.PARTIALLY_RENDERABLE
            provider = "sqlite_sidecar"
            sidecar_kind = chosen_sidecar.kind
            notes = ("HONMON body pointers resolve to short numeric anchor records; selected SQLite sidecar body provider",)
        elif sidecars:
            kind = SsedBodySourceKind.SIDECAR_UNKNOWN
            support = BodySourceSupport.DEFERRED
            provider = "sqlite_sidecar_deferred"
            sidecar_kind = ",".join(dict.fromkeys(sidecar.kind for sidecar in sidecars))
            notes = ("HONMON body pointers resolve to short numeric anchor records; sidecar files were found but no supported body table schema was identified",)
        else:
            kind = SsedBodySourceKind.DENSE_ANCHOR_TABLE
            support = BodySourceSupport.DEFERRED
            provider = "dense_anchor_deferred"
            sidecar_kind = None
            notes = ("HONMON body pointers resolve to short numeric anchor records; no supported body sidecar found",)
    else:
        kind = SsedBodySourceKind.BODY_STREAM
        support = BodySourceSupport.RENDERABLE
        provider = "honmon_body_stream"
        sidecar_kind = None
        notes = ("HONMON body pointers resolve directly into readable body-stream data",)

    if not debug:
        notes = (*notes, "fast body-source classification; run with --debug for index-pointer evidence")

    return BodySourceInfo(
        package_family=host.info.family,
        ssed_kind=kind,
        support=support,
        confidence=Confidence.INFERRED if is_dense else Confidence.PROVEN,
        honmon_component=honmon.name,
        expanded_size=reader.expanded_size,
        marker_count=marker_count,
        marker_density=round(marker_density, 8),
        dense_record_size=evidence.get("dense_record_size") if is_dense else None,
        anchor_count=len(evidence.get("anchor_ids") or ()) if is_dense else None,
        sidecar_paths=sidecar_paths,
        sidecar_kind=sidecar_kind,
        render_provider=provider,
        notes=notes,
        sidecars=sidecars,
    )


def inspect_body_pointer(host: BodySourceClassifierHost, address: Address) -> BodyPointerInspection:
    honmon = host.component_for_address(address, role=ComponentRole.HONMON)
    if honmon is None or honmon.path is None:
        return BodyPointerInspection(
            diagnostics=(
                Diagnostic(
                    severity=Severity.ERROR,
                    area=DiagnosticArea.BODY,
                    code=DiagnosticCode.BODY_POINTER_OUTSIDE_HONMON,
                    message="body pointer does not resolve to a HONMON component",
                    location=host._location_for_address(address, role=ComponentRole.HONMON),
                ),
            )
        )
    start = host._relative_offset(honmon, address)
    anchor_id, length = decode_anchor_at(host, honmon, start)
    raw_text_hash = hashlib.sha256(anchor_id.encode("utf-8")).hexdigest()[:16] if anchor_id else None
    return BodyPointerInspection(
        anchor_id=anchor_id or None,
        raw_text_hash=raw_text_hash,
        raw_text_length=len(anchor_id),
        record_offset=start,
        record_length=length,
    )
