"""统一的 Freqtrade REST API 访问"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# 缓存 Freqtrade 连接配置，避免每次请求重复读取 settings.yaml
_ft_config: dict | None = None


def _get_ft_config() -> dict:
    """获取 Freqtrade 配置（首次调用时从 settings.yaml 加载并缓存）"""
    global _ft_config
    if _ft_config is None:
        from cryptobot.config import load_settings
        settings = load_settings()
        ft_cfg = settings.get("freqtrade", {})
        api_server = ft_cfg.get("api_server", {})
        _ft_config = {
            "host": api_server.get("host", "127.0.0.1"),
            "port": api_server.get("port", 8080),
            "username": ft_cfg.get("username", "freqtrader"),
            "password": os.environ.get("FREQTRADE_PASSWORD", ft_cfg.get("password", "")),
        }
    return _ft_config


def reset_ft_config_cache() -> None:
    """重置配置缓存（热更新或测试用）"""
    global _ft_config
    _ft_config = None


def ft_api_get(endpoint: str) -> dict | list | None:
    """调用 Freqtrade REST API

    从 settings.yaml 读取配置（带缓存），连接失败返回 None。
    """
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
        return resp.json()
    except httpx.ConnectError:
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
