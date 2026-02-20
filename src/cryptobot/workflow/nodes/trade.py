"""Node: trade — 所有币种的交易决策并行"""

import json
import logging
import time

from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.prompts import TRADER, TRADE_SCHEMA
from cryptobot.workflow.state import WorkflowState
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

    # 置信度校准上下文
    confidence_ctx = ""
    try:
        from cryptobot.journal.confidence_tuner import build_threshold_context
        confidence_ctx = build_threshold_context(regime, 30)
    except Exception as e:
        logger.warning("置信度校准失败: %s", e)

    all_tasks = []
    task_meta = []  # (symbol, current_price)

    for symbol in research_data:
        bull = research_data[symbol].get("bull", {})
        bear = research_data[symbol].get("bear", {})
        analysis = analyses.get(symbol, {})
        data = market_data.get(symbol, {})
        pair_cfg = get_pair_config(symbol) or {}

        current_price = (data.get("tech") or {}).get("latest_close", 0)
        max_leverage = pair_cfg.get("leverage_range", [1, 3])[1]

        all_tasks.append({
            "prompt": (
                f"## {symbol} 交易决策\n\n"
                f"当前价格: {current_price}\n"
                f"最大杠杆: {max_leverage}x\n\n"
                f"{portfolio_ctx}"
                f"{perf_ctx}"
                f"{weights_ctx}"
                f"{regime_ctx}"
                f"{confidence_ctx}"
                f"### 看多研究员观点\n{json.dumps(bull, ensure_ascii=False, indent=2)}\n\n"
                f"### 看空研究员观点\n{json.dumps(bear, ensure_ascii=False, indent=2)}\n\n"
                f"### 分析师数据\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
                f"请做出交易决策。"
            ),
            "model": "sonnet",
            "role": "trader",
            "system_prompt": TRADER,
            "json_schema": TRADE_SCHEMA,
        })
        task_meta.append((symbol, current_price))

    results = call_claude_parallel(all_tasks)

    decisions = []
    for i, result in enumerate(results):
        symbol, current_price = task_meta[i]
        if isinstance(result, dict) and "error" not in result:
            result["symbol"] = symbol
            result["current_price"] = current_price
            decisions.append(result)
        else:
            err = result.get("error", "非 JSON 响应") if isinstance(result, dict) else "非 JSON 响应"
            errors.append(f"trade_{symbol}: {err}")

    actions = [f"{d['symbol']}={d.get('action', '?')}" for d in decisions]
    _console.print(f"    完成: {', '.join(actions) or '无交易'}, 耗时 {time.time() - t0:.0f}s")
    return {"decisions": decisions, "errors": errors}
