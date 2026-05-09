"""Authoritative readiness derivation for Decoded LogoVista Model v0."""

from __future__ import annotations

from collections import Counter
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
    PackageFamily.SSED_SIZK_READ_ALOUD.value,
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
    resolved_destinations = 0
    warnings = 0
    errors = 0
    for row in components:
        if not isinstance(row, dict):
            continue
        total_destinations += as_int(row.get("destinations"))
        resolved_destinations += as_int(row.get("resolved_destinations"))
        warnings += len(row.get("warnings") or [])
        errors += 1 if row.get("error") else 0
    if errors:
        return status(ReadinessStatus.NO, f"menu component errors={errors}", errors=errors)
    if warnings or resolved_destinations < total_destinations:
        return status(
            ReadinessStatus.PARTIAL,
            f"destinations={total_destinations}; resolved={resolved_destinations}; warnings={warnings}",
            destinations=total_destinations,
            resolved_destinations=resolved_destinations,
            warnings=warnings,
        )
    return status(ReadinessStatus.YES, f"destinations={total_destinations}; resolved={resolved_destinations}", destinations=total_destinations, resolved_destinations=resolved_destinations)


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
    colscr = media.get("colscr") or {}
    pcmdata = media.get("pcmdata") or {}
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
    errors = 0
    for component in model.get("components", []):
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


def common_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if row["raw_honmon_body"] == ReadinessStatus.NO.value:
        blockers.append("body_requires_sidecar_or_is_missing")
    elif row["raw_honmon_body"] == ReadinessStatus.UNKNOWN.value:
        blockers.append("body_source_unknown")
    if row["indexes_fully_parsed"] in {ReadinessStatus.NO.value, ReadinessStatus.PARTIAL.value, ReadinessStatus.UNKNOWN.value}:
        blockers.append("indexes_not_fully_parsed")
    if row["titles_fully_parsed"] in {ReadinessStatus.NO.value, ReadinessStatus.PARTIAL.value, ReadinessStatus.UNKNOWN.value}:
        blockers.append("titles_not_fully_parsed")
    if row["gaiji_fully_resolved"] in {ReadinessStatus.NO.value, ReadinessStatus.PARTIAL.value, ReadinessStatus.UNKNOWN.value}:
        blockers.append("gaiji_not_fully_resolved")
    if row["media_refs_resolved"] in {ReadinessStatus.NO.value, ReadinessStatus.PARTIAL.value, ReadinessStatus.UNKNOWN.value}:
        blockers.append("media_not_fully_resolved")
    if row["menu_pointers_resolved"] in {ReadinessStatus.NO.value, ReadinessStatus.PARTIAL.value, ReadinessStatus.UNKNOWN.value}:
        blockers.append("menu_not_fully_resolved")
    if row["unknown_controls"] or row["unknown_bytes"] or row["structural_text_issues"]:
        blockers.append("unknown_or_structural_text_issues")
    if row["component_parse_errors"]:
        blockers.append("component_parse_errors")
    if row["requires_sidecar_body"]:
        blockers.append("raw_body_not_self_contained")
    return blockers


def writer_v0_blockers(row: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(common_blockers(row)))


def lossless_repacker_blockers(row: dict[str, Any]) -> list[str]:
    blockers = common_blockers(row)
    if row["package_status"] == "incomplete":
        blockers.insert(0, "missing_declared_components")
    return list(dict.fromkeys(blockers))


def capability_status(blockers: list[str], *, red_blockers: set[str]) -> str:
    red = {
        "body_requires_sidecar_or_is_missing",
        "body_source_unknown",
        "indexes_not_fully_parsed",
        "unknown_or_structural_text_issues",
        "component_parse_errors",
    } | red_blockers
    if any(blocker in red for blocker in blockers):
        return WriterStatus.RED.value
    if blockers:
        return WriterStatus.YELLOW.value
    return WriterStatus.GREEN.value


