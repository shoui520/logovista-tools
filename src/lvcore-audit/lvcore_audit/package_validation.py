"""Package validation and corpus scorecard helpers."""

from __future__ import annotations

from lvcore import (
    Address,
    BlockNode,
    ComponentRole,
    Diagnostic,
    DiagnosticArea,
    DiagnosticCode,
    Entry,
    GaijiDisplayStatus,
    IndexRow,
    InlineKind,
    InlineNode,
    ResourceKind,
    ResourceRef,
    ResourceStatus,
    SearchProfile,
    SidecarRole,
    SsedBodySourceKind,
    Severity,
    normalize_query,
    render_html,
    render_text,
)

TEXT_LIKE_INDEX_OUTLIER_TYPES = {0x27}


COMPATIBILITY_SIGNIFICANT_SIDECAR_ROLES = {
    SidecarRole.BODY_CRITICAL.value,
    SidecarRole.MEDIA_RESOURCE.value,
    SidecarRole.EXAMPLES_IDIOMS.value,
    SidecarRole.LINK_REFERENCE.value,
    SidecarRole.SEARCH.value,
    SidecarRole.UNKNOWN.value,
}


class _PackageValidationAdapter:
    """Audit-side validation adapter around a public lvcore package."""

    def __init__(self, package) -> None:
        self._package = package

    def __getattr__(self, name: str):
        return getattr(self._package, name)

    @staticmethod
    def _count_diagnostics(target: tuple[Diagnostic, ...], by_severity: dict[str, int], by_area: dict[str, int], by_code: dict[str, int]) -> None:
        for diagnostic in target:
            by_severity[diagnostic.severity.value] = by_severity.get(diagnostic.severity.value, 0) + 1
            by_area[diagnostic.area.value] = by_area.get(diagnostic.area.value, 0) + 1
            by_code[diagnostic.code.value] = by_code.get(diagnostic.code.value, 0) + 1

    @staticmethod
    def _increment_reason(counts: dict[str, int], reason: object) -> None:
        key = str(reason or "unknown")
        counts[key] = counts.get(key, 0) + 1

    def _sidecar_role_summary(self) -> dict[str, object]:
        role_counts: dict[str, int] = {}
        unsupported_role_counts: dict[str, int] = {}
        supported_role_counts: dict[str, int] = {}
        compatibility_significant_unsupported_counts: dict[str, int] = {}
        support_status_counts: dict[str, int] = {}
        unsupported_sidecars: list[dict[str, object]] = []
        candidates = tuple(self.sidecar_candidate_paths()) if hasattr(self._package, "sidecar_candidate_paths") else ()
        sidecars = tuple(self.sidecars())
        sidecars_by_path = {getattr(sidecar, "path", None): sidecar for sidecar in sidecars}
        sqlite_count = 0
        non_sqlite_count = 0
        for path in candidates:
            sidecar = sidecars_by_path.get(path)
            if sidecar is None:
                non_sqlite_count += 1
                role = SidecarRole.NON_SQLITE_OR_UNKNOWN.value
                status = "non_sqlite_or_unknown"
            else:
                sqlite_count += 1
                role = sidecar.role.value if hasattr(sidecar.role, "value") else str(sidecar.role)
                status = sidecar.support_status.value if hasattr(sidecar.support_status, "value") else str(sidecar.support_status)
            role_counts[role] = role_counts.get(role, 0) + 1
            support_status_counts[status] = support_status_counts.get(status, 0) + 1
            if status in {"body_resolver", "supplement_resolver", "resource_resolver", "search_metadata"}:
                supported_role_counts[role] = supported_role_counts.get(role, 0) + 1
            else:
                unsupported_role_counts[role] = unsupported_role_counts.get(role, 0) + 1
                significant = role in COMPATIBILITY_SIGNIFICANT_SIDECAR_ROLES
                if significant:
                    compatibility_significant_unsupported_counts[role] = compatibility_significant_unsupported_counts.get(role, 0) + 1
                if sidecar is not None:
                    unsupported_sidecars.append(
                        {
                            "name": sidecar.path.name,
                            "kind": sidecar.kind,
                            "role": role,
                            "support_status": status,
                            "compatibility_significant": significant,
                            "tables": [table.table for table in sidecar.tables] or list(sidecar.notes),
                        }
                    )
        return {
            "candidate_count": len(candidates),
            "sqlite_count": sqlite_count,
            "non_sqlite_or_unknown_count": non_sqlite_count,
            "role_counts": role_counts,
            "supported_role_counts": supported_role_counts,
            "unsupported_role_counts": unsupported_role_counts,
            "compatibility_significant_unsupported_counts": compatibility_significant_unsupported_counts,
            "support_status_counts": support_status_counts,
            "unsupported_sidecars": unsupported_sidecars,
        }

    def _sidecar_supplement_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "examples_idioms_rows_seen": 0,
            "examples_idioms_rows_attached": 0,
            "entry_supplements_attached": 0,
            "link_reference_rows_seen": 0,
            "link_reference_rows_matched": 0,
            "link_reference_targets_resolved": 0,
            "sidecar_search_rows_seen": 0,
            "sidecar_search_rows_supported": 0,
            "sidecar_search_rows_deferred": 0,
            "sidecar_media_rows_seen": 0,
            "sidecar_media_rows_resolved": 0,
            "sidecar_media_bytes_available": 0,
            "sidecar_media_mime_counts": {},
        }
        for sidecar in self.sidecars():
            role = sidecar.role.value if hasattr(sidecar.role, "value") else str(sidecar.role)
            status = sidecar.support_status.value if hasattr(sidecar.support_status, "value") else str(sidecar.support_status)
            for table in sidecar.tables:
                rows = int(table.row_count or 0)
                if role == SidecarRole.EXAMPLES_IDIOMS.value:
                    summary["examples_idioms_rows_seen"] = int(summary["examples_idioms_rows_seen"]) + rows
                elif role == SidecarRole.LINK_REFERENCE.value:
                    summary["link_reference_rows_seen"] = int(summary["link_reference_rows_seen"]) + rows
                elif role == SidecarRole.SEARCH.value:
                    summary["sidecar_search_rows_seen"] = int(summary["sidecar_search_rows_seen"]) + rows
                    if status == "search_metadata":
                        summary["sidecar_search_rows_supported"] = int(summary["sidecar_search_rows_supported"]) + rows
                    else:
                        summary["sidecar_search_rows_deferred"] = int(summary["sidecar_search_rows_deferred"]) + rows
                elif role == SidecarRole.MEDIA_RESOURCE.value and table.blob_column:
                    summary["sidecar_media_rows_seen"] = int(summary["sidecar_media_rows_seen"]) + rows
        media_mime_counts = summary["sidecar_media_mime_counts"]
        for resource in self.sidecar_media_resources():
            info = self.resource_info(resource)
            if info.status == ResourceStatus.RESOLVED:
                summary["sidecar_media_rows_resolved"] = int(summary["sidecar_media_rows_resolved"]) + 1
                summary["sidecar_media_bytes_available"] = int(summary["sidecar_media_bytes_available"]) + 1
                if isinstance(media_mime_counts, dict):
                    mime = str(info.mime_type or "unknown")
                    media_mime_counts[mime] = media_mime_counts.get(mime, 0) + 1
        return summary

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
                info_debug = info.to_dict(debug=True)
                display_status = str(
                    resource.details.get("display_status")
                    or info_debug.get("display_status")
                    or ("unresolved" if status != "resolved" else "unicode_mapped")
                )
                display_reason = resource.details.get("reason") or info.reason or info_debug.get("unresolved_reason") or reason
                source = resource.details.get("source") or info_debug.get("source") or "unknown"
                counters["gaiji_occurrences"] = int(counters.get("gaiji_occurrences", 0)) + 1
                status_key = f"gaiji_{display_status}"
                counters[status_key] = int(counters.get(status_key, 0)) + 1
                if isinstance(counters.get("gaiji_by_status"), dict):
                    self._increment_reason(counters["gaiji_by_status"], display_status)
                if isinstance(counters.get("gaiji_by_source"), dict):
                    self._increment_reason(counters["gaiji_by_source"], source)
                if isinstance(counters.get("gaiji_by_reason"), dict):
                    self._increment_reason(counters["gaiji_by_reason"], display_reason)
                byte_length = info.byte_length
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
                if info.status == ResourceStatus.RESOLVED:
                    counters["resolved_media"] = int(counters.get("resolved_media", 0)) + 1
                    counters["media_bytes_available"] = int(counters.get("media_bytes_available", 0)) + 1
                    if isinstance(counters["media_mime_counts"], dict):
                        self._increment_reason(counters["media_mime_counts"], info.mime_type)
                    if isinstance(counters["media_store_kind_counts"], dict):
                        self._increment_reason(counters["media_store_kind_counts"], info.store_kind)
                    if info.store_kind == "colscr":
                        counters["colscr_records_resolved"] = int(counters.get("colscr_records_resolved", 0)) + 1
                    elif info.store_kind == "pcmdata":
                        counters["pcmdata_ranges_resolved"] = int(counters.get("pcmdata_ranges_resolved", 0)) + 1
                else:
                    counters["unresolved_media"] = int(counters.get("unresolved_media", 0)) + 1
                    unresolved_reason = info.reason or reason
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

    def validate(
        self,
        *,
        sample_entries: int = 3,
        sample_search_hits: int = 5,
        debug: bool = False,
        max_bytes_per_scan: int | None = None,
    ) -> dict[str, object]:
        body_source = self.body_source(debug=debug)
        sidecar_roles = self._sidecar_role_summary()
        sidecar_supplement_counters = self._sidecar_supplement_summary()
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
            matches = self.sidecar_address_matches(address)
            if not matches:
                return
            sidecar_reference_counters["matched"] = int(sidecar_reference_counters.get("matched", 0)) + len(matches)
            by_role = sidecar_reference_counters.setdefault("by_role", {})
            by_status = sidecar_reference_counters.setdefault("by_status", {})
            by_table = sidecar_reference_counters.setdefault("by_table", {})
            if isinstance(by_role, dict) and isinstance(by_status, dict) and isinstance(by_table, dict):
                for match in matches:
                    role = match.role.value if hasattr(match.role, "value") else str(match.role)
                    support_status = match.support_status.value if hasattr(match.support_status, "value") else str(match.support_status)
                    self._increment_reason(by_role, role)
                    self._increment_reason(by_status, support_status)
                    self._increment_reason(by_table, match.table)
                    count = int(match.match_count)
                    if role == SidecarRole.EXAMPLES_IDIOMS.value:
                        sidecar_supplement_counters["examples_idioms_rows_attached"] = int(sidecar_supplement_counters.get("examples_idioms_rows_attached", 0)) + count
                        sidecar_supplement_counters["entry_supplements_attached"] = int(sidecar_supplement_counters.get("entry_supplements_attached", 0)) + count
                    elif role == SidecarRole.LINK_REFERENCE.value:
                        sidecar_supplement_counters["link_reference_rows_matched"] = int(sidecar_supplement_counters.get("link_reference_rows_matched", 0)) + count
                        sidecar_supplement_counters["link_reference_targets_resolved"] = int(sidecar_supplement_counters.get("link_reference_targets_resolved", 0)) + count
                        sidecar_supplement_counters["entry_supplements_attached"] = int(sidecar_supplement_counters.get("entry_supplements_attached", 0)) + count

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
            for entry in self.iter_entries(limit=sample_entries, max_bytes=max_bytes_per_scan):
                entries_checked += 1
                try:
                    document = entry.document()
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
        if debug:
            for component_name, parsed in self.indexes().items():
                for row in parsed.rows:
                    sampled_rows.append((component_name, row))
                    if len(sampled_rows) >= sample_search_hits:
                        break
                if len(sampled_rows) >= sample_search_hits:
                    break
        else:
            for component in self.components_by_role(ComponentRole.INDEX):
                if component.path is None:
                    continue
                for row in self.iter_index_rows(component, max_bytes=max_bytes_per_scan):
                    sampled_rows.append((component.name, row))
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
                results = self.search(query, profile=SearchProfile.EXACT, limit=1, max_bytes=max_bytes_per_scan)
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
                    if diagnostic.code.value.startswith("title_dereference"):
                        self._increment_reason(title_failure_by_reason, diagnostic.details.get("reason"))
                entry = self.entry_for_hit(hit)
                search_hits_dereferenced += 1
                count_sidecar_references(entry.address)
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

        index_component_type_counts: dict[str, int] = {}
        index_rows_by_component_type: dict[str, int] = {}
        if debug:
            def unsupported_type_for(name: str, parsed) -> str | None:
                for diagnostic in parsed.diagnostics:
                    if diagnostic.code == DiagnosticCode.UNSUPPORTED_COMPONENT_TYPE:
                        value = diagnostic.details.get("component_type")
                        if value:
                            return str(value)
                component = self.component(name)
                return (
                    f"{component.type:02x}"
                    if component is not None and any(d.code == DiagnosticCode.UNSUPPORTED_COMPONENT_TYPE for d in parsed.diagnostics)
                    else None
                )

            index_stats = {
                name: {
                    "rows": len(parsed.rows),
                    "internal_rows": len(parsed.internal_rows),
                    "leaf_pages": parsed.leaf_pages,
                    "internal_pages": parsed.internal_pages,
                    "unknown_leaf_bytes": parsed.unknown_leaf_bytes,
                    "component_type": f"{self.component(name).type:02x}" if self.component(name) is not None else None,
                    "unsupported_component_type": unsupported_type_for(name, parsed),
                    "unsupported_leaf_pages": sum(
                        1
                        for diagnostic in parsed.diagnostics
                        if diagnostic.code == DiagnosticCode.UNSUPPORTED_COMPONENT_TYPE and diagnostic.page is not None
                    ),
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
            unsupported_component_types = {
                name: unsupported_type
                for name, parsed in self.indexes().items()
                for unsupported_type in [unsupported_type_for(name, parsed)]
                if unsupported_type is not None
            }
            malformed_leaf_rows = sum(parsed.malformed_leaf_rows for parsed in self.indexes().values())
            physical_tail_bytes = sum(parsed.physical_tail_bytes for parsed in self.indexes().values())
            physical_tail_nonzero_bytes = sum(parsed.physical_tail_nonzero_bytes for parsed in self.indexes().values())
            continuation_groups = sum(parsed.continuation_groups for parsed in self.indexes().values())
            dangling_continuation_rows = sum(parsed.dangling_continuation_rows for parsed in self.indexes().values())
            for name, parsed in self.indexes().items():
                component = self.component(name)
                component_type = f"{component.type:02x}" if component is not None else "unknown"
                index_component_type_counts[component_type] = index_component_type_counts.get(component_type, 0) + 1
                index_rows_by_component_type[component_type] = index_rows_by_component_type.get(component_type, 0) + len(parsed.rows)
                for diagnostic in parsed.diagnostics:
                    diagnostics_by_severity["warning"] = diagnostics_by_severity.get("warning", 0) + 1
                    diagnostics_by_area[DiagnosticArea.INDEX.value] = diagnostics_by_area.get(DiagnosticArea.INDEX.value, 0) + 1
                    diagnostics_by_code[diagnostic.code.value] = diagnostics_by_code.get(diagnostic.code.value, 0) + 1
        else:
            index_stats = {}
            unsupported_component_types = {}
            malformed_leaf_rows = 0
            physical_tail_bytes = 0
            physical_tail_nonzero_bytes = 0
            continuation_groups = 0
            dangling_continuation_rows = 0
            for component in self.components_by_role(ComponentRole.INDEX):
                component_type = f"{component.type:02x}"
                index_component_type_counts[component_type] = index_component_type_counts.get(component_type, 0) + 1
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
            "body_source": body_source.to_dict(debug=debug),
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
                "unsupported_component_types": unsupported_component_types,
                "malformed_leaf_rows": malformed_leaf_rows,
                "physical_tail_bytes": physical_tail_bytes,
                "physical_tail_nonzero_bytes": physical_tail_nonzero_bytes,
                "text_like_index_outliers": {
                    component.name: f"{component.type:02x}"
                    for component in self.components
                    if component.name.upper() == "INDEX.DIC" and component.type in TEXT_LIKE_INDEX_OUTLIER_TYPES
                },
                "continuation_groups": continuation_groups,
                "dangling_continuation_rows": dangling_continuation_rows,
                "debug_deep_index_parse": debug,
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

def validate_package(
    package,
    *,
    sample_entries: int = 3,
    sample_search_hits: int = 5,
    debug: bool = False,
    max_bytes_per_scan: int | None = None,
) -> dict[str, object]:
    """Validate a package using audit-side scorecard logic."""

    return _PackageValidationAdapter(package).validate(
        sample_entries=sample_entries,
        sample_search_hits=sample_search_hits,
        debug=debug,
        max_bytes_per_scan=max_bytes_per_scan,
    )


def sidecar_role_summary(package) -> dict[str, object]:
    """Return audit-side sidecar role/support counters without body validation."""

    return _PackageValidationAdapter(package)._sidecar_role_summary()
