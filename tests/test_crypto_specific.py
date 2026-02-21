"""加密货币特有指标测试"""

from unittest.mock import patch


from cryptobot.indicators.crypto_specific import (
    _analyze_funding,
    _analyze_oi,
    _analyze_taker,
    _analyze_long_short,
    _composite_score,
    calc_crypto_indicators,
)


# ---------------------------------------------------------------------------
# _analyze_funding
# ---------------------------------------------------------------------------
class TestAnalyzeFunding:
    def test_extreme_high_rate_short_signal(self):
        """费率 > 0.001 → 极度看多，反向做空"""
        result = _analyze_funding({
            "current_rate": 0.002,
            "avg_rate_30": 0.001,
            "positive_count": 8,
            "negative_count": 2,
        })
        assert result["bias"] == "short"
        assert result["score"] == -2
        assert result["current_rate"] == 0.002
        assert result["current_rate_pct"] == 0.2
        assert result["positive_ratio"] == 0.8

    def test_high_rate_short_lean(self):
        """费率 > 0.0005 → 偏多，轻微做空倾向"""
        result = _analyze_funding({
            "current_rate": 0.0007,
            "avg_rate_30": 0.0004,
            "positive_count": 6,
            "negative_count": 4,
        })
        assert result["bias"] == "short_lean"
        assert result["score"] == -1

    def test_extreme_low_rate_long_signal(self):
        """费率 < -0.001 → 极度看空，反向做多"""
        result = _analyze_funding({
            "current_rate": -0.002,
            "avg_rate_30": -0.001,
            "positive_count": 2,
            "negative_count": 8,
        })
        assert result["bias"] == "long"
        assert result["score"] == 2

    def test_low_rate_long_lean(self):
        """费率 < -0.0005 → 偏空，轻微做多倾向"""
        result = _analyze_funding({
            "current_rate": -0.0007,
            "avg_rate_30": -0.0003,
            "positive_count": 3,
            "negative_count": 7,
        })
        assert result["bias"] == "long_lean"
        assert result["score"] == 1

    def test_neutral_rate(self):
        """费率在 [-0.0005, 0.0005] → 中性"""
        result = _analyze_funding({
            "current_rate": 0.0001,
            "avg_rate_30": 0.0002,
            "positive_count": 5,
            "negative_count": 5,
        })
        assert result["bias"] == "neutral"
        assert result["score"] == 0
        assert result["signal"] == "中性"

    def test_defaults_on_empty_data(self):
        """空字典使用默认值"""
        result = _analyze_funding({})
        assert result["current_rate"] == 0
        assert result["bias"] == "neutral"
        assert result["score"] == 0
        assert result["positive_ratio"] == 0.0

    def test_avg_rate_pct_rounding(self):
        """avg_rate_pct 正确四舍五入"""
        result = _analyze_funding({
            "current_rate": 0,
            "avg_rate_30": 0.000123456,
            "positive_count": 0,
            "negative_count": 0,
        })
        assert result["avg_rate_pct"] == 0.0123


# ---------------------------------------------------------------------------
# _analyze_oi
# ---------------------------------------------------------------------------
class TestAnalyzeOi:
    def test_big_increase(self):
        """OI 变化 > 10% → 大幅上升"""
        result = _analyze_oi({"oi_change_pct": 15.0, "current_oi_value": 1_000_000})
        assert result["trend"] == "increasing"
        assert "大幅上升" in result["signal"]
        assert result["current_oi_value"] == 1_000_000

    def test_moderate_increase(self):
        """OI 变化 > 3% → 上升"""
        result = _analyze_oi({"oi_change_pct": 5.0, "current_oi_value": 500_000})
        assert result["trend"] == "increasing"
        assert result["signal"] == "OI 上升"

    def test_big_decrease(self):
        """OI 变化 < -10% → 大幅下降"""
        result = _analyze_oi({"oi_change_pct": -12.0, "current_oi_value": 800_000})
        assert result["trend"] == "decreasing"
        assert "大幅下降" in result["signal"]

    def test_moderate_decrease(self):
        """OI 变化 < -3% → 下降"""
        result = _analyze_oi({"oi_change_pct": -5.0, "current_oi_value": 600_000})
        assert result["trend"] == "decreasing"
        assert result["signal"] == "OI 下降"

    def test_stable(self):
        """OI 变化在 [-3, 3] → 平稳"""
        result = _analyze_oi({"oi_change_pct": 1.5, "current_oi_value": 700_000})
        assert result["trend"] == "stable"
        assert result["signal"] == "OI 平稳"

    def test_change_pct_rounding(self):
        """change_pct 四舍五入到两位小数"""
        result = _analyze_oi({"oi_change_pct": 1.5678, "current_oi_value": 0})
        assert result["change_pct"] == 1.57


