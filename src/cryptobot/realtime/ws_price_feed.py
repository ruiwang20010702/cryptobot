"""Binance WebSocket 价格推送

连接 Binance Futures miniTicker 流，维护 price_cache 供 monitor/price_monitor 使用。
自动重连，断线 5s 后重试。
"""

import json
import logging
import random
import threading
import time

logger = logging.getLogger(__name__)

# 线程安全的价格缓存: {symbol: (price, timestamp)}
_lock = threading.Lock()
price_cache: dict[str, tuple[float, float]] = {}


def get_cached_price(symbol: str, max_age_seconds: float = 30) -> float | None:
    """从缓存获取价格（线程安全），超过 max_age 返回 None"""
    with _lock:
        entry = price_cache.get(symbol)
    if entry is None:
        return None
    price, ts = entry
    if time.time() - ts > max_age_seconds:
        return None
    return price


def get_all_cached_prices() -> dict[str, float]:
    """获取所有缓存价格的快照（忽略过期检查）"""
    with _lock:
        return {s: p for s, (p, _ts) in price_cache.items()}


def _process_message(data: dict) -> None:
    """处理单条 miniTicker 消息，更新缓存"""
    symbol = data.get("s")
    price_str = data.get("c")  # close price
    if symbol and price_str:
        try:
            price = float(price_str)
            with _lock:
                price_cache[symbol] = (price, time.time())
        except (ValueError, TypeError):
            pass


def run_ws_price_feed(
    symbols: list[str],
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """主循环：连接 Binance WS miniTicker，断线自动重连

    Args:
        symbols: 要订阅的交易对列表 (e.g. ["BTCUSDT", "ETHUSDT"])
        stop_event: 停止信号
    """
    import websockets.sync.client as ws_client

    streams = "/".join(f"{s.lower()}@miniTicker" for s in symbols)
    url = f"wss://fstream.binance.com/stream?streams={streams}"

    logger.info("WS 价格推送启动: %d 币种", len(symbols))

    backoff = 5  # 初始退避秒数
    max_backoff = 60

    while not (stop_event and stop_event.is_set()):
        try:
            with ws_client.connect(url, close_timeout=5) as ws:
                logger.info("WS 已连接: %s", url[:80])
                backoff = 5  # 连接成功，重置退避
                while not (stop_event and stop_event.is_set()):
                    try:
                        raw = ws.recv(timeout=10)
                    except TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                        data = msg.get("data", msg)
                        _process_message(data)
                    except (json.JSONDecodeError, AttributeError):
                        pass

        except Exception as e:
            if stop_event and stop_event.is_set():
                break
            jitter = random.uniform(0, backoff * 0.3)
            wait = backoff + jitter
            logger.warning("WS 断线: %s, %.1fs 后重连...", e, wait)
            time.sleep(wait)
            backoff = min(backoff * 2, max_backoff)

    logger.info("WS 价格推送已停止")
