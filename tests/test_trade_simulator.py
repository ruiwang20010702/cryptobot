"""trade_simulator 单元测试

覆盖: 多空止损/止盈/分批/超时/滑点/MFE/MAE/无效输入
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from cryptobot.backtest.cost_model import CostConfig
from cryptobot.backtest.trade_simulator import (
    TradeResult,
    simulate_trade,
    _parse_take_profits,
)


# ── 辅助函数 ──────────────────────────────────────────────────────────────

def _make_klines(
    prices: list[tuple[float, float, float, float]],
    start: str = "2026-01-01T00:00:00",
) -> pd.DataFrame:
    """构建 1h K线 DataFrame

    prices: [(open, high, low, close), ...]
    """
    ts = pd.date_range(start, periods=len(prices), freq="h")
    rows = [
        {"open": o, "high": h, "low": l, "close": c, "volume": 1000.0}
        for o, h, l, c in prices
    ]
    df = pd.DataFrame(rows, index=ts)
    return df


def _make_signal(
    symbol: str = "BTCUSDT",
    action: str = "long",
    entry_lo: float = 99.0,
    entry_hi: float = 101.0,
    stop_loss: float = 95.0,
    take_profit: list | None = None,
    leverage: int = 3,
    confidence: int = 80,
    signal_source: str = "ai",
    timestamp: str = "2026-01-01T00:00:00",
) -> dict:
    if take_profit is None:
        take_profit = [
            {"price": 105.0, "ratio": 0.5},
            {"price": 110.0, "ratio": 0.5},
        ]
    return {
        "symbol": symbol,
        "action": action,
        "entry_price_range": [entry_lo, entry_hi],
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "leverage": leverage,
        "confidence": confidence,
        "signal_source": signal_source,
        "timestamp": timestamp,
    }


# 零成本配置 (简化 PnL 验证)
ZERO_COST = CostConfig(taker_fee_pct=0, slippage_pct=0, funding_rate_per_8h=0)


# ── 基础测试 ──────────────────────────────────────────────────────────────

class TestSimulateTradeBasic:
    def test_returns_none_for_empty_klines(self):
        sig = _make_signal()
        assert simulate_trade(sig, pd.DataFrame()) is None

    def test_returns_none_for_missing_entry_range(self):
        sig = {"symbol": "BTC", "action": "long"}
        kl = _make_klines([(100, 101, 99, 100)] * 5)
        assert simulate_trade(sig, kl) is None

    def test_returns_none_for_invalid_entry_range(self):
        sig = _make_signal(entry_lo=0, entry_hi=0)
        kl = _make_klines([(100, 101, 99, 100)] * 5)
        assert simulate_trade(sig, kl) is None

    def test_trade_result_is_frozen(self):
        sig = _make_signal()
        kl = _make_klines([(100, 110, 90, 100)] * 10)
        result = simulate_trade(sig, kl, ZERO_COST)
        assert result is not None
        with pytest.raises(AttributeError):
            result.symbol = "ETH"  # type: ignore


# ── 止损测试 ──────────────────────────────────────────────────────────────

class TestStopLoss:
    def test_long_sl_hit(self):
        """多单: 价格跌破止损"""
        # entry_mid = 100, SL = 95
        prices = [
            (100, 102, 99, 101),  # bar 0: 正常
            (101, 103, 94, 95),   # bar 1: low=94 < SL=95 → 触发
        ]
        sig = _make_signal(stop_loss=95.0)
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "sl_hit"
        assert result.exit_price == 95.0
        assert result.net_pnl_pct < 0  # 亏损

    def test_short_sl_hit(self):
        """空单: 价格突破止损"""
        # entry_mid = 100, SL = 105, TP far away at 85
        prices = [
            (100, 101, 98, 99),   # bar 0: 正常
            (99, 106, 98, 104),   # bar 1: high=106 > SL=105 → 触发
        ]
        sig = _make_signal(
            action="short", stop_loss=105.0,
            take_profit=[{"price": 85.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "sl_hit"
        assert result.exit_price == 105.0
        assert result.net_pnl_pct < 0

    def test_sl_priority_over_tp(self):
        """同根K线止损优先于止盈"""
        # entry=100, SL=95, TP=110
        # bar 1: high=111(触TP) 但 low=94(触SL) → SL 先执行
        prices = [
            (100, 101, 99, 100),
            (100, 111, 94, 100),
        ]
        sig = _make_signal(
            stop_loss=95.0,
            take_profit=[{"price": 110.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "sl_hit"


# ── 止盈测试 ──────────────────────────────────────────────────────────────

class TestTakeProfit:
    def test_long_single_tp(self):
        """多单: 单级止盈全部平仓"""
        prices = [
            (100, 101, 99, 100),
            (100, 106, 99, 105),  # high=106 > TP=105
        ]
        sig = _make_signal(
            take_profit=[{"price": 105.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "tp_full"
        assert result.exit_price == 105.0
        assert result.net_pnl_pct > 0

    def test_long_partial_tp(self):
        """多单: 分批止盈 — 第一级触发，第二级未触发"""
        prices = [
            (100, 101, 99, 100),   # bar 0
            (100, 106, 99, 105),   # bar 1: TP1=105 触发 (50%)
            (105, 106, 104, 105),  # bar 2: TP2=110 未触发
            (105, 107, 104, 106),  # bar 3: 还是没到 110
        ]
        sig = _make_signal(
            take_profit=[
                {"price": 105.0, "ratio": 0.5},
                {"price": 110.0, "ratio": 0.5},
            ],
        )
        result = simulate_trade(
            sig, _make_klines(prices), ZERO_COST, max_bars=4,
        )
        assert result is not None
        # TP1 触发 (50%) + 剩余以收盘价平仓 → tp_partial 或 timeout
        assert result.exit_reason in ("tp_partial", "timeout")
        # exit_price = 105*0.5 + last_close*0.5
        expected_exit = 105.0 * 0.5 + 106.0 * 0.5
        assert abs(result.exit_price - expected_exit) < 0.01

    def test_long_full_tp_both_levels(self):
        """多单: 两级止盈全部触发"""
        prices = [
            (100, 101, 99, 100),
            (100, 106, 99, 105),   # TP1=105 触发
            (105, 111, 104, 110),  # TP2=110 触发
        ]
        sig = _make_signal(
            take_profit=[
                {"price": 105.0, "ratio": 0.5},
                {"price": 110.0, "ratio": 0.5},
            ],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "tp_full"
        expected_exit = 105.0 * 0.5 + 110.0 * 0.5
        assert abs(result.exit_price - expected_exit) < 0.01

    def test_short_tp_hit(self):
        """空单: 止盈触发"""
        prices = [
            (100, 101, 99, 100),
            (100, 101, 94, 95),   # low=94 < TP=95
        ]
        sig = _make_signal(
            action="short",
            stop_loss=105.0,
            take_profit=[{"price": 95.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "tp_full"
        assert result.net_pnl_pct > 0

    def test_tp_plain_price_list(self):
        """止盈列表为纯价格数组"""
        prices = [
            (100, 101, 99, 100),
            (100, 106, 99, 105),   # TP1=105 触发
            (105, 111, 104, 110),  # TP2=110 触发
        ]
        sig = _make_signal(take_profit=[105.0, 110.0])
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert result.exit_reason == "tp_full"


# ── 超时测试 ──────────────────────────────────────────────────────────────

class TestTimeout:
    def test_timeout_exit(self):
        """超时以最后收盘价平仓"""
        # 10根K线，价格稳定在100附近，不触发止损止盈
        prices = [(100, 101, 99, 100)] * 10
        sig = _make_signal(
            stop_loss=80.0,
            take_profit=[{"price": 120.0, "ratio": 1.0}],
        )
        result = simulate_trade(
            sig, _make_klines(prices), ZERO_COST, max_bars=10,
        )
        assert result is not None
        assert result.exit_reason == "timeout"
        assert result.exit_price == 100.0  # 最后收盘价


# ── 成本与滑点测试 ──────────────────────────────────────────────────────

class TestCostsAndSlippage:
    def test_costs_reduce_pnl(self):
        """默认成本降低净收益"""
        prices = [
            (100, 101, 99, 100),
            (100, 106, 99, 105),
        ]
        sig = _make_signal(
            take_profit=[{"price": 105.0, "ratio": 1.0}],
        )
        # 零成本
        r_zero = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        # 默认成本
        r_default = simulate_trade(sig, _make_klines(prices))
        assert r_zero is not None and r_default is not None
        assert r_default.net_pnl_pct < r_zero.net_pnl_pct
        assert r_default.costs_pct > 0

    def test_slippage_in_costs_not_entry(self):
        """滑点在 cost_model 中扣除，不偏移入场价"""
        prices = [(100, 101, 99, 100)] * 5
        sig = _make_signal()
        # 有滑点
        cfg_slip = CostConfig(taker_fee_pct=0, slippage_pct=0.1, funding_rate_per_8h=0)
        r = simulate_trade(sig, _make_klines(prices), cfg_slip, max_bars=5)
        assert r is not None
        # 入场价 = entry_range 中点，滑点在 costs_pct 中体现
        assert r.entry_price == 100.0
        assert r.costs_pct > 0


# ── MFE / MAE 测试 ──────────────────────────────────────────────────────

class TestMfeMAe:
    def test_mfe_mae_long(self):
        """多单 MFE/MAE 计算"""
        # entry ≈ 100, high=110 → MFE≈10%, low=90 → MAE≈10%
        prices = [
            (100, 110, 90, 100),
        ] * 3
        sig = _make_signal(stop_loss=80.0, take_profit=[{"price": 120, "ratio": 1.0}])
        result = simulate_trade(
            sig, _make_klines(prices), ZERO_COST, max_bars=3,
        )
        assert result is not None
        assert result.mfe_pct > 5.0
        assert result.mae_pct > 5.0

    def test_mfe_mae_short(self):
        """空单 MFE/MAE 计算"""
        # entry ≈ 100, low=90 → MFE≈10%, high=110 → MAE≈10%
        prices = [
            (100, 110, 90, 100),
        ] * 3
        sig = _make_signal(
            action="short", stop_loss=120.0,
            take_profit=[{"price": 80, "ratio": 1.0}],
        )
        result = simulate_trade(
            sig, _make_klines(prices), ZERO_COST, max_bars=3,
        )
        assert result is not None
        assert result.mfe_pct > 5.0
        assert result.mae_pct > 5.0


# ── PnL 精度测试 ──────────────────────────────────────────────────────────

class TestPnlAccuracy:
    def test_long_profit_calculation(self):
        """多单盈利: PnL = (exit-entry)/entry * leverage * 100"""
        # entry=100, exit=105, lev=3 → gross = 5/100*3*100 = 15%
        prices = [
            (100, 101, 99, 100),
            (100, 106, 99, 105),
        ]
        sig = _make_signal(
            leverage=3,
            take_profit=[{"price": 105.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert abs(result.gross_pnl_pct - 15.0) < 0.5  # 容差: 滑点=0

    def test_short_profit_calculation(self):
        """空单盈利: PnL = (entry-exit)/entry * leverage * 100"""
        # entry=100, exit=95, lev=3 → gross = 5/100*3*100 = 15%
        prices = [
            (100, 101, 99, 100),
            (100, 101, 94, 95),
        ]
        sig = _make_signal(
            action="short",
            leverage=3,
            stop_loss=110.0,
            take_profit=[{"price": 95.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, _make_klines(prices), ZERO_COST)
        assert result is not None
        assert abs(result.gross_pnl_pct - 15.0) < 0.5

    def test_net_pnl_usdt(self):
        """USDT PnL = position * leverage * net_pnl_pct / 100"""
        prices = [
            (100, 101, 99, 100),
            (100, 106, 99, 105),
        ]
        sig = _make_signal(
            leverage=3,
            take_profit=[{"price": 105.0, "ratio": 1.0}],
        )
        result = simulate_trade(
            sig, _make_klines(prices), ZERO_COST, position_usdt=500.0,
        )
        assert result is not None
        expected_usdt = 500.0 * 3 * result.net_pnl_pct / 100
        assert abs(result.net_pnl_usdt - expected_usdt) < 0.1


# ── 辅助函数测试 ──────────────────────────────────────────────────────────

class TestParseTakeProfits:
    def test_dict_format(self):
        tps = [{"price": 105, "ratio": 0.5}, {"price": 110, "ratio": 0.5}]
        levels = _parse_take_profits(tps, is_long=True)
        assert len(levels) == 2
        assert levels[0]["price"] == 105  # long: 低价先触发

    def test_plain_price_format(self):
        tps = [105, 110]
        levels = _parse_take_profits(tps, is_long=True)
        assert len(levels) == 2
        assert levels[0]["ratio"] == 0.5

    def test_empty_list(self):
        assert _parse_take_profits([], is_long=True) == []

    def test_short_order(self):
        """空单: 高价先触发"""
        tps = [90, 85]
        levels = _parse_take_profits(tps, is_long=False)
        assert levels[0]["price"] == 90
