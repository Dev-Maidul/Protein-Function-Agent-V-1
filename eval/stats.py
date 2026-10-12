"""Small statistics helpers for the hubness diagnostic: a Wilson-score 95%
confidence interval per hit rate (more reliable than a normal approximation
for small/skewed samples, which hub-flagged counts often are), and a Fisher
exact test comparing hub vs non-hub hit counts. The point: Phase 3's first
eval run called the hubness result "confirmed" with only 10 hub-flagged
neighbours and no significance test -- this adds the test so that claim is
only made when the statistics actually support it.
"""
from __future__ import annotations

import math

from scipy.stats import fisher_exact


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.96)
    p = successes / total
    denom = 1 + z**2 / total
    center = p + z**2 / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2))
    low = (center - margin) / denom
    high = (center + margin) / denom
    return (max(0.0, low), min(1.0, high))


def compare_hit_rates(hub_hits: int, hub_total: int, nonhub_hits: int, nonhub_total: int) -> dict:
    """2x2 Fisher exact test: [[hub_hits, hub_total-hub_hits], [nonhub_hits, nonhub_total-nonhub_hits]].
    Returns the hit rates, their 95% Wilson CIs, the odds ratio, and the p-value.
    `significant` is only True if p < 0.05 -- callers should not say a result
    is "confirmed" unless this is True."""
    table = [[hub_hits, hub_total - hub_hits], [nonhub_hits, nonhub_total - nonhub_hits]]
    odds_ratio, p_value = fisher_exact(table)
    odds_ratio, p_value = float(odds_ratio), float(p_value)
    # scipy returns inf/nan when a 2x2 cell is 0 (odds ratio undefined) --
    # neither is valid JSON, so store None instead of silently emitting a
    # malformed "NaN"/"Infinity" token in the saved results file.
    if math.isnan(odds_ratio) or math.isinf(odds_ratio):
        odds_ratio = None

    hub_rate = hub_hits / hub_total if hub_total else None
    nonhub_rate = nonhub_hits / nonhub_total if nonhub_total else None

    return {
        "hub_hit_rate": hub_rate,
        "hub_hit_rate_95ci": wilson_interval(hub_hits, hub_total) if hub_total else None,
        "nonhub_hit_rate": nonhub_rate,
        "nonhub_hit_rate_95ci": wilson_interval(nonhub_hits, nonhub_total) if nonhub_total else None,
        "odds_ratio": odds_ratio,
        "p_value": p_value,
        "significant_at_0.05": bool(p_value < 0.05),
        "n_hub": hub_total,
        "n_nonhub": nonhub_total,
    }
