# Phase 3 Evaluation

This directory implements a small, honest evaluation of the protein-function agent against three simpler baselines, with leakage control so no method can simply look up the answer.

## Benchmark (`build_benchmark.py` → `benchmark.json`)

- **Why these proteins:** CAFA-style benchmarks score predictions against *experimental* GO evidence only — computational/inferred annotations (IEA, etc.) don't count as ground truth, since they're themselves predictions. We additionally prefer *recently* annotated proteins, since an LLM is less likely to have memorized a GO assignment made after (or close to) its training cutoff.
- **Selection method:**
  1. Query QuickGO's annotation-search API (`https://www.ebi.ac.uk/QuickGO/services/annotation/search`) for the six experimental evidence codes — EXP (ECO:0000269), IDA (ECO:0000314), IMP (ECO:0000315), IGI (ECO:0000316), IPI (ECO:0000353), IEP (ECO:0000270) — across 15 pages (200 rows each), with no taxon restriction. QuickGO is used instead of UniProt's own query language because UniProt doesn't expose evidence-code filtering directly.
  2. Group the rows by accession; keep every experimental GO term per protein (this is the ground-truth set used for Fmax) and the single most recent annotation date.
  3. Cross-check each candidate accession is reviewed (Swiss-Prot), not TrEMBL, via a batched UniProt query.
  4. Sort by most-recent-annotation-date descending, keep the top N with sequence length in [20, 1500] aa (so even the slower tools, like InterProScan, finish in reasonable time), and fetch the full sequence.
- **Reproduce:** `python -m eval.build_benchmark --n 20 --pages 15`
- **Caveat, stated honestly:** this is a convenience sample (recency-sorted, not a random sample of all experimentally-annotated proteins), and 15 QuickGO pages is a shallow scan of a dataset with >1.28M experimental annotation rows — it is NOT a representative sample of Swiss-Prot as a whole, only of *very recently annotated* proteins. That bias is exactly the point (fresher = less likely memorized), but it does mean this benchmark should not be read as "performance on typical Swiss-Prot proteins."

## Leakage control

For every benchmark protein, before any baseline runs: `agent.dispatch.AgentContext.build_exclusions()` runs DIAMOND once against the query sequence and builds an exclusion set = {the query's own accession} ∪ {every DIAMOND hit with ≥ 50% sequence identity} (threshold: `HOMOLOG_IDENTITY_THRESHOLD` in `agent/dispatch.py`, configurable). This same exclusion set is passed to every baseline that does retrieval against Swiss-Prot:
- `diamond_transfer`: the top DIAMOND hit used for GO transfer is never the query or an excluded homolog.
- `protrek_transfer`: ProTrek's candidate pool excludes those accessions.
- `full_agent`: runs in `eval_mode=True`, which additionally removes `uniprot_lookup` from the agent's tool list entirely (the ground-truth source itself must not be queryable).
- `llm_no_tools` has no database access at all, so there is nothing to exclude.

## The four baselines (`baselines.py`)

| Baseline | What it does | Evidence strength (per agent's own rules) |
|---|---|---|
| `diamond_transfer` | Classic BLAST-transfer: take the top non-excluded DIAMOND hit's own UniProt GO annotations, confidence = % identity | Strong |
| `protrek_transfer` | ProTrek retrieves top-10 non-excluded Swiss-Prot matches; each retrieved protein's GO terms are a "vote", confidence = fraction of the 10 hits carrying that term | Weak (pathway/interaction-partner risk) |
| `llm_no_tools` | One direct chat call, sequence only, no tools — the LLM guesses GO terms from pretraining knowledge alone | Baseline floor |
| `full_agent` | The actual agent (`agent/loop.py`), `eval_mode=True`, same MAX_STEPS/MAX_TOKENS_PER_RUN as normal use | The system under test |

## Metrics (`metrics.py`)

Protein-centric Fmax, computed separately per GO aspect (molecular_function / biological_process / cellular_component): for a grid of confidence thresholds τ, compute macro-averaged precision and recall (only using predictions with confidence ≥ τ), then report the τ that maximizes F1 = 2PR/(P+R).

**Simplification, stated honestly:** real CAFA propagates both predictions and ground truth up the GO DAG via the true-path rule (a prediction of a child term counts toward all its ancestors) before scoring. This implementation does **direct-term matching only** — no ancestor propagation. That makes this Fmax a stricter, lower-bound approximation of the real CAFA metric, not a faithful reproduction of it. See `docs/RESULTS.md`'s Limitations section.

## Hubness diagnostic

Separately from the 4 scored baselines, `run_hubness_diagnostic()` calls `tools/esm2_knn.py` directly on every benchmark protein (same exclusion set) and checks, for each of the top-10 returned neighbours: does *any* of that neighbour's own GO terms overlap the query's ground truth? It reports this hit-rate separately for hub-flagged vs. non-hub-flagged neighbours — a direct empirical answer to "are hub-flagged neighbours actually less reliable evidence," not just a theoretical justification for down-weighting them.

## Running it

```bash
# Build the benchmark once
python -m eval.build_benchmark --n 20

# Run the full evaluation (takes hours locally -- see docs/RESULTS.md for
# measured per-protein runtimes; the full_agent baseline dominates the cost)
python -m eval.run_eval

# Smaller/faster smoke test
python -m eval.run_eval --limit 2 --max-steps 5
```

Output for each run is saved under `eval/runs/<timestamp>/`: `predictions.json` (raw per-protein, per-method predicted GO terms), `metrics.json` (Fmax per method per aspect), `full_agent_reports.json` (stop reason, steps used, token usage per protein), `hubness_diagnostic.json`, `runtimes.json`.

Nothing in this benchmark is used for tuning — every number reported in `docs/RESULTS.md` is from one read-only pass over `benchmark.json`, weak results included.
