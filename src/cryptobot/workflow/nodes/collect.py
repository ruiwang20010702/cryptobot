"""Node: collect_data — 采集所有币种的市场数据"""

import logging
import time

from rich.console import Console

from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage, fetch_market_data

logger = logging.getLogger(__name__)
_console = Console()


# ─── 市场状态检测 ─────────────────────────────────────────────────────────

# 各状态的策略参数默认值
_REGIME_PARAMS = {
    "trending": {
        "min_confidence": 55,
        "max_leverage": 5,
        "trailing_stop": True,
        "description": "趋势市: EMA 多头/空头排列，ADX>25。适合顺势交易，可适度加仓。",
    },
    "ranging": {
        "min_confidence": 65,
        "max_leverage": 3,
        "trailing_stop": False,
        "description": "震荡市: ADX<20，布林带收窄。区间交易为主，轻仓博反弹。",
    },
    "volatile": {
        "min_confidence": 70,
        "max_leverage": 2,
        "trailing_stop": True,
        "description": "剧烈波动: ATR 显著放大，恐惧贪婪极端。降低杠杆，严格止损。",
    },
}


def _detect_market_regime(market_data: dict, fear_greed: dict) -> dict:
    """基于 BTC 技术指标 + 恐惧贪婪判断市场状态

    Returns:
        {regime: "trending"|"ranging"|"volatile",
         confidence: 0-100,
         params: {...},
         description: "..."}
    """
    btc = market_data.get("BTCUSDT", {})
    tech = btc.get("tech") or {}

    adx = (tech.get("trend") or {}).get("adx")
    atr_pct = (tech.get("volatility") or {}).get("atr_pct", 0)
    bb_width = (tech.get("volatility") or {}).get("bb_width", 0)
    fg_val = fear_greed.get("current_value", 50)

    # 评分
    volatile_score = 0
    trending_score = 0
    ranging_score = 0

    # ATR 波动率
    if atr_pct > 4:
        volatile_score += 3
    elif atr_pct > 2.5:
        volatile_score += 1
    elif atr_pct < 1.5:
        ranging_score += 2

    # 布林带宽度
    if bb_width and bb_width > 8:
        volatile_score += 2
    elif bb_width and bb_width < 3:
        ranging_score += 2

    # ADX 趋势强度
    if adx and adx > 30:
        trending_score += 3
    elif adx and adx > 25:
        trending_score += 2
    elif adx and adx < 20:
        ranging_score += 2

    # 恐惧贪婪极端值
    if fg_val < 20 or fg_val > 80:
        volatile_score += 2
    elif 40 <= fg_val <= 60:
        ranging_score += 1

    # 选择得分最高的状态
    scores = {
        "trending": trending_score,
        "ranging": ranging_score,
        "volatile": volatile_score,
    }
    regime = max(scores, key=scores.get)
    max_score = scores[regime]
    total = sum(scores.values()) or 1
    confidence = round(max_score / total * 100)

    params = _REGIME_PARAMS[regime]
    return {
        "regime": regime,
        "confidence": confidence,
        "params": {k: v for k, v in params.items() if k != "description"},
        "description": params["description"],
    }


def collect_data(state: WorkflowState) -> dict:
    """采集所有币种的市场数据（纯 Python，不调 LLM）"""
    from cryptobot.config import get_all_symbols

    errors = list(state.get("errors", []))
    symbols = get_all_symbols()
    _stage(1, f"数据采集 — {len(symbols)} 个币种 (扩展数据)")
    t0 = time.time()

    market_data, fear_greed, market_overview, global_news, fetch_errors = (
        fetch_market_data(symbols)
    )
    errors.extend(fetch_errors)

    ok = sum(1 for d in market_data.values() if d.get("tech"))
    fail_rate = (len(symbols) - ok) / len(symbols) if symbols else 1

    _console.print(f"    完成: {ok}/{len(symbols)} 有技术数据, "
                    f"恐惧贪婪={fear_greed.get('current_value', '?')}, "
                    f"耗时 {time.time() - t0:.0f}s")

    if fail_rate > 0.5:
        logger.error("数据采集失败率 %.0f%% > 50%%, 跳过本轮分析", fail_rate * 100)
        _console.print(f"    [red]数据质量不足 ({ok}/{len(symbols)})，跳过本轮[/red]")
        return {
            "market_data": {},
            "screened_symbols": [],
            "errors": errors,
        }

    # 市场状态检测
    regime = _detect_market_regime(market_data, fear_greed)
    _console.print(f"    市场状态: {regime['regime']} (置信度 {regime['confidence']}%)")

    return {
        "market_data": market_data,
        "market_overview": market_overview,
        "fear_greed": fear_greed,
        "global_news": global_news,
        "market_regime": regime,
        "errors": errors,
    }
