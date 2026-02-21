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


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """发送 Telegram 消息

    Returns:
        True 发送成功, False 未配置或失败
    """
    config = _get_config()
    if config is None:
        return False

    bot_token, chat_id = config
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"

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
        logger.warning("Telegram 发送失败: %d %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.warning("Telegram 发送异常: %s", e)
        return False


# ─── 预置消息模板 ──────────────────────────────────────────────────────────

def notify_new_signal(signal: dict) -> bool:
    """通知: 新信号写入"""
    action = signal.get("action", "?").upper()
    symbol = signal.get("symbol", "?")
    leverage = signal.get("leverage", "?")
    confidence = signal.get("confidence", "?")
    entry = signal.get("entry_price_range", [])
    sl = signal.get("stop_loss", "?")
    size = signal.get("position_size_usdt", "?")

    entry_str = f"{entry[0]:.2f} - {entry[1]:.2f}" if entry and len(entry) == 2 else "?"

    text = (
        f"📊 *新信号*\n\n"
        f"*{action} {symbol}* {leverage}x\n"
        f"入场: {entry_str}\n"
        f"止损: {sl}\n"
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
    return send_message(text)


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


def notify_workflow_error(error_count: int, errors: list[str]) -> bool:
    """通知: 工作流异常"""
    detail = "\n".join(f"• {e[:100]}" for e in errors[:5])
    text = f"⚠️ *工作流异常*\n\n错误数: {error_count}\n\n{detail}"
    return send_message(text)
