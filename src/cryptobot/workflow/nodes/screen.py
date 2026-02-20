"""Node: screen — 规则筛选 3-5 个最值得分析的币种"""

import logging
import time

from rich.console import Console

from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage

logger = logging.getLogger(__name__)
_console = Console()


def _data_quality_score(data: dict) -> int:
    """评估单币种数据完整性 (0-100)

    权重: tech=30, crypto=15, multi_tf=15, volume_analysis=10,
          support_resistance=10, liquidation=10, btc_correlation=10
    """
    weights = {
        "tech": 30,
        "crypto": 15,
        "multi_tf": 15,
        "volume_analysis": 10,
        "support_resistance": 10,
        "liquidation": 10,
        "btc_correlation": 10,
    }
    score = 0
    for key, weight in weights.items():
        val = data.get(key)
        if val is not None and not (isinstance(val, dict) and "error" in val):
            score += weight
    return score


def screen(state: WorkflowState) -> dict:
    """规则筛选，选出 3-5 个最值得分析的币种，筛选后延迟加载 CoinGecko/CryptoPanic"""
    from cryptobot.config import get_pair_config
    from cryptobot.data.news import get_coin_info
    from cryptobot.data.crypto_news import get_coin_specific_news

    _stage(2, "规则筛选")
    market_data = state.get("market_data", {})
    errors = list(state.get("errors", []))
    scores = []

    for symbol, data in market_data.items():
        score = 0
        tech = data.get("tech")
        crypto = data.get("crypto")

        # 数据质量门槛: < 40 跳过
        quality = _data_quality_score(data)
        if quality < 40:
            logger.info("跳过 %s: 数据质量 %d/100 < 40", symbol, quality)
            continue

        if not tech:
            continue

        signals = tech.get("signals", {})
        tech_score = abs(signals.get("technical_score", 0))

        # 技术评分越极端越值得分析
        score += tech_score * 2

        # RSI 极端值
        rsi = tech.get("momentum", {}).get("rsi_14")
        if rsi is not None:
            if rsi > 70 or rsi < 30:
                score += 3
            elif rsi > 60 or rsi < 40:
                score += 1

        # MACD 交叉
        macd_cross = tech.get("trend", {}).get("macd_cross", "none")
        if macd_cross in ("golden_cross", "death_cross"):
            score += 3

        # ATR 波动率 — 高波动更有交易机会
        atr_pct = tech.get("volatility", {}).get("atr_pct", 0)
        if atr_pct > 3:
            score += 2
        elif atr_pct > 1.5:
            score += 1

        # 链上数据综合评分
        if crypto:
            composite = crypto.get("composite", {})
            score += abs(composite.get("score", 0))

        # 新增: 多时间框架共振
        multi_tf = data.get("multi_tf")
        if multi_tf and multi_tf.get("aligned_count", 0) >= 2:
            score += 2

        # 新增: 清算强度
        liq = data.get("liquidation")
        if liq and liq.get("intensity") in ("high", "extreme"):
            score += 2

        # 新增: 量价背离
        vol = data.get("volume_analysis")
        if vol and vol.get("obv_divergence") in ("bullish_divergence", "bearish_divergence"):
            score += 2

        # 配对配置优先级 (BTC/ETH 权重高)
        pair_cfg = get_pair_config(symbol)
        if pair_cfg and pair_cfg.get("category") in ("store_of_value", "smart_contract"):
            score += 1

        scores.append((symbol, round(score, 1)))

    # 按分数降序，取前 5
    scores.sort(key=lambda x: x[1], reverse=True)
    screened = [s[0] for s in scores[:5]]

    ranked = [(s, sc) for s, sc in scores[:5]]
    _console.print(f"    筛选结果: {', '.join(f'{s}({sc})' for s, sc in ranked)}")
    logger.info("筛选结果: %s", ranked)

    # 延迟加载: 只对筛选出的 5 个币种获取 CoinGecko + CryptoPanic 数据
    for i, symbol in enumerate(screened):
        base = symbol.replace("USDT", "")
        # CoinGecko coin_info (间隔 2s 避免 429)
        if i > 0:
            time.sleep(2)
        try:
            market_data[symbol]["coin_info"] = get_coin_info(base)
        except Exception as e:
            logger.warning("币种信息失败 %s: %s", symbol, e)
            market_data[symbol]["coin_info"] = None
            errors.append(f"coin_info_{symbol}: {e}")
        # CryptoPanic 币种新闻
        try:
            market_data[symbol]["coin_news"] = get_coin_specific_news(symbol)
        except Exception as e:
            logger.warning("币种新闻失败 %s: %s", symbol, e)
            market_data[symbol]["coin_news"] = None
            errors.append(f"coin_news_{symbol}: {e}")

    return {"screened_symbols": screened, "market_data": market_data, "errors": errors}
