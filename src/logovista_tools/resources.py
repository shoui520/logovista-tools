"""LogoVista package resource discovery."""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Hashable

from .ssed import CaseFoldedDirectory, find_case_insensitive, resolve_case_insensitive_path


IMAGE_SUFFIX_RE = re.compile(r"^(?P<key>.+)_(?P<theme>n|w|m|1|3|1_1)$", re.IGNORECASE)
GAIJI_IMAGE_KEY_RE = re.compile(r"[A-Fa-f][0-9A-Fa-f]{3}")
IMAGE_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"}


@dataclass(frozen=True)
class ImageResource:
    """A discovered image resource and its optional theme variants."""

    key: str
    files: tuple[Path, ...]
    normal: Path | None = None
    white: Path | None = None
    default: Path | None = None
    listed_in_resources_copy: bool = False
    listed_in_gaijiicon: bool = False


@dataclass(frozen=True)
class ImageResourceProfile:
    """Image resources found near one dictionary package."""

    image_dirs: tuple[Path, ...]
    resources: dict[str, ImageResource]
    gaiji_image_keys: frozenset[str]
    resources_copy_paths: tuple[Path, ...]
    gaijiicon_paths: tuple[Path, ...]
    resources_copy_entries: tuple[str, ...]
    gaijiicon_entries: tuple[str, ...]


def file_identity(path: Path) -> Hashable:
    try:
        stat = path.stat()
    except OSError:
        return str(path).casefold()
    return (stat.st_dev, stat.st_ino, stat.st_size)


def candidate_package_roots(path: Path) -> list[Path]:
    """Return likely LogoVista package roots for an IDX file or package dir."""

    resolved = path.resolve()
    roots: list[Path] = []
    if resolved.is_file():
        roots.extend([resolved.parent, resolved.parent.parent])
    else:
        roots.extend([resolved, resolved.parent])

    deduped: list[Path] = []
    seen: set[Hashable] = set()
    for root in roots:
        identity = file_identity(root)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(root)
    return deduped


def load_string_list_plists(paths: list[Path]) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    values: list[str] = []
    loaded: list[Path] = []
    seen_paths: set[Hashable] = set()
    seen_values: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        identity = file_identity(path)
        if identity in seen_paths:
            continue
        seen_paths.add(identity)
        try:
            data = plistlib.load(path.open("rb"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        loaded.append(path)
        for item in data:
            if not isinstance(item, str) or item in seen_values:
                continue
            seen_values.add(item)
            values.append(item)
    return tuple(values), tuple(loaded)


def image_key_and_theme(path: Path) -> tuple[str, str | None]:
    match = IMAGE_SUFFIX_RE.fullmatch(path.stem)
    if match:
        return match.group("key").lower(), match.group("theme").lower()
    return path.stem.lower(), None


def candidate_image_dirs(root: Path) -> list[Path]:
    package_specific = [
        root / "res",
        root / "resources",
        root / "img",
        root / "image",
        root / "images",
        root / "Templates",
        root / "templates",
        root / "HANREI" / "img",
        root / "HANREI" / "contents" / "img",
        root / "OTHER" / "images",
        root / "resource" / "kmkimges",
        root / "appendix" / "img",
        root / "manual" / "contents" / "img",
    ]
    companion_names = {f"{root.name}_GAIJI"}
    if root.name.upper().startswith("_DCT_"):
        companion_names.add(f"{root.name[5:]}_GAIJI")
    package_specific.extend(root.parent / name for name in sorted(companion_names))
    return package_specific


def _actual_candidate_path(root: Path, candidate: Path) -> Path | None:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return find_case_insensitive(candidate.parent, candidate.name)
    return resolve_case_insensitive_path(root, relative)


def relative_image_source(path: Path, package_hint: Path) -> str:
    for root in candidate_package_roots(package_hint):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def load_image_resource_profile(path: Path) -> ImageResourceProfile:
    """Discover PNG resources, resource copy plists, and gaiji icon plists.

    LogoVista packages commonly keep dictionary-specific icon PNGs in a sibling
    ``img`` directory next to the dictionary directory. Windows packages can put
    dictionary-template assets in ``Templates``; platformless/core-SSED packages
    can use generic ``res`` / ``resources`` / ``templates`` directories; Android
    packages can use ``resource/kmkimges`` and omit plist manifests. Files ending
    in ``_n`` / ``_w`` and Android-style ``_1`` / ``_3`` are grouped as theme
    variants of the same resource key.
    """

    roots = candidate_package_roots(path)
    image_dirs: list[Path] = []
    seen_dirs: set[Hashable] = set()
    for root in roots:
        for image_dir in candidate_image_dirs(root):
            actual = _actual_candidate_path(root, image_dir)
            if actual is not None:
                image_dir = actual
            if not image_dir.is_dir():
                continue
            identity = file_identity(image_dir)
            if identity in seen_dirs:
                continue
            seen_dirs.add(identity)
            image_dirs.append(image_dir)

    resources_copy_entries, resources_copy_paths = load_string_list_plists(
        [path for root in roots if (path := CaseFoldedDirectory.from_path(root).find("resourcesCopy.plist")) is not None]
    )
    gaijiicon_entries, gaijiicon_paths = load_string_list_plists(
        [path for root in roots if (path := CaseFoldedDirectory.from_path(root).find("gaijiicon.plist")) is not None]
    )

    listed_resources = {image_key_and_theme(Path(name))[0] for name in resources_copy_entries}
    listed_gaijiicons = {name.lower() for name in gaijiicon_entries}

    grouped: dict[str, dict[str, object]] = {}
    for image_dir in image_dirs:
        for file in sorted(child for child in image_dir.iterdir() if child.suffix.lower() in IMAGE_EXTENSIONS):
            key, theme = image_key_and_theme(file)
            bucket = grouped.setdefault(key, {"files": [], "normal": None, "white": None, "default": None})
            bucket["files"].append(file)
            if theme in {"n", "1", "1_1"}:
                bucket["normal"] = file
            elif theme in {"w", "3"}:
                bucket["white"] = file
            else:
                bucket["default"] = file

    resources: dict[str, ImageResource] = {}
    for key, bucket in sorted(grouped.items()):
        files = tuple(bucket["files"])  # type: ignore[arg-type]
        normal = bucket["normal"] if isinstance(bucket["normal"], Path) else None
        white = bucket["white"] if isinstance(bucket["white"], Path) else None
        default = bucket["default"] if isinstance(bucket["default"], Path) else None
        resources[key] = ImageResource(
            key=key,
            files=files,
            normal=normal,
            white=white,
            default=default,
            listed_in_resources_copy=key in listed_resources,
            listed_in_gaijiicon=key in listed_gaijiicons,
        )

    gaiji_image_keys = frozenset(key for key in resources if GAIJI_IMAGE_KEY_RE.fullmatch(key))
    return ImageResourceProfile(
        image_dirs=tuple(image_dirs),
        resources=resources,
        gaiji_image_keys=gaiji_image_keys,
        resources_copy_paths=resources_copy_paths,
        gaijiicon_paths=gaijiicon_paths,
        resources_copy_entries=resources_copy_entries,
        gaijiicon_entries=gaijiicon_entries,
    )
