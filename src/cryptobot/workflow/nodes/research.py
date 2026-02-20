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

    for symbol, analysis in analyses.items():
        analysis_text = json.dumps(analysis, ensure_ascii=False, indent=2)

        for role, prompt_prefix, sys_prompt, schema in [
            ("bull", "构建看涨论据", BULL_RESEARCHER, BULL_SCHEMA),
            ("bear", "构建看跌论据", BEAR_RESEARCHER, BEAR_SCHEMA),
        ]:
            task_index.append((symbol, role))
            all_tasks.append({
                "prompt": f"基于以下 {symbol} 的分析师报告，{prompt_prefix}:\n{analysis_text}",
                "model": "sonnet",
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
