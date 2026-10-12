# Phase 3 Evaluation

This directory implements an evaluation of the protein-function agent against four simpler baselines, with leakage control so no method can simply look up the answer, and a dev/test split so nothing is tuned on the set the final numbers are reported on.

## Two benchmarks: dev set and held-out test set

**Dev set** (`eval/benchmark.json`, 20 proteins) -- the original Phase 3 benchmark. Used to find and fix problems (it's how the grounding gap, the checkpoint bug, and the plain-text-fallback issue were all found). **No longer used for reporting final numbers** -- it was inspected closely while diagnosing `full_agent`'s Fmax=0.000 result, so reporting on it again would be tuning on the test set in spirit even without touching the code based on its specific contents.

**Held-out test set** (`eval/test_set.json`, 57 proteins, target was ~60) -- built fresh (`eval/build_test_set.py`) and never inspected before any method ran on it:
1. Scan up to 100 pages (200 rows each) of QuickGO's annotation-search API for the same 6 experimental evidence codes as the dev set (EXP/IDA/IMP/IGI/IPI/IEP), collecting distinct accessions. **Honest caveat**: this is a bounded scan (6,178 distinct accessions found), not the full universe of experimentally-annotated proteins (QuickGO's annotation-search has no cheap accession-only projection, and the real universe has 1.28M+ annotation rows -- scanning all of it isn't practical). It's a large, bounded sample, not a true census.
2. Cross-check reviewed (Swiss-Prot) status via batched UniProt queries -> 1,330 candidates.
3. Remove every accession already in the dev set (0 overlap possible by construction).
4. `random.Random(seed=43)` (a different seed from the dev set's recency-sort and from `shared_subset.tsv`'s seed 42, so there's no hidden correlation) samples 60 accessions uniformly.
5. Fetch sequence + GO terms; 3 were dropped by the length filter ([20, 1500] aa), leaving 57.
- Reproduce: `python -m eval.build_test_set --n 60 --pages 100 --seed 43`

## Leakage control

Unchanged from the dev-set phase, but now applied consistently everywhere -- a real gap was found and fixed here: `tools/diamond_blast.py` and `tools/alphafold_foldseek.py` used to have NO exclusion logic at all when called via the agent's own tool-calling path (only `eval/baselines.py`'s `diamond_transfer` post-filtered). Both tools now accept `exclude_accessions` directly.

For every protein: `agent.context.AgentContext.build_exclusions()` runs DIAMOND once and builds an exclusion set = {query's own accession} ∪ {every hit at ≥ 50% identity}. This set is passed into every retrieval call in every method, and `full_agent`/`aggregator_only` run with `eval_mode=True`.

## Five methods (`eval/baselines.py`)

| Method | What it does |
|---|---|
| `diamond_transfer` | Top non-excluded DIAMOND hit's own UniProt GO annotations, confidence = % identity |
| `protrek_transfer` | Vote across top-10 non-excluded ProTrek matches' own GO terms |
| `llm_no_tools` | One direct chat call, sequence only, no tools |
| `aggregator_only` | **New.** `agent/evidence_pipeline.py` + `agent/aggregator.py`, deterministic, **no LLM at all** |
| `full_agent` | `agent/evidence_pipeline.py` + `agent/aggregator.py` + one LLM review call (`agent/loop.py`) |

### The root-cause fix: closing the evidence gap

Phase 3's first run found `full_agent` scoring below every baseline, root-caused to: the agent's DIAMOND/Foldseek tool calls only ever returned hit accessions and identity/score, never the hits' own GO annotations, while `diamond_transfer` reads those GO terms directly from UniProt. The agent had strictly less evidence available than the baseline it was being compared against. Fixed with:
- **`tools/homolog_go.py`** (new): given a list of homolog accessions (from DIAMOND, Foldseek, ProTrek, or ESM-2), fetches their own GO terms with evidence codes from UniProt, excluding the query/close-homolog set. This is now called automatically for every one of those four tools' hits.
- **`tools/interpro2go.py`** (new): a second, independent path from an InterPro ID to its GO terms (EBI's canonical interpro2go mapping file, downloaded once and cached), supplementing InterProScan's own `goXRefs` field for entries where the live API response doesn't have it populated.
- **`agent/evidence_pipeline.py`** (new): gathers all of the above deterministically, in a fixed order, for every protein -- evidence gathering no longer depends on the LLM reliably choosing to call the right tools (it was writing plain text instead of calling a tool ~30% of the time in the first eval run).
- **`agent/aggregator.py`** (new): scores every candidate GO term with fixed, documented weights (InterPro domain match: 1.0; DIAMOND homolog transfer: identity-weighted; Foldseek: similarity-weighted, discounted 0.9x; ProTrek: WEAK, 0.25x; ESM-2: cosine-weighted, hub-flagged neighbours cut to 0.1x vs 0.4x for non-hub). This is itself the `aggregator_only` method -- reported separately from `full_agent` to answer whether the LLM review step adds anything over the deterministic rule-based score.
- **`agent/loop.py`** (rewritten): the LLM is no longer given free-form tool-calling choice. It receives the already-gathered evidence and the aggregator's scored candidates, and gets exactly one review call (accept/reject/re-weight, write the summary) plus one retry if it writes plain text instead of calling `submit_final_answer`. If it fails twice, GO IDs and evidence-ID citations are parsed out of its free text with a documented regex heuristic (`agent.loop._parse_go_terms_from_text`) and the run is marked `recovered_from_text` -- reported, never hidden, never treated as equivalent to a real structured answer.

## Scoring (`eval/metrics.py`)

Both **direct-match** Fmax (exact GO ID match, what the first eval run reported) and **propagated** Fmax (CAFA's true-path rule: both predictions and ground truth are expanded to their full ancestor sets before scoring, excluding the 3 GO root terms) are computed and reported side by side, per aspect. Ancestor sets are fetched once per unique GO ID via QuickGO and cached (`eval/runs/ancestor_cache.json`, persists across runs) -- an evaluation run across 2 methods x 3 aspects x dozens of proteins would otherwise re-query the same handful of common ancestor terms hundreds of times.

## Hubness diagnostic, with actual statistics (`eval/stats.py`)

Same mechanism as before (run `tools/esm2_knn.py` directly, check whether each neighbour's own GO terms overlap ground truth, compare hub-flagged vs non-hub hit rates) but now reports a 95% Wilson-score confidence interval per rate and a Fisher exact test p-value on the 2x2 table. **The result is only described as "confirmed" if `significant_at_0.05` is True** -- the first eval run's dev-set sample (10 hub-flagged neighbours) was too small to responsibly make that claim without a test; this is why.

## Running it

```bash
# Build the held-out test set once (already done, checked into eval/test_set.json)
python -m eval.build_test_set --n 60 --pages 100 --seed 43

# Full run on the test set -- the cheap methods (diamond/protrek/llm_no_tools) run
# on all 57 proteins; aggregator_only/full_agent are restricted to a seeded random
# subset via --expensive-limit since each full_agent call costs ~9.5 minutes on
# this hardware (CPU-only M1, qwen3:8b via Ollama) -- running all 57 would take
# ~9 hours. This is reported plainly in run_config.json, never silently.
python -m eval.run_eval --dataset test --expensive-limit 15

# Resumable: a crash or interruption picks back up from eval/runs/in_progress_checkpoint.json
# unless --fresh is passed.
python -m eval.run_eval --dataset test --expensive-limit 15  # re-run to resume

# Smaller/faster smoke test
python -m eval.run_eval --dataset test --limit 2 --expensive-limit 1 --fresh
```

Output for each run is saved under `eval/runs/<dataset>_<timestamp>/`: `predictions.json`, `metrics.json` (direct + propagated Fmax per method per aspect), `full_agent_reports.json` (stop reason, `recovered_from_text`, token usage per protein), `hubness_diagnostic.json` (with CI + p-value), `runtimes.json`, `run_config.json` (exact CLI args used, including any `--expensive-limit`).

Nothing in either benchmark is used for tuning -- every number reported in `docs/RESULTS.md` is from a read-only pass, weak results included.
