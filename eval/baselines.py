"""Four baselines compared in Phase 3, all under the same leakage control:
the query's own accession and any DIAMOND homolog at >= HOMOLOG_IDENTITY_THRESHOLD
identity are excluded from every search database / retrieval result (same
exclusion set computed once per protein by agent.context.AgentContext).

(a) diamond_transfer   -- classic BLAST-transfer baseline: take the top
    non-excluded homolog's own UniProt GO annotations as the prediction,
    confidence = that hit's % sequence identity (0-1).
(b) protrek_transfer   -- ProTrek retrieves the top-k best-matching Swiss-Prot
    descriptions; each retrieved accession's own GO terms are treated as a
    "vote", confidence = fraction of the top-k hits annotated with that term.
(c) llm_no_tools        -- the LLM guesses GO terms from the sequence alone,
    no tool access at all (single chat call, not the agent loop).
(d) aggregator_only     -- agent/evidence_pipeline.py + agent/aggregator.py,
    deterministic, NO LLM at all. Reported to answer: does the LLM review
    step add anything over the rule-based aggregator alone?
(e) full_agent          -- agent.loop.AgentRun in eval_mode=True (pipeline +
    aggregator + one LLM review call).
"""
from __future__ import annotations

import json
import re

from agent.aggregator import aggregate
from agent.context import AgentContext
from agent.evidence_pipeline import gather_evidence
from agent.llm_client import LLMClient
from agent.loop import AgentRun
from tools import protrek, uniprot


def diamond_transfer(sequence: str, ctx: AgentContext, top_n: int = 10) -> list[dict]:
    from tools import diamond_blast

    ev = diamond_blast.run(sequence, query_id=ctx.query_accession or "query", top_n=top_n)
    if ev.error:
        return []
    hits = [h for h in ev.raw_scores.get("hits", []) if h["uniprot_accession"] not in ctx.exclude_accessions]
    if not hits:
        return []
    top = hits[0]
    confidence = top["pident"] / 100.0
    hit_ev = uniprot.run(top["uniprot_accession"])
    if hit_ev.error:
        return []
    return [{"go_id": g["go_id"], "confidence": confidence} for g in hit_ev.raw_scores.get("go_terms", [])]


def protrek_transfer(sequence: str, ctx: AgentContext, top_k: int = 10) -> list[dict]:
    ev = protrek.run(sequence, top_k=top_k, exclude_accessions=ctx.exclude_accessions or None)
    if ev.error:
        return []
    ranked = ev.raw_scores.get("ranked", [])
    if not ranked:
        return []
    vote_count: dict[str, int] = {}
    for hit in ranked:
        hit_ev = uniprot.run(hit["accession"])
        if hit_ev.error:
            continue
        for g in hit_ev.raw_scores.get("go_terms", []):
            vote_count[g["go_id"]] = vote_count.get(g["go_id"], 0) + 1
    return [{"go_id": go_id, "confidence": count / len(ranked)} for go_id, count in vote_count.items()]


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

NO_TOOLS_PROMPT = """You are a protein function prediction expert. Given only an amino-acid sequence \
(no tools, no database access), guess its likely GO (Gene Ontology) terms from your own knowledge of \
protein sequence motifs and homologous families you have seen during training.

Respond with ONLY a JSON object, no other text, in this exact shape:
{"go_terms": [{"go_id": "GO:XXXXXXX", "confidence": 0.0_to_1.0}, ...]}

Sequence:
"""


def llm_no_tools(sequence: str, client: LLMClient | None = None) -> list[dict]:
    client = client or LLMClient()
    response = client.chat(
        messages=[{"role": "user", "content": NO_TOOLS_PROMPT + sequence}],
        tools=[],
    )
    text = response.get("content") or ""
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    terms = parsed.get("go_terms", [])
    return [{"go_id": t["go_id"], "confidence": float(t.get("confidence", 0.5))} for t in terms if "go_id" in t]


def gather_once(ctx: AgentContext) -> tuple[list[dict], list[dict]]:
    """Runs the deterministic evidence pipeline + aggregator exactly once.
    Callers that need both aggregator_only AND full_agent predictions for
    the same protein should call this once and pass the results to both
    (via aggregator_only_from_ledger and full_agent's ledger= param) instead
    of each separately re-running InterProScan/DIAMOND/etc."""
    import os

    ledger = gather_evidence(ctx, os.environ.get("EBI_CONTACT_EMAIL"))
    candidates = aggregate(ledger)
    return ledger, candidates


def aggregator_only_from_candidates(candidates: list[dict]) -> list[dict]:
    """Every candidate is grounded by construction (agent/aggregator.py only
    ever scores GO IDs that came from a real tool's raw output), so no
    separate grounding check is needed here."""
    return [{"go_id": c["go_id"], "confidence": c["confidence"]} for c in candidates]


def aggregator_only(sequence: str, ctx: AgentContext) -> tuple[list[dict], list[dict]]:
    """Standalone version (gathers its own evidence) for callers that only
    want this one method, not both aggregator_only and full_agent."""
    ledger, candidates = gather_once(ctx)
    return aggregator_only_from_candidates(candidates), candidates


def full_agent(
    sequence: str,
    accession: str,
    max_steps: int | None = None,
    ctx: AgentContext | None = None,
    ledger: list[dict] | None = None,
    aggregator_candidates: list[dict] | None = None,
    client=None,
) -> tuple[list[dict], dict]:
    run = AgentRun(
        query_sequence=sequence,
        query_accession=accession,
        eval_mode=True,
        max_steps=max_steps,
        ctx=ctx,
        ledger=ledger,
        aggregator_candidates=aggregator_candidates,
        client=client,
    )
    report = run.run()
    terms = [{"go_id": t["go_id"], "confidence": t["confidence"]} for t in report.get("go_terms", [])]
    return terms, report
