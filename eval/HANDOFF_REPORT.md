# Handoff Report — DIAMOND Exclusion-Cap Leakage Fix + Re-evaluation

Generated 2026-10-30. Everything below is sourced from files, logs, and commands actually run in this session; `docs/RESULTS.md` and `README.md` were **not edited**.

---

## 1. What was run (with real timestamps where logged) and what was NOT run

| # | Command / action | Start (file-system evidence) | End (file-system evidence) | Duration |
|---|---|---|---|---|
| 1 | `python3 -m venv .venv` + `pip install -r requirements.txt` (torch/transformers/torchmetrics/pytorch-lightning + faiss-cpu/pandas/scikit-learn/biopython/omegaconf/easydict/tabulate/joblib/threadpoolctl/antlr4-python3-runtime/cloudpickle/dill/multiprocess/narwhals) | 2026-10-29 19:27:41 (`.venv` created) | — | not separately timed |
| 2 | `python -m agent.run --accession P04637 --eval-mode` (Ollama restarted first; full end-to-end run with the fixed ProTrek env) | — | 2026-10-29 20:06:10 (`runs/P04637/20261029T120610Z/report.md` written) | ~11 min (not independently re-verified this report; based on process wall-clock observed at the time) |
| 3 | `agent/context.py` edited: `build_exclusions()` now calls `diamond_blast.run(..., top_n=5000)` | — | 2026-10-30 00:21:03 (file mtime) | — |
| 4 | `eval/baselines.py` edited: `diamond_transfer()` now passes `exclude_accessions=ctx.exclude_accessions` | — | 2026-10-30 00:21:46 (file mtime) | — |
| 5 | `tests/test_agent_context.py` added (2 regression tests) + 2 new fixtures | — | 2026-10-30 00:23:21 (file mtime) | — |
| 6 | 57-protein leakage audit (DIAMOND only, no LLM, no other tool) → `eval/leakage_audit_57.csv` | — | 2026-10-30 00:38:11 (file mtime) | — |
| 7 | `pytest -q` (full suite) | — | — | 70 passed, 12.15s (last run) |
| 8 | `aggregator_only` on all 57 held-out proteins (`gather_once()` + `aggregator_only_from_candidates()`, no LLM) → `eval/runs/aggregator_only_57_results.json` | checkpoint file created 2026-10-30 01:03:21 | 2026-10-30 06:24:15 | ≈5h21m |
| 9 | `diamond_transfer` + `protrek_transfer` + `llm_no_tools` re-run on the same 57 proteins with the fixed exclusion set → `eval/runs/baselines_57_fixed_results.json` | log file created 2026-10-30 09:39:50 | 2026-10-30 15:23:28 | ≈5h44m **wall-clock span, but this includes an unlogged pause** — the run was manually killed ~5 proteins in after discovering a UniProt REST API outage (repeated live `curl` checks showing `500`/`503` for most accessions), then resumed from checkpoint once UniProt recovered (confirmed recovered via repeated `curl` 200s). Exact pause duration was not logged; **unknown**. |
| 10 | Re-ran `diamond_transfer`/`protrek_transfer` for the 5 proteins computed *during* the UniProt outage (their original predictions were outage-contaminated — mostly empty) | — | — | not run (completed quickly, <1 min, not separately timed) |
| 11 | `eval/metrics.py` edited: added `fmax_bootstrap_ci()` (protein-resampling bootstrap, 1000 resamples, re-optimizes threshold per resample) | — | 2026-10-30 09:40:39 (file mtime) | — |
| 12 | Fmax (direct + CAFA-propagated) + bootstrap 95% CI, all 4 methods × 3 aspects, on the same 57 proteins → `eval/runs/fmax_57_fixed_metrics.json` | log file created 2026-10-30 15:23:52 | 2026-10-30 18:06:13 | ≈2h42m (mostly spent fetching QuickGO ancestors for 988 new GO IDs, sequential, ~2 calls/ID) |

