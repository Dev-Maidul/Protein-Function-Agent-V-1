"""QuickGO REST API -- validate a predicted GO term ID and resolve its name /
ancestors, so the agent never reports a made-up or obsolete GO term.

Docs: https://www.ebi.ac.uk/QuickGO/api/index.html
No auth required.
"""
from __future__ import annotations

from tools.evidence import Evidence, get_with_retry, timed_call

BASE_URL = "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms"


def _lookup(go_id: str) -> dict:
    resp = get_with_retry(f"{BASE_URL}/{go_id}", headers={"Accept": "application/json"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("results"):
        return {
            "result_summary": f"{go_id} is not a valid/known GO term",
            "raw_scores": {"valid": False},
            "source_ids": [go_id],
            "source_urls": [f"https://www.ebi.ac.uk/QuickGO/term/{go_id}"],
        }

    term = data["results"][0]
    is_obsolete = term.get("isObsolete", False)

    ancestors: list[str] = []
    if not is_obsolete:
        anc_resp = get_with_retry(
            f"{BASE_URL}/{go_id}/ancestors",
            params={"relations": "is_a,part_of"},
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if anc_resp.ok:
            anc_results = anc_resp.json().get("results", [])
            if anc_results:
                ancestors = anc_results[0].get("ancestors", [])

    return {
        "result_summary": f"{go_id} = \"{term['name']}\" ({term.get('aspect')}), valid={not is_obsolete}",
        "raw_scores": {
            "valid": not is_obsolete,
            "name": term.get("name"),
            "aspect": term.get("aspect"),
            "is_obsolete": is_obsolete,
            "definition": term.get("definition", {}).get("text"),
            "ancestor_count": len(ancestors),
            "ancestors": ancestors,
        },
        "source_ids": [go_id] + ancestors,
        "source_urls": [f"https://www.ebi.ac.uk/QuickGO/term/{go_id}"],
    }


def run(go_id: str) -> Evidence:
    return timed_call("quickgo", go_id, _lookup, go_id)
