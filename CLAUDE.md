# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**সবসময় বাংলায় আলোচনা করবে।** কোড/technical term ইংরেজিতে থাকতে পারে, কিন্তু
ব্যাখ্যা, চিন্তা, আলোচনা, প্রশ্ন — সবকিছু বাংলায় লিখবে।

## Project

একটা LLM agent যে অনেকগুলো biological/computational tool orchestrate করে একটা
protein-এর function infer করে (sequence, optionally UniProt accession বা
structure file থেকে)। Prof. Fajie Yuan (Westlake, SaProt/ProTrek/Pinal/Evolla-এর
author)-এর suggestion অনুযায়ী বানানো হচ্ছে — উদ্দেশ্য তাঁকে architecture ও প্রাথমিক
ফলাফল পাঠানো।

Output: function summary + GO term prediction (confidence সহ) + evidence table
(প্রতিটা claim কোন tool থেকে এসেছে, raw score, source link) + disagreement/
uncertainty section। **প্রতিটা claim অবশ্যই একটা tool call-এ traceable হতে হবে।**

## Hard constraints (সবসময় মানতে হবে)

- **কোনো GPU নেই।** সবকিছু normal laptop CPU-তে, বা free public web API দিয়ে
  চলতে হবে।
- Free/publicly-accessible tool-কে প্রায়োরিটি দিতে হবে। প্রতিটা tool-এর
  license/terms of use `docs/TOOLS.md`-এ লিখে রাখতে হবে।
- **কোনো API endpoint অনুমান/invent করা যাবে না।** Wrapper লেখার আগে: tool-এর
  current official doc পড়তে হবে, তারপর একটা real live test call করে raw
  response `tests/fixtures/`-এ save করতে হবে। কোনো API unavailable হলে
  বলে দিতে হবে এবং সেই tool skip করতে হবে।
- **কখনো result/number/example output বানানো (fabricate) করা যাবে না।** কিছু
  আসলে run করা না হলে স্পষ্ট করে বলতে হবে যে এটা run করা হয়নি।
- LLM provider pluggable হতে হবে (OpenAI-compatible chat API, tool/function
  calling সহ), `.env` দিয়ে configure করা — কোনো key hard-code করা যাবে না।
- কোড simple ও readable রাখতে হবে — ব্যবহারকারীকে প্রতিটা অংশ interview-এ
  ব্যাখ্যা করতে পারতে হবে।

## Architecture

```
agent/       – agent loop (LLM + tool calling, max steps, evidence ledger)
tools/       – প্রতিটা tool-এর জন্য একটা ফাইল; প্রতিটা একটা standard JSON
               "Evidence" object return করে:
               {tool, query, result_summary, raw_scores, source_ids,
                source_urls, runtime_s, error}
eval/        – benchmark set, leakage control, baseline, metric
docs/        – ARCHITECTURE.md, TOOLS.md, RESULTS.md
examples/    – কয়েকটা protein-এর জন্য সম্পূর্ণ agent trace
runs/        – প্রতিটা run-এর full trace (<protein>/<timestamp>/)
tests/fixtures/ – প্রতিটা tool-এর real live call-এর saved raw response
```

## Tools (candidate list, verified availability — `docs/TOOLS.md`-এ বিস্তারিত)

1. UniProt REST API — ground truth-ও এটা থেকেই আসে, তাই evaluation mode-এ এই
   tool agent-কে ব্যবহার করতে দেওয়া হয় না
2. Local DIAMOND/BLAST বনাম Swiss-Prot (fallback: EBI/NCBI BLAST web API)
3. InterProScan (EBI Job Dispatcher REST, async job — submit → poll → fetch)
4. AlphaFold DB (structure fetch) + Foldseek web search (async ticket)
5. ProTrek_35M (westlake-repl, MIT license) — sequence/structure → text retrieval
6. ESM-2 (35M/150M) embeddings + kNN + hubness diagnostic (k-occurrence)
7. QuickGO REST API — predicted GO term valid কিনা যাচাই, ancestor resolve
8. PubMed E-utilities (optional, lowest priority)

## Commands

```bash
# venv বানানো ও dependency install (একবারই)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# একটা tool-এর test চালানো (fixture দিয়ে, network লাগে না)
pytest tests/test_uniprot.py -v

# পুরো test suite
pytest -v
```
