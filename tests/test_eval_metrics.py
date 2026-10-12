from eval.metrics import fmax, fmax_both, precision_recall_at_threshold


def test_precision_recall_perfect_match():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.9}, {"go_id": "GO:B", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:A", "GO:B"}}
    p, r, n = precision_recall_at_threshold(predictions, ground_truth, 0.5)
    assert p == 1.0
    assert r == 1.0
    assert n == 1


def test_precision_recall_partial_match():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.9}, {"go_id": "GO:C", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:A", "GO:B"}}
    p, r, n = precision_recall_at_threshold(predictions, ground_truth, 0.5)
    assert p == 0.5  # 1 of 2 predicted terms correct
    assert r == 0.5  # 1 of 2 true terms recovered


def test_precision_recall_no_predictions_above_threshold():
    predictions = {"P1": [{"go_id": "GO:A", "confidence": 0.3}]}
    ground_truth = {"P1": {"GO:A"}}
    p, r, n = precision_recall_at_threshold(predictions, ground_truth, 0.5)
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


def test_propagation_credits_shared_ancestor():
    # Predicted term GO:CHILD and true term GO:PARENT share no direct
    # match, but GO:CHILD's ancestor set includes GO:PARENT -- propagated
    # scoring should credit this; direct-match scoring should not.
    # Per the true-path rule, the predicted SET is expanded to {CHILD,
    # PARENT} before scoring, so precision = 1/2 (CHILD itself isn't a true
    # term), recall = 1/1 -> F = 2*0.5*1/(0.5+1) = 0.667, not a perfect 1.0 --
    # propagation gives partial credit, it doesn't pretend CHILD == PARENT.
    predictions = {"P1": [{"go_id": "GO:CHILD", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:PARENT"}}
    ancestor_cache = {"GO:CHILD": {"GO:PARENT"}}

    direct = fmax(predictions, ground_truth, thresholds=[0.5])
    propagated = fmax(predictions, ground_truth, thresholds=[0.5], ancestor_cache=ancestor_cache)

    assert direct["fmax"] == 0.0
    assert propagated["fmax"] == 2 / 3
    assert propagated["precision"] == 0.5
    assert propagated["recall"] == 1.0


def test_propagation_excludes_root_terms():
    predictions = {"P1": [{"go_id": "GO:CHILD", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:0008150"}}  # a root term, should never count
    ancestor_cache = {"GO:CHILD": {"GO:0008150"}}

    result = fmax(predictions, ground_truth, thresholds=[0.5], ancestor_cache=ancestor_cache)
    assert result["fmax"] == 0.0  # root term excluded from both sides, nothing left to match


def test_fmax_both_returns_direct_and_propagated():
    predictions = {"P1": [{"go_id": "GO:CHILD", "confidence": 0.9}]}
    ground_truth = {"P1": {"GO:PARENT"}}
    ancestor_cache = {"GO:CHILD": {"GO:PARENT"}}

    result = fmax_both(predictions, ground_truth, ancestor_cache, thresholds=[0.5])
    assert result["direct"]["fmax"] == 0.0
    assert result["propagated"]["fmax"] == 2 / 3
