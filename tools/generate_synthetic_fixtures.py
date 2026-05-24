#!/usr/bin/env python3
"""Generate committed synthetic LogoVista/SSED fixtures.

The fixtures are intentionally tiny and deterministic. They are not corpus
evidence; they are stable regression inputs for known format structures.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from logovista_tools.writer import (
    BLOCK_SIZE,
    BodyControl,
    BodyMarkup,
    BodyMedia,
    ColscrMediaAllocator,
    GaijiAllocator,
    IndexPointer,
    IndexTarget,
    SsedInfoComponent,
    WriterEntry,
    WriterMenuItem,
    be16,
    be32,
    build_colscr_records,
    build_plain_honmon_package,
    default_catalog_data,
    encode_bcd_decimal,
    encode_body_text,
    encode_ccaltstr_table,
    encode_ga16_resource,
    encode_honmon_stream,
    encode_keyword_index_pages,
    encode_menu_stream,
    encode_panel_bin,
    encode_search_key,
    encode_simple_index_pages,
    encode_sseddata,
    encode_sseddata_literal,
    encode_ssedinfo,
    encode_tagged_index_pages,
    encode_title_stream,
    encode_uni_resource,
    logical_block_count,
    rows_to_ga16_glyph,
    write_plain_package,
)

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"


@dataclass(frozen=True)
class Component:
    name: str
    component_type: int
    expanded: bytes
    start: int
    kind: int | None = None
    literal: bool = True
    catalog_data: bytes | None = None

    @property
    def end(self) -> int:
        return self.start + logical_block_count(self.expanded) - 1

    def encoded(self) -> bytes:
        kind = self.component_type if self.kind is None else self.kind
        if self.literal:
            return encode_sseddata_literal(self.expanded, start_block=self.start, kind=kind)
        return encode_sseddata(self.expanded, start_block=self.start, kind=kind, jobs=1)

    def catalog(self) -> SsedInfoComponent:
        data = self.catalog_data
        if data is None:
            data = default_catalog_data(self.component_type, self.name)
        return SsedInfoComponent(self.name, self.component_type, self.start, self.end, data)


def reset() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)


def write_manifest(path: Path, *, fixture: str, features: Iterable[str], files: Iterable[str], notes: str = "") -> None:
    data = {
        "schema": "logovista.synthetic_fixture.v1",
        "fixture": fixture,
        "features": sorted(features),
        "files": sorted(files),
    }
    if notes:
        data["notes"] = notes
    (path / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fixture_files(path: Path) -> list[str]:
    return sorted(
        item.relative_to(path).as_posix()
        for item in path.rglob("*")
        if item.is_file() and item.name != "manifest.json"
    )


def refresh_manifest(path: Path, *, features: Iterable[str], notes: str = "") -> None:
    write_manifest(path, fixture=path.name, features=features, files=fixture_files(path), notes=notes)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_package(path: Path, *, dict_id: str, title: str, components: list[Component], extras: dict[str, bytes] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{dict_id}.IDX").write_bytes(encode_ssedinfo(title, [component.catalog() for component in components]))
    for component in components:
        (path / component.name).parent.mkdir(parents=True, exist_ok=True)
        (path / component.name).write_bytes(component.encoded())
    for name, data in (extras or {}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_bytes(data)
    write_manifest(path, fixture=path.name, features=[component.name for component in components], files=[f"{dict_id}.IDX", *(component.name for component in components), *((extras or {}).keys())])


def simple_entries() -> list[WriterEntry]:
    return [
        WriterEntry("alpha", "first body", ("alpha", "ALPHA")),
        WriterEntry("beta", "second body", ("beta",)),
    ]


def write_plain_fixture(path: Path, *, dict_id: str, entries: list[WriterEntry] | None = None, tagged: bool = False, compression: str = "literal") -> None:
    package = build_plain_honmon_package(
        dict_id=dict_id,
        title=f"Synthetic {dict_id}",
        entries=entries or simple_entries(),
        include_tagged_indexes=tagged,
        compression=compression,
        compression_jobs=1,
    )
    write_plain_package(package, path)
    write_manifest(path, fixture=path.name, features=["plain_honmon", "title_index"], files=package.files)


def core_streams(entries: list[WriterEntry]) -> tuple[GaijiAllocator, bytes, list[int], bytes, list[int]]:
    gaiji = GaijiAllocator()
    honmon, body_offsets = encode_honmon_stream(entries, gaiji)
    title, title_offsets = encode_title_stream([entry.headword for entry in entries], gaiji)
    return gaiji, honmon, body_offsets, title, title_offsets


def package_with_indexes(
    path: Path,
    *,
    dict_id: str,
    entries: list[WriterEntry] | None = None,
    four_way: bool = False,
    kw: bool = False,
    cr: bool = False,
    multi: bool = False,
    menu: bool = False,
    toc: bool = False,
    lower_files: bool = False,
) -> None:
    entry_list = entries or simple_entries()
    gaiji, honmon, body_offsets, title, title_offsets = core_streams(entry_list)
    cursor = 2
    honmon_start = cursor
    cursor += logical_block_count(honmon)
    fhtitle_start = cursor
    cursor += logical_block_count(title)
    bhtitle_start = cursor
    cursor += logical_block_count(title)
    fktitle_start = cursor
    bktitle_start = cursor
    if four_way:
        fktitle_start = cursor
        cursor += logical_block_count(title)
        bktitle_start = cursor
        cursor += logical_block_count(title)
    body_ptrs = [IndexPointer(honmon_start, offset) for offset in body_offsets]
    fh_targets = [IndexTarget(entry.keys[0], body_ptrs[i], IndexPointer(fhtitle_start, title_offsets[i]), entry.headword) for i, entry in enumerate(entry_list)]
    bh_targets = [IndexTarget(entry.keys[0], body_ptrs[i], IndexPointer(bhtitle_start, title_offsets[i]), entry.headword) for i, entry in enumerate(entry_list)]
    fhindex_start = cursor
    fhindex = encode_simple_index_pages(fh_targets, start_block=fhindex_start, gaiji=gaiji)
    cursor += logical_block_count(fhindex)
    bhindex_start = cursor
    bhindex = encode_simple_index_pages(bh_targets, start_block=bhindex_start, gaiji=gaiji, reverse_keys=True)
    cursor += logical_block_count(bhindex)
    components = [
        Component("HONMON.DIC", 0x00, honmon, honmon_start),
        Component("FHTITLE.DIC", 0x05, title, fhtitle_start),
        Component("BHTITLE.DIC", 0x07, title, bhtitle_start),
        Component("FHINDEX.DIC", 0x91, fhindex, fhindex_start),
        Component("BHINDEX.DIC", 0x71, bhindex, bhindex_start),
    ]
    if four_way:
        fk_targets = [IndexTarget(entry.keys[0], body_ptrs[i], IndexPointer(fktitle_start, title_offsets[i]), entry.headword) for i, entry in enumerate(entry_list)]
        bk_targets = [IndexTarget(entry.keys[0], body_ptrs[i], IndexPointer(bktitle_start, title_offsets[i]), entry.headword) for i, entry in enumerate(entry_list)]
        fkindex_start = cursor
        fkindex = encode_tagged_index_pages(fk_targets, start_block=fkindex_start, gaiji=gaiji)
        cursor += logical_block_count(fkindex)
        bkindex_start = cursor
        bkindex = encode_tagged_index_pages(bk_targets, start_block=bkindex_start, gaiji=gaiji, reverse_keys=True)
        cursor += logical_block_count(bkindex)
        components.extend(
            [
                Component("FKTITLE.DIC", 0x04, title, fktitle_start),
                Component("BKTITLE.DIC", 0x06, title, bktitle_start),
                Component("FKINDEX.DIC", 0x90, fkindex, fkindex_start),
                Component("BKINDEX.DIC", 0x70, bkindex, bkindex_start),
            ]
        )
    if kw:
        kwtitle_start = cursor
        kwtitle, kw_offsets = encode_title_stream(["keyword alpha"], gaiji)
        cursor += logical_block_count(kwtitle)
        kwindex_start = cursor
        kwindex = encode_keyword_index_pages([IndexTarget("keyword", body_ptrs[0], IndexPointer(kwtitle_start, kw_offsets[0]))], start_block=kwindex_start, gaiji=gaiji)
        cursor += logical_block_count(kwindex)
        components.extend([Component("KWTITLE.DIC", 0x03, kwtitle, kwtitle_start), Component("KWINDEX.DIC", 0x80, kwindex, kwindex_start)])
    if cr:
        crtitle_start = cursor
        crtitle, cr_offsets = encode_title_stream(["cross alpha"], gaiji)
        cursor += logical_block_count(crtitle)
        crindex_start = cursor
        crindex = leaf_page([direct_row("cross", body_ptrs[0], IndexPointer(crtitle_start, cr_offsets[0]))])
        cursor += logical_block_count(crindex)
        components.extend([Component("CRTITLE.DIC", 0x0A, crtitle, crtitle_start), Component("CRINDEX.DIC", 0x81, crindex, crindex_start)])
    if multi:
        multitle_start = cursor
        multitle, multi_offsets = encode_title_stream(["selector alpha"], gaiji)
        cursor += logical_block_count(multitle)
        multiindex_start = cursor
        multiindex = leaf_page([direct_row("selector", body_ptrs[0], IndexPointer(multitle_start, multi_offsets[0]))])
        cursor += logical_block_count(multiindex)
        components.extend([Component("MUL1_1_1.DIC", 0x0D, multitle, multitle_start), Component("MUL1_1_2.DIC", 0xA1, multiindex, multiindex_start)])
    if menu:
        menu_start = cursor
        menu_stream = encode_menu_stream([WriterMenuItem("body target", body_ptrs[0])], gaiji)
        cursor += logical_block_count(menu_stream)
        components.append(Component("MENU.DIC", 0x01, menu_stream, menu_start))
    if toc:
        toc_start = cursor
        payload = b"\x00\x01\x02\x03" + be32(body_ptrs[0].block) + be16(body_ptrs[0].offset)
        toc_stream = b"\x1f\x09\x00\x01\x1f\x49" + payload + encode_body_text("TOC target", gaiji) + b"\x1f\x69\x1f\x0a"
        cursor += logical_block_count(toc_stream)
        components.append(Component("TOC.DIC", 0x20, toc_stream, toc_start))
    if lower_files:
        lower_components = []
        renamed = {
            "HONMON.DIC": "honmon.dic",
            "FHINDEX.DIC": "fhindex.dic",
            "FHTITLE.DIC": "fhtitle.dic",
        }
        for component in components:
            lower_components.append(Component(renamed.get(component.name, component.name), component.component_type, component.expanded, component.start, component.kind, component.literal, component.catalog_data))
        components = lower_components
    write_package(path, dict_id=dict_id, title=f"Synthetic {dict_id}", components=components)


def leaf_page(records: list[bytes]) -> bytes:
    data = be16(0xD000) + be16(len(records)) + b"".join(records)
    return data + bytes(BLOCK_SIZE - len(data))


def direct_row(key: str, body: IndexPointer, title: IndexPointer) -> bytes:
    key_bytes = encode_search_key(key)
    return b"\x00" + bytes([len(key_bytes)]) + key_bytes + be32(body.block) + be16(body.offset) + be32(title.block) + be16(title.offset)


def media_payload_png() -> bytes:
    # 1x1 transparent PNG.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63600000020001e221bc330000000049454e44ae426082"
    )


def media_payload_bmp() -> bytes:
    return (
        b"BM"
        + (58).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (54).to_bytes(4, "little")
        + (40).to_bytes(4, "little")
        + (1).to_bytes(4, "little", signed=True)
        + (1).to_bytes(4, "little", signed=True)
        + (1).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (4).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + b"\xff\x00\x00\x00"
    )


def riffless_wave_record() -> bytes:
    fmt = (
        b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (8000).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (8).to_bytes(2, "little")
    )
    data = b"data" + (4).to_bytes(4, "little") + b"\x80\x80\x80\x80"
    return fmt + data + bytes(12)


def fixture_catalog_basic() -> None:
    write_plain_fixture(ROOT / "catalog_basic", dict_id="BASIC", tagged=False)


def fixture_catalog_inactive_missing_component() -> None:
    path = ROOT / "catalog_inactive_missing_component"
    honmon = encode_body_text("inactive fixture") + b"\x1f\x0a"
    component = Component("HONMON.DIC", 0x00, honmon, 2)
    path.mkdir(parents=True)
    (path / "INACTIVE.IDX").write_bytes(
        encode_ssedinfo(
            "Inactive Missing",
            [
                component.catalog(),
                SsedInfoComponent("MISSING.DIC", 0x20, 0, 0, b"\x00\x00\x00\x00"),
            ],
        )
    )
    (path / "HONMON.DIC").write_bytes(component.encoded())
    write_manifest(path, fixture=path.name, features=["inactive_catalog_component", "missing_component"], files=["INACTIVE.IDX", "HONMON.DIC"])


def fixture_sseddata_chunk_roundtrip() -> None:
    path = ROOT / "sseddata_chunk_roundtrip"
    path.mkdir(parents=True)
    payload = (b"chunkable payload " * 4000) + bytes(range(256)) * 16
    (path / "SAMPLE.DIC").write_bytes(encode_sseddata(payload, start_block=11, kind=0x91, jobs=1))
    (path / "expanded.bin").write_bytes(payload)
    write_manifest(path, fixture=path.name, features=["compressed_sseddata", "chunk_roundtrip"], files=["SAMPLE.DIC", "expanded.bin"])


def fixture_case_insensitive_component_lookup() -> None:
    package_with_indexes(ROOT / "case_insensitive_component_lookup", dict_id="CASE", lower_files=True)


def fixture_basic_bidirectional_title_index() -> None:
    package_with_indexes(ROOT / "basic_bidirectional_title_index", dict_id="BIDIR")


def fixture_standard_four_title_index() -> None:
    package_with_indexes(ROOT / "standard_four_title_index", dict_id="FOUR", four_way=True)


def fixture_kw_cr_title_index() -> None:
    package_with_indexes(ROOT / "kw_cr_title_index", dict_id="KWCR", kw=True, cr=True)


def fixture_multi_selector_components() -> None:
    package_with_indexes(ROOT / "multi_selector_components", dict_id="MULTI", multi=True)


def fixture_honmon_common_controls() -> None:
    entries = [
        WriterEntry(
            "controls",
            BodyMarkup(
                (
                    "fullwidth ",
                    BodyControl(b"\x1f\x04"),
                    "ABC",
                    BodyControl(b"\x1f\x05"),
                    " ruby ",
                    BodyControl(bytes.fromhex("1f100004")),
                    "over",
                    BodyControl(b"\x1f\x30"),
                    "base",
                    BodyControl(b"\x1f\x70"),
                )
            ),
        )
    ]
    write_plain_fixture(ROOT / "honmon_common_controls", dict_id="CTRL", entries=entries)


def fixture_honmon_entry_marker_variants() -> None:
    path = ROOT / "honmon_entry_marker_variants"
    body = b"\x1f\x09\x00\x08" + encode_body_text("section eight") + b"\x1f\x0a" + b"\x1f\x09\x10\x01" + encode_body_text("section 1001") + b"\x1f\x0a"
    write_package(path, dict_id="MARKER", title="Marker Variants", components=[Component("HONMON.DIC", 0x00, body, 2)])


def fixture_honmon_index_boundary_slicing() -> None:
    entries = [
        WriterEntry("parent", BodyMarkup(("parent body", BodyControl(b"\x1f\x09\x00\x02"), "child section"))),
        WriterEntry("next", "next body"),
    ]
    package_with_indexes(ROOT / "honmon_index_boundary_slicing", dict_id="BOUND", entries=entries)


def fixture_honmon_links() -> None:
    entries = [
        WriterEntry("target", "linked target"),
        WriterEntry("source", BodyMarkup(("see ", BodyControl(b"\x1f\x42" + bytes(14)), "target", BodyControl(b"\x1f\x62")))),
    ]
    write_plain_fixture(ROOT / "honmon_links", dict_id="LINKS", entries=entries)


def fixture_title_pointer_resolution() -> None:
    package_with_indexes(ROOT / "title_pointer_resolution", dict_id="TITLEPTR")


def fixture_simple_index_leaf() -> None:
    package_with_indexes(ROOT / "simple_index_leaf", dict_id="SIMPLE")


def fixture_tagged_index_leaf() -> None:
    package_with_indexes(ROOT / "tagged_index_leaf", dict_id="TAGGED", four_way=True)


def fixture_normalized_index_keys() -> None:
    entries = [WriterEntry("mixed case", "body", ("mixed case", "Ｍｉｘｅｄ Ｃａｓｅ", "lower-case"))]
    package_with_indexes(ROOT / "normalized_index_keys", dict_id="NORM", entries=entries, four_way=True)


def fixture_menu_body_targets() -> None:
    package_with_indexes(ROOT / "menu_body_targets", dict_id="MENUBODY", menu=True)


def fixture_menu_menu_targets() -> None:
    path = ROOT / "menu_menu_targets"
    entries = simple_entries()
    gaiji, honmon, body_offsets, title, title_offsets = core_streams(entries)
    honmon_start = 2
    menu_start = 3
    menu_stream = encode_menu_stream([WriterMenuItem("submenu", IndexPointer(menu_start, 0))], gaiji)
    write_package(
        path,
        dict_id="MENUMENU",
        title="Menu Menu",
        components=[Component("HONMON.DIC", 0x00, honmon, honmon_start), Component("MENU.DIC", 0x01, menu_stream, menu_start)],
    )


def fixture_toc_tree_targets() -> None:
    package_with_indexes(ROOT / "toc_tree_targets", dict_id="TOCTREE", toc=True)


def fixture_uni_gaiji_mapping() -> None:
    path = ROOT / "uni_gaiji_mapping"
    gaiji = GaijiAllocator(glyph_renderer=lambda _text, width, height, _space: rows_to_ga16_glyph(["#" * width] * height, width=width, height=height))
    gaiji.allocate("Ω", prefer_half=True)
    gaiji.allocate("𰻞", prefer_half=False)
    path.mkdir(parents=True)
    (path / "GAIJI.uni").write_bytes(encode_uni_resource(gaiji))
    write_manifest(path, fixture=path.name, features=["uni_gaiji_mapping"], files=["GAIJI.uni"])


def fixture_ga16_half_full() -> None:
    path = ROOT / "ga16_half_full"
    gaiji = GaijiAllocator(glyph_renderer=lambda _text, width, height, _space: rows_to_ga16_glyph(["#" * width] * height, width=width, height=height))
    half = gaiji.allocate("Ω", prefer_half=True)
    full = gaiji.allocate("𰻞", prefer_half=False)
    path.mkdir(parents=True)
    (path / "GA16HALF").write_bytes(encode_ga16_resource([half], width=8, start_code=half.code))
    (path / "GA16FULL").write_bytes(encode_ga16_resource([full], width=16, start_code=full.code))
    write_manifest(path, fixture=path.name, features=["ga16_half", "ga16_full"], files=["GA16HALF", "GA16FULL"])


def fixture_gai16_name_variants() -> None:
    path = ROOT / "gai16_name_variants"
    gaiji = GaijiAllocator(glyph_renderer=lambda _text, width, height, _space: rows_to_ga16_glyph(["#" * width] * height, width=width, height=height))
    half = gaiji.allocate("A", prefer_half=True)
    full = gaiji.allocate("漢", prefer_half=False)
    path.mkdir(parents=True)
    (path / "GAI16H00").write_bytes(encode_ga16_resource([half], width=8, start_code=half.code))
    (path / "GAI16F00").write_bytes(encode_ga16_resource([full], width=16, start_code=full.code))
    write_manifest(path, fixture=path.name, features=["gai16_name_variants"], files=["GAI16H00", "GAI16F00"])


def fixture_template_gaiji_fallback() -> None:
    path = ROOT / "template_gaiji_fallback" / "Templates"
    path.mkdir(parents=True)
    (path / "b121.png").write_bytes(media_payload_png())
    (path / "b121.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><text x="0" y="14">Ω</text></svg>\n', encoding="utf-8")
    write_manifest(path.parent, fixture=path.parent.name, features=["template_gaiji_fallback"], files=["Templates/b121.png", "Templates/b121.svg"])


def fixture_nonliteral_gaiji_marker() -> None:
    path = ROOT / "nonliteral_gaiji_marker"
    entries = [WriterEntry("marker", BodyMarkup(("before", BodyControl(bytes.fromhex("b162")), "after")))]
    package = build_plain_honmon_package(dict_id="NONGAIJI", title="Nonliteral Gaiji Marker", entries=entries, compression="literal")
    write_plain_package(package, path)
    write_manifest(path, fixture=path.name, features=["nonliteral_gaiji_marker"], files=package.files)


def fixture_colscr_image_records() -> None:
    path = ROOT / "colscr_image_records"
    media = ColscrMediaAllocator()
    assignment = media.allocate(BodyMedia(resource_key="one-pixel", payload=media_payload_png(), mime_type="image/png", label="one pixel"))
    expanded, assignments, _ = build_colscr_records(media.assignments, {})
    media.set_layout(start_block=2, assignments=assignments)
    pointer = media.media_control(BodyMedia(resource_key="one-pixel", payload=media_payload_png(), mime_type="image/png", label="one pixel"))
    honmon = b"\x1f\x09\x00\x01" + encode_body_text("image") + b"\x1f\x0a" + pointer + b"\x1f\x0a"
    write_package(path, dict_id="COLSCR", title="COLSCR", components=[Component("HONMON.DIC", 0x00, honmon, 5), Component("COLSCR.DIC", 0xD2, expanded, 2)])


def fixture_pcmdata_audio_records() -> None:
    path = ROOT / "pcmdata_audio_records"
    record = riffless_wave_record()
    expanded = bytearray(2048)
    record_offset = len(expanded)
    expanded.extend(record)
    start_block = 2
    target_block = start_block + record_offset // BLOCK_SIZE
    target_offset = record_offset % BLOCK_SIZE
    end = record_offset + len(record) - 1
    end_block = start_block + end // BLOCK_SIZE
    end_offset = end % BLOCK_SIZE
    payload = b"\x00\x01\x00\x00" + encode_bcd_decimal(target_block, 8) + encode_bcd_decimal(target_offset, 4) + encode_bcd_decimal(end_block, 8) + encode_bcd_decimal(end_offset, 4)
    honmon = b"\x1f\x09\x00\x01" + encode_body_text("audio") + b"\x1f\x0a\x1f\x4a" + payload + encode_body_text("sound") + b"\x1f\x6a\x1f\x0a"
    write_package(path, dict_id="PCM", title="PCMDATA", components=[Component("HONMON.DIC", 0x00, honmon, 5), Component("PCMDATA.DIC", 0xD8, bytes(expanded), start_block)])


def dense_honmon_bytes(ids: Iterable[int]) -> bytes:
    out = bytearray()
    for value in ids:
        out.extend(b"\x1f\x09\x00\x01")
        out.extend(f"{value:06d}".encode("ascii"))
        out.extend(b"\x1f\x0a")
    return bytes(out)


def fixture_dense_honmon_t_contents() -> None:
    path = ROOT / "dense_honmon_t_contents"
    write_package(path, dict_id="DENSETC", title="Dense t_contents", components=[Component("HONMON.DIC", 0x00, dense_honmon_bytes([1, 2]), 2)])
    con = sqlite3.connect(path / "vlpljblF")
    con.execute("create table t_contents (f_DataId integer primary key, f_Title text, f_Html text)")
    con.execute("insert into t_contents values (1, 'alpha', '<div>alpha body</div>')")
    con.commit()
    con.close()
    refresh_manifest(path, features=["dense_honmon_t_contents", "sqlite_sidecar"])


def fixture_dense_honmon_honbun() -> None:
    path = ROOT / "dense_honmon_honbun"
    write_package(path, dict_id="DENSEHB", title="Dense HONBUN", components=[Component("HONMON.DIC", 0x00, dense_honmon_bytes([1, 2]), 2)])
    con = sqlite3.connect(path / "body.db")
    con.execute("create table HONBUN (ID integer primary key, Title_UTF8 text, Contents_HTML_box text)")
    con.execute("insert into HONBUN values (1, 'alpha', '<div>alpha box</div>')")
    con.commit()
    con.close()
    refresh_manifest(path, features=["dense_honmon_honbun", "sqlite_sidecar"])


def fixture_dense_honmon_main_table() -> None:
    path = ROOT / "dense_honmon_main_table"
    write_package(path, dict_id="DENSEMAIN", title="Dense main table", components=[Component("HONMON.DIC", 0x00, dense_honmon_bytes([1, 2]), 2)])
    con = sqlite3.connect(path / "DENSEMAIN")
    con.execute("create table DENSEMAIN (id integer primary key, body text)")
    con.execute("insert into DENSEMAIN values (1, '<p>main table body</p>')")
    con.commit()
    con.close()
    refresh_manifest(path, features=["dense_honmon_main_table", "sqlite_sidecar"])


def fixture_block_offset_rendererdb() -> None:
    path = ROOT / "block_offset_rendererdb"
    write_package(path, dict_id="BLOCKDB", title="Block DB", components=[Component("HONMON.DIC", 0x00, dense_honmon_bytes([1]), 2)])
    con = sqlite3.connect(path / "renderer.db")
    con.execute("create table body (Block integer, Offset integer, Title text, Body text)")
    con.execute("insert into body values (2, 0, 'alpha', '<div>block body</div>')")
    con.commit()
    con.close()
    refresh_manifest(path, features=["block_offset_rendererdb", "sqlite_sidecar"])


def fixture_sidecar_media_blob() -> None:
    path = ROOT / "sidecar_media_blob"
    write_package(path, dict_id="MEDIADB", title="Media DB", components=[Component("HONMON.DIC", 0x00, dense_honmon_bytes([1]), 2)])
    con = sqlite3.connect(path / "media.db")
    con.execute("create table media (name text primary key, data blob, mime text)")
    con.execute("insert into media values ('pixel.png', ?, 'image/png')", (media_payload_png(),))
    con.commit()
    con.close()
    refresh_manifest(path, features=["sidecar_media_blob", "sqlite_sidecar", "media_blob"])


def fixture_ziptomedia_links() -> None:
    path = ROOT / "ziptomedia_links"
    entries = [WriterEntry("zip", '<a href="lved.ziptomedia:sound001">sound</a>')]
    write_plain_fixture(path, dict_id="ZIPMEDIA", entries=entries)
    (path / "ziptomedia").mkdir(exist_ok=True)
    (path / "ziptomedia" / "sound001").write_bytes(b"synthetic encrypted-ish media placeholder")
    refresh_manifest(path, features=["plain_honmon", "title_index", "ziptomedia_links"])


def fixture_panel_xml_cells() -> None:
    path = ROOT / "panel_xml_cells"
    path.mkdir(parents=True)
    write_text(
        path / "Panels.dtd",
        "<!ELEMENT panels (panel*)>\n<!ELEMENT panel (title?,data?)>\n<!ELEMENT title (#PCDATA)>\n<!ELEMENT data (cell*)>\n<!ELEMENT cell (#PCDATA)>\n",
    )
    write_text(path / "Panel.html", "<html><body>Panel</body></html>\n")
    write_text(path / "Cell.html", "<html><body>Cell</body></html>\n")
    write_text(
        path / "Panels.xml",
        """<?xml version="1.0" encoding="utf-8"?>
