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
    "risk_review",
    "execute",
    # Re-review
    "re_review",
    "collect_data_for_symbols",
    # Routing
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

def should_risk_review(state: WorkflowState) -> str:
    """trade → risk_review 或 END"""
    decisions = state.get("decisions", [])
    actionable = [d for d in decisions if d.get("action") != "no_trade"]
    if actionable:
        return "risk_review"
    logger.info("无可执行交易决策，工作流结束")
    return END


def should_execute(state: WorkflowState) -> str:
    """risk_review → execute 或 END"""
    approved = state.get("approved_signals", [])
    if approved:
        return "execute"
    logger.info("无通过风控的信号，工作流结束")
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
    graph.add_node("risk_review", risk_review)
    graph.add_node("execute", execute)

    # 线性边
    graph.add_edge("collect_data", "screen")
    graph.add_edge("screen", "analyze")
    graph.add_edge("analyze", "research")
    graph.add_edge("research", "trade")

    # 条件路由
    graph.add_conditional_edges("trade", should_risk_review)
    graph.add_conditional_edges("risk_review", should_execute)

    # 入口
    graph.set_entry_point("collect_data")

    return graph.compile()
