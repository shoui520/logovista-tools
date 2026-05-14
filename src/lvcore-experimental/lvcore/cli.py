"""lvcore command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback
from typing import Any

from .detect import detect_family
from .errors import UnsupportedPackageError
from .model import ComponentRole, PackageFamily, SearchProfile
from .package import open_package
from .render import HtmlProfile



def emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _extract_verbose(argv: list[str] | None) -> tuple[list[str] | None, bool]:
    raw = list(sys.argv[1:] if argv is None else argv)
    filtered: list[str] = []
    verbose = False
    literal_args = False
    for item in raw:
        if literal_args:
            filtered.append(item)
        elif item == "--":
            literal_args = True
            filtered.append(item)
        elif item == "--verbose":
            verbose = True
        else:
            filtered.append(item)
    return filtered, verbose


def _is_verbose(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "verbose", False) or getattr(args, "debug", False))


def _status(args: argparse.Namespace, message: str, *, verbose: bool = False) -> None:
    if verbose and not _is_verbose(args):
        return
    print(message, file=sys.stderr, flush=True)


def _path_display(path: object) -> str:
    return str(path) if path is not None else "<unknown path>"


def _friendly_exception_message(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"file not found: {_path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, IsADirectoryError):
        return f"expected a file but got a directory: {_path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, NotADirectoryError):
        return f"expected a directory in this path: {_path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, PermissionError):
        return f"permission denied: {_path_display(getattr(exc, 'filename', None) or exc)}"
    return str(exc) or f"{type(exc).__name__}"


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
    emit(package.summary(debug=args.debug))
    return 0


def cmd_body_source(args: argparse.Namespace) -> int:
    info = detect_family(args.path)
    if info.family != PackageFamily.SSED:
        emit({"package": info.to_dict(), "body_source": {"package_family": info.family.value, "support": "deferred"}})
        return 0
    package = open_package(args.path)
    report = {"package": package.info.to_dict(), "body_source": package.body_source(debug=args.debug).to_dict(debug=args.debug)}
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
    if args.limit and not args.debug:
        components = (
            [component]
            if args.component and (component := package.component(args.component)) is not None
            else list(package.components_by_role(ComponentRole.INDEX))
        )
        for component in components:
            rows = []
            for row in package.iter_index_rows(component):
                rows.append(row)
                if len(rows) >= args.limit:
                    break
            emit(
                {
                    "component": component.name,
                    "component_type": f"{component.type:02x}",
                    "rows": [row.to_dict() for row in rows],
                    "row_count": None,
                    "rows_seen": len(rows),
                    "rows_complete": False,
                    "leaf_pages": None,
                    "internal_pages": None,
                    "unknown_leaf_bytes": None,
                    "physical_tail_bytes": None,
                    "physical_tail_nonzero_bytes": None,
                    "notes": ["fast limited index stream; use --debug or --limit 0 for complete index diagnostics"],
                }
            )
        return 0
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
                "physical_tail_bytes": result.physical_tail_bytes,
                "physical_tail_nonzero_bytes": result.physical_tail_nonzero_bytes,
            }
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    _status(args, f"lvcore: searching {args.path} term={args.term!r} profile={args.search_profile} limit={args.limit}", verbose=True)
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
    _status(args, f"lvcore: rendering {args.path} term={args.term!r} format={args.format} limit={args.limit}", verbose=True)
    package = open_package(args.path)
    results = package.search(args.term, limit=args.limit, profile=args.search_profile, debug=args.debug)
    profile_name = args.profile.replace("-", "_")
    profile: HtmlProfile | str = "debug" if profile_name == "debug" else HtmlProfile(profile_name)
    profile_value = profile if isinstance(profile, str) else profile.value

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
                "profile": profile_value,
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
        print(
            package.render_hit_html(
                hit,
                profile=profile,
                include_diagnostics=args.diagnostics,
            )
        )
    return 0


def _resources_for_query(
    package,
    term: str,
    *,
    profile: str,
    limit: int,
    debug: bool,
    include_sidecar: bool = False,
    include_gaiji: bool = False,
) -> list[dict[str, Any]]:
    results = package.search(term, limit=limit, profile=profile, debug=debug)
    rows: list[dict[str, Any]] = []
    for hit_index, hit in enumerate(results.hits):
        entry = package.entry_for_hit(hit)
        document = entry.document()
        for resource in document.resources:
            row = {
                "hit_index": hit_index,
                "hit": hit.to_dict(debug=debug),
                "resource": resource.to_dict(debug=debug),
                "info": package.resource_info(resource),
            }
            rows.append(row)
    if include_sidecar:
        for resource in package.sidecar_media_resources(limit=limit):
            rows.append(
                {
                    "hit_index": None,
                    "hit": None,
                    "resource": resource.to_dict(debug=debug),
                    "info": package.resource_info(resource),
                }
            )
    if include_gaiji:
        for resource in package.gaiji_resources(limit=limit):
            rows.append(
                {
                    "hit_index": None,
                    "hit": None,
                    "resource": resource.to_dict(debug=debug),
                    "info": package.resource_info(resource),
                }
            )
    return rows


def _find_resource_for_query(package, term: str, resource_id: str, *, profile: str, limit: int, debug: bool):
    for row in _resources_for_query(package, term, profile=profile, limit=limit, debug=debug, include_sidecar=True, include_gaiji=True):
        resource = row.get("resource") if isinstance(row.get("resource"), dict) else {}
        if resource.get("id") == resource_id:
            return row
    return None


def cmd_resources(args: argparse.Namespace) -> int:
    _status(args, f"lvcore: resolving resources for {args.path} term={args.term!r}", verbose=True)
    package = open_package(args.path)
    rows = _resources_for_query(
        package,
        args.term,
        profile=args.search_profile,
        limit=args.limit,
        debug=args.debug,
        include_sidecar=args.include_sidecar,
        include_gaiji=args.include_gaiji,
    )
    emit({"query": args.term, "search_profile": args.search_profile, "resources": rows})
    return 0


def cmd_gaiji(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    gaiji_summary = {
        "records": len(package.gaiji.records),
        "mapped": len(package.gaiji.mapping),
        "image_resources": len(package.gaiji_images),
        "plist_unicode_mappings": package.gaiji.plist_unicode_mappings,
        "plist_mapping_ambiguous": package.gaiji.plist_mapping_ambiguous,
        "plist_parse_failures": package.gaiji.plist_parse_failures,
    }
    if args.debug:
        gaiji_summary["paths"] = [str(path) for path in package.gaiji.paths]
        gaiji_summary["ga16"] = [
            {
                "path": str(resource.path),
                "width": resource.width,
                "height": resource.height,
                "start_code": f"{resource.start_code:04x}",
                "count": resource.count,
                "glyph_bytes": resource.glyph_bytes,
                "section": resource.section,
            }
            for resource in package.ga16
        ]
    else:
        gaiji_summary["ga16_resources"] = len(package.ga16)
    resources = tuple(package.gaiji_resources(limit=args.limit))
    resource_rows = [resource.to_dict(debug=args.debug) for resource in resources]
    rows = [
        {
            "resource": resource.to_dict(debug=args.debug),
            "info": package.resource_info(resource),
        }
        for resource in resources
    ]
    emit(
        {
            "package": package.info.to_dict(),
            "gaiji": gaiji_summary,
            "resources": rows if args.debug else resource_rows,
        }
    )
    return 0


def cmd_sidecars(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    sidecars = [sidecar.to_dict(debug=args.debug) for sidecar in package.sidecars()]
    media_resources = [resource.to_dict(debug=args.debug) for resource in package.sidecar_media_resources(limit=args.limit)]
    emit(
        {
            "package": package.info.to_dict(),
            "sidecars": sidecars,
            "sidecar_media_resources": media_resources,
        }
    )
    return 0


def cmd_resource_info(args: argparse.Namespace) -> int:
    package = open_package(args.path)
    row = _find_resource_for_query(package, args.term, args.resource_id, profile=args.search_profile, limit=args.limit, debug=args.debug)
    if row is None:
        emit({"ok": False, "error": "resource_not_found", "resource_id": args.resource_id})
        return 1
    emit({"ok": True, **row})
    return 0


def cmd_resource_bytes(args: argparse.Namespace) -> int:
    _status(args, f"lvcore: writing resource bytes to {args.output}", verbose=True)
    package = open_package(args.path)
    row = _find_resource_for_query(package, args.term, args.resource_id, profile=args.search_profile, limit=args.limit, debug=True)
    if row is None:
        emit({"ok": False, "error": "resource_not_found", "resource_id": args.resource_id})
        return 1
    resource = row.get("resource") if isinstance(row.get("resource"), dict) else {}
    payload = package.resource_bytes(resource)
    if payload is None:
        emit({"ok": False, "error": "resource_bytes_unavailable", "resource_id": args.resource_id, "info": row.get("info")})
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    emit({"ok": True, "resource_id": args.resource_id, "output": str(args.output), "byte_length": len(payload), "info": row.get("info")})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lvcore", description="Experimental LogoVista reader core")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show extra progress details and Python tracebacks for unexpected errors.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_identify = sub.add_parser("identify", help="Detect package family")
    p_identify.add_argument("path", type=Path)
    p_identify.set_defaults(func=cmd_identify)

    p_info = sub.add_parser("info", help="Show parsed package summary")
    p_info.add_argument("path", type=Path)
    p_info.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_info.add_argument("--debug", action="store_true", help="Include body-source, gaiji, and resource evidence")
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
    p_indexes.add_argument("--debug", action="store_true", help="Parse complete indexes and include complete diagnostics")
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
    p_render.add_argument("--profile", choices=[*(profile.value for profile in HtmlProfile), "logovista-like", "debug"], default=HtmlProfile.FRIENDLY.value)
    p_render.add_argument("--format", choices=["html", "text", "json"], default="html")
    p_render.add_argument("--diagnostics", action="store_true", help="Include diagnostics in rendered output")
    p_render.add_argument("--debug", action="store_true", help="Include debug hit details in JSON output")
    p_render.set_defaults(func=cmd_render)

    p_resources = sub.add_parser("resources", help="Search an entry and list app-facing resources")
    p_resources.add_argument("path", type=Path)
    p_resources.add_argument("term")
    p_resources.add_argument("--limit", type=int, default=3)
    p_resources.add_argument("--search-profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    p_resources.add_argument("--include-sidecar", action="store_true", help="Also list package-level sidecar media resources")
    p_resources.add_argument("--include-gaiji", action="store_true", help="Also list package-level gaiji mappings/resources")
    p_resources.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_resources.add_argument("--debug", action="store_true", help="Include decoded resource details")
    p_resources.set_defaults(func=cmd_resources)

    p_gaiji = sub.add_parser("gaiji", help="Inspect package gaiji display mappings and resource readiness")
    p_gaiji.add_argument("path", type=Path)
    p_gaiji.add_argument("--limit", type=int, default=50)
    p_gaiji.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_gaiji.add_argument("--debug", action="store_true", help="Include gaiji source/resource details")
    p_gaiji.set_defaults(func=cmd_gaiji)

    p_sidecars = sub.add_parser("sidecars", help="Inspect package sidecar roles and supported sidecar resources")
    p_sidecars.add_argument("path", type=Path)
    p_sidecars.add_argument("--limit", type=int, default=20)
    p_sidecars.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_sidecars.add_argument("--debug", action="store_true", help="Include sidecar schema details")
    p_sidecars.set_defaults(func=cmd_sidecars)

    p_resource_info = sub.add_parser("resource-info", help="Resolve metadata for a resource found by search")
    p_resource_info.add_argument("path", type=Path)
    p_resource_info.add_argument("term")
    p_resource_info.add_argument("resource_id")
    p_resource_info.add_argument("--limit", type=int, default=3)
    p_resource_info.add_argument("--search-profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    p_resource_info.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    p_resource_info.add_argument("--debug", action="store_true", help="Include decoded resource details")
    p_resource_info.set_defaults(func=cmd_resource_info)

    p_resource_bytes = sub.add_parser("resource-bytes", help="Write untouched resolved resource payload bytes")
    p_resource_bytes.add_argument("path", type=Path)
    p_resource_bytes.add_argument("term")
    p_resource_bytes.add_argument("resource_id")
    p_resource_bytes.add_argument("--output", type=Path, required=True, help="Destination file for original resource bytes")
    p_resource_bytes.add_argument("--limit", type=int, default=3)
    p_resource_bytes.add_argument("--search-profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    p_resource_bytes.set_defaults(func=cmd_resource_bytes)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    filtered_argv, verbose = _extract_verbose(argv)
    args = parser.parse_args(filtered_argv)
    args.verbose = bool(getattr(args, "verbose", False) or verbose)
    command = str(getattr(args, "command", "command"))
    _status(args, f"lvcore: running {command}")
    try:
        for attr in ("path", "root"):
            maybe_path = getattr(args, attr, None)
            if isinstance(maybe_path, Path) and not maybe_path.exists():
                raise FileNotFoundError(maybe_path)
        result = int(args.func(args))
    except UnsupportedPackageError as exc:
        print(f"lvcore: error: {exc}", file=sys.stderr)
        if _is_verbose(args):
            traceback.print_exc()
        return 2
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        print("lvcore: interrupted", file=sys.stderr)
        return 130
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError, ValueError) as exc:
        print(f"lvcore: error: {_friendly_exception_message(exc)}", file=sys.stderr)
        if _is_verbose(args):
            traceback.print_exc()
        return 2
    except Exception as exc:
        print(f"lvcore: error: {_friendly_exception_message(exc)}", file=sys.stderr)
        if _is_verbose(args):
            traceback.print_exc()
        else:
            print("lvcore: rerun with --verbose for a Python traceback", file=sys.stderr)
        return 2
    if result == 0:
        _status(args, f"lvcore: completed {command}")
    else:
        _status(args, f"lvcore: {command} exited with status {result}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
