"""The agent: gathers evidence deterministically (agent/evidence_pipeline.py,
every tool called exactly once, not left to the LLM's discretion), scores
candidate GO terms deterministically (agent/aggregator.py), then asks the LLM
to review those candidates ONCE (accept/reject/re-weight, write the summary
and uncertainty section) and submit a structured final answer.

This replaces the earlier free-form "LLM picks which of 8 tools to call, in
which order, for up to MAX_STEPS turns" design. Phase 3's first eval run
showed two problems with that design: (1) the model wrote its final answer
as plain text instead of calling submit_final_answer on ~30% of proteins,
burning steps; (2) the agent was gathering strictly LESS evidence than the
diamond_transfer baseline, because nothing ever fetched a homolog's own GO
terms. Both are fixed structurally here: evidence gathering no longer
depends on the LLM reliably choosing to call the right tools, and every
evidence source a baseline uses is now also gathered for the agent.

The LLM still gets exactly one chance to fail at tool-calling (writing plain
text instead of calling submit_final_answer) before a second, sharper
attempt; if that also fails, GO IDs and evidence-ID citations are parsed
out of its free text with a documented, honest heuristic and the run is
marked `recovered_from_text` -- never silently treated as equivalent to a
real structured answer, and never used to fabricate evidence it didn't cite.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.aggregator import aggregate
from agent.context import AgentContext
from agent.evidence_pipeline import gather_evidence
from agent.llm_client import LLMClient
from agent.report import build_report, render_markdown
from agent.run_metadata import capture_run_metadata
from agent.tool_schemas import SUBMIT_FINAL_ANSWER_SCHEMA

PROJECT_ROOT = Path(__file__).parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"

REVIEW_SYSTEM_PROMPT = """You are a protein function annotation agent. All evidence has already been \
gathered for you (homology search, domain scan, structural search, retrieval search) and candidate GO \
terms have already been scored by a deterministic rule-based aggregator -- you do not call any tools \
yourself. Your job is to REVIEW the evidence and candidates, then call submit_final_answer exactly once.

