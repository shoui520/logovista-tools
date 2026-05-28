"""Runtime navigation/address helpers for SSED packages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .ssed import BLOCK_SIZE, SsedInfoElement, is_honmon_component


INDEX_COMPONENT_TYPES = frozenset({0x30, 0x60, 0x70, 0x71, 0x72, 0x80, 0x81, 0x90, 0x91, 0x92, 0xA1})
TITLE_COMPONENT_TYPES = frozenset({0x03, 0x04, 0x05, 0x06, 0x07, 0x0A})

_LVADDR_RE = re.compile(r"^lvaddr://(?P<block>[0-9A-Fa-f]+)/(?P<offset>[0-9A-Fa-f]+)$")


def component_kind(component_type: int, filename: str = "") -> str:
    """Return the app-facing role for an SSED catalog component."""

    upper = filename.upper()
    if component_type == 0x00 or upper in {"HONMON.DIC", "HONMON.DIN"}:
        return "body"
    if component_type == 0x01 or upper == "MENU.DIC":
        return "menu"
    if component_type == 0x02 or upper == "RIGHT.DIC":
        return "right"
    if component_type == 0x20 or upper == "TOC.DIC":
        return "toc"
    if component_type == 0x28 or upper == "IDXJUMP.DIC":
        return "idxjump"
    if component_type in TITLE_COMPONENT_TYPES or upper.endswith("TITLE.DIC"):
        return "title"
    if component_type in INDEX_COMPONENT_TYPES or upper.endswith("INDEX.DIC"):
        return "index"
    if component_type == 0xD2:
        return "media-image"
    if upper == "MONOSCR.DIC":
        return "monochrome-image"
    if component_type == 0xD8:
        return "media-audio"
    if component_type in {0xF1, 0xF2}:
        return "gaiji-resource"
    return "component"


@dataclass(frozen=True)
class NavigationTarget:
    """Resolved package-local block/offset target."""

    component: str
    component_type: str
    kind: str
    start_block: int
    end_block: int
    block: int
    offset: int
    relative_offset: int
    offset_in_block: int

    @property
    def address_key(self) -> str:
        return address_key(self.block, self.offset)

    @property
    def absolute_book_offset(self) -> int:
        return (self.block - 1) * BLOCK_SIZE + self.offset

    @property
    def is_body(self) -> bool:
        return self.kind == "body"

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "component_type": self.component_type,
            "kind": self.kind,
            "start_block": self.start_block,
            "end_block": self.end_block,
            "block": self.block,
            "offset": self.offset,
            "relative_offset": self.relative_offset,
            "offset_in_block": self.offset_in_block,
            "absolute_book_offset": self.absolute_book_offset,
            "address_key": self.address_key,
        }


@dataclass(frozen=True)
class NavigationResolution:
    """Resolution status for an absolute SSED address."""

    block: int
    offset: int
    status: str
    target: NavigationTarget | None = None
    reason: str | None = None

    @property
    def is_null(self) -> bool:
        return self.block == 0 and self.offset == 0

    @property
    def address_key(self) -> str:
        return address_key(self.block, self.offset)

    def as_dict(self) -> dict[str, Any]:
        return {
            "block": self.block,
            "offset": self.offset,
            "status": self.status,
            "reason": self.reason,
            "is_null": self.is_null,
            "target": self.target.as_dict() if self.target else None,
        }


class NavigationResolver:
    """Resolve SSED block/offset pointers against a parsed SSEDINFO catalog."""

    def __init__(self, elements: Iterable[SsedInfoElement]) -> None:
        self.elements = tuple(elements)

    def resolve(self, block: int, offset: int) -> NavigationResolution:
        if block == 0 and offset == 0:
            return NavigationResolution(block=block, offset=offset, status="null", reason="null_pointer")
        target = resolve_component_target(block, offset, self.elements)
        if target is None:
            return NavigationResolution(block=block, offset=offset, status="unresolved", reason="outside_catalog")
        return NavigationResolution(block=block, offset=offset, status="resolved", target=target)

    def resolve_body(self, block: int, offset: int, *, expanded_size: int | None = None) -> NavigationResolution:
        resolution = self.resolve(block, offset)
        if resolution.target is None or not resolution.target.is_body:
            return NavigationResolution(
                block=block,
                offset=offset,
                status="unresolved",
                target=resolution.target,
                reason="not_body_component" if resolution.target is not None else resolution.reason,
            )
        if expanded_size is not None and not (0 <= resolution.target.relative_offset < expanded_size):
            return NavigationResolution(
                block=block,
                offset=offset,
                status="unresolved",
                target=resolution.target,
                reason="outside_body_stream",
            )
        return resolution


def address_key(block: int, offset: int) -> str:
    return f"{block:08d}:{offset:04d}"


def lvaddr_href(block: int, offset: int) -> str:
    return f"lvaddr://{block:08d}/{offset:04d}"


def parse_lvaddr_href(href: str) -> tuple[int, int] | None:
    match = _LVADDR_RE.match(href.strip())
    if not match:
        return None
    block = match.group("block")
    offset = match.group("offset")
    base = 16 if _looks_hex(block) or _looks_hex(offset) else 10
    return int(block, base), int(offset, base)


def resolve_component_target(
    block: int,
    offset: int,
    elements: Iterable[SsedInfoElement],
) -> NavigationTarget | None:
    if block == 0 and offset == 0:
        return None
    for element in elements:
        if not element.start:
            continue
        if element.start <= block <= element.end:
            relative_offset = (block - element.start) * BLOCK_SIZE + offset
            return NavigationTarget(
                component=element.filename,
                component_type=f"{element.type:02x}",
                kind=component_kind(element.type, element.filename),
                start_block=element.start,
                end_block=element.end,
                block=block,
                offset=offset,
                relative_offset=relative_offset,
                offset_in_block=offset,
            )
    return None


def honmon_target_for(
    block: int,
    offset: int,
    elements: Iterable[SsedInfoElement],
    *,
    expanded_size: int | None = None,
) -> NavigationTarget | None:
    catalog = tuple(elements)
    target = resolve_component_target(block, offset, catalog)
    if target is None:
        return None
    element = next((item for item in catalog if item.filename == target.component), None)
    if element is None or not is_honmon_component(element):
        return None
    if expanded_size is not None and not (0 <= target.relative_offset < expanded_size):
        return None
    return target


def _looks_hex(value: str) -> bool:
    return any(ch in value for ch in "abcdefABCDEF")
