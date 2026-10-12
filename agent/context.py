"""AgentContext: the per-run leakage-control state. Builds the exclusion set
(the query's own accession plus any close DIAMOND homolog) once per run, in
evaluation mode only, and carries the query sequence/accession/eval_mode
flag that agent/evidence_pipeline.py, agent/aggregator.py, and
eval/baselines.py all read.

(Previously lived in agent/dispatch.py alongside a dispatch() function that
routed free-form LLM tool calls to tools/*.run() -- removed when the agent
moved to a deterministic evidence pipeline that calls every tool directly,
not via LLM-chosen dispatch; see agent/loop.py's module docstring.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tools import diamond_blast
from tools.evidence import Evidence

HOMOLOG_IDENTITY_THRESHOLD = 50.0  # percent; configurable, see docs/ARCHITECTURE.md


@dataclass
class AgentContext:
    query_sequence: str
    query_accession: str | None
    eval_mode: bool
    exclude_accessions: set[str] = field(default_factory=set)

    def build_exclusions(self) -> Evidence | None:
        """Called once before evidence gathering starts, in eval mode only.
        Runs DIAMOND once (logged separately as a setup action, not as a
        regular evidence-ledger entry) to find close homologs at >= 50%
        identity, so the agent cannot retrieve the query's own annotation or
        a near-identical one through any retrieval tool."""
        if not self.eval_mode:
            return None
        self.exclude_accessions = {self.query_accession} if self.query_accession else set()
        ev = diamond_blast.run(self.query_sequence, query_id=self.query_accession or "query")
        if ev.error is None:
            for hit in ev.raw_scores.get("hits", []):
                if hit["pident"] >= HOMOLOG_IDENTITY_THRESHOLD:
                    self.exclude_accessions.add(hit["uniprot_accession"])
        return ev
