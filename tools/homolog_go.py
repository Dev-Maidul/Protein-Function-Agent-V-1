"""Fetches GO terms (with evidence codes) for a list of homolog accessions
found by diamond_blast_search or alphafold_foldseek_search. This is the
direct fix for the root cause identified after Phase 3's first eval run: the
DIAMOND/Foldseek tools only ever returned hit accessions and identity/score,
never the hits' own GO annotations -- so the agent had strictly less
grounding evidence available than the diamond_transfer baseline, which reads
a hit's GO terms directly from UniProt. This tool closes that gap by giving
the agent the same homolog-GO-transfer evidence, with an evidence ID and
source hit recorded per term, and (just like the baseline) respects leakage
control: any accession that is the query itself or a close homolog is
excluded even if the caller forgets to filter it out first.
"""
from __future__ import annotations

from tools import uniprot
from tools.evidence import Evidence, timed_call


def _fetch(accessions: list[str], exclude_accessions: set[str] | None) -> dict:
    exclude_accessions = exclude_accessions or set()
    # de-dupe while preserving order (callers may pass hits from both DIAMOND
    # and Foldseek, which can overlap)
    seen = set()
    candidates = []
    for acc in accessions:
        if acc not in seen:
            seen.add(acc)
            candidates.append(acc)

    kept = [a for a in candidates if a not in exclude_accessions]
    excluded_count = len(candidates) - len(kept)

    go_terms = []
    queried = []
    for acc in kept:
        ev = uniprot.run(acc)
        queried.append(acc)
        if ev.error:
            continue
        for g in ev.raw_scores.get("go_terms", []):
            go_terms.append(
                {
                    "go_id": g["go_id"],
                    "term": g.get("term"),
                    "evidence_code": g.get("evidence"),
                    "source_accession": acc,
                }
            )

    unique_go_ids = sorted({g["go_id"] for g in go_terms})
    result_summary = (
        f"{len(unique_go_ids)} distinct GO term(s) from {len(queried)} homolog(s)"
        + (f" ({excluded_count} excluded)" if excluded_count else "")
        if queried
        else "no homolog accessions left to query after exclusion"
    )
    return {
        "result_summary": result_summary,
        "raw_scores": {"go_terms": go_terms, "queried_accessions": queried, "excluded_count": excluded_count},
        "source_ids": unique_go_ids + queried,
        "source_urls": [f"https://www.uniprot.org/uniprotkb/{a}" for a in queried],
    }


def run(accessions: list[str], exclude_accessions: set[str] | None = None) -> Evidence:
    label = ",".join(accessions[:3]) + ("..." if len(accessions) > 3 else "")
    return timed_call("homolog_go", label, _fetch, accessions, exclude_accessions)
