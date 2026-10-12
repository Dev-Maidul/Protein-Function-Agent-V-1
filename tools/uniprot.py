"""UniProt REST API -- known/curated annotation for a UniProt accession.

Doubles as ground truth for evaluation, so the agent (Phase 2) must NOT be
allowed to call this tool when running in evaluation mode.

Docs: https://www.uniprot.org/help/api
Endpoint used: GET https://rest.uniprot.org/uniprotkb/{accession}.json
No auth, no rate-limit key required for light use.
"""
from __future__ import annotations

from tools.evidence import Evidence, get_with_retry, timed_call

BASE_URL = "https://rest.uniprot.org/uniprotkb"


def _fetch(accession: str) -> dict:
    resp = get_with_retry(f"{BASE_URL}/{accession}.json", timeout=20)
    resp.raise_for_status()
    data = resp.json()

    go_xrefs = [x for x in data.get("uniProtKBCrossReferences", []) if x.get("database") == "GO"]
    go_terms = []
    for x in go_xrefs:
        term_prop = next((p["value"] for p in x.get("properties", []) if p["key"] == "GoTerm"), None)
        evidence_prop = next((p["value"] for p in x.get("properties", []) if p["key"] == "GoEvidenceType"), None)
        go_terms.append({"go_id": x["id"], "term": term_prop, "evidence": evidence_prop})

    function_comments = [c for c in data.get("comments", []) if c.get("commentType") == "FUNCTION"]
    function_text = function_comments[0]["texts"][0]["value"] if function_comments else None

    protein_name = (
        data.get("proteinDescription", {})
        .get("recommendedName", {})
        .get("fullName", {})
        .get("value")
    )
    organism = data.get("organism", {}).get("scientificName")
    sequence = data.get("sequence", {}).get("value")

    summary_bits = [protein_name or accession]
    if organism:
        summary_bits.append(f"({organism})")
    if function_text:
        summary_bits.append(f"- {function_text[:160]}")
    result_summary = " ".join(summary_bits)

    return {
        "result_summary": result_summary,
        "raw_scores": {
            "annotation_score": data.get("annotationScore"),
            "go_term_count": len(go_terms),
            "go_terms": go_terms,
            "protein_name": protein_name,
            "organism": organism,
            "sequence": sequence,
            "sequence_length": data.get("sequence", {}).get("length"),
            "function_text": function_text,
        },
        "source_ids": [accession] + [g["go_id"] for g in go_terms],
        "source_urls": [f"https://www.uniprot.org/uniprotkb/{accession}"],
    }


def run(accession: str) -> Evidence:
    return timed_call("uniprot", accession, _fetch, accession)
