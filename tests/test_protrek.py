from unittest.mock import patch

from tools import protrek


def test_protrek_ranks_candidates_by_score():
    """Uses the real scores captured from this project's live verification
    call on human p53 (P04637) against 5 candidate descriptions -- see
    docs/TOOLS.md for the honest note that the top-1 result was WRONG
    (structural protein) with the correct answer (tumor suppressor) a close
    second. This test only checks the wrapper's ranking/summary logic, not
    model correctness."""
    fixed_scores = [
        ("A structural protein found in hair and nails", 7.802918434143066),
        ("A tumor suppressor protein that prevents cancer by regulating the cell cycle", 7.239231586456299),
        ("A hormone that regulates blood glucose levels", 5.97115421295166),
        ("An enzyme that digests starch into sugars", -0.39910703897476196),
        ("A photosynthesis-related protein found in plants", -1.7391657829284568),
    ]

    with patch("tools.protrek._load_model", return_value="fake-model"), patch(
        "tools.protrek._score", return_value=fixed_scores
    ):
        ev = protrek.run("MEEPQSDP...", [t for t, _ in fixed_scores], device="cpu")

    assert ev.error is None
    assert ev.raw_scores["ranked"][0]["text"] == "A structural protein found in hair and nails"
    assert len(ev.raw_scores["ranked"]) == 5
    assert "structural protein" in ev.result_summary


def test_protrek_missing_weights_becomes_error_not_exception(tmp_path):
    missing_dir = tmp_path / "does_not_exist"
    with patch("tools.protrek._apply_monkeypatches", side_effect=RuntimeError("torchmetrics not installed")):
        ev = protrek.run("MEEPQSDP...", ["a description"], weights_dir=missing_dir, device="cpu")

    assert ev.error is not None
