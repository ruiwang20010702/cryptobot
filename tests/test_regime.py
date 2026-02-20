"""Tests for cryptobot.indicators.regime"""

from unittest.mock import patch

import numpy as np
import pandas as pd

from cryptobot.indicators.regime import (
    _analyze_timeframe,
    _classify_volatility,
    detect_regime,
)


def _make_df(prices: list[float]) -> pd.DataFrame:
    """构造简单的 K 线 DataFrame"""
    n = len(prices)
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000.0] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="4h"),
    )


# ─── _classify_volatility ────────────────────────────────────────────────


class TestClassifyVolatility:
    def test_high_vol(self):
        assert _classify_volatility([2.0, 3.5, 1.0]) == "high_vol"

    def test_low_vol(self):
        assert _classify_volatility([0.5, 0.8, 0.3]) == "low_vol"

    def test_normal(self):
        assert _classify_volatility([1.5, 2.0, 1.2]) == "normal"

    def test_empty_list(self):
        assert _classify_volatility([]) == "normal"

    def test_single_element_high(self):
        """只有一个 TF 时用 index 0"""
        assert _classify_volatility([4.0]) == "high_vol"

    def test_single_element_low(self):
        assert _classify_volatility([0.5]) == "low_vol"

    def test_boundary_high(self):
        """atr_pct == 3.0 属于 normal"""
        assert _classify_volatility([1.0, 3.0]) == "normal"

    def test_boundary_low(self):
        """atr_pct == 1.0 属于 normal"""
        assert _classify_volatility([1.0, 1.0]) == "normal"


# ─── _analyze_timeframe ──────────────────────────────────────────────────


class TestAnalyzeTimeframe:
    @patch("cryptobot.indicators.regime.load_klines")
    def test_bullish_trend(self, mock_load):
        """递增价格 → bullish"""
        prices = list(np.linspace(100, 200, 100))
        mock_load.return_value = _make_df(prices)
        result = _analyze_timeframe("BTCUSDT", "4h")

        assert result["trend"] == "bullish"
        assert isinstance(result["adx"], float)
        assert isinstance(result["atr_pct"], float)
        assert result["atr_pct"] > 0

    @patch("cryptobot.indicators.regime.load_klines")
    def test_bearish_trend(self, mock_load):
        """递减价格 → bearish"""
        prices = list(np.linspace(200, 100, 100))
        mock_load.return_value = _make_df(prices)
        result = _analyze_timeframe("BTCUSDT", "4h")

        assert result["trend"] == "bearish"
        assert isinstance(result["adx"], float)

    @patch("cryptobot.indicators.regime.load_klines")
    def test_strong_adx(self, mock_load):
        """强趋势 (大幅单边) → ADX > 25 → strength=strong"""
        prices = list(np.linspace(100, 300, 100))
        mock_load.return_value = _make_df(prices)
        result = _analyze_timeframe("BTCUSDT", "4h")

        assert result["strength"] == "strong"
        assert result["adx"] > 25

    @patch("cryptobot.indicators.regime.load_klines")
    def test_output_structure(self, mock_load):
        """输出包含所有必要字段"""
        prices = list(np.linspace(100, 150, 100))
        mock_load.return_value = _make_df(prices)
        result = _analyze_timeframe("BTCUSDT", "1h")

        assert set(result.keys()) == {"trend", "strength", "adx", "atr_pct"}


# ─── detect_regime ───────────────────────────────────────────────────────


