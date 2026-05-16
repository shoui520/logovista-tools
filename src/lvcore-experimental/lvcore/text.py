"""SSED text stream decoding."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Span, SpanDebug
from .opcodes import (
    CONTROL_ARG_LENGTHS,
    END_TAGS,
    FriendlyVisibility,
    SEMANTIC_CONTROL_TAGS,
    START_TAGS,
    behavior_for,
    is_known_opcode,
)


@dataclass(frozen=True)
class DecodeResult:
    spans: tuple[Span, ...]
    text: str
    unknown_controls: int
    unknown_bytes: int
    invalid_jis_pairs: int = 0
    spans_debug: tuple[SpanDebug, ...] = ()


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
    invalid_jis_pairs = 0

    while i < len(data):
        b = data[i]
        if b == 0:
            i += 1
            continue
        if b == 0x0A:
            spans.append(Span(kind="break", raw=data[i : i + 1], offset=i, length=1, hidden=bool(private)))
            if not private:
                text_parts.append("\n")
            i += 1
            continue
        if i + 1 < len(data) and data[i : i + 2] == b"\x11\x03":
            spans.append(
                Span(
                    kind="control",
                    raw=data[i : i + 2],
                    offset=i,
                    length=2,
                    hidden=bool(private),
                    attrs={"tag": "title_separator"},
                )
            )
            i += 2
            continue
        if b == 0x1F and i + 1 < len(data):
            op = data[i + 1]
            if op == 0x0A:
                spans.append(Span(kind="break", raw=data[i : i + 2], offset=i, length=2, op=op, hidden=bool(private)))
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

            behavior = behavior_for(op)
            tag = START_TAGS.get(op) or END_TAGS.get(op) or SEMANTIC_CONTROL_TAGS.get(op)
            known = is_known_opcode(op)
            if not known:
                unknown_controls += 1
            kind = "control"
            if op == 0x09:
                kind = "section"
            elif op == 0x4D:
                kind = "media_ref"
            attrs = {"tag": tag} if tag else {}
            if behavior is not None:
                attrs.update(
                    {
                        "opcode_category": behavior.category.value,
                        "semantic_name": behavior.semantic_name,
                        "argument_shape": behavior.argument_shape,
                    }
                )
                if behavior.diagnostic_code:
                    attrs["diagnostic_code"] = behavior.diagnostic_code
            spans.append(
                Span(
                    kind=kind,
                    raw=data[i : i + 2 + arg_len],
                    offset=i,
                    length=2 + arg_len,
                    op=op,
                    payload=payload,
                    hidden=bool(private) or (behavior is not None and behavior.friendly_visibility == FriendlyVisibility.HIDDEN),
                    attrs=attrs,
                )
            )
            i += 2 + arg_len
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            raw = data[i : i + 2]
            value = decode_jis_pair(raw)
            if not value:
                invalid_jis_pairs += 1
                spans.append(
                    Span(
                        kind="invalid_jis_pair",
                        raw=raw,
                        offset=i,
                        length=2,
                        hidden=bool(private),
                    )
                )
                i += 2
                continue
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

    return DecodeResult(
        spans=tuple(spans),
        spans_debug=tuple(span.debug for span in spans),
        text="".join(text_parts),
        unknown_controls=unknown_controls,
        unknown_bytes=unknown_bytes,
        invalid_jis_pairs=invalid_jis_pairs,
    )
