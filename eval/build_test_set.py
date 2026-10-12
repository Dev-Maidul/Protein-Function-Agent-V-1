"""Builds a NEW held-out TEST set, disjoint from the 20-protein development
set (eval/benchmark.json, used so far and now explicitly relabeled "dev" --
see eval/README.md), by a seeded random sample instead of the dev set's
recency-sorted selection. Frozen before any method is run on it: this file,
once generated, is read-only ground truth, same policy as the dev set.

Selection method (documented honestly, not hidden):
1. Scan a bounded number of pages of QuickGO's annotation-search API (the
   same 6 experimental evidence codes as the dev set), collecting distinct
   accessions. This is NOT the full universe of experimentally-annotated
   proteins (QuickGO's annotation-search has no cheap "accession-only"
   projection the way UniProt's stream endpoint does, and the real universe
   has >1.28M annotation rows across probably hundreds of thousands of
   proteins -- scanning all of it isn't practical). It is a large (default:
   100 pages x 200 rows = up to 20,000 annotation rows), bounded scan.
2. Cross-check reviewed (Swiss-Prot) status via a batched UniProt query.
3. Remove every accession already in the dev set (eval/benchmark.json).
4. `random.Random(seed=43)` (a DIFFERENT seed from the dev set's 42, and
   from the shared_subset.tsv's 42, so there is no hidden correlation)
   samples N=60 accessions uniformly from what's left.
5. Fetch full sequence + experimental GO terms for the sampled 60, same
   length filter as the dev set ([20, 1500] aa).

Run manually:
    python -m eval.build_test_set --n 60 --pages 100 --seed 43
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from eval.build_benchmark import fetch_experimental_annotations, fetch_sequences, filter_to_reviewed

PROJECT_ROOT = Path(__file__).parent.parent
DEV_SET_PATH = PROJECT_ROOT / "eval" / "benchmark.json"
OUT_PATH = PROJECT_ROOT / "eval" / "test_set.json"


def _dev_set_accessions() -> set[str]:
    if not DEV_SET_PATH.exists():
        return set()
    return {e["accession"] for e in json.loads(DEV_SET_PATH.read_text())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=60)
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--max-len", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=43)
    args = parser.parse_args()

    dev_accessions = _dev_set_accessions()
    print(f"Dev set has {len(dev_accessions)} accessions; these will be excluded from the test set.")

    print(f"Scanning up to {args.pages} QuickGO annotation-search pages for candidates ...")
    by_accession = fetch_experimental_annotations(args.pages)
    print(f"Found {len(by_accession)} distinct candidate accessions.")

    print("Cross-checking which are reviewed (Swiss-Prot) ...")
    reviewed = filter_to_reviewed(list(by_accession.keys()))
    candidates = {acc: by_accession[acc] for acc in by_accession if acc in reviewed and acc not in dev_accessions}
    print(f"{len(candidates)} are reviewed and not already in the dev set.")

    rng = random.Random(args.seed)
    sampled_accs = rng.sample(list(candidates.keys()), min(args.n, len(candidates)))
    print(f"Seeded random sample (seed={args.seed}): {len(sampled_accs)} accessions selected.")

    print("Fetching sequences (filtering by length) ...")
    seq_info = fetch_sequences(sampled_accs)
    test_set = []
    for acc in sampled_accs:
        info = seq_info.get(acc)
        if not info or not (20 <= info["length"] <= args.max_len):
            continue
        test_set.append(
            {
                "accession": acc,
                "protein_name": info["protein_name"],
                "organism": info["organism"],
                "sequence": info["sequence"],
                "length": info["length"],
                "experimental_go_terms": candidates[acc]["go_terms"],
            }
        )

    overlap = dev_accessions & {e["accession"] for e in test_set}
    assert not overlap, f"Test set overlaps with dev set: {overlap}"

    print(f"Final test set size: {len(test_set)} (0 overlap with dev set, verified).")
    OUT_PATH.write_text(json.dumps(test_set, indent=2))
    print(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
