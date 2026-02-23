"""Hurst 指数计算 — R/S (Rescaled Range) 分析法

H > 0.55  -> 趋势持续 (persistent)
0.45 < H < 0.55 -> 随机游走
H < 0.45  -> 均值回归 (anti-persistent)
"""

import math

import numpy as np

# 数据不足时的默认值 (随机游走)
_DEFAULT_HURST = 0.5


def calc_hurst_exponent(prices: list[float], max_lag: int = 20) -> float:
    """R/S 分析法计算 Hurst 指数

    Args:
        prices: 收盘价序列 (至少 max_lag*2 个点)
        max_lag: 最大滞后窗口，默认 20

    Returns:
        Hurst 指数 (0~1)，数据不足返回 0.5
    """
    if len(prices) < max_lag * 2:
        return _DEFAULT_HURST

    arr = np.array(prices, dtype=np.float64)

    # 安全检查: 价格必须 > 0 才能取对数
    if np.any(arr <= 0):
        return _DEFAULT_HURST

    # 转 log returns
    log_returns = np.diff(np.log(arr))
    if len(log_returns) < max_lag:
        return _DEFAULT_HURST

    lags = range(2, max_lag + 1)
    rs_values = []

    for lag in lags:
        rs = _calc_rs_for_lag(log_returns, lag)
        if rs is None or rs <= 0:
            continue
        rs_values.append((lag, rs))

    if len(rs_values) < 3:
        return _DEFAULT_HURST

    # log-log 线性回归: log(R/S) = H * log(lag) + c
    log_lags = np.array([math.log(lag) for lag, _ in rs_values])
    log_rs = np.array([math.log(rs) for _, rs in rs_values])

    # numpy polyfit 求斜率
    coeffs = np.polyfit(log_lags, log_rs, 1)
    h = float(coeffs[0])

    # 限制在合理范围
    return max(0.0, min(1.0, h))


def _calc_rs_for_lag(log_returns: np.ndarray, lag: int) -> float | None:
    """计算指定 lag 下的平均 R/S 值

    将序列切分为 lag 长度的子段，每段计算 R/S，取均值。
    """
    n = len(log_returns)
    num_segments = n // lag
    if num_segments < 1:
        return None

    rs_list: list[float] = []

    for i in range(num_segments):
        segment = log_returns[i * lag : (i + 1) * lag]

        mean_seg = np.mean(segment)
        deviations = segment - mean_seg

        # 累积偏差
        cumulative = np.cumsum(deviations)
        r = float(np.max(cumulative) - np.min(cumulative))

        # 标准差
        s = float(np.std(segment, ddof=0))
        if s < 1e-12:
            continue

        rs_list.append(r / s)

    if not rs_list:
        return None
    return float(np.mean(rs_list))


def classify_hurst(h: float) -> tuple[str, float]:
    """根据 Hurst 指数分类并返回置信度

    Returns:
        (regime_hint, confidence)
        - trending: H > 0.55, conf = min((H - 0.5) * 4, 1.0)
        - ranging:  H < 0.45, conf = min((0.5 - H) * 4, 1.0)
        - random:   else,     conf = 0.5
    """
    if h > 0.55:
        conf = min((h - 0.5) * 4, 1.0)
        return ("trending", round(conf, 3))
    if h < 0.45:
        conf = min((0.5 - h) * 4, 1.0)
        return ("ranging", round(conf, 3))
    return ("random", 0.5)
