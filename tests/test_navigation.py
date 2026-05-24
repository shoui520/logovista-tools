from __future__ import annotations

from logovista_tools.navigation import NavigationResolver, address_key, lvaddr_href, parse_lvaddr_href, resolve_component_target
from logovista_tools.ssed import SsedInfoElement


def _elements() -> list[SsedInfoElement]:
    return [
        SsedInfoElement(0, 0, 0x00, 10, 20, b"", "HONMON.DIC"),
        SsedInfoElement(1, 0, 0x01, 30, 30, b"", "MENU.DIC"),
        SsedInfoElement(2, 0, 0x20, 31, 31, b"", "TOC.DIC"),
        SsedInfoElement(3, 0, 0x90, 40, 42, b"", "FKINDEX.DIC"),
        SsedInfoElement(4, 0, 0xD2, 50, 51, b"", "COLSCR.DIC"),
    ]


def test_resolve_component_target_classifies_navigation_components() -> None:
    body = resolve_component_target(10, 12, _elements())
    toc = resolve_component_target(31, 4, _elements())
    index = resolve_component_target(40, 0, _elements())
    media = resolve_component_target(50, 8, _elements())

    assert body is not None
    assert body.kind == "body"
    assert body.relative_offset == 12
    assert body.address_key == "00000010:0012"
    assert toc is not None
    assert toc.kind == "toc"
    assert index is not None
    assert index.kind == "index"
    assert media is not None
    assert media.kind == "media-image"


def test_navigation_resolver_reports_null_unresolved_and_body_status() -> None:
    resolver = NavigationResolver(_elements())

    assert resolver.resolve(0, 0).status == "null"
    assert resolver.resolve(999, 0).status == "unresolved"
    assert resolver.resolve_body(31, 0).reason == "not_body_component"
    assert resolver.resolve_body(10, 12, expanded_size=20).status == "resolved"
    assert resolver.resolve_body(20, 2047, expanded_size=20).reason == "outside_body_stream"


def test_lvaddr_helpers_accept_decimal_and_hex_forms() -> None:
    assert address_key(42, 16) == "00000042:0016"
    assert lvaddr_href(42, 16) == "lvaddr://00000042/0016"
    assert parse_lvaddr_href("lvaddr://00000042/0016") == (42, 16)
    assert parse_lvaddr_href("lvaddr://0000002a/0010") == (42, 16)
    assert parse_lvaddr_href("lvaddr://unresolved") is None
