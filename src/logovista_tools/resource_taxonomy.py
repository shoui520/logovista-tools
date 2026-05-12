"""Corpus-scale file/resource taxonomy helpers.

The helpers in this module are deliberately file-first.  They classify package
side files, panel assets, and resource naming relationships from structural
evidence without decoding or emitting private dictionary text.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from .parallel import parallel_map_ordered
from .ssed import find_case_insensitive, parse_ssedinfo


FILE_REF_EXTENSIONS = {
    ".bin",
    ".bmp",
    ".css",
    ".dic",
    ".dll",
    ".dtd",
    ".gif",
    ".ha",
    ".htm",
    ".html",
    ".idx",
    ".ini",
    ".jpg",
    ".jpeg",
    ".js",
    ".mp3",
    ".png",
    ".sqlite",
    ".uni",
    ".wav",
    ".xml",
}
PANEL_NAME_RE = re.compile(r"^panels?$", re.IGNORECASE)
EIGHT_HEX_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
FILE_TOKEN_RE = re.compile(
    r"(?i)(?:[A-Za-z0-9_. -]+/)*[A-Za-z0-9_. -]+\."
    r"(?:bin|bmp|css|dic|dll|dtd|gif|ha|html?|idx|ini|jpe?g|js|mp3|png|uni|wav|xml)"
)
ADDRESS_TOKEN_RE = re.compile(r"(?i)\b(?:block|offset|body|title|pointer|addr(?:ess)?)\b")


@dataclass(frozen=True)
class CaseLookupResult:
    """Result for one case-insensitive lookup."""

    requested: str
    matched: str | None
    status: str
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "matched": self.matched,
            "status": self.status,
            "candidates": list(self.candidates),
        }


class PanelHTMLReferenceParser(HTMLParser):
    """Collect only structural HTML references, not text content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.refs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag.lower()] += 1
        for key, value in attrs:
            if not value:
                continue
            if looks_like_file_reference(value) or value.startswith(("#", "http:", "https:", "javascript:")):
                self.refs.append({"kind": "html_attr", "tag": tag.lower(), "attribute": key.lower(), "value": value})


def safe_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def byte_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def read_sample(path: Path, limit: int = 65536) -> bytes:
    with path.open("rb") as fh:
        return fh.read(limit)


