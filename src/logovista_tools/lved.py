"""Inspection helpers for LVED/WebView2 SQLCipher dictionary packages."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .parallel import parallel_map_ordered


LVED_SQLCIPHER_PAGE_SIZE = 4096
LVED_SQLCIPHER_RESERVE_BYTES = 80
LVED_SQLCIPHER_KDF_ITER = 256000
LVED_SQLCIPHER_KDF_ALGORITHM = "PBKDF2-HMAC-SHA512"


@dataclass(frozen=True)
class LvedPayload:
    path: Path
    kind: str
    size: int
    pages_4096: int
    mod_4096: int
    sha256: str
    entropy_sample: float
    header_hex: str
    classification: str
    inferred_dict_code: str | None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts if count)


def entropy_sample_for_file(path: Path, *, sample_size: int = 1024 * 1024) -> float:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size <= sample_size:
            data = handle.read()
        else:
            first = handle.read(sample_size // 2)
            handle.seek(max(0, size - sample_size // 2))
            data = first + handle.read(sample_size // 2)
    return shannon_entropy(data)


def is_lved_payload_name(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith(":zone.identifier"):
        return False
    return name == "main.data" or name.endswith(".dbc")


def discover_lved_payloads(roots: Iterable[Path]) -> list[Path]:
    payloads: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates = [root] if is_lved_payload_name(root) else []
        elif root.is_dir():
            candidates = [path for path in root.rglob("*") if path.is_file() and is_lved_payload_name(path)]
        else:
            candidates = []
        for path in sorted(candidates):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            payloads.append(resolved)
    return payloads


def infer_lved_dict_code(path: Path) -> str | None:
    if path.suffix.lower() == ".dbc":
        return path.stem.upper()
    for part in (path.parent.name, path.parent.parent.name if path.parent.parent else ""):
        upper = part.upper()
        if upper.startswith("_DCT_") and len(upper) > 5:
            return upper[5:]
        if re.fullmatch(r"[A-Z0-9]{4,16}", upper) and upper not in {"DIC", "DATA"}:
            return upper
    return None


def classify_lved_payload(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(16)
    size = path.stat().st_size
    if header == b"SQLite format 3\x00":
        return "plaintext_sqlite"
    if header.startswith(b"SSEDINFO") or header.startswith(b"SSEDDATA"):
        return "ssed"
    if size and size % LVED_SQLCIPHER_PAGE_SIZE == 0 and entropy_sample_for_file(path) > 7.95:
        return "sqlcipher_lved_candidate"
    return "unknown"


def inspect_lved_payload(path: Path) -> LvedPayload:
    size = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(32)
    return LvedPayload(
        path=path,
        kind="dbc" if path.suffix.lower() == ".dbc" else "main.data",
        size=size,
        pages_4096=size // LVED_SQLCIPHER_PAGE_SIZE,
        mod_4096=size % LVED_SQLCIPHER_PAGE_SIZE,
        sha256=sha256_file(path),
        entropy_sample=entropy_sample_for_file(path),
        header_hex=header.hex(),
        classification=classify_lved_payload(path),
        inferred_dict_code=infer_lved_dict_code(path),
    )


def derive_lved_sqlcipher_key(dict_id: int, dict_code: str) -> str:
    """Return the SQLCipher key string constructed by the observed LVED viewer.

    The viewer uses only dictionary metadata: the numeric dictionary id and the
    product/dictionary code. It does not use the product serial number for this
    SQLCipher key derivation path. Callers should derive it locally when
    decrypting a payload; reports must not emit final key strings.
    """

    if dict_id < 0:
        raise ValueError("dict_id must be non-negative")
    if not dict_code:
        raise ValueError("dict_code must be non-empty")
    key_code = (dict_code[0] + dict_code[-1]).lower()
    return "jlasgoi" + "ahoiam" + "pvsjhosD" + "Hfopj" + key_code + str(dict_id * 19286)


def _crypto_primitives():
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "SQLCipher validation requires the optional crypto dependency. "
            "Install with: python -m pip install -e '.[crypto]'"
        ) from exc
    return default_backend, hashes, Cipher, algorithms, modes, PBKDF2HMAC


def _derive_sqlcipher4_key(payload: bytes, key: str) -> bytes:
    default_backend, hashes, _cipher, _algorithms, _modes, PBKDF2HMAC = _crypto_primitives()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=32,
        salt=payload[:16],
        iterations=LVED_SQLCIPHER_KDF_ITER,
        backend=default_backend(),
    )
    return kdf.derive(key.encode("utf-8"))


def decrypt_lved_sqlcipher_page(
    page: bytes,
    key_material: bytes,
    *,
    page_no: int,
) -> bytes:
    default_backend, _hashes, Cipher, algorithms, modes, _PBKDF2HMAC = _crypto_primitives()
    page_size = LVED_SQLCIPHER_PAGE_SIZE
    reserve = LVED_SQLCIPHER_RESERVE_BYTES
    usable = page_size - reserve
    if len(page) != page_size:
        raise ValueError(f"expected {page_size}-byte page")
    if page_no == 1:
        ciphertext = page[16:usable]
        plaintext = Cipher(
            algorithms.AES(key_material),
            modes.CBC(page[usable : usable + 16]),
            backend=default_backend(),
        ).decryptor().update(ciphertext)
        return b"SQLite format 3\x00" + plaintext + bytes(reserve)
    plaintext = Cipher(
        algorithms.AES(key_material),
        modes.CBC(page[usable : usable + 16]),
        backend=default_backend(),
    ).decryptor().update(page[:usable])
    return plaintext + bytes(reserve)


def validate_lved_sqlcipher4(path: Path, key: str) -> dict[str, Any]:
    with path.open("rb") as handle:
        first_page = handle.read(LVED_SQLCIPHER_PAGE_SIZE)
    if len(first_page) != LVED_SQLCIPHER_PAGE_SIZE:
        return {"valid": False, "reason": "file is shorter than one page"}
    try:
        key_material = _derive_sqlcipher4_key(first_page, key)
        first_plain = decrypt_lved_sqlcipher_page(first_page, key_material, page_no=1)
    except Exception as exc:
        return {"valid": False, "reason": str(exc)}

    header_tail = first_plain[16:64]
    valid = (
        first_plain.startswith(b"SQLite format 3\x00")
        and header_tail[:2] in (b"\x10\x00", b"\x20\x00", b"\x04\x00")
        and header_tail[2] in (1, 2)
        and header_tail[3] in (1, 2)
        and header_tail[4] == LVED_SQLCIPHER_RESERVE_BYTES
    )
    return {
        "valid": valid,
        "page_size": LVED_SQLCIPHER_PAGE_SIZE,
        "reserve_bytes": LVED_SQLCIPHER_RESERVE_BYTES,
        "kdf_iter": LVED_SQLCIPHER_KDF_ITER,
        "kdf_algorithm": LVED_SQLCIPHER_KDF_ALGORITHM,
        "sqlite_header_tail_hex": header_tail[:16].hex(),
        "reason": None if valid else "decrypted first page did not match SQLite header fields",
    }


def decrypt_lved_sqlcipher4_to_path(path: Path, out_path: Path, key: str) -> int:
    raw_size = path.stat().st_size
    if raw_size % LVED_SQLCIPHER_PAGE_SIZE:
        raise ValueError("payload size is not a 4096-byte page multiple")
    with path.open("rb") as source:
        first_page = source.read(LVED_SQLCIPHER_PAGE_SIZE)
    key_material = _derive_sqlcipher4_key(first_page, key)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("rb") as source, out_path.open("wb") as out:
        for page_no in range(1, raw_size // LVED_SQLCIPHER_PAGE_SIZE + 1):
            page = source.read(LVED_SQLCIPHER_PAGE_SIZE)
            out.write(decrypt_lved_sqlcipher_page(page, key_material, page_no=page_no))
            written += LVED_SQLCIPHER_PAGE_SIZE
    return written


MEMORY_KEY_RE = re.compile(r"jlasgoiahoiampvsjhosDHfopj[A-Za-z0-9]{2,40}")


def find_lved_key_candidates_in_dump(path: Path) -> list[str]:
    data = path.read_bytes()
    keys: set[str] = set()
    for encoding in ("latin1", "utf-16le"):
        text = data.decode(encoding, errors="ignore")
        keys.update(match.group(0) for match in MEMORY_KEY_RE.finditer(text) if len(match.group(0)) < 96)
    return sorted(keys)


def _inspect_lved_payload_task(
    payload: tuple[Path, int | None, str | None, str | None, list[str]]
) -> dict[str, Any]:
    path, dict_id, dict_code, explicit_key, memory_keys = payload
    inspected = inspect_lved_payload(path)
    row: dict[str, Any] = {
        "path": str(inspected.path),
        "kind": inspected.kind,
        "size": inspected.size,
        "pages_4096": inspected.pages_4096,
        "mod_4096": inspected.mod_4096,
        "sha256": inspected.sha256,
        "entropy_sample": inspected.entropy_sample,
        "header_hex": inspected.header_hex,
        "classification": inspected.classification,
        "inferred_dict_code": inspected.inferred_dict_code,
        "validation": [],
    }
    validation_keys: list[tuple[str, str]] = []
    if explicit_key:
        validation_keys.append(("explicit_key", explicit_key))
    if dict_id is not None:
        code = dict_code or inspected.inferred_dict_code
        if code:
            validation_keys.append(("derived_metadata", derive_lved_sqlcipher_key(dict_id, code)))
    for candidate in memory_keys:
        validation_keys.append(("memory_dump_candidate", candidate))
    for source, candidate in validation_keys:
        result = validate_lved_sqlcipher4(path, candidate)
        safe_result = {key: value for key, value in result.items() if key != "reason" or value}
        safe_result["source"] = source
        row["validation"].append(safe_result)
    return row


def inspect_lved_roots(
    roots: Iterable[Path],
    *,
    dict_id: int | None = None,
    dict_code: str | None = None,
    key: str | None = None,
    memory_dump: Path | None = None,
    jobs: int | None = 1,
) -> dict[str, Any]:
    payload_paths = discover_lved_payloads(roots)
    explicit_key = key
    memory_keys = find_lved_key_candidates_in_dump(memory_dump) if memory_dump else []
    payloads = parallel_map_ordered(
        _inspect_lved_payload_task,
        [(path, dict_id, dict_code, explicit_key, memory_keys) for path in payload_paths],
        jobs=jobs,
    )

    return {
        "payloads": payloads,
        "memory_dump": {
            "path": str(memory_dump) if memory_dump else None,
            "candidate_keys": len(memory_keys),
            "candidate_key_lengths": sorted({len(item) for item in memory_keys}),
        },
        "notes": [
            "Memory dump key candidates are counted and used for validation but never emitted.",
            "The LVED SQLCipher key derivation uses dictionary id/code metadata, not the product serial.",
        ],
    }
