"""资金费率套利测试"""

from unittest.mock import patch, MagicMock

import pytest

from cryptobot.strategy.funding_arb import (
    FundingArbSignal,
    _annualize_rate,
    calc_arb_pnl,
    check_arb_positions,
    execute_arb_virtual,
    scan_funding_opportunities,
)
from cryptobot.strategy.virtual_portfolio import (
    VirtualPortfolio,
    VirtualPosition,
)


def _make_portfolio(balance=10000.0, positions=None, closed=None):
    return VirtualPortfolio(
        initial_balance=balance,
        current_balance=balance,
        positions=positions or [],
        closed_trades=closed or [],
        updated_at="2026-01-01T00:00:00",
    )


def _make_signal(symbol="BTCUSDT", rate=0.0003, action="open"):
    return FundingArbSignal(
        symbol=symbol,
        funding_rate=rate,
        annualized_rate=_annualize_rate(rate),
        action=action,
        entry_threshold=0.0001,
        exit_threshold=0.00003,
        confidence=70,
    )


class TestAnnualizeRate:
    def test_positive_rate(self):
        # 0.01% / 8h = 0.0001 → 年化 = 0.0001 * 3 * 365 * 100 = 10.95%
        result = _annualize_rate(0.0001)
        assert result == pytest.approx(10.95, abs=0.01)

    def test_zero_rate(self):
        assert _annualize_rate(0) == 0.0


class TestScanFundingOpportunities:
    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    @patch("cryptobot.data.onchain.get_funding_rate")
    def test_finds_opportunity(self, mock_rate, mock_cfg):
        mock_cfg.return_value = {
            "enabled": True,
            "min_funding_rate": 0.01,
            "consecutive_positive": 3,
            "max_positions": 3,
        }
        mock_rate.return_value = {
            "rates": [
                {"rate": 0.0003},
                {"rate": 0.0004},
                {"rate": 0.0005},
            ],
        }
        signals = scan_funding_opportunities(symbols=["BTCUSDT"])
        assert len(signals) == 1
        assert signals[0].symbol == "BTCUSDT"
        assert signals[0].action == "open"

    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    def test_disabled(self, mock_cfg):
        mock_cfg.return_value = {"enabled": False}
        assert scan_funding_opportunities() == []

    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    @patch("cryptobot.data.onchain.get_funding_rate")
    def test_negative_rate_rejected(self, mock_rate, mock_cfg):
        mock_cfg.return_value = {
            "enabled": True,
            "min_funding_rate": 0.01,
            "consecutive_positive": 3,
            "max_positions": 3,
        }
        mock_rate.return_value = {
            "rates": [
                {"rate": 0.0003},
                {"rate": -0.0001},  # 中断连续
                {"rate": 0.0005},
            ],
        }
        signals = scan_funding_opportunities(symbols=["BTCUSDT"])
        assert len(signals) == 0


class TestExecuteArbVirtual:
    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    def test_opens_short(self, mock_cfg):
        mock_cfg.return_value = {"position_size_pct": 20}
        portfolio = _make_portfolio(10000.0)
        signal = _make_signal()
        result = execute_arb_virtual(signal, portfolio, 50000.0, 50000.0)
        # margin = 10000 * 20% = 2000
        # amount = 2000 / 50000 = 0.04
        assert len(result.positions) == 1
        assert result.positions[0].side == "short"
        assert result.positions[0].strategy == "funding_arb"

    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    def test_insufficient_balance(self, mock_cfg):
        mock_cfg.return_value = {"position_size_pct": 20}
        portfolio = _make_portfolio(5.0)  # 太少
        signal = _make_signal()
        result = execute_arb_virtual(signal, portfolio, 50000.0, 50000.0)
        # 余额不足，跳过
        assert len(result.positions) == 0


class TestCheckArbPositions:
    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    def test_rate_turns_negative(self, mock_cfg):
        mock_cfg.return_value = {"min_funding_rate": 0.01}
        pos = VirtualPosition(
            "BTCUSDT", "short", 50000, 0.04, 1,
            "2026-01-01T00:00:00", "funding_arb",
        )
        portfolio = _make_portfolio(8000.0, [pos])
        # 费率变为负
        close_signals = check_arb_positions(portfolio, {"BTCUSDT": -0.0001})
        assert len(close_signals) == 1
        assert close_signals[0].action == "close"

    @patch("cryptobot.strategy.funding_arb._get_arb_config")
    def test_rate_still_positive(self, mock_cfg):
        mock_cfg.return_value = {"min_funding_rate": 0.01}
        pos = VirtualPosition(
            "BTCUSDT", "short", 50000, 0.04, 1,
            "2026-01-01T00:00:00", "funding_arb",
        )
        portfolio = _make_portfolio(8000.0, [pos])
        close_signals = check_arb_positions(portfolio, {"BTCUSDT": 0.0005})
        assert len(close_signals) == 0


class TestCalcArbPnl:
    def test_empty(self):
        portfolio = _make_portfolio()
        result = calc_arb_pnl(portfolio)
        assert result["total_trades"] == 0

    def test_with_trades(self):
        closed = [
            {"pnl": 100, "strategy": "funding_arb"},
            {"pnl": -30, "strategy": "funding_arb"},
            {"pnl": 50, "strategy": "grid"},  # 不是套利
        ]
        portfolio = _make_portfolio(closed=closed)
        result = calc_arb_pnl(portfolio)
        assert result["total_trades"] == 2
        assert result["total_pnl"] == 70.0
        assert result["win_rate"] == 0.5
