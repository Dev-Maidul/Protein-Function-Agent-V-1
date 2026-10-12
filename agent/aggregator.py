"""Deterministic GO-term candidate scorer -- no LLM involved. Given the
evidence ledger from agent/evidence_pipeline.py, scores every candidate GO
term using fixed, documented weights per evidence source, cross-referencing
each `homolog_go_annotations` entry's GO terms against the matching
diamond/foldseek/protrek/esm2 hit (by accession) so the weight reflects that
hit's own identity/similarity/hub-flag.

This is itself a reportable method ("aggregator_only" in eval/run_eval.py):
running it alone, with no LLM review at all, answers whether the LLM step
adds anything beyond what deterministic score-and-rank already gets you.

Weighting scheme (documented here, not tuned on any benchmark):
- InterPro domain match (direct sequence motif -> GO, via InterProScan's own
  goXRefs or the interpro2go fallback): weight 1.0 per domain match. STRONG.
- DIAMOND homolog transfer: weight = (hit % identity / 100) * evidence_quality.
  STRONG (this is exactly what diamond_transfer baseline does).
- Foldseek structural homolog transfer: weight = max(seq_identity/100, 0.3) *
  evidence_quality * 0.9 (slightly discounted vs. direct sequence identity,
  since structural similarity is a weaker homology signal than sequence ID).
- ProTrek-retrieved candidate's own GO terms: weight = 0.25 * evidence_quality
  -- WEAK by project rule (a ProTrek hit may be a pathway/interaction partner,
  not the query's own function; see agent/loop.py's system prompt).
- ESM-2 neighbour's own GO terms: weight = (0.1 if hub-flagged else 0.4) *
  cosine_similarity * evidence_quality -- hub-flagged neighbours are heavily
  down-weighted (Phase 3's hubness diagnostic empirically confirmed they are
  less reliable: 0% hit rate vs 25.3% for non-hub, see docs/RESULTS.md).
- evidence_quality = 1.0 for an experimental evidence code (EXP/IDA/IPI/IMP/
  IGI/IEP) on the SOURCE hit's own annotation, else 0.6.
- A GO ID's final score is the SUM of every contribution above (multiple
  corroborating sources add up), capped at 1.0 as the reported confidence.
"""
from __future__ import annotations

EXPERIMENTAL_CODES = {"EXP", "IDA", "IMP", "IGI", "IPI", "IEP"}


def _evidence_quality(evidence_code: str | None) -> float:
    code = (evidence_code or "").split(":")[0]
    return 1.0 if code in EXPERIMENTAL_CODES else 0.6


def _index_by_tool(ledger: list[dict]):
    diamond_hits, foldseek_hits, protrek_hits, esm2_hits = {}, {}, {}, {}
    domain_entries = []
    for entry in ledger:
        if entry["evidence"].get("error"):
            continue
        raw = entry["evidence"].get("raw_scores", {})
        tool = entry["tool"]
        if tool == "diamond_blast_search":
            for h in raw.get("hits", []):
                diamond_hits[h["uniprot_accession"]] = h
        elif tool == "alphafold_foldseek_search":
            for h in raw.get("hits", []):
                if h.get("accession"):
                    foldseek_hits[h["accession"]] = h
        elif tool == "protrek_retrieve":
            for h in raw.get("ranked", []):
                protrek_hits[h["accession"]] = h
        elif tool == "esm2_knn_search":
            for h in raw.get("neighbours", []):
                esm2_hits[h["accession"]] = h
        elif tool == "interproscan_scan":
            domain_entries.append(entry)
    return diamond_hits, foldseek_hits, protrek_hits, esm2_hits, domain_entries


def aggregate(ledger: list[dict]) -> list[dict]:
    diamond_hits, foldseek_hits, protrek_hits, esm2_hits, domain_entries = _index_by_tool(ledger)

    scores: dict[str, float] = {}
    evidence_ids: dict[str, set[str]] = {}
    explanations: dict[str, list[str]] = {}

    def bump(go_id: str, weight: float, eid: str, explanation: str) -> None:
        if weight <= 0:
            return
        scores[go_id] = scores.get(go_id, 0.0) + weight
        evidence_ids.setdefault(go_id, set()).add(eid)
        explanations.setdefault(go_id, []).append(explanation)

    for entry in domain_entries:
        eid = entry["id"]
        for d in entry["evidence"]["raw_scores"].get("domains", []):
            for go_id in d.get("go_ids", []):
                bump(go_id, 1.0, eid, f"InterPro domain {d['interpro_id']} ({d.get('name')})")

    for entry in ledger:
        if entry["tool"] != "homolog_go_annotations" or entry["evidence"].get("error"):
            continue
        eid = entry["id"]
        source = entry["args"].get("source", "unknown")
        for g in entry["evidence"]["raw_scores"].get("go_terms", []):
            acc = g["source_accession"]
            quality = _evidence_quality(g.get("evidence_code"))

            if source == "diamond" and acc in diamond_hits:
                identity = diamond_hits[acc]["pident"] / 100.0
                weight = identity * quality
                explanation = f"DIAMOND homolog {acc} ({diamond_hits[acc]['pident']:.1f}% identity) -> {g['go_id']}"
            elif source == "foldseek" and acc in foldseek_hits:
                sim = max((foldseek_hits[acc].get("seq_identity_pct") or 0) / 100.0, 0.3)
                weight = sim * quality * 0.9
                explanation = f"Foldseek structural homolog {acc} (seqId={foldseek_hits[acc].get('seq_identity_pct')}%) -> {g['go_id']}"
            elif source == "protrek" and acc in protrek_hits:
                weight = 0.25 * quality
                explanation = f"ProTrek-retrieved {acc} (WEAK: pathway/interaction-partner risk) -> {g['go_id']}"
            elif source == "esm2" and acc in esm2_hits:
                hub = esm2_hits[acc].get("is_hub", False)
                cos = esm2_hits[acc].get("cosine_similarity", 0.0)
                weight = (0.1 if hub else 0.4) * cos * quality
                hub_note = ", HUB-FLAGGED (down-weighted)" if hub else ""
                explanation = f"ESM-2 neighbour {acc} (cos={cos:.3f}{hub_note}) -> {g['go_id']}"
            else:
                weight = 0.2 * quality
                explanation = f"Homolog {acc} ({source}) -> {g['go_id']}"

            bump(g["go_id"], weight, eid, explanation)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [
        {
            "go_id": go_id,
            "confidence": round(min(1.0, score), 3),
            "raw_score": round(score, 3),
            "evidence_ids": sorted(evidence_ids[go_id]),
            "explanations": explanations[go_id],
        }
        for go_id, score in ranked
    ]
