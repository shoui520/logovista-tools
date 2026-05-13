"""Lightweight shared argparse builders for latency-sensitive commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from .parallel import add_jobs_argument


def add_info_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", type=Path)
    parser.add_argument("--all", action="store_true", help="Show zero-start/resource components too.")
    parser.add_argument(
        "--try-decrypt",
        action="store_true",
        help="For unknown raw files, attempt encrypted SSEDDATA detection. Slow forensic fallback.",
    )


def add_entries_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-extract"))
    parser.add_argument("--limit", type=int, help="Limit entries per dictionary for smoke tests.")
    parser.add_argument("--min-chars", type=int, default=1)
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument(
        "--image-gaiji",
        action="store_true",
        help="Preserve unresolved gaiji that have PNG assets as <img:code> placeholders.",
    )
    parser.add_argument(
        "--media-placeholder",
        action="store_true",
        help="Preserve 1f4d media controls as <media:payload-hex> placeholders.",
    )
    parser.add_argument(
        "--section-markers",
        action="store_true",
        help="Preserve 1f09 section markers as <section:xxxx> placeholders.",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Also emit body_html with conservative inline HTML and img tags for image gaiji.",
    )
    parser.add_argument(
        "--section-image",
        action="append",
        help="For HTML output, insert an image at a section marker. Format: CODE=IMAGE_KEY, e.g. 0011=exam.",
    )
    parser.add_argument(
        "--no-skip-dense-marker-honmon",
        dest="skip_dense_marker_honmon",
        action="store_false",
        help="Attempt extraction even when HONMON looks like an anchor/id table.",
    )
    parser.add_argument(
        "--index-boundaries",
        dest="index_boundaries",
        action="store_true",
        help="Add raw index body pointers as extra entry boundaries. This is a slower forensic path.",
    )
    parser.add_argument(
        "--no-index-boundaries",
        dest="index_boundaries",
        action="store_false",
        help="Compatibility no-op: index boundaries are disabled by default in the fast path.",
    )
    parser.add_argument(
        "--full-scan",
        dest="full_scan",
        action="store_true",
        help="Use full HONMON expansion and forensic boundary accounting.",
    )
    parser.add_argument(
        "--debug",
        dest="full_scan",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    add_jobs_argument(parser)
    parser.set_defaults(skip_dense_marker_honmon=True, index_boundaries=False, full_scan=False)


def add_titles_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-titles"))
    parser.add_argument("--limit", type=int, help="Limit emitted title lines per component.")
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    add_jobs_argument(parser)


def add_indexes_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-indexes"))
    parser.add_argument("--limit", type=int, help="Limit emitted index rows per run.")
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    parser.add_argument("--component", action="append", help="Only extract matching component filename(s).")
    parser.add_argument(
        "--include-internal",
        action="store_true",
        help="Also emit binary-search tree internal rows, not only leaf search records.",
    )
    add_jobs_argument(parser)


def add_menus_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-menus"))
    parser.add_argument("--limit", type=int, help="Limit emitted menu lines per component.")
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    add_jobs_argument(parser)


def add_colscr_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-colscr"))
    parser.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    parser.add_argument("--limit", type=int, help="Limit media references per dictionary.")
    parser.add_argument(
        "--write-media",
        "--write-bmp",
        dest="write_media",
        action="store_true",
        help="Write referenced image files next to the manifest.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    add_jobs_argument(parser)


def add_pcmdata_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-pcmdata"))
    parser.add_argument("--dict", action="append", help="Only inspect matching dictionary id(s).")
    parser.add_argument("--limit", type=int, help="Limit HONMON audio references per dictionary.")
    parser.add_argument(
        "--write-audio",
        action="store_true",
        help="Write portable audio files next to the manifest.",
    )
    parser.add_argument(
        "--no-include-unreferenced",
        dest="include_unreferenced",
        action="store_false",
        help="Do not scan unreferenced records in PCMDATA gaps.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary.")
    add_jobs_argument(parser)
    parser.set_defaults(include_unreferenced=True)
