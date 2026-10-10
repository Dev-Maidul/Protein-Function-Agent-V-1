"""One-time offline step: pre-compute ProTrek_35M text embeddings for the
shared Swiss-Prot subset (data/shared_subset.tsv, built by
tools/build_shared_subset.py) -- Phase 1 fix #1. tools/protrek.py loads this
cache at query time instead of embedding a hand-picked list of 5 candidates.

Batched and checkpointed (resumable): if interrupted, re-running picks up
after the last fully-embedded batch rather than starting over.

Run manually:
    python -m tools.build_protrek_cache --batch-size 250
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from tools.protrek import DEFAULT_WEIGHTS_DIR, _load_model

PROJECT_ROOT = Path(__file__).parent.parent
SUBSET_PATH = PROJECT_ROOT / "data" / "shared_subset.tsv"
OUT_PATH = PROJECT_ROOT / "data" / "protrek_text_embeddings.npz"
CHECKPOINT_PATH = PROJECT_ROOT / "data" / "protrek_text_embeddings.checkpoint.npz"


def _load_subset() -> list[dict]:
    with open(SUBSET_PATH) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _load_checkpoint(n_total: int, dim: int) -> tuple[np.ndarray, int]:
    if CHECKPOINT_PATH.exists():
        data = np.load(CHECKPOINT_PATH)
        done = int(data["done"])
        embeddings = np.zeros((n_total, dim), dtype=np.float32)
        embeddings[:done] = data["embeddings"][:done]
        return embeddings, done
    return np.zeros((n_total, dim), dtype=np.float32), 0


def build(batch_size: int, device: str) -> None:
    import torch

    rows = _load_subset()
    texts = [r["function_text"] for r in rows]
    accessions = [r["accession"] for r in rows]
    print(f"Loaded {len(rows)} descriptions from {SUBSET_PATH}")

    model = _load_model(str(DEFAULT_WEIGHTS_DIR), device)

    with torch.no_grad():
        dim = model.get_text_repr([texts[0]]).shape[1]

    embeddings, done = _load_checkpoint(len(texts), dim)
    if done:
        print(f"Resuming from checkpoint: {done}/{len(texts)} already embedded.")

    t0 = time.monotonic()
    with torch.no_grad():
        for start in range(done, len(texts), batch_size):
            end = min(start + batch_size, len(texts))
            batch_emb = model.get_text_repr(texts[start:end])
            embeddings[start:end] = batch_emb.cpu().numpy()
            np.savez(CHECKPOINT_PATH, embeddings=embeddings, done=end)
            elapsed = time.monotonic() - t0
            print(f"  embedded {end}/{len(texts)} ({elapsed:.1f}s elapsed)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_PATH,
        embeddings=embeddings,
        accessions=np.array(accessions),
        descriptions=np.array(texts),
    )
    CHECKPOINT_PATH.unlink(missing_ok=True)
    print(f"Saved -> {OUT_PATH} (total time {time.monotonic() - t0:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    import torch

    device = args.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Using device: {device}")
    build(args.batch_size, device)


if __name__ == "__main__":
    main()
