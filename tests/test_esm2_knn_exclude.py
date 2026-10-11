import numpy as np
from unittest.mock import patch

from tools import esm2_knn


def _fake_index():
    rng = np.random.default_rng(0)
    embeddings = rng.normal(size=(5, 8)).astype("float32")
    return {
        "embeddings": embeddings,
        "accessions": np.array(["A1", "A2", "A3", "A4", "QUERY_ACC"]),
        "descriptions": np.array(["d1", "d2", "d3", "d4", "self"]),
    }


def _fake_hubness():
    return {"k": 3, "k_occurrence": [1, 2, 3, 4, 5], "mean": 3.0, "std": 1.4, "hub_threshold": 40.0}


def test_esm2_knn_excludes_query_accession_in_eval_mode(tmp_path):
    index = _fake_index()
    # query embedding identical to QUERY_ACC's own row -- would be top-1 if not excluded
    query_emb = index["embeddings"][4].copy()

    fake_index_path = tmp_path / "index.npz"
    fake_hubness_path = tmp_path / "hubness.json"
    fake_index_path.write_bytes(b"")
    fake_hubness_path.write_text("{}")

    with patch("tools.esm2_knn._load_index", return_value=index), patch(
        "tools.esm2_knn._load_hubness", return_value=_fake_hubness()
    ), patch("tools.esm2_knn._embed_query", return_value=query_emb):
        ev = esm2_knn.run(
            "FAKESEQ",
            top_k=3,
            exclude_accessions={"QUERY_ACC"},
            index_path=fake_index_path,
            hubness_path=fake_hubness_path,
        )

    assert ev.error is None
    assert ev.raw_scores["excluded_count"] == 1
    assert all(n["accession"] != "QUERY_ACC" for n in ev.raw_scores["neighbours"])
