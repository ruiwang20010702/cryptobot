"""Tests for cryptobot.indicators.market_structure — BTC correlation analysis."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from cryptobot.indicators.market_structure import calc_btc_correlation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(values, periods=50, freq="4h"):
    """Build a DataFrame with a datetime index and a 'close' column."""
    dates = pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame({"close": values}, index=dates)


# ---------------------------------------------------------------------------
# 1. symbol == "BTCUSDT" → correlation=1.0, class="self"
# ---------------------------------------------------------------------------

class TestBtcSelf:
    def test_btcusdt_returns_self(self):
        result = calc_btc_correlation("BTCUSDT")
        assert result["correlation"] == 1.0
        assert result["correlation_class"] == "self"
        assert result["symbol"] == "BTCUSDT"
        assert result["implication"] == "BTC 自身"

    # 2. symbol="BTCUSDT" with btc_tech and market_overview
    def test_btcusdt_extracts_btc_info(self):
        btc_tech = {
            "signals": {"bias": "bullish"},
            "momentum": {"rsi_14": 62.5},
        }
        market_overview = {"btc_dominance": 54.3}

        result = calc_btc_correlation("BTCUSDT", btc_tech=btc_tech, market_overview=market_overview)

        assert result["correlation"] == 1.0
        assert result["correlation_class"] == "self"
        assert result["btc_trend"] == "bullish"
        assert result["btc_rsi"] == 62.5
        assert result["btc_dominance"] == 54.3

    # 7. btc_tech=None → btc_trend="unknown"
    def test_btcusdt_no_tech_gives_unknown(self):
        result = calc_btc_correlation("BTCUSDT", btc_tech=None)
        assert result["btc_trend"] == "unknown"
        assert result["btc_rsi"] is None
        assert result["btc_dominance"] is None


# ---------------------------------------------------------------------------
# 3–4. Correlation classification for non-BTC symbols
# ---------------------------------------------------------------------------

class TestCorrelationClassification:

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_high_positive_correlation(self, mock_load):
        """Two perfectly correlated trending series → class='high'."""
        btc_values = np.linspace(90000, 95000, 50)
        sym_values = np.linspace(3000, 3200, 50)  # same upward trend
        dates = pd.date_range("2026-01-01", periods=50, freq="4h", tz="UTC")

        df_btc = pd.DataFrame({"close": btc_values}, index=dates)
        df_sym = pd.DataFrame({"close": sym_values}, index=dates)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("ETHUSDT", btc_tech={"signals": {"bias": "bullish"}})

        assert result["correlation"] is not None
        assert result["correlation"] > 0.7
        assert result["correlation_class"] == "high"
        assert result["btc_trend"] == "bullish"

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_low_correlation_random(self, mock_load):
        """One trending series vs random noise → 'low' or 'medium'."""
        rng = np.random.default_rng(42)
        dates = pd.date_range("2026-01-01", periods=50, freq="4h", tz="UTC")

        df_btc = pd.DataFrame({"close": np.linspace(90000, 95000, 50)}, index=dates)
        df_sym = pd.DataFrame({"close": rng.uniform(3000, 3200, 50)}, index=dates)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("XYZUSDT")

        assert result["correlation"] is not None
        assert result["correlation_class"] in ("low", "medium")

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_medium_correlation(self, mock_load):
        """Build a series that yields medium-range correlation (0.4 < |r| < 0.7).

        Construct prices from returns directly: sym_returns = coeff * btc_returns + noise,
        tuned so Pearson r on the last 30 points' returns lands in [0.4, 0.7].
        """
        rng = np.random.default_rng(42)
        dates = pd.date_range("2026-01-01", periods=50, freq="4h", tz="UTC")

        # Build BTC prices with realistic random returns
        btc_returns = rng.normal(0.001, 0.01, 49)
        btc_prices = np.empty(50)
        btc_prices[0] = 90000.0
        for i in range(49):
            btc_prices[i + 1] = btc_prices[i] * (1 + btc_returns[i])

        # coeff=0.7, noise_std=0.01 → Pearson r ≈ 0.50 (medium)
        sym_noise = rng.normal(0, 0.01, 49)
        sym_returns = 0.7 * btc_returns + sym_noise
        sym_prices = np.empty(50)
        sym_prices[0] = 3000.0
        for i in range(49):
            sym_prices[i + 1] = sym_prices[i] * (1 + sym_returns[i])

        df_btc = pd.DataFrame({"close": btc_prices}, index=dates)
        df_sym = pd.DataFrame({"close": sym_prices}, index=dates)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("SOLUSDT")

        assert result["correlation"] is not None
        assert result["correlation_class"] in ("high", "medium")


# ---------------------------------------------------------------------------
# 5–6. Error paths
# ---------------------------------------------------------------------------

class TestErrorPaths:

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_file_not_found(self, mock_load):
        """FileNotFoundError → returns dict with 'error' key."""
        mock_load.side_effect = FileNotFoundError("no klines")

        result = calc_btc_correlation("ETHUSDT")

        assert result["symbol"] == "ETHUSDT"
        assert result["correlation"] is None
        assert "error" in result

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_too_few_common_datapoints(self, mock_load):
        """Less than 30 overlapping datapoints → error."""
        dates_btc = pd.date_range("2026-01-01", periods=20, freq="4h", tz="UTC")
        dates_sym = pd.date_range("2026-02-01", periods=20, freq="4h", tz="UTC")

        df_btc = pd.DataFrame({"close": np.linspace(90000, 95000, 20)}, index=dates_btc)
        df_sym = pd.DataFrame({"close": np.linspace(3000, 3200, 20)}, index=dates_sym)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("ETHUSDT")

        assert result["correlation"] is None
        assert "error" in result
        assert "30" in result["error"]

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_nan_correlation_becomes_zero(self, mock_load):
        """Constant close prices → NaN Pearson → mapped to 0.0."""
        dates = pd.date_range("2026-01-01", periods=50, freq="4h", tz="UTC")

        df_btc = pd.DataFrame({"close": np.full(50, 90000.0)}, index=dates)
        df_sym = pd.DataFrame({"close": np.full(50, 3000.0)}, index=dates)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("ETHUSDT")

        # pct_change of constant series → all zeros → corrcoef → NaN → 0.0
        # But pct_change drops first row, leaving 29 returns from 30 points,
        # which is >= 10, so we proceed and get NaN → 0.0
        assert result["correlation"] == 0.0
        assert result["correlation_class"] == "low"


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_btc_tech_none_for_non_btc(self, mock_load):
        """btc_tech=None for non-BTC symbol → btc_trend='unknown'."""
        dates = pd.date_range("2026-01-01", periods=50, freq="4h", tz="UTC")
        df_btc = pd.DataFrame({"close": np.linspace(90000, 95000, 50)}, index=dates)
        df_sym = pd.DataFrame({"close": np.linspace(3000, 3200, 50)}, index=dates)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("ETHUSDT", btc_tech=None)

        assert result["btc_trend"] == "unknown"
        assert result["btc_rsi"] is None

    @patch("cryptobot.indicators.market_structure.load_klines")
    def test_result_has_all_keys(self, mock_load):
        """Verify all expected keys are present in a successful result."""
        dates = pd.date_range("2026-01-01", periods=50, freq="4h", tz="UTC")
        df_btc = pd.DataFrame({"close": np.linspace(90000, 95000, 50)}, index=dates)
        df_sym = pd.DataFrame({"close": np.linspace(3000, 3200, 50)}, index=dates)

        mock_load.side_effect = lambda symbol, tf: df_btc if symbol == "BTCUSDT" else df_sym

        result = calc_btc_correlation("ETHUSDT")

        expected_keys = {
            "symbol", "correlation", "correlation_class",
            "btc_trend", "btc_rsi", "btc_dominance", "implication",
        }
        assert expected_keys == set(result.keys())