<panels version="1.0">
  <panel index="01000000" paneltype="menu" count_x="2">
    <title>Kana</title>
    <data><cell action_verb="lved.panel:01010000" ref="01010000">あ</cell></data>
  </panel>
</panels>
""",
    )
    write_manifest(path, fixture=path.name, features=["panel_xml_cells"], files=["Panels.dtd", "Panels.xml", "Panel.html", "Cell.html"])


def fixture_panel_bin_rows() -> None:
    path = ROOT / "panel_bin_rows" / "Panel"
    path.mkdir(parents=True)
    (path / "kana.bin").write_bytes(encode_panel_bin([("あ", IndexPointer(2, 0)), ("い", IndexPointer(2, 20))]))
    write_manifest(path.parent, fixture=path.parent.name, features=["panel_bin_rows"], files=["Panel/kana.bin"])


def fixture_panel_cell_to_entry_address() -> None:
    path = ROOT / "panel_cell_to_entry_address"
    package_with_indexes(path, dict_id="PANELADR")
    write_text(
        path / "Panels.xml",
        """<?xml version="1.0" encoding="utf-8"?>
<panels version="1.0">
  <panel index="04000000" paneltype="menu" count_x="1">
    <data><cell action_verb="lved.addr:00000002:0000">alpha</cell></data>
  </panel>
  <panel index="01010000" paneltype="contents" datatype="external">
    <data type="bin" filename="Panel\\kana.bin" />
  </panel>
