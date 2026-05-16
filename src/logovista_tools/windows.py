"""LogoVista sidecar helpers.

Most helpers in this module were introduced for Windows renderer packages, but
SQLite sidecar classification is intentionally platform-neutral: the same
plain ``*.db`` patterns also appear in Android and portable SSED layouts.
"""

from __future__ import annotations

import configparser
import hashlib
import html
import os
import re
import sqlite3
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_logofont_cipher_file_to_path,
    decrypt_logofont_cipher_prefix,
)
from .ssed import BLOCK_SIZE, SsedInfoElement, find_case_insensitive, read_file_prefix


SQLITE_MAGIC = b"SQLite format 3\x00"
HEX_POINTER_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
NUMERIC_AUX_INDEX_RE = re.compile(r"^[0-9A-Fa-f]{8}(?:_[0-9]+)?\.idx$", re.IGNORECASE)
VLPLJBL_RE = re.compile(r"^vlpljbl(?:$|[A-Za-z]$|\.(?:bin|exe)$)", re.IGNORECASE)
HC_RENDERER_RE = re.compile(r"^HC([0-9A-Fa-f]{4})(?:\..*)?$", re.IGNORECASE)
ASCII_STRING_RE = re.compile(rb"[\x20-\x7e]{5,}")


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
    """A renderer/body sidecar that is or decrypts to SQLite."""

    path: Path
    storage: str


@dataclass(frozen=True)
class SqliteSidecarClassification:
    """Schema-level classification for one plain/encrypted SQLite sidecar."""

    path: Path
    storage: str | None
    role: str
    tables: tuple[dict[str, Any], ...] = ()
    content_kind: str = "sqlite"


@dataclass(frozen=True)
class VlpljblClassification:
    """Forensic classification for one observed ``vlpljbl*`` sibling."""

    path: Path
    suffix: str
    size: int
    sha256: str | None
    storage: str
    content_kind: str
    role: str
    decrypt_prefix_kind: str | None = None
    sqlite_tables: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PeImport:
    """One imported PE function, by name or ordinal."""

    name: str | None
    ordinal: int | None = None


@dataclass(frozen=True)
class PeImportDll:
    """One imported DLL and its imported functions."""

    dll: str
    functions: tuple[PeImport, ...]


@dataclass(frozen=True)
class PeSummary:
    """Small PE summary extracted without optional third-party dependencies."""

    kind: str
    machine: str | None = None
    pe_kind: str | None = None
    timestamp: int | None = None
    section_names: tuple[str, ...] = ()
    export_dll_name: str | None = None
    exports: tuple[str, ...] = ()
    imports: tuple[PeImportDll, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class HcRendererClassification:
    """Forensic classification for one Windows ``HC????.dll`` HTML renderer."""

    path: Path
    code: str
    expected_numeric_index: str
    size: int
    sha256: str | None
    pe: PeSummary
    exinfo_html_dll: str | None
    exinfo_declares_this: bool | None
    numeric_indexes: tuple[str, ...]
    expected_numeric_index_present: bool
    vlpljbl_siblings: tuple[str, ...]
    dic_tokens: tuple[str, ...]
    vlpljbl_tokens: tuple[str, ...]
    html_templates: tuple[str, ...]
    sql_snippets: tuple[str, ...]
    image_templates: tuple[str, ...]
    features: dict[str, bool]


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
        if not name and index == 0:
            name = exinfo.general.get("IDXTITLE", "")
        info = exinfo.general.get(f"IDXINFO{index}", "")
        path = find_case_insensitive(exinfo.path.parent, info) if info else None
        if path is None and info:
            path = exinfo.path.parent / info
        rows.append(AuxIndexSpec(index=index, name=name, info=info, path=path))
    if not rows and exinfo.general.get("IDXINFO"):
        info = exinfo.general["IDXINFO"]
        name = exinfo.general.get("IDXTITLE", "")
        path = find_case_insensitive(exinfo.path.parent, info) or exinfo.path.parent / info
        rows.append(AuxIndexSpec(index=0, name=name, info=info, path=path))
    return rows


def discover_numeric_aux_indexes(idx: Path) -> list[Path]:
    """Return sibling numeric auxiliary ``*.idx`` sidecar trees.

    These files are distinct from the main SSEDINFO ``.IDX`` catalog. Windows
    packages usually reference them from ``EXINFO.INI``. The common basename is
    eight hexadecimal digits such as ``0000013A.idx``; observed large
    auxiliary trees can also be sharded as ``00000151_0.idx`` /
    ``00000151_1.idx``. Some iOS-style packages also carry numeric auxiliary
    indexes, and a few local packages leave them unreferenced by EXINFO.
    """

    rows: list[Path] = []
    seen: set[Path] = set()
    for child in sorted(idx.parent.iterdir()):
        if not child.is_file() or not NUMERIC_AUX_INDEX_RE.fullmatch(child.name):
            continue
        with child.open("rb") as fh:
            if fh.read(8) == b"SSEDINFO":
                continue
        resolved = child.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        rows.append(resolved)
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


def sqlite_sidecar_classification_to_json(row: SqliteSidecarClassification) -> dict[str, Any]:
    return {
        "path": str(row.path),
        "name": row.path.name,
        "storage": row.storage,
        "content_kind": row.content_kind,
        "role": row.role,
        "sqlite_tables": list(row.tables),
    }


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def vlpljbl_suffix(path: Path) -> str:
    name = path.name
    if name.lower() == "vlpljbl":
        return "<none>"
    if name.lower().startswith("vlpljbl."):
        return name[len("vlpljbl") :]
    return name[len("vlpljbl") :]


def is_vlpljbl_path(path: Path) -> bool:
    return path.is_file() and VLPLJBL_RE.fullmatch(path.name) is not None


def file_magic_kind(data: bytes) -> str:
    if data.startswith(SQLITE_MAGIC):
        return "sqlite"
    if data.startswith(b"MZ"):
        return "pe_executable"
    if data.startswith(b"OTTO"):
        return "opentype_cff"
    if data.startswith(b"\x00\x01\x00\x00"):
        return "opentype_ttf"
    if data.startswith(b"SSEDDATA"):
        return "sseddata"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"<!DOCTYPE html") or data.startswith(b"<html"):
        return "html"
    if data.startswith(b"RIFF"):
        return "riff"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole_compound_file"
    return "unknown"


