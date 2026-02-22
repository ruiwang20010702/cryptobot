"""交易执行优化器测试

覆盖: 结算时间计算、urgent 模式、滑点估算、HourlyCostProfile、frozen dataclass。
所有外部依赖 mock。
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from cryptobot.risk.execution_optimizer import (
    ExecutionWindow,
    FundingSchedule,
    calc_funding_schedule,
    calc_optimal_execution_window,
    estimate_slippage,
)
from cryptobot.backtest.cost_model import (
    HourlyCostProfile,
    calc_hourly_cost_profile,
    CostConfig,
)


# ---------- mock helpers ----------

def _mock_funding_rate(symbol, limit=1):
    return {"current_rate": 0.0005}  # 0.05%


def _mock_funding_rate_negative(symbol, limit=1):
    return {"current_rate": -0.0003}


def _mock_funding_rate_zero(symbol, limit=1):
    return {"current_rate": 0.0}


def _mock_orderbook(symbol, limit=5):
    return {"spread_pct": 0.015}


# ---------- FundingSchedule 结算时间计算 ----------

class TestFundingSchedule:
    """资金费率结算时间计算"""

    @patch(
        "cryptobot.risk.execution_optimizer.get_funding_rate",
        _mock_funding_rate_zero,
        create=True,
    )
    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=0.0,
    )
    def test_next_settlement_01_utc(self, _mock):
        """01:00 UTC → 下次 08:00 UTC (7h)"""
        now = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        fs = calc_funding_schedule("BTCUSDT", now=now)
        assert fs.hours_until == 7.0
        assert "08:00:00" in fs.next_settlement_utc

    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=0.0,
    )
    def test_next_settlement_0730_utc(self, _mock):
        """07:30 UTC → 下次 08:00 UTC (0.5h)"""
        now = datetime(2026, 1, 15, 7, 30, 0, tzinfo=timezone.utc)
        fs = calc_funding_schedule("BTCUSDT", now=now)
        assert fs.hours_until == 0.5
        assert "08:00:00" in fs.next_settlement_utc

    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=0.0,
    )
    def test_next_settlement_16_utc(self, _mock):
        """16:01 UTC → 下次 00:00 UTC 次日 (~7.98h)"""
        now = datetime(2026, 1, 15, 16, 1, 0, tzinfo=timezone.utc)
        fs = calc_funding_schedule("BTCUSDT", now=now)
        assert 7.9 <= fs.hours_until <= 8.0
        assert "00:00:00" in fs.next_settlement_utc
        # 日期应为次日
        assert "2026-01-16" in fs.next_settlement_utc

    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=0.0,
    )
    def test_frozen_dataclass(self, _mock):
        """FundingSchedule 是不可变的"""
        now = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        fs = calc_funding_schedule("BTCUSDT", now=now)
        with pytest.raises(AttributeError):
            fs.hours_until = 999


# ---------- ExecutionWindow ----------

class TestExecutionWindow:
    """最优执行窗口"""

    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=0.0005,
    )
    @patch(
        "cryptobot.risk.execution_optimizer._get_spread_pct",
        return_value=0.01,
    )
    def test_urgent_returns_current_hour(self, _sp, _fr):
        """urgent 模式 → 推荐当前小时"""
        now = datetime(2026, 1, 15, 3, 0, 0, tzinfo=timezone.utc)
        w = calc_optimal_execution_window(
            "BTCUSDT", "long", urgency="urgent", now=now,
        )
        assert w.recommended_hour_utc == 3
        assert "urgent" in w.reasoning

    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=0.0005,
    )
    @patch(
        "cryptobot.risk.execution_optimizer._get_spread_pct",
        return_value=0.01,
    )
    def test_positive_rate_long_waits(self, _sp, _fr):
        """正费率做多 → 建议等结算后"""
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        w = calc_optimal_execution_window(
            "BTCUSDT", "long", urgency="normal", now=now,
        )
        # 下次结算 08:00
        assert w.recommended_hour_utc == 8
        assert w.funding_rate_impact == 0.0  # 结算后无费率影响

    @patch(
        "cryptobot.risk.execution_optimizer._get_funding_rate",
        return_value=-0.0003,
    )
    @patch(
        "cryptobot.risk.execution_optimizer._get_spread_pct",
        return_value=0.02,
    )
    def test_negative_rate_short_waits(self, _sp, _fr):
        """负费率做空 → 建议等结算后"""
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        w = calc_optimal_execution_window(
            "ETHUSDT", "short", urgency="normal", now=now,
        )
        assert w.recommended_hour_utc == 8

    def test_frozen_dataclass(self):
        """ExecutionWindow 是不可变的"""
        w = ExecutionWindow(
            recommended_hour_utc=8,
            funding_rate_impact=0.0,
            expected_slippage=0.01,
            total_cost_estimate=0.01,
            reasoning="test",
        )
        with pytest.raises(AttributeError):
            w.recommended_hour_utc = 999


# ---------- estimate_slippage ----------

class TestEstimateSlippage:
    """滑点估算"""

    def test_large_position_higher_slippage(self):
        """大单滑点 > 小单滑点"""
        small = estimate_slippage("BTCUSDT", 10_000, "long")
        large = estimate_slippage("BTCUSDT", 1_000_000, "long")
        assert large > small

    def test_btc_lower_slippage_than_alt(self):
        """BTC 滑点 < 其他币滑点 (同等金额)"""
        btc = estimate_slippage("BTCUSDT", 100_000, "long")
        alt = estimate_slippage("DOGEUSDT", 100_000, "long")
        assert btc < alt

    def test_eth_between_btc_and_alt(self):
        """ETH 滑点介于 BTC 和 ALT 之间"""
        btc = estimate_slippage("BTCUSDT", 100_000, "long")
        eth = estimate_slippage("ETHUSDT", 100_000, "long")
        alt = estimate_slippage("SOLUSDT", 100_000, "long")
        assert btc < eth < alt


# ---------- HourlyCostProfile ----------

class TestHourlyCostProfile:
    """24 小时成本概况"""

    def test_24h_coverage(self):
        """返回 24 个小时的 profile"""
        profiles = calc_hourly_cost_profile("BTCUSDT", leverage=3)
        assert len(profiles) == 24
        hours = [p.hour_utc for p in profiles]
        assert hours == list(range(24))

    def test_settlement_hours_marked(self):
        """结算时段 (0, 8, 16) funding_rate_applies=True"""
        profiles = calc_hourly_cost_profile("BTCUSDT", leverage=3)
        for p in profiles:
            if p.hour_utc in (0, 8, 16):
                assert p.funding_rate_applies is True
            else:
                assert p.funding_rate_applies is False

    def test_settlement_cost_higher(self):
        """结算时段 cost > 非结算时段 (费率 > 0)"""
        config = CostConfig(funding_rate_per_8h=0.01)
        profiles = calc_hourly_cost_profile(
            "BTCUSDT", leverage=3, config=config,
        )
        settlement = [p for p in profiles if p.funding_rate_applies]
        non_settlement = [p for p in profiles if not p.funding_rate_applies]
        assert all(
            s.total_cost > ns.total_cost
            for s in settlement
            for ns in non_settlement
        )

    def test_frozen_dataclass(self):
        """HourlyCostProfile 是不可变的"""
        p = HourlyCostProfile(
            hour_utc=0, avg_slippage=0.03,
            funding_rate_applies=True, total_cost=0.3,
        )
        with pytest.raises(AttributeError):
            p.total_cost = 999

    def test_custom_config(self):
        """自定义 CostConfig 传播到 profile"""
        config = CostConfig(
            taker_fee_pct=0.02, slippage_pct=0.01,
            funding_rate_per_8h=0.02,
        )
        profiles = calc_hourly_cost_profile(
            "BTCUSDT", leverage=2, config=config,
        )
        # 非结算: (0.02*2 + 0.01) * 2 = 0.1
        non_settle = profiles[1]
        assert non_settle.total_cost == pytest.approx(0.1)
        # 结算: 0.1 + 0.02*2 = 0.14
        settle = profiles[0]
        assert settle.total_cost == pytest.approx(0.14)
