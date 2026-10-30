# Protein Function Agent

An LLM agent that infers a protein's function by orchestrating multiple bioinformatics tools, built as a prototype following a suggestion from Prof. Fajie Yuan (Westlake University, author of SaProt / ProTrek / Pinal / Evolla) to try building an AI agent for this task. Everything runs on a normal CPU laptop -- no GPU is used anywhere.

Given a protein (sequence, optionally a UniProt accession, or a structure), the agent produces:
- a function summary,
- predicted GO (Gene Ontology) terms with confidence scores,
- an **evidence table** -- every claim traces back to a specific tool call, its raw score, and a source link,
- a disagreement/uncertainty section when tools point different ways.

## What it does, in one paragraph

Rather than letting an LLM free-form call tools (which turned out to be unreliable -- see "Why the deterministic pipeline" below), the agent first gathers evidence **deterministically** from every tool in a fixed order, then **scores** every candidate GO term with a fixed, documented weighting rule, and only then gives the LLM one review pass over the already-scored candidates (accept, reject, re-weight -- always citing an evidence ID) to write the final summary. This means the deterministic scoring step (`aggregator_only`) is itself a usable, LLM-free method, and the LLM's contribution can be measured on top of it rather than assumed.

## Architecture

```mermaid
flowchart TD
    A[Input: sequence / accession / structure] --> B[agent/context.py<br/>AgentContext + leakage-control exclusions]
    B --> C[agent/evidence_pipeline.py<br/>gather_evidence -- fixed order, deterministic]
    C --> C1[tools/diamond_blast.py<br/>local DIAMOND vs Swiss-Prot]
    C --> C2[tools/alphafold_foldseek.py<br/>AlphaFold structure + Foldseek search]
    C --> C3[tools/interproscan.py<br/>+ tools/interpro2go.py]
    C --> C4[tools/protrek.py<br/>ProTrek_35M retrieval]
    C --> C5[tools/esm2_knn.py<br/>ESM-2 embeddings + kNN + hubness]
    C1 --> C6[tools/homolog_go.py<br/>GO terms of DIAMOND/Foldseek/ProTrek/ESM-2 hits]
    C2 --> C6
    C4 --> C6
    C5 --> C6
    C3 --> D[agent/aggregator.py<br/>deterministic scoring: fixed weights per source,<br/>hub-flagged neighbours down-weighted]
    C6 --> D
    D --> E{LLM review<br/>agent/loop.py<br/>one call, one retry}
    E -->|submit_final_answer tool call| F[agent/report.py<br/>grounding check vs evidence ledger<br/>+ QuickGO validity check]
    E -->|plain text twice| G[regex-parsed GO IDs + evidence IDs<br/>marked recovered_from_text]
    F --> H[Function summary + GO predictions<br/>+ evidence table + uncertainty section]
    G --> H
    D -.->|aggregator_only method,<br/>no LLM at all| H
```

## Tools, with licenses (full detail in `docs/TOOLS.md`)

| # | Tool | What for | License / terms |
|---|---|---|---|
| 1 | UniProt REST API | Ground truth + sequence fetch | CC BY 4.0 |
| 2 | QuickGO REST API | GO term validity, ancestors | Free for all use (EBI) |
| 3 | InterProScan (EBI Job Dispatcher) + `interpro2go` mapping | Domain/family detection, domain→GO | EBI terms of use |
| 4 | AlphaFold DB + Foldseek web search | Structure fetch + structural homology search | CC BY 4.0 (AlphaFold DB); free, non-commercial research (Foldseek web) |
| 5 | Local DIAMOND vs Swiss-Prot | Sequence homology search | DIAMOND: GPL; Swiss-Prot data: CC BY 4.0 |
| 6 | ProTrek_35M (westlake-repl) | Sequence/structure → text retrieval | MIT |
| 7 | ESM-2 (35M) | Embeddings + kNN + hubness diagnostic | Meta/FAIR ESM license |
| 8 | PubMed E-utilities | Literature (lowest priority) | Not yet implemented |

