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
        for idx, r in enumerate(data["records"]):
            if r.get("signal_id") == signal_id:
                data["records"][idx] = {**r, **updates}
                found = True
                break

        if not found:
            return False

        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(RECORDS_FILE, data)
    return True


def archive_old_records(keep_days: int = 90) -> int:
    """归档超过 keep_days 天的 closed 记录到独立文件

    Returns:
        归档的记录数
    """
    from datetime import timedelta

    with _journal_lock:
        data = _load_data()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()

        to_archive = [
            r for r in data["records"]
            if r.get("status") == "closed" and r.get("timestamp", "") < cutoff
        ]
        if not to_archive:
            return 0

        remaining = [r for r in data["records"] if r not in to_archive]

        # 写归档文件
        archive_path = JOURNAL_DIR / "archive.json"
        existing = []
        if archive_path.exists():
            try:
                existing = json.loads(archive_path.read_text())
                if not isinstance(existing, list):
                    existing = []
            except (json.JSONDecodeError, OSError):
                existing = []

        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(archive_path, existing + to_archive)

        # 更新主记录
        data = {
            "records": remaining,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write(RECORDS_FILE, data)

    logger.info("归档 %d 条旧记录 (>%d天)", len(to_archive), keep_days)
    return len(to_archive)


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
