"""Unit tests for tools.bench_path_a stats wrappers + MetricRow.

The scraping code (_scrape_latest_n, record_cmd, compare_cmd) is exercised
by the acceptance gate manually — we don't unit-test the CLI because it
depends on real ~/.clicky-windows/debug/ folders.
"""
import numpy as np


def test_mann_whitney_less_detects_lower_after():
    """When 'after' is clearly lower than 'before', p-value should be < 0.05."""
    from tools.bench_path_a import mann_whitney_less
    rng = np.random.default_rng(42)
    before = rng.gamma(shape=2.0, scale=500, size=20) + 2000  # median ~3000ms
    after = rng.gamma(shape=2.0, scale=500, size=20) + 500   # median ~1500ms
    _, p = mann_whitney_less(before.tolist(), after.tolist())
    assert p < 0.05, f"Expected p < 0.05 for clearly-lower 'after', got p={p:.4f}"


def test_mann_whitney_less_does_not_reject_null_for_no_difference():
    """Identical distributions → should NOT reject the null at α=0.05.

    RNG variance can give any p > 0.05 — we only require 'no rejection' to
    guard against the wrapper being miscoded (e.g. one-sided in wrong direction).
    """
    from tools.bench_path_a import mann_whitney_less
    rng = np.random.default_rng(42)
    a = rng.gamma(shape=2.0, scale=500, size=30) + 1000
    b = rng.gamma(shape=2.0, scale=500, size=30) + 1000
    _, p = mann_whitney_less(a.tolist(), b.tolist())
    assert p > 0.05, f"Expected p > 0.05 (do not reject null), got p={p:.4f}"


def test_bootstrap_median_ci_contains_sample_median():
    """95% CI built from the sample itself must contain the sample's median."""
    from tools.bench_path_a import bootstrap_median_ci
    rng = np.random.default_rng(42)
    samples = (rng.gamma(shape=2.0, scale=500, size=20) + 1000).tolist()
    lo, hi = bootstrap_median_ci(samples, confidence=0.95, n_resamples=2000)
    true_median = float(np.median(samples))
    assert lo <= true_median <= hi, (
        f"CI [{lo:.1f}, {hi:.1f}] should contain sample median {true_median:.1f}"
    )


def test_metric_row_summary_has_expected_keys():
    """MetricRow.summary returns a dict with all documented keys."""
    from tools.bench_path_a import MetricRow
    row = MetricRow(
        name="stt_finalize_ms",
        before=[300, 310, 305, 320, 295],
        after=[50, 55, 52, 48, 51],
    )
    s = row.summary()
    for k in ("name", "before_p50", "after_p50", "delta_ms", "p_value", "ci_lo", "ci_hi"):
        assert k in s, f"summary missing key {k!r}"
    assert s["name"] == "stt_finalize_ms"
    # After median (51) - Before median (305) should be negative (~-254)
    assert s["delta_ms"] < 0


def test_metric_row_summary_handles_empty_before_gracefully():
    """With no 'before' data, summary should return nan medians but not crash."""
    from tools.bench_path_a import MetricRow
    row = MetricRow(name="missing", before=[], after=[100, 110, 105])
    s = row.summary()
    assert s["name"] == "missing"
    # before_p50 should be NaN, after_p50 should be finite
    import math
    assert math.isnan(s["before_p50"])
    assert not math.isnan(s["after_p50"])
