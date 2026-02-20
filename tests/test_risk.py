"""风控计算测试"""

import pytest

from cryptobot.risk.liquidation_calc import (
    calc_liquidation_price,
    calc_liquidation_distance,
    assess_liquidation_risk,
    full_liquidation_analysis,
)
from cryptobot.risk.position_sizer import calc_position_size


class TestLiquidationCalc:
    def test_long_liquidation_price(self):
        """多单强平价 < 入场价"""
        liq = calc_liquidation_price(100_000, leverage=5, side="long")
        assert liq < 100_000
        # 5x 杠杆: 大约 100000 × (1 - 0.2 + 0.004) = 80400
        assert 79_000 < liq < 81_000

    def test_short_liquidation_price(self):
        """空单强平价 > 入场价"""
        liq = calc_liquidation_price(100_000, leverage=5, side="short")
        assert liq > 100_000
        # 5x 杠杆: 大约 100000 × (1 + 0.2 - 0.004) = 119600
        assert 119_000 < liq < 121_000

    def test_higher_leverage_closer_liquidation(self):
        """杠杆越高，强平价越近"""
        liq_3x = calc_liquidation_price(100_000, leverage=3, side="long")
        liq_5x = calc_liquidation_price(100_000, leverage=5, side="long")
        assert liq_5x > liq_3x  # 5x 强平价更高 (离入场价更近)

    def test_liquidation_distance(self):
        dist = calc_liquidation_distance(100_000, 80_000)
        assert dist == 20.0

    def test_risk_levels(self):
        assert assess_liquidation_risk(60)["level"] == "safe"
        assert assess_liquidation_risk(40)["level"] == "caution"
        assert assess_liquidation_risk(25)["level"] == "warning"
        assert assess_liquidation_risk(15)["level"] == "danger"
        assert assess_liquidation_risk(5)["level"] == "critical"

    def test_full_analysis_long(self):
        result = full_liquidation_analysis(
            entry_price=100_000,
            current_price=105_000,
            leverage=3,
            side="long",
            position_size_usdt=3000,
        )
        assert result["pnl_pct"] > 0  # 盈利
        assert result["distance_pct"] > 0
        assert result["risk_level"] in ("safe", "caution", "warning", "danger", "critical")

    def test_full_analysis_short_losing(self):
        result = full_liquidation_analysis(
            entry_price=100_000,
            current_price=105_000,
            leverage=3,
            side="short",
            position_size_usdt=3000,
        )
        assert result["pnl_pct"] < 0  # 亏损

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError):
            calc_liquidation_price(100_000, leverage=3, side="invalid")


class TestPositionSizer:
    def test_basic_position_sizing(self):
        result = calc_position_size(
            symbol="BTCUSDT",
            account_balance=10_000,
            entry_price=100_000,
            stop_loss_price=96_000,
            leverage=3,
        )
        assert result["leverage"] == 3
        assert result["margin_usdt"] > 0
        assert result["notional_usdt"] == pytest.approx(
            result["margin_usdt"] * result["leverage"], rel=0.01
        )
        assert result["max_loss_pct_of_balance"] <= 2.5  # 应接近但不超过 2%

    def test_higher_leverage_smaller_margin(self):
        """同样风险下，杠杆越高保证金越少"""
        r3 = calc_position_size("BTCUSDT", 10_000, 100_000, 96_000, leverage=3)
        r5 = calc_position_size("BTCUSDT", 10_000, 100_000, 96_000, leverage=5)
        # 高杠杆下保证金应更小 (因为有效止损更大)
        assert r5["margin_usdt"] <= r3["margin_usdt"]

    def test_wider_stop_smaller_position(self):
        """止损越宽，仓位越小 (禁用凯利以纯风险法比较)"""
        r_tight = calc_position_size(
            "BTCUSDT", 10_000, 100_000, 97_000, leverage=3, win_rate=0
        )
        r_wide = calc_position_size(
            "BTCUSDT", 10_000, 100_000, 93_000, leverage=3, win_rate=0
        )
        assert r_wide["margin_usdt"] < r_tight["margin_usdt"]

    def test_zero_price_raises(self):
        with pytest.raises(ValueError):
            calc_position_size("BTCUSDT", 10_000, 0, 96_000)

    def test_same_price_raises(self):
        with pytest.raises(ValueError):
            calc_position_size("BTCUSDT", 10_000, 100_000, 100_000)

    def test_leverage_capped_by_config(self):
        """杠杆应被 pairs.yaml 配置限制"""
        result = calc_position_size("DOGEUSDT", 10_000, 0.1, 0.095, leverage=5)
        assert result["leverage"] <= 2  # DOGE 配置最大 2x
