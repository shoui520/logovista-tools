"""Loose KOJIEN-style ``_MOVIE`` resource discovery."""

from __future__ import annotations

from pathlib import Path

from .ssed import is_metadata_noise_path, resolve_case_insensitive_path


def _candidate_movie_dirs(package: Path) -> tuple[Path, ...]:
    names = []
    if package.name:
        names.append(f"{package.name}_MOVIE")
    if package.name.startswith("_DCT_"):
        names.append(f"{package.name[5:]}_MOVIE")
    names.append("_MOVIE")
    candidates = [package / name for name in names]
    candidates.extend(package.parent / name for name in names)
    seen: set[Path] = set()
    resolved: list[Path] = []
    for candidate in candidates:
        key = candidate.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return tuple(resolved)


def find_movie_dir(idx: Path) -> Path | None:
    for candidate in _candidate_movie_dirs(idx.parent):
        if candidate.is_dir():
            return candidate
    return None


def movie_ids_for_idx(idx: Path) -> frozenset[str]:
    movie_dir = find_movie_dir(idx)
    if movie_dir is None:
        return frozenset()
    ids = {
        child.name
        for child in movie_dir.iterdir()
        if child.is_file() and not is_metadata_noise_path(child) and child.name.isdecimal() and len(child.name) == 8
    }
    return frozenset(ids)


def movie_file_for_id(idx: Path, movie_id: str) -> Path | None:
    movie_dir = find_movie_dir(idx)
    if movie_dir is None:
        return None
    path = resolve_case_insensitive_path(movie_dir, movie_id)
    if path is None or not path.is_file():
        return None
    return path

