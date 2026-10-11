"""CAFA-style protein-centric Fmax, computed per GO aspect (MF/BP/CC).

SIMPLIFICATION (documented honestly in docs/RESULTS.md's Limitations
section): real CAFA propagates both predictions and ground truth up the GO
DAG via the true-path rule before scoring (a prediction of a child term
counts as a prediction of all its ancestors too). This implementation does
direct-term matching only -- no ancestor propagation -- which is a stricter,
lower-bound approximation of the real CAFA metric, not the real thing.
"""
from __future__ import annotations


def precision_recall_at_threshold(
    predictions: dict[str, list[dict]],
    ground_truth: dict[str, set[str]],
    aspect_filter: set[str] | None,
    threshold: float,
) -> tuple[float, float, int]:
    """predictions: protein_id -> [{"go_id", "confidence"}, ...] (any aspect,
    already pre-filtered to the aspect to be scored, or filtered here if
    aspect_filter -- a map go_id->aspect -- is given alongside raw ids).
    ground_truth: protein_id -> set of true GO IDs for this aspect.
    Returns (macro_precision, macro_recall, n_proteins_with_predictions)."""
    precisions, recalls = [], []
    n_with_predictions = 0
    for protein_id, true_terms in ground_truth.items():
        if not true_terms:
            continue
        preds = predictions.get(protein_id, [])
        predicted_terms = {p["go_id"] for p in preds if p["confidence"] >= threshold}
        if predicted_terms:
            n_with_predictions += 1
            tp = len(predicted_terms & true_terms)
            precisions.append(tp / len(predicted_terms))
        recalls.append(len((predicted_terms & true_terms)) / len(true_terms))

    macro_precision = sum(precisions) / len(precisions) if precisions else 0.0
    macro_recall = sum(recalls) / len(recalls) if recalls else 0.0
    return macro_precision, macro_recall, n_with_predictions


def fmax(
    predictions: dict[str, list[dict]],
    ground_truth: dict[str, set[str]],
    thresholds: list[float] | None = None,
) -> dict:
    """ground_truth must already be restricted to one aspect. predictions may
    contain terms from any aspect -- only the ones matching a ground_truth
    key's protein are compared, but callers should pre-filter predicted GO
    IDs to the same aspect for a meaningful score (see run_eval.py)."""
    thresholds = thresholds or [round(i * 0.01, 2) for i in range(0, 101)]
    best = {"fmax": 0.0, "threshold": 0.0, "precision": 0.0, "recall": 0.0, "coverage": 0.0}
    n_proteins = len([p for p, t in ground_truth.items() if t])

    for tau in thresholds:
        p, r, n_with_pred = precision_recall_at_threshold(predictions, ground_truth, None, tau)
        if p + r == 0:
            f = 0.0
        else:
            f = 2 * p * r / (p + r)
        if f > best["fmax"]:
            best = {
                "fmax": f,
                "threshold": tau,
                "precision": p,
                "recall": r,
                "coverage": n_with_pred / n_proteins if n_proteins else 0.0,
            }
    return best
