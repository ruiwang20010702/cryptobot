"""Prompt 版本管理

版本化存储 prompt addon，支持创建、切换、查看版本。
持久化: data/output/evolution/prompt_versions.json
"""

import json
import logging
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_VERSIONS_DIR = DATA_OUTPUT_DIR / "evolution"
_VERSIONS_FILE = _VERSIONS_DIR / "prompt_versions.json"

_DEFAULT_DATA = {
    "active_version": "v1.0",
    "versions": {
        "v1.0": {
            "created_at": "2026-01-01T00:00:00+00:00",
            "note": "初始版本",
            "addons": {},
        },
    },
}


def _load() -> dict:
    """加载版本数据"""
    if not _VERSIONS_FILE.exists():
        return {**_DEFAULT_DATA, "versions": {**_DEFAULT_DATA["versions"]}}
    try:
        return json.loads(_VERSIONS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {**_DEFAULT_DATA, "versions": {**_DEFAULT_DATA["versions"]}}


def _save(data: dict) -> None:
    """原子写入版本数据"""
    _VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _VERSIONS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(_VERSIONS_FILE)


def get_active_version() -> str:
    """返回当前活跃版本号"""
    return _load()["active_version"]


def list_versions() -> dict:
    """列出所有版本

    Returns:
        {"active_version": str, "versions": {version: info}}
    """
    data = _load()
    return {"active_version": data["active_version"], "versions": data["versions"]}


def get_version_detail(version: str) -> dict | None:
    """获取指定版本详情"""
    data = _load()
    return data["versions"].get(version)


def create_version(note: str, addons: dict | None = None) -> str:
    """创建新版本，自动递增版本号

    Args:
        note: 版本说明
        addons: 角色 addon 映射 {"TRADER": "额外段落...", ...}

    Returns:
        新版本号 (如 "v1.1")
    """
    data = _load()
    versions = data["versions"]

    # 计算下一个版本号
    max_minor = 0
    for v in versions:
        if v.startswith("v1."):
            try:
                minor = int(v.split(".")[1])
                max_minor = max(max_minor, minor)
            except (IndexError, ValueError):
                pass
    new_version = f"v1.{max_minor + 1}"

    versions[new_version] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "addons": addons or {},
    }
    _save(data)
    logger.info("创建 prompt 版本: %s — %s", new_version, note)
    return new_version


def activate_version(version: str) -> bool:
    """切换活跃版本

    Returns:
        True 成功, False 版本不存在
    """
    data = _load()
    if version not in data["versions"]:
        return False
    data["active_version"] = version
    _save(data)
    logger.info("切换活跃 prompt 版本: %s", version)
    return True


def get_prompt_addon(role: str) -> str:
    """获取当前活跃版本中特定角色的 addon 文本

    Args:
        role: 角色名 (如 "TRADER", "RISK_MANAGER")

    Returns:
        addon 文本，无则返回空字符串
    """
    data = _load()
    active = data["active_version"]
    version_info = data["versions"].get(active, {})
    return version_info.get("addons", {}).get(role, "")
