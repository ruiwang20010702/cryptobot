"""MFE 自适应尾随止盈测试

覆盖: 2×ATR 保本触发 / 未达阈值原始止损 / 阶梯收紧 / exit_strategy 字段 / 空单
"""

import pandas as pd

from cryptobot.backtest.cost_model import CostConfig
from cryptobot.backtest.trade_simulator import simulate_trade


# ── 辅助 ──────────────────────────────────────────────────────────────────

ZERO_COST = CostConfig(taker_fee_pct=0, slippage_pct=0, funding_rate_per_8h=0)


def _make_signal(
    action: str = "long",
    entry: float = 100.0,
    sl: float = 95.0,
    tp: list | None = None,
) -> dict:
    return {
        "symbol": "TESTUSDT",
        "action": action,
        "entry_price_range": [entry - 0.5, entry + 0.5],
        "stop_loss": sl,
        "take_profit": tp or [{"price": 120.0, "ratio": 1.0}],
        "leverage": 3,
        "confidence": 70,
        "timestamp": "2026-01-01T00:00:00",
        "signal_source": "ai",
    }


def _make_klines(
    ohlc_list: list[tuple[float, float, float, float]],
) -> pd.DataFrame:
    """从 (open, high, low, close) 列表构造 1h K 线"""
    rows = [
        {"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}
        for o, h, lo, c in ohlc_list
    ]
    idx = pd.date_range("2026-01-01", periods=len(ohlc_list), freq="h")
    return pd.DataFrame(rows, index=idx)


# ── 测试类 ────────────────────────────────────────────────────────────────


class TestMFETrailing:
    """MFE 自适应尾随止盈"""

    def test_mfe_above_2atr_then_reverse_exits_at_breakeven(self):
        """浮盈 > 2×ATR 后反转 → 在保本位 (≥ entry) 出场"""
        # ATR% = 1.0, 触发 = 2%
        # 价格: 100 → 逐步涨到 104 (MFE≈4%) → 回落到 99 触止损
        klines = _make_klines(
            [(100, 101, 99, 100)]           # bar 0
            + [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(1, 5)]  # bar 1-4: 涨
            + [(104, 104.5, 99, 99)] * 3    # bar 5-7: 暴跌回 99
        )
        sig = _make_signal(sl=95.0, tp=[{"price": 120.0, "ratio": 1.0}])
        result = simulate_trade(
            sig, klines, ZERO_COST,
            mfe_trailing=True, atr_pct=1.0, max_bars=8,
        )
        assert result is not None
        assert result.exit_reason == "sl_hit"
        assert result.exit_strategy == "mfe_trailing"
        # MFE 尾随止损应 >= entry_price (保本)
        assert result.exit_price >= 99.5  # entry_mid=100, 零滑点

    def test_mfe_below_2atr_uses_original_sl(self):
        """浮盈 < 2×ATR 就反转 → 按原始止损出场"""
        # ATR% = 3.0, 触发 = 6%
        # 价格只涨 2% 就回落 → 不触发 MFE 尾随
        klines = _make_klines(
            [(100, 101, 99, 100)]           # bar 0
            + [(100, 102, 99.5, 101)]       # bar 1: 小涨
            + [(101, 102, 94, 95)] * 2      # bar 2-3: 跌破原始 SL=95
        )
        sig = _make_signal(sl=95.0)
        result = simulate_trade(
            sig, klines, ZERO_COST,
            mfe_trailing=True, atr_pct=3.0, max_bars=4,
        )
        assert result is not None
        assert result.exit_reason == "sl_hit"
        assert result.exit_strategy == "fixed"  # 未触发 MFE
        assert result.exit_price == 95.0

    def test_step_tightening_moves_sl_up(self):
        """阶梯收紧: MFE 从 2% → 5% → 止损上移到 entry 以上"""
        # ATR% = 1.0, trigger = 2%
        # bar 0: 正常, bar 1-5: 逐步涨到 105
        # MFE 在 bar 5 达到 ~5%, trail_steps = int((5-2)/1) = 3
        # tightened_sl = 100 * (1 + 3*1/100) = 103
        # bar 6-7: 回落但 low 不触发 103
        # bar 8: low=102.5 < 103 → 止损
        klines = _make_klines(
            [(100, 101, 99.5, 100)]                           # bar 0
            + [(100 + i, 101 + i, 100 + i - 0.5, 100 + i)
               for i in range(1, 6)]                          # bar 1-5: 涨到 105
            + [(105, 105.5, 103.5, 104)]                      # bar 6: 小回落
            + [(104, 104.5, 103.2, 103.5)]                    # bar 7: 继续下行
            + [(103.5, 103.5, 102.5, 103)]                    # bar 8: low < 103 触止损
        )
        sig = _make_signal(sl=95.0, tp=[{"price": 115.0, "ratio": 1.0}])
        result = simulate_trade(
            sig, klines, ZERO_COST,
            mfe_trailing=True, atr_pct=1.0, max_bars=10,
        )
        assert result is not None
        assert result.exit_strategy == "mfe_trailing"
        # 止损应在 entry 以上 (保本+阶梯): effective_sl = 103
        assert result.exit_price >= 103.0
        assert result.exit_price > 100.0

    def test_exit_strategy_field_default_fixed(self):
        """不启用 mfe_trailing 时 exit_strategy = 'fixed'"""
        klines = _make_klines(
            [(100, 101, 99, 100)]
            + [(100, 101, 94, 95)]  # SL hit
        )
        sig = _make_signal(sl=95.0)
        result = simulate_trade(sig, klines, ZERO_COST, mfe_trailing=False)
        assert result is not None
        assert result.exit_strategy == "fixed"

    def test_exit_strategy_field_in_dataclass(self):
        """TradeResult.exit_strategy 在 frozen dataclass 中存在"""
        klines = _make_klines([(100, 106, 99, 105)] * 5)
        sig = _make_signal(
            tp=[{"price": 105.0, "ratio": 1.0}],
        )
        result = simulate_trade(sig, klines, ZERO_COST)
        assert result is not None
        assert hasattr(result, "exit_strategy")
        assert result.exit_strategy in ("fixed", "mfe_trailing")

    def test_short_mfe_trailing(self):
        """空单: MFE 尾随止损 — 止损下移"""
        # ATR% = 1.0, trigger = 2%
        # 空单: entry≈100, 价格跌到 96 (MFE=4%) → 反弹到 101
        klines = _make_klines(
            [(100, 101, 99, 100)]                             # bar 0
            + [(100 - i, 101 - i, 99 - i, 100 - i) for i in range(1, 5)]  # 跌到 96
            + [(96, 101, 95.5, 100)]                          # bar 5: 反弹到 101
        )
        sig = _make_signal(
            action="short", sl=105.0,
            tp=[{"price": 90.0, "ratio": 1.0}],
        )
        result = simulate_trade(
            sig, klines, ZERO_COST,
            mfe_trailing=True, atr_pct=1.0, max_bars=6,
        )
        assert result is not None
        assert result.exit_strategy == "mfe_trailing"
        # 空单保本止损 <= entry
        assert result.exit_price <= 100.5

    def test_mfe_trailing_disabled_when_atr_none(self):
        """atr_pct=None 时不激活 MFE 尾随"""
        klines = _make_klines(
            [(100, 101, 99, 100)]
            + [(100, 101, 94, 95)]
        )
        sig = _make_signal(sl=95.0)
        result = simulate_trade(
            sig, klines, ZERO_COST,
            mfe_trailing=True, atr_pct=None,
        )
        assert result is not None
        assert result.exit_strategy == "fixed"
        assert result.exit_price == 95.0

    def test_tp_still_works_with_mfe_trailing(self):
        """MFE 尾随启用时止盈仍正常触发"""
        # ATR% = 2.0, trigger = 4%
        # bar 0: MFE = 1% (< trigger), bar 1: MFE = 3% (< trigger)
        # bar 2: high=106 触发 TP=105, MFE=6% >= trigger=4%
        #   trail_steps=int((6-4)/2)=1, tightened_sl=100*(1+1*2/100)=102
        #   low=104.5 > effective_sl=102 → SL 不触发 → TP 正常触发
        klines = _make_klines(
            [(100, 101, 99.5, 100)]         # bar 0
            + [(100, 103, 100, 102)]        # bar 1
            + [(102, 106, 104.5, 105)]      # bar 2: TP=105 触发
        )
        sig = _make_signal(
            sl=95.0,
            tp=[{"price": 105.0, "ratio": 1.0}],
        )
        result = simulate_trade(
            sig, klines, ZERO_COST,
            mfe_trailing=True, atr_pct=2.0,
        )
        assert result is not None
        assert result.exit_reason == "tp_full"
        assert result.exit_price == 105.0
