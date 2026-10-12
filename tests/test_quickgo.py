from unittest.mock import patch

from tests.conftest import fake_response, load_fixture_json
from tools import quickgo


def test_quickgo_valid_term():
    term_fixture = load_fixture_json("quickgo_term_GO-0006915.json")
    ancestors_fixture = load_fixture_json("quickgo_ancestors_GO-0006915.json")
    with patch(
        "tools.evidence.requests.get",
        side_effect=[fake_response(json_body=term_fixture), fake_response(json_body=ancestors_fixture)],
    ):
        ev = quickgo.run("GO:0006915")

    assert ev.error is None
    assert ev.raw_scores["valid"] is True
    assert ev.raw_scores["name"] == "apoptotic process"
    assert ev.raw_scores["ancestor_count"] == 5
    assert "GO:0008150" in ev.source_ids


def test_quickgo_invalid_term():
    invalid_fixture = load_fixture_json("quickgo_term_invalid.json")
    with patch("tools.evidence.requests.get", return_value=fake_response(json_body=invalid_fixture)):
        ev = quickgo.run("GO:9999999")

    assert ev.error is None
    assert ev.raw_scores["valid"] is False