UniProt (#1) is excluded from the agent's own tool list in evaluation mode, since it is also where ground truth comes from.

## Why a deterministic pipeline instead of free-form tool calling

The first design let the LLM (qwen3:8b, local via Ollama, CPU-only) decide which tools to call and when. Two problems showed up on the first real evaluation run (20-protein dev set, see "Superseded" section of `docs/RESULTS.md`):
1. The model wrote a plain-text final answer instead of calling the submit tool on 30% of proteins.
2. Even when it did call tools correctly, its own DIAMOND/Foldseek calls returned only hit accessions and identity scores -- never the hits' own GO annotations -- so the agent had strictly *less* evidence available than the simple "look up top hit's GO terms" baseline it was being compared against.

Both are structural, not prompting issues, so the fix was architectural: evidence gathering and GO-term scoring no longer depend on the LLM making the right tool-calling decisions every time (`agent/evidence_pipeline.py`, `agent/aggregator.py`). The LLM's job narrows to reviewing already-assembled, already-scored evidence and writing the explanation -- with a documented fallback (regex-based parsing, flagged `recovered_from_text`) if it still doesn't use the structured submit call. Full root-cause writeup: `docs/RESULTS.md`'s "Superseded" section and `eval/README.md`'s "The root-cause fix" section.

## Leakage control

For evaluation, the agent must not be able to simply look up the answer. For every query protein, `agent/context.py`'s `AgentContext.build_exclusions()` runs DIAMOND against Swiss-Prot once and builds an exclusion set = {the query's own accession} ∪ {every hit at >=50% sequence identity}. This set is passed into every retrieval tool (DIAMOND, Foldseek, ProTrek, ESM-2, and the GO-annotation lookups on their hits) whenever `eval_mode=True`, so no close homolog's annotation can leak into the "prediction". Full detail: `eval/README.md`'s "Leakage control" section.

**Bug found and fixed (2026-10-30).** The exclusion search was originally capped at the top 10 DIAMOND hits, so any protein with more than 10 close homologs still leaked the rest (for example, P04637 / p53 has 36 hits at >=50% identity and only 10 were excluded). The cap is now 5,000 hits, with regression tests in `tests/test_agent_context.py`. On the 57 held-out proteins, 13 were affected (maximum 134 missing homologs for one protein; median 0). See `eval/leakage_audit_57.csv`. All numbers reported before this fix are superseded. The rule uses percent identity only; alignment coverage is not used.

## How to run it on one protein

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env needs: LLM base_url/api_key/model (OpenAI-compatible), EBI_CONTACT_EMAIL

# by UniProt accession (sequence fetched automatically)
python -m agent.run --accession P04637

# by raw sequence
python -m agent.run --sequence MEEPQSDPSV...