**NOT run in this session:**
- `full_agent` (the LLM-review pipeline) on all 57 proteins — only `aggregator_only` was run, per explicit instruction.
- The `diamond_transfer`-only-tries-top-hit fragility (finding #2 below) was identified but **not fixed**.
- README.md and docs/RESULTS.md were **not edited**.
- No old results were deleted.
- The exact duration of the UniProt-outage pause in step 9 — **unknown**, not logged.
- Independent re-verification of step 2's ~11-minute duration — **not re-run**, taken from earlier in-session observation.

---

## 2. Environment

| | Value | Source |
|---|---|---|
| Python | 3.13.9 | `.venv/bin/python3 -c "import sys; print(sys.version)"` |
| torch | 2.14.0 | live import |
| transformers | 5.17.0 | live import |
| torchmetrics | 1.9.0 | live import |
| faiss | 1.15.0 (faiss-cpu) | live import |
| diamond | 2.2.8 | `diamond --version` |
| ProTrek candidate pool size | 5,000 | `len(np.load('data/protrek_text_embeddings.npz')['accessions'])` |
| Leakage rule | `HOMOLOG_IDENTITY_THRESHOLD = 50.0` (percent identity, `agent/context.py`) | source read |
| Is coverage (query/subject alignment coverage) used in the exclusion rule? | **No.** Only `pident >= 50.0`; no `--query-cover`/`--subject-cover` flag is passed to DIAMOND anywhere in `tools/diamond_blast.py`. | source read |
| DIAMOND `--max-target-seqs` used for the exclusion-set-building call | 5,000 (was 10 before the fix) | `agent/context.py` diff |
| DIAMOND `--evalue` override | None — DIAMOND's own default is used; no explicit `--evalue` flag is set anywhere in `tools/diamond_blast.py`. | source read |

---

## 3. Leakage audit totals (`eval/leakage_audit_57.csv`, recomputed from the CSV just now, not from memory)

- **Proteins affected (`n_previously_missing > 0`): 13 / 57**
- **Max missing homologs (one protein): 134** (accession `A0A0D8BWP6`: 144 true ≥50%-identity hits, old cap caught only 10)
- **Median missing, across all 57: 0** (most proteins have ≤10 true ≥50%-identity hits, so the old `top_n=10` cap never bound)
- Mean missing, among the 13 affected proteins only: 20.2

---

## 4. Main results table — same 57 proteins, all 4 methods, with old numbers alongside

n = number of the 57 proteins with experimental ground-truth for that aspect. Coverage = fraction of those n with ≥1 prediction (direct / propagated — propagated coverage can exceed direct coverage because ancestor expansion can turn a previously-non-overlapping prediction into one that overlaps).

### molecular_function

| Method | n | Coverage (d/p) | OLD direct Fmax | **NEW direct Fmax [95% CI]** | OLD prop. Fmax | **NEW prop. Fmax [95% CI]** |
|---|---|---|---|---|---|---|
| `diamond_transfer` ⚠️superseded | 36 | 0.556 / 0.806 | 0.358 | **0.385 [0.263, 0.523]** | 0.526 | **0.572 [0.452, 0.676]** |
| `protrek_transfer` ⚠️superseded | 36 | 0.694 / 0.694 | 0.241 | **0.218 [0.124, 0.348]** | 0.327 | **0.336 [0.228, 0.445]** |
| `llm_no_tools` ⚠️superseded | 36 | 0.083 / 0.778 | 0.031 | **0.051 [0.000, 0.154]** | 0.117 | **0.183 [0.112, 0.275]** |
| `aggregator_only` (old n=17, not directly comparable) | 36 | 0.917 / 0.917 | 0.268 | **0.343 [0.260, 0.458]** | 0.284 | **0.545 [0.441, 0.661]** |

### biological_process

| Method | n | Coverage (d/p) | OLD direct Fmax | **NEW direct Fmax [95% CI]** | OLD prop. Fmax | **NEW prop. Fmax [95% CI]** |
|---|---|---|---|---|---|---|
| `diamond_transfer` ⚠️superseded | 34 | 0.588 / 0.588 | 0.261 | **0.233 [0.108, 0.384]** | 0.348 | **0.353 [0.224, 0.495]** |
| `protrek_transfer` ⚠️superseded | 34 | 0.118 / 0.706 | 0.140 | **0.132 [0.056, 0.270]** | 0.281 | **0.269 [0.159, 0.387]** |
| `llm_no_tools` ⚠️superseded | 34 | 0.000 / 0.647 | 0.000 | **0.000 [0.000, 0.000]** | 0.058 | **0.082 [0.037, 0.135]** |
| `aggregator_only` (old n=17, not directly comparable) | 34 | 0.824 / 0.824 | 0.169 | **0.220 [0.137, 0.320]** | 0.125 | **0.280 [0.196, 0.399]** |

### cellular_component

| Method | n | Coverage (d/p) | OLD direct Fmax | **NEW direct Fmax [95% CI]** | OLD prop. Fmax | **NEW prop. Fmax [95% CI]** |
|---|---|---|---|---|---|---|
| `diamond_transfer` ⚠️superseded | 28 | 0.643 / 0.643 | 0.332 | **0.386 [0.224, 0.547]** | 0.420 | **0.465 [0.311, 0.624]** |
| `protrek_transfer` ⚠️superseded | 28 | 0.929 / 0.929 | 0.517 | **0.520 [0.370, 0.683]** | 0.625 | **0.612 [0.493, 0.753]** |
| `llm_no_tools` ⚠️superseded | 28 | 0.286 / 0.357 | 0.077 | **0.045 [0.000, 0.130]** | 0.173 | **0.192 [0.066, 0.320]** |
| `aggregator_only` (old n=17, not directly comparable) | 28 | 0.964 / 0.964 | 0.379 | **0.531 [0.392, 0.673]** | 0.398 | **0.621 [0.502, 0.757]** |

"⚠️ superseded" rows: OLD numbers were computed under the pre-fix exclusion cap (and, for `aggregator_only`, also under the then-separate ProTrek-environment bug and an n=17 subset) — not directly comparable in rigor to the NEW numbers, shown only as the prior reference point.

---

## 5. Anything surprising or suspicious

1. **UniProt REST API outage mid-run** (step 9 above) — external, not a bug in this project's code, but large enough (most accessions returning `500`) that it silently zeroed out `diamond_transfer`/`protrek_transfer` predictions for the first 5 proteins processed. Caught, the run was paused, and those 5 proteins' predictions were redone after confirming recovery.
2. **`diamond_transfer`'s fragility**: it looks up only the UniProt record of the single top non-excluded DIAMOND hit; if that one lookup fails for any reason, the function returns an empty prediction instead of trying the next-best hit. This is why the UniProt outage produced exactly 0 predictions (not partial/degraded ones) for `diamond_transfer` specifically. **Not fixed in this session** (out of the approved scope).
3. **`aggregator_only`'s propagated Fmax roughly doubled on molecular_function** (0.284 → 0.545) between the old (n=17, two bugs present) and new (n=57, both bugs fixed) numbers. Cannot attribute this cleanly between "leakage fix" vs. "3.4x larger, different sample" — both changed at once.
4. **Coverage gap between direct and propagated** is large for some rows (e.g. `protrek_transfer` BP: 0.118 direct vs 0.706 propagated; `llm_no_tools` MF: 0.083 vs 0.778). This is the expected effect of CAFA ancestor-expansion (a predicted child term now "covers" a true parent term), not a bug, but it is a large enough swing to flag.
5. **No protein had zero `aggregator_only` candidates** in the final 57-protein run (checked: all 57 entries in `eval/runs/aggregator_only_57_results.json["done"]` have a non-empty list) — contradicts nothing, just noting it as a clean result worth having on record.
6. **13/57 (23%) proteins were affected by the leakage bug**, but the *median* missing-homolog count across all 57 is 0 — the bug's impact is concentrated in a minority of highly-conserved proteins (paralog/ortholog-rich), not spread evenly.

---

## 6. Files created or changed, and exact reproduction commands

### Code changed
- `agent/context.py` — `build_exclusions()` now calls `diamond_blast.run(..., top_n=5000)`.
- `eval/baselines.py` — `diamond_transfer()` now passes `exclude_accessions=ctx.exclude_accessions`.
- `eval/metrics.py` — added `fmax_bootstrap_ci()`; `precision_recall_at_threshold()` and `fmax()` gained an optional `protein_ids` param to support resampling.
- `agent/loop.py`, `eval/run_eval.py` — (from an earlier sub-task this session) save `metadata.json` per run via new `agent/run_metadata.py`.
- `requirements.txt` — ProTrek's full transitive dependency set added (faiss-cpu, pandas, scikit-learn, biopython, omegaconf, easydict, tabulate, joblib, threadpoolctl, antlr4-python3-runtime, cloudpickle, dill, multiprocess, narwhals), on top of the already-present torch/transformers/torchmetrics/pytorch-lightning.

### New files
- `agent/run_metadata.py`
- `tests/test_agent_context.py` (2 regression tests for the leakage-cap fix)
- `tests/fixtures/diamond_blastp_P04637_uncapped.tsv`, `tests/fixtures/diamond_blastp_small_protein.tsv`
- `eval/leakage_audit_57.csv`
- `eval/runs/aggregator_only_57_results.json`
- `eval/runs/baselines_57_fixed_results.json`
- `eval/runs/fmax_57_fixed_metrics.json`
- `eval/HANDOFF_REPORT.md` (this file)
- `eval/scripts/leakage_audit_57.py`, `eval/scripts/aggregator_only_57.py`, `eval/scripts/baselines_57.py`, `eval/scripts/compute_fmax_57.py` (moved from ad hoc `/tmp/*.py`, paths fixed to be repo-relative)

### Not changed
- `README.md`, `docs/RESULTS.md` — untouched.
- `eval/runs/20261011T095847Z/`, `eval/runs/test_20261012T075650Z_fixed/` — old result directories, untouched.

### Reproduction commands

The four scripts below were originally written as ad hoc `/tmp/*.py` files during this session and have since been moved into `eval/scripts/` (paths fixed to be repo-relative, verified to still start correctly) for commit.

```bash
# Environment (already done; re-running is idempotent)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Leakage audit (DIAMOND only, ~57 proteins, no LLM)
PYTHONPATH="." .venv/bin/python3 -m eval.scripts.leakage_audit_57   # -> eval/leakage_audit_57.csv

# aggregator_only on all 57
PYTHONPATH="." .venv/bin/python3 -m eval.scripts.aggregator_only_57   # -> eval/runs/aggregator_only_57_results.json

# diamond_transfer / protrek_transfer / llm_no_tools on all 57 (needs Ollama running for llm_no_tools)
ollama serve &
PYTHONPATH="." .venv/bin/python3 -m eval.scripts.baselines_57   # -> eval/runs/baselines_57_fixed_results.json

# Fmax + bootstrap CI, all 4 methods x 3 aspects
PYTHONPATH="." .venv/bin/python3 -m eval.scripts.compute_fmax_57   # -> eval/runs/fmax_57_fixed_metrics.json

# Full test suite
PYTHONPATH="." .venv/bin/python3 -m pytest -q
```

---

## 7. Known limitations

- **ProTrek candidate pool is only 5,000 Swiss-Prot entries** (`data/protrek_text_embeddings.npz`), a random subsample, not the full Swiss-Prot. A true homolog or functionally-similar protein may simply not be in the pool.
- **DIAMOND search is against local Swiss-Prot only** (`data/swissprot.dmnd`), not TrEMBL or any other database.
- **Held-out test set is 57 proteins**, not a large benchmark — bootstrap CIs are consequently wide (often spanning ±0.1-0.15 Fmax), and per-aspect n drops further (28-36), so point estimates should not be over-interpreted.
- **CPU-only** — no GPU was used anywhere in this session (ESM-2/ProTrek inference, DIAMOND search, everything ran on CPU/MPS as applicable to this machine).
- **`llm_no_tools` depends on a local Ollama model (qwen3:8b)** with known non-deterministic output; re-running it will not reproduce bit-identical numbers.
- **QuickGO ancestor/aspect lookups are sequential HTTP calls** with no batching — this is why step 12 took ~2h42m for 988 new GO IDs; a batched QuickGO endpoint (if one exists) was not investigated.

---

## 8. Open questions / things I am unsure about

- Exact duration of the UniProt outage pause in step 9 — not logged, genuinely unknown.
- How much of `aggregator_only`'s large Fmax increase (point 5.3) is attributable to the leakage fix specifically versus the n=17→57 sample-size change — cannot be disentangled with the data on hand; would need a controlled re-run of the OLD (buggy) code on the SAME 57 proteins to isolate it, which was not done.
- Whether `protrek_transfer`'s and ESM-2/Foldseek's own exclusion logic (confirmed structurally leakage-cap-free earlier this session, by code-reading) could still have some other, un-audited leakage path not covered by this DIAMOND-focused investigation — not re-verified in this session.
- Whether the UniProt `500` errors seen were a genuine UniProt-side incident or something specific to the request pattern used here (e.g. rate-limiting disguised as 500s) — not confirmed with UniProt directly; inferred only from repeated `curl` checks across multiple accessions and a brief recovery window.
- Whether `diamond_transfer`'s single-top-hit fragility (point 5.2) has caused silent under-counting in the OLD (superseded) numbers in `docs/RESULTS.md` as well, from some earlier, undiagnosed transient UniProt issue — not investigated.
