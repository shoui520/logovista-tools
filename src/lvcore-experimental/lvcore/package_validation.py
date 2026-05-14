"""Package validation and corpus scorecard helpers."""

from __future__ import annotations

from .body_source import (
    SidecarRole,
    SsedBodySourceKind,
)
from .diagnostics import Diagnostic, DiagnosticArea, Severity
from .document import BlockNode, InlineKind, InlineNode, ResourceKind, ResourceRef
from .gaiji import GaijiDisplayStatus
from .index import IndexRow
from .model import Address, ComponentRole, Entry, SearchProfile
from .render import render_html, render_text
from .search import normalize_query
from .ssed import TEXT_LIKE_INDEX_OUTLIER_TYPES


class PackageValidationMixin:
    """Validation and summary methods for LogoVistaPackage."""

    @staticmethod
    def _count_diagnostics(target: tuple[Diagnostic, ...], by_severity: dict[str, int], by_area: dict[str, int], by_code: dict[str, int]) -> None:
        for diagnostic in target:
            by_severity[diagnostic.severity.value] = by_severity.get(diagnostic.severity.value, 0) + 1
            by_area[diagnostic.area.value] = by_area.get(diagnostic.area.value, 0) + 1
            by_code[diagnostic.code] = by_code.get(diagnostic.code, 0) + 1

    @staticmethod
    def _increment_reason(counts: dict[str, int], reason: object) -> None:
        key = str(reason or "unknown")
        counts[key] = counts.get(key, 0) + 1

    def _count_document_resources(self, resources: tuple[ResourceRef, ...], counters: dict[str, object]) -> None:
        gaiji_by_reason = counters.setdefault("unresolved_gaiji_by_reason", {})
        gaiji_by_status = counters.setdefault("gaiji_by_status", {})
        gaiji_by_source = counters.setdefault("gaiji_by_source", {})
        gaiji_by_reason = counters.setdefault("gaiji_by_reason", gaiji_by_reason)
        media_by_reason = counters.setdefault("unresolved_media_by_reason", {})
        media_kind_counts = counters.setdefault("media_kind_counts", {})
        media_mime_counts = counters.setdefault("media_mime_counts", {})
        media_store_kind_counts = counters.setdefault("media_store_kind_counts", {})
        colscr_malformed = counters.setdefault("colscr_records_malformed_by_reason", {})
        pcmdata_unresolved = counters.setdefault("pcmdata_ranges_unresolved_by_reason", {})
        media_bytes_unavailable = counters.setdefault("media_bytes_unavailable_by_reason", {})
        for key, value in (
            ("unresolved_gaiji_by_reason", gaiji_by_reason),
            ("gaiji_by_status", gaiji_by_status),
            ("gaiji_by_source", gaiji_by_source),
            ("gaiji_by_reason", counters.get("gaiji_by_reason")),
        ):
            if not isinstance(value, dict):
                value = {}
                counters[key] = value
        gaiji_by_reason = counters["unresolved_gaiji_by_reason"]
        if not isinstance(media_by_reason, dict):
            media_by_reason = {}
            counters["unresolved_media_by_reason"] = media_by_reason
        for key, value in (
            ("media_kind_counts", media_kind_counts),
            ("media_mime_counts", media_mime_counts),
            ("media_store_kind_counts", media_store_kind_counts),
            ("colscr_records_malformed_by_reason", colscr_malformed),
            ("pcmdata_ranges_unresolved_by_reason", pcmdata_unresolved),
            ("media_bytes_unavailable_by_reason", media_bytes_unavailable),
        ):
            if not isinstance(value, dict):
                value = {}
                counters[key] = value
        for resource in resources:
            status = resource.status.value if hasattr(resource.status, "value") else str(resource.status)
            reason = resource.details.get("reason")
            if resource.kind == ResourceKind.GAIJI:
                info = self.resource_info(resource)
                info_details = info.get("details") if isinstance(info.get("details"), dict) else {}
                display_status = str(
                    resource.details.get("display_status")
                    or info.get("display_status")
                    or info_details.get("display_status")
                    or ("unresolved" if status != "resolved" else "unicode_mapped")
                )
                display_reason = resource.details.get("reason") or info.get("reason") or info_details.get("reason") or reason
                source = resource.details.get("source") or info.get("source") or info_details.get("source") or "unknown"
                counters["gaiji_occurrences"] = int(counters.get("gaiji_occurrences", 0)) + 1
                status_key = f"gaiji_{display_status}"
                counters[status_key] = int(counters.get(status_key, 0)) + 1
                if isinstance(counters.get("gaiji_by_status"), dict):
                    self._increment_reason(counters["gaiji_by_status"], display_status)
                if isinstance(counters.get("gaiji_by_source"), dict):
                    self._increment_reason(counters["gaiji_by_source"], source)
                if isinstance(counters.get("gaiji_by_reason"), dict):
                    self._increment_reason(counters["gaiji_by_reason"], display_reason)
                byte_length = info.get("byte_length")
                if isinstance(byte_length, int) and byte_length > 0:
                    counters["gaiji_resource_bytes_available"] = int(counters.get("gaiji_resource_bytes_available", 0)) + 1
                elif display_status in {GaijiDisplayStatus.BITMAP_BACKED.value, GaijiDisplayStatus.IMAGE_BACKED.value}:
                    unavailable = counters.setdefault("gaiji_resource_bytes_unavailable_by_reason", {})
                    if isinstance(unavailable, dict):
                        self._increment_reason(unavailable, display_reason)
                if display_status != GaijiDisplayStatus.UNRESOLVED.value:
                    counters["resolved_gaiji"] = int(counters.get("resolved_gaiji", 0)) + 1
                else:
                    counters["unresolved_gaiji"] = int(counters.get("unresolved_gaiji", 0)) + 1
                    counters["gaiji_display_unresolved"] = int(counters.get("gaiji_display_unresolved", 0)) + 1
                    self._increment_reason(gaiji_by_reason, display_reason)
            elif resource.kind in {ResourceKind.MEDIA, ResourceKind.IMAGE, ResourceKind.AUDIO}:
                info = self.resource_info(resource)
                kind_value = resource.kind.value
                if isinstance(counters["media_kind_counts"], dict):
                    self._increment_reason(counters["media_kind_counts"], kind_value)
                if info.get("status") == "resolved":
                    counters["resolved_media"] = int(counters.get("resolved_media", 0)) + 1
                    counters["media_bytes_available"] = int(counters.get("media_bytes_available", 0)) + 1
                    if isinstance(counters["media_mime_counts"], dict):
                        self._increment_reason(counters["media_mime_counts"], info.get("mime_type"))
                    if isinstance(counters["media_store_kind_counts"], dict):
                        self._increment_reason(counters["media_store_kind_counts"], info.get("store_kind"))
                    if info.get("store_kind") == "colscr":
                        counters["colscr_records_resolved"] = int(counters.get("colscr_records_resolved", 0)) + 1
                    elif info.get("store_kind") == "pcmdata":
                        counters["pcmdata_ranges_resolved"] = int(counters.get("pcmdata_ranges_resolved", 0)) + 1
                else:
                    counters["unresolved_media"] = int(counters.get("unresolved_media", 0)) + 1
                    info_details = info.get("details") if isinstance(info.get("details"), dict) else {}
                    unresolved_reason = info_details.get("reason") if isinstance(info_details, dict) else reason
                    self._increment_reason(media_by_reason, unresolved_reason)
                    if isinstance(counters["media_bytes_unavailable_by_reason"], dict):
                        self._increment_reason(counters["media_bytes_unavailable_by_reason"], unresolved_reason)
                    store_reason = str(unresolved_reason or "unknown")
                    if store_reason.startswith(("missing_data", "malformed_data", "truncated_data", "media_target", "target_media", "truncated_media")):
                        if isinstance(counters["colscr_records_malformed_by_reason"], dict):
                            self._increment_reason(counters["colscr_records_malformed_by_reason"], unresolved_reason)
                    if store_reason.startswith(("target_pcm", "range_", "zero_length", "malformed_audio")):
                        if isinstance(counters["pcmdata_ranges_unresolved_by_reason"], dict):
                            self._increment_reason(counters["pcmdata_ranges_unresolved_by_reason"], unresolved_reason)

    @staticmethod
    def _iter_inline_nodes(blocks: tuple[BlockNode, ...]):
        stack: list[InlineNode] = [node for block in reversed(blocks) for node in reversed(block.inlines)]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def _count_document_links(self, blocks: tuple[BlockNode, ...], counters: dict[str, object]) -> None:
        link_by_reason = counters.setdefault("unresolved_link_by_reason", {})
        if not isinstance(link_by_reason, dict):
            link_by_reason = {}
            counters["unresolved_link_by_reason"] = link_by_reason
        for node in self._iter_inline_nodes(blocks):
            if node.kind != InlineKind.LINK:
                continue
            target = node.attrs.get("link_target") if isinstance(node.attrs, dict) else None
            target = target if isinstance(target, dict) else {}
            status = str(target.get("status") or "unresolved")
            if status in {"resolved", "content", "deferred"}:
                counters["resolved_link"] = int(counters.get("resolved_link", 0)) + 1
            else:
                counters["unresolved_link"] = int(counters.get("unresolved_link", 0)) + 1
                self._increment_reason(link_by_reason, target.get("reason") or status)

    def validate(self, *, sample_entries: int = 3, sample_search_hits: int = 5) -> dict[str, object]:
        body_source = self.body_source()
        sidecar_roles = self.sidecar_role_summary()
        sidecar_supplement_counters = self.sidecar_supplement_summary()
        diagnostics_by_severity = {"info": 0, "warning": 0, "error": 0}
        diagnostics_by_area: dict[str, int] = {}
        diagnostics_by_code: dict[str, int] = {}
        title_failure_by_reason: dict[str, int] = {}
        title_status_counts: dict[str, int] = {}
        heading_source_counts: dict[str, int] = {}
        title_attempts = 0
        resource_counters: dict[str, object] = {
            "resolved_gaiji": 0,
            "unresolved_gaiji": 0,
            "unresolved_gaiji_by_reason": {},
            "gaiji_occurrences": 0,
            "gaiji_unicode_mapped": 0,
            "gaiji_bitmap_backed": 0,
            "gaiji_image_backed": 0,
            "gaiji_formatting_helper": 0,
            "gaiji_renderer_entry_backed": 0,
            "gaiji_display_unresolved": 0,
            "gaiji_search_fallback_missing": 0,
            "gaiji_resource_bytes_available": 0,
            "gaiji_resource_bytes_unavailable_by_reason": {},
            "gaiji_by_reason": {},
            "gaiji_by_source": {},
            "gaiji_by_status": {},
            "resolved_media": 0,
            "unresolved_media": 0,
            "unresolved_media_by_reason": {},
            "media_kind_counts": {},
            "media_mime_counts": {},
            "media_store_kind_counts": {},
            "colscr_records_resolved": 0,
            "colscr_records_malformed_by_reason": {},
            "pcmdata_ranges_resolved": 0,
            "pcmdata_ranges_unresolved_by_reason": {},
            "sidecar_media_resolved": 0,
            "sidecar_media_unresolved_by_reason": {},
            "media_bytes_available": 0,
            "media_bytes_unavailable_by_reason": {},
            "resolved_link": 0,
            "unresolved_link": 0,
            "unresolved_link_by_reason": {},
        }
        resource_counters["sidecar_media_resolved"] = int(sidecar_supplement_counters.get("sidecar_media_rows_resolved", 0) or 0)
        resource_counters["media_bytes_available"] = int(resource_counters.get("media_bytes_available", 0)) + int(
            sidecar_supplement_counters.get("sidecar_media_bytes_available", 0) or 0
        )
        if isinstance(resource_counters.get("media_mime_counts"), dict) and isinstance(sidecar_supplement_counters.get("sidecar_media_mime_counts"), dict):
            for mime, count in sidecar_supplement_counters["sidecar_media_mime_counts"].items():
                resource_counters["media_mime_counts"][mime] = resource_counters["media_mime_counts"].get(mime, 0) + int(count)
        if isinstance(resource_counters.get("media_store_kind_counts"), dict) and resource_counters["sidecar_media_resolved"]:
            resource_counters["media_store_kind_counts"]["sidecar_media"] = int(resource_counters["sidecar_media_resolved"])
        sidecar_reference_counters: dict[str, object] = {
            "addresses_checked": 0,
            "matched": 0,
            "by_role": {},
            "by_status": {},
            "by_table": {},
        }
        sidecar_reference_seen: set[tuple[int, int, str | None]] = set()
        decode_counters = {"unknown_controls": 0, "unknown_bytes": 0}
        decode_counter_seen: set[tuple[int, int, str | None]] = set()
        entries_checked = 0
        render_ok = 0
        entry_errors: list[str] = []

        def count_sidecar_references(address: Address) -> None:
            key = (address.block, address.offset, address.component)
            if key in sidecar_reference_seen:
                return
            sidecar_reference_seen.add(key)
            sidecar_reference_counters["addresses_checked"] = int(sidecar_reference_counters.get("addresses_checked", 0)) + 1
            matches = self.sidecar_references(address)
            if not matches:
                return
            sidecar_reference_counters["matched"] = int(sidecar_reference_counters.get("matched", 0)) + len(matches)
            by_role = sidecar_reference_counters.setdefault("by_role", {})
            by_status = sidecar_reference_counters.setdefault("by_status", {})
            by_table = sidecar_reference_counters.setdefault("by_table", {})
            if isinstance(by_role, dict) and isinstance(by_status, dict) and isinstance(by_table, dict):
                for match in matches:
                    self._increment_reason(by_role, match.get("role"))
                    self._increment_reason(by_status, match.get("support_status"))
                    self._increment_reason(by_table, match.get("table"))

        def count_entry_supplements(entry: Entry) -> None:
            if not entry.supplements:
                return
            sidecar_supplement_counters["entry_supplements_attached"] = int(sidecar_supplement_counters.get("entry_supplements_attached", 0)) + len(entry.supplements)
            for supplement in entry.supplements:
                role = str(supplement.get("role") or "")
                kind = str(supplement.get("kind") or "")
                if role == SidecarRole.EXAMPLES_IDIOMS.value:
                    sidecar_supplement_counters["examples_idioms_rows_attached"] = int(sidecar_supplement_counters.get("examples_idioms_rows_attached", 0)) + 1
                elif role == SidecarRole.LINK_REFERENCE.value:
                    sidecar_supplement_counters["link_reference_rows_matched"] = int(sidecar_supplement_counters.get("link_reference_rows_matched", 0)) + 1
                    if supplement.get("link_target"):
                        sidecar_supplement_counters["link_reference_targets_resolved"] = int(sidecar_supplement_counters.get("link_reference_targets_resolved", 0)) + 1
                elif role == SidecarRole.SEARCH.value or kind == "sidecar_search":
                    sidecar_supplement_counters["sidecar_search_rows_supported"] = int(sidecar_supplement_counters.get("sidecar_search_rows_supported", 0)) + 1

        for unsupported in sidecar_roles.get("unsupported_sidecars", []) if isinstance(sidecar_roles.get("unsupported_sidecars"), list) else []:
            if not isinstance(unsupported, dict):
                continue
            role = str(unsupported.get("role") or "unknown")
            significant = bool(unsupported.get("compatibility_significant"))
            severity = Severity.WARNING if significant else Severity.INFO
            diagnostics_by_severity[severity.value] = diagnostics_by_severity.get(severity.value, 0) + 1
            diagnostics_by_area[DiagnosticArea.VALIDATION.value] = diagnostics_by_area.get(DiagnosticArea.VALIDATION.value, 0) + 1
            diagnostics_by_code["unsupported_sidecar_schema"] = diagnostics_by_code.get("unsupported_sidecar_schema", 0) + 1
            if significant:
                diagnostics_by_code[f"unsupported_{role}_sidecar"] = diagnostics_by_code.get(f"unsupported_{role}_sidecar", 0) + 1

        def count_decode_telemetry(entry: Entry) -> None:
            key = (entry.address.block, entry.address.offset, entry.address.component)
            if key in decode_counter_seen:
                return
            decode_counter_seen.add(key)
            decode_counters["unknown_controls"] += entry.decode_unknown_controls
            decode_counters["unknown_bytes"] += entry.decode_unknown_bytes

        if body_source.ssed_kind == SsedBodySourceKind.BODY_STREAM:
            for entry in self.iter_entries(limit=sample_entries, include_supplements=True):
                entries_checked += 1
                try:
                    document = entry.document()
                    count_entry_supplements(entry)
                    count_sidecar_references(entry.address)
                    count_decode_telemetry(entry)
                    render_html(document)
                    render_text(document)
                    render_ok += 1
                    self._count_diagnostics(document.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                    self._count_document_resources(document.resources, resource_counters)
                    self._count_document_links(document.blocks, resource_counters)
                except Exception as exc:  # pragma: no cover - defensive validation report path
                    diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                    entry_errors.append(f"{entry.address.block}:{entry.address.offset}: {exc}")

        index_rows_sampled = 0
        search_hits_dereferenced = 0
        search_hits_rendered_html = 0
        search_hits_rendered_text = 0
        search_errors: list[str] = []
        sampled_rows: list[tuple[str, IndexRow]] = []
        for component_name, parsed in self.indexes().items():
            for row in parsed.rows:
                sampled_rows.append((component_name, row))
                if len(sampled_rows) >= sample_search_hits:
                    break
            if len(sampled_rows) >= sample_search_hits:
                break
        for component_name, row in sampled_rows:
            index_rows_sampled += 1
            query = self._row_display_key(row, backward=self._is_backward_index(component_name))
            if not normalize_query(query):
                diagnostics_by_severity["info"] = diagnostics_by_severity.get("info", 0) + 1
                diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                diagnostics_by_code["sample_search_skipped_empty_query"] = diagnostics_by_code.get("sample_search_skipped_empty_query", 0) + 1
                continue
            try:
                results = self.search(query, profile=SearchProfile.EXACT, limit=1)
                self._count_diagnostics(results.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                if not results.hits:
                    search_errors.append(f"no hit for sampled index key on page {row.page} row {row.row}")
                    diagnostics_by_severity["warning"] = diagnostics_by_severity.get("warning", 0) + 1
                    diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                    diagnostics_by_code["sample_search_miss"] = diagnostics_by_code.get("sample_search_miss", 0) + 1
                    continue
                hit = results.hits[0]
                title_attempts += 1
                title_status_counts[hit.title_status] = title_status_counts.get(hit.title_status, 0) + 1
                heading_source_counts[hit.heading_source] = heading_source_counts.get(hit.heading_source, 0) + 1
                if hit.title_reason and hit.title_status == "fallback":
                    self._increment_reason(title_failure_by_reason, hit.title_reason)
                self._count_diagnostics(hit.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                for diagnostic in hit.diagnostics:
                    if diagnostic.code.startswith("title_dereference"):
                        self._increment_reason(title_failure_by_reason, diagnostic.details.get("reason"))
                entry = self.entry_for_hit(hit, include_supplements=True)
                search_hits_dereferenced += 1
                count_sidecar_references(entry.address)
                count_entry_supplements(entry)
                document = entry.document()
                count_decode_telemetry(entry)
                self._count_diagnostics(document.diagnostics, diagnostics_by_severity, diagnostics_by_area, diagnostics_by_code)
                self._count_document_resources(document.resources, resource_counters)
                self._count_document_links(document.blocks, resource_counters)
                render_html(document)
                search_hits_rendered_html += 1
                render_text(document)
                search_hits_rendered_text += 1
            except Exception as exc:  # pragma: no cover - defensive validation report path
                diagnostics_by_severity["error"] = diagnostics_by_severity.get("error", 0) + 1
                search_errors.append(f"{row.page}:{row.row}: {exc}")

        index_stats = {
            name: {
                "rows": len(parsed.rows),
                "internal_rows": len(parsed.internal_rows),
                "leaf_pages": parsed.leaf_pages,
                "internal_pages": parsed.internal_pages,
                "unknown_leaf_bytes": parsed.unknown_leaf_bytes,
                "component_type": f"{self.component(name).type:02x}" if self.component(name) is not None else None,
                "unsupported_component_type": f"{parsed.unsupported_component_type:02x}" if parsed.unsupported_component_type is not None else None,
                "unsupported_leaf_pages": parsed.unsupported_leaf_pages,
                "malformed_leaf_rows": parsed.malformed_leaf_rows,
                "physical_tail_bytes": parsed.physical_tail_bytes,
                "physical_tail_nonzero_bytes": parsed.physical_tail_nonzero_bytes,
                "row_type_counts": dict(parsed.row_type_counts),
                "continuation_groups": parsed.continuation_groups,
                "dangling_continuation_rows": parsed.dangling_continuation_rows,
                "diagnostics": [diagnostic.to_dict() for diagnostic in parsed.diagnostics],
            }
            for name, parsed in self.indexes().items()
        }
        index_component_type_counts: dict[str, int] = {}
        index_rows_by_component_type: dict[str, int] = {}
        for name, parsed in self.indexes().items():
            component = self.component(name)
            component_type = f"{component.type:02x}" if component is not None else "unknown"
            index_component_type_counts[component_type] = index_component_type_counts.get(component_type, 0) + 1
            index_rows_by_component_type[component_type] = index_rows_by_component_type.get(component_type, 0) + len(parsed.rows)
            for diagnostic in parsed.diagnostics:
                diagnostics_by_severity["warning"] = diagnostics_by_severity.get("warning", 0) + 1
                diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                diagnostics_by_code[diagnostic.code] = diagnostics_by_code.get(diagnostic.code, 0) + 1
        sidecar_resolution = {
            "resolved": diagnostics_by_code.get("sidecar_body_resolved", 0),
            "missing_anchor_id": diagnostics_by_code.get("dense_anchor_missing_id", 0),
            "missing_row": diagnostics_by_code.get("sidecar_body_not_found", 0),
            "unsupported_body_source": diagnostics_by_code.get("unsupported_body_source", 0),
            "missing_body_component": diagnostics_by_code.get("missing_body_component", 0),
        }
        resource_resolution = {
            "unresolved_gaiji": diagnostics_by_code.get("unresolved_gaiji", 0),
            "unresolved_media": resource_counters["unresolved_media"],
            "unresolved_link": diagnostics_by_code.get("unresolved_link_target", 0),
            "resolved_gaiji": resource_counters["resolved_gaiji"],
            "gaiji_occurrences": resource_counters.get("gaiji_occurrences", 0),
            "gaiji_unicode_mapped": resource_counters.get("gaiji_unicode_mapped", 0),
            "gaiji_bitmap_backed": resource_counters.get("gaiji_bitmap_backed", 0),
            "gaiji_image_backed": resource_counters.get("gaiji_image_backed", 0),
            "gaiji_formatting_helper": resource_counters.get("gaiji_formatting_helper", 0),
            "gaiji_renderer_entry_backed": resource_counters.get("gaiji_renderer_entry_backed", 0),
            "gaiji_display_unresolved": resource_counters.get("gaiji_display_unresolved", 0),
            "gaiji_search_fallback_missing": resource_counters.get("gaiji_search_fallback_missing", 0),
            "gaiji_resource_bytes_available": resource_counters.get("gaiji_resource_bytes_available", 0),
            "gaiji_resource_bytes_unavailable_by_reason": resource_counters.get("gaiji_resource_bytes_unavailable_by_reason", {}),
            "gaiji_by_reason": resource_counters.get("gaiji_by_reason", {}),
            "gaiji_by_source": resource_counters.get("gaiji_by_source", {}),
            "gaiji_by_status": resource_counters.get("gaiji_by_status", {}),
            "resolved_media": resource_counters["resolved_media"],
            "resolved_link": resource_counters["resolved_link"],
            "unresolved_gaiji_by_reason": resource_counters["unresolved_gaiji_by_reason"],
            "unresolved_media_by_reason": resource_counters["unresolved_media_by_reason"],
            "unresolved_link_by_reason": resource_counters["unresolved_link_by_reason"],
            "media_kind_counts": resource_counters["media_kind_counts"],
            "media_mime_counts": resource_counters["media_mime_counts"],
            "media_store_kind_counts": resource_counters["media_store_kind_counts"],
            "colscr_records_resolved": resource_counters["colscr_records_resolved"],
            "colscr_records_malformed_by_reason": resource_counters["colscr_records_malformed_by_reason"],
            "pcmdata_ranges_resolved": resource_counters["pcmdata_ranges_resolved"],
            "pcmdata_ranges_unresolved_by_reason": resource_counters["pcmdata_ranges_unresolved_by_reason"],
            "sidecar_media_resolved": resource_counters["sidecar_media_resolved"],
            "sidecar_media_unresolved_by_reason": resource_counters["sidecar_media_unresolved_by_reason"],
            "media_bytes_available": resource_counters["media_bytes_available"],
            "media_bytes_unavailable_by_reason": resource_counters["media_bytes_unavailable_by_reason"],
        }
        return {
            "package": self.info.to_dict(),
            "body_source": body_source.to_dict(debug=True),
            "sidecar_resolution": sidecar_resolution,
            "sidecar_roles": sidecar_roles,
            "resource_resolution": resource_resolution,
            "sidecar_references": sidecar_reference_counters,
            "sidecar_supplements": sidecar_supplement_counters,
            "decode_telemetry": decode_counters,
            "title_dereference": {
                "attempts": title_attempts,
                "resolved": title_status_counts.get("resolved", 0),
                "fallback": title_status_counts.get("fallback", 0),
                "failed": diagnostics_by_code.get("title_dereference_failed", 0),
                "empty": diagnostics_by_code.get("title_dereference_empty", 0),
                "by_reason": title_failure_by_reason,
                "title_status_counts": title_status_counts,
                "heading_source_counts": heading_source_counts,
            },
            "component_count": len(self.components),
            "components": [
                {
                    "name": component.name,
                    "role": component.role.value,
                    "type": f"{component.type:02x}",
                    "present": component.path is not None,
                }
                for component in self.components
            ],
            "gaiji": {
                "uni_records": len(self.gaiji.records),
                "unicode_mappings": len(self.gaiji.mapping),
                "ga16_resources": len(self.ga16),
                "image_resources": len(self.gaiji_images),
                "plist_unicode_mappings": self.gaiji.plist_unicode_mappings,
                "plist_mapping_ambiguous": self.gaiji.plist_mapping_ambiguous,
                "plist_parse_failures": self.gaiji.plist_parse_failures,
            },
            "indexes": index_stats,
            "index_summary": {
                "component_type_counts": index_component_type_counts,
                "rows_by_component_type": index_rows_by_component_type,
                "unsupported_component_types": {
                    name: f"{parsed.unsupported_component_type:02x}"
                    for name, parsed in self.indexes().items()
                    if parsed.unsupported_component_type is not None
                },
                "malformed_leaf_rows": sum(parsed.malformed_leaf_rows for parsed in self.indexes().values()),
                "physical_tail_bytes": sum(parsed.physical_tail_bytes for parsed in self.indexes().values()),
                "physical_tail_nonzero_bytes": sum(parsed.physical_tail_nonzero_bytes for parsed in self.indexes().values()),
                "text_like_index_outliers": {
                    component.name: f"{component.type:02x}"
                    for component in self.components
                    if component.name.upper() == "INDEX.DIC" and component.type in TEXT_LIKE_INDEX_OUTLIER_TYPES
                },
                "continuation_groups": sum(parsed.continuation_groups for parsed in self.indexes().values()),
                "dangling_continuation_rows": sum(parsed.dangling_continuation_rows for parsed in self.indexes().values()),
            },
            "title_components": len(self.components_by_role(ComponentRole.TITLE)),
            "sample_entries_checked": entries_checked,
            "sample_entries_rendered": render_ok,
            "sample_index_rows_checked": index_rows_sampled,
            "sample_search_hits_dereferenced": search_hits_dereferenced,
            "sample_search_hits_rendered_html": search_hits_rendered_html,
            "sample_search_hits_rendered_text": search_hits_rendered_text,
            "diagnostics": {
                "by_severity": diagnostics_by_severity,
                "by_area": diagnostics_by_area,
                "by_code": diagnostics_by_code,
                "entry_errors": entry_errors,
                "search_errors": search_errors,
            },
            "ok": diagnostics_by_severity.get("error", 0) == 0 and not entry_errors,
        }

    def summary(self, *, debug: bool = False) -> dict[str, object]:
        data: dict[str, object] = {
            "package": self.info.to_dict(),
            "components": [component.to_dict() for component in self.components],
        }
        if not debug:
            data["notes"] = ["fast summary; use --debug for body-source, gaiji, and resource evidence"]
            return data

        data["body_source"] = self.body_source(debug=True).to_dict(debug=False)
        data["gaiji"] = {
                "records": len(self.gaiji.records),
                "mapped": len(self.gaiji.mapping),
                "paths": [str(path) for path in self.gaiji.paths],
                "image_resources": len(self.gaiji_images),
                "plist_unicode_mappings": self.gaiji.plist_unicode_mappings,
                "plist_mapping_ambiguous": self.gaiji.plist_mapping_ambiguous,
                "plist_parse_failures": self.gaiji.plist_parse_failures,
                "ga16": [
                    {
                        "path": str(resource.path),
                        "width": resource.width,
                        "height": resource.height,
                        "start_code": f"{resource.start_code:04x}",
                        "count": resource.count,
                        "glyph_bytes": resource.glyph_bytes,
                        "section": resource.section,
                    }
                    for resource in self.ga16
                ],
            }
        return data
