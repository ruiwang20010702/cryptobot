"""交易记录 JSON 文件存储

文件: data/output/journal/records.json
"""

import json
import logging
import threading
from datetime import datetime, timezone

from cryptobot.config import PROJECT_ROOT
from cryptobot.journal.models import SignalRecord

logger = logging.getLogger(__name__)

_journal_lock = threading.Lock()

JOURNAL_DIR = PROJECT_ROOT / "data" / "output" / "journal"
RECORDS_FILE = JOURNAL_DIR / "records.json"


def _atomic_write(path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(path)


def _load_data() -> dict:
    if not RECORDS_FILE.exists():
        return {"records": [], "last_updated": None}
    try:
        return json.loads(RECORDS_FILE.read_text())
    except json.JSONDecodeError:
        return {"records": [], "last_updated": None}


def save_record(record: SignalRecord) -> SignalRecord:
    """保存新记录（替换同 signal_id 的旧记录）"""
    with _journal_lock:
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        data = _load_data()

        # 替换同 ID
        data["records"] = [r for r in data["records"] if r.get("signal_id") != record.signal_id]
        data["records"].append(record.to_dict())
        data["last_updated"] = datetime.now(timezone.utc).isoformat()

        _atomic_write(RECORDS_FILE, data)
    return record


def get_record(signal_id: str) -> SignalRecord | None:
    """按 signal_id 查找"""
    data = _load_data()
    for r in data["records"]:
        if r.get("signal_id") == signal_id:
            return SignalRecord.from_dict(r)
    return None


def get_records_by_symbol(symbol: str) -> list[SignalRecord]:
    """按币种查找所有记录"""
    data = _load_data()
    return [
        SignalRecord.from_dict(r) for r in data["records"]
        if r.get("symbol") == symbol
    ]


def get_records_by_status(status: str) -> list[SignalRecord]:
    """按状态查找"""
    data = _load_data()
    return [
        SignalRecord.from_dict(r) for r in data["records"]
        if r.get("status") == status
    ]


def get_all_records() -> list[SignalRecord]:
    """获取所有记录"""
    data = _load_data()
    return [SignalRecord.from_dict(r) for r in data["records"]]


def update_record(signal_id: str, **updates) -> bool:
    """更新记录字段

    Returns:
        True 成功, False 记录不存在
    """
    with _journal_lock:
        data = _load_data()

        found = False
        for r in data["records"]:
            if r.get("signal_id") == signal_id:
                r.update(updates)
                found = True
                break

        if not found:
            return False

        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(RECORDS_FILE, data)
    return True


def find_active_record_for_symbol(symbol: str) -> SignalRecord | None:
    """查找某币种最新的 pending/active 记录"""
    data = _load_data()
    candidates = [
        r for r in data["records"]
        if r.get("symbol") == symbol and r.get("status") in ("pending", "active")
    ]
    if not candidates:
        return None
    # 按时间倒序取最新
    candidates.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return SignalRecord.from_dict(candidates[0])
