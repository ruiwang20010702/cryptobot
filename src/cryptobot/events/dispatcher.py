"""事件分发器

处理价格异动事件:
- crash: 急跌 → 紧急复审持仓 + 通知
- spike: 急涨 → 通知（可能的入场机会）
"""

import logging

from cryptobot.events.price_monitor import PriceEvent

logger = logging.getLogger(__name__)


def handle_events(events: list[PriceEvent]) -> None:
    """分发处理事件列表"""
    for event in events:
        logger.warning(
            "价格异动: %s %s %.1f%% (%dmin), 价格 %.2f",
            event.symbol, event.direction, event.change_pct,
            event.window_minutes, event.current_price,
        )

        _notify_event(event)

        if event.direction == "crash":
            _handle_crash(event)
        else:
            _handle_spike(event)


def _notify_event(event: PriceEvent) -> None:
    """发送 Telegram 通知"""
    from cryptobot.notify import notify_alert

    arrow = "📉" if event.direction == "crash" else "📈"
    level = "CRITICAL" if abs(event.change_pct) >= 5 else "WARNING"
    message = (
        f"{arrow} {event.symbol} {event.change_pct:+.1f}% "
        f"({event.window_minutes}min)\n"
        f"当前价格: {event.current_price:.2f}"
    )
    notify_alert(level, message)


def _handle_crash(event: PriceEvent) -> None:
    """价格急跌: 检查是否有同方向持仓，触发紧急复审"""
    from cryptobot.freqtrade_api import ft_api_get

    positions = ft_api_get("/status") or []
    if not positions:
        return

    # 找到受影响的持仓（该币种的多头 或 急涨时的空头）
    affected = []
    for p in positions:
        pair = p.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        if symbol != event.symbol:
            continue
        # 急跌影响多头
        if not p.get("is_short"):
            affected.append(p)

    if not affected:
        logger.info("%s 急跌但无相关多头持仓", event.symbol)
        return

    logger.warning("%s 急跌，触发 %d 个持仓紧急复审", event.symbol, len(affected))
    _emergency_re_review(event.symbol, affected)


def _handle_spike(event: PriceEvent) -> None:
    """价格急涨: 检查是否有空头持仓"""
    from cryptobot.freqtrade_api import ft_api_get

    positions = ft_api_get("/status") or []
    if not positions:
        return

    affected = []
    for p in positions:
        pair = p.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        if symbol != event.symbol:
            continue
        # 急涨影响空头
        if p.get("is_short"):
            affected.append(p)

    if not affected:
        logger.info("%s 急涨但无相关空头持仓", event.symbol)
        return

    logger.warning("%s 急涨，触发 %d 个空头持仓紧急复审", event.symbol, len(affected))
    _emergency_re_review(event.symbol, affected)


def _emergency_re_review(symbol: str, positions: list[dict]) -> None:
    """紧急复审: 只对受影响币种运行快速复审"""
    try:
        from cryptobot.workflow.graph import collect_data_for_symbols, re_review
        from cryptobot.signal.bridge import update_signal_field
        from cryptobot.notify import notify_stop_loss_adjusted

        state = collect_data_for_symbols([symbol])
        suggestions = re_review(positions, state)

        for s in suggestions:
            if s["decision"] == "adjust_stop_loss" and s.get("new_stop_loss"):
                updated = update_signal_field(
                    s["symbol"], "stop_loss", s["new_stop_loss"],
                )
                if updated:
                    logger.info(
                        "紧急复审 → 更新 %s 止损 → %s",
                        s["symbol"], s["new_stop_loss"],
                    )
                    notify_stop_loss_adjusted(
                        s["symbol"], 0, s["new_stop_loss"],
                    )
            elif s["decision"] == "close_position":
                logger.warning(
                    "紧急复审 → 建议平仓 %s: %s", s["symbol"], s["reasoning"],
                )
                from cryptobot.notify import notify_alert
                notify_alert(
                    "CRITICAL",
                    f"紧急复审建议平仓 {s['symbol']}: {s['reasoning'][:200]}",
                )

    except Exception as e:
        logger.error("紧急复审失败 %s: %s", symbol, e, exc_info=True)
