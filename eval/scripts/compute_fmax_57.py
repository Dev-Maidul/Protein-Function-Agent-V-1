"""Computes direct-match + CAFA-propagated Fmax with bootstrap 95% CI, per
method per GO aspect, over the predictions saved by baselines_57.py and
aggregator_only_57.py (run those first). Run from the project root:
`python -m eval.scripts.compute_fmax_57`.
Writes eval/runs/fmax_57_fixed_metrics.json."""
import json
from eval.metrics import build_ancestor_cache, fmax_both, fmax_bootstrap_ci
from eval.run_eval import ASPECTS, _filter_preds_by_aspect, _ground_truth_by_aspect

test_set = json.load(open("eval/test_set.json"))
gt_by_aspect = _ground_truth_by_aspect(test_set)

baselines = json.load(open("eval/runs/baselines_57_fixed_results.json"))
aggregator = json.load(open("eval/runs/aggregator_only_57_results.json"))

predictions = {
    "diamond_transfer": baselines["predictions"]["diamond"],
    "protrek_transfer": baselines["predictions"]["protrek"],
    "llm_no_tools": baselines["predictions"]["llm_no_tools"],
    "aggregator_only": aggregator["done"],
}

all_go_ids = set()
for method_preds in predictions.values():
    for terms in method_preds.values():
        all_go_ids.update(t["go_id"] for t in terms)
for aspect_gt in gt_by_aspect.values():
    for terms in aspect_gt.values():
        all_go_ids.update(terms)

ANCESTOR_CACHE_PATH = "eval/runs/ancestor_cache.json"
ASPECT_CACHE_PATH = "eval/runs/aspect_cache.json"
cached = json.loads(open(ANCESTOR_CACHE_PATH).read()) if __import__("os").path.exists(ANCESTOR_CACHE_PATH) else {}
missing = all_go_ids - set(cached)
print(f"fetching ancestors for {len(missing)} new GO IDs (cache had {len(cached)}) ...", flush=True)
if missing:
    cached.update({k: list(v) for k, v in build_ancestor_cache(missing).items()})
    json.dump(cached, open(ANCESTOR_CACHE_PATH, "w"), indent=2)
ancestor_cache = {k: set(v) for k, v in cached.items()}

aspect_cache = json.loads(open(ASPECT_CACHE_PATH).read()) if __import__("os").path.exists(ASPECT_CACHE_PATH) else {}

print("computing Fmax + bootstrap CI per method per aspect ...", flush=True)
metrics = {}
for method, preds in predictions.items():
    preds_by_aspect = {acc: _filter_preds_by_aspect(terms, aspect_cache) for acc, terms in preds.items()}
    metrics[method] = {}
    for aspect in ASPECTS:
        aspect_preds = {acc: preds_by_aspect.get(acc, {}).get(aspect, []) for acc in gt_by_aspect[aspect]}
        m = fmax_both(aspect_preds, gt_by_aspect[aspect], ancestor_cache)
        m["direct"]["ci95"] = fmax_bootstrap_ci(aspect_preds, gt_by_aspect[aspect], ancestor_cache=None, n_boot=1000)
        m["propagated"]["ci95"] = fmax_bootstrap_ci(aspect_preds, gt_by_aspect[aspect], ancestor_cache=ancestor_cache, n_boot=1000)
        n_proteins_total = len(gt_by_aspect[aspect])
        n_with_truth = len([p for p, t in gt_by_aspect[aspect].items() if t])
        n_with_pred = len([p for p in gt_by_aspect[aspect] if gt_by_aspect[aspect][p] and aspect_preds.get(p)])
        m["n_proteins_with_ground_truth"] = n_with_truth
        m["n_with_at_least_one_prediction"] = n_with_pred
        metrics[method][aspect] = m
        print(f"  {method} / {aspect}: direct={m['direct']['fmax']:.3f} ci={m['direct']['ci95']} prop={m['propagated']['fmax']:.3f} ci={m['propagated']['ci95']}", flush=True)

json.dump(aspect_cache, open(ASPECT_CACHE_PATH, "w"), indent=2)
json.dump(metrics, open("eval/runs/fmax_57_fixed_metrics.json", "w"), indent=2, default=str)
print("DONE")
