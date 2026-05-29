"""CLI helpers for LogoVista exporters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .epwing_v6 import EpwingV6Backend, EpwingV6Options
from .model import SourceFamily
from .sources import ExportSourceOptions, load_export_book


def add_export_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", choices=("epwing-v6",), help="Exporter target format.")
    parser.add_argument("source", type=Path, help="LogoVista package, direct .IDX, or LVED_SQLITE3 payload.")
    parser.add_argument("out_dir", type=Path, help="Output directory for the exported dictionary.")
    parser.add_argument("--source-family", choices=("auto", "ssed", "lved-sqlite3"), default="auto")
    parser.add_argument("--gaiji-policy", default="unicode-map,ga16")
    parser.add_argument("--include-media", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--drop-style-markers", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--diagnostics-json", type=Path)
    parser.add_argument("--limit", type=int, help="Development/smoke limit for entries; omitted exports all rows.")
    parser.add_argument("--jobs", type=int, default=1, help="Reserved for future parallel export stages.")
    parser.add_argument("--json", action="store_true", help="Print the export report as JSON.")


def run_export(args: argparse.Namespace) -> dict:
    if args.target != "epwing-v6":
        raise ValueError(f"unsupported export target: {args.target}")
    book = load_export_book(
        args.source,
        ExportSourceOptions(source_family=SourceFamily(args.source_family), limit=args.limit),
    )
    report = EpwingV6Backend(
        EpwingV6Options(
            include_media=args.include_media,
            drop_style_markers=args.drop_style_markers,
            gaiji_policy=args.gaiji_policy,
        )
    ).write(book, args.out_dir)
    data = report.as_dict()
    if args.diagnostics_json is not None:
        args.diagnostics_json.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
