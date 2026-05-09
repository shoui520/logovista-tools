from __future__ import annotations

from types import SimpleNamespace

from logovista_tools.decoded_model import (
    dense_anchor_and_body_dereferences,
    index_pointer_dereferences,
    media_dereferences,
    menu_destination_dereferences,
    write_package_model_chunked_to_dir,
)
from logovista_tools.rendererdb import HonmonIdRecord
from logovista_tools.ssed import SsedInfoElement


def elements() -> list[SsedInfoElement]:
    return [
        SsedInfoElement(0, 0, 0x00, 10, 20, b"", "HONMON.DIC"),
        SsedInfoElement(1, 0, 0x90, 30, 32, b"", "FKINDEX.DIC"),
        SsedInfoElement(2, 0, 0x01, 40, 41, b"", "MENU.DIC"),
        SsedInfoElement(3, 0, 0xD2, 50, 55, b"", "COLSCR.DIC"),
        SsedInfoElement(4, 0, 0xD8, 60, 65, b"", "PCMDATA.DIC"),
    ]


def test_index_menu_and_media_dereferences_use_typed_addresses() -> None:
    model = {
        "package": {"dict_id": "DEREF"},
        "indexes": {
            "samples": [
                {
                    "kind": "leaf",
                    "component": "FKINDEX.DIC",
                    "page_index": 0,
                    "logical_block": 30,
                    "row_index": 1,
                    "key": "head",
                    "target_key": "head",
                    "body": {"block": 10, "offset": 2},
                    "title": {"block": 10, "offset": 12},
                }
            ]
        },
        "menus": {
            "samples": [
                {
                    "component": "MENU.DIC",
                    "byte_end": 8,
                    "path": ["root", "item"],
                    "links": [
                        {
                            "label": "item",
                            "end_offset": 8,
                            "destination": {
                                "payload": "000000100002",
                                "encoding": "bcd",
                                "block": 10,
                                "offset": 2,
                                "target": {"kind": "body"},
                            },
                        }
                    ],
                }
            ]
        },
        "media": {
            "colscr": {
                "records": [
                    {
                        "valid": True,
                        "honmon_position": 4,
                        "payload": "00",
                        "block": 50,
                        "offset": 0,
                        "media_type": "png",
                    }
                ]
            },
            "pcmdata": {
                "records": [
                    {
                        "valid": True,
                        "honmon_position": 6,
                        "payload": "00",
                        "block": 60,
                        "offset": 0,
                        "codec": "wav",
                        "label": "audio",
                    }
                ]
            },
        },
    }

    rows = [
        *index_pointer_dereferences(model, elements()),
        *menu_destination_dereferences(model, elements()),
        *media_dereferences(model, elements()),
    ]

    assert {row["kind"] for row in rows} == {"index_pointer", "menu_destination", "media_reference"}
    assert rows[0]["method"] == "index_body_pointer"
    assert rows[0]["from"]["kind"] == "component"
    assert rows[0]["to"]["component"] == "HONMON.DIC"
    assert rows[2]["method"] == "menu_destination"
    assert rows[2]["to"]["component"] == "HONMON.DIC"
    assert rows[3]["to"]["component"] == "COLSCR.DIC"
    assert rows[4]["to"]["component"] == "PCMDATA.DIC"


def test_dense_anchor_dereference_records_are_first_class(monkeypatch) -> None:
    source = SimpleNamespace(
        dict_id="DENSE",
        idx=None,
        honmon="HONMON.DIC",
        honmon_start_block=10,
    )
    record = HonmonIdRecord(
        data_id=123,
        record_index=7,
        record_offset=224,
        block=10,
        offset=224,
        marker_block=10,
        marker_offset=226,
    )

    monkeypatch.setattr("logovista_tools.decoded_model.expand_sseddata_file_with_storage", lambda _path: (b"", {}))
    monkeypatch.setattr("logovista_tools.decoded_model.iter_dense_honmon_id_records", lambda *_args, **_kwargs: [record])
    monkeypatch.setattr("logovista_tools.decoded_model.find_fulldb", lambda _source: None)
    monkeypatch.setattr("logovista_tools.decoded_model.discover_android_body_databases", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("logovista_tools.decoded_model.discover_renderer_sidecars", lambda *_args, **_kwargs: [])

    rows = dense_anchor_and_body_dereferences(source, elements(), SimpleNamespace(sidecar_sample_limit=20, deep_sidecars=False))

    assert len(rows) == 1
    assert rows[0]["kind"] == "dense_honmon_anchor"
    assert rows[0]["from"]["kind"] == "dense_anchor"
    assert rows[0]["from"]["row_id"] == 123
    assert rows[0]["anchor_value"] == 123


def test_chunked_model_writes_dereferences_jsonl(tmp_path) -> None:
    model = {
        "schema": "logovista-decoded-model-v0",
        "package": {"dict_id": "DEREF"},
        "classification": {},
        "components": [],
        "entry_spans": {"entries": []},
        "titles": {"samples": []},
        "indexes": {"samples": []},
        "menus": {"samples": []},
        "gaiji": {},
        "media": {},
        "dereferences": [
            {
                "id": "dereference:DEREF:manual:1",
                "kind": "index_pointer",
                "method": "index_body_pointer",
                "from": {"kind": "component", "component": "FKINDEX.DIC"},
                "to": {"kind": "component", "component": "HONMON.DIC"},
                "status": "resolved",
                "confidence": "proven",
            }
        ],
    }

    package_json = write_package_model_chunked_to_dir(model, tmp_path / "bundle")

    assert package_json.exists()
    assert (tmp_path / "bundle" / "dereferences.jsonl").read_text(encoding="utf-8").count("\n") == 1
