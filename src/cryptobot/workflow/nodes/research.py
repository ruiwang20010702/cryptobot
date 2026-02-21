"""Node: research — 所有币种 × 2 研究员全并行"""

import json
import logging
import time

from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.prompts import BULL_RESEARCHER, BEAR_RESEARCHER, BULL_SCHEMA, BEAR_SCHEMA
from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage

logger = logging.getLogger(__name__)
_console = Console()


def research(state: WorkflowState) -> dict:
    """所有币种的看多/看空研究员并行（5币 × 2 = 10 个 sonnet，5 并发）"""
    analyses = state.get("analyses", {})
    n_tasks = len(analyses) * 2
    _stage(4, f"多空辩论 — {n_tasks} 个 sonnet ({len(analyses)} 币 x 看多/看空)")
    t0 = time.time()
    errors = list(state.get("errors", []))

    all_tasks = []
    task_index = []
    market_data = state.get("market_data", {})

    for symbol, analysis in analyses.items():
        analysis_text = json.dumps(analysis, ensure_ascii=False, indent=2)

        # O4: 从 market_data 提取关键数值快照注入研究员 prompt
        data_snapshot = ""
        sym_data = market_data.get(symbol, {})
        if sym_data:
            tech = sym_data.get("tech") or {}
            crypto = sym_data.get("crypto") or {}
            momentum = tech.get("momentum", {})
            trend = tech.get("trend", {})
            sr = sym_data.get("support_resistance") or {}
            parts = []
            price = momentum.get("close")
            if price:
                parts.append(f"当前价: {price}")
            rsi = momentum.get("rsi_14")
            if rsi is not None:
                parts.append(f"RSI(14): {rsi:.1f}")
            adx = trend.get("adx")
            if adx is not None:
                parts.append(f"ADX: {adx:.1f}")
            funding = (crypto.get("derivatives") or {}).get("funding_rate")
            if funding is not None:
                parts.append(f"资金费率: {funding}")
            supports = (sr.get("pivot", {}) or {}).get("support", [])
            resistances = (sr.get("pivot", {}) or {}).get("resistance", [])
            if supports:
                parts.append(f"支撑位: {supports[:2]}")
            if resistances:
                parts.append(f"阻力位: {resistances[:2]}")
            if parts:
                data_snapshot = "\n\n### 关键市场数据快照\n" + " | ".join(parts)

        for role_name, prompt_prefix, sys_prompt, schema in [
            ("bull", "构建看涨论据", BULL_RESEARCHER, BULL_SCHEMA),
            ("bear", "构建看跌论据", BEAR_RESEARCHER, BEAR_SCHEMA),
        ]:
            task_index.append((symbol, role_name))
            all_tasks.append({
                "prompt": f"基于以下 {symbol} 的分析师报告，{prompt_prefix}:\n{analysis_text}{data_snapshot}",
                "model": "sonnet",
                "role": f"{role_name}_researcher",
                "system_prompt": sys_prompt,
                "json_schema": schema,
            })

    results = call_claude_parallel(all_tasks)

    research_results = {s: {} for s in analyses}
    for i, result in enumerate(results):
        symbol, role = task_index[i]
        if isinstance(result, dict) and "error" in result:
            errors.append(f"research_{symbol}_{role}: {result['error']}")
        research_results[symbol][role] = result

    err_count = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    _console.print(f"    完成: {len(results) - err_count}/{len(results)} 成功, 耗时 {time.time() - t0:.0f}s")
    return {"research": research_results, "errors": errors}
