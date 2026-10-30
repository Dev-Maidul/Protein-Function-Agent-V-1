"""Runs diamond_transfer, protrek_transfer, and llm_no_tools on all 57
held-out test proteins with the fixed DIAMOND exclusion set. llm_no_tools
needs a running Ollama server (`ollama serve`). Checkpointed per protein --
safe to kill and re-run. Run from the project root:
`python -m eval.scripts.baselines_57`.
Writes eval/runs/baselines_57_fixed_results.json."""
import json, time
from pathlib import Path
from agent.context import AgentContext
from eval.baselines import diamond_transfer, protrek_transfer, llm_no_tools

CHECKPOINT = Path("eval/runs/baselines_57_checkpoint.json")
test_set = json.load(open("eval/test_set.json"))

state = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {
    "predictions": {"diamond": {}, "protrek": {}, "llm_no_tools": {}},
    "runtimes": {"diamond": {}, "protrek": {}, "llm_no_tools": {}},
    "errors": {"diamond": {}, "protrek": {}, "llm_no_tools": {}},
    "done": {},
}

for i, entry in enumerate(test_set, 1):
    acc, seq = entry["accession"], entry["sequence"]
    if state["done"].get(acc):
        print(f"[{i}/{len(test_set)}] {acc} -- already done, skipping", flush=True)
        continue

    ctx = AgentContext(query_sequence=seq, query_accession=acc, eval_mode=True)
    ctx.build_exclusions()

    t0 = time.monotonic()
    try:
        d_preds = diamond_transfer(seq, ctx)
        state["predictions"]["diamond"][acc] = d_preds
    except Exception as e:
        state["errors"]["diamond"][acc] = str(e)
        d_preds = []
    dt_d = time.monotonic() - t0
    state["runtimes"]["diamond"][acc] = round(dt_d, 1)

    t0 = time.monotonic()
    try:
        p_preds = protrek_transfer(seq, ctx)
        state["predictions"]["protrek"][acc] = p_preds
    except Exception as e:
        state["errors"]["protrek"][acc] = str(e)
        p_preds = []
    dt_p = time.monotonic() - t0
    state["runtimes"]["protrek"][acc] = round(dt_p, 1)

    t0 = time.monotonic()
    try:
        l_preds = llm_no_tools(seq)
        state["predictions"]["llm_no_tools"][acc] = l_preds
    except Exception as e:
        state["errors"]["llm_no_tools"][acc] = str(e)
        l_preds = []
    dt_l = time.monotonic() - t0
    state["runtimes"]["llm_no_tools"][acc] = round(dt_l, 1)

    state["done"][acc] = True
    CHECKPOINT.write_text(json.dumps(state, indent=2))
    print(f"[{i}/{len(test_set)}] {acc}: diamond={dt_d:.1f}s({len(d_preds)}) protrek={dt_p:.1f}s({len(p_preds)}) llm={dt_l:.1f}s({len(l_preds)})", flush=True)

json.dump(state, open("eval/runs/baselines_57_fixed_results.json", "w"), indent=2)
print("\n=== DONE ===")
for m in ["diamond", "protrek", "llm_no_tools"]:
    rt = list(state["runtimes"][m].values())
    errs = len(state["errors"][m])
    print(f"{m}: avg={sum(rt)/len(rt):.1f}s, errors={errs}")
