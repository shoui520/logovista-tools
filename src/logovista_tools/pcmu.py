"""KOJIEN6-style loose ``_PCM_U`` encrypted MP3 resource support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lvcrypto import decrypt_logofont_cipher_bytes
from .pcmdata import PcmPointer, PcmRecord, RiffChunk, parse_pcmdata_record
from .ssed import resolve_case_insensitive_path


@dataclass(frozen=True)
class PcmuMapRecord:
    """One ``WaveFile.map`` row from a ``_PCM_U`` directory."""

    stem: str
    start_block: int
    line_index: int
    path: Path
    encrypted_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "stem": self.stem,
            "start_block": self.start_block,
            "line_index": self.line_index,
            "encrypted_bytes": self.encrypted_bytes,
        }


@dataclass(frozen=True)
class PcmuIndex:
    """Resolved ``_PCM_U`` directory and block-address map."""

    directory: Path
    map_path: Path
    rows: tuple[PcmuMapRecord, ...]
    records_by_block: dict[int, PcmuMapRecord]

    def record_for_pointer(self, pointer: PcmPointer) -> PcmuMapRecord | None:
        return self.records_by_block.get(pointer.start_block)


def _candidate_pcmu_dirs(package: Path) -> tuple[Path, ...]:
    names = []
    if package.name:
        names.append(f"{package.name}_PCM_U")
    if package.name.startswith("_DCT_"):
        names.append(f"{package.name[5:]}_PCM_U")
    names.append("_PCM_U")
    candidates = [package / name for name in names]
    candidates.extend(package.parent / name for name in names)
    seen: set[Path] = set()
    resolved: list[Path] = []
    for candidate in candidates:
        key = candidate.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return tuple(resolved)


def find_pcmu_dir(idx: Path) -> Path | None:
    for candidate in _candidate_pcmu_dirs(idx.parent):
        if not candidate.is_dir():
            continue
        map_path = resolve_case_insensitive_path(candidate, "WaveFile.map")
        if map_path is not None and map_path.is_file():
            return candidate
    return None


def parse_pcmu_wavefile_map(directory: Path) -> tuple[PcmuMapRecord, ...]:
    map_path = resolve_case_insensitive_path(directory, "WaveFile.map")
    if map_path is None or not map_path.is_file():
        raise ValueError(f"missing WaveFile.map in {directory}")
    rows: list[PcmuMapRecord] = []
    for line_index, raw_line in enumerate(map_path.read_text(encoding="ascii", errors="replace").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"Malformed _PCM_U WaveFile.map row {line_index}: {raw_line!r}")
        stem, block_text = parts
        path = resolve_case_insensitive_path(directory, stem)
        if path is None or not path.is_file():
            raise ValueError(f"_PCM_U WaveFile.map row {line_index} references missing file {stem!r}")
        rows.append(
            PcmuMapRecord(
                stem=stem,
                start_block=int(block_text, 10),
                line_index=line_index,
                path=path,
                encrypted_bytes=path.stat().st_size,
            )
        )
    return tuple(rows)


def load_pcmu_index(idx: Path) -> PcmuIndex | None:
    directory = find_pcmu_dir(idx)
    if directory is None:
        return None
    map_path = resolve_case_insensitive_path(directory, "WaveFile.map")
    if map_path is None:
        return None
    rows = parse_pcmu_wavefile_map(directory)
    records_by_block: dict[int, PcmuMapRecord] = {}
    for row in rows:
        records_by_block.setdefault(row.start_block, row)
    return PcmuIndex(directory=directory, map_path=map_path, rows=rows, records_by_block=records_by_block)


def read_pcmu_record(index: PcmuIndex, pointer: PcmPointer) -> tuple[PcmuMapRecord, bytes, PcmRecord, list[RiffChunk]] | None:
    map_record = index.record_for_pointer(pointer)
    if map_record is None:
        return None
    decrypted = decrypt_logofont_cipher_bytes(map_record.path.read_bytes())
    parsed = parse_pcmdata_record(decrypted, 0)
    if parsed is None:
        return None
    record, chunks = parsed
    return map_record, decrypted, record, chunks

