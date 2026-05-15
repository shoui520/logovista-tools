"""LogoVista text-stream control grammar.

The wire grammar is mostly EPWING/JIS X 4081 derived, but Windows HTML
renderers add or reinterpret a small set of controls.  Keep the byte lengths
here rather than scattering renderer-derived skip rules through individual
decoders.
"""

from __future__ import annotations


CONTROL_ARG_LENGTHS: dict[int, int] = {
    0x09: 2,
    0x1A: 2,
    0x1C: 2,
    0x36: 12,
    0x37: 10,
    0x3C: 18,
    0x41: 2,
    0x42: 0,
    0x43: 0,
    0x44: 10,
    0x48: 10,
    0x49: 10,
    0x4A: 16,
    0x4B: 6,
    0x4C: 2,
    0x4D: 18,
    0x4E: 38,
    0x4F: 34,
    0x62: 6,
    0x63: 6,
    0x64: 6,
    0x69: 0,
    0xE0: 2,
    0xE2: 2,
    0xE4: 2,
    0xE6: 2,
}


KNOWN_NONPRINTING_CONTROLS: frozenset[int] = frozenset(
    {
        0x00,
        0x02,
        0x03,
        0x1A,
        0x1C,
        0x36,
        0x37,
        0x48,
        0x4B,
        0x4C,
        0x4E,
        0x4F,
        0xE4,
        0xE6,
    }
)


def _be16(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 2 > len(data):
        return None
    return (data[offset] << 8) | data[offset + 1]


def control_arg_length(data: bytes, offset: int) -> int:
    """Return payload length for a ``0x1f`` control at ``offset``.

    Most controls have fixed payload lengths.  A few renderer-observed controls
    encode a mode in the first payload word and the Windows HC renderers skip
    different record sizes for those modes.  If the mode cannot be read, fall
    back to the conservative fixed length.
    """

    if offset < 0 or offset + 1 >= len(data) or data[offset] != 0x1F:
        return 0
    op = data[offset + 1]
    if op == 0x4A:
        # Sound/jump start.  HC renderers skip 14 payload bytes for mode 0 and
        # 16 for modes 1/2.  PCMDATA ranges observed in SSED use mode 1.
        word = _be16(data, offset + 2)
        if word is not None:
            mode = word & 0x000F
            if mode == 0:
                return 14
            if mode in {1, 2}:
                return 16
            return 2
    if op == 0x4E:
        word = _be16(data, offset + 2)
        if word is not None:
            mode = word & 0x0F00
            if mode == 0:
                return 38
            if mode in {0x0100, 0x0200}:
                return 40
            return 2
    if op == 0x4F:
        # Some renderer records start with an inline 1f6f marker and carry a
        # longer payload; otherwise the common record is 34 payload bytes.
        if data[offset + 2 : offset + 4] == b"\x1f\x6f":
            return 48
        return 34
    return CONTROL_ARG_LENGTHS.get(op, 0)

