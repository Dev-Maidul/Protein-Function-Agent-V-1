from unittest.mock import patch

from tests.conftest import fake_response, load_fixture_json
from tools import uniprot


def test_uniprot_p53_fixture():
    fixture = load_fixture_json("uniprot_P04637.json")
    with patch("tools.uniprot.requests.get", return_value=fake_response(json_body=fixture)):
        ev = uniprot.run("P04637")

    assert ev.tool == "uniprot"
    assert ev.error is None
    assert "p53" in ev.result_summary.lower() or "tumor" in ev.result_summary.lower()
    assert ev.raw_scores["go_term_count"] > 0
    assert ev.raw_scores["sequence_length"] == 393
    assert "P04637" in ev.source_ids


def test_uniprot_error_becomes_evidence_not_exception():
    with patch("tools.uniprot.requests.get", side_effect=ConnectionError("boom")):
        ev = uniprot.run("P04637")

    assert ev.error is not None
    assert "boom" in ev.error
