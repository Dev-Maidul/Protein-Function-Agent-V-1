import numpy as np
from unittest.mock import patch

from tools import protrek


def _fake_cache():
    return {
        "embeddings": np.array(
            [
                [1.0, 0.0],  # A1 -- best match
                [0.0, 1.0],  # A2
                [0.9, 0.1],  # A3 -- second-best, close to query
                [-1.0, 0.0],  # A4 -- query's own accession, should be excluded
            ],
            dtype=np.float32,
        ),
        "accessions": np.array(["A1", "A2", "A3", "QUERY_ACC"]),
        "descriptions": np.array(["desc A1", "desc A2", "desc A3", "desc of query itself"]),
    }


def test_protrek_retrieves_and_ranks_from_pool():
    cache = _fake_cache()
    query_emb = np.array([1.0, 0.0], dtype=np.float32)

    with patch("tools.protrek._load_model", return_value="fake-model"), patch(
        "tools.protrek._load_text_cache", return_value=cache
    ), patch("tools.protrek._embed_sequence", return_value=query_emb), patch(
        "tools.protrek._temperature", return_value=1.0
    ):
        ev = protrek.run("FAKESEQ", top_k=3, device="cpu")

    assert ev.error is None
    assert ev.raw_scores["candidate_pool_size"] == 4
    assert ev.raw_scores["ranked"][0]["accession"] == "A1"
    assert ev.raw_scores["ranked"][1]["accession"] == "A3"
    assert "A1" in ev.source_ids


def test_protrek_excludes_accessions_in_evaluation_mode():
    cache = _fake_cache()
    query_emb = np.array([-1.0, 0.0], dtype=np.float32)  # closest to QUERY_ACC itself

    with patch("tools.protrek._load_model", return_value="fake-model"), patch(
        "tools.protrek._load_text_cache", return_value=cache
    ), patch("tools.protrek._embed_sequence", return_value=query_emb), patch(
        "tools.protrek._temperature", return_value=1.0
    ):
        ev = protrek.run("FAKESEQ", top_k=3, exclude_accessions={"QUERY_ACC"}, device="cpu")

    assert ev.error is None
    assert ev.raw_scores["excluded_count"] == 1
    assert ev.raw_scores["candidate_pool_size"] == 3
    assert all(r["accession"] != "QUERY_ACC" for r in ev.raw_scores["ranked"])


def test_protrek_missing_cache_becomes_error_not_exception(tmp_path):
    missing_cache = tmp_path / "does_not_exist.npz"
    ev = protrek.run("FAKESEQ", cache_path=missing_cache, device="cpu")

    assert ev.error is not None
