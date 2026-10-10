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

## 6. ProTrek_35M (`tools/protrek.py`)
- **কী করে:** একটা protein sequence ও কয়েকটা candidate function-description
  (text) দিলে, কোনটা সবচেয়ে বেশি মিলছে সেটা trimodal contrastive embedding
  দিয়ে rank করে।
- **Source:** কোড — https://github.com/westlake-repl/ProTrek (MIT license),
  `vendor/ProTrek/`-এ clone করা (মাত্র ~2.1MB, কোনো বড় ফাইল নেই; `.gitignore`-এ
  আছে, reproduce করতে: `git clone --depth 1 https://github.com/westlake-repl/ProTrek.git vendor/ProTrek`)।
  Weight — https://huggingface.co/westlake-repl/ProTrek_35M_UniRef50 (~678MB) —
  **নতুন করে download করা হয়নি**, আগে থেকেই `Westlake-ProteinAI-Prep/ProTrek/weights/`-এ
  থাকা checkpoint `weights/ProTrek_35M_UniRef50`-এ symlink করা হয়েছে
  (অন্য মেশিনে চালাতে হলে সরাসরি HuggingFace থেকে download করতে হবে)।
- **তিনটা version-mismatch monkey-patch লাগে** (আগের প্রজেক্টেই আবিষ্কার করা
  হয়েছিল, এখানে পুনরায় ব্যবহার করা হয়েছে) — repo পুরনো `torchmetrics`/
  `transformers` API আশা করে।
- **Verify করা হয়েছে (সৎভাবে রিপোর্ট করা হচ্ছে):** human p53 (P04637)-এর পূর্ণ
  sequence দিয়ে ৫টা candidate description-এর সাথে মিলিয়ে দেখা হয়েছে real
  wrapper code দিয়েই (mock না) — মডেল লোড+inference মিলিয়ে **~8.4 সেকেন্ড**
  (MPS backend)। **ফলাফল এবার ভুল এসেছে:** top-1 prediction হলো "A structural
  protein found in hair and nails" (+7.803), আর সঠিক উত্তর "A tumor suppressor
  protein..." (+7.239) খুব কাছাকাছি স্কোরে দ্বিতীয় হয়েছে। আগের প্রজেক্টে
  insulin দিয়ে test করে সঠিক top-1 পাওয়া গিয়েছিল — কিন্তু p53-এ এবার ভুল
  হয়েছে, এটা **লুকানো হচ্ছে না**। এটা নিজেই একটা গুরুত্বপূর্ণ পর্যবেক্ষণ —
  model সবসময় সঠিক না, candidate-set-এর উপরও নির্ভর করে (p53-এর অনেক domain
  structural/binding-related, তাই "structural protein" confuse করতে পারে)।
  Phase 3 evaluation-এ এটা আরও systematic ভাবে measure করা হবে। Fixture:
  `tests/fixtures/protrek_scores_P04637.json`।

---

## এখনো implement হয়নি (bigger download লাগে, ব্যবহারকারীর অনুমতির অপেক্ষায়)

- **ESM-2 (35M/150M) + kNN + hubness** — model weight download + একটা
  Swiss-Prot subset-এর embedding বানাতে হবে।
- **PubMed E-utilities** — lowest priority, এখনো শুরু করা হয়নি।
