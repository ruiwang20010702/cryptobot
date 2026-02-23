"""网格交易测试"""

from unittest.mock import patch

import pytest

from cryptobot.strategy.grid_trading import (
    GridConfig,
    GridLevel,
    GridState,
    calc_grid_metrics,
    check_grid_triggers,
    create_grid,
    load_grid_state,
    save_grid_state,
)
from cryptobot.strategy.virtual_portfolio import VirtualPortfolio


def _make_portfolio(balance=10000.0, positions=None, closed=None):
    return VirtualPortfolio(
        initial_balance=balance,
        current_balance=balance,
        positions=positions or [],
        closed_trades=closed or [],
        updated_at="2026-01-01T00:00:00",
    )


class TestCreateGrid:
    def test_basic_grid(self):
        config = GridConfig(
            symbol="BTCUSDT",
            upper_price=52000.0,
            lower_price=48000.0,
            grid_count=4,
            total_investment=1000.0,
            leverage=1,
        )
        state = create_grid(config)
        assert len(state.levels) == 5  # grid_count + 1
        # 第一个 level 在下界
        assert state.levels[0].price == 48000.0
        # 最后一个 level 在上界
        assert state.levels[-1].price == 52000.0
        # 低于中间价(50000) 的是 buy
        assert state.levels[0].side == "buy"
        assert state.levels[1].side == "buy"
        # 高于中间价的是 sell
        assert state.levels[-1].side == "sell"

    def test_invalid_grid_count(self):
        config = GridConfig("BTC", 52000, 48000, 1, 1000, 1)
        with pytest.raises(ValueError, match="至少为 2"):
            create_grid(config)

    def test_invalid_range(self):
        config = GridConfig("BTC", 48000, 52000, 4, 1000, 1)
        with pytest.raises(ValueError, match="上界必须大于下界"):
            create_grid(config)

    def test_grid_step(self):
        config = GridConfig("BTC", 100.0, 0.0, 10, 1000.0, 1)
        state = create_grid(config)
        # step = (100 - 0) / 10 = 10
        prices = [lv.price for lv in state.levels]
        for i in range(1, len(prices)):
            assert prices[i] - prices[i - 1] == pytest.approx(10.0, abs=0.001)


class TestCheckGridTriggers:
    def _make_state(self):
        config = GridConfig("BTCUSDT", 52000, 48000, 4, 1000, 1)
        return create_grid(config)

    def test_buy_trigger(self):
        state = self._make_state()
        portfolio = _make_portfolio(10000.0)
        # 价格跌到最低 level
        new_state, new_portfolio = check_grid_triggers(state, 48000.0, portfolio)
        # 应该触发 buy levels (价格 <= level.price 且 side == buy)
        filled = [lv for lv in new_state.levels if lv.filled]
        assert len(filled) >= 1

    def test_sell_trigger(self):
        # 先设置一个有 long 仓位的场景
        from cryptobot.strategy.virtual_portfolio import VirtualPosition

        config = GridConfig("BTCUSDT", 52000, 48000, 4, 1000, 1)
        state = create_grid(config)

        # 手动创建已有 long 仓位
        pos = VirtualPosition(
            "BTCUSDT", "long", 48000, 0.005, 1,
            "2026-01-01T00:00:00", "grid",
        )
        portfolio = _make_portfolio(9750.0, [pos])

        # 价格涨到最高 level
        new_state, new_portfolio = check_grid_triggers(state, 52000.0, portfolio)
        # 应触发 sell levels
        filled = [lv for lv in new_state.levels if lv.filled]
        assert len(filled) >= 1

    def test_no_trigger(self):
        state = self._make_state()
        portfolio = _make_portfolio(10000.0)
        # 价格在中间，buy levels 都高于此价格
        new_state, new_portfolio = check_grid_triggers(state, 50000.0, portfolio)
        # 50000 不低于任何 buy level (48000, 49000 都低于 50000)
        # 但 50000 >= buy level 的不触发，只有 <= 才触发
        # buy levels at 48000, 49000; 50000 > both → no buy trigger
        # sell levels at 50000, 51000, 52000; 50000 >= 50000 → sell trigger (但没仓位)
        assert new_portfolio.current_balance == 10000.0  # 没变化

    def test_immutability(self):
        state = self._make_state()
        portfolio = _make_portfolio(10000.0)
        new_state, new_portfolio = check_grid_triggers(state, 48000.0, portfolio)
        # 原始不变
        assert portfolio.current_balance == 10000.0
        assert all(not lv.filled for lv in state.levels)


