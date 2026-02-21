"""Node: analyze — 所有币种 × 4 分析师全并行"""

import json
import logging
import time

from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.prompts import (
    TECHNICAL_ANALYST, ONCHAIN_ANALYST, SENTIMENT_ANALYST, FUNDAMENTAL_ANALYST,
    ANALYST_SCHEMA,
)
from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage

logger = logging.getLogger(__name__)
_console = Console()


def analyze(state: WorkflowState) -> dict:
    """所有币种的 4 位分析师并行（5币 × 4 = 20 个 haiku，5 并发）"""
    screened = state.get("screened_symbols", [])
    n_tasks = len(screened) * 4
    _stage(3, f"分析师分析 — {n_tasks} 个 haiku ({len(screened)} 币 x 4 分析师)")
    t0 = time.time()
    market_data = state.get("market_data", {})
    fear_greed = state.get("fear_greed", {})
    market_overview = state.get("market_overview", {})
    global_news = state.get("global_news", {})
    stablecoin_flows = state.get("stablecoin_flows", {})
    errors = list(state.get("errors", []))

    # 打平所有任务: [(symbol, analyst_type, task_dict), ...]
    all_tasks = []
    task_index = []  # 记录每个任务对应的 (symbol, analyst_type)

    for symbol in screened:
        data = market_data.get(symbol, {})
        tech = data.get("tech", {})
        crypto = data.get("crypto", {})
        coin_info = data.get("coin_info", {})
        multi_tf = data.get("multi_tf", {})
        volume_analysis = data.get("volume_analysis", {})
        support_resistance = data.get("support_resistance", {})
        liquidation = data.get("liquidation", {})
        btc_correlation = data.get("btc_correlation", {})
        coin_news = data.get("coin_news", {})
        dilution_risk = data.get("dilution_risk", {})

        orderbook = data.get("orderbook", {})
        coinglass_liq = data.get("coinglass_liq", {})
        exchange_reserve = data.get("exchange_reserve", {})

        # 技术分析师: tech + multi_tf + volume_analysis + support_resistance + orderbook
        tech_data = {
            "tech_indicators": tech,
            "multi_timeframe": multi_tf,
            "volume_analysis": volume_analysis,
            "support_resistance": support_resistance,
            "orderbook": orderbook,
        }
        options_sentiment = data.get("options_sentiment", {})

        whale_activity = data.get("whale_activity", {})

        # 链上分析师: crypto + liquidation + coinglass_liq + exchange_reserve + options + whale
        onchain_data = {
            "derivatives": crypto,
            "liquidation": liquidation,
            "coinglass_liquidation": coinglass_liq,
            "exchange_reserve": exchange_reserve,
            "options_sentiment": options_sentiment,
            "whale_activity": whale_activity,
        }
        # 情绪分析师: fear_greed + market_overview + global_news + stablecoin_flows + macro_events + dxy
        macro_events = state.get("macro_events", {})
        dxy_data = state.get("dxy_data", {})
        sentiment_data = {
            "fear_greed": fear_greed,
            "market_overview": market_overview,
            "global_news": global_news,
            "stablecoin_flows": stablecoin_flows,
            "macro_events": macro_events,
            "dxy": dxy_data,
        }
        defi_tvl = data.get("defi_tvl", {})

        # 基本面分析师: coin_info + btc_correlation + coin_news + dilution + defi_tvl
        fundamental_data = {
            "coin_info": coin_info,
            "btc_correlation": btc_correlation,
            "coin_news": coin_news,
            "dilution_risk": dilution_risk,
            "defi_tvl": defi_tvl,
        }

        tasks_for_symbol = [
            ("technical", {
                "prompt": f"分析 {symbol} 的技术指标数据:\n{json.dumps(tech_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "role": "technical",
                "system_prompt": TECHNICAL_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
            ("onchain", {
                "prompt": f"分析 {symbol} 的链上与衍生品数据:\n{json.dumps(onchain_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "role": "onchain",
                "system_prompt": ONCHAIN_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
            ("sentiment", {
                "prompt": f"分析 {symbol} 的市场情绪:\n{json.dumps(sentiment_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "role": "sentiment",
                "system_prompt": SENTIMENT_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
            ("fundamental", {
                "prompt": f"分析 {symbol} 的基本面数据:\n{json.dumps(fundamental_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "role": "fundamental",
                "system_prompt": FUNDAMENTAL_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
        ]
        for analyst_type, task in tasks_for_symbol:
            task_index.append((symbol, analyst_type))
            all_tasks.append(task)

    # 并行调用，受 MAX_CONCURRENT 全局限制 (默认 2 并发)
    results = call_claude_parallel(all_tasks)

    # 按 symbol 重组结果
    analyses = {s: {} for s in screened}
    for i, result in enumerate(results):
        symbol, analyst_type = task_index[i]
        if isinstance(result, dict) and "error" in result:
            errors.append(f"analyze_{symbol}_{analyst_type}: {result['error']}")
        analyses[symbol][analyst_type] = result

    err_count = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    _console.print(f"    完成: {len(results) - err_count}/{len(results)} 成功, 耗时 {time.time() - t0:.0f}s")
    return {"analyses": analyses, "errors": errors}
