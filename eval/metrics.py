"""CAFA-style protein-centric Fmax, computed per GO aspect (MF/BP/CC).

Two variants are reported everywhere this is used:
- DIRECT-MATCH Fmax: a predicted term only counts if it is the exact same
  GO ID as a ground-truth term. This is what Phase 3's first eval run used,
  flagged there as a stricter, lower-bound approximation of real CAFA.
- PROPAGATED Fmax: both predictions and ground truth are expanded to include
  all ancestor terms (true-path rule) before scoring, excluding the 3 GO
  root terms (GO:0008150 biological_process, GO:0005575 cellular_component,
  GO:0003674 molecular_function -- a "prediction" of the root carries no
  information). This is the real CAFA protocol. A prediction that shares an
  ancestor with a ground-truth term (e.g. predicted a child, true term is a
  parent, or vice versa) now counts as a partial match.
"""
from __future__ import annotations

ROOT_TERMS = {"GO:0008150", "GO:0005575", "GO:0003674"}


def build_ancestor_cache(go_ids: set[str]) -> dict[str, set[str]]:
    """One QuickGO ancestor lookup per unique GO ID, cached by the caller
    across the whole eval run (not just one protein) to avoid re-querying
    the same term hundreds of times."""
    from tools import quickgo

    cache: dict[str, set[str]] = {}
    for go_id in go_ids:
        ev = quickgo.run(go_id)
        cache[go_id] = set(ev.raw_scores.get("ancestors", [])) if not ev.error else set()
    return cache


def _expand(go_ids: set[str], ancestor_cache: dict[str, set[str]]) -> set[str]:
    out = set(go_ids)
    for g in go_ids:
        out |= ancestor_cache.get(g, set())
    return out - ROOT_TERMS


def precision_recall_at_threshold(
    predictions: dict[str, list[dict]],
    ground_truth: dict[str, set[str]],
    threshold: float,
    ancestor_cache: dict[str, set[str]] | None = None,
) -> tuple[float, float, int]:
    """predictions: protein_id -> [{"go_id", "confidence"}, ...] (should
    already be filtered to one GO aspect by the caller).
    ground_truth: protein_id -> set of true GO IDs for that aspect.
    If ancestor_cache is given, both sides are expanded (true-path rule,
    propagated Fmax); otherwise this is direct-term matching.
    Returns (macro_precision, macro_recall, n_proteins_with_predictions)."""
    precisions, recalls = [], []
    n_with_predictions = 0
    for protein_id, true_terms in ground_truth.items():
        if not true_terms:
            continue
        preds = predictions.get(protein_id, [])
        predicted_terms = {p["go_id"] for p in preds if p["confidence"] >= threshold}

        if ancestor_cache is not None:
            predicted_terms = _expand(predicted_terms, ancestor_cache) if predicted_terms else predicted_terms
            true_terms_cmp = _expand(true_terms, ancestor_cache)
        else:
            true_terms_cmp = true_terms

        if predicted_terms:
            n_with_predictions += 1
            tp = len(predicted_terms & true_terms_cmp)
            precisions.append(tp / len(predicted_terms))
        recalls.append(len(predicted_terms & true_terms_cmp) / len(true_terms_cmp) if true_terms_cmp else 0.0)

    macro_precision = sum(precisions) / len(precisions) if precisions else 0.0
    macro_recall = sum(recalls) / len(recalls) if recalls else 0.0
    return macro_precision, macro_recall, n_with_predictions


def fmax(
    predictions: dict[str, list[dict]],
    ground_truth: dict[str, set[str]],
    thresholds: list[float] | None = None,
    ancestor_cache: dict[str, set[str]] | None = None,
) -> dict:
    thresholds = thresholds or [round(i * 0.01, 2) for i in range(0, 101)]
    best = {"fmax": 0.0, "threshold": 0.0, "precision": 0.0, "recall": 0.0, "coverage": 0.0}
    n_proteins = len([p for p, t in ground_truth.items() if t])

    for tau in thresholds:
        p, r, n_with_pred = precision_recall_at_threshold(predictions, ground_truth, tau, ancestor_cache)
        f = 0.0 if p + r == 0 else 2 * p * r / (p + r)
        if f > best["fmax"]:
            best = {
                "fmax": f,
                "threshold": tau,
                "precision": p,
                "recall": r,
                "coverage": n_with_pred / n_proteins if n_proteins else 0.0,
            }
    return best


def fmax_both(
    predictions: dict[str, list[dict]],
    ground_truth: dict[str, set[str]],
    ancestor_cache: dict[str, set[str]],
    thresholds: list[float] | None = None,
) -> dict:
    """Convenience: both direct-match and propagated Fmax in one call."""
    return {
        "direct": fmax(predictions, ground_truth, thresholds, ancestor_cache=None),
        "propagated": fmax(predictions, ground_truth, thresholds, ancestor_cache=ancestor_cache),
    }
