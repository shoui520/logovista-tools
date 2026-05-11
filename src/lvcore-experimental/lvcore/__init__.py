"""Experimental LogoVista reader core.

This package is intentionally independent from ``logovista_tools``.  It is a
clean reader-oriented reimplementation of the currently understood package
model, starting with SSED.
"""

from .detect import detect_family
from .body_source import BodySourceInfo, BodySourceSupport, Confidence, SsedBodySourceKind
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
from .model import Address, Component, PackageFamily, PackageInfo, SearchProfile, Span
from .opcodes import OpcodeBehavior, OpcodeCategory, behavior_for
from .package import LogoVistaPackage, open_package
from .render import GaijiPolicy, HtmlProfile, render_html, render_text
from .search import SearchHit, SearchResults, normalize_query

__all__ = [
    "Address",
    "BlockKind",
    "BlockNode",
    "Component",
    "BodySourceInfo",
    "BodySourceSupport",
    "Confidence",
    "Diagnostic",
    "DiagnosticArea",
    "EntryDocument",
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
    "OpcodeBehavior",
    "OpcodeCategory",
    "ResourceRef",
    "ResourceKind",
    "ResourceStatus",
    "SearchProfile",
    "SearchHit",
    "SearchResults",
    "SsedBodySourceKind",
    "Severity",
    "Span",
    "behavior_for",
    "detect_family",
    "normalize_query",
    "open_package",
    "render_html",
    "render_text",
]
