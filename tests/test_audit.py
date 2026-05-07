from logovista_tools.audit import classify_audit


def test_audit_classifies_readable_and_dense_honmon_shapes() -> None:
    sample = [{"body": "あ【亜】"}]

    assert (
        classify_audit(
            body_samples=sample,
            dense_marker_honmon=False,
            id_records=0,
            dictfulldb=False,
            title_components=[],
            index_boundaries=1,
        )
        == "raw_honmon_body_stream"
    )
    assert (
        classify_audit(
            body_samples=[],
            dense_marker_honmon=True,
            id_records=100,
            dictfulldb=True,
            title_components=[],
            index_boundaries=100,
        )
        == "dense_honmon_id_table_dictfulldb"
    )
    assert (
        classify_audit(
            body_samples=[],
            dense_marker_honmon=True,
            id_records=0,
            dictfulldb=True,
            title_components=[],
            index_boundaries=100,
        )
        == "dense_honmon_token_table_dictfulldb"
    )
    assert (
        classify_audit(
            body_samples=[],
            dense_marker_honmon=True,
            id_records=100,
            dictfulldb=False,
            rendererdb=True,
            title_components=[],
            index_boundaries=100,
        )
        == "dense_honmon_id_table_rendererdb"
    )
