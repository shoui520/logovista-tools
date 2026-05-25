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
    ResourceKind,
    ResourceRef,
    ResourceStatus,
)
from .gaiji import GaijiDisplayStatus
from .gaiji_resolution import GaijiResolution
from .model import Address, Entry, PackageFamily, PackageInfo, SearchProfile
from .package import LogoVistaPackage, open_package, open_reader_package
from .reader import (
    EntryTarget,
    EntryTargetKind,
    EntryView,
    HomeSurface,
    HomeSurfaceKind,
    ReaderStatus,
    UnsupportedReaderPackage,
    VisualBody,
    VisualBodyKind,
)
from .resources import ResourceLocation
from .search import SearchHit, SearchResults, TitleResolution, normalize_query

__all__ = [
    "Address",
    "BodySourceInfo",
    "BodySourceSupport",
    "Confidence",
    "Diagnostic",
    "DiagnosticArea",
    "DiagnosticCode",
    "Dictionary",
    "Entry",
    "EntryTarget",
    "EntryTargetKind",
    "EntryView",
    "GaijiDisplayStatus",
    "GaijiResolution",
    "HomeSurface",
    "HomeSurfaceKind",
    "LogoVistaPackage",
    "Location",
    "PackageFamily",
    "PackageInfo",
    "ResourceRef",
    "ResourceKind",
    "ResourceLocation",
    "ResourceStatus",
    "ReaderStatus",
    "SearchProfile",
    "SearchHit",
    "SearchResults",
    "SidecarRole",
    "SsedBodySourceKind",
    "Severity",
    "TitleResolution",
    "UnsupportedReaderPackage",
    "VisualBody",
    "VisualBodyKind",
    "detect_family",
    "normalize_query",
    "open_package",
    "open_reader_package",
]
