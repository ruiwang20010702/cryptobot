"""动态置信度阈值

基于历史置信度校准数据自动调整 min_confidence，减少低质量信号。
"""

import logging

logger = logging.getLogger(__name__)

# 置信度区间定义
_BUCKETS = {
    "60-70": {"min": 60, "max": 70, "midpoint": 65},
    "70-80": {"min": 70, "max": 80, "midpoint": 75},
    "80-90": {"min": 80, "max": 90, "midpoint": 85},
}

_MIN_CLOSED = 50  # 最少已平仓数量才启用动态调整
_MIN_BUCKET_SAMPLES = 15  # 每个区间最少样本


def calc_dynamic_threshold(days: int = 30) -> dict:
    """基于历史校准数据计算推荐的最低置信度

    Returns:
        {"recommended_min_confidence": int,
         "current_regime_min": int,
         "calibration_notes": [str],
         "sample_size": int}
    """
    from cryptobot.journal.analytics import calc_performance

    try:
        perf = calc_performance(days)
    except Exception as e:
        logger.warning("绩效数据获取失败: %s", e)
        return {
            "recommended_min_confidence": 60,
            "current_regime_min": 60,
            "calibration_notes": ["绩效数据不可用"],
            "sample_size": 0,
        }

    closed_count = perf.get("closed", 0)
    calibration = perf.get("confidence_calibration", {})

    if closed_count < _MIN_CLOSED:
        return {
            "recommended_min_confidence": 60,
            "current_regime_min": 60,
            "calibration_notes": [f"样本不足 ({closed_count}/{_MIN_CLOSED})，使用默认阈值"],
            "sample_size": closed_count,
        }

    notes = []
    adjustment = 0  # 基于 60 的调整值

    for bucket_name, bucket_def in _BUCKETS.items():
        cal_data = calibration.get(bucket_name, {})
        count = cal_data.get("count", 0)
        actual_wr = cal_data.get("actual_win_rate")

        if count < _MIN_BUCKET_SAMPLES or actual_wr is None:
            continue

        midpoint = bucket_def["midpoint"]
        expected_wr = midpoint / 100  # 用中位数作为期望胜率

        if actual_wr < expected_wr * 0.5:
            # 偏差>50%: 大幅调整
            notes.append(
                f"confidence {bucket_name}: 实际胜率 {actual_wr * 100:.0f}% "
                f"远低于预期 {midpoint}%, 严重偏乐观"
            )
            if bucket_def["min"] <= 70:
                adjustment += 15
        elif actual_wr < expected_wr * 0.7:
            # 偏差>30%: 中幅调整
            notes.append(
                f"confidence {bucket_name}: 实际胜率 {actual_wr * 100:.0f}% "
                f"远低于预期 {midpoint}%, 偏乐观"
            )
            if bucket_def["min"] <= 70:
                adjustment += 10
        elif actual_wr > expected_wr * 1.5:
            # 偏差>50%: 大幅放宽
            notes.append(
                f"confidence {bucket_name}: 实际胜率 {actual_wr * 100:.0f}% "
                f"远高于预期 {midpoint}%, 严重偏保守"
            )
            if bucket_def["min"] >= 70:
                adjustment -= 15
        elif actual_wr > expected_wr * 1.2:
            # 偏差>20%: 中幅放宽
            notes.append(
                f"confidence {bucket_name}: 实际胜率 {actual_wr * 100:.0f}% "
                f"高于预期 {midpoint}%, 偏保守"
            )
            if bucket_def["min"] >= 70:
                adjustment -= 10

    recommended = max(50, min(85, 60 + adjustment))

    return {
        "recommended_min_confidence": recommended,
        "current_regime_min": 60,
        "calibration_notes": notes if notes else ["校准正常，无需调整"],
        "sample_size": closed_count,
    }


def build_threshold_context(regime: dict, days: int = 30) -> str:
    """生成可注入 TRADER/RISK_MANAGER prompt 的置信度校准上下文

    样本不足时返回空字符串（不污染 prompt）。
    """
    result = calc_dynamic_threshold(days)

    if result["sample_size"] < _MIN_CLOSED:
        return ""

    # 更新 current_regime_min
    regime_min = regime.get("params", {}).get("min_confidence", 60)
    result["current_regime_min"] = regime_min

    lines = [
        "### 置信度校准",
        f"- 建议最低置信度: {result['recommended_min_confidence']} "
        f"(当前 regime 默认: {regime_min})",
    ]
    for note in result["calibration_notes"]:
        lines.append(f"- {note}")
    lines.append("")

    return "\n".join(lines)
