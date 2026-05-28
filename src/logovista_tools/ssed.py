#!/usr/bin/env python3
"""Raw LogoVista/SystemSoft SSED probes.

This is intentionally low-level: it treats the .IDX/.DIC files as the primary
dictionary source, not the mobile SQLite cache.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .lvcrypto import (
    LogoVistaCryptoError,
    LogoVistaCryptoUnavailable,
    decrypt_macos_logofont_cipher_bytes,
    decrypt_macos_logofont_cipher_prefix,
    decrypt_logofont_cipher_bytes,
    decrypt_logofont_cipher_prefix,
)


BLOCK_SIZE = 2048
CHUNK_SIZE = 0x8000
WINDOW_SIZE = 0xFF0
SSEDDATA_MAGIC = b"SSEDDATA"
SSEDINFO_MAGIC = b"SSEDINFO"
ANDROID_LVEDINFO_MAGIC = b"LVEDINFO"

HONMON_COMPONENT_NAMES = {"HONMON.DIC", "HONMON.DIN", "HONMON.DIW", "HONMON"}
MAC_HONMON_ZIP_PASSWORD_PREFIX = "casKet"
ANDROID_DIW_PREFIX = b"LV_"
ANDROID_DIW_PASSWORD = "resworbncidnatsivogol--emulator"
ANDROID_DIW_IV = bytes.fromhex("24c015a337a525ae70d000cf0a41eb40")
ANDROID_DIW_SALT = bytes(
    [0xE4, 0x4D, 0x20, 0x8A, 0x8F, 0xDB, 0x4A, 0xD4, 0x1F, 0x58, 0xDD, 0xE7, 0x61, 0x8B, 0xC8, 0x8F, 0xE1, 0x34, 0xAC, 0x6D]
)


def read_file_prefix(path: Path, size: int) -> bytes:
    with path.open("rb") as fh:
        return fh.read(size)


@dataclass(frozen=True)
class SsedInfoElement:
    index: int
    multi: int
    type: int
    start: int
    end: int
    data: bytes
    filename: str

    @property
    def block_count(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class SsedInfoLayout:
    """Physical layout details for an observed ``SSEDINFO`` catalog."""

    component_count_offset: int
    record_start: int
    record_size: int
    component_count: int
    trailing_bytes: int


def is_metadata_noise_path(path: Path) -> bool:
    """Return true for platform metadata files that are not dictionary files."""

    return (
        path.name.startswith("._")
        or path.name.endswith(":Zone.Identifier")
        or "__MACOSX" in path.parts
    )


@dataclass(frozen=True)
class CaseFoldedDirectory:
    """One-directory, casefolded lookup preserving actual on-disk names."""

    root: Path
    entries_by_key: dict[str, tuple[Path, ...]]

    @classmethod
    def from_path(cls, root: Path) -> "CaseFoldedDirectory":
        entries: dict[str, list[Path]] = {}
        try:
            children = list(root.iterdir())
        except OSError:
            children = []
        for child in children:
            if is_metadata_noise_path(child):
                continue
            entries.setdefault(child.name.casefold(), []).append(child)
        return cls(
            root=root,
            entries_by_key={
                key: tuple(sorted(paths, key=lambda path: path.name))
                for key, paths in sorted(entries.items())
            },
        )

    def find(self, name: str) -> Path | None:
        matches = self.entries_by_key.get(name.casefold(), ())
        if len(matches) == 1:
            return matches[0]
        return None

    def files_with_suffix(self, suffix: str) -> tuple[Path, ...]:
        folded = suffix.casefold()
        return tuple(
            sorted(
                (path for paths in self.entries_by_key.values() for path in paths if path.is_file() and path.suffix.casefold() == folded),
                key=lambda path: (path.name.casefold(), path.name),
            )
        )

    def collisions(self) -> dict[str, tuple[str, ...]]:
        return {
            key: tuple(path.name for path in paths)
            for key, paths in self.entries_by_key.items()
            if len(paths) > 1
        }


def iter_files_with_suffix(root: Path, suffix: str, *, recursive: bool = False) -> Iterator[Path]:
    """Yield files with *suffix* using casefolded suffix comparison."""

    if root.is_file():
        actual = CaseFoldedDirectory.from_path(root.parent).find(root.name)
        candidate = actual if actual is not None else root
        if candidate.suffix.casefold() == suffix.casefold() and not is_metadata_noise_path(candidate):
            yield candidate
        return
    if not root.is_dir():
        return
    if not recursive:
        yield from CaseFoldedDirectory.from_path(root).files_with_suffix(suffix)
        return
    try:
        children = sorted(root.iterdir(), key=lambda path: (path.name.casefold(), path.name))
    except OSError:
        return
    for child in children:
        if is_metadata_noise_path(child):
            continue
        if child.is_dir():
            yield from iter_files_with_suffix(child, suffix, recursive=True)
        elif child.is_file() and child.suffix.casefold() == suffix.casefold():
            yield child


def resolve_case_insensitive_path(root: Path, relative: str | Path) -> Path | None:
    """Resolve a package-local path without trusting caller/catalog casing."""

    candidate = Path(relative)
    if candidate.is_absolute():
        if not candidate.exists():
            return None
        return CaseFoldedDirectory.from_path(candidate.parent).find(candidate.name) or candidate
    current = root
    for part in Path(str(relative).replace("\\", "/")).parts:
        child = CaseFoldedDirectory.from_path(current).find(part)
        if child is None:
            return None
        current = child
    return current


def is_honmon_component(element: SsedInfoElement) -> bool:
    """Return true for the SSED body component across Windows and Mac packages."""

    return element.type == 0x00 or element.filename.upper() in HONMON_COMPONENT_NAMES


def honmon_component(elements: list[SsedInfoElement]) -> SsedInfoElement | None:
    return next((element for element in elements if is_honmon_component(element)), None)


def is_honmon_component_name(name: str) -> bool:
    return name.upper() in HONMON_COMPONENT_NAMES


def be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _is_ascii_filename(data: bytes) -> bool:
    if not data:
        return False
    return all(0x20 <= value < 0x7F for value in data)


def _decode_ssedinfo_filename(rec: bytes) -> tuple[str, bool]:
    if len(rec) < 0x11:
        return "", False
    length = rec[0x10]
    if 0 < length <= len(rec) - 0x11:
        raw = rec[0x11 : 0x11 + length]
        if _is_ascii_filename(raw):
            return raw.decode("ascii", errors="replace"), True
    raw = rec[0x11:].split(b"\x00", 1)[0]
    if _is_ascii_filename(raw):
        return raw.decode("ascii", errors="replace"), True
    return raw.decode("ascii", errors="replace"), False


def _parse_ssedinfo_records(
    data: bytes,
    *,
    component_count_offset: int,
    record_start: int,
    record_size: int = 0x30,
) -> tuple[list[SsedInfoElement], SsedInfoLayout, int]:
    if component_count_offset >= len(data):
        raise ValueError("component count offset outside SSEDINFO")
    n_element = data[component_count_offset]
    end = record_start + n_element * record_size
    if n_element == 0 or record_start < 0 or end > len(data):
        raise ValueError("component records outside SSEDINFO")

    elements: list[SsedInfoElement] = []
    valid_filenames = 0
    for index in range(n_element):
        pos = record_start + index * record_size
        rec = data[pos : pos + record_size]
        if len(rec) != record_size:
            raise ValueError("short SSEDINFO component record")
        filename, valid_filename = _decode_ssedinfo_filename(rec)
        valid_filenames += int(valid_filename)
        elements.append(
            SsedInfoElement(
                index=index,
                multi=rec[2],
                type=rec[3],
                start=int.from_bytes(rec[4:8], "big"),
                end=int.from_bytes(rec[8:12], "big"),
                data=bytes(rec[12:16]),
                filename=filename,
            )
        )

    layout = SsedInfoLayout(
        component_count_offset=component_count_offset,
        record_start=record_start,
        record_size=record_size,
        component_count=n_element,
        trailing_bytes=len(data) - end,
    )
    return elements, layout, valid_filenames


def parse_ssedinfo_with_layout(path: Path) -> tuple[str, list[SsedInfoElement], SsedInfoLayout]:
    data = path.read_bytes()
    if data[:8] not in {SSEDINFO_MAGIC, ANDROID_LVEDINFO_MAGIC}:
        raise ValueError(f"not SSEDINFO: {path}")

    title_len = data[12]
    title = data[13 : 13 + title_len].split(b"\x00", 1)[0].decode("cp932", errors="replace")
    candidates = (
        (0x4D, 0x80),
        # Observed in LVLMultiView packages: the header/record table is
        # shifted left by one byte, but the record body layout is unchanged.
        (0x4C, 0x7F),
        (0x4C, 0x80),
        (0x4D, 0x7F),
    )
    parsed: list[tuple[int, int, list[SsedInfoElement], SsedInfoLayout]] = []
    for component_count_offset, record_start in candidates:
        try:
            elements, layout, score = _parse_ssedinfo_records(
                data,
                component_count_offset=component_count_offset,
                record_start=record_start,
            )
        except ValueError:
            continue
        parsed.append((score, int(component_count_offset == 0x4D and record_start == 0x80), elements, layout))

    if not parsed:
        raise ValueError(f"could not parse SSEDINFO component records: {path}")

    parsed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _score, _preferred, elements, layout = parsed[0]
    if _score != layout.component_count:
        raise ValueError(f"could not identify SSEDINFO component filename layout: {path}")
    return title, elements, layout


def parse_ssedinfo(path: Path) -> tuple[str, list[SsedInfoElement]]:
    title, elements, _layout = parse_ssedinfo_with_layout(path)
    return title, elements


def is_sseddata_bytes(data: bytes) -> bool:
    return data[:8] == SSEDDATA_MAGIC


def _pkcs12_password_bytes(password: str) -> bytes:
    return password.encode("utf-16-be") + b"\x00\x00"


def _pkcs12_adjust(blocks: bytearray, offset: int, block: bytes) -> None:
    carry = block[-1] + blocks[offset + len(block) - 1] + 1
    blocks[offset + len(block) - 1] = carry & 0xFF
    carry >>= 8
    for index in range(len(block) - 2, -1, -1):
        carry += block[index] + blocks[offset + index]
        blocks[offset + index] = carry & 0xFF
        carry >>= 8


def _repeat_to_block_multiple(data: bytes, block_size: int) -> bytes:
    if not data:
        return b""
    length = block_size * math.ceil(len(data) / block_size)
    return (data * (length // len(data) + 1))[:length]


def _derive_android_diw_aes_key() -> bytes:
    """Match BouncyCastle ``PBEWITHSHAAND256BITAES-CBC-BC`` key bytes."""

    u = 20
    v = 64
    target_len = 32
    diversifier = b"\x01" * v
    salt = _repeat_to_block_multiple(ANDROID_DIW_SALT, v)
    password = _repeat_to_block_multiple(_pkcs12_password_bytes(ANDROID_DIW_PASSWORD), v)
    blocks = bytearray(salt + password)
    out = bytearray()
    for _round in range(math.ceil(target_len / u)):
        digest = hashlib.sha1(diversifier + blocks).digest()
        for _iteration in range(1023):
            digest = hashlib.sha1(digest).digest()
        out.extend(digest)
        adjust = (digest * math.ceil(v / len(digest)))[:v]
        for offset in range(0, len(blocks), v):
            _pkcs12_adjust(blocks, offset, adjust)
    return bytes(out[:target_len])


def _android_diw_cipher():
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ValueError("Android HONMON.DIW decryption requires the cryptography package") from exc
    return Cipher(
        algorithms.AES(_derive_android_diw_aes_key()),
        modes.CBC(ANDROID_DIW_IV),
        backend=default_backend(),
    )


def decrypt_android_diw_bytes(data: bytes) -> bytes:
    """Decrypt Android ``HONMON.DIW`` into an SSEDDATA byte stream."""

    if len(data) % 16:
        raise ValueError("Android HONMON.DIW length is not an AES block multiple")
    decryptor = _android_diw_cipher().decryptor()
    decrypted = decryptor.update(data) + decryptor.finalize()
    pad = decrypted[-1] if decrypted else 0
    if 0 < pad <= 16 and decrypted.endswith(bytes([pad]) * pad):
        decrypted = decrypted[:-pad]
    if decrypted.startswith(ANDROID_DIW_PREFIX):
        decrypted = decrypted[len(ANDROID_DIW_PREFIX) :]
    if (
        decrypted.startswith(SSEDDATA_MAGIC)
        and len(decrypted) >= 70
        and int.from_bytes(decrypted[64:68], "big") == 0
        and int.from_bytes(decrypted[66:70], "big") > 0
    ):
        decrypted = decrypted[:64] + decrypted[66:]
    return decrypted


def decrypt_android_diw_prefix(data: bytes, *, size: int = 64) -> bytes:
    if len(data) < 16:
        return b""
    take = min(len(data), 16 * math.ceil((size + len(ANDROID_DIW_PREFIX)) / 16))
    take -= take % 16
    if take <= 0:
        return b""
    decryptor = _android_diw_cipher().decryptor()
    decrypted = decryptor.update(data[:take])
    if decrypted.startswith(ANDROID_DIW_PREFIX):
        decrypted = decrypted[len(ANDROID_DIW_PREFIX) :]
    return decrypted[:size]


def sseddata_storage_for_bytes(data: bytes) -> str:
    if is_sseddata_bytes(data):
        return "plain"
    try:
        if decrypt_android_diw_prefix(data).startswith(SSEDDATA_MAGIC):
            return "android_honmon_diw"
    except ValueError:
        pass
    try:
        if decrypt_logofont_cipher_prefix(data).startswith(SSEDDATA_MAGIC):
            return "logofont_cipher"
        if decrypt_macos_logofont_cipher_prefix(data).startswith(SSEDDATA_MAGIC):
            return "macos_logofont_cipher"
    except (LogoVistaCryptoError, LogoVistaCryptoUnavailable):
        return "unknown"
    return "unknown"


def sseddata_storage_for_file(path: Path) -> str:
    return sseddata_storage_for_bytes(read_file_prefix(path, BLOCK_SIZE))


def load_sseddata_bytes(data: bytes) -> tuple[bytes, str]:
    if is_sseddata_bytes(data):
        return data, "plain"
    try:
        decrypted = decrypt_android_diw_bytes(data)
        if is_sseddata_bytes(decrypted):
            return decrypted, "android_honmon_diw"
    except ValueError:
        pass
    try:
        for storage, decrypt in (
            ("logofont_cipher", decrypt_logofont_cipher_bytes),
            ("macos_logofont_cipher", decrypt_macos_logofont_cipher_bytes),
        ):
            decrypted = decrypt(data)
            if is_sseddata_bytes(decrypted):
                return decrypted, storage
    except LogoVistaCryptoUnavailable as exc:
        raise ValueError(f"not SSEDDATA and encrypted support is unavailable: {exc}") from exc
    except LogoVistaCryptoError as exc:
        raise ValueError(f"not SSEDDATA: {exc}") from exc
    raise ValueError("not SSEDDATA")


def load_sseddata_file(path: Path) -> tuple[bytes, str]:
    try:
        return load_sseddata_bytes(path.read_bytes())
    except ValueError as exc:
        raise ValueError(f"{exc}: {path}") from exc


def parse_sseddata_header(path: Path) -> dict[str, int | bytes | str]:
    data = read_file_prefix(path, 64)
    storage = "plain"
    if not is_sseddata_bytes(data):
        storage = sseddata_storage_for_bytes(data)
        if storage == "android_honmon_diw":
            data = decrypt_android_diw_prefix(read_file_prefix(path, BLOCK_SIZE), size=64)
        elif storage == "logofont_cipher":
            data = decrypt_logofont_cipher_prefix(data, size=64)
        elif storage == "macos_logofont_cipher":
            data = decrypt_macos_logofont_cipher_prefix(data, size=64)
        else:
            raise ValueError(f"not SSEDDATA: {path}")
    return {
        "magic": data[:8],
        "storage": storage,
        "kind": data[0x0F],
        "n_chunk": be16(data, 0x16),
        "start_block": be32(data, 0x18),
        "end_block": be32(data, 0x1C),
    }


def ssed_chunk_offsets(data: bytes) -> list[int]:
    n_chunk = be16(data, 0x16)
    return [be32(data, 64 + i * 4) for i in range(n_chunk)]


def expand_sseddata_chunk(data: bytes, chunk_offset: int) -> bytes:
    pos = chunk_offset + 2
    n_data = be16(data, pos)
    init = data[pos + 2]
    pos += 3

    window = bytearray([init]) * WINDOW_SIZE
    wintop = 0
    chunk_out = bytearray()

    for d_index in range(n_data):
        if pos + 3 > len(data):
            break
        b0, b1, literal = data[pos], data[pos + 1], data[pos + 2]
        pos += 3

        wp = (b0 << 4) | (b1 >> 4)
        length = b1 & 0x0F

        for _ in range(length):
            if len(chunk_out) >= CHUNK_SIZE or (
                d_index == n_data - 1 and len(chunk_out) % BLOCK_SIZE == 0
            ):
                break
            w = wp + wintop
            if w >= WINDOW_SIZE:
                w -= WINDOW_SIZE
            value = window[w]
            window[wintop] = value
            wintop = (wintop + 1) % WINDOW_SIZE
            chunk_out.append(value)

        if len(chunk_out) >= CHUNK_SIZE or (
            d_index == n_data - 1 and len(chunk_out) % BLOCK_SIZE == 0
        ):
            break

        window[wintop] = literal
        wintop = (wintop + 1) % WINDOW_SIZE
        chunk_out.append(literal)

    return bytes(chunk_out)


def expand_sseddata_bytes(data: bytes) -> bytes:
    data, _storage = load_sseddata_bytes(data)

    offsets = ssed_chunk_offsets(data)
    out = bytearray()

    for chunk_offset in offsets:
        out.extend(expand_sseddata_chunk(data, chunk_offset))

    return bytes(out)


def expand_sseddata_file(path: Path) -> bytes:
    data, _storage = load_sseddata_file(path)
    return expand_sseddata_bytes(data)


def expand_sseddata_file_with_storage(path: Path) -> tuple[bytes, str]:
    data, storage = load_sseddata_file(path)
    return expand_sseddata_bytes(data), storage


class SsedRandomReader:
    """Read slices from a SSED component without expanding the whole file."""

    def __init__(self, path: Path):
        self.path = path
        prefix = read_file_prefix(path, 64)
        self.data: bytes | None = None
        self.file_size = path.stat().st_size
        if is_sseddata_bytes(prefix):
            self.storage = "plain"
            n_chunk = be16(prefix, 0x16)
            header_size = 64 + (4 * n_chunk)
            header_bytes = read_file_prefix(path, header_size)
        else:
            # Encrypted components still need full-file decryption before chunk
            # offsets can be used. Plain SSEDDATA stays file-backed.
            self.data, self.storage = load_sseddata_file(path)
            header_bytes = self.data
        self.header = {
            "kind": header_bytes[0x0F],
            "n_chunk": be16(header_bytes, 0x16),
            "start_block": be32(header_bytes, 0x18),
            "end_block": be32(header_bytes, 0x1C),
        }
        self.offsets = ssed_chunk_offsets(header_bytes)
        self._chunk_cache: dict[int, bytes] = {}

    @property
    def start_block(self) -> int:
        return int(self.header["start_block"])

    @property
    def end_block(self) -> int:
        return int(self.header["end_block"])

    @property
    def expanded_size(self) -> int:
        return (self.end_block - self.start_block + 1) * BLOCK_SIZE

    def _chunk(self, index: int) -> bytes:
        if index < 0 or index >= len(self.offsets):
            return b""
        if index not in self._chunk_cache:
            chunk_offset = self.offsets[index]
            if self.data is not None:
                self._chunk_cache[index] = expand_sseddata_chunk(self.data, chunk_offset)
            else:
                next_offset = self.offsets[index + 1] if index + 1 < len(self.offsets) else self.file_size
                with self.path.open("rb") as fh:
                    fh.seek(chunk_offset)
                    chunk_data = fh.read(max(0, next_offset - chunk_offset))
                self._chunk_cache[index] = expand_sseddata_chunk(chunk_data, 0)
        return self._chunk_cache[index]

    def read(self, offset: int, size: int) -> bytes:
        if offset < 0 or size <= 0:
            return b""
        out = bytearray()
        current = offset
        while len(out) < size:
            chunk_index = current // CHUNK_SIZE
            chunk_offset = current % CHUNK_SIZE
            chunk = self._chunk(chunk_index)
            if not chunk or chunk_offset >= len(chunk):
                break
            take = min(size - len(out), len(chunk) - chunk_offset)
            out.extend(chunk[chunk_offset : chunk_offset + take])
            current += take
        return bytes(out)


def command_info(args: argparse.Namespace) -> None:
    path = args.path
    data = read_file_prefix(path, 8)
    if data in {SSEDINFO_MAGIC, ANDROID_LVEDINFO_MAGIC}:
        title, elements = parse_ssedinfo(path)
        print(f"title: {title}")
        print(f"elements: {len(elements)}")
        for element in elements:
            if args.all or element.start:
                print(
                    f"{element.index:02d} {element.filename:16s} "
                    f"multi={element.multi:02x} type={element.type:02x} "
                    f"start={element.start:#x} end={element.end:#x} "
                    f"blocks={element.block_count:#x} data={element.data.hex()}"
                )
    elif data == b"SSEDDATA":
        header = parse_sseddata_header(path)
        print(
            f"chunks={header['n_chunk']} start={header['start_block']:#x} "
            f"end={header['end_block']:#x} kind={header['kind']:#x}"
        )
    else:
        raise ValueError(f"unknown raw file type: {path}")


def command_expand(args: argparse.Namespace) -> None:
    expanded = expand_sseddata_file(args.dic)
    args.out.write_bytes(expanded)
    print(f"expanded {args.dic} -> {args.out}")
    print(f"bytes: {len(expanded)}")


def find_case_insensitive(directory: Path, name: str) -> Path | None:
    lookup = CaseFoldedDirectory.from_path(directory)
    direct = lookup.find(name)
    if direct is not None:
        return direct
    if name.upper() == "HONMON.DIN":
        wrapper = lookup.find("HONMON")
        if wrapper is not None and wrapper.is_file():
            return _extract_zip_component(wrapper, name, directory=directory)
    return None


def _mac_honmon_zip_passwords(directory: Path) -> tuple[bytes | None, ...]:
    """Return observed Mac HONMON zip passwords for a package directory.

    The NKGORIN2 Mac updater invokes ``unzip -P`` against an extensionless
    ``HONMON`` zip wrapper. Its password is ``casKet`` plus the product's
    eight-hex renderer/index stem, e.g. ``casKet0000012e``.
    """

    stems: list[str] = []
    for path in CaseFoldedDirectory.from_path(directory).files_with_suffix(".idx"):
        stem = path.stem
        if len(stem) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in stem):
            stems.append(stem.lower())
    passwords: list[bytes | None] = [None]
    for stem in sorted(set(stems)):
        passwords.append(f"{MAC_HONMON_ZIP_PASSWORD_PREFIX}{stem}".encode("ascii"))
    return tuple(passwords)


def _component_cache_path(zip_path: Path, member_name: str, password: bytes | None) -> Path:
    stat = zip_path.stat()
    digest = hashlib.sha256()
    digest.update(str(zip_path.resolve()).encode("utf-8", errors="surrogateescape"))
    digest.update(stat.st_size.to_bytes(8, "big", signed=False))
    digest.update(stat.st_mtime_ns.to_bytes(8, "big", signed=False))
    digest.update(member_name.casefold().encode("utf-8", errors="surrogateescape"))
    if password:
        digest.update(password)
    cache_root = Path(tempfile.gettempdir()) / "logovista-tools-component-cache" / digest.hexdigest()
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root / member_name


def _extract_zip_component(zip_path: Path, member_name: str, *, directory: Path) -> Path | None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = {name.casefold(): name for name in archive.namelist()}
            actual_member = names.get(member_name.casefold())
            if actual_member is None:
                return None
            for password in _mac_honmon_zip_passwords(directory):
                out_path = _component_cache_path(zip_path, member_name, password)
                if out_path.is_file():
                    return out_path
                try:
                    data = archive.read(actual_member, pwd=password)
                except RuntimeError:
                    continue
                tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
                tmp_path.write_bytes(data)
                tmp_path.replace(out_path)
                return out_path
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def resolve_component_file(directory: Path, name: str) -> Path | None:
    """Resolve a declared SSED component to a readable file.

    Most components are direct package files. Observed Mac OS X packages may
    declare ``HONMON.DIN`` in SSEDINFO while storing an extensionless encrypted
    zip wrapper named ``HONMON`` on disk. For that narrow layout, materialize
    the declared member into a deterministic temp cache and return that path.
    """

    return find_case_insensitive(directory, name)


def write_epwing_catalog_header(out, elements: list[SsedInfoElement]) -> None:
    visible = [element for element in elements if element.start and element.multi != 0xFF]
    out.seek(0)
    out.write(len(visible).to_bytes(2, "big"))
    out.write(b"\x00" * 14)
    for element in visible:
        record = bytearray(16)
        record[0] = element.type
        record[2:6] = element.start.to_bytes(4, "big")
        record[6:10] = element.block_count.to_bytes(4, "big")
        record[10:14] = element.data
        out.write(record)
    for _ in range(len(visible) + 1, 0x80):
        out.write(b"\x00" * 16)


def command_expand_book(args: argparse.Namespace) -> None:
    title, elements = parse_ssedinfo(args.idx)
    source_dir = args.idx.parent
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w+b") as out:
        for element in elements:
            if not element.start:
                continue
            source = find_case_insensitive(source_dir, element.filename)
            if source is None:
                if args.strict:
                    raise FileNotFoundError(element.filename)
                print(f"skip missing {element.filename}")
                continue
            expanded = expand_sseddata_file(source)
            out.seek((element.start - 1) * BLOCK_SIZE)
            out.write(expanded)
            print(
                f"{element.filename:16s} start={element.start:#x} "
                f"bytes={len(expanded)}"
            )
        write_epwing_catalog_header(out, elements)
    print(f"title: {title}")
    print(f"expanded book: {args.out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info")
    p_info.add_argument("path", type=Path)
    p_info.add_argument("--all", action="store_true")
    p_info.set_defaults(func=command_info)

    p_expand = sub.add_parser("expand")
    p_expand.add_argument("dic", type=Path)
    p_expand.add_argument("out", type=Path)
    p_expand.set_defaults(func=command_expand)

    p_book = sub.add_parser("expand-book")
    p_book.add_argument("idx", type=Path)
    p_book.add_argument("out", type=Path)
    p_book.add_argument("--strict", action="store_true")
    p_book.set_defaults(func=command_expand_book)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
