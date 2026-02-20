"""简单文件缓存"""

import json
import time
from pathlib import Path

from cryptobot.config import DATA_OUTPUT_DIR


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
