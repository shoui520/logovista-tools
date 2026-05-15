"""Inspect NHK Bungaku no Shizuku (SIZK) read-aloud SSED packages."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .controls import control_arg_length
from .entries import (
    BLOCK_SIZE,
    Break,
    Image,
    Media,
    Section,
    Text,
    decode_tokens,
    extract_heading,
    iter_entry_slices_with_boundaries,
    tokens_to_text,
)
from .gaiji import load_gaiji_profile, parse_uni_resource
from .ssed import expand_sseddata_file_with_storage, find_case_insensitive, is_metadata_noise_path, parse_ssedinfo_with_layout
from .windows import Exinfo, parse_exinfo


SIZK_TEMPLATE_ROLES = {
    "b121": "overview",
    "b122": "author",
    "b123": "narrator",
    "b124": "playback",
}
SIZK_SECTION_LABELS = {
    "0004": "work_title",
    "0005": "work_reading",
    "0006": "publication_year",
    "0007": "intro_line",
    "0008": "synopsis",
    "0010": "author_section_label",
    "0011": "author_name",
    "0012": "author_reading",
    "0013": "author_dates",
    "0014": "author_image",
    "0015": "author_image_credit",
    "0018": "author_bio",
    "0020": "narrator_section_label",
    "0021": "narrator_name",
    "0022": "narrator_reading",
    "0023": "narrator_profile",
    "0024": "narrator_image",
    "0028": "narrator_credit",
    "0030": "playback_section_label",
    "0031": "audio_file",
    "0032": "time_file",
    "0033": "text_file",
}
HONBUN_DIV_RE = re.compile(
    r"<div\b(?=[^>]*\bclass=[\"']honbun[\"'])(?=[^>]*\bid=[\"'](\d+)[\"'])[^>]*>(.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
TIMESTAMP_RE = re.compile(r"\d+(?:[.,]\d+)?")


def package_dict_id(path: Path, idx: Path | None = None) -> str:
    if idx is not None:
        return idx.stem
    name = path.name
    return name[5:] if name.upper().startswith("_DCT_") else name


def resolve_relative_case_insensitive(root: Path, value: str) -> Path:
    normalized = value.strip().strip('"').replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate
    current = root
    for part in candidate.parts:
        resolved = find_case_insensitive(current, part)
        current = resolved if resolved is not None else current / part
    return current


def relative_to_package(path: Path | None, package_dir: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(package_dir))
    except ValueError:
        return str(path)


def find_main_idx(package_dir: Path) -> Path | None:
    direct = sorted(
        child
        for child in package_dir.iterdir()
        if child.is_file() and child.suffix.lower() == ".idx" and not re.fullmatch(r"[0-9a-f]{8}\.idx", child.name, re.I)
    )
    for path in direct:
        try:
            _title, elements, _layout = parse_ssedinfo_with_layout(path)
        except Exception:
            continue
        if any(element.filename.upper() == "HONMON.DIC" for element in elements):
            return path
    return None


def exinfo_for_package(package_dir: Path) -> Exinfo | None:
    path = find_case_insensitive(package_dir, "EXINFO.INI")
    if path is None:
        return None
    try:
        return parse_exinfo(path)
    except Exception:
        return None


def is_sizk_package(package_dir: Path) -> bool:
    exinfo = exinfo_for_package(package_dir)
    if exinfo is None:
        return False
    general = {key.upper(): value for key, value in exinfo.general.items()}
    srcinfo = general.get("SRCINFO", "")
    has_shizuku_sidecars = (
        find_case_insensitive(package_dir, "shizuku.mp3") is not None
        and find_case_insensitive(package_dir, "shizuku_honbun.txt") is not None
        and find_case_insensitive(package_dir, "shizuku_time.txt") is not None
    )
    return (
        "文学のしずく" in srcinfo
        or (
            general.get("HTMLDLL", "").lower() == "hc0190.dll"
            and general.get("MP3NAME", "").lower() == "shizuku.mp3"
        )
        or has_shizuku_sidecars
    )


def discover_sizk_packages(roots: Iterable[Path]) -> list[Path]:
    packages: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        candidates: list[Path] = []
        if root.is_file():
            candidates.append(root.parent)
        elif root.is_dir():
            if find_case_insensitive(root, "EXINFO.INI") is not None:
                candidates.append(root)
            candidates.extend(
                path.parent
                for path in root.rglob("*")
                if path.is_file() and not is_metadata_noise_path(path) and path.name.casefold() == "exinfo.ini"
            )
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen or not resolved.is_dir():
                continue
            if is_sizk_package(resolved):
                seen.add(resolved)
                packages.append(resolved)
    return sorted(packages)


def read_text_auto(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data[:4] in {b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"}:
        return data.decode("utf-32", errors="replace"), "utf-32"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), "utf-16"

    def score(text: str) -> int:
        japanese = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff")
        ascii_printable = sum(1 for ch in text if " " <= ch <= "~")
        whitespace = sum(1 for ch in text if ch in "\r\n\t ")
        controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
        replacements = text.count("\ufffd")
        return japanese * 4 + ascii_printable + whitespace - controls * 8 - replacements * 12

    candidates: list[tuple[str, str]] = []
    if len(data) % 2 == 0:
        candidates.append((data.decode("utf-16le", errors="replace"), "utf-16le"))
    for encoding in ("utf-8-sig", "cp932"):
        try:
            candidates.append((data.decode(encoding), encoding))
        except UnicodeDecodeError:
            candidates.append((data.decode(encoding, errors="replace"), encoding))
    return max(candidates, key=lambda item: score(item[0]))


def parse_timestamp_ms(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    parts = text.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
        if len(parts) == 2:
            minutes, seconds = parts
            return int((int(minutes) * 60 + float(seconds)) * 1000)
    except ValueError:
        pass
    match = TIMESTAMP_RE.search(text)
    if match is None:
        return None
    raw = match.group(0).replace(",", ".")
    if "." in raw:
        return int(float(raw) * 1000)
    return int(raw)


def parse_honbun_template(path: Path) -> tuple[list[dict[str, Any]], str]:
    text, encoding = read_text_auto(path)
    rows = []
    for index, match in enumerate(HONBUN_DIV_RE.finditer(text), start=1):
        raw = match.group(2)
        clean = html.unescape(TAG_RE.sub("", raw)).strip()
        rows.append({"index": index, "time_ms": int(match.group(1)), "text": clean})
    return rows, encoding


def sidecar_text_lines(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"path": str(path) if path is not None else None, "exists": False, "line_count": 0}
    text, encoding = read_text_auto(path)
    lines = text.splitlines()
    return {
        "path": str(path),
        "exists": True,
        "encoding": encoding,
        "line_count": len(lines),
        "lines": lines,
    }


def playback_report(package_dir: Path, exinfo: Exinfo | None, include_rows: bool = False) -> dict[str, Any]:
    general = exinfo.general if exinfo is not None else {}
    mp3_name = general.get("MP3NAME", "shizuku.mp3")
    mp3_path = resolve_relative_case_insensitive(package_dir, mp3_name)
    honbun_path = resolve_relative_case_insensitive(package_dir, "shizuku_honbun.txt")
    time_path = resolve_relative_case_insensitive(package_dir, "shizuku_time.txt")
    template_path = resolve_relative_case_insensitive(package_dir, "Templates/honbun.html")

    text_sidecar = sidecar_text_lines(honbun_path)
    time_sidecar = sidecar_text_lines(time_path)
    template_rows: list[dict[str, Any]] = []
    template_encoding = None
    if template_path.exists():
        template_rows, template_encoding = parse_honbun_template(template_path)

    text_lines = list(text_sidecar.get("lines", []))
    time_lines = list(time_sidecar.get("lines", []))
    rows: list[dict[str, Any]] = []
    for index, (time_line, text_line) in enumerate(zip(time_lines, text_lines), start=1):
        rows.append(
            {
                "index": index,
                "time_ms": parse_timestamp_ms(time_line),
                "time_raw": time_line,
                "text": text_line,
            }
        )
    time_values = [row["time_ms"] for row in rows if row["time_ms"] is not None]
    template_times = [row["time_ms"] for row in template_rows]
    synchronized = (
        bool(rows)
        and len(text_lines) == len(time_lines)
        and (not template_rows or (len(template_rows) == len(rows) and template_times == time_values))
    )

    report: dict[str, Any] = {
        "mp3": {
            "path": str(mp3_path),
            "exists": mp3_path.exists(),
            "size": mp3_path.stat().st_size if mp3_path.exists() else 0,
        },
        "text_sidecar": {
            key: value for key, value in text_sidecar.items() if key != "lines"
        },
        "time_sidecar": {
            key: value for key, value in time_sidecar.items() if key != "lines"
        },
        "template_honbun": {
            "path": str(template_path),
            "exists": template_path.exists(),
            "encoding": template_encoding,
            "div_count": len(template_rows),
        },
        "synchronized": synchronized,
        "row_count": len(rows),
        "duration_ms": max(time_values) if time_values else None,
        "samples": rows[:3],
        "tail_samples": rows[-3:] if len(rows) > 3 else [],
    }
    if include_rows:
        report["rows"] = rows
        report["template_rows"] = template_rows
    return report


def html_template_paths(package_dir: Path) -> dict[str, str]:
    html_dir = find_case_insensitive(package_dir, "HTMLs")
    if html_dir is None or not html_dir.is_dir():
        return {}
    rows: dict[str, str] = {}
    for path in sorted(html_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".html":
            continue
        code = path.stem.lower()
        if re.fullmatch(r"b[0-9a-f]{3}", code):
            rows[code] = relative_to_package(path, package_dir) or str(path)
    return rows


def raw_gaiji_codes(data: bytes) -> list[str]:
    codes: list[str] = []
    i = 0
    while i < len(data):
        if data[i] == 0x1F and i + 1 < len(data):
            i += 2 + control_arg_length(data, i)
            continue
        if i + 1 < len(data) and 0xA1 <= data[i] <= 0xFE:
            codes.append(f"{data[i]:02x}{data[i + 1]:02x}")
            i += 2
            continue
        i += 1
    return codes


def clean_section_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def section_rows_from_tokens(tokens: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_code: str | None = None
    parts: list[str] = []

    def flush() -> None:
        nonlocal current_code, parts
        if current_code is None:
            parts = []
            return
        text = clean_section_text("".join(parts))
        rows.append(
            {
                "code": current_code,
                "label": SIZK_SECTION_LABELS.get(current_code),
                "text": text,
            }
        )
        parts = []

    for token in tokens:
        if isinstance(token, Section):
            flush()
            current_code = token.code
        elif isinstance(token, Text):
            parts.append(token.value)
        elif isinstance(token, Break):
            parts.append("\n")
        elif isinstance(token, Image):
            parts.append(f"<img:{token.key}>")
        elif isinstance(token, Media):
            parts.append(f"<media:{token.payload}>")
    flush()
    return rows


def first_section_text(sections: list[dict[str, Any]], code: str) -> str | None:
    for row in sections:
        if row["code"] == code and row["text"]:
            return str(row["text"])
    return None


def parse_sizk_honmon_entries(
    package_dir: Path,
    idx: Path,
    template_paths: dict[str, str],
    issues: list[str],
) -> dict[str, Any]:
    try:
        title, elements, layout = parse_ssedinfo_with_layout(idx)
    except Exception as exc:
        issues.append(f"could not parse SSEDINFO: {exc}")
        return {"entries": [], "error": str(exc)}

    honmon_element = next((element for element in elements if element.filename.upper() == "HONMON.DIC"), None)
    if honmon_element is None:
        issues.append("SSEDINFO has no HONMON.DIC component")
        return {"title": title, "entries": []}
    honmon_path = find_case_insensitive(package_dir, honmon_element.filename)
    if honmon_path is None:
        issues.append("HONMON.DIC component is missing")
        return {"title": title, "entries": []}

    gaiji_profile = load_gaiji_profile(idx)
    try:
        expanded, storage = expand_sseddata_file_with_storage(honmon_path)
    except Exception as exc:
        issues.append(f"could not expand HONMON.DIC: {exc}")
        return {"title": title, "entries": [], "honmon": str(honmon_path), "error": str(exc)}

    entries: list[dict[str, Any]] = []
    for entry_index, (start, end) in enumerate(iter_entry_slices_with_boundaries(expanded), start=1):
        segment = expanded[start:end]
        tokens, stats = decode_tokens(
            segment,
            gaiji="drop",
            gaiji_map=gaiji_profile.map,
            preserve_sections=True,
        )
        sections = section_rows_from_tokens(tokens)
        body = tokens_to_text(tokens)
        codes = raw_gaiji_codes(segment)
        template_code = next((code for code in codes if code in template_paths), None)
        heading = first_section_text(sections, "0004") or first_section_text(sections, "0011")
        heading = heading or first_section_text(sections, "0021") or extract_heading(tokens, body)
        entries.append(
            {
                "entry_index": entry_index,
                "address": {
                    "component": "HONMON.DIC",
                    "block": honmon_element.start + start // BLOCK_SIZE,
                    "offset": start % BLOCK_SIZE,
                    "component_offset": start,
                },
                "length": end - start,
                "template_code": template_code,
                "template_role": SIZK_TEMPLATE_ROLES.get(template_code or ""),
                "template_path": template_paths.get(template_code or ""),
                "gaiji_codes": codes,
                "heading": heading,
                "sections": sections,
                "references": {
                    "audio_file": first_section_text(sections, "0031"),
                    "time_file": first_section_text(sections, "0032"),
                    "text_file": first_section_text(sections, "0033"),
                },
                "stats": stats,
            }
        )

    return {
        "title": title,
        "layout": {
            "component_count_offset": layout.component_count_offset,
            "record_start": layout.record_start,
            "trailing_bytes": layout.trailing_bytes,
        },
        "honmon": str(honmon_path),
        "honmon_storage": storage,
        "expanded_bytes": len(expanded),
        "entry_markers": expanded.count(b"\x1f\x09\x00\x01"),
        "entries": entries,
        "gaiji": {
            "map_entries": len(gaiji_profile.map),
            "uni_entries": gaiji_profile.uni_entries,
            "plist_entries": gaiji_profile.plist_entries,
            "uni_paths": [str(path) for path in gaiji_profile.uni_paths],
            "plist_paths": [str(path) for path in gaiji_profile.plist_paths],
        },
    }


def inspect_sizk_package(package_dir: Path, *, include_playback_rows: bool = False) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    issues: list[str] = []
    idx = find_main_idx(package_dir)
    exinfo = exinfo_for_package(package_dir)
    general = exinfo.general if exinfo is not None else {}
    template_paths = html_template_paths(package_dir)

    gaiji_path: Path | None = None
    if general.get("GAIJI"):
        gaiji_path = resolve_relative_case_insensitive(package_dir, general["GAIJI"])
    uni_summary = None
    if gaiji_path is not None and gaiji_path.exists():
        resource = parse_uni_resource(gaiji_path)
        if resource is not None:
            uni_summary = {
                "path": str(gaiji_path),
                "format": resource.format,
                "half_count": resource.half_count,
                "full_count": resource.full_count,
                "records": len(resource.records),
                "mapped_records": sum(1 for record in resource.records if record.display),
                "trailing_bytes": resource.trailing_bytes,
            }
        else:
            issues.append(f"could not parse declared gaiji file: {gaiji_path}")
    elif gaiji_path is not None:
        issues.append(f"declared gaiji file is missing: {gaiji_path}")

    honmon = parse_sizk_honmon_entries(package_dir, idx, template_paths, issues) if idx is not None else {"entries": []}
    if idx is None:
        issues.append("no parseable SSEDINFO .IDX with HONMON.DIC found")

    playback = playback_report(package_dir, exinfo, include_rows=include_playback_rows)
    entries = list(honmon.get("entries", []))
    template_codes_seen = sorted({entry.get("template_code") for entry in entries if entry.get("template_code")})
    missing_template_entries = [
        entry["entry_index"]
        for entry in entries
        if entry.get("template_code") and not entry.get("template_path")
    ]
    if missing_template_entries:
        issues.append(f"HONMON entries reference missing HTML templates: {missing_template_entries}")
    if playback["mp3"]["exists"] is False:
        issues.append("declared/read-aloud MP3 is missing")
    if playback["text_sidecar"]["exists"] is False:
        issues.append("shizuku_honbun.txt is missing")
    if playback["time_sidecar"]["exists"] is False:
        issues.append("shizuku_time.txt is missing")
    if playback["template_honbun"]["exists"] is False:
        issues.append("Templates/honbun.html is missing")
    if playback["row_count"] and not playback["synchronized"]:
        issues.append("read-aloud text/time/template rows are not synchronized")

    return {
        "schema": "logovista-sizk-package-v1",
        "dict_id": package_dict_id(package_dir, idx),
        "package_dir": str(package_dir),
        "idx": str(idx) if idx is not None else None,
        "title": honmon.get("title"),
        "exinfo": {
            "path": str(exinfo.path) if exinfo is not None else None,
            "general": general,
        },
        "classification": {
            "kind": "sizk-read-aloud-html-package",
            "is_sizk": is_sizk_package(package_dir),
            "html_renderer": general.get("HTMLDLL"),
            "template_selectors_seen": template_codes_seen,
            "html_template_count": len(template_paths),
        },
        "declared_gaiji": uni_summary,
        "html_templates": {
            code: {
                "path": path,
                "role": SIZK_TEMPLATE_ROLES.get(code),
            }
            for code, path in sorted(template_paths.items())
        },
        "honmon": honmon,
        "playback": playback,
        "issues": issues,
    }


def write_sizk_report(report: dict[str, Any], out_dir: Path, *, write_playback_jsonl: bool = False) -> dict[str, Any]:
    dict_out = out_dir / str(report["dict_id"])
    dict_out.mkdir(parents=True, exist_ok=True)
    report = dict(report)
    playback = dict(report.get("playback", {}))
    rows = list(playback.get("rows", []))
    if write_playback_jsonl and rows:
        playback_path = dict_out / "playback.jsonl"
        with playback_path.open("w", encoding="utf-8") as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                out.write("\n")
        playback["jsonl_path"] = str(playback_path)
    playback.pop("rows", None)
    playback.pop("template_rows", None)
    report["playback"] = playback
    summary_path = dict_out / "sizk_summary.json"
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["summary_path"] = str(summary_path)
    return report
