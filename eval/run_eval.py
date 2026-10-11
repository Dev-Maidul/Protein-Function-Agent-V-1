"""Phase 3 evaluation runner: loads eval/benchmark.json, runs all 4 baselines
under the same leakage control on every protein, scores them with CAFA-style
Fmax per GO aspect, and separately measures how often the ESM-2 hubness flag
fires and whether hub-flagged neighbours are less reliable evidence.

Never tunes anything on this benchmark -- it is read-only ground truth, run
once per baseline and reported as-is, including weak results.

Run manually:
    python -m eval.run_eval [--limit N] [--max-steps 10]
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.dispatch import AgentContext
from eval.baselines import diamond_transfer, full_agent, llm_no_tools, protrek_transfer
from eval.metrics import fmax
from tools import esm2_knn, uniprot

PROJECT_ROOT = Path(__file__).parent.parent
BENCHMARK_PATH = PROJECT_ROOT / "eval" / "benchmark.json"
RESULTS_DIR = PROJECT_ROOT / "eval" / "runs"
CHECKPOINT_PATH = PROJECT_ROOT / "eval" / "runs" / "in_progress_checkpoint.json"

ASPECTS = ["molecular_function", "biological_process", "cellular_component"]


def _ground_truth_by_aspect(benchmark: list[dict]) -> dict[str, dict[str, set[str]]]:
    gt = {aspect: {} for aspect in ASPECTS}
    for entry in benchmark:
        acc = entry["accession"]
        for aspect in ASPECTS:
            gt[aspect][acc] = {t["go_id"] for t in entry["experimental_go_terms"] if t["aspect"] == aspect}
    return gt


def _filter_preds_by_aspect(preds: list[dict]) -> dict[str, list[dict]]:
    """We don't know a predicted GO ID's aspect without a QuickGO lookup;
    resolve it once per unique ID (cached) so the same term isn't re-queried
    across proteins."""
    from tools import quickgo

    cache: dict[str, str | None] = {}
    by_aspect = {aspect: [] for aspect in ASPECTS}
    for p in preds:
        go_id = p["go_id"]
        if go_id not in cache:
            ev = quickgo.run(go_id)
            cache[go_id] = ev.raw_scores.get("aspect") if not ev.error else None
        aspect = cache[go_id]
        if aspect in by_aspect:
            by_aspect[aspect].append(p)
    return by_aspect


def run_hubness_diagnostic(benchmark: list[dict], ctxs: dict[str, AgentContext]) -> dict:
    """For every benchmark protein, run ESM-2 kNN (same exclusions as the
    other baselines) and check: for each returned neighbour, does ANY of its
    own GO terms overlap the query's ground truth? Compare the hit rate for
    hub-flagged vs non-hub neighbours -- this is the direct answer to
    "are hub-flagged neighbours less reliable evidence"."""
    gt_by_aspect = _ground_truth_by_aspect(benchmark)
    all_true_terms = {
        entry["accession"]: {t["go_id"] for t in entry["experimental_go_terms"]} for entry in benchmark
    }

    total_neighbours = 0
    total_hub = 0
    hub_hits, hub_total = 0, 0
    nonhub_hits, nonhub_total = 0, 0

    for entry in benchmark:
        acc = entry["accession"]
        ctx = ctxs[acc]
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
                total_hub += 1
                hub_total += 1
                hub_hits += int(is_hit)
            else:
                nonhub_total += 1
                nonhub_hits += int(is_hit)

    return {
        "total_neighbours_examined": total_neighbours,
        "hub_flag_rate": total_hub / total_neighbours if total_neighbours else 0.0,
        "hub_neighbour_hit_rate": hub_hits / hub_total if hub_total else None,
        "nonhub_neighbour_hit_rate": nonhub_hits / nonhub_total if nonhub_total else None,
        "note": "hit = at least one of the neighbour's own GO terms overlaps the query's ground truth",
    }


def _load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text())
    return {
        "completed_accessions": [],
        "predictions": {"diamond": {}, "protrek": {}, "llm_no_tools": {}, "full_agent": {}},
        "runtimes": {"diamond": [], "protrek": [], "llm_no_tools": [], "full_agent": []},
        "full_agent_reports": {},
    }


def _save_checkpoint(state: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(CHECKPOINT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--fresh", action="store_true", help="ignore any existing checkpoint and start over")
    args = parser.parse_args()

    benchmark = json.loads(BENCHMARK_PATH.read_text())
    if args.limit:
        benchmark = benchmark[: args.limit]
    print(f"Evaluating on {len(benchmark)} proteins.")

    if args.fresh and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
    state = _load_checkpoint()
    done = set(state["completed_accessions"])
    if done:
        print(f"Resuming from checkpoint: {len(done)}/{len(benchmark)} proteins already done.")

    gt_by_aspect = _ground_truth_by_aspect(benchmark)
    predictions = state["predictions"]
    runtimes = state["runtimes"]
    full_agent_reports = state["full_agent_reports"]
    ctxs: dict[str, AgentContext] = {}

    for i, entry in enumerate(benchmark, 1):
        acc, seq = entry["accession"], entry["sequence"]
        if acc in done:
            print(f"[{i}/{len(benchmark)}] {acc} -- already done, skipping")
            ctx = AgentContext(query_sequence=seq, query_accession=acc, eval_mode=True)
            ctx.build_exclusions()  # cheap (DIAMOND only); needed below for the hubness diagnostic
            ctxs[acc] = ctx
            continue
        print(f"[{i}/{len(benchmark)}] {acc} ({entry['length']} aa) ...")

        ctx = AgentContext(query_sequence=seq, query_accession=acc, eval_mode=True)
        setup_ev = ctx.build_exclusions()
        ctxs[acc] = ctx
        print(f"  excluded {len(ctx.exclude_accessions)} accession(s) (query + close homologs)")

        t0 = time.monotonic()
        predictions["diamond"][acc] = diamond_transfer(seq, ctx)
        runtimes["diamond"].append(time.monotonic() - t0)

        t0 = time.monotonic()
        predictions["protrek"][acc] = protrek_transfer(seq, ctx)
        runtimes["protrek"].append(time.monotonic() - t0)

        t0 = time.monotonic()
        predictions["llm_no_tools"][acc] = llm_no_tools(seq)
        runtimes["llm_no_tools"].append(time.monotonic() - t0)

        t0 = time.monotonic()
        terms, report = full_agent(seq, acc, max_steps=args.max_steps)
        runtimes["full_agent"].append(time.monotonic() - t0)
        predictions["full_agent"][acc] = terms
        full_agent_reports[acc] = {
            "stop_reason": report["stop_reason"],
            "steps_used": report["steps_used"],
            "token_usage": report["token_usage"],
            "n_go_terms": len(terms),
            "n_go_terms_ungrounded": len(report.get("go_terms_ungrounded", [])),
        }
        print(f"  full_agent: {report['stop_reason']} ({report['steps_used']} steps, {len(terms)} GO terms)")

        done.add(acc)
        _save_checkpoint(
            {
                "completed_accessions": sorted(done),
                "predictions": predictions,
                "runtimes": runtimes,
                "full_agent_reports": full_agent_reports,
            }
        )

    print("\nComputing Fmax per method per aspect ...")
    metrics = {}
    for method, preds in predictions.items():
        preds_by_aspect = {acc: _filter_preds_by_aspect(terms) for acc, terms in preds.items()}
        metrics[method] = {}
        for aspect in ASPECTS:
            aspect_preds = {acc: preds_by_aspect[acc][aspect] for acc in preds_by_aspect}
            metrics[method][aspect] = fmax(aspect_preds, gt_by_aspect[aspect])

    print("Running ESM-2 hubness diagnostic ...")
    hubness_diagnostic = run_hubness_diagnostic(benchmark, ctxs)

    run_dir = RESULTS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "predictions.json").write_text(json.dumps(predictions, indent=2, default=str))
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    (run_dir / "full_agent_reports.json").write_text(json.dumps(full_agent_reports, indent=2, default=str))
    (run_dir / "hubness_diagnostic.json").write_text(json.dumps(hubness_diagnostic, indent=2, default=str))
    (run_dir / "runtimes.json").write_text(json.dumps(runtimes, indent=2, default=str))
    CHECKPOINT_PATH.unlink(missing_ok=True)  # full run succeeded, no longer need the resume checkpoint

    print(f"\nSaved results -> {run_dir}")
    print("\n=== Fmax summary ===")
    for method in metrics:
        print(f"\n{method}:")
        for aspect in ASPECTS:
            m = metrics[method][aspect]
            print(f"  {aspect}: Fmax={m['fmax']:.3f} (P={m['precision']:.3f} R={m['recall']:.3f} cov={m['coverage']:.2f} @tau={m['threshold']})")

    print("\n=== Hubness diagnostic ===")
    print(json.dumps(hubness_diagnostic, indent=2))

    print("\n=== Avg runtime per protein ===")
    for method, times in runtimes.items():
        avg = sum(times) / len(times) if times else 0
        print(f"  {method}: {avg:.1f}s avg")


if __name__ == "__main__":
    main()
