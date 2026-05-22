"""Confirmed HC renderer visual-parity locks.

These locks record package-level reference renders that have passed visual
review.  They are not a claim that every product-specific HC DLL hook is fully
emulated; they mean the generated renderer output for the reviewed package is
now treated as a golden target and should not be changed accidentally.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderParityLock:
    """One package whose rendered output has been visually confirmed."""

    dict_id: str
    package_id: str
    source: str
    notes: str = ""


CONFIRMED_RENDER_PARITY_LOCKS: tuple[RenderParityLock, ...] = (
    RenderParityLock("ARCHSIC4", "_DCT_ARCHSIC4", "v5 visual review"),
    RenderParityLock("BMANNAR", "_DCT_BMANNER", "v2 visual review"),
    RenderParityLock("DAIJIRN4", "_DCT_DAIJIRN4", "v5 visual review"),
    RenderParityLock("Dconci87", "_DCT_Dconci87", "v5 visual review"),
    RenderParityLock("EJJE200", "_DCT_EJJE200", "v5 visual review"),
    RenderParityLock("GENKANA5", "_DCT_GENKANA5", "v5 visual review"),
    RenderParityLock("GKKANAN3", "_DCT_GKKANAN3", "v5 visual review"),
    RenderParityLock("GKKANYOK", "_DCT_GKKANYOK", "v2 visual review"),
    RenderParityLock("GKKEIGO", "_DCT_GKKEIGO", "v5 visual review"),
    RenderParityLock("GKKOJIKT", "_DCT_GKKOJIKT", "v5 visual review"),
    RenderParityLock("GKTISIKI", "_DCT_GKTISIKI", "v5 visual review"),
    RenderParityLock("HABGESPA", "_DCT_HABGESPA", "v5 visual review"),
    RenderParityLock("HKBYOIN4", "_DCT_HKBYOIN4", "v5 visual review", "User note wrote GKBYOIN4; repository package is HKBYOIN4."),
    RenderParityLock("HOUGAKU5", "_DCT_HOUGAKU5", "v5 visual review"),
    RenderParityLock("IBIO4", "_DCT_IBIO4", "v5 visual review"),
    RenderParityLock("IBIO5", "_DCT_IBIO5", "v5 visual review"),
    RenderParityLock("IPHYCHE5", "_DCT_IPHYCHE5", "v5 visual review"),
    RenderParityLock("JSSAURU2", "_DCT_JSSAURU2", "v5 visual review"),
    RenderParityLock("KCOMPEJ2", "_DCT_KCOMPEJ2", "v5 visual review"),
    RenderParityLock("KENROWA", "_DCT_KENROWA", "v5 visual review"),
    RenderParityLock("KOJIEN7", "_DCT_KOJIEN7", "v5 visual review"),
    RenderParityLock("KQBIZEJ", "_DCT_KQBIZEJ", "v5 visual review"),
    RenderParityLock("KQDENTAL", "_DCT_KQDENTAL", "v5 visual review"),
    RenderParityLock("KQEJMED2", "_DCT_KQEJMED2", "v2 visual review"),
    RenderParityLock("KQNEWJE5", "_DCT_KQNEWJE5", "v5 visual review"),
    RenderParityLock("KQSYNONM", "_DCT_KQSYNONM", "v2 visual review"),
    RenderParityLock("MEIKYOU", "_DCT_MEIKYOU", "v5 visual review"),
    RenderParityLock("MEIKYOU2", "_DCT_MEIKYOU2", "v5 visual review"),
    RenderParityLock("NANMED19", "_DCT_NANMED19", "v5 visual review"),
    RenderParityLock("NANMED20", "_DCT_NANMED20", "v5 visual review"),
    RenderParityLock("NGFINANC", "_DCT_NGFINANC", "v5 visual review"),
    RenderParityLock("PROYAL43", "_DCT_PROYAL43", "v5 visual review"),
    RenderParityLock("PROYAL53", "_DCT_PROYAL53", "v5 visual review"),
    RenderParityLock("SAIYOREI", "_DCT_SAIYOREI", "v2 visual review"),
    RenderParityLock("SPEECH", "_DCT_SPEECH", "v5 visual review"),
    RenderParityLock("TEGAMI", "_DCT_TEGAMI", "v2 visual review"),
    RenderParityLock("YHOUGO4", "_DCT_YHOUGO4", "v5 visual review"),
    RenderParityLock("YHOUGO5", "_DCT_YHOUGO5", "v5 visual review"),
    RenderParityLock("YUECONO5", "_DCT_YUECONO5", "v5 visual review"),
)

CONFIRMED_RENDER_PARITY_DICT_IDS = frozenset(lock.dict_id for lock in CONFIRMED_RENDER_PARITY_LOCKS)

_CONFIRMED_BY_DICT_ID = {lock.dict_id.casefold(): lock for lock in CONFIRMED_RENDER_PARITY_LOCKS}


def confirmed_render_parity_lock(dict_id: str | None) -> RenderParityLock | None:
    """Return the visual-parity lock for ``dict_id`` if one exists."""

    if not dict_id:
        return None
    return _CONFIRMED_BY_DICT_ID.get(dict_id.casefold())
