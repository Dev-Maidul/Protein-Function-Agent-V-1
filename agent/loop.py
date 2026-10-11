"""The agent loop: LLM decides which tool to call, we validate the call's
JSON arguments against that tool's schema, dispatch it, feed the result
back, and repeat until the model calls `submit_final_answer` or a budget
(MAX_STEPS / MAX_TOKENS_PER_RUN) is hit. Every tool result is logged to an
evidence ledger (E1, E2, ...) that the final report cites by ID.

An invalid tool call (bad JSON, or JSON that fails its schema) is never
silently dropped -- the error is both returned to the model (as a tool-role
message, so it can retry) and recorded in the trace (CLAUDE.md: never hide
failures).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from agent.dispatch import AgentContext, dispatch
from agent.llm_client import LLMClient
from agent.report import build_report, render_markdown
from agent.tool_schemas import ALL_SCHEMAS, build_tools_list

PROJECT_ROOT = Path(__file__).parent.parent
RUNS_DIR = PROJECT_ROOT / "runs"

SYSTEM_PROMPT = """You are a protein function annotation agent. Given a protein, you must decide \
which bioinformatics tools to call to gather evidence, then give a final answer with GO term \
predictions. You have a limited number of steps -- use them efficiently.

Evidence strength rules (follow strictly):
- Homology (diamond_blast_search) and domain matches (interproscan_scan) are STRONG evidence.
- ProTrek (protrek_retrieve) is WEAK evidence only: a high-scoring match may be a pathway member \
or interaction partner of the query protein, not the query's own function. Never state a ProTrek \
result as the query's function without corroboration from homology or domain evidence.
- ESM-2 (esm2_knn_search) cosine-similarity values are a RANKING ONLY, never an absolute confidence \
or probability. If a neighbour has is_hub=true, down-weight it and say so explicitly in your final \
disagreements_or_uncertainty field.
- Validate every GO ID you plan to report with quickgo_validate before submitting it.
- ONLY report a GO ID that actually appeared in a tool's output (e.g. one of interproscan_scan's \
domain GO cross-references, or uniprot_lookup's annotated terms). NEVER report a GO ID purely from \
your own background knowledge about this protein family, even if you are confident it is correct -- \
if no tool surfaced it, you have no evidence trail for it and it will be flagged as ungrounded. \
A real example of this going wrong: for human beta-actin, a model reported GO:0005576 \
"extracellular region" and GO:0006973 "intracellular accumulation of glycerol" with 0.95 confidence \
-- both valid GO IDs, but neither came from any tool call, and both are wrong for a cytoplasmic protein.
- Every claim in function_summary must cite an evidence ID (E1, E2, ...) in parentheses.
- When you have enough evidence, call submit_final_answer. Do not keep calling tools indefinitely.

