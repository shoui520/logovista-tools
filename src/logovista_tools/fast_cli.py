"""Small front controller for latency-sensitive logovista-tools commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def _entries_task(payload):
    from .entries import extract_dictionary

    source, out_dir, args = payload
    return extract_dictionary(source, out_dir, args)


def _cmd_info(argv: list[str]) -> int:
    from .ssed import parse_sseddata_header, parse_ssedinfo_with_layout

    parser = argparse.ArgumentParser(prog="logovista-tools info", description="Inspect an SSEDINFO .IDX or SSEDDATA .DIC file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--all", action="store_true", help="Show zero-start/resource components too.")
    parser.add_argument(
        "--try-decrypt",
        action="store_true",
        help="For unknown raw files, attempt encrypted SSEDDATA detection. Slow forensic fallback.",
    )
    args = parser.parse_args(argv)

    data = args.path.read_bytes()[:8]
    if data == b"SSEDINFO":
        title, elements, layout = parse_ssedinfo_with_layout(args.path)
        print(f"title: {title}")
        print(f"elements: {len(elements)}")
        print(
            f"layout: count_offset={layout.component_count_offset:#x} "
            f"record_start={layout.record_start:#x} trailing_bytes={layout.trailing_bytes}"
        )
        for element in elements:
            if args.all or element.start:
                print(
                    f"{element.index:02d} {element.filename:16s} "
                    f"multi={element.multi:02x} type={element.type:02x} "
                    f"start={element.start:#x} end={element.end:#x} "
                    f"blocks={element.block_count:#x} data={element.data.hex()}"
                )
        return 0

    if data == b"SSEDDATA" or args.try_decrypt:
        try:
            header = parse_sseddata_header(args.path)
        except ValueError:
            header = None
        if header is not None:
            print(
                f"chunks={header['n_chunk']} start={header['start_block']:#x} "
                f"end={header['end_block']:#x} kind={header['kind']:#x} "
                f"storage={header['storage']}"
            )
            return 0

    print(f"unknown raw file type: {args.path}", file=sys.stderr)
    return 1


def _cmd_entries(argv: list[str]) -> int:
    from .entries import discover_dictionaries, write_json
    from .parallel import add_jobs_argument, parallel_map_ordered, worker_args

    parser = argparse.ArgumentParser(prog="logovista-tools entries", description="Extract readable HONMON body entries as JSONL.")
    parser.add_argument("root", type=Path, nargs="*", help="Collection directory or direct .IDX path.")
    parser.add_argument("--out-dir", type=Path, default=Path("logovista-raw-extract"))
    parser.add_argument("--limit", type=int, help="Limit entries per dictionary for smoke tests.")
    parser.add_argument("--min-chars", type=int, default=1)
    parser.add_argument("--gaiji", choices=("drop", "h-placeholder", "placeholder"), default="h-placeholder")
    parser.add_argument("--image-gaiji", action="store_true")
    parser.add_argument("--media-placeholder", action="store_true")
    parser.add_argument("--section-markers", action="store_true")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--section-image", action="append")
    parser.add_argument("--no-skip-dense-marker-honmon", dest="skip_dense_marker_honmon", action="store_false")
    parser.add_argument("--index-boundaries", dest="index_boundaries", action="store_true")
    parser.add_argument("--no-index-boundaries", dest="index_boundaries", action="store_false")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dict", action="append", help="Only extract matching dictionary id(s).")
    add_jobs_argument(parser)
    parser.set_defaults(skip_dense_marker_honmon=True, index_boundaries=False, debug=False)
    args = parser.parse_args(argv)

    sources = discover_dictionaries(args.root or [Path(".")], jobs=args.jobs)
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    def log_summary(summary: dict[str, object]) -> None:
        print(
            f"{str(summary['dict_id']):12s} entries={summary['entries_emitted']} "
            f"markers={summary['entry_markers']} "
            f"bytes={summary['expanded_bytes']}",
            file=sys.stderr,
        )

    task_args = worker_args(args)
    summaries = parallel_map_ordered(
        _entries_task,
        [(source, args.out_dir, task_args) for source in sources],
        jobs=args.jobs,
        on_result=log_summary,
    )
    write_json(args.out_dir / "summary.json", summaries)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        from .cli import main as full_main

        return full_main(args)
    if args[0] in {"-h", "--help"}:
        from .cli import main as full_main

        return full_main(args)
    if args[0] == "--version":
        print(f"logovista-tools {__version__}")
        return 0
    if args[0] == "info":
        return _cmd_info(args[1:])
    if args[0] == "entries":
        return _cmd_entries(args[1:])

    from .cli import main as full_main

    return full_main(args)
