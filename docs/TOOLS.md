# Tools

For each tool: what it does, its API/license, and how it was verified in this project with a real live call (raw response saved under `tests/fixtures/`).

## 1. UniProt (`tools/uniprot.py`)
- **What it does:** fetches curated annotation for a UniProt accession — protein name, organism, GO terms, function description, sequence.
- **API:** `GET https://rest.uniprot.org/uniprotkb/{accession}.json` — no auth.
- **License/terms:** UniProt data is CC BY 4.0 (https://www.uniprot.org/help/license).
- **Verified:** real call against human p53 (P04637) — got 119 GO cross-refs, function text, sequence (length 393). Fixture: `tests/fixtures/uniprot_P04637.json`.
- **Important:** this is also the agent's source of ground truth (used in evaluation), so in Phase 2 the agent is not allowed to call this tool in "evaluation mode".

## 2. QuickGO (`tools/quickgo.py`)
- **What it does:** checks whether a predicted GO ID is valid/obsolete, and gets its name and ancestor terms (the hierarchy above it).
- **API:** `GET https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/{id}` and `.../ancestors` — no auth.
- **License/terms:** EBI data is free for all use (https://www.ebi.ac.uk/about/terms-of-use).
- **Verified:** tested with `GO:0006915` ("apoptotic process") — got name, aspect, 5 ancestors. Also deliberately tested with an invalid ID (`GO:9999999`) — the API returns `numberOfHits: 0` instead of a 404, so the wrapper treats that as "invalid" (not an exception). Fixtures: `tests/fixtures/quickgo_term_GO-0006915.json`, `tests/fixtures/quickgo_ancestors_GO-0006915.json`, `tests/fixtures/quickgo_term_invalid.json`.

## 3. InterProScan (`tools/interproscan.py`)
- **What it does:** finds protein domains/families (InterPro entries) in an amino-acid sequence, with GO cross-references where available.
- **API:** EMBL-EBI Job Dispatcher REST (`iprscan5`), base `https://www.ebi.ac.uk/Tools/services/rest/iprscan5` — **async job**: `POST /run` → job ID, `GET /status/{id}` (poll), `GET /result/{id}/json`. Submission requires a contact email (per EBI's usage policy) — set via `EBI_CONTACT_EMAIL` in `.env`.
- **License/terms:** EBI Job Dispatcher terms (https://www.ebi.ac.uk/about/terms-of-use).
- **Verified:** submitted a real job for the human p53 sequence (393 aa) — took **~1 min 35 s** (RUNNING to FINISHED), got 24 raw signature matches, 12 of which have a curated InterPro entry (the rest are raw signature hits without integration) — e.g. `IPR011615` (p53 DNA-binding domain, linked to `GO:0000976`). Fixture: `tests/fixtures/interproscan_result_P04637.json`.
- **Caveat:** the async job can be slow/stuck — the wrapper has a hard timeout (20 minutes) and a fixed poll interval (15 seconds); on timeout it reports via the `error` field instead of crashing the agent.

## 4. AlphaFold DB + Foldseek web search (`tools/alphafold_foldseek.py`)
- **What it does:** downloads a UniProt accession's AlphaFold-predicted 3D structure, then searches Foldseek for structurally similar proteins (can catch functional similarity even when sequence similarity is low).
- **API:**
  - AlphaFold DB metadata: `GET https://alphafold.ebi.ac.uk/api/prediction/{accession}`, structure file downloaded directly from the `pdbUrl`.
  - Foldseek web: `POST https://search.foldseek.com/api/ticket` (structure file upload) → ticket ID, `GET /api/ticket/{id}` (poll), `GET /api/result/{id}/0` (result) — no auth.
- **License/terms:** AlphaFold DB — CC BY 4.0. Foldseek web server — free, non-commercial research use (https://search.foldseek.com).
- **Verified:** downloaded the real AlphaFold structure for human p53 (P04637) (pLDDT global metric 75.06), searched the `afdb-swissprot` database in Foldseek — got **179 structural neighbours**, the top hit (lowest e-value) was p53's own AlphaFold entry (seqId=100%, the expected sanity check). The search completed instantly in this live test (same response), but polling/timeout code is still included for the general case. Fixtures: `tests/fixtures/alphafold_P04637.json`, `tests/fixtures/foldseek_ticket.json`, `tests/fixtures/foldseek_result_P04637.json`.
- **Caveat:** AlphaFold's file server returns `403` to requests without a `User-Agent` header (caught in this project's live testing) — the wrapper always sends one.

## 5. Local DIAMOND/BLAST vs Swiss-Prot (`tools/diamond_blast.py`)
- **What it does:** searches a local Swiss-Prot database for sequence homologs of an amino-acid sequence — on CPU, no network, no rate limits. The EBI/NCBI BLAST web API is documented as a fallback but not implemented, since the local path is faster and has no quota.
- **Setup (one-time):**
  ```bash
  brew install diamond
  curl -L "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz" -o data/uniprot_sprot.fasta.gz
  gunzip -k data/uniprot_sprot.fasta.gz
  diamond makedb --in data/uniprot_sprot.fasta --db data/swissprot
  ```
- **License/terms:** Swiss-Prot data is CC BY 4.0 (UniProt's license, same as #1 above). DIAMOND itself is a GPL-licensed open-source tool.
- **Verified:** ran a real `diamond blastp` against human p53 (393 aa) (took ~7.7 seconds; the full index, 575,748 sequences, was built in ~2.8 seconds) — top hit was p53's own human entry (100% identity, sanity check passed), followed by p53 orthologs across several primates (95.4%, 94.9%, ...) — exactly as expected. Fixtures: `tests/fixtures/diamond_blastp_P04637.tsv`, `tests/fixtures/query_p53.fasta`.
- **Note:** the `data/` folder (~670MB — FASTA + .dmnd index) is not committed to git (in `.gitignore`) — it's reproducible with the three commands above.

## Shared Swiss-Prot subset (`tools/build_shared_subset.py`) — used by both ProTrek and ESM-2

The first version of Phase 1 used only 5 hand-picked candidate texts for ProTrek and 800 random sequences for ESM-2 — neither was large enough for a meaningful evaluation. To fix this, a **single subset shared by both tools** was built, so results are comparable and the subset-selection method only needs to be documented once:

- **⚠️ Selection method was corrected (the user caught this, reported honestly):** the first version took the first 5,000 qualifying entries in **accession-ascending** order — deterministic, but **not a random sample** (alphabetically biased, mostly "A0A0..." prefixes, didn't even reach "P0xxxx"). It has now been fixed to a **true seeded random sample**:
  1. A lightweight pass — `query=reviewed:true AND cc_function:* AND length:[20 TO 1000]` (server-side filter) streaming only the `accession` field gets the full list of the qualifying universe (**455,644 entries, only ~3.2MB** — small because the heavy fields are skipped).
  2. `random.Random(seed=42).sample(all_accessions, 5000)` — a truly uniform random sample of 5,000 accessions from the whole universe.
  3. Full fields (function text, sequence, etc.) are then batch-fetched for just those 5,000 (`accession:(A OR B OR ...)` queries, batch size 50 — batch size 200 hit a UniProt 400 error, a URL-length limit, caught in live testing).
  This approach **downloads even less data than before** (universe list + detail for just the sampled 5,000 ≈ ~8MB total) and gives a genuinely uniform random sample.
- **Source:** the UniProt REST stream API, the same API as tool #1 above, in bulk/stream mode.
- **Function text cleaning:** multi-chain polyproteins have several `;`-separated "FUNCTION: [Chain name]: ..." segments — the first one is taken, `{ECO:...}` evidence codes and the `[Chain name]:` label are stripped, and the text is truncated to 320 characters (at a word boundary).
- **Result (new random sample, seed=42):** `data/shared_subset.tsv` has 5,000 rows. Accessions now span the full alphabet from A to W (not just A0A0.../O3xxx as before — verified). Sequence length min/max/avg: 21/998/332.4 aa. **Neither p53 (P04637) nor beta-actin (P60709) is in this subset** — so there is no self-leakage in the tests below.
- **To reproduce:** `python -m tools.build_shared_subset --n 5000 --max-len 1000 --seed 42` (`data/` is not in git).

## 6. ProTrek_35M (`tools/protrek.py`, cache built by `tools/build_protrek_cache.py`)
- **What it does:** given a protein sequence, retrieves the best-matching top-k descriptions from the 5,000 real Swiss-Prot function descriptions in the shared subset above (each with its UniProt accession) — the earlier version only ranked within 5 hand-picked candidates, which wasn't a meaningful test (Phase 1 fix #1).
- **Source:** code — https://github.com/westlake-repl/ProTrek (MIT license), cloned into `vendor/ProTrek/`. Weights — https://huggingface.co/westlake-repl/ProTrek_35M_UniRef50 (~678MB) — already downloaded earlier, symlinked at `weights/ProTrek_35M_UniRef50`.
- **Needs three version-mismatch monkeypatches** (discovered in an earlier project, reused here) — the repo expects older `torchmetrics`/`transformers` APIs.
- **Building the text-embedding cache (one-time, offline):** `python -m tools.build_protrek_cache --batch-size 250` — builds ProTrek text embeddings for the shared subset's 5,000 function texts (batched, with a resumable checkpoint). **Real run time: 63.3 seconds** (MPS, batch=250); the 5,000×1024 embedding matrix is saved to `data/protrek_text_embeddings.npz`.
- **Evaluation mode:** if the caller passes `exclude_accessions`, those accessions are removed from the candidate pool (the query's own accession, or close homologs found via DIAMOND — the threshold will be finalized in Phase 3).
- **Verified (on the true random subset, not mocked, re-verified after the sampling fix):** retrieved the top-10 matches for the full human p53 sequence — took **~9.2 seconds** (MPS).
  ```
  +13.409  O35147  Promotes cell death; competes for binding to Bcl-X(L)/Bcl-2/Bcl-W (BH3-only, apoptosis)
  +12.482  Q8C0R0  Deubiquitinase -- cell cycle regulation, DNA damage response
  +12.323  P99025  Mediates tetrahydrobiopterin inhibition of GTP cyclohydrolase 1
  +12.249  Q08050  Transcription factor regulating cell-cycle genes for DNA replication (FOXM1-like)
  +11.994  Q9H147  Increases DNTT terminal deoxynucleotidyltransferase activity
  +11.856  P45974  Deubiquitinating enzyme, wide range of cellular processes
  +11.642  Q96676  Viral genome replication, drives quiescent cells into cell cycle
  +11.498  Q969S2  Base excision repair of oxidatively/mutagen-damaged DNA
  +11.318  Q9R0P9  Deubiquitinase, cell-cycle/DNA-repair-adjacent regulation
  +11.241  P85118  Probable acetyltransferase (no detectable HAT activity)
  ```
  This candidate pool (5,000 from the random subset) differs from the earlier accession-ascending subset, so the top score is a bit lower (13.4 vs. 17.6 before) — but thematically it's still clearly centered on apoptosis/cell-cycle/DNA-damage-repair (a BH3-only apoptosis regulator, cell-cycle-related deubiquitinases, a FOXM1-like TF, base excision repair) — consistent with p53's pathway, although **a direct p53 interactor (e.g. ASPP2/MDM4) isn't in this pool this time** (didn't land in the truly random sample). This is exactly why ProTrek is treated as "weak evidence" — it picks up pathway-level themes, but finding a specific interactor depends on what's in the pool. Fixture: `tests/fixtures/protrek_top10_p53.json`.

## 7. ESM-2 embedding + kNN + hubness diagnostic (`tools/esm2_knn.py`, index built by `tools/build_esm2_index.py`)
- **What it does:** builds an ESM-2 (35M) embedding for a protein sequence, then finds nearest neighbours by cosine similarity in the shared subset's (5,000-sequence) embedding index. Each neighbour gets a **hubness flag** — i.e. whether this neighbour keeps showing up as "nearest" for many unrelated queries too (high k-occurrence), a known problem in high-dimensional nearest-neighbour search (directly related to the user's own hubness research) — a hub-flagged neighbour's evidence is treated as less trustworthy. The earlier version used only 800 random sequences (Phase 1 fixes #2, #3).
- **Model:** https://huggingface.co/facebook/esm2_t12_35M_UR50D (Meta/FAIR ESM license).
- **Building the index (one-time, offline, batched + resumable checkpoint):**
  ```bash
  python -m tools.build_esm2_index --batch-size 16 --k 10
  ```
  Builds ESM-2 embeddings (mean-pooled) for the shared subset's (`data/shared_subset.tsv`, see above) 5,000 sequences (sorted by length before batching to reduce padding waste — measured to be slower on CPU without this), then runs a leave-one-out top-10 neighbour search to count each point's k-occurrence (N_k).
- **⚠️ A real bug was found and fixed (reported honestly, not hidden):** in the first run, the checkpoint-resume logic was wrong — it restored via `embeddings[:done] = checkpoint[:done]`, a *contiguous* slice, but the actual writes happen at *scattered* positions following the length-sorted batch order (`order[]`). As a result, most already-computed rows were silently zeroed out on every resume. In the final result, **1,532 of 5,000 (30.6%) embeddings were zero-vectors**, which made the hubness stats completely unrealistic (max k-occurrence 1542 — one zero-vector's cosine similarity became NaN and biased `argpartition`). **After catching this bug:** it was fixed (the checkpoint's whole saved array is now loaded as-is, not sliced), a regression test was added (`tests/test_build_esm2_index.py`), and the whole index was **rebuilt from scratch** — all numbers below are from that clean rebuild.
- **Rebuilt after the sampling fix (true random subset, seed=42). Real build time: 373.5 seconds (~6.2 min), batch=16, CPU.** Zero-norm/NaN rows: 0/5000 (verified).
- **Hubness distribution (k=10, n=5,000, true random subset):** mean N_k = 10.00 (expected), **std = 8.38, max = 94, skewness = 2.09** (hub_threshold = mean + 3×std = 35.13). Close to the numbers from the earlier (accession-ascending) subset (std=8.70, max=90, skew=2.20) — meaning the hubness phenomenon is not a function of the sampling method, but a property of the ESM-2 embedding space itself, which is exactly what this diagnostic is meant to catch.
- **Top-5 hub proteins (true random subset):**
  ```
  N_k=94  P0A2D7  HTH-type transcriptional regulatory protein TyrR
  N_k=77  Q5U5R9  Probable E3 ubiquitin-protein ligase HECTD2
  N_k=70  Q6BLS3  GMP synthase [glutamine-hydrolyzing]
  N_k=67  P22021  Mating-type locus allele B7 protein
  N_k=64  P53137  RQC trigger complex subunit CUE3
  ```
  (full top-20 in `data/esm2_hubness.json`).
- **Real kNN query on p53 (P04637, not in the index), top-10 honestly (true random subset):**
  ```
  0.984  hub=YES ko=40  Q99PI4  Phosphatidate phosphatase LPIN3 (Lipin-3)
  0.980  hub=No  ko=11  Q08DD6  Interferon regulatory factor 6 (IRF-6)
  0.980  hub=YES ko=37  Q9H147  Deoxynucleotidyltransferase terminal-interacting protein 1
  0.980  hub=No  ko=26  Q64448  Gap junction alpha-3 protein (Connexin-46)
  0.978  hub=YES ko=67  P22021  Mating-type locus allele B7 protein
  0.978  hub=No  ko=30  Q66HC7  Transcription factor SPT20 homolog
  0.977  hub=YES ko=47  Q3KQ71  Sodium channel modifier 1
  0.976  hub=No  ko=33  Q3UNW5  Transcription factor CP2-like protein 1
  0.976  hub=No  ko=16  Q13568  Interferon regulatory factor 5 (IRF-5)
  0.976  hub=YES ko=43  Q9H6S3  EPS8-like protein 2
  ```
  Runtime: ~8.2 seconds. **Being honest about this:** this true-random 5,000-subset has no direct ortholog/homolog of p53 (the earlier accession-ascending subset happened to contain mouse p53, cos_sim 0.995 — that's gone now with the corrected sampling). This time the neighbours are not clearly biologically related to p53 (a random mix of IRF-6, a connexin, an ion channel, etc.) — and **5 of the 10 are hub-flagged**, which is precisely the warning this tool is designed to give: in a small subset, hub-heavy neighbours should be down-weighted rather than trusted, exactly as the agent's system prompt instructs.
- **To reproduce:** `python -m tools.build_esm2_index --batch-size 16 --k 10` (`data/` is not in git). Fixture: `tests/fixtures/esm2_knn_result_P04637.json`.

---

## Not implemented yet

- **PubMed E-utilities** — lowest priority, not started yet.
