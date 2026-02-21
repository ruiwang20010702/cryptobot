"""实时入场监控

轮询 Binance 价格，等待 pending 信号价格进入 entry_price_range 后，
经 5m 指标确认再写入 signal.json 供 Freqtrade 执行。
"""

import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
import numpy as np
import pandas as pd

from cryptobot.config import (
    load_settings,
    FREQTRADE_DATA_DIR,
    FREQTRADE_DATA_DIR_ALT,
)
from cryptobot.signal.bridge import (
    read_pending_signals,
    remove_pending_signal,
    write_signal,
)

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"


def _fetch_price(symbol: str) -> float | None:
    """获取最新价格：优先 WS 缓存，fallback REST"""
    try:
        from cryptobot.realtime.ws_price_feed import get_cached_price
        cached = get_cached_price(symbol)
        if cached is not None:
            return cached
    except ImportError:
        pass

    try:
        resp = httpx.get(
            BINANCE_TICKER_URL,
            params={"symbol": symbol},
            timeout=5,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        logger.warning("获取 %s 价格失败: %s", symbol, e)
        return None


def _check_entry(signal: dict, price: float, tolerance_pct: float = 0.1) -> bool:
    """判断当前价格是否在 entry_price_range 内（含容忍度）"""
    entry_range = signal.get("entry_price_range")
    if not entry_range or len(entry_range) != 2:
        # 无入场区间，直接通过
        return True

    low, high = entry_range
    if low is None or high is None:
        return True

    # 容忍度扩展
    margin = (high - low) * tolerance_pct / 100 if tolerance_pct else 0
    return (low - margin) <= price <= (high + margin)


def _load_5m_indicators(symbol: str) -> dict | None:
    """读取 Freqtrade 5m feather 数据，计算 RSI14 + EMA7/EMA25"""
    try:
        import talib
    except ImportError:
        logger.warning("TA-Lib 未安装，跳过指标确认")
        return None

    base = symbol.replace("USDT", "")
    filename = f"{base}_USDT_USDT-5m-futures.feather"
    path = FREQTRADE_DATA_DIR / filename
    if not path.exists():
        path = FREQTRADE_DATA_DIR_ALT / filename
    if not path.exists():
        logger.warning("5m 数据不存在: %s", filename)
        return None

    df = pd.read_feather(path)

    # M13: 检查最后一根 K 线时效，超过 15min 视为数据过期
    if "date" in df.columns and len(df) > 0:
        last_ts = pd.Timestamp(df["date"].iloc[-1])
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize("UTC")
        age = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds()
        if age > 15 * 60:
            logger.warning("5m 数据过期 %s: 最后 K 线 %.0f 秒前", symbol, age)
            return None

    close = df["close"].values.astype(np.float64)

    if len(close) < 30:
        return None

    rsi = talib.RSI(close, timeperiod=14)
    ema7 = talib.EMA(close, timeperiod=7)
    ema25 = talib.EMA(close, timeperiod=25)

    return {
        "rsi": float(rsi[-1]) if not np.isnan(rsi[-1]) else None,
        "ema7": float(ema7[-1]) if not np.isnan(ema7[-1]) else None,
        "ema25": float(ema25[-1]) if not np.isnan(ema25[-1]) else None,
    }


def _confirm_indicators(symbol: str, action: str) -> bool:
    """5m 指标评分制确认：替代硬性 pass/fail"""
    indicators = _load_5m_indicators(symbol)
    if indicators is None:
        return True

    rsi = indicators.get("rsi")
    ema7 = indicators.get("ema7")
    ema25 = indicators.get("ema25")

    if rsi is None or ema7 is None or ema25 is None:
        return True

    score = 0

    if action == "long":
        # EMA 方向
        if ema7 > ema25:
            score += 2
        else:
            score -= 1
        # RSI
        if rsi >= 80:
            score -= 3
        elif rsi >= 70:
            score -= 1
        elif rsi < 40:
            score += 1
    elif action == "short":
        # EMA 方向
        if ema7 < ema25:
            score += 2
        else:
            score -= 1
        # RSI
        if rsi <= 20:
            score -= 3
        elif rsi <= 30:
            score -= 1
        elif rsi > 60:
            score += 1

    logger.info(
        "%s 入场评分: score=%d (RSI=%.1f, EMA7=%.2f, EMA25=%.2f)",
        symbol, score, rsi, ema7, ema25,
    )
    return score >= 0


def _promote_signal(signal: dict) -> None:
    """将 pending 信号写入 signal.json，供 Freqtrade 读取

    先写 signal.json，再删 pending。即使删除失败，
    下次 _is_already_promoted() 检查时会跳过已存在的信号。
    """
    from cryptobot.signal.bridge import read_signals

    # 去重: 检查 signal.json 中是否已有该符号
    existing = read_signals(filter_expired=False)
    for s in existing:
        if s["symbol"] == signal["symbol"]:
            logger.info("信号已存在于 signal.json, 跳过 promote: %s", signal["symbol"])
            remove_pending_signal(signal["symbol"])
            return

    write_signal(signal)
    remove_pending_signal(signal["symbol"])

    # 更新交易日志: pending → active
    try:
        from cryptobot.journal.storage import find_active_record_for_symbol, update_record
        record = find_active_record_for_symbol(signal["symbol"])
        if record:
            update_record(record.signal_id, status="active")
    except Exception as e:
        logger.warning("更新交易日志失败: %s", e)


def run_monitor(*, stop_event=None) -> None:
    """主循环：每 N 秒轮询，检查所有 pending 信号

    Args:
        stop_event: threading.Event，设置后停止循环
    """
    settings = load_settings()
    rt_cfg = settings.get("realtime", {})
    poll_interval = rt_cfg.get("poll_interval_seconds", 10)
    tolerance_pct = rt_cfg.get("price_tolerance_pct", 0.1)
    require_confirm = rt_cfg.get("require_indicator_confirm", True)
    max_wait = rt_cfg.get("max_wait_minutes", 120)

    logger.info(
        "实时监控启动: 轮询=%ds, 容忍度=%.1f%%, 指标确认=%s, 最大等待=%dmin",
        poll_interval, tolerance_pct, require_confirm, max_wait,
    )

    while not (stop_event and stop_event.is_set()):
        try:
            pending = read_pending_signals(filter_expired=False)
            if not pending:
                time.sleep(poll_interval)
                continue

            now = datetime.now(timezone.utc)

            for signal in pending:
                symbol = signal["symbol"]

                # 1. 过期检查 (expires_at 或 max_wait)
                expires_at = datetime.fromisoformat(signal["expires_at"])
                created_at = datetime.fromisoformat(signal["timestamp"])
                wait_deadline = created_at + timedelta(minutes=max_wait)
                if now > expires_at or now > wait_deadline:
                    logger.info("信号过期: %s", symbol)
                    remove_pending_signal(symbol)
                    continue

                # 2. 获取当前价
                price = _fetch_price(symbol)
                if price is None:
                    continue

                # 3. 价格在入场区间内？
                if not _check_entry(signal, price, tolerance_pct):
                    entry_range = signal.get("entry_price_range", [])
                    logger.debug(
                        "%s 价格 %.2f 不在入场区间 %s", symbol, price, entry_range
                    )
                    continue

                # 4. 5m 指标确认
                if require_confirm:
                    if not _confirm_indicators(symbol, signal.get("action", "")):
                        continue

                # 5. 写入 signal.json
                _promote_signal(signal)
                logger.info("信号激活: %s @ %.2f", symbol, price)

        except KeyboardInterrupt:
            logger.info("监控停止")
            break
        except Exception as e:
            logger.error("监控循环异常: %s", e, exc_info=True)

        time.sleep(poll_interval)
