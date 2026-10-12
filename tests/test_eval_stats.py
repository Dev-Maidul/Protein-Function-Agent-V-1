from eval.stats import compare_hit_rates, wilson_interval


def test_wilson_interval_contains_point_estimate():
    low, high = wilson_interval(5, 10)
    assert low < 0.5 < high


def test_wilson_interval_zero_total():
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_interval_widens_for_small_n():
    low_small, high_small = wilson_interval(5, 10)
    low_big, high_big = wilson_interval(500, 1000)
    assert (high_small - low_small) > (high_big - low_big)


def test_compare_hit_rates_significant_difference():
    # Phase 3's real numbers: 0/10 hub hits, 48/190 non-hub hits.
    result = compare_hit_rates(hub_hits=0, hub_total=10, nonhub_hits=48, nonhub_total=190)
    assert result["hub_hit_rate"] == 0.0
    assert abs(result["nonhub_hit_rate"] - 48 / 190) < 1e-9
    assert "p_value" in result
    assert isinstance(result["significant_at_0.05"], bool)


def test_compare_hit_rates_no_difference_not_significant():
    result = compare_hit_rates(hub_hits=25, hub_total=100, nonhub_hits=25, nonhub_total=100)
    assert result["significant_at_0.05"] is False


def test_compare_hit_rates_empty_groups_handled():
    result = compare_hit_rates(hub_hits=0, hub_total=0, nonhub_hits=10, nonhub_total=40)
    assert result["hub_hit_rate"] is None
    assert result["hub_hit_rate_95ci"] is None


def test_compare_hit_rates_odds_ratio_is_json_safe_when_undefined():
    import json

    # both zero-hit groups -> odds ratio is mathematically undefined (0/0 pattern)
    result = compare_hit_rates(hub_hits=0, hub_total=3, nonhub_hits=0, nonhub_total=17)
    assert result["odds_ratio"] is None  # never NaN/inf -- must round-trip through JSON
    json.dumps(result)  # must not raise and must not contain a bare NaN/Infinity token