def hc_code_from_name(path: Path) -> str | None:
    """Return the four-hex product/plugin code from an ``HC????`` filename."""

    match = HC_RENDERER_RE.fullmatch(path.name)
    if match is None:
        return None
    return match.group(1).upper()


def expected_numeric_index_for_hc_code(code: str) -> str:
    """Return the conventional eight-hex auxiliary index filename for HC code."""

    return f"{int(code, 16):08X}.idx"


def is_hc_renderer_path(path: Path) -> bool:
    return path.is_file() and hc_code_from_name(path) is not None


def _read_c_string(data: bytes, offset: int | None) -> str | None:
    if offset is None or offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset)
    if end < 0:
        return None
    return data[offset:end].decode("ascii", errors="replace")


def _rva_to_offset(sections: list[dict[str, int | str]], rva: int) -> int | None:
    for section in sections:
        start = int(section["virtual_address"])
        size = max(int(section["virtual_size"]), int(section["raw_size"]))
        if start <= rva < start + size and int(section["raw_pointer"]):
            return int(section["raw_pointer"]) + (rva - start)
    return None


def parse_pe_summary(path: Path) -> PeSummary:
    """Parse enough PE metadata for renderer forensics.

    This intentionally avoids a third-party PE dependency. It covers the normal
    PE32 files observed in LogoVista Windows packages and reports parse errors
    instead of raising when a future file is malformed or uses another shape.
    """

    data = path.read_bytes()
    if not data.startswith(b"MZ"):
        return PeSummary(kind=file_magic_kind(data[:64]))
    try:
        if len(data) < 0x40:
            return PeSummary(kind="pe", error="short_mz_header")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
            return PeSummary(kind="pe", error="missing_pe_signature")

        machine, section_count, timestamp, _ptrsym, _nsym, optional_size, _chars = struct.unpack_from(
            "<HHIIIHH", data, pe_offset + 4
        )
        optional_offset = pe_offset + 24
        optional_magic = struct.unpack_from("<H", data, optional_offset)[0]
        is_pe64 = optional_magic == 0x20B
        if optional_magic not in {0x10B, 0x20B}:
            return PeSummary(
                kind="pe",
                machine=f"{machine:04X}",
                timestamp=timestamp,
                error=f"unsupported_optional_header:{optional_magic:04X}",
            )

        data_dir_offset = optional_offset + (112 if is_pe64 else 96)
        data_dirs: list[tuple[int, int]] = []
        if data_dir_offset + 16 * 8 <= len(data):
            for index in range(16):
                data_dirs.append(struct.unpack_from("<II", data, data_dir_offset + index * 8))
        else:
            data_dirs = [(0, 0)] * 16

        section_offset = pe_offset + 24 + optional_size
        sections: list[dict[str, int | str]] = []
        for index in range(section_count):
            offset = section_offset + index * 40
            if offset + 40 > len(data):
                break
            name = data[offset : offset + 8].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", data, offset + 8)
            sections.append(
                {
                    "name": name,
                    "virtual_size": virtual_size,
                    "virtual_address": virtual_address,
                    "raw_size": raw_size,
                    "raw_pointer": raw_pointer,
                }
            )

        export_dll_name: str | None = None
        exports: list[str] = []
        export_rva, _export_size = data_dirs[0]
        if export_rva:
            export_offset = _rva_to_offset(sections, export_rva)
            if export_offset is not None and export_offset + 40 <= len(data):
                (
                    _characteristics,
                    _export_timestamp,
                    _major,
                    _minor,
                    export_name_rva,
                    _base,
                    _function_count,
                    name_count,
                    _address_functions,
                    address_names,
                    _address_ordinals,
                ) = struct.unpack_from("<IIHHIIIIIII", data, export_offset)
                export_dll_name = _read_c_string(data, _rva_to_offset(sections, export_name_rva))
                name_pointer_offset = _rva_to_offset(sections, address_names)
                if name_pointer_offset is not None:
                    for index in range(min(name_count, 4096)):
                        item_offset = name_pointer_offset + index * 4
                        if item_offset + 4 > len(data):
                            break
                        name_rva = struct.unpack_from("<I", data, item_offset)[0]
                        name = _read_c_string(data, _rva_to_offset(sections, name_rva))
                        if name:
                            exports.append(name)

        imports: list[PeImportDll] = []
        import_rva, _import_size = data_dirs[1]
        if import_rva:
            descriptor_offset = _rva_to_offset(sections, import_rva)
            if descriptor_offset is not None:
                while descriptor_offset + 20 <= len(data):
                    original_first_thunk, _stamp, _chain, name_rva, first_thunk = struct.unpack_from(
                        "<IIIII", data, descriptor_offset
                    )
                    if not any((original_first_thunk, _stamp, _chain, name_rva, first_thunk)):
                        break
                    dll = _read_c_string(data, _rva_to_offset(sections, name_rva)) or "<unknown>"
                    thunk_rva = original_first_thunk or first_thunk
                    thunk_offset = _rva_to_offset(sections, thunk_rva)
                    functions: list[PeImport] = []
                    if thunk_offset is not None:
                        index = 0
                        thunk_size = 8 if is_pe64 else 4
                        ordinal_flag = 0x8000000000000000 if is_pe64 else 0x80000000
                        unpack = "<Q" if is_pe64 else "<I"
                        while thunk_offset + index * thunk_size + thunk_size <= len(data):
                            value = struct.unpack_from(unpack, data, thunk_offset + index * thunk_size)[0]
                            if value == 0:
                                break
                            if value & ordinal_flag:
                                functions.append(PeImport(name=None, ordinal=int(value & 0xFFFF)))
                            else:
                                import_name_offset = _rva_to_offset(sections, int(value))
                                functions.append(
                                    PeImport(
                                        name=_read_c_string(
                                            data,
                                            import_name_offset + 2 if import_name_offset is not None else None,
                                        )
                                    )
                                )
                            index += 1
                            if index > 8192:
                                break
                    imports.append(PeImportDll(dll=dll, functions=tuple(functions)))
                    descriptor_offset += 20

        return PeSummary(
            kind="pe",
            machine={0x014C: "i386", 0x8664: "x86_64"}.get(machine, f"{machine:04X}"),
            pe_kind="PE32+" if is_pe64 else "PE32",
            timestamp=timestamp,
            section_names=tuple(str(section["name"]) for section in sections),
            export_dll_name=export_dll_name,
            exports=tuple(exports),
            imports=tuple(imports),
        )
    except (struct.error, ValueError, OSError) as exc:
        return PeSummary(kind="pe", error=f"{type(exc).__name__}:{exc}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_import_to_json(row: PeImport) -> dict[str, Any]:
    return {"name": row.name, "ordinal": row.ordinal}


def pe_import_dll_to_json(row: PeImportDll) -> dict[str, Any]:
    return {"dll": row.dll, "functions": [pe_import_to_json(function) for function in row.functions]}


def pe_summary_to_json(row: PeSummary) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "machine": row.machine,
        "pe_kind": row.pe_kind,
        "timestamp": row.timestamp,
        "section_names": list(row.section_names),
        "export_dll_name": row.export_dll_name,
        "exports": list(row.exports),
        "imports": [pe_import_dll_to_json(item) for item in row.imports],
        "error": row.error,
    }


