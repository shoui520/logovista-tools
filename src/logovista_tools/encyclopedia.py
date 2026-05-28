"""LVEDBRSR encyclopedia index sidecar parsing.

Some late SSED dictionaries ship an ``encyclop.idx`` sidecar instead of a
plain ``MENU.DIC``.  It is a CP932, tab-indented navigation index whose rows
point back into HONMON by block/offset.  The file is reader-side navigation
metadata, not an SSEDATA component.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ssed import resolve_case_insensitive_path


ENCYCLOPEDIA_HEADER = "#LVEDBRSR encyclopedia#"


@dataclass(frozen=True)
class EncyclopediaSection:
    index: int
    title: str
    raw_line: str

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "title": self.title}


@dataclass(frozen=True)
class EncyclopediaRow:
    index: int
    section: str
    depth: int
    label: str
    block: int
    offset: int
    raw_line: str

    @property
    def has_target(self) -> bool:
        return self.block != 0 or self.offset != 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "section": self.section,
            "depth": self.depth,
            "label": self.label,
            "block": self.block,
            "offset": self.offset,
            "has_target": self.has_target,
        }


@dataclass(frozen=True)
class EncyclopediaIndex:
    path: Path
    header: str
    sections: tuple[EncyclopediaSection, ...]
    rows: tuple[EncyclopediaRow, ...]
    encoding: str = "cp932"

    def as_dict(self, *, row_limit: int | None = None) -> dict[str, Any]:
        rows = self.rows if row_limit is None else self.rows[:row_limit]
        return {
            "path": str(self.path),
            "name": self.path.name,
            "header": self.header,
            "encoding": self.encoding,
            "sections": [section.as_dict() for section in self.sections],
            "rows": len(self.rows),
            "target_rows": sum(1 for row in self.rows if row.has_target),
            "items": [row.as_dict() for row in rows],
        }


def find_encyclopedia_index(package: Path) -> Path | None:
    path = resolve_case_insensitive_path(package, "encyclop.idx")
    if path is None or not path.is_file():
        return None
    return path


def _hex_field(value: str) -> int:
    value = value.strip()
    if not value:
        return 0
    return int(value, 16)


def _label_and_depth(fields: list[str]) -> tuple[str, int] | None:
    for depth, field in enumerate(fields[2:]):
        label = field.strip()
        if label:
            return label, depth
    return None


def parse_encyclopedia_index(path: Path) -> EncyclopediaIndex:
    text = path.read_text(encoding="cp932", errors="replace")
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty encyclopedia index")
    header = lines[0].strip()
    if not header.startswith(ENCYCLOPEDIA_HEADER):
        raise ValueError(f"not an LVEDBRSR encyclopedia index: {header!r}")

    sections: list[EncyclopediaSection] = []
    rows: list[EncyclopediaRow] = []
    current_section = ""
    for index, raw_line in enumerate(lines[1:], start=2):
        if not raw_line.strip():
            continue
        if raw_line.startswith("#"):
            title = raw_line[1:].strip()
            current_section = title
            sections.append(EncyclopediaSection(index=index, title=title, raw_line=raw_line))
            continue
        fields = raw_line.split("\t")
        if len(fields) < 3:
            continue
        label_depth = _label_and_depth(fields)
        if label_depth is None:
            continue
        label, depth = label_depth
        try:
            block = _hex_field(fields[0])
            offset = _hex_field(fields[1])
        except ValueError:
            continue
        rows.append(
            EncyclopediaRow(
                index=index,
                section=current_section,
                depth=depth,
                label=label,
                block=block,
                offset=offset,
                raw_line=raw_line,
            )
        )
    return EncyclopediaIndex(path=path, header=header, sections=tuple(sections), rows=tuple(rows))

