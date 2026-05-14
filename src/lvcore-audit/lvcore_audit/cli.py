"""Command line interface for deterministic lvcore corpus audits."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import os
from pathlib import Path
import sys
from typing import Any

from lvcore import PackageFamily, detect_family, open_package

from .determinism import canonical_json, canonical_jsonl_row
from .package_validation import validate_package


CORPUS_AUDIT_SCHEMA = "lvcore.audit.corpus.v1"
PACKAGE_AUDIT_SCHEMA = "lvcore.audit.package.v1"
SHAPE_SCHEMA = "lvcore.audit.shape.v1"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(data), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(canonical_jsonl_row(row))
            fh.write("\n")


def _emit(data: Any, output: Path | None = None) -> None:
    text = canonical_json(data)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _top_counts(counts: dict[str, int], *, limit: int = 30) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _package_id(path: Path) -> str:
    return path.name


def _audit_path(path: Path) -> str:
    return f"<{_package_id(path)}>"


def _sample_limits(sample_entries: int, sample_search_hits: int, max_bytes_per_scan: int | None) -> dict[str, Any]:
    data: dict[str, Any] = {"sample_entries": sample_entries, "sample_search_hits": sample_search_hits}
    if max_bytes_per_scan is not None:
        data["max_bytes_per_scan"] = max_bytes_per_scan
    return data


def _sanitize(value: Any, *, package_id: str | None = None, package_root: Path | None = None, corpus_root: Path | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item, package_id=package_id, package_root=package_root, corpus_root=corpus_root)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, package_id=package_id, package_root=package_root, corpus_root=corpus_root) for item in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            if package_root is not None:
                try:
                    rel = candidate.relative_to(package_root)
                    return f"<{package_id}>/{rel.as_posix()}" if str(rel) != "." else f"<{package_id}>"
                except ValueError:
                    pass
            if corpus_root is not None:
                try:
                    rel = candidate.relative_to(corpus_root)
                    return f"<corpus>/{rel.as_posix()}" if str(rel) != "." else "<corpus>"
                except ValueError:
                    pass
        return value
    return value


def _shape(root: Path) -> dict[str, Any]:
    rows = []
    family_counts: dict[str, int] = {}
    for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name):
        info = detect_family(path)
        family = info.family.value
        family_counts[family] = family_counts.get(family, 0) + 1
        rows.append({"package_id": _package_id(path), "package_family": family})
    return {
        "schema": SHAPE_SCHEMA,
        "model_version": 1,
        "root": "<corpus>",
        "total_packages": len(rows),
        "family_counts": family_counts,
        "packages": rows,
    }


def cmd_package(args: argparse.Namespace) -> int:
    info = detect_family(args.path)
    package_id = _package_id(args.path)
    if info.family != PackageFamily.SSED:
        report = {
            "schema": PACKAGE_AUDIT_SCHEMA,
            "model_version": 1,
            "package_id": package_id,
            "package_family": info.family.value,
            "ok": True,
            "deferred_family": info.family in {PackageFamily.LVED, PackageFamily.LVLMULTI},
            "unsupported_family": info.family == PackageFamily.UNKNOWN,
            "package": _sanitize(info.to_dict(), package_id=package_id, package_root=args.path),
        }
    else:
        package = open_package(args.path)
        report = validate_package(
            package,
            sample_entries=args.sample_entries,
            sample_search_hits=args.sample_search_hits,
            debug=args.debug,
            max_bytes_per_scan=args.max_bytes_per_scan,
        )
        report = {
            "schema": PACKAGE_AUDIT_SCHEMA,
            "model_version": 1,
            "package_id": package_id,
            **_sanitize(report, package_id=package_id, package_root=args.path),
        }
    _emit(report, args.output)
    return 0 if report.get("ok", True) else 1


def _corpus_one(path_str: str, sample_entries: int, sample_search_hits: int, debug: bool, max_bytes_per_scan: int | None) -> dict[str, Any]:
    path = Path(path_str)
    package_id = _package_id(path)
    try:
        info = detect_family(path)
        if info.family != PackageFamily.SSED:
            family_deferred = info.family in {PackageFamily.LVED, PackageFamily.LVLMULTI}
            return {
                "package_id": package_id,
                "path": _audit_path(path),
                "name": path.name,
                "package_family": info.family.value,
                "ok": True,
                "deferred_family": family_deferred,
                "unsupported_family": info.family == PackageFamily.UNKNOWN,
                "sample_limits": _sample_limits(sample_entries, sample_search_hits, max_bytes_per_scan),
                "package": _sanitize(info.to_dict(), package_id=package_id, package_root=path),
            }
        package = open_package(path)
        report = validate_package(
            package,
            sample_entries=sample_entries,
            sample_search_hits=sample_search_hits,
            debug=debug,
            max_bytes_per_scan=max_bytes_per_scan,
        )
        report = _sanitize(report, package_id=package_id, package_root=path)
        return {
            "package_id": package_id,
            "path": _audit_path(path),
            "name": path.name,
            "package_family": info.family.value,
            "ok": bool(report.get("ok")),
            "deferred_family": False,
            "unsupported_family": False,
            "sample_limits": _sample_limits(sample_entries, sample_search_hits, max_bytes_per_scan),
            "package": report.get("package"),
            "body_source": report.get("body_source"),
            "sidecar_resolution": report.get("sidecar_resolution"),
            "sidecar_roles": report.get("sidecar_roles"),
            "sidecar_references": report.get("sidecar_references"),
            "sidecar_supplements": report.get("sidecar_supplements"),
            "resource_resolution": report.get("resource_resolution"),
            "decode_telemetry": report.get("decode_telemetry"),
            "title_dereference": report.get("title_dereference"),
            "component_count": report.get("component_count"),
            "gaiji": report.get("gaiji"),
            "indexes": report.get("indexes"),
            "index_summary": report.get("index_summary"),
            "title_components": report.get("title_components"),
            "diagnostics": report.get("diagnostics"),
            "sample_entries_checked": report.get("sample_entries_checked"),
            "sample_entries_rendered": report.get("sample_entries_rendered"),
            "sample_index_rows_checked": report.get("sample_index_rows_checked"),
            "sample_search_hits_dereferenced": report.get("sample_search_hits_dereferenced"),
            "sample_search_hits_rendered_html": report.get("sample_search_hits_rendered_html"),
            "sample_search_hits_rendered_text": report.get("sample_search_hits_rendered_text"),
        }
    except Exception as exc:  # pragma: no cover - aggregate defensive path
        return {
            "package_id": package_id,
            "path": _audit_path(path),
            "name": path.name,
            "package_family": "error",
            "ok": False,
            "deferred_family": False,
            "unsupported_family": False,
            "sample_limits": _sample_limits(sample_entries, sample_search_hits, max_bytes_per_scan),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _diagnostic_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        diagnostics = row.get("diagnostics") or {}
        if not diagnostics:
            continue
        by_severity = diagnostics.get("by_severity") or {}
        by_area = diagnostics.get("by_area") or {}
        by_code = diagnostics.get("by_code") or {}
        if not any(by_severity.values()) and not by_area and not by_code:
            continue
        body_source = row.get("body_source") or {}
        out.append(
            {
                "package_id": row.get("package_id"),
                "name": row.get("name"),
                "package_family": row.get("package_family"),
                "ok": row.get("ok"),
                "body_source_kind": body_source.get("ssed_kind"),
                "body_source_support": body_source.get("support"),
                "diagnostics": {
                    "by_severity": by_severity,
                    "by_area": by_area,
                    "by_code": by_code,
                },
            }
        )
    return sorted(out, key=lambda item: str(item.get("package_id")))


def _blockers(rows: list[dict[str, Any]], diagnostics_by_code: dict[str, int], *, limit: int = 20) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        family = str(row.get("package_family"))
        if not row.get("ok"):
            counts[("package", "validation_failed", family)] = counts.get(("package", "validation_failed", family), 0) + 1
        if family == PackageFamily.SSED.value:
            body_source = row.get("body_source") or {}
            support = str(body_source.get("support") or "")
            kind = str(body_source.get("ssed_kind") or "")
            if kind == "missing_body_component":
                continue
            if support in {"deferred", "unsupported", "unknown"}:
                code = f"body_source_{support}"
                counts[("ssed_body_source", code, kind or "unknown")] = counts.get(("ssed_body_source", code, kind or "unknown"), 0) + 1
        elif row.get("deferred_family"):
            counts[("package_family", "family_deferred", family)] = counts.get(("package_family", "family_deferred", family), 0) + 1
        elif row.get("unsupported_family"):
            counts[("package_family", "family_unknown", family)] = counts.get(("package_family", "family_unknown", family), 0) + 1

    blockers = [
        {"scope": scope, "code": code, "subject": subject, "count": count}
        for (scope, code, subject), count in counts.items()
    ]
    for code, count in _top_counts(diagnostics_by_code, limit=10).items():
        blockers.append({"scope": "diagnostic", "code": code, "subject": code, "count": count})
    blockers.sort(key=lambda item: (-int(item["count"]), str(item["scope"]), str(item["code"]), str(item["subject"])))
    return blockers[:limit]


def _merge_counts(dest: dict[str, int], source: dict[str, Any] | None) -> None:
    for key, count in (source or {}).items():
        dest[key] = dest.get(key, 0) + int(count)


def _merge_nested_counts(dest: dict[str, Any], source: dict[str, Any] | None) -> None:
    for key, count in (source or {}).items():
        if isinstance(count, dict):
            inner = dest.setdefault(key, {})
            if not isinstance(inner, dict):
                inner = {}
                dest[key] = inner
            _merge_counts(inner, count)
        else:
            dest[key] = int(dest.get(key, 0)) + int(count or 0)


def build_corpus_summary(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    output_dir = args.output_dir.resolve() if args.output_dir else None
    paths = sorted(
        (
            path
            for path in args.root.iterdir()
            if path.is_dir() and (output_dir is None or path.resolve() != output_dir)
        ),
        key=lambda item: item.name,
    )
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else max(1, args.jobs)
    sample_entries = args.sample_entries if args.sample_entries is not None else (3 if args.full else 1)
    sample_search_hits = args.sample_search_hits if args.sample_search_hits is not None else (8 if args.full else 2)
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(_corpus_one, str(path), sample_entries, sample_search_hits, args.debug, args.max_bytes_per_scan): path
            for path in paths
        }
        done = 0
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            done += 1
            if args.progress and (done % 10 == 0 or done == len(paths)):
                status = "ok" if row.get("ok") else "fail"
                print(
                    f"progress {done}/{len(paths)} {row.get('name')} family={row.get('package_family')} status={status}",
                    file=sys.stderr,
                    flush=True,
                )
    rows.sort(key=lambda item: str(item["package_id"]))

    family_counts: dict[str, int] = {}
    family_deferred_counts: dict[str, int] = {}
    unsupported_family_counts: dict[str, int] = {}
    body_kind_counts: dict[str, int] = {}
    support_counts: dict[str, int] = {}
    render_support_counts: dict[str, int] = {}
    diagnostics_by_severity = {"info": 0, "warning": 0, "error": 0}
    diagnostics_by_code: dict[str, int] = {}
    diagnostics_by_area: dict[str, int] = {}
    sidecar_resolution_counts = {
        "resolved": 0,
        "missing_anchor_id": 0,
        "missing_row": 0,
        "unsupported_body_source": 0,
        "missing_body_component": 0,
    }
    index_component_type_counts: dict[str, int] = {}
    index_rows_by_component_type: dict[str, int] = {}
    index_unsupported_component_types: dict[str, int] = {}
    index_malformed_leaf_rows = 0
    index_physical_tail_bytes = 0
    index_physical_tail_nonzero_bytes = 0
    index_text_like_outliers: dict[str, int] = {}
    index_continuation_groups = 0
    index_dangling_continuation_rows = 0
    sidecar_role_counts: dict[str, int] = {}
    supported_sidecar_role_counts: dict[str, int] = {}
    unsupported_sidecar_role_counts: dict[str, int] = {}
    compatibility_significant_unsupported_sidecar_counts: dict[str, int] = {}
    sidecar_support_status_counts: dict[str, int] = {}
    sidecar_reference_counts: dict[str, Any] = {"addresses_checked": 0, "matched": 0, "by_role": {}, "by_status": {}, "by_table": {}}
    sidecar_supplement_counts: dict[str, Any] = {}
    resource_resolution_counts = {
        "unresolved_gaiji": 0,
        "unresolved_media": 0,
        "unresolved_link": 0,
        "resolved_gaiji": 0,
        "resolved_media": 0,
        "resolved_link": 0,
    }
    resource_resolution_by_reason = {"unresolved_gaiji": {}, "unresolved_media": {}, "unresolved_link": {}}
    media_resolution_counts: dict[str, dict[str, int]] = {}
    decode_telemetry_counts = {"unknown_controls": 0, "unknown_bytes": 0}
    title_dereference_counts: dict[str, Any] = {
        "attempts": 0,
        "resolved": 0,
        "fallback": 0,
        "failed": 0,
        "empty": 0,
        "by_reason": {},
        "title_status_counts": {},
        "heading_source_counts": {},
    }
    render_summary = {
        "sample_entries_checked": 0,
        "sample_entries_rendered": 0,
        "sample_index_rows_checked": 0,
        "search_hits_dereferenced": 0,
        "search_hits_rendered_html": 0,
        "search_hits_rendered_text": 0,
    }
    dense_honmon_count = 0
    sidecar_backed_count = 0
    renderer_sidecar_like_count = 0
    failures: list[dict[str, Any]] = []

    for row in rows:
        family = str(row.get("package_family"))
        family_counts[family] = family_counts.get(family, 0) + 1
        if row.get("deferred_family"):
            family_deferred_counts[family] = family_deferred_counts.get(family, 0) + 1
        if row.get("unsupported_family"):
            unsupported_family_counts[family] = unsupported_family_counts.get(family, 0) + 1
        if not row.get("ok"):
            failures.append(row)
        body_source = row.get("body_source") or {}
        kind = body_source.get("ssed_kind")
        support = body_source.get("support")
        if kind:
            body_kind_counts[kind] = body_kind_counts.get(kind, 0) + 1
            if str(kind).startswith("dense_"):
                dense_honmon_count += 1
            if kind in {"renderer_sqlite_sidecar", "dictfulldb_sidecar", "honbun_sidecar", "vlpljbl_sidecar"}:
                renderer_sidecar_like_count += 1
        if support:
            support_counts[support] = support_counts.get(support, 0) + 1
            render_support_counts[support] = render_support_counts.get(support, 0) + 1
        if body_source.get("sidecar_kind") or body_source.get("sidecars"):
            sidecar_backed_count += 1
        diagnostics = row.get("diagnostics") or {}
        _merge_counts(diagnostics_by_severity, diagnostics.get("by_severity"))
        _merge_counts(diagnostics_by_code, diagnostics.get("by_code"))
        _merge_counts(diagnostics_by_area, diagnostics.get("by_area"))
        _merge_counts(sidecar_resolution_counts, row.get("sidecar_resolution"))
        sidecar_roles = row.get("sidecar_roles") or {}
        _merge_counts(sidecar_role_counts, sidecar_roles.get("role_counts"))
        _merge_counts(supported_sidecar_role_counts, sidecar_roles.get("supported_role_counts"))
        _merge_counts(unsupported_sidecar_role_counts, sidecar_roles.get("unsupported_role_counts"))
        _merge_counts(compatibility_significant_unsupported_sidecar_counts, sidecar_roles.get("compatibility_significant_unsupported_counts"))
        _merge_counts(sidecar_support_status_counts, sidecar_roles.get("support_status_counts"))
        references = row.get("sidecar_references") or {}
        sidecar_reference_counts["addresses_checked"] = int(sidecar_reference_counts.get("addresses_checked", 0)) + int(references.get("addresses_checked") or 0)
        sidecar_reference_counts["matched"] = int(sidecar_reference_counts.get("matched", 0)) + int(references.get("matched") or 0)
        for bucket in ("by_role", "by_status", "by_table"):
            _merge_counts(sidecar_reference_counts.setdefault(bucket, {}), references.get(bucket))
        _merge_nested_counts(sidecar_supplement_counts, row.get("sidecar_supplements"))
        index_summary = row.get("index_summary") or {}
        _merge_counts(index_component_type_counts, index_summary.get("component_type_counts"))
        _merge_counts(index_rows_by_component_type, index_summary.get("rows_by_component_type"))
        for _name, component_type in (index_summary.get("unsupported_component_types") or {}).items():
            index_unsupported_component_types[component_type] = index_unsupported_component_types.get(component_type, 0) + 1
        index_malformed_leaf_rows += int(index_summary.get("malformed_leaf_rows") or 0)
        index_physical_tail_bytes += int(index_summary.get("physical_tail_bytes") or 0)
        index_physical_tail_nonzero_bytes += int(index_summary.get("physical_tail_nonzero_bytes") or 0)
        for _name, component_type in (index_summary.get("text_like_index_outliers") or {}).items():
            index_text_like_outliers[component_type] = index_text_like_outliers.get(component_type, 0) + 1
        index_continuation_groups += int(index_summary.get("continuation_groups") or 0)
        index_dangling_continuation_rows += int(index_summary.get("dangling_continuation_rows") or 0)
        for key, count in (row.get("resource_resolution") or {}).items():
            if isinstance(count, dict):
                if key.endswith("_by_reason"):
                    reason_bucket = resource_resolution_by_reason.setdefault(key.removesuffix("_by_reason"), {})
                    _merge_counts(reason_bucket, count)
                else:
                    media_bucket = media_resolution_counts.setdefault(key, {})
                    _merge_counts(media_bucket, count)
            else:
                resource_resolution_counts[key] = resource_resolution_counts.get(key, 0) + int(count)
        _merge_counts(decode_telemetry_counts, row.get("decode_telemetry"))
        title_deref = row.get("title_dereference") or {}
        for key in ("attempts", "resolved", "fallback", "failed", "empty"):
            title_dereference_counts[key] += int(title_deref.get(key) or 0)
        _merge_counts(title_dereference_counts["by_reason"], title_deref.get("by_reason"))
        _merge_counts(title_dereference_counts["title_status_counts"], title_deref.get("title_status_counts"))
        _merge_counts(title_dereference_counts["heading_source_counts"], title_deref.get("heading_source_counts"))
        render_summary["sample_entries_checked"] += int(row.get("sample_entries_checked") or 0)
        render_summary["sample_entries_rendered"] += int(row.get("sample_entries_rendered") or 0)
        render_summary["sample_index_rows_checked"] += int(row.get("sample_index_rows_checked") or 0)
        render_summary["search_hits_dereferenced"] += int(row.get("sample_search_hits_dereferenced") or 0)
        render_summary["search_hits_rendered_html"] += int(row.get("sample_search_hits_rendered_html") or 0)
        render_summary["search_hits_rendered_text"] += int(row.get("sample_search_hits_rendered_text") or 0)

    diagnostics_rows = _diagnostic_rows(rows)
    top_blockers = _blockers(rows, diagnostics_by_code)
    true_display_unresolved = int(resource_resolution_counts.get("gaiji_display_unresolved", resource_resolution_counts.get("unresolved_gaiji", 0)) or 0)
    hard_ssed_failures = sum(
        1
        for row in failures
        if row.get("package_family") == PackageFamily.SSED.value
        and (row.get("body_source") or {}).get("ssed_kind") != "missing_body_component"
    )
    named_residuals: list[dict[str, Any]] = []
    ignored_package_integrity_residuals: list[dict[str, Any]] = []
    for row in rows:
        if row.get("package_family") != PackageFamily.SSED.value:
            continue
        body_source = row.get("body_source") or {}
        support = str(body_source.get("support") or "")
        kind = str(body_source.get("ssed_kind") or "")
        if kind == "missing_body_component":
            ignored_package_integrity_residuals.append(
                {
                    "package_id": row.get("package_id"),
                    "name": row.get("name"),
                    "blocker_class": "package_integrity",
                    "status": "ignored_broken_package",
                    "kind": kind,
                    "diagnostics": (row.get("diagnostics") or {}).get("by_code") or {},
                }
            )
            continue
        if not row.get("ok") or support in {"unsupported", "unknown", "deferred"}:
            named_residuals.append(
                {
                    "package_id": row.get("package_id"),
                    "name": row.get("name"),
                    "blocker_class": "body_source",
                    "status": support or "unknown",
                    "kind": kind,
                    "diagnostics": (row.get("diagnostics") or {}).get("by_code") or {},
                }
            )
    closure_status = "closure_ready_for_deeper_audit"
    if hard_ssed_failures or compatibility_significant_unsupported_sidecar_counts or named_residuals:
        closure_status = "blocked_by_named_residuals"
    elif int(diagnostics_by_code.get("sample_search_miss", 0)) or true_display_unresolved:
        closure_status = "blocked_by_diagnostics"

    closure_scorecard = {
        "status": closure_status,
        "total_packages": len(rows),
        "ssed_packages": family_counts.get(PackageFamily.SSED.value, 0),
        "hard_ssed_failures": hard_ssed_failures,
        "renderable": support_counts.get("renderable", 0),
        "partially_renderable": support_counts.get("partially_renderable", 0),
        "deferred": support_counts.get("deferred", 0),
        "unsupported_or_unknown": support_counts.get("unsupported", 0) + support_counts.get("unknown", 0),
        "compatibility_unsupported_or_unknown": len(named_residuals),
        "body_source_kind_counts": body_kind_counts,
        "compatibility_significant_unsupported_sidecars": compatibility_significant_unsupported_sidecar_counts,
        "native_search_misses": diagnostics_by_code.get("sample_search_miss", 0),
        "sample_search_skipped_empty_query": diagnostics_by_code.get("sample_search_skipped_empty_query", 0),
        "true_display_unresolved_gaiji": true_display_unresolved,
        "unresolved_media": resource_resolution_counts.get("unresolved_media", 0),
        "unresolved_link": resource_resolution_counts.get("unresolved_link", 0),
        "named_residuals": named_residuals,
        "ignored_package_integrity_residuals": ignored_package_integrity_residuals,
        "top_blockers": top_blockers,
    }

    summary_sample_limits: dict[str, Any] = {
        "full": bool(args.full),
        "sample_entries": sample_entries,
        "sample_search_hits": sample_search_hits,
        "jobs": args.jobs,
    }
    if args.max_bytes_per_scan is not None:
        summary_sample_limits["max_bytes_per_scan"] = args.max_bytes_per_scan

    summary = {
        "schema": CORPUS_AUDIT_SCHEMA,
        "model_version": 1,
        "root": "<corpus>",
        "sample_limits": summary_sample_limits,
        "total_packages": len(rows),
        "family_counts": family_counts,
        "family_deferred_counts": family_deferred_counts,
        "unsupported_family_counts": unsupported_family_counts,
        "ssed_body_source_kind_counts": body_kind_counts,
        "ssed_body_source_support_counts": support_counts,
        "render_support_counts": render_support_counts,
        "ssed_renderable_count": support_counts.get("renderable", 0),
        "ssed_partially_renderable_count": support_counts.get("partially_renderable", 0),
        "ssed_deferred_count": support_counts.get("deferred", 0),
        "ssed_unsupported_or_unknown_count": support_counts.get("unsupported", 0) + support_counts.get("unknown", 0),
        "dense_honmon_count": dense_honmon_count,
        "sidecar_backed_count": sidecar_backed_count,
        "renderer_sidecar_like_count": renderer_sidecar_like_count,
        "open_failure_count": family_counts.get("error", 0),
        "render_summary": render_summary,
        "sidecar_resolution_counts": sidecar_resolution_counts,
        "index_summary": {
            "component_type_counts": index_component_type_counts,
            "rows_by_component_type": index_rows_by_component_type,
            "unsupported_component_types": index_unsupported_component_types,
            "malformed_leaf_rows": index_malformed_leaf_rows,
            "physical_tail_bytes": index_physical_tail_bytes,
            "physical_tail_nonzero_bytes": index_physical_tail_nonzero_bytes,
            "text_like_index_outliers": index_text_like_outliers,
            "continuation_groups": index_continuation_groups,
            "dangling_continuation_rows": index_dangling_continuation_rows,
        },
        "sidecar_role_counts": sidecar_role_counts,
        "supported_sidecar_role_counts": supported_sidecar_role_counts,
        "unsupported_sidecar_role_counts": unsupported_sidecar_role_counts,
        "compatibility_significant_unsupported_sidecar_counts": compatibility_significant_unsupported_sidecar_counts,
        "sidecar_support_status_counts": sidecar_support_status_counts,
        "sidecar_reference_counts": sidecar_reference_counts,
        "sidecar_supplement_counts": sidecar_supplement_counts,
        "resource_resolution_counts": resource_resolution_counts,
        "resource_resolution_by_reason": resource_resolution_by_reason,
        "media_resolution_counts": media_resolution_counts,
        "decode_telemetry_counts": decode_telemetry_counts,
        "title_dereference_counts": title_dereference_counts,
        "failure_count": len(failures),
        "closure_scorecard": closure_scorecard,
        "diagnostics": {"by_severity": diagnostics_by_severity, "by_area": diagnostics_by_area, "by_code": diagnostics_by_code},
        "top_diagnostics_by_severity": _top_counts(diagnostics_by_severity),
        "top_diagnostics_by_code": _top_counts(diagnostics_by_code),
        "top_diagnostics_by_area": _top_counts(diagnostics_by_area),
        "top_blockers": top_blockers,
        "targets": rows,
    }
    return summary, rows, failures, diagnostics_rows


def cmd_corpus(args: argparse.Namespace) -> int:
    if args.shape_only:
        _emit(_shape(args.root), args.output)
        return 0
    summary, rows, failures, diagnostics_rows = build_corpus_summary(args)
    output_files: dict[str, str] = {}
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = args.output_dir / "summary.json"
        targets_path = args.output_dir / "targets.jsonl"
        failures_path = args.failures_jsonl or (args.output_dir / "failures.jsonl")
        diagnostics_path = args.diagnostics_jsonl or (args.output_dir / "diagnostics.jsonl")
        _write_json(summary_path, summary)
        _write_jsonl(targets_path, rows)
        _write_jsonl(failures_path, failures)
        _write_jsonl(diagnostics_path, diagnostics_rows)
        output_files = {
            "summary_json": "summary.json",
            "targets_jsonl": "targets.jsonl",
            "failures_jsonl": failures_path.name,
            "diagnostics_jsonl": diagnostics_path.name,
        }
    else:
        if args.failures_jsonl:
            _write_jsonl(args.failures_jsonl, failures)
            output_files["failures_jsonl"] = args.failures_jsonl.name
        if args.diagnostics_jsonl:
            _write_jsonl(args.diagnostics_jsonl, diagnostics_rows)
            output_files["diagnostics_jsonl"] = args.diagnostics_jsonl.name
    if output_files:
        summary["output_files"] = output_files
        if args.output_dir:
            _write_json(args.output_dir / "summary.json", summary)
    _emit(summary, args.output)
    return 0 if not failures else 1


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda item: str(item[0])):
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(item, child))
        return out
    if isinstance(value, list):
        out: dict[str, Any] = {}
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def cmd_diff(args: argparse.Namespace) -> int:
    baseline = __import__("json").loads(args.baseline.read_text(encoding="utf-8"))
    current = __import__("json").loads(args.current.read_text(encoding="utf-8"))
    left = _flatten(baseline)
    right = _flatten(current)
    added = {key: right[key] for key in sorted(right.keys() - left.keys())}
    removed = {key: left[key] for key in sorted(left.keys() - right.keys())}
    changed = {
        key: {"baseline": left[key], "current": right[key]}
        for key in sorted(left.keys() & right.keys())
        if left[key] != right[key]
    }
    report = {
        "schema": "lvcore.audit.diff.v1",
        "model_version": 1,
        "ok": not added and not removed and not changed,
        "added": added,
        "removed": removed,
        "changed": changed,
    }
    _emit(report, args.output)
    return 1 if args.strict and not report["ok"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lvcore_audit", description="Deterministic audit harness for lvcore")
    sub = parser.add_subparsers(dest="command", required=True)

    package = sub.add_parser("package", help="Audit one LogoVista package")
    package.add_argument("path", type=Path)
    package.add_argument("--sample-entries", type=int, default=3)
    package.add_argument("--sample-search-hits", type=int, default=5)
    package.add_argument("--max-bytes-per-scan", type=int)
    package.add_argument("--json", action="store_true", help="Compatibility no-op; JSON is always emitted")
    package.add_argument("--debug", action="store_true")
    package.add_argument("--output", type=Path)
    package.set_defaults(func=cmd_package)

    corpus = sub.add_parser("corpus", help="Audit a corpus directory")
    corpus.add_argument("root", type=Path)
    corpus.add_argument("--full", action="store_true")
    corpus.add_argument("--json", action="store_true", help="Compatibility no-op; JSON is always emitted")
    corpus.add_argument("--debug", action="store_true")
    corpus.add_argument("--output", type=Path)
    corpus.add_argument("--output-dir", type=Path)
    corpus.add_argument("--failures-jsonl", type=Path)
    corpus.add_argument("--diagnostics-jsonl", type=Path)
    corpus.add_argument("--sample-entries", type=int)
    corpus.add_argument("--sample-search-hits", type=int)
    corpus.add_argument("--max-bytes-per-scan", type=int)
    corpus.add_argument("--jobs", type=int, default=0)
    corpus.add_argument("--progress", action="store_true")
    corpus.add_argument("--shape-only", action="store_true")
    corpus.set_defaults(func=cmd_corpus)

    diff = sub.add_parser("diff", help="Compare two audit JSON files")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("current", type=Path)
    diff.add_argument("--strict", action="store_true")
    diff.add_argument("--output", type=Path)
    diff.set_defaults(func=cmd_diff)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
