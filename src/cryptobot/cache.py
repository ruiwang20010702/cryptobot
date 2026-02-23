"""简单文件缓存"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

# 允许清理的缓存子目录白名单
_CACHE_SUBDIRS = frozenset({
    ".cache", "klines", "sentiment", "onchain", "orderbook", "dxy",
    "news", "crypto_news", "coinglass", "stablecoin", "exchange_reserve",
    "whale", "defi_tvl", "economic_calendar", "liquidation", "dilution",
    "options",
})


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
        cached_at = _parse_timestamp(data.get("_cached_at", 0))
        if time.time() - cached_at < ttl:
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def set_cache(category: str, key: str, data: dict) -> None:
    """写入缓存（不修改传入的 data）"""
    path = _cache_path(category, key)
    cached = {**data, "_cached_at": time.time()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cached, ensure_ascii=False, indent=2))
    tmp.rename(path)


def cleanup_stale(max_age_hours: int = 72) -> int:
    """清理超龄缓存文件（仅扫描白名单子目录）

    Args:
        max_age_hours: 缓存最大保留时长 (小时)

    Returns:
        删除的文件数
    """
    cutoff = time.time() - max_age_hours * 3600
    removed = 0

    if not DATA_OUTPUT_DIR.is_dir():
        return 0

    for subdir_name in _CACHE_SUBDIRS:
        cache_dir = DATA_OUTPUT_DIR / subdir_name
        if not cache_dir.is_dir():
            continue
        for f in cache_dir.iterdir():
            if not f.is_file() or f.suffix != ".json":
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


def _parse_timestamp(ts) -> float:
    """统一解析时间戳为 Unix epoch float

    支持: float/int (epoch), ISO 8601 字符串, datetime 对象
    """
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        return ts.timestamp()
    if isinstance(ts, str):
        ts = ts.strip()
        if not ts:
            return 0.0
        try:
            return float(ts)
        except ValueError:
            pass
        # ISO 8601 解析
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0