</panels>
""",
    )
    (path / "Panel").mkdir(exist_ok=True)
    (path / "Panel" / "kana.bin").write_bytes(encode_panel_bin([("alpha", IndexPointer(2, 0))]))
    refresh_manifest(path, features=["panel_cell_to_entry_address", "panel_xml_cells", "panel_bin_rows", "title_index"])


def fixture_malformed_catalog_offsets() -> None:
    path = ROOT / "malformed_catalog_offsets"
    path.mkdir(parents=True)
    (path / "BAD.IDX").write_bytes(
        encode_ssedinfo(
            "Malformed Catalog",
            [SsedInfoComponent("HONMON.DIC", 0x00, 9, 8, b"\x02\x00\x00\x00")],
        )
    )
    (path / "HONMON.DIC").write_bytes(encode_sseddata_literal(b"bad offsets", start_block=2, kind=0))
    write_manifest(path, fixture=path.name, features=["malformed_catalog_offsets"], files=["BAD.IDX", "HONMON.DIC"])


def fixture_truncated_sseddata_chunk() -> None:
    path = ROOT / "truncated_sseddata_chunk"
    path.mkdir(parents=True)
    good = encode_sseddata(b"truncated payload" * 200, start_block=2, kind=0, jobs=1)
    (path / "TRUNC.DIC").write_bytes(good[:-7])
    write_manifest(path, fixture=path.name, features=["truncated_sseddata_chunk"], files=["TRUNC.DIC"])


def fixture_lossless_repack_representative() -> None:
    path = ROOT / "lossless_repack_representative"
    package_with_indexes(path, dict_id="REPACK", kw=True)
    gaiji = GaijiAllocator(glyph_renderer=lambda _text, width, height, _space: rows_to_ga16_glyph(["#" * width] * height, width=width, height=height))
    half = gaiji.allocate("A", prefer_half=True)
    (path / "REPACK.uni").write_bytes(encode_uni_resource(gaiji))
    (path / "GA16HALF").write_bytes(encode_ga16_resource([half], width=8, start_code=half.code))
    (path / "CCALTSTR.HA").write_bytes(encode_ccaltstr_table([half], kind="half", values={"A": "A"}))
    (path / "Panel").mkdir(exist_ok=True)
    (path / "Panel" / "bunya.bin").write_bytes(encode_panel_bin([("alpha", IndexPointer(2, 0))]))
    con = sqlite3.connect(path / "helper.db")
    con.execute("create table helper(id integer primary key, value text)")
    con.execute("insert into helper(value) values ('x')")
    con.commit()
    con.close()
    (path / "SPINDEX.DIC").write_bytes(encode_sseddata_literal(bytes(2048), start_block=50, kind=0x91))
    (path / "00000041.idx").write_text("00000002\t00000010\t\tauxiliary\n", encoding="cp932")
    write_manifest(
        path,
        fixture=path.name,
        features=["lossless_repack_representative", "catalog", "sseddata", "gaiji", "panel", "sqlite", "aux_idx"],
        files=fixture_files(path),
    )


FIXTURES = [
    fixture_catalog_basic,
    fixture_catalog_inactive_missing_component,
    fixture_sseddata_chunk_roundtrip,
    fixture_case_insensitive_component_lookup,
    fixture_basic_bidirectional_title_index,
    fixture_standard_four_title_index,
    fixture_kw_cr_title_index,
    fixture_multi_selector_components,
    fixture_honmon_common_controls,
    fixture_honmon_entry_marker_variants,
    fixture_honmon_index_boundary_slicing,
    fixture_honmon_links,
    fixture_title_pointer_resolution,
    fixture_simple_index_leaf,
    fixture_tagged_index_leaf,
    fixture_normalized_index_keys,
    fixture_menu_body_targets,
    fixture_menu_menu_targets,
    fixture_toc_tree_targets,
    fixture_uni_gaiji_mapping,
    fixture_ga16_half_full,
    fixture_gai16_name_variants,
    fixture_template_gaiji_fallback,
    fixture_nonliteral_gaiji_marker,
    fixture_colscr_image_records,
    fixture_pcmdata_audio_records,
    fixture_dense_honmon_t_contents,
    fixture_dense_honmon_honbun,
    fixture_dense_honmon_main_table,
    fixture_block_offset_rendererdb,
    fixture_sidecar_media_blob,
    fixture_ziptomedia_links,
    fixture_panel_xml_cells,
    fixture_panel_bin_rows,
    fixture_panel_cell_to_entry_address,
    fixture_malformed_catalog_offsets,
    fixture_truncated_sseddata_chunk,
    fixture_lossless_repack_representative,
]


def write_readme() -> None:
    names = [func.__name__.replace("fixture_", "") for func in FIXTURES]
    text = "# Synthetic LogoVista/SSED Fixtures\n\n"
    text += "These fixtures are generated, deterministic, and committed on purpose. They contain no private corpus text.\n\n"
    text += "Regenerate from the repository root with:\n\n"
    text += "```bash\nPYTHONPATH=src python3 tools/generate_synthetic_fixtures.py\n```\n\n"
    text += "Fixtures:\n\n"
    for name in names:
        text += f"- `{name}`\n"
    (ROOT / "README.md").write_text(text, encoding="utf-8")


def write_root_manifest() -> None:
    rows = []
    for fixture_dir in sorted(path for path in ROOT.iterdir() if path.is_dir()):
        manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
        rows.append({"fixture": fixture_dir.name, "features": manifest["features"], "files": manifest["files"]})
    (ROOT / "manifest.json").write_text(json.dumps({"schema": "logovista.synthetic_fixtures.v1", "fixtures": rows}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    reset()
    for generate in FIXTURES:
        generate()
    write_readme()
    write_root_manifest()
    print(f"generated {len(FIXTURES)} fixtures under {ROOT}")


if __name__ == "__main__":
    main()
