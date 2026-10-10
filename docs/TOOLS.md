# Tools

প্রতিটা tool-এর জন্য: কী করে, API/license, এবং এই প্রজেক্টে একটা real live call দিয়ে
কীভাবে verify করা হয়েছে (raw response `tests/fixtures/`-এ সংরক্ষিত)।

## 1. UniProt (`tools/uniprot.py`)
- **কী করে:** একটা UniProt accession থেকে curated annotation আনে — protein name,
  organism, GO terms, function description, sequence।
- **API:** `GET https://rest.uniprot.org/uniprotkb/{accession}.json` — no auth।
- **License/terms:** UniProt data CC BY 4.0 (https://www.uniprot.org/help/license)।
- **Verify করা হয়েছে:** human p53 (P04637)-এ real call করে — 119টা GO cross-ref,
  function text, sequence (length 393) পাওয়া গেছে। Fixture:
  `tests/fixtures/uniprot_P04637.json`।
- **গুরুত্বপূর্ণ:** এটা agent-এর জন্য ground-truth-এর উৎসও (evaluation-এ ব্যবহৃত হবে),
  তাই Phase 2-এ "evaluation mode"-এ agent-কে এই tool call করতে দেওয়া হবে না।

## 2. QuickGO (`tools/quickgo.py`)
- **কী করে:** একটা predicted GO ID আসলে valid/obsolete কিনা, তার নাম ও ancestor
  (হায়ারার্কি-তে উপরের term) যাচাই করে।
- **API:** `GET https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/{id}` এবং
  `.../ancestors` — no auth।
- **License/terms:** EBI data free for all use (https://www.ebi.ac.uk/about/terms-of-use)।
- **Verify করা হয়েছে:** `GO:0006915` ("apoptotic process") দিয়ে — নাম, aspect,
  5টা ancestor পাওয়া গেছে। ইচ্ছাকৃতভাবে ভুল ID (`GO:9999999`) দিয়েও টেস্ট করা
  হয়েছে — API কোনো 404 না দিয়ে `numberOfHits: 0` দেয়, তাই wrapper সেটাকেই
  "invalid" হিসেবে ধরে (exception না)। Fixtures:
  `tests/fixtures/quickgo_term_GO-0006915.json`,
  `tests/fixtures/quickgo_ancestors_GO-0006915.json`,
  `tests/fixtures/quickgo_term_invalid.json`।

## 3. InterProScan (`tools/interproscan.py`)
- **কী করে:** একটা amino-acid sequence-এ protein domain/family (InterPro entry)
  খুঁজে বের করে, প্রতিটার সাথে GO cross-reference থাকলে সেটাও দেয়।
- **API:** EMBL-EBI Job Dispatcher REST (`iprscan5`), base
  `https://www.ebi.ac.uk/Tools/services/rest/iprscan5` — **async job**:
  `POST /run` → job ID, `GET /status/{id}` (poll), `GET /result/{id}/json`।
  Submission-এ একটা contact email লাগে (EBI-এর usage policy অনুযায়ী) —
  `.env`-এর `EBI_CONTACT_EMAIL`-এ সেট করতে হয়।
- **License/terms:** EBI Job Dispatcher terms (https://www.ebi.ac.uk/about/terms-of-use)।
- **Verify করা হয়েছে:** human p53 sequence (393 aa) দিয়ে real job submit করে —
  সময় লেগেছে **~1 মিনিট 35 সেকেন্ড** (RUNNING থেকে FINISHED), 24টা raw signature
  match পাওয়া গেছে যার মধ্যে 12টার curated InterPro entry আছে (বাকিগুলো
  integration ছাড়া raw signature hit) — যেমন `IPR011615` (p53 DNA-binding
  domain, এর সাথে `GO:0000976` জড়িত)। Fixture:
  `tests/fixtures/interproscan_result_P04637.json`।
- **সাবধানতা:** async job slow/stuck হতে পারে — wrapper-এ hard timeout
  (২০ মিনিট) ও fixed poll interval (১৫ সেকেন্ড) আছে, timeout হলে agent
  crash না করে `error` field-এ জানায়।

## 4. AlphaFold DB + Foldseek web search (`tools/alphafold_foldseek.py`)
- **কী করে:** একটা UniProt accession-এর AlphaFold-predicted 3D structure
  download করে, তারপর সেটা দিয়ে Foldseek-এ structurally similar protein খোঁজে
  (sequence না মিললেও structure মিলতে পারে এমন protein ধরতে পারে)।
- **API:**
  - AlphaFold DB metadata: `GET https://alphafold.ebi.ac.uk/api/prediction/{accession}`,
    structure file: pdbUrl থেকে সরাসরি download।
  - Foldseek web: `POST https://search.foldseek.com/api/ticket` (structure
    file upload) → ticket ID, `GET /api/ticket/{id}` (poll), `GET
    /api/result/{id}/0` (result) — no auth।
- **License/terms:** AlphaFold DB — CC BY 4.0। Foldseek web server — free,
  non-commercial research use (https://search.foldseek.com)।
- **Verify করা হয়েছে:** human p53 (P04637)-এর real AlphaFold structure
  download করে (pLDDT global metric 75.06), Foldseek-এ `afdb-swissprot`
  database-এ সার্চ করে — **179টা structural neighbour** পাওয়া গেছে, top hit
  (সবচেয়ে কম e-value) ছিল p53-এর নিজের AlphaFold entry (seqId=100%, যেটা
  প্রত্যাশিত sanity check)। সার্চ real-এ instant-ই COMPLETE হয়ে গেছে (একই
  response-এ), কিন্তু general case-এর জন্য polling/timeout কোডও রাখা হয়েছে।
  Fixtures: `tests/fixtures/alphafold_P04637.json`,
  `tests/fixtures/foldseek_ticket.json`,
  `tests/fixtures/foldseek_result_P04637.json`।
- **সাবধানতা:** AlphaFold-এর file server কোনো `User-Agent` header ছাড়া
  request-এ `403` দেয় (এই প্রজেক্টে লাইভ টেস্টে ধরা পড়েছে) — wrapper-এ তাই
  সবসময় একটা `User-Agent` header পাঠানো হয়।

## 5. Local DIAMOND/BLAST বনাম Swiss-Prot (`tools/diamond_blast.py`)
- **কী করে:** একটা amino-acid sequence-এর জন্য local Swiss-Prot database-এ
  homolog (একই রকম sequence-এর protein) খোঁজে — CPU-তে, network ছাড়া, কোনো
  rate limit নেই। EBI/NCBI BLAST web API fallback হিসেবে document করা আছে
  কিন্তু implement করা হয়নি, কারণ local path দ্রুত ও quota-ছাড়া।
- **Setup (একবারই):**
  ```bash
  brew install diamond
  curl -L "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz" -o data/uniprot_sprot.fasta.gz
  gunzip -k data/uniprot_sprot.fasta.gz
  diamond makedb --in data/uniprot_sprot.fasta --db data/swissprot
  ```
- **License/terms:** Swiss-Prot data CC BY 4.0 (UniProt-এর license, উপরে ১-এ
  উল্লেখ)। DIAMOND নিজে GPL-licensed open-source tool।
- **Verify করা হয়েছে:** human p53 (৩৯৩ aa) দিয়ে real `diamond blastp` চালিয়ে
  (সময় ~৭.৭ সেকেন্ড, পুরো index ৫,৭৫,৭৪৮টা sequence নিয়ে বানানো হয়েছে ~২.৮
  সেকেন্ডে) — top hit ছিল p53-এর নিজের human entry (100% identity, sanity
  check pass), তারপর বিভিন্ন primate-এর p53 ortholog (95.4%, 94.9%...) — ঠিক
  যেমন আশা করা যায়। Fixtures: `tests/fixtures/diamond_blastp_P04637.tsv`,
  `tests/fixtures/query_p53.fasta`।
- **নোট:** `data/` ফোল্ডারটা (~670MB — FASTA + .dmnd index) git-এ কমিট করা
  হয়নি (`.gitignore`-এ আছে) — উপরের তিনটা কমান্ড দিয়েই reproduce করা যায়।

## Shared Swiss-Prot subset (`tools/build_shared_subset.py`) — ProTrek ও ESM-2 দুটোই ব্যবহার করে

Phase 1-এর প্রথম ভার্সনে ProTrek-এর জন্য শুধু ৫টা হাতে-বাছা candidate text এবং
ESM-2-এর জন্য ৮০০টা random sequence ব্যবহার করা হয়েছিল — দুটোই অর্থবহ মূল্যায়নের
জন্য যথেষ্ট ছিল না। সেটা ঠিক করতে একটা **একক, দুটো tool-ই শেয়ার করা subset**
বানানো হয়েছে, যাতে result-গুলো তুলনীয় থাকে ও subset-selection একবারই document
করতে হয়:

- **Source:** UniProt REST stream API (`GET https://rest.uniprot.org/uniprotkb/stream`),
  `query=reviewed:true`, fields = accession/protein_name/organism_name/
  cc_function/sequence/length — একই tool #1-এর API, শুধু bulk/stream mode-এ।
- **Selection method (deterministic, random না — honestly document করা হচ্ছে):**
  UniProt-এর default stream order-এ (accession ascending) প্রথম থেকে entry
  পড়ে, যেগুলোর (ক) non-empty "Function [CC]" annotation আছে এবং (খ) sequence
  length ২০-১০০০ aa-র মধ্যে, সেগুলো রাখা হয়েছে — ৫,০০০টা এমন entry পাওয়ার পর
  থেমে গেছে। এটা truly random sample না (reproducible, কিন্তু accession-এর
  alphabetical ক্রম অনুযায়ী বায়াসড — মূলত "A0A0..." প্রিফিক্সের entry বেশি)।
  Phase 3-এ আরও ভালো (stratified/random) sampling নিয়ে ভাবা হবে।
- **Function text cleaning:** multi-chain polyprotein-এ একাধিক "FUNCTION: [Chain
  name]: ..." segment থাকে (`;`-separated) — প্রথমটা নেওয়া হয়, `{ECO:...}`
  evidence code ও `[Chain name]:` label বাদ দেওয়া হয়, ৩২০ character-এ truncate
  করা হয় (word boundary-তে)।
- **ফলাফল:** `data/shared_subset.tsv`-এ ৫,০০০টা row (~5MB) — build time
  **~25 সেকেন্ড** (pure network stream, কোনো heavy compute না)। Sequence
  length min/max/avg: 21/1000/464.6 aa। Function text length min/max/avg:
  7/322/271.5 chars। **p53 (P04637) এই subset-এ নেই** (alphabetically subset
  শেষ হয়েছে "O3xxxx"-এ, "P0xxxx" পর্যন্ত পৌঁছায়নি) — তাই নিচের p53 test-গুলোতে
  কোনো self-leakage নেই, এটা genuinely zero-shot retrieval।
- **Reproduce করতে:** `python -m tools.build_shared_subset --n 5000 --max-len 1000`
  (git-এ `data/` রাখা হয়নি)।

## 6. ProTrek_35M (`tools/protrek.py`, cache built by `tools/build_protrek_cache.py`)
- **কী করে:** একটা protein sequence দিলে উপরের shared subset-এর ৫,০০০টা real
  Swiss-Prot function-description থেকে সবচেয়ে ভালো মিলে যাওয়া top-k বের করে
  (প্রতিটার সাথে UniProt accession সহ) — আগের ভার্সন শুধু ৫টা হাতে-বাছা
  candidate-এর মধ্যে rank করত, যেটা অর্থবহ পরীক্ষা ছিল না (Phase 1 fix #1)।
- **Source:** কোড — https://github.com/westlake-repl/ProTrek (MIT license),
  `vendor/ProTrek/`-এ clone করা। Weight —
  https://huggingface.co/westlake-repl/ProTrek_35M_UniRef50 (~678MB) — আগে
  থেকে downloaded, `weights/ProTrek_35M_UniRef50`-এ symlink করা।
- **তিনটা version-mismatch monkey-patch লাগে** (আগের প্রজেক্টে discover করা,
  পুনর্ব্যবহার করা হয়েছে) — repo পুরনো `torchmetrics`/`transformers` API আশা করে।
- **Text-embedding cache বানানো (একবারই, offline):**
  `python -m tools.build_protrek_cache --batch-size 250` — শেয়ার্ড subset-এর
  ৫,০০০টা function-text-এর ProTrek text-embedding বানায় (batched, resumable
  checkpoint সহ)। **Real run time: ৬৩.৩ সেকেন্ড** (MPS, batch=250), ৫,০০০×১০২৪
  embedding matrix সংরক্ষিত হয় `data/protrek_text_embeddings.npz`-এ।
- **Evaluation mode:** caller `exclude_accessions` দিলে সেই accession-গুলো
  candidate pool থেকে বাদ পড়ে (query-র নিজের accession বা DIAMOND দিয়ে পাওয়া
  close homolog — threshold Phase 3-এ ঠিক হবে)।
- **Verify করা হয়েছে (নতুন, real 5,000-candidate pool দিয়ে, mock না):** human
  p53-এর পূর্ণ sequence দিয়ে top-10 retrieve করা হয়েছে real wrapper code দিয়ে —
  সময় **~8.6 সেকেন্ড** (MPS)। **এবার ফলাফল নাটকীয়ভাবে ভালো এবং সবই biologically
  relevant:**
  ```
  +17.569  O15350  Participates in the apoptotic response to DNA damage ... (ASPP2/TP53BP2)
  +14.176  O02703  Plays a role in the mitochondrial apoptotic process (BAX-family)
  +14.048  O14929  Histone acetyltransferase ... cell cycle (p300/CBP family)
  +13.518  A0A2K5TU92  NAD-dependent protein deacetylase (sirtuin family)
  +13.452  O15151  Contributes to p53/TP53 regulation; inhibits p53-mediated apoptosis (MDM4/MDMX)
  +13.272  G5EFF5  Nuclear receptor binding target gene promoters
  +13.207  O02718  Suppresses apoptosis (IAP-family)
  +13.109  O14737  In response to DNA damage, associates with TP53 to prevent MDM2-mediated degradation
  +13.083  A0A250YGJ5  NAD-dependent protein deacetylase (sirtuin family)
  +13.078  A0A0J9UVG7  NAD-dependent protein-lysine deacylase
  ```
  এই ১০টার প্রায় সবই সরাসরি p53/apoptosis/DNA-damage pathway-র সাথে সম্পর্কিত
  (বিশেষভাবে O15350 ASPP2 আর O14737 — এরা literally TP53-এর সাথে interact
  করে)। ছোট ৫-candidate test-এর (আগের) ভুল top-1 ফলাফলের তুলনায় এটা স্পষ্ট
  প্রমাণ করে — ছোট candidate pool দিয়ে পরীক্ষা করাটাই সমস্যা ছিল, model নিজে
  ভুল ছিল না। Fixture: `tests/fixtures/protrek_top10_p53.json`।

## 7. ESM-2 embedding + kNN + hubness diagnostic (`tools/esm2_knn.py`, index built by `tools/build_esm2_index.py`)
- **কী করে:** ESM-2 (35M) দিয়ে একটা protein sequence-এর embedding বানায়, তারপর
  উপরের shared subset (৫,০০০ sequence)-এর embedding index-এ cosine-similarity
  দিয়ে nearest neighbour খোঁজে। প্রতিটা neighbour-এর সাথে একটা **hubness flag**
  দেয় — অর্থাৎ এই neighbour কি বারবার "nearest" হিসেবে উঠে আসে অনেক আলাদা
  query-র জন্যও (k-occurrence বেশি), যেটা high-dimensional nearest-neighbour
  search-এর একটা পরিচিত সমস্যা (ব্যবহারকারীর নিজের hubness গবেষণার সাথে সরাসরি
  সম্পর্কিত) — hub flag করা neighbour-এর evidence কম বিশ্বাসযোগ্য ধরে নেওয়া হয়।
  আগের ভার্সন শুধু ৮০০টা random sequence ব্যবহার করত (Phase 1 fix #2, #3)।
- **Model:** https://huggingface.co/facebook/esm2_t12_35M_UR50D (Meta/FAIR ESM license)।
- **Index বানানো (একবারই, offline, batched + resumable checkpoint সহ):**
  ```bash
  python -m tools.build_esm2_index --batch-size 16 --k 10
  ```
  Shared subset (`data/shared_subset.tsv`, উপরে দেখুন)-এর ৫,০০০টা sequence-এর
  ESM-2 embedding বানায় (length অনুযায়ী sort করে batch করা হয়, padding-waste
  কমাতে — না করলে CPU-তে উল্টো ধীর হয়ে যায়, measured), mean-pooled, তারপর
  leave-one-out top-10 neighbour search করে প্রতিটা point-এর k-occurrence (N_k) গোনে।
- **⚠️ একটা real bug ধরা পড়েছে ও ঠিক করা হয়েছে (সৎভাবে রিপোর্ট করা হচ্ছে,
  লুকানো হচ্ছে না):** প্রথম রানে checkpoint resume logic ভুল ছিল —
  `embeddings[:done] = checkpoint[:done]` দিয়ে restore করা হতো, যেটা একটা
  *contiguous* slice, কিন্তু আসল write হয় length-sorted batch অনুযায়ী
  *scattered* position-এ (`order[]`)। ফলে প্রতিবার resume করার সময় বেশিরভাগ
  আগে-গণনা-করা row silently zero হয়ে যাচ্ছিল। শেষ ফলাফলে **৫,০০০-এর মধ্যে
  ১,৫৩২টা (৩০.৬%) embedding zero-vector ছিল**, যার কারণে hubness stats
  সম্পূর্ণ অবাস্তব এসেছিল (max k-occurrence 1542, একটা zero-vector-এর
  cosine-similarity NaN হয়ে argpartition-এ bias তৈরি করেছিল)। **এই bug
  ধরা পড়ার পর:** fix করা হয়েছে (checkpoint-এর পুরো সংরক্ষিত array-টাই
  as-is load করা হয়, slice না করে), একটা regression test লেখা হয়েছে
  (`tests/test_build_esm2_index.py`), এবং পুরো index **from scratch rebuild**
  করা হয়েছে — নিচের সব সংখ্যা সেই পরিষ্কার rebuild থেকে।
- **Real build time (clean rebuild, ৫,০০০ sequence, batch=16, CPU):
  ৫৩৮ সেকেন্ড (~৯ মিনিট)।** Zero-norm/NaN row: 0/5000 (verified)।
- **Hubness distribution (k=10, n=5,000):** mean N_k = 10.00 (প্রত্যাশিত —
  গড়ে প্রতিটা point ঠিক k বারই কারো top-k-এ আসার কথা), **std = 8.70, max = 90,
  skewness = 2.20** — mean-এর ৯ গুণ বেশি একটা single point (hub_threshold =
  mean + 3×std = 36.11)। Positive skew (2.20) নিশ্চিত করে real hubness
  phenomenon আছে এই subset-এও, যদিও আগের corrupted রানের (std=59.80, max=1542,
  skew=22.47) তুলনায় অনেক বেশি বিশ্বাসযোগ্য মাত্রার।
- **Top-5 hub protein (সবচেয়ে বেশি k-occurrence):**
  ```
  N_k=90  C6KIE6      Extra-large guanine nucleotide-binding protein 2
  N_k=86  A0A061AE05  Bifunctional PAPS synthase pps-1
  N_k=78  A2A8Z1      Oxysterol-binding protein-related protein 9
  N_k=69  H2KZH5      Target of ERK kinase mpk-1
  N_k=69  O18017      RecQ-like DNA helicase blm-1
  ```
  (সম্পূর্ণ top-20 `data/esm2_hubness.json`-এ)।
- **p53 (P04637, index-এ নেই) দিয়ে real kNN query, top-10 honestly:**
  ```
  0.995  hub=No   ko=18  O09185      Cellular tumor antigen p53 (mouse p53!)
  0.984  hub=YES  ko=55  D4A4K3      Beclin-1-associated autophagy regulator
  0.982  hub=No   ko= 9  O08856      RNA pol II elongation factor ELL
  0.982  hub=No   ko=34  E7FBF7      F-BAR domain only protein 1
  0.981  hub=No   ko=17  O15178      Brachyury (T-box transcription factor T)
  0.981  hub=YES  ko=47  A0A0R4IKJ1  mRNA cap methyltransferase
  0.981  hub=No   ko=16  O15198      SMAD9
  0.981  hub=No   ko= 4  O14896      Interferon regulatory factor 6
  0.981  hub=No   ko=17  F5HF68      VIRF-1
  0.980  hub=No   ko=16  A2ALS5      Rap1GAP1
  ```
  **সময়: ~8.2 সেকেন্ড।** Top hit **O09185 "Cellular tumor antigen p53"** —
  এটা আসলে mouse p53, মানে query (human p53)-এর খুব কাছের homolog, cos_sim
  0.995 — এটাই সবচেয়ে ভালো sanity check, bug-fix-এর পর ঠিক এই ধরনের ফলাফল
  আশা করা গিয়েছিল। বাকি নয়টা neighbour কম স্পষ্টভাবে সম্পর্কিত (এটা honest
  সীমাবদ্ধতা — mean-pooled embedding খুব precise functional retrieval না,
  general sequence-similarity ধরে), দুটো hub-flagged (D4A4K3, A0A0R4IKJ1,
  N_k 55/47, threshold 36.11 পার করেছে) — এগুলোর evidence কম বিশ্বাসযোগ্য
  ধরা উচিত।
- **Reproduce করতে:** `python -m tools.build_esm2_index --batch-size 16 --k 10`
  (git-এ `data/` রাখা হয়নি)। Fixture: `tests/fixtures/esm2_knn_result_P04637.json`।

---

## এখনো implement হয়নি

- **PubMed E-utilities** — lowest priority, এখনো শুরু করা হয়নি।
