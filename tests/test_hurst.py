"""Tests for cryptobot.indicators.hurst"""

import random
from unittest.mock import patch

from cryptobot.indicators.hurst import calc_hurst_exponent, classify_hurst
from cryptobot.indicators.regime import detect_regime


# ─── calc_hurst_exponent ──────────────────────────────────────────────────


class TestCalcHurstExponent:
    def test_trending_sequence(self):
        """单调递增序列 → H > 0.5 (persistent)"""
        prices = [100.0 + i * 0.5 for i in range(100)]
        h = calc_hurst_exponent(prices)
        assert h > 0.5, f"趋势序列 H={h} 应 > 0.5"

    def test_mean_reverting_sequence(self):
        """交替涨跌序列 → H < 0.5 (anti-persistent)"""
        prices = [100.0 + ((-1) ** i) * 2.0 for i in range(100)]
        h = calc_hurst_exponent(prices)
        assert h < 0.5, f"均值回归序列 H={h} 应 < 0.5"

    def test_insufficient_data(self):
        """数据不足 → 返回 0.5"""
        assert calc_hurst_exponent([100.0, 101.0, 102.0]) == 0.5

    def test_empty_list(self):
        """空列表 → 返回 0.5"""
        assert calc_hurst_exponent([]) == 0.5

    def test_single_price(self):
        """单个价格 → 返回 0.5"""
        assert calc_hurst_exponent([100.0]) == 0.5

    def test_result_in_range(self):
        """结果始终在 [0, 1] 范围内"""
        random.seed(42)
        prices = [100.0]
        for _ in range(200):
            prices.append(prices[-1] * (1 + random.gauss(0, 0.02)))
        h = calc_hurst_exponent(prices)
        assert 0.0 <= h <= 1.0

    def test_custom_max_lag(self):
        """自定义 max_lag 正常工作"""
        prices = [100.0 + i * 0.5 for i in range(200)]
        h = calc_hurst_exponent(prices, max_lag=40)
        assert 0.0 <= h <= 1.0

    def test_constant_prices(self):
        """价格不变 → 返回 0.5 (log returns 全 0, std=0)"""
        prices = [100.0] * 100
        h = calc_hurst_exponent(prices)
        assert h == 0.5


# ─── classify_hurst ───────────────────────────────────────────────────────


class TestClassifyHurst:
    def test_strong_trending(self):
        """H=0.7 → trending, 高置信度"""
        hint, conf = classify_hurst(0.7)
        assert hint == "trending"
        assert conf > 0.5

    def test_strong_ranging(self):
        """H=0.3 → ranging, 高置信度"""
        hint, conf = classify_hurst(0.3)
        assert hint == "ranging"
        assert conf > 0.5

    def test_random_walk(self):
        """H=0.5 → random, 置信度 0.5"""
        hint, conf = classify_hurst(0.5)
        assert hint == "random"
        assert conf == 0.5

    def test_boundary_trending(self):
        """H=0.55 刚好不是 trending"""
        hint, _ = classify_hurst(0.55)
        assert hint == "random"

    def test_just_above_trending(self):
        """H=0.56 → trending"""
        hint, conf = classify_hurst(0.56)
        assert hint == "trending"
        assert conf > 0

    def test_boundary_ranging(self):
        """H=0.45 刚好不是 ranging"""
        hint, _ = classify_hurst(0.45)
        assert hint == "random"

    def test_just_below_ranging(self):
        """H=0.44 → ranging"""
        hint, conf = classify_hurst(0.44)
        assert hint == "ranging"
        assert conf > 0

    def test_extreme_trending(self):
        """H=0.95 → trending, conf 封顶 1.0"""
        hint, conf = classify_hurst(0.95)
        assert hint == "trending"
        assert conf == 1.0

    def test_extreme_ranging(self):
        """H=0.05 → ranging, conf 封顶 1.0"""
        hint, conf = classify_hurst(0.05)
        assert hint == "ranging"
        assert conf == 1.0


# ─── detect_regime 集成 (Hurst 字段) ──────────────────────────────────────


class TestDetectRegimeHurstIntegration:
    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_returns_hurst_fields(self, mock_analyze):
        """detect_regime 返回 hurst_exponent 和 regime_confidence"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "strong",
            "adx": 30.0,
            "atr_pct": 2.0,
            "closes": [100.0 + i * 0.5 for i in range(100)],
        }
        result = detect_regime("BTCUSDT")

        assert "hurst_exponent" in result
        assert "regime_confidence" in result
        assert isinstance(result["hurst_exponent"], float)
        assert isinstance(result["regime_confidence"], float)
        assert 0.0 <= result["hurst_exponent"] <= 1.0
        assert 0.0 <= result["regime_confidence"] <= 1.0

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_hurst_enhances_trending(self, mock_analyze):
        """趋势价格 + ADX>25 → trending, Hurst 提升 confidence"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "strong",
            "adx": 28.0,
            "atr_pct": 2.0,
            "closes": [100.0 + i * 0.5 for i in range(100)],
        }
        result = detect_regime("BTCUSDT")
        assert result["regime"] == "trending"
        assert result["regime_confidence"] > 0.5

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_no_closes_fallback(self, mock_analyze):
        """无 closes 数据时 Hurst 默认 0.5"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "strong",
            "adx": 30.0,
            "atr_pct": 2.0,
        }
        result = detect_regime("BTCUSDT")

        assert result["hurst_exponent"] == 0.5
        # 仍然正常返回 regime
        assert result["regime"] in ("trending", "ranging", "volatile")

    @patch("cryptobot.indicators.regime._analyze_timeframe")
    def test_closes_not_in_output(self, mock_analyze):
        """timeframe_details 不包含 closes (太大)"""
        mock_analyze.return_value = {
            "trend": "bullish",
            "strength": "weak",
            "adx": 20.0,
            "atr_pct": 1.5,
            "closes": [100.0] * 100,
        }
        result = detect_regime("BTCUSDT")

        for tf_detail in result["timeframe_details"].values():
            assert "closes" not in tf_detail
