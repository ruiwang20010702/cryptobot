"""独立流程: 持仓复审

对现有持仓进行 AI 重新评估，决定是否调整止损或平仓。
"""

import json
import logging

from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.utils import fetch_market_data

logger = logging.getLogger(__name__)
_console = Console()


def collect_data_for_symbols(symbols: list[str]) -> dict:
    """仅为指定币种采集数据（持仓复审专用）

    Returns:
        {market_data: {...}, fear_greed: {...}, market_overview: {...}, global_news: {...}}
    """
    _console.print(f"[cyan]采集 {len(symbols)} 个持仓币种数据...[/cyan]")

    market_data, fear_greed, market_overview, global_news, _stablecoin, _macro, errors = (
        fetch_market_data(symbols)
    )

    if errors:
        for err in errors:
            logger.warning("数据采集: %s", err)

    # O28: 复审时也检测 regime
    regime = {}
    try:
        from cryptobot.workflow.nodes.collect import _detect_market_regime
        regime = _detect_market_regime(market_data, fear_greed)
    except Exception as e:
        logger.warning("复审 regime 检测失败: %s", e)

    return {
        "market_data": market_data,
        "fear_greed": fear_greed,
        "market_overview": market_overview,
        "global_news": global_news,
        "market_regime": regime,
    }


def re_review(positions: list[dict], state: dict) -> list[dict]:
    """对现有持仓进行 AI 重新评估

    Args:
        positions: Freqtrade /status 返回的持仓列表
        state: collect_data_for_symbols 返回的结构化数据
               {market_data, fear_greed, market_overview, global_news}

    Returns:
        评估建议列表 [{symbol, decision, new_stop_loss, reasoning}, ...]
    """
    from cryptobot.workflow.prompts import RE_REVIEWER, RE_REVIEW_SCHEMA, ANALYST_SCHEMA
    from cryptobot.workflow.prompts import (
        TECHNICAL_ANALYST, ONCHAIN_ANALYST, SENTIMENT_ANALYST, FUNDAMENTAL_ANALYST,
    )

    if not positions:
        return []

    market_data = state.get("market_data", {})
    fear_greed = state.get("fear_greed", {})
    market_overview = state.get("market_overview", {})
    global_news = state.get("global_news", {})

    _console.print(f"[cyan]持仓复审 — {len(positions)} 个持仓[/cyan]")

    # Step 1: 为每个持仓币种运行 4 位分析师
    analyses = {}
    all_tasks = []
    task_index = []

    for pos in positions:
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        data = market_data.get(symbol, {})

        if not data.get("tech"):
            continue

        for analyst_type, sys_prompt, data_key in [
            ("technical", TECHNICAL_ANALYST, {
                "tech_indicators": data.get("tech"),
                "multi_timeframe": data.get("multi_tf"),
                "volume_analysis": data.get("volume_analysis"),
                "support_resistance": data.get("support_resistance"),
            }),
            ("onchain", ONCHAIN_ANALYST, {
                "derivatives": data.get("crypto"),
                "liquidation": data.get("liquidation"),
                "coinglass_liq": data.get("coinglass_liq"),
                "open_interest": data.get("open_interest"),
                "options_sentiment": data.get("options_sentiment"),
                "whale_activity": data.get("whale_activity"),
            }),
            ("sentiment", SENTIMENT_ANALYST, {
                "fear_greed": fear_greed,
                "market_overview": market_overview,
                "global_news": global_news,
            }),
            ("fundamental", FUNDAMENTAL_ANALYST, {
                "coin_info": data.get("coin_info"),
                "btc_correlation": data.get("btc_correlation"),
                "coin_news": data.get("coin_news"),
            }),
        ]:
            task_index.append((symbol, analyst_type))
            all_tasks.append({
                "prompt": f"分析 {symbol} 的最新数据:\n{json.dumps(data_key, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "system_prompt": sys_prompt,
                "json_schema": ANALYST_SCHEMA,
            })

    if all_tasks:
        analyst_results = call_claude_parallel(all_tasks)
        for i, result in enumerate(analyst_results):
            symbol, analyst_type = task_index[i]
            if symbol not in analyses:
                analyses[symbol] = {}
            analyses[symbol][analyst_type] = result

    # O28: 构建 regime + 绩效上下文
    regime = state.get("market_regime", {})
    regime_ctx = ""
    if regime:
        regime_ctx = (
            f"### 市场状态\n"
            f"- 状态: {regime.get('regime', 'unknown')}\n"
            f"- {regime.get('description', '')}\n\n"
        )

    perf_ctx = ""
    try:
        from cryptobot.journal.analytics import build_performance_summary
        perf_ctx = build_performance_summary(30)
    except Exception:
        pass

    # Step 2: 对每个持仓运行复审
    review_tasks = []
    review_positions = []

    for pos in positions:
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        analysis = analyses.get(symbol, {})

        if not analysis:
            continue

        review_tasks.append({
            "prompt": (
                f"## 持仓复审: {symbol}\n\n"
                f"{regime_ctx}"
                f"{perf_ctx}"
                f"### 当前持仓\n"
                f"- 方向: {'空' if pos.get('is_short') else '多'}\n"
                f"- 入场价: {pos.get('open_rate')}\n"
                f"- 当前价: {pos.get('current_rate')}\n"
                f"- 盈亏: {pos.get('profit_pct', 0):.2%}\n"
                f"- 杠杆: {pos.get('leverage')}x\n"
                f"- 当前止损: {pos.get('stop_loss_abs')}\n"
                f"- 持仓时长: {pos.get('trade_duration')}\n\n"
                f"### 最新分析师报告\n"
                f"{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
                f"请评估此持仓是否需要调整。"
            ),
            "model": "sonnet",
            "system_prompt": RE_REVIEWER,
            "json_schema": RE_REVIEW_SCHEMA,
        })
        review_positions.append(pos)

    if not review_tasks:
        return []

    _console.print(f"    运行 {len(review_tasks)} 个复审...")
    review_results = call_claude_parallel(review_tasks)

    suggestions = []
    for i, result in enumerate(review_results):
        pos = review_positions[i]
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")

        if isinstance(result, dict) and "error" not in result:
            decision = result.get("decision", "hold")

            # P10: close_position 决策写入平仓信号
            if decision == "close_position":
                try:
                    from datetime import datetime, timezone
                    from cryptobot.signal.bridge import write_signal
                    action = "close_short" if pos.get("is_short") else "close_long"
                    close_sig = {
                        "symbol": symbol,
                        "action": action,
                        "reasoning": result.get("reasoning", "复审建议平仓"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    write_signal(close_sig)
                    logger.info("复审平仓信号已写入: %s %s", symbol, action)
                except Exception as e:
                    logger.error("复审平仓信号写入失败: %s", e)

            suggestions.append({
                "symbol": symbol,
                "pair": pair,
                "decision": decision,
                "new_stop_loss": result.get("new_stop_loss"),
                "reasoning": result.get("reasoning", ""),
                "risk_level": result.get("risk_level", "medium"),
            })
        else:
            logger.error("复审失败 %s: %s", symbol, result)

    return suggestions
