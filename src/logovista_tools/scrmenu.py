"""KOJIEN6-style ``SCRMENU.DIC`` screen-menu parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .colscr import decode_bcd_decimal
from .navigation import NavigationTarget, resolve_component_target
from .ssed import SsedInfoElement, expand_sseddata_file, find_case_insensitive, parse_ssedinfo


SCRMENU_TYPE = 0x10


@dataclass(frozen=True)
class ScreenMenuPointer:
    payload: bytes
    block: int
    offset: int
    target: NavigationTarget | None = None

    @property
    def is_null(self) -> bool:
        return self.block == 0 and self.offset == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload.hex(),
            "block": self.block,
            "offset": self.offset,
            "is_null": self.is_null,
            "target": self.target.as_dict() if self.target else None,
        }


@dataclass(frozen=True)
class ScreenMenuRect:
    x: int
    y: int
    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class ScreenMenuHotspot:
    start_offset: int
    payload: bytes
    rect: ScreenMenuRect
    destination: ScreenMenuPointer
    target_screen_index: int | None = None
    target_direct_screen_index: int | None = None
    target_direct_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        target_kind = None
        if self.target_screen_index is not None:
            target_kind = "screen"
        elif self.target_direct_screen_index is not None:
            target_kind = "direct_target"
        return {
            "start_offset": self.start_offset,
            "payload": self.payload.hex(),
            "rect": self.rect.as_dict(),
            "destination": self.destination.as_dict(),
            "target_kind": target_kind,
            "target_screen_index": self.target_screen_index,
            "target_direct_screen_index": self.target_direct_screen_index,
            "target_direct_index": self.target_direct_index,
        }


@dataclass(frozen=True)
class ScreenMenuDirectTarget:
    start_offset: int
    destination: ScreenMenuPointer

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_offset": self.start_offset,
            "destination": self.destination.as_dict(),
        }


@dataclass
class ScreenMenuScreen:
    screen_index: int
    start_offset: int
    end_offset: int | None
    start_payload: bytes
    image_payload: bytes | None = None
    image: ScreenMenuPointer | None = None
    width: int | None = None
    height: int | None = None
    hotspots: list[ScreenMenuHotspot] = field(default_factory=list)
    direct_targets: list[ScreenMenuDirectTarget] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "screen_index": self.screen_index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "start_payload": self.start_payload.hex(),
            "image_payload": self.image_payload.hex() if self.image_payload is not None else None,
            "width": self.width,
            "height": self.height,
            "image": self.image.as_dict() if self.image else None,
            "hotspots": [hotspot.as_dict() for hotspot in self.hotspots],
            "direct_targets": [target.as_dict() for target in self.direct_targets],
        }


@dataclass(frozen=True)
class ScreenMenuParseResult:
    screens: tuple[ScreenMenuScreen, ...]
    stats: dict[str, int]

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        screens = self.screens if limit is None else self.screens[:limit]
        return {
            "schema": "logovista-scrmenu-v1",
            "screen_count": len(self.screens),
            "stats": self.stats,
            "screens": [screen.as_dict() for screen in screens],
        }


def _parse_bcd_pointer(payload: bytes, elements: list[SsedInfoElement] | None) -> ScreenMenuPointer | None:
    if len(payload) != 6:
        return None
    block = decode_bcd_decimal(payload[:4])
    offset = decode_bcd_decimal(payload[4:6])
    if block is None or offset is None:
        return None
    target = resolve_component_target(block, offset, elements) if elements is not None else None
    return ScreenMenuPointer(payload=payload, block=block, offset=offset, target=target)


def _parse_bcd_word(payload: bytes) -> int | None:
    if len(payload) != 2:
        return None
    return decode_bcd_decimal(payload)


def _parse_rect(payload: bytes) -> ScreenMenuRect | None:
    if len(payload) != 8:
        return None
    values = [_parse_bcd_word(payload[offset : offset + 2]) for offset in range(0, 8, 2)]
    if any(value is None for value in values):
        return None
    x, y, width, height = (int(value) for value in values if value is not None)
    return ScreenMenuRect(x=x, y=y, width=width, height=height)


def parse_screen_menu_stream(
    data: bytes,
    *,
    elements: list[SsedInfoElement] | None = None,
) -> ScreenMenuParseResult:
    screens: list[ScreenMenuScreen] = []
    current: ScreenMenuScreen | None = None
    last_closed: ScreenMenuScreen | None = None
    stats = {
        "controls": 0,
        "unknown_controls": 0,
        "screens": 0,
        "image_records": 0,
        "hotspots": 0,
        "direct_targets": 0,
        "resolved_targets": 0,
        "screen_targets": 0,
        "body_targets": 0,
        "media_image_targets": 0,
        "unresolved_targets": 0,
        "hotspot_screen_targets": 0,
        "hotspot_direct_targets": 0,
        "hotspot_unclassified_screen_menu_targets": 0,
    }

    i = 0
    while i < len(data):
        if data[i] != 0x1F or i + 1 >= len(data):
            i += 1
            continue
        op = data[i + 1]
        stats["controls"] += 1

        if op == 0x4C and i + 4 <= len(data):
            if current is not None:
                current.end_offset = i
                screens.append(current)
            current = ScreenMenuScreen(
                screen_index=len(screens),
                start_offset=i,
                end_offset=None,
                start_payload=data[i + 2 : i + 4],
            )
            stats["screens"] += 1
            i += 4
            continue

        if op == 0x4D and i + 20 <= len(data):
            payload = data[i + 2 : i + 20]
            image = _parse_bcd_pointer(payload[12:18], elements)
            width = _parse_bcd_word(payload[8:10])
            height = _parse_bcd_word(payload[10:12])
            if current is not None:
                current.image_payload = payload
                current.image = image
                current.width = width
                current.height = height
            if image is not None:
                _count_pointer_target(stats, image)
            stats["image_records"] += 1
            i += 20
            continue

        if op == 0x6D:
            i += 2
            continue

        if op == 0x4F and i + 36 <= len(data):
            payload = data[i + 2 : i + 36]
            rect = _parse_rect(payload[6:14])
            destination = _parse_bcd_pointer(payload[26:32], elements)
            if current is not None and rect is not None and destination is not None:
                hotspot = ScreenMenuHotspot(
                    start_offset=i,
                    payload=payload,
                    rect=rect,
                    destination=destination,
                )
                current.hotspots.append(hotspot)
                _count_pointer_target(stats, destination)
                stats["hotspots"] += 1
            else:
                stats["unresolved_targets"] += 1
            i += 36
            continue

        if op == 0x6C:
            if current is not None:
                current.end_offset = i + 2
                screens.append(current)
                last_closed = current
                current = None
            i += 2
            continue

        if op == 0x4B and i + 8 <= len(data):
            payload = data[i + 2 : i + 8]
            destination = _parse_bcd_pointer(payload, elements)
            if destination is not None and last_closed is not None:
                last_closed.direct_targets.append(ScreenMenuDirectTarget(start_offset=i, destination=destination))
                _count_pointer_target(stats, destination)
                stats["direct_targets"] += 1
            else:
                stats["unresolved_targets"] += 1
            i += 8
            continue

        if op == 0x6B:
            i += 2
            continue

        stats["unknown_controls"] += 1
        i += 2

    if current is not None:
        current.end_offset = len(data)
        screens.append(current)

    _annotate_screen_targets(screens, stats)
    return ScreenMenuParseResult(screens=tuple(screens), stats=stats)


def _count_pointer_target(stats: dict[str, int], pointer: ScreenMenuPointer) -> None:
    if pointer.target is None:
        stats["unresolved_targets"] += 1
        return
    stats["resolved_targets"] += 1
    if pointer.target.component == "SCRMENU.DIC":
        stats["screen_targets"] += 1
    elif pointer.target.kind == "body":
        stats["body_targets"] += 1
    elif pointer.target.kind == "media-image":
        stats["media_image_targets"] += 1


def _annotate_screen_targets(screens: list[ScreenMenuScreen], stats: dict[str, int]) -> None:
    by_offset = {screen.start_offset: screen.screen_index for screen in screens}
    direct_by_offset: dict[int, tuple[int, int]] = {}
    for screen in screens:
        for direct_index, direct in enumerate(screen.direct_targets):
            direct_by_offset[direct.start_offset] = (screen.screen_index, direct_index)
    for screen in screens:
        for hotspot in screen.hotspots:
            target = hotspot.destination.target
            if target is None or target.component != "SCRMENU.DIC":
                continue
            hotspot.target_screen_index = by_offset.get(target.relative_offset)
            if hotspot.target_screen_index is not None:
                stats["hotspot_screen_targets"] += 1
                continue
            direct_target = direct_by_offset.get(target.relative_offset)
            if direct_target is not None:
                hotspot.target_direct_screen_index, hotspot.target_direct_index = direct_target
                stats["hotspot_direct_targets"] += 1
                continue
            stats["hotspot_unclassified_screen_menu_targets"] += 1


def _screen_menu_element(elements: list[SsedInfoElement]) -> SsedInfoElement | None:
    return next(
        (
            element
            for element in elements
            if element.type == SCRMENU_TYPE or element.filename.upper() == "SCRMENU.DIC"
        ),
        None,
    )


def find_screen_menu_path(idx: Path) -> Path | None:
    _title, elements = parse_ssedinfo(idx)
    element = _screen_menu_element(elements)
    if element is None or not element.start:
        return None
    return find_case_insensitive(idx.parent, element.filename)


def load_screen_menu(idx: Path) -> ScreenMenuParseResult | None:
    _title, elements = parse_ssedinfo(idx)
    element = _screen_menu_element(elements)
    if element is None or not element.start:
        return None
    path = find_case_insensitive(idx.parent, element.filename)
    if path is None:
        return None
    return parse_screen_menu_stream(expand_sseddata_file(path), elements=elements)