def _dedupe_preserve_order(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return tuple(rows)


def _interesting_hc_strings(path: Path) -> dict[str, tuple[str, ...]]:
    data = path.read_bytes()
    strings = [match.group().decode("ascii", errors="replace") for match in ASCII_STRING_RE.finditer(data)]
    all_text = "\n".join(strings)
    dic_tokens = _dedupe_preserve_order(re.findall(r"\bDIC[0-9A-Fa-f]{4}\w*\b", all_text))
    vlpljbl_tokens = _dedupe_preserve_order(re.findall(r"\bvlpljbl[A-Za-z.]*\b", all_text))
    html_templates = _dedupe_preserve_order(
        [
            value
            for value in strings
            if "%s" in value and any(marker in value for marker in ("HTMLs", "body", "fix", "Media"))
        ]
    )
    sql_snippets = _dedupe_preserve_order(
        [
            value
            for value in strings
            if value.upper().startswith("SELECT ")
            and not any(
                sqlite_internal in value
                for sqlite_internal in (
                    "sqlite_master",
                    "sqlite_temp_master",
                    "vacuum_db",
                    "sqlite_stat1",
                    "sqlite_sequence",
                )
            )
        ]
    )
    image_templates = _dedupe_preserve_order(
        [value for value in strings if "<img " in value.lower() or value.lower().startswith("<img")]
    )
    return {
        "dic_tokens": dic_tokens,
        "vlpljbl_tokens": vlpljbl_tokens,
        "html_templates": html_templates,
        "sql_snippets": sql_snippets,
        "image_templates": image_templates,
    }


def _imported_function_names(pe: PeSummary, dll_name: str) -> set[str]:
    wanted = dll_name.lower()
    rows: set[str] = set()
    for imported in pe.imports:
        if imported.dll.lower() != wanted:
            continue
        for function in imported.functions:
            if function.name:
                rows.add(function.name)
            elif function.ordinal is not None:
                rows.add(f"#{function.ordinal}")
    return rows


DICTIONARY_BRIDGE_DLL = "SS" "Dic" "Lib.dll"


def _hc_features(pe: PeSummary, sidecar_strings: dict[str, tuple[str, ...]]) -> dict[str, bool]:
    exports = set(pe.exports)
    bridge_imports = _imported_function_names(pe, DICTIONARY_BRIDGE_DLL)
    return {
        "html_body_renderer": "epwing2HtmlBodydata" in exports,
        "vertical_renderer": "epwing2HtmlBodydataVertical" in exports,
        "lvelib_renderer": "epwing2HtmlBodydataLVELib" in exports,
        "custom_gaiji_dib": "getCustomCharacterDIB" in exports,
        "headword_modifier": any(name.startswith("modifyHeadword") for name in exports),
        "panel_hooks": bool({"initializePanel", "finalizePanel"} & exports),
        "sql_hooks": bool({"initializeSQL", "finalizeSQL"} & exports)
        or bool({"SDicSQLSearchAndHtml", "SDicSQLSearchAndHtmlEx", "SDicExecSQLSearch"} & bridge_imports),
        "plugin_hooks": any(name.startswith("pluginFunction") for name in exports),
        "user_data_hooks": bool({"openUserData", "closeUserData"} & exports),
        "dictionary_original_search": any(name.startswith("execDicOrgSearch") for name in exports),
        "fulltext_search": "execDicZenbunSearch" in exports,
        "zip_media_export": "createMediaFileFromZip" in exports,
        "uses_body_api": "SDicGetBodyData" in bridge_imports,
        "uses_picture_api": "SDicGetPictureData" in bridge_imports,
        "uses_gaiji_unicode_api": "SDicGetCustomCharacterUincode" in bridge_imports,
        "uses_gaiji_bitmap_api": "SDicGetCustomCharacterBitmap" in bridge_imports,
        "uses_menu_api": "SDicGetMenuData" in bridge_imports,
        "mentions_vlpljbl": bool(sidecar_strings["vlpljbl_tokens"]),
    }


def hc_renderer_effects(row: HcRendererClassification) -> dict[str, Any]:
    """Return renderer behavior inferred from exports/imports and decoded code.

    The HC binaries all expose small C entrypoints and call the dictionary
    bridge DLL for body bytes, gaiji, pictures, menus, and SQL search.  This is
    not a file inventory; it is the app-facing behavior the renderer can
    perform when those entrypoints/imports are present.
    """

    exports = set(row.pe.exports)
    bridge_imports = _imported_function_names(row.pe, DICTIONARY_BRIDGE_DLL)
    body_renderer = "epwing2HtmlBodydata" in exports
    vertical_renderer = "epwing2HtmlBodydataVertical" in exports
    lvelib_renderer = "epwing2HtmlBodydataLVELib" in exports
    uses_picture = "SDicGetPictureData" in bridge_imports
    uses_body = "SDicGetBodyData" in bridge_imports
    uses_unicode_gaiji = "SDicGetCustomCharacterUincode" in bridge_imports
    uses_bitmap_gaiji = "SDicGetCustomCharacterBitmap" in bridge_imports
    return {
        "entrypoints": sorted(exports),
        "body_renderer": body_renderer,
        "vertical_renderer": vertical_renderer,
        "lvelib_renderer": lvelib_renderer,
        "body_source": "SDicGetBodyData" if uses_body else None,
        "gaiji_strategy": {
            "unicode_first": uses_unicode_gaiji,
            "bitmap_fallback": uses_bitmap_gaiji,
            "bitmap_api": "SDicGetCustomCharacterBitmap" if uses_bitmap_gaiji else None,
        },
        "picture_strategy": {
            "bridge_api": "SDicGetPictureData" if uses_picture else None,
            "inline_picture_controls": ["1f3c", "1f4d"] + (["1f44"] if uses_picture else []),
            "output_uri_shape": "lved.imag / generated image paths" if uses_picture else None,
        },
        "audio_strategy": {
            "sound_range_controls": ["1f4a", "1f6a"] if body_renderer else [],
            "output_uri_shape": "lved.sond" if body_renderer else None,
        },
        "link_strategy": {
            "address_controls": ["1f42/1f62", "1f43/1f63"] if body_renderer else [],
            "extended_image_link_controls": ["1f44/1f64"] if uses_picture else [],
            "output_uri_shape": "lved.addr" if body_renderer else None,
        },
        "layout_strategy": {
            "section_indent_control": "1f09",
            "body_break_control": "1f0a",
            "heading_control": "1f41",
            "private_renderer_directives": ["1fe0", "1fe1", "1fe2", "1fe4", "1fe6"] if body_renderer else [],
        },
        "search_strategy": {
            "dictionary_original_search": any(name.startswith("execDicOrgSearch") for name in exports),
            "fulltext_search": "execDicZenbunSearch" in exports,
            "sql_search": bool({"initializeSQL", "finalizeSQL"} & exports)
            or bool({"SDicSQLSearchAndHtml", "SDicSQLSearchAndHtmlEx", "SDicExecSQLSearch"} & bridge_imports),
        },
        "panel_strategy": {
            "panel_hooks": bool({"initializePanel", "finalizePanel"} & exports),
            "menu_api": "SDicGetMenuData" if "SDicGetMenuData" in bridge_imports else None,
        },
    }


def discover_hc_renderer_files(roots: list[Path]) -> list[Path]:
    if not roots:
        roots = [Path(".")]
    rows: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for current_root, _dirs, filenames in os.walk(root):
                for filename in filenames:
                    if HC_RENDERER_RE.fullmatch(filename):
                        candidates.append(Path(current_root) / filename)
        for candidate in sorted(candidates):
            if not is_hc_renderer_path(candidate):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rows.append(resolved)
    return rows


def classify_hc_renderer_file(path: Path, *, compute_hash: bool = True) -> HcRendererClassification:
    code = hc_code_from_name(path)
    if code is None:
        raise ValueError(f"not an HC renderer filename: {path}")
    expected_numeric_index = expected_numeric_index_for_hc_code(code)
    exinfo = exinfo_path_for_idx(path.parent / f"{path.parent.name.removeprefix('_DCT_')}.IDX")
    parsed_exinfo = parse_exinfo(exinfo) if exinfo is not None else None
    html_dll = parsed_exinfo.general.get("HTMLDLL") if parsed_exinfo is not None else None
    # The sidecar rule here is simply sibling eight-hex-digit .idx files. Some
    # packages declare them in EXINFO, some leave them unreferenced, and some HC
    # renderers have no numeric sidecar at all.
    numeric_indexes = tuple(
        child.name
        for child in sorted(path.parent.iterdir())
        if child.is_file() and NUMERIC_AUX_INDEX_RE.fullmatch(child.name)
    )
    vlpljbl_siblings = tuple(
        child.name for child in sorted(path.parent.iterdir()) if child.is_file() and is_vlpljbl_path(child)
    )
    pe = parse_pe_summary(path)
    strings = _interesting_hc_strings(path)
    return HcRendererClassification(
        path=path.resolve(),
        code=code,
        expected_numeric_index=expected_numeric_index,
        size=path.stat().st_size,
        sha256=sha256_file(path) if compute_hash else None,
        pe=pe,
        exinfo_html_dll=html_dll,
        exinfo_declares_this=(html_dll.lower() == path.name.lower()) if html_dll is not None else None,
        numeric_indexes=numeric_indexes,
        expected_numeric_index_present=expected_numeric_index.lower() in {name.lower() for name in numeric_indexes},
        vlpljbl_siblings=vlpljbl_siblings,
        dic_tokens=strings["dic_tokens"],
        vlpljbl_tokens=strings["vlpljbl_tokens"],
        html_templates=strings["html_templates"],
        sql_snippets=strings["sql_snippets"],
        image_templates=strings["image_templates"],
        features=_hc_features(pe, strings),
    )


def hc_renderer_classification_to_json(row: HcRendererClassification) -> dict[str, Any]:
    return {
        "path": str(row.path),
        "dict_dir": row.path.parent.name,
        "name": row.path.name,
        "code": row.code,
        "expected_numeric_index": row.expected_numeric_index,
        "size": row.size,
        "sha256": row.sha256,
        "pe": pe_summary_to_json(row.pe),
        "exports": list(row.pe.exports),
        "dictionary_bridge_imports": sorted(_imported_function_names(row.pe, DICTIONARY_BRIDGE_DLL)),
        "exinfo_html_dll": row.exinfo_html_dll,
        "exinfo_declares_this": row.exinfo_declares_this,
        "numeric_indexes": list(row.numeric_indexes),
        "expected_numeric_index_present": row.expected_numeric_index_present,
        "vlpljbl_siblings": list(row.vlpljbl_siblings),
        "dic_tokens": list(row.dic_tokens),
        "vlpljbl_tokens": list(row.vlpljbl_tokens),
        "html_templates": list(row.html_templates),
        "sql_snippets": list(row.sql_snippets),
        "image_templates": list(row.image_templates),
        "features": row.features,
        "renderer_effects": hc_renderer_effects(row),
    }


def sqlite_table_summaries(db_path: Path) -> tuple[dict[str, Any], ...]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows: list[dict[str, Any]] = []
        for name, sql in con.execute("select name, sql from sqlite_master where type='table' order by name"):
            columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(name)})")]
            try:
                count = int(con.execute(f"select count(*) from {quote_identifier(name)}").fetchone()[0])
            except sqlite3.Error:
                count = None
            rows.append({"name": name, "rows": count, "columns": columns, "sql": sql})
        return tuple(rows)
    finally:
        con.close()