# ---------------------------------------------------------------------------
# _analyze_taker
# ---------------------------------------------------------------------------
class TestAnalyzeTaker:
    def test_strong_buy(self):
        """ratio > 1.2 → 主动买入强势"""
        result = _analyze_taker({
            "current_ratio": 1.35,
            "avg_ratio": 1.1,
            "bullish_count": 20,
            "bearish_count": 5,
        })
        assert result["bias"] == "bullish"
        assert result["score"] == 1.5
        assert result["bullish_periods"] == 20
        assert result["bearish_periods"] == 5

    def test_lean_buy(self):
        """ratio > 1.05 → 偏买入"""
        result = _analyze_taker({
            "current_ratio": 1.1,
            "avg_ratio": 1.02,
            "bullish_count": 15,
            "bearish_count": 10,
        })
        assert result["bias"] == "bullish_lean"
        assert result["score"] == 0.5

    def test_strong_sell(self):
        """ratio < 0.8 → 主动卖出强势"""
        result = _analyze_taker({
            "current_ratio": 0.7,
            "avg_ratio": 0.85,
            "bullish_count": 5,
            "bearish_count": 20,
        })
        assert result["bias"] == "bearish"
        assert result["score"] == -1.5

    def test_lean_sell(self):
        """ratio < 0.95 → 偏卖出"""
        result = _analyze_taker({
            "current_ratio": 0.9,
            "avg_ratio": 0.95,
            "bullish_count": 10,
            "bearish_count": 15,
        })
        assert result["bias"] == "bearish_lean"
        assert result["score"] == -0.5

    def test_balanced(self):
        """ratio 在 [0.95, 1.05] → 均衡"""
        result = _analyze_taker({
            "current_ratio": 1.0,
            "avg_ratio": 1.0,
            "bullish_count": 12,
            "bearish_count": 12,
        })
        assert result["bias"] == "neutral"
        assert result["score"] == 0


# ---------------------------------------------------------------------------
# _analyze_long_short
# ---------------------------------------------------------------------------
class TestAnalyzeLongShort:
    def test_bullish_with_severe_divergence(self):
        """大户 ratio > 1.2 且与散户严重分歧 (divergence > 0.3)"""
        ls_data = {"current_ratio": 0.9, "current_long_pct": 47, "current_short_pct": 53}
        top_data = {"current_ratio": 1.3, "current_long_pct": 57}
        result = _analyze_long_short(ls_data, top_data)
        assert result["bias"] == "bullish"
        assert result["score"] == 1
        assert result["divergence_signal"] == "大户与散户严重分歧"
        assert result["divergence"] == 0.4

    def test_bearish_with_moderate_divergence(self):
        """大户 ratio < 0.8 且有分歧 (divergence > 0.1)"""
        ls_data = {"current_ratio": 1.0, "current_long_pct": 50, "current_short_pct": 50}
        top_data = {"current_ratio": 0.75, "current_long_pct": 43}
        result = _analyze_long_short(ls_data, top_data)
        assert result["bias"] == "bearish"
        assert result["score"] == -1
        assert result["divergence_signal"] == "大户与散户存在分歧"

    def test_neutral_aligned(self):
        """大户与散户一致，bias 中性"""
        ls_data = {"current_ratio": 1.0, "current_long_pct": 50, "current_short_pct": 50}
        top_data = {"current_ratio": 1.05, "current_long_pct": 51}
        result = _analyze_long_short(ls_data, top_data)
        assert result["bias"] == "neutral"
        assert result["score"] == 0
        assert result["divergence_signal"] == "大户散户方向一致"

    def test_global_pct_rounding(self):
        """global_long_pct / global_short_pct 四舍五入到一位"""
        ls_data = {"current_ratio": 1.0, "current_long_pct": 52.567, "current_short_pct": 47.433}
        top_data = {"current_ratio": 1.0, "current_long_pct": 50}
        result = _analyze_long_short(ls_data, top_data)
        assert result["global_long_pct"] == 52.6
        assert result["global_short_pct"] == 47.4

    def test_divergence_boundary_small(self):
        """divergence = 0.05 → 方向一致（不满足 > 0.1）"""
        ls_data = {"current_ratio": 1.0, "current_long_pct": 50, "current_short_pct": 50}
        top_data = {"current_ratio": 1.05, "current_long_pct": 51}
        result = _analyze_long_short(ls_data, top_data)
        assert result["divergence_signal"] == "大户散户方向一致"

    def test_divergence_boundary_moderate(self):
        """divergence = 0.15 → 存在分歧（> 0.1 但 <= 0.3）"""
        ls_data = {"current_ratio": 1.0, "current_long_pct": 50, "current_short_pct": 50}
        top_data = {"current_ratio": 1.15, "current_long_pct": 53}
        result = _analyze_long_short(ls_data, top_data)
        assert result["divergence_signal"] == "大户与散户存在分歧"


