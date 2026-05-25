"""Construction helpers for reader-facing entry views."""

from __future__ import annotations

from .body_source import SidecarBody, SidecarInfo
from .diagnostics import Diagnostic, DiagnosticArea, DiagnosticCode, Location, Severity
from .model import Entry
from .reader import EntryTarget, EntryView, ReaderStatus, VisualBody, VisualBodyKind
from .search import SearchHit


def semantic_entry_view(target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView:
    document = entry.document()
    visual = VisualBody(
        kind=VisualBodyKind.SEMANTIC_DOCUMENT,
        status=ReaderStatus.AVAILABLE,
        text=entry.text,
        component=entry.address.component,
        address=entry.address,
        end_address=entry.end_address,
        source=source,
        diagnostics=tuple(document.diagnostics),
    )
    return EntryView(
        target=target,
        heading=entry.headword,
        visual_body=visual,
        semantic_document=document,
        resources=tuple(document.resources),
        diagnostics=tuple(document.diagnostics) + tuple(entry.entry_diagnostics),
        body_source=source,
    )


def body_stream_entry_view(target: EntryTarget, entry: Entry, *, location: Location) -> EntryView:
    document = entry.document()
    diagnostic = Diagnostic(
        severity=Severity.INFO,
        area=DiagnosticArea.RENDER,
        code=DiagnosticCode.VISUAL_RENDERER_DEFERRED,
        message="raw SSED body stream requires HC/profile rendering for first-class visual parity",
        location=location,
        details={"component": entry.address.component or ""},
    )
    visual = VisualBody(
        kind=VisualBodyKind.SSED_STREAM,
        status=ReaderStatus.DEFERRED,
        component=entry.address.component,
        address=entry.address,
        end_address=entry.end_address,
        renderer_profile="hc_profile_deferred",
        source="honmon_body_stream",
        diagnostics=(diagnostic,),
    )
    return EntryView(
        target=target,
        heading=entry.headword,
        visual_body=visual,
        semantic_document=document,
        resources=tuple(document.resources),
        diagnostics=tuple(document.diagnostics) + (diagnostic,),
        body_source="body_stream",
    )


def unsupported_entry_view(target: EntryTarget, entry: Entry, *, source: str | None = None) -> EntryView:
    document = entry.document()
    visual = VisualBody(
        kind=VisualBodyKind.UNSUPPORTED,
        status=ReaderStatus.UNSUPPORTED,
        text=entry.text,
        component=entry.address.component,
        address=entry.address,
        end_address=entry.end_address,
        source=source,
        diagnostics=entry.entry_diagnostics,
    )
    return EntryView(
        target=target,
        heading=entry.headword,
        visual_body=visual,
        semantic_document=document,
        resources=tuple(document.resources),
        diagnostics=tuple(document.diagnostics),
        body_source=source,
    )


def sidecar_body_entry_view(hit: SearchHit, entry: Entry, body: SidecarBody, *, sidecar: SidecarInfo, anchor_id: str) -> EntryView:
    document = entry.document()
    target = hit.target.with_sidecar_row(sidecar_name=sidecar.path.name, table=sidecar.table, row_id=anchor_id)
    if body.html:
        visual = VisualBody(
            kind=VisualBodyKind.PRESERVED_HTML,
            status=ReaderStatus.AVAILABLE,
            html=body.html,
            text=body.text,
            component=entry.address.component,
            address=entry.address,
            end_address=entry.end_address,
            source=f"sidecar:{sidecar.kind}",
            diagnostics=entry.entry_diagnostics,
        )
    else:
        visual = VisualBody(
            kind=VisualBodyKind.SEMANTIC_DOCUMENT,
            status=ReaderStatus.AVAILABLE,
            text=entry.text,
            component=entry.address.component,
            address=entry.address,
            end_address=entry.end_address,
            source=f"sidecar:{sidecar.kind}",
            diagnostics=entry.entry_diagnostics,
        )
    return EntryView(
        target=target,
        heading=entry.headword,
        visual_body=visual,
        semantic_document=document,
        resources=tuple(document.resources),
        diagnostics=tuple(document.diagnostics) + tuple(entry.entry_diagnostics),
        body_source=sidecar.kind,
    )
