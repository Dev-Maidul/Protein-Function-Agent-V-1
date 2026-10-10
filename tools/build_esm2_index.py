"""One-time offline step (not a per-call tool): build an ESM-2 embedding
index over the SAME Swiss-Prot subset used by ProTrek (data/shared_subset.tsv,
built by tools/build_shared_subset.py -- Phase 1 fix #3: one shared subset,
documented once in docs/TOOLS.md), plus a hubness diagnostic (k-occurrence of
each reference point as a top-k neighbour, computed leave-one-out over the
index itself). tools/esm2_knn.py loads the output at query time.

Batched (padded batches via the tokenizer's attention mask) and checkpointed:
if interrupted, re-running resumes after the last fully-embedded batch rather
than starting over (Phase 1 fix #2).

Run manually:
    python -m tools.build_esm2_index --batch-size 16 --k 10
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
SUBSET_PATH = PROJECT_ROOT / "data" / "shared_subset.tsv"
OUT_EMBEDDINGS = PROJECT_ROOT / "data" / "esm2_swissprot_subset.npz"
OUT_HUBNESS = PROJECT_ROOT / "data" / "esm2_hubness.json"
CHECKPOINT_PATH = PROJECT_ROOT / "data" / "esm2_embeddings.checkpoint.npz"
MODEL_NAME = "facebook/esm2_t12_35M_UR50D"


def _load_subset() -> list[dict]:
    with open(SUBSET_PATH) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _load_checkpoint(n_total: int, dim: int) -> tuple[np.ndarray, int]:
    """BUG FIXED (caught during Phase 1 fixes, see docs/TOOLS.md): this used to
    restore only `embeddings[:done]` -- a contiguous slice by ORIGINAL array
    index. But rows are written at scattered original-index positions (via
    the length-sorted `order[]` in _embed_all), so most already-computed rows
    have an original index >= done and were silently wiped back to zero on
    every resume. The fix: the checkpoint's full embeddings array is already
    correct as saved (unwritten rows are zero, written rows -- wherever they
    are -- hold real values); only `done`, a separate sorted-rank counter,
    needs to be restored to know where the loop should resume."""
    if CHECKPOINT_PATH.exists():
        data = np.load(CHECKPOINT_PATH)
        return data["embeddings"], int(data["done"])
    return np.zeros((n_total, dim), dtype=np.float32), 0


def _embed_all(records: list[dict], batch_size: int) -> np.ndarray:
    import torch
    from transformers import AutoTokenizer, EsmModel

    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = EsmModel.from_pretrained(MODEL_NAME).eval()
    dim = model.config.hidden_size

    sequences = [r["sequence"] for r in records]
    n = len(sequences)
    embeddings, done = _load_checkpoint(n, dim)
    if done:
        print(f"Resuming from checkpoint: {done}/{n} already embedded.")

    # Length-sort before batching so each padded batch wastes little compute
    # on padding tokens (CPU batching is otherwise slower than one-by-one for
    # this length-varied a subset, 21-1000 aa -- measured in this project).
    # Deterministic given the same input records, so a resumed run recomputes
    # the identical order and `done` (a count of sorted-order positions) still
    # means the same thing across runs.
    order = sorted(range(n), key=lambda i: len(sequences[i]))

    t0 = time.monotonic()
    with torch.no_grad():
        for start in range(done, n, batch_size):
            end = min(start + batch_size, n)
            batch_positions = order[start:end]
            batch = [sequences[i] for i in batch_positions]
            inputs = tok(batch, return_tensors="pt", padding=True)
            out = model(**inputs)
            # mean-pool over real (non-pad, non-BOS/EOS) tokens per sequence
            mask = inputs["attention_mask"].clone()
            mask[:, 0] = 0  # BOS
            for i, seq in enumerate(batch):
                mask[i, len(seq) + 1 :] = 0  # EOS + padding
            mask = mask.unsqueeze(-1).float()
            pooled = (out.last_hidden_state * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            for row, pos in zip(pooled.numpy(), batch_positions):
                embeddings[pos] = row

            np.savez(CHECKPOINT_PATH, embeddings=embeddings, done=end)
            elapsed = time.monotonic() - t0
            print(f"  embedded {end}/{n} ({elapsed:.1f}s elapsed)")
    return embeddings


def _compute_hubness(embeddings: np.ndarray, k: int) -> dict:
    """Leave-one-out top-k neighbour search over the index itself; counts how
    often each point appears as a top-k neighbour of some other point
    (k-occurrence N_k). A point whose N_k is much higher than average is a
    'hub' -- it keeps appearing as a 'nearest neighbour' for many unrelated
    queries, a known failure mode of high-dimensional nearest-neighbour
    search, which should make its retrieval evidence less trustworthy."""
    norm = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    sims = norm @ norm.T
    np.fill_diagonal(sims, -np.inf)

    k_occurrence = np.zeros(len(embeddings), dtype=int)
    for i in range(len(embeddings)):
        top_k = np.argpartition(-sims[i], k)[:k]
        k_occurrence[top_k] += 1

    mean_ko = float(k_occurrence.mean())
    std_ko = float(k_occurrence.std())
    max_ko = int(k_occurrence.max())
    # Fisher-Pearson skewness, computed manually (no scipy dependency)
    skew = float(np.mean(((k_occurrence - mean_ko) / std_ko) ** 3)) if std_ko > 0 else 0.0

    return {
        "k": k,
        "n": len(embeddings),
        "k_occurrence": k_occurrence.tolist(),
        "mean": mean_ko,
        "std": std_ko,
        "max": max_ko,
        "skewness": skew,
        "hub_threshold": mean_ko + 3 * std_ko,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    records = _load_subset()
    print(f"Loaded {len(records)} sequences from {SUBSET_PATH}. Embedding with {MODEL_NAME} (CPU, batched) ...")
    embeddings = _embed_all(records, args.batch_size)

    print(f"Computing hubness diagnostic (k={args.k}) over {len(records)} points ...")
    hubness = _compute_hubness(embeddings, args.k)
    top20_idx = np.argsort(-np.array(hubness["k_occurrence"]))[:20]

    OUT_EMBEDDINGS.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_EMBEDDINGS,
        embeddings=embeddings,
        accessions=np.array([r["accession"] for r in records]),
        descriptions=np.array([r["protein_name"] for r in records]),
        sequences=np.array([r["sequence"] for r in records]),
    )
    hubness["top20_hubs"] = [
        {"accession": records[i]["accession"], "protein_name": records[i]["protein_name"], "k_occurrence": hubness["k_occurrence"][i]}
        for i in top20_idx
    ]
    OUT_HUBNESS.write_text(json.dumps(hubness, indent=2))
    CHECKPOINT_PATH.unlink(missing_ok=True)

    print(f"Saved embeddings -> {OUT_EMBEDDINGS}")
    print(f"Saved hubness stats -> {OUT_HUBNESS}")
    print(
        f"k-occurrence: mean={hubness['mean']:.2f} std={hubness['std']:.2f} "
        f"max={hubness['max']} skewness={hubness['skewness']:.2f} hub_threshold={hubness['hub_threshold']:.2f}"
    )


if __name__ == "__main__":
    main()
