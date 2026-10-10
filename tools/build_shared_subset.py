"""One-time offline step: build the SAME Swiss-Prot subset used by both
tools/protrek.py (candidate text pool) and tools/build_esm2_index.py
(embedding index) -- Phase 1 fix #3 (same subset, documented selection).

Selection method (deterministic, not random -- see docs/TOOLS.md):
stream reviewed (Swiss-Prot) entries from UniProt in the API's default order
(accession ascending), keep only entries with a non-empty "Function [CC]"
annotation and a sequence length in [20, MAX_LEN], and stop once N such
entries are collected. This is reproducible (re-running gives the same
subset) and documented honestly as "first N qualifying entries by accession",
not a random sample.

Run manually:
    python -m tools.build_shared_subset --n 5000 --max-len 1000
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
OUT_PATH = PROJECT_ROOT / "data" / "shared_subset.tsv"
STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"
FIELDS = "accession,protein_name,organism_name,cc_function,sequence,length"

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


def build_subset(n: int, max_len: int) -> list[dict]:
    resp = requests.get(
        STREAM_URL,
        params={"query": "reviewed:true", "fields": FIELDS, "format": "tsv"},
        stream=True,
        timeout=300,
    )
    resp.raise_for_status()

    rows: list[dict] = []
    lines = resp.iter_lines(decode_unicode=True)
    header = next(lines).split("\t")

    for line in lines:
        if len(rows) >= n:
            break
        parts = line.split("\t")
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts))
        func_raw = row.get("Function [CC]", "")
        seq = row.get("Sequence", "")
        if not func_raw or not (20 <= len(seq) <= max_len):
            continue
        rows.append(
            {
                "accession": row["Entry"],
                "protein_name": row.get("Protein names", ""),
                "organism": row.get("Organism", ""),
                "function_text": clean_function_text(func_raw),
                "sequence": seq,
                "length": len(seq),
            }
        )
    resp.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--max-len", type=int, default=1000)
    args = parser.parse_args()

    print(f"Streaming UniProt (reviewed:true), collecting first {args.n} qualifying entries ...")
    rows = build_subset(args.n, args.max_len)
    print(f"Got {len(rows)} entries (stopped early once target reached, or stream exhausted).")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["accession", "protein_name", "organism", "function_text", "sequence", "length"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
