"""Inspection helpers for modern LogoVista SQLCipher dictionary packages.

``lved`` is kept as the command/module name because the recovered viewer and
older reports use that shorthand.  The payload family itself is modeled as a
SQLCipher package family, not as a variant of SSED.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from .parallel import parallel_map_ordered


LVED_SQLCIPHER_PAGE_SIZE = 4096
LVED_SQLCIPHER_RESERVE_BYTES = 80
LVED_SQLCIPHER_KDF_ITER = 256000
LVED_SQLCIPHER_KDF_ALGORITHM = "PBKDF2-HMAC-SHA512"
LVED_SQLITE3_FAMILY_NAME = "LVED_SQLITE3"

_SQLITE_TEXT_SIGNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("html_tag", "%<%>%"),
    ("html_img", "%<img%"),
    ("html_table", "%<table%"),
    ("html_ruby", "%<ruby%"),
    ("html_math", "%<math%"),
    ("html_script", "%<script%"),
    ("lved_dataid", "%lved.dataid:%"),
    ("lved_addr", "%lved.addr%"),
    ("lved_image", "%lved.image:%"),
    ("lved_imag", "%lved.imag:%"),
    ("lved_ziptomedia", "%lved.ziptomedia:%"),
    ("lved_bookmark", "%lved.bookmark:%"),
    ("lved_sound", "%lved.sond:%"),
    ("http_link", "%http://%"),
    ("https_link", "%https://%"),
)


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


@dataclass(frozen=True)
class LvedKeyFile:
    path: Path
    match: str


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


def discover_lved_key_file(path: Path) -> LvedKeyFile | None:
    """Return the best local ``.key`` file for a SQLCipher payload.

    These files are local analysis artifacts, not dictionary resources.  The
    key text is consumed for validation/decryption but is never emitted in
    reports.
    """

    parent = path.parent
    candidates: list[tuple[Path, str]] = []
    dict_code = infer_lved_dict_code(path)
    if dict_code:
        candidates.append((parent / f"{dict_code}.key", "inferred_dict_code"))
        candidates.append((parent / f"{dict_code.lower()}.key", "inferred_dict_code_lower"))
    if path.suffix:
        candidates.append((path.with_suffix(path.suffix + ".key"), "payload_name"))
    candidates.append((parent / f"{path.stem}.key", "payload_stem"))

    seen: set[Path] = set()
    for candidate, match in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return LvedKeyFile(path=candidate, match=match)

    key_files = sorted(parent.glob("*.key"), key=lambda item: item.name.casefold())
    if len(key_files) == 1:
        return LvedKeyFile(path=key_files[0], match="single_key_in_payload_dir")
    return None


def read_lved_key_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _safe_count(con: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(con.execute(f"select count(*) from {_quote_identifier(table)}").fetchone()[0])
    except sqlite3.Error:
        return None


def _safe_pragma_rows(con: sqlite3.Connection, pragma: str, name: str) -> list[tuple[Any, ...]]:
    try:
        return list(con.execute(f"pragma {pragma}({_quote_identifier(name)})"))
    except sqlite3.Error:
        return []


def _sqlite_table_role(name: str, sql: str | None) -> str:
    lower = name.casefold()
    sql_lower = (sql or "").casefold()
    if lower == "list":
        return "entry_list"
    if lower == "content":
        return "entry_content"
    if lower == "media":
        return "media_blob_store"
    if lower == "info":
        return "info_page_store"
    if lower == "exam":
        return "example_lookup"
    if lower == "genre":
        return "genre_or_category_lookup"
    if lower == "search":
        return "fts_search"
    if lower.startswith("search_"):
        return "fts_shadow"
    if "using fts" in sql_lower:
        return "fts_search"
    return "unclassified"


def _sqlite_text_signals(values: list[object]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        if isinstance(value, bytes):
            continue
        text = str(value).casefold()
        for name, pattern in _SQLITE_TEXT_SIGNAL_PATTERNS:
            needle = pattern.strip("%").casefold()
            if needle and needle in text:
                counts[name] += 1
    return dict(sorted(counts.items()))


def _sqlite_column_profile(
    con: sqlite3.Connection,
    table: str,
    column: str,
    declared_type: str,
    *,
    sample_rows: int,
) -> dict[str, Any]:
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    profile: dict[str, Any] = {
        "name": column,
        "declared_type": declared_type,
        "sample_limit": sample_rows,
    }
    try:
        rows = con.execute(
            f"select {quoted_column} from {quoted_table} where {quoted_column} is not null limit ?",
            (sample_rows,),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    values = [row[0] for row in rows]
    profile["non_null_sample_count"] = len(values)
    if values:
        storage_types = Counter(type(value).__name__ for value in values)
        profile["sample_storage_types"] = dict(sorted(storage_types.items()))
        profile["sample_max_length"] = max(len(value) if isinstance(value, bytes) else len(str(value)) for value in values)
    type_hint = declared_type.casefold()
    if "text" in type_hint or not declared_type:
        signals = _sqlite_text_signals(values)
        if signals:
            profile["sample_text_signals"] = signals
    return profile


def _sqlite_value_distribution(con: sqlite3.Connection, table: str, column: str, *, limit: int = 64) -> list[dict[str, Any]]:
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    try:
        rows = con.execute(
            f"select {quoted_column}, count(*) as n from {quoted_table} "
            f"group by {quoted_column} order by n desc, {quoted_column} limit ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "value": None if row[0] is None else str(row[0]),
            "count": int(row[1]),
        }
        for row in rows
    ]


def _sqlite_table_features(name: str, columns: list[str]) -> list[str]:
    lower = name.casefold()
    colset = {column.casefold() for column in columns}
    features: list[str] = []
    if lower == "list":
        features.append("entry_listing")
        if {"refid", "anchor"} <= colset:
            features.append("anchor_to_content_reference")
        if "title" in colset:
            features.append("display_title")
        if "titlesub" in colset:
            features.append("display_subtitle_titlesub")
        if "subtext" in colset:
            features.append("display_subtext")
    elif lower == "content":
        features.append("entry_body_store")
        if "body" in colset:
            features.append("html_or_text_body")
        if "media" in colset:
            features.append("entry_media_reference_field")
    elif lower == "media":
        features.append("binary_media_store")
        if "os" in colset:
            features.append("os_specific_media_rows")
    elif lower == "info":
        features.append("auxiliary_info_pages")
    elif lower == "exam":
        features.append("example_sentence_lookup")
    elif lower == "genre":
        features.append("genre_or_filter_metadata")
    elif lower == "search":
        features.append("native_fts_search")
        for search_column in ("forward", "back", "part", "fts", "advanced", "filter"):
            if search_column in colset:
                features.append(f"search_column_{search_column}")
    elif lower.startswith("search_"):
        features.append("native_fts_shadow_table")
    return features


def inspect_lved_sqlite_database(path: Path, *, sample_rows: int = 1024) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        pragma: dict[str, Any] = {}
        for key in ("page_size", "page_count", "encoding", "user_version", "application_id", "schema_version"):
            try:
                pragma[key] = con.execute(f"pragma {key}").fetchone()[0]
            except sqlite3.Error:
                pass
        objects: list[dict[str, Any]] = []
        feature_counts: Counter[str] = Counter()
        role_counts: Counter[str] = Counter()
        for row in con.execute(
            "select type, name, tbl_name, sql from sqlite_master "
            "where name not like 'sqlite_%' order by type, name"
        ):
            object_type = str(row["type"])
            name = str(row["name"])
            sql = row["sql"]
            role = _sqlite_table_role(name, sql if isinstance(sql, str) else None)
            item: dict[str, Any] = {
                "type": object_type,
                "name": name,
                "tbl_name": row["tbl_name"],
                "role": role,
            }
            role_counts[role] += 1
            if isinstance(sql, str):
                sql_lower = sql.casefold()
                item["virtual"] = "create virtual table" in sql_lower
                if "using fts" in sql_lower:
                    item["virtual_module"] = "fts"
            if object_type == "table":
                item["row_count"] = _safe_count(con, name)
                column_rows = _safe_pragma_rows(con, "table_info", name)
                columns: list[str] = [str(column[1]) for column in column_rows]
                item["columns"] = [
                    {
                        "cid": int(column[0]),
                        "name": str(column[1]),
                        "declared_type": str(column[2] or ""),
                        "notnull": bool(column[3]),
                        "pk": int(column[5] or 0),
                    }
                    for column in column_rows
                ]
                features = _sqlite_table_features(name, columns)
                if features:
                    item["features"] = features
                    feature_counts.update(features)
                if name.casefold() in {"list", "content", "media", "info", "exam", "genre", "search"}:
                    item["column_profiles"] = [
                        _sqlite_column_profile(
                            con,
                            name,
                            str(column[1]),
                            str(column[2] or ""),
                            sample_rows=sample_rows,
                        )
                        for column in column_rows
                    ]
                distributions: dict[str, list[dict[str, Any]]] = {}
                for distribution_column in ("type", "os"):
                    if distribution_column in {column.casefold() for column in columns}:
                        source_column = next(column for column in columns if column.casefold() == distribution_column)
                        distribution = _sqlite_value_distribution(con, name, source_column)
                        if distribution:
                            distributions[source_column] = distribution
                if distributions:
                    item["value_distributions"] = distributions
                indexes = _safe_pragma_rows(con, "index_list", name)
                if indexes:
                    item["indexes"] = [
                        {
                            "name": str(index[1]),
                            "unique": bool(index[2]),
                            "origin": str(index[3]) if len(index) > 3 else "",
                            "partial": bool(index[4]) if len(index) > 4 else False,
                        }
                        for index in indexes
                    ]
            objects.append(item)
        return {
            "preferred_family_name": LVED_SQLITE3_FAMILY_NAME,
            "sqlite_pragmas": pragma,
            "object_count": len(objects),
            "role_counts": dict(sorted(role_counts.items())),
            "feature_counts": dict(sorted(feature_counts.items())),
            "objects": objects,
        }
    finally:
        con.close()


def _package_layout_features(payload_path: Path) -> dict[str, Any]:
    root = payload_path.parent
    resource_dirs = []
    retained_ssed = []
    tree_indexes = []
    extension_counts: Counter[str] = Counter()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        rel = path.relative_to(root).as_posix()
        if ":zone.identifier" in rel.casefold():
            continue
        if path.is_dir():
            if path.name.casefold() in {"res", "resources", "templates", "img", "images", "sound", "media"}:
                resource_dirs.append(rel)
            continue
        extension = path.suffix.casefold() or "<none>"
        extension_counts[extension] += 1
        name = path.name.casefold()
        if name.endswith((".dic", ".idx", ".uni")) or name in {"ga16half", "ga16full"}:
            retained_ssed.append(rel)
        if name == "tree.idx":
            tree_indexes.append(rel)
    return {
        "resource_dirs": resource_dirs,
        "retained_ssed_like_files": retained_ssed,
        "tree_idx_files": tree_indexes,
        "extension_counts": dict(sorted(extension_counts.items())),
    }


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
    payload: tuple[Path, int | None, str | None, str | None, list[str], bool]
) -> dict[str, Any]:
    path, dict_id, dict_code, explicit_key, memory_keys, inspect_sqlite = payload
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
        "package_layout": _package_layout_features(path),
        "validation": [],
    }
    validation_keys: list[tuple[str, str]] = []
    if explicit_key:
        validation_keys.append(("explicit_key", explicit_key))
    key_file = discover_lved_key_file(path)
    if key_file is not None:
        row["key_file"] = {
            "detected": True,
            "name": key_file.path.name,
            "match": key_file.match,
        }
        validation_keys.append(("local_key_file", read_lved_key_file(key_file.path)))
    else:
        row["key_file"] = {"detected": False}
    if dict_id is not None:
        code = dict_code or inspected.inferred_dict_code
        if code:
            validation_keys.append(("derived_metadata", derive_lved_sqlcipher_key(dict_id, code)))
    for candidate in memory_keys:
        validation_keys.append(("memory_dump_candidate", candidate))
    first_valid_key: str | None = None
    for source, candidate in validation_keys:
        result = validate_lved_sqlcipher4(path, candidate)
        if first_valid_key is None and result.get("valid"):
            first_valid_key = candidate
        safe_result = {key: value for key, value in result.items() if key != "reason" or value}
        safe_result["source"] = source
        row["validation"].append(safe_result)
    if inspect_sqlite:
        if first_valid_key is None:
            row["sqlite"] = {
                "status": "not_inspected",
                "reason": "no valid SQLCipher key candidate",
            }
        else:
            with TemporaryDirectory() as temp_dir:
                decrypted = Path(temp_dir) / f"{path.name}.sqlite"
                try:
                    decrypt_lved_sqlcipher4_to_path(path, decrypted, first_valid_key)
                    sqlite_report = inspect_lved_sqlite_database(decrypted)
                    sqlite_report["status"] = "inspected"
                    row["sqlite"] = sqlite_report
                except Exception as exc:
                    row["sqlite"] = {
                        "status": "inspect_error",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
    return row


def inspect_lved_roots(
    roots: Iterable[Path],
    *,
    dict_id: int | None = None,
    dict_code: str | None = None,
    key: str | None = None,
    memory_dump: Path | None = None,
    inspect_sqlite: bool = False,
    jobs: int | None = 1,
) -> dict[str, Any]:
    payload_paths = discover_lved_payloads(roots)
    explicit_key = key
    memory_keys = find_lved_key_candidates_in_dump(memory_dump) if memory_dump else []
    payloads = parallel_map_ordered(
        _inspect_lved_payload_task,
        [(path, dict_id, dict_code, explicit_key, memory_keys, inspect_sqlite) for path in payload_paths],
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
            "The LVED_SQLITE3 SQLCipher key derivation uses dictionary id/code metadata, not the product serial.",
            "LVED_SQLITE3 is the preferred reader-facing name for the decrypted SQLite3 data model; lved remains the historical command shorthand.",
        ],
    }
