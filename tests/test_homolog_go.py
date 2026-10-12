from unittest.mock import patch

from tools import homolog_go
from tools.evidence import Evidence


def _fake_uniprot_go(go_terms):
    def _run(accession):
        return Evidence(
            tool="uniprot", query=accession, result_summary="ok",
            raw_scores={"go_terms": go_terms.get(accession, [])}, source_ids=[], source_urls=[],
        )
    return _run


def test_homolog_go_aggregates_across_hits():
    go_by_acc = {
        "A1": [{"go_id": "GO:0001", "term": "x", "evidence": "IDA"}],
        "A2": [{"go_id": "GO:0001", "term": "x", "evidence": "IEA"}, {"go_id": "GO:0002", "term": "y", "evidence": "IDA"}],
    }
    with patch("tools.homolog_go.uniprot.run", side_effect=_fake_uniprot_go(go_by_acc)):
        ev = homolog_go.run(["A1", "A2"])

    assert ev.error is None
    go_ids = {g["go_id"] for g in ev.raw_scores["go_terms"]}
    assert go_ids == {"GO:0001", "GO:0002"}
    assert ev.raw_scores["excluded_count"] == 0
    assert "A1" in ev.source_ids and "A2" in ev.source_ids


def test_homolog_go_excludes_query_and_homologs():
    go_by_acc = {"A1": [{"go_id": "GO:0001", "term": "x", "evidence": "IDA"}]}
    with patch("tools.homolog_go.uniprot.run", side_effect=_fake_uniprot_go(go_by_acc)) as mock_run:
        ev = homolog_go.run(["QUERY_ACC", "A1"], exclude_accessions={"QUERY_ACC"})

    assert ev.error is None
    assert ev.raw_scores["excluded_count"] == 1
    assert ev.raw_scores["queried_accessions"] == ["A1"]
    mock_run.assert_called_once_with("A1")


def test_homolog_go_dedupes_accessions_from_multiple_sources():
    go_by_acc = {"A1": [{"go_id": "GO:0001", "term": "x", "evidence": "IDA"}]}
    with patch("tools.homolog_go.uniprot.run", side_effect=_fake_uniprot_go(go_by_acc)) as mock_run:
        # A1 found by both diamond and foldseek -- should only be queried once
        ev = homolog_go.run(["A1", "A1"])

    assert mock_run.call_count == 1
    assert ev.raw_scores["queried_accessions"] == ["A1"]


def test_homolog_go_all_excluded_no_crash():
    with patch("tools.homolog_go.uniprot.run") as mock_run:
        ev = homolog_go.run(["QUERY_ACC"], exclude_accessions={"QUERY_ACC"})

    assert ev.error is None
    assert ev.raw_scores["go_terms"] == []
    mock_run.assert_not_called()
