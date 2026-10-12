from unittest.mock import patch

from tests.conftest import fake_response, load_fixture_json
from tools import alphafold_foldseek as af


def test_alphafold_foldseek_full_pipeline_p53():
    af_meta = load_fixture_json("alphafold_P04637.json")
    ticket = load_fixture_json("foldseek_ticket.json")
    result = load_fixture_json("foldseek_result_P04637.json")
    pdb_bytes = b"HEADER fake pdb bytes for test\n"

    pdb_response = fake_response(text_body="irrelevant", status_code=200)
    pdb_response.content = pdb_bytes
    responses = [
        fake_response(json_body=af_meta),  # AFDB metadata lookup
        pdb_response,  # pdb file fetch
        fake_response(json_body=ticket),  # first poll -> already COMPLETE, no sleep
        fake_response(json_body=result),  # result fetch
    ]

    with patch(
        "tools.alphafold_foldseek.requests.get", side_effect=responses
    ) as get, patch(
        "tools.alphafold_foldseek.requests.post", return_value=fake_response(json_body=ticket)
    ) as post:
        ev = af.run("P04637")

    assert ev.error is None
    # real fixture: 179 structural neighbours in afdb-swissprot, top hit is the
    # query's own exact AlphaFold entry (seqId=100, lowest e-value)
    assert ev.raw_scores["hit_count"] == 179
    assert ev.raw_scores["hits"][0]["seq_identity_pct"] == 100
    assert ev.raw_scores["hits"][0]["target"].startswith("AF-P04637")
    assert ev.raw_scores["hits"][0]["accession"] == "P04637"
    post.assert_called_once()


def test_alphafold_foldseek_excludes_accessions():
    """Regression test: like diamond_blast_search, this tool had no leakage
    control at all before -- the agent could see its own query's AlphaFold
    entry as the top Foldseek hit even in eval_mode."""
    af_meta = load_fixture_json("alphafold_P04637.json")
    ticket = load_fixture_json("foldseek_ticket.json")
    result = load_fixture_json("foldseek_result_P04637.json")
    pdb_response = fake_response(text_body="irrelevant", status_code=200)
    pdb_response.content = b"HEADER fake pdb bytes for test\n"
    responses = [fake_response(json_body=af_meta), pdb_response, fake_response(json_body=ticket), fake_response(json_body=result)]

    with patch("tools.alphafold_foldseek.requests.get", side_effect=responses), patch(
        "tools.alphafold_foldseek.requests.post", return_value=fake_response(json_body=ticket)
    ):
        ev = af.run("P04637", exclude_accessions={"P04637"})

    assert ev.error is None
    assert ev.raw_scores["excluded_count"] == 1
    assert all(h["accession"] != "P04637" for h in ev.raw_scores["hits"])


def test_alphafold_not_found_becomes_error():
    with patch("tools.alphafold_foldseek.requests.get", return_value=fake_response(json_body=[])):
        ev = af.run("NOTAREALACCESSION")

    assert ev.error is not None
    assert "No AlphaFold structure" in ev.error