def decode_text_sample(data: bytes) -> tuple[str | None, str]:
    encodings = ["utf-8-sig", "cp932", "shift_jis"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    else:
        encodings.append("utf-16")
    encodings.append("latin-1")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "latin-1":
            printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
            if data and printable / len(data) < 0.75:
                continue
        return encoding, text
    return None, ""


def magic_kind(data: bytes, path: Path) -> str:
    if data.startswith(b"SSEDINFO"):
        return "ssedinfo"
    if data.startswith(b"SSEDDATA"):
        return "sseddata"
    if data.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith(b"ID3"):
        return "mp3"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wave"
    suffix = path.suffix.lower()
    if suffix in {".xml", ".dtd", ".html", ".htm", ".ini", ".uni"}:
        encoding, text = decode_text_sample(data)
        if encoding and text.strip():
            return suffix[1:] or "text"
    encoding, text = decode_text_sample(data)
    if encoding and text.strip():
        return "text"
    return "binary"


def fingerprint_file(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    data = read_sample(path)
    stat = path.stat()
    encoding, text = decode_text_sample(data)
    kind = magic_kind(data, path)
    refs = structural_references_from_text(path, text) if text else []
    return {
        "path": safe_rel(path, root or path.parent),
        "name": path.name,
        "suffix": path.suffix,
        "size": stat.st_size,
        "sha256": sha256_file(path),
        "magic": data[:16].hex(),
        "kind": kind,
        "text_encoding": encoding if kind != "binary" else None,
        "entropy_sample": round(byte_entropy(data), 4),
        "structural_reference_count": len(refs),
        "structural_references": refs[:100],
    }


def looks_like_file_reference(value: str) -> bool:
    cleaned = value.strip().strip("\"'")
    if not cleaned:
        return False
    if FILE_TOKEN_RE.search(cleaned):
        return True
    suffix = Path(cleaned.replace("\\", "/")).suffix.lower()
    if suffix in FILE_REF_EXTENSIONS:
        return True
    return bool(EIGHT_HEX_RE.fullmatch(Path(cleaned).stem))


def structural_references_from_text(path: Path, text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        parser = PanelHTMLReferenceParser()
        try:
            parser.feed(text)
        except Exception:
            pass
        refs.extend(parser.refs)
        refs.append({"kind": "html_tags", "counts": dict(sorted(parser.tags.items()))})
    elif suffix == ".xml":
        refs.extend(xml_structural_references(text))
    elif suffix == ".dtd":
        refs.extend(dtd_structural_references(text))
    else:
        for match in FILE_TOKEN_RE.finditer(text):
            refs.append({"kind": "file_token", "value": match.group(0)})
        if ADDRESS_TOKEN_RE.search(text):
            refs.append({"kind": "address_words_present"})
    return dedupe_reference_rows(refs)


def xml_structural_references(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        for match in FILE_TOKEN_RE.finditer(text):
            refs.append({"kind": "xml_file_token", "value": match.group(0)})
        return refs

    tags: Counter[str] = Counter()
    attrs: Counter[str] = Counter()
    for elem in root.iter():
        tag = str(elem.tag).split("}", 1)[-1]
        tags[tag] += 1
        for key, value in elem.attrib.items():
            attr = str(key).split("}", 1)[-1]
            attrs[attr] += 1
            if looks_like_file_reference(value) or ADDRESS_TOKEN_RE.search(attr):
                refs.append({"kind": "xml_attr", "tag": tag, "attribute": attr, "value": value})
    refs.append({"kind": "xml_tags", "counts": dict(sorted(tags.items()))})
    refs.append({"kind": "xml_attributes", "counts": dict(sorted(attrs.items()))})
    return refs


def dtd_structural_references(text: str) -> list[dict[str, Any]]:
    elements = sorted(set(re.findall(r"<!ELEMENT\s+([A-Za-z0-9_.:-]+)", text)))
    entities = sorted(set(re.findall(r"<!ENTITY\s+([A-Za-z0-9_.:-]+)", text)))
    refs: list[dict[str, Any]] = [
        {"kind": "dtd_elements", "values": elements},
        {"kind": "dtd_entities", "values": entities},
    ]
    for match in FILE_TOKEN_RE.finditer(text):
        refs.append({"kind": "dtd_file_token", "value": match.group(0)})
    return refs


def dedupe_reference_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def find_case_insensitive_matches(directory: Path, name: str) -> list[Path]:
    if not directory.is_dir():
        return []
    folded = name.casefold()
    return sorted((child for child in directory.iterdir() if child.name.casefold() == folded), key=lambda p: p.name)


def resolve_case_insensitive(directory: Path, name: str) -> CaseLookupResult:
    direct = directory / name
    if direct.exists():
        return CaseLookupResult(requested=name, matched=direct.name, status="exact")
    matches = find_case_insensitive_matches(directory, name)
    if len(matches) == 1:
        return CaseLookupResult(requested=name, matched=matches[0].name, status="case_insensitive")
    if len(matches) > 1:
        return CaseLookupResult(
            requested=name,
            matched=None,
            status="ambiguous",
            candidates=tuple(match.name for match in matches),
        )
    return CaseLookupResult(requested=name, matched=None, status="missing")


def case_collision_rows(package: Path) -> list[dict[str, Any]]:
    by_parent: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        parent = safe_rel(path.parent, package)
        by_parent[(parent, path.name.casefold())].append(path)
    rows: list[dict[str, Any]] = []
    for (parent, folded), paths in sorted(by_parent.items()):
        names = sorted(path.name for path in paths)
        if len(names) > 1:
            rows.append({"package": package.name, "parent": parent, "casefold_name": folded, "files": names})
    return rows


def parse_ini(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    encoding, text = decode_text_sample(raw)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        return {"path": path.name, "encoding": encoding, "parse_error": str(exc), "sections": {}}
    sections = {section: dict(parser[section]) for section in parser.sections()}
    return {"path": path.name, "encoding": encoding, "sections": sections}


def ini_reference_rows(path: Path, package: Path) -> list[dict[str, Any]]:
    parsed = parse_ini(path)
    rows: list[dict[str, Any]] = []
    for section, values in parsed.get("sections", {}).items():
        for key, value in values.items():
            parts = split_reference_value(value)
            for ref in parts:
                if not looks_like_file_reference(ref):
                    continue
                lookup = resolve_case_insensitive(package, ref)
                rows.append(
                    {
                        "package": package.name,
                        "ini": safe_rel(path, package),
                        "section": section,
                        "key": key,
                        "reference": ref,
                        "lookup": lookup.to_dict(),
                    }
                )
    return rows


def split_reference_value(value: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"[;,]", value):
        piece = piece.strip().strip("\"'")
        if piece:
            parts.append(piece)
    return parts


def package_idx_paths(package: Path) -> list[Path]:
    rows: list[Path] = []
    for pattern in ("*.IDX", "*.idx"):
        rows.extend(package.glob(pattern))
    return sorted(set(rows), key=lambda p: p.name.casefold())


def catalog_reference_rows(package: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in package_idx_paths(package):
        try:
            title, elements = parse_ssedinfo(idx)
        except Exception:
            continue
        for element in elements:
            if not element.filename:
                continue
            lookup = resolve_case_insensitive(package, element.filename)
            if lookup.status != "exact":
                rows.append(
                    {
                        "package": package.name,
                        "idx": idx.name,
                        "catalog_name": element.filename,
                        "component_type": f"0x{element.type:02x}",
                        "lookup": lookup.to_dict(),
                    }
                )
    return rows


def discover_package_dirs(roots: list[Path]) -> list[Path]:
    packages: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root.is_file():
            packages.add(root.parent)
            continue
        root_is_package = root_has_package_markers(root)
        if root_is_package:
            packages.add(root)
        else:
            for child in root.iterdir():
                if child.is_dir():
                    packages.add(child)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.casefold()
            if name.endswith(".idx") or name in {"dicprof.ini", "exinfo.ini"}:
                packages.add(path.parent)
            elif any(PANEL_NAME_RE.fullmatch(part) for part in path.relative_to(root).parts[:-1]):
                package = package_root_for_panel_asset(path, root)
                packages.add(package)
    return sorted(packages, key=lambda p: p.as_posix().casefold())


def root_has_package_markers(root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        children = list(root.iterdir())
    except OSError:
        return False
    return any(
        child.is_file() and (child.name.casefold().endswith(".idx") or child.name.casefold() in {"dicprof.ini", "exinfo.ini"})
        for child in children
    ) or any(child.is_dir() and PANEL_NAME_RE.fullmatch(child.name) for child in children)


def package_root_for_panel_asset(path: Path, corpus_root: Path) -> Path:
    rel_parts = path.relative_to(corpus_root).parts
    for index, part in enumerate(rel_parts[:-1]):
        if PANEL_NAME_RE.fullmatch(part):
            return corpus_root.joinpath(*rel_parts[:index]) if index else corpus_root
    return path.parent


def panel_files(package: Path) -> list[Path]:
    rows: list[Path] = []
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        parts = [part.casefold() for part in path.relative_to(package).parts]
        if any(PANEL_NAME_RE.fullmatch(part) for part in parts):
            rows.append(path)
        elif path.name.casefold() in {"panels.xml", "panel.xml"}:
            rows.append(path)
    return sorted(rows, key=lambda p: safe_rel(p, package).casefold())


def classify_panel_role(files: list[dict[str, Any]], refs: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = Counter(row["kind"] for row in files)
    ref_values = [str(row.get("value", "")) for row in refs if "value" in row]
    has_bin = any(row["name"].lower().endswith(".bin") for row in files)
    has_address_words = any(row.get("kind") == "address_words_present" for row in refs)
    has_entry_refs = any("block" in value.casefold() or "offset" in value.casefold() for value in ref_values) or has_address_words
    has_media_refs = any(Path(value).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".mp3", ".wav"} for value in ref_values)
    if has_entry_refs:
        role = "navigation"
        impact = "supplemental_reader_navigation"
        confidence = "medium"
    elif has_media_refs or has_bin:
        role = "panel_ui"
        impact = "optional_viewer_panel_or_resource"
        confidence = "medium"
    else:
        role = "panel_ui"
        impact = "optional_viewer_panel"
        confidence = "low"
    return {
        "role": role,
        "reader_impact": impact,
        "confidence": confidence,
        "file_kind_counts": dict(kinds),
        "evidence": {
            "has_bin": has_bin,
            "has_address_words": has_address_words,
            "has_media_refs": has_media_refs,
            "reference_count": len(refs),
        },
    }


def analyze_panel_package(package: Path, corpus_root: Path) -> dict[str, Any] | None:
    files = panel_files(package)
    if not files:
        return None
    file_rows = [fingerprint_file(path, root=package) for path in files]
    refs: list[dict[str, Any]] = []
    for row in file_rows:
        for ref in row.get("structural_references", []):
            if "value" in ref and not looks_like_file_reference(str(ref["value"])):
                continue
            refs.append({"package": package.name, "file": row["path"], **ref})
    return {
        "package": package.name,
        "package_path": safe_rel(package, corpus_root),
        "file_count": len(files),
        "files": file_rows,
        "classification": classify_panel_role(file_rows, refs),
        "references": dedupe_reference_rows(refs),
    }


def printable_ascii_tokens(data: bytes, min_length: int = 5) -> list[str]:
    # Returned values are filtered to structural/file-like tokens only.
    tokens = [match.group(0).decode("ascii", errors="ignore") for match in re.finditer(rb"[\x20-\x7e]{5,}", data)]
    return sorted({token for token in tokens if looks_like_file_reference(token)})[:100]


def analyze_bin_file(path: Path, package: Path) -> dict[str, Any]:
    data = read_sample(path, limit=1024 * 1024)
    header_words_le = [
        int.from_bytes(data[offset : offset + 4], "little")
        for offset in range(0, min(len(data), 32), 4)
        if len(data[offset : offset + 4]) == 4
    ]
    control_count = data.count(b"\x1f")
    signatures: list[dict[str, Any]] = []
    for label, magic in {
        "png": b"\x89PNG\r\n\x1a\n",
        "jpeg": b"\xff\xd8",
        "gif": b"GIF8",
        "bmp": b"BM",
        "sqlite": b"SQLite format 3\x00",
        "zip": b"PK\x03\x04",
        "zlib": b"\x78\x9c",
    }.items():
        offsets: list[int] = []
        start = 0
        while True:
            pos = data.find(magic, start)
            if pos < 0:
                break
            offsets.append(pos)
            start = pos + 1
            if len(offsets) >= 20:
                break
        if offsets:
            signatures.append({"kind": label, "offsets": offsets})
    return {
        "package": package.name,
        "path": safe_rel(path, package),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "magic": data[:32].hex(),
        "header_words_le": header_words_le,
        "entropy_sample": round(byte_entropy(data[:65536]), 4),
        "nul_count_sample": data[:65536].count(0),
        "control_1f_count_sample": control_count,
        "structural_string_refs": printable_ascii_tokens(data),
        "embedded_signatures": signatures,
        "classification": classify_bin_from_signatures(signatures, path, control_count),
    }


def classify_bin_from_signatures(signatures: list[dict[str, Any]], path: Path, control_count: int = 0) -> dict[str, Any]:
    kinds = {sig["kind"] for sig in signatures}
    if kinds & {"png", "jpeg", "gif", "bmp"}:
        return {"role": "media_resource", "reader_impact": "panel_or_renderer_resource", "confidence": "medium"}
    if "sqlite" in kinds:
        return {"role": "sidecar_database", "reader_impact": "unknown_until_schema_inspected", "confidence": "medium"}
    if kinds & {"zip", "zlib"}:
        return {"role": "compressed_resource", "reader_impact": "unknown", "confidence": "low"}
    if control_count:
        return {
            "role": "panel_text_payload",
            "reader_impact": "optional_panel_content_or_navigation_text",
            "confidence": "medium",
        }
    return {"role": "unknown_panel_binary", "reader_impact": "unknown_or_panel_ui", "confidence": "low"}


def analyze_ccaltstr(path: Path, package: Path) -> dict[str, Any]:
    fp = fingerprint_file(path, root=package)
    data = read_sample(path, limit=1024 * 1024)
    return {
        "package": package.name,
        "path": safe_rel(path, package),
        "fingerprint": fp,
        "structural_string_ref_count": len(printable_ascii_tokens(data)),
        "zero_delimited_token_count_sample": len([part for part in data[:65536].split(b"\x00") if len(part) >= 4]),
        "role": "unknown_alt_string_or_renderer_helper",
        "reader_impact": "unknown",
        "confidence": "low",
    }


def classify_dicprof_key(key: str, value: str) -> str:
    key_cf = key.casefold()
    if looks_like_file_reference(value):
        return "file_reference"
    if key_cf in {"dictid", "dicid", "dicdir"}:
        return "dictionary_id"
    if "idx" in key_cf:
        return "idx_reference_or_setting"
    if "gaiji" in key_cf or value.lower().endswith(".uni"):
        return "gaiji_reference"
    if "panel" in key_cf:
        return "panel_setting"
    if "html" in key_cf or "template" in key_cf:
        return "renderer_setting"
    if EIGHT_HEX_RE.fullmatch(value.strip()):
        return "resource_id"
    return "unknown"


def dicprof_report_for_package(package: Path) -> dict[str, Any] | None:
    path = find_case_insensitive(package, "DICPROF.INI")
    if path is None:
        return None
    parsed = parse_ini(path)
    key_rows: list[dict[str, Any]] = []
    for section, values in parsed.get("sections", {}).items():
        for key, value in values.items():
            key_rows.append({"section": section, "key": key, "value_class": classify_dicprof_key(key, value)})
    return {
        "package": package.name,
        "path": safe_rel(path, package),
        "encoding": parsed.get("encoding"),
        "parse_error": parsed.get("parse_error"),
        "section_count": len(parsed.get("sections", {})),
        "keys": key_rows,
        "references": ini_reference_rows(path, package),
    }


def idx_uni_associations_for_package(package: Path) -> dict[str, Any]:
    idx_files = sorted(package.glob("*.idx")) + sorted(package.glob("*.IDX"))
    uni_files = sorted(package.glob("*.uni")) + sorted(package.glob("*.UNI"))
    idx_files = sorted(set(idx_files), key=lambda p: p.name.casefold())
    uni_files = sorted(set(uni_files), key=lambda p: p.name.casefold())
    ini_refs: list[dict[str, Any]] = []
    for name in ("EXINFO.INI", "DICPROF.INI"):
        path = find_case_insensitive(package, name)
        if path is not None:
            ini_refs.extend(ini_reference_rows(path, package))
    refs_by_name = Counter(Path(row["reference"]).name.casefold() for row in ini_refs)
    dicprof_basenames = dicprof_declared_basenames(package)

    files: list[dict[str, Any]] = []
    for path in idx_files + uni_files:
        stem = path.stem
        files.append(
            {
                "name": path.name,
                "extension": path.suffix,
                "eight_hex_stem": bool(EIGHT_HEX_RE.fullmatch(stem)),
                "declared_reference_count": refs_by_name[path.name.casefold()],
                "association": association_for_resource_name(
                    package,
                    path,
                    refs_by_name[path.name.casefold()],
                    dicprof_basenames,
                ),
            }
        )
    return {"package": package.name, "files": files, "ini_references": ini_refs}


def dicprof_declared_basenames(package: Path) -> set[str]:
    path = find_case_insensitive(package, "DICPROF.INI")
    if path is None:
        return set()
    parsed = parse_ini(path)
    names: set[str] = set()
    for values in parsed.get("sections", {}).values():
        for key, value in values.items():
            if classify_dicprof_key(key, value) != "dictionary_id":
                continue
            clean = value.strip().strip("\"'")
            if re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", clean):
                names.add(Path(clean).stem.casefold())
    return names


def association_for_resource_name(
    package: Path,
    path: Path,
    declared_count: int,
    dicprof_basenames: set[str] | None = None,
) -> dict[str, str]:
    dicprof_basenames = dicprof_basenames or set()
    if declared_count:
        return {"rule": "declared_reference", "confidence": "high"}
    if path.stem.casefold() in dicprof_basenames:
        return {"rule": "dicprof_declared_basename", "confidence": "high"}
    if EIGHT_HEX_RE.fullmatch(path.stem):
        return {"rule": "eight_hex_resource_id", "confidence": "medium"}
    if path.stem.casefold() == package.name.casefold().removeprefix("_dct_"):
        return {"rule": "folder_name_basename", "confidence": "medium"}
    return {"rule": "sibling_resource", "confidence": "low"}


def analyze_figure_dic(package: Path) -> dict[str, Any] | None:
    path = find_case_insensitive(package, "FIGURE.DIC")
    if path is None:
        return None
    fp = fingerprint_file(path, root=package)
    refs: list[dict[str, Any]] = []
    for name in ("EXINFO.INI", "DICPROF.INI"):
        ini = find_case_insensitive(package, name)
        if ini:
            refs.extend(row for row in ini_reference_rows(ini, package) if Path(row["reference"]).name.casefold() == "figure.dic")
    role = "media_resource" if fp["kind"] in {"sseddata", "bmp", "jpeg", "png", "gif"} else "unknown_resource"
    return {
        "package": package.name,
        "path": safe_rel(path, package),
        "fingerprint": fp,
        "cross_references": refs,
        "role": role,
        "reader_impact": "unknown_until_referenced" if not refs else "resource_or_navigation_related",
        "confidence": "low" if not refs else "medium",
    }


def inspect_package(package: Path, corpus_root: Path) -> dict[str, Any]:
    panel = analyze_panel_package(package, corpus_root)
    bin_rows = [analyze_bin_file(path, package) for path in panel_files(package) if path.suffix.lower() == ".bin"]
    ccaltstr_rows = [analyze_ccaltstr(path, package) for path in package.rglob("*") if path.is_file() and path.name.casefold() == "ccaltstr.ha"]
    dicprof = dicprof_report_for_package(package)
    idx_uni = idx_uni_associations_for_package(package)
    figure = analyze_figure_dic(package)
    case_rows = case_collision_rows(package)
    case_rows.extend(catalog_reference_rows(package))
    for name in ("EXINFO.INI", "DICPROF.INI"):
        ini = find_case_insensitive(package, name)
        if ini:
            case_rows.extend(row for row in ini_reference_rows(ini, package) if row["lookup"]["status"] != "exact")
    return {
        "package": package.name,
        "package_path": safe_rel(package, corpus_root),
        "panel": panel,
        "panel_bins": bin_rows,
        "ccaltstr": ccaltstr_rows,
        "dicprof": dicprof,
        "idx_uni": idx_uni,
        "figure_dic": figure,
        "case_anomalies": case_rows,
    }


def _inspect_package_worker(item: tuple[str, str]) -> dict[str, Any]:
    package, root = item
    return inspect_package(Path(package), Path(root))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_taxonomy_report(roots: list[Path], out_dir: Path, *, jobs: int | None = 1) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_root = roots[0].resolve() if roots else Path(".").resolve()
    packages = discover_package_dirs(roots)
    rows = parallel_map_ordered(
        _inspect_package_worker,
        [(str(package), str(corpus_root)) for package in packages],
        jobs=jobs,
    )

    panel_rows = [row["panel"] for row in rows if row.get("panel")]
    panel_bin_rows = [bin_row for row in rows for bin_row in row.get("panel_bins", [])]
    panel_cross_refs = [ref for panel in panel_rows for ref in panel.get("references", [])]
    panel_roles = [
        {
            "package": panel["package"],
            "package_path": panel["package_path"],
            **panel["classification"],
        }
        for panel in panel_rows
    ]
    ccaltstr_rows = [cc for row in rows for cc in row.get("ccaltstr", [])]
    case_rows = [case for row in rows for case in row.get("case_anomalies", [])]
    dicprof_rows = [row["dicprof"] for row in rows if row.get("dicprof")]
    figure_rows = [row["figure_dic"] for row in rows if row.get("figure_dic")]
    idx_uni_rows = [row["idx_uni"] for row in rows if row.get("idx_uni")]

    write_json(out_dir / "panel-inventory.json", panel_rows)
    write_text(out_dir / "panel-inventory.md", panel_markdown(panel_rows))
    write_jsonl(out_dir / "panel-bin-analysis.jsonl", panel_bin_rows)
    write_jsonl(out_dir / "panel-cross-references.jsonl", panel_cross_refs)
    write_jsonl(out_dir / "panel-role-classification.jsonl", panel_roles)
    write_jsonl(out_dir / "case-anomalies.jsonl", case_rows)
    write_text(out_dir / "case-anomalies.md", case_anomalies_markdown(case_rows))
    write_text(out_dir / "ccaltstr-ha-report.md", ccaltstr_markdown(ccaltstr_rows))
    write_json(out_dir / "ccaltstr-ha-structures.json", ccaltstr_rows)
    write_jsonl(out_dir / "ccaltstr-ha-crossrefs.jsonl", [])
    write_json(out_dir / "idx-uni-associations.json", idx_uni_rows)
    write_text(out_dir / "idx-uni-associations.md", idx_uni_markdown(idx_uni_rows))
    write_jsonl(out_dir / "idx-uni-anomalies.jsonl", idx_uni_anomalies(idx_uni_rows))
    write_text(out_dir / "resource-association-rules.md", association_rules_markdown())
    write_text(out_dir / "figure-dic-report.md", figure_markdown(figure_rows))
    write_json(out_dir / "figure-dic-structure.json", figure_rows)
    write_jsonl(out_dir / "figure-dic-crossrefs.jsonl", [ref for row in figure_rows for ref in row.get("cross_references", [])])
    write_text(out_dir / "dicprof-report.md", dicprof_markdown(dicprof_rows))
    write_json(out_dir / "dicprof-keys.json", dicprof_key_summary(dicprof_rows))
    write_jsonl(out_dir / "dicprof-references.jsonl", [ref for row in dicprof_rows for ref in row.get("references", [])])
    write_text(out_dir / "dicprof-exinfo-comparison.md", dicprof_exinfo_markdown(rows))

    handoff = lvcore_handoff(panel_rows, ccaltstr_rows, idx_uni_rows, figure_rows, dicprof_rows, case_rows)
    write_json(out_dir / "lvcore-handoff.json", handoff)
    write_text(out_dir / "lvcore-handoff.md", lvcore_handoff_markdown(handoff))
    write_text(out_dir / "unknowns-and-next-evidence.md", unknowns_markdown(panel_roles, ccaltstr_rows, figure_rows))

    summary = {
        "schema": "logovista.resource_sidecar_taxonomy.v2",
        "packages_scanned": len(packages),
        "panel_package_count": len(panel_rows),
        "panel_file_count": sum(panel.get("file_count", 0) for panel in panel_rows),
        "panel_bin_count": len(panel_bin_rows),
        "case_anomaly_count": len(case_rows),
        "ccaltstr_ha_count": len(ccaltstr_rows),
        "dicprof_count": len(dicprof_rows),
        "idx_uni_package_count": len(idx_uni_rows),
        "figure_dic_count": len(figure_rows),
        "panel_role_counts": dict(Counter(row["role"] for row in panel_roles)),
        "case_anomaly_status_counts": dict(Counter((row.get("lookup") or {}).get("status", "collision") for row in case_rows)),
        "handoff_priorities": dict(Counter(item["lvcore_priority"] for item in handoff["file_families"])),
        "output_dir": str(out_dir),
    }
    write_json(out_dir / "summary.json", summary)
    write_text(out_dir / "summary.md", summary_markdown(summary))
    return summary


def panel_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Panel Inventory", "", f"Panel packages: {len(rows)}", ""]
    for row in rows:
        cls = row["classification"]
        lines.append(f"- `{row['package']}`: {row['file_count']} files; role `{cls['role']}`; impact `{cls['reader_impact']}`; confidence `{cls['confidence']}`")
    return "\n".join(lines) + "\n"


def case_anomalies_markdown(rows: list[dict[str, Any]]) -> str:
    counts = Counter((row.get("lookup") or {}).get("status", "collision") for row in rows)
    lines = ["# Case Anomalies", "", f"Rows: {len(rows)}", ""]
    for status, count in sorted(counts.items()):
        lines.append(f"- `{status}`: {count}")
    lines.append("")
    lines.append("Case-insensitive lookup is required for catalog, metadata, Panel, gaiji, and resource references.")
    return "\n".join(lines) + "\n"


def ccaltstr_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# CCALTSTR.HA", "", f"Files: {len(rows)}", ""]
    for row in rows:
        fp = row["fingerprint"]
        lines.append(f"- `{row['package']}` `{row['path']}`: {fp['size']} bytes, kind `{fp['kind']}`, role `{row['role']}`, confidence `{row['confidence']}`")
    return "\n".join(lines) + "\n"


def idx_uni_markdown(rows: list[dict[str, Any]]) -> str:
    eight_hex = sum(1 for row in rows for file in row.get("files", []) if file["eight_hex_stem"])
    lines = ["# IDX / UNI Associations", "", f"Packages: {len(rows)}", f"Eight-hex resources: {eight_hex}", ""]
    for row in rows:
        notable = [file for file in row["files"] if file["eight_hex_stem"] or file["declared_reference_count"]]
        if notable:
            lines.append(f"- `{row['package']}`: {len(notable)} notable IDX/UNI resources")
    return "\n".join(lines) + "\n"


def idx_uni_anomalies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        package = row["package"]
        for file in row["files"]:
            assoc = file["association"]
            if file["eight_hex_stem"] or assoc["rule"] != "folder_name_basename":
                out.append({"package": package, **file})
    return out


def association_rules_markdown() -> str:
    return """# Resource Association Rules

Observed resource association should prefer evidence in this order:

1. Explicit EXINFO.INI or DICPROF.INI reference, resolved case-insensitively.
2. Case-insensitive catalog component reference from SSEDINFO.
3. Eight-hex resource identifier naming for auxiliary IDX/UNI resources.
4. Folder or dictionary-id basename association.
5. Sibling resource fallback with low confidence.

Filename spelling differences are not treated as typos when metadata declares
the observed spelling. Collisions under case-insensitive comparison must be
reported as ambiguous instead of guessed.
"""


def figure_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# FIGURE.DIC", "", f"Files: {len(rows)}", ""]
    for row in rows:
        fp = row["fingerprint"]
        lines.append(f"- `{row['package']}`: {fp['size']} bytes, kind `{fp['kind']}`, role `{row['role']}`, confidence `{row['confidence']}`")
    return "\n".join(lines) + "\n"


def dicprof_markdown(rows: list[dict[str, Any]]) -> str:
    key_counts = dicprof_key_summary(rows)["value_class_counts"]
    lines = ["# DICPROF.INI", "", f"Files: {len(rows)}", ""]
    for cls, count in sorted(key_counts.items()):
        lines.append(f"- `{cls}`: {count}")
    return "\n".join(lines) + "\n"


def dicprof_key_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    key_counts: Counter[str] = Counter()
    value_class_counts: Counter[str] = Counter()
    for row in rows:
        for key in row.get("keys", []):
            key_counts[f"{key['section']}.{key['key']}"] += 1
            value_class_counts[key["value_class"]] += 1
    return {
        "dicprof_count": len(rows),
        "key_counts": dict(sorted(key_counts.items())),
        "value_class_counts": dict(sorted(value_class_counts.items())),
    }


def dicprof_exinfo_markdown(rows: list[dict[str, Any]]) -> str:
    both = 0
    dicprof_only = 0
    exinfo_only = 0
    for row in rows:
        package = Path(row["package_path"]).name
        has_dicprof = bool(row.get("dicprof"))
        has_exinfo = any(ref.get("ini", "").casefold() == "exinfo.ini" for ref in row.get("case_anomalies", []))
        if has_dicprof and has_exinfo:
            both += 1
        elif has_dicprof:
            dicprof_only += 1
        elif has_exinfo:
            exinfo_only += 1
    return f"""# DICPROF / EXINFO Comparison

- Packages with both metadata families: {both}
- Packages with DICPROF only: {dicprof_only}
- Packages with EXINFO references only in extracted metadata rows: {exinfo_only}

DICPROF is treated as independent package metadata. EXINFO remains the stronger
Windows renderer/index declaration source where present, while DICPROF can
explain dictionary ids, IDX/UNI basenames, UI/resource names, and local spelling
that should be respected by case-insensitive lookup.
"""


def lvcore_handoff(
    panel_rows: list[dict[str, Any]],
    ccaltstr_rows: list[dict[str, Any]],
    idx_uni_rows: list[dict[str, Any]],
    figure_rows: list[dict[str, Any]],
    dicprof_rows: list[dict[str, Any]],
    case_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    families: list[dict[str, Any]] = []
    if panel_rows:
        families.append(
            {
                "family": "Panel",
                "role": "panel_ui_or_navigation",
                "reader_impact": "supplemental_navigation_or_optional_viewer_panel",
                "confidence": "medium",
                "packages": [row["package"] for row in panel_rows],
                "observed_files": sorted({file["name"] for row in panel_rows for file in row["files"]}),
                "reference_patterns": ["XML/HTML file references", "DTD element declarations", "Panel BIN assets"],
                "mapping_strategy": "Resolve file references case-insensitively; attach only address/link references that are structurally explicit.",
                "entry_attachment_strategy": "Do not attach panel content to ordinary entries unless an address or entry id relationship is proven.",
                "linktarget_strategy": "Panel links with entry addresses can become supplemental LinkTarget records.",
                "resourceref_strategy": "Panel image/media assets can become ResourceRef records when directly referenced.",
                "native_search_strategy": "No native search impact unless panel metadata declares searchable keys.",
                "friendly_render_strategy": "Keep Panel output separate from ordinary entry body rendering by default.",
                "debug_render_strategy": "Expose panel file, reference, and BIN fingerprint metadata.",
                "synthetic_fixture_plan": "Panel XML/DTD/HTML with one referenced BIN and one explicit address link.",
                "lvcore_priority": "medium",
                "remaining_uncertainty": "Most BIN payload roles remain structural until entry/address references are proven.",
            }
        )
    if case_rows:
        families.append(
            {
                "family": "case_insensitive_lookup",
                "role": "resource_resolution",
                "reader_impact": "core_file_lookup",
                "confidence": "high",
                "packages": sorted({row.get("package", "") for row in case_rows if row.get("package")}),
                "observed_files": [],
                "reference_patterns": ["SSEDINFO component names", "EXINFO/DICPROF file references"],
                "mapping_strategy": "Lookup by exact path first, then casefolded path; report collisions.",
                "entry_attachment_strategy": "N/A",
                "linktarget_strategy": "N/A",
                "resourceref_strategy": "Apply to all package resource paths.",
                "native_search_strategy": "Apply to index component discovery.",
                "friendly_render_strategy": "No visible output.",
                "debug_render_strategy": "Expose requested and matched casing.",
                "synthetic_fixture_plan": "Mixed-case catalog and metadata references with a collision case.",
                "lvcore_priority": "high",
                "remaining_uncertainty": "None for single-match lookup; collisions require caller policy.",
            }
        )
    if idx_uni_rows:
        families.append(
            {
                "family": "IDX_UNI_resource_association",
                "role": "metadata_and_gaiji_resource",
                "reader_impact": "gaiji_or_auxiliary_index_resolution",
                "confidence": "medium",
                "packages": [row["package"] for row in idx_uni_rows if any(file["eight_hex_stem"] for file in row["files"])],
                "observed_files": sorted({file["name"] for row in idx_uni_rows for file in row["files"] if file["eight_hex_stem"]}),
                "reference_patterns": ["metadata declaration", "eight-hex resource id", "folder basename"],
                "mapping_strategy": "Prefer metadata declarations; otherwise classify eight-hex resources as auxiliary ids.",
                "entry_attachment_strategy": "N/A unless an auxiliary index row points to an entry.",
                "linktarget_strategy": "Auxiliary index rows can become navigation targets when row grammar is decoded.",
                "resourceref_strategy": "UNI files feed gaiji/resource resolution.",
                "native_search_strategy": "Do not merge auxiliary IDX with native indexes unless role is proven.",
                "friendly_render_strategy": "No direct rendering.",
                "debug_render_strategy": "Expose association rule and confidence.",
                "synthetic_fixture_plan": "DICPROF-declared basename mismatch and eight-hex UNI/IDX fixtures.",
                "lvcore_priority": "medium",
                "remaining_uncertainty": "Some eight-hex IDX files may be renderer/navigation helpers rather than search indexes.",
            }
        )
    if ccaltstr_rows:
        families.append(
            {
                "family": "CCALTSTR.HA",
                "role": "unknown_alt_string_or_renderer_helper",
                "reader_impact": "unknown",
                "confidence": "low",
                "packages": [row["package"] for row in ccaltstr_rows],
                "observed_files": ["CCALTSTR.HA"],
                "reference_patterns": [],
                "mapping_strategy": "Keep as explicitly classified unknown until record grammar or references are found.",
                "entry_attachment_strategy": "None yet.",
                "linktarget_strategy": "None yet.",
                "resourceref_strategy": "None yet.",
                "native_search_strategy": "Do not use for native search without evidence.",
                "friendly_render_strategy": "No friendly output.",
                "debug_render_strategy": "Expose fingerprint and structural counters only.",
                "synthetic_fixture_plan": "Binary fingerprint fixture with no text leakage.",
                "lvcore_priority": "defer",
                "remaining_uncertainty": "Needs record grammar or metadata references.",
            }
        )
    if figure_rows:
        families.append(
            {
                "family": "FIGURE.DIC",
                "role": "unknown_resource",
                "reader_impact": "unknown_until_referenced",
                "confidence": "low",
                "packages": [row["package"] for row in figure_rows],
                "observed_files": ["FIGURE.DIC"],
                "reference_patterns": ["metadata reference if present", "body/panel reference if later found"],
                "mapping_strategy": "Classify by component/container magic first; attach only with explicit references.",
                "entry_attachment_strategy": "None until a body/control/panel reference is identified.",
                "linktarget_strategy": "None yet.",
                "resourceref_strategy": "Possible ResourceRef only after record extents are decoded.",
                "native_search_strategy": "No evidence for native search.",
                "friendly_render_strategy": "No output until referenced.",
                "debug_render_strategy": "Expose fingerprint and reference evidence.",
                "synthetic_fixture_plan": "FIGURE.DIC fingerprint and reference extraction fixture.",
                "lvcore_priority": "low",
                "remaining_uncertainty": "Record structure and references.",
            }
        )
    return {
        "schema": "lvcore.resource_sidecar_handoff.v1",
        "file_families": families,
        "resource_association_rules": [
            {
                "rule": "case_insensitive_exact_then_casefold",
                "applies_to": "all package file/resource lookups",
                "confidence": "high",
                "evidence": "catalog and metadata references can differ in case from filesystem names",
                "lvcore_recommendation": "Use exact match first, casefold match second, report collisions.",
            },
            {
                "rule": "metadata_declared_basename",
                "applies_to": "DICPROF/EXINFO IDX and UNI references",
                "confidence": "high",
                "evidence": "metadata can declare resource basenames that differ from folder names",
                "lvcore_recommendation": "Treat declared names as authoritative resource ids when file exists.",
            },
            {
                "rule": "eight_hex_resource_id",
                "applies_to": "auxiliary IDX/UNI resources",
                "confidence": "medium",
                "evidence": "eight-hex resources recur as side resources and metadata targets",
                "lvcore_recommendation": "Classify separately from primary dictionary id resources until role is proven.",
            },
        ],
    }


def lvcore_handoff_markdown(handoff: dict[str, Any]) -> str:
    lines = ["# lvcore Handoff", ""]
    for family in handoff["file_families"]:
        lines.append(
            f"- `{family['family']}`: role `{family['role']}`, impact `{family['reader_impact']}`, "
            f"priority `{family['lvcore_priority']}`, confidence `{family['confidence']}`"
        )
    return "\n".join(lines) + "\n"


def unknowns_markdown(panel_roles: list[dict[str, Any]], ccaltstr_rows: list[dict[str, Any]], figure_rows: list[dict[str, Any]]) -> str:
    lines = ["# Unknowns And Next Evidence", ""]
    low_panel = [row for row in panel_roles if row["confidence"] == "low"]
    if low_panel:
        lines.append(f"- Panel packages with low-confidence role: {len(low_panel)}. Need explicit entry/address/resource references.")
    if ccaltstr_rows:
        lines.append("- CCALTSTR.HA remains structurally fingerprinted but semantically unresolved. Need record grammar or metadata/body references.")
    if figure_rows:
        lines.append("- FIGURE.DIC remains unresolved unless references or record extents are identified.")
    if len(lines) == 2:
        lines.append("- No unresolved file-family unknowns were identified by this pass.")
    return "\n".join(lines) + "\n"


def summary_markdown(summary: dict[str, Any]) -> str:
    return f"""# Resource / Sidecar Taxonomy v2

- Packages scanned: {summary['packages_scanned']}
- Panel packages: {summary['panel_package_count']}
- Panel files: {summary['panel_file_count']}
- Panel BIN files: {summary['panel_bin_count']}
- Case anomalies: {summary['case_anomaly_count']}
- CCALTSTR.HA files: {summary['ccaltstr_ha_count']}
- DICPROF.INI files: {summary['dicprof_count']}
- IDX/UNI packages: {summary['idx_uni_package_count']}
- FIGURE.DIC files: {summary['figure_dic_count']}

This report is structural. It records observations, inferred roles, confidence,
reader impact, and lvcore handoff recommendations without copying dictionary
entry text.
"""


def cmd_resource_taxonomy(args: argparse.Namespace) -> int:
    roots = args.root or [Path(".")]
    summary = build_taxonomy_report(roots, args.out_dir, jobs=args.jobs)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"packages scanned: {summary['packages_scanned']}")
        print(f"panel packages: {summary['panel_package_count']}")
        print(f"case anomalies: {summary['case_anomaly_count']}")
        print(f"output: {args.out_dir}")
    return 0
