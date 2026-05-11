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


def emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


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
        record = entry.to_dict() if args.spans else {
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
            entries.append({"hit": hit.to_dict(debug=args.debug), "entry": entry.to_dict() if args.debug else {"headword": entry.headword}})
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
                rendered.append(
                    {
                        "hit": hit.to_dict(debug=args.debug),
                        "address": entry.address.to_dict() if args.debug else None,
                        "headword": entry.headword,
                        "html": package.render_entry_html(entry, profile=profile, include_diagnostics=args.diagnostics),
                        "text": package.render_entry_text(entry),
                        "diagnostics": [diagnostic.to_dict() for diagnostic in entry.diagnostics()] if args.diagnostics or args.debug else [],
                    }
                )
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
            return {
                "path": str(path),
                "name": path.name,
                "package_family": info.family.value,
                "ok": True,
                "deferred_family": True,
                "package": info.to_dict(),
            }
        package = open_package(path)
        report = package.validate(sample_entries=sample_entries, sample_search_hits=sample_search_hits)
        return {
            "path": str(path),
            "name": path.name,
            "package_family": info.family.value,
            "ok": bool(report.get("ok")),
            "package": package.info.to_dict(),
            "body_source": report.get("body_source"),
            "sidecar_resolution": report.get("sidecar_resolution"),
            "component_count": report.get("component_count"),
            "gaiji": report.get("gaiji"),
            "indexes": report.get("indexes"),
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
            "error": f"{type(exc).__name__}: {exc}",
        }


def cmd_corpus_validate(args: argparse.Namespace) -> int:
    paths = sorted(path for path in args.root.iterdir() if path.is_dir())
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else max(1, args.jobs)
    sample_entries = 3 if args.full else 1
    sample_search_hits = 8 if args.full else 2
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
                print(f"progress {done}/{len(paths)}", file=sys.stderr, flush=True)
    rows.sort(key=lambda item: item["path"])

    family_counts: dict[str, int] = {}
    body_kind_counts: dict[str, int] = {}
    support_counts: dict[str, int] = {}
    diagnostics_by_code: dict[str, int] = {}
    diagnostics_by_area: dict[str, int] = {}
    sidecar_resolution_counts = {
        "resolved": 0,
        "missing_anchor_id": 0,
        "missing_row": 0,
        "unsupported_body_source": 0,
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
    failures = []
    for row in rows:
        family = str(row.get("package_family"))
        family_counts[family] = family_counts.get(family, 0) + 1
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
        if body_source.get("sidecar_kind") or body_source.get("sidecars"):
            sidecar_backed_count += 1
        diagnostics = row.get("diagnostics") or {}
        for code, count in (diagnostics.get("by_code") or {}).items():
            diagnostics_by_code[code] = diagnostics_by_code.get(code, 0) + int(count)
        for area, count in (diagnostics.get("by_area") or {}).items():
            diagnostics_by_area[area] = diagnostics_by_area.get(area, 0) + int(count)
        for key, count in (row.get("sidecar_resolution") or {}).items():
            sidecar_resolution_counts[key] = sidecar_resolution_counts.get(key, 0) + int(count)
        render_summary["sample_entries_checked"] += int(row.get("sample_entries_checked") or 0)
        render_summary["sample_entries_rendered"] += int(row.get("sample_entries_rendered") or 0)
        render_summary["sample_index_rows_checked"] += int(row.get("sample_index_rows_checked") or 0)
        render_summary["search_hits_dereferenced"] += int(row.get("sample_search_hits_dereferenced") or 0)
        render_summary["search_hits_rendered_html"] += int(row.get("sample_search_hits_rendered_html") or 0)
        render_summary["search_hits_rendered_text"] += int(row.get("sample_search_hits_rendered_text") or 0)

    summary = {
        "root": str(args.root),
        "total_packages": len(rows),
        "family_counts": family_counts,
        "ssed_body_source_kind_counts": body_kind_counts,
        "ssed_body_source_support_counts": support_counts,
        "ssed_renderable_count": support_counts.get("renderable", 0),
        "ssed_deferred_count": support_counts.get("deferred", 0),
        "ssed_unsupported_or_unknown_count": support_counts.get("unsupported", 0) + support_counts.get("unknown", 0),
        "dense_honmon_count": dense_honmon_count,
        "sidecar_backed_count": sidecar_backed_count,
        "renderer_sidecar_like_count": renderer_sidecar_like_count,
        "open_failure_count": family_counts.get("error", 0),
        "render_summary": render_summary,
        "sidecar_resolution_counts": sidecar_resolution_counts,
        "failure_count": len(failures),
        "top_diagnostics_by_code": dict(sorted(diagnostics_by_code.items(), key=lambda item: (-item[1], item[0]))[:30]),
        "top_diagnostics_by_area": dict(sorted(diagnostics_by_area.items(), key=lambda item: (-item[1], item[0]))[:30]),
        "targets": rows,
    }
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
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
