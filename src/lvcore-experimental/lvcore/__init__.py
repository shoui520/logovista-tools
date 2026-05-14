"""Experimental LogoVista reader core.

This package is intentionally independent from ``logovista_tools``.  It is a
clean reader-oriented reimplementation of the currently understood package
model, starting with SSED.
"""

from .detect import detect_family
from .body_source import BodySourceInfo, BodySourceSupport, Confidence, SidecarAddressMatch, SidecarRole, SsedBodySourceKind
from .diagnostics import Diagnostic, DiagnosticArea, DiagnosticCode, Location, Severity
from .dictionary import Dictionary
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
from .gaiji_resolution import GaijiResolution
from .index import IndexRow
from .inspect import InspectorRenderer
from .model import Address, Component, ComponentRole, Entry, PackageFamily, PackageInfo, SearchProfile, Span, SpanDebug
from .opcodes import OpcodeBehavior, OpcodeCategory, behavior_for
from .package import LogoVistaPackage, open_package
from .render import GaijiPolicy, HtmlProfile, render_html, render_text
from .resources import ColscrLocator, GaijiLocator, PcmRangeLocator, ResourceLocation, SidecarBlobLocator, UnresolvedAddress
from .search import SearchHit, SearchHitDebug, SearchResults, TitleResolution, normalize_query
from .ssed import TEXT_LIKE_INDEX_OUTLIER_TYPES

__all__ = [
    "Address",
    "BlockKind",
    "BlockNode",
    "Component",
    "ComponentRole",
    "ColscrLocator",
    "BodySourceInfo",
    "BodySourceSupport",
    "Confidence",
    "Diagnostic",
    "DiagnosticArea",
    "DiagnosticCode",
    "Dictionary",
    "Entry",
    "EntryDocument",
    "GaijiDisplayStatus",
    "GaijiResolution",
    "GaijiLocator",
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
    "PcmRangeLocator",
    "OpcodeBehavior",
    "OpcodeCategory",
    "ResourceRef",
    "ResourceKind",
    "ResourceLocation",
    "ResourceStatus",
    "SearchProfile",
    "SearchHit",
    "SearchHitDebug",
    "SearchResults",
    "SidecarAddressMatch",
    "SidecarBlobLocator",
    "SidecarRole",
    "SsedBodySourceKind",
    "Severity",
    "Span",
    "SpanDebug",
    "TEXT_LIKE_INDEX_OUTLIER_TYPES",
    "TitleResolution",
    "UnresolvedAddress",
    "behavior_for",
    "detect_family",
    "normalize_query",
    "open_package",
    "render_html",
    "render_text",
]
