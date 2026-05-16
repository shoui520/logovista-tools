"""Decoders for loose LogoVista media/resource side files.

These helpers cover small file families that sit outside the main SSED
SSEDINFO/SSEDDATA component catalog: Britannica media snippets and selected
extensionless LogoFontCipher resources.  They are deliberately structural:
callers may choose whether to expose private text, but the parser records the
address/resource relationships either way.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_file_to_path,
    decrypt_logofont_cipher_prefix,
    decrypt_macos_logofont_cipher_file_to_path,
    decrypt_macos_logofont_cipher_prefix,
)
from .ssed import CaseFoldedDirectory, is_metadata_noise_path, read_file_prefix
from .windows import file_magic_kind


LVED_ADDR_RE = re.compile(r"lved\.addr(?P<block>[0-9A-Fa-f]{8}):(?P<offset>[0-9A-Fa-f]{4})")
WHATDAY_NAME_RE = re.compile(r"^(?P<month>\d{1,2})-(?P<day>\d{1,2})\.(?P<kind>body|top)$", re.IGNORECASE)
BRITANNICA_TOP_NAME_RE = re.compile(r"^top_(?P<category>[A-Za-z0-9_-]+)\.dat$", re.IGNORECASE)
TOP_ADDRESS_RE = re.compile(r"^(?P<block>[0-9A-Fa-f]{8}):(?P<offset>[0-9A-Fa-f]{4})$")
LAW_RESOURCE_NAMES = frozenset({"inshizei", "minji", "zenkoku", "zeihou"})


@dataclass(frozen=True)
class LooseAddress:
    """One decoded LogoVista address reference."""

    raw: str
    block: int
    offset: int

    def to_dict(self) -> dict[str, object]:
        return {"raw": self.raw, "block": self.block, "offset": self.offset}


@dataclass(frozen=True)
class HtmlReference:
    """One href/src-style reference extracted from an HTML fragment."""

    attribute: str
    value: str
    address: LooseAddress | None

    def to_dict(self) -> dict[str, object]:
        return {
            "attribute": self.attribute,
            "value": self.value,
            "address": self.address.to_dict() if self.address else None,
        }


@dataclass(frozen=True)
class BritannicaWhatdayFile:
    """A decoded Britannica ``whatday`` ``.body`` or ``.top`` HTML fragment."""

    path: Path
    month: int
    day: int
    kind: str
    encoding: str
    html: str
    text: str
    references: tuple[HtmlReference, ...]

    def to_dict(self, *, include_text: bool = False) -> dict[str, object]:
        row: dict[str, object] = {
            "path": str(self.path),
            "name": self.path.name,
            "month": self.month,
            "day": self.day,
            "fragment_kind": self.kind,
            "encoding": self.encoding,
            "bytes": self.path.stat().st_size,
            "references": [ref.to_dict() for ref in self.references],
        }
        if include_text:
            row["html"] = self.html
            row["text"] = self.text
        return row


@dataclass(frozen=True)
class BritannicaTopRecord:
    """One five-line record from Britannica media ``top/top_*.dat``."""

    index: int
    item_id: str
    title: str
    description: str
    address: LooseAddress
    image_name: str
    image_paths: tuple[Path, ...]

    def to_dict(self, *, include_text: bool = False) -> dict[str, object]:
        row: dict[str, object] = {
            "index": self.index,
            "item_id": self.item_id,
            "address": self.address.to_dict(),
            "image_name": self.image_name,
            "image_paths": [str(path) for path in self.image_paths],
        }
        if include_text:
            row["title"] = self.title
            row["description"] = self.description
        return row


@dataclass(frozen=True)
class BritannicaTopDat:
    """Decoded Britannica media top-list DAT file."""

    path: Path
    category: str
    encoding: str
    records: tuple[BritannicaTopRecord, ...]

    def to_dict(self, *, include_text: bool = False, limit: int | None = None) -> dict[str, object]:
        records = self.records if limit is None else self.records[:limit]
        return {
            "path": str(self.path),
            "name": self.path.name,
            "category": self.category,
            "encoding": self.encoding,
            "records": len(self.records),
            "items": [record.to_dict(include_text=include_text) for record in records],
        }


@dataclass(frozen=True)
class LooseDecodedResource:
    """A loose resource with optional LogoFontCipher decoding."""

    path: Path
    storage: str
    content_kind: str
    role: str
    output_extension: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "bytes": self.path.stat().st_size,
            "storage": self.storage,
            "content_kind": self.content_kind,
            "role": self.role,
            "output_extension": self.output_extension,
        }


class _HtmlReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[HtmlReference] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if value is None:
                continue
            lower = key.lower()
            if lower not in {"href", "src", "background"}:
                continue
            self.references.append(HtmlReference(attribute=lower, value=value, address=parse_lved_address(value)))

    def handle_data(self, data: str) -> None:
        if data:
            self.text_parts.append(data)


def parse_lved_address(value: str) -> LooseAddress | None:
    match = LVED_ADDR_RE.search(value)
    if match is None:
        return None
    raw = match.group(0)
    return LooseAddress(
        raw=raw,
        block=int(match.group("block"), 16),
        offset=int(match.group("offset"), 16),
    )


def parse_top_address(value: str) -> LooseAddress:
    match = TOP_ADDRESS_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"not a Britannica top address: {value!r}")
    raw = match.group(0)
    return LooseAddress(raw=raw, block=int(match.group("block"), 16), offset=int(match.group("offset"), 16))


def decode_loose_text(data: bytes) -> tuple[str, str]:
    """Decode observed loose text resources.

    Britannica media files in the current corpus are CP932, but UTF-8 is kept
    first for portable synthetic fixtures and mobile-style resources.
    """

    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace"), "cp932-replace"


def parse_britannica_whatday_file(path: Path) -> BritannicaWhatdayFile:
    match = WHATDAY_NAME_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"not a Britannica whatday file name: {path.name}")
    text, encoding = decode_loose_text(path.read_bytes())
    parser = _HtmlReferenceParser()
    parser.feed(text)
    plain = " ".join(
        " ".join(html.unescape(part).split()) for part in parser.text_parts if part.strip()
    ).strip()
    return BritannicaWhatdayFile(
        path=path,
        month=int(match.group("month")),
        day=int(match.group("day")),
        kind=match.group("kind").lower(),
        encoding=encoding,
        html=text,
        text=plain,
        references=tuple(parser.references),
    )


def is_britannica_whatday_path(path: Path) -> bool:
    return path.is_file() and WHATDAY_NAME_RE.fullmatch(path.name) is not None


def resolve_sibling_image_paths(dat_path: Path, image_name: str) -> tuple[Path, ...]:
    root = dat_path.parent.parent
    candidates = [
        root / "thumb" / image_name,
        root / "mini" / image_name,
        root / "full" / image_name,
        root / image_name,
    ]
    found: list[Path] = []
    for candidate in candidates:
        actual = CaseFoldedDirectory.from_path(candidate.parent).find(candidate.name)
        if actual is not None and actual.is_file() and actual not in found:
            found.append(actual)
    return tuple(found)


def parse_britannica_top_dat(path: Path) -> BritannicaTopDat:
    match = BRITANNICA_TOP_NAME_RE.fullmatch(path.name)
    if match is None:
        raise ValueError(f"not a Britannica top DAT file name: {path.name}")
    text, encoding = decode_loose_text(path.read_bytes())
    lines = [line.rstrip("\r") for line in text.splitlines()]
    records: list[BritannicaTopRecord] = []
    cursor = 0
    while cursor < len(lines):
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor >= len(lines):
            break
        chunk = lines[cursor : cursor + 5]
        if len(chunk) < 5:
            raise ValueError(f"truncated Britannica top record at line {cursor + 1}")
        item_id, title, description, address, image_name = [part.strip() for part in chunk]
        records.append(
            BritannicaTopRecord(
                index=len(records),
                item_id=item_id,
                title=title,
                description=description,
                address=parse_top_address(address),
                image_name=image_name,
                image_paths=resolve_sibling_image_paths(path, image_name),
            )
        )
        cursor += 5
        if cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
    return BritannicaTopDat(path=path, category=match.group("category").lower(), encoding=encoding, records=tuple(records))


def is_britannica_top_dat_path(path: Path) -> bool:
    return path.is_file() and path.parent.name.lower() == "top" and BRITANNICA_TOP_NAME_RE.fullmatch(path.name) is not None


def output_extension_for_kind(kind: str) -> str:
    return {
        "pdf": ".pdf",
        "wave": ".wav",
        "riff": ".riff",
        "mp3": ".mp3",
        "sqlite": ".sqlite",
        "zip": ".zip",
        "png": ".png",
        "html": ".html",
    }.get(kind, ".bin")


def role_for_loose_resource(path: Path, content_kind: str) -> str:
    parent = path.parent.name.lower()
    package = path.parent.parent.name.lower() if parent == "resources" else path.parent.name.lower()
    if parent == "dat" and content_kind == "wave":
        return "proyal53_dat_wave"
    if path.parent.name.casefold().endswith("_sound_files") and content_kind == "wave":
        return "ziptomedia_wave"
    if parent == "resources" and path.name.casefold() in LAW_RESOURCE_NAMES and content_kind == "pdf":
        return "multiview_law_pdf"
    if path.suffix == "" and content_kind == "sqlite":
        return "renderer_sqlite_sidecar"
    if "cjjc160" in package and content_kind == "sqlite":
        return "renderer_sqlite_sidecar"
    return f"loose_{content_kind}"


def _loose_content_kind(data: bytes) -> str:
    kind = file_magic_kind(data)
    if kind == "riff" and data[8:12] == b"WAVE":
        return "wave"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    return kind


def classify_loose_decoded_resource(path: Path) -> LooseDecodedResource | None:
    if not path.is_file() or is_metadata_noise_path(path):
        return None
    prefix = read_file_prefix(path, 4096)
    raw_kind = _loose_content_kind(prefix)
    if raw_kind != "unknown":
        return LooseDecodedResource(
            path=path,
            storage="plain",
            content_kind=raw_kind,
            role=role_for_loose_resource(path, raw_kind),
            output_extension=output_extension_for_kind(raw_kind),
        )
    if path.stat().st_size % 16:
        return None
    storage = "logofont_cipher"
    try:
        decrypted_prefix = decrypt_logofont_cipher_prefix(prefix, size=min(len(prefix), 4096))
        kind = _loose_content_kind(decrypted_prefix)
    except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError):
        kind = "unknown"
    if kind == "unknown":
        try:
            decrypted_prefix = decrypt_macos_logofont_cipher_prefix(prefix, size=min(len(prefix), 4096))
            kind = _loose_content_kind(decrypted_prefix)
            storage = "macos_logofont_cipher"
        except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError):
            return None
    if kind == "unknown":
        return None
    return LooseDecodedResource(
        path=path,
        storage=storage,
        content_kind=kind,
        role=role_for_loose_resource(path, kind),
        output_extension=output_extension_for_kind(kind),
    )


def should_consider_loose_resource(path: Path) -> bool:
    if not path.is_file() or is_metadata_noise_path(path):
        return False
    if path.suffix:
        return False
    parent = path.parent.name.casefold()
    if parent in {"dat", "resources"}:
        return True
    if parent.endswith("_sound_files"):
        return True
    if path.name.upper() == path.parent.name.removeprefix("_DCT_").upper():
        return True
    return path.name.casefold() in LAW_RESOURCE_NAMES


def iter_candidate_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or is_metadata_noise_path(path):
                continue
            if is_britannica_whatday_path(path) or is_britannica_top_dat_path(path) or should_consider_loose_resource(path):
                yield path


def write_decoded_resource(resource: LooseDecodedResource, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = resource.path.name
    if not Path(out_name).suffix:
        out_name += resource.output_extension
    out_path = out_dir / out_name
    if resource.storage == "logofont_cipher":
        decrypt_logofont_cipher_file_to_path(resource.path, out_path)
    elif resource.storage == "macos_logofont_cipher":
        decrypt_macos_logofont_cipher_file_to_path(resource.path, out_path)
    else:
        out_path.write_bytes(resource.path.read_bytes())
    return out_path