class TestDetectRegime:
    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_all_bullish_strong(self, mock_analyze):
        """3/3 bullish + strong ADX → trending + bullish"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "strong",
            "adx": 30.5,
            "atr_pct": 2.0,
        }
        result = detect_regime("BTCUSDT")

        assert result["regime"] == "trending"
        assert result["trend_direction"] == "bullish"
        assert result["trend_strength"] == "strong"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_two_bullish_one_bearish(self, mock_analyze):
        """2 bullish + 1 bearish → trending + bullish"""
        returns = [
            {"trend": "bullish", "strength": "strong", "adx": 28.0, "atr_pct": 2.0},
            {"trend": "bullish", "strength": "weak", "adx": 22.0, "atr_pct": 1.8},
            {"trend": "bearish", "strength": "weak", "adx": 18.0, "atr_pct": 1.5},
        ]
        mock_analyze.side_effect = returns
        result = detect_regime("BTCUSDT")

        assert result["trend_direction"] == "bullish"
        # 有 strong ADX(28) + 方向共识 → trending
        assert result["regime"] == "trending"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_all_weak_adx(self, mock_analyze):
        """全部 weak ADX + neutral → ranging"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "weak",
            "adx": 15.0,
            "atr_pct": 1.5,
        }
        # 3/3 bullish 但全部 ADX < 25
        # 有方向共识但没有强 ADX → 不满足 trending 条件
        # volatility_state=normal → 不满足 volatile
        # → ranging
        # 等等，根据实现: has_strong_adx 需要 adx > 25
        # 这里 adx=15 全部 weak → has_strong_adx=False → 不是 trending
        # volatility_state = _classify_volatility([1.5, 1.5, 1.5]) = "normal"
        # → ranging
        result = detect_regime("BTCUSDT")
        assert result["regime"] == "ranging"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_high_volatility(self, mock_analyze):
        """high_vol → volatile"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "weak",
            "adx": 18.0,
            "atr_pct": 4.0,
        }
        result = detect_regime("BTCUSDT")

        assert result["regime"] == "volatile"
        assert result["volatility_state"] == "high_vol"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_trending_beats_volatile(self, mock_analyze):
        """strong ADX + 方向共识 → trending，即使 high_vol"""
        mock_analyze.return_value = {
            "trend": "bearish",
            "strength": "strong",
            "adx": 35.0,
            "atr_pct": 4.0,
        }
        result = detect_regime("BTCUSDT")

        # trending 判定优先于 volatile
        assert result["regime"] == "trending"
        assert result["trend_direction"] == "bearish"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_neutral_direction(self, mock_analyze):
        """1 bullish + 1 bearish + 1 bearish weak → bearish 2/3"""
        returns = [
            {"trend": "bullish", "strength": "strong", "adx": 30.0, "atr_pct": 2.0},
            {"trend": "bearish", "strength": "weak", "adx": 22.0, "atr_pct": 1.8},
            {"trend": "bearish", "strength": "weak", "adx": 18.0, "atr_pct": 1.5},
        ]
        mock_analyze.side_effect = returns
        result = detect_regime("BTCUSDT")

        assert result["trend_direction"] == "bearish"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_mixed_no_majority(self, mock_analyze):
        """1 bullish + 1 bearish (2 TF) → neutral"""
        returns = [
            {"trend": "bullish", "strength": "strong", "adx": 30.0, "atr_pct": 2.0},
            {"trend": "bearish", "strength": "strong", "adx": 28.0, "atr_pct": 1.8},
        ]
        mock_analyze.side_effect = returns
        # 模拟第三个 TF 失败
        mock_analyze.side_effect = [
            returns[0],
            returns[1],
            Exception("加载失败"),
        ]
        result = detect_regime("BTCUSDT")

        # 1 bullish vs 1 bearish (total=2) → 需要 2/2 才算多数 → neutral
        assert result["trend_direction"] == "neutral"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_all_tf_fail(self, mock_analyze):
        """全部 TF 加载失败 → 默认 ranging"""
        mock_analyze.side_effect = Exception("网络错误")
        result = detect_regime("BTCUSDT")

        assert result["regime"] == "ranging"
        assert result["trend_direction"] == "neutral"
        assert result["timeframe_details"] == {}

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_output_completeness(self, mock_analyze):
        """输出包含所有必要字段"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "strong",
            "adx": 30.0,
            "atr_pct": 2.0,
        }
        result = detect_regime("BTCUSDT")

        expected_keys = {
            "regime",
            "trend_direction",
            "trend_strength",
            "volatility_state",
            "timeframe_details",
            "description",
        }
        assert set(result.keys()) == expected_keys
        assert result["regime"] in ("trending", "ranging", "volatile")
        assert result["trend_direction"] in ("bullish", "bearish", "neutral")
        assert result["trend_strength"] in ("strong", "weak")
        assert result["volatility_state"] in ("low_vol", "normal", "high_vol")
        assert isinstance(result["timeframe_details"], dict)
        assert isinstance(result["description"], str)

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_timeframe_details_populated(self, mock_analyze):
        """timeframe_details 包含各 TF 数据"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "weak",
            "adx": 20.0,
            "atr_pct": 1.5,
        }
        result = detect_regime("BTCUSDT")

        assert "1h" in result["timeframe_details"]
        assert "4h" in result["timeframe_details"]
        assert "1d" in result["timeframe_details"]

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_partial_tf_failure(self, mock_analyze):
        """部分 TF 失败时仍能正常工作"""
        calls = [0]

        def side_effect(symbol, tf):
            calls[0] += 1
            if tf == "1d":
                raise Exception("1d 数据不可用")
            return {
                "trend": "bullish",
                "strength": "strong",
                "adx": 30.0,
                "atr_pct": 2.0,
            }

        mock_analyze.side_effect = side_effect
        result = detect_regime("BTCUSDT")

        assert "1h" in result["timeframe_details"]
        assert "4h" in result["timeframe_details"]
        assert "1d" not in result["timeframe_details"]
        assert result["trend_direction"] == "bullish"

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_description_contains_regime_info(self, mock_analyze):
        """description 包含 regime 相关信息"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "strong",
            "adx": 30.0,
            "atr_pct": 2.0,
        }
        result = detect_regime("BTCUSDT")

        assert "趋势市" in result["description"]
        assert "多头" in result["description"]
