"""Runs aggregator_only (deterministic evidence pipeline, no LLM) on all 57
held-out test proteins with the fixed DIAMOND exclusion set. Checkpointed
per protein -- safe to kill and re-run. Run from the project root:
`python -m eval.scripts.aggregator_only_57`.
Writes eval/runs/aggregator_only_57_results.json."""
import json, time, os
from pathlib import Path
from agent.context import AgentContext
from eval.baselines import gather_once, aggregator_only_from_candidates

CHECKPOINT = Path("eval/runs/aggregator_only_57_checkpoint.json")
test_set = json.load(open("eval/test_set.json"))

state = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": {}, "runtimes": {}}
done = state["done"]
runtimes = state["runtimes"]

for i, entry in enumerate(test_set, 1):
    acc, seq = entry["accession"], entry["sequence"]
    if acc in done:
        print(f"[{i}/{len(test_set)}] {acc} -- already done, skipping", flush=True)
        continue

    ctx = AgentContext(query_sequence=seq, query_accession=acc, eval_mode=True)
    ctx.build_exclusions()
    t0 = time.monotonic()
    ledger, candidates = gather_once(ctx)
    dt = time.monotonic() - t0
    preds = aggregator_only_from_candidates(candidates)

    done[acc] = preds
    runtimes[acc] = round(dt, 1)
    CHECKPOINT.write_text(json.dumps(state, indent=2))
    print(f"[{i}/{len(test_set)}] {acc}: {dt:.1f}s, {len(preds)} candidates", flush=True)

json.dump(state, open("eval/runs/aggregator_only_57_results.json", "w"), indent=2)
avg = sum(runtimes.values()) / len(runtimes)
print(f"\n=== DONE: {len(done)}/{len(test_set)} proteins. avg time/protein: {avg:.1f}s ===")
