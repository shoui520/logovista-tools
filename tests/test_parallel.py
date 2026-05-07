import argparse

from logovista_tools.parallel import parallel_map_ordered, resolve_jobs, worker_args


def square(value: int) -> int:
    return value * value


def maybe_none(value: int) -> int | None:
    return None if value == 2 else value


def test_resolve_jobs() -> None:
    assert resolve_jobs(None) == 1
    assert resolve_jobs(1) == 1
    assert resolve_jobs(2) == 2
    assert resolve_jobs(0) >= 1


def test_worker_args_removes_func() -> None:
    def placeholder() -> None:
        return None

    args = argparse.Namespace(func=placeholder, jobs=2, limit=10)
    copied = worker_args(args)

    assert not hasattr(copied, "func")
    assert copied.jobs == 2
    assert copied.limit == 10


def test_parallel_map_ordered_serial_and_parallel() -> None:
    assert parallel_map_ordered(square, [1, 2, 3], jobs=1) == [1, 4, 9]
    assert parallel_map_ordered(square, [1, 2, 3], jobs=2) == [1, 4, 9]


def test_parallel_map_ordered_preserves_none_results() -> None:
    assert parallel_map_ordered(maybe_none, [1, 2, 3], jobs=2) == [1, None, 3]
