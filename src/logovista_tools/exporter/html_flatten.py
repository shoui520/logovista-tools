"""Conservative HTML-to-export-body lowering."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from collections.abc import Callable

from .model import ExportBody, ExportBodyEvent


_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


class _ExportHtmlParser(HTMLParser):
    def __init__(self, link_resolver: Callable[[str], str | None] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[ExportBodyEvent] = []
        self._link_stack: list[tuple[str, str | None, list[ExportBodyEvent]]] = []
        self._last_break = False
        self._link_resolver = link_resolver

    def _append(self, event: ExportBodyEvent) -> None:
        if self._link_stack:
            self._link_stack[-1][2].append(event)
        else:
            self.events.append(event)

    @staticmethod
    def _label_from_events(events: list[ExportBodyEvent], fallback: str) -> str:
        parts: list[str] = []
        for event in events:
            if event.kind == "text":
                parts.append(event.text)
            elif event.kind == "line_break":
                parts.append(" ")
            elif event.kind == "media_ref":
                parts.append(event.text or event.value)
        label = _SPACE_RE.sub(" ", "".join(parts)).strip()
        return label or fallback

    def _break(self) -> None:
        if not self._last_break:
            self._append(ExportBodyEvent.line_break())
            self._last_break = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.casefold()
        if lower in {"br", "p", "div", "section", "article", "tr", "li", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._break()
        elif lower in {"b", "strong"}:
            self._append(ExportBodyEvent(kind="style_start", value="bold"))
            self._last_break = False
        elif lower in {"i", "em"}:
            self._append(ExportBodyEvent(kind="style_start", value="italic"))
            self._last_break = False
        elif lower in {"sub", "sup"}:
            self._append(ExportBodyEvent(kind="style_start", value=lower))
            self._last_break = False
        elif lower == "a":
            attr_map = {name.casefold(): value or "" for name, value in attrs}
            href = attr_map.get("href", "")
            target = self._link_resolver(href) if self._link_resolver is not None and href else None
            self._link_stack.append((href, target, []))
            self._last_break = False
        elif lower == "img":
            attr_map = {name.casefold(): value or "" for name, value in attrs}
            label = attr_map.get("alt") or attr_map.get("src") or "image"
            self._append(ExportBodyEvent(kind="media_ref", text=label, value=attr_map.get("src", ""), attrs=attr_map))
            self._last_break = False

    def handle_endtag(self, tag: str) -> None:
        lower = tag.casefold()
        if lower in {"p", "div", "section", "article", "tr", "li", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._break()
        elif lower in {"b", "strong"}:
            self._append(ExportBodyEvent(kind="style_end", value="bold"))
            self._last_break = False
        elif lower in {"i", "em"}:
            self._append(ExportBodyEvent(kind="style_end", value="italic"))
            self._last_break = False
        elif lower in {"sub", "sup"}:
            self._append(ExportBodyEvent(kind="style_end", value=lower))
            self._last_break = False
        elif lower == "a" and self._link_stack:
            href, target, link_events = self._link_stack.pop()
            label = self._label_from_events(link_events, href or "link")
            event = ExportBodyEvent(kind="link", text=label, value=target or "", attrs={"href": href})
            self._append(event)
            self._last_break = False

    def handle_data(self, data: str) -> None:
        text = _SPACE_RE.sub(" ", html.unescape(data))
        if not text:
            return
        if text.strip():
            self._append(ExportBodyEvent.text_event(text))
            self._last_break = False


def html_to_export_body(
    fragment: str,
    *,
    source_kind: str = "preserved_html_flattened",
    link_resolver: Callable[[str], str | None] | None = None,
) -> ExportBody:
    parser = _ExportHtmlParser(link_resolver=link_resolver)
    parser.feed(fragment)
    parser.close()
    while parser._link_stack:
        href, target, link_events = parser._link_stack.pop()
        label = parser._label_from_events(link_events, href or "link")
        parser.events.append(ExportBodyEvent(kind="link", text=label, value=target or "", attrs={"href": href}))
    events = list(parser.events)
    while events and events[0].kind == "line_break":
        events.pop(0)
    while events and events[-1].kind == "line_break":
        events.pop()
    return ExportBody(events=tuple(events), source_kind=source_kind)
