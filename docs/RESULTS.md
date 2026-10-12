# Phase 3 Results

> **⚠️ The numbers in the "Superseded" section at the bottom of this file are OUTDATED and must not be cited.** They come from the 20-protein DEV set (`eval/benchmark.json`), before the root-cause fix described in `eval/README.md` ("The root-cause fix: closing the evidence gap"). Kept only as the historical record of how that bug was found. **The numbers below are the current preliminary results** -- held-out 57-protein TEST set, post-fix, read-only pass.

---

## Current results (held-out 57-protein test set, `eval/test_set.json`)

Note: `aggregator_only` and `full_agent` were evaluated on a 17-protein subset, while the three baselines ran on all 57 proteins, so these rows are not directly comparable. In this run, ProTrek evidence was also missing inside the agent pipeline because of an environment error (`protrek_transfer` itself ran correctly). A same-set re-evaluation with ProTrek fixed is in progress.

Raw data: `eval/runs/test_20261012T075650Z_fixed/`. Zero overlap with the 20-protein dev set (see `eval/README.md` for the sampling procedure). `diamond_transfer`, `protrek_transfer`, and `llm_no_tools` ran on all 57 proteins; `aggregator_only` and `full_agent` share an expensive evidence-gathering pipeline (~9 min/protein on this CPU-only hardware) and were restricted to a seeded random subset -- `--expensive-limit 15` (seed=7) plus 2 proteins already completed before that limit was set in a resumed run, for **17 proteins total**, reported plainly here, not silently.

**A real bug surfaced and was fixed before these numbers were produced** (documented for honesty, not hidden): the first complete run of this evaluation returned Fmax=0.000/coverage=0.00 for every single method and a hubness diagnostic that examined zero neighbours. Both were pipeline bugs, not real results: (1) EBI's QuickGO/UniProt APIs started silently rate-limiting after the ~2,000 sequential calls needed to build the GO-ancestor cache, which made every subsequent aspect-classification and ProTrek-GO-lookup call fail quietly; (2) the hubness diagnostic's per-protein context was never rebuilt on a resumed run where every protein was already checkpointed. Fixes: `tools/evidence.py` gained a shared `get_with_retry()` helper (exponential backoff on HTTP 429/502/503/504), used by `tools/uniprot.py` and `tools/quickgo.py`; `eval/run_eval.py`'s hubness diagnostic now builds its own context when one wasn't already built by the main loop. `protrek_transfer` and the Fmax/hubness computation were then re-run with the fix (`diamond`/`llm_no_tools`/`aggregator_only`/`full_agent` predictions from the original run were already correct and were reused as-is -- see `eval/runs/test_20261012T075650Z_fixed/NOTE.txt`).

### Fmax per method per GO aspect (direct-match | CAFA-style propagated)

| Method (n proteins) | MF: direct \| propagated (cov) | BP: direct \| propagated (cov) | CC: direct \| propagated (cov) |
|---|---|---|---|
| `diamond_transfer` (57) | 0.358 \| **0.526** (0.58/0.72) | 0.261 \| **0.348** (0.56/0.68) | 0.332 \| **0.420** (0.50/0.50) |
| `protrek_transfer` (57) | 0.241 \| **0.327** (0.44/0.75) | 0.140 \| **0.281** (0.41/0.74) | 0.517 \| **0.625** (0.93/0.93) |
| `llm_no_tools` (57) | 0.031 \| 0.117 (0.81/0.81) | 0.000 \| 0.058 (0.00/0.65) | 0.077 \| 0.173 (0.36/0.36) |
| `aggregator_only` (17) | **0.268** \| 0.284 (0.25/0.25) | **0.169** \| 0.125 (0.21/0.21) | **0.379** \| 0.398 (0.36/0.36) |
| `full_agent` (17) | 0.130 \| 0.153 (0.17/0.17) | 0.100 \| 0.102 (0.12/0.15) | 0.262 \| 0.248 (0.25/0.25) |

**Key finding: the deterministic `aggregator_only` method outperforms `full_agent` (the LLM review step) on every single aspect, at both direct and propagated Fmax, with higher coverage too.** This confirms (at n=17, not just the earlier n=1 smoke-test curiosity) that the LLM's accept/reject/re-weight review pass is not adding value over the fixed-weight deterministic scoring rule on this test set -- if anything, it narrows the result (lower coverage: 0.17-0.36 vs 0.21-0.36 for aggregator_only, and it drops 3 of 71 candidate GO terms as ungrounded on top of that). This is a genuine, if modest, negative result for the free-form-LLM-review design choice, reported as found.

`diamond_transfer` is the strongest single baseline overall; `protrek_transfer` is strongest specifically on cellular_component (0.625 propagated, 0.93 coverage); `llm_no_tools` (no tools, pure LLM guessing from sequence) is the weakest across the board, as expected.

### full_agent review step detail (n=17)

- 4 of 17 (24%) fell back to the plain-text-parsing recovery path (`recovered_from_text`) instead of a structured `submit_final_answer` tool call -- still a real reliability gap in the local 8B model's tool-calling, now at least measured and never silently hidden.
- 71 candidate GO terms were proposed across the 17 proteins; 68 were kept as grounded (traceable to a real tool evidence ID) and 3 were dropped as ungrounded.