class TestCalcGridMetrics:
    def test_metrics(self):
        config = GridConfig("BTCUSDT", 52000, 48000, 4, 1000, 1)
        levels = [
            GridLevel(48000, "buy", 0.005, True),
            GridLevel(49000, "buy", 0.005, False),
            GridLevel(50000, "sell", 0.005, False),
            GridLevel(51000, "sell", 0.005, True),
            GridLevel(52000, "sell", 0.005, False),
        ]
        state = GridState(config, levels, 25.0, 2, "2026-01-01")
        metrics = calc_grid_metrics(state)
        assert metrics["symbol"] == "BTCUSDT"
        assert metrics["filled_levels"] == 2
        assert metrics["total_levels"] == 5
        assert metrics["fill_rate"] == 0.4
        assert metrics["realized_pnl"] == 25.0


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        with patch("cryptobot.strategy.grid_trading.GRID_STATE_DIR", tmp_path):
            config = GridConfig("BTCUSDT", 52000, 48000, 4, 1000, 1)
            state = create_grid(config)
            save_grid_state(state)

            loaded = load_grid_state("BTCUSDT")
            assert loaded is not None
            assert loaded.config.symbol == "BTCUSDT"
            assert len(loaded.levels) == len(state.levels)

    def test_load_missing(self, tmp_path):
        with patch("cryptobot.strategy.grid_trading.GRID_STATE_DIR", tmp_path):
            assert load_grid_state("NONEXIST") is None


# ─── P14: wide_mode 宽网格 ──────────────────────────────


class TestWideMode:
    def test_wide_mode_fewer_levels(self):
        """wide_mode=True 时网格数量减半"""
        config = GridConfig("BTCUSDT", 52000.0, 48000.0, 10, 1000.0, 1)
        normal_state = create_grid(config, wide_mode=False)
        wide_state = create_grid(config, wide_mode=True)
        # normal: 10+1=11 levels, wide: 5+1=6 levels
        assert len(normal_state.levels) == 11
        assert len(wide_state.levels) == 6

    def test_wide_mode_wider_step(self):
        """wide_mode 网格间距更大"""
        config = GridConfig("BTCUSDT", 52000.0, 48000.0, 10, 1000.0, 1)
        normal_state = create_grid(config, wide_mode=False)
        wide_state = create_grid(config, wide_mode=True)
        normal_step = normal_state.levels[1].price - normal_state.levels[0].price
        wide_step = wide_state.levels[1].price - wide_state.levels[0].price
        assert wide_step > normal_step

    def test_wide_mode_min_count(self):
        """wide_mode grid_count=2 时保持至少 2 格"""
        config = GridConfig("BTCUSDT", 52000.0, 48000.0, 2, 1000.0, 1)
        state = create_grid(config, wide_mode=True)
        # 2 // 2 = 1 → max(2, 1) = 2 → 3 levels
        assert len(state.levels) >= 3


class TestFloatingLossProtection:
    def _make_filled_state(self):
        """所有 level 已填充的网格状态（不触发新交易）"""
        config = GridConfig("BTCUSDT", 52000.0, 48000.0, 4, 1000.0, 1)
        state = create_grid(config)
        filled_levels = [
            GridLevel(lv.price, lv.side, lv.amount, True) for lv in state.levels
        ]
        return GridState(config, filled_levels, 0.0, 0, state.created_at)

    def test_loss_over_5pct_closes_position(self):
        """浮亏 > 5% 的网格仓位被自动关闭"""
        from cryptobot.strategy.virtual_portfolio import VirtualPosition

        state = self._make_filled_state()

        # long 仓位 entry=50000, 当前价=47000 → 浮亏 6%
        pos = VirtualPosition(
            "BTCUSDT", "long", 50000, 0.005, 1,
            "2026-01-01T00:00:00", "grid",
        )
        portfolio = _make_portfolio(9750.0, [pos])

        new_state, new_portfolio = check_grid_triggers(state, 47000.0, portfolio)
        grid_longs = [p for p in new_portfolio.positions if p.strategy == "grid" and p.side == "long"]
        assert len(grid_longs) == 0  # 已被保护性平仓

    def test_small_loss_keeps_position(self):
        """浮亏 < 5% 的网格仓位保持不动"""
        from cryptobot.strategy.virtual_portfolio import VirtualPosition

        state = self._make_filled_state()

        # long 仓位 entry=50000, 当前价=48500 → 浮亏 3%
        pos = VirtualPosition(
            "BTCUSDT", "long", 50000, 0.005, 1,
            "2026-01-01T00:00:00", "grid",
        )
        portfolio = _make_portfolio(9750.0, [pos])

        new_state, new_portfolio = check_grid_triggers(state, 48500.0, portfolio)
        grid_longs = [p for p in new_portfolio.positions if p.strategy == "grid" and p.side == "long"]
        assert len(grid_longs) == 1  # 仓位保持
