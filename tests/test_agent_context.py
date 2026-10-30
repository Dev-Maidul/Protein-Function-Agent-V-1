"""Regression tests for the 2026-10-30 DIAMOND exclusion-cap leakage bug.

agent/context.py's build_exclusions() used to call diamond_blast.run() with
the default top_n=10, which capped DIAMOND's own --max-target-seqs at 10
BEFORE the >=50% identity filter ran. For any protein with more than 10
Swiss-Prot entries at >=50% identity (e.g. human p53 has 36), only the top 10
by e-value were ever considered -- silently missing the rest, confirmed live:
P53_CAVPO/Q9WUR6 at 79.7% identity was never excluded. Fixed by passing
top_n=5000 explicitly in that one call.
"""
from pathlib import Path
from unittest.mock import patch

from tests.conftest import FIXTURES_DIR
from agent.context import AgentContext


def _fake_diamond_subprocess(fixture_name: str, expect_max_target_seqs: str | None = None):
    def _run(cmd, **kwargs):
        if expect_max_target_seqs is not None:
            assert expect_max_target_seqs in cmd, (
                f"expected --max-target-seqs {expect_max_target_seqs} in DIAMOND command, got: {cmd}"
            )
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text((FIXTURES_DIR / fixture_name).read_text())

    return _run


def test_build_exclusions_uncapped_catches_all_ge50_identity_hits(tmp_path):
    """The fixture is the REAL uncapped DIAMOND output for p53 (40 raw hits,
    captured live with --max-target-seqs 5000 against this project's own
    Swiss-Prot index on 2026-10-30). 36 of those 40 are at >=50% identity
    (including the query's own 100% self-hit) -- all 36 must end up in
    exclude_accessions, including Q9WUR6 (79.7%), which the old top_n=10 cap
    missed."""
    sequence = (FIXTURES_DIR / "query_p53.fasta").read_text().splitlines()[1]
    fake_db = tmp_path / "swissprot.dmnd"
    fake_db.write_text("not a real db, just needs to exist")

    ctx = AgentContext(query_sequence=sequence, query_accession="P04637", eval_mode=True)
    with patch("tools.diamond_blast.shutil.which", return_value="/usr/local/bin/diamond"), patch(
        "tools.diamond_blast.DB_PATH", fake_db
    ), patch(
        "tools.diamond_blast.subprocess.run",
        side_effect=_fake_diamond_subprocess("diamond_blastp_P04637_uncapped.tsv", expect_max_target_seqs="5000"),
    ):
        ctx.build_exclusions()

    assert len(ctx.exclude_accessions) == 36
    assert "Q9WUR6" in ctx.exclude_accessions
    assert "P04637" in ctx.exclude_accessions  # the query's own accession


def test_build_exclusions_unchanged_for_protein_with_few_homologs(tmp_path):
    """A protein with only 3 raw DIAMOND hits total (well under the old
    top_n=10 cap) must behave identically before and after the fix -- the
    cap was never the limiting factor for it, so raising it to 5000 should
    not change which accessions get excluded."""
    sequence = "MFAKEPROTEINSEQUENCEFORTESTINGPURPOSESONLYNOTAREALPROTEIN"
    fake_db = tmp_path / "swissprot.dmnd"
    fake_db.write_text("not a real db, just needs to exist")

    ctx = AgentContext(query_sequence=sequence, query_accession="Q00000", eval_mode=True)
    with patch("tools.diamond_blast.shutil.which", return_value="/usr/local/bin/diamond"), patch(
        "tools.diamond_blast.DB_PATH", fake_db
    ), patch(
        "tools.diamond_blast.subprocess.run",
        side_effect=_fake_diamond_subprocess("diamond_blastp_small_protein.tsv"),
    ):
        ctx.build_exclusions()

    # fixture has 3 hits: self (100%), A11111 (70%, >=50 -> excluded), B22222 (30%, <50 -> kept)
    assert ctx.exclude_accessions == {"Q00000", "A11111"}
    assert "B22222" not in ctx.exclude_accessions
