"""Conservative HTML-to-export-body lowering."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from .model import ExportBody, ExportBodyEvent


_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


class _ExportHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[ExportBodyEvent] = []
        self._last_break = False

    def _break(self) -> None:
        if not self._last_break:
            self.events.append(ExportBodyEvent.line_break())
            self._last_break = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.casefold()
        if lower in {"br", "p", "div", "section", "article", "tr", "li", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._break()
        elif lower in {"b", "strong"}:
            self.events.append(ExportBodyEvent(kind="style_start", value="bold"))
            self._last_break = False
        elif lower in {"i", "em"}:
            self.events.append(ExportBodyEvent(kind="style_start", value="italic"))
            self._last_break = False
        elif lower in {"sub", "sup"}:
            self.events.append(ExportBodyEvent(kind="style_start", value=lower))
            self._last_break = False
        elif lower == "img":
            attr_map = {name.casefold(): value or "" for name, value in attrs}
            label = attr_map.get("alt") or attr_map.get("src") or "image"
            self.events.append(ExportBodyEvent(kind="media_ref", text=label, value=attr_map.get("src", ""), attrs=attr_map))
            self._last_break = False

    def handle_endtag(self, tag: str) -> None:
        lower = tag.casefold()
        if lower in {"p", "div", "section", "article", "tr", "li", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._break()
        elif lower in {"b", "strong"}:
            self.events.append(ExportBodyEvent(kind="style_end", value="bold"))
            self._last_break = False
        elif lower in {"i", "em"}:
            self.events.append(ExportBodyEvent(kind="style_end", value="italic"))
            self._last_break = False
        elif lower in {"sub", "sup"}:
            self.events.append(ExportBodyEvent(kind="style_end", value=lower))
            self._last_break = False

    def handle_data(self, data: str) -> None:
        text = _SPACE_RE.sub(" ", html.unescape(data))
        if not text:
            return
        if text.strip():
            self.events.append(ExportBodyEvent.text_event(text))
            self._last_break = False


def html_to_export_body(fragment: str, *, source_kind: str = "preserved_html_flattened") -> ExportBody:
    parser = _ExportHtmlParser()
    parser.feed(fragment)
    parser.close()
    events = list(parser.events)
    while events and events[0].kind == "line_break":
        events.pop(0)
    while events and events[-1].kind == "line_break":
        events.pop()
    return ExportBody(events=tuple(events), source_kind=source_kind)