def sqlite_table_schema_summaries(db_path: Path) -> tuple[dict[str, Any], ...]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows: list[dict[str, Any]] = []
        for name, sql in con.execute("select name, sql from sqlite_master where type='table' order by name"):
            columns = [row[1] for row in con.execute(f"pragma table_info({quote_identifier(name)})")]
            rows.append({"name": name, "rows": None, "columns": columns, "sql": sql})
        return tuple(rows)
    finally:
        con.close()


def _column_sets(tables: tuple[dict[str, Any], ...]) -> dict[str, set[str]]:
    return {
        str(table["name"]).lower(): {str(column).lower() for column in table.get("columns", [])}
        for table in tables
    }


def _has_any(columns: set[str], *candidates: str) -> bool:
    return any(candidate.lower() in columns for candidate in candidates)


def _sqlite_table_role(name: str, columns: set[str]) -> str:
    """Infer a LogoVista SQLite table role from schema capabilities.

    Table names are still useful evidence, but they are not stable enough to be
    the primary dispatch mechanism. Prefer id/body/blob/address capabilities,
    then fall back to a few known names only when the schema is ambiguous.
    """

    lower = name.lower()
    if lower == "android_metadata":
        return "platform_metadata"
    if lower == "indexinfo":
        return "index_metadata"
    if lower == "info":
        return "metadata"
    if lower in {"bamen", "bamendetail", "koudou", "koudoudetail"}:
        return "metadata"
    if lower in {"katsuyou", "kisoku"}:
        return "conjugation"
    has_id = _has_any(
        columns,
        "id",
        "no",
        "itemid",
        "dataid",
        "contentid",
        "content_id",
        "contents_id",
        "row_id",
        "f_dataid",
        "f_data_id",
        "f_contents_id",
        "f_order_id",
        "index",
    )
    has_title = _has_any(
        columns,
        "title",
        "heading",
        "headword",
        "label",
        "title_utf8",
        "title_sjis",
        "f_title",
        "f_midasi",
        "f_midashi",
        "f_midashi_hyoki",
        "keyword",
        "midashi",
        "midashij",
        "japanese",
        "c_text",
        "k_text",
        "j_text",
        "f_katakana",
        "f_std_name",
        "f_2nd_name",
        "f_kan_name",
    )
    has_body = _has_any(
        columns,
        "body",
        "text",
        "plain",
        "body_text",
        "body_html",
        "html_body",
        "content_html",
        "plain_text",
        "contents_html_box",
        "contents_html_list",
        "f_html",
        "f_html_text",
        "f_contents",
        "f_body",
        "f_plane",
        "f_plane_text",
        "h_text",
        "c_text",
        "k_text",
        "j_text",
    )
    has_blob = _has_any(columns, "blob", "payload", "payload_blob", "resource_blob", "image_blob", "media_blob", "f_blob", "f_main", "main")
    has_name = _has_any(columns, "name", "filename", "file_name", "path", "asset_name", "resource_name", "f_name")
    has_address = _has_any(columns, "block", "block_s", "f_block") and _has_any(columns, "offset", "offset_s", "f_offset")
    has_search_type = _has_any(columns, "f_type", "type", "search_type", "category")
    if not columns:
        return "empty"
    if columns == {"index", "data"}:
        return "ancillary"
    if "chronology" in lower:
        return "ancillary"
    if lower in {"t_all", "t_bushu", "t_jukugo", "t_yomi", "t_exam"}:
        return "kanji_support"
    if lower in {"d_example", "d_idiom"}:
        return "examples_idioms"
    if lower in {"d_goyo", "d_keigo", "d_kininaru", "d_english"}:
        return "supplemental"
    if has_blob and (has_name or has_id):
        return "media_store"
    if columns == {"html"}:
        return "body"
    if has_id and has_body and not has_address:
        return "body"
    if has_address and has_body:
        return "block_offset_body"
    if has_address and has_search_type and has_title:
        return "search"
    if has_address and has_title:
        return "link_reference"
    if has_id and has_title:
        return "search"
    if lower.startswith("t_search_") or "zenbun" in lower or lower == "t_index":
        return "search"
    if lower.startswith("d_"):
        return "supplemental"
    return "unknown"


