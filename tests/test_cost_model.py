"""交易成本模型测试"""

import pytest

from cryptobot.backtest.cost_model import CostConfig, calc_trade_costs


class TestCalcTradeCosts:
    """calc_trade_costs 核心逻辑测试"""

    def test_default_1x_24h(self):
        """默认参数 1x 杠杆 24h 持仓"""
        costs = calc_trade_costs(CostConfig(), duration_hours=24, leverage=1)
        assert costs.entry_fee_pct == pytest.approx(0.04)
        assert costs.exit_fee_pct == pytest.approx(0.04)
        assert costs.slippage_pct == pytest.approx(0.05)
        assert costs.funding_pct == pytest.approx(0.03)  # 0.01 * 3 * 1
        assert costs.total_pct == pytest.approx(0.16)

    def test_3x_leverage(self):
        """3x 杠杆：所有成本乘 3"""
        costs = calc_trade_costs(CostConfig(), duration_hours=24, leverage=3)
        assert costs.entry_fee_pct == pytest.approx(0.12)
        assert costs.exit_fee_pct == pytest.approx(0.12)
        assert costs.slippage_pct == pytest.approx(0.15)
        assert costs.funding_pct == pytest.approx(0.09)  # 0.01 * 3 * 3
        assert costs.total_pct == pytest.approx(0.48)

    def test_zero_duration(self):
        """零持仓时长：无资金费率"""
        costs = calc_trade_costs(CostConfig(), duration_hours=0, leverage=1)
        assert costs.funding_pct == pytest.approx(0.0)
        assert costs.total_pct == pytest.approx(0.13)  # 0.04 + 0.04 + 0.05

    def test_short_duration_4h(self):
        """4h 短持仓：funding = 0.01 * 0.5 * leverage"""
        costs = calc_trade_costs(CostConfig(), duration_hours=4, leverage=2)
        assert costs.funding_pct == pytest.approx(0.01)  # 0.01 * 0.5 * 2
        assert costs.entry_fee_pct == pytest.approx(0.08)
        assert costs.exit_fee_pct == pytest.approx(0.08)
        assert costs.slippage_pct == pytest.approx(0.10)

    def test_custom_config(self):
        """自定义 CostConfig"""
        config = CostConfig(
            taker_fee_pct=0.02,
            slippage_pct=0.03,
            funding_rate_per_8h=0.005,
        )
        costs = calc_trade_costs(config, duration_hours=16, leverage=5)
        assert costs.entry_fee_pct == pytest.approx(0.1)  # 0.02 * 5
        assert costs.exit_fee_pct == pytest.approx(0.1)  # 0.02 * 5
        assert costs.slippage_pct == pytest.approx(0.15)  # 0.03 * 5
        assert costs.funding_pct == pytest.approx(0.05)  # 0.005 * 2 * 5
        assert costs.total_pct == pytest.approx(0.4)

    def test_returns_frozen_dataclass(self):
        """TradeCosts 是不可变的"""
        costs = calc_trade_costs(CostConfig(), duration_hours=8)
        with pytest.raises(AttributeError):
            costs.total_pct = 999
