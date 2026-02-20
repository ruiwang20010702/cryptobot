"""信号桥接: Agent JSON → Freqtrade 信号

职责:
1. 信号 JSON 的完整读写 + 校验
2. 信号过期清理
3. 提供给 AgentSignalStrategy 的读取接口
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from cryptobot.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SIGNAL_DIR = PROJECT_ROOT / "data" / "output" / "signals"
SIGNAL_FILE = SIGNAL_DIR / "signal.json"
PENDING_FILE = SIGNAL_DIR / "pending_signals.json"

VALID_ACTIONS = {"long", "short", "close_long", "close_short"}
MAX_LEVERAGE = 5


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
                if datetime.fromisoformat(s["expires_at"]) > now
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


def validate_signal(signal: dict) -> dict:
    """校验并补全信号字段"""
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
    if leverage < 1 or leverage > MAX_LEVERAGE:
        raise ValueError(f"杠杆 {leverage}x 超出范围 [1, {MAX_LEVERAGE}]")

    # 止损校验 (方向一致性)
    sl = signal.get("stop_loss")
    entry_low = signal.get("entry_price_range", [None, None])[0]
    if sl and entry_low and action == "long" and sl >= entry_low:
        raise ValueError(f"多单止损 {sl} 不能高于入场价 {entry_low}")
    if sl and entry_low and action == "short" and sl <= entry_low:
        raise ValueError(f"空单止损 {sl} 不能低于入场价 {entry_low}")

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
            (now + timedelta(hours=4)).isoformat(),
        ),
    }


def cleanup_expired() -> int:
    """清理过期信号，返回清理数量"""
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
        if datetime.fromisoformat(s["expires_at"]) > now
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

    if not SIGNAL_FILE.exists():
        return False

    try:
        data = json.loads(SIGNAL_FILE.read_text())
    except json.JSONDecodeError:
        return False

    found = False
    for s in data.get("signals", []):
        if s["symbol"] == symbol:
            s[field] = value
            found = True
            break

    if not found:
        return False

    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(SIGNAL_FILE, data)
    return True


# ─── Pending 信号管理 ─────────────────────────────────────────────────────

def write_pending_signal(signal: dict) -> dict:
    """写入 pending 信号（与 write_signal 逻辑相同，写不同文件）"""
    validated = validate_signal(signal)

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
                if datetime.fromisoformat(s["expires_at"]) > now
            ]
        return signals
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"读取 pending 信号失败: {e}")
        return []


def remove_pending_signal(symbol: str) -> bool:
    """移除指定币种的 pending 信号（激活或过期后调用）"""
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
