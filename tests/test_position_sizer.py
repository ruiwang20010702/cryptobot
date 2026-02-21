"""仓位计算器测试

覆盖:
- O14: Kelly 半比例动态化
- O15: Kelly 冷启动默认值修复
"""

from unittest.mock import patch

import pytest

from cryptobot.risk.position_sizer import calc_position_size, _load_kelly_params


# ─── O15: Kelly 冷启动默认值 ─────────────────────────────────────────────

class TestKellyColdStartDefaults:
    def test_kelly_cold_start_defaults(self):
        """样本不足 (<10) 时返回 (0.50, 1.5)"""
        with patch("cryptobot.journal.analytics.calc_performance", return_value={"closed": 5}):
            wr, ratio = _load_kelly_params("BTCUSDT")
        assert wr == 0.50
        assert ratio == 1.5

    def test_kelly_cold_start_zero_closed(self):
        """无已平仓交易 → 返回保守默认值 (0.50, 1.5)"""
        with patch("cryptobot.journal.analytics.calc_performance", return_value={"closed": 0}):
            wr, ratio = _load_kelly_params("BTCUSDT")
        assert wr == 0.50
        assert ratio == 1.5


# ─── O14: Kelly 半比例动态化 ─────────────────────────────────────────────

class TestKellyScaleByRegime:
    """测试 Kelly 缩放因子随 regime 和 confidence 变化"""

    def _calc_kelly_fraction(self, regime: str, confidence: int) -> float:
        """辅助方法：使用固定参数计算 kelly_fraction"""
        # 使用显式 win_rate=0.6, avg_win_loss_ratio=2.0 避免加载 journal
        result = calc_position_size(
            symbol="BTCUSDT",
            account_balance=10000,
            entry_price=60000,
            stop_loss_price=58000,
            leverage=3,
            win_rate=0.6,
            avg_win_loss_ratio=2.0,
            confidence=confidence,
            regime=regime,
        )
        return result["kelly_fraction"]

    def test_trending_high_conf(self):
        """trending + high confidence (>=85) → scale=0.6"""
        kf = self._calc_kelly_fraction("trending", 90)
        # raw kelly = (0.6*2 - 0.4)/2 = 0.4, scaled = 0.4 * 0.6 = 0.24
        assert kf == pytest.approx(0.24, abs=0.001)

    def test_trending_low_conf(self):
        """trending + low confidence (<85) → scale=0.5"""
        kf = self._calc_kelly_fraction("trending", 70)
        # raw kelly = 0.4, scaled = 0.4 * 0.5 = 0.20
        assert kf == pytest.approx(0.20, abs=0.001)

    def test_ranging_high_conf(self):
        """ranging + high confidence → scale=0.4"""
        kf = self._calc_kelly_fraction("ranging", 85)
        assert kf == pytest.approx(0.16, abs=0.001)

    def test_ranging_low_conf(self):
        """ranging + low confidence → scale=0.3"""
        kf = self._calc_kelly_fraction("ranging", 60)
        assert kf == pytest.approx(0.12, abs=0.001)

    def test_volatile_high_conf(self):
        """volatile + high confidence → scale=0.35"""
        kf = self._calc_kelly_fraction("volatile", 90)
        assert kf == pytest.approx(0.14, abs=0.001)

    def test_volatile_low_conf(self):
        """volatile + low confidence → scale=0.25"""
        kf = self._calc_kelly_fraction("volatile", 50)
        assert kf == pytest.approx(0.10, abs=0.001)

    def test_unknown_regime_default(self):
        """未知 regime → 默认 scale=0.5"""
        kf = self._calc_kelly_fraction("unknown", 70)
        assert kf == pytest.approx(0.20, abs=0.001)

    def test_empty_regime_default(self):
        """空 regime → 默认 scale=0.5"""
        kf = self._calc_kelly_fraction("", 70)
        assert kf == pytest.approx(0.20, abs=0.001)

    def test_none_confidence_treated_as_low(self):
        """confidence=None → high_conf=False"""
        result = calc_position_size(
            symbol="BTCUSDT",
            account_balance=10000,
            entry_price=60000,
            stop_loss_price=58000,
            leverage=3,
            win_rate=0.6,
            avg_win_loss_ratio=2.0,
            confidence=None,
            regime="trending",
        )
        # None → high_conf=False → scale=0.5
        assert result["kelly_fraction"] == pytest.approx(0.20, abs=0.001)
