"""LogoVista encryption helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO


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


def macos_logofont_cipher_key_iv() -> tuple[bytes, bytes]:
    """Return the AES-128-CBC key/IV used by observed Mac OS X SSED payloads.

    Mac SSED ``HONMON.DIN`` payloads use the same obfuscated passphrase string
    as LogoFontCipher, but the AES key is the first 16 ASCII bytes of the
    SHA-256 hex digest and the IV is all zeroes.
    """

    key = hashlib.sha256(LOGOFONT_CIPHER_PASSPHRASE).hexdigest().encode("ascii")[:16]
    return key, b"\x00" * BLOCK_SIZE


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


def decrypt_macos_logofont_cipher_prefix(data: bytes, *, size: int = BLOCK_SIZE) -> bytes:
    """Decrypt a prefix of the Mac OS X SSED AES-CBC variant."""

    if len(data) < BLOCK_SIZE:
        return b""
    size = max(BLOCK_SIZE, size)
    size -= size % BLOCK_SIZE
    chunk = data[:size]
    Cipher, algorithms, modes, _padding = _cryptography_modules()
    key, iv = macos_logofont_cipher_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(chunk) + decryptor.finalize()


def decrypt_macos_logofont_cipher_bytes(data: bytes) -> bytes:
    """Decrypt a full Mac OS X SSED AES-CBC payload."""

    if len(data) % BLOCK_SIZE:
        raise LogoVistaCryptoError("encrypted payload length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, padding = _cryptography_modules()
    key, iv = macos_logofont_cipher_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(data) + decryptor.finalize()

    unpadder = padding.PKCS7(BLOCK_SIZE * 8).unpadder()
    try:
        return unpadder.update(plaintext) + unpadder.finalize()
    except ValueError:
        return plaintext


def decrypt_logofont_cipher_file(path: Path) -> bytes:
    return decrypt_logofont_cipher_bytes(path.read_bytes())


def decrypt_logofont_cipher_stream(infile: BinaryIO, outfile: BinaryIO, *, chunk_size: int = 1024 * 1024) -> int:
    """Decrypt a LogoFontCipher payload from one binary file object to another.

    Unlike :func:`decrypt_logofont_cipher_file`, this keeps memory use bounded.
    It expects a normal PKCS#7-padded payload, which matches the observed
    Windows sidecars that decrypt to SQLite.
    """

    if chunk_size < BLOCK_SIZE:
        chunk_size = BLOCK_SIZE
    chunk_size -= chunk_size % BLOCK_SIZE

    Cipher, algorithms, modes, padding = _cryptography_modules()
    key, iv = logofont_cipher_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(BLOCK_SIZE * 8).unpadder()
    written = 0
    pending = b""

    while True:
        chunk = infile.read(chunk_size)
        if not chunk:
            break
        pending += chunk
        process_len = len(pending) - (len(pending) % BLOCK_SIZE)
        if not process_len:
            continue
        plaintext = decryptor.update(pending[:process_len])
        pending = pending[process_len:]
        out = unpadder.update(plaintext)
        outfile.write(out)
        written += len(out)

    if pending:
        raise LogoVistaCryptoError("encrypted payload length is not a multiple of 16 bytes")

    out = unpadder.update(decryptor.finalize()) + unpadder.finalize()
    outfile.write(out)
    written += len(out)
    return written


def decrypt_logofont_cipher_file_to_path(path: Path, out: Path, *, chunk_size: int = 1024 * 1024) -> int:
    """Decrypt a LogoFontCipher file to *out* without reading it all at once."""

    with path.open("rb") as infile, out.open("wb") as outfile:
        return decrypt_logofont_cipher_stream(infile, outfile, chunk_size=chunk_size)


def decrypt_macos_logofont_cipher_stream(infile: BinaryIO, outfile: BinaryIO, *, chunk_size: int = 1024 * 1024) -> int:
    """Decrypt the Mac OS X SSED AES-CBC variant from one binary stream."""

    if chunk_size < BLOCK_SIZE:
        chunk_size = BLOCK_SIZE
    chunk_size -= chunk_size % BLOCK_SIZE

    Cipher, algorithms, modes, padding = _cryptography_modules()
    key, iv = macos_logofont_cipher_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(BLOCK_SIZE * 8).unpadder()
    written = 0
    pending = b""

    while True:
        chunk = infile.read(chunk_size)
        if not chunk:
            break
        pending += chunk
        process_len = len(pending) - (len(pending) % BLOCK_SIZE)
        if not process_len:
            continue
        plaintext = decryptor.update(pending[:process_len])
        pending = pending[process_len:]
        out = unpadder.update(plaintext)
        outfile.write(out)
        written += len(out)

    if pending:
        raise LogoVistaCryptoError("encrypted payload length is not a multiple of 16 bytes")

    out = unpadder.update(decryptor.finalize()) + unpadder.finalize()
    outfile.write(out)
    written += len(out)
    return written


def decrypt_macos_logofont_cipher_file_to_path(path: Path, out: Path, *, chunk_size: int = 1024 * 1024) -> int:
    """Decrypt a Mac OS X SSED AES-CBC payload to *out*."""

    with path.open("rb") as infile, out.open("wb") as outfile:
        return decrypt_macos_logofont_cipher_stream(infile, outfile, chunk_size=chunk_size)


def decrypt_logofont_cipher_auto_file_to_path(path: Path, out: Path, *, chunk_size: int = 1024 * 1024) -> tuple[int, str]:
    """Decrypt an observed LogoVista AES-CBC payload, auto-selecting known variants."""

    with path.open("rb") as infile:
        prefix = infile.read(4096)
    try:
        if decrypt_macos_logofont_cipher_prefix(prefix, size=min(len(prefix), 4096)).startswith(b"SSEDDATA"):
            written = decrypt_macos_logofont_cipher_file_to_path(path, out, chunk_size=chunk_size)
            return written, "macos_logofont_cipher"
    except LogoVistaCryptoError:
        pass
    written = decrypt_logofont_cipher_file_to_path(path, out, chunk_size=chunk_size)
    return written, "logofont_cipher"
