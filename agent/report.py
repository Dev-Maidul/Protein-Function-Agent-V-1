"""Builds the final JSON + Markdown report from the evidence ledger and the
model's `submit_final_answer` payload.

Two independent checks run on every predicted GO term, regardless of what
the model itself already did:

1. VALIDITY (tools/quickgo.py) -- is this a real, non-obsolete GO ID at all.
2. GROUNDING -- did this GO ID actually appear in the raw output of a tool
   the agent called (e.g. one of InterProScan's domain GO cross-references),
   or is the model just reporting a GO ID from its own background knowledge
   with no evidence trail behind it. A real bug this check was added for:
   a live run on human beta-actin (P60709) had the model confidently (0.95)
   report GO:0005576 "extracellular region" and GO:0006973 "intracellular
   accumulation of glycerol" for a cytoplasmic protein -- both valid GO IDs
   (passed check 1) but neither ever appeared in any tool's output, i.e.
   check 2 would have caught them. Only a term that passes BOTH checks is
   treated as a trustworthy prediction; valid-but-ungrounded terms are kept
   in the report (never silently dropped) but moved to a separate section so
   they cannot be mistaken for evidence-backed predictions.
"""
from __future__ import annotations

from tools import quickgo


def _grounded_go_ids(ledger: list[dict]) -> set[str]:
    """Every GO ID that actually appeared in some tool's raw output (not the
    model's own text) -- currently from interproscan_scan's domain
    GO-cross-references and uniprot_lookup's annotated GO terms, both of
    which put GO IDs in Evidence.source_ids (see tools/interproscan.py,
    tools/uniprot.py)."""
    grounded = set()
    for entry in ledger:
        for sid in entry["evidence"].get("source_ids", []):
            if isinstance(sid, str) and sid.startswith("GO:"):
                grounded.add(sid)
    return grounded


def validate_go_terms(go_terms: list[dict], ledger: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    grounded = _grounded_go_ids(ledger)
    kept, ungrounded, dropped = [], [], []
    for term in go_terms:
        ev = quickgo.run(term["go_id"])
        is_valid = ev.error is None and ev.raw_scores.get("valid", False)
        entry = dict(term)
        if not is_valid:
            entry["reason"] = ev.error or "QuickGO marked this ID invalid/obsolete"
            dropped.append(entry)
            continue

        entry["name"] = ev.raw_scores.get("name")
        entry["aspect"] = ev.raw_scores.get("aspect")
        if term["go_id"] in grounded:
            kept.append(entry)
        else:
            entry["reason"] = "valid GO ID, but never appeared in any tool's raw output -- not evidence-grounded"
            ungrounded.append(entry)
    return kept, ungrounded, dropped


def build_report(
    protein_label: str,
    eval_mode: bool,
    final_answer: dict | None,
    ledger: list[dict],
    token_usage: dict,
    stop_reason: str,
    fallback_text: str | None = None,
) -> dict:
    if final_answer is None:
        go_terms_kept, go_terms_ungrounded, go_terms_dropped = [], [], []
        if fallback_text:
            # The model wrote its answer as plain text instead of calling
            # submit_final_answer (a known small-local-model failure mode --
            # see agent/loop.py). Surface it, clearly labeled as UNVALIDATED:
            # no GO terms are extracted from free text, only from a real
            # structured submit_final_answer call.
            function_summary = f"[UNVALIDATED -- model did not call submit_final_answer] {fallback_text}"
        else:
            function_summary = "(agent did not submit a final answer -- see stop_reason)"
        disagreements = f"Run stopped early: {stop_reason}"
    else:
        go_terms_kept, go_terms_ungrounded, go_terms_dropped = validate_go_terms(final_answer.get("go_terms", []), ledger)
        function_summary = final_answer.get("function_summary", "")
        disagreements = final_answer.get("disagreements_or_uncertainty", "")

    evidence_table = [
        {
            "id": entry["id"],
            "tool": entry["tool"],
            "args": entry["args"],
            "summary": entry["evidence"]["result_summary"],
            "error": entry["evidence"]["error"],
            "source_urls": entry["evidence"]["source_urls"],
        }
        for entry in ledger
    ]

    return {
        "protein": protein_label,
        "eval_mode": eval_mode,
        "stop_reason": stop_reason,
        "function_summary": function_summary,
        "go_terms": go_terms_kept,
        "go_terms_ungrounded": go_terms_ungrounded,
        "go_terms_dropped_invalid": go_terms_dropped,
        "evidence_table": evidence_table,
        "disagreements_or_uncertainty": disagreements,
        "token_usage": token_usage,
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"# Function report: {report['protein']}",
        "",
        f"*Evaluation mode: {report['eval_mode']} | Stop reason: {report['stop_reason']}*",
        "",
        "## Function summary",
        report["function_summary"],
        "",
        "## Predicted GO terms",
    ]
    if report["go_terms"]:
        lines.append("| GO ID | Name | Aspect | Confidence | Evidence |")
        lines.append("|---|---|---|---|---|")
        for t in report["go_terms"]:
            lines.append(
                f"| {t['go_id']} | {t.get('name', '?')} | {t.get('aspect', '?')} | "
                f"{t['confidence']:.2f} | {', '.join(t['evidence_ids'])} |"
            )
    else:
        lines.append("_(none)_")

    if report["go_terms_ungrounded"]:
        lines.append("")
        lines.append("### ⚠️ Valid GO IDs, but NOT evidence-grounded (not in any tool's raw output -- treat as unreliable)")
        lines.append("| GO ID | Name | Aspect | Confidence (claimed) | Evidence (claimed) |")
        lines.append("|---|---|---|---|---|")
        for t in report["go_terms_ungrounded"]:
            lines.append(
                f"| {t['go_id']} | {t.get('name', '?')} | {t.get('aspect', '?')} | "
                f"{t['confidence']:.2f} | {', '.join(t['evidence_ids'])} |"
            )

    if report["go_terms_dropped_invalid"]:
        lines.append("")
        lines.append("### GO terms dropped (failed QuickGO validation)")
        for t in report["go_terms_dropped_invalid"]:
            lines.append(f"- `{t['go_id']}` -- {t['reason']}")

    lines += ["", "## Evidence table", "| ID | Tool | Summary | Error |", "|---|---|---|---|"]
    for e in report["evidence_table"]:
        summary = (e["summary"] or "").replace("|", "/")[:200]
        lines.append(f"| {e['id']} | {e['tool']} | {summary} | {e['error'] or ''} |")

    lines += [
        "",
        "## Disagreement / uncertainty",
        report["disagreements_or_uncertainty"] or "_(none)_",
        "",
        "## Token usage",
        f"input={report['token_usage']['input']}, output={report['token_usage']['output']}, "
        f"total={report['token_usage']['total']}",
    ]
    return "\n".join(lines)
