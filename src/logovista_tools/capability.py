"""Corpus capability matrix built from redacted audit reports."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CAPABILITY_FIELDS = [
    "raw_honmon_body",
    "indexes_fully_parsed",
    "titles_fully_parsed",
    "gaiji_fully_resolved",
    "media_refs_resolved",
    "menu_pointers_resolved",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_summary_map(report_dir: Path) -> dict[str, dict[str, Any]]:
    summary = read_json(report_dir / "summary.json")
    rows = summary.get("profiles", summary if isinstance(summary, list) else [])
    return {str(row["dict_id"]): row for row in rows if isinstance(row, dict) and row.get("dict_id")}


def load_gaiji_readiness_map(report_dir: Path | None) -> dict[str, dict[str, Any]]:
    if report_dir is None:
        return {}
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        return {}
    summary = read_json(summary_path)
    rows = summary.get("rows", []) if isinstance(summary, dict) else []
    return {str(row["dict_id"]): row for row in rows if isinstance(row, dict) and row.get("dict_id")}


def detail_path(report_dir: Path, summary_row: dict[str, Any] | None, filename: str, dict_id: str) -> Path | None:
    if summary_row:
        raw = summary_row.get("profile") or summary_row.get("report")
        if isinstance(raw, str):
            candidate = Path(raw)
            if candidate.exists():
                return candidate
    candidate = report_dir / dict_id / filename
    if candidate.exists():
        return candidate
    return None


def load_detail(report_dir: Path, summary_row: dict[str, Any] | None, filename: str, dict_id: str) -> dict[str, Any]:
    path = detail_path(report_dir, summary_row, filename, dict_id)
    if path is None:
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def status_rank(status: str) -> int:
    return {
        "yes": 0,
        "n/a": 1,
        "partial": 2,
        "unknown": 3,
        "no": 4,
    }.get(status, 3)


def worst_status(statuses: list[str]) -> str:
    if not statuses:
        return "n/a"
    return max(statuses, key=status_rank)


def components_by_role(component_detail: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [row for row in component_detail.get("components", []) if row.get("role") == role]


def role_status_counts(component_detail: dict[str, Any], role: str) -> Counter[str]:
    return Counter(str(row.get("status") or "unknown") for row in components_by_role(component_detail, role))


def role_has_bad_component(component_detail: dict[str, Any], role: str) -> bool:
    return any((row.get("status") or "ok") != "ok" for row in components_by_role(component_detail, role))


def sum_text_stats(component_detail: dict[str, Any], roles: set[str]) -> Counter[str]:
    totals: Counter[str] = Counter()
    for component in component_detail.get("components", []):
        if component.get("role") not in roles:
            continue
        stats = component.get("decode", {}).get("stats", {})
        coverage = component.get("coverage", {})
        for key, value in stats.items():
            totals[key] += as_int(value)
        for key, value in coverage.items():
            totals[f"coverage_{key}"] += as_int(value)
    return totals


def raw_honmon_status(profile: dict[str, Any], honmon: dict[str, Any]) -> tuple[str, str]:
    body_hint = str(profile.get("body_source_hint") or "")
    shape = str(profile.get("honmon_shape") or honmon.get("byte_shape") or "")
    honmon_status = str(honmon.get("status") or profile.get("status") or "")
    if body_hint == "honmon":
        return "yes", f"body_source_hint={body_hint}; shape={shape}"
    if body_hint == "honmon_anchor_dereference":
        return "no", f"raw HONMON is anchor/dereference layer; shape={shape}"
    if body_hint == "none" or honmon_status in {"missing_honmon_file", "no_honmon_component"}:
        return "no", honmon_status or "no raw body source"
    if shape in {"body_stream_indexed", "body_stream_marker_sliced", "marker_rich_text_stream"}:
        return "partial", f"raw text-like HONMON but profile did not mark final body source; shape={shape}"
    if shape in {"dense_marker_table", "dense_numeric_id_table", "dense_token_table"}:
        return "no", f"dense HONMON shape={shape}"
    return "unknown", f"body_source_hint={body_hint or 'unknown'}; shape={shape or 'unknown'}"


def index_status(profile: dict[str, Any], component: dict[str, Any]) -> tuple[str, str]:
    count = as_int(component.get("component_counts", {}).get("index"))
    if not count and not as_int(profile.get("index_boundary_offsets")):
        return "n/a", "no structured index components reported"
    if role_has_bad_component(component, "index"):
        return "no", f"bad index component statuses={dict(role_status_counts(component, 'index'))}"
    totals = component.get("totals", {})
    residual = as_int(totals.get("index_nonzero_residual_bytes"))
    trailing = as_int(totals.get("index_trailing_component_nonzero"))
    unknown_leaf = as_int(profile.get("index_unknown_leaf_bytes"))
    if residual or trailing or unknown_leaf:
        return "partial", f"residual={residual}; trailing={trailing}; unknown_leaf={unknown_leaf}"
    return "yes", f"index_components={count}"


def title_status(component: dict[str, Any]) -> tuple[str, str]:
    titles = components_by_role(component, "title")
    if not titles:
        return "n/a", "no title components"
    if role_has_bad_component(component, "title"):
        return "no", f"bad title component statuses={dict(role_status_counts(component, 'title'))}"
    stats = sum_text_stats(component, {"title"})
    problems = (
        stats["coverage_uncovered_bytes"]
        + stats["unknown_controls"]
        + stats["unknown_bytes"]
        + stats["invalid_jis_pairs"]
        + stats["truncated_controls"]
        + stats["truncated_gaiji"]
    )
    if problems:
        return "partial", f"title text issues={problems}"
    return "yes", f"title_components={len(titles)}"


def gaiji_status(
    honmon_detail: dict[str, Any],
    component: dict[str, Any],
    readiness: dict[str, Any] | None = None,
) -> tuple[str, str, int, int]:
    if readiness:
        status = str(readiness.get("readiness_status") or "unknown")
        raw_occurrences = as_int(readiness.get("raw_occurrences"))
        display_unresolved = as_int(readiness.get("display_unresolved_occurrences"))
        reason = (
            f"gaiji_readiness={status}; raw={raw_occurrences}; "
            f"display_unresolved={display_unresolved}; "
            f"formatting_helper_candidates={as_int(readiness.get('formatting_helper_candidate_occurrences'))}; "
            f"search_fallback_missing={as_int(readiness.get('search_fallback_missing_occurrences'))}"
        )
        return status, reason, raw_occurrences, display_unresolved

    honmon_stats = honmon_detail.get("decode", {}).get("stats", {})
    text_stats = sum_text_stats(component, {"menu", "title", "text_index"})
    gaiji_total = as_int(honmon_stats.get("gaiji")) + text_stats["gaiji"]
    unresolved = as_int(honmon_stats.get("gaiji_unresolved")) + text_stats["gaiji_unresolved"]
    totals = component.get("totals", {})
    ga16_problem = (
        as_int(totals.get("ga16_missing_glyph_bytes"))
        + as_int(totals.get("ga16_trailing_nonzero_bytes"))
        + as_int(totals.get("ga16_unknown_header_nonzero"))
    )
    uni_problem = as_int(totals.get("uni_trailing_nonzero_bytes"))
    if not gaiji_total and not as_int(totals.get("ga16_components")) and not as_int(totals.get("uni_files")):
        return "n/a", "no gaiji occurrences/resources reported", gaiji_total, unresolved
    if role_has_bad_component(component, "ga16"):
        return "no", f"bad GA16 component statuses={dict(role_status_counts(component, 'ga16'))}", gaiji_total, unresolved
    if unresolved or ga16_problem or uni_problem:
        return "partial", f"gaiji_total={gaiji_total}; unresolved={unresolved}; ga16_problem={ga16_problem}; uni_trailer_nonzero={uni_problem}", gaiji_total, unresolved
    return "yes", f"gaiji_total={gaiji_total}; unresolved=0", gaiji_total, unresolved


def media_status(component: dict[str, Any]) -> tuple[str, str]:
    media_components = components_by_role(component, "colscr") + components_by_role(component, "pcmdata")
    totals = component.get("totals", {})
    if not media_components:
        return "n/a", "no COLSCR/PCMDATA components"
    if role_has_bad_component(component, "colscr") or role_has_bad_component(component, "pcmdata"):
        statuses = {
            "colscr": dict(role_status_counts(component, "colscr")),
            "pcmdata": dict(role_status_counts(component, "pcmdata")),
        }
        return "no", f"bad media component statuses={statuses}"
    problems = (
        as_int(totals.get("colscr_nonzero_unparsed_bytes"))
        + as_int(totals.get("colscr_invalid_referenced_records"))
        + as_int(totals.get("pcmdata_nonzero_unparsed_bytes"))
        + as_int(totals.get("pcmdata_invalid_referenced_records"))
    )
    if problems:
        return "partial", (
            f"colscr_invalid={as_int(totals.get('colscr_invalid_referenced_records'))}; "
            f"pcmdata_invalid_or_unclassified={as_int(totals.get('pcmdata_invalid_referenced_records'))}; "
            f"unparsed={as_int(totals.get('colscr_nonzero_unparsed_bytes')) + as_int(totals.get('pcmdata_nonzero_unparsed_bytes'))}"
        )
    return "yes", f"media_components={len(media_components)}"


def menu_status(component: dict[str, Any]) -> tuple[str, str]:
    menus = components_by_role(component, "menu")
    if not menus:
        return "n/a", "no MENU.DIC"
    if role_has_bad_component(component, "menu"):
        return "no", f"bad menu component statuses={dict(role_status_counts(component, 'menu'))}"
    total_destinations = 0
    total_resolved = 0
    text_problems = 0
    for menu in menus:
        coverage = menu.get("coverage", {})
        text_problems += (
            as_int(coverage.get("uncovered_bytes"))
            + as_int(coverage.get("unknown_controls"))
            + as_int(coverage.get("unknown_bytes"))
            + as_int(coverage.get("invalid_jis_pairs"))
            + as_int(coverage.get("truncated_controls"))
            + as_int(coverage.get("truncated_gaiji"))
        )
        menu_stats = menu.get("menu", {})
        total_destinations += as_int(menu_stats.get("destinations"))
        total_resolved += as_int(menu_stats.get("resolved_destinations"))
    if text_problems or total_resolved < total_destinations:
        return "partial", f"destinations={total_destinations}; resolved={total_resolved}; text_issues={text_problems}"
    return "yes", f"destinations={total_destinations}; resolved={total_resolved}"


def unknown_text_counts(honmon_detail: dict[str, Any], component: dict[str, Any]) -> tuple[int, int, int]:
    honmon_stats = honmon_detail.get("decode", {}).get("stats", {})
    text_stats = sum_text_stats(component, {"menu", "title", "text_index"})
    unknown_controls = as_int(honmon_stats.get("unknown_controls")) + text_stats["unknown_controls"]
    unknown_bytes = as_int(honmon_stats.get("unknown_bytes")) + text_stats["unknown_bytes"]
    structural_issues = (
        as_int(honmon_stats.get("invalid_jis_pairs"))
        + as_int(honmon_stats.get("truncated_controls"))
        + as_int(honmon_stats.get("truncated_gaiji"))
        + text_stats["invalid_jis_pairs"]
        + text_stats["truncated_controls"]
        + text_stats["truncated_gaiji"]
    )
    return unknown_controls, unknown_bytes, structural_issues


def common_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if row["raw_honmon_body"] == "no":
        blockers.append("body_requires_sidecar_or_is_missing")
    elif row["raw_honmon_body"] == "unknown":
        blockers.append("body_source_unknown")
    if row["indexes_fully_parsed"] in {"no", "partial", "unknown"}:
        blockers.append("indexes_not_fully_parsed")
    if row["titles_fully_parsed"] in {"no", "partial", "unknown"}:
        blockers.append("titles_not_fully_parsed")
    if row["gaiji_fully_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("gaiji_not_fully_resolved")
    if row["media_refs_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("media_not_fully_resolved")
    if row["menu_pointers_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("menu_not_fully_resolved")
    if row["unknown_controls"] or row["unknown_bytes"] or row["structural_text_issues"]:
        blockers.append("unknown_or_structural_text_issues")
    if row["component_parse_errors"]:
        blockers.append("component_parse_errors")
    if row["requires_sidecar_body"]:
        blockers.append("raw_body_not_self_contained")
    return blockers


def writer_v0_blockers(row: dict[str, Any]) -> list[str]:
    blockers = common_blockers(row)
    if row["titles_fully_parsed"] in {"no", "partial", "unknown"}:
        blockers.append("titles_not_fully_parsed")
    if row["gaiji_fully_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("gaiji_not_fully_resolved")
    if row["media_refs_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("media_not_fully_resolved")
    if row["menu_pointers_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("menu_not_fully_resolved")
    return list(dict.fromkeys(blockers))


def lossless_repacker_blockers(row: dict[str, Any]) -> list[str]:
    blockers = common_blockers(row)
    if row["package_status"] == "incomplete":
        blockers.insert(0, "missing_declared_components")
    if row["titles_fully_parsed"] in {"no", "partial", "unknown"}:
        blockers.append("titles_not_fully_parsed")
    if row["gaiji_fully_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("gaiji_not_fully_resolved")
    if row["media_refs_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("media_not_fully_resolved")
    if row["menu_pointers_resolved"] in {"no", "partial", "unknown"}:
        blockers.append("menu_not_fully_resolved")
    return list(dict.fromkeys(blockers))


def capability_status(blockers: list[str], *, red_blockers: set[str]) -> str:
    red = {
        "body_requires_sidecar_or_is_missing",
        "body_source_unknown",
        "indexes_not_fully_parsed",
        "unknown_or_structural_text_issues",
        "component_parse_errors",
    }
    red = red | red_blockers
    if any(blocker in red for blocker in blockers):
        return "red"
    if blockers:
        return "yellow"
    return "green"


def worst_writer_repacker_status(writer_status: str, repacker_status: str) -> str:
    if "red" in {writer_status, repacker_status}:
        return "red"
    if "yellow" in {writer_status, repacker_status}:
        return "yellow"
    return "green"


def matrix_row(
    dict_id: str,
    profile_row: dict[str, Any],
    honmon_row: dict[str, Any],
    component_row: dict[str, Any],
    profile_detail: dict[str, Any],
    honmon_detail: dict[str, Any],
    component_detail: dict[str, Any],
    gaiji_readiness_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    component_view = dict(component_row)
    for key in ("components", "uni_files"):
        if key in component_detail:
            component_view[key] = component_detail[key]
    raw_status, raw_reason = raw_honmon_status(profile_row, honmon_row)
    index_state, index_reason = index_status(profile_row, component_view)
    title_state, title_reason = title_status(component_view)
    gaiji_state, gaiji_reason, gaiji_total, gaiji_unresolved = gaiji_status(
        honmon_detail,
        component_view,
        gaiji_readiness_row,
    )
    media_state, media_reason = media_status(component_view)
    menu_state, menu_reason = menu_status(component_view)
    unknown_controls, unknown_bytes, structural_issues = unknown_text_counts(honmon_detail, component_view)
    package_status = str(profile_row.get("status") or profile_detail.get("classification", {}).get("status") or "unknown")
    component_statuses = Counter(str(component.get("status") or "unknown") for component in component_view.get("components", []))
    component_parse_errors = component_statuses.get("parse_error", 0)
    requires_sidecar = str(profile_row.get("body_source_hint") or "") == "honmon_anchor_dereference"
    row: dict[str, Any] = {
        "dict_id": dict_id,
        "title": profile_row.get("title") or honmon_row.get("title") or component_row.get("title") or "",
        "package_status": package_status,
        "honmon_shape": profile_row.get("honmon_shape") or honmon_row.get("byte_shape") or "",
        "body_source_hint": profile_row.get("body_source_hint") or "",
        "raw_honmon_body": raw_status,
        "raw_honmon_body_reason": raw_reason,
        "indexes_fully_parsed": index_state,
        "indexes_reason": index_reason,
        "titles_fully_parsed": title_state,
        "titles_reason": title_reason,
        "gaiji_fully_resolved": gaiji_state,
        "gaiji_reason": gaiji_reason,
        "gaiji_occurrences": gaiji_total,
        "gaiji_unresolved": gaiji_unresolved,
        "media_refs_resolved": media_state,
        "media_reason": media_reason,
        "menu_pointers_resolved": menu_state,
        "menu_reason": menu_reason,
        "unknown_controls": unknown_controls,
        "unknown_bytes": unknown_bytes,
        "structural_text_issues": structural_issues,
        "requires_sidecar_body": requires_sidecar,
        "component_parse_errors": component_parse_errors,
        "missing_components": ";".join(profile_detail.get("classification", {}).get("missing_components", [])),
    }
    writer_blockers = writer_v0_blockers(row)
    repacker_blockers = lossless_repacker_blockers(row)
    writer_status = capability_status(writer_blockers, red_blockers=set())
    repacker_status = capability_status(
        repacker_blockers,
        red_blockers={
            "missing_declared_components",
            "media_not_fully_resolved",
            "menu_not_fully_resolved",
            "titles_not_fully_parsed",
        },
    )
    row["legacy_writer_v0_status"] = writer_status
    row["legacy_writer_v0_blockers"] = ";".join(writer_blockers)
    row["lossless_repacker_status"] = repacker_status
    row["lossless_repacker_blockers"] = ";".join(repacker_blockers)
    row["writer_repacker_status"] = worst_writer_repacker_status(writer_status, repacker_status)
    row["writer_repacker_blockers"] = ";".join(list(dict.fromkeys(writer_blockers + repacker_blockers)))
    return row


def build_capability_matrix(
    *,
    profile_dir: Path,
    honmon_bytes_dir: Path,
    component_forensics_dir: Path,
    gaiji_readiness_dir: Path | None = None,
    selected: set[str] | None = None,
) -> dict[str, Any]:
    profiles = load_summary_map(profile_dir)
    honmon = load_summary_map(honmon_bytes_dir)
    components = load_summary_map(component_forensics_dir)
    gaiji_readiness = load_gaiji_readiness_map(gaiji_readiness_dir)
    dict_ids = sorted(set(profiles) | set(honmon) | set(components) | set(gaiji_readiness))
    if selected:
        dict_ids = [dict_id for dict_id in dict_ids if dict_id in selected]

    rows: list[dict[str, Any]] = []
    for dict_id in dict_ids:
        profile_row = profiles.get(dict_id, {})
        honmon_row = honmon.get(dict_id, {})
        component_row = components.get(dict_id, {})
        profile_detail = load_detail(profile_dir, profile_row, "profile.json", dict_id)
        honmon_detail = load_detail(honmon_bytes_dir, honmon_row, "honmon_bytes.json", dict_id)
        component_detail = load_detail(component_forensics_dir, component_row, "component_forensics.json", dict_id)
        rows.append(
            matrix_row(
                dict_id,
                profile_row,
                honmon_row,
                component_row,
                profile_detail,
                honmon_detail,
                component_detail,
                gaiji_readiness.get(dict_id),
            )
        )

    capability_counts = {
        field: dict(sorted(Counter(str(row.get(field) or "unknown") for row in rows).items()))
        for field in CAPABILITY_FIELDS
    }
    writer_counts = Counter(str(row.get("legacy_writer_v0_status") or "unknown") for row in rows)
    repacker_counts = Counter(str(row.get("lossless_repacker_status") or "unknown") for row in rows)
    combined_counts = Counter(str(row.get("writer_repacker_status") or "unknown") for row in rows)
    blocker_counts: Counter[str] = Counter()
    for row in rows:
        for blocker in str(row.get("writer_repacker_blockers") or "").split(";"):
            if blocker:
                blocker_counts[blocker] += 1
    return {
        "schema": "logovista-capability-matrix-v1",
        "sources": {
            "profile_dir": str(profile_dir),
            "honmon_bytes_dir": str(honmon_bytes_dir),
            "component_forensics_dir": str(component_forensics_dir),
            "gaiji_readiness_dir": str(gaiji_readiness_dir) if gaiji_readiness_dir else None,
        },
        "total": len(rows),
        "capability_counts": capability_counts,
        "legacy_writer_v0_status_counts": dict(sorted(writer_counts.items())),
        "lossless_repacker_status_counts": dict(sorted(repacker_counts.items())),
        "writer_repacker_status_counts": dict(sorted(combined_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "rows": rows,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = [
        "dict_id",
        "title",
        "package_status",
        "honmon_shape",
        "body_source_hint",
        "raw_honmon_body",
        "indexes_fully_parsed",
        "titles_fully_parsed",
        "gaiji_fully_resolved",
        "media_refs_resolved",
        "menu_pointers_resolved",
        "unknown_controls",
        "unknown_bytes",
        "structural_text_issues",
        "legacy_writer_v0_status",
        "legacy_writer_v0_blockers",
        "lossless_repacker_status",
        "lossless_repacker_blockers",
        "writer_repacker_status",
        "writer_repacker_blockers",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        ("Dict", "dict_id"),
        ("Body", "raw_honmon_body"),
        ("Index", "indexes_fully_parsed"),
        ("Title", "titles_fully_parsed"),
        ("Gaiji", "gaiji_fully_resolved"),
        ("Media", "media_refs_resolved"),
        ("Menu", "menu_pointers_resolved"),
        ("Unknown", "unknown_controls"),
        ("Status", "writer_repacker_status"),
        ("Blockers", "writer_repacker_blockers"),
    ]
    lines = ["| " + " | ".join(title for title, _key in columns) + " |"]
    lines.append("| " + " | ".join("---" for _title, _key in columns) + " |")
    for row in rows:
        values = []
        for _title, key in columns:
            value = str(row.get(key, ""))
            value = value.replace("|", "\\|")
            values.append(value)
        lines.append("| " + " | ".join(values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_capability_matrix_for_args(args: argparse.Namespace) -> dict[str, Any]:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.dict) if args.dict else None
    report = build_capability_matrix(
        profile_dir=args.profile_dir,
        honmon_bytes_dir=args.honmon_bytes_dir,
        component_forensics_dir=args.component_forensics_dir,
        gaiji_readiness_dir=getattr(args, "gaiji_readiness_dir", None),
        selected=selected,
    )
    (args.out_dir / "capability_matrix.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(args.out_dir / "capability_matrix.csv", report["rows"])
    write_markdown(args.out_dir / "capability_matrix.md", report["rows"])
    (args.out_dir / "summary.json").write_text(
        json.dumps(
            {key: value for key, value in report.items() if key != "rows"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report
