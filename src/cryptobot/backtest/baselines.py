"""基线信号生成器

用于 P8.3 随机基线 + P8.4 简单策略基线。
生成与 AI 信号格式一致的信号，作为 A/B 测试的对照组。
"""

import logging
import random
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import talib

logger = logging.getLogger(__name__)

# 基线信号固定置信度
_BASELINE_CONFIDENCE = 65

# 策略信号之间最小冷却间隔 (K 线根数)
_COOLDOWN_BARS = 24


def _build_signal_from_kline(
    symbol: str,
    action: str,
    kline_row: pd.Series,
    atr: float,
    leverage: int = 3,
    signal_source: str = "",
    timestamp: str = "",
) -> dict:
    """构建统一格式信号

    - entry_price_range: [close * 0.998, close * 1.002]
    - stop_loss: long -> close - 2*ATR, short -> close + 2*ATR
    - take_profit: [3*ATR, 5*ATR] 各50%
    - confidence: 65 (基线固定)
    """
    close = float(kline_row["close"])
    ts = timestamp or datetime.now(timezone.utc).isoformat()

    if action == "long":
        stop_loss = close - 2 * atr
        tp1 = close + 3 * atr
        tp2 = close + 5 * atr
    else:
        stop_loss = close + 2 * atr
        tp1 = close - 3 * atr
        tp2 = close - 5 * atr

    return {
        "symbol": symbol,
        "action": action,
        "entry_price_range": [round(close * 0.998, 6), round(close * 1.002, 6)],
        "stop_loss": round(stop_loss, 6),
        "take_profit": [
            {"price": round(tp1, 6), "ratio": 0.5},
            {"price": round(tp2, 6), "ratio": 0.5},
        ],
        "leverage": leverage,
        "confidence": _BASELINE_CONFIDENCE,
        "signal_source": signal_source,
        "timestamp": ts,
    }


def _calc_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """计算 ATR，返回 ndarray"""
    return talib.ATR(
        df["high"].values.astype(np.float64),
        df["low"].values.astype(np.float64),
        df["close"].values.astype(np.float64),
        timeperiod=period,
    )


def generate_random_signals(
    reference_signals: list[dict],
    klines_cache: dict[str, pd.DataFrame],
    seed: int = 42,
) -> list[dict]:
    """随机基线: 保持相同数量/方向/币种/杠杆分布，随机化入场时机

    对每个 reference signal:
    - 保持 symbol, action, leverage 不变
    - 在该币种K线范围内随机选择入场时间
    - 用该时间点的close作为入场价
    - SL = 2 * ATR, TP = [3*ATR, 5*ATR]
    """
    if not reference_signals:
        return []

    rng = random.Random(seed)
    signals = []

    for ref in reference_signals:
        symbol = ref.get("symbol", "")
        action = ref.get("action", "long")
        leverage = ref.get("leverage", 3)

        df = klines_cache.get(symbol)
        if df is None or len(df) < 20:
            continue

        atr_arr = _calc_atr(df)

        # 找有效 ATR 的索引范围 (跳过 NaN)
        valid_mask = ~np.isnan(atr_arr)
        valid_indices = [i for i in range(len(df)) if valid_mask[i]]
        if not valid_indices:
            continue

        idx = rng.choice(valid_indices)
        row = df.iloc[idx]
        atr_val = float(atr_arr[idx])
        ts = row.name.isoformat() if hasattr(row.name, "isoformat") else str(row.name)

        signals.append(_build_signal_from_kline(
            symbol=symbol,
            action=action,
            kline_row=row,
            atr=atr_val,
            leverage=leverage,
            signal_source="random",
            timestamp=ts,
        ))

    return signals


