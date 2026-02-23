"""Strategy Advisor Agent — 从历史交易绩效中学习并生成策略规则

每日自动:
1. 分析绩效数据 → 发现失败/成功模式
2. 生成策略规则（如"震荡市避免做多 DOGE"）
3. 规则以 Prompt Addon 注入 trader / risk_manager
4. 规则 14 天过期 → 评估有效性 → 续期或淘汰

持久化: data/output/evolution/strategy_rules.json
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_RULES_DIR = DATA_OUTPUT_DIR / "evolution"
_RULES_FILE = _RULES_DIR / "strategy_rules.json"

MAX_ACTIVE_RULES = 5
MAX_EXPIRED_RULES = 30
RULE_TTL_DAYS = 30
MIN_CLOSED_TRADES = 50
MAX_NEW_RULES_PER_CYCLE = 2

STRATEGY_ADVISOR_PROMPT = """你是量化交易系统的策略顾问。分析以下交易绩效数据，识别失败模式和改进机会。

分析维度:
1. 哪些币种/方向组合持续亏损？
2. 特定市场状态下是否有系统性偏差？
3. 置信度校准是否合理？
4. 杠杆使用是否过激？
5. 是否有过度交易（信号太多但质量低）？
6. 分析师准确率是否失衡？

生成具体、可执行的策略规则。每条规则必须:
- 针对具体场景（币种、方向、市场状态）
- 给出明确的行动（限制杠杆、跳过交易、降低置信度等）
- 不要过于宽泛（如"提高整体谨慎度"太模糊）

当前已有规则（避免重复）: {existing_rules}