# ---------------------------------------------------------------------------
# _composite_score
# ---------------------------------------------------------------------------
class TestCompositeScore:
    def test_bullish_combination(self):
        """多个看多信号组合 → 综合看多"""
        funding = {"score": 2, "signal": "极度看空 (费率负，做多可能获利)"}
        oi = {"trend": "increasing"}
        taker = {"score": 1.5, "signal": "主动买入强势"}
        ls = {"score": 1, "bias": "bullish", "signal": "大户偏bullish"}
        result = _composite_score(funding, oi, taker, ls)
        # score = 2*1.5 + 1.5*1.0 + 1*1.0 = 3+1.5+1 = 5.5
        assert result["score"] == 5.5
        assert result["bias"] == "bullish"
        assert "资金入场" in result["signals"]

    def test_bearish_combination(self):
        """多个看空信号组合 → 综合看空"""
        funding = {"score": -2, "signal": "极度看多 (费率高，做空可能获利)"}
        oi = {"trend": "decreasing"}
        taker = {"score": -1.5, "signal": "主动卖出强势"}
        ls = {"score": -1, "bias": "bearish", "signal": "大户偏bearish"}
        result = _composite_score(funding, oi, taker, ls)
        # score = -2*1.5 + -1.5*1.0 + -1*1.0 = -3-1.5-1 = -5.5
        assert result["score"] == -5.5
        assert result["bias"] == "bearish"
        assert "资金撤离" in result["signals"]

    def test_neutral_result(self):
        """中性信号组合"""
        funding = {"score": 0, "signal": "中性"}
        oi = {"trend": "stable"}
        taker = {"score": 0, "signal": "买卖均衡"}
        ls = {"score": 0, "bias": "neutral", "signal": "中性"}
        result = _composite_score(funding, oi, taker, ls)
        assert result["score"] == 0
        assert result["bias"] == "neutral"
        assert result["signals"] == []

    def test_clamping_upper_bound(self):
        """得分超过 10 → 限制为 10"""
        oi = {"trend": "increasing"}
        # 手动构造极端数据
        extreme_funding = {"score": 10, "signal": "极端"}
        extreme_taker = {"score": 10, "signal": "极端"}
        extreme_ls = {"score": 10, "bias": "bullish", "signal": "极端"}
        result = _composite_score(extreme_funding, oi, extreme_taker, extreme_ls)
        # score = 10*1.5 + 10*1.0 + 10*1.0 = 35, clamped to 10
        assert result["score"] == 10

    def test_clamping_lower_bound(self):
        """得分低于 -10 → 限制为 -10"""
        oi = {"trend": "stable"}
        extreme_funding = {"score": -10, "signal": "极端空"}
        extreme_taker = {"score": -10, "signal": "极端空"}
        extreme_ls = {"score": -10, "bias": "bearish", "signal": "极端空"}
        result = _composite_score(extreme_funding, oi, extreme_taker, extreme_ls)
        assert result["score"] == -10

    def test_oi_increasing_adds_signal(self):
        """OI 上升时加入 '资金入场' 信号"""
        funding = {"score": 0, "signal": "中性"}
        oi = {"trend": "increasing"}
        taker = {"score": 0, "signal": "均衡"}
        ls = {"score": 0, "bias": "neutral", "signal": "中性"}
        result = _composite_score(funding, oi, taker, ls)
        assert "资金入场" in result["signals"]

    def test_oi_decreasing_adds_signal(self):
        """OI 下降时加入 '资金撤离' 信号"""
        funding = {"score": 0, "signal": "中性"}
        oi = {"trend": "decreasing"}
        taker = {"score": 0, "signal": "均衡"}
        ls = {"score": 0, "bias": "neutral", "signal": "中性"}
        result = _composite_score(funding, oi, taker, ls)
        assert "资金撤离" in result["signals"]

    def test_oi_stable_no_signal(self):
        """OI 平稳不产生额外信号"""
        funding = {"score": 0, "signal": "中性"}
        oi = {"trend": "stable"}
        taker = {"score": 0, "signal": "均衡"}
        ls = {"score": 0, "bias": "neutral", "signal": "中性"}
        result = _composite_score(funding, oi, taker, ls)
        assert result["signals"] == []

    def test_boundary_bullish_at_1_6(self):
        """score = 1.6 → bias 为 bullish (> 1.5)"""
        # funding_score * 1.5 = 1.6 → funding_score ≈ 1.0667
        # 用 taker 直接设置: taker_score=1.6, others=0
        funding = {"score": 0, "signal": "中性"}
        oi = {"trend": "stable"}
        taker = {"score": 1.6, "signal": "偏买"}
        ls = {"score": 0, "bias": "neutral", "signal": "中性"}
        result = _composite_score(funding, oi, taker, ls)
        assert result["score"] == 1.6
        assert result["bias"] == "bullish"

    def test_boundary_neutral_at_1_5(self):
        """score = 1.5 → bias 为 neutral (不满足 > 1.5)"""
        funding = {"score": 1, "signal": "偏空"}
        oi = {"trend": "stable"}
        taker = {"score": 0, "signal": "均衡"}
        ls = {"score": 0, "bias": "neutral", "signal": "中性"}
        result = _composite_score(funding, oi, taker, ls)
        # score = 1 * 1.5 = 1.5
        assert result["score"] == 1.5
        assert result["bias"] == "neutral"