def sqlite_role_for_tables(tables: tuple[dict[str, Any], ...]) -> str:
    """Return an evidence-backed role for a LogoVista SQLite sidecar.

    The same schema families appear as encrypted Windows ``vlpljbl*`` payloads,
    plain Windows ``*.db`` sidecars, Android body databases, and portable SSED
    helper databases. This function therefore classifies by schema first and
    by platform marker tables only when they are part of the observed layout.
    """

    columns = _column_sets(tables)
    names_all = set(columns)
    names = {name for name in names_all if name not in {"android_metadata"}}
    if not names:
        return "sqlite_metadata_only" if names_all else "sqlite_empty"
    roles = {name: _sqlite_table_role(name, columns[name]) for name in names}
    role_values = set(roles.values())
    has_android_metadata = "android_metadata" in names_all
    if has_android_metadata and names == {"indexinfo"}:
        return "sqlite_android_index_metadata"
    if {"bamen", "koudou"} & names and any(name.startswith("data") for name in names):
        return "sqlite_template_navigation"
    if any(name.startswith("t_kyz_") for name in names):
        return "sqlite_category_search_index"
    if role_values <= {"media_store"}:
        return "sqlite_media_store"
    if "honbun" in names:
        honbun = columns["honbun"]
        if {"id", "title_utf8", "contents_html_box"} <= honbun:
            return "sqlite_row_ordered_honbun_renderer_body"
        if {"f_data_id", "f_honbun"} <= honbun or {"f_data_id", "f_contents"} <= honbun:
            return "sqlite_honbun_data_id_body"
        return "sqlite_honbun"
    if "block_offset_body" in role_values:
        return "sqlite_block_offset_body"
    if "body" in role_values:
        if "media_store" in role_values:
            return "sqlite_renderer_body_with_media"
        return "sqlite_renderer_body"
    if role_values <= {"search"} and any(name.startswith("t_search_") for name in names):
        return "sqlite_category_search_index"
    if role_values <= {"search"} and len(names) == 1:
        return "sqlite_search_index"
    if "t_contents" in names:
        cols = columns["t_contents"]
        body_columns = {
            "f_html",
            "f_plane",
            "f_contents",
            "f_media",
            "f_html_text",
            "f_plane_text",
            "f_body",
        }
        if cols & body_columns:
            if {"media", "t_media"} & names:
                return "sqlite_renderer_body_with_media"
            return "sqlite_renderer_body"
    if "examples_idioms" in role_values:
        return "sqlite_examples_idioms"
    if role_values <= {"supplemental", "examples_idioms"}:
        return "sqlite_supplemental"
    if "kanji_support" in role_values:
        return "sqlite_kanji_support"
    if "conjugation" in role_values:
        return "sqlite_search_or_conjugation"
    if role_values <= {"ancillary", "metadata", "platform_metadata", "index_metadata"}:
        return "sqlite_ancillary"
    if any({"block", "offset", "title"} <= columns[name] for name in names):
        return "sqlite_block_offset_title_index"
    if "link_reference" in role_values:
        return "sqlite_link_reference"
    if "search" in role_values or {"t_search", "t_zenbun"} & names:
        return "sqlite_search_or_fulltext"
    return "sqlite_unclassified"