输出 JSON 数组: [{{"rule_text": "...", "target_role": "trader|risk_manager", "rationale": "..."}}]
最多 2 条规则。如果绩效良好无需调整，返回空数组 []。"""


# ── 存储 ──────────────────────────────────────────────────────────────

def _load_rules() -> dict:
    """加载规则文件"""
    if not _RULES_FILE.exists():
        return {"active_rules": [], "expired_rules": [], "evaluation_log": []}
    try:
        data = json.loads(_RULES_FILE.read_text())
        return {
            "active_rules": data.get("active_rules", []),
            "expired_rules": data.get("expired_rules", []),
            "evaluation_log": data.get("evaluation_log", []),
        }
    except (json.JSONDecodeError, OSError):
        return {"active_rules": [], "expired_rules": [], "evaluation_log": []}


def _save_rules(data: dict) -> None:
    """原子写入规则文件"""
    _RULES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _RULES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(_RULES_FILE)


# ── Prompt Addon (供 trade/risk 节点调用) ──────────────────────────────

def get_strategy_addon(role: str) -> str:
    """获取指定角色的策略规则 addon 文本

    Args:
        role: "trader" 或 "risk_manager"

    Returns:
        Prompt addon 文本，无规则时返回空字符串
    """
    data = _load_rules()
    rules = [r for r in data["active_rules"] if r.get("target_role") == role]
    if not rules:
        return ""

    lines = ["\n\n## 策略顾问规则（基于近期绩效自动生成，14天有效期）"]
    for r in rules:
        lines.append(f"- {r['rule_text']}")
    return "\n".join(lines)


# ── 规则生命周期 ─────────────────────────────────────────────────────

def _expire_old_rules(data: dict) -> dict:
    """处理过期规则: 评估有效性后移至 expired_rules"""
    now = datetime.now(timezone.utc)
    still_active = []
    newly_expired = []

    for rule in data["active_rules"]:
        expires_at = datetime.fromisoformat(rule["expires_at"])
        if now >= expires_at:
            evaluation = _evaluate_expired_rule(rule)
            rule_with_eval = {**rule, "evaluation": evaluation}

            # 有效规则续期
            if evaluation["verdict"] == "effective":
                renewed = {
                    **rule,
                    "expires_at": (now + timedelta(days=RULE_TTL_DAYS)).isoformat(),
                    "renewed_at": now.isoformat(),
                }
                still_active.append(renewed)
                logger.info("规则续期: %s (提升 %.1f%%)", rule["id"], evaluation["improvement_pct"])
            else:
                newly_expired.append(rule_with_eval)
                logger.info(
                    "规则淘汰: %s (%s)", rule["id"], evaluation["verdict"],
                )
        else:
            still_active.append(rule)

    expired = data["expired_rules"] + newly_expired
    evaluation_log = data["evaluation_log"] + [
        {
            "rule_id": r["id"],
            "evaluated_at": now.isoformat(),
            "verdict": r["evaluation"]["verdict"],
        }
        for r in newly_expired
    ]

    return {
        "active_rules": still_active,
        "expired_rules": expired[-MAX_EXPIRED_RULES:],
        "evaluation_log": evaluation_log[-100:],
    }


def _evaluate_expired_rule(rule: dict) -> dict:
    """评估规则有效性: Regime 感知对比启用前后绩效

    使用 regime_evaluator 在相同 regime 下对比，避免 regime 切换导致的误判。
    """
    try:
        from cryptobot.journal.regime_evaluator import evaluate_rule_effectiveness
        from cryptobot.journal.storage import get_all_records

        created_at = rule.get("created_at", "")
        all_records = get_all_records()
        closed = [
            r for r in all_records
            if r.status == "closed" and r.actual_pnl_pct is not None
        ]

        created_dt = datetime.fromisoformat(created_at) if created_at else datetime.min
        before = [
            r for r in closed
            if datetime.fromisoformat(r.timestamp) < created_dt
        ]
        after = [
            r for r in closed
            if datetime.fromisoformat(r.timestamp) >= created_dt
        ]

        if len(before) < 2 or len(after) < 2:
            return {
                "verdict": "neutral",
                "improvement_pct": 0,
                "reason": "前后样本不足，无法评估",
            }

        result = evaluate_rule_effectiveness(rule.get("id", ""), before, after)

        verdict = result["overall_verdict"]
        # 汇总各 regime 改善百分比 (加权平均)
        total_samples = 0
        weighted_improvement = 0.0
        for regime_data in result["by_regime"].values():
            n = regime_data["sample_size"]
            total_samples += n
            weighted_improvement += regime_data["improvement_pct"] * n
        avg_improvement = (
            weighted_improvement / total_samples if total_samples > 0 else 0.0
        )

        regime_details = ", ".join(
            f"{k}: {v['verdict']}"
            for k, v in result["by_regime"].items()
        )

        return {
            "verdict": verdict,
            "improvement_pct": round(avg_improvement, 1),
            "regime_analysis": result["by_regime"],
            "reason": f"Regime 感知评估: {regime_details}",
        }
    except Exception as e:
        logger.warning("Regime 感知评估失败，回退到简单对比: %s", e)
        return _evaluate_expired_rule_simple(rule)


def _evaluate_expired_rule_simple(rule: dict) -> dict:
    """简单胜率对比 (回退方案)"""
    try:
        from cryptobot.journal.analytics import calc_performance
        current = calc_performance(RULE_TTL_DAYS)
    except Exception:
        return {"verdict": "neutral", "improvement_pct": 0, "reason": "绩效计算异常"}

    before = rule.get("perf_snapshot_before", {})
    before_wr = before.get("win_rate", 0)
    after_wr = current.get("win_rate", 0)

    if before_wr <= 0:
        return {"verdict": "neutral", "improvement_pct": 0, "reason": "启用前无基线数据"}

    improvement = (after_wr - before_wr) / before_wr * 100

    if improvement > 5:
        verdict = "effective"
    elif improvement < -5:
        verdict = "harmful"
    else:
        verdict = "neutral"

    return {
        "verdict": verdict,
        "improvement_pct": round(improvement, 1),
        "before_win_rate": before_wr,
        "after_win_rate": after_wr,
        "reason": f"胜率 {before_wr:.1%} → {after_wr:.1%} ({improvement:+.1f}%)",
    }


# ── 证据收集 ─────────────────────────────────────────────────────────

def _gather_evidence() -> dict | None:
    """收集 LLM 分析所需的绩效证据

    Returns:
        证据字典，数据不足时返回 None
    """
    from cryptobot.journal.analytics import calc_performance, calc_analyst_accuracy

    perf_14 = calc_performance(14)
    perf_30 = calc_performance(30)

    if perf_14.get("closed", 0) < MIN_CLOSED_TRADES:
        logger.info("策略顾问: 近14天已平仓 %d 笔 < %d，跳过", perf_14["closed"], MIN_CLOSED_TRADES)
        return None

    accuracy = calc_analyst_accuracy(14)

    # 置信度校准
    threshold_info = {}
    try:
        from cryptobot.journal.confidence_tuner import calc_dynamic_threshold
        threshold_info = calc_dynamic_threshold(30)
    except Exception:
        pass

    # 当前 regime
    regime_info = {}
    try:
        from cryptobot.evolution.regime_prompts import load_regime_history
        history = load_regime_history()
        if history:
            regime_info = history[-1]
    except Exception:
        pass

    # 当前资金层级
    capital_info = {}
    try:
        from cryptobot.capital_strategy import detect_capital_tier
        from cryptobot.freqtrade_api import ft_api_get
        balance_data = ft_api_get("/balance") or {}
        balance = balance_data.get("total", 0)
        if balance > 0:
            capital_info = detect_capital_tier(balance)
    except Exception:
        pass

    return {
        "perf_14d": perf_14,
        "perf_30d": perf_30,
        "analyst_accuracy": accuracy,
        "confidence_threshold": threshold_info,
        "regime": regime_info,
        "capital_tier": capital_info,
    }


# ── LLM 调用 ────────────────────────────────────────────────────────

def _call_advisor(evidence: dict, existing_rules: list) -> list:
    """调用 LLM 生成策略建议

    Returns:
        [{"rule_text": "...", "target_role": "trader|risk_manager", "rationale": "..."}]
    """
    existing_text = (
        json.dumps([r["rule_text"] for r in existing_rules], ensure_ascii=False)
        if existing_rules else "无"
    )

    prompt = (
        f"## 绩效数据\n\n"
        f"### 近 14 天\n{json.dumps(evidence['perf_14d'], ensure_ascii=False, indent=2)}\n\n"
        f"### 近 30 天（基线）\n{json.dumps(evidence['perf_30d'], ensure_ascii=False, indent=2)}\n\n"
        f"### 分析师准确率\n{json.dumps(evidence['analyst_accuracy'], ensure_ascii=False, indent=2)}\n\n"
        f"### 置信度校准\n{json.dumps(evidence.get('confidence_threshold', {}), ensure_ascii=False, indent=2)}\n\n"
        f"### 当前市场状态\n{json.dumps(evidence.get('regime', {}), ensure_ascii=False, indent=2)}\n\n"
        f"### 资金层级\n{json.dumps(evidence.get('capital_tier', {}), ensure_ascii=False, indent=2)}\n\n"
        f"请基于以上数据生成策略规则。"
    )

    system = STRATEGY_ADVISOR_PROMPT.format(existing_rules=existing_text)

    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "rule_text": {"type": "string"},
                "target_role": {"type": "string", "enum": ["trader", "risk_manager"]},
                "rationale": {"type": "string"},
            },
            "required": ["rule_text", "target_role", "rationale"],
        },
    }

    try:
        from cryptobot.workflow.api_llm import call_api
        result = call_api(
            prompt,
            model="sonnet",
            role="strategy_advisor",
            system_prompt=system,
            json_schema=schema,
        )
    except Exception:
        from cryptobot.workflow.llm import call_claude
        result = call_claude(
            prompt, model="sonnet", role="strategy_advisor",
            system_prompt=system, json_schema=schema,
        )

    if isinstance(result, list):
        valid = [
            r for r in result
            if isinstance(r, dict)
            and r.get("rule_text")
            and r.get("target_role") in ("trader", "risk_manager")
        ]
        return valid[:MAX_NEW_RULES_PER_CYCLE]

    return []


def _create_rules(suggestions: list, perf_snapshot: dict, data: dict) -> dict:
    """创建新规则并追加到 active_rules

    Returns:
        更新后的 data
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    existing_ids = {r["id"] for r in data["active_rules"]}

    new_rules = []
    for i, s in enumerate(suggestions):
        seq = 1
        while True:
            rule_id = f"rule_{date_str}_{seq:03d}"
            if rule_id not in existing_ids:
                break
            seq += 1
        existing_ids.add(rule_id)

        new_rules.append({
            "id": rule_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=RULE_TTL_DAYS)).isoformat(),
            "rule_text": s["rule_text"],
            "target_role": s["target_role"],
            "rationale": s["rationale"],
            "perf_snapshot_before": {
                "win_rate": perf_snapshot.get("win_rate", 0),
                "avg_pnl_pct": perf_snapshot.get("avg_pnl_pct", 0),
                "closed": perf_snapshot.get("closed", 0),
            },
        })

    return {
        **data,
        "active_rules": data["active_rules"] + new_rules,
    }


