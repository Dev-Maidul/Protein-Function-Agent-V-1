from unittest.mock import patch

from tests.conftest import fake_response, load_fixture_json
from tools import interproscan


def test_interproscan_full_pipeline_p53():
    job_id = "iprscan5-R20261010-181733-0487-99296560-p1m"
    result_fixture = load_fixture_json("interproscan_result_P04637.json")

    with patch("tools.interproscan.requests.post", return_value=fake_response(text_body=job_id)) as post, patch(
        "tools.interproscan.requests.get",
        side_effect=[
            fake_response(text_body="FINISHED"),  # first poll already finished -> no sleep
            fake_response(json_body=result_fixture),
        ],
    ):
        ev = interproscan.run("MEEPQSDPSVEPPLSQ", email="someone@example.com")

    assert ev.error is None
    # fixture has 24 raw signature matches, only 12 carry a curated InterPro entry
    assert ev.raw_scores["domain_count"] == 12
    assert any(d["interpro_id"] == "IPR011615" for d in ev.raw_scores["domains"])
    assert "GO:0000976" in ev.source_ids
    post.assert_called_once()


def test_interproscan_missing_email_becomes_error_not_exception():
    with patch.dict("os.environ", {}, clear=True):
        ev = interproscan.run("MEEPQSDPSVEPPLSQ", email=None)

    assert ev.error is not None
    assert "EBI_CONTACT_EMAIL" in ev.error


def test_interproscan_job_error_status_becomes_evidence_error():
    with patch("tools.interproscan.requests.post", return_value=fake_response(text_body="job123")), patch(
        "tools.interproscan.requests.get", return_value=fake_response(text_body="ERROR")
    ):
        ev = interproscan.run("MEEPQSDPSVEPPLSQ", email="someone@example.com")

    assert ev.error is not None
    assert "ERROR" in ev.error
