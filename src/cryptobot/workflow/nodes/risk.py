"""Node: risk_review — 风控审核每个交易决策"""

import json
import logging
import time
from datetime import datetime, timezone

from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.prompts import RISK_MANAGER, RISK_SCHEMA
from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage, _build_portfolio_context

logger = logging.getLogger(__name__)
_console = Console()


def _extract_votes(analyses: dict, symbol: str) -> dict | None:
    """从分析师结果中提取各角色的方向投票

    Returns:
        {"technical": "bullish", "onchain": "bearish", ...} 或 None
    """
    analysis = analyses.get(symbol, {})
    if not analysis:
        return None
    votes = {}
    for role, result in analysis.items():
        if isinstance(result, dict) and "direction" in result:
            votes[role] = result["direction"]
    return votes or None


def _decision_to_signal(
    decision: dict, risk_result: dict, account_balance: float,
    *, analyst_votes: dict | None = None,
) -> dict:
    """将交易决策转换为信号格式，调用 position_sizer 计算仓位"""
    from cryptobot.risk.position_sizer import calc_position_size

    now = datetime.now(timezone.utc)
    leverage = decision.get("leverage", 3)
    entry_range = decision.get("entry_price_range")
    stop_loss = decision.get("stop_loss")

    # 计算精确仓位（需要入场价和止损价）
    position_size_usdt = None
    if entry_range and len(entry_range) == 2 and entry_range[0] and stop_loss and account_balance > 0:
        entry_price = (entry_range[0] + entry_range[1]) / 2
        try:
            sizing = calc_position_size(
                symbol=decision["symbol"],
                account_balance=account_balance,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
                leverage=leverage,
            )
            position_size_usdt = sizing["margin_usdt"]
            logger.info(
                "仓位计算 %s: balance=%.0f, margin=%.0f, max_loss=%.0f (%.1f%%)",
                decision["symbol"], account_balance, sizing["margin_usdt"],
                sizing["max_loss_usdt"], sizing["max_loss_pct_of_balance"],
            )
        except (ValueError, KeyError) as e:
            logger.warning("仓位计算失败 %s: %s, 使用 AI 建议比例", decision["symbol"], e)

    # fallback: 用 AI 建议的百分比 × 余额
    if position_size_usdt is None:
        pct = decision.get("position_size_pct", 10)
        position_size_usdt = account_balance * pct / 100 if account_balance > 0 else 1000

    return {
        "symbol": decision["symbol"],
        "action": decision["action"],
        "leverage": leverage,
        "entry_price_range": entry_range,
        "stop_loss": stop_loss,
        "take_profit": decision.get("take_profit", []),
        "confidence": decision.get("confidence", 50),
        "position_size_usdt": round(position_size_usdt, 2),
        "analysis_summary": {
            "reasoning": decision.get("reasoning", ""),
            "risk_score": risk_result.get("risk_score"),
            "warnings": risk_result.get("warnings", []),
        },
        "timestamp": now.isoformat(),
        "analyst_votes": analyst_votes,
    }


