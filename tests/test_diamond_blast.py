from pathlib import Path
from unittest.mock import patch

from tests.conftest import FIXTURES_DIR
from tools import diamond_blast


def _fake_diamond_run(cmd, **kwargs):
    out_path = Path(cmd[cmd.index("-o") + 1])
    out_path.write_text((FIXTURES_DIR / "diamond_blastp_P04637.tsv").read_text())


def test_diamond_blast_p53_fixture(tmp_path):
    sequence = (FIXTURES_DIR / "query_p53.fasta").read_text().splitlines()[1]
    fake_db = tmp_path / "swissprot.dmnd"
    fake_db.write_text("not a real db, just needs to exist")

    with patch("tools.diamond_blast.shutil.which", return_value="/usr/local/bin/diamond"), patch(
        "tools.diamond_blast.subprocess.run", side_effect=_fake_diamond_run
    ):
        ev = diamond_blast.run(sequence, query_id="P04637_query", db_path=fake_db)

    assert ev.error is None
    assert ev.raw_scores["hit_count"] == 10
    top = ev.raw_scores["hits"][0]
    assert top["uniprot_accession"] == "P04637"
    assert top["pident"] == 100.0
    assert "P04637" in ev.source_ids


def test_diamond_blast_excludes_accessions(tmp_path):
    """Regression test: the agent's own diamond_blast_search tool call used to
    have no leakage control at all (unlike eval/baselines.py's diamond_transfer,
    which post-filtered). Fixed by adding exclude_accessions here directly."""
    sequence = (FIXTURES_DIR / "query_p53.fasta").read_text().splitlines()[1]
    fake_db = tmp_path / "swissprot.dmnd"
    fake_db.write_text("not a real db, just needs to exist")

    with patch("tools.diamond_blast.shutil.which", return_value="/usr/local/bin/diamond"), patch(
        "tools.diamond_blast.subprocess.run", side_effect=_fake_diamond_run
    ):
        ev = diamond_blast.run(sequence, query_id="P04637_query", db_path=fake_db, exclude_accessions={"P04637"})

    assert ev.error is None
    assert ev.raw_scores["excluded_count"] == 1
    assert all(h["uniprot_accession"] != "P04637" for h in ev.raw_scores["hits"])
    assert "P04637" not in ev.source_ids


def test_diamond_blast_missing_binary_becomes_error():
    with patch("tools.diamond_blast.shutil.which", return_value=None):
        ev = diamond_blast.run("MEEPQSDP", query_id="q1")

    assert ev.error is not None
    assert "diamond" in ev.error.lower()


def test_diamond_blast_missing_db_becomes_error(tmp_path):
    missing_db = tmp_path / "does_not_exist.dmnd"
    with patch("tools.diamond_blast.shutil.which", return_value="/usr/local/bin/diamond"):
        ev = diamond_blast.run("MEEPQSDP", query_id="q1", db_path=missing_db)

    assert ev.error is not None
    assert "database not found" in ev.error
