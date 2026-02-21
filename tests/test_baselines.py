"""基线信号生成器测试"""

import numpy as np
import pandas as pd


# ─── 辅助: 构建确定性 K 线 ──────────────────────────────────────────────────

def _make_klines(
    n: int = 100,
    base_close: float = 50000.0,
    trend: str = "flat",
) -> pd.DataFrame:
    """构建确定性 K 线 DataFrame

    trend: "flat" | "up" | "down" | "volatile"
    """
    dates = pd.date_range("2026-01-01", periods=n, freq="1h")
    closes = np.full(n, base_close, dtype=np.float64)

    if trend == "up":
        closes = base_close + np.arange(n, dtype=np.float64) * 100
    elif trend == "down":
        closes = base_close - np.arange(n, dtype=np.float64) * 100
    elif trend == "volatile":
        # 大幅波动: 在 base 上下 10% 震荡
        swing = base_close * 0.10
        closes = base_close + swing * np.sin(np.linspace(0, 6 * np.pi, n))

    highs = closes * 1.005
    lows = closes * 0.995
    opens = closes * 1.001
    volumes = np.full(n, 1000.0)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def _make_cache(n: int = 100, **kwargs) -> dict[str, pd.DataFrame]:
    """构建单币种 klines_cache"""
    return {"BTCUSDT": _make_klines(n=n, **kwargs)}


# ─── 信号格式校验 ────────────────────────────────────────────────────────────

_REQUIRED_FIELDS = {
    "symbol", "action", "entry_price_range", "stop_loss",
    "take_profit", "leverage", "confidence", "signal_source", "timestamp",
}


def _assert_valid_signal(sig: dict) -> None:
    """验证信号包含所有必需字段且格式正确"""
    missing = _REQUIRED_FIELDS - sig.keys()
    assert not missing, f"缺少字段: {missing}"

    assert sig["action"] in ("long", "short")
    assert isinstance(sig["entry_price_range"], list)
    assert len(sig["entry_price_range"]) == 2
    assert sig["entry_price_range"][0] < sig["entry_price_range"][1]

    assert isinstance(sig["stop_loss"], (int, float))
    assert isinstance(sig["take_profit"], list)
    assert len(sig["take_profit"]) == 2
    for tp in sig["take_profit"]:
        assert "price" in tp
        assert "ratio" in tp
        assert tp["ratio"] == 0.5

    assert isinstance(sig["leverage"], int)
    assert sig["confidence"] == 65
    assert sig["signal_source"] in ("random", "ma_cross", "rsi", "bollinger")
    assert isinstance(sig["timestamp"], str)


# ─── _build_signal_from_kline ────────────────────────────────────────────────

class TestBuildSignal:
    def test_long_signal_structure(self):
        from cryptobot.backtest.baselines import _build_signal_from_kline

        row = pd.Series(
            {"close": 50000.0, "high": 50100.0, "low": 49900.0},
            name=pd.Timestamp("2026-01-01"),
        )
        sig = _build_signal_from_kline(
            "BTCUSDT", "long", row, atr=500.0, signal_source="random",
        )
        _assert_valid_signal(sig)
        assert sig["stop_loss"] == 50000.0 - 2 * 500.0
        assert sig["take_profit"][0]["price"] == 50000.0 + 3 * 500.0
        assert sig["take_profit"][1]["price"] == 50000.0 + 5 * 500.0

    def test_short_signal_structure(self):
        from cryptobot.backtest.baselines import _build_signal_from_kline

        row = pd.Series(
            {"close": 50000.0, "high": 50100.0, "low": 49900.0},
            name=pd.Timestamp("2026-01-01"),
        )
        sig = _build_signal_from_kline(
            "BTCUSDT", "short", row, atr=500.0, signal_source="random",
        )
        _assert_valid_signal(sig)
        assert sig["stop_loss"] == 50000.0 + 2 * 500.0
        assert sig["take_profit"][0]["price"] == 50000.0 - 3 * 500.0
        assert sig["take_profit"][1]["price"] == 50000.0 - 5 * 500.0


# ─── generate_random_signals ─────────────────────────────────────────────────

