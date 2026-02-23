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

# ── 硬性规则常量 ──
_MAX_DAILY_TRADES_RANGING = 2
_MAX_DAILY_TRADES_VOLATILE = 1
_VOLATILE_MAX_LEVERAGE = 2
_VOLATILE_MIN_CONFIDENCE = 75
_RR_THRESHOLDS = {"trending": 1.2, "ranging": 2.0, "volatile": 2.0}
_SL_MIN_PCT = 0.5
_SL_MAX_PCT = 15.0


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


def _normalize_take_profit(tp_raw: list) -> list[dict]:
    """标准化 take_profit 为 [{"price": x, "ratio": y}] 格式

    支持输入:
    - [63000, 65000]  (纯数字)
    - [{"price": 63000, "pct": 50}]  (旧 pct 字段)
    - [{"price": 63000, "ratio": 0.5}]  (已标准化)
    """
    if not tp_raw:
        return []
    result = []
    n = len(tp_raw)
    for item in tp_raw:
        if isinstance(item, dict):
            price = item.get("price", 0)
            ratio = item.get("ratio", 0)
            if not ratio:
                pct = item.get("pct", 0)
                ratio = pct / 100 if pct else (1.0 / n)
            result.append({"price": price, "ratio": ratio})
        elif isinstance(item, (int, float)):
            result.append({"price": item, "ratio": 1.0 / n})
    return result


