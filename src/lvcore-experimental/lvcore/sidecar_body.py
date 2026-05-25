"""Helpers for sidecar-backed SSED entry bodies."""

from __future__ import annotations

from html.parser import HTMLParser
import sqlite3

from .body_source import SidecarBody, SidecarInfo, strip_html
from .diagnostics import Diagnostic, DiagnosticArea, Location, Severity
from .json_types import JsonObject
from .model import Address, Entry, Span

AnchorQueryValue = str | int

_SIDECAR_BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}

_SIDECAR_STYLE_TAGS = {
    "b": ("bold", 0x06, 0x07),
    "strong": ("bold", 0x06, 0x07),
    "i": ("italic", 0x0E, 0x0F),
    "em": ("em", 0x10, 0x11),
}


class _SidecarHtmlSpanParser(HTMLParser):
    """Convert supported sidecar HTML into normal text/control spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.spans: list[Span] = []
        self._offset = 0

    def _append(self, span: Span) -> None:
        self.spans.append(span)

    def _break(self) -> None:
        if not self.spans or self.spans[-1].kind == "break":
            return
        self._append(Span(kind="break", offset=self._offset, length=0))

    def _control(self, tag: str, op: int) -> None:
        self._append(Span(kind="control", offset=self._offset, length=0, op=op, attrs={"tag": tag}))

    def handle_starttag(self, tag: str, attrs) -> None:
        lower = tag.lower()
        if lower == "br":
            self._break()
            return
        if lower in _SIDECAR_BLOCK_TAGS:
            self._break()
        style = _SIDECAR_STYLE_TAGS.get(lower)
        if style is not None:
            logical_tag, start_op, _end_op = style
            self._control(logical_tag, start_op)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        style = _SIDECAR_STYLE_TAGS.get(lower)
        if style is not None:
            logical_tag, _start_op, end_op = style
            self._control(logical_tag, end_op)
        if lower in _SIDECAR_BLOCK_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self._append(Span(kind="text", text=data, offset=self._offset, length=len(data)))
        self._offset += len(data)


def sidecar_html_to_spans(value: str, *, fallback_text: str) -> tuple[Span, ...]:
    parser = _SidecarHtmlSpanParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return (Span(kind="text", text=fallback_text),)
    spans = tuple(span for span in parser.spans if not (span.kind == "break" and span is parser.spans[-1]))
    if any(span.kind == "text" and span.text for span in spans):
        return spans
    return (Span(kind="text", text=fallback_text),)


def anchor_query_values(anchor_id: str) -> tuple[AnchorQueryValue, ...]:
    values: list[AnchorQueryValue] = [anchor_id]
    stripped = anchor_id.lstrip("0") or "0"
    if stripped != anchor_id:
        values.append(stripped)
    try:
        values.append(int(stripped))
    except ValueError:
        pass
    return tuple(dict.fromkeys(values))


def sidecar_debug_details(sidecar: SidecarInfo, anchor_id: str) -> JsonObject:
    query_values = [str(value) for value in anchor_query_values(anchor_id)]
    return {
        "anchor_id": anchor_id,
        "query_values": query_values,
        "sidecar": sidecar.path.name,
        "sidecar_kind": sidecar.kind,
        "storage": sidecar.storage,
        "table": sidecar.table,
        "id_column": sidecar.id_column,
        "title_column": sidecar.title_column,
        "html_column": sidecar.html_column,
        "plain_column": sidecar.plain_column,
    }


def sidecar_body_from_row(sidecar: SidecarInfo, row: sqlite3.Row) -> SidecarBody | None:
    title = str(row[sidecar.title_column]) if sidecar.title_column and row[sidecar.title_column] is not None else ""
    html_value = str(row[sidecar.html_column]) if sidecar.html_column and row[sidecar.html_column] is not None else ""
    plain_value = str(row[sidecar.plain_column]) if sidecar.plain_column and row[sidecar.plain_column] is not None else ""
    text = plain_value.strip() or strip_html(html_value) or title.strip()
    if not (text or html_value or title):
        return None
    return SidecarBody(title=strip_html(title), text=text, html=html_value or None, source=sidecar)


def make_sidecar_body_entry(
    address: Address,
    body: SidecarBody,
    *,
    sidecar: SidecarInfo,
    anchor_id: str,
    headword_hint: str,
    location: Location,
) -> Entry:
    note = Diagnostic(
        severity=Severity.INFO,
        area=DiagnosticArea.BODY,
        code="sidecar_body_resolved",
        message="entry body resolved from SSED sidecar database",
        location=location,
        details=sidecar_debug_details(sidecar, anchor_id),
    )
    text = body.text or body.title or headword_hint
    html_fallback_text = strip_html(body.html) if body.html else ""
    spans = (
        sidecar_html_to_spans(body.html, fallback_text=text)
        if body.html and (not body.text or body.text == html_fallback_text)
        else (Span(kind="text", text=text),)
    )
    return Entry(
        address=address,
        end_address=address,
        headword=body.title or headword_hint,
        text=text,
        spans=spans,
        entry_diagnostics=(note,),
    )