class TestRandomSignals:
    def test_count_matches_reference(self):
        """生成数量 = reference 数量"""
        from cryptobot.backtest.baselines import generate_random_signals

        cache = _make_cache(n=100)
        refs = [
            {"symbol": "BTCUSDT", "action": "long", "leverage": 3},
            {"symbol": "BTCUSDT", "action": "short", "leverage": 2},
        ]
        result = generate_random_signals(refs, cache)
        assert len(result) == 2

    def test_preserves_symbol_action_leverage(self):
        """保持 symbol/action/leverage 分布一致"""
        from cryptobot.backtest.baselines import generate_random_signals

        cache = _make_cache(n=100)
        refs = [
            {"symbol": "BTCUSDT", "action": "long", "leverage": 5},
            {"symbol": "BTCUSDT", "action": "short", "leverage": 2},
        ]
        result = generate_random_signals(refs, cache)
        assert result[0]["symbol"] == "BTCUSDT"
        assert result[0]["action"] == "long"
        assert result[0]["leverage"] == 5
        assert result[1]["action"] == "short"
        assert result[1]["leverage"] == 2

    def test_seed_reproducible(self):
        """相同 seed 生成相同结果"""
        from cryptobot.backtest.baselines import generate_random_signals

        cache = _make_cache(n=100)
        refs = [{"symbol": "BTCUSDT", "action": "long", "leverage": 3}]

        r1 = generate_random_signals(refs, cache, seed=123)
        r2 = generate_random_signals(refs, cache, seed=123)
        assert r1[0]["timestamp"] == r2[0]["timestamp"]
        assert r1[0]["entry_price_range"] == r2[0]["entry_price_range"]

    def test_different_seed_different_result(self):
        """不同 seed 生成不同结果"""
        from cryptobot.backtest.baselines import generate_random_signals

        cache = _make_cache(n=100, trend="up")
        refs = [{"symbol": "BTCUSDT", "action": "long", "leverage": 3}]

        r1 = generate_random_signals(refs, cache, seed=1)
        r2 = generate_random_signals(refs, cache, seed=999)
        # 极小概率两个 seed 选中同一根，用 trend=up 降低概率
        assert r1[0]["timestamp"] != r2[0]["timestamp"]

    def test_signal_format(self):
        """所有信号格式正确"""
        from cryptobot.backtest.baselines import generate_random_signals

        cache = _make_cache(n=100)
        refs = [{"symbol": "BTCUSDT", "action": "long", "leverage": 3}]
        result = generate_random_signals(refs, cache)
        for sig in result:
            _assert_valid_signal(sig)
            assert sig["signal_source"] == "random"

    def test_empty_reference(self):
        """空 reference 返回空列表"""
        from cryptobot.backtest.baselines import generate_random_signals

        assert generate_random_signals([], {}) == []

    def test_missing_symbol_in_cache(self):
        """reference 中的 symbol 不在 cache 中，跳过"""
        from cryptobot.backtest.baselines import generate_random_signals

        refs = [{"symbol": "ETHUSDT", "action": "long", "leverage": 3}]
        cache = _make_cache(n=100)  # 只有 BTCUSDT
        result = generate_random_signals(refs, cache)
        assert len(result) == 0


# ─── generate_ma_cross_signals ───────────────────────────────────────────────

class TestMACrossSignals:
    def test_golden_cross_generates_long(self):
        """构建明确金叉场景 -> 生成 long 信号"""
        from cryptobot.backtest.baselines import generate_ma_cross_signals

        # 构建: 前 40 根下跌，后 60 根上涨 -> 在转折处产生金叉
        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.concatenate([
            np.linspace(50000, 48000, 40),  # 下跌: fast < slow
            np.linspace(48000, 55000, 60),  # 上涨: fast 穿越 slow
        ])
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_ma_cross_signals(cache, fast=7, slow=25)

        # 至少产生一个 long 信号
        long_signals = [s for s in result if s["action"] == "long"]
        assert len(long_signals) >= 1
        for sig in result:
            _assert_valid_signal(sig)
            assert sig["signal_source"] == "ma_cross"

    def test_death_cross_generates_short(self):
        """构建死叉场景 -> 生成 short 信号"""
        from cryptobot.backtest.baselines import generate_ma_cross_signals

        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.concatenate([
            np.linspace(48000, 55000, 40),  # 上涨: fast > slow
            np.linspace(55000, 45000, 60),  # 下跌: fast 穿越 slow
        ])
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_ma_cross_signals(cache, fast=7, slow=25)

        short_signals = [s for s in result if s["action"] == "short"]
        assert len(short_signals) >= 1

    def test_empty_klines(self):
        """空K线 -> 返回空列表"""
        from cryptobot.backtest.baselines import generate_ma_cross_signals

        assert generate_ma_cross_signals({}) == []
        assert generate_ma_cross_signals({"BTCUSDT": pd.DataFrame()}) == []

    def test_insufficient_data(self):
        """数据量不足 -> 返回空"""
        from cryptobot.backtest.baselines import generate_ma_cross_signals

        cache = _make_cache(n=10)  # 少于 slow + 10 = 35
        assert generate_ma_cross_signals(cache) == []


# ─── generate_rsi_signals ────────────────────────────────────────────────────

