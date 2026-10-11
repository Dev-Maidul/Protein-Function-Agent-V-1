# Phase 3 Results

One read-only pass over `eval/benchmark.json` (n=20, see `eval/README.md` for how it was built and why). Nothing here was tuned on this benchmark — these are the numbers from the first full run that completed without crashing, including the uncomfortable ones. Raw data: `eval/runs/20261011T095847Z/`.

## Fmax per method per GO aspect

| Method | MF Fmax | BP Fmax | CC Fmax |
|---|---|---|---|
| `diamond_transfer` | **0.379** (P=0.393 R=0.367, cov=0.47) | 0.200 (P=1.000 R=0.111, cov=0.11) | 0.000 |
| `protrek_transfer` | 0.133 (P=0.133 R=0.133, cov=1.00) | 0.067 (P=0.083 R=0.056, cov=0.67) | **0.416** (P=0.722 R=0.292, cov=0.60) |
| `llm_no_tools` | 0.167 (P=0.222 R=0.133, cov=0.60) | 0.000 | 0.143 (P=0.250 R=0.100, cov=0.40) |
| **`full_agent`** | **0.000** | **0.000** | **0.000** |

**The full agent scored zero on every aspect, below all three simpler baselines.** This is reported honestly rather than re-run until it looked better. The cause is fully diagnosed below, not a mystery.

## Why `full_agent` scored zero (root-caused, not guessed)

Across the 20 proteins, the model proposed 16 GO terms in total inside `submit_final_answer` calls. The grounding check (`agent/report.py`, added after the beta-actin bug in Phase 2) kept only **3 of those 16** — the other 13 were valid GO IDs that never appeared in any tool's raw output, so they were correctly moved to `go_terms_ungrounded` and excluded from scoring. Of the 3 that passed grounding, all 3 are checked against this benchmark's ground truth and **none match**:

- `A0A0A7EPL0`: predicted `GO:0008270` (zinc ion binding); ground truth is `GO:0005515`, `GO:0061665`, `GO:0016925`, `GO:0060966` — no overlap.
- `A0A0A2JC26`: predicted `GO:0016664` and `GO:0003674`; ground truth is `GO:0046562` — no overlap. (`GO:0003674` is literally the root `molecular_function` term — a non-informative fallback, not a real prediction.)

So the zero is not a bug in the scoring code; it is three false positives and seventeen proteins with no scored prediction at all. Two compounding causes:

1. **In `eval_mode`, `uniprot_lookup` is unavailable** (by design — it's the ground-truth source). That leaves `interproscan_scan` as the *only* tool in the agent's kit whose output can ground a GO ID. Outside eval mode, a real run can also ground terms via `uniprot_lookup` on a homolog; inside eval mode, that path is closed off by the same leakage control that makes the evaluation trustworthy. This benchmark's proteins (deliberately picked for being recently-annotated and therefore obscure) have sparse InterPro domain curation, so there was often nothing to ground against even when the model's guess was reasonable.
2. **The tool-calling reliability issue from Phase 2** (model writes plain text instead of calling `submit_final_answer`) fired on 6 of 20 proteins (30%), cutting those runs short at 3-4 steps with no GO terms at all.

Net effect: the grounding check — added specifically to stop the agent from fabricating GO terms from memory — combined with leakage control's removal of `uniprot_lookup`, makes `full_agent` extremely conservative on a benchmark of exactly the hard, under-annotated proteins this evaluation deliberately selected. The simpler baselines don't carry this cost: `diamond_transfer` and `protrek_transfer` transfer another protein's *existing* GO annotations wholesale (no grounding check applies to them), and `llm_no_tools` has no grounding check either. This is a real tradeoff, not a wash: the full agent is trading recall for not fabricating evidence, and on this benchmark that trade comes out badly in Fmax terms.

## Hubness diagnostic — a clean positive result

Run separately from the 4 scored baselines (`run_hubness_diagnostic()` in `eval/run_eval.py`): for all 200 ESM-2 neighbours examined (20 proteins × top-10), checked whether the neighbour's *own* GO annotations overlap the query's ground truth.

| | Hit rate |
|---|---|
| Hub-flagged neighbours (10 of 200, 5%) | **0.0%** |
| Non-hub neighbours (190 of 200) | **25.3%** |

Every single hub-flagged neighbour in this benchmark was *wrong* (zero overlap with ground truth), while roughly a quarter of ordinary neighbours were right. This directly confirms, empirically rather than just theoretically, the design decision (from Phase 2, following the user's own hubness research) to down-weight hub-flagged ESM-2 evidence in the agent's reasoning. Caveat: only 10 hub-flagged neighbours total — a small sample for that specific number, though the direction is unambiguous.

## Runtime (average per protein, this hardware: M1 Mac, CPU-only, qwen3:8b via Ollama)

| Method | Avg time |
|---|---|
| `diamond_transfer` | 9.5s |
| `protrek_transfer` | 19.9s |
| `llm_no_tools` | 226.5s (~3.8 min) |
| `full_agent` | 568.5s (~9.5 min) |

`full_agent` costs ~60x `diamond_transfer` and still loses to it on Fmax on this benchmark — the single clearest number in this whole report for whether the added LLM-orchestration complexity is paying for itself *on this specific hard benchmark*. It is not, here.

## Limitations (stated plainly)

- **Fmax here is direct-term matching only, not real CAFA Fmax.** Real CAFA propagates both predictions and ground truth up the GO DAG via the true-path rule before scoring; this implementation does not. All Fmax numbers above are a stricter, lower-bound approximation — a real CAFA-style score would likely be higher for every method, including `full_agent`, since its 3 predictions might share ancestors with ground-truth terms even though the exact terms don't match. Not implemented here due to time; a clear next step.
- **n=20 is small**, and the benchmark is a recency-sorted convenience sample (see `eval/README.md`), not a random sample of Swiss-Prot. It specifically selects *hard*, under-characterized proteins (that's the point — fresher annotations are less likely memorized by the LLM) which plausibly disadvantages every method, but especially disadvantages `full_agent`'s grounding requirement.
- **LLM non-determinism**: the same protein, run twice, can produce different results (observed directly in Phase 2 — p53 sometimes got a structured answer, sometimes the plain-text fallback). This benchmark run is one sample from that distribution, not a stable number; re-running would shift individual protein outcomes, though probably not the overall pattern (full_agent's grounding-driven conservatism is structural, not a fluke of this particular run).
- **`eval_mode`'s removal of `uniprot_lookup`, combined with the grounding check, is a specific interaction this report surfaces for the first time** — neither piece was designed with the other in mind. A real fix (not implemented here, flagged for Phase 4 discussion) could let `full_agent` ground a GO term via a DIAMOND or Foldseek homolog's *domain* hits too, not just InterProScan's, or relax grounding to "any tool surfaced this GO ID *or* a direct ontological parent of it" -- deliberately not done now, to avoid tuning the pipeline on this exact benchmark's results.
- **Not CAFA's actual evaluation protocol** in other respects too: no "no-knowledge" vs "limited-knowledge" split, no multi-species stratification, single run per method (no repeated-run variance reporting given each `full_agent` run takes ~9.5 minutes).
