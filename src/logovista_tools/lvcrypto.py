"""LogoVista encryption helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


LOGOFONT_CIPHER_PASSPHRASE = b"LogoFontCipher"
BLOCK_SIZE = 16


class LogoVistaCryptoError(RuntimeError):
    """Raised when a LogoVista encrypted payload cannot be decrypted."""


class LogoVistaCryptoUnavailable(LogoVistaCryptoError):
    """Raised when the optional cryptography backend is unavailable."""


def logofont_cipher_key_iv() -> tuple[bytes, bytes]:
    """Return the AES-128-CBC key/IV used by the observed LogoVista decryptor.

    EJJE200 ships ``vlpljbl.bin``, a small Crypto++ program. Static analysis
    shows it SHA-256 hashes the obfuscated literal ``LogoFontCipher``; the first
    16 digest bytes are the AES-128 key and the second 16 bytes are the CBC IV.
    """

    digest = hashlib.sha256(LOGOFONT_CIPHER_PASSPHRASE).digest()
    return digest[:16], digest[16:]


def _cryptography_modules():
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise LogoVistaCryptoUnavailable(
            "LogoVista encrypted payload support requires the optional "
            "'cryptography' package. Install with: pip install .[crypto]"
        ) from exc
    return Cipher, algorithms, modes, padding


def decrypt_logofont_cipher_prefix(data: bytes, *, size: int = BLOCK_SIZE) -> bytes:
    """Decrypt a prefix without PKCS#7 unpadding.

    This is used for cheap magic-byte detection before reading/decrypting a
    large payload.
    """

    if len(data) < BLOCK_SIZE:
        return b""
    size = max(BLOCK_SIZE, size)
    size -= size % BLOCK_SIZE
    chunk = data[:size]
    Cipher, algorithms, modes, _padding = _cryptography_modules()
    key, iv = logofont_cipher_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(chunk) + decryptor.finalize()


def decrypt_logofont_cipher_bytes(data: bytes) -> bytes:
    """Decrypt a full LogoFontCipher AES-CBC payload.

    The shipped Crypto++ decryptor uses ``StreamTransformationFilter`` with the
    default PKCS#7 padding behavior. If a payload has no valid padding we return
    the raw decrypted bytes, which keeps this helper useful for forensic probes.
    Callers that expect a specific plaintext magic should still validate it.
    """

    if len(data) % BLOCK_SIZE:
        raise LogoVistaCryptoError("encrypted payload length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, padding = _cryptography_modules()
    key, iv = logofont_cipher_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(data) + decryptor.finalize()

    unpadder = padding.PKCS7(BLOCK_SIZE * 8).unpadder()
    try:
        return unpadder.update(plaintext) + unpadder.finalize()
    except ValueError:
        return plaintext


def decrypt_logofont_cipher_file(path: Path) -> bytes:
    return decrypt_logofont_cipher_bytes(path.read_bytes())