Rules:
- You may accept a candidate as-is, reject it, or adjust its confidence -- but every GO term you report \
must cite at least one real evidence ID (E1, E2, ...) from the evidence below. Do not add a GO term that \
has no evidence ID behind it, even if you are confident it is correct from your own training knowledge.
- The aggregator already down-weights ProTrek-sourced and hub-flagged ESM-2-sourced terms (weak evidence); \
you may lower them further but should not treat a low-scored weak-evidence term as strong proof.
- Validate GO IDs for plausibility -- do not invent a GO ID that isn't in the candidate list or evidence below.
- Write function_summary citing evidence IDs in parentheses, and use disagreements_or_uncertainty to flag \
anything where evidence conflicted or was thin.
- You MUST respond by calling submit_final_answer. Never write your answer as plain text content -- it \
will not be scored.
"""

GO_ID_RE = re.compile(r"GO:\d{7}")
EVIDENCE_REF_RE = re.compile(r"\bE\d+\b")
CONFIDENCE_RE = re.compile(r"\b(0\.\d+|1\.0+)\b")


def _format_evidence_for_prompt(ledger: list[dict]) -> str:
    lines = []
    for entry in ledger:
        ev = entry["evidence"]
        if ev.get("error"):
            lines.append(f"{entry['id']} ({entry['tool']}): ERROR -- {ev['error']}")
        else:
            lines.append(f"{entry['id']} ({entry['tool']}): {ev['result_summary']}")
    return "\n".join(lines)


def _format_candidates_for_prompt(candidates: list[dict]) -> str:
    if not candidates:
        return "(the aggregator found no candidate GO terms grounded in any evidence)"
    lines = []
    for c in candidates[:30]:
        reasons = "; ".join(c["explanations"][:3])
        lines.append(f"- {c['go_id']} (aggregator confidence {c['confidence']:.2f}, evidence {','.join(c['evidence_ids'])}): {reasons}")
    return "\n".join(lines)


def _parse_go_terms_from_text(text: str) -> list[dict]:
    """Heuristic, honest recovery when the model fails to call the tool
    twice: scan line by line, collect GO IDs plus any evidence-ID references
    and confidence-looking numbers on the SAME line. A GO ID with no
    evidence-ID reference on its line gets an empty evidence_ids list, which
    means it will be dropped by agent/report.py's grounding check just like
    any other ungrounded claim -- this parser does not get a free pass."""
    terms = []
    for line in (text or "").splitlines():
        go_ids = GO_ID_RE.findall(line)
        if not go_ids:
            continue
        evidence_ids = EVIDENCE_REF_RE.findall(line)
        conf_match = CONFIDENCE_RE.search(line)
        confidence = float(conf_match.group(1)) if conf_match else 0.5
        for go_id in go_ids:
            terms.append({"go_id": go_id, "confidence": confidence, "evidence_ids": evidence_ids})
    return terms


class AgentRun:
    def __init__(
        self,
        query_sequence: str,
        query_accession: str | None,
        eval_mode: bool,
        max_steps: int | None = None,  # kept for CLI/eval compatibility; unused by the new 2-attempt review design
        client=None,
        ctx: AgentContext | None = None,
        ledger: list[dict] | None = None,
        aggregator_candidates: list[dict] | None = None,
    ):
        # ctx/ledger/aggregator_candidates let a caller (eval/run_eval.py) reuse
        # one evidence-gathering pass for both aggregator_only and full_agent,
        # instead of paying for InterProScan/DIAMOND/etc. twice per protein.
        self.ctx = ctx or AgentContext(query_sequence=query_sequence, query_accession=query_accession, eval_mode=eval_mode)
        self.client = client or LLMClient()
        self.ledger: list[dict] = ledger or []
        self.aggregator_candidates: list[dict] = aggregator_candidates or []
        self._evidence_already_gathered = ledger is not None
        self.trace: list[dict] = []
        self.final_answer: dict | None = None
        self.recovered_from_text = False
        self.stop_reason = "unknown"

    def _log(self, entry: dict) -> None:
        entry["t"] = time.monotonic()
        self.trace.append(entry)

    def run(self) -> dict:
        if self._evidence_already_gathered:
            self._log({"type": "evidence_reused", "n_entries": len(self.ledger)})
        else:
            if not self.ctx.exclude_accessions and self.ctx.eval_mode:
                setup_evidence = self.ctx.build_exclusions()
                if setup_evidence is not None:
                    self._log({"type": "setup_exclusion", "evidence": setup_evidence.to_dict(), "excluded": sorted(self.ctx.exclude_accessions)})

            email = os.environ.get("EBI_CONTACT_EMAIL")
            self.ledger = gather_evidence(self.ctx, email)
            self.aggregator_candidates = aggregate(self.ledger)

        for entry in self.ledger:
            self._log({"type": "tool_result", "id": entry["id"], "tool": entry["tool"], "evidence": entry["evidence"]})
        self._log({"type": "aggregator_candidates", "candidates": self.aggregator_candidates})

        user_prompt = (
            f"Protein sequence ({len(self.ctx.query_sequence)} aa):\n{self.ctx.query_sequence}\n\n"
            f"Evidence gathered:\n{_format_evidence_for_prompt(self.ledger)}\n\n"
            f"Aggregator-scored candidate GO terms:\n{_format_candidates_for_prompt(self.aggregator_candidates)}\n\n"
            "Review the above and call submit_final_answer now."
        )
        messages = [{"role": "system", "content": REVIEW_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
        tools = [SUBMIT_FINAL_ANSWER_SCHEMA]

        for attempt in (1, 2):
            if self.client.budget_exceeded():
                self.stop_reason = f"MAX_TOKENS_PER_RUN ({self.client.max_tokens_per_run}) exceeded"
                self._log({"type": "stop", "reason": self.stop_reason})
                break

            assistant_msg = self.client.chat(messages, tools)
            messages.append({"role": "assistant", "content": assistant_msg["content"], "tool_calls": assistant_msg["tool_calls"] or None})
            self._log({"type": "assistant", "attempt": attempt, "message": assistant_msg})

            if assistant_msg["tool_calls"]:
                tc = assistant_msg["tool_calls"][0]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError as exc:
                    self._log({"type": "tool_call_error", "attempt": attempt, "error": f"Invalid JSON: {exc}"})
                    if attempt == 1:
                        messages.append({"role": "user", "content": f"Your submit_final_answer call had invalid JSON arguments: {exc}. Please retry with valid JSON."})
                        continue
                else:
                    self.final_answer = args
                    self.stop_reason = "submitted final answer"
                    break
            else:
                self._log({"type": "no_tool_call", "attempt": attempt, "content": assistant_msg["content"]})
                if attempt == 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You did not call submit_final_answer. Your previous response was:\n"
                                f"{assistant_msg['content']}\n"
                                "Call submit_final_answer now with that same information as structured arguments."
                            ),
                        }
                    )
                    continue
                # second failure: recover a best-effort answer from the free text
                parsed_terms = _parse_go_terms_from_text(assistant_msg["content"])
                self.final_answer = {
                    "function_summary": (assistant_msg["content"] or "")[:2000],
                    "go_terms": parsed_terms,
                    "disagreements_or_uncertainty": (
                        "Recovered from free text after the model failed to call submit_final_answer twice; "
                        "GO-ID/evidence-ID extraction is heuristic (regex-based), not a structured answer."
                    ),
                }
                self.recovered_from_text = True
                self.stop_reason = "recovered_from_text"
                self._log({"type": "recovered_from_text", "parsed_terms": parsed_terms})

        if self.stop_reason == "unknown":
            self.stop_reason = "failed to produce a final answer"

        return self._finish()

    def _finish(self) -> dict:
        token_usage = {
            "input": self.client.usage.input_tokens,
            "output": self.client.usage.output_tokens,
            "total": self.client.usage.total,
        }
        label = self.ctx.query_accession or "query"
        report = build_report(label, self.ctx.eval_mode, self.final_answer, self.ledger, token_usage, self.stop_reason)
        report["recovered_from_text"] = self.recovered_from_text
        report["aggregator_candidates"] = self.aggregator_candidates

        run_dir = RUNS_DIR / label / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "trace.json").write_text(json.dumps(self.trace, indent=2, default=str))
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
        (run_dir / "report.md").write_text(render_markdown(report))
        (run_dir / "metadata.json").write_text(json.dumps(capture_run_metadata(), indent=2, default=str))
        report["run_dir"] = str(run_dir)
        return report
