"""宏观经济日历数据 (FinnHub API, 需可选 FINNHUB_API_KEY)"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

FINNHUB_API = "https://finnhub.io/api/v1"
CACHE_TTL = 3600


def _empty_result() -> dict:
    return {
        "events": [],
        "has_high_impact": False,
        "next_high_impact": None,
        "event_count": 0,
    }


def get_upcoming_events(hours: int = 24) -> dict:
    """获取未来 N 小时内的高影响力宏观经济事件

    Returns:
        {"events": [...], "has_high_impact": bool,
         "next_high_impact": {"event": str, "hours_until": float} | None,
         "event_count": int}
    """
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        logger.debug("FINNHUB_API_KEY 未设置, 跳过宏观日历")
        return _empty_result()

    cache_key = "economic_calendar"
    cached = get_cache("economic_calendar", cache_key, CACHE_TTL)
    if cached:
        return cached

    now = datetime.now(timezone.utc)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(hours=hours)).strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            f"{FINNHUB_API}/calendar/economic",
            params={"from": date_from, "to": date_to, "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("FinnHub 经济日历请求失败: %s", e)
        return _empty_result()

    raw_events = data.get("economicCalendar", [])
    if not raw_events:
        result = _empty_result()
        set_cache("economic_calendar", cache_key, result)
        return result

    # 过滤: US 相关 + 高影响
    high_events = []
    for ev in raw_events:
        if ev.get("country") != "US":
            continue
        if ev.get("impact") != "high":
            continue
        high_events.append({
            "event": ev.get("event", ""),
            "time": ev.get("time", ""),
            "impact": "high",
            "country": "US",
        })

    # 计算距最近高影响事件的时间
    next_high = None
    if high_events:
        for ev in high_events:
            try:
                ev_time = datetime.fromisoformat(ev["time"].replace("Z", "+00:00"))
                hours_until = (ev_time - now).total_seconds() / 3600
                if hours_until > 0 and (
                    next_high is None or hours_until < next_high["hours_until"]
                ):
                    next_high = {
                        "event": ev["event"],
                        "hours_until": round(hours_until, 1),
                    }
            except (ValueError, TypeError):
                continue

    result = {
        "events": high_events,
        "has_high_impact": len(high_events) > 0,
        "next_high_impact": next_high,
        "event_count": len(high_events),
    }
    set_cache("economic_calendar", cache_key, result)
    return result