# evaluation mode (excludes the query + close homologs from retrieval, for benchmarking)
python -m agent.run --accession P04637 --eval-mode
```

Output is printed and the full trace (evidence ledger, aggregator candidates, LLM review) is saved under `runs/<protein>/<timestamp>/`.

## Evaluation

Full methodology (dev set vs. held-out test set, leakage control, scoring) is in `eval/README.md`. Older results and the history of fixed bugs are in `docs/RESULTS.md`. A complete log of the corrected re-evaluation, with commands and open questions, is in `eval/HANDOFF_REPORT.md`.

### Status: corrected re-evaluation on the same 57 proteins (preliminary)

After fixing the leakage bug above, `diamond_transfer`, `protrek_transfer`, `llm_no_tools` and `aggregator_only` were all re-run on the **same 57 held-out proteins** in one environment (Python 3.13, torch 2.14, transformers 5.17, DIAMOND 2.2.8). Numbers are Fmax, direct-match | CAFA-propagated [95% bootstrap CI of the propagated value, resampling proteins]. `n` is the number of proteins with experimental ground truth for that aspect.

| Method | molecular_function (n=36) | biological_process (n=34) | cellular_component (n=28) |
|---|---|---|---|
| `diamond_transfer` | 0.385 \| 0.572 [0.45, 0.68] | 0.233 \| 0.353 [0.22, 0.50] | 0.386 \| 0.465 [0.31, 0.62] |
| `protrek_transfer` | 0.218 \| 0.336 [0.23, 0.45] | 0.132 \| 0.269 [0.16, 0.39] | 0.520 \| 0.612 [0.49, 0.75] |
| `llm_no_tools` | 0.051 \| 0.183 [0.11, 0.28] | 0.000 \| 0.082 [0.04, 0.14] | 0.045 \| 0.192 [0.07, 0.32] |
| `aggregator_only` | 0.343 \| 0.545 [0.44, 0.66] | 0.220 \| 0.280 [0.20, 0.40] | 0.531 \| 0.621 [0.50, 0.76] |

How to read this honestly:
- The confidence intervals overlap widely, so no method can be called better than another on this sample. In particular, the deterministic aggregator is roughly comparable to the DIAMOND top-hit baseline, not clearly better.
- Its clearest advantage is **coverage**: the fraction of proteins with at least one prediction (direct) is 0.92 vs. 0.56 for DIAMOND in molecular function, 0.82 vs. 0.59 in biological process, and 0.96 vs. 0.64 in cellular component.
- `full_agent` (the LLM review step) has **not** been re-run on the corrected pipeline. Its earlier 17-protein result is superseded and is not reported here.
- The earlier hubness result (hub-flagged ESM-2 neighbours vs. non-hub neighbours, p=0.0202, pooled over neighbours) was computed before the leakage fix and is **being re-tested at the level of each query protein**. Treat it as unconfirmed.
- ProTrek evidence now works inside the agent pipeline (the earlier failure was a missing-dependency problem in the environment, fixed in `requirements.txt` with a regression test).

Raw numbers: `eval/runs/fmax_57_fixed_metrics.json`, `eval/runs/aggregator_only_57_results.json`, `eval/runs/baselines_57_fixed_results.json`.

<details>
<summary>Superseded results (computed before the leakage fix; kept for transparency only)</summary>

| Method (n) | molecular_function | biological_process | cellular_component |
|---|---|---|---|
| `diamond_transfer` (57) | 0.358 \| 0.526 | 0.261 \| 0.348 | 0.332 \| 0.420 |
| `protrek_transfer` (57) | 0.241 \| 0.327 | 0.140 \| 0.281 | 0.517 \| 0.625 |
| `llm_no_tools` (57) | 0.031 \| 0.117 | 0.000 \| 0.058 | 0.077 \| 0.173 |
| `aggregator_only` (17) | 0.268 \| 0.284 | 0.169 \| 0.125 | 0.379 \| 0.398 |
| `full_agent` (17) | 0.130 \| 0.153 | 0.100 \| 0.102 | 0.262 \| 0.248 |

These rows used the capped exclusion search, a 17-protein subset for the last two methods, and an agent pipeline in which ProTrek evidence was missing.
</details>

## Repository layout

```
agent/       -- agent loop, evidence pipeline, deterministic aggregator, report builder
tools/       -- one file per external tool, each returning a standard Evidence object
eval/        -- benchmark sets, leakage control, baselines, metrics, held-out test set
docs/        -- TOOLS.md, RESULTS.md (this README covers architecture)
runs/        -- full trace of every run (<protein>/<timestamp>/) (local only, not committed)
tests/       -- unit tests, with real-call fixtures under tests/fixtures/
```

## Limitations (honest, as of this writing)

- CPU-only, local 8B-parameter LLM (qwen3:8b via Ollama) -- slower and less reliable at structured tool-calling than a larger hosted model; its output is not bit-for-bit reproducible.
- The held-out test set (57 proteins) is a bounded sample (built from a bounded QuickGO scan), not a full census of experimentally annotated Swiss-Prot entries. Confidence intervals are wide (per-aspect n is 28-36).
- The ProTrek retrieval pool contains only 5,000 Swiss-Prot entries (a random subsample), so a relevant protein may simply be absent. Homology search uses Swiss-Prot only.
- The leakage rule removes hits at >=50% identity; it does not use alignment coverage and does not remove more distant homologs.
- `diamond_transfer` looks up only the top non-excluded hit; if that lookup fails (for example during a UniProt outage) it returns no prediction instead of trying the next hit. This is a known weakness of the baseline and is not yet fixed.
- `full_agent` has not been re-run on the corrected pipeline, and the per-protein hubness analysis is still pending.
- Code was written with the help of AI coding assistants; I designed the experiments, ran them, and checked the outputs.