RENDERER_BODY_SQLITE_ROLES = {
    "sqlite_renderer_body",
    "sqlite_renderer_body_with_media",
    "sqlite_row_ordered_honbun_renderer_body",
    "sqlite_honbun_data_id_body",
    "sqlite_block_offset_body",
}


def vlpljbl_role_for_content(content_kind: str, suffix: str, tables: tuple[dict[str, Any], ...]) -> str:
    if suffix in {".bin", ".exe"} and content_kind == "pe_executable":
        return "logofont_decryptor_binary"
    if content_kind in {"opentype_cff", "opentype_ttf"}:
        return "font"
    if content_kind == "sqlite":
        return sqlite_role_for_tables(tables)
    return content_kind


def classify_vlpljbl_file(path: Path, *, inspect_sqlite: bool = True, compute_hash: bool = True) -> VlpljblClassification:
    """Classify one ``vlpljbl*`` file by magic and optional SQLite schema."""

    prefix = read_file_prefix(path, 4096)
    suffix = vlpljbl_suffix(path)
    plain_kind = file_magic_kind(prefix)
    storage = "plain"
    content_kind = plain_kind
    decrypt_prefix_kind: str | None = None
    if plain_kind == "unknown" and path.stat().st_size % 16 == 0:
        try:
            decrypted_prefix = decrypt_logofont_cipher_prefix(prefix, size=4096)
            decrypt_prefix_kind = file_magic_kind(decrypted_prefix)
            if decrypt_prefix_kind != "unknown":
                storage = "logofont_cipher"
                content_kind = decrypt_prefix_kind
        except (LogoVistaCryptoError, LogoVistaCryptoUnavailable, ValueError) as exc:
            decrypt_prefix_kind = f"decrypt_error:{type(exc).__name__}"

    sqlite_tables: tuple[dict[str, Any], ...] = ()
    if inspect_sqlite and content_kind == "sqlite":
        if storage == "plain":
            sqlite_tables = sqlite_table_summaries(path)
        else:
            with tempfile.TemporaryDirectory(prefix="lv-vlpljbl-") as tmp:
                db_path = Path(tmp) / f"{path.name}.sqlite"
                decrypt_logofont_cipher_file_to_path(path, db_path)
                sqlite_tables = sqlite_table_summaries(db_path)

    return VlpljblClassification(
        path=path.resolve(),
        suffix=suffix,
        size=path.stat().st_size,
        sha256=sha256_file(path) if compute_hash else None,
        storage=storage,
        content_kind=content_kind,
        decrypt_prefix_kind=decrypt_prefix_kind,
        role=vlpljbl_role_for_content(content_kind, suffix, sqlite_tables),
        sqlite_tables=sqlite_tables,
    )


