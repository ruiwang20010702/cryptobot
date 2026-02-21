"""全局配置加载"""

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv() -> None:
    """从项目根目录加载 .env 文件（不覆盖已有环境变量）"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 去除首尾引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if not os.environ.get(key):
            os.environ[key] = value


_dotenv_loaded = False
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
FREQTRADE_DATA_DIR = PROJECT_ROOT / "user_data" / "data" / "binance" / "futures"
FREQTRADE_DATA_DIR_ALT = PROJECT_ROOT / "user_data" / "data" / "futures"


_settings_cache: dict | None = None
_settings_mtime: float = 0.0


def load_settings() -> dict:
    global _settings_cache, _settings_mtime, _dotenv_loaded
    if not _dotenv_loaded:
        _load_dotenv()
        _dotenv_loaded = True

    path = CONFIG_DIR / "settings.yaml"
    if not path.exists():
        return {}

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _settings_cache or {}

    if _settings_cache is not None and mtime == _settings_mtime:
        return _settings_cache

    try:
        settings = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        logger.error("settings.yaml 解析失败: %s", e)
        return _settings_cache or {}

    _validate_settings(settings)
    _settings_cache = settings
    _settings_mtime = mtime
    return settings


def _validate_settings(settings: dict) -> None:
    """校验关键配置项范围"""
    risk = settings.get("risk", {})
    max_lev = risk.get("max_leverage")
    if max_lev is not None and not (1 <= max_lev <= 20):
        logger.warning("risk.max_leverage=%s 超出 [1,20] 范围，已钳位", max_lev)
        risk["max_leverage"] = max(1, min(20, max_lev))


def load_pairs() -> dict:
    path = CONFIG_DIR / "pairs.yaml"
    if not path.exists():
        return {"pairs": []}
    return yaml.safe_load(path.read_text())


def get_pair_config(symbol: str) -> dict | None:
    pairs = load_pairs()
    for p in pairs.get("pairs", []):
        if p["symbol"] == symbol:
            return p
    return None


def get_all_symbols() -> list[str]:
    pairs = load_pairs()
    return [p["symbol"] for p in pairs.get("pairs", [])]


def get_correlation_groups() -> dict:
    """获取相关性分组: 优先读动态文件, 否则用 pairs.yaml 静态值"""
    import json
    import time

    dynamic_path = DATA_OUTPUT_DIR / "evolution" / "correlation.json"
    if dynamic_path.exists():
        try:
            data = json.loads(dynamic_path.read_text())
            # 7 天内有效
            if time.time() - data.get("_updated_at", 0) < 7 * 24 * 3600:
                return data.get("groups", {})
        except (json.JSONDecodeError, KeyError):
            pass

    # 回退到 pairs.yaml 静态配置
    pairs_data = load_pairs()
    return pairs_data.get("correlation_groups", {})


def get_coingecko_demo_key() -> str:
    return os.environ.get("COINGECKO_DEMO_KEY", "")


def get_cryptonews_api_key() -> str:
    return os.environ.get("CRYPTONEWS_API_KEY", "")
