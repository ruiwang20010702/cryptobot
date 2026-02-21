"""信号桥接: Agent JSON → Freqtrade 信号

职责:
1. 信号 JSON 的完整读写 + 校验
2. 信号过期清理
3. 提供给 AgentSignalStrategy 的读取接口
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta

from cryptobot.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """确保 datetime 带 UTC 时区信息"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

_signal_lock = threading.Lock()

SIGNAL_DIR = PROJECT_ROOT / "data" / "output" / "signals"
SIGNAL_FILE = SIGNAL_DIR / "signal.json"
PENDING_FILE = SIGNAL_DIR / "pending_signals.json"

VALID_ACTIONS = {"long", "short", "close_long", "close_short"}


def _max_leverage() -> int:
    from cryptobot.config import load_settings
    return load_settings().get("risk", {}).get("max_leverage", 5)


def _atomic_write_json(path, data: dict) -> None:
    """原子写入 JSON：先写 .tmp 再 rename"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(path)


def read_signals(filter_expired: bool = True) -> list[dict]:
    """读取有效信号列表"""
    if not SIGNAL_FILE.exists():
        return []
    try:
        data = json.loads(SIGNAL_FILE.read_text())
        signals = data.get("signals", [])
        if filter_expired:
            now = datetime.now(timezone.utc)
            signals = [
                s for s in signals
                if _ensure_utc(datetime.fromisoformat(s["expires_at"])) > now
            ]
        return signals
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"读取信号失败: {e}")
        return []


def get_signal_for_pair(pair: str) -> dict | None:
    """获取指定交易对的有效信号

    pair: Freqtrade 格式 (BTC/USDT:USDT) 或 Binance 格式 (BTCUSDT)
    """
    symbol = pair.replace("/", "").replace(":USDT", "")
    for s in read_signals():
        if s["symbol"] == symbol:
            return s
    return None


def write_signal(signal: dict) -> dict:
    """写入信号 (含校验)，返回写入的信号"""
    validated = validate_signal(signal)

    with _signal_lock:
        SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
        data = {"signals": [], "last_updated": None}
        if SIGNAL_FILE.exists():
            try:
                data = json.loads(SIGNAL_FILE.read_text())
            except json.JSONDecodeError:
                pass

        # 替换同交易对旧信号
        data["signals"] = [s for s in data["signals"] if s["symbol"] != validated["symbol"]]
        data["signals"].append(validated)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()

        _atomic_write_json(SIGNAL_FILE, data)

    return validated


def validate_signal(signal: dict, *, regime: str = "") -> dict:
    """校验并补全信号字段

    Args:
        signal: 信号数据
        regime: 市场状态，用于动态调整过期时间
               trending → 6h, ranging → 2h, volatile → 1.5h, 默认 → 4h
    """
    # 必填校验
    symbol = signal.get("symbol")
    action = signal.get("action")
    if not symbol or not action:
        raise ValueError("信号必须包含 symbol 和 action")
    if action not in VALID_ACTIONS:
        raise ValueError(f"无效 action: {action}, 必须是 {VALID_ACTIONS}")

    now = datetime.now(timezone.utc)

    # 杠杆校验
    leverage = signal.get("leverage", 3)
    max_lev = _max_leverage()
    if leverage < 1 or leverage > max_lev:
        raise ValueError(f"杠杆 {leverage}x 超出范围 [1, {max_lev}]")

    # entry_price_range 有效性校验
    entry_range = signal.get("entry_price_range", [None, None])
    entry_low = entry_range[0] if entry_range and len(entry_range) >= 1 else None
    entry_high = entry_range[1] if entry_range and len(entry_range) >= 2 else None
    if entry_low is not None and entry_low <= 0:
        raise ValueError(f"入场价下限 {entry_low} 必须 > 0")
    if entry_low is not None and entry_high is not None and entry_low > entry_high:
        raise ValueError(f"入场价下限 {entry_low} 不能大于上限 {entry_high}")

    # 止损校验 (方向一致性)
    sl = signal.get("stop_loss")
    if sl is not None and entry_low is not None and action == "long" and sl >= entry_low:
        raise ValueError(f"多单止损 {sl} 不能高于入场价 {entry_low}")
    if sl is not None and entry_high is not None and action == "short" and sl <= entry_high:
        raise ValueError(f"空单止损 {sl} 不能低于入场价上限 {entry_high}")

    # regime 动态过期时间：优先使用参数，其次从 signal dict 中读取
    _REGIME_EXPIRY_HOURS = {"trending": 6, "ranging": 2, "volatile": 1.5}
    effective_regime = regime or signal.get("regime", "")
    expiry_hours = _REGIME_EXPIRY_HOURS.get(effective_regime, 4)

    # 构建完整信号
    return {
        "symbol": symbol,
        "timestamp": signal.get("timestamp", now.isoformat()),
        "action": action,
        "leverage": leverage,
        "position_size_usdt": signal.get("position_size_usdt"),
        "entry_price_range": signal.get("entry_price_range"),
        "stop_loss": sl,
        "take_profit": signal.get("take_profit", []),
        "trailing_stop_pct": signal.get("trailing_stop_pct"),
        "confidence": signal.get("confidence", 50),
        "analysis_summary": signal.get("analysis_summary", {}),
        "expires_at": signal.get(
            "expires_at",
            (now + timedelta(hours=expiry_hours)).isoformat(),
        ),
    }


def cleanup_expired() -> int:
    """清理过期信号，返回清理数量"""
    with _signal_lock:
        if not SIGNAL_FILE.exists():
            return 0

        try:
            data = json.loads(SIGNAL_FILE.read_text())
        except json.JSONDecodeError:
            return 0

        now = datetime.now(timezone.utc)
        before = len(data.get("signals", []))
        data["signals"] = [
            s for s in data.get("signals", [])
            if _ensure_utc(datetime.fromisoformat(s["expires_at"])) > now
        ]
        after = len(data["signals"])
        removed = before - after

        if removed > 0:
            data["last_updated"] = now.isoformat()
            _atomic_write_json(SIGNAL_FILE, data)

    return removed


UPDATABLE_FIELDS = {"stop_loss", "trailing_stop_pct", "take_profit", "expires_at"}


def update_signal_field(symbol: str, field: str, value) -> bool:
    """更新已有信号的单个字段（如 stop_loss）

    Returns:
        True 如果成功更新，False 如果信号不存在

    Raises:
        ValueError: 字段不在白名单中
    """
    if field not in UPDATABLE_FIELDS:
        raise ValueError(f"不允许更新字段: {field}, 允许: {UPDATABLE_FIELDS}")

    with _signal_lock:
        if not SIGNAL_FILE.exists():
            return False

        try:
            data = json.loads(SIGNAL_FILE.read_text())
        except json.JSONDecodeError:
            return False

        found = False
        new_signals = []
        for s in data.get("signals", []):
            if s["symbol"] == symbol and not found:
                new_signals.append({**s, field: value})
                found = True
            else:
                new_signals.append(s)
        data["signals"] = new_signals

        if not found:
            return False

        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(SIGNAL_FILE, data)
    return True


# ─── Pending 信号管理 ─────────────────────────────────────────────────────

def write_pending_signal(signal: dict) -> dict:
    """写入 pending 信号（与 write_signal 逻辑相同，写不同文件）"""
    validated = validate_signal(signal)

    with _signal_lock:
        SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
        data = {"signals": [], "last_updated": None}
        if PENDING_FILE.exists():
            try:
                data = json.loads(PENDING_FILE.read_text())
            except json.JSONDecodeError:
                pass

        # 替换同交易对旧信号
        data["signals"] = [s for s in data["signals"] if s["symbol"] != validated["symbol"]]
        data["signals"].append(validated)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()

        _atomic_write_json(PENDING_FILE, data)

    return validated


def read_pending_signals(filter_expired: bool = True) -> list[dict]:
    """读取 pending 信号列表"""
    if not PENDING_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_FILE.read_text())
        signals = data.get("signals", [])
        if filter_expired:
            now = datetime.now(timezone.utc)
            signals = [
                s for s in signals
                if _ensure_utc(datetime.fromisoformat(s["expires_at"])) > now
            ]
        return signals
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"读取 pending 信号失败: {e}")
        return []


def remove_pending_signal(symbol: str) -> bool:
    """移除指定币种的 pending 信号（激活或过期后调用）"""
    with _signal_lock:
        if not PENDING_FILE.exists():
            return False

        try:
            data = json.loads(PENDING_FILE.read_text())
        except json.JSONDecodeError:
            return False

        before = len(data.get("signals", []))
        data["signals"] = [s for s in data.get("signals", []) if s["symbol"] != symbol]
        after = len(data["signals"])

        if before == after:
            return False

        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(PENDING_FILE, data)
    return True