def capability_row_from_model(model: dict[str, Any]) -> dict[str, Any]:
    classification = model.get("classification", {})
    capabilities = model.get("readiness", {}).get("capabilities") or build_model_readiness(model)["capabilities"]
    metrics = unknown_text_metrics(model)
    package_status = str(classification.get("status") or "unknown")
    requires_sidecar = str(classification.get("body_source_hint") or "") == BodySource.HONMON_ANCHOR_DEREFERENCE.value
    row: dict[str, Any] = {
        "dict_id": model.get("package", {}).get("dict_id") or "",
        "title": model.get("package", {}).get("title") or "",
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
    readiness_writer = model.get("readiness", {}).get("writer_readiness", {})
    if not is_ssed_family(model):
        writer_blockers = list(readiness_writer.get("legacy_ssed_subset_blockers") or ["non_ssed_package_family"])
        repacker_blockers = list(readiness_writer.get("lossless_repacker_blockers") or ["non_ssed_package_family"])
        writer_status = str(readiness_writer.get("legacy_ssed_subset") or WriterStatus.GRAY.value)
        repacker_status = str(readiness_writer.get("lossless_repacker") or WriterStatus.GRAY.value)
    else:
        writer_blockers = writer_v0_blockers(row)
        repacker_blockers = lossless_repacker_blockers(row)
        writer_status = capability_status(writer_blockers, red_blockers=set())
        repacker_status = capability_status(
            repacker_blockers,
            red_blockers={
                "missing_declared_components",
                "media_not_fully_resolved",
                "menu_not_fully_resolved",
                "titles_not_fully_parsed",
            },
        )
    row["legacy_writer_v0_status"] = writer_status
    row["legacy_writer_v0_blockers"] = ";".join(writer_blockers)
    row["lossless_repacker_status"] = repacker_status
    row["lossless_repacker_blockers"] = ";".join(repacker_blockers)
    row["writer_repacker_status"] = worst_writer_status(writer_status, repacker_status)
    row["writer_repacker_blockers"] = ";".join(list(dict.fromkeys(writer_blockers + repacker_blockers)))
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
            "writer_readiness": {
                "legacy_ssed_subset": WriterStatus.GRAY.value,
                "legacy_ssed_subset_blockers": blockers,
                "lossless_repacker": WriterStatus.GRAY.value,
                "lossless_repacker_blockers": blockers,
                "combined": WriterStatus.GRAY.value,
                "combined_blockers": blockers,
            },
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
    writer_blockers = writer_v0_blockers(row)
    repacker_blockers = lossless_repacker_blockers(row)
    writer_status = capability_status(writer_blockers, red_blockers=set())
    repacker_status = capability_status(
        repacker_blockers,
        red_blockers={
            "missing_declared_components",
            "media_not_fully_resolved",
            "menu_not_fully_resolved",
            "titles_not_fully_parsed",
        },
    )
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
        "writer_readiness": {
            "legacy_ssed_subset": writer_status,
            "legacy_ssed_subset_blockers": writer_blockers,
            "lossless_repacker": repacker_status,
            "lossless_repacker_blockers": repacker_blockers,
            "combined": worst_writer_status(writer_status, repacker_status),
            "combined_blockers": list(dict.fromkeys(writer_blockers + repacker_blockers)),
        },
    }


def summarize_capability_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    capability_counts = {
        field: dict(sorted(Counter(str(row.get(field) or "unknown") for row in rows).items()))
        for field in CAPABILITY_FIELDS
    }
    writer_counts = Counter(str(row.get("legacy_writer_v0_status") or "unknown") for row in rows)
    repacker_counts = Counter(str(row.get("lossless_repacker_status") or "unknown") for row in rows)
    combined_counts = Counter(str(row.get("writer_repacker_status") or "unknown") for row in rows)
    blocker_counts: Counter[str] = Counter()
    for row in rows:
        for blocker in str(row.get("writer_repacker_blockers") or "").split(";"):
            if blocker:
                blocker_counts[blocker] += 1
    return {
        "capability_counts": capability_counts,
        "legacy_writer_v0_status_counts": dict(sorted(writer_counts.items())),
        "lossless_repacker_status_counts": dict(sorted(repacker_counts.items())),
        "writer_repacker_status_counts": dict(sorted(combined_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
    }
