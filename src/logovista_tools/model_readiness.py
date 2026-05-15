"""Authoritative readiness derivation for Decoded LogoVista Model v0."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .model_types import BodySource, HonmonShape, PackageFamily, ReadinessStatus, StatusRecord, WriterStatus


CAPABILITY_FIELDS = [
    "raw_honmon_body",
    "indexes_fully_parsed",
    "titles_fully_parsed",
    "gaiji_fully_resolved",
    "media_refs_resolved",
    "menu_pointers_resolved",
]

SSED_PACKAGE_FAMILIES = {
    PackageFamily.SSED.value,
    PackageFamily.MIXED.value,
}


def package_family(model: dict[str, Any]) -> str:
    return str(
        model.get("classification", {}).get("package_family")
        or model.get("wrapper", {}).get("package_family")
        or PackageFamily.UNKNOWN.value
    )


def is_ssed_family(model: dict[str, Any]) -> bool:
    return package_family(model) in SSED_PACKAGE_FAMILIES


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def readiness_rank(status: str) -> int:
    return {
        ReadinessStatus.YES.value: 0,
        ReadinessStatus.NA.value: 1,
        ReadinessStatus.PARTIAL.value: 2,
        ReadinessStatus.UNKNOWN.value: 3,
        ReadinessStatus.NO.value: 4,
    }.get(status, 3)


def writer_rank(status: str) -> int:
    return {
        WriterStatus.GREEN.value: 0,
        WriterStatus.GRAY.value: 1,
        WriterStatus.YELLOW.value: 2,
        WriterStatus.UNKNOWN.value: 3,
        WriterStatus.RED.value: 4,
    }.get(status, 3)


def worst_writer_status(*statuses: str) -> str:
    return max(statuses, key=writer_rank) if statuses else WriterStatus.UNKNOWN.value


def status(status: ReadinessStatus, reason: str, **metrics: Any) -> dict[str, Any]:
    clean_metrics = {key: value for key, value in metrics.items() if value is not None}
    return StatusRecord(status=status, reason=reason, metrics=clean_metrics or None).as_dict()


def _component_count(model: dict[str, Any], role: str) -> int:
    role_counts = model.get("honmon", {}).get("catalog_role_counts")
    if isinstance(role_counts, dict):
        return as_int(role_counts.get(role))
    return sum(1 for row in model.get("components", []) if row.get("role") == role)


def raw_honmon_capability(model: dict[str, Any]) -> dict[str, Any]:
    classification = model.get("classification", {})
    body_hint = str(classification.get("body_source_hint") or "")
    shape = str(classification.get("honmon_shape") or model.get("honmon", {}).get("shape") or "")
    entry_status = str(model.get("entry_spans", {}).get("status") or "")
    if body_hint == BodySource.HONMON.value:
        return status(ReadinessStatus.YES, f"body_source_hint={body_hint}; shape={shape}", body_source_hint=body_hint, honmon_shape=shape)
    if body_hint == BodySource.HONMON_ANCHOR_DEREFERENCE.value:
        return status(ReadinessStatus.NO, f"raw HONMON is an anchor/dereference layer; shape={shape}", body_source_hint=body_hint, honmon_shape=shape)
    if body_hint == BodySource.NONE.value or entry_status in {"missing_honmon_file", "no_honmon_component"}:
        return status(ReadinessStatus.NO, entry_status or "no raw body source", body_source_hint=body_hint, honmon_shape=shape)
    if shape in {
        HonmonShape.BODY_STREAM_INDEXED.value,
        HonmonShape.BODY_STREAM_MARKER_SLICED.value,
        HonmonShape.MARKER_RICH_TEXT_STREAM.value,
        HonmonShape.TEXT_STREAM_WITHOUT_ENTRY_MARKERS.value,
    }:
        return status(ReadinessStatus.PARTIAL, f"text-like HONMON but final body source is not authoritative; shape={shape}", body_source_hint=body_hint, honmon_shape=shape)
    if shape in {
        HonmonShape.DENSE_MARKER_TABLE.value,
        HonmonShape.DENSE_NUMERIC_ID_TABLE.value,
        HonmonShape.DENSE_TOKEN_TABLE.value,
    }:
        return status(ReadinessStatus.NO, f"dense HONMON shape={shape}", body_source_hint=body_hint, honmon_shape=shape)
    return status(ReadinessStatus.UNKNOWN, f"body_source_hint={body_hint or 'unknown'}; shape={shape or 'unknown'}", body_source_hint=body_hint, honmon_shape=shape)


def indexes_capability(model: dict[str, Any]) -> dict[str, Any]:
    summary = model.get("indexes", {}).get("summary", {})
    if summary.get("status") == "skipped":
        return status(ReadinessStatus.UNKNOWN, "index row model skipped")
    components = summary.get("index_components") or []
    boundary_count = as_int(model.get("honmon", {}).get("index_boundary_offsets")) or as_int(model.get("entry_spans", {}).get("index_boundary_offsets"))
    if not components and not boundary_count:
        return status(ReadinessStatus.NA, "no structured index components reported")
    unknown_leaf = sum(as_int(row.get("unknown_leaf_bytes")) for row in components if isinstance(row, dict))
    warnings = sum(len(row.get("warnings") or []) for row in components if isinstance(row, dict))
    if unknown_leaf or warnings:
        return status(ReadinessStatus.PARTIAL, f"unknown_leaf_bytes={unknown_leaf}; warnings={warnings}", unknown_leaf_bytes=unknown_leaf, warnings=warnings)
    return status(ReadinessStatus.YES, f"index_components={len(components)}", index_components=len(components), index_boundary_offsets=boundary_count)


def titles_capability(model: dict[str, Any]) -> dict[str, Any]:
    summary = model.get("titles", {}).get("summary", {})
    if summary.get("status") == "skipped":
        return status(ReadinessStatus.UNKNOWN, "title row model skipped")
    components = summary.get("title_components") or []
    if not components:
        return status(ReadinessStatus.NA, "no title components")
    warnings = sum(len(row.get("warnings") or []) for row in components if isinstance(row, dict))
    errors = sum(1 for row in components if isinstance(row, dict) and row.get("error"))
    if errors:
        return status(ReadinessStatus.NO, f"title component errors={errors}", errors=errors)
    if warnings:
        return status(ReadinessStatus.PARTIAL, f"title component warnings={warnings}", warnings=warnings)
    return status(ReadinessStatus.YES, f"title_components={len(components)}", title_components=len(components), lines_emitted=as_int(summary.get("lines_emitted")))


def menus_capability(model: dict[str, Any]) -> dict[str, Any]:
    summary = model.get("menus", {}).get("summary", {})
    if summary.get("status") == "skipped":
        return status(ReadinessStatus.UNKNOWN, "menu row model skipped")
    components = summary.get("menu_components") or []
    if not components:
        return status(ReadinessStatus.NA, "no MENU.DIC")
    total_destinations = 0
    null_destinations = 0
    resolved_destinations = 0
    unresolved_destinations = 0
    warnings = 0
    errors = 0
    for row in components:
        if not isinstance(row, dict):
            continue
        row_destinations = as_int(row.get("destinations"))
        row_null = as_int(row.get("null_destinations"))
        row_resolved = as_int(row.get("resolved_destinations"))
        legacy_null_selector = (
            "null_destinations" not in row
            and "unresolved_destinations" not in row
            and str(row.get("component") or "").upper().startswith(("MUL", "MULTI"))
            and row_destinations
            and not row_resolved
            and not row.get("target_kinds")
        )
        if legacy_null_selector:
            row_null = row_destinations

        total_destinations += row_destinations
        null_destinations += row_null
        resolved_destinations += row_resolved
        if "unresolved_destinations" in row:
            unresolved_destinations += as_int(row.get("unresolved_destinations"))
        else:
            unresolved_destinations += max(
                0,
                row_destinations - row_resolved - row_null,
            )
        warnings += len(row.get("warnings") or [])
        errors += 1 if row.get("error") else 0
    if errors:
        return status(ReadinessStatus.NO, f"menu component errors={errors}", errors=errors)
    if warnings or unresolved_destinations:
        return status(
            ReadinessStatus.PARTIAL,
            (
                f"destinations={total_destinations}; resolved={resolved_destinations}; "
                f"null={null_destinations}; unresolved={unresolved_destinations}; warnings={warnings}"
            ),
            destinations=total_destinations,
            null_destinations=null_destinations,
            resolved_destinations=resolved_destinations,
            unresolved_destinations=unresolved_destinations,
            warnings=warnings,
        )
    return status(
        ReadinessStatus.YES,
        f"destinations={total_destinations}; resolved={resolved_destinations}; null={null_destinations}",
        destinations=total_destinations,
        null_destinations=null_destinations,
        resolved_destinations=resolved_destinations,
        unresolved_destinations=0,
    )


def gaiji_capability(model: dict[str, Any]) -> dict[str, Any]:
    readiness = model.get("gaiji", {}).get("readiness")
    if isinstance(readiness, dict) and readiness.get("readiness_status"):
        raw_occurrences = as_int(readiness.get("raw_occurrences"))
        unresolved = as_int(readiness.get("display_unresolved_occurrences"))
        readiness_status = str(readiness.get("readiness_status") or ReadinessStatus.UNKNOWN.value)
        return status(
            ReadinessStatus(readiness_status) if readiness_status in {item.value for item in ReadinessStatus} else ReadinessStatus.UNKNOWN,
            (
                f"gaiji_readiness={readiness_status}; raw={raw_occurrences}; "
                f"display_unresolved={unresolved}; "
                f"formatting_helper_candidates={as_int(readiness.get('formatting_helper_candidate_occurrences'))}; "
                f"search_fallback_missing={as_int(readiness.get('search_fallback_missing_occurrences'))}"
            ),
            raw_occurrences=raw_occurrences,
            display_unresolved_occurrences=unresolved,
            formatting_helper_candidate_occurrences=as_int(readiness.get("formatting_helper_candidate_occurrences")),
            search_fallback_missing_occurrences=as_int(readiness.get("search_fallback_missing_occurrences")),
        )

    stats = model.get("honmon", {}).get("decode_aggregate", {}).get("stats", {})
    entry_stats = model.get("entry_spans", {}).get("decode_aggregate", {}).get("stats", {})
    gaiji_total = as_int(stats.get("gaiji")) or as_int(entry_stats.get("gaiji"))
    unresolved = as_int(stats.get("gaiji_unresolved")) or as_int(entry_stats.get("gaiji_unresolved"))
    resources = model.get("gaiji", {})
    profile = resources.get("profile", {})
    ga16_count = sum(as_int(row.get("count")) for row in resources.get("ga16_resources", []) if isinstance(row, dict))
    image_count = as_int(resources.get("image_resources", {}).get("resource_count"))
    mapped = as_int(profile.get("merged_map_entries"))
    if not gaiji_total and not mapped and not ga16_count and not image_count:
        return status(ReadinessStatus.NA, "no gaiji occurrences/resources reported", raw_occurrences=0)
    if unresolved == 0:
        return status(ReadinessStatus.YES, f"gaiji_total={gaiji_total}; unresolved=0", raw_occurrences=gaiji_total, display_unresolved_occurrences=0)
    if mapped or ga16_count or image_count:
        return status(
            ReadinessStatus.PARTIAL,
            f"gaiji_total={gaiji_total}; unresolved={unresolved}; mapped={mapped}; ga16_glyphs={ga16_count}; image_resources={image_count}",
            raw_occurrences=gaiji_total,
            display_unresolved_occurrences=unresolved,
            mapped_entries=mapped,
            ga16_glyphs=ga16_count,
            image_resources=image_count,
        )
    return status(ReadinessStatus.NO, f"gaiji_total={gaiji_total}; unresolved={unresolved}", raw_occurrences=gaiji_total, display_unresolved_occurrences=unresolved)


def media_capability(model: dict[str, Any]) -> dict[str, Any]:
    media = model.get("media", {})
    summary = media.get("summary") if isinstance(media.get("summary"), dict) else {}
    colscr = media.get("colscr") or summary.get("colscr") or {}
    pcmdata = media.get("pcmdata") or summary.get("pcmdata") or {}
    refs = as_int(colscr.get("media_references")) + as_int(pcmdata.get("audio_references"))
    components = sum(1 for row in (colscr, pcmdata) if row and row.get("status") != "not_scanned" and (row.get("colscr") or row.get("pcmdata")))
    if not refs and not components:
        return status(ReadinessStatus.NA, "no COLSCR/PCMDATA references/components")
    invalid = as_int(colscr.get("invalid_records")) + as_int(pcmdata.get("invalid_referenced_records"))
    unclassified = as_int((pcmdata.get("media_type_counts") or {}).get("unknown_audio_payload"))
    if invalid:
        return status(ReadinessStatus.NO, f"invalid media records={invalid}", references=refs, invalid_records=invalid)
    if unclassified:
        return status(ReadinessStatus.PARTIAL, f"unclassified_audio={unclassified}", references=refs, unclassified_audio=unclassified)
    return status(ReadinessStatus.YES, f"media_references={refs}", references=refs, components=components)


def unknown_text_metrics(model: dict[str, Any]) -> dict[str, int]:
    stats = model.get("honmon", {}).get("decode_aggregate", {}).get("stats", {})
    entry_stats = model.get("entry_spans", {}).get("decode_aggregate", {}).get("stats", {})
    source = stats or entry_stats
    return {
        "unknown_controls": as_int(source.get("unknown_controls")),
        "unknown_bytes": as_int(source.get("unknown_bytes")),
        "structural_text_issues": (
            as_int(source.get("invalid_jis_pairs"))
            + as_int(source.get("truncated_controls"))
            + as_int(source.get("truncated_gaiji"))
        ),
    }


def component_parse_errors(model: dict[str, Any]) -> int:
    components = model.get("components", [])
    if not isinstance(components, list):
        readiness_metrics = model.get("readiness", {}).get("metrics", {})
        return as_int(readiness_metrics.get("component_parse_errors"))
    errors = 0
    for component in components:
        if not isinstance(component, dict):
            continue
        expected_raw_resource = (
            component.get("role") == "gaiji_bitmap"
            and isinstance(component.get("storage_error"), str)
            and str(component["storage_error"]).startswith("not SSEDDATA:")
        )
        if expected_raw_resource:
            continue
        if component.get("storage_error") or component.get("status") == "parse_error":
            errors += 1
    return errors


READINESS_PROFILES = [
    "author_core_ssed_v0",
    "lossless_repack_existing",
    "export_existing",
    "read_existing",
]


def unique_blockers(blockers: list[str]) -> list[str]:
    return list(dict.fromkeys(blockers))


def _status_is_blocked(row: dict[str, Any], key: str) -> bool:
    return row.get(key) in {ReadinessStatus.NO.value, ReadinessStatus.PARTIAL.value, ReadinessStatus.UNKNOWN.value}


def read_existing_blockers(row: dict[str, Any]) -> list[str]:
    """Blockers for reading/searching an existing SSED package.

    This profile is about whether the package can be consumed as-is. It does
    not require raw HONMON body text, because dense-HONMON and sidecar-backed
    products are still real readable packages when their dereference path is
    understood.
    """

    blockers: list[str] = []
    if row["raw_honmon_body"] == ReadinessStatus.UNKNOWN.value:
        blockers.append("body_source_unknown")
    if _status_is_blocked(row, "indexes_fully_parsed"):
        blockers.append("indexes_not_fully_parsed")
    if row["unknown_controls"] or row["unknown_bytes"] or row["structural_text_issues"]:
        blockers.append("unknown_or_structural_text_issues")
    if row["component_parse_errors"]:
        blockers.append("component_parse_errors")
    return unique_blockers(blockers)


def export_existing_blockers(row: dict[str, Any]) -> list[str]:
    """Blockers for exporting an existing SSED dictionary to another format."""

    blockers: list[str] = []
    if row["raw_honmon_body"] == ReadinessStatus.NO.value:
        blockers.append("body_requires_sidecar_or_is_missing")
    elif row["raw_honmon_body"] == ReadinessStatus.UNKNOWN.value:
        blockers.append("body_source_unknown")
    if _status_is_blocked(row, "indexes_fully_parsed"):
        blockers.append("indexes_not_fully_parsed")
    if _status_is_blocked(row, "gaiji_fully_resolved"):
        blockers.append("gaiji_not_fully_resolved")
    if _status_is_blocked(row, "media_refs_resolved"):
        blockers.append("media_not_fully_resolved")
    if row["unknown_controls"] or row["unknown_bytes"] or row["structural_text_issues"]:
        blockers.append("unknown_or_structural_text_issues")
    if row["component_parse_errors"]:
        blockers.append("component_parse_errors")
    if row["requires_sidecar_body"]:
        blockers.append("raw_body_not_self_contained")
    return unique_blockers(blockers)


def lossless_repack_existing_blockers(row: dict[str, Any]) -> list[str]:
    """Blockers for reproducing/repacking the observed package structure."""

    blockers = export_existing_blockers(row)
    if _status_is_blocked(row, "titles_fully_parsed"):
        blockers.append("titles_not_fully_parsed")
    if _status_is_blocked(row, "menu_pointers_resolved"):
        blockers.append("menu_not_fully_resolved")
    if row["package_status"] == "incomplete":
        blockers.insert(0, "missing_declared_components")
    return unique_blockers(blockers)


def author_core_ssed_v0_blockers(row: dict[str, Any]) -> list[str]:
    """Blockers for authoring a new plain/core SSED v0 dictionary.

    This is deliberately narrower than repacking. Dense HONMON, renderer DBs,
    menus, title stream variation, and existing media do not block a clean
    authoring subset. Unknown text/control structure and component parse errors
    still do, because those indicate the core read model is not stable enough.
    """

    blockers: list[str] = []
    if _status_is_blocked(row, "indexes_fully_parsed"):
        blockers.append("indexes_not_fully_parsed")
    if row["unknown_controls"] or row["unknown_bytes"] or row["structural_text_issues"]:
        blockers.append("unknown_or_structural_text_issues")
    if row["component_parse_errors"]:
        blockers.append("component_parse_errors")
    return unique_blockers(blockers)


def capability_status(
    blockers: list[str],
    *,
    red_blockers: set[str],
    include_default_red: bool = True,
) -> str:
    red = red_blockers | (
        {
            "body_requires_sidecar_or_is_missing",
            "body_source_unknown",
            "indexes_not_fully_parsed",
            "unknown_or_structural_text_issues",
            "component_parse_errors",
        }
        if include_default_red
        else set()
    )
    if any(blocker in red for blocker in blockers):
        return WriterStatus.RED.value
    if blockers:
        return WriterStatus.YELLOW.value
    return WriterStatus.GREEN.value


def readiness_profile_values(row: dict[str, Any], *, ssed_family: bool) -> dict[str, Any]:
    if not ssed_family:
        blockers = ["non_ssed_package_family"]
        result: dict[str, Any] = {}
        for profile in READINESS_PROFILES:
            result[profile] = WriterStatus.GRAY.value
            result[f"{profile}_blockers"] = blockers
        result["legacy_ssed_subset"] = result["export_existing"]
        result["legacy_ssed_subset_blockers"] = result["export_existing_blockers"]
        result["lossless_repacker"] = result["lossless_repack_existing"]
        result["lossless_repacker_blockers"] = result["lossless_repack_existing_blockers"]
        result["combined"] = WriterStatus.GRAY.value
        result["combined_blockers"] = blockers
        return result

    author_blockers = author_core_ssed_v0_blockers(row)
    lossless_blockers = lossless_repack_existing_blockers(row)
    export_blockers = export_existing_blockers(row)
    read_blockers = read_existing_blockers(row)
    author_status = capability_status(
        author_blockers,
        red_blockers={"unknown_or_structural_text_issues", "component_parse_errors"},
        include_default_red=False,
    )
    lossless_status = capability_status(
        lossless_blockers,
        red_blockers={
            "missing_declared_components",
            "media_not_fully_resolved",
            "menu_not_fully_resolved",
            "titles_not_fully_parsed",
            "gaiji_not_fully_resolved",
        },
    )
    export_status = capability_status(
        export_blockers,
        red_blockers={
            "gaiji_not_fully_resolved",
        },
    )
    read_status = capability_status(read_blockers, red_blockers=set())
    combined_blockers = unique_blockers(export_blockers + lossless_blockers)
    return {
        "author_core_ssed_v0": author_status,
        "author_core_ssed_v0_blockers": author_blockers,
        "lossless_repack_existing": lossless_status,
        "lossless_repack_existing_blockers": lossless_blockers,
        "export_existing": export_status,
        "export_existing_blockers": export_blockers,
        "read_existing": read_status,
        "read_existing_blockers": read_blockers,
        # Compatibility aliases for older reports/commands.
        "legacy_ssed_subset": export_status,
        "legacy_ssed_subset_blockers": export_blockers,
        "lossless_repacker": lossless_status,
        "lossless_repacker_blockers": lossless_blockers,
        "combined": worst_writer_status(export_status, lossless_status),
        "combined_blockers": combined_blockers,
    }


def _model_target_path(model: dict[str, Any]) -> str:
    package = model.get("package", {})
    idx = package.get("idx")
    if idx:
        return str(idx)
    package_path = package.get("path")
    if package_path:
        base = Path(str(package_path))
        family = package_family(model)
        if family == PackageFamily.LVED_SQLCIPHER.value:
            return str(base / "main.data")
        return str(base)
    honmon = package.get("honmon")
    if honmon:
        return str(honmon)
    return ""


def capability_row_from_model(model: dict[str, Any]) -> dict[str, Any]:
    classification = model.get("classification", {})
    capabilities = build_model_readiness(model)["capabilities"]
    metrics = unknown_text_metrics(model)
    package_status = str(classification.get("status") or "unknown")
    requires_sidecar = str(classification.get("body_source_hint") or "") == BodySource.HONMON_ANCHOR_DEREFERENCE.value
    row: dict[str, Any] = {
        "dict_id": model.get("package", {}).get("dict_id") or "",
        "title": model.get("package", {}).get("title") or "",
        "target_path": model.get("_target_path") or _model_target_path(model),
        "model_path": model.get("_model_path") or "",
        "package_status": package_status,
        "package_family": classification.get("package_family") or model.get("wrapper", {}).get("package_family") or "unknown",
        "platform": classification.get("platform") or model.get("wrapper", {}).get("platform") or "unknown",
        "honmon_shape": classification.get("honmon_shape") or model.get("honmon", {}).get("shape") or "",
        "body_source_hint": classification.get("body_source_hint") or "",
        "raw_honmon_body": capabilities["raw_honmon_body"]["status"],
        "raw_honmon_body_reason": capabilities["raw_honmon_body"]["reason"],
        "indexes_fully_parsed": capabilities["indexes_fully_parsed"]["status"],
        "indexes_reason": capabilities["indexes_fully_parsed"]["reason"],
        "titles_fully_parsed": capabilities["titles_fully_parsed"]["status"],
        "titles_reason": capabilities["titles_fully_parsed"]["reason"],
        "gaiji_fully_resolved": capabilities["gaiji_fully_resolved"]["status"],
        "gaiji_reason": capabilities["gaiji_fully_resolved"]["reason"],
        "gaiji_occurrences": as_int((capabilities["gaiji_fully_resolved"].get("metrics") or {}).get("raw_occurrences")),
        "gaiji_unresolved": as_int((capabilities["gaiji_fully_resolved"].get("metrics") or {}).get("display_unresolved_occurrences")),
        "media_refs_resolved": capabilities["media_refs_resolved"]["status"],
        "media_reason": capabilities["media_refs_resolved"]["reason"],
        "menu_pointers_resolved": capabilities["menu_pointers_resolved"]["status"],
        "menu_reason": capabilities["menu_pointers_resolved"]["reason"],
        "unknown_controls": metrics["unknown_controls"],
        "unknown_bytes": metrics["unknown_bytes"],
        "structural_text_issues": metrics["structural_text_issues"],
        "requires_sidecar_body": requires_sidecar,
        "component_parse_errors": component_parse_errors(model),
        "missing_components": ";".join(classification.get("missing_components") or []),
    }
    readiness_values = readiness_profile_values(row, ssed_family=is_ssed_family(model))
    for profile in READINESS_PROFILES:
        row[f"{profile}_status"] = readiness_values[profile]
        row[f"{profile}_blockers"] = ";".join(readiness_values[f"{profile}_blockers"])
    # Compatibility aliases for older matrix consumers.
    row["legacy_writer_v0_status"] = readiness_values["legacy_ssed_subset"]
    row["legacy_writer_v0_blockers"] = ";".join(readiness_values["legacy_ssed_subset_blockers"])
    row["lossless_repacker_status"] = readiness_values["lossless_repacker"]
    row["lossless_repacker_blockers"] = ";".join(readiness_values["lossless_repacker_blockers"])
    row["writer_repacker_status"] = readiness_values["combined"]
    row["writer_repacker_blockers"] = ";".join(readiness_values["combined_blockers"])
    return row


def build_model_readiness(model: dict[str, Any]) -> dict[str, Any]:
    family = package_family(model)
    if family not in SSED_PACKAGE_FAMILIES:
        capabilities = {
            field: status(ReadinessStatus.NA, f"{field} is outside the SSED model for package_family={family}")
            for field in CAPABILITY_FIELDS
        }
        blockers = ["non_ssed_package_family"]
        return {
            "schema": "logovista-model-readiness-v0",
            "capabilities": capabilities,
            "metrics": {
                "unknown_controls": 0,
                "unknown_bytes": 0,
                "structural_text_issues": 0,
                "component_parse_errors": 0,
            },
            "requirements": {
                "requires_sidecar_body": False,
                "deferred_package_family": True,
            },
            "writer_readiness": readiness_profile_values({}, ssed_family=False),
        }

    capabilities = {
        "raw_honmon_body": raw_honmon_capability(model),
        "indexes_fully_parsed": indexes_capability(model),
        "titles_fully_parsed": titles_capability(model),
        "gaiji_fully_resolved": gaiji_capability(model),
        "media_refs_resolved": media_capability(model),
        "menu_pointers_resolved": menus_capability(model),
    }
    row = {
        "raw_honmon_body": capabilities["raw_honmon_body"]["status"],
        "indexes_fully_parsed": capabilities["indexes_fully_parsed"]["status"],
        "titles_fully_parsed": capabilities["titles_fully_parsed"]["status"],
        "gaiji_fully_resolved": capabilities["gaiji_fully_resolved"]["status"],
        "media_refs_resolved": capabilities["media_refs_resolved"]["status"],
        "menu_pointers_resolved": capabilities["menu_pointers_resolved"]["status"],
        **unknown_text_metrics(model),
        "requires_sidecar_body": str(model.get("classification", {}).get("body_source_hint") or "") == BodySource.HONMON_ANCHOR_DEREFERENCE.value,
        "component_parse_errors": component_parse_errors(model),
        "package_status": str(model.get("classification", {}).get("status") or "unknown"),
    }
    readiness_values = readiness_profile_values(row, ssed_family=True)
    return {
        "schema": "logovista-model-readiness-v0",
        "capabilities": capabilities,
        "metrics": {
            **unknown_text_metrics(model),
            "component_parse_errors": component_parse_errors(model),
        },
        "requirements": {
            "requires_sidecar_body": row["requires_sidecar_body"],
        },
        "writer_readiness": readiness_values,
    }


def summarize_capability_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    capability_counts = {
        field: dict(sorted(Counter(str(row.get(field) or "unknown") for row in rows).items()))
        for field in CAPABILITY_FIELDS
    }
    profile_counts = {
        f"{profile}_status_counts": dict(
            sorted(Counter(str(row.get(f"{profile}_status") or "unknown") for row in rows).items())
        )
        for profile in READINESS_PROFILES
    }
    writer_counts = Counter(str(row.get("legacy_writer_v0_status") or "unknown") for row in rows)
    repacker_counts = Counter(str(row.get("lossless_repacker_status") or "unknown") for row in rows)
    combined_counts = Counter(str(row.get("writer_repacker_status") or "unknown") for row in rows)
    blocker_counts: Counter[str] = Counter()
    for row in rows:
        for blocker in str(row.get("writer_repacker_blockers") or "").split(";"):
            if blocker:
                blocker_counts[blocker] += 1
    profile_blocker_counts: dict[str, dict[str, int]] = {}
    for profile in READINESS_PROFILES:
        counter: Counter[str] = Counter()
        for row in rows:
            for blocker in str(row.get(f"{profile}_blockers") or "").split(";"):
                if blocker:
                    counter[blocker] += 1
        profile_blocker_counts[f"{profile}_blocker_counts"] = dict(sorted(counter.items()))
    return {
        "capability_counts": capability_counts,
        **profile_counts,
        **profile_blocker_counts,
        # Compatibility summary keys for older generated reports/docs.
        "legacy_writer_v0_status_counts": dict(sorted(writer_counts.items())),
        "lossless_repacker_status_counts": dict(sorted(repacker_counts.items())),
        "writer_repacker_status_counts": dict(sorted(combined_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
    }
