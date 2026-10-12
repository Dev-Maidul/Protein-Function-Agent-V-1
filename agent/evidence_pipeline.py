"""Deterministic evidence gathering: calls every tool exactly once, in a
fixed order, respecting eval_mode exclusions throughout. This replaces
letting the LLM freely decide which tools to call in the main loop -- the
Phase 3 eval showed that approach is unreliable (plain-text fallback ~30% of
the time) and, more importantly, that the agent was gathering strictly LESS
evidence than the simple baselines because nothing ever fetched a homolog's
own GO terms. Now every evidence source a baseline uses, the pipeline also
gathers, so the LLM (or the deterministic aggregator) is reviewing at least
as much evidence as any single baseline.

Returns a ledger in the same {"id": "E1", "tool":, "args":, "evidence": ...}
shape agent/loop.py already uses, so agent/report.py's grounding check and
agent/aggregator.py both work unchanged against it.
"""
from __future__ import annotations

from tools import alphafold_foldseek, diamond_blast, esm2_knn, homolog_go, interproscan, protrek


def gather_evidence(ctx, email: str | None) -> list[dict]:
    ledger: list[dict] = []

    def add(tool_name: str, args: dict, evidence) -> str:
        eid = f"E{len(ledger) + 1}"
        ledger.append({"id": eid, "tool": tool_name, "args": args, "evidence": evidence.to_dict()})
        return eid

    exclude = ctx.exclude_accessions or None

    diamond_ev = diamond_blast.run(ctx.query_sequence, query_id=ctx.query_accession or "query", exclude_accessions=exclude)
    add("diamond_blast_search", {"sequence": "<query>"}, diamond_ev)
    if not diamond_ev.error:
        diamond_accs = [h["uniprot_accession"] for h in diamond_ev.raw_scores.get("hits", [])]
        if diamond_accs:
            hg_ev = homolog_go.run(diamond_accs, exclude_accessions=exclude)
            add("homolog_go_annotations", {"accessions": diamond_accs, "source": "diamond"}, hg_ev)

    # Foldseek needs a UniProt accession (AlphaFold DB is looked up by accession,
    # not a raw sequence) -- skipped, honestly, when the query has none.
    if ctx.query_accession:
        foldseek_ev = alphafold_foldseek.run(ctx.query_accession, exclude_accessions=exclude)
        add("alphafold_foldseek_search", {"accession": ctx.query_accession}, foldseek_ev)
        if not foldseek_ev.error:
            foldseek_accs = [h["accession"] for h in foldseek_ev.raw_scores.get("hits", []) if h.get("accession")]
            if foldseek_accs:
                hg_ev2 = homolog_go.run(foldseek_accs, exclude_accessions=exclude)
                add("homolog_go_annotations", {"accessions": foldseek_accs, "source": "foldseek"}, hg_ev2)

    ipr_ev = interproscan.run(ctx.query_sequence, email=email)
    add("interproscan_scan", {"sequence": "<query>"}, ipr_ev)

    protrek_ev = protrek.run(ctx.query_sequence, top_k=10, exclude_accessions=exclude)
    add("protrek_retrieve", {"sequence": "<query>"}, protrek_ev)
    if not protrek_ev.error:
        protrek_accs = [h["accession"] for h in protrek_ev.raw_scores.get("ranked", [])]
        if protrek_accs:
            hg_ev3 = homolog_go.run(protrek_accs, exclude_accessions=exclude)
            add("homolog_go_annotations", {"accessions": protrek_accs, "source": "protrek"}, hg_ev3)

    esm2_ev = esm2_knn.run(ctx.query_sequence, top_k=10, exclude_accessions=exclude)
    add("esm2_knn_search", {"sequence": "<query>"}, esm2_ev)
    if not esm2_ev.error:
        esm2_accs = [n["accession"] for n in esm2_ev.raw_scores.get("neighbours", [])]
        if esm2_accs:
            hg_ev4 = homolog_go.run(esm2_accs, exclude_accessions=exclude)
            add("homolog_go_annotations", {"accessions": esm2_accs, "source": "esm2"}, hg_ev4)

    return ledger
