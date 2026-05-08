"""Lossless-ish text stream spans for expanded LogoVista body bytes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .entries import (
    CONTROL_ARG_LENGTHS,
    control_tag_for_end,
    control_tag_for_start,
    decode_jis_pair,
    normalize_fullwidth_ascii,
)


ParseMode = Literal["lenient", "forensic", "strict"]


class LosslessDecodeError(ValueError):
    """Raised by strict span decoding when bytes cannot be classified safely."""


@dataclass(frozen=True)
class DecodeIssue:
    kind: str
    offset: int
    length: int
    raw_hex: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "offset": self.offset,
            "length": self.length,
            "raw_hex": self.raw_hex,
            "message": self.message,
        }


@dataclass(frozen=True)
class Span:
    kind: str
    start: int
    end: int
    raw_hex: str
    text: str | None = None
    normalized: str | None = None
    op: str | None = None
    payload_hex: str | None = None
    tag: str | None = None
    code: str | None = None
    gaiji_space: str | None = None
    resolved: str | None = None
    image_backed: bool | None = None
    issue: str | None = None

    def as_dict(self, *, include_raw: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
        }
        if include_raw:
            row["raw_hex"] = self.raw_hex
        for key, value in (
            ("text", self.text),
            ("normalized", self.normalized),
            ("op", self.op),
            ("payload_hex", self.payload_hex),
            ("tag", self.tag),
            ("code", self.code),
            ("gaiji_space", self.gaiji_space),
            ("resolved", self.resolved),
            ("image_backed", self.image_backed),
            ("issue", self.issue),
        ):
            if value is not None:
                row[key] = value
        return row


@dataclass
class SpanDecodeResult:
    spans: list[Span] = field(default_factory=list)
    issues: list[DecodeIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    control_ops: dict[str, int] = field(default_factory=dict)
    unknown_control_ops: dict[str, int] = field(default_factory=dict)
    issue_counts: dict[str, int] = field(default_factory=dict)
    collect_spans: bool = True
    max_issues: int | None = None

    def as_dict(
        self,
        *,
        include_spans: bool = True,
        include_raw: bool = True,
        max_issues: int | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "stats": dict(sorted(self.stats.items())),
            "control_ops": dict(sorted(self.control_ops.items())),
            "unknown_control_ops": dict(sorted(self.unknown_control_ops.items())),
            "issue_counts": dict(sorted(self.issue_counts.items())),
            "issues": [
                issue.as_dict()
                for issue in (self.issues if max_issues is None else self.issues[:max_issues])
            ],
        }
        if include_spans:
            row["spans"] = [span.as_dict(include_raw=include_raw) for span in self.spans]
        return row


BASE_STATS = {
    "bytes_total": 0,
    "bytes_covered": 0,
    "padding_bytes": 0,
    "ascii_bytes": 0,
    "jis_pairs": 0,
    "jis_bytes": 0,
    "controls": 0,
    "known_controls": 0,
    "unknown_controls": 0,
    "control_payload_bytes": 0,
    "sections": 0,
    "breaks": 0,
    "links": 0,
    "media": 0,
    "gaiji": 0,
    "gaiji_resolved": 0,
    "gaiji_unresolved": 0,
    "gaiji_image_backed": 0,
    "invalid_jis_pairs": 0,
    "unknown_bytes": 0,
    "truncated_controls": 0,
    "truncated_gaiji": 0,
}


def _bump(mapping: dict[str, int], key: str, value: int = 1) -> None:
    mapping[key] = mapping.get(key, 0) + value


def _record_issue(
    result: SpanDecodeResult,
    *,
    mode: ParseMode,
    kind: str,
    offset: int,
    raw: bytes,
    message: str,
) -> None:
    issue = DecodeIssue(kind=kind, offset=offset, length=len(raw), raw_hex=raw.hex(), message=message)
    _bump(result.issue_counts, kind)
    if result.max_issues is None or len(result.issues) < result.max_issues:
        result.issues.append(issue)
    if mode == "strict":
        raise LosslessDecodeError(f"{kind} at offset {offset}: {message}")


def _add_span(result: SpanDecodeResult, span: Span) -> None:
    if result.collect_spans:
        result.spans.append(span)
    result.stats["bytes_covered"] += span.end - span.start


def _span_raw_hex(result: SpanDecodeResult, raw: bytes) -> str:
    return raw.hex() if result.collect_spans else ""


def decode_lossless_spans(
    data: bytes,
    *,
    gaiji_map: dict[str, str] | None = None,
    image_gaiji_keys: frozenset[str] | set[str] | None = None,
    mode: ParseMode = "forensic",
    include_padding: bool = True,
    collect_spans: bool = True,
    max_issues: int | None = None,
) -> SpanDecodeResult:
    """Decode expanded text bytes into offset-addressed spans.

    The decoder classifies every byte into a span. Unknown or unsafe structures
    remain represented as problem spans so callers can measure damage instead
    of silently losing bytes.
    """

    if mode not in {"lenient", "forensic", "strict"}:
        raise ValueError(f"invalid parse mode: {mode}")

    gaiji_map = gaiji_map or {}
    image_gaiji_keys = image_gaiji_keys or frozenset()
    result = SpanDecodeResult(
        stats=dict(BASE_STATS),
        collect_spans=collect_spans,
        max_issues=max_issues,
    )
    result.stats["bytes_total"] = len(data)
    i = 0
    while i < len(data):
        b = data[i]

        if b == 0:
            start = i
            while i < len(data) and data[i] == 0:
                i += 1
            raw = data[start:i]
            result.stats["padding_bytes"] += len(raw)
            if include_padding:
                _add_span(result, Span("padding", start, i, _span_raw_hex(result, raw)))
            else:
                result.stats["bytes_covered"] += len(raw)
            continue

        if b == 0x1F:
            start = i
            if i + 1 >= len(data):
                raw = data[start:]
                result.stats["truncated_controls"] += 1
                _record_issue(
                    result,
                    mode=mode,
                    kind="truncated_control",
                    offset=start,
                    raw=raw,
                    message="0x1f control introducer has no opcode byte",
                )
                _add_span(result, Span("problem", start, len(data), _span_raw_hex(result, raw), issue="truncated_control"))
                break

            op = data[i + 1]
            op_hex = f"{op:02x}"
            arg_len = CONTROL_ARG_LENGTHS.get(op, 0)
            length = 2 + arg_len
            raw = data[start : min(len(data), start + length)]
            result.stats["controls"] += 1
            result.stats["control_payload_bytes"] += max(0, len(raw) - 2)
            _bump(result.control_ops, op_hex)

            if len(raw) < length:
                result.stats["truncated_controls"] += 1
                _record_issue(
                    result,
                    mode=mode,
                    kind="truncated_control",
                    offset=start,
                    raw=raw,
                    message=f"control 1f{op_hex} expected {length} bytes, got {len(raw)}",
                )
                _add_span(
                    result,
                    Span("control", start, start + len(raw), _span_raw_hex(result, raw), op=op_hex, issue="truncated_control"),
                )
                break

            payload = raw[2:]
            if op == 0x09:
                result.stats["known_controls"] += 1
                result.stats["sections"] += 1
                _add_span(
                    result,
                    Span("section", start, start + length, _span_raw_hex(result, raw), op=op_hex, payload_hex=payload.hex()),
                )
                i += length
                continue

            if op == 0x0A:
                result.stats["known_controls"] += 1
                result.stats["breaks"] += 1
                _add_span(result, Span("break", start, start + length, _span_raw_hex(result, raw), op=op_hex))
                i += length
                continue

            if op == 0x4D:
                result.stats["known_controls"] += 1
                result.stats["media"] += 1
                _add_span(
                    result,
                    Span("media_ref", start, start + length, _span_raw_hex(result, raw), op=op_hex, payload_hex=payload.hex()),
                )
                i += length
                continue

            start_tag = control_tag_for_start(op)
            end_tag = control_tag_for_end(op)
            if start_tag is not None or end_tag is not None or op in (
                0x00,
                0x02,
                0x03,
                0x04,
                0x05,
                0x1A,
                0x1C,
            ):
                tag = start_tag or end_tag
                if start_tag in {"link", "url"}:
                    result.stats["links"] += 1
                result.stats["known_controls"] += 1
                _add_span(
                    result,
                    Span(
                        "control",
                        start,
                        start + length,
                        _span_raw_hex(result, raw),
                        op=op_hex,
                        payload_hex=payload.hex() or None,
                        tag=tag,
                    ),
                )
                i += length
                continue

            result.stats["unknown_controls"] += 1
            _bump(result.unknown_control_ops, op_hex)
            _record_issue(
                result,
                mode=mode,
                kind="unknown_control",
                offset=start,
                raw=raw,
                message=f"unknown control opcode 1f{op_hex}; argument length is not known",
            )
            _add_span(
                result,
                Span("unknown_control", start, start + length, _span_raw_hex(result, raw), op=op_hex, issue="unknown_control"),
            )
            i += length
            continue

        if i + 1 < len(data) and data[i : i + 2] == b"\x11\x03":
            raw = data[i : i + 2]
            result.stats["controls"] += 1
            result.stats["known_controls"] += 1
            result.stats["legacy_controls"] += 1
            _bump(result.control_ops, "bare_1103")
            _add_span(
                result,
                Span("control", i, i + 2, _span_raw_hex(result, raw), op="bare_1103", tag="title-separator"),
            )
            i += 2
            continue

        if b == 0x0A:
            raw = data[i : i + 1]
            result.stats["breaks"] += 1
            _add_span(result, Span("break", i, i + 1, _span_raw_hex(result, raw)))
            i += 1
            continue

        if i + 1 < len(data) and 0x21 <= b <= 0x7E and 0x21 <= data[i + 1] <= 0x7E:
            raw = data[i : i + 2]
            text = decode_jis_pair(raw)
            if text:
                normalized = normalize_fullwidth_ascii(text)
                result.stats["jis_pairs"] += 1
                result.stats["jis_bytes"] += 2
                _add_span(result, Span("text", i, i + 2, _span_raw_hex(result, raw), text=text, normalized=normalized))
            else:
                result.stats["invalid_jis_pairs"] += 1
                _record_issue(
                    result,
                    mode=mode,
                    kind="invalid_jis_pair",
                    offset=i,
                    raw=raw,
                    message="JIS pair could not be decoded as ISO-2022-JP",
                )
                _add_span(result, Span("problem", i, i + 2, _span_raw_hex(result, raw), issue="invalid_jis_pair"))
            i += 2
            continue

        if 0xA1 <= b <= 0xFE:
            if i + 1 >= len(data):
                raw = data[i:]
                result.stats["truncated_gaiji"] += 1
                _record_issue(
                    result,
                    mode=mode,
                    kind="truncated_gaiji",
                    offset=i,
                    raw=raw,
                    message="gaiji lead byte has no trailing byte",
                )
                _add_span(result, Span("gaiji", i, len(data), _span_raw_hex(result, raw), issue="truncated_gaiji"))
                break
            raw = data[i : i + 2]
            key = raw.hex()
            resolved = gaiji_map.get(key)
            image_backed = key in image_gaiji_keys
            result.stats["gaiji"] += 1
            if resolved is not None:
                result.stats["gaiji_resolved"] += 1
            else:
                result.stats["gaiji_unresolved"] += 1
            if image_backed:
                result.stats["gaiji_image_backed"] += 1
            _add_span(
                result,
                Span(
                    "gaiji",
                    i,
                    i + 2,
                    _span_raw_hex(result, raw),
                    code=key,
                    gaiji_space="half" if b < 0xB0 else "full",
                    resolved=resolved,
                    image_backed=image_backed,
                ),
            )
            i += 2
            continue

        if 0x20 <= b <= 0x7E:
            raw = data[i : i + 1]
            result.stats["ascii_bytes"] += 1
            _add_span(result, Span("ascii", i, i + 1, _span_raw_hex(result, raw), text=chr(b), normalized=chr(b)))
            i += 1
            continue

        raw = data[i : i + 1]
        result.stats["unknown_bytes"] += 1
        _record_issue(
            result,
            mode=mode,
            kind="unknown_byte",
            offset=i,
            raw=raw,
            message=f"byte 0x{b:02x} is not classified by the text decoder",
        )
        _add_span(result, Span("problem", i, i + 1, _span_raw_hex(result, raw), issue="unknown_byte"))
        i += 1

    return result


def combine_span_stats(results: list[SpanDecodeResult]) -> dict[str, Any]:
    stats: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    control_ops: dict[str, int] = {}
    unknown_control_ops: dict[str, int] = {}
    for result in results:
        for key, value in result.stats.items():
            _bump(stats, key, value)
        for key, value in result.control_ops.items():
            _bump(control_ops, key, value)
        for key, value in result.issue_counts.items():
            _bump(issue_counts, key, value)
        for key, value in result.unknown_control_ops.items():
            _bump(unknown_control_ops, key, value)
    return {
        "stats": dict(sorted(stats.items())),
        "control_ops": dict(sorted(control_ops.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "unknown_control_ops": dict(sorted(unknown_control_ops.items())),
    }
