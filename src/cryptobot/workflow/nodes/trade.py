"""Node: trade — 所有币种的交易决策并行"""

import json
import logging
import time

from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.prompts import TRADER, TRADE_SCHEMA
from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.strategy_router import (
    StrategyRoute,
    route_strategy,
    route_strategies,
)
from cryptobot.workflow.utils import _stage, _build_portfolio_context

logger = logging.getLogger(__name__)
_console = Console()


def trade(state: WorkflowState) -> dict:
    """所有币种的交易决策并行（5 个 sonnet，5 并发）"""
    research_data = state.get("research", {})
    _stage(5, f"交易决策 — {len(research_data)} 个 sonnet")
    t0 = time.time()
    analyses = state.get("analyses", {})
    market_data = state.get("market_data", {})
    errors = list(state.get("errors", []))

    from cryptobot.config import get_pair_config

    # 获取持仓和账户上下文
    portfolio_ctx = _build_portfolio_context()

    # 获取历史绩效摘要
    perf_ctx = ""
    try:
        from cryptobot.journal.analytics import build_performance_summary
        perf_ctx = build_performance_summary(30)
    except Exception as e:
        logger.warning("绩效摘要生成失败: %s", e)

    # 分析师权重上下文
    weights_ctx = ""
    try:
        from cryptobot.journal.analyst_weights import build_weights_context
        weights_ctx = build_weights_context(30)
    except Exception as e:
        logger.warning("分析师权重生成失败: %s", e)

    # 市场状态上下文
    regime = state.get("market_regime", {})
    regime_ctx = ""
    if regime:
        regime_ctx = (
            f"### 当前市场状态\n"
            f"- 状态: {regime.get('regime', 'unknown')}\n"
            f"- {regime.get('description', '')}\n"
            f"- 建议最低置信度: {regime.get('params', {}).get('min_confidence', 60)}\n"
            f"- 建议最大杠杆: {regime.get('params', {}).get('max_leverage', 5)}x\n\n"
        )

    # Regime prompt addon (trade 角色)
    regime_trader_addon = ""
    try:
        from cryptobot.evolution.regime_prompts import get_regime_addon
        regime_name = regime.get("regime", "") if regime else ""
        regime_trader_addon = get_regime_addon(regime_name, "TRADER")
    except Exception as e:
        logger.warning("Regime addon 加载失败: %s", e)

    # 置信度校准上下文
    confidence_ctx = ""
    try:
        from cryptobot.journal.confidence_tuner import build_threshold_context
        confidence_ctx = build_threshold_context(regime, 30)
    except Exception as e:
        logger.warning("置信度校准失败: %s", e)

    # 资金层级上下文
    capital_tier = state.get("capital_tier", {})
    capital_ctx = ""
    capital_trader_addon = ""
    merged_params = {}
    if capital_tier:
        tier_name = capital_tier.get("tier", "medium")
        tier_balance = capital_tier.get("balance", 0)
        tier_params = capital_tier.get("params", {})

        # 计算回撤杠杆缩放因子
        from cryptobot.capital_strategy import merge_regime_capital_params, calc_drawdown_factor
        dd_info = calc_drawdown_factor(lookback_days=7)
        drawdown_factor = dd_info.get("leverage_factor", 1.0)
        if drawdown_factor < 1.0:
            logger.info(
                "回撤感知: %.1f%% 回撤, 杠杆因子 %.2f (%s)",
                dd_info["drawdown_pct"], drawdown_factor, dd_info["tier"],
            )

        # 合并 regime + capital 参数
        merged_params = merge_regime_capital_params(
            regime.get("params", {}), tier_params, drawdown_factor=drawdown_factor,
        )

        capital_ctx = (
            f"### 资金层级\n"
            f"- 层级: {tier_name} (余额 ${tier_balance:.0f})\n"
            f"- 合并后最低置信度: {merged_params.get('min_confidence', 55)}\n"
            f"- 合并后最大杠杆: {merged_params.get('max_leverage', 5)}x\n"
            f"- 止盈风格: {merged_params.get('take_profit_style', 'standard')}\n\n"
        )

        try:
            from cryptobot.evolution.capital_prompts import get_capital_addon
            capital_trader_addon = get_capital_addon(tier_name, "TRADER")
        except Exception:
            pass

    # O22: 获取币种级绩效数据
    perf_feedback = state.get("perf_feedback", {})
    by_symbol_perf = perf_feedback.get("by_symbol", {})

    all_tasks = []
    task_meta = []  # (symbol, current_price)
    decisions = []  # 提前初始化，observe/mean_reversion 直接写入
    strategy_routes = {}

    for symbol in research_data:
        # -- P13.6: 策略路由 --
        regime_info = regime if regime else {}
        route = route_strategy(
            regime=regime_info.get("regime", ""),
            regime_confidence=regime_info.get("regime_confidence", 0.5),
            hurst=regime_info.get("hurst_exponent", 0.5),
            volatility_state=regime_info.get("volatility_state", "normal"),
        )
        strategy_routes[symbol] = route

        data = market_data.get(symbol, {})
        current_price = (data.get("tech") or {}).get("latest_close", 0)

        # P13.10: 多策略路由 -- 获取辅助策略
        try:
            routes = route_strategies(
                regime=regime_info.get("regime", ""),
                regime_confidence=regime_info.get("regime_confidence", 0.5),
                hurst=regime_info.get("hurst_exponent", 0.5),
                volatility_state=regime_info.get("volatility_state", "normal"),
            )
            for aux in routes[1:]:
                if aux.strategy == "grid" and aux.weight > 0:
                    _update_virtual_grid(symbol, current_price)
                elif aux.strategy == "funding_arb" and aux.weight > 0:
                    _update_virtual_funding(symbol)
        except Exception as e:
            logger.warning("多策略路由失败 %s: %s", symbol, e)

        # observe -> 直接 no_trade
        if route.strategy == "observe":
            decisions.append({
                "symbol": symbol,
                "action": "no_trade",
                "confidence": 0,
                "reasoning": route.reason,
                "current_price": current_price,
            })
            _console.print(f"    {symbol}: 观望 ({route.reason})")
            continue

        # mean_reversion -> 规则化信号，不走 LLM
        if route.strategy == "mean_reversion":
            try:
                from cryptobot.strategy.mean_reversion import (
                    check_bb_entry,
                    signal_to_dict,
                )

                tech = data.get("tech", {})
                latest = {}
                if tech:
                    latest["close"] = tech.get("latest_close", 0)
                    bb = tech.get("bollinger", {})
                    latest["bb_upper"] = bb.get("upper", 0)
                    latest["bb_lower"] = bb.get("lower", 0)
                    latest["bb_mid"] = bb.get("middle", 0)
                    latest["rsi_14"] = tech.get("rsi", 50)
                    latest["atr_14"] = tech.get("atr", 0)
                    latest["volume_ratio"] = tech.get("volume_ratio", 1.0)

                mr_sig = check_bb_entry(symbol, {"latest": latest})
                if mr_sig:
                    sig_dict = signal_to_dict(mr_sig)
                    sig_dict["current_price"] = current_price
                    decisions.append(sig_dict)
                    _console.print(
                        f"    {symbol}: 均值回归 {mr_sig.action}"
                        f" conf={mr_sig.confidence}"
                    )
                else:
                    decisions.append({
                        "symbol": symbol,
                        "action": "no_trade",
                        "confidence": 0,
                        "reasoning": "均值回归无入场信号",
                        "current_price": current_price,
                    })
                    _console.print(f"    {symbol}: 均值回归无信号")
            except ImportError:
                logger.warning("mean_reversion 模块未就绪，fallback 到 ai_trend")
                route = StrategyRoute(
                    strategy="ai_trend", weight=0.5,
                    reason="均值回归模块未就绪，fallback", params={},
                )
                strategy_routes[symbol] = route
            except Exception as e:
                logger.warning("均值回归策略失败 %s: %s", symbol, e)
                route = StrategyRoute(
                    strategy="ai_trend", weight=0.5,
                    reason="均值回归失败，fallback", params={},
                )
                strategy_routes[symbol] = route
            else:
                continue  # 均值回归已处理，跳过 LLM

        # ai_trend -> 继续现有 LLM 决策流
        bull = research_data[symbol].get("bull", {})
        bear = research_data[symbol].get("bear", {})
        analysis = analyses.get(symbol, {})
        pair_cfg = get_pair_config(symbol) or {}

        pair_max_lev = pair_cfg.get("leverage_range", [1, 3])[1]
        # 资金层级限制杠杆: 取 pair_cfg 和 merged_params 中更低值
        capital_lev_cap = merged_params.get("max_leverage", 5) if merged_params else 5
        max_leverage = min(pair_max_lev, capital_lev_cap)

        # 构建增强 system prompt: 基础 + prompt version addon + regime addon + capital addon
        trader_system = TRADER
        try:
            from cryptobot.evolution.prompt_manager import get_prompt_addon
            version_addon = get_prompt_addon("TRADER")
            if version_addon:
                trader_system += "\n" + version_addon
        except Exception:
            pass
        if regime_trader_addon:
            trader_system += regime_trader_addon
        if capital_trader_addon:
            trader_system += capital_trader_addon

        # 第5层: strategy advisor addon
        try:
            from cryptobot.evolution.strategy_advisor import get_strategy_addon
            strategy_addon = get_strategy_addon("trader")
            if strategy_addon:
                trader_system += strategy_addon
        except Exception:
            pass

        # O13: 分析师加权一致性评分
        analyst_weight_map = {}
        try:
            from cryptobot.journal.analyst_weights import load_weights
            analyst_weight_map = load_weights()
        except Exception:
            pass

        weighted_bull = 0.0
        weighted_bear = 0.0
        total_weight = 0.0
        for role, a in analysis.items():
            if not isinstance(a, dict) or "direction" not in a:
                continue
            w = analyst_weight_map.get(role, 1.0)
            total_weight += w
            if a["direction"] == "bullish":
                weighted_bull += w
            elif a["direction"] == "bearish":
                weighted_bear += w
        consistency = max(weighted_bull, weighted_bear) / total_weight if total_weight > 0 else 0
        consistency_note = ""
        if consistency < 0.5:
            consistency_note = (
                "\n\n⚠️ 分析师意见严重分歧，建议 confidence ≤ 65 或 no_trade"
            )
        elif consistency < 0.75:
            consistency_note = "\n\n⚠️ 分析师意见中度分歧，建议 confidence ≤ 75"

        # O22: 币种级历史绩效注入
        symbol_perf_ctx = ""
        sp = by_symbol_perf.get(symbol, {})
        if sp.get("count", 0) >= 3:
            symbol_perf_ctx = (
                f"### {symbol} 历史绩效 (30天)\n"
                f"- 交易次数: {sp['count']}\n"
                f"- 胜率: {sp.get('win_rate', 0):.0%}\n"
                f"- 平均盈亏: {sp.get('avg_pnl_pct', 0):+.1f}%\n\n"
            )

        all_tasks.append({
            "prompt": (
                f"## {symbol} 交易决策\n\n"
                f"当前价格: {current_price}\n"
                f"最大杠杆: {max_leverage}x\n\n"
                f"{portfolio_ctx}"
                f"{perf_ctx}"
                f"{weights_ctx}"
                f"{regime_ctx}"
                f"{capital_ctx}"
                f"{confidence_ctx}"
                f"{symbol_perf_ctx}"
                f"### 看多研究员观点\n{json.dumps(bull, ensure_ascii=False, indent=2)}\n\n"
                f"### 看空研究员观点\n{json.dumps(bear, ensure_ascii=False, indent=2)}\n\n"
                f"### 分析师数据\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
                f"{consistency_note}\n请做出交易决策。"
            ),
            "model": "sonnet",
            "role": "trader",
            "system_prompt": trader_system,
            "json_schema": TRADE_SCHEMA,
        })
        task_meta.append((symbol, current_price))

    # 检查竞赛模式
    competition_cfg = None
    try:
        from cryptobot.evolution.model_competition import (
            get_competition_config, run_competition, select_winner,
        )
        competition_cfg = get_competition_config()
    except Exception as e:
        logger.warning("竞赛模式加载失败: %s", e)

    if competition_cfg:
        # 竞赛模式: 多模型并行
        logger.info("竞赛模式启用: %d 模型", len(competition_cfg["models"]))
        multi_results = run_competition(all_tasks, competition_cfg["models"])
        results = []
        for i, mr in enumerate(multi_results):
            winner = select_winner(mr, competition_cfg["strategy"], task_meta[i][0])
            result = winner["result"]
            if isinstance(result, dict):
                result["model_id"] = winner["model_id"]
            results.append(result)
    else:
        results = call_claude_parallel(all_tasks)

    for i, result in enumerate(results):
        symbol, current_price = task_meta[i]
        if isinstance(result, dict) and "error" not in result:
            # 创建副本，不修改原始 result
            corrected = {**result}

            # C6 兜底: long/short 必须有 stop_loss，否则强制 no_trade
            if corrected.get("action") in ("long", "short") and corrected.get("stop_loss") is None:
                logger.warning("%s: action=%s 但无 stop_loss，强制改为 no_trade", symbol, corrected["action"])
                corrected = {
                    **corrected,
                    "action": "no_trade",
                    "reasoning": corrected.get("reasoning", "") + " [系统: 缺少止损，已拦截]",
                }
            # P2: 止损方向验证
            if corrected.get("action") == "long" and corrected.get("stop_loss") is not None:
                if corrected["stop_loss"] >= current_price and current_price > 0:
                    logger.warning(
                        "%s: long 止损 %.2f >= 当前价 %.2f, 方向错误",
                        symbol, corrected["stop_loss"], current_price,
                    )
                    corrected = {
                        **corrected,
                        "action": "no_trade",
                        "reasoning": corrected.get("reasoning", "") + " [系统: 止损方向错误]",
                    }
            elif corrected.get("action") == "short" and corrected.get("stop_loss") is not None:
                if corrected["stop_loss"] <= current_price and current_price > 0:
                    logger.warning(
                        "%s: short 止损 %.2f <= 当前价 %.2f, 方向错误",
                        symbol, corrected["stop_loss"], current_price,
                    )
                    corrected = {
                        **corrected,
                        "action": "no_trade",
                        "reasoning": corrected.get("reasoning", "") + " [系统: 止损方向错误]",
                    }

            # P2: 止损距离验证 (0.5% - 15%)
            if (
                corrected.get("action") in ("long", "short")
                and corrected.get("stop_loss") is not None
                and current_price > 0
            ):
                sl_dist = abs(corrected["stop_loss"] - current_price) / current_price * 100
                if sl_dist < 0.5 or sl_dist > 15:
                    logger.warning(
                        "%s: 止损距离 %.1f%% 不在合理范围 0.5-15%%", symbol, sl_dist,
                    )
                    corrected = {
                        **corrected,
                        "action": "no_trade",
                        "reasoning": (
                            corrected.get("reasoning", "")
                            + f" [系统: 止损距离 {sl_dist:.1f}% 超出合理范围 0.5-15%]"
                        ),
                    }

            corrected = {**corrected, "symbol": symbol, "current_price": current_price}
            decisions.append(corrected)
        else:
            err = result.get("error", "非 JSON 响应") if isinstance(result, dict) else "非 JSON 响应"
            errors.append(f"trade_{symbol}: {err}")

    actions = [f"{d['symbol']}={d.get('action', '?')}" for d in decisions]
    _console.print(f"    完成: {', '.join(actions) or '无交易'}, 耗时 {time.time() - t0:.0f}s")
    return {
        "decisions": decisions,
        "portfolio_context": portfolio_ctx,
        "errors": errors,
        "strategy_routes": strategy_routes,
    }


def _update_virtual_grid(symbol: str, current_price: float) -> None:
    """更新网格虚拟盘（静默失败）"""
    try:
        from cryptobot.strategy.grid_trading import run_grid_check
        run_grid_check(symbol)
    except Exception as e:
        logger.debug("网格更新失败 %s: %s", symbol, e)


def _update_virtual_funding(symbol: str) -> None:
    """更新资金费率套利虚拟盘（静默失败）"""
    try:
        from cryptobot.strategy.funding_arb import scan_funding_opportunities
        scan_funding_opportunities()
    except Exception as e:
        logger.debug("资金费率套利更新失败 %s: %s", symbol, e)
