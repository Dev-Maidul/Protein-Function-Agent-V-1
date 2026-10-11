from eval.metrics import fmax, precision_recall_at_threshold


def test_precision_recall_perfect_match():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.9}, {"go_id": "GO:B", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:A", "GO:B"}}
    p, r, n = precision_recall_at_threshold(predictions, ground_truth, None, 0.5)
    assert p == 1.0
    assert r == 1.0
    assert n == 1


def test_precision_recall_partial_match():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.9}, {"go_id": "GO:C", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:A", "GO:B"}}
    p, r, n = precision_recall_at_threshold(predictions, ground_truth, None, 0.5)
    assert p == 0.5  # 1 of 2 predicted terms correct
    assert r == 0.5  # 1 of 2 true terms recovered


def test_precision_recall_no_predictions_above_threshold():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.3}]}
    ground_truth = {"P1": {"GO:A"}}
    p, r, n = precision_recall_at_threshold(predictions, ground_truth, None, 0.5)
    assert p == 0.0
    assert r == 0.0
    assert n == 0


def test_fmax_picks_best_threshold():
    # Protein has 2 true terms. At confidence 0.9 it predicts both correctly
    # (F=1.0); a lower-confidence extra wrong term at 0.3 would hurt
    # precision if the threshold were lowered to include it.
    predictions = {
        "P1": [
            {"go_id": "GO:A", "confidence": 0.9},
            {"go_id": "GO:B", "confidence": 0.9},
            {"go_id": "GO:WRONG", "confidence": 0.3},
        ]
    }
    ground_truth = {"P1": {"GO:A", "GO:B"}}
    result = fmax(predictions, ground_truth, thresholds=[0.2, 0.5, 0.95])
    assert result["fmax"] == 1.0
    assert result["threshold"] == 0.5


def test_fmax_zero_when_no_overlap():
    predictions = {"P1": [{"go_id": "GO:WRONG", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:A"}}
    result = fmax(predictions, ground_truth, thresholds=[0.5])
    assert result["fmax"] == 0.0


def test_fmax_coverage_counts_proteins_with_any_prediction():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:A"}, "P2": {"GO:B"}}  # P2 has no prediction at all
    result = fmax(predictions, ground_truth, thresholds=[0.5])
    assert result["coverage"] == 0.5
