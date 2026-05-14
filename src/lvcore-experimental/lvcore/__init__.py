"""Experimental LogoVista reader core.

This package is intentionally independent from ``logovista_tools``.  It is a
clean reader-oriented reimplementation of the currently understood package
model, starting with SSED.
"""

from .detect import detect_family
from .body_source import BodySourceInfo, BodySourceSupport, Confidence, SidecarAddressMatch, SidecarRole, SsedBodySourceKind
from .diagnostics import Diagnostic, DiagnosticArea, Location, Severity
from .document import (
    BlockKind,
    BlockNode,
    EntryDocument,
    InlineKind,
    InlineNode,
    LinkTarget,
    LinkTargetKind,
    LinkTargetStatus,
    ResourceKind,
    ResourceRef,
    ResourceStatus,
)
from .gaiji import GaijiDisplayStatus
from .index import IndexRow
from .inspect import InspectorRenderer
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile, Span
from .opcodes import OpcodeBehavior, OpcodeCategory, behavior_for
from .package import LogoVistaPackage, open_package
from .render import GaijiPolicy, HtmlProfile, render_html, render_text
from .search import SearchHit, SearchResults, normalize_query
from .ssed import TEXT_LIKE_INDEX_OUTLIER_TYPES

__all__ = [
    "Address",
    "BlockKind",
    "BlockNode",
    "Component",
    "ComponentRole",
    "BodySourceInfo",
    "BodySourceSupport",
    "Confidence",
    "Diagnostic",
    "DiagnosticArea",
    "Entry",
    "EntryDocument",
    "GaijiDisplayStatus",
    "GaijiPolicy",
    "HtmlProfile",
    "IndexRow",
    "InlineKind",
    "InlineNode",
    "InspectorRenderer",
    "LinkTarget",
    "LinkTargetKind",
    "LinkTargetStatus",
    "LogoVistaPackage",
    "Location",
    "PackageFamily",
    "PackageInfo",
    "OpcodeBehavior",
    "OpcodeCategory",
    "ResourceRef",
    "ResourceKind",
    "ResourceStatus",
    "SearchProfile",
    "SearchHit",
    "SearchResults",
    "SidecarAddressMatch",
    "SidecarRole",
    "SsedBodySourceKind",
    "Severity",
    "Span",
    "TEXT_LIKE_INDEX_OUTLIER_TYPES",
    "behavior_for",
    "detect_family",
    "normalize_query",
    "open_package",
    "render_html",
    "render_text",
]
