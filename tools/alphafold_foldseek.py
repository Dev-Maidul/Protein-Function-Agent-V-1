"""AlphaFold DB (fetch a predicted structure by UniProt accession) + Foldseek
web search (structure -> structurally similar proteins, which carry function).

AlphaFold DB docs: https://alphafold.ebi.ac.uk/api-docs
Foldseek web API confirmed live in this project's Phase 0: https://search.foldseek.com/api/ticket
Foldseek search is async (submit -> ticket -> poll -> result), but in this
project's live test it returned COMPLETE on the same response as the submit
call -- polling is still implemented for the general case, with a timeout.

Note: AlphaFold's file server returns 403 to requests without a User-Agent
header (observed live in this project), so one is always sent.
"""
from __future__ import annotations

import time

import requests

from tools.evidence import Evidence, timed_call

AFDB_API = "https://alphafold.ebi.ac.uk/api/prediction"
AFDB_FILES = "https://alphafold.ebi.ac.uk/files"
FOLDSEEK_API = "https://search.foldseek.com/api"
USER_AGENT = {"User-Agent": "protein-function-agent/0.1 (research prototype)"}
POLL_INTERVAL_S = 5
MAX_WAIT_S = 5 * 60


def fetch_alphafold_structure(accession: str) -> tuple[str, bytes]:
    """Returns (pdb_url, pdb_file_bytes) for the latest AlphaFold model."""
    resp = requests.get(f"{AFDB_API}/{accession}", timeout=20)
    resp.raise_for_status()
    entries = resp.json()
    if not entries:
        raise ValueError(f"No AlphaFold structure found for {accession}")
    pdb_url = entries[0]["pdbUrl"]
    pdb_resp = requests.get(pdb_url, headers=USER_AGENT, timeout=30)
    pdb_resp.raise_for_status()
    return pdb_url, pdb_resp.content


def _submit_foldseek(pdb_bytes: bytes, database: str) -> str:
    resp = requests.post(
        f"{FOLDSEEK_API}/ticket",
        files={"q": ("query.pdb", pdb_bytes)},
        data={"mode": "3diaa", "database[]": database},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _poll_foldseek(ticket_id: str) -> str:
    waited = 0
    while waited < MAX_WAIT_S:
        resp = requests.get(f"{FOLDSEEK_API}/ticket/{ticket_id}", timeout=20)
        resp.raise_for_status()
        status = resp.json().get("status")
        if status in ("COMPLETE", "ERROR"):
            return status
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    return "TIMEOUT"


def _fetch_foldseek_result(ticket_id: str) -> dict:
    resp = requests.get(f"{FOLDSEEK_API}/result/{ticket_id}/0", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse(af_url: str, foldseek_result: dict) -> dict:
    hits = []
    for db_result in foldseek_result.get("results", []):
        for aln_group in db_result.get("alignments", []):
            for aln in aln_group:
                hits.append(
                    {
                        "target": aln["target"],
                        "seq_identity_pct": aln.get("seqId"),
                        "e_value": aln.get("eval"),
                        "score": aln.get("score"),
                        "aligned_length": aln.get("alnLength"),
                        "db": db_result.get("db"),
                    }
                )
    hits.sort(key=lambda h: h.get("e_value", float("inf")))

    top = hits[0] if hits else None
    result_summary = (
        f"{len(hits)} structural neighbour(s); top hit={top['target']} "
        f"(seqId={top['seq_identity_pct']}%, eval={top['e_value']:.2e})"
        if top
        else "no structural neighbours found"
    )
    return {
        "result_summary": result_summary,
        "raw_scores": {"hit_count": len(hits), "hits": hits[:20]},
        "source_ids": [h["target"] for h in hits[:20]],
        "source_urls": [af_url],
    }


def _run_pipeline(accession: str, database: str) -> dict:
    af_url, pdb_bytes = fetch_alphafold_structure(accession)
    ticket_id = _submit_foldseek(pdb_bytes, database)
    status = _poll_foldseek(ticket_id)
    if status != "COMPLETE":
        raise RuntimeError(f"Foldseek search {ticket_id} ended with status={status}")
    result = _fetch_foldseek_result(ticket_id)
    return _parse(af_url, result)


def run(accession: str, database: str = "afdb-swissprot") -> Evidence:
    return timed_call("alphafold_foldseek", accession, _run_pipeline, accession, database)
