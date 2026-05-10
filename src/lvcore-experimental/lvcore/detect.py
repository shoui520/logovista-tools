"""Package-family detection."""

from __future__ import annotations

from pathlib import Path

from .model import PackageFamily, PackageInfo
from .ssed import candidate_idx_files, is_ssedinfo, parse_catalog


def _multiview_markers(root: Path) -> bool:
    if not root.is_dir():
        return False
    names = {child.name.lower() for child in root.iterdir()}
    return "vlpljbl.exe" in names and bool({"blvbat", "blvdat", "hlvbat", "ilvbat", "jlvbat", "nlvbat"} & names)


def detect_family(path: Path) -> PackageInfo:
    path = path.resolve()
    root = path.parent if path.is_file() else path
    multiview = _multiview_markers(root)

    for idx in candidate_idx_files(path):
        if is_ssedinfo(idx):
            catalog = parse_catalog(idx)
            missing_declared = [
                component.name
                for component in catalog.components
                if component.name and component.start_block and not (idx.parent / component.name).exists()
            ]
            if multiview and missing_declared:
                return PackageInfo(
                    family=PackageFamily.LVLMULTI,
                    root=idx.parent,
                    idx_path=idx,
                    dict_id=idx.stem,
                    title=catalog.title,
                    notes=("LVLMultiView sidecar payload detected; SSEDINFO catalog is not self-contained",),
                )
            notes = ("SSED package with LVLMultiView sidecars",) if multiview else ()
            return PackageInfo(
                family=PackageFamily.SSED,
                root=idx.parent,
                idx_path=idx,
                dict_id=idx.stem,
                title=catalog.title,
                notes=notes,
            )

    if (root / "main.data").is_file():
        return PackageInfo(
            family=PackageFamily.LVED,
            root=root,
            dict_id=root.name.removeprefix("_DCT_"),
            notes=("LVED SQLCipher/SQLite package detected; reader implementation deferred",),
        )

    if multiview:
        return PackageInfo(
            family=PackageFamily.LVLMULTI,
            root=root,
            dict_id=root.name.removeprefix("_DCT_"),
            notes=("LVLMultiView package detected; reader implementation deferred",),
        )

    return PackageInfo(family=PackageFamily.UNKNOWN, root=root, dict_id=root.name.removeprefix("_DCT_"))
