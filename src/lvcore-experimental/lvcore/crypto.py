"""Local crypto helpers for observed LogoVista SSED payloads."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import CryptoError


LOGOFONT_PASSPHRASE = b"LogoFontCipher"
AES_BLOCK = 16


def logofont_key_iv() -> tuple[bytes, bytes]:
    digest = hashlib.sha256(LOGOFONT_PASSPHRASE).digest()
    return digest[:16], digest[16:]


def macos_logofont_key_iv() -> tuple[bytes, bytes]:
    """Return the AES key/IV used by observed Mac OS X SSED payloads."""

    key = hashlib.sha256(LOGOFONT_PASSPHRASE).hexdigest().encode("ascii")[:16]
    return key, b"\x00" * AES_BLOCK


def _cipher_modules():
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover
        raise CryptoError("LogoFontCipher support requires the optional cryptography package") from exc
    return Cipher, algorithms, modes, padding


def _decrypt_aes_cbc_blocks(data: bytes, *, key: bytes, iv: bytes) -> bytes:
    if len(data) % AES_BLOCK:
        raise CryptoError("encrypted block range length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, _padding = _cipher_modules()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def aes_cbc_plaintext_size(path: Path, *, key: bytes, iv: bytes) -> int:
    """Return the unpadded plaintext size for an AES-CBC file.

    The observed LogoVista AES-CBC payloads are whole-file CBC streams. The
    last plaintext block is enough to identify PKCS#7 padding, so callers can
    know the logical plaintext length without decrypting the whole file.
    """

    cipher_size = path.stat().st_size
    if cipher_size % AES_BLOCK:
        raise CryptoError("encrypted payload length is not a multiple of 16 bytes")
    if cipher_size == 0:
        return 0
    with path.open("rb") as fh:
        if cipher_size == AES_BLOCK:
            block_iv = iv
            fh.seek(0)
        else:
            fh.seek(cipher_size - (AES_BLOCK * 2))
            block_iv = fh.read(AES_BLOCK)
        block = fh.read(AES_BLOCK)
    plain = _decrypt_aes_cbc_blocks(block, key=key, iv=block_iv)
    pad = plain[-1]
    if 1 <= pad <= AES_BLOCK and plain.endswith(bytes([pad]) * pad):
        return cipher_size - pad
    return cipher_size


def decrypt_aes_cbc_file_range(
    path: Path,
    *,
    key: bytes,
    iv: bytes,
    offset: int,
    size: int,
    plaintext_size: int | None = None,
) -> bytes:
    """Decrypt one plaintext byte range from an AES-CBC file.

    This is intentionally range-oriented for SSED random access: a caller can
    read the header/table or one compressed chunk without materializing a full
    decrypted component.
    """

    if offset < 0:
        raise ValueError("offset must be non-negative")
    if size <= 0:
        return b""
    plain_size = plaintext_size if plaintext_size is not None else aes_cbc_plaintext_size(path, key=key, iv=iv)
    if offset >= plain_size:
        return b""
    end = min(offset + size, plain_size)
    block_start = (offset // AES_BLOCK) * AES_BLOCK
    block_end = ((end + AES_BLOCK - 1) // AES_BLOCK) * AES_BLOCK
    cipher_size = path.stat().st_size
    block_end = min(block_end, cipher_size)

    with path.open("rb") as fh:
        if block_start == 0:
            range_iv = iv
        else:
            fh.seek(block_start - AES_BLOCK)
            range_iv = fh.read(AES_BLOCK)
        fh.seek(block_start)
        encrypted = fh.read(block_end - block_start)
    if len(encrypted) % AES_BLOCK:
        raise CryptoError("encrypted range length is not a multiple of 16 bytes")
    decrypted = _decrypt_aes_cbc_blocks(encrypted, key=key, iv=range_iv)
    start_in_block = offset - block_start
    return decrypted[start_in_block : start_in_block + (end - offset)]


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


def decrypt_macos_logofont_prefix(data: bytes, *, size: int = AES_BLOCK) -> bytes:
    if len(data) < AES_BLOCK:
        return b""
    size = max(AES_BLOCK, size)
    size -= size % AES_BLOCK
    Cipher, algorithms, modes, _padding = _cipher_modules()
    key, iv = macos_logofont_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data[:size]) + decryptor.finalize()


def decrypt_macos_logofont(data: bytes) -> bytes:
    if len(data) % AES_BLOCK:
        raise CryptoError("encrypted payload length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, padding = _cipher_modules()
    key, iv = macos_logofont_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(data) + decryptor.finalize()

    unpadder = padding.PKCS7(AES_BLOCK * 8).unpadder()
    try:
        return unpadder.update(plaintext) + unpadder.finalize()
    except ValueError:
        return plaintext


def decrypt_logofont_file_to_path(path: Path, out: Path, *, chunk_size: int = 1024 * 1024) -> int:
    if chunk_size < AES_BLOCK:
        chunk_size = AES_BLOCK
    chunk_size -= chunk_size % AES_BLOCK
    if chunk_size <= 0:
        chunk_size = AES_BLOCK
    if path.stat().st_size % AES_BLOCK:
        raise CryptoError("encrypted payload length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, padding = _cipher_modules()
    key, iv = logofont_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    pending = b""
    written = 0
    with path.open("rb") as infile, out.open("wb") as outfile:
        for chunk in iter(lambda: infile.read(chunk_size), b""):
            decrypted = decryptor.update(chunk)
            data = pending + decrypted
            if len(data) > AES_BLOCK:
                body = data[:-AES_BLOCK]
                outfile.write(body)
                written += len(body)
                pending = data[-AES_BLOCK:]
            else:
                pending = data
        final = pending + decryptor.finalize()
        unpadder = padding.PKCS7(AES_BLOCK * 8).unpadder()
        try:
            final = unpadder.update(final) + unpadder.finalize()
        except ValueError:
            pass
        outfile.write(final)
        written += len(final)
    return written


def decrypt_logofont_file(path: Path, *, chunk_size: int = 1024 * 1024) -> bytes:
    """Return the original decrypted bytes for a LogoFontCipher file."""

    if chunk_size < AES_BLOCK:
        chunk_size = AES_BLOCK
    chunk_size -= chunk_size % AES_BLOCK
    if chunk_size <= 0:
        chunk_size = AES_BLOCK
    if path.stat().st_size % AES_BLOCK:
        raise CryptoError("encrypted payload length is not a multiple of 16 bytes")
    Cipher, algorithms, modes, padding = _cipher_modules()
    key, iv = logofont_key_iv()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    pending = b""
    out = bytearray()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(chunk_size), b""):
            decrypted = decryptor.update(chunk)
            data = pending + decrypted
            if len(data) > AES_BLOCK:
                body = data[:-AES_BLOCK]
                out.extend(body)
                pending = data[-AES_BLOCK:]
            else:
                pending = data
        final = pending + decryptor.finalize()
        unpadder = padding.PKCS7(AES_BLOCK * 8).unpadder()
        try:
            final = unpadder.update(final) + unpadder.finalize()
        except ValueError:
            pass
        out.extend(final)
    return bytes(out)
