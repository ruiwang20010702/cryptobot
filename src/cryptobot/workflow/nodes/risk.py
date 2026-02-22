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


def _check_loss_limits(risk_cfg: dict, account_balance: float) -> tuple[bool, str]:
    """检查日度/周度/月度亏损是否超限

    基于实际亏损 USDT 占账户余额百分比来判断。

    Returns:
        (通过, 原因) — 通过=True 表示未超限可以开仓
    """
    if account_balance <= 0:
        return False, "账户余额为 0，无法检查亏损限制"

    max_loss = risk_cfg.get("max_loss", {})
    daily_limit = max_loss.get("daily_pct", 5)
    weekly_limit = max_loss.get("weekly_pct", 8)
    monthly_limit = max_loss.get("monthly_drawdown_pct", 15)

    try:
        from cryptobot.journal.storage import get_all_records
        from datetime import datetime, timezone, timedelta

        all_records = get_all_records()

        for days, limit, label in [
            (1, daily_limit, "日度"),
            (7, weekly_limit, "周度"),
            (30, monthly_limit, "月度"),
        ]:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            closed = [
                r for r in all_records
                if r.status == "closed" and r.timestamp >= cutoff
            ]
            if not closed:
                continue
            total_loss_usdt = sum(
                r.actual_pnl_usdt for r in closed
                if r.actual_pnl_usdt and r.actual_pnl_usdt < 0
            )
            loss_pct = abs(total_loss_usdt) / account_balance * 100
            if loss_pct > limit:
                return False, f"{label}亏损 {loss_pct:.1f}% 超过限制 {limit}%"
    except Exception as e:
        logger.error("亏损限制检查异常: %s", e)
        return False, "亏损限制检查异常，安全起见暂停开仓"

    return True, ""


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
    *, analyst_votes: dict | None = None, regime: str = "",
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

    # fallback: 用 AI 建议的百分比 × 余额 (余额为0时返回0，由硬性规则拦截)
    if position_size_usdt is None:
        pct = decision.get("position_size_pct", 10)
        position_size_usdt = account_balance * pct / 100 if account_balance > 0 else 0

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
        "regime": regime,
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
    from cryptobot.capital_strategy import _extract_usdt_balance

    account_balance = _extract_usdt_balance(ft_api_get("/balance"))
    if account_balance <= 0:
        logger.warning("无法获取账户余额, 拒绝所有新开仓")
        _console.print("    [red]余额为 0 或 Freqtrade 离线, 拒绝所有新开仓[/red]")
        from cryptobot.notify import send_message
        send_message("⚠️ *风控拦截*\n\n余额为 0 或 Freqtrade 离线，拒绝所有新开仓")
        return {"approved_signals": [], "errors": errors}

    positions = ft_api_get("/status") or []
    portfolio_ctx = state.get("portfolio_context") or _build_portfolio_context()

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

    # 资金层级上下文
    capital_tier = state.get("capital_tier", {})
    capital_ctx = ""
    capital_risk_addon = ""
    merged_params = {}
    if capital_tier:
        tier_name = capital_tier.get("tier", "medium")
        tier_balance = capital_tier.get("balance", 0)
        tier_params = capital_tier.get("params", {})

        from cryptobot.capital_strategy import merge_regime_capital_params, calc_drawdown_factor
        dd_info = calc_drawdown_factor(lookback_days=7)
        drawdown_factor = dd_info.get("leverage_factor", 1.0)
        if drawdown_factor < 1.0:
            logger.info(
                "风控回撤感知: %.1f%% 回撤, 杠杆因子 %.2f (%s)",
                dd_info["drawdown_pct"], drawdown_factor, dd_info["tier"],
            )
        merged_params = merge_regime_capital_params(
            regime.get("params", {}), tier_params, drawdown_factor=drawdown_factor,
        )

        capital_ctx = (
            f"### 资金层级\n"
            f"- 层级: {tier_name} (余额 ${tier_balance:.0f})\n"
            f"- 最大持仓数: {merged_params.get('max_positions', 5)}\n"
            f"- 合并后最大杠杆: {merged_params.get('max_leverage', 5)}x\n"
            f"- 合并后最低置信度: {merged_params.get('min_confidence', 55)}\n\n"
        )

        try:
            from cryptobot.evolution.capital_prompts import get_capital_addon
            capital_risk_addon = get_capital_addon(tier_name, "RISK_MANAGER")
        except Exception:
            pass

    # 第5层: strategy advisor addon
    _strategy_risk_addon = ""
    try:
        from cryptobot.evolution.strategy_advisor import get_strategy_addon
        _strategy_risk_addon = get_strategy_addon("risk_manager")
    except Exception:
        pass

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

    # ── C1: 日度/周度/月度亏损限制检查 ──
    loss_ok, loss_reason = _check_loss_limits(risk_cfg, account_balance)
    if not loss_ok:
        logger.warning("亏损限制触发: %s, 拒绝所有新开仓", loss_reason)
        _console.print(f"    [red]亏损限制: {loss_reason}, 拒绝所有新开仓[/red]")
        from cryptobot.notify import send_message
        send_message(f"🚫 *亏损限制触发*\n\n{loss_reason}\n已拒绝所有新开仓")
        return {"approved_signals": [], "errors": errors}

    analyses = state.get("analyses", {})
    approved = []
    hard_rule_results = []
    ai_review_results = []
    rejected_signals = []

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
        hard_checks: list[dict] = []

        if account_balance > 0:
            max_total_pct = risk_cfg.get("max_total_position_pct", 80)
            total_pct = total_used / account_balance * 100
            if total_pct >= max_total_pct:
                reason = f"总仓位 {total_pct:.1f}% >= 上限 {max_total_pct}%"
                hard_checks.append({"rule": "max_total_position", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(f"    [red]拒绝 {symbol}: 总仓位已达上限 {total_pct:.0f}%[/red]")
                continue
            hard_checks.append({"rule": "max_total_position", "passed": True})

            max_dir_pct = risk_cfg.get("max_same_direction_pct", 50)
            dir_used = short_used if action == "short" else long_used
            dir_pct = dir_used / account_balance * 100
            if dir_pct >= max_dir_pct:
                dir_name = "空头" if action == "short" else "多头"
                reason = f"{dir_name}仓位 {dir_pct:.1f}% >= 上限 {max_dir_pct}%"
                hard_checks.append({"rule": "max_same_direction", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(f"    [red]拒绝 {symbol}: {dir_name}仓位已达上限 {dir_pct:.0f}%[/red]")
                continue
            hard_checks.append({"rule": "max_same_direction", "passed": True})

        # 置信度硬性检查
        try:
            from cryptobot.journal.confidence_tuner import calc_dynamic_threshold
            threshold = calc_dynamic_threshold(30)
            min_conf = threshold["recommended_min_confidence"]
            decision_conf = decision.get("confidence", 0)
            if decision_conf < min_conf and threshold["sample_size"] >= 15:
                reason = f"置信度 {decision_conf} < 动态阈值 {min_conf}"
                hard_checks.append({"rule": "dynamic_confidence", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(
                    f"    [red]拒绝 {symbol}: 置信度 {decision_conf} < 动态阈值 {min_conf}[/red]"
                )
                continue
            hard_checks.append({"rule": "dynamic_confidence", "passed": True})
        except Exception as e:
            logger.warning("动态置信度检查失败: %s", e)

        # ── 资金层级硬性规则 ──
        if merged_params:
            # 持仓数限制（含本批已通过的数量）
            max_pos = merged_params.get("max_positions", 5)
            if len(positions) + len(approved) >= max_pos:
                reason = f"持仓数 {len(positions)} >= 资金层级上限 {max_pos}"
                hard_checks.append({"rule": "max_positions", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(
                    f"    [red]拒绝 {symbol}: 持仓数已达层级上限 {max_pos}[/red]"
                )
                continue
            hard_checks.append({"rule": "max_positions", "passed": True})

            # 资金层级置信度门槛 (regime_min + capital_boost)
            capital_min_conf = merged_params.get("min_confidence", 55)
            decision_conf = decision.get("confidence", 0)
            if decision_conf < capital_min_conf:
                reason = f"置信度 {decision_conf} < 资金层级阈值 {capital_min_conf}"
                hard_checks.append({"rule": "capital_confidence", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(
                    f"    [red]拒绝 {symbol}: 置信度 {decision_conf} < 层级阈值 {capital_min_conf}[/red]"
                )
                continue
            hard_checks.append({"rule": "capital_confidence", "passed": True})

            # 杠杆强制降低
            capital_lev_cap = merged_params.get("max_leverage", 5)
            if leverage > capital_lev_cap:
                hard_checks.append({
                    "rule": "leverage_cap", "passed": True,
                    "note": f"杠杆 {leverage}x → {capital_lev_cap}x",
                })
                logger.info(
                    "强制降杠杆 %s: %dx → %dx (资金层级限制)",
                    symbol, leverage, capital_lev_cap,
                )
                _console.print(
                    f"    [yellow]{symbol}: 杠杆 {leverage}x → {capital_lev_cap}x (层级限制)[/yellow]"
                )
                decision["leverage"] = capital_lev_cap
                leverage = capital_lev_cap

        # P3: 盈亏比 RR 检查（阈值随 regime 动态调整）
        _RR_THRESHOLDS = {"trending": 1.2, "ranging": 2.0, "volatile": 2.0}
        regime_name = regime.get("regime", "")
        rr_threshold = _RR_THRESHOLDS.get(regime_name, 1.5)

        stop_loss = decision.get("stop_loss")
        tp_list = decision.get("take_profit", [])
        if entry_range and len(entry_range) == 2 and entry_range[0] and stop_loss and tp_list:
            entry_mid = (entry_range[0] + entry_range[1]) / 2
            sl_dist = abs(entry_mid - stop_loss)
            first_tp = (
                tp_list[0].get("price", 0) if isinstance(tp_list[0], dict) else tp_list[0]
            )
            tp_dist = abs(first_tp - entry_mid) if first_tp else 0
            rr = tp_dist / sl_dist if sl_dist > 0 else 0
            if rr < rr_threshold:
                reason = f"盈亏比 {rr:.2f} < {rr_threshold} ({regime_name})"
                hard_checks.append({"rule": "risk_reward", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(
                    f"    [red]拒绝 {symbol}: 盈亏比 {rr:.2f} < {rr_threshold} ({regime_name})[/red]"
                )
                continue
            hard_checks.append({"rule": "risk_reward", "passed": True})

        # 计算爆仓距离
        liq_info = ""
        if entry_range and len(entry_range) == 2 and entry_range[0]:
            entry_mid = (entry_range[0] + entry_range[1]) / 2
            liq_price = calc_liquidation_price(entry_mid, leverage, action)
            liq_dist = calc_liquidation_distance(current_price or entry_mid, liq_price)
            liq_info = f"爆仓价: {liq_price:.2f}, 爆仓距离: {liq_dist:.1f}%"

            # P13: 爆仓距离杠杆感知动态阈值
            min_liq_dist = max(15, 30 - (5 - leverage) * 3)
            if liq_dist < min_liq_dist:
                reason = f"爆仓距离 {liq_dist:.1f}% < {min_liq_dist:.0f}% ({leverage}x杠杆)"
                hard_checks.append({"rule": "liquidation_distance", "passed": False, "reason": reason})
                hard_rule_results.append({"symbol": symbol, "passed": False, "checks": hard_checks})
                rejected_signals.append({"symbol": symbol, "reason": reason})
                logger.info("硬性拒绝 %s: %s", symbol, reason)
                _console.print(
                    f"    [red]拒绝 {symbol}: 爆仓距离 {liq_dist:.1f}% < "
                    f"{min_liq_dist:.0f}% ({leverage}x杠杆)[/red]"
                )
                continue
            hard_checks.append({"rule": "liquidation_distance", "passed": True})

        # 硬规则全部通过
        hard_rule_results.append({"symbol": symbol, "passed": True, "checks": hard_checks})

        existing = [s for s in existing_signals if s["symbol"] == symbol]

        all_tasks.append({
            "prompt": (
                f"## 风控审核: {symbol}\n\n"
                f"{portfolio_ctx}"
                f"{perf_ctx}"
                f"{regime_ctx}"
                f"{capital_ctx}"
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
            "system_prompt": RISK_MANAGER + capital_risk_addon + _strategy_risk_addon,
            "json_schema": RISK_SCHEMA,
        })
        task_decisions.append(decision)
        _console.print(f"    审核 {symbol} ({action})...")

    # 并行风控审核
    if all_tasks:
        results = call_claude_parallel(all_tasks)
        for i, result in enumerate(results):
            decision = {**task_decisions[i]}
            symbol = decision.get("symbol", "")
            action = decision.get("action", "")
            if isinstance(result, dict) and "error" not in result:
                _ADJUSTABLE_FIELDS = {
                    "leverage", "stop_loss", "take_profit",
                    "position_size_pct", "entry_price_range",
                }
                if result.get("decision") in ("approved", "modified"):
                    if result.get("decision") == "modified" and result.get("adjustments"):
                        for k, v in result["adjustments"].items():
                            if k in _ADJUSTABLE_FIELDS:
                                decision[k] = v
                    votes = _extract_votes(analyses, symbol)
                    regime_name = regime.get("regime", "")
                    sig = _decision_to_signal(
                        decision, result, account_balance,
                        analyst_votes=votes, regime=regime_name,
                    )
                    # P20: 注入风控修改内容
                    if result.get("decision") == "modified" and result.get("adjustments"):
                        sig["risk_review_changes"] = result["adjustments"]
                    approved.append(sig)
                    ai_review_results.append({
                        "symbol": symbol,
                        "verdict": result.get("decision", "approved"),
                        "reasoning": result.get("reasoning", ""),
                        "modifications": result.get("adjustments", {}),
                    })
                    # C2: 累加仓位占用，避免同批信号竞态
                    margin = sig.get("position_size_usdt", 0)
                    total_used += margin
                    if action == "short":
                        short_used += margin
                    else:
                        long_used += margin
                    logger.info("风控通过: %s %s", symbol, action)
                else:
                    reason = result.get("reasoning", "未知")
                    ai_review_results.append({
                        "symbol": symbol,
                        "verdict": "rejected",
                        "reasoning": reason,
                    })
                    rejected_signals.append({"symbol": symbol, "reason": f"AI审核拒绝: {reason}"})
                    logger.info("风控拒绝: %s, 原因: %s", symbol, reason)
                    from cryptobot.notify import notify_risk_rejected
                    notify_risk_rejected(symbol, reason[:200])
            else:
                err = result.get("error", str(result)) if isinstance(result, dict) else str(result)
                errors.append(f"risk_{symbol}: {err}")

    risk_details = {
        "hard_rule_results": hard_rule_results,
        "ai_review_results": ai_review_results,
        "rejected_signals": rejected_signals,
    }

    _console.print(f"    完成: {len(approved)} 通过 / {len(task_decisions) - len(approved)} 拒绝, "
                    f"耗时 {time.time() - t0:.0f}s")
    return {"approved_signals": approved, "risk_details": risk_details, "errors": errors}
