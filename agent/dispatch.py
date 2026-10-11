"""Maps a validated tool-call name/args to the actual tools/*.run() call,
injecting context the model must never control: EBI_CONTACT_EMAIL, and (in
evaluation mode) the set of accessions to exclude from retrieval results so
the agent cannot see the query's own annotation or a close homolog's.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from tools import alphafold_foldseek, diamond_blast, esm2_knn, interproscan, protrek, quickgo, uniprot
from tools.evidence import Evidence

HOMOLOG_IDENTITY_THRESHOLD = 50.0  # percent; configurable, see docs/ARCHITECTURE.md


@dataclass
class AgentContext:
    query_sequence: str
    query_accession: str | None
    eval_mode: bool
    exclude_accessions: set[str] = field(default_factory=set)

    def build_exclusions(self) -> Evidence | None:
        """Called once before the loop starts, in eval mode only. Runs DIAMOND
        once (not counted as an agent step/evidence item -- a setup action,
        logged separately in the trace) to find close homologs at >= 50%
        identity, so the agent cannot retrieve the query's own annotation or
        a near-identical one through ProTrek/ESM-2."""
        if not self.eval_mode:
            return None
        self.exclude_accessions = {self.query_accession} if self.query_accession else set()
        ev = diamond_blast.run(self.query_sequence, query_id=self.query_accession or "query")
        if ev.error is None:
            for hit in ev.raw_scores.get("hits", []):
                if hit["pident"] >= HOMOLOG_IDENTITY_THRESHOLD:
                    self.exclude_accessions.add(hit["uniprot_accession"])
        return ev


def dispatch(tool_name: str, args: dict, ctx: AgentContext) -> Evidence:
    if tool_name == "uniprot_lookup":
        return uniprot.run(args["accession"])

    if tool_name == "quickgo_validate":
        return quickgo.run(args["go_id"])

    if tool_name == "interproscan_scan":
        return interproscan.run(args["sequence"], email=os.environ.get("EBI_CONTACT_EMAIL"))

    if tool_name == "alphafold_foldseek_search":
        return alphafold_foldseek.run(args["accession"], database=args.get("database", "afdb-swissprot"))

    if tool_name == "diamond_blast_search":
        return diamond_blast.run(args["sequence"], query_id=ctx.query_accession or "query", top_n=args.get("top_n", 10))

    if tool_name == "protrek_retrieve":
        return protrek.run(args["sequence"], top_k=args.get("top_k", 10), exclude_accessions=ctx.exclude_accessions or None)

    if tool_name == "esm2_knn_search":
        return esm2_knn.run(args["sequence"], top_k=args.get("top_k", 10), exclude_accessions=ctx.exclude_accessions or None)

    raise ValueError(f"Unknown tool: {tool_name}")
