"""Telegram 命令 Bot — 长轮询

daemon 线程，随主进程退出。仅响应配置的 chat_id，防止他人操控。
"""

import logging
import threading
import time

import httpx

from cryptobot.notify import TELEGRAM_API, _get_config

logger = logging.getLogger(__name__)


def start_bot_thread() -> threading.Thread | None:
    """启动 bot 长轮询线程，返回 Thread 或 None（未配置时）"""
    config = _get_config()
    if config is None:
        logger.info("Telegram 未配置，跳过 bot 启动")
        return None

    bot_token, chat_id = config
    t = threading.Thread(
        target=_poll_loop,
        args=(bot_token, chat_id),
        daemon=True,
        name="telegram-bot",
    )
    t.start()
    logger.info("Telegram bot 长轮询线程已启动")
    return t


def _poll_loop(bot_token: str, chat_id: str) -> None:
    """长轮询主循环"""
    from cryptobot.telegram.handlers import handle_command

    offset = 0
    url = f"{TELEGRAM_API}/bot{bot_token}/getUpdates"

    while True:
        try:
            resp = httpx.get(
                url, params={"offset": offset, "timeout": 30}, timeout=35,
            )
            if resp.status_code != 200:
                time.sleep(5)
                continue

            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                # 安全检查：仅响应配置的 chat_id
                if str(msg.get("chat", {}).get("id")) != chat_id:
                    continue
                text = msg.get("text", "")
                if text.startswith("/"):
                    reply = handle_command(text)
                    _send_reply(bot_token, chat_id, reply)
        except Exception:
            logger.warning("Telegram bot 轮询异常", exc_info=True)
            time.sleep(5)


def _send_reply(bot_token: str, chat_id: str, text: str) -> None:
    """发送回复消息"""
    try:
        httpx.post(
            f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        logger.warning("Telegram bot 回复发送失败", exc_info=True)
