"""Experimental LogoVista reader core.

This package is intentionally independent from ``logovista_tools``.  It is a
clean reader-oriented reimplementation of the currently understood package
model, starting with SSED.
"""

from .detect import detect_family
from .body_source import BodySourceInfo, BodySourceSupport, Confidence, SsedBodySourceKind
from .diagnostics import Diagnostic, DiagnosticArea, Location, Severity
from .document import BlockNode, EntryDocument, InlineNode, ResourceRef
from .model import Address, Component, PackageFamily, PackageInfo, SearchProfile, Span
from .package import LogoVistaPackage, open_package
from .render import GaijiPolicy, HtmlProfile, render_html, render_text
from .search import SearchHit, SearchResults, normalize_query

__all__ = [
    "Address",
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
    "InlineNode",
    "LogoVistaPackage",
    "Location",
    "PackageFamily",
    "PackageInfo",
    "ResourceRef",
    "SearchProfile",
    "SearchHit",
    "SearchResults",
    "SsedBodySourceKind",
    "Severity",
    "Span",
    "detect_family",
    "normalize_query",
    "open_package",
    "render_html",
    "render_text",
]
