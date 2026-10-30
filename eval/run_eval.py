"""Phase 3 evaluation runner. Loads a benchmark (the 20-protein DEV set,
eval/benchmark.json, or the 57-protein held-out TEST set, eval/test_set.json)
and runs up to 5 methods under the same leakage control on every protein:
diamond_transfer, protrek_transfer, llm_no_tools, aggregator_only, full_agent.
Scores each with both direct-match and GO-DAG-propagated CAFA-style Fmax per
aspect, and separately measures the ESM-2 hubness diagnostic with a 95%
Wilson CI and a Fisher exact test (never calls a result "confirmed" unless
the test is actually significant).

full_agent (and aggregator_only, which shares its expensive evidence
pipeline) can be restricted to a smaller seeded random subset via
--expensive-limit, since each full_agent call costs ~9.5 minutes on this
hardware -- the cheap methods still run on every protein. If restricted,
this is reported plainly in the output, never silently.

Never tunes anything on either benchmark -- both are read-only ground truth,
run once per method and reported as-is, including weak results.

Run manually:
    python -m eval.run_eval --dataset test --expensive-limit 15
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.context import AgentContext
from agent.run_metadata import capture_run_metadata
from eval.baselines import aggregator_only_from_candidates, diamond_transfer, full_agent, gather_once, llm_no_tools, protrek_transfer
from eval.metrics import build_ancestor_cache, fmax_both
from eval.stats import compare_hit_rates
from tools import esm2_knn, uniprot

PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / "eval" / "runs"
CHECKPOINT_PATH = PROJECT_ROOT / "eval" / "runs" / "in_progress_checkpoint.json"
ANCESTOR_CACHE_PATH = PROJECT_ROOT / "eval" / "runs" / "ancestor_cache.json"
ASPECT_CACHE_PATH = PROJECT_ROOT / "eval" / "runs" / "aspect_cache.json"

ASPECTS = ["molecular_function", "biological_process", "cellular_component"]
CHEAP_METHODS = ["diamond", "protrek", "llm_no_tools"]
EXPENSIVE_METHODS = ["aggregator_only", "full_agent"]
ALL_METHODS = CHEAP_METHODS + EXPENSIVE_METHODS


def _dataset_path(name: str) -> Path:
    return PROJECT_ROOT / "eval" / ("benchmark.json" if name == "dev" else "test_set.json")


def _ground_truth_by_aspect(benchmark: list[dict]) -> dict[str, dict[str, set[str]]]:
    gt = {aspect: {} for aspect in ASPECTS}
    for entry in benchmark:
        acc = entry["accession"]
        for aspect in ASPECTS:
            gt[aspect][acc] = {t["go_id"] for t in entry["experimental_go_terms"] if t["aspect"] == aspect}
    return gt


def _filter_preds_by_aspect(preds: list[dict], aspect_cache: dict[str, str | None]) -> dict[str, list[dict]]:
    from tools import quickgo

    by_aspect = {aspect: [] for aspect in ASPECTS}
    for p in preds:
        go_id = p["go_id"]
        if go_id not in aspect_cache:
            ev = quickgo.run(go_id)
            aspect_cache[go_id] = ev.raw_scores.get("aspect") if not ev.error else None
        aspect = aspect_cache[go_id]
        if aspect in by_aspect:
            by_aspect[aspect].append(p)
    return by_aspect


def run_hubness_diagnostic(benchmark: list[dict], ctxs: dict[str, AgentContext]) -> dict:
    """For every benchmark protein, run ESM-2 kNN (same exclusions as the
    other methods) and check: for each returned neighbour, does ANY of its
    own GO terms overlap the query's ground truth? Compares hub vs non-hub
    hit rates with a 95% Wilson CI and a Fisher exact test -- "confirmed" is
    only claimed by the caller if significant_at_0.05 is True.

    ctxs is reused from the main loop when available (already has exclusions
    built), but on a resumed run where every protein was already checkpointed,
    the main loop never builds a ctx for anyone -- so this builds one itself
    rather than silently skipping (a real bug found in the first full run,
    where that silent skip produced n_hub=0/n_nonhub=0 for all 57 proteins)."""
    all_true_terms = {entry["accession"]: {t["go_id"] for t in entry["experimental_go_terms"]} for entry in benchmark}

    total_neighbours = 0
    hub_hits, hub_total = 0, 0
    nonhub_hits, nonhub_total = 0, 0

    for entry in benchmark:
        acc = entry["accession"]
        ctx = ctxs.get(acc)
        if ctx is None:
            ctx = AgentContext(query_sequence=entry["sequence"], query_accession=acc, eval_mode=True)
            ctx.build_exclusions()
            ctxs[acc] = ctx
        ev = esm2_knn.run(entry["sequence"], top_k=10, exclude_accessions=ctx.exclude_accessions or None)
        if ev.error:
            continue
        true_terms = all_true_terms[acc]
        for n in ev.raw_scores.get("neighbours", []):
            total_neighbours += 1
            neighbour_ev = uniprot.run(n["accession"])
            neighbour_terms = {g["go_id"] for g in neighbour_ev.raw_scores.get("go_terms", [])} if not neighbour_ev.error else set()
            is_hit = bool(neighbour_terms & true_terms)
            if n["is_hub"]:
                hub_total += 1
                hub_hits += int(is_hit)
            else:
                nonhub_total += 1
                nonhub_hits += int(is_hit)

    stats = compare_hit_rates(hub_hits, hub_total, nonhub_hits, nonhub_total)
    stats["total_neighbours_examined"] = total_neighbours
    stats["note"] = "hit = at least one of the neighbour's own GO terms overlaps the query's ground truth"
    return stats


def _load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text())
    return {
        "completed_accessions": {"cheap": [], "expensive": []},
        "predictions": {m: {} for m in ALL_METHODS},
        "runtimes": {m: [] for m in ALL_METHODS},
        "full_agent_reports": {},
    }


def _save_checkpoint(state: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(CHECKPOINT_PATH)


def _load_ancestor_cache() -> dict[str, list[str]]:
    if ANCESTOR_CACHE_PATH.exists():
        return json.loads(ANCESTOR_CACHE_PATH.read_text())
    return {}


def _save_ancestor_cache(cache: dict[str, set[str]]) -> None:
    ANCESTOR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANCESTOR_CACHE_PATH.write_text(json.dumps({k: sorted(v) for k, v in cache.items()}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["dev", "test"], default="test")
    parser.add_argument("--limit", type=int, default=None, help="cap total proteins evaluated (cheap methods included)")
    parser.add_argument(
        "--expensive-limit", type=int, default=None,
        help="run aggregator_only/full_agent on only this many proteins (seeded random subset) -- cheap methods still run on all. Omit to run on everything.",
    )
    parser.add_argument("--expensive-seed", type=int, default=7)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    benchmark_path = _dataset_path(args.dataset)
    benchmark = json.loads(benchmark_path.read_text())
    if args.limit:
        benchmark = benchmark[: args.limit]
    print(f"Evaluating on {len(benchmark)} proteins from {benchmark_path.name} ({args.dataset} set).")

    expensive_accs = {e["accession"] for e in benchmark}
    if args.expensive_limit and args.expensive_limit < len(benchmark):
        rng = random.Random(args.expensive_seed)
        expensive_accs = set(rng.sample([e["accession"] for e in benchmark], args.expensive_limit))
        print(
            f"NOTE: aggregator_only/full_agent restricted to a seeded random subset of "
            f"{len(expensive_accs)}/{len(benchmark)} proteins (seed={args.expensive_seed}) -- too slow to run on all."
        )

    if args.fresh and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
    state = _load_checkpoint()
    done_cheap = set(state["completed_accessions"]["cheap"])
    done_expensive = set(state["completed_accessions"]["expensive"])
    if done_cheap or done_expensive:
        print(f"Resuming from checkpoint: {len(done_cheap)} cheap, {len(done_expensive)} expensive already done.")

    gt_by_aspect = _ground_truth_by_aspect(benchmark)
    predictions = state["predictions"]
    runtimes = state["runtimes"]
    full_agent_reports = state["full_agent_reports"]
    ctxs: dict[str, AgentContext] = {}

    for i, entry in enumerate(benchmark, 1):
        acc, seq = entry["accession"], entry["sequence"]
        need_cheap = acc not in done_cheap
        need_expensive = acc in expensive_accs and acc not in done_expensive
        if not need_cheap and not need_expensive:
            print(f"[{i}/{len(benchmark)}] {acc} -- already done, skipping")
            continue
        print(f"[{i}/{len(benchmark)}] {acc} ({entry['length']} aa) ...")

        ctx = AgentContext(query_sequence=seq, query_accession=acc, eval_mode=True)
        ctx.build_exclusions()
        ctxs[acc] = ctx
        print(f"  excluded {len(ctx.exclude_accessions)} accession(s) (query + close homologs)")

        if need_cheap:
            t0 = time.monotonic()
            predictions["diamond"][acc] = diamond_transfer(seq, ctx)
            runtimes["diamond"].append(time.monotonic() - t0)

            t0 = time.monotonic()
            predictions["protrek"][acc] = protrek_transfer(seq, ctx)
            runtimes["protrek"].append(time.monotonic() - t0)

            t0 = time.monotonic()
            predictions["llm_no_tools"][acc] = llm_no_tools(seq)
            runtimes["llm_no_tools"].append(time.monotonic() - t0)

            done_cheap.add(acc)

        if need_expensive:
            t0 = time.monotonic()
            ledger, candidates = gather_once(ctx)
            gather_time = time.monotonic() - t0
            predictions["aggregator_only"][acc] = aggregator_only_from_candidates(candidates)
            runtimes["aggregator_only"].append(gather_time)

            t0 = time.monotonic()
            terms, report = full_agent(seq, acc, ctx=ctx, ledger=ledger, aggregator_candidates=candidates)
            runtimes["full_agent"].append(gather_time + (time.monotonic() - t0))
            predictions["full_agent"][acc] = terms
            full_agent_reports[acc] = {
                "stop_reason": report["stop_reason"],
                "recovered_from_text": report["recovered_from_text"],
                "token_usage": report["token_usage"],
                "n_go_terms": len(terms),
                "n_go_terms_ungrounded": len(report.get("go_terms_ungrounded", [])),
            }
            print(f"  full_agent: {report['stop_reason']} (recovered_from_text={report['recovered_from_text']}, {len(terms)} GO terms)")
            done_expensive.add(acc)

        _save_checkpoint(
            {
                "completed_accessions": {"cheap": sorted(done_cheap), "expensive": sorted(done_expensive)},
                "predictions": predictions,
                "runtimes": runtimes,
                "full_agent_reports": full_agent_reports,
            }
        )

    print("\nBuilding GO ancestor cache (for propagated Fmax) ...")
    all_go_ids: set[str] = set()
    for method_preds in predictions.values():
        for terms in method_preds.values():
            all_go_ids.update(t["go_id"] for t in terms)
    for aspect_gt in gt_by_aspect.values():
        for terms in aspect_gt.values():
            all_go_ids.update(terms)
    cached = _load_ancestor_cache()
    missing = all_go_ids - set(cached)
    if missing:
        print(f"  fetching ancestors for {len(missing)} new GO IDs (cache had {len(cached)}) ...")
        cached.update({k: list(v) for k, v in build_ancestor_cache(missing).items()})
        _save_ancestor_cache({k: set(v) for k, v in cached.items()})
    ancestor_cache = {k: set(v) for k, v in cached.items()}

    print("Computing Fmax (direct + propagated) per method per aspect ...")
    aspect_cache: dict[str, str | None] = json.loads(ASPECT_CACHE_PATH.read_text()) if ASPECT_CACHE_PATH.exists() else {}
    metrics = {}
    for method, preds in predictions.items():
        preds_by_aspect = {acc: _filter_preds_by_aspect(terms, aspect_cache) for acc, terms in preds.items()}
        metrics[method] = {}
        for aspect in ASPECTS:
            aspect_preds = {acc: preds_by_aspect.get(acc, {}).get(aspect, []) for acc in gt_by_aspect[aspect]}
            metrics[method][aspect] = fmax_both(aspect_preds, gt_by_aspect[aspect], ancestor_cache)
    ASPECT_CACHE_PATH.write_text(json.dumps(aspect_cache, indent=2))

    print("Running ESM-2 hubness diagnostic ...")
    hubness_diagnostic = run_hubness_diagnostic(benchmark, ctxs)

    run_dir = RESULTS_DIR / f"{args.dataset}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "predictions.json").write_text(json.dumps(predictions, indent=2, default=str))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    (run_dir / "full_agent_reports.json").write_text(json.dumps(full_agent_reports, indent=2, default=str))
    (run_dir / "hubness_diagnostic.json").write_text(json.dumps(hubness_diagnostic, indent=2, default=str))
    (run_dir / "runtimes.json").write_text(json.dumps(runtimes, indent=2, default=str))
    (run_dir / "run_config.json").write_text(json.dumps(vars(args), indent=2, default=str))
    (run_dir / "metadata.json").write_text(json.dumps(capture_run_metadata(), indent=2, default=str))
    CHECKPOINT_PATH.unlink(missing_ok=True)

    print(f"\nSaved results -> {run_dir}")
    print("\n=== Fmax summary (direct | propagated) ===")
    for method in metrics:
        print(f"\n{method}:")
        for aspect in ASPECTS:
            d, p = metrics[method][aspect]["direct"], metrics[method][aspect]["propagated"]
            print(f"  {aspect}: direct Fmax={d['fmax']:.3f} (cov={d['coverage']:.2f})  |  propagated Fmax={p['fmax']:.3f} (cov={p['coverage']:.2f})")

    print("\n=== Hubness diagnostic (with significance test) ===")
    print(json.dumps(hubness_diagnostic, indent=2))
    if hubness_diagnostic["n_hub"] < 30 or hubness_diagnostic["n_nonhub"] < 30:
        print(f"CAVEAT: small sample (n_hub={hubness_diagnostic['n_hub']}, n_nonhub={hubness_diagnostic['n_nonhub']}) -- treat the CI/p-value cautiously.")
    print(f"Statistically significant at p<0.05: {hubness_diagnostic['significant_at_0.05']}")

    print("\n=== Avg runtime per protein ===")
    for method, times in runtimes.items():
        avg = sum(times) / len(times) if times else 0
        print(f"  {method}: {avg:.1f}s avg (n={len(times)})")


if __name__ == "__main__":
    main()
