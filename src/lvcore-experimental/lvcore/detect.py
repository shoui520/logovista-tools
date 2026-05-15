"""Package-family detection."""

from __future__ import annotations

from pathlib import Path

from .model import PackageFamily, PackageInfo
from .ssed import CaseFoldedDirectory, candidate_idx_files, find_file_case_insensitive, is_metadata_noise_path, is_ssedinfo, parse_catalog


def _multiview_markers(root: Path) -> bool:
    if not root.is_dir():
        return False
    names = {child.name.casefold() for child in root.iterdir() if not is_metadata_noise_path(child)}
    return "vlpljbl.exe" in names and bool({"blvbat", "blvdat", "hlvbat", "ilvbat", "jlvbat", "nlvbat"} & names)


def detect_family(path: Path) -> PackageInfo:
    path = path.resolve()
    root = path.parent if path.is_file() else path
    multiview = _multiview_markers(root)
    lookup = CaseFoldedDirectory.from_path(root) if root.is_dir() else None
    collision_notes = (
        tuple(f"case-insensitive filename collision: {', '.join(names)}" for names in (lookup.collisions().values() if lookup else ()))
    )

    for idx in candidate_idx_files(path):
        if is_ssedinfo(idx):
            catalog = parse_catalog(idx)
            missing_declared = [
                component.name
                for component in catalog.components
                if component.name and component.start_block and find_file_case_insensitive(idx.parent, component.name) is None
            ]
            if multiview and missing_declared:
                return PackageInfo(
                    family=PackageFamily.LVLMULTI,
                    root=idx.parent,
                    idx_path=idx,
                    dict_id=idx.stem,
                    title=catalog.title,
                    notes=("LVLMultiView sidecar payload detected; SSEDINFO catalog is not self-contained",) + collision_notes,
                )
            notes = (("SSED package with LVLMultiView sidecars",) if multiview else ()) + collision_notes
            return PackageInfo(
                family=PackageFamily.SSED,
                root=idx.parent,
                idx_path=idx,
                dict_id=idx.stem,
                title=catalog.title,
                notes=notes,
            )

    lved_payload = lookup.find("main.data") if lookup is not None else None
    if lved_payload is None and lookup is not None:
        dbc_files = lookup.files_with_suffix(".dbc")
        lved_payload = dbc_files[0] if dbc_files else None
    if lved_payload is not None and lved_payload.is_file():
        return PackageInfo(
            family=PackageFamily.LVED,
            root=root,
            dict_id=(lved_payload.stem if lved_payload.suffix.casefold() == ".dbc" else root.name.removeprefix("_DCT_")),
            notes=("LVED SQLCipher/SQLite package detected; reader implementation deferred",) + collision_notes,
        )

    if multiview:
        return PackageInfo(
            family=PackageFamily.LVLMULTI,
            root=root,
            dict_id=root.name.removeprefix("_DCT_"),
            notes=("LVLMultiView package detected; reader implementation deferred",) + collision_notes,
        )

    return PackageInfo(family=PackageFamily.UNKNOWN, root=root, dict_id=root.name.removeprefix("_DCT_"), notes=collision_notes)
