"""Audits the 2026-10-30 DIAMOND exclusion-cap leakage bug across all 57
held-out test proteins: compares the OLD (pre-fix, top_n=10) exclusion set
against the NEW (fixed, top_n=5000) one. DIAMOND only, no LLM, no other
tool. Run from the project root: `python -m eval.scripts.leakage_audit_57`.
Writes eval/leakage_audit_57.csv."""
import json, csv
from agent.context import AgentContext, HOMOLOG_IDENTITY_THRESHOLD
from tools import diamond_blast

test_set = json.load(open("eval/test_set.json"))
rows = []

for i, entry in enumerate(test_set, 1):
    acc, seq = entry["accession"], entry["sequence"]

    # OLD behavior: replicate the pre-fix default (top_n=10, no exclude_accessions)
    old_ev = diamond_blast.run(seq, query_id=acc, top_n=10)
    old_exclude = {acc}
    if old_ev.error is None:
        for hit in old_ev.raw_scores.get("hits", []):
            if hit["pident"] >= HOMOLOG_IDENTITY_THRESHOLD:
                old_exclude.add(hit["uniprot_accession"])

    # NEW behavior: current (fixed) code, top_n=5000
    ctx = AgentContext(query_sequence=seq, query_accession=acc, eval_mode=True)
    ctx.build_exclusions()
    new_exclude = set(ctx.exclude_accessions)

    n_ge50_true = len(new_exclude)  # new exclusion set IS the true >=50% count (uncapped)
    n_missing = len(new_exclude - old_exclude)

    rows.append({
        "accession": acc,
        "n_ge50_hits": n_ge50_true,
        "old_exclusion_set_size": len(old_exclude),
        "new_exclusion_set_size": len(new_exclude),
        "n_previously_missing": n_missing,
    })
    print(f"[{i}/{len(test_set)}] {acc}: ge50={n_ge50_true} old={len(old_exclude)} new={len(new_exclude)} missing={n_missing}", flush=True)

with open("eval/leakage_audit_57.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["accession", "n_ge50_hits", "old_exclusion_set_size", "new_exclusion_set_size", "n_previously_missing"])
    writer.writeheader()
    writer.writerows(rows)

n_affected = sum(1 for r in rows if r["n_previously_missing"] > 0)
missing_vals = sorted(r["n_previously_missing"] for r in rows)
median = missing_vals[len(missing_vals)//2] if len(missing_vals) % 2 == 1 else (missing_vals[len(missing_vals)//2 - 1] + missing_vals[len(missing_vals)//2]) / 2
print(f"\n=== TOTALS: {n_affected}/{len(rows)} proteins affected. max missing={max(missing_vals)}, median missing={median} ===")
