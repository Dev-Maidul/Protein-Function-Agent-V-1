"""Local sequence homology search: DIAMOND blastp against a local Swiss-Prot
database. Runs entirely on CPU, no network, no rate limits -- the preferred
homology tool. (EBI/NCBI BLAST web API is documented as a fallback only, not
implemented here, since this local path is faster and has no quota.)

Requires:
- the `diamond` binary on PATH (installed in this project via `brew install diamond`)
- a pre-built index at DB_PATH, built once from Swiss-Prot with:
    diamond makedb --in uniprot_sprot.fasta --db swissprot
  (Swiss-Prot source: https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz,
   CC BY 4.0 license, same as tools/uniprot.py)

DIAMOND docs: https://github.com/bbuchfink/diamond/wiki
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.evidence import Evidence, timed_call

DB_PATH = Path(__file__).parent.parent / "data" / "swissprot.dmnd"
OUTFMT_FIELDS = [
    "qseqid", "sseqid", "pident", "length", "mismatch", "gapopen",
    "qstart", "qend", "sstart", "send", "evalue", "bitscore", "stitle",
]
UNIPROT_ACC_RE = re.compile(r"^sp\|([A-Z0-9]+)\|")


def _parse_tsv(tsv_text: str) -> list[dict]:
    hits = []
    for line in tsv_text.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        row = dict(zip(OUTFMT_FIELDS, parts))
        acc_match = UNIPROT_ACC_RE.match(row["sseqid"])
        hits.append(
            {
                "uniprot_accession": acc_match.group(1) if acc_match else row["sseqid"],
                "pident": float(row["pident"]),
                "evalue": float(row["evalue"]),
                "bitscore": float(row["bitscore"]),
                "aligned_length": int(row["length"]),
                "title": row["stitle"],
            }
        )
    hits.sort(key=lambda h: h["evalue"])
    return hits


def _run_diamond(sequence: str, query_id: str, db_path: Path, top_n: int) -> dict:
    if not shutil.which("diamond"):
        raise RuntimeError("the `diamond` binary is not on PATH (brew install diamond)")
    if not db_path.exists():
        raise RuntimeError(f"DIAMOND database not found at {db_path} (run `diamond makedb` first)")

    with tempfile.TemporaryDirectory() as tmp:
        query_path = Path(tmp) / "query.fasta"
        out_path = Path(tmp) / "hits.tsv"
        query_path.write_text(f">{query_id}\n{sequence}\n")

        subprocess.run(
            [
                "diamond", "blastp",
                "-q", str(query_path),
                "-d", str(db_path),
                "-o", str(out_path),
                "--outfmt", "6", *OUTFMT_FIELDS,
                "--max-target-seqs", str(top_n),
                "--quiet",
            ],
            check=True,
            timeout=120,
        )
        tsv_text = out_path.read_text() if out_path.exists() else ""

    hits = _parse_tsv(tsv_text)
    top = hits[0] if hits else None
    result_summary = (
        f"{len(hits)} Swiss-Prot homolog(s); top hit={top['uniprot_accession']} "
        f"(identity={top['pident']}%, eval={top['evalue']:.2e}) -- {top['title']}"
        if top
        else "no homologs found in Swiss-Prot"
    )
    return {
        "result_summary": result_summary,
        "raw_scores": {"hit_count": len(hits), "hits": hits},
        "source_ids": [h["uniprot_accession"] for h in hits],
        "source_urls": [f"https://www.uniprot.org/uniprotkb/{h['uniprot_accession']}" for h in hits],
    }


def run(sequence: str, query_id: str = "query", db_path: Path | None = None, top_n: int = 10) -> Evidence:
    db_path = db_path or DB_PATH
    return timed_call("diamond_blast", query_id, _run_diamond, sequence, query_id, db_path, top_n)
