import pytest

from schemas.debate import metadata_updater


def test_metadata_updater_merges_parallel_node_metadata() -> None:
    merged = metadata_updater(
        {"reasons": ["stale_evidence_48h"], "rag": {"chunks": 4}, "flash_calls": 1},
        {"reasons": ["stale_evidence_48h", "low_confidence"], "rag": {"tokens": 800}},
    )

    assert merged["reasons"] == ["stale_evidence_48h", "low_confidence"]
    assert merged["rag"] == {"chunks": 4, "tokens": 800}
    assert merged["flash_calls"] == 1


def test_metadata_updater_rejects_invalid_metadata_type() -> None:
    with pytest.raises(TypeError, match="metadata reducer expected dict or None"):
        metadata_updater({}, ["not", "metadata"])  # type: ignore[arg-type]