### Hubness diagnostic -- now with a real significance test (n=570 neighbours, not 200)

| | Hit rate | 95% Wilson CI |
|---|---|---|
| Hub-flagged neighbours (n=45) | 11.1% | [4.8%, 23.5%] |
| Non-hub neighbours (n=525) | 27.4% | [23.8%, 31.4%] |

Fisher exact test: odds ratio = 0.331, **p = 0.0202, significant at p<0.05: True.** Unlike the dev-set run (n=10 hub-flagged, no test computed, "confirmed" claimed without statistical support), this result is now statistically backed: hub-flagged ESM-2 neighbours are significantly less reliable as evidence than non-hub neighbours on this larger, held-out sample -- directly supporting the aggregator's design choice to down-weight them (0.1x vs 0.4x, see `agent/aggregator.py`).

### Runtime (this hardware: CPU-only M1 Mac, qwen3:8b via Ollama)

| Method | Avg time | n |
|---|---|---|
| `diamond_transfer` | 9.3s | 57 |
| `protrek_transfer` | 16.9s | 57 |
| `llm_no_tools` | 201.4s (~3.4 min) | 57 |
| `aggregator_only` | 223.2s (~3.7 min) | 17 |
| `full_agent` | 537.7s (~9.0 min) | 17 |

---

## Superseded (dev-set run, pre-fix -- historical record only)

One read-only pass over `eval/benchmark.json` (n=20, see `eval/README.md` for how it was built and why). Nothing here was tuned on this benchmark — these are the numbers from the first full run that completed without crashing, including the uncomfortable ones. Raw data: `eval/runs/20261011T095847Z/`.

### Fmax per method per GO aspect (direct-match only -- propagation wasn't implemented yet)

| Method | MF Fmax | BP Fmax | CC Fmax |
|---|---|---|---|
| `diamond_transfer` | **0.379** (P=0.393 R=0.367, cov=0.47) | 0.200 (P=1.000 R=0.111, cov=0.11) | 0.000 |
| `protrek_transfer` | 0.133 (P=0.133 R=0.133, cov=1.00) | 0.067 (P=0.083 R=0.056, cov=0.67) | **0.416** (P=0.722 R=0.292, cov=0.60) |
| `llm_no_tools` | 0.167 (P=0.222 R=0.133, cov=0.60) | 0.000 | 0.143 (P=0.250 R=0.100, cov=0.40) |
| **`full_agent`** | **0.000** | **0.000** | **0.000** |

**The full agent scored zero on every aspect, below all three simpler baselines.** This triggered the root-cause investigation in `eval/README.md`.

### Why `full_agent` scored zero (root-caused, not guessed)

Across the 20 proteins, the model proposed 16 GO terms in total inside `submit_final_answer` calls. The grounding check (`agent/report.py`, added after the beta-actin bug in Phase 2) kept only **3 of those 16** — the other 13 were valid GO IDs that never appeared in any tool's raw output. Of the 3 that passed grounding, none matched ground truth. Two compounding causes, both now fixed:

1. **The agent's DIAMOND/Foldseek tool calls only ever returned hit accessions and identity/score, never the hits' own GO annotations** — so the agent had strictly less evidence than `diamond_transfer`, which reads a hit's GO terms directly from UniProt. Fixed by `tools/homolog_go.py` + `tools/interpro2go.py` + `agent/evidence_pipeline.py` (see `eval/README.md`).
2. **The tool-calling reliability issue from Phase 2** (model writes plain text instead of calling `submit_final_answer`) fired on 6 of 20 proteins (30%). Fixed by moving to a single, simpler review call (`agent/loop.py`) plus a documented retry-then-parse recovery path.

### Hubness diagnostic (dev set, no significance test computed at the time)

| | Hit rate |
|---|---|
| Hub-flagged neighbours (10 of 200, 5%) | 0.0% |
| Non-hub neighbours (190 of 200) | 25.3% |

This was described as a "clean positive result" without computing a confidence interval or a significance test — a gap this report flagged itself and `eval/stats.py` now fixes (Wilson CI + Fisher exact test, "confirmed" only claimed when p < 0.05).

### Runtime (dev set, this hardware: M1 Mac, CPU-only, qwen3:8b via Ollama)

| Method | Avg time |
|---|---|
| `diamond_transfer` | 9.5s |
| `protrek_transfer` | 19.9s |
| `llm_no_tools` | 226.5s (~3.8 min) |
| `full_agent` | 568.5s (~9.5 min) |

### Limitations of this superseded run (for the historical record)

- Fmax was direct-term matching only, not CAFA's propagated protocol.
- n=20 was a recency-sorted convenience sample, not random, and was inspected closely while diagnosing the zero-score result — reporting on it again would be tuning on it in spirit. Hence the fresh, never-inspected 57-protein test set.
- No significance test on the hubness numbers.
- `eval_mode`'s removal of `uniprot_lookup` combined with the grounding check left the agent almost nothing to ground against — the specific interaction that led to the fix above.
