"""One-time offline step: build the SAME Swiss-Prot subset used by both
tools/protrek.py (candidate text pool) and tools/build_esm2_index.py
(embedding index) -- Phase 1 fix #3 (same subset, documented selection).

Selection method -- SEEDED RANDOM sample over the full qualifying universe
(fixed after a user review caught that the first version was
accession-ascending, not random -- see docs/TOOLS.md):

1. Stream ONLY the `accession` field for every Swiss-Prot entry matching
   `reviewed:true AND cc_function:* AND length:[20 TO 1000]` -- this is
   lightweight (~3.2MB for ~455k accessions, verified live) because it skips
   the heavy text fields (function/sequence) for entries that won't be kept.
2. `random.seed(SEED); random.sample(all_accessions, n)` -- a true uniform
   random sample over the whole qualifying universe, reproducible via the
   fixed seed.
3. Fetch full fields (protein_name, organism, cc_function, sequence, length)
   for just the n sampled accessions, batched via `accession:(A OR B OR ...)`
   queries (chunks of BATCH_SIZE) -- avoids ever downloading the full ~455k
   heavy dataset.

Run manually:
    python -m tools.build_shared_subset --n 5000 --max-len 1000 --seed 42
"""
from __future__ import annotations

import argparse
import csv
import re
import time
import random
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
OUT_PATH = PROJECT_ROOT / "data" / "shared_subset.tsv"
STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"
FIELDS = "accession,protein_name,organism_name,cc_function,sequence,length"
BATCH_SIZE = 50  # larger batches (e.g. 200) hit UniProt's URL-length limit (400 error), verified live
DEFAULT_SEED = 42

_EVIDENCE_RE = re.compile(r"\{[^}]*\}")
_CHAIN_LABEL_RE = re.compile(r"^\[[^\]]+\]:\s*")


def clean_function_text(raw: str, max_chars: int = 320) -> str:
    """UniProt's cc_function field can hold several ';'-separated FUNCTION:
    entries (one per polyprotein chain) with bracketed evidence codes. Take
    the first entry, strip the 'FUNCTION:' prefix, evidence codes, and any
    '[Chain name]:' label, then truncate to a word boundary."""
    first_entry = raw.split(";")[0].strip()
    first_entry = first_entry.removeprefix("FUNCTION:").strip()
    first_entry = _CHAIN_LABEL_RE.sub("", first_entry)
    first_entry = _EVIDENCE_RE.sub("", first_entry)
    first_entry = re.sub(r"\s+", " ", first_entry).strip()
    first_entry = re.sub(r"\s+\.", ".", first_entry)  # "...glucan. ." -> "...glucan."
    if len(first_entry) <= max_chars:
        return first_entry
    truncated = first_entry[:max_chars].rsplit(" ", 1)[0]
    return truncated + "..."


def _qualifying_query(max_len: int) -> str:
    return f"reviewed:true AND cc_function:* AND length:[20 TO {max_len}]"


def fetch_all_qualifying_accessions(max_len: int) -> list[str]:
    resp = requests.get(
        STREAM_URL,
        params={"query": _qualifying_query(max_len), "fields": "accession", "format": "list"},
        timeout=300,
    )
    resp.raise_for_status()
    return [line for line in resp.text.splitlines() if line.strip()]


def fetch_batch(accessions: list[str]) -> list[dict]:
    or_query = "(" + " OR ".join(f"accession:{a}" for a in accessions) + ")"
    resp = requests.get(
        STREAM_URL,
        params={"query": or_query, "fields": FIELDS, "format": "tsv"},
        timeout=60,
    )
    resp.raise_for_status()
    lines = resp.text.splitlines()
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts))
        rows.append(
            {
                "accession": row["Entry"],
                "protein_name": row.get("Protein names", ""),
                "organism": row.get("Organism", ""),
                "function_text": clean_function_text(row.get("Function [CC]", "")),
                "sequence": row.get("Sequence", ""),
                "length": row.get("Length", ""),
            }
        )
    return rows


def build_subset(n: int, max_len: int, seed: int) -> list[dict]:
    print(f"Fetching the full list of qualifying accessions (reviewed, has Function[CC], length<={max_len}) ...")
    all_accessions = fetch_all_qualifying_accessions(max_len)
    print(f"Universe size: {len(all_accessions)} qualifying entries.")

    rng = random.Random(seed)
    sampled = rng.sample(all_accessions, min(n, len(all_accessions)))
    print(f"Seeded random sample (seed={seed}): {len(sampled)} accessions selected.")

    rows: list[dict] = []
    for start in range(0, len(sampled), BATCH_SIZE):
        batch = sampled[start : start + BATCH_SIZE]
        rows.extend(fetch_batch(batch))
        print(f"  fetched {min(start + BATCH_SIZE, len(sampled))}/{len(sampled)}")
        time.sleep(0.2)  # be polite to the API between batches
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--max-len", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    rows = build_subset(args.n, args.max_len, args.seed)
    print(f"Got {len(rows)} entries.")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["accession", "protein_name", "organism", "function_text", "sequence", "length"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
