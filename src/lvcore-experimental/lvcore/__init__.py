"""Experimental LogoVista reader core.

This package is intentionally independent from ``logovista_tools``.  It is a
clean reader-oriented reimplementation of the currently understood package
model, starting with SSED.
"""

from .detect import detect_family
from .model import Address, Component, PackageFamily, PackageInfo, Span
from .package import LogoVistaPackage, open_package

__all__ = [
    "Address",
    "Component",
    "LogoVistaPackage",
    "PackageFamily",
    "PackageInfo",
    "Span",
    "detect_family",
    "open_package",
]
