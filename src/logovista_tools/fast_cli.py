"""Small front controller for latency-sensitive logovista-tools commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .cli_ux import extract_verbose, run_callback_with_friendly_errors, status


def _entries_task(payload):
    from .entries import extract_dictionary

    source, out_dir, args = payload
    return extract_dictionary(source, out_dir, args)


def _titles_task(payload):
    from .titles import extract_titles_for_idx

    source, out_dir, args = payload
    return extract_titles_for_idx(source.idx, out_dir, args)


def _indexes_task(payload):
    from .indexes import extract_indexes_for_idx

    source, out_dir, args = payload
    return extract_indexes_for_idx(source.idx, out_dir, args)


def _menus_task(payload):
    from .menus import extract_menus_for_idx

    source, out_dir, args = payload
    return extract_menus_for_idx(source.idx, out_dir, args)


def _colscr_task(payload):
    from .colscr import extract_colscr_for_source

    source, out_dir, args = payload
    return extract_colscr_for_source(source, out_dir, args)


def _pcmdata_task(payload):
    from .pcmdata import extract_pcmdata_for_source

    source, out_dir, args = payload
    return extract_pcmdata_for_source(source, out_dir, args)


def _cmd_info(argv: list[str]) -> int:
    from .cli_args import add_info_args
    from .ssed import parse_sseddata_header, parse_ssedinfo_with_layout, read_file_prefix

    parser = argparse.ArgumentParser(prog="logovista-tools info", description="Inspect an SSEDINFO .IDX or SSEDDATA .DIC file.")
    parser.add_argument("--verbose", action="store_true", help="Show extra progress details.")
    add_info_args(parser)
    args = parser.parse_args(argv)

    status(args, f"info: reading {args.path}", verbose=True)
    data = read_file_prefix(args.path, 8)
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
    from .cli_args import add_entries_args
    from .entries import discover_dictionaries, entry_marker_status_text, write_json
    from .parallel import parallel_map_ordered, worker_args

    parser = argparse.ArgumentParser(prog="logovista-tools entries", description="Extract readable HONMON body entries as JSONL.")
    parser.add_argument("--verbose", action="store_true", help="Show extra progress details.")
    add_entries_args(parser)
    args = parser.parse_args(argv)

    roots = args.root or [Path(".")]
    for root in roots:
        if not root.exists():
            raise FileNotFoundError(root)
    status(args, f"entries: discovering dictionaries under {', '.join(str(root) for root in roots)}")
    include_images = bool(args.image_gaiji or args.html or args.section_image)
    sources = discover_dictionaries(
        args.root or [Path(".")],
        jobs=args.jobs,
        dict_ids=args.dict,
        include_images=include_images,
    )
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1
    status(args, f"entries: found {len(sources)} dictionary package(s)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    status(args, f"entries: writing output under {args.out_dir}", verbose=True)

    def log_summary(summary: dict[str, object]) -> None:
        print(
            f"{str(summary['dict_id']):12s} entries={summary['entries_emitted']} "
            f"markers={entry_marker_status_text(summary)} "
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


def _cmd_extract(
    argv: list[str],
    *,
    command: str,
    description: str,
    add_args: Callable[[argparse.ArgumentParser], None],
    task: Callable[[tuple[Any, Path, argparse.Namespace]], dict[str, Any]],
    count_field: str,
    label: str,
    include_gaiji: bool = True,
    include_images: bool = False,
) -> int:
    from .entries import discover_dictionaries, write_json
    from .parallel import parallel_map_ordered, worker_args

    parser = argparse.ArgumentParser(prog=f"logovista-tools {command}", description=description)
    parser.add_argument("--verbose", action="store_true", help="Show extra progress details.")
    add_args(parser)
    args = parser.parse_args(argv)

    roots = args.root or [Path(".")]
    for root in roots:
        if not root.exists():
            raise FileNotFoundError(root)
    status(args, f"{command}: discovering dictionaries under {', '.join(str(root) for root in roots)}")
    sources = discover_dictionaries(
        roots,
        jobs=args.jobs,
        dict_ids=args.dict,
        include_gaiji=include_gaiji,
        include_images=include_images,
    )
    if args.dict:
        selected = set(args.dict)
        sources = [source for source in sources if source.dict_id in selected or source.idx.stem in selected]
    if not sources:
        print("no dictionaries found", file=sys.stderr)
        return 1
    status(args, f"{command}: found {len(sources)} dictionary package(s)")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    def log_summary(summary: dict[str, Any]) -> None:
        print(f"{summary['dict_id']:12s} {label}={summary[count_field]}", file=sys.stderr)

    task_args = worker_args(args)
    summaries = parallel_map_ordered(
        task,
        [(source, args.out_dir, task_args) for source in sources],
        jobs=args.jobs,
        on_result=log_summary,
    )
    write_json(args.out_dir / "summary.json", summaries)
    if getattr(args, "json", False):
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def _cmd_titles(argv: list[str]) -> int:
    from .cli_args import add_titles_args

    return _cmd_extract(
        argv,
        command="titles",
        description="Extract raw *TITLE.DIC headword/title lines as JSONL.",
        add_args=add_titles_args,
        task=_titles_task,
        count_field="lines_emitted",
        label="title_lines",
        include_images=False,
    )


def _cmd_indexes(argv: list[str]) -> int:
    from .cli_args import add_indexes_args

    return _cmd_extract(
        argv,
        command="indexes",
        description="Extract raw *INDEX.DIC search rows as JSONL.",
        add_args=add_indexes_args,
        task=_indexes_task,
        count_field="rows_emitted",
        label="index_rows",
        include_images=False,
    )


def _cmd_menus(argv: list[str]) -> int:
    from .cli_args import add_menus_args

    return _cmd_extract(
        argv,
        command="menus",
        description="Extract MENU.DIC menu trees and destination pointers.",
        add_args=add_menus_args,
        task=_menus_task,
        count_field="lines_emitted",
        label="menu_lines",
        include_images=False,
    )


def _cmd_colscr(argv: list[str]) -> int:
    from .cli_args import add_colscr_args

    return _cmd_extract(
        argv,
        command="colscr",
        description="Inspect or extract COLSCR.DIC media image records.",
        add_args=add_colscr_args,
        task=_colscr_task,
        count_field="media_references",
        label="refs",
        include_gaiji=False,
        include_images=False,
    )


def _cmd_pcmdata(argv: list[str]) -> int:
    from .cli_args import add_pcmdata_args

    return _cmd_extract(
        argv,
        command="pcmdata",
        description="Inspect or extract PCMDATA.DIC audio/media records.",
        add_args=add_pcmdata_args,
        task=_pcmdata_task,
        count_field="audio_references",
        label="refs",
        include_images=False,
    )


def main(argv: list[str] | None = None) -> int:
    args, verbose = extract_verbose(argv)
    args = list(args or [])
    if not args:
        from .cli import main as full_main

        return full_main(["--verbose"] if verbose else [])
    if args[0] in {"-h", "--help"}:
        from .cli import main as full_main

        return full_main((["--verbose"] if verbose else []) + args)
    if args[0] == "--version":
        print(f"logovista-tools {__version__}")
        return 0
    if args[0] == "info":
        if any(item in {"-h", "--help"} for item in args[1:]):
            return _cmd_info(args[1:])
        return run_callback_with_friendly_errors(
            program="logovista-tools",
            command="info",
            verbose=verbose,
            func=lambda: _cmd_info((["--verbose"] if verbose else []) + args[1:]),
        )
    if args[0] == "entries":
        if any(item in {"-h", "--help"} for item in args[1:]):
            return _cmd_entries(args[1:])
        return run_callback_with_friendly_errors(
            program="logovista-tools",
            command="entries",
            verbose=verbose,
            func=lambda: _cmd_entries((["--verbose"] if verbose else []) + args[1:]),
        )
    fast_commands = {
        "titles": _cmd_titles,
        "indexes": _cmd_indexes,
        "menus": _cmd_menus,
        "colscr": _cmd_colscr,
        "pcmdata": _cmd_pcmdata,
    }
    if args[0] in fast_commands:
        command_func = fast_commands[args[0]]
        if any(item in {"-h", "--help"} for item in args[1:]):
            return command_func(args[1:])
        return run_callback_with_friendly_errors(
            program="logovista-tools",
            command=args[0],
            verbose=verbose,
            func=lambda: command_func((["--verbose"] if verbose else []) + args[1:]),
        )

    from .cli import main as full_main

    return full_main((["--verbose"] if verbose else []) + args)