# ---------------------------------------------------------------------------
# calc_crypto_indicators (集成测试，mock 所有外部数据)
# ---------------------------------------------------------------------------
class TestCalcCryptoIndicators:
    @patch("cryptobot.indicators.crypto_specific.get_top_trader_long_short")
    @patch("cryptobot.indicators.crypto_specific.get_long_short_ratio")
    @patch("cryptobot.indicators.crypto_specific.get_taker_buy_sell_ratio")
    @patch("cryptobot.indicators.crypto_specific.get_open_interest_hist")
    @patch("cryptobot.indicators.crypto_specific.get_funding_rate")
    def test_full_pipeline_structure(
        self, mock_funding, mock_oi, mock_taker, mock_ls, mock_top_ls,
    ):
        """完整流水线返回正确结构"""
        mock_funding.return_value = {
            "current_rate": 0.0003,
            "avg_rate_30": 0.0002,
            "positive_count": 5,
            "negative_count": 5,
        }
        mock_oi.return_value = {"oi_change_pct": 2.0, "current_oi_value": 500_000}
        mock_taker.return_value = {
            "current_ratio": 1.0,
            "avg_ratio": 1.0,
            "bullish_count": 12,
            "bearish_count": 12,
        }
        mock_ls.return_value = {
            "current_ratio": 1.0,
            "current_long_pct": 50,
            "current_short_pct": 50,
        }
        mock_top_ls.return_value = {
            "current_ratio": 1.05,
            "current_long_pct": 51,
        }

        result = calc_crypto_indicators("ETHUSDT")

        assert result["symbol"] == "ETHUSDT"
        assert "funding" in result
        assert "open_interest" in result
        assert "taker_ratio" in result
        assert "long_short" in result
        assert "composite" in result
        assert result["funding"]["bias"] == "neutral"
        assert result["open_interest"]["trend"] == "stable"
        assert result["composite"]["bias"] == "neutral"

        mock_funding.assert_called_once_with("ETHUSDT")
        mock_oi.assert_called_once_with("ETHUSDT", period="1h", limit=48)
        mock_taker.assert_called_once_with("ETHUSDT", period="1h", limit=48)
        mock_ls.assert_called_once_with("ETHUSDT", period="1h", limit=30)
        mock_top_ls.assert_called_once_with("ETHUSDT", period="1h", limit=30)

    @patch("cryptobot.indicators.crypto_specific.get_top_trader_long_short")
    @patch("cryptobot.indicators.crypto_specific.get_long_short_ratio")
    @patch("cryptobot.indicators.crypto_specific.get_taker_buy_sell_ratio")
    @patch("cryptobot.indicators.crypto_specific.get_open_interest_hist")
    @patch("cryptobot.indicators.crypto_specific.get_funding_rate")
    def test_default_symbol_is_btcusdt(
        self, mock_funding, mock_oi, mock_taker, mock_ls, mock_top_ls,
    ):
        """默认 symbol 为 BTCUSDT"""
        mock_funding.return_value = {
            "current_rate": 0, "avg_rate_30": 0, "positive_count": 0, "negative_count": 0,
        }
        mock_oi.return_value = {"oi_change_pct": 0, "current_oi_value": 0}
        mock_taker.return_value = {
            "current_ratio": 1.0, "avg_ratio": 1.0, "bullish_count": 0, "bearish_count": 0,
        }
        mock_ls.return_value = {
            "current_ratio": 1.0, "current_long_pct": 50, "current_short_pct": 50,
        }
        mock_top_ls.return_value = {"current_ratio": 1.0, "current_long_pct": 50}

        result = calc_crypto_indicators()

        assert result["symbol"] == "BTCUSDT"
        mock_funding.assert_called_once_with("BTCUSDT")

    @patch("cryptobot.indicators.crypto_specific.get_top_trader_long_short")
    @patch("cryptobot.indicators.crypto_specific.get_long_short_ratio")
    @patch("cryptobot.indicators.crypto_specific.get_taker_buy_sell_ratio")
    @patch("cryptobot.indicators.crypto_specific.get_open_interest_hist")
    @patch("cryptobot.indicators.crypto_specific.get_funding_rate")
    def test_bullish_scenario_end_to_end(
        self, mock_funding, mock_oi, mock_taker, mock_ls, mock_top_ls,
    ):
        """看多场景端到端验证"""
        mock_funding.return_value = {
            "current_rate": -0.002,
            "avg_rate_30": -0.001,
            "positive_count": 2,
            "negative_count": 8,
        }
        mock_oi.return_value = {"oi_change_pct": 15.0, "current_oi_value": 1_000_000}
        mock_taker.return_value = {
            "current_ratio": 1.3,
            "avg_ratio": 1.1,
            "bullish_count": 20,
            "bearish_count": 5,
        }
        mock_ls.return_value = {
            "current_ratio": 0.9,
            "current_long_pct": 47,
            "current_short_pct": 53,
        }
        mock_top_ls.return_value = {"current_ratio": 1.3, "current_long_pct": 57}

        result = calc_crypto_indicators("BTCUSDT")

        assert result["funding"]["bias"] == "long"
        assert result["open_interest"]["trend"] == "increasing"
        assert result["taker_ratio"]["bias"] == "bullish"
        assert result["long_short"]["bias"] == "bullish"
        assert result["composite"]["bias"] == "bullish"
        assert result["composite"]["score"] > 1.5
