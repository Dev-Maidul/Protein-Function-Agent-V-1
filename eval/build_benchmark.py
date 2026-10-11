"""Builds the Phase 3 evaluation benchmark: N reviewed Swiss-Prot proteins
with EXPERIMENTAL GO evidence (EXP/IDA/IPI/IMP/IGI/IEP -- the only evidence
codes CAFA-style benchmarks treat as ground truth), preferring the most
recently annotated ones to reduce the chance the LLM memorized them during
pretraining.

Selection method (documented honestly in eval/README.md too):
1. Query QuickGO's annotation search (not UniProt's own search -- QuickGO
   lets us filter directly by evidence code, which UniProt's query language
   does not expose) for the 6 experimental evidence codes, no taxon
   restriction, paginated. Collect (accession, go_id, aspect, evidence,
   annotation_date) rows.
2. Group by accession; keep every experimental GO term per protein (the
   ground-truth set for Fmax) and the single most recent annotation date.
3. Cross-check each candidate accession is reviewed (Swiss-Prot) via a
   batched UniProt query (unreviewed TrEMBL entries are excluded -- same
   batch-OR-query pattern as tools/build_shared_subset.py).
4. Sort candidates by most-recent-annotation-date descending, keep the top N
   with sequence length in [20, 1500] (so every tool, including the slower
   InterProScan, finishes in reasonable time), fetch full sequence.

Run manually:
    python -m eval.build_benchmark --n 20
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent
OUT_PATH = PROJECT_ROOT / "eval" / "benchmark.json"
QUICKGO_URL = "https://www.ebi.ac.uk/QuickGO/services/annotation/search"
UNIPROT_STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"

EXPERIMENTAL_EVIDENCE_CODES = {
    "ECO:0000269": "EXP",
    "ECO:0000314": "IDA",
    "ECO:0000315": "IMP",
    "ECO:0000316": "IGI",
    "ECO:0000353": "IPI",
    "ECO:0000270": "IEP",
}


def fetch_experimental_annotations(pages: int, page_size: int = 200) -> dict[str, dict]:
    by_accession: dict[str, dict] = {}
    codes = ",".join(EXPERIMENTAL_EVIDENCE_CODES)
    for page in range(1, pages + 1):
        resp = requests.get(
            QUICKGO_URL,
            params={"evidenceCode": codes, "limit": page_size, "page": page},
            headers={"Accept": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            break
        for row in results:
            accession = row["geneProductId"].removeprefix("UniProtKB:")
            accession = accession.split("-")[0]  # strip isoform/proteoform suffixes (e.g. -PRO_..., -2)
            entry = by_accession.setdefault(accession, {"go_terms": [], "latest_date": "00000000"})
            entry["go_terms"].append(
                {
                    "go_id": row["goId"],
                    "aspect": row["goAspect"],
                    "evidence": EXPERIMENTAL_EVIDENCE_CODES.get(row["evidenceCode"], row["evidenceCode"]),
                }
            )
            if row["date"] > entry["latest_date"]:
                entry["latest_date"] = row["date"]
        print(f"  page {page}: {len(results)} rows, {len(by_accession)} distinct accessions so far")
        time.sleep(0.1)
    return by_accession


def filter_to_reviewed(accessions: list[str], batch_size: int = 50) -> set[str]:
    reviewed = set()
    for start in range(0, len(accessions), batch_size):
        batch = accessions[start : start + batch_size]
        or_query = "(" + " OR ".join(f"accession:{a}" for a in batch) + ") AND reviewed:true"
        resp = requests.get(UNIPROT_STREAM_URL, params={"query": or_query, "fields": "accession", "format": "list"}, timeout=30)
        resp.raise_for_status()
        reviewed.update(line.strip() for line in resp.text.splitlines() if line.strip())
    return reviewed


def fetch_sequences(accessions: list[str], batch_size: int = 50) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for start in range(0, len(accessions), batch_size):
        batch = accessions[start : start + batch_size]
        or_query = "(" + " OR ".join(f"accession:{a}" for a in batch) + ")"
        resp = requests.get(
            UNIPROT_STREAM_URL,
            params={"query": or_query, "fields": "accession,protein_name,organism_name,sequence,length", "format": "tsv"},
            timeout=30,
        )
        resp.raise_for_status()
        lines = resp.text.splitlines()
        header = lines[0].split("\t")
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            info[row["Entry"]] = {
                "protein_name": row.get("Protein names", ""),
                "organism": row.get("Organism", ""),
                "sequence": row.get("Sequence", ""),
                "length": int(row.get("Length", 0)),
            }
    return info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--pages", type=int, default=15, help="QuickGO annotation-search pages to scan for candidates")
    parser.add_argument("--max-len", type=int, default=1500)
    args = parser.parse_args()

    print("Fetching experimentally-annotated proteins from QuickGO ...")
    by_accession = fetch_experimental_annotations(args.pages)
    print(f"Found {len(by_accession)} distinct candidate accessions with experimental GO evidence.")

    print("Cross-checking which are reviewed (Swiss-Prot) ...")
    reviewed = filter_to_reviewed(list(by_accession.keys()))
    candidates = {acc: by_accession[acc] for acc in by_accession if acc in reviewed}
    print(f"{len(candidates)} are reviewed Swiss-Prot entries.")

    ranked = sorted(candidates.items(), key=lambda kv: kv[1]["latest_date"], reverse=True)

    print("Fetching sequences for top candidates (filtering by length) ...")
    benchmark = []
    seq_info = fetch_sequences([acc for acc, _ in ranked[: args.n * 3]])  # fetch extra in case some get filtered by length
    for acc, entry in ranked:
        if len(benchmark) >= args.n:
            break
        info = seq_info.get(acc)
        if not info or not (20 <= info["length"] <= args.max_len):
            continue
        benchmark.append(
            {
                "accession": acc,
                "protein_name": info["protein_name"],
                "organism": info["organism"],
                "sequence": info["sequence"],
                "length": info["length"],
                "latest_annotation_date": entry["latest_date"],
                "experimental_go_terms": entry["go_terms"],
            }
        )

    print(f"Final benchmark size: {len(benchmark)}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(benchmark, indent=2))
    print(f"Saved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
