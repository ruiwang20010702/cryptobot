"""统一的 Freqtrade REST API 访问"""

import logging

import httpx

from cryptobot.config import load_settings

logger = logging.getLogger(__name__)


def ft_api_get(endpoint: str) -> dict | list | None:
    """调用 Freqtrade REST API

    从 settings.yaml 读取配置，连接失败返回 None。
    """
    settings = load_settings()
    ft_cfg = settings.get("freqtrade", {})
    api_server = ft_cfg.get("api_server", {})
    host = api_server.get("host", "127.0.0.1")
    port = api_server.get("port", 8080)
    base_url = f"http://{host}:{port}/api/v1"
    import os
    username = ft_cfg.get("username", "freqtrader")
    password = os.environ.get("FREQTRADE_PASSWORD", ft_cfg.get("password", ""))

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
