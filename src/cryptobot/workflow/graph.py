"""LangGraph 自动化分析工作流

状态图: collect_data → screen → analyze → research → trade → risk_review → execute

各节点实现已拆分到 workflow/nodes/ 下的独立模块。
本模块负责图装配和向后兼容 re-export。
"""

import logging

from langgraph.graph import StateGraph, END

# ─── 向后兼容 re-export ──────────────────────────────────────────────────
# 所有公共符号从子模块 re-export，保证 `from cryptobot.workflow.graph import X` 不变

from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage, _build_portfolio_context
from cryptobot.workflow.nodes.collect import (
    collect_data, _detect_market_regime, _REGIME_PARAMS,
)
from cryptobot.workflow.nodes.screen import screen, _data_quality_score
from cryptobot.workflow.nodes.analyze import analyze
from cryptobot.workflow.nodes.research import research
from cryptobot.workflow.nodes.trade import trade
from cryptobot.workflow.nodes.risk import risk_review, _decision_to_signal, _extract_votes
from cryptobot.workflow.nodes.execute import execute
from cryptobot.workflow.nodes.ml_filter import ml_filter
from cryptobot.workflow.re_review import re_review, collect_data_for_symbols

logger = logging.getLogger(__name__)

__all__ = [
    # State
    "WorkflowState",
    # Nodes
    "collect_data",
    "screen",
    "analyze",
    "research",
    "trade",
    "ml_filter",
    "risk_review",
    "execute",
    # Re-review
    "re_review",
    "collect_data_for_symbols",
    # Routing
    "should_analyze",
    "should_risk_review",
    "should_execute",
    # Graph
    "build_graph",
    # Utilities (used in tests)
    "_stage",
    "_build_portfolio_context",
    "_decision_to_signal",
    "_extract_votes",
    "_data_quality_score",
    "_detect_market_regime",
    "_REGIME_PARAMS",
]


# ─── 条件路由 ─────────────────────────────────────────────────────────────

def _send_workflow_summary(state: WorkflowState, approved_count: int = 0) -> None:
    """发送每轮分析摘要通知"""
    try:
        from cryptobot.notify import notify_workflow_summary
        regime = state.get("market_regime", {})
        capital_tier = state.get("capital_tier", {})
        fg = state.get("fear_greed", {})
        notify_workflow_summary(
            screened=state.get("screened_symbols", []),
            decisions=state.get("decisions", []),
            approved_count=approved_count,
            regime=regime.get("regime", "unknown"),
            capital_tier=capital_tier.get("tier", "unknown"),
            fear_greed=fg.get("current_value"),
        )
    except Exception as e:
        logger.warning("分析摘要通知失败: %s", e)


def _save_archive_on_early_exit(state: WorkflowState) -> None:
    """提前终止时也保存归档"""
    try:
        from cryptobot.archive.writer import save_archive
        run_id = save_archive(state)
        logger.info("提前终止归档完成: %s", run_id)
    except Exception as e:
        logger.warning("提前终止归档失败: %s", e)


def should_analyze(state: WorkflowState) -> str:
    """screen → analyze 或直接 trade（volatile 时跳过 LLM 节省调用）"""
    regime = state.get("market_regime", {})
    if regime.get("regime") != "volatile":
        return "analyze"

    # volatile 时检查是否有币需要 LLM 决策
    from cryptobot.workflow.strategy_router import route_strategy

    symbols = state.get("screened_symbols", [])
    fg_val = regime.get("fear_greed_value", 50)
    for sym_info in symbols:
        route = route_strategy(
            regime=regime.get("regime", ""),
            regime_confidence=regime.get("regime_confidence", 0.5),
            hurst=regime.get("hurst_exponent", 0.5),
            volatility_state=regime.get("volatility_state", "normal"),
            fear_greed_value=fg_val,
        )
        if route.strategy == "ai_trend":
            return "analyze"  # 至少一个币需要 LLM
        # P15: funding_arb + 趋势空头 → 可能 fallback 到 ai_trend
        if route.strategy == "funding_arb":
            trend_dir = regime.get("trend_direction", "")
            hurst = regime.get("hurst_exponent", 0.5)
            if trend_dir == "bearish" and hurst > 0.55:
                return "analyze"

    logger.info("所有币种策略均不需 LLM，跳过 analyze/research")
    return "trade"


def should_risk_review(state: WorkflowState) -> str:
    """trade → risk_review 或 END"""
    decisions = state.get("decisions", [])
    actionable = [d for d in decisions if d.get("action") != "no_trade"]
    if actionable:
        return "risk_review"
    logger.info("无可执行交易决策，工作流结束")
    _send_workflow_summary(state, approved_count=0)
    _save_archive_on_early_exit(state)
    return END


def should_execute(state: WorkflowState) -> str:
    """risk_review → execute 或 END"""
    approved = state.get("approved_signals", [])
    if approved:
        return "execute"
    logger.info("无通过风控的信号，工作流结束")
    _send_workflow_summary(state, approved_count=0)
    _save_archive_on_early_exit(state)
    return END


# ─── 图构建 ───────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """构建并编译 LangGraph 工作流"""
    graph = StateGraph(WorkflowState)

    # 添加节点
    graph.add_node("collect_data", collect_data)
    graph.add_node("screen", screen)
    graph.add_node("analyze", analyze)
    graph.add_node("research", research)
    graph.add_node("trade", trade)
    graph.add_node("ml_filter", ml_filter)
    graph.add_node("risk_review", risk_review)
    graph.add_node("execute", execute)

    # 线性边
    graph.add_edge("collect_data", "screen")
    graph.add_conditional_edges("screen", should_analyze, {
        "analyze": "analyze",
        "trade": "trade",
    })
    graph.add_edge("analyze", "research")
    graph.add_edge("research", "trade")

    # 条件路由
    graph.add_edge("trade", "ml_filter")
    graph.add_conditional_edges("ml_filter", should_risk_review)
    graph.add_conditional_edges("risk_review", should_execute)

    # 入口
    graph.set_entry_point("collect_data")

    return graph.compile()
