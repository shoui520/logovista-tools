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


COMMAND_INPUT_HINTS: dict[str, dict[str, str]] = {
    "entries": {
        "component": "HONMON.DIC",
        "why": "entries needs the dictionary .IDX or package directory so it can locate HONMON start blocks, gaiji maps, and related resources",
        "example": "logovista-tools entries /path/to/DICT/DICT.IDX --limit 100 --print",
    },
    "titles": {
        "component": "TITLE.DIC",
        "why": "titles needs the dictionary .IDX or package directory so it can find every title component and its component metadata",
        "example": "logovista-tools titles /path/to/DICT/DICT.IDX --limit 100",
    },
    "indexes": {
        "component": "INDEX.DIC",
        "why": "indexes needs the dictionary .IDX or package directory so it can read component types, start blocks, and companion title/body pointers",
        "example": "logovista-tools indexes /path/to/DICT/DICT.IDX --limit 100",
    },
    "menus": {
        "component": "MENU.DIC",
        "why": "menus needs the dictionary .IDX or package directory so it can resolve MENU.DIC component metadata and destinations",
        "example": "logovista-tools menus /path/to/DICT/DICT.IDX --limit 100",
    },
    "colscr": {
        "component": "COLSCR.DIC",
        "why": "colscr needs the dictionary .IDX or package directory so it can scan HONMON media controls before resolving COLSCR records",
        "example": "logovista-tools colscr /path/to/DICT/DICT.IDX --write-media --out-dir colscr",
    },
    "pcmdata": {
        "component": "PCMDATA.DIC",
        "why": "pcmdata needs the dictionary .IDX or package directory so it can scan HONMON audio controls before resolving PCMDATA ranges",
        "example": "logovista-tools pcmdata /path/to/DICT/DICT.IDX --write-audio --out-dir pcmdata",
    },
    "resources": {
        "component": "resource component",
        "why": "resources needs the dictionary .IDX or package directory so it can associate resource files with dictionary metadata",
        "example": "logovista-tools resources /path/to/DICT/DICT.IDX",
    },
}


def _find_idx_hint(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    for pattern in ("*.IDX", "*.idx"):
        matches = sorted(path for path in directory.glob(pattern) if not path.name.startswith("._"))
        if matches:
            return matches[0]
    return None


def dictionary_source_error(command: str, roots: object, *, dict_ids: object = None) -> str:
    """Return a specific user-facing error for commands that need dictionary packages."""

    raw_roots = roots if isinstance(roots, list) else [roots]
    paths = [root for root in raw_roots if isinstance(root, Path)]
    hint = COMMAND_INPUT_HINTS.get(command, {})
    examples: list[str] = []

    for path in paths:
        if path.is_file() and path.suffix.upper() != ".IDX":
            idx_hint = _find_idx_hint(path.parent)
            suggestion = str(idx_hint) if idx_hint is not None else "/path/to/DICT/DICT.IDX"
            example = hint.get("example", f"logovista-tools {command} {suggestion}").replace(
                "/path/to/DICT/DICT.IDX", suggestion
            )
            return (
                f"{command}: {path.name} was provided directly, but {hint.get('why', 'this command needs a dictionary .IDX or package directory')}. "
                f"Use the package directory or .IDX instead. Example: {example}"
            )
        if path.is_dir():
            idx_hint = _find_idx_hint(path)
            if idx_hint is not None:
                examples.append(str(idx_hint))

    if dict_ids:
        wanted = ", ".join(str(value) for value in dict_ids)
        return f"{command}: no dictionary packages matched --dict {wanted}. Check the dictionary id or pass the direct .IDX path."
    if paths:
        joined = ", ".join(str(path) for path in paths)
        example_path = examples[0] if examples else "/path/to/DICT/DICT.IDX"
        return (
            f"{command}: no SSED .IDX dictionary packages were found under {joined}. "
            f"Pass a collection root, a dictionary package directory, or a direct .IDX path. Example: logovista-tools {command} {example_path}"
        )
    return f"{command}: no dictionary input was provided. Pass a collection root, dictionary package directory, or direct .IDX path."


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
