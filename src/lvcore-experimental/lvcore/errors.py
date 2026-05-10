"""lvcore exception types."""


class LvCoreError(RuntimeError):
    """Base exception for lvcore failures."""


class UnsupportedPackageError(LvCoreError):
    """Raised when a recognized package family has no reader implementation."""


class FormatError(LvCoreError):
    """Raised for malformed or unsupported binary data."""


class CryptoError(LvCoreError):
    """Raised for encrypted payload errors."""
