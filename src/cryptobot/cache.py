"""简单文件缓存"""

import json
import logging
import time
from pathlib import Path

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)


def _cache_path(category: str, key: str) -> Path:
    d = DATA_OUTPUT_DIR / category
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def get_cache(category: str, key: str, ttl: int = 900) -> dict | None:
    """读取缓存，ttl 秒内有效"""
    path = _cache_path(category, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("_cached_at", 0) < ttl:
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def set_cache(category: str, key: str, data: dict) -> None:
    """写入缓存"""
    path = _cache_path(category, key)
    data["_cached_at"] = time.time()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(path)


# 缓存子目录列表（与数据源模块对应）
_CACHE_SUBDIRS = [
    ".cache", "stablecoin", "exchange_reserve", "orderbook",
    "coinglass", "dxy", "defi_tvl", "whale",
]


def cleanup_stale(max_age_hours: int = 72) -> int:
    """清理超龄缓存文件

    Args:
        max_age_hours: 缓存最大保留时长 (小时)

    Returns:
        删除的文件数
    """
    cutoff = time.time() - max_age_hours * 3600
    removed = 0

    for subdir in _CACHE_SUBDIRS:
        cache_dir = DATA_OUTPUT_DIR / subdir
        if not cache_dir.is_dir():
            continue
        for f in cache_dir.iterdir():
            if not f.is_file() or not f.suffix == ".json":
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass

    if removed:
        logger.info("缓存清理: 删除 %d 个超龄文件 (>%dh)", removed, max_age_hours)
    return removed