class TestRSISignals:
    def test_oversold_generates_long(self):
        """RSI < 30 场景 -> 生成 long 信号"""
        from cryptobot.backtest.baselines import generate_rsi_signals

        # 持续下跌制造超卖
        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.linspace(60000, 40000, n)
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_rsi_signals(cache, oversold=30, overbought=70)

        long_signals = [s for s in result if s["action"] == "long"]
        assert len(long_signals) >= 1
        for sig in result:
            _assert_valid_signal(sig)
            assert sig["signal_source"] == "rsi"

    def test_overbought_generates_short(self):
        """RSI > 70 场景 -> 生成 short 信号"""
        from cryptobot.backtest.baselines import generate_rsi_signals

        # 持续上涨制造超买
        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.linspace(40000, 60000, n)
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_rsi_signals(cache, oversold=30, overbought=70)

        short_signals = [s for s in result if s["action"] == "short"]
        assert len(short_signals) >= 1

    def test_empty_klines(self):
        """空K线 -> 返回空列表"""
        from cryptobot.backtest.baselines import generate_rsi_signals

        assert generate_rsi_signals({}) == []

    def test_cooldown_respected(self):
        """冷却期内不产生重复信号"""
        from cryptobot.backtest.baselines import generate_rsi_signals

        # 持续下跌: RSI 长期 < 30，但冷却限制信号数量
        n = 200
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.linspace(60000, 30000, n)
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_rsi_signals(cache)
        # 200 根 / 24 根冷却 = 最多约 8 个信号 (去掉前 14 根)
        assert len(result) <= 10


# ─── generate_bollinger_signals ──────────────────────────────────────────────

class TestBollingerSignals:
    def test_lower_band_touch_generates_long(self):
        """触及下轨 -> 生成 long 信号"""
        from cryptobot.backtest.baselines import generate_bollinger_signals

        # 先平稳再急跌: close 低于 lower band
        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.concatenate([
            np.full(60, 50000.0),   # 前 60 根平稳建立布林带
            np.linspace(50000, 45000, 40),  # 急跌突破下轨
        ])
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_bollinger_signals(cache, period=20, std_dev=2.0)

        long_signals = [s for s in result if s["action"] == "long"]
        assert len(long_signals) >= 1
        for sig in result:
            _assert_valid_signal(sig)
            assert sig["signal_source"] == "bollinger"

    def test_upper_band_touch_generates_short(self):
        """触及上轨 -> 生成 short 信号"""
        from cryptobot.backtest.baselines import generate_bollinger_signals

        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        closes = np.concatenate([
            np.full(60, 50000.0),
            np.linspace(50000, 55000, 40),  # 急涨突破上轨
        ])
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)

        cache = {"BTCUSDT": df}
        result = generate_bollinger_signals(cache, period=20, std_dev=2.0)

        short_signals = [s for s in result if s["action"] == "short"]
        assert len(short_signals) >= 1

    def test_empty_klines(self):
        """空K线 -> 返回空列表"""
        from cryptobot.backtest.baselines import generate_bollinger_signals

        assert generate_bollinger_signals({}) == []
        assert generate_bollinger_signals({"BTCUSDT": pd.DataFrame()}) == []

    def test_insufficient_data(self):
        """数据不足 -> 返回空"""
        from cryptobot.backtest.baselines import generate_bollinger_signals

        cache = _make_cache(n=15)  # 少于 period + 10 = 30
        assert generate_bollinger_signals(cache) == []


# ─── 格式兼容性 ──────────────────────────────────────────────────────────────

class TestSignalFormat:
    def test_all_strategies_produce_valid_signals(self):
        """所有策略生成的信号都包含必需字段"""
        from cryptobot.backtest.baselines import (
            generate_bollinger_signals,
            generate_ma_cross_signals,
            generate_random_signals,
            generate_rsi_signals,
        )

        # 用有趋势的数据确保各策略都能产生信号
        n = 200
        dates = pd.date_range("2026-01-01", periods=n, freq="1h")
        # V 形走势: 先跌后涨
        closes = np.concatenate([
            np.linspace(55000, 42000, 100),
            np.linspace(42000, 58000, 100),
        ])
        df = pd.DataFrame({
            "open": closes * 1.001,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=dates)
        cache = {"BTCUSDT": df}

        refs = [
            {"symbol": "BTCUSDT", "action": "long", "leverage": 3},
            {"symbol": "BTCUSDT", "action": "short", "leverage": 2},
        ]

        all_signals = []
        all_signals.extend(generate_random_signals(refs, cache))
        all_signals.extend(generate_ma_cross_signals(cache))
        all_signals.extend(generate_rsi_signals(cache))
        all_signals.extend(generate_bollinger_signals(cache))

        assert len(all_signals) > 0, "至少应产生一些信号"
        for sig in all_signals:
            _assert_valid_signal(sig)
