"""价格异动监控

轮询 Binance 价格 (30s)，检测短时间内的大幅变动:
- 5min 涨跌幅 > 3%
- 15min 涨跌幅 > 5%

触发事件时调用 dispatcher 处理。
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"


@dataclass
class PriceSnapshot:
    price: float
    timestamp: float  # time.time()


@dataclass
class PriceTracker:
    """维护单个币种的滚动价格窗口"""

    symbol: str
    # 保留 20 分钟的价格数据（30s 间隔 → 最多 40 个点）
    history: deque[PriceSnapshot] = field(default_factory=lambda: deque(maxlen=40))

    def add(self, price: float) -> None:
        self.history.append(PriceSnapshot(price=price, timestamp=time.time()))

    def change_pct(self, window_seconds: int) -> float | None:
        """计算指定时间窗口内的价格变动百分比"""
        if len(self.history) < 2:
            return None

        now = self.history[-1].timestamp
        cutoff = now - window_seconds

        # 找到窗口起始点的价格
        old_price = None
        for snap in self.history:
            if snap.timestamp >= cutoff:
                old_price = snap.price
                break

        if old_price is None or old_price == 0:
            return None

        # 数据实际跨度 < 窗口 50% 时数据不足，返回 None
        actual_span = now - self.history[0].timestamp
        if actual_span < window_seconds * 0.5:
            return None

        current = self.history[-1].price
        return (current - old_price) / old_price * 100


@dataclass
class PriceEvent:
    """价格异动事件"""

    symbol: str
    change_pct: float
    window_minutes: int
    current_price: float
    direction: str  # "crash" or "spike"
    timestamp: str


def fetch_all_prices(symbols: list[str]) -> dict[str, float]:
    """批量获取价格：优先 WS 缓存，覆盖率 < 80% 时 fallback REST"""
    prices = {}

    try:
        from cryptobot.realtime.ws_price_feed import get_all_cached_prices
        cached = get_all_cached_prices()
        for s in symbols:
            if s in cached:
                prices[s] = cached[s]
    except ImportError:
        pass

    # 缓存覆盖率足够则直接返回 (90%: 10 币种允许缺 1 个)
    if len(prices) >= len(symbols) * 0.9:
        logger.info("WS 覆盖率: %d/%d", len(prices), len(symbols))
        return prices

    # Fallback REST
    try:
        resp = httpx.get(BINANCE_TICKER_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        symbol_set = set(symbols)
        for item in data:
            s = item.get("symbol")
            if s in symbol_set and s not in prices:
                prices[s] = float(item["price"])
    except Exception as e:
        logger.warning("批量获取价格失败: %s", e)

    return prices


def check_events(
    trackers: dict[str, PriceTracker],
    thresholds: list[tuple[int, float]],
) -> list[PriceEvent]:
    """检查所有 tracker 是否触发事件

    Args:
        trackers: {symbol: PriceTracker}
        thresholds: [(window_seconds, threshold_pct), ...]
            例如 [(300, 3.0), (900, 5.0)] 表示 5min>3% 和 15min>5%

    Returns:
        触发的事件列表
    """
    events = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for symbol, tracker in trackers.items():
        if len(tracker.history) < 2:
            continue

        current_price = tracker.history[-1].price

        for window_sec, threshold in thresholds:
            change = tracker.change_pct(window_sec)
            if change is None:
                continue

            if abs(change) >= threshold:
                direction = "crash" if change < 0 else "spike"
                events.append(PriceEvent(
                    symbol=symbol,
                    change_pct=round(change, 2),
                    window_minutes=window_sec // 60,
                    current_price=current_price,
                    direction=direction,
                    timestamp=now_iso,
                ))

    return events


def run_price_monitor(*, stop_event=None) -> None:
    """主循环: 轮询价格，检测异动，调用 dispatcher

    Args:
        stop_event: threading.Event，设置后停止循环
    """
    from cryptobot.config import get_all_symbols, load_settings
    from cryptobot.events.dispatcher import handle_events

    settings = load_settings()
    event_cfg = settings.get("events", {})
    poll_interval = event_cfg.get("poll_interval_seconds", 30)

    # 阈值配置
    thresholds = [
        (300, event_cfg.get("threshold_5min_pct", 3.0)),    # 5min > 3%
        (900, event_cfg.get("threshold_15min_pct", 5.0)),   # 15min > 5%
    ]

    symbols = get_all_symbols()
    trackers = {s: PriceTracker(symbol=s) for s in symbols}

    # 冷却: 同一币种同一窗口 30 分钟内不重复触发
    cooldowns: dict[str, float] = {}  # "BTCUSDT_5" → last_trigger_time
    cooldown_seconds = event_cfg.get("cooldown_minutes", 30) * 60

    logger.info(
        "价格异动监控启动: %d 币种, 轮询 %ds, 阈值 %s",
        len(symbols), poll_interval, thresholds,
    )

    while not (stop_event and stop_event.is_set()):
        try:
            prices = fetch_all_prices(symbols)

            for symbol, price in prices.items():
                trackers[symbol].add(price)

            events = check_events(trackers, thresholds)

            # 过滤冷却中的事件
            now = time.time()
            active_events = []
            for event in events:
                key = f"{event.symbol}_{event.window_minutes}"
                last_trigger = cooldowns.get(key, 0)
                if now - last_trigger > cooldown_seconds:
                    active_events.append(event)
                    cooldowns[key] = now

            if active_events:
                handle_events(active_events)

        except KeyboardInterrupt:
            logger.info("价格异动监控停止")
            break
        except Exception as e:
            logger.error("价格异动监控异常: %s", e, exc_info=True)

        time.sleep(poll_interval)