def generate_ma_cross_signals(
    klines_cache: dict[str, pd.DataFrame],
    fast: int = 7,
    slow: int = 25,
) -> list[dict]:
    """MA交叉策略: EMA金叉做多，死叉做空

    - 逐根K线扫描，检测 fast_ema 与 slow_ema 的交叉
    - 金叉 (fast从下穿上slow) -> long
    - 死叉 (fast从上穿下slow) -> short
    - 最少间隔 24 根K线避免频繁交叉
    """
    signals = []

    for symbol, df in klines_cache.items():
        if df is None or len(df) < slow + 10:
            continue

        close = df["close"].values.astype(np.float64)
        fast_ema = talib.EMA(close, timeperiod=fast)
        slow_ema = talib.EMA(close, timeperiod=slow)
        atr_arr = _calc_atr(df)

        last_signal_idx = -_COOLDOWN_BARS  # 允许第一根就触发

        for i in range(slow + 1, len(df)):
            if i - last_signal_idx < _COOLDOWN_BARS:
                continue

            if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]):
                continue
            if np.isnan(fast_ema[i - 1]) or np.isnan(slow_ema[i - 1]):
                continue
            if np.isnan(atr_arr[i]):
                continue

            prev_diff = fast_ema[i - 1] - slow_ema[i - 1]
            curr_diff = fast_ema[i] - slow_ema[i]

            action = None
            if prev_diff <= 0 < curr_diff:
                action = "long"
            elif prev_diff >= 0 > curr_diff:
                action = "short"

            if action is None:
                continue

            row = df.iloc[i]
            ts = (
                row.name.isoformat()
                if hasattr(row.name, "isoformat")
                else str(row.name)
            )

            signals.append(_build_signal_from_kline(
                symbol=symbol,
                action=action,
                kline_row=row,
                atr=float(atr_arr[i]),
                signal_source="ma_cross",
                timestamp=ts,
            ))
            last_signal_idx = i

    return signals


def generate_rsi_signals(
    klines_cache: dict[str, pd.DataFrame],
    oversold: int = 30,
    overbought: int = 70,
) -> list[dict]:
    """RSI策略: 超卖做多，超买做空

    - RSI < oversold -> long
    - RSI > overbought -> short
    - 每个信号后冷却 24 根K线
    """
    signals = []

    for symbol, df in klines_cache.items():
        if df is None or len(df) < 20:
            continue

        close = df["close"].values.astype(np.float64)
        rsi = talib.RSI(close, timeperiod=14)
        atr_arr = _calc_atr(df)

        last_signal_idx = -_COOLDOWN_BARS

        for i in range(14, len(df)):
            if i - last_signal_idx < _COOLDOWN_BARS:
                continue
            if np.isnan(rsi[i]) or np.isnan(atr_arr[i]):
                continue

            action = None
            if rsi[i] < oversold:
                action = "long"
            elif rsi[i] > overbought:
                action = "short"

            if action is None:
                continue

            row = df.iloc[i]
            ts = (
                row.name.isoformat()
                if hasattr(row.name, "isoformat")
                else str(row.name)
            )

            signals.append(_build_signal_from_kline(
                symbol=symbol,
                action=action,
                kline_row=row,
                atr=float(atr_arr[i]),
                signal_source="rsi",
                timestamp=ts,
            ))
            last_signal_idx = i

    return signals


def generate_bollinger_signals(
    klines_cache: dict[str, pd.DataFrame],
    period: int = 20,
    std_dev: float = 2.0,
) -> list[dict]:
    """布林通道策略: 触下轨做多，触上轨做空

    - close < lower_band -> long
    - close > upper_band -> short
    - 冷却 24 根K线
    """
    signals = []

    for symbol, df in klines_cache.items():
        if df is None or len(df) < period + 10:
            continue

        close = df["close"].values.astype(np.float64)
        upper, _middle, lower = talib.BBANDS(
            close,
            timeperiod=period,
            nbdevup=std_dev,
            nbdevdn=std_dev,
        )
        atr_arr = _calc_atr(df)

        last_signal_idx = -_COOLDOWN_BARS

        for i in range(period, len(df)):
            if i - last_signal_idx < _COOLDOWN_BARS:
                continue
            if np.isnan(upper[i]) or np.isnan(lower[i]) or np.isnan(atr_arr[i]):
                continue

            action = None
            if close[i] < lower[i]:
                action = "long"
            elif close[i] > upper[i]:
                action = "short"

            if action is None:
                continue

            row = df.iloc[i]
            ts = (
                row.name.isoformat()
                if hasattr(row.name, "isoformat")
                else str(row.name)
            )

            signals.append(_build_signal_from_kline(
                symbol=symbol,
                action=action,
                kline_row=row,
                atr=float(atr_arr[i]),
                signal_source="bollinger",
                timestamp=ts,
            ))
            last_signal_idx = i

    return signals