CRITICAL: you must ALWAYS respond with a function/tool call. NEVER write your final answer as plain \
text in the message content -- that answer will be discarded and the run will fail. Once you decide \
you are ready to answer, your very next response must be a call to submit_final_answer with the \
function_summary/go_terms/disagreements_or_uncertainty arguments filled in -- not a text message \
describing what you would answer.
"""


def _user_prompt(ctx: AgentContext) -> str:
    parts = [f"Protein sequence ({len(ctx.query_sequence)} aa):\n{ctx.query_sequence}"]
    if ctx.query_accession:
        parts.append(f"\n(Its UniProt accession is {ctx.query_accession} -- do not assume you already know its function; verify with tools.)")
    if ctx.eval_mode:
        parts.append(
            "\nEVALUATION MODE: uniprot_lookup is unavailable. The query's own accession and any "
            "close homolog have already been excluded from protrek_retrieve/esm2_knn_search results."
        )
    return "\n".join(parts)


class AgentRun:
    def __init__(
        self,
        query_sequence: str,
        query_accession: str | None,
        eval_mode: bool,
        max_steps: int | None = None,
        client=None,
    ):
        self.ctx = AgentContext(query_sequence=query_sequence, query_accession=query_accession, eval_mode=eval_mode)
        self.client = client or LLMClient()
        self.max_steps = max_steps or int(os.environ.get("MAX_STEPS", "10"))
        self.ledger: list[dict] = []
        self.trace: list[dict] = []
        self.final_answer: dict | None = None
        self.stop_reason = "unknown"

    def _next_evidence_id(self) -> str:
        return f"E{len(self.ledger) + 1}"

    def _log(self, entry: dict) -> None:
        entry["t"] = time.monotonic()
        self.trace.append(entry)

    def run(self) -> dict:
        setup_evidence = self.ctx.build_exclusions()
        if setup_evidence is not None:
            self._log({"type": "setup_exclusion", "evidence": setup_evidence.to_dict(), "excluded": sorted(self.ctx.exclude_accessions)})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(self.ctx)},
        ]
        tools = build_tools_list(self.ctx.eval_mode)

        step = 0
        consecutive_no_tool_call = 0
        last_text_content = None
        for step in range(1, self.max_steps + 1):
            if self.client.budget_exceeded():
                self.stop_reason = f"MAX_TOKENS_PER_RUN ({self.client.max_tokens_per_run}) exceeded"
                self._log({"type": "stop", "reason": self.stop_reason})
                break

            assistant_msg = self.client.chat(messages, tools)
            messages.append({"role": "assistant", "content": assistant_msg["content"], "tool_calls": assistant_msg["tool_calls"] or None})
            self._log({"type": "assistant", "step": step, "message": assistant_msg})

            if not assistant_msg["tool_calls"]:
                # A known failure mode of small local models (documented by the user up front):
                # the model writes its final answer as plain text instead of actually calling
                # submit_final_answer, even with tool_choice="required" (verified live: Ollama/
                # qwen3:8b still ignored it). One nudge is given; if it fails again, the run stops
                # here instead of silently burning the rest of MAX_STEPS -- the failure is recorded
                # in the trace/report, never hidden, and the free text is kept as an UNVALIDATED
                # fallback summary (no GO terms -- those must come from a real submit_final_answer).
                last_text_content = assistant_msg["content"]
                consecutive_no_tool_call += 1
                self._log({"type": "no_tool_call", "step": step, "consecutive": consecutive_no_tool_call, "content": last_text_content})
                if consecutive_no_tool_call >= 2:
                    self.stop_reason = (
                        "model did not use tool-calling for its final answer after 2 attempts "
                        "(wrote plain text instead of calling submit_final_answer) -- stopped early "
                        "rather than burning the remaining step budget"
                    )
                    self._log({"type": "stop", "reason": self.stop_reason})
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You did not call a tool. You MUST respond with a function/tool call, never "
                            "plain text. If you are ready to answer, call submit_final_answer (not write "
                            "the answer as text)."
                        ),
                    }
                )
                continue

            consecutive_no_tool_call = 0
            for tc in assistant_msg["tool_calls"]:
                tool_msg = self._handle_tool_call(tc, step)
                messages.append(tool_msg)

            if self.final_answer is not None:
                self.stop_reason = "submitted final answer"
                break
        else:
            self.stop_reason = f"MAX_STEPS ({self.max_steps}) reached"
            self._log({"type": "stop", "reason": self.stop_reason})

        if self.final_answer is None and self.stop_reason == "unknown":
            self.stop_reason = f"MAX_STEPS ({self.max_steps}) reached"

        return self._finish(step, last_text_content)

    def _handle_tool_call(self, tc: dict, step: int) -> dict:
        name = tc["function"]["name"]
        raw_args = tc["function"]["arguments"]

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError as exc:
            error = f"Invalid JSON in tool-call arguments: {exc}"
            self._log({"type": "tool_call_error", "step": step, "tool": name, "error": error, "raw_arguments": raw_args})
            return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"error": error})}

        schema = ALL_SCHEMAS.get(name)
        if schema is None:
            error = f"Unknown tool name: {name}"
            self._log({"type": "tool_call_error", "step": step, "tool": name, "error": error})
            return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"error": error})}

        try:
            jsonschema.validate(args, schema["function"]["parameters"])
        except jsonschema.ValidationError as exc:
            error = f"Arguments for {name} failed schema validation: {exc.message}"
            self._log({"type": "tool_call_error", "step": step, "tool": name, "error": error, "arguments": args})
            return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"error": error})}

        if name == "submit_final_answer":
            self.final_answer = args
            self._log({"type": "final_answer", "step": step, "args": args})
            return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"status": "received"})}

        evidence = dispatch(name, args, self.ctx)
        eid = self._next_evidence_id()
        self.ledger.append({"id": eid, "tool": name, "args": args, "evidence": evidence.to_dict()})
        self._log({"type": "tool_result", "step": step, "id": eid, "tool": name, "evidence": evidence.to_dict()})

        if evidence.error:
            content = {"evidence_id": eid, "error": evidence.error}
        else:
            content = {"evidence_id": eid, "summary": evidence.result_summary, "raw_scores": evidence.raw_scores}
        return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(content, default=str)[:8000]}

    def _finish(self, steps_used: int, fallback_text: str | None = None) -> dict:
        token_usage = {
            "input": self.client.usage.input_tokens,
            "output": self.client.usage.output_tokens,
            "total": self.client.usage.total,
        }
        label = self.ctx.query_accession or "query"
        report = build_report(label, self.ctx.eval_mode, self.final_answer, self.ledger, token_usage, self.stop_reason, fallback_text)
        report["steps_used"] = steps_used

        run_dir = RUNS_DIR / label / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "trace.json").write_text(json.dumps(self.trace, indent=2, default=str))
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
        (run_dir / "report.md").write_text(render_markdown(report))
        report["run_dir"] = str(run_dir)
        return report
