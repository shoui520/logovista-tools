"""Entry body provider registry for reader-facing entry views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .body_source import BodyPointerInspection, BodySourceInfo, SidecarBody, SidecarInfo, SsedBodySourceKind, select_body_sidecar
from .diagnostics import Severity
from .json_types import JsonObject
from .model import Component, Entry
from .reader import EntryTarget, EntryView
from .search import SearchHit


SourceGetter = Callable[[], BodySourceInfo]


class BodyProviderHost(Protocol):
    """Methods body providers need from an SSED package implementation."""

    def honmon_component(self) -> Component | None: ...

    def body_stream_entry_for_hit(self, hit: SearchHit) -> Entry: ...

    def view_for_semantic_entry(self, target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView: ...

    def view_for_body_stream_entry(self, target: EntryTarget, entry: Entry) -> EntryView: ...

    def sidecar_entry_for_hit(self, hit: SearchHit, sidecar: SidecarInfo, inspection: BodyPointerInspection) -> Entry: ...

    def view_for_unsupported_entry(self, target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView: ...

    def placeholder_entry(
        self,
        address,
        *,
        headword: str,
        code: str,
        message: str,
        severity: Severity = Severity.WARNING,
        details: JsonObject | None = None,
        placeholder_text: str = "Entry body is not yet supported for this LogoVista body source.",
    ) -> Entry: ...

    def fetch_sidecar_body(self, sidecar: SidecarInfo, anchor_id: str) -> SidecarBody | None: ...

    def sidecar_debug_details(self, sidecar: SidecarInfo, anchor_id: str) -> JsonObject: ...

    def view_for_sidecar_body(self, hit: SearchHit, body: SidecarBody, *, sidecar: SidecarInfo, anchor_id: str) -> EntryView: ...


@dataclass(frozen=True)
class BodyResolveContext:
    """Inputs shared by all body providers for one search hit."""

    hit: SearchHit
    inspection: BodyPointerInspection
    source_getter: SourceGetter

    def source(self) -> BodySourceInfo:
        return self.source_getter()


class BodyProvider:
    """Ordered entry body resolver used by the pre-Rust provider registry."""

    def resolve(self, package: BodyProviderHost, context: BodyResolveContext) -> Entry | None:
        raise NotImplementedError

    def resolve_view(self, package: BodyProviderHost, context: BodyResolveContext) -> EntryView | None:
        entry = self.resolve(package, context)
        if entry is None:
            return None
        return package.view_for_semantic_entry(context.hit.target, entry, source="semantic_compatibility")


class BodyStreamProvider(BodyProvider):
    def resolve(self, package: BodyProviderHost, context: BodyResolveContext) -> Entry | None:
        honmon = package.honmon_component()
        if not context.inspection.anchor_id and honmon is not None and honmon.path is not None:
            return package.body_stream_entry_for_hit(context.hit)
        source = context.source()
        if source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            return package.body_stream_entry_for_hit(context.hit)
        return None

    def resolve_view(self, package: BodyProviderHost, context: BodyResolveContext) -> EntryView | None:
        entry = self.resolve(package, context)
        if entry is None:
            return None
        return package.view_for_body_stream_entry(context.hit.target, entry)


class DenseAnchorSidecarBodyProvider(BodyProvider):
    """Resolve a dense-anchor body through one supported sidecar schema."""

    SUPPORTED = {
        SsedBodySourceKind.DENSE_ANCHOR_TABLE,
        SsedBodySourceKind.DENSE_MARKER_TABLE,
        SsedBodySourceKind.DENSE_ANCHOR_WITH_SIDECAR,
        SsedBodySourceKind.RENDERER_SQLITE_SIDECAR,
        SsedBodySourceKind.DICTFULLDB_SIDECAR,
        SsedBodySourceKind.HONBUN_SIDECAR,
        SsedBodySourceKind.VLPLJBL_SIDECAR,
        SsedBodySourceKind.SIDECAR_UNKNOWN,
    }
    kind: str

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def resolve(self, package: BodyProviderHost, context: BodyResolveContext) -> Entry | None:
        source = context.source()
        if source.ssed_kind not in self.SUPPORTED:
            return None
        sidecar = select_body_sidecar(source.sidecars, kind=self.kind)
        if sidecar is not None:
            return package.sidecar_entry_for_hit(context.hit, sidecar, context.inspection)
        return None

    def resolve_view(self, package: BodyProviderHost, context: BodyResolveContext) -> EntryView | None:
        source = context.source()
        if source.ssed_kind not in self.SUPPORTED:
            return None
        sidecar = select_body_sidecar(source.sidecars, kind=self.kind)
        if sidecar is None:
            return None
        anchor_id = context.inspection.anchor_id
        if not anchor_id:
            entry = package.placeholder_entry(
                context.hit.debug_info.body,
                headword=context.hit.heading,
                code="dense_anchor_missing_id",
                message="dense HONMON record did not expose a numeric anchor id",
                severity=Severity.ERROR,
            )
            return package.view_for_unsupported_entry(context.hit.target, entry, source=source.ssed_kind.value)
        body = package.fetch_sidecar_body(sidecar, anchor_id)
        if body is None:
            entry = package.placeholder_entry(
                context.hit.debug_info.body,
                headword=context.hit.heading,
                code="sidecar_body_not_found",
                message="body sidecar did not contain a row for the dense HONMON anchor",
                severity=Severity.ERROR,
                details=package.sidecar_debug_details(sidecar, anchor_id),
            )
            return package.view_for_unsupported_entry(context.hit.target, entry, source=source.ssed_kind.value)
        return package.view_for_sidecar_body(context.hit, body, sidecar=sidecar, anchor_id=anchor_id)


class DeferredDenseSidecarProvider(BodyProvider):
    SUPPORTED = DenseAnchorSidecarBodyProvider.SUPPORTED

    def resolve(self, package: BodyProviderHost, context: BodyResolveContext) -> Entry | None:
        source = context.source()
        if source.ssed_kind not in self.SUPPORTED:
            return None
        return package.placeholder_entry(
            context.hit.debug_info.body,
            headword=context.hit.heading,
            code="unsupported_body_source",
            message="SSED dense HONMON body source is not renderable without a supported sidecar",
            severity=Severity.ERROR,
            details={"body_source": source.ssed_kind.value},
        )

    def resolve_view(self, package: BodyProviderHost, context: BodyResolveContext) -> EntryView | None:
        entry = self.resolve(package, context)
        if entry is None:
            return None
        source = context.source()
        return package.view_for_unsupported_entry(context.hit.target, entry, source=source.ssed_kind.value)


class MissingBodyComponentProvider(BodyProvider):
    def resolve(self, package: BodyProviderHost, context: BodyResolveContext) -> Entry | None:
        source = context.source()
        if source.ssed_kind != SsedBodySourceKind.MISSING_BODY_COMPONENT:
            return None
        return package.placeholder_entry(
            context.hit.debug_info.body,
            headword=context.hit.heading,
            code="missing_body_component",
            message="local SSED package declares no readable HONMON component for entry bodies",
            severity=Severity.WARNING,
            details={"body_source": source.ssed_kind.value, "missing_component": "HONMON.DIC"},
        )

    def resolve_view(self, package: BodyProviderHost, context: BodyResolveContext) -> EntryView | None:
        entry = self.resolve(package, context)
        if entry is None:
            return None
        source = context.source()
        return package.view_for_unsupported_entry(context.hit.target, entry, source=source.ssed_kind.value)


class UnsupportedBodyProvider(BodyProvider):
    def resolve(self, package: BodyProviderHost, context: BodyResolveContext) -> Entry | None:
        source = context.source()
        return package.placeholder_entry(
            context.hit.debug_info.body,
            headword=context.hit.heading,
            code="unsupported_body_source",
            message="entry body source is not supported by lvcore",
            severity=Severity.ERROR,
            details={"body_source": source.ssed_kind.value},
        )

    def resolve_view(self, package: BodyProviderHost, context: BodyResolveContext) -> EntryView | None:
        entry = self.resolve(package, context)
        if entry is None:
            return None
        source = context.source()
        return package.view_for_unsupported_entry(context.hit.target, entry, source=source.ssed_kind.value)


BODY_PROVIDERS: tuple[BodyProvider, ...] = (
    BodyStreamProvider(),
    DenseAnchorSidecarBodyProvider("t_contents"),
    DenseAnchorSidecarBodyProvider("honbun"),
    DenseAnchorSidecarBodyProvider("main_wordlist"),
    DenseAnchorSidecarBodyProvider("sqlite_body"),
    DeferredDenseSidecarProvider(),
    MissingBodyComponentProvider(),
    UnsupportedBodyProvider(),
)
