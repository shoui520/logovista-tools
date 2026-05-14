"""Shared helpers for the lvcore SSED package reader."""

from __future__ import annotations

import re
import unicodedata

from .index import IndexRow
from .model import Component

ENTRY_MARKER = b"\x1f\x09\x00\x01"
EXACT_INDEX_PROBE_PAGES = 128
EXPENSIVE_SIDECAR_BYTES = 64 * 1024 * 1024
SearchValueRow = tuple[IndexRow, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]
TitleMatch = tuple[Component, int, str]
GAIJI_DEBUG_TAG_RE = re.compile(r"<z[0-9A-Fa-f]{4}>")


def _media_mime_and_format(payload: bytes, *, store_kind: str) -> tuple[str, str, str]:
    if payload.startswith(b"BM"):
        return "image/bmp", "bmp", "bitmap"
    if payload.startswith(b"\xff\xd8"):
        return "image/jpeg", "jpeg", "image"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png", "image"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif", "image"
    if payload.startswith(b"ID3") or (len(payload) >= 2 and payload[0] == 0xFF and (payload[1] & 0xE0) == 0xE0):
        return "audio/mpeg", "mp3", "audio"
    if payload.startswith(b"RIFF") and b"WAVE" in payload[:16]:
        return "audio/wav", "wave", "audio"
    if payload.startswith(b"fmt ") or (b"fmt " in payload[:64] and b"data" in payload[:256]):
        return "audio/x-logovista-wave-chunks", "wave_chunks", "audio"
    if store_kind == "pcmdata" and payload:
        return "application/octet-stream", "unknown_pcmdata_payload", "audio"
    return "application/octet-stream", "unknown", "binary"


def _jis_cell_bytes(text: str) -> bytes | None:
    out = bytearray()
    for ch in text:
        try:
            encoded = ch.encode("iso2022_jp")
        except UnicodeEncodeError:
            return None
        out.extend(encoded.removeprefix(b"\x1b$B").removesuffix(b"\x1b(B"))
    return bytes(out)


def _title_surface_query_bytes(query: str) -> bytes | None:
    text = unicodedata.normalize("NFKC", str(query or "")).strip()
    if not text:
        return None
    converted: list[str] = []
    for ch in text:
        if ch == " ":
            converted.append("\u3000")
        elif 0x21 <= ord(ch) <= 0x7E:
            converted.append(chr(ord(ch) + 0xFEE0))
        else:
            converted.append(ch)
    return _jis_cell_bytes("".join(converted))


def _index_ascii_passthrough_query_bytes(query: str) -> bytes | None:
    text = unicodedata.normalize("NFKC", str(query or "")).strip()
    if not text:
        return None
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if ch == " ":
            continue
        if 0x21 <= code <= 0x7E:
            out.append(code)
            continue
        raw = _jis_cell_bytes(ch)
        if raw is None:
            return None
        out.extend(raw)
    return bytes(out)


def _contains_cjk_ideograph(value: str) -> bool:
    for ch in str(value or ""):
        code = ord(ch)
        if (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0x20000 <= code <= 0x2FA1F
        ):
            return True
    return False


_SMALL_KANA_INDEX_SEEK = str.maketrans(
    {
        "ぁ": "あ",
        "ぃ": "い",
        "ぅ": "う",
        "ぇ": "え",
        "ぉ": "お",
        "っ": "つ",
        "ゃ": "や",
        "ゅ": "ゆ",
        "ょ": "よ",
        "ゎ": "わ",
        "ァ": "ア",
        "ィ": "イ",
        "ゥ": "ウ",
        "ェ": "エ",
        "ォ": "オ",
        "ッ": "ツ",
        "ャ": "ヤ",
        "ュ": "ユ",
        "ョ": "ヨ",
        "ヮ": "ワ",
    }
)


_ASCII_JIS_SYMBOLS = {
    "-": "−",
    "~": "￣",
    "/": "／",
    "+": "＋",
    "&": "＆",
    ".": "．",
    ",": "，",
    ":": "：",
    ";": "；",
    "(": "（",
    ")": "）",
}


def _fold_small_kana_for_index_seek(value: str) -> str:
    return str(value or "").translate(_SMALL_KANA_INDEX_SEEK)


def _jis_symbol_index_query_bytes(query: str) -> bytes | None:
    text = unicodedata.normalize("NFKC", str(query or "")).strip()
    if not text:
        return None
    converted: list[str] = []
    for ch in text:
        if ch == " ":
            converted.append("\u3000")
        elif ch in _ASCII_JIS_SYMBOLS:
            converted.append(_ASCII_JIS_SYMBOLS[ch])
        elif 0x21 <= ord(ch) <= 0x7E:
            converted.append(chr(ord(ch) + 0xFEE0))
        else:
            converted.append(ch)
    return _jis_cell_bytes("".join(converted))


