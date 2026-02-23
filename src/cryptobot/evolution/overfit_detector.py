"""过拟合检测器

通过分析 prompt/strategy 修改频率与绩效变化趋势，
检测潜在过拟合行为。

数据源:
- evolution/iterations.json  -- prompt 优化记录
- evolution/strategy_rules.json -- 策略规则变更
- evolution/prompt_versions.json -- prompt 版本历史
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_EVOLUTION_DIR = DATA_OUTPUT_DIR / "evolution"


@dataclass(frozen=True)
class OverfitReport:
    """过拟合检测报告"""

    modification_frequency: dict  # 7天修改频率统计
    performance_trend: dict  # 绩效趋势 (前后对比)
    overfit_score: float  # 0-100 过拟合分数
    signals: list[str]  # 过拟合信号描述列表
    recommendation: str  # 综合建议


def _load_json_safe(path: Path) -> list | dict:
    """安全读取 JSON 文件，不存在或解析失败返回空列表"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, (list, dict)) else []
    except (json.JSONDecodeError, OSError):
        return []


def _count_recent_modifications(
    data: list | dict,
    lookback_days: int,
    date_field: str = "timestamp",
) -> int:
    """统计指定天数内的修改次数"""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    ).isoformat()
    count = 0
    items = (
        data
        if isinstance(data, list)
        else (data.values() if isinstance(data, dict) else [])
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = (
            item.get(date_field, "")
            or item.get("created_at", "")
            or ""
        )
        if ts >= cutoff:
            count += 1
    return count


def _calc_performance_trend(lookback_days: int) -> dict:
    """对比修改前后的绩效变化

    取 lookback_days 的全量和后半段，对比胜率和平均盈亏。
    """
    try:
        from cryptobot.journal.analytics import calc_performance

        full = calc_performance(lookback_days)
        half = calc_performance(lookback_days // 2)

        full_wr = full.get("win_rate", 0)
        half_wr = half.get("win_rate", 0)
        full_pnl = full.get("avg_pnl_pct", 0)
        half_pnl = half.get("avg_pnl_pct", 0)

        improved = half_wr >= full_wr and half_pnl >= full_pnl

        return {
            "full_period": {
                "win_rate": full_wr,
                "avg_pnl_pct": full_pnl,
                "closed": full.get("closed", 0),
            },
            "recent_half": {
                "win_rate": half_wr,
                "avg_pnl_pct": half_pnl,
                "closed": half.get("closed", 0),
            },
            "improved": improved,
        }
    except Exception as e:
        logger.warning("绩效趋势计算失败: %s", e)
        return {
            "full_period": {},
            "recent_half": {},
            "improved": True,
        }


def _calc_frequency_score(total_mods: int) -> tuple[float, list[str]]:
    """修改频率评分 (0-50)"""
    signals: list[str] = []
    if total_mods >= 5:
        signals.append(f"7天内{total_mods}次修改，频率极高")
        return 50, signals
    if total_mods >= 3:
        signals.append(f"7天内{total_mods}次修改，频率偏高")
        return 30, signals
    if total_mods >= 1:
        return 10, signals
    return 0, signals


def _calc_perf_score(
    improved: bool, total_mods: int,
) -> tuple[float, list[str]]:
    """绩效趋势评分 (0-30)"""
    if improved:
        return 0, []
    if total_mods >= 3:
        return 30, ["高频修改但绩效未改善，强过拟合信号"]
    return 15, ["绩效趋势下降"]


def _calc_rule_score(rule_count: int) -> tuple[float, list[str]]:
    """策略规则频繁变动评分 (0-20)"""
    if rule_count >= 3:
        return 20, [f"策略规则7天内变更{rule_count}次"]
    if rule_count >= 2:
        return 10, []
    return 0, []


def _make_recommendation(score: float) -> str:
    """根据分数生成综合建议"""
    if score >= 70:
        return (
            "强烈建议暂停自动优化，"
            "回退到上一个稳定版本，增加验证周期"
        )
    if score >= 40:
        return "建议降低优化频率，增加回测验证，关注样本外表现"
    if score >= 20:
        return "轻微过拟合风险，继续观察"
    return "未检测到过拟合迹象"


def detect_overfit(lookback_days: int = 30) -> OverfitReport:
    """主检测函数

    过拟合信号:
    - 7天内 >= 3次修改 + 绩效未改善 -> 疑似过拟合
    - 频繁修改策略规则 -> 过拟合风险
    - 高频迭代但绩效持续下降 -> 强过拟合信号

    评分规则 (0-100):
    - 修改频率分 (0-50)
    - 绩效趋势分 (0-30)
    - 策略规则稳定性分 (0-20)
    """
    # 1. 加载数据
    iterations = _load_json_safe(
        _EVOLUTION_DIR / "iterations.json",
    )
    rules = _load_json_safe(
        _EVOLUTION_DIR / "strategy_rules.json",
    )
    versions = _load_json_safe(
        _EVOLUTION_DIR / "prompt_versions.json",
    )

    # 2. 统计修改频率 (7天窗口)
    iter_count = _count_recent_modifications(iterations, 7)
    rule_count = _count_recent_modifications(
        rules, 7, "created_at",
    )
    version_count = _count_recent_modifications(
        versions, 7, "created_at",
    )

    mod_freq = {
        "iterations_7d": iter_count,
        "strategy_rules_7d": rule_count,
        "prompt_versions_7d": version_count,
        "total_7d": iter_count + rule_count + version_count,
    }

    # 3. 绩效趋势
    perf_trend = _calc_performance_trend(lookback_days)

    # 4. 计算过拟合分数
    signals: list[str] = []
    score = 0.0

    # 4a. 修改频率分 (0-50)
    freq_score, freq_signals = _calc_frequency_score(
        mod_freq["total_7d"],
    )
    score += freq_score
    signals.extend(freq_signals)

    # 4b. 绩效趋势分 (0-30)
    perf_score, perf_signals = _calc_perf_score(
        perf_trend.get("improved", True),
        mod_freq["total_7d"],
    )
    score += perf_score
    signals.extend(perf_signals)

    # 4c. 策略规则频繁变动 (0-20)
    rule_score, rule_signals = _calc_rule_score(rule_count)
    score += rule_score
    signals.extend(rule_signals)

    # 4d. IS/OOS Sharpe 退化检测 (0-20)
    wf_score, wf_signals = _calc_walk_forward_degradation()
    score += wf_score
    signals.extend(wf_signals)

    # 5. 综合建议
    score = min(100.0, score)
    recommendation = _make_recommendation(score)

    return OverfitReport(
        modification_frequency=mod_freq,
        performance_trend=perf_trend,
        overfit_score=round(score, 1),
        signals=signals,
        recommendation=recommendation,
    )


def _calc_walk_forward_degradation() -> tuple[float, list[str]]:
    """从 walk_forward 结果文件检测 IS/OOS Sharpe 退化 (0-20)"""
    import glob

    signals: list[str] = []
    wf_dir = DATA_OUTPUT_DIR / "backtest"

    try:
        wf_files = sorted(glob.glob(str(wf_dir / "wf_*.json")))
        if not wf_files:
            return 0.0, signals

        # 读取最新的 walk-forward 结果
        latest = _load_json_safe(Path(wf_files[-1]))
        if not isinstance(latest, dict):
            return 0.0, signals

        folds = latest.get("folds", [])
        if not folds:
            return 0.0, signals

        is_sharpes = []
        oos_sharpes = []
        for fold in folds:
            is_s = fold.get("is_sharpe")
            oos_s = fold.get("oos_sharpe")
            if is_s is not None and oos_s is not None:
                is_sharpes.append(is_s)
                oos_sharpes.append(oos_s)

        if not is_sharpes:
            return 0.0, signals

        avg_is = sum(is_sharpes) / len(is_sharpes)
        avg_oos = sum(oos_sharpes) / len(oos_sharpes)

        # IS >> OOS 表示过拟合
        if avg_is > 0 and avg_oos < avg_is * 0.5:
            signals.append(
                f"IS/OOS Sharpe 退化严重: IS={avg_is:.2f} vs OOS={avg_oos:.2f}"
            )
            return 20.0, signals
        elif avg_is > 0 and avg_oos < avg_is * 0.7:
            signals.append(
                f"IS/OOS Sharpe 有退化: IS={avg_is:.2f} vs OOS={avg_oos:.2f}"
            )
            return 10.0, signals

        return 0.0, signals
    except Exception as e:
        logger.warning("walk_forward 退化检测失败: %s", e)
        return 0.0, signals
