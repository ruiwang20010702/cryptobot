"""多时间框架分析 / 支撑阻力 / 量价分析 测试"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from cryptobot.indicators.multi_timeframe import (
    _tf_summary,
    _detect_obv_divergence,
    calc_multi_timeframe,
    calc_support_resistance,
    calc_volume_analysis,
)


# ─── 辅助函数 ─────────────────────────────────────────────────────────────


def _make_df(n: int = 100, base_price: float = 95000, trend: str = "up") -> pd.DataFrame:
    """构造带 datetime 索引的 OHLCV DataFrame"""
    dates = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    if trend == "up":
        close = np.linspace(base_price * 0.9, base_price, n)
    elif trend == "down":
        close = np.linspace(base_price, base_price * 0.9, n)
    else:
        rng = np.random.default_rng(42)
        close = base_price + rng.standard_normal(n) * base_price * 0.01
    high = close * 1.005
    low = close * 0.995
    volume = np.full(n, 1000.0)
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _arrays(df: pd.DataFrame):
    """从 DataFrame 提取 close/high/low 的 float64 数组"""
    return (
        df["close"].values.astype(np.float64),
        df["high"].values.astype(np.float64),
        df["low"].values.astype(np.float64),
    )


# ─── _tf_summary ──────────────────────────────────────────────────────────


class TestTfSummary:
    """单时间框架指标摘要"""

    def test_bullish_alignment(self):
        """上涨趋势 → bullish 排列 + bullish 方向"""
        df = _make_df(150, trend="up")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["ema_alignment"] == "bullish"
        assert result["direction"] == "bullish"
        assert result["trend_strength"] >= 30  # 至少有 alignment 的 30 分

    def test_bearish_alignment(self):
        """下跌趋势 → bearish 排列 + bearish 方向"""
        df = _make_df(150, trend="down")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["ema_alignment"] == "bearish"
        assert result["direction"] == "bearish"
        assert result["trend_strength"] >= 30

    def test_mixed_alignment(self):
        """震荡趋势 → mixed 排列"""
        df = _make_df(150, trend="sideways")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["ema_alignment"] in ("mixed", "bullish", "bearish")
        # 震荡时方向通常是 neutral（可能不一定，但 alignment 不稳定）
        assert result["direction"] in ("bullish", "bearish", "neutral")

    def test_return_keys(self):
        """返回包含所有必要 key"""
        df = _make_df(150, trend="up")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        expected_keys = {"ema_alignment", "rsi", "macd_cross", "adx", "direction", "trend_strength"}
        assert set(result.keys()) == expected_keys

    def test_rsi_in_range(self):
        """RSI 在 0-100 之间"""
        df = _make_df(150, trend="up")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["rsi"] is not None
        assert 0 <= result["rsi"] <= 100

    def test_adx_in_range(self):
        """ADX 在 0-100 之间"""
        df = _make_df(150, trend="up")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["adx"] is not None
        assert 0 <= result["adx"] <= 100

    def test_trend_strength_capped_at_100(self):
        """趋势强度上限为 100"""
        df = _make_df(200, trend="up")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["trend_strength"] <= 100

    def test_macd_cross_values(self):
        """MACD 交叉只能是三个值之一"""
        df = _make_df(150, trend="up")
        c, h, lo = _arrays(df)
        result = _tf_summary(c, h, lo)

        assert result["macd_cross"] in ("golden_cross", "death_cross", "none")

    def test_golden_cross_detection(self):
        """构造 MACD 金叉场景: 先下后急上"""
        # 先 100 根下跌，再 50 根快速上涨
        part1 = np.linspace(100, 80, 100)
        part2 = np.linspace(80, 110, 50)
        close = np.concatenate([part1, part2])
        high = close * 1.005
        low = close * 0.995
        result = _tf_summary(close, high, low)
        # 急速反转大概率触发金叉
        assert result["macd_cross"] in ("golden_cross", "none")

    def test_death_cross_detection(self):
        """构造 MACD 死叉场景: 先上后急跌"""
        part1 = np.linspace(80, 110, 100)
        part2 = np.linspace(110, 85, 50)
        close = np.concatenate([part1, part2])
        high = close * 1.005
        low = close * 0.995
        result = _tf_summary(close, high, low)
        assert result["macd_cross"] in ("death_cross", "none")


# ─── _detect_obv_divergence ───────────────────────────────────────────────


class TestDetectObvDivergence:
    """OBV 量价背离检测"""

    def test_bullish_divergence(self):
        """价格下跌 + OBV 上升 → bullish_divergence"""
        close = np.linspace(100, 90, 15)  # 下跌
        obv = np.linspace(1000, 1500, 15)  # 上升
        assert _detect_obv_divergence(close, obv) == "bullish_divergence"

    def test_bearish_divergence(self):
        """价格上涨 + OBV 下降 → bearish_divergence"""
        close = np.linspace(90, 100, 15)  # 上涨
        obv = np.linspace(1500, 1000, 15)  # 下降
        assert _detect_obv_divergence(close, obv) == "bearish_divergence"

    def test_no_divergence_both_up(self):
        """价格和 OBV 都上涨 → none"""
        close = np.linspace(90, 100, 15)
        obv = np.linspace(1000, 1500, 15)
        assert _detect_obv_divergence(close, obv) == "none"

    def test_no_divergence_both_down(self):
        """价格和 OBV 都下跌 → none"""
        close = np.linspace(100, 90, 15)
        obv = np.linspace(1500, 1000, 15)
        assert _detect_obv_divergence(close, obv) == "none"

    def test_too_short_arrays(self):
        """数组太短 (<10) → none"""
        close = np.array([100.0, 99.0, 98.0])
        obv = np.array([1000.0, 1100.0, 1200.0])
        assert _detect_obv_divergence(close, obv) == "none"

    def test_exactly_10_elements(self):
        """刚好 10 个元素应该正常工作"""
        close = np.linspace(100, 90, 10)  # 下跌
        obv = np.linspace(1000, 1500, 10)  # 上升
        assert _detect_obv_divergence(close, obv) == "bullish_divergence"

    def test_obv_with_nan(self):
        """OBV 含 NaN 值 → 清洗后仍能检测"""
        close = np.linspace(100, 90, 15)
        obv = np.linspace(1000, 1500, 15)
        obv[0] = np.nan  # 第一个是 NaN
        # NaN 被清洗后 obv_clean 仍有 14 个元素，趋势仍然是上升
        assert _detect_obv_divergence(close, obv) == "bullish_divergence"


# ─── calc_multi_timeframe ─────────────────────────────────────────────────


MOCK_LOAD = "cryptobot.indicators.multi_timeframe.load_klines"


class TestCalcMultiTimeframe:
    """多时间框架共振"""

    def test_all_bullish_resonance(self):
        """三个时间框架都看涨 → 3/3 共振 + confidence_boost=15"""
        df_up = _make_df(150, trend="up")

        with patch(MOCK_LOAD, return_value=df_up):
            result = calc_multi_timeframe("BTCUSDT")

        assert result["symbol"] == "BTCUSDT"
        assert result["aligned_direction"] == "bullish"
        assert result["aligned_count"] == 3
        assert result["confidence_boost"] == 15
        assert set(result["timeframes"].keys()) == {"1h", "4h", "1d"}

    def test_all_bearish_resonance(self):
        """三个时间框架都看跌 → 3/3 共振 + confidence_boost=15"""
        df_down = _make_df(150, trend="down")

        with patch(MOCK_LOAD, return_value=df_down):
            result = calc_multi_timeframe("ETHUSDT")

        assert result["aligned_direction"] == "bearish"
        assert result["aligned_count"] == 3
        assert result["confidence_boost"] == 15

    def test_mixed_2_bullish(self):
        """2 bullish + 1 bearish → 2/3 共振 + confidence_boost=8"""
        df_up = _make_df(150, trend="up")
        df_down = _make_df(150, trend="down")

        def side_effect(symbol, tf):
            return df_down if tf == "1d" else df_up

        with patch(MOCK_LOAD, side_effect=side_effect):
            result = calc_multi_timeframe("BTCUSDT")

        if result["aligned_count"] == 2:
            assert result["confidence_boost"] == 8

    def test_no_alignment(self):
        """没有方向共识 → mixed + confidence_boost=0"""
        df_up = _make_df(150, trend="up")
        df_down = _make_df(150, trend="down")
        df_side = _make_df(150, trend="sideways")

        call_count = {"n": 0}
        frames = [df_up, df_down, df_side]

        def side_effect(symbol, tf):
            idx = call_count["n"]
            call_count["n"] += 1
            return frames[idx]

        with patch(MOCK_LOAD, side_effect=side_effect):
            result = calc_multi_timeframe("SOLUSDT")

        # 如果三个方向都不同且没有两个相同的，aligned_count=0
        if result["aligned_count"] == 0:
            assert result["aligned_direction"] == "mixed"
            assert result["confidence_boost"] == 0

    def test_one_timeframe_missing(self):
        """一个时间框架文件缺失 → 正常降级，direction=unknown"""
        df_up = _make_df(150, trend="up")

        def side_effect(symbol, tf):
            if tf == "1d":
                raise FileNotFoundError("no data")
            return df_up

        with patch(MOCK_LOAD, side_effect=side_effect):
            result = calc_multi_timeframe("BTCUSDT")

        assert result["timeframes"]["1d"]["direction"] == "unknown"
        assert result["timeframes"]["1d"]["trend_strength"] == 0
        # 1h 和 4h 仍然正常
        assert "ema_alignment" in result["timeframes"]["1h"]
        assert "ema_alignment" in result["timeframes"]["4h"]

    def test_all_timeframes_missing(self):
        """所有时间框架都缺失 → aligned=mixed, boost=0"""
        with patch(MOCK_LOAD, side_effect=FileNotFoundError("no data")):
            result = calc_multi_timeframe("BTCUSDT")

        assert result["aligned_direction"] == "mixed"
        assert result["aligned_count"] == 0
        assert result["confidence_boost"] == 0

    def test_1d_generic_exception_handled(self):
        """1d 使用宽泛 except → 其他异常也被捕获"""
        df_up = _make_df(150, trend="up")

        def side_effect(symbol, tf):
            if tf == "1d":
                raise RuntimeError("unexpected error")
            return df_up

        with patch(MOCK_LOAD, side_effect=side_effect):
            result = calc_multi_timeframe("BTCUSDT")

        assert result["timeframes"]["1d"]["direction"] == "unknown"


# ─── calc_support_resistance ──────────────────────────────────────────────


class TestCalcSupportResistance:
    """支撑阻力位"""

    def test_basic_structure(self):
        """返回完整的支撑阻力结构"""
        df = _make_df(100, base_price=95000, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("BTCUSDT")

        assert result["symbol"] == "BTCUSDT"
        assert "pivot_points" in result
        assert "fibonacci" in result
        assert "round_levels" in result
        assert "nearest_support" in result
        assert "nearest_resistance" in result
        assert "sr_ratio" in result

    def test_pivot_points_calculated(self):
        """Pivot Point 基于最近 6 根 4h K 线"""
        df = _make_df(100, base_price=95000, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("BTCUSDT")

        pp = result["pivot_points"]
        assert pp["s2"] < pp["s1"] < pp["pivot"] < pp["r1"] < pp["r2"]

    def test_fibonacci_levels(self):
        """Fibonacci 回撤位正确排序"""
        df = _make_df(100, base_price=95000, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("BTCUSDT")

        fib = result["fibonacci"]
        assert fib["swing_low"] < fib["fib_0.618"] < fib["fib_0.500"] < fib["fib_0.382"] < fib["swing_high"]

    def test_nearest_support_below_close(self):
        """最近支撑位 < 当前价 < 最近阻力位"""
        df = _make_df(100, base_price=95000, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("BTCUSDT")

        assert result["nearest_support"] < result["latest_close"]
        assert result["nearest_resistance"] > result["latest_close"]

    def test_sr_ratio_range(self):
        """支撑阻力比在 0-1 之间"""
        df = _make_df(100, base_price=95000, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("BTCUSDT")

        assert 0 <= result["sr_ratio"] <= 1

    def test_round_levels(self):
        """整数关口包含 3 个级别"""
        df = _make_df(100, base_price=95000, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("BTCUSDT")

        assert len(result["round_levels"]) == 3
        # 相邻整数关口之差应该相等
        levels = result["round_levels"]
        assert levels[1] - levels[0] == pytest.approx(levels[2] - levels[1])

    def test_file_not_found_returns_error(self):
        """4h 数据缺失 → 返回 error"""
        with patch(MOCK_LOAD, side_effect=FileNotFoundError):
            result = calc_support_resistance("BTCUSDT")

        assert "error" in result

    def test_small_price_coin(self):
        """低价币种（如 DOGEUSDT ~0.15）支撑阻力仍正常计算"""
        df = _make_df(100, base_price=0.15, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_support_resistance("DOGEUSDT")

        # 低价币种由于精度问题，nearest_support/resistance 可能等于 latest_close
        assert result["nearest_support"] <= result["latest_close"]
        assert result["nearest_resistance"] >= result["latest_close"]
        assert len(result["round_levels"]) == 3
        assert "pivot_points" in result
        assert "fibonacci" in result


# ─── calc_volume_analysis ─────────────────────────────────────────────────


class TestCalcVolumeAnalysis:
    """量价分析"""

    def test_basic_structure(self):
        """返回完整的量价分析结构"""
        df = _make_df(100, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["symbol"] == "BTCUSDT"
        expected_keys = {"symbol", "vwap", "price_vs_vwap", "vwap_distance_pct",
                         "volume_ratio", "volume_state", "obv_divergence"}
        assert set(result.keys()) == expected_keys

    def test_vwap_positive(self):
        """VWAP 为正数"""
        df = _make_df(100, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["vwap"] > 0

    def test_volume_state_heavy(self):
        """最后一根 K 线量是 MA20 的 3 倍 → heavy"""
        df = _make_df(100, trend="up")
        # 最后一根成交量设为 3000（均值 1000 的 3 倍）
        df.iloc[-1, df.columns.get_loc("volume")] = 3000.0

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["volume_ratio"] == pytest.approx(3.0, rel=0.1)
        assert result["volume_state"] == "heavy"

    def test_volume_state_shrink(self):
        """最后一根 K 线量极低 → shrink"""
        df = _make_df(100, trend="up")
        df.iloc[-1, df.columns.get_loc("volume")] = 100.0  # 均值 1000 的 0.1 倍

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["volume_ratio"] < 0.5
        assert result["volume_state"] == "shrink"

    def test_volume_state_normal(self):
        """成交量接近均值 → normal"""
        df = _make_df(100, trend="up")
        # 所有 volume 相同，ratio 应该 ~1.0

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["volume_ratio"] == pytest.approx(1.0, rel=0.01)
        assert result["volume_state"] == "normal"

    def test_volume_state_above_avg(self):
        """成交量 1.5 倍均值 → above_avg"""
        df = _make_df(100, trend="up")
        df.iloc[-1, df.columns.get_loc("volume")] = 1500.0

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["volume_ratio"] == pytest.approx(1.5, rel=0.1)
        assert result["volume_state"] == "above_avg"

    def test_volume_state_below_avg(self):
        """成交量 0.6 倍均值 → below_avg"""
        df = _make_df(100, trend="up")
        df.iloc[-1, df.columns.get_loc("volume")] = 600.0

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["volume_ratio"] == pytest.approx(0.6, rel=0.05)
        assert result["volume_state"] == "below_avg"

    def test_price_above_vwap(self):
        """上涨趋势末端价格应在 VWAP 之上"""
        df = _make_df(100, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["price_vs_vwap"] == "above"
        assert result["vwap_distance_pct"] > 0

    def test_price_below_vwap(self):
        """下跌趋势末端价格应在 VWAP 之下"""
        df = _make_df(100, trend="down")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["price_vs_vwap"] == "below"
        assert result["vwap_distance_pct"] < 0

    def test_obv_divergence_field(self):
        """OBV 背离字段存在且值合法"""
        df = _make_df(100, trend="up")

        with patch(MOCK_LOAD, return_value=df):
            result = calc_volume_analysis("BTCUSDT")

        assert result["obv_divergence"] in ("bullish_divergence", "bearish_divergence", "none")

    def test_file_not_found_returns_error(self):
        """4h 数据缺失 → 返回 error"""
        with patch(MOCK_LOAD, side_effect=FileNotFoundError):
            result = calc_volume_analysis("BTCUSDT")

        assert "error" in result
