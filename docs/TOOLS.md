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

---

## এখনো implement হয়নি (bigger downloads লাগে, ব্যবহারকারীর অনুমতির অপেক্ষায়)

- **Local DIAMOND/BLAST বনাম Swiss-Prot** — ~90MB Swiss-Prot FASTA download +
  DIAMOND index build লাগবে।
- **ProTrek_35M (westlake-repl)** — ~140-700MB checkpoint download লাগবে
  (model variant অনুযায়ী)।
- **ESM-2 (35M/150M) + kNN + hubness** — model weight download + একটা
  Swiss-Prot subset-এর embedding বানাতে হবে।
- **PubMed E-utilities** — lowest priority, এখনো শুরু করা হয়নি।
