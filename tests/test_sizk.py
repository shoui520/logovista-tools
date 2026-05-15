from argparse import Namespace
from pathlib import Path

from logovista_tools.decoded_model import detect_platform_wrapper_for_idx, dump_package_model_for_path
from logovista_tools.gaiji import load_gaiji_profile
from logovista_tools.sizk import discover_sizk_packages, inspect_sizk_package


def simple_uni_record(code: int, primary: tuple[int, int]) -> bytes:
    values = [code, 0, primary[0], primary[1], 0, 0]
    return b"".join(value.to_bytes(2, "big") for value in values)


def jis_text(text: str) -> bytes:
    encoded = text.encode("iso2022_jp")
    return encoded.replace(b"\x1b$B", b"").replace(b"\x1b(B", b"")


def make_ssedinfo(path: Path, title: str = "NHK 文学のしずく") -> None:
    data = bytearray(0x80 + 0x30)
    data[:8] = b"SSEDINFO"
    title_bytes = title.encode("cp932")
    data[12] = len(title_bytes)
    data[13 : 13 + len(title_bytes)] = title_bytes
    data[0x4D] = 1
    rec = bytearray(0x30)
    rec[2] = 0
    rec[3] = 0x00
    rec[4:8] = (2).to_bytes(4, "big")
    rec[8:12] = (2).to_bytes(4, "big")
    rec[0x10] = len("HONMON.DIC")
    rec[0x11 : 0x11 + len("HONMON.DIC")] = b"HONMON.DIC"
    data[0x80 : 0x80 + 0x30] = rec
    path.write_bytes(bytes(data))


def make_sseddata(payload: bytes) -> bytes:
    expanded = payload + bytes(2048 - len(payload))
    header = bytearray(64)
    header[:8] = b"SSEDDATA"
    header[0x16:0x18] = (1).to_bytes(2, "big")
    header[0x18:0x1C] = (2).to_bytes(4, "big")
    header[0x1C:0x20] = (2).to_bytes(4, "big")
    chunk_offset = 68
    data = bytearray(header)
    data.extend(chunk_offset.to_bytes(4, "big"))
    data.extend(b"\x00\x00")
    data.extend(len(expanded).to_bytes(2, "big"))
    data.append(0)
    for value in expanded:
        data.extend(bytes((0, 0, value)))
    return bytes(data)


def make_sizk_package(tmp_path: Path) -> Path:
    package = tmp_path / "_DCT_SIZK0101"
    (package / "HTMLs").mkdir(parents=True)
    (package / "Templates").mkdir()
    (package / "EXINFO.INI").write_text(
        "[GENERAL]\n"
        "SRCINFO=NHK 文学のしずく\n"
        "HTML=1\n"
        "HTMLDLL=HC0190.dll\n"
        "MP3NAME=shizuku.mp3\n"
        "GAIJI=shizuku.uni\n",
        encoding="cp932",
    )
    make_ssedinfo(package / "SIZK0101.IDX")
    entry = (
        bytes.fromhex("1f090001")
        + bytes.fromhex("b121")
        + bytes.fromhex("1f090004")
        + jis_text("作品")
        + bytes.fromhex("1f0a1f090031")
        + bytes.fromhex("1f04")
        + jis_text("ｓｈｉｚｕｋｕ．ｍｐ３")
        + bytes.fromhex("1f05")
    )
    (package / "HONMON.DIC").write_bytes(make_sseddata(entry))
    (package / "shizuku.uni").write_bytes(
        (0).to_bytes(4, "big")
        + (1).to_bytes(4, "big")
        + simple_uni_record(0xB12A, (0, 0x9DD7))
    )
    (package / "HTMLs" / "b121.html").write_text("<!--&IND0004;-->", encoding="cp932")
    (package / "Templates" / "honbun.html").write_text(
        '<html><body><div class="honbun" id="1000">本文一</div></body></html>',
        encoding="utf-16le",
    )
    (package / "shizuku_honbun.txt").write_text("本文一\n", encoding="utf-16le")
    (package / "shizuku_time.txt").write_text("1000\n", encoding="utf-16le")
    (package / "shizuku.mp3").write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00")
    return package


def test_load_gaiji_profile_uses_exinfo_declared_uni(tmp_path) -> None:
    package = make_sizk_package(tmp_path)

    profile = load_gaiji_profile(package / "SIZK0101.IDX")

    assert profile.map["b12a"] == "鷗"
    assert profile.uni_entries == 1
    assert profile.uni_paths == (package / "shizuku.uni",)


def test_detect_platformless_core_ssed_wrapper(tmp_path) -> None:
    package = tmp_path / "CORE"
    package.mkdir()
    idx = package / "CORE.IDX"
    make_ssedinfo(idx, title="Core")

    wrapper = detect_platform_wrapper_for_idx(idx)

    assert wrapper["package_family"] == "ssed"
    assert wrapper["platform"] == "noplatform"
    assert wrapper["markers"]["numeric_aux_indexes"] is False


def test_inspect_sizk_package_links_honmon_templates_and_playback(tmp_path) -> None:
    package = make_sizk_package(tmp_path)

    report = inspect_sizk_package(package, include_playback_rows=True)

    assert report["classification"]["is_sizk"]
    assert report["classification"]["html_renderer"] == "HC0190.dll"
    assert report["declared_gaiji"]["format"] == "simple12"
    assert report["honmon"]["entry_markers"] == 1
    assert report["honmon"]["entries"][0]["template_code"] == "b121"
    assert report["honmon"]["entries"][0]["template_role"] == "overview"
    assert report["honmon"]["entries"][0]["heading"] == "作品"
    assert report["honmon"]["entries"][0]["references"]["audio_file"] == "shizuku.mp3"
    assert report["playback"]["synchronized"]
    assert report["playback"]["rows"][0]["time_ms"] == 1000
    assert discover_sizk_packages([tmp_path]) == [package.resolve()]


def test_dump_package_model_embeds_sizk_family(tmp_path) -> None:
    package = make_sizk_package(tmp_path)
    args = Namespace(
        dict=None,
        parse_mode="forensic",
        entry_limit=2,
        profile_max_slices=2,
        title_limit=10,
        index_limit=10,
        menu_limit=10,
        media_limit=10,
        sample_limit=5,
        sidecar_sample_limit=5,
        max_issue_samples=10,
        include_spans=True,
        include_raw=False,
        include_padding_spans=False,
        include_internal_indexes=False,
        deep_sidecars=False,
        include_playback_rows=False,
        no_hash=True,
    )

    model = dump_package_model_for_path(package, args)

    assert model["schema"] == "logovista-decoded-model-v0"
    assert model["classification"]["package_family"] == "ssed"
    assert model["classification"]["platform"] == "windows"
    assert model["wrapper"]["markers"]["sizk"] is True
    assert model["readiness"]["schema"] == "logovista-model-readiness-v0"
    assert model["writer_readiness"] == model["readiness"]["writer_readiness"]
    assert model["entry_spans"]["entries_emitted"] == 1
    assert model["families"]["sizk"]["playback"]["row_count"] == 1
    assert model["gaiji"]["profile"]["uni_entries"] == 1
    assert model["resources"]["static_sidecars"]["file_count"] >= 2
    assert "HTMLs" in model["resources"]["static_sidecars"]["directories"]
