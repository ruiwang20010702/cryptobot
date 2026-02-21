"""工作流公共工具函数"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console

logger = logging.getLogger(__name__)
_console = Console()

STEPS_TOTAL = 7


def _stage(step: int, msg: str):
    """打印工作流阶段进度"""
    _console.print(f"[cyan][{step}/{STEPS_TOTAL}][/cyan] {msg}")


def _build_portfolio_context() -> str:
    """构建当前持仓和账户上下文（供 TRADER/RISK_MANAGER 使用）"""
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.config import load_settings

    positions = ft_api_get("/status") or []
    balance_data = ft_api_get("/balance")
    settings = load_settings()
    risk_cfg = settings.get("risk", {})

    # 解析余额
    usdt_balance = 0.0
    usdt_free = 0.0
    usdt_used = 0.0
    if balance_data:
        for cur in balance_data.get("currencies", []):
            if cur.get("currency") == "USDT":
                usdt_balance = float(cur.get("balance", 0))
                usdt_free = float(cur.get("free", 0))
                usdt_used = float(cur.get("used", 0))
                break

    if not positions and usdt_balance <= 0:
        return ""  # Freqtrade 未运行，不注入

    # 构建持仓摘要
    lines = [
        "### 账户状态",
        f"USDT 余额: {usdt_balance:.2f} (可用: {usdt_free:.2f}, 已用: {usdt_used:.2f})",
        f"当前持仓: {len(positions)} 个",
    ]
    long_used = 0.0
    short_used = 0.0
    for p in positions:
        direction = "SHORT" if p.get("is_short") else "LONG"
        pnl_pct = (p.get("profit_pct", 0) or 0) * 100
        lines.append(
            f"  - {p.get('pair', '?')} {direction} {p.get('leverage', '?')}x "
            f"盈亏:{pnl_pct:+.1f}%"
        )
        stake = float(p.get("stake_amount", 0) or 0)
        if p.get("is_short"):
            short_used += stake
        else:
            long_used += stake

    if usdt_balance > 0:
        lines.append(f"多头仓位占比: {long_used / usdt_balance * 100:.1f}%")
        lines.append(f"空头仓位占比: {short_used / usdt_balance * 100:.1f}%")
        lines.append(f"总仓位占比: {(long_used + short_used) / usdt_balance * 100:.1f}%")

    lines.append("")
    lines.append("### 风控规则")
    lines.append(f"- 同方向总仓位上限: {risk_cfg.get('max_same_direction_pct', 50)}%")
    lines.append(f"- 同类别最大: {risk_cfg.get('max_same_category_pct', 40)}%")
    lines.append(f"- 总持仓上限: {risk_cfg.get('max_total_position_pct', 80)}%")
    lines.append(f"- 单笔最大亏损: {risk_cfg.get('max_loss', {}).get('per_trade_pct', 2)}%")
    lines.append("")

    return "\n".join(lines)


def _fetch_global(symbols: list[str]) -> tuple:
    """获取全局市场数据 (恐惧贪婪、市场概览、全局新闻、稳定币流、宏观日历)

    Returns:
        (fear_greed, market_overview, global_news, stablecoin_flows, macro_events, errors)
    """
    from cryptobot.data.sentiment import get_fear_greed_index
    from cryptobot.data.news import get_market_overview
    from cryptobot.data.crypto_news import get_crypto_news
    from cryptobot.data.stablecoin import get_stablecoin_flows
    from cryptobot.data.economic_calendar import get_upcoming_events

    _fg, _mo, _gn, _sf, _me = None, None, None, None, None
    _errs = []
    try:
        _fg = get_fear_greed_index()
    except Exception as e:
        _errs.append(f"fear_greed: {e}")
    try:
        _mo = get_market_overview()
    except Exception as e:
        _errs.append(f"market_overview: {e}")
    try:
        currencies = [s.replace("USDT", "") for s in symbols]
        _gn = get_crypto_news(currencies)
    except Exception as e:
        _errs.append(f"global_news: {e}")
    try:
        _sf = get_stablecoin_flows()
    except Exception as e:
        _errs.append(f"stablecoin_flows: {e}")
    try:
        _me = get_upcoming_events()
    except Exception as e:
        _errs.append(f"macro_events: {e}")
    return _fg, _mo, _gn, _sf, _me, _errs


def _fetch_symbol(symbol: str) -> tuple[str, dict, list]:
    """采集单个币种的所有指标数据

    Returns:
        (symbol, data_dict, errors)
    """
    from cryptobot.indicators.calculator import calc_all_indicators
    from cryptobot.indicators.crypto_specific import calc_crypto_indicators
    from cryptobot.indicators.multi_timeframe import (
        calc_multi_timeframe, calc_volume_analysis, calc_support_resistance,
    )
    from cryptobot.data.liquidation import get_force_orders

    data = {"symbol": symbol}
    errs = []
    try:
        data["tech"] = calc_all_indicators(symbol, "4h")
    except Exception as e:
        logger.warning("技术指标失败 %s: %s", symbol, e)
        data["tech"] = None
        errs.append(f"tech_{symbol}: {e}")
    try:
        data["crypto"] = calc_crypto_indicators(symbol)
    except Exception as e:
        logger.warning("链上指标失败 %s: %s", symbol, e)
        data["crypto"] = None
        errs.append(f"crypto_{symbol}: {e}")
    try:
        data["multi_tf"] = calc_multi_timeframe(symbol)
    except Exception as e:
        logger.warning("多时间框架失败 %s: %s", symbol, e)
        data["multi_tf"] = None
        errs.append(f"multi_tf_{symbol}: {e}")
    try:
        data["volume_analysis"] = calc_volume_analysis(symbol)
    except Exception as e:
        logger.warning("量价分析失败 %s: %s", symbol, e)
        data["volume_analysis"] = None
        errs.append(f"volume_{symbol}: {e}")
    try:
        data["support_resistance"] = calc_support_resistance(symbol)
    except Exception as e:
        logger.warning("支撑阻力失败 %s: %s", symbol, e)
        data["support_resistance"] = None
        errs.append(f"sr_{symbol}: {e}")
    try:
        data["liquidation"] = get_force_orders(symbol)
    except Exception as e:
        logger.warning("强平数据失败 %s: %s", symbol, e)
        data["liquidation"] = None
        errs.append(f"liq_{symbol}: {e}")
    try:
        from cryptobot.data.orderbook import get_orderbook_depth
        data["orderbook"] = get_orderbook_depth(symbol)
    except Exception as e:
        logger.warning("订单簿失败 %s: %s", symbol, e)
        data["orderbook"] = None
        errs.append(f"orderbook_{symbol}: {e}")
    try:
        from cryptobot.data.coinglass import get_liquidation_heatmap
        data["coinglass_liq"] = get_liquidation_heatmap(symbol)
    except Exception as e:
        logger.warning("CoinGlass清算失败 %s: %s", symbol, e)
        data["coinglass_liq"] = None
        errs.append(f"coinglass_{symbol}: {e}")
    try:
        from cryptobot.data.exchange_reserve import get_open_interest_trend
        data["open_interest"] = get_open_interest_trend(symbol)
    except Exception as e:
        logger.warning("持仓量趋势失败 %s: %s", symbol, e)
        data["open_interest"] = None
        errs.append(f"oi_{symbol}: {e}")
    try:
        from cryptobot.data.token_unlocks import get_dilution_risk
        data["dilution_risk"] = get_dilution_risk(symbol)
    except Exception as e:
        logger.warning("稀释风险失败 %s: %s", symbol, e)
        data["dilution_risk"] = None
        errs.append(f"dilution_{symbol}: {e}")
    try:
        from cryptobot.data.options import get_options_sentiment
        data["options_sentiment"] = get_options_sentiment(symbol)
    except Exception as e:
        logger.warning("期权情绪失败 %s: %s", symbol, e)
        data["options_sentiment"] = None
        errs.append(f"options_{symbol}: {e}")
    try:
        from cryptobot.data.defi_tvl import get_defi_tvl
        data["defi_tvl"] = get_defi_tvl(symbol)
    except Exception as e:
        logger.warning("DeFi TVL 失败 %s: %s", symbol, e)
        data["defi_tvl"] = None
        errs.append(f"defi_tvl_{symbol}: {e}")
    try:
        from cryptobot.data.whale_tracker import get_whale_activity
        data["whale_activity"] = get_whale_activity(symbol)
    except Exception as e:
        logger.warning("巨鲸追踪失败 %s: %s", symbol, e)
        data["whale_activity"] = None
        errs.append(f"whale_{symbol}: {e}")
    return symbol, data, errs


def fetch_market_data(symbols: list[str]) -> tuple[dict, dict, dict, dict, dict, dict, list]:
    """并行采集全局和每币种数据

    Returns:
        (market_data, fear_greed, market_overview, global_news, stablecoin_flows, macro_events, errors)
    """
    from cryptobot.indicators.market_structure import calc_btc_correlation

    market_data = {}
    fear_greed = {"current_value": 50, "current_classification": "Neutral"}
    market_overview = {}
    global_news = {}
    stablecoin_flows = {}
    macro_events = {}
    errors = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        global_future = executor.submit(_fetch_global, symbols)
        symbol_futures = {executor.submit(_fetch_symbol, s): s for s in symbols}

        fg, mo, gn, sf, me, g_errs = global_future.result()
        if fg:
            fear_greed = fg
        if mo:
            market_overview = mo
        if gn:
            global_news = gn
        if sf:
            stablecoin_flows = sf
        if me:
            macro_events = me
        errors.extend(g_errs)

        for future in as_completed(symbol_futures):
            symbol, data, errs = future.result()
            market_data[symbol] = data
            errors.extend(errs)

    # BTC 联动性 (需要 BTC 的 tech 数据先就绪)
    btc_tech = market_data.get("BTCUSDT", {}).get("tech")
    for symbol in symbols:
        if symbol in market_data:
            try:
                market_data[symbol]["btc_correlation"] = calc_btc_correlation(
                    symbol, btc_tech=btc_tech, market_overview=market_overview
                )
            except Exception as e:
                logger.warning("BTC 联动失败 %s: %s", symbol, e)
                market_data[symbol]["btc_correlation"] = None
                errors.append(f"btc_corr_{symbol}: {e}")

    return market_data, fear_greed, market_overview, global_news, stablecoin_flows, macro_events, errors
