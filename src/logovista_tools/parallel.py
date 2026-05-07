"""Small multiprocessing helpers for corpus-scale commands."""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable, Iterable, TypeVar, cast


T = TypeVar("T")
R = TypeVar("R")


def resolve_jobs(jobs: int | None) -> int:
    if jobs is None:
        return 1
    if jobs == 0:
        return os.cpu_count() or 1
    if jobs < 0:
        raise ValueError("--jobs must be 0 or a positive integer")
    return max(1, jobs)


def add_jobs_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Parallel worker processes. Use 0 for os.cpu_count(); default is 1.",
    )


def worker_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(**{key: value for key, value in vars(args).items() if key != "func"})


def parallel_map_ordered(
    func: Callable[[T], R],
    items: Iterable[T],
    *,
    jobs: int | None = 1,
    on_result: Callable[[R], Any] | None = None,
) -> list[R]:
    item_list = list(items)
    if not item_list:
        return []

    worker_count = min(resolve_jobs(jobs), len(item_list))
    if worker_count == 1:
        results = []
        for item in item_list:
            result = func(item)
            if on_result is not None:
                on_result(result)
            results.append(result)
        return results

    missing = object()
    results: list[R | object] = [missing] * len(item_list)
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(func, item): index
            for index, item in enumerate(item_list)
        }
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            results[index] = result
            if on_result is not None:
                on_result(result)

    return [cast(R, result) for result in results]
