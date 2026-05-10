"""Local crypto helpers for observed LogoVista SSED payloads."""

from __future__ import annotations

import hashlib

from .errors import CryptoError


LOGOFONT_PASSPHRASE = b"LogoFontCipher"
AES_BLOCK = 16


def logofont_key_iv() -> tuple[bytes, bytes]:
    digest = hashlib.sha256(LOGOFONT_PASSPHRASE).digest()
    return digest[:16], digest[16:]


def _cipher_modules():
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover
        raise CryptoError("LogoFontCipher support requires the optional cryptography package") from exc
    return Cipher, algorithms, modes, padding


def decrypt_logofont_prefix(data: bytes, *, size: int = AES_BLOCK) -> bytes:
    if len(data) < AES_BLOCK:
        return b""
    size = max(AES_BLOCK, size)
    size -= size % AES_BLOCK
    Cipher, algorithms, modes, _padding = _cipher_modules()
    key, iv = logofont_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data[:size]) + decryptor.finalize()


def decrypt_logofont(data: bytes) -> bytes:
    if len(data) % AES_BLOCK:
        raise CryptoError("encrypted payload length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, padding = _cipher_modules()
    key, iv = logofont_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(data) + decryptor.finalize()

    unpadder = padding.PKCS7(AES_BLOCK * 8).unpadder()
    try:
        return unpadder.update(plaintext) + unpadder.finalize()
    except ValueError:
        return plaintext
