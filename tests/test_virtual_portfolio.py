"""虚拟盘基础设施测试"""

import json
from unittest.mock import patch

import pytest

from cryptobot.strategy.virtual_portfolio import (
    VirtualPortfolio,
    VirtualPosition,
    close_position,
    get_portfolio_summary,
    get_unrealized_pnl,
    load_portfolio,
    open_position,
    save_portfolio,
)


def _make_portfolio(balance=10000.0, positions=None, closed=None):
    return VirtualPortfolio(
        initial_balance=balance,
        current_balance=balance,
        positions=positions or [],
        closed_trades=closed or [],
        updated_at="2026-01-01T00:00:00",
    )


def _make_position(symbol="BTCUSDT", side="long", price=50000.0, amount=0.1, leverage=1):
    return VirtualPosition(
        symbol=symbol,
        side=side,
        entry_price=price,
        amount=amount,
        leverage=leverage,
        opened_at="2026-01-01T00:00:00",
        strategy="test",
    )


class TestOpenPosition:
    def test_opens_long(self):
        portfolio = _make_portfolio(10000.0)
        pos = _make_position(price=50000.0, amount=0.1, leverage=1)
        # margin = 0.1 * 50000 / 1 = 5000
        result = open_position(portfolio, pos)
        assert result.current_balance == 5000.0
        assert len(result.positions) == 1
        assert result.positions[0].symbol == "BTCUSDT"

    def test_opens_with_leverage(self):
        portfolio = _make_portfolio(1000.0)
        pos = _make_position(price=50000.0, amount=0.1, leverage=5)
        # margin = 0.1 * 50000 / 5 = 1000
        result = open_position(portfolio, pos)
        assert result.current_balance == 0.0

    def test_insufficient_balance(self):
        portfolio = _make_portfolio(100.0)
        pos = _make_position(price=50000.0, amount=0.1, leverage=1)
        with pytest.raises(ValueError, match="余额不足"):
            open_position(portfolio, pos)

    def test_immutability(self):
        portfolio = _make_portfolio(10000.0)
        pos = _make_position()
        result = open_position(portfolio, pos)
        # 原始 portfolio 不变
        assert portfolio.current_balance == 10000.0
        assert len(portfolio.positions) == 0
        assert result is not portfolio


class TestClosePosition:
    def test_close_long_profit(self):
        pos = _make_position(price=50000.0, amount=0.1, leverage=1)
        portfolio = _make_portfolio(5000.0, [pos])
        result = close_position(portfolio, "BTCUSDT", "long", 55000.0)
        # pnl = (55000 - 50000) * 0.1 * 1 = 500
        # fee = 5000 * 0.001 = 5 (往返手续费 0.1%)
        # net_pnl = 500 - 5 = 495
        # margin = 0.1 * 50000 / 1 = 5000
        # new_balance = 5000 + 5000 + 495 = 10495
        assert result.current_balance == 10495.0
        assert len(result.positions) == 0
        assert len(result.closed_trades) == 1
        assert result.closed_trades[0]["pnl"] == 495.0

    def test_close_short_profit(self):
        pos = _make_position(side="short", price=50000.0, amount=0.1, leverage=2)
        portfolio = _make_portfolio(7500.0, [pos])
        result = close_position(portfolio, "BTCUSDT", "short", 48000.0)
        # pnl = (50000 - 48000) * 0.1 * 2 = 400
        # fee = 2500 * 0.001 = 2.5 (往返手续费 0.1%)
        # net_pnl = 400 - 2.5 = 397.5
        # margin = 0.1 * 50000 / 2 = 2500
        # new_balance = 7500 + 2500 + 397.5 = 10397.5
        assert result.current_balance == 10397.5

    def test_close_long_loss(self):
        pos = _make_position(price=50000.0, amount=0.1, leverage=1)
        portfolio = _make_portfolio(5000.0, [pos])
        result = close_position(portfolio, "BTCUSDT", "long", 45000.0)
        # pnl = (45000 - 50000) * 0.1 * 1 = -500
        # fee = 5000 * 0.001 = 5 (往返手续费 0.1%)
        # net_pnl = -500 - 5 = -505
        assert result.current_balance == 9495.0
        assert result.closed_trades[0]["pnl"] == -505.0

    def test_close_not_found(self):
        portfolio = _make_portfolio()
        with pytest.raises(ValueError, match="未找到匹配仓位"):
            close_position(portfolio, "BTCUSDT", "long", 50000.0)

    def test_close_with_strategy_filter(self):
        pos1 = VirtualPosition("BTCUSDT", "long", 50000, 0.1, 1, "t", "grid")
        pos2 = VirtualPosition("BTCUSDT", "long", 48000, 0.2, 1, "t", "funding_arb")
        portfolio = _make_portfolio(5000.0, [pos1, pos2])
        result = close_position(portfolio, "BTCUSDT", "long", 52000.0, "grid")
        # 应该平掉 pos1 (grid)
        assert len(result.positions) == 1
        assert result.positions[0].strategy == "funding_arb"


class TestUnrealizedPnl:
    def test_long_profit(self):
        pos = _make_position(price=50000.0, amount=0.1, leverage=1)
        portfolio = _make_portfolio(5000.0, [pos])
        pnl = get_unrealized_pnl(portfolio, {"BTCUSDT": 55000.0})
        assert pnl == 500.0

    def test_short_profit(self):
        pos = _make_position(side="short", price=50000.0, amount=0.1, leverage=2)
        portfolio = _make_portfolio(5000.0, [pos])
        pnl = get_unrealized_pnl(portfolio, {"BTCUSDT": 48000.0})
        # (50000 - 48000) * 0.1 * 2 = 400
        assert pnl == 400.0

    def test_no_price_data(self):
        pos = _make_position()
        portfolio = _make_portfolio(5000.0, [pos])
        pnl = get_unrealized_pnl(portfolio, {})
        assert pnl == 0.0


class TestPortfolioSummary:
    def test_summary(self):
        closed = [{"pnl": 100}, {"pnl": -50}]
        portfolio = _make_portfolio(10000.0, closed=closed)
        summary = get_portfolio_summary(portfolio)
        assert summary["realized_pnl"] == 50.0
        assert summary["total_return_pct"] == 0.5


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        with patch("cryptobot.strategy.virtual_portfolio.VIRTUAL_DIR", tmp_path):
            pos = _make_position()
            portfolio = _make_portfolio(10000.0, [pos])
            save_portfolio(portfolio, "test")

            loaded = load_portfolio("test", 10000.0)
            assert loaded.initial_balance == 10000.0
            assert len(loaded.positions) == 1
            assert loaded.positions[0].symbol == "BTCUSDT"

    def test_load_missing_creates_new(self, tmp_path):
        with patch("cryptobot.strategy.virtual_portfolio.VIRTUAL_DIR", tmp_path):
            portfolio = load_portfolio("nonexistent", 5000.0)
            assert portfolio.initial_balance == 5000.0
            assert portfolio.current_balance == 5000.0
            assert len(portfolio.positions) == 0