def risk_review(state: WorkflowState) -> dict:
    """风控审核每个交易决策"""
    decisions = state.get("decisions", [])
    actionable = [d for d in decisions if d.get("action") != "no_trade"]
    _stage(6, f"风控审核 — {len(actionable)} 个决策")
    t0 = time.time()
    errors = list(state.get("errors", []))

    from cryptobot.config import load_settings, get_pair_config
    from cryptobot.risk.liquidation_calc import calc_liquidation_price, calc_liquidation_distance
    from cryptobot.signal.bridge import read_signals

    settings = load_settings()
    risk_cfg = settings.get("risk", {})
    existing_signals = read_signals()

    # 获取账户余额和持仓（用于仓位计算和硬性规则检查）
    from cryptobot.freqtrade_api import ft_api_get

    balance_data = ft_api_get("/balance")
    account_balance = 0.0
    if balance_data:
        for cur in balance_data.get("currencies", []):
            if cur.get("currency") == "USDT":
                account_balance = float(cur.get("balance", 0))
                break
    if account_balance <= 0:
        logger.warning("无法获取账户余额, 仓位计算将使用 AI 建议比例 fallback")

    positions = ft_api_get("/status") or []
    portfolio_ctx = _build_portfolio_context()

    # 获取历史绩效摘要
    perf_ctx = ""
    try:
        from cryptobot.journal.analytics import build_performance_summary
        perf_ctx = build_performance_summary(30)
    except Exception as e:
        logger.warning("绩效摘要生成失败: %s", e)

    # 市场状态上下文
    regime = state.get("market_regime", {})
    regime_ctx = ""
    if regime:
        regime_ctx = (
            f"### 当前市场状态\n"
            f"- 状态: {regime.get('regime', 'unknown')}\n"
            f"- {regime.get('description', '')}\n"
            f"- 建议最大杠杆: {regime.get('params', {}).get('max_leverage', 5)}x\n\n"
        )

    # 置信度校准上下文（注入到风控 prompt）
    confidence_ctx = ""
    try:
        from cryptobot.journal.confidence_tuner import build_threshold_context
        confidence_ctx = build_threshold_context(regime, 30)
    except Exception as e:
        logger.warning("置信度校准失败: %s", e)

    # 宏观事件风险提示
    macro_events = state.get("macro_events", {})
    macro_ctx = ""
    if macro_events.get("has_high_impact"):
        next_ev = macro_events.get("next_high_impact")
        if next_ev:
            macro_ctx = (
                f"### 宏观风险提示\n"
                f"- 注意: {next_ev['hours_until']:.0f} 小时后有 {next_ev['event']}，建议降低杠杆\n"
                f"- 高影响事件数: {macro_events.get('event_count', 0)}\n\n"
            )

    # 计算当前仓位占比（用于硬性规则）
    long_used = sum(float(p.get("stake_amount", 0) or 0) for p in positions if not p.get("is_short"))
    short_used = sum(float(p.get("stake_amount", 0) or 0) for p in positions if p.get("is_short"))
    total_used = long_used + short_used

    analyses = state.get("analyses", {})
    approved = []

    # 构建所有风控审核任务
    all_tasks = []
    task_decisions = []

    for decision in decisions:
        if decision.get("action") == "no_trade":
            continue

        symbol = decision.get("symbol", "")
        action = decision.get("action", "")
        leverage = decision.get("leverage", 3)
        entry_range = decision.get("entry_price_range", [])
        current_price = decision.get("current_price", 0)

        pair_cfg = get_pair_config(symbol) or {}

        # ── 硬性规则检查（不依赖 AI 判断）──
        if account_balance > 0:
            max_total_pct = risk_cfg.get("max_total_position_pct", 80)
            total_pct = total_used / account_balance * 100
            if total_pct >= max_total_pct:
                logger.info("硬性拒绝 %s: 总仓位 %.1f%% >= 上限 %d%%", symbol, total_pct, max_total_pct)
                _console.print(f"    [red]拒绝 {symbol}: 总仓位已达上限 {total_pct:.0f}%[/red]")
                continue

            max_dir_pct = risk_cfg.get("max_same_direction_pct", 50)
            dir_used = short_used if action == "short" else long_used
            dir_pct = dir_used / account_balance * 100
            if dir_pct >= max_dir_pct:
                dir_name = "空头" if action == "short" else "多头"
                logger.info("硬性拒绝 %s: %s仓位 %.1f%% >= 上限 %d%%", symbol, dir_name, dir_pct, max_dir_pct)
                _console.print(f"    [red]拒绝 {symbol}: {dir_name}仓位已达上限 {dir_pct:.0f}%[/red]")
                continue

        # 置信度硬性检查
        try:
            from cryptobot.journal.confidence_tuner import calc_dynamic_threshold
            threshold = calc_dynamic_threshold(30)
            min_conf = threshold["recommended_min_confidence"]
            decision_conf = decision.get("confidence", 0)
            if decision_conf < min_conf and threshold["sample_size"] >= 15:
                logger.info(
                    "硬性拒绝 %s: 置信度 %d < 动态阈值 %d",
                    symbol, decision_conf, min_conf,
                )
                _console.print(
                    f"    [red]拒绝 {symbol}: 置信度 {decision_conf} < 动态阈值 {min_conf}[/red]"
                )
                continue
        except Exception as e:
            logger.warning("动态置信度检查失败: %s", e)

        # 计算爆仓距离
        liq_info = ""
        if entry_range and len(entry_range) == 2 and entry_range[0]:
            entry_mid = (entry_range[0] + entry_range[1]) / 2
            liq_price = calc_liquidation_price(entry_mid, leverage, action)
            liq_dist = calc_liquidation_distance(current_price or entry_mid, liq_price)
            liq_info = f"爆仓价: {liq_price:.2f}, 爆仓距离: {liq_dist:.1f}%"

        existing = [s for s in existing_signals if s["symbol"] == symbol]

        all_tasks.append({
            "prompt": (
                f"## 风控审核: {symbol}\n\n"
                f"{portfolio_ctx}"
                f"{perf_ctx}"
                f"{regime_ctx}"
                f"{confidence_ctx}"
                f"{macro_ctx}"
                f"### 交易决策\n{json.dumps(decision, ensure_ascii=False, indent=2)}\n\n"
                f"### 风控参数\n"
                f"- 最大杠杆: {pair_cfg.get('leverage_range', [1, 5])[1]}x\n"
                f"- 单笔最大亏损: {risk_cfg.get('max_loss', {}).get('per_trade_pct', 2)}%\n"
                f"- {liq_info}\n"
                f"- 现有持仓: {len(existing)} 个\n"
                f"- 现有信号: {json.dumps(existing, ensure_ascii=False, indent=2) if existing else '无'}\n\n"
                f"请进行风控审核。"
            ),
            "model": "sonnet",
            "role": "risk_manager",
            "system_prompt": RISK_MANAGER,
            "json_schema": RISK_SCHEMA,
        })
        task_decisions.append(decision)
        _console.print(f"    审核 {symbol} ({action})...")

    # 并行风控审核
    if all_tasks:
        results = call_claude_parallel(all_tasks)
        for i, result in enumerate(results):
            decision = task_decisions[i]
            symbol = decision.get("symbol", "")
            action = decision.get("action", "")
            if isinstance(result, dict) and "error" not in result:
                if result.get("decision") in ("approved", "modified"):
                    if result.get("decision") == "modified" and result.get("adjustments"):
                        for k, v in result["adjustments"].items():
                            if k in decision:
                                decision[k] = v
                    votes = _extract_votes(analyses, symbol)
                    approved.append(_decision_to_signal(
                        decision, result, account_balance, analyst_votes=votes,
                    ))
                    logger.info("风控通过: %s %s", symbol, action)
                else:
                    reason = result.get("reasoning", "未知")
                    logger.info("风控拒绝: %s, 原因: %s", symbol, reason)
                    from cryptobot.notify import notify_risk_rejected
                    notify_risk_rejected(symbol, reason[:200])
            else:
                err = result.get("error", str(result)) if isinstance(result, dict) else str(result)
                errors.append(f"risk_{symbol}: {err}")

    _console.print(f"    完成: {len(approved)} 通过 / {len(task_decisions) - len(approved)} 拒绝, "
                    f"耗时 {time.time() - t0:.0f}s")
    return {"approved_signals": approved, "errors": errors}
