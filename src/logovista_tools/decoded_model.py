"""Decoded LogoVista Model v0 package aggregation.

This module is not a new dictionary format. It is a research model that pulls
the current parser outputs into one package-level JSON object so contradictions
and remaining gaps are visible in one place.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .colscr import extract_colscr_for_source
from .entries import DictionarySource, discover_dictionaries, iter_entry_slices_with_boundaries
from .fulldb import find_fulldb
from .gaiji import (
    candidate_gaiji_paths,
    is_bitmap_gaiji_resource_name,
    load_gaiji_profile,
    parse_ga16_resource,
    parse_uni_resource,
)
from .gaiji_readiness import extract_gaiji_readiness
from .indexes import INDEX_TYPES, collect_index_body_offsets_for_idx, extract_indexes_for_idx
from .lved import discover_lved_payloads, inspect_lved_roots
from .menus import extract_menus_for_idx
from .model_readiness import build_model_readiness
from .model_types import (
    AddressKind,
    BodySource,
    HonmonShape,
    ModelAddress,
    PackageFamily,
    PlatformWrapper,
    normalize_body_source,
    normalize_component_role,
    normalize_honmon_shape,
    normalize_package_family,
    normalize_platform,
)
from .multiview import discover_multiview_packages, inspect_multiview_package
from .pcmdata import extract_pcmdata_for_source
from .profiles import ProfileTarget, build_profile
from .rendererdb import (
    discover_android_body_databases,
    honbun_columns,
    iter_honmon_id_records as iter_dense_honmon_id_records,
    prepare_sidecar_database,
    quote_identifier,
    t_contents_columns,
    table_count,
    table_exists,
)
from .resources import candidate_image_dirs, file_identity, load_image_resource_profile, relative_image_source
from .sizk import inspect_sizk_package, is_sizk_package
from .spans import LosslessDecodeError, decode_lossless_spans
from .ssed import (
    BLOCK_SIZE,
    SsedInfoElement,
    expand_sseddata_file_with_storage,
    find_case_insensitive,
    is_metadata_noise_path,
    iter_files_with_suffix,
    parse_ssedinfo_with_layout,
)
from .titles import TITLE_TYPES, extract_titles_for_idx
from .windows import (
    aux_index_row_to_json,
    classify_hc_renderer_file,
    classify_vlpljbl_file,
    discover_hc_renderer_files,
    discover_numeric_aux_indexes,
    discover_renderer_sidecars,
    discover_vlpljbl_files,
    hc_renderer_classification_to_json,
    iter_aux_index_specs,
    load_exinfo_for_idx,
    parse_aux_index_text,
    vlpljbl_classification_to_json,
)


MODEL_SCHEMA = "logovista-decoded-model-v0"


@dataclass(frozen=True)
class PackageModelTarget:
    dict_id: str
    path: Path
    family_hint: str

    def as_dict(self) -> dict[str, str]:
        return {
            "dict_id": self.dict_id,
            "path": str(self.path),
            "family_hint": self.family_hint,
        }


def read_jsonl_samples(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if len(rows) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def read_jsonl_records(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit is not None and limit > 0 and len(rows) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def strip_temp_paths(summary: dict[str, Any], *keys: str) -> dict[str, Any]:
    row = dict(summary)
    for key in keys:
        row.pop(key, None)
    return row


def issue(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "message": message, **extra}


def select_single_source(root: Path, dict_id: str | None = None) -> DictionarySource:
    sources = discover_dictionaries([root], jobs=1)
    if dict_id:
        sources = [source for source in sources if source.dict_id == dict_id or source.idx.stem == dict_id]
    if not sources:
        raise ValueError(f"no SSED dictionary with HONMON.DIC found under {root}")
    if len(sources) > 1:
        ids = ", ".join(source.dict_id for source in sources[:20])
        suffix = "..." if len(sources) > 20 else ""
        raise ValueError(f"dump-package-model expects one dictionary; found {len(sources)}: {ids}{suffix}. Use --dict.")
    return sources[0]


def dict_id_for_idx(idx: Path) -> str:
    return idx.parent.parent.name.removeprefix("_DCT_") if idx.parent.name == idx.parent.parent.name else idx.stem


def select_single_idx(root: Path, dict_id: str | None = None) -> Path:
    candidates = list(iter_files_with_suffix(root, ".idx", recursive=root.is_dir()))
    valid: list[Path] = []
    for idx in sorted({path.resolve() for path in candidates}):
        try:
            parse_ssedinfo_with_layout(idx)
        except Exception:
            continue
        if dict_id and idx.stem != dict_id and dict_id_for_idx(idx) != dict_id:
            continue
        valid.append(idx)
    if not valid:
        raise ValueError(f"no parseable SSEDINFO .IDX found under {root}")
    if len(valid) > 1:
        ids = ", ".join(dict_id_for_idx(path) for path in valid[:20])
        suffix = "..." if len(valid) > 20 else ""
        raise ValueError(f"dump-package-model expects one dictionary; found {len(valid)} SSEDINFO catalogs: {ids}{suffix}. Use --dict.")
    return valid[0]


def detect_platform_wrapper_for_idx(idx: Path) -> dict[str, Any]:
    package = idx.parent
    parent = package.parent
    exinfo = load_exinfo_for_idx(idx)
    markers = {
        "exinfo": exinfo is not None,
        "dictlist_plist": (package / "DictList.plist").exists() or (parent / "DictList.plist").exists(),
        "ios_gaiji_plist": any((base / name).exists() for base in (package, parent) for name in ("Gaiji.plist", "GaijiS.plist")),
        "android_conf": (package / "resource" / "conf.ini").exists() or (parent / "resource" / "conf.ini").exists(),
        "templates": (package / "Templates").is_dir(),
        "templates_lower": (package / "templates").is_dir(),
        "res_dir": (package / "res").is_dir(),
        "htmls": (package / "HTMLs").is_dir(),
        "hc_renderer": bool(discover_hc_renderer_files([package])),
        "vlpljbl": bool(discover_vlpljbl_files([package])),
        "numeric_aux_indexes": bool(discover_numeric_aux_indexes(idx)),
        "sizk": is_sizk_package(package),
    }
    if markers["sizk"]:
        platform = "windows"
        family = "ssed"
    elif markers["android_conf"]:
        platform = "android"
        family = "ssed"
    elif markers["dictlist_plist"] or markers["ios_gaiji_plist"]:
        platform = "ios"
        family = "ssed"
    elif markers["exinfo"] or markers["hc_renderer"] or markers["vlpljbl"]:
        platform = "windows"
        family = "ssed"
    else:
        platform = "noplatform"
        family = "ssed"
    return {"package_family": family, "platform": platform, "markers": markers}


def detect_platform_wrapper(source: DictionarySource) -> dict[str, Any]:
    return detect_platform_wrapper_for_idx(source.idx)


def component_address(element: Any, component_offset: int = 0) -> dict[str, Any]:
    return ModelAddress(
        kind=AddressKind.COMPONENT,
        component=element.filename,
        component_type=f"{element.type:02x}",
        block=element.start + component_offset // BLOCK_SIZE if element.start else None,
        offset=component_offset % BLOCK_SIZE,
        component_offset=component_offset,
        absolute_book_offset=(element.start - 1) * BLOCK_SIZE + component_offset if element.start else None,
    ).as_dict()


def database_row_address(path: Path | str, table: str, row_id: str | int | None) -> dict[str, Any]:
    db_path = Path(path)
    return ModelAddress(
        kind=AddressKind.DATABASE_ROW,
        path=str(db_path),
        database=db_path.name,
        table=table,
        row_id=row_id,
    ).as_dict()


def resource_address(path: Path | str, selector: str | None = None) -> dict[str, Any]:
    resource_path = Path(path)
    return ModelAddress(
        kind=AddressKind.RESOURCE,
        path=str(resource_path),
        selector=selector,
    ).as_dict()


def component_address_by_name(
    elements: list[SsedInfoElement],
    component_name: str,
    component_offset: int,
) -> dict[str, Any]:
    for element in elements:
        if element.filename.upper() == component_name.upper():
            return component_address(element, component_offset)
    return ModelAddress(
        kind=AddressKind.COMPONENT,
        component=component_name,
        component_offset=component_offset,
    ).as_dict()


def logical_pointer_address(
    elements: list[SsedInfoElement],
    *,
    block: int,
    offset: int,
) -> dict[str, Any]:
    for element in elements:
        if element.start and element.start <= block <= element.end:
            component_offset = (block - element.start) * BLOCK_SIZE + offset
            return component_address(element, component_offset)
    return ModelAddress(
        kind=AddressKind.BOOK,
        block=block,
        offset=offset,
        absolute_book_offset=(block - 1) * BLOCK_SIZE + offset,
    ).as_dict()


def dense_anchor_address(source: DictionarySource, record: Any) -> dict[str, Any]:
    component_offset = (record.marker_block - source.honmon_start_block) * BLOCK_SIZE + record.marker_offset
    return ModelAddress(
        kind=AddressKind.DENSE_ANCHOR,
        component="HONMON.DIC",
        component_type="00",
        block=record.marker_block,
        offset=record.marker_offset,
        component_offset=component_offset,
        absolute_book_offset=(record.marker_block - 1) * BLOCK_SIZE + record.marker_offset,
        row_id=record.data_id,
    ).as_dict()


def dereference_record(
    *,
    id: str,
    kind: str,
    method: str,
    from_address: dict[str, Any] | None,
    to_address: dict[str, Any] | None,
    status: str,
    confidence: str,
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "id": id,
        "kind": kind,
        "method": method,
        "from": from_address,
        "to": to_address,
        "status": status,
        "confidence": confidence,
    }
    row.update({key: value for key, value in extra.items() if value is not None})
    return row


def limited_rows(rows: list[Any], limit: int | None) -> list[Any]:
    if limit is None or limit == 0:
        return rows
    return rows[: max(0, limit)]


def normalize_classification(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["package_family"] = normalize_package_family(normalized.get("package_family"))
    normalized["platform"] = normalize_platform(normalized.get("platform"))
    normalized["honmon_shape"] = normalize_honmon_shape(normalized.get("honmon_shape"))
    normalized["body_source_hint"] = normalize_body_source(normalized.get("body_source_hint"))
    return normalized


def normalize_component_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    if "role" in normalized:
        normalized["role"] = normalize_component_role(normalized["role"])
    return normalized


def component_rows_for_idx(idx: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
    _title, elements, _layout = parse_ssedinfo_with_layout(idx)
    by_name = {row["filename"].upper(): row for row in profile.get("catalog", {}).get("components", [])}
    rows: list[dict[str, Any]] = []
    for element in elements:
        row = dict(by_name.get(element.filename.upper(), {}))
        row.setdefault("filename", element.filename)
        row.setdefault("type", f"{element.type:02x}")
        row["address"] = component_address(element)
        rows.append(normalize_component_row(row))
    return rows


def matches_dict_id(path: Path, dict_id: str | None) -> bool:
    if not dict_id:
        return True
    wanted = dict_id.upper().removeprefix("_DCT_")
    candidates = {path.stem.upper(), path.name.upper()}
    candidates.update(part.upper().removeprefix("_DCT_") for part in path.parts)
    return wanted in candidates


def _path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def select_single_lved_payload(root: Path, dict_id: str | None = None) -> Path | None:
    payloads = [path for path in discover_lved_payloads([root]) if not is_metadata_noise_path(path)]
    payloads = [path for path in payloads if matches_dict_id(path, dict_id)]
    if not payloads:
        return None
    if len(payloads) > 1:
        ids = ", ".join(str(path) for path in payloads[:10])
        suffix = "..." if len(payloads) > 10 else ""
        raise ValueError(f"dump-package-model found {len(payloads)} LVED payloads: {ids}{suffix}. Use --dict or a package root.")
    return payloads[0]


def select_single_multiview_package(root: Path, dict_id: str | None = None) -> Path | None:
    packages = [path for path in discover_multiview_packages([root]) if matches_dict_id(path, dict_id)]
    if not packages:
        return None
    if len(packages) > 1:
        ids = ", ".join(path.name.removeprefix("_DCT_") for path in packages[:20])
        suffix = "..." if len(packages) > 20 else ""
        raise ValueError(f"dump-package-model found {len(packages)} LVLMultiView packages: {ids}{suffix}. Use --dict.")
    return packages[0]


def _candidate_idx_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return [path.resolve() for path in iter_files_with_suffix(root, ".idx")]
    return sorted({path.resolve() for path in iter_files_with_suffix(root, ".idx", recursive=True)})


def discover_package_model_targets(roots: list[Path], dict_ids: set[str] | None = None) -> list[PackageModelTarget]:
    """Discover package roots/payloads suitable for dump-package-models.

    This discovery layer is deliberately family-aware. SSED targets are keyed by
    parseable SSEDINFO indexes, LVED targets by `main.data`/`.dbc` payloads, and
    LVLMultiView targets by package directories with `menuData.xml` plus
    encrypted/plain SQLite payloads.
    """

    if not roots:
        roots = [Path(".")]
    wanted = {item.upper().removeprefix("_DCT_") for item in dict_ids} if dict_ids else None
    targets: list[PackageModelTarget] = []
    seen: set[tuple[str, Path]] = set()

    multiview_packages = []
    for package in discover_multiview_packages(roots):
        dict_id = package.name.removeprefix("_DCT_")
        if wanted and dict_id.upper() not in wanted:
            continue
        resolved = package.resolve()
        key = (PackageFamily.MULTIVIEW_SQLITE.value, resolved)
        if key in seen:
            continue
        seen.add(key)
        multiview_packages.append(resolved)
        targets.append(PackageModelTarget(dict_id=dict_id, path=resolved, family_hint=PackageFamily.MULTIVIEW_SQLITE.value))

    for payload in discover_lved_payloads(roots):
        if is_metadata_noise_path(payload):
            continue
        dict_id = payload.parent.name.removeprefix("_DCT_")
        if wanted and dict_id.upper() not in wanted:
            continue
        resolved = payload.resolve()
        key = (PackageFamily.LVED_SQLCIPHER.value, resolved)
        if key in seen:
            continue
        seen.add(key)
        targets.append(PackageModelTarget(dict_id=dict_id, path=resolved, family_hint=PackageFamily.LVED_SQLCIPHER.value))

    for root in roots:
        for idx in _candidate_idx_paths(root):
            if any(_path_is_inside(idx, package) for package in multiview_packages):
                continue
            try:
                parse_ssedinfo_with_layout(idx)
            except Exception:
                continue
            dict_id = dict_id_for_idx(idx)
            if wanted and dict_id.upper() not in wanted and idx.stem.upper() not in wanted:
                continue
            resolved = idx.resolve()
            key = (PackageFamily.SSED.value, resolved)
            if key in seen:
                continue
            seen.add(key)
            targets.append(PackageModelTarget(dict_id=dict_id, path=resolved, family_hint=PackageFamily.SSED.value))

    return sorted(targets, key=lambda target: (target.dict_id, target.family_hint, str(target.path)))


def component_rows(source: DictionarySource, profile: dict[str, Any]) -> list[dict[str, Any]]:
    return component_rows_for_idx(source.idx, profile)


def profile_for_idx(idx: Path, args: argparse.Namespace) -> dict[str, Any]:
    title, elements, _layout = parse_ssedinfo_with_layout(idx)
    target = ProfileTarget(dict_id=dict_id_for_idx(idx), idx=idx, title=title, elements=elements)
    profile_args = argparse.Namespace(
        parse_mode=args.parse_mode,
        max_slices=args.profile_max_slices,
        max_issue_samples=args.max_issue_samples,
        hash_files=not args.no_hash,
        skip_index_scan=(not getattr(args, "full_profile_indexes", False) and args.profile_max_slices != 0),
    )
    return build_profile(target, [idx.parent], profile_args)


def profile_for_source(source: DictionarySource, args: argparse.Namespace) -> dict[str, Any]:
    return profile_for_idx(source.idx, args)


def entry_span_samples(source: DictionarySource, args: argparse.Namespace) -> dict[str, Any]:
    title, elements, _layout = parse_ssedinfo_with_layout(source.idx)
    honmon_element = next((element for element in elements if element.filename.upper() == "HONMON.DIC"), None)
    if honmon_element is None:
        return {"status": "no_honmon_component", "entries": [], "issues": []}
    expanded, storage = expand_sseddata_file_with_storage(source.honmon)
    index_boundary_offsets: set[int] = set()
    warnings: list[str] = []
    if getattr(args, "full_entry_boundaries", False):
        try:
            index_boundary_offsets = collect_index_body_offsets_for_idx(
                source.idx,
                honmon_start_block=source.honmon_start_block,
                expanded_size=len(expanded),
            )
        except Exception as exc:
            warnings.append(f"index boundary collection failed: {exc}")
    else:
        warnings.append("index boundary collection skipped for bounded package model")

    limit = args.entry_limit
    gaiji_profile = load_gaiji_profile(source.idx)
    image_profile = load_image_resource_profile(source.idx)
    entries: list[dict[str, Any]] = []
    strict_failures = 0
    sampled_slices = 0
    sample_truncated = False
    for entry_index, (start, end) in enumerate(
        iter_entry_slices_with_boundaries(expanded, index_boundary_offsets),
        start=1,
    ):
        if limit and len(entries) >= limit:
            sample_truncated = True
            break
        sampled_slices += 1
        segment = expanded[start:end]
        strict_error = None
        try:
            decoded = decode_lossless_spans(
                segment,
                gaiji_map=gaiji_profile.map,
                image_gaiji_keys=image_profile.gaiji_image_keys,
                mode=args.parse_mode,
                include_padding=args.include_padding_spans,
            )
        except LosslessDecodeError as exc:
            strict_failures += 1
            strict_error = str(exc)
            decoded = decode_lossless_spans(
                segment,
                gaiji_map=gaiji_profile.map,
                image_gaiji_keys=image_profile.gaiji_image_keys,
                mode="forensic",
                include_padding=args.include_padding_spans,
            )
        entries.append(
            {
                "entry_index": entry_index,
                "source": {
                    "address": {
                        **component_address(honmon_element, start),
                        "component": "HONMON.DIC",
                    },
                    "length": end - start,
                    "boundary_source": "index+marker" if index_boundary_offsets else "marker",
                },
                "strict_error": strict_error,
                "decode": decoded.as_dict(
                    include_spans=args.include_spans,
                    include_raw=args.include_raw,
                    max_issues=args.max_issue_samples,
                ),
            }
        )
    return {
        "schema": "logovista-decoded-entries-v0",
        "status": "ok",
        "honmon_storage": storage,
        "expanded_bytes": len(expanded),
        "entry_markers": expanded.count(b"\x1f\x09\x00\x01"),
        "index_boundary_offsets": len(index_boundary_offsets),
        "sampled_slices": sampled_slices,
        "entries_emitted": len(entries),
        "sample_truncated": sample_truncated,
        "strict_failures": strict_failures,
        "warnings": warnings,
        "entries": entries,
    }


def windows_sidecars_for_idx(idx: Path, args: argparse.Namespace) -> dict[str, Any]:
    exinfo = load_exinfo_for_idx(idx)
    aux_indexes = []
    if exinfo is not None:
        for spec in iter_aux_index_specs(exinfo):
            rows = []
            if spec.path is not None and spec.path.exists():
                try:
                    _title, elements, _layout = parse_ssedinfo_with_layout(idx)
                    parsed = parse_aux_index_text(spec.path, elements)
                    rows = [aux_index_row_to_json(row) for row in parsed[: args.sidecar_sample_limit]]
                    row_count = len(parsed)
                except Exception as exc:
                    rows = []
                    row_count = 0
                    aux_indexes.append(
                        {
                            "index": spec.index,
                            "name": spec.name,
                            "info": spec.info,
                            "path": str(spec.path),
                            "error": str(exc),
                        }
                    )
                    continue
            else:
                row_count = 0
            aux_indexes.append(
                {
                    "index": spec.index,
                    "name": spec.name,
                    "info": spec.info,
                    "path": str(spec.path) if spec.path is not None else None,
                    "rows": row_count,
                    "samples": rows,
                }
            )

    hc_rows = [
        hc_renderer_classification_to_json(classify_hc_renderer_file(path, compute_hash=not args.no_hash))
        for path in discover_hc_renderer_files([idx.parent])
    ]
    vlpljbl_rows = [
        vlpljbl_classification_to_json(
            classify_vlpljbl_file(path, inspect_sqlite=args.deep_sidecars, compute_hash=not args.no_hash)
        )
        for path in discover_vlpljbl_files([idx.parent])
    ]
    renderer_sidecars = [
        {"path": str(sidecar.path), "storage": sidecar.storage}
        for sidecar in discover_renderer_sidecars(idx)
    ]
    numeric_aux = [str(path) for path in discover_numeric_aux_indexes(idx)]
    return {
        "exinfo": {"path": str(exinfo.path), "general": exinfo.general} if exinfo is not None else None,
        "aux_indexes": aux_indexes,
        "numeric_aux_indexes": numeric_aux,
        "hc_renderers": hc_rows,
        "vlpljbl": vlpljbl_rows,
        "renderer_sidecars": renderer_sidecars,
    }


def windows_sidecars(source: DictionarySource, args: argparse.Namespace) -> dict[str, Any]:
    return windows_sidecars_for_idx(source.idx, args)


def gaiji_resources_for_idx(idx: Path, args: argparse.Namespace | None = None) -> dict[str, Any]:
    profile = load_gaiji_profile(idx)
    uni_candidates, plist_candidates = candidate_gaiji_paths(idx)
    uni_resources = []
    seen_uni: set[Path] = set()
    for path in uni_candidates:
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen_uni:
            continue
        seen_uni.add(resolved)
        parsed = parse_uni_resource(path)
        uni_resources.append(
            {
                "path": str(path),
                "parsed": parsed is not None,
                "format": parsed.format if parsed is not None else None,
                "half_count": parsed.half_count if parsed is not None else None,
                "full_count": parsed.full_count if parsed is not None else None,
                "records": len(parsed.records) if parsed is not None else 0,
                "mapped_records": sum(1 for record in parsed.records if record.display) if parsed is not None else 0,
                "trailing_bytes": parsed.trailing_bytes if parsed is not None else None,
            }
        )
    ga16_resources = []
    _title, elements, _layout = parse_ssedinfo_with_layout(idx)
    for element in elements:
        if not is_bitmap_gaiji_resource_name(element.filename):
            continue
        path = find_case_insensitive(idx.parent, element.filename)
        parsed = parse_ga16_resource(path) if path is not None else None
        ga16_resources.append(
            {
                "filename": element.filename,
                "path": str(path) if path is not None else None,
                "present": path is not None,
                "parsed": parsed is not None,
                "width": parsed.width if parsed is not None else None,
                "height": parsed.height if parsed is not None else None,
                "start_code": f"{parsed.start_code:04X}" if parsed is not None else None,
                "count": parsed.count if parsed is not None else None,
            }
        )
    image_profile = load_image_resource_profile(idx)
    resources = {
        "profile": {
            "merged_map_entries": len(profile.map),
            "uni_entries": profile.uni_entries,
            "plist_entries": profile.plist_entries,
            "uni_paths": [str(path) for path in profile.uni_paths],
            "plist_paths": [str(path) for path in profile.plist_paths],
        },
        "uni_resources": uni_resources,
        "plist_candidates_present": [str(path) for path in plist_candidates if path.exists()],
        "ga16_resources": ga16_resources,
        "image_resources": {
            "image_dirs": [str(path) for path in image_profile.image_dirs],
            "resource_count": len(image_profile.resources),
            "gaiji_image_keys": sorted(image_profile.gaiji_image_keys),
            "samples": {
                key: {
                    "normal": relative_image_source(resource.normal, idx) if resource.normal else None,
                    "white": relative_image_source(resource.white, idx) if resource.white else None,
                    "default": relative_image_source(resource.default, idx) if resource.default else None,
                }
                for key, resource in list(sorted(image_profile.resources.items()))[:25]
            },
        },
    }
    if args is not None and getattr(args, "gaiji_readiness", False):
        title, elements, _layout = parse_ssedinfo_with_layout(idx)
        target = ProfileTarget(dict_id=dict_id_for_idx(idx), idx=idx, title=title, elements=elements)
        with tempfile.TemporaryDirectory(prefix="lv-model-gaiji-") as tmp:
            readiness_args = argparse.Namespace(
                renderer_sidecars=getattr(args, "renderer_sidecar_gaiji", False),
                renderer_inference_limit=getattr(args, "renderer_inference_limit", None),
            )
            readiness = extract_gaiji_readiness(target, Path(tmp), readiness_args)
            readiness.pop("report", None)
            resources["readiness"] = readiness
    return resources


def gaiji_resources(source: DictionarySource, args: argparse.Namespace | None = None) -> dict[str, Any]:
    return gaiji_resources_for_idx(source.idx, args)


def static_package_resources_for_idx(idx: Path, sample_limit: int = 50) -> dict[str, Any]:
    package = idx.parent
    resource_dirs = []
    seen_resource_dirs: set[Any] = set()
    known_dir_names = {
        "gaijitemp",
        "hanrei",
        "help",
        "html",
        "htmls",
        "image",
        "images",
        "img",
        "manual",
        "panel",
        "res",
        "resources",
        "templates",
    }
    for path in sorted(package.iterdir()):
        if is_metadata_noise_path(path):
            continue
        if path.is_dir() and path.name.lower() in known_dir_names:
            identity = file_identity(path)
            if identity in seen_resource_dirs:
                continue
            seen_resource_dirs.add(identity)
            resource_dirs.append(path)
    for path in candidate_image_dirs(package):
        if not path.is_dir():
            continue
        identity = file_identity(path)
        if identity in seen_resource_dirs:
            continue
        seen_resource_dirs.add(identity)
        resource_dirs.append(path)

    root_files = [
        path
        for path in package.iterdir()
        if path.is_file()
        and not is_metadata_noise_path(path)
        and path.suffix.lower() in {".html", ".htm", ".css", ".js", ".gif", ".png", ".jpg", ".jpeg", ".svg"}
    ]
    files = list(root_files)
    for directory in resource_dirs:
        files.extend(path for path in directory.rglob("*") if path.is_file() and not is_metadata_noise_path(path))

    extension_counts: dict[str, int] = {}
    total_bytes = 0
    samples = []
    for path in sorted(files):
        suffix = path.suffix.lower() or "<none>"
        extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        if len(samples) < sample_limit:
            samples.append(
                {
                    "path": relative_image_source(path, idx),
                    "bytes": size,
                    "extension": suffix,
                }
            )

    return {
        "root_files": [relative_image_source(path, idx) for path in sorted(root_files)],
        "directories": [relative_image_source(path, idx) for path in resource_dirs],
        "file_count": len(files),
        "total_bytes": total_bytes,
        "extension_counts": dict(sorted(extension_counts.items())),
        "samples": samples,
    }


def static_resources_for_package(package: Path, *, sample_limit: int = 50) -> dict[str, Any]:
    files = [path for path in package.rglob("*") if path.is_file() and not is_metadata_noise_path(path)]
    extension_counts: dict[str, int] = {}
    total_bytes = 0
    samples = []
    for path in sorted(files):
        suffix = path.suffix.lower() or "<none>"
        extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        size = path.stat().st_size
        total_bytes += size
        if len(samples) < sample_limit:
            samples.append(
                {
                    "path": str(path.relative_to(package)),
                    "bytes": size,
                    "extension": suffix,
                }
            )
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "extension_counts": dict(sorted(extension_counts.items())),
        "samples": samples,
    }


def static_package_resources(source: DictionarySource, args: argparse.Namespace) -> dict[str, Any]:
    return static_package_resources_for_idx(source.idx, sample_limit=args.sample_limit)


def media_summaries(source: DictionarySource, args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="lv-model-media-") as tmp:
        common = {
            "out_dir": Path(tmp),
            "dict": None,
            "jobs": 1,
            "limit": args.media_limit,
        }
        colscr_args = argparse.Namespace(**common, write_media=False)
        pcm_args = argparse.Namespace(**common, write_audio=False, include_unreferenced=True)
        colscr = extract_colscr_for_source(source, Path(tmp), colscr_args)
        pcmdata = extract_pcmdata_for_source(source, Path(tmp), pcm_args)
        colscr_records = read_jsonl_records(Path(colscr.get("manifest_path", "")), args.media_limit)
        pcmdata_records = read_jsonl_records(Path(pcmdata.get("manifest_path", "")), args.media_limit)
        return {
            "colscr": {**strip_temp_paths(colscr, "manifest_path"), "records": colscr_records},
            "pcmdata": {**strip_temp_paths(pcmdata, "manifest_path"), "records": pcmdata_records},
        }


def _sqlite_row_exists(con: sqlite3.Connection, table: str, column: str, value: Any) -> bool:
    try:
        row = con.execute(
            f"select 1 from {quote_identifier(table)} where {quote_identifier(column)}=? limit 1",
            (value,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def _raw_id_limit(args: argparse.Namespace) -> int | None:
    return getattr(args, "sidecar_sample_limit", 20)


def dense_anchor_and_body_dereferences(
    source: DictionarySource,
    elements: list[SsedInfoElement],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        expanded, _storage = expand_sseddata_file_with_storage(source.honmon)
    except Exception as exc:
        return [
            dereference_record(
                id=f"dereference:{source.dict_id}:honmon-anchor:error",
                kind="dense_honmon_anchor_scan",
                method="dense_numeric_id",
                from_address=None,
                to_address=None,
                status="error",
                confidence="structural_only",
                error=str(exc),
            )
        ]

    raw_ids = list(iter_dense_honmon_id_records(expanded, honmon_start_block=source.honmon_start_block))
    limited_ids = limited_rows(raw_ids, _raw_id_limit(args))
    for record in limited_ids:
        rows.append(
            dereference_record(
                id=f"dereference:{source.dict_id}:dense-anchor:{record.record_index}",
                kind="dense_honmon_anchor",
                method="dense_numeric_id",
                from_address=dense_anchor_address(source, record),
                to_address=None,
                status="observed",
                confidence="strongly_inferred",
                anchor_type="numeric_id",
                anchor_value=record.data_id,
                record_index=record.record_index,
                record_offset=record.record_offset,
            )
        )

    fulldb = find_fulldb(source)
    if fulldb is not None and raw_ids:
        try:
            con = sqlite3.connect(f"file:{fulldb}?mode=ro", uri=True)
            try:
                columns = t_contents_columns(con) if table_exists(con, "t_contents") else {}
                data_id_col = columns.get("f_DataId")
                row_count = table_count(con, "t_contents") if data_id_col else None
                for record in limited_ids:
                    exists = bool(data_id_col and _sqlite_row_exists(con, "t_contents", data_id_col, record.data_id))
                    rows.append(
                        dereference_record(
                            id=f"dereference:{source.dict_id}:dictfulldb:{record.record_index}",
                            kind="body_link",
                            method="dictfulldb_id",
                            from_address=dense_anchor_address(source, record),
                            to_address=database_row_address(fulldb, "t_contents", record.data_id),
                            status="resolved" if exists else "missing_target",
                            confidence="proven" if exists else "strongly_inferred",
                            anchor_value=record.data_id,
                            target_rows=row_count,
                        )
                    )
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            rows.append(
                dereference_record(
                    id=f"dereference:{source.dict_id}:dictfulldb:error",
                    kind="body_link",
                    method="dictfulldb_id",
                    from_address=None,
                    to_address=resource_address(fulldb),
                    status="error",
                    confidence="structural_only",
                    error=str(exc),
                )
            )

    android_dbs = discover_android_body_databases(source.idx, source.dict_id)
    for body_db in android_dbs[:1]:
        try:
            con = sqlite3.connect(f"file:{body_db.path}?mode=ro", uri=True)
            try:
                for record in limited_ids:
                    if record.data_id % 5:
                        status = "invalid_pointer"
                        row_id: int | None = None
                    else:
                        row_id = record.data_id // 5
                        status = "resolved" if _sqlite_row_exists(con, body_db.table, "rowid", row_id) else "missing_target"
                    rows.append(
                        dereference_record(
                            id=f"dereference:{source.dict_id}:android-body:{record.record_index}",
                            kind="body_link",
                            method="android_rowid_times_5",
                            from_address=dense_anchor_address(source, record),
                            to_address=database_row_address(body_db.path, body_db.table, row_id),
                            status=status,
                            confidence="proven" if status == "resolved" else "strongly_inferred",
                            anchor_value=record.data_id,
                        )
                    )
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            rows.append(
                dereference_record(
                    id=f"dereference:{source.dict_id}:android-body:error",
                    kind="body_link",
                    method="android_rowid_times_5",
                    from_address=None,
                    to_address=resource_address(body_db.path),
                    status="error",
                    confidence="structural_only",
                    error=str(exc),
                )
            )

    sidecars = discover_renderer_sidecars(source.idx)
    if sidecars and raw_ids and not getattr(args, "deep_sidecars", False):
        for sidecar in sidecars[:1]:
            for record in limited_ids:
                rows.append(
                    dereference_record(
                        id=f"dereference:{source.dict_id}:rendererdb-unverified:{record.record_index}",
                        kind="body_link",
                        method="rendererdb_data_id",
                        from_address=dense_anchor_address(source, record),
                        to_address=database_row_address(sidecar.path, "t_contents", record.data_id),
                        status="unverified",
                        confidence="structural_only",
                        anchor_value=record.data_id,
                        storage=sidecar.storage,
                        note="Run dump-package-model with --deep-sidecars to verify renderer DB rows.",
                    )
                )

    if sidecars and getattr(args, "deep_sidecars", False):
        with tempfile.TemporaryDirectory(prefix="lv-model-deref-sidecar-") as tmp:
            tmp_path = Path(tmp)
            for sidecar in sidecars[:1]:
                try:
                    db_path = prepare_sidecar_database(sidecar, tmp_path, args)
                    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                except Exception as exc:
                    rows.append(
                        dereference_record(
                            id=f"dereference:{source.dict_id}:rendererdb:error",
                            kind="body_link",
                            method="rendererdb_data_id",
                            from_address=None,
                            to_address=resource_address(sidecar.path),
                            status="error",
                            confidence="structural_only",
                            error=str(exc),
                        )
                    )
                    continue
                try:
                    if table_exists(con, "t_contents"):
                        columns = t_contents_columns(con)
                        data_id_col = columns.get("f_DataId")
                        for record in limited_ids:
                            exists = bool(data_id_col and _sqlite_row_exists(con, "t_contents", data_id_col, record.data_id))
                            rows.append(
                                dereference_record(
                                    id=f"dereference:{source.dict_id}:rendererdb:{record.record_index}",
                                    kind="body_link",
                                    method="rendererdb_data_id",
                                    from_address=dense_anchor_address(source, record),
                                    to_address=database_row_address(sidecar.path, "t_contents", record.data_id),
                                    status="resolved" if exists else "missing_target",
                                    confidence="proven" if exists else "strongly_inferred",
                                    anchor_value=record.data_id,
                                    storage=sidecar.storage,
                                )
                            )
                    elif table_exists(con, "HONBUN"):
                        columns = honbun_columns(con)
                        id_col = columns.get("ID")
                        raw_slices = iter_entry_slices_with_boundaries(expanded)
                        query = f"select {quote_identifier(id_col)} from HONBUN order by {quote_identifier(id_col)}" if id_col else None
                        if query:
                            for index, ((start, _end), db_row) in enumerate(zip(raw_slices, con.execute(query)), start=1):
                                if _raw_id_limit(args) and index > _raw_id_limit(args):
                                    break
                                row_id = db_row[0]
                                rows.append(
                                    dereference_record(
                                        id=f"dereference:{source.dict_id}:rendererdb-honbun:{index}",
                                        kind="body_link",
                                        method="rendererdb_row_order",
                                        from_address=component_address_by_name(elements, "HONMON.DIC", start),
                                        to_address=database_row_address(sidecar.path, "HONBUN", row_id),
                                        status="resolved",
                                        confidence="strongly_inferred",
                                        entry_index=index,
                                        storage=sidecar.storage,
                                    )
                                )
                    else:
                        rows.append(
                            dereference_record(
                                id=f"dereference:{source.dict_id}:rendererdb:unsupported-schema",
                                kind="body_link",
                                method="rendererdb_data_id",
                                from_address=None,
                                to_address=resource_address(sidecar.path),
                                status="unsupported_schema",
                                confidence="structural_only",
                                storage=sidecar.storage,
                            )
                        )
                finally:
                    con.close()
    return rows


def index_pointer_dereferences(model: dict[str, Any], elements: list[SsedInfoElement]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate((model.get("indexes") or {}).get("samples") or [], start=1):
        if row.get("kind") != "leaf":
            continue
        component = str(row.get("component") or "")
        from_address = ModelAddress(
            kind=AddressKind.COMPONENT,
            component=component,
            block=row.get("logical_block"),
            selector=f"page:{row.get('page_index')}:row:{row.get('row_index')}",
        ).as_dict()
        for target_name, method in (("body", "index_body_pointer"), ("title", "index_title_pointer")):
            pointer = row.get(target_name)
            if not isinstance(pointer, dict):
                continue
            block = pointer.get("block")
            offset = pointer.get("offset")
            if not isinstance(block, int) or not isinstance(offset, int):
                continue
            rows.append(
                dereference_record(
                    id=f"dereference:{model['package']['dict_id']}:index:{index}:{target_name}",
                    kind="index_pointer",
                    method=method,
                    from_address=from_address,
                    to_address=logical_pointer_address(elements, block=block, offset=offset),
                    status="resolved",
                    confidence="strongly_inferred",
                    key=row.get("key"),
                    target_key=row.get("target_key"),
                )
            )
    return rows


def menu_destination_dereferences(model: dict[str, Any], elements: list[SsedInfoElement]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dict_id = str(model.get("package", {}).get("dict_id") or "")
    for record_index, record in enumerate((model.get("menus") or {}).get("samples") or [], start=1):
        component = str(record.get("component") or "MENU.DIC")
        for link_index, link in enumerate(record.get("links") or [], start=1):
            if not isinstance(link, dict):
                continue
            destination = link.get("destination")
            if not isinstance(destination, dict):
                continue
            block = destination.get("block")
            offset = destination.get("offset")
            from_address = component_address_by_name(elements, component, int(link.get("end_offset") or record.get("byte_end") or 0))
            is_null = bool(destination.get("is_null")) or (block == 0 and offset == 0)
            to_address = (
                logical_pointer_address(elements, block=block, offset=offset)
                if isinstance(block, int) and isinstance(offset, int) and not is_null
                else None
            )
            if is_null:
                dereference_status = "null"
            elif destination.get("target"):
                dereference_status = "resolved"
            else:
                dereference_status = "unresolved"
            rows.append(
                dereference_record(
                    id=f"dereference:{dict_id}:menu:{record_index}:{link_index}",
                    kind="menu_destination",
                    method="menu_destination",
                    from_address=from_address,
                    to_address=to_address,
                    status=dereference_status,
                    confidence="strongly_inferred",
                    label=link.get("label"),
                    path=record.get("path"),
                    payload=destination.get("payload"),
                    encoding=destination.get("encoding"),
                )
            )
    return rows


def media_dereferences(model: dict[str, Any], elements: list[SsedInfoElement]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dict_id = str(model.get("package", {}).get("dict_id") or "")
    honmon_element = next((element for element in elements if element.filename.upper() == "HONMON.DIC"), None)
    for component, method, records in (
        ("COLSCR.DIC", "packed_bcd_media_pointer", ((model.get("media") or {}).get("colscr") or {}).get("records") or []),
        ("PCMDATA.DIC", "packed_bcd_media_pointer", ((model.get("media") or {}).get("pcmdata") or {}).get("records") or []),
    ):
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict) or record.get("source") == "unreferenced":
                continue
            position = record.get("honmon_position")
            from_address = (
                component_address(honmon_element, int(position))
                if honmon_element is not None and isinstance(position, int)
                else None
            )
            block = record.get("block")
            offset = record.get("offset")
            to_address = (
                logical_pointer_address(elements, block=block, offset=offset)
                if isinstance(block, int) and isinstance(offset, int)
                else None
            )
            rows.append(
                dereference_record(
                    id=f"dereference:{dict_id}:media:{component.lower()}:{index}",
                    kind="media_reference",
                    method=method,
                    from_address=from_address,
                    to_address=to_address,
                    status="resolved" if record.get("valid") else "invalid_pointer",
                    confidence="proven" if record.get("valid") else "strongly_inferred",
                    component=component,
                    payload=record.get("payload"),
                    media_type=record.get("media_type"),
                    codec=record.get("codec"),
                    label=record.get("label"),
                    section_code=record.get("section_code"),
                )
            )
    return rows


def model_dereferences(
    source: DictionarySource,
    model: dict[str, Any],
    elements: list[SsedInfoElement],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(dense_anchor_and_body_dereferences(source, elements, args))
    rows.extend(index_pointer_dereferences(model, elements))
    rows.extend(menu_destination_dereferences(model, elements))
    rows.extend(media_dereferences(model, elements))
    return rows


def title_index_menu_model(idx: Path, args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "skip_row_models", False):
        _title, elements, _layout = parse_ssedinfo_with_layout(idx)
        title_components = [element.filename for element in elements if element.type in TITLE_TYPES and element.start]
        index_components = [element.filename for element in elements if element.type in INDEX_TYPES and element.start]
        menu_components = [element.filename for element in elements if element.filename.upper() == "MENU.DIC" and element.start]
        skipped = {
            "status": "skipped",
            "reason": "title/index/menu row model skipped by request",
        }
        return {
            "titles": {
                "summary": {**skipped, "component_count": len(title_components), "components": title_components},
                "samples": [],
            },
            "indexes": {
                "summary": {**skipped, "component_count": len(index_components), "components": index_components},
                "samples": [],
            },
            "menus": {
                "summary": {**skipped, "component_count": len(menu_components), "components": menu_components},
                "samples": [],
            },
        }

    with tempfile.TemporaryDirectory(prefix="lv-model-rows-") as tmp:
        tmp_path = Path(tmp)
        title_args = argparse.Namespace(out_dir=tmp_path, limit=args.title_limit, gaiji="h-placeholder")
        index_args = argparse.Namespace(
            out_dir=tmp_path,
            limit=args.index_limit,
            gaiji="h-placeholder",
            component=None,
            include_internal=args.include_internal_indexes,
        )
        menu_args = argparse.Namespace(out_dir=tmp_path, limit=args.menu_limit, gaiji="h-placeholder")
        titles = extract_titles_for_idx(idx, tmp_path, title_args)
        indexes = extract_indexes_for_idx(idx, tmp_path, index_args)
        menus = extract_menus_for_idx(idx, tmp_path, menu_args)
        return {
            "titles": {
                "summary": strip_temp_paths(titles, "titles_path"),
                "samples": read_jsonl_samples(Path(titles.get("titles_path", "")), args.sample_limit),
            },
            "indexes": {
                "summary": strip_temp_paths(indexes, "indexes_path"),
                "samples": read_jsonl_samples(Path(indexes.get("indexes_path", "")), args.sample_limit),
            },
            "menus": {
                "summary": strip_temp_paths(menus, "menus_path", "tree_path"),
                "samples": read_jsonl_samples(Path(menus.get("menus_path", "")), args.sample_limit),
            },
        }


def package_notes(model: dict[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    if model.get("wrapper", {}).get("markers", {}).get("sizk"):
        notes.append(issue("sizk_read_aloud", "SIZK package uses template-selector HONMON entries and loose playback sidecars."))
    media = model.get("media", {}).get("pcmdata", {})
    if media.get("media_type_counts", {}).get("unknown_audio_payload"):
        notes.append(issue("unclassified_pcmdata_payload", "PCMDATA has valid referenced ranges whose codec is not classified."))
    return notes


def collect_inconsistencies(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    classification = model.get("classification", {})
    for filename in classification.get("missing_components", []) or []:
        rows.append(issue("missing_component", "SSEDINFO declares a component file that is missing.", component=filename))
    honmon = model.get("honmon", {})
    decode = honmon.get("decode_aggregate", {})
    stats = decode.get("stats", {})
    for key in ("unknown_controls", "unknown_bytes", "invalid_jis_pairs", "truncated_controls", "truncated_gaiji"):
        if stats.get(key):
            rows.append(issue("honmon_decode_residual", f"HONMON decode has nonzero {key}.", metric=key, value=stats[key]))
    indexes = model.get("indexes", {}).get("summary", {}).get("aggregate", {})
    if indexes.get("unknown_leaf_bytes"):
        rows.append(
            issue(
                "index_unparsed_leaf_bytes",
                "Index parser left nonzero leaf bytes unclassified.",
                value=indexes["unknown_leaf_bytes"],
            )
        )
    entries = model.get("entry_spans", {})
    if entries.get("strict_failures"):
        rows.append(issue("entry_strict_failures", "Lossless entry parser had strict-mode failures.", value=entries["strict_failures"]))
    media = model.get("media", {})
    colscr = media.get("colscr", {})
    if colscr.get("invalid_records"):
        rows.append(issue("invalid_colscr_records", "COLSCR references failed to resolve.", value=colscr["invalid_records"]))
    pcmdata = media.get("pcmdata", {})
    if pcmdata.get("invalid_referenced_records"):
        rows.append(
            issue("invalid_pcmdata_records", "PCMDATA references failed to resolve.", value=pcmdata["invalid_referenced_records"])
        )
    sizk = model.get("families", {}).get("sizk")
    if isinstance(sizk, dict):
        for item in sizk.get("issues", []):
            rows.append(issue("sizk_issue", str(item)))
    return rows


def dump_package_model(source: DictionarySource, args: argparse.Namespace) -> dict[str, Any]:
    _title, elements, _layout = parse_ssedinfo_with_layout(source.idx)
    wrapper = detect_platform_wrapper(source)
    wrapper = {
        **wrapper,
        "package_family": normalize_package_family(wrapper.get("package_family")),
        "platform": normalize_platform(wrapper.get("platform")),
    }
    profile = profile_for_source(source, args)
    components = component_rows(source, profile)
    rows = title_index_menu_model(source.idx, args)

    families: dict[str, Any] = {}
    if wrapper["markers"].get("sizk"):
        families["sizk"] = inspect_sizk_package(source.idx.parent, include_playback_rows=args.include_playback_rows)
        families["sizk"].get("playback", {}).pop("rows", None)
        families["sizk"].get("playback", {}).pop("template_rows", None)

    model: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "model_version": 0,
        "stability": "research-draft",
        "package": {
            "dict_id": source.dict_id,
            "title": source.title,
            "path": str(source.idx.parent),
            "idx": str(source.idx),
            "honmon": str(source.honmon),
        },
        "wrapper": wrapper,
        "classification": normalize_classification({
            **profile.get("classification", {}),
            "package_family": wrapper["package_family"],
            "platform": wrapper["platform"],
        }),
        "components": components,
        "honmon": profile.get("honmon", {}),
        "entry_spans": entry_span_samples(source, args),
        "titles": rows["titles"],
        "indexes": rows["indexes"],
        "menus": rows["menus"],
        "gaiji": gaiji_resources(source, args),
        "resources": {
            **profile.get("resources", {}),
            "static_sidecars": static_package_resources(source, args),
        },
        "media": media_summaries(source, args),
        "sidecars": windows_sidecars(source, args),
        "families": families,
        "dereferences": [],
        "notes": [],
        "inconsistencies": [],
    }
    model["dereferences"] = model_dereferences(source, model, elements, args)
    model["readiness"] = build_model_readiness(model)
    model["writer_readiness"] = model["readiness"]["writer_readiness"]
    model["notes"] = package_notes(model)
    model["inconsistencies"] = collect_inconsistencies(model)
    return model


def dump_incomplete_package_model(idx: Path, args: argparse.Namespace, reason: str) -> dict[str, Any]:
    title, elements, _layout = parse_ssedinfo_with_layout(idx)
    wrapper = detect_platform_wrapper_for_idx(idx)
    wrapper = {
        **wrapper,
        "package_family": normalize_package_family(wrapper.get("package_family")),
        "platform": normalize_platform(wrapper.get("platform")),
    }
    profile = profile_for_idx(idx, args)
    components = component_rows_for_idx(idx, profile)
    rows = title_index_menu_model(idx, args)
    honmon_element = next((element for element in elements if element.filename.upper() == "HONMON.DIC"), None)
    honmon_path = find_case_insensitive(idx.parent, "HONMON.DIC")
    model: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "model_version": 0,
        "stability": "research-draft",
        "package": {
            "dict_id": dict_id_for_idx(idx),
            "title": title,
            "path": str(idx.parent),
            "idx": str(idx),
            "honmon": str(honmon_path) if honmon_path is not None else None,
        },
        "wrapper": wrapper,
        "classification": normalize_classification({
            **profile.get("classification", {}),
            "status": "incomplete",
            "package_family": wrapper["package_family"],
            "platform": wrapper["platform"],
        }),
        "components": components,
        "honmon": profile.get("honmon", {}),
        "entry_spans": {
            "schema": "logovista-decoded-entries-v0",
            "status": "missing_honmon_file" if honmon_element is not None else "no_honmon_component",
            "entries": [],
            "warnings": [reason],
        },
        "titles": rows["titles"],
        "indexes": rows["indexes"],
        "menus": rows["menus"],
        "gaiji": gaiji_resources_for_idx(idx, args),
        "resources": {
            **profile.get("resources", {}),
            "static_sidecars": static_package_resources_for_idx(idx, sample_limit=args.sample_limit),
        },
        "media": {
            "colscr": {"status": "not_scanned", "warnings": ["HONMON.DIC is missing; media references cannot be collected."]},
            "pcmdata": {"status": "not_scanned", "warnings": ["HONMON.DIC is missing; audio references cannot be collected."]},
        },
        "sidecars": windows_sidecars_for_idx(idx, args),
        "families": {},
        "dereferences": [],
        "notes": [
            issue(
                "incomplete_package",
                "Package has a parseable SSEDINFO catalog but cannot be decoded as a full dictionary because a required raw component is missing.",
                reason=reason,
            )
        ],
        "inconsistencies": [],
    }
    model["dereferences"] = [
        *index_pointer_dereferences(model, elements),
        *menu_destination_dereferences(model, elements),
        *media_dereferences(model, elements),
    ]
    model["readiness"] = build_model_readiness(model)
    model["writer_readiness"] = model["readiness"]["writer_readiness"]
    model["inconsistencies"] = collect_inconsistencies(model)
    if not any(row["kind"] == "missing_component" for row in model["inconsistencies"]):
        model["inconsistencies"].append(issue("incomplete_package", reason))
    return model


def _deferred_family_common(
    *,
    dict_id: str,
    title: str,
    package_path: Path,
    package_family: PackageFamily,
    platform: PlatformWrapper,
    body_source: BodySource,
    markers: dict[str, Any],
    families: dict[str, Any],
    resources: dict[str, Any],
    components: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "model_version": 0,
        "stability": "research-draft",
        "package": {
            "dict_id": dict_id,
            "title": title,
            "path": str(package_path),
            "idx": None,
            "honmon": None,
        },
        "wrapper": {
            "package_family": package_family.value,
            "platform": platform.value,
            "markers": markers,
        },
        "classification": normalize_classification(
            {
                "status": "deferred",
                "package_family": package_family.value,
                "platform": platform.value,
                "honmon_shape": HonmonShape.MISSING.value,
                "body_source_hint": body_source.value,
                "missing_components": [],
            }
        ),
        "components": components or [],
        "honmon": {
            "status": "not_applicable",
            "shape": HonmonShape.MISSING.value,
            "decode_aggregate": {"stats": {}},
        },
        "entry_spans": {
            "schema": "logovista-decoded-entries-v0",
            "status": "not_applicable",
            "entries": [],
            "warnings": [f"{package_family.value} is a separate package family; SSED HONMON parsing is not applicable."],
        },
        "titles": {"summary": {"status": "not_applicable"}, "samples": []},
        "indexes": {"summary": {"status": "not_applicable"}, "samples": []},
        "menus": {"summary": {"status": "not_applicable"}, "samples": []},
        "gaiji": {"status": "not_scanned", "reason": f"{package_family.value} support is deferred."},
        "resources": resources,
        "media": {
            "colscr": {"status": "not_applicable"},
            "pcmdata": {"status": "not_applicable"},
        },
        "sidecars": {},
        "families": families,
        "dereferences": [],
        "notes": notes or [],
        "inconsistencies": [],
    }
    model["readiness"] = build_model_readiness(model)
    model["writer_readiness"] = model["readiness"]["writer_readiness"]
    return model


def dump_lved_deferred_model(payload: Path, args: argparse.Namespace) -> dict[str, Any]:
    package = payload.parent
    dict_id = package.name.removeprefix("_DCT_")
    report = inspect_lved_roots([payload], jobs=1)
    payload_rows = report.get("payloads", [])
    components = [
        normalize_component_row(
            {
                "filename": Path(row.get("path", payload)).name,
                "path": row.get("path"),
                "role": "component",
                "type": "lved_sqlcipher_payload",
                "bytes": row.get("size"),
                "storage": row.get("classification"),
                "sha256": row.get("sha256"),
                "inferred_dict_code": row.get("inferred_dict_code"),
            }
        )
        for row in payload_rows
        if isinstance(row, dict)
    ]
    markers = {
        "sqlcipher_payloads": len(payload_rows),
        "main_data": payload.name.lower() == "main.data",
        "dbc": payload.suffix.lower() == ".dbc",
        "res_dir": (package / "res").is_dir(),
    }
    return _deferred_family_common(
        dict_id=dict_id,
        title="",
        package_path=package,
        package_family=PackageFamily.LVED_SQLCIPHER,
        platform=PlatformWrapper.WINDOWS,
        body_source=BodySource.LVED_SQLCIPHER,
        markers=markers,
        families={"lved": report},
        resources={"static_sidecars": static_resources_for_package(package, sample_limit=args.sample_limit)},
        components=components,
        notes=[
            issue(
                "deferred_lved_sqlcipher",
                "LVED is a separate SQLCipher/SQLite package family. It is classified here but not parsed through SSED/HONMON.",
                payload=str(payload),
            )
        ],
    )


def dump_multiview_deferred_model(package: Path, args: argparse.Namespace) -> dict[str, Any]:
    report = inspect_multiview_package(package)
    payloads = report.get("payloads", [])
    components = [
        normalize_component_row(
            {
                "filename": row.get("name"),
                "path": row.get("path"),
                "role": "component",
                "type": "multiview_sqlite_payload",
                "bytes": row.get("size"),
                "storage": row.get("storage"),
                "content_kind": row.get("content_kind"),
                "role_hint": row.get("role"),
                "sha256": row.get("sha256"),
            }
        )
        for row in payloads
        if isinstance(row, dict)
    ]
    if report.get("idx"):
        for row in report["idx"].get("components", []):
            components.append(
                normalize_component_row(
                    {
                        "filename": row.get("filename"),
                        "role": "multi_descriptor",
                        "type": row.get("type"),
                        "physical_file_present": row.get("physical_file_present"),
                    }
                )
            )
    markers = {
        "menuData_xml": report.get("menu") is not None,
        "payloads": len(payloads),
        "idx_facade": report.get("idx") is not None,
    }
    return _deferred_family_common(
        dict_id=str(report.get("dict_id") or package.name.removeprefix("_DCT_")),
        title=str((report.get("idx") or {}).get("title") or ""),
        package_path=package,
        package_family=PackageFamily.MULTIVIEW_SQLITE,
        platform=PlatformWrapper.WINDOWS,
        body_source=BodySource.MULTIVIEW_SQLITE,
        markers=markers,
        families={"multiview": report},
        resources={"static_sidecars": static_resources_for_package(package, sample_limit=args.sample_limit)},
        components=components,
        notes=[
            issue(
                "deferred_multiview_sqlite",
                "LVLMultiView is a separate SQLite-backed package family. It is classified here but not parsed through SSED/HONMON.",
                package=str(package),
            )
        ],
    )


def dump_package_model_for_path(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    try:
        source = select_single_source(root, dict_id=args.dict)
    except ValueError as exc:
        multiview = select_single_multiview_package(root, dict_id=args.dict)
        if multiview is not None:
            return dump_multiview_deferred_model(multiview, args)
        lved_payload = select_single_lved_payload(root, dict_id=args.dict)
        if lved_payload is not None:
            return dump_lved_deferred_model(lved_payload, args)
        idx = select_single_idx(root, dict_id=args.dict)
        return dump_incomplete_package_model(idx, args, str(exc))
    return dump_package_model(source, args)


def write_package_model(model: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{model['package']['dict_id']}_decoded_model_v0.json"
    path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    return len(rows)


def _chunk_ref(path: str, records: int) -> dict[str, Any]:
    return {"path": path, "records": records}


def gaiji_chunk_rows(gaiji: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("uni_resources", "ga16_resources"):
        for row in gaiji.get(key, []) or []:
            if isinstance(row, dict):
                rows.append({"kind": key.removesuffix("s"), **row})
    for key, row in (gaiji.get("image_resources", {}).get("samples") or {}).items():
        if isinstance(row, dict):
            rows.append({"kind": "image_resource", "key": key, **row})
    readiness = gaiji.get("readiness")
    if isinstance(readiness, dict):
        rows.append({"kind": "readiness", **readiness})
    return rows


def media_ref_chunk_rows(media: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("colscr", "pcmdata"):
        value = media.get(key)
        if isinstance(value, dict):
            summary = dict(value)
            summary.pop("records", None)
            rows.append({"component": key, "kind": "summary", **summary})
    return rows


def media_record_chunk_rows(media: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in ("colscr", "pcmdata"):
        value = media.get(component)
        if not isinstance(value, dict):
            continue
        for key, records in value.items():
            if not isinstance(records, list):
                continue
            for record in records:
                if isinstance(record, dict):
                    rows.append({"component": component, "source_list": key, **record})
    return rows


def dereference_chunk_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in model.get("dereferences", []) or [] if isinstance(row, dict)]


def issue_chunk_rows(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_key in ("notes", "inconsistencies"):
        for row in model.get(source_key, []) or []:
            if isinstance(row, dict):
                rows.append({"source": source_key, **row})
    return rows


def metrics_for_chunked_model(model: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    return {
        "schema": "logovista-decoded-model-metrics-v0",
        "package": model.get("package", {}),
        "classification": model.get("classification", {}),
        "counts": counts,
        "readiness": model.get("readiness", {}),
        "writer_readiness": model.get("writer_readiness", {}),
        "entry_spans": {
            key: value
            for key, value in (model.get("entry_spans") or {}).items()
            if key != "entries"
        },
    }


def chunked_package_summary(model: dict[str, Any], refs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entry_spans = dict(model.get("entry_spans") or {})
    entry_spans.pop("entries", None)
    entry_spans["entries_ref"] = refs["entries"]

    titles = dict(model.get("titles") or {})
    titles.pop("samples", None)
    titles["samples_ref"] = refs["titles"]
    indexes = dict(model.get("indexes") or {})
    indexes.pop("samples", None)
    indexes["samples_ref"] = refs["indexes"]
    menus = dict(model.get("menus") or {})
    menus.pop("samples", None)
    menus["samples_ref"] = refs["menus"]

    return {
        "schema": MODEL_SCHEMA,
        "model_version": model.get("model_version", 0),
        "stability": model.get("stability", "research-draft"),
        "storage": {
            "mode": "chunked-jsonl",
            "layout": "logovista-decoded-model-chunked-v0",
        },
        "package": model.get("package", {}),
        "wrapper": model.get("wrapper", {}),
        "classification": model.get("classification", {}),
        "components_ref": refs["components"],
        "components": {"status": "externalized", **refs["components"]},
        "honmon": model.get("honmon", {}),
        "entry_spans": entry_spans,
        "titles": titles,
        "indexes": indexes,
        "menus": menus,
        "gaiji": {
            key: value
            for key, value in (model.get("gaiji") or {}).items()
            if key not in {"uni_resources", "ga16_resources"}
        }
        | {"resources_ref": refs["gaiji"]},
        "resources": model.get("resources", {}),
        "media": {"summary": model.get("media", {}), "refs_ref": refs["media_refs"], "records_ref": refs["media_records"]},
        "sidecars": model.get("sidecars", {}),
        "families": model.get("families", {}),
        "dereferences_ref": refs["dereferences"],
        "issues_ref": refs["issues"],
        "metrics_ref": refs["metrics"],
        "readiness": model.get("readiness", {}),
        "writer_readiness": model.get("writer_readiness", {}),
        "notes": {"status": "externalized", **refs["issues"]},
        "inconsistencies": {"status": "externalized", **refs["issues"]},
    }


def write_package_model_chunked_to_dir(model: dict[str, Any], bundle_dir: Path) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    chunks = {
        "components": ("components.jsonl", list(model.get("components") or [])),
        "entries": ("entries.jsonl", list((model.get("entry_spans") or {}).get("entries") or [])),
        "titles": ("titles.jsonl", list((model.get("titles") or {}).get("samples") or [])),
        "indexes": ("indexes.jsonl", list((model.get("indexes") or {}).get("samples") or [])),
        "menus": ("menus.jsonl", list((model.get("menus") or {}).get("samples") or [])),
        "gaiji": ("gaiji.jsonl", gaiji_chunk_rows(model.get("gaiji") or {})),
        "media_refs": ("media_refs.jsonl", media_ref_chunk_rows(model.get("media") or {})),
        "media_records": ("media_records.jsonl", media_record_chunk_rows(model.get("media") or {})),
        "dereferences": ("dereferences.jsonl", dereference_chunk_rows(model)),
        "issues": ("issues.jsonl", issue_chunk_rows(model)),
    }
    refs: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for key, (filename, rows) in chunks.items():
        count = write_jsonl(bundle_dir / filename, rows)
        refs[key] = _chunk_ref(filename, count)
        counts[key] = count
    metrics = metrics_for_chunked_model(model, counts)
    metrics_path = bundle_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    refs["metrics"] = _chunk_ref("metrics.json", 1)
    package = chunked_package_summary(model, refs)
    package_path = bundle_dir / "package.json"
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package_path


def write_package_model_chunked(model: dict[str, Any], out_dir: Path) -> Path:
    dict_id = model["package"]["dict_id"]
    safe_dict_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(dict_id))
    return write_package_model_chunked_to_dir(model, out_dir / f"{safe_dict_id}_decoded_model_v0")
