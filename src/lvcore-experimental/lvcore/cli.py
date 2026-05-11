"""lvcore command line interface."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
from typing import Any

from .detect import detect_family
from .errors import UnsupportedPackageError
from .model import PackageFamily, SearchProfile
from .package import open_package
from .render import HtmlProfile


CORPUS_VALIDATE_SCHEMA = "lvcore.corpus_validate.v1"


def emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _json_dump_compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(_json_dump_compact(row))
            fh.write("\n")


def _top_counts(counts: dict[str, int], *, limit: int = 30) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def cmd_identify(args: argparse.Namespace) -> int:
    emit(detect_family(args.path).to_dict())
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    info = detect_family(args.path)
    if info.family.value != "ssed":
        emit(info.to_dict())
        return 0
    package = open_package(args.path)
    emit(package.summary())
    return 0


def cmd_body_source(args: argparse.Namespace) -> int:
    info = detect_family(args.path)
    if info.family != PackageFamily.SSED:
        emit({"package": info.to_dict(), "body_source": {"package_family": info.family.value, "support": "deferred"}})
        return 0
    package = open_package(args.path)
    report = {"package": package.info.to_dict(), "body_source": package.body_source().to_dict(debug=args.debug)}
    emit(report)
    return 0


def cmd_entries(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    for entry in package.iter_entries(limit=args.limit):
        record = entry.to_dict(debug=True) if args.spans else {
            "address": entry.address.to_dict(),
            "headword": entry.headword,
            "text": entry.text,
        }
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


def cmd_titles(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    for index, title in enumerate(package.titles(component=args.component, limit=args.limit), start=1):
        print(json.dumps({"index": index, "title": title}, ensure_ascii=False, separators=(",", ":")))
    return 0


def cmd_indexes(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    parsed = package.indexes(component=args.component)
    for name, result in parsed.items():
        rows = result.rows[: args.limit] if args.limit else result.rows
        emit(
            {
                "component": name,
                "leaf_pages": result.leaf_pages,
                "internal_pages": result.internal_pages,
                "rows": [row.to_dict() for row in rows],
                "row_count": len(result.rows),
                "unknown_leaf_bytes": result.unknown_leaf_bytes,
            }
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    results = package.search(args.term, limit=args.limit, profile=args.search_profile, debug=args.debug)
    if args.entries:
        entries = []
        for hit in results.hits:
            try:
                entry = package.entry_for_hit(hit)
            except Exception as exc:
                entries.append({"hit": hit.to_dict(debug=args.debug), "error": str(exc)})
                continue
            entries.append({"hit": hit.to_dict(debug=args.debug), "entry": entry.to_dict(debug=True) if args.debug else {"headword": entry.headword}})
        emit(
            {
                **results.to_dict(debug=args.debug),
                "entries": entries,
            }
        )
        return 0
    emit(results.to_dict(debug=args.debug))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    results = package.search(args.term, limit=args.limit, profile=args.search_profile, debug=args.debug)
    profile = HtmlProfile(args.profile.replace("-", "_"))

    if args.format == "json":
        rendered = []
        for hit in results.hits:
            try:
                entry = package.entry_for_hit(hit)
                record = {
                    "hit": hit.to_dict(debug=args.debug),
                    "headword": entry.headword,
                    "html": package.render_entry_html(entry, profile=profile, include_diagnostics=args.diagnostics),
                    "text": package.render_entry_text(entry),
                    "diagnostics": [diagnostic.to_dict() for diagnostic in entry.diagnostics()] if args.diagnostics or args.debug else [],
                }
                if args.debug:
                    record["address"] = entry.address.to_dict()
                    record["entry"] = entry.to_dict(debug=True)
                rendered.append(record)
            except Exception as exc:
                rendered.append({"hit": hit.to_dict(debug=args.debug), "error": str(exc)})
        emit(
            {
                "query": args.term,
                "profile": profile.value,
                "search_profile": args.search_profile,
                "results": results.to_dict(debug=args.debug),
                "entries": rendered,
            }
        )
        return 0

    if args.format == "text":
        for index, hit in enumerate(results.hits):
            if index:
                print()
            print(package.render_hit_text(hit))
        return 0

    for hit in results.hits:
        print(package.render_hit_html(hit, profile=profile, include_diagnostics=args.diagnostics))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    report = package.validate(sample_entries=args.sample_entries, sample_search_hits=args.sample_search_hits)
    if args.json:
        emit(report)
    else:
        print(f"family: {report['package']['family']}")
        print(f"title: {report['package']['title']}")
        print(f"components: {report['component_count']}")
        print(f"sample entries rendered: {report['sample_entries_rendered']}/{report['sample_entries_checked']}")
        print(f"diagnostics: {report['diagnostics']['by_severity']}")
    return 0 if report.get("ok") else 1


def _corpus_validate_one(path_str: str, sample_entries: int, sample_search_hits: int, debug: bool) -> dict[str, Any]:
    path = Path(path_str)
    try:
        info = detect_family(path)
        if info.family != PackageFamily.SSED:
            family_deferred = info.family in {PackageFamily.LVED, PackageFamily.LVLMULTI}
            return {
                "path": str(path),
                "name": path.name,
                "package_family": info.family.value,
                "ok": True,
                "deferred_family": family_deferred,
                "unsupported_family": info.family == PackageFamily.UNKNOWN,
                "sample_limits": {"sample_entries": sample_entries, "sample_search_hits": sample_search_hits},
                "package": info.to_dict(),
            }
        package = open_package(path)
        report = package.validate(sample_entries=sample_entries, sample_search_hits=sample_search_hits)
        return {
            "path": str(path),
            "name": path.name,
            "package_family": info.family.value,
            "ok": bool(report.get("ok")),
            "deferred_family": False,
            "unsupported_family": False,
            "sample_limits": {"sample_entries": sample_entries, "sample_search_hits": sample_search_hits},
            "package": package.info.to_dict(),
            "body_source": report.get("body_source"),
            "sidecar_resolution": report.get("sidecar_resolution"),
            "sidecar_roles": report.get("sidecar_roles"),
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
    except Exception as exc:  # pragma: no cover - CLI aggregate defensive path
        return {
            "path": str(path),
            "name": path.name,
            "package_family": "error",
            "ok": False,
            "deferred_family": False,
            "unsupported_family": False,
            "sample_limits": {"sample_entries": sample_entries, "sample_search_hits": sample_search_hits},
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
                "path": row.get("path"),
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
    return out


def _blockers(
    rows: list[dict[str, Any]],
    diagnostics_by_code: dict[str, int],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        family = str(row.get("package_family"))
        if not row.get("ok"):
            counts[("package", "validation_failed", family)] = counts.get(("package", "validation_failed", family), 0) + 1
        if family == PackageFamily.SSED.value:
            body_source = row.get("body_source") or {}
            support = str(body_source.get("support") or "")
            kind = str(body_source.get("ssed_kind") or "")
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


def cmd_corpus_validate(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve() if args.output_dir else None
    paths = sorted(
        path
        for path in args.root.iterdir()
        if path.is_dir() and (output_dir is None or path.resolve() != output_dir)
    )
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else max(1, args.jobs)
    sample_entries = args.sample_entries if args.sample_entries is not None else (3 if args.full else 1)
    sample_search_hits = args.sample_search_hits if args.sample_search_hits is not None else (8 if args.full else 2)
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(_corpus_validate_one, str(path), sample_entries, sample_search_hits, args.debug): path
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
    rows.sort(key=lambda item: item["path"])

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
    }
    index_component_type_counts: dict[str, int] = {}
    index_rows_by_component_type: dict[str, int] = {}
    index_unsupported_component_types: dict[str, int] = {}
    index_malformed_leaf_rows = 0
    index_continuation_groups = 0
    index_dangling_continuation_rows = 0
    sidecar_role_counts: dict[str, int] = {}
    supported_sidecar_role_counts: dict[str, int] = {}
    unsupported_sidecar_role_counts: dict[str, int] = {}
    resource_resolution_counts = {
        "unresolved_gaiji": 0,
        "unresolved_media": 0,
        "unresolved_link": 0,
        "resolved_gaiji": 0,
        "resolved_media": 0,
        "resolved_link": 0,
    }
    resource_resolution_by_reason = {
        "unresolved_gaiji": {},
        "unresolved_media": {},
        "unresolved_link": {},
    }
    decode_telemetry_counts = {"unknown_controls": 0, "unknown_bytes": 0}
    title_dereference_counts = {
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
            if kind.startswith("dense_"):
                dense_honmon_count += 1
            if kind in {"renderer_sqlite_sidecar", "dictfulldb_sidecar", "honbun_sidecar", "vlpljbl_sidecar"}:
                renderer_sidecar_like_count += 1
        if support:
            support_counts[support] = support_counts.get(support, 0) + 1
            render_support_counts[support] = render_support_counts.get(support, 0) + 1
        if body_source.get("sidecar_kind") or body_source.get("sidecars"):
            sidecar_backed_count += 1
        diagnostics = row.get("diagnostics") or {}
        for severity, count in (diagnostics.get("by_severity") or {}).items():
            diagnostics_by_severity[severity] = diagnostics_by_severity.get(severity, 0) + int(count)
        for code, count in (diagnostics.get("by_code") or {}).items():
            diagnostics_by_code[code] = diagnostics_by_code.get(code, 0) + int(count)
        for area, count in (diagnostics.get("by_area") or {}).items():
            diagnostics_by_area[area] = diagnostics_by_area.get(area, 0) + int(count)
        for key, count in (row.get("sidecar_resolution") or {}).items():
            sidecar_resolution_counts[key] = sidecar_resolution_counts.get(key, 0) + int(count)
        sidecar_roles = row.get("sidecar_roles") or {}
        for key, count in (sidecar_roles.get("role_counts") or {}).items():
            sidecar_role_counts[key] = sidecar_role_counts.get(key, 0) + int(count)
        for key, count in (sidecar_roles.get("supported_role_counts") or {}).items():
            supported_sidecar_role_counts[key] = supported_sidecar_role_counts.get(key, 0) + int(count)
        for key, count in (sidecar_roles.get("unsupported_role_counts") or {}).items():
            unsupported_sidecar_role_counts[key] = unsupported_sidecar_role_counts.get(key, 0) + int(count)
        index_summary = row.get("index_summary") or {}
        for key, count in (index_summary.get("component_type_counts") or {}).items():
            index_component_type_counts[key] = index_component_type_counts.get(key, 0) + int(count)
        for key, count in (index_summary.get("rows_by_component_type") or {}).items():
            index_rows_by_component_type[key] = index_rows_by_component_type.get(key, 0) + int(count)
        for _name, component_type in (index_summary.get("unsupported_component_types") or {}).items():
            index_unsupported_component_types[component_type] = index_unsupported_component_types.get(component_type, 0) + 1
        index_malformed_leaf_rows += int(index_summary.get("malformed_leaf_rows") or 0)
        index_continuation_groups += int(index_summary.get("continuation_groups") or 0)
        index_dangling_continuation_rows += int(index_summary.get("dangling_continuation_rows") or 0)
        for key, count in (row.get("resource_resolution") or {}).items():
            if isinstance(count, dict):
                reason_bucket = resource_resolution_by_reason.setdefault(key.removesuffix("_by_reason"), {})
                for reason, reason_count in count.items():
                    reason_bucket[reason] = reason_bucket.get(reason, 0) + int(reason_count)
                continue
            resource_resolution_counts[key] = resource_resolution_counts.get(key, 0) + int(count)
        for key, count in (row.get("decode_telemetry") or {}).items():
            decode_telemetry_counts[key] = decode_telemetry_counts.get(key, 0) + int(count)
        title_deref = row.get("title_dereference") or {}
        title_dereference_counts["attempts"] += int(title_deref.get("attempts") or 0)
        title_dereference_counts["resolved"] += int(title_deref.get("resolved") or 0)
        title_dereference_counts["fallback"] += int(title_deref.get("fallback") or 0)
        title_dereference_counts["failed"] += int(title_deref.get("failed") or 0)
        title_dereference_counts["empty"] += int(title_deref.get("empty") or 0)
        title_reasons = title_dereference_counts["by_reason"]
        if not isinstance(title_reasons, dict):
            title_reasons = {}
            title_dereference_counts["by_reason"] = title_reasons
        for reason, count in (title_deref.get("by_reason") or {}).items():
            title_reasons[reason] = title_reasons.get(reason, 0) + int(count)
        title_statuses = title_dereference_counts["title_status_counts"]
        if not isinstance(title_statuses, dict):
            title_statuses = {}
            title_dereference_counts["title_status_counts"] = title_statuses
        for status, count in (title_deref.get("title_status_counts") or {}).items():
            title_statuses[status] = title_statuses.get(status, 0) + int(count)
        heading_sources = title_dereference_counts["heading_source_counts"]
        if not isinstance(heading_sources, dict):
            heading_sources = {}
            title_dereference_counts["heading_source_counts"] = heading_sources
        for source, count in (title_deref.get("heading_source_counts") or {}).items():
            heading_sources[source] = heading_sources.get(source, 0) + int(count)
        render_summary["sample_entries_checked"] += int(row.get("sample_entries_checked") or 0)
        render_summary["sample_entries_rendered"] += int(row.get("sample_entries_rendered") or 0)
        render_summary["sample_index_rows_checked"] += int(row.get("sample_index_rows_checked") or 0)
        render_summary["search_hits_dereferenced"] += int(row.get("sample_search_hits_dereferenced") or 0)
        render_summary["search_hits_rendered_html"] += int(row.get("sample_search_hits_rendered_html") or 0)
        render_summary["search_hits_rendered_text"] += int(row.get("sample_search_hits_rendered_text") or 0)

    diagnostics_rows = _diagnostic_rows(rows)
    output_files: dict[str, str] = {}
    summary = {
        "schema": CORPUS_VALIDATE_SCHEMA,
        "root": str(args.root),
        "sample_limits": {
            "full": bool(args.full),
            "sample_entries": sample_entries,
            "sample_search_hits": sample_search_hits,
            "jobs": jobs,
        },
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
            "continuation_groups": index_continuation_groups,
            "dangling_continuation_rows": index_dangling_continuation_rows,
        },
        "sidecar_role_counts": sidecar_role_counts,
        "supported_sidecar_role_counts": supported_sidecar_role_counts,
        "unsupported_sidecar_role_counts": unsupported_sidecar_role_counts,
        "resource_resolution_counts": resource_resolution_counts,
        "resource_resolution_by_reason": resource_resolution_by_reason,
        "decode_telemetry_counts": decode_telemetry_counts,
        "title_dereference_counts": title_dereference_counts,
        "failure_count": len(failures),
        "diagnostics": {
            "by_severity": diagnostics_by_severity,
            "by_area": diagnostics_by_area,
            "by_code": diagnostics_by_code,
        },
        "top_diagnostics_by_severity": _top_counts(diagnostics_by_severity),
        "top_diagnostics_by_code": _top_counts(diagnostics_by_code),
        "top_diagnostics_by_area": _top_counts(diagnostics_by_area),
        "top_blockers": _blockers(rows, diagnostics_by_code),
        "targets": rows,
    }
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
            "summary_json": str(summary_path),
            "targets_jsonl": str(targets_path),
            "failures_jsonl": str(failures_path),
            "diagnostics_jsonl": str(diagnostics_path),
        }
    else:
        if args.failures_jsonl:
            _write_jsonl(args.failures_jsonl, failures)
            output_files["failures_jsonl"] = str(args.failures_jsonl)
        if args.diagnostics_jsonl:
            _write_jsonl(args.diagnostics_jsonl, diagnostics_rows)
            output_files["diagnostics_jsonl"] = str(args.diagnostics_jsonl)
    if output_files:
        summary["output_files"] = output_files
        if args.output_dir:
            _write_json(args.output_dir / "summary.json", summary)
    emit(summary)
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lvcore", description="Experimental LogoVista reader core")
    sub = parser.add_subparsers(dest="command", required=True)

    p_identify = sub.add_parser("identify", help="Detect package family")
    p_identify.add_argument("path", type=Path)
    p_identify.set_defaults(func=cmd_identify)

    p_info = sub.add_parser("info", help="Show parsed package summary")
    p_info.add_argument("path", type=Path)
    p_info.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_info.set_defaults(func=cmd_info)

    p_body = sub.add_parser("body-source", help="Classify the package body source")
    p_body.add_argument("path", type=Path)
    p_body.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_body.add_argument("--debug", action="store_true", help="Include full sidecar paths and debug details")
    p_body.set_defaults(func=cmd_body_source)

    p_entries = sub.add_parser("entries", help="Emit HONMON body-stream entries as JSONL")
    p_entries.add_argument("path", type=Path)
    p_entries.add_argument("--limit", type=int, default=10)
    p_entries.add_argument("--spans", action="store_true")
    p_entries.set_defaults(func=cmd_entries)

    p_titles = sub.add_parser("titles", help="Emit title rows")
    p_titles.add_argument("path", type=Path)
    p_titles.add_argument("--component")
    p_titles.add_argument("--limit", type=int, default=20)
    p_titles.set_defaults(func=cmd_titles)

    p_indexes = sub.add_parser("indexes", help="Parse index rows")
    p_indexes.add_argument("path", type=Path)
    p_indexes.add_argument("--component")
    p_indexes.add_argument("--limit", type=int, default=20)
    p_indexes.set_defaults(func=cmd_indexes)

    p_search = sub.add_parser("search", help="Native exact/forward/backward search through parsed index rows")
    p_search.add_argument("path", type=Path)
    p_search.add_argument("term")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--search-profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    p_search.add_argument("--entries", action="store_true", help="Dereference body pointers and return entries")
    p_search.add_argument("--json", action="store_true", help="Emit JSON search results (default)")
    p_search.add_argument("--debug", action="store_true", help="Include raw index pointers and row details")
    p_search.set_defaults(func=cmd_search)

    p_render = sub.add_parser("render", help="Search and render entries through the document renderer")
    p_render.add_argument("path", type=Path)
    p_render.add_argument("term")
    p_render.add_argument("--limit", type=int, default=3)
    p_render.add_argument("--search-profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    p_render.add_argument("--profile", choices=[*(profile.value for profile in HtmlProfile), "logovista-like"], default=HtmlProfile.FRIENDLY.value)
    p_render.add_argument("--format", choices=["html", "text", "json"], default="html")
    p_render.add_argument("--diagnostics", action="store_true", help="Include diagnostics in rendered output")
    p_render.add_argument("--debug", action="store_true", help="Include debug hit details in JSON output")
    p_render.set_defaults(func=cmd_render)

    p_validate = sub.add_parser("validate", help="Validate reader-side open/search/decode/render safety")
    p_validate.add_argument("path", type=Path)
    p_validate.add_argument("--sample-entries", type=int, default=3)
    p_validate.add_argument("--sample-search-hits", type=int, default=5)
    p_validate.add_argument("--json", action="store_true")
    p_validate.add_argument("--debug", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_corpus = sub.add_parser("corpus-validate", help="Validate a directory of LogoVista package roots")
    p_corpus.add_argument("root", type=Path)
    p_corpus.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_corpus.add_argument("--full", action="store_true", help="Use larger reader samples")
    p_corpus.add_argument("--debug", action="store_true")
    p_corpus.add_argument("--output-dir", type=Path, help="Write summary.json, targets.jsonl, failures.jsonl, and diagnostics.jsonl")
    p_corpus.add_argument("--failures-jsonl", type=Path, help="Write failing package rows as JSONL")
    p_corpus.add_argument("--diagnostics-jsonl", type=Path, help="Write per-package diagnostic aggregates as JSONL")
    p_corpus.add_argument("--sample-entries", type=int, help="Number of marker-discovered entries to render per SSED package")
    p_corpus.add_argument("--sample-search-hits", type=int, help="Number of native index rows/search hits to dereference and render per SSED package")
    p_corpus.add_argument("--jobs", type=int, default=0, help="Worker count, 0 means all available CPUs")
    p_corpus.add_argument("--progress", action="store_true")
    p_corpus.set_defaults(func=cmd_corpus_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UnsupportedPackageError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
