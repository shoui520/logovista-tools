"""Experimental LogoVista reader core.

This package is intentionally independent from ``logovista_tools``.  It is a
clean reader-oriented reimplementation of the currently understood package
model, starting with SSED.
"""

from .detect import detect_family
from .body_source import BodySourceInfo, BodySourceSupport, Confidence, SidecarRole, SsedBodySourceKind
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
from .model import Address, Entry, PackageFamily, PackageInfo, SearchProfile
from .package import LogoVistaPackage, open_package
from .render import GaijiPolicy, HtmlProfile, render_html, render_text
from .resources import ResourceLocation
from .search import SearchHit, SearchResults, TitleResolution, normalize_query

__all__ = [
    "Address",
    "BlockKind",
    "BlockNode",
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
    "GaijiPolicy",
    "HtmlProfile",
    "InlineKind",
    "InlineNode",
    "LinkTarget",
    "LinkTargetKind",
    "LinkTargetStatus",
    "LogoVistaPackage",
    "Location",
    "PackageFamily",
    "PackageInfo",
    "ResourceRef",
    "ResourceKind",
    "ResourceLocation",
    "ResourceStatus",
    "SearchProfile",
    "SearchHit",
    "SearchResults",
    "SidecarRole",
    "SsedBodySourceKind",
    "Severity",
    "TitleResolution",
    "detect_family",
    "normalize_query",
    "open_package",
    "render_html",
    "render_text",
]
