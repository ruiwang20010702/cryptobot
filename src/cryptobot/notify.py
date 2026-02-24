"""Telegram 通知

未配置时所有调用静默跳过，不影响正常流程。
"""

import logging
import os

import httpx

from cryptobot.config import load_settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def _get_config() -> tuple[str, str] | None:
    """获取 Telegram 配置，返回 (bot_token, chat_id) 或 None"""
    settings = load_settings()
    tg = settings.get("telegram", {})

    if not tg.get("enabled", False):
        return None

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id", "")

    if not bot_token or not chat_id:
        return None

    return bot_token, chat_id


def send_message(text: str, parse_mode: str = "Markdown", *, retries: int = 1) -> bool:
    """发送 Telegram 消息

    Args:
        retries: 重试次数 (默认 1，CRITICAL 告警建议传 3)

    Returns:
        True 发送成功, False 未配置或失败
    """
    config = _get_config()
    if config is None:
        return False

    bot_token, chat_id = config
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    masked_token = bot_token[:4] + "***" + bot_token[-4:] if len(bot_token) > 8 else "***"

    import time as _time

    for attempt in range(max(1, retries)):
        try:
            resp = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            logger.warning(
                "Telegram 发送失败 (token=%s, 尝试 %d/%d): %d %s",
                masked_token, attempt + 1, retries, resp.status_code, resp.text[:200],
            )
        except Exception as e:
            logger.warning("Telegram 发送异常 (尝试 %d/%d): %s", attempt + 1, retries, e)
        if attempt < retries - 1:
            _time.sleep(2)
    return False


# ─── 预置消息模板 ──────────────────────────────────────────────────────────

def _format_price(price) -> str:
    """格式化价格为精确显示（去除尾部多余零）"""
    if not isinstance(price, (int, float)) or price <= 0:
        return "?"
    if price >= 1000:
        return f"{price:.2f}".rstrip("0").rstrip(".")
    if price >= 1:
        return f"{price:.4f}".rstrip("0").rstrip(".")
    return f"{price:.6f}".rstrip("0").rstrip(".")


def notify_new_signal(signal: dict) -> bool:
    """通知: 新信号写入（价格模糊化，防止信息泄露）"""
    action = signal.get("action", "?").upper()
    symbol = signal.get("symbol", "?")
    leverage = signal.get("leverage", "?")
    confidence = signal.get("confidence", "?")
    entry = signal.get("entry_price_range", [])
    size = signal.get("position_size_usdt", "?")

    entry_str = (
        f"{_format_price(entry[0])} - {_format_price(entry[1])}"
        if entry and len(entry) == 2 else "?"
    )
    sl_str = _format_price(signal.get("stop_loss"))

    text = (
        f"📊 *新信号*\n\n"
        f"*{action} {symbol}* {leverage}x\n"
        f"入场: {entry_str}\n"
        f"止损: {sl_str}\n"
        f"仓位: {size} USDT\n"
        f"置信度: {confidence}"
    )
    return send_message(text)


def notify_risk_rejected(symbol: str, reason: str) -> bool:
    """通知: 风控拒绝"""
    text = f"🚫 *风控拒绝*\n\n{symbol}\n原因: {reason}"
    return send_message(text)


def notify_stop_loss_adjusted(symbol: str, old_sl: float | None, new_sl: float) -> bool:
    """通知: 止损调整"""
    old_str = f"{old_sl:.2f}" if old_sl else "?"
    text = f"⚠️ *止损调整*\n\n{symbol}\n{old_str} → {new_sl:.2f}"
    return send_message(text)


def notify_alert(level: str, message: str) -> bool:
    """通知: 告警"""
    icon = {"CRITICAL": "🔴", "WARNING": "🟡", "IMPORTANT": "🔵"}.get(level, "⚪")
    text = f"{icon} *{level}*\n\n{message}"
    return send_message(text, retries=3 if level == "CRITICAL" else 1)


def notify_daily_report(text: str) -> bool:
    """通知: 每日绩效日报"""
    return send_message(text, parse_mode="Markdown")


def notify_regime_change(old_regime: str, new_regime: str, confidence: int) -> bool:
    """通知: 市场状态切换"""
    text = (
        f"🔄 *市场状态切换*\n\n"
        f"{old_regime} → *{new_regime}*\n"
        f"置信度: {confidence}%"
    )
    return send_message(text)


def notify_capital_tier_change(old_tier: str, new_tier: str, balance: float) -> bool:
    """通知: 资金层级变更"""
    text = (
        f"💰 *资金层级变更*\n\n"
        f"{old_tier} → *{new_tier}*\n"
        f"当前余额: ${balance:.0f}"
    )
    return send_message(text)


def _escape_md(text: str) -> str:
    """转义 Telegram Markdown 特殊字符"""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def notify_workflow_summary(
    screened: list[str],
    decisions: list[dict],
    approved_count: int,
    regime: str,
    capital_tier: str,
    fear_greed: int | None = None,
) -> bool:
    """通知: 每轮分析摘要（不管有没有信号都推送）"""
    actions = []
    for d in decisions:
        sym = _escape_md(str(d.get("symbol", "?")))
        act = _escape_md(str(d.get("action", "?")))
        conf = d.get("confidence", "?")
        actions.append(f"  {sym}: {act} (置信度{conf})")
    actions_str = "\n".join(actions) if actions else "  无"

    fg_str = f"恐惧贪婪: {fear_greed}" if fear_greed is not None else ""
    regime_safe = _escape_md(regime)
    tier_safe = _escape_md(capital_tier)
    screened_safe = ", ".join(_escape_md(s) for s in screened) if screened else "无"

    text = (
        f"📋 *分析摘要*\n\n"
        f"市场: {regime_safe} | {fg_str}\n"
        f"资金层级: {tier_safe}\n"
        f"筛选: {screened_safe}\n\n"
        f"*决策:*\n{actions_str}\n\n"
        f"信号产出: {approved_count} 个"
    )
    return send_message(text)


def notify_trade_closed(record: dict) -> bool:
    """通知: 交易关闭（journal_sync 同步后触发）"""
    symbol = record.get("symbol", "?")
    action = record.get("action", "?").upper()
    pnl = record.get("pnl_pct", 0)
    icon = "\u2705" if pnl >= 0 else "\u274c"
    pnl_str = f"{pnl:+.2f}%"
    entry = _format_price(record.get("entry_price"))
    exit_p = _format_price(record.get("close_price"))
    leverage = record.get("leverage", "?")
    text = (
        f"{icon} *交易关闭*\n\n"
        f"*{action} {symbol}* {leverage}x\n"
        f"入场: {entry} \u2192 出场: {exit_p}\n"
        f"盈亏: {pnl_str}"
    )
    return send_message(text)


def notify_signal_expired(symbol: str, action: str) -> bool:
    """通知: 信号过期未入场"""
    text = f"\u23f0 *信号过期*\n\n{action.upper()} {symbol}\n未在有效期内入场，已清除"
    return send_message(text)


def notify_signal_activated(symbol: str, action: str, entry_price: float) -> bool:
    """通知: 信号入场激活"""
    text = (
        f"\U0001f7e2 *信号激活*\n\n"
        f"{action.upper()} {symbol}\n"
        f"入场价: {_format_price(entry_price)}"
    )
    return send_message(text)


def notify_workflow_error(error_count: int, errors: list[str]) -> bool:
    """通知: 工作流异常"""
    detail = "\n".join(f"• {e[:100]}" for e in errors[:5])
    text = f"⚠️ *工作流异常*\n\n错误数: {error_count}\n\n{detail}"
    return send_message(text)
