"""Console UX helpers for command line entry points."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable
from pathlib import Path


def extract_verbose(argv: list[str] | None) -> tuple[list[str] | None, bool]:
    """Accept --verbose before or after the subcommand.

    argparse only accepts global options before subcommands. For a CLI used
    mostly by direct shell invocations, accepting it anywhere is friendlier and
    avoids needing to add the same option to every subparser.
    """

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


def is_verbose(args: argparse.Namespace | None) -> bool:
    if args is None:
        return False
    return bool(getattr(args, "verbose", False) or getattr(args, "debug", False))


def status(args: argparse.Namespace | None, message: str, *, verbose: bool = False) -> None:
    if verbose and not is_verbose(args):
        return
    print(message, file=sys.stderr, flush=True)


def command_name(args: argparse.Namespace | None) -> str:
    if args is None:
        return "command"
    return str(getattr(args, "command", None) or getattr(args, "_command_name", None) or "command")


def path_display(path: object) -> str:
    if path is None:
        return "<unknown path>"
    try:
        return str(Path(path))
    except TypeError:
        return str(path)


def friendly_exception_message(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"file not found: {path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, IsADirectoryError):
        return f"expected a file but got a directory: {path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, NotADirectoryError):
        return f"expected a directory in this path: {path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, PermissionError):
        return f"permission denied: {path_display(getattr(exc, 'filename', None) or exc)}"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def validate_common_input_paths(args: argparse.Namespace) -> None:
    for attr in ("path", "file", "dic", "idx"):
        maybe_path = getattr(args, attr, None)
        if isinstance(maybe_path, Path) and not maybe_path.exists():
            raise FileNotFoundError(maybe_path)
    roots = getattr(args, "root", None)
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, Path) and not root.exists():
                raise FileNotFoundError(root)
    elif isinstance(roots, Path) and not roots.exists():
        raise FileNotFoundError(roots)


def run_with_friendly_errors(
    *,
    program: str,
    args: argparse.Namespace,
    func: Callable[[argparse.Namespace], int],
) -> int:
    label = command_name(args)
    status(args, f"{program}: running {label}")
    if is_verbose(args):
        status(args, f"{program}: debug/verbose output enabled", verbose=True)
    try:
        validate_common_input_paths(args)
        result = int(func(args))
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        status(args, f"{program}: interrupted")
        return 130
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError, ValueError) as exc:
        status(args, f"{program}: error: {friendly_exception_message(exc)}")
        if is_verbose(args):
            traceback.print_exc()
        return 2
    except Exception as exc:
        status(args, f"{program}: error: {friendly_exception_message(exc)}")
        if is_verbose(args):
            traceback.print_exc()
        else:
            status(args, f"{program}: rerun with --verbose for a Python traceback")
        return 2
    if result == 0:
        status(args, f"{program}: completed {label}")
    else:
        status(args, f"{program}: {label} exited with status {result}")
    return result


def run_callback_with_friendly_errors(
    *,
    program: str,
    command: str,
    verbose: bool,
    func: Callable[[], int],
) -> int:
    args = argparse.Namespace(command=command, verbose=verbose)
    status(args, f"{program}: running {command}")
    try:
        result = int(func())
    except BrokenPipeError:
        return 1
    except KeyboardInterrupt:
        status(args, f"{program}: interrupted")
        return 130
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError, ValueError) as exc:
        status(args, f"{program}: error: {friendly_exception_message(exc)}")
        if verbose:
            traceback.print_exc()
        return 2
    except Exception as exc:
        status(args, f"{program}: error: {friendly_exception_message(exc)}")
        if verbose:
            traceback.print_exc()
        else:
            status(args, f"{program}: rerun with --verbose for a Python traceback")
        return 2
    if result == 0:
        status(args, f"{program}: completed {command}")
    else:
        status(args, f"{program}: {command} exited with status {result}")
    return result
