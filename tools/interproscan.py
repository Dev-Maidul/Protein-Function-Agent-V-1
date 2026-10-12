"""InterProScan5 via the EMBL-EBI Job Dispatcher REST API -- finds protein
domains/families in a sequence and their GO cross-references.

Docs: https://www.ebi.ac.uk/jdispatcher/docs/webservices/
Base URL confirmed live in this project's Phase 0: https://www.ebi.ac.uk/Tools/services/rest/iprscan5
This is an ASYNC job: submit -> poll status -> fetch result. EBI requires a
contact email on submission (read from EBI_CONTACT_EMAIL in .env) and rate-limits
abuse, so polling uses a fixed interval and a hard timeout -- a slow/stuck job
must become an `error`-carrying Evidence, never an exception or an infinite wait.
"""
from __future__ import annotations

import os
import time

import requests

from tools import interpro2go
from tools.evidence import Evidence, timed_call

BASE_URL = "https://www.ebi.ac.uk/Tools/services/rest/iprscan5"
POLL_INTERVAL_S = 15
MAX_WAIT_S = 20 * 60  # InterProScan jobs commonly take 1-10 minutes; this project's
                        # own live test (p53, 393 aa) finished in ~1m35s.


def _submit(sequence: str, email: str) -> str:
    resp = requests.post(
        f"{BASE_URL}/run",
        data={
            "email": email,
            "sequence": sequence,
            "goterms": "true",
            "pathways": "false",
            "stype": "p",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text.strip()


def _poll_until_done(job_id: str) -> str:
    waited = 0
    while waited < MAX_WAIT_S:
        resp = requests.get(f"{BASE_URL}/status/{job_id}", timeout=20)
        resp.raise_for_status()
        status = resp.text.strip()
        if status in ("FINISHED", "ERROR", "FAILURE", "NOT_FOUND"):
            return status
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    return "TIMEOUT"


def _fetch_result(job_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/result/{job_id}/json", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse(result: dict) -> dict:
    matches = result["results"][0].get("matches", [])
    domains = []
    go_terms: set[str] = set()
    for m in matches:
        entry = m.get("signature", {}).get("entry") or {}
        if not entry.get("accession"):
            continue
        loc = m["locations"][0] if m.get("locations") else {}
        entry_go_ids = {go["id"] for go in entry.get("goXRefs", [])}
        # The live InterProScan response's goXRefs is the primary source, but
        # it doesn't always come back populated for every entry -- fall back
        # to EBI's own interpro2go mapping file as a second, independent path
        # to the same curated mapping (see tools/interpro2go.py).
        from_mapping = set(interpro2go.get_go_terms(entry["accession"])) - entry_go_ids
        domains.append(
            {
                "interpro_id": entry["accession"],
                "name": entry.get("name"),
                "type": entry.get("type"),
                "start": loc.get("start"),
                "end": loc.get("end"),
                "evalue": loc.get("evalue"),
                "signature_db": m.get("signature", {}).get("signatureLibraryRelease", {}).get("library"),
                "go_ids": sorted(entry_go_ids | from_mapping),
                "go_ids_from_interpro2go_fallback": sorted(from_mapping),
            }
        )
        go_terms |= entry_go_ids | from_mapping

    top_names = ", ".join(d["name"] for d in domains[:3] if d.get("name"))
    result_summary = (
        f"{len(domains)} InterPro domain/family hits, {len(go_terms)} distinct GO term(s)"
        + (f" -- top: {top_names}" if top_names else "")
    )
    return {
        "result_summary": result_summary,
        "raw_scores": {"domain_count": len(domains), "domains": domains},
        "source_ids": [d["interpro_id"] for d in domains] + sorted(go_terms),
        "source_urls": [f"https://www.ebi.ac.uk/interpro/entry/InterPro/{d['interpro_id']}" for d in domains],
    }


def _run_pipeline(sequence: str, email: str | None) -> dict:
    email = email or os.environ.get("EBI_CONTACT_EMAIL")
    if not email:
        raise ValueError("EBI_CONTACT_EMAIL is not set (required by EBI's usage policy)")

    job_id = _submit(sequence, email)
    status = _poll_until_done(job_id)
    if status != "FINISHED":
        raise RuntimeError(f"InterProScan job {job_id} ended with status={status}")

    result = _fetch_result(job_id)
    parsed = _parse(result)
    parsed["source_urls"].append(f"https://www.ebi.ac.uk/Tools/services/rest/iprscan5/result/{job_id}/json")
    return parsed


def run(sequence: str, email: str | None = None) -> Evidence:
    return timed_call("interproscan", sequence[:30] + "...", _run_pipeline, sequence, email)
