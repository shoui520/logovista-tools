"""SSED text stream decoding."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Span


CONTROL_ARG_LENGTHS = {
    0x09: 2,
    0x1A: 2,
    0x1C: 2,
    0x41: 2,
    0x42: 0,
    0x43: 0,
    0x44: 10,
    0x49: 10,
    0x4A: 16,
    0x4D: 18,
    0x62: 6,
    0x63: 6,
    0x64: 6,
    0x69: 0,
    0xE0: 2,
    0xE2: 2,
}

START_TAGS = {
    0x04: "halfwidth",
    0x06: "sub",
    0x0B: "literal",
    0x0E: "sup",
    0x10: "italic",
    0x12: "em",
    0x3B: "url",
    0x41: "head",
    0x42: "link",
    0x43: "link",
    0x44: "link",
    0x49: "link",
    0x4A: "link",
    0x4D: "media",
    0xE0: "bold",
    0xE2: "private",
}

END_TAGS = {
    0x05: "halfwidth",
    0x07: "sub",
    0x0C: "literal",
    0x0F: "sup",
    0x11: "italic",
    0x13: "em",
    0x5B: "url",
    0x61: "head",
    0x62: "link",
    0x63: "link",
    0x64: "link",
    0x69: "link",
    0x6A: "link",
    0x6D: "media",
    0xE1: "bold",
    0xE3: "private",
}


@dataclass(frozen=True)
class DecodeResult:
    spans: tuple[Span, ...]
    text: str
    unknown_controls: int
    unknown_bytes: int


def jis_to_sjis(pair: bytes) -> bytes:
    row = pair[0] - 0x21
    cell = pair[1] - 0x21
    lead = (row >> 1) + 0x81
    if lead > 0x9F:
        lead += 0x40
    if row & 1:
        trail = cell + 0x9F
    else:
        trail = cell + 0x40
        if trail >= 0x7F:
            trail += 1
    return bytes((lead, trail))


def decode_jis_pair(pair: bytes) -> str:
    try:
        return (b"\x1b$B" + pair + b"\x1b(B").decode("iso2022_jp")
    except UnicodeDecodeError:
        pass
    sjis = jis_to_sjis(pair)
    for encoding in ("cp932", "shift_jis_2004"):
        try:
            return sjis.decode(encoding)
        except UnicodeDecodeError:
            pass
    return ""


def narrow_fullwidth(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch == "\u3000":
            out.append(" ")
        elif code == 0x2212:
            out.append("-")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def gaiji_placeholder(code: str) -> str:
    prefix = "h" if int(code[:2], 16) < 0xB0 else "z"
    return f"<{prefix}{code.upper()}>"


def decode_text_stream(data: bytes, gaiji: dict[str, str] | None = None) -> DecodeResult:
    gaiji = gaiji or {}
    spans: list[Span] = []
    text_parts: list[str] = []
    i = 0
    halfwidth = 0
    private = 0
    unknown_controls = 0
    unknown_bytes = 0

    while i < len(data):
        b = data[i]
        if b == 0:
            i += 1
            continue
        if b == 0x0A:
            spans.append(Span(kind="line_break", raw=data[i : i + 1], offset=i, length=1, hidden=bool(private)))
            if not private:
                text_parts.append("\n")
            i += 1
            continue
        if i + 1 < len(data) and data[i : i + 2] == b"\x11\x03":
            spans.append(Span(kind="legacy_control", raw=data[i : i + 2], offset=i, length=2, hidden=bool(private)))
            i += 2
            continue
        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            if op == 0x0A:
                spans.append(Span(kind="line_break", raw=data[i : i + 2], offset=i, length=2, op=op, hidden=bool(private)))
                if not private:
                    text_parts.append("\n")
                i += 2
                continue
            arg_len = CONTROL_ARG_LENGTHS.get(op, 0)
            payload = data[i + 2 : i + 2 + arg_len]
            if op == 0x04:
                halfwidth += 1
            elif op == 0x05 and halfwidth:
                halfwidth -= 1
            elif op == 0xE2:
                private += 1
            elif op == 0xE3 and private:
                private -= 1

            tag = START_TAGS.get(op) or END_TAGS.get(op)
            known = tag is not None or op in {0x00, 0x02, 0x03, 0x09, 0x1A, 0x1C}
            if not known:
                unknown_controls += 1
            kind = "control"
            if op == 0x09:
                kind = "section"
            elif op == 0x4D:
                kind = "media"
            spans.append(
                Span(
                    kind=kind,
                    raw=data[i : i + 2 + arg_len],
                    offset=i,
                    length=2 + arg_len,
                    op=op,
                    payload=payload,
                    hidden=bool(private) or op in {0xE2, 0xE3},
                    attrs={"tag": tag} if tag else {},
                )
            )
            i += 2 + arg_len
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            raw = data[i : i + 2]
            value = decode_jis_pair(raw)
            if halfwidth:
                value = narrow_fullwidth(value)
            spans.append(Span(kind="text", text=value, raw=raw, offset=i, length=2, hidden=bool(private)))
            if not private:
                text_parts.append(value)
            i += 2
            continue

        if i + 1 < len(data) and 0xA1 <= b <= 0xFE:
            raw = data[i : i + 2]
            code = raw.hex().lower()
            value = gaiji.get(code) or gaiji_placeholder(code)
            spans.append(Span(kind="gaiji", text=value, raw=raw, offset=i, length=2, code=code, hidden=bool(private)))
            if not private:
                text_parts.append(value)
            i += 2
            continue

        unknown_bytes += 1
        spans.append(Span(kind="unknown_byte", raw=data[i : i + 1], offset=i, length=1, hidden=bool(private)))
        i += 1

    return DecodeResult(spans=tuple(spans), text="".join(text_parts), unknown_controls=unknown_controls, unknown_bytes=unknown_bytes)
