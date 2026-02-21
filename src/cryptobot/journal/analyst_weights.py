"""分析师动态权重

基于历史准确率自动升降权，注入交易决策 prompt。
"""

import json
import logging

from cryptobot.config import DATA_OUTPUT_DIR
from cryptobot.journal.analytics import calc_analyst_accuracy

logger = logging.getLogger(__name__)

_WEIGHTS_DIR = DATA_OUTPUT_DIR / "evolution"
_WEIGHTS_FILE = _WEIGHTS_DIR / "weights.json"


def calc_analyst_weights(days: int = 30) -> dict:
    """基于准确率计算分析师权重等级

    Returns:
        {role: {"accuracy": float, "weight": "high"|"normal"|"low",
                "label": str, "total": int}}
    """
    accuracy_data = calc_analyst_accuracy(days)

    result = {}
    for role, stats in accuracy_data.items():
        total = stats["total"]
        acc = stats["accuracy"]

        if total >= 10 and acc >= 0.75:
            weight = "very_high"
            label = "表现卓越，高度参考"
        elif total >= 10 and acc >= 0.65:
            weight = "high"
            label = "近期表现优异，重点参考"
        elif total >= 10 and acc <= 0.35:
            weight = "very_low"
            label = "近期准确率极低，建议反向参考"
        elif total >= 10 and acc <= 0.45:
            weight = "low"
            label = "近期准确率偏低，仅供参考"
        else:
            weight = "normal"
            label = ""

        result[role] = {
            "accuracy": acc,
            "weight": weight,
            "label": label,
            "total": total,
        }

    return result


def save_weights(weights: dict | None = None) -> None:
    """持久化权重到文件"""
    if weights is None:
        weights = calc_analyst_weights()

    _WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _WEIGHTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(weights, ensure_ascii=False, indent=2))
    tmp.rename(_WEIGHTS_FILE)
    logger.info("分析师权重已保存: %s", _WEIGHTS_FILE)


def build_weights_context(days: int = 30) -> str:
    """生成可注入 TRADER prompt 的权重标注文本

    样本不足或无调整时返回空字符串。
    """
    weights = calc_analyst_weights(days)

    if not weights:
        return ""

    # 只在有 high/low 标注时注入
    annotated = {r: w for r, w in weights.items() if w["weight"] != "normal"}
    if not annotated:
        return ""

    lines = ["### 分析师权重"]
    for role, w in weights.items():
        acc_pct = round(w["accuracy"] * 100)
        arrow = ""
        suffix = ""
        if w["weight"] in ("very_high", "high"):
            arrow = " ^^" if w["weight"] == "very_high" else " ^"
            suffix = f" {w['label']}"
        elif w["weight"] in ("very_low", "low"):
            arrow = " vv" if w["weight"] == "very_low" else " v"
            suffix = f" {w['label']}"
        lines.append(f"- {role}: {acc_pct}% 准确率{arrow}{suffix}")

    lines.append("")
    return "\n".join(lines)
