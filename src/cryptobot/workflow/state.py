"""工作流状态定义"""

from typing import TypedDict


class WorkflowState(TypedDict, total=False):
    market_data: dict        # collect_data: {symbol: {tech, crypto, multi_tf, volume_analysis, support_resistance, liquidation, btc_correlation, coin_info, coin_news}}
    market_overview: dict    # collect_data: 全局市场概览
    fear_greed: dict         # collect_data: 恐惧贪婪指数
    global_news: dict        # collect_data: 全局新闻情绪
    market_regime: dict      # collect_data: {regime, confidence, params, description}
    screened_symbols: list   # screen: 筛选出的 3-5 个币种
    analyses: dict           # analyze: {symbol: {tech, onchain, sentiment, fundamental}}
    research: dict           # research: {symbol: {bull, bear}}
    decisions: list          # trade: [{symbol, action, ...}]
    approved_signals: list   # risk_review: 通过风控的信号
    executed: list           # execute: 写入 signal.json 的结果
    errors: list             # 各节点错误收集
