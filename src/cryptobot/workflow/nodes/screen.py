"""Node: screen — 规则筛选 3-5 个最值得分析的币种"""

import logging

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

    # O12: 获取当前持仓币种
    _held_symbols: set[str] = set()
    try:
        from cryptobot.freqtrade_api import ft_api_get
        positions = ft_api_get("/status") or []
        for pos in positions:
            pair = pos.get("pair", "")
            sym = pair.replace("/", "").replace(":USDT", "")
            if sym:
                _held_symbols.add(sym)
    except Exception:
        pass

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

        # O10: perf_feedback 低胜率币种扣分
        perf_feedback = state.get("perf_feedback", {})
        by_symbol = perf_feedback.get("by_symbol", {})
        sym_perf = by_symbol.get(symbol, {})
        if sym_perf.get("count", 0) >= 15 and sym_perf.get("win_rate", 1) < 0.3:
            score -= 3

        # 配对配置优先级 (BTC/ETH 权重高)
        pair_cfg = get_pair_config(symbol)
        if pair_cfg and pair_cfg.get("category") in ("store_of_value", "smart_contract"):
            score += 1

        # O12: 持仓币种优先（确保持仓币种持续被分析）
        if symbol in _held_symbols:
            score += 5

        # 资金层级优选币种加分
        capital_tier = state.get("capital_tier", {})
        preferred = capital_tier.get("params", {}).get("preferred_symbols", [])
        if preferred and symbol in preferred:
            score += 2

        scores.append((symbol, round(score, 1)))

    # 按分数降序，取前 max_coins（资金层级控制）
    scores.sort(key=lambda x: x[1], reverse=True)
    capital_tier = state.get("capital_tier", {})
    max_coins = capital_tier.get("params", {}).get("max_coins", 5)
    screened = [s[0] for s in scores[:max_coins]]

    ranked = [(s, sc) for s, sc in scores[:5]]
    _console.print(f"    筛选结果: {', '.join(f'{s}({sc})' for s, sc in ranked)}")
    logger.info("筛选结果: %s", ranked)

    # O23: 延迟加载并行化 — CoinGecko + CryptoPanic (Semaphore 限速)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    _cg_semaphore = threading.Semaphore(2)  # CoinGecko 限速 2 并发

    def _fetch_coin_extra(sym: str) -> tuple[str, dict | None, dict | None, list]:
        base = sym.replace("USDT", "")
        info, news = None, None
        errs = []
        with _cg_semaphore:
            try:
                info = get_coin_info(base)
            except Exception as e:
                errs.append(f"coin_info_{sym}: {e}")
        try:
            news = get_coin_specific_news(sym)
        except Exception as e:
            errs.append(f"coin_news_{sym}: {e}")
        return sym, info, news, errs

    # 收集结果后批量创建新 dict，不在线程中修改 state
    extra_results: dict[str, tuple] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_coin_extra, s): s for s in screened}
        for future in as_completed(futures):
            sym, info, news, errs = future.result()
            extra_results[sym] = (info, news)
            errors.extend(errs)

    # 批量创建新 market_data，不原地修改
    updated_market_data = {**market_data}
    for sym, (info, news) in extra_results.items():
        updated_market_data[sym] = {**updated_market_data[sym], "coin_info": info, "coin_news": news}

    return {
        "screened_symbols": screened,
        "screening_scores": scores,
        "market_data": updated_market_data,
        "errors": errors,
    }
