"""统一的 Freqtrade REST API 访问"""

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)

# 缓存 Freqtrade 连接配置，避免每次请求重复读取 settings.yaml
_ft_config: dict | None = None
_ft_config_time: float = 0.0
_ft_config_lock = threading.Lock()
_FT_CONFIG_TTL = 300  # 5 分钟

# 连续连接失败计数 (用于 Telegram 告警)
_connect_fail_count = 0
_CONNECT_FAIL_ALERT_THRESHOLD = 3


def _get_ft_config() -> dict:
    """获取 Freqtrade 配置（带缓存 + TTL 300s）"""
    global _ft_config, _ft_config_time
    with _ft_config_lock:
        now = time.time()
        if _ft_config is not None and (now - _ft_config_time) < _FT_CONFIG_TTL:
            return _ft_config

        from cryptobot.config import load_settings
        settings = load_settings()
        ft_cfg = settings.get("freqtrade", {})
        api_server = ft_cfg.get("api_server", {})

        host = api_server.get("host", "127.0.0.1")
        password = os.environ.get("FREQTRADE_PASSWORD", "")
        if not password:
            logger.warning("FREQTRADE_PASSWORD 环境变量未设置")

        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning("Freqtrade API 非本地连接 (%s)，建议使用 HTTPS", host)

        _ft_config = {
            "host": host,
            "port": api_server.get("port", 8080),
            "username": ft_cfg.get("username", "freqtrader"),
            "password": password,
        }
        _ft_config_time = now
    return _ft_config


def reset_ft_config_cache() -> None:
    """重置配置缓存（热更新或测试用）"""
    global _ft_config, _ft_config_time
    with _ft_config_lock:
        _ft_config = None
        _ft_config_time = 0.0


def ft_api_get(endpoint: str) -> dict | list | None:
    """调用 Freqtrade REST API

    从 settings.yaml 读取配置（带缓存），连接失败返回 None。
    """
    global _connect_fail_count
    cfg = _get_ft_config()
    base_url = f"http://{cfg['host']}:{cfg['port']}/api/v1"
    username = cfg["username"]
    password = cfg["password"]

    try:
        resp = httpx.get(
            f"{base_url}{endpoint}",
            auth=(username, password),
            timeout=10,
        )
        resp.raise_for_status()
        _connect_fail_count = 0
        return resp.json()
    except httpx.ConnectError as e:
        logger.warning("Freqtrade 连接失败 %s: %s", endpoint, e)
        _connect_fail_count += 1
        if _connect_fail_count >= _CONNECT_FAIL_ALERT_THRESHOLD:
            _send_connect_failure_alert(_connect_fail_count)
        return None
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        body = e.response.text[:200]
        if status in (401, 403):
            logger.error("Freqtrade 认证失败 %s: %d %s", endpoint, status, body)
        else:
            logger.warning("Freqtrade API HTTP 错误 %s: %d %s", endpoint, status, body)
        return None
    except Exception as e:
        logger.warning("Freqtrade API 调用失败 %s: %s", endpoint, e)
        return None


def _send_connect_failure_alert(count: int) -> None:
    """连续连接失败时发送 Telegram 告警"""
    try:
        from cryptobot.notify import send_telegram
        send_telegram(f"Freqtrade 连续 {count} 次连接失败，请检查服务状态")
    except Exception:
        logger.debug("Telegram 告警发送失败", exc_info=True)