def vlpljbl_classification_to_json(row: VlpljblClassification) -> dict[str, Any]:
    return {
        "path": str(row.path),
        "dict_dir": row.path.parent.name,
        "name": row.path.name,
        "suffix": row.suffix,
        "size": row.size,
        "sha256": row.sha256,
        "storage": row.storage,
        "content_kind": row.content_kind,
        "decrypt_prefix_kind": row.decrypt_prefix_kind,
        "role": row.role,
        "sqlite_tables": list(row.sqlite_tables),
    }


def discover_vlpljbl_files(roots: list[Path]) -> list[Path]:
    if not roots:
        roots = [Path(".")]
    rows: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("vlpljbl*"))
        for candidate in candidates:
            if not is_vlpljbl_path(candidate):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rows.append(resolved)
    return rows


def sqlite_has_supported_sidecar_schema(path: Path) -> bool:
    try:
        role = sqlite_role_for_tables(sqlite_table_schema_summaries(path))
    except sqlite3.Error:
        return False
    return role in RENDERER_BODY_SQLITE_ROLES


def _resolve_sidecar_reference(root: Path, name: str) -> Path | None:
    candidates = [
        root / name,
        root / "Templates" / name,
        root / "templates" / name,
        root / "resource" / name,
        root / "innerdata" / name,
        root / "manual" / name,
    ]
    for candidate in candidates:
        found = find_case_insensitive(candidate.parent, candidate.name)
        if found is not None and found.is_file():
            return found.resolve()
    return None