def _decision_to_signal(
    decision: dict, risk_result: dict, account_balance: float,
    *, analyst_votes: dict | None = None, regime: str = "",
    positions: list | None = None,
    corr_matrix: object | None = None,
    current_atr_pct: float | None = None,
    hist_atr_pct: float | None = None,
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
                action=decision.get("action"),
                confidence=decision.get("confidence"),
                regime=regime,
                positions=positions,
                corr_matrix=corr_matrix,
                current_atr_pct=current_atr_pct,
                hist_atr_pct=hist_atr_pct,
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

    # R6-L8: margin=0 时返回 None 让调用方 reject
    if position_size_usdt <= 0:
        logger.warning(
            "仓位为 0, reject %s (balance=%.0f)",
            decision["symbol"], account_balance,
        )
        return None

    return {
        "symbol": decision["symbol"],
        "action": decision["action"],
        "leverage": leverage,
        "entry_price_range": entry_range,
        "stop_loss": stop_loss,
        "take_profit": _normalize_take_profit(decision.get("take_profit", [])),
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
        "strategy_route": decision.get("strategy_route"),
        "strategy_weight": decision.get("strategy_weight"),
        "ml_score": decision.get("ml_score"),
        "model_id": decision.get("model_id"),
    }


def _apply_hard_rules(
    decision: dict, regime: dict, risk_cfg: dict, settings: dict,
    cb_state, merged_params: dict, account_balance: float,
    positions: list, approved: list, preloaded_records: list | None,
    total_used: float, long_used: float, short_used: float,
) -> dict:
    """硬性规则检查（不依赖 AI 判断）

    Returns:
        {"passed": bool, "decision": dict, "hard_result": dict, "rejected": dict|None}
    """
    from cryptobot.risk.liquidation_calc import calc_liquidation_price, calc_liquidation_distance

    symbol = decision.get("symbol", "")
    action = decision.get("action", "")
    leverage = decision.get("leverage", 3)
    entry_range = decision.get("entry_price_range", [])
    current_price = decision.get("current_price", 0)

    hard_checks: list[dict] = []

    def _reject(reason: str) -> dict:
        hard_checks.append({"rule": "reject", "passed": False, "reason": reason})
        logger.info("硬性拒绝 %s: %s", symbol, reason)
        _console.print(f"    [red]拒绝 {symbol}: {reason}[/red]")
        return {
            "passed": False,
            "decision": decision,
            "hard_result": {"symbol": symbol, "passed": False, "checks": hard_checks},
            "rejected": {"symbol": symbol, "reason": reason},
        }

    # ── 月度熔断: 降仓 + 禁止做多 ──
    if cb_state.action == "reduce":
        if action == "long" and cb_state.block_long:
            reason = f"月度熔断禁止做多: {cb_state.reason}"
            hard_checks.append({
                "rule": "monthly_circuit_breaker", "passed": False, "reason": reason,
            })
            return _reject(reason)
        scaled_lev = max(1, int(leverage * cb_state.position_scale))
        if scaled_lev != leverage:
            logger.info("月度熔断降杠杆 %s: %dx → %dx", symbol, leverage, scaled_lev)
            _console.print(
                f"    [yellow]{symbol}: 杠杆 {leverage}x → {scaled_lev}x (月度熔断)[/yellow]"
            )
            decision = {**decision, "leverage": scaled_lev}
            leverage = scaled_lev
        hard_checks.append({"rule": "monthly_circuit_breaker", "passed": True})

    # ── P13.2: 置信度绝对下限 ──
    decision_conf = decision.get("confidence", 0)
    regime_name = regime.get("regime", "")
    conf_floor = risk_cfg.get("confidence_floor", 60)
    conf_floor_ranging = risk_cfg.get("confidence_floor_ranging", 65)
    min_floor = conf_floor_ranging if regime_name == "ranging" else conf_floor
    if decision_conf < min_floor:
        reason = f"置信度 {decision_conf} < 绝对下限 {min_floor} ({regime_name or 'default'})"
        hard_checks.append({"rule": "confidence_floor", "passed": False, "reason": reason})
        return _reject(reason)
    hard_checks.append({"rule": "confidence_floor", "passed": True})

    # ── P13.3: 做多加严 ──
    long_min_conf = risk_cfg.get("long_min_confidence", 65)
    ranging_block = risk_cfg.get("ranging_block_long", True)
    if action == "long":
        if ranging_block and regime_name == "ranging":
            reason = "震荡市禁止做多"
            hard_checks.append({"rule": "ranging_block_long", "passed": False, "reason": reason})
            return _reject(reason)
        if decision_conf < long_min_conf:
            reason = f"做多置信度 {decision_conf} < {long_min_conf}"
            hard_checks.append({"rule": "long_min_confidence", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "long_min_confidence", "passed": True})

    # ── P13.7: 震荡市日交易数限制 ──
    if regime_name == "ranging" and preloaded_records is not None:
        ranging_cfg = settings.get("market_regime", {}).get("ranging", {})
        max_daily = ranging_cfg.get("max_daily_trades", _MAX_DAILY_TRADES_RANGING)
        if max_daily:
            from datetime import timedelta
            today_cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            today_trades = [
                r for r in preloaded_records
                if r.timestamp >= today_cutoff and r.status != "expired"
            ]
            if len(today_trades) >= max_daily:
                reason = f"震荡市日交易数 {len(today_trades)} >= 限制 {max_daily}"
                hard_checks.append({
                    "rule": "ranging_daily_limit", "passed": False, "reason": reason,
                })
                return _reject(reason)
            hard_checks.append({"rule": "ranging_daily_limit", "passed": True})

    # ── R6-H3: 高波动 regime 硬性规则 ──
    if regime_name == "volatile":
        if leverage > _VOLATILE_MAX_LEVERAGE:
            logger.info("volatile 降杠杆 %s: %dx → %dx", symbol, leverage, _VOLATILE_MAX_LEVERAGE)
            _console.print(
                f"    [yellow]{symbol}: 杠杆 {leverage}x → "
                f"{_VOLATILE_MAX_LEVERAGE}x (volatile)[/yellow]"
            )
            decision = {**decision, "leverage": _VOLATILE_MAX_LEVERAGE}
            leverage = _VOLATILE_MAX_LEVERAGE
        hard_checks.append({"rule": "volatile_leverage_cap", "passed": True})

        if decision_conf < _VOLATILE_MIN_CONFIDENCE:
            reason = f"volatile 置信度 {decision_conf} < {_VOLATILE_MIN_CONFIDENCE}"
            hard_checks.append({"rule": "volatile_confidence", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "volatile_confidence", "passed": True})

        if preloaded_records is not None:
            from datetime import timedelta
            today_cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            today_trades = [
                r for r in preloaded_records
                if r.timestamp >= today_cutoff and r.status != "expired"
            ]
            if len(today_trades) >= _MAX_DAILY_TRADES_VOLATILE:
                reason = (
                    f"volatile 日交易数 {len(today_trades)}"
                    f" >= 限制 {_MAX_DAILY_TRADES_VOLATILE}"
                )
                hard_checks.append({
                    "rule": "volatile_daily_limit", "passed": False, "reason": reason,
                })
                return _reject(reason)
            hard_checks.append({"rule": "volatile_daily_limit", "passed": True})

    # ── 仓位占比检查 ──
    if account_balance > 0:
        max_total_pct = risk_cfg.get("max_total_position_pct", 80)
        total_pct = total_used / account_balance * 100
        if total_pct >= max_total_pct:
            reason = f"总仓位 {total_pct:.1f}% >= 上限 {max_total_pct}%"
            hard_checks.append({"rule": "max_total_position", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "max_total_position", "passed": True})

        max_dir_pct = risk_cfg.get("max_same_direction_pct", 50)
        dir_used = short_used if action == "short" else long_used
        dir_pct = dir_used / account_balance * 100
        if dir_pct >= max_dir_pct:
            dir_name = "空头" if action == "short" else "多头"
            reason = f"{dir_name}仓位 {dir_pct:.1f}% >= 上限 {max_dir_pct}%"
            hard_checks.append({"rule": "max_same_direction", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "max_same_direction", "passed": True})

    # ── 动态置信度 ──
    try:
        from cryptobot.journal.confidence_tuner import calc_dynamic_threshold
        threshold = calc_dynamic_threshold(30)
        min_conf = threshold["recommended_min_confidence"]
        decision_conf = decision.get("confidence", 0)
        if decision_conf < min_conf and threshold["sample_size"] >= 15:
            reason = f"置信度 {decision_conf} < 动态阈值 {min_conf}"
            hard_checks.append({"rule": "dynamic_confidence", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "dynamic_confidence", "passed": True})
    except Exception as e:
        logger.warning("动态置信度检查失败: %s", e)

    # ── 资金层级硬性规则 ──
    if merged_params:
        max_pos = merged_params.get("max_positions", 5)
        if len(positions) + len(approved) >= max_pos:
            reason = f"持仓数 {len(positions)} >= 资金层级上限 {max_pos}"
            hard_checks.append({"rule": "max_positions", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "max_positions", "passed": True})

        capital_min_conf = merged_params.get("min_confidence", 55)
        decision_conf = decision.get("confidence", 0)
        if decision_conf < capital_min_conf:
            reason = f"置信度 {decision_conf} < 资金层级阈值 {capital_min_conf}"
            hard_checks.append({"rule": "capital_confidence", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "capital_confidence", "passed": True})

        capital_lev_cap = merged_params.get("max_leverage", 5)
        if leverage > capital_lev_cap:
            hard_checks.append({
                "rule": "leverage_cap", "passed": True,
                "note": f"杠杆 {leverage}x → {capital_lev_cap}x",
            })
            logger.info("强制降杠杆 %s: %dx → %dx (资金层级限制)", symbol, leverage, capital_lev_cap)
            _console.print(
                f"    [yellow]{symbol}: 杠杆 {leverage}x → {capital_lev_cap}x (层级限制)[/yellow]"
            )
            decision = {**decision, "leverage": capital_lev_cap}
            leverage = capital_lev_cap

    # ── P3: 盈亏比 RR 检查 ──
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
            return _reject(reason)
        hard_checks.append({"rule": "risk_reward", "passed": True})

    # ── 爆仓距离检查 ──
    if entry_range and len(entry_range) == 2 and entry_range[0]:
        entry_mid = (entry_range[0] + entry_range[1]) / 2
        liq_price = calc_liquidation_price(entry_mid, leverage, action)
        liq_dist = calc_liquidation_distance(current_price or entry_mid, liq_price)
        min_liq_dist = max(15, 30 - (5 - leverage) * 3)
        if liq_dist < min_liq_dist:
            reason = f"爆仓距离 {liq_dist:.1f}% < {min_liq_dist:.0f}% ({leverage}x杠杆)"
            hard_checks.append({"rule": "liquidation_distance", "passed": False, "reason": reason})
            return _reject(reason)
        hard_checks.append({"rule": "liquidation_distance", "passed": True})

    # ── 相关性检查 ──
    try:
        from cryptobot.risk.correlation import (
            calc_correlation_matrix,
            check_portfolio_correlation,
        )
        corr_positions = [
            {
                "symbol": p.get("pair", "").replace("/", "").replace(":USDT", ""),
                "is_short": p.get("is_short", False),
            }
            for p in positions
        ]
        if corr_positions:
            corr_symbols = list({p["symbol"] for p in corr_positions} | {symbol})
            corr_matrix = calc_correlation_matrix(corr_symbols)
            corr_check = check_portfolio_correlation(
                corr_positions, {"symbol": symbol, "action": action}, corr_matrix,
            )
            if not corr_check.passed:
                for v in corr_check.violations:
                    reason = f"相关性风控: {v}"
                    hard_checks.append({"rule": "correlation", "passed": False, "reason": reason})
                return _reject(hard_checks[-1]["reason"])
            hard_checks.append({"rule": "correlation", "passed": True})
    except Exception as e:
        logger.warning("相关性检查跳过: %s", e)

    # ── 币种分级检查 ──
    try:
        from cryptobot.risk.symbol_profile import get_symbol_grade
        sym_grade = get_symbol_grade(symbol)
        if sym_grade is not None:
            if sym_grade.blocked:
                reason = f"币种 {symbol} 为 D 级，禁止交易"
                hard_checks.append({"rule": "symbol_grade", "passed": False, "reason": reason})
                return _reject(reason)
            if sym_grade.min_confidence > 0:
                base_conf = risk_cfg.get("confidence_floor", 60)
                required = base_conf + sym_grade.min_confidence
                if decision_conf < required:
                    reason = f"置信度 {decision_conf} < 币种 {sym_grade.grade} 级阈值 {required}"
                    hard_checks.append({
                        "rule": "symbol_grade_confidence", "passed": False, "reason": reason,
                    })
                    return _reject(reason)
            if sym_grade.recommended_leverage < leverage:
                logger.info(
                    "币种分级降杠杆 %s: %dx → %dx (%s级)",
                    symbol, leverage, sym_grade.recommended_leverage, sym_grade.grade,
                )
                decision = {**decision, "leverage": sym_grade.recommended_leverage}
                leverage = sym_grade.recommended_leverage
            hard_checks.append({"rule": "symbol_grade", "passed": True})
    except Exception as e:
        logger.warning("币种分级检查跳过: %s", e)

    # 硬规则全部通过
    return {
        "passed": True,
        "decision": decision,
        "hard_result": {"symbol": symbol, "passed": True, "checks": hard_checks},
        "rejected": None,
    }


def _build_ai_task(
    decision: dict, risk_cfg: dict, existing_signals: list,
    positions: list, leverage: int,
    portfolio_ctx: str, perf_ctx: str, regime_ctx: str,
    capital_ctx: str, confidence_ctx: str, macro_ctx: str,
    capital_risk_addon: str, strategy_risk_addon: str,
) -> tuple[dict, str]:
    """构建 AI 风控审核任务"""
    from cryptobot.config import get_pair_config
    from cryptobot.risk.liquidation_calc import calc_liquidation_price, calc_liquidation_distance

    symbol = decision.get("symbol", "")
    action = decision.get("action", "")
    entry_range = decision.get("entry_price_range", [])
    current_price = decision.get("current_price", 0)
    pair_cfg = get_pair_config(symbol) or {}

    liq_info = ""
    if entry_range and len(entry_range) == 2 and entry_range[0]:
        entry_mid = (entry_range[0] + entry_range[1]) / 2
        liq_price = calc_liquidation_price(entry_mid, leverage, action)
        liq_dist = calc_liquidation_distance(current_price or entry_mid, liq_price)
        liq_info = f"爆仓价: {liq_price:.2f}, 爆仓距离: {liq_dist:.1f}%"

    existing = [s for s in existing_signals if s["symbol"] == symbol]

    task = {
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
        "system_prompt": RISK_MANAGER + capital_risk_addon + strategy_risk_addon,
        "json_schema": RISK_SCHEMA,
    }
    return task, liq_info


def _merge_ai_result(
    decision: dict, result: dict | str, analyses: dict, regime: dict,
    account_balance: float, positions: list,
) -> dict:
    """合并 AI 审核结果

    Returns:
        {"approved": bool, "signal": dict|None, "ai_result": dict, "rejected": dict|None, "error": str|None}
    """
    from cryptobot.config import get_pair_config

    symbol = decision.get("symbol", "")
    decision = {**decision}

    if not isinstance(result, dict) or "error" in result:
        err = result.get("error", str(result)) if isinstance(result, dict) else str(result)
        return {"approved": False, "signal": None, "ai_result": None, "rejected": None, "error": f"risk_{symbol}: {err}"}

    _ADJUSTABLE_FIELDS = {
        "leverage", "stop_loss", "take_profit",
        "position_size_pct", "entry_price_range",
    }
    if result.get("decision") in ("approved", "modified"):
        # R6-C2: modified 分支校验 + 不可变更新
        if result.get("decision") == "modified" and result.get("adjustments"):
            adjustments = {}
            pair_cfg = get_pair_config(symbol) or {}
            max_lev = pair_cfg.get("leverage_range", [1, 5])[1]
            for k, v in result["adjustments"].items():
                if k not in _ADJUSTABLE_FIELDS:
                    continue
                if k == "stop_loss" and v:
                    entry_range = decision.get("entry_price_range", [])
                    if entry_range and len(entry_range) == 2 and entry_range[0]:
                        entry_mid = (entry_range[0] + entry_range[1]) / 2
                        sl_dist = abs(entry_mid - v) / entry_mid * 100
                        if sl_dist < _SL_MIN_PCT or sl_dist > _SL_MAX_PCT:
                            logger.warning(
                                "AI 修改 %s stop_loss 距离 %.1f%% 超出 [%.1f, %.1f]%%，跳过",
                                symbol, sl_dist, _SL_MIN_PCT, _SL_MAX_PCT,
                            )
                            continue
                if k == "leverage" and v:
                    v = min(int(v), max_lev)
                adjustments[k] = v
            decision = {**decision, **adjustments}

        votes = _extract_votes(analyses, symbol)
        regime_name = regime.get("regime", "")
        sig = _decision_to_signal(
            decision, result, account_balance,
            analyst_votes=votes, regime=regime_name,
            positions=positions,
        )
        if sig is None:
            reason = "仓位计算为 0，拒绝信号"
            logger.info("仓位为 0 拒绝: %s", symbol)
            return {
                "approved": False, "signal": None,
                "ai_result": {"symbol": symbol, "verdict": "rejected", "reasoning": reason},
                "rejected": {"symbol": symbol, "reason": reason},
                "error": None,
            }
        if result.get("decision") == "modified" and result.get("adjustments"):
            sig = {**sig, "risk_review_changes": result["adjustments"]}
        return {
            "approved": True, "signal": sig,
            "ai_result": {
                "symbol": symbol,
                "verdict": result.get("decision", "approved"),
                "reasoning": result.get("reasoning", ""),
                "modifications": result.get("adjustments", {}),
            },
            "rejected": None, "error": None,
        }

    # AI 拒绝
    reason = result.get("reasoning", "未知")
    logger.info("风控拒绝: %s, 原因: %s", symbol, reason)
    from cryptobot.notify import notify_risk_rejected
    notify_risk_rejected(symbol, reason[:200])
    return {
        "approved": False, "signal": None,
        "ai_result": {"symbol": symbol, "verdict": "rejected", "reasoning": reason},
        "rejected": {"symbol": symbol, "reason": f"AI审核拒绝: {reason}"},
        "error": None,
    }


def risk_review(state: WorkflowState) -> dict:
    """风控审核每个交易决策"""
    decisions = state.get("decisions", [])
    actionable = [d for d in decisions if d.get("action") != "no_trade"]
    _stage(6, f"风控审核 — {len(actionable)} 个决策")
    t0 = time.time()
    errors = list(state.get("errors", []))

    from cryptobot.config import load_settings
    from cryptobot.signal.bridge import read_signals

    settings = load_settings()
    risk_cfg = settings.get("risk", {})
    existing_signals = read_signals()

    # 获取账户余额和持仓（用于仓位计算和硬性规则检查）
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.capital_strategy import get_balance_from_freqtrade

    account_balance = get_balance_from_freqtrade()
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

    # ── C1.5: 月度亏损熔断检查 ──
    from cryptobot.risk.monthly_circuit_breaker import check_circuit_breaker

    cb_state = check_circuit_breaker()
    if cb_state.action == "suspend":
        logger.warning("月度熔断触发: %s", cb_state.reason)
        _console.print(f"    [red]月度熔断: {cb_state.reason}[/red]")
        from cryptobot.notify import send_message
        send_message(f"🚫 *月度熔断触发*\n\n{cb_state.reason}")
        return {"approved_signals": [], "errors": errors}
    if cb_state.action == "reduce":
        logger.info("月度熔断降仓: %s", cb_state.reason)
        _console.print(f"    [yellow]月度熔断降仓: {cb_state.reason}[/yellow]")

    analyses = state.get("analyses", {})
    approved = []
    hard_rule_results = []
    ai_review_results = []
    rejected_signals = []

    # 预加载交易记录，避免循环内重复 I/O
    _preloaded_records = None
    try:
        from cryptobot.journal.storage import get_all_records
        _preloaded_records = get_all_records()
    except Exception as e:
        logger.warning("预加载交易记录失败: %s", e)

    # 构建所有风控审核任务
    all_tasks = []
    task_decisions = []

    for decision in decisions:
        if decision.get("action") == "no_trade":
            continue

        hr = _apply_hard_rules(
            decision, regime, risk_cfg, settings, cb_state, merged_params,
            account_balance, positions, approved, _preloaded_records,
            total_used, long_used, short_used,
        )
        hard_rule_results.append(hr["hard_result"])
        if not hr["passed"]:
            rejected_signals.append(hr["rejected"])
            continue

        # 硬规则可能修改 decision/leverage
        decision = hr["decision"]
        leverage = decision.get("leverage", 3)
        symbol = decision.get("symbol", "")
        action = decision.get("action", "")

        # R6-H1: 通过后累加仓位占比
        pct = decision.get("position_size_pct", 10)
        position_size_pct_usdt = account_balance * pct / 100 if account_balance > 0 else 0
        total_used += position_size_pct_usdt
        if action == "short":
            short_used += position_size_pct_usdt
        else:
            long_used += position_size_pct_usdt

        task, liq_info = _build_ai_task(
            decision, risk_cfg, existing_signals, positions, leverage,
            portfolio_ctx, perf_ctx, regime_ctx, capital_ctx,
            confidence_ctx, macro_ctx, capital_risk_addon,
            _strategy_risk_addon,
        )
        all_tasks.append(task)
        task_decisions.append(decision)
        _console.print(f"    审核 {symbol} ({action})...")

    # 并行风控审核
    if all_tasks:
        results = call_claude_parallel(all_tasks)
        for i, result in enumerate(results):
            mr = _merge_ai_result(
                task_decisions[i], result, analyses, regime,
                account_balance, positions,
            )
            if mr.get("error"):
                errors.append(mr["error"])
            elif mr.get("approved"):
                sig = mr["signal"]
                approved.append(sig)
                ai_review_results.append(mr["ai_result"])
                # C2: 用精确 margin 替代预估
                margin = sig.get("position_size_usdt", 0)
                total_used += margin
                symbol = sig.get("symbol", "")
                if sig.get("action") == "short":
                    short_used += margin
                else:
                    long_used += margin
                logger.info("风控通过: %s %s", symbol, sig.get("action"))
            else:
                ai_review_results.append(mr["ai_result"])
                rejected_signals.append(mr["rejected"])

    risk_details = {
        "hard_rule_results": hard_rule_results,
        "ai_review_results": ai_review_results,
        "rejected_signals": rejected_signals,
    }

    _console.print(f"    完成: {len(approved)} 通过 / {len(task_decisions) - len(approved)} 拒绝, "
                    f"耗时 {time.time() - t0:.0f}s")
    return {"approved_signals": approved, "risk_details": risk_details, "errors": errors}
