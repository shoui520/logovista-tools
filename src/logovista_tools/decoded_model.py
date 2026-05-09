"""Decoded LogoVista Model v0 package aggregation.

This module is not a new dictionary format. It is a research model that pulls
the current parser outputs into one package-level JSON object so contradictions
and remaining gaps are visible in one place.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from .colscr import extract_colscr_for_source
from .entries import DictionarySource, discover_dictionaries, iter_entry_slices_with_boundaries
from .gaiji import candidate_gaiji_paths, load_gaiji_profile, parse_ga16_resource, parse_uni_resource
from .indexes import INDEX_TYPES, collect_index_body_offsets_for_idx, extract_indexes_for_idx
from .menus import extract_menus_for_idx
from .pcmdata import extract_pcmdata_for_source
from .profiles import ProfileTarget, build_profile
from .resources import candidate_image_dirs, load_image_resource_profile, relative_image_source
from .sizk import inspect_sizk_package, is_sizk_package
from .spans import LosslessDecodeError, decode_lossless_spans
from .ssed import BLOCK_SIZE, expand_sseddata_file_with_storage, find_case_insensitive, parse_ssedinfo_with_layout
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
    candidates: list[Path] = []
    if root.is_file() and root.suffix.lower() == ".idx":
        candidates.append(root)
    elif root.is_dir():
        candidates.extend(root.rglob("*.IDX"))
        candidates.extend(root.rglob("*.idx"))
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
        "htmls": (package / "HTMLs").is_dir(),
        "hc_renderer": bool(discover_hc_renderer_files([package])),
        "vlpljbl": bool(discover_vlpljbl_files([package])),
        "numeric_aux_indexes": bool(discover_numeric_aux_indexes(idx)),
        "sizk": is_sizk_package(package),
    }
    if markers["sizk"]:
        platform = "windows-sizk"
        family = "ssed-sizk-read-aloud"
    elif markers["android_conf"]:
        platform = "android"
        family = "ssed"
    elif markers["dictlist_plist"] or markers["ios_gaiji_plist"]:
        platform = "ios"
        family = "ssed"
    elif markers["exinfo"] or markers["hc_renderer"] or markers["vlpljbl"] or markers["numeric_aux_indexes"]:
        platform = "windows"
        family = "ssed"
    else:
        platform = "unknown"
        family = "ssed"
    return {"package_family": family, "platform": platform, "markers": markers}


def detect_platform_wrapper(source: DictionarySource) -> dict[str, Any]:
    return detect_platform_wrapper_for_idx(source.idx)


def component_address(element: Any, component_offset: int = 0) -> dict[str, Any]:
    return {
        "kind": "component",
        "component": element.filename,
        "component_type": f"{element.type:02x}",
        "block": element.start + component_offset // BLOCK_SIZE if element.start else None,
        "offset": component_offset % BLOCK_SIZE,
        "component_offset": component_offset,
        "absolute_book_offset": (element.start - 1) * BLOCK_SIZE + component_offset if element.start else None,
    }


def component_rows_for_idx(idx: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
    _title, elements, _layout = parse_ssedinfo_with_layout(idx)
    by_name = {row["filename"].upper(): row for row in profile.get("catalog", {}).get("components", [])}
    rows: list[dict[str, Any]] = []
    for element in elements:
        row = dict(by_name.get(element.filename.upper(), {}))
        row.setdefault("filename", element.filename)
        row.setdefault("type", f"{element.type:02x}")
        row["address"] = component_address(element)
        rows.append(row)
    return rows


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


def gaiji_resources_for_idx(idx: Path) -> dict[str, Any]:
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
        if not (element.filename.upper().startswith("GA16") or element.filename.upper().startswith("GAI16")):
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
    return {
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


def gaiji_resources(source: DictionarySource) -> dict[str, Any]:
    return gaiji_resources_for_idx(source.idx)


def static_package_resources_for_idx(idx: Path, sample_limit: int = 50) -> dict[str, Any]:
    package = idx.parent
    resource_dirs = []
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
        "templates",
    }
    for path in sorted(package.iterdir()):
        if path.is_dir() and path.name.lower() in known_dir_names:
            resource_dirs.append(path)
    for path in candidate_image_dirs(package):
        if path.is_dir() and path not in resource_dirs:
            resource_dirs.append(path)

    root_files = [
        path
        for path in package.iterdir()
        if path.is_file() and path.suffix.lower() in {".html", ".htm", ".css", ".js", ".gif", ".png", ".jpg", ".jpeg", ".svg"}
    ]
    files = list(root_files)
    for directory in resource_dirs:
        files.extend(path for path in directory.rglob("*") if path.is_file())

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
        return {
            "colscr": strip_temp_paths(colscr, "manifest_path"),
            "pcmdata": strip_temp_paths(pcmdata, "manifest_path"),
        }


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
    classification = model.get("classification", {})
    honmon_shape = classification.get("honmon_shape")
    body_source = classification.get("body_source_hint")
    if honmon_shape in {"dense_marker_table", "dense_numeric_id_table", "dense_token_table"} or body_source == "honmon_anchor_dereference":
        notes.append(
            issue(
                "dense_honmon",
                "HONMON appears to be an anchor/table layer; complete body rendering needs a dereference path.",
                honmon_shape=honmon_shape,
                body_source_hint=body_source,
            )
        )
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
    wrapper = detect_platform_wrapper(source)
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
        "classification": {
            **profile.get("classification", {}),
            "package_family": wrapper["package_family"],
            "platform": wrapper["platform"],
        },
        "components": components,
        "honmon": profile.get("honmon", {}),
        "entry_spans": entry_span_samples(source, args),
        "titles": rows["titles"],
        "indexes": rows["indexes"],
        "menus": rows["menus"],
        "gaiji": gaiji_resources(source),
        "resources": {
            **profile.get("resources", {}),
            "static_sidecars": static_package_resources(source, args),
        },
        "media": media_summaries(source, args),
        "sidecars": windows_sidecars(source, args),
        "families": families,
        "notes": [],
        "inconsistencies": [],
    }
    model["notes"] = package_notes(model)
    model["inconsistencies"] = collect_inconsistencies(model)
    return model


def dump_incomplete_package_model(idx: Path, args: argparse.Namespace, reason: str) -> dict[str, Any]:
    title, elements, _layout = parse_ssedinfo_with_layout(idx)
    wrapper = detect_platform_wrapper_for_idx(idx)
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
        "classification": {
            **profile.get("classification", {}),
            "status": "incomplete",
            "package_family": wrapper["package_family"],
            "platform": wrapper["platform"],
        },
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
        "gaiji": gaiji_resources_for_idx(idx),
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
        "notes": [
            issue(
                "incomplete_package",
                "Package has a parseable SSEDINFO catalog but cannot be decoded as a full dictionary because a required raw component is missing.",
                reason=reason,
            )
        ],
        "inconsistencies": [],
    }
    model["inconsistencies"] = collect_inconsistencies(model)
    if not any(row["kind"] == "missing_component" for row in model["inconsistencies"]):
        model["inconsistencies"].append(issue("incomplete_package", reason))
    return model


def dump_package_model_for_path(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    try:
        source = select_single_source(root, dict_id=args.dict)
    except ValueError as exc:
        idx = select_single_idx(root, dict_id=args.dict)
        return dump_incomplete_package_model(idx, args, str(exc))
    return dump_package_model(source, args)


def write_package_model(model: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{model['package']['dict_id']}_decoded_model_v0.json"
    path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
