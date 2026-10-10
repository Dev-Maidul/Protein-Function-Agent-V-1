"""One-time offline step (not a per-call tool): build a small ESM-2 embedding
index over a Swiss-Prot subset, plus a hubness diagnostic (k-occurrence of
each reference point as a top-k neighbour, computed leave-one-out over the
index itself). tools/esm2_knn.py loads the output at query time.

Run manually:
    python -m tools.build_esm2_index --n 800 --max-len 1000 --k 10

Reuses data/uniprot_sprot.fasta (already downloaded for tools/diamond_blast.py
-- no separate download needed, per this project's Phase 1 plan).
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
FASTA_PATH = PROJECT_ROOT / "data" / "uniprot_sprot.fasta"
OUT_EMBEDDINGS = PROJECT_ROOT / "data" / "esm2_swissprot_subset.npz"
OUT_HUBNESS = PROJECT_ROOT / "data" / "esm2_hubness.json"
MODEL_NAME = "facebook/esm2_t12_35M_UR50D"


def _iter_fasta(path: Path):
    header, seq_lines = None, []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(seq_lines)
                header, seq_lines = line[1:], []
            else:
                seq_lines.append(line)
    if header is not None:
        yield header, "".join(seq_lines)


def _sample_sequences(n: int, max_len: int, seed: int = 42) -> list[dict]:
    random.seed(seed)
    reservoir: list[dict] = []
    seen = 0
    for header, seq in _iter_fasta(FASTA_PATH):
        if not (20 <= len(seq) <= max_len):
            continue
        parts = header.split("|")
        accession = parts[1] if len(parts) > 1 else header.split()[0]
        description = header.split(None, 1)[1] if " " in header else header
        record = {"accession": accession, "description": description, "sequence": seq}
        seen += 1
        if len(reservoir) < n:
            reservoir.append(record)
        else:
            j = random.randint(0, seen - 1)
            if j < n:
                reservoir[j] = record
    return reservoir


def _embed_all(records: list[dict]) -> np.ndarray:
    import torch
    from transformers import AutoTokenizer, EsmModel

    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = EsmModel.from_pretrained(MODEL_NAME).eval()

    embeddings = np.zeros((len(records), model.config.hidden_size), dtype=np.float32)
    t0 = time.monotonic()
    with torch.no_grad():
        for i, rec in enumerate(records):
            inputs = tok(rec["sequence"], return_tensors="pt")
            out = model(**inputs)
            # mean-pool over real tokens, excluding BOS/EOS
            embeddings[i] = out.last_hidden_state[0, 1:-1].mean(dim=0).numpy()
            if (i + 1) % 100 == 0:
                elapsed = time.monotonic() - t0
                print(f"  embedded {i + 1}/{len(records)} ({elapsed:.1f}s elapsed)")
    return embeddings


def _compute_hubness(embeddings: np.ndarray, k: int) -> dict:
    """Leave-one-out top-k neighbour search over the index itself; counts how
    often each point appears as a top-k neighbour of some other point
    (k-occurrence). A point whose k-occurrence is much higher than average is
    a 'hub' -- it keeps appearing as a 'nearest neighbour' for many unrelated
    queries, which should make its retrieval evidence less trustworthy."""
    norm = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    sims = norm @ norm.T
    np.fill_diagonal(sims, -np.inf)

    k_occurrence = np.zeros(len(embeddings), dtype=int)
    for i in range(len(embeddings)):
        top_k = np.argpartition(-sims[i], k)[:k]
        k_occurrence[top_k] += 1

    mean_ko, std_ko = float(k_occurrence.mean()), float(k_occurrence.std())
    return {
        "k": k,
        "k_occurrence": k_occurrence.tolist(),
        "mean": mean_ko,
        "std": std_ko,
        "hub_threshold": mean_ko + 3 * std_ko,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=800)
    parser.add_argument("--max-len", type=int, default=1000)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    print(f"Sampling {args.n} Swiss-Prot sequences (<= {args.max_len} aa) from {FASTA_PATH} ...")
    records = _sample_sequences(args.n, args.max_len)
    print(f"Got {len(records)} sequences. Embedding with {MODEL_NAME} (CPU) ...")
    embeddings = _embed_all(records)

    print(f"Computing hubness diagnostic (k={args.k}) ...")
    hubness = _compute_hubness(embeddings, args.k)

    OUT_EMBEDDINGS.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_EMBEDDINGS,
        embeddings=embeddings,
        accessions=np.array([r["accession"] for r in records]),
        descriptions=np.array([r["description"] for r in records]),
        sequences=np.array([r["sequence"] for r in records]),
    )
    OUT_HUBNESS.write_text(json.dumps(hubness, indent=2))
    print(f"Saved embeddings -> {OUT_EMBEDDINGS}")
    print(f"Saved hubness stats -> {OUT_HUBNESS}")
    print(f"k-occurrence: mean={hubness['mean']:.2f} std={hubness['std']:.2f} hub_threshold={hubness['hub_threshold']:.2f}")


if __name__ == "__main__":
    main()
