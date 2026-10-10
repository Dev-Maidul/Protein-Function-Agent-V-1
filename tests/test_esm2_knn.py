import numpy as np
from unittest.mock import patch

from tools import esm2_knn


def _fake_index():
    rng = np.random.default_rng(0)
    embeddings = rng.normal(size=(5, 8)).astype("float32")
    return {
        "embeddings": embeddings,
        "accessions": np.array(["A1", "A2", "A3", "A4", "A5"]),
        "descriptions": np.array(["desc1", "desc2", "desc3", "desc4", "desc5"]),
    }


def _fake_hubness():
    return {"k": 3, "k_occurrence": [1, 50, 2, 3, 1], "mean": 11.4, "std": 19.0, "hub_threshold": 40.0}


def test_esm2_knn_flags_hub_neighbour():
    index = _fake_index()
    query_emb = index["embeddings"][1] + 0.01  # close to A2, the flagged hub

    # default index_path/hubness_path point at this project's real,
    # already-built files (see docs/TOOLS.md) -- only the loader functions and
    # the live embedding call are mocked, to keep this test fast and offline.
    with patch("tools.esm2_knn._load_index", return_value=index), patch(
        "tools.esm2_knn._load_hubness", return_value=_fake_hubness()
    ), patch("tools.esm2_knn._embed_query", return_value=query_emb):
        ev = esm2_knn.run("FAKESEQUENCE", top_k=3)

    assert ev.error is None
    assert ev.raw_scores["neighbour_count"] == 3
    assert ev.raw_scores["neighbours"][0]["accession"] == "A2"
    assert ev.raw_scores["neighbours"][0]["is_hub"] is True
    assert ev.raw_scores["hub_count"] == 1
    assert "hub" in ev.result_summary.lower()


def test_esm2_knn_missing_index_becomes_error(tmp_path):
    missing = tmp_path / "does_not_exist.npz"
    ev = esm2_knn.run("FAKESEQUENCE", index_path=missing, hubness_path=missing)

    assert ev.error is not None
    assert "build_esm2_index" in ev.error