# ── 主入口 ───────────────────────────────────────────────────────────

def run_advisor_cycle() -> dict:
    """运行一次完整的策略顾问周期

    Returns:
        {"triggered": bool, "new_rules": int, "reason": str}
    """
    # 1. 加载并处理过期规则
    data = _load_rules()
    data = _expire_old_rules(data)

    # 2. 检查是否还有空间
    if len(data["active_rules"]) >= MAX_ACTIVE_RULES:
        _save_rules(data)
        return {"triggered": False, "new_rules": 0, "reason": "活跃规则已满"}

    # 3. 收集绩效证据
    evidence = _gather_evidence()
    if evidence is None:
        _save_rules(data)
        return {"triggered": False, "new_rules": 0, "reason": "数据不足"}

    # 4. 调用 LLM 生成策略
    slots = MAX_ACTIVE_RULES - len(data["active_rules"])
    suggestions = _call_advisor(evidence, data["active_rules"])
    suggestions = suggestions[:slots]

    if not suggestions:
        _save_rules(data)
        return {"triggered": False, "new_rules": 0, "reason": "LLM 判断绩效良好，无需新规则"}

    # 5. 保存新规则
    perf_snapshot = evidence["perf_14d"]
    data = _create_rules(suggestions, perf_snapshot, data)
    _save_rules(data)

    # 6. Telegram 通知
    _notify_new_rules(suggestions)

    logger.info("策略顾问: 生成 %d 条新规则", len(suggestions))
    return {
        "triggered": True,
        "new_rules": len(suggestions),
        "reason": f"生成 {len(suggestions)} 条策略规则",
    }


def _notify_new_rules(suggestions: list) -> None:
    """推送新规则通知"""
    try:
        from cryptobot.notify import send_message
        lines = ["🧠 *策略顾问* — 新规则生成\n"]
        for s in suggestions:
            role_label = "交易" if s["target_role"] == "trader" else "风控"
            lines.append(f"[{role_label}] {s['rule_text']}")
            lines.append(f"  依据: {s['rationale']}\n")
        send_message("\n".join(lines))
    except Exception as e:
        logger.warning("策略通知发送失败: %s", e)
