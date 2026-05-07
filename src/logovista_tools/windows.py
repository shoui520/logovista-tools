"""Windows LogoVista sidecar helpers."""

from __future__ import annotations

import configparser
import html
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lvcrypto import LogoVistaCryptoError, LogoVistaCryptoUnavailable, decrypt_logofont_cipher_prefix
from .ssed import BLOCK_SIZE, SsedInfoElement, find_case_insensitive


SQLITE_MAGIC = b"SQLite format 3\x00"
HEX_POINTER_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


@dataclass(frozen=True)
class Exinfo:
    """Parsed Windows ``EXINFO.INI`` fields."""

    path: Path
    general: dict[str, str]


@dataclass(frozen=True)
class AuxIndexSpec:
    """One ``IDXNAME`` / ``IDXINFO`` pair from ``EXINFO.INI``."""

    index: int
    name: str
    info: str
    path: Path | None


@dataclass(frozen=True)
class AuxIndexRow:
    """One row from a Windows text auxiliary index."""

    line_number: int
    block: int
    offset: int
    depth: int
    label: str
    path: tuple[str, ...]
    target: dict[str, Any] | None = None


@dataclass(frozen=True)
class RendererSidecar:
    """A Windows renderer sidecar that is or decrypts to SQLite."""

    path: Path
    storage: str


def exinfo_path_for_idx(idx: Path) -> Path | None:
    return find_case_insensitive(idx.parent, "EXINFO.INI")


def parse_exinfo(path: Path) -> Exinfo:
    text = path.read_text(encoding="cp932", errors="replace")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    general = dict(parser["GENERAL"]) if parser.has_section("GENERAL") else {}
    return Exinfo(path=path, general=general)


def load_exinfo_for_idx(idx: Path) -> Exinfo | None:
    path = exinfo_path_for_idx(idx)
    if path is None:
        return None
    return parse_exinfo(path)


def iter_aux_index_specs(exinfo: Exinfo) -> list[AuxIndexSpec]:
    raw_count = exinfo.general.get("IDXCOUNT", "0")
    try:
        count = int(raw_count)
    except ValueError:
        count = 0

    rows: list[AuxIndexSpec] = []
    for index in range(count):
        name = exinfo.general.get(f"IDXNAME{index}", "")
        info = exinfo.general.get(f"IDXINFO{index}", "")
        path = exinfo.path.parent / info if info else None
        rows.append(AuxIndexSpec(index=index, name=name, info=info, path=path))
    if not rows and exinfo.general.get("IDXINFO"):
        info = exinfo.general["IDXINFO"]
        name = exinfo.general.get("IDXTITLE", "")
        rows.append(AuxIndexSpec(index=0, name=name, info=info, path=exinfo.path.parent / info))
    return rows


def resolve_aux_virtual_target(block: int, offset: int) -> dict[str, Any] | None:
    if offset != 0xFFFF or block & 0x0FFFFFFF:
        return None
    selector = block >> 28
    if not selector:
        return None
    return {
        "kind": "virtual-index-selector",
        "selector": f"{selector:x}",
        "block": f"{block:08x}",
        "offset": f"{offset:04x}",
    }


def resolve_component_target(block: int, offset: int, elements: list[SsedInfoElement]) -> dict[str, Any] | None:
    for element in elements:
        if not element.start:
            continue
        if element.start <= block <= element.end:
            relative_offset = (block - element.start) * BLOCK_SIZE + offset
            return {
                "component": element.filename,
                "component_type": f"{element.type:02x}",
                "start_block": element.start,
                "end_block": element.end,
                "relative_offset": relative_offset,
                "offset_in_block": offset,
            }
    return resolve_aux_virtual_target(block, offset)


def parse_aux_index_text(path: Path, elements: list[SsedInfoElement] | None = None) -> list[AuxIndexRow]:
    """Parse Windows CP932 auxiliary ``*.IDX`` trees.

    These files are not ``SSEDINFO`` catalogs. Each line starts with two
    eight-digit hexadecimal numbers and then uses tab depth before the visible
    label.
    """

    data = path.read_bytes()
    if data.startswith(b"SSEDINFO"):
        return []
    text = data.decode("cp932", errors="replace")
    rows: list[AuxIndexRow] = []
    stack: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 3 or not HEX_POINTER_RE.fullmatch(parts[0]) or not HEX_POINTER_RE.fullmatch(parts[1]):
            continue
        label_index = None
        label = ""
        for idx, part in enumerate(parts[2:], start=1):
            if part:
                label_index = idx
                label = html.unescape(part)
        if label_index is None:
            continue
        depth = label_index
        if len(stack) < depth:
            stack.extend([""] * (depth - len(stack)))
        stack = stack[:depth]
        stack[depth - 1] = label
        block = int(parts[0], 16)
        offset = int(parts[1], 16)
        target = resolve_component_target(block, offset, elements) if elements is not None else None
        rows.append(
            AuxIndexRow(
                line_number=line_number,
                block=block,
                offset=offset,
                depth=depth,
                label=label,
                path=tuple(item for item in stack if item),
                target=target,
            )
        )
    return rows


def aux_index_row_to_json(row: AuxIndexRow) -> dict[str, Any]:
    return {
        "line_number": row.line_number,
        "block": row.block,
        "offset": row.offset,
        "depth": row.depth,
        "label": row.label,
        "path": list(row.path),
        "target": row.target,
    }


def sqlite_storage_for_path(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as infile:
        prefix = infile.read(64)
    if prefix.startswith(SQLITE_MAGIC):
        return "plain"
    if path.stat().st_size % 16:
        return None
    try:
        if decrypt_logofont_cipher_prefix(prefix, size=64).startswith(SQLITE_MAGIC):
            return "logofont_cipher"
    except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError):
        return None
    return None


def sqlite_has_table(path: Path, table_name: str) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = con.execute(
            "select 1 from sqlite_master where type='table' and name=? limit 1",
            (table_name,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        con.close()
    return row is not None


def discover_renderer_sidecars(idx: Path, exinfo: Exinfo | None = None) -> list[RendererSidecar]:
    """Return sibling files that are plain/encrypted SQLite renderer payloads."""

    candidates: list[Path] = []
    if exinfo is not None:
        rosql = exinfo.general.get("ROSQLNAME")
        if rosql:
            candidate = idx.parent / rosql
            candidates.append(candidate)
    for child in sorted(idx.parent.iterdir()):
        lower = child.name.lower()
        if not child.is_file():
            continue
        if lower == "vlpljbl.bin":
            continue
        if lower.startswith("vlpljbl"):
            candidates.append(child)
        elif child.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            candidates.append(child)

    rows: list[RendererSidecar] = []
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        storage = sqlite_storage_for_path(candidate)
        if storage is None:
            continue
        if storage == "plain" and not sqlite_has_table(candidate, "t_contents"):
            continue
        rows.append(RendererSidecar(path=candidate, storage=storage))
    return rows
