"""ESM-2 (facebook/esm2_t12_35M_UR50D) embedding + kNN search over a small
Swiss-Prot subset, plus a hubness diagnostic: some reference points turn up
as a "nearest neighbour" for many unrelated queries (a known failure mode of
high-dimensional nearest-neighbour search -- see this user's own hubness
research). Those over-popular neighbours are flagged so the agent can treat
their evidence as less trustworthy.

The embedding index + hubness stats are built OFFLINE, once, by
tools/build_esm2_index.py (not at query time) -- see docs/TOOLS.md for the
exact command and runtime. This module only loads that output and embeds the
query sequence live.

Model: https://huggingface.co/facebook/esm2_t12_35M_UR50D (MIT-style,
Meta/FAIR's ESM license -- see docs/TOOLS.md).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import numpy as np

from tools.evidence import Evidence, timed_call

PROJECT_ROOT = Path(__file__).parent.parent
INDEX_PATH = PROJECT_ROOT / "data" / "esm2_swissprot_subset.npz"
HUBNESS_PATH = PROJECT_ROOT / "data" / "esm2_hubness.json"
MODEL_NAME = "facebook/esm2_t12_35M_UR50D"


@functools.lru_cache(maxsize=1)
def _load_index(index_path: str):
    data = np.load(index_path)
    return {
        "embeddings": data["embeddings"],
        "accessions": data["accessions"],
        "descriptions": data["descriptions"],
    }


@functools.lru_cache(maxsize=1)
def _load_hubness(hubness_path: str) -> dict:
    return json.loads(Path(hubness_path).read_text())


@functools.lru_cache(maxsize=1)
def _load_model():
    from transformers import AutoTokenizer, EsmModel

    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = EsmModel.from_pretrained(MODEL_NAME).eval()
    return tok, model


def _embed_query(sequence: str) -> np.ndarray:
    import torch

    tok, model = _load_model()
    inputs = tok(sequence, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    return out.last_hidden_state[0, 1:-1].mean(dim=0).numpy()


def _knn_search(query_emb: np.ndarray, index: dict, hubness: dict, top_k: int) -> list[dict]:
    embeddings = index["embeddings"]
    norm_index = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    norm_query = query_emb / np.linalg.norm(query_emb)
    sims = norm_index @ norm_query

    top_idx = np.argsort(-sims)[:top_k]
    k_occurrence = hubness["k_occurrence"]
    hub_threshold = hubness["hub_threshold"]

    hits = []
    for i in top_idx:
        hits.append(
            {
                "accession": str(index["accessions"][i]),
                "description": str(index["descriptions"][i]),
                "cosine_similarity": float(sims[i]),
                "k_occurrence": k_occurrence[i],
                "is_hub": k_occurrence[i] > hub_threshold,
            }
        )
    return hits


def _run_pipeline(sequence: str, top_k: int, index_path: Path, hubness_path: Path) -> dict:
    if not index_path.exists() or not hubness_path.exists():
        raise RuntimeError(
            f"ESM-2 index not found at {index_path} -- run "
            "`python -m tools.build_esm2_index` first (see docs/TOOLS.md)"
        )

    index = _load_index(str(index_path))
    hubness = _load_hubness(str(hubness_path))
    query_emb = _embed_query(sequence)
    hits = _knn_search(query_emb, index, hubness, top_k)

    hub_count = sum(1 for h in hits if h["is_hub"])
    top = hits[0] if hits else None
    result_summary = (
        f"{len(hits)} nearest neighbour(s) in Swiss-Prot subset; top={top['accession']} "
        f"(cos_sim={top['cosine_similarity']:.3f})"
        + (f" -- {hub_count} flagged as hub(s), treat with caution" if hub_count else "")
        if top
        else "no neighbours found"
    )
    return {
        "result_summary": result_summary,
        "raw_scores": {
            "neighbour_count": len(hits),
            "neighbours": hits,
            "hub_count": hub_count,
            "index_size": len(index["accessions"]),
            "hubness_mean": hubness["mean"],
            "hubness_std": hubness["std"],
            "hub_threshold": hubness["hub_threshold"],
        },
        "source_ids": [h["accession"] for h in hits],
        "source_urls": [f"https://www.uniprot.org/uniprotkb/{h['accession']}" for h in hits],
    }


def run(
    sequence: str,
    top_k: int = 10,
    index_path: Path | None = None,
    hubness_path: Path | None = None,
) -> Evidence:
    index_path = index_path or INDEX_PATH
    hubness_path = hubness_path or HUBNESS_PATH
    return timed_call("esm2_knn", sequence[:30] + "...", _run_pipeline, sequence, top_k, index_path, hubness_path)