def discover_sqlite_sidecar_files(idx: Path, exinfo: Exinfo | None = None) -> list[Path]:
    """Return package-local SQLite-ish sidecars without assuming a platform.

    This is intentionally bounded. It checks direct package siblings, common
    resource/template directories, and explicit ``EXINFO.INI`` references such
    as ``SQLNAME`` / ``ROSQLNAME``. It does not recursively inventory arbitrary
    app trees.
    """

    root = idx.parent
    candidates: list[Path] = []
    if exinfo is not None:
        for key, value in exinfo.general.items():
            if key.upper() in {"SQLNAME", "ROSQLNAME"} or value.lower().endswith((".db", ".sqlite", ".sqlite3")):
                resolved = _resolve_sidecar_reference(root, value)
                if resolved is not None:
                    candidates.append(resolved)
    for directory in (root, root / "Templates", root / "templates", root / "resource", root / "innerdata", root / "manual"):
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.is_file() and child.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                candidates.append(child.resolve())
    rows: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        rows.append(candidate)
    return rows


def classify_sqlite_sidecar_file(path: Path) -> SqliteSidecarClassification:
    prefix = read_file_prefix(path, 64)
    storage = sqlite_storage_for_path(path)
    content_kind = file_magic_kind(prefix)
    if storage is None:
        return SqliteSidecarClassification(
            path=path.resolve(),
            storage=None,
            content_kind=content_kind,
            role="not_sqlite",
        )
    try:
        if storage == "plain":
            tables = sqlite_table_schema_summaries(path)
        else:
            with tempfile.TemporaryDirectory(prefix="lv-sqlite-sidecar-") as tmp:
                decrypted = Path(tmp) / f"{path.name}.sqlite"
                decrypt_logofont_cipher_file_to_path(path, decrypted)
                tables = sqlite_table_schema_summaries(decrypted)
        role = sqlite_role_for_tables(tables)
    except sqlite3.Error:
        tables = ()
        role = "sqlite_open_error"
    return SqliteSidecarClassification(
        path=path.resolve(),
        storage=storage,
        role=role,
        tables=tables,
    )


def discover_sqlite_sidecars(idx: Path, exinfo: Exinfo | None = None) -> list[SqliteSidecarClassification]:
    return [classify_sqlite_sidecar_file(path) for path in discover_sqlite_sidecar_files(idx, exinfo)]


def discover_renderer_sidecars(idx: Path, exinfo: Exinfo | None = None) -> list[RendererSidecar]:
    """Return body-capable files that are plain/encrypted SQLite payloads."""

    candidates: list[Path] = []
    if exinfo is not None:
        rosql = exinfo.general.get("ROSQLNAME")
        if rosql:
            candidate = idx.parent / rosql
            candidates.append(candidate)
    for child in sorted(idx.parent.iterdir()):
        lower = child.name.lower()
        if child.is_file() and lower.startswith("vlpljbl") and lower != "vlpljbl.bin":
            candidates.append(child)
    candidates.extend(discover_sqlite_sidecar_files(idx, exinfo))

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
        if storage == "plain" and not sqlite_has_supported_sidecar_schema(candidate):
            continue
        rows.append(RendererSidecar(path=candidate, storage=storage))
    return rows
