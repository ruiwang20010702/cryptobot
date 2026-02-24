"""Telegram 命令处理器 — 11 个查询命令

每个处理器返回 Markdown 字符串，由 bot.py 统一发送。
Freqtrade 离线时自动 fallback 到虚拟盘数据。
"""

import logging

import httpx

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"
_VIRTUAL_STRATEGIES = ("funding_arb", "grid")


def handle_command(text: str) -> str:
    """路由命令到处理器"""
    cmd = text.strip().split()[0].lower()
    handler = COMMANDS.get(cmd, _cmd_unknown)
    try:
        return handler()
    except Exception as e:
        logger.warning("命令 %s 执行异常: %s", cmd, e, exc_info=True)
        return f"\u26a0\ufe0f 命令执行失败: {e}"


# ─── 命令实现 ──────────────────────────────────────────────────────────


def _cmd_help() -> str:
    return (
        "\U0001f916 *CryptoBot 命令*\n\n"
        "/status — 系统状态摘要\n"
        "/signals — 当前活跃信号\n"
        "/positions — Freqtrade 持仓\n"
        "/alerts — 持仓告警\n"
        "/pnl — 近 30 天绩效\n"
        "/edge — Edge 仪表盘\n"
        "/liq — 爆仓距离\n"
        "/weights — 策略权重\n"
        "/balance — 账户余额\n"
        "/risk — 风控概览\n"
        "/help — 本帮助"
    )


def _cmd_status() -> str:
    from cryptobot.signal.bridge import read_signals
    from cryptobot.journal.analytics import calc_performance

    signals = read_signals(filter_expired=True)
    perf = calc_performance(30)

    active_count = len(signals)
    closed = perf.get("closed", 0)
    win_rate = perf.get("win_rate", 0) * 100
    avg_pnl = perf.get("avg_pnl_pct", 0)

    return (
        "\U0001f4ca *系统状态*\n\n"
        f"活跃信号: {active_count}\n"
        f"30天已平仓: {closed}\n"
        f"胜率: {win_rate:.0f}%\n"
        f"平均盈亏: {avg_pnl:+.2f}%"
    )


def _cmd_signals() -> str:
    from cryptobot.signal.bridge import read_signals
    from cryptobot.notify import _format_price

    signals = read_signals(filter_expired=True)
    if not signals:
        return "\U0001f4ad 当前无活跃信号"

    lines = ["\U0001f4e1 *活跃信号*\n"]
    for s in signals:
        action = s.get("action", "?").upper()
        symbol = s.get("symbol", "?")
        leverage = s.get("leverage", "?")
        entry = s.get("entry_price_range", [])
        entry_str = (
            f"{_format_price(entry[0])}-{_format_price(entry[1])}"
            if entry and len(entry) == 2 else "?"
        )
        sl = _format_price(s.get("stop_loss"))
        lines.append(f"*{action} {symbol}* {leverage}x\n  入场: {entry_str} | 止损: {sl}")
    return "\n".join(lines)


def _cmd_positions() -> str:
    from cryptobot.freqtrade_api import ft_api_get

    positions = ft_api_get("/status")
    if positions:
        lines = [f"\U0001f4b0 *持仓* ({len(positions)})\n"]
        for p in positions:
            pair = p.get("pair", "?")
            symbol = pair.replace("/", "").replace(":USDT", "")
            direction = "SHORT" if p.get("is_short") else "LONG"
            leverage = p.get("leverage", "?")
            profit = (p.get("profit_ratio", 0) or 0) * 100
            lines.append(f"*{symbol}* {direction} {leverage}x {profit:+.1f}%")
        return "\n".join(lines)

    # Freqtrade 离线 → 虚拟盘 fallback
    vpositions, prices = _get_virtual_positions()
    if not vpositions:
        return "\U0001f4ad 当前无持仓"

    lines = [f"\U0001f4b0 *持仓* ({len(vpositions)}) \\[虚拟盘]\n"]
    for pos, strategy in vpositions:
        direction = pos.side.upper()
        current = prices.get(pos.symbol, 0)
        if current and pos.entry_price:
            if pos.side == "long":
                pnl_pct = (current - pos.entry_price) / pos.entry_price * 100 * pos.leverage
            else:
                pnl_pct = (pos.entry_price - current) / pos.entry_price * 100 * pos.leverage
        else:
            pnl_pct = 0.0
        lines.append(
            f"*{pos.symbol}* {direction} {pos.leverage}x {pnl_pct:+.1f}% ({strategy})"
        )
    return "\n".join(lines)


def _cmd_alerts() -> str:
    from cryptobot.cli.monitor import _build_position_alerts, _build_signal_only_alerts
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.signal.bridge import read_signals

    signals = read_signals(filter_expired=False)
    positions = ft_api_get("/status")

    if positions:
        alerts = _build_position_alerts(positions, signals)
    else:
        alerts = _build_signal_only_alerts(signals)

    if not alerts:
        return "\u2705 无告警"

    icons = {"CRITICAL": "\U0001f534", "WARNING": "\U0001f7e1"}
    lines = [f"\u26a0\ufe0f *告警* ({len(alerts)})\n"]
    for a in alerts:
        icon = icons.get(a["level"], "\u26aa")
        lines.append(f"{icon} {a['level']}: {a['message']}")
    return "\n".join(lines)


def _cmd_pnl() -> str:
    from cryptobot.journal.analytics import calc_performance

    perf = calc_performance(30)
    closed = perf.get("closed", 0)
    if closed == 0:
        return "\U0001f4ad 近 30 天无已平仓交易"

    win_rate = perf.get("win_rate", 0) * 100
    avg_pnl = perf.get("avg_pnl_pct", 0)
    pf = perf.get("profit_factor", 0)
    total_usdt = perf.get("total_pnl_usdt", 0)

    by_dir = perf.get("by_direction", {})
    long_info = by_dir.get("long", {})
    short_info = by_dir.get("short", {})

    lines = [
        "\U0001f4c8 *30天绩效*\n",
        f"已平仓: {closed} 笔",
        f"胜率: {win_rate:.0f}%",
        f"平均盈亏: {avg_pnl:+.2f}%",
        f"盈亏比: {pf:.2f}",
        f"总盈亏: {total_usdt:+.0f} USDT",
    ]

    if long_info.get("closed", 0) > 0:
        l_wr = long_info.get("win_rate", 0) * 100
        lines.append(f"\nLONG: {long_info['closed']}笔 胜率{l_wr:.0f}%")
    if short_info.get("closed", 0) > 0:
        s_wr = short_info.get("win_rate", 0) * 100
        lines.append(f"SHORT: {short_info['closed']}笔 胜率{s_wr:.0f}%")

    return "\n".join(lines)


def _cmd_edge() -> str:
    from cryptobot.journal.edge import calc_edge

    edge = calc_edge(30)

    lines = [
        "\U0001f4ca *Edge 仪表盘*\n",
        f"期望值: {edge.expectancy_pct:+.2f}%",
        f"Edge Ratio: {edge.edge_ratio:.2f}",
        f"SQN: {edge.sqn:.2f}",
    ]

    # R 分布
    r_dist = edge.r_distribution
    if r_dist:
        lines.append(f"\nR 分布: 正 {r_dist.get('positive', 0)} / 负 {r_dist.get('negative', 0)}")
        lines.append(f"平均R: {r_dist.get('avg_r', 0):+.2f}")

    # 7d vs 30d
    comp = edge.recent_vs_baseline
    if comp:
        lines.append(
            f"\n7d vs 30d: "
            f"期望值 {comp.get('recent_expectancy', 0):+.2f}% "
            f"vs {comp.get('baseline_expectancy', 0):+.2f}%"
        )

    return "\n".join(lines)


def _cmd_liq() -> str:
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.risk.liquidation_calc import full_liquidation_analysis

    positions = ft_api_get("/status")
    if positions:
        lines = ["\U0001f4a3 *爆仓距离*\n"]
        for p in positions:
            pair = p.get("pair", "?")
            symbol = pair.replace("/", "").replace(":USDT", "")
            current = p.get("current_rate", 0) or p.get("open_rate", 0)
            entry = p.get("open_rate", 0)
            leverage = p.get("leverage", 1)
            side = "short" if p.get("is_short") else "long"
            stake = p.get("stake_amount", 0)

            try:
                analysis = full_liquidation_analysis(
                    entry_price=entry,
                    current_price=current,
                    leverage=leverage,
                    side=side,
                    position_size_usdt=stake * leverage,
                )
                _append_liq_line(lines, symbol, side, leverage, analysis)
            except Exception as e:
                lines.append(f"\u26a0\ufe0f {symbol}: 计算失败 ({e})")
        return "\n".join(lines)

    # Freqtrade 离线 → 虚拟盘 fallback
    vpositions, prices = _get_virtual_positions()
    if not vpositions:
        return "\U0001f4ad 无持仓，无需计算爆仓距离"

    lines = ["\U0001f4a3 *爆仓距离* \\[虚拟盘]\n"]
    for pos, _strategy in vpositions:
        current = prices.get(pos.symbol, 0)
        if not current:
            lines.append(f"\u26a0\ufe0f {pos.symbol}: 无法获取价格")
            continue
        notional = pos.amount * pos.entry_price
        try:
            analysis = full_liquidation_analysis(
                entry_price=pos.entry_price,
                current_price=current,
                leverage=pos.leverage,
                side=pos.side,
                position_size_usdt=notional,
                symbol=pos.symbol,
            )
            _append_liq_line(lines, pos.symbol, pos.side, pos.leverage, analysis)
        except Exception as e:
            lines.append(f"\u26a0\ufe0f {pos.symbol}: 计算失败 ({e})")
    return "\n".join(lines)


def _append_liq_line(lines: list, symbol: str, side: str, leverage: int, analysis: dict) -> None:
    """格式化单条爆仓距离信息"""
    dist = analysis.get("distance_pct", 0)
    liq_price = analysis.get("liquidation_price", 0)
    level = analysis.get("risk_level", "")
    icon = {"critical": "\U0001f534", "danger": "\U0001f534", "warning": "\U0001f7e1"}.get(
        level, "\U0001f7e2"
    )
    lines.append(
        f"{icon} *{symbol}* {side.upper()} {leverage}x\n"
        f"  爆仓价: {liq_price:.2f} | 距离: {dist:.1f}%"
    )


def _cmd_weights() -> str:
    from cryptobot.strategy.weight_tracker import load_weights

    alloc = load_weights()
    if alloc is None:
        return "\U0001f4ad 未配置策略权重"

    lines = [f"\u2696\ufe0f *策略权重* (regime: {alloc.regime})\n"]
    for w in alloc.weights:
        pct = w.weight * 100
        bar = "\u2588" * int(pct / 10) + "\u2591" * (10 - int(pct / 10))
        lines.append(f"{bar} {w.strategy}: {pct:.0f}%")
    return "\n".join(lines)


def _cmd_balance() -> str:
    from cryptobot.freqtrade_api import ft_api_get

    data = ft_api_get("/balance")
    if data:
        total = data.get("total", 0)
        free = data.get("free", 0)
        used = data.get("used", 0)
        currencies = data.get("currencies", [])

        lines = [
            "\U0001f4b5 *账户余额*\n",
            f"总计: {total:.2f} USDT",
            f"可用: {free:.2f} USDT",
            f"占用: {used:.2f} USDT",
        ]

        for c in currencies:
            if c.get("currency") == "USDT":
                continue
            bal = c.get("balance", 0)
            if bal > 0:
                lines.append(f"  {c['currency']}: {bal:.4f}")

        return "\n".join(lines)

    # Freqtrade 离线 → 虚拟盘 fallback
    virtual_balance, mock_balance = _get_virtual_balance()

    lines = ["\U0001f4b5 *账户余额* \\[虚拟盘]\n"]
    if mock_balance > 0:
        lines.append(f"总资产参考: {mock_balance:.2f} USDT")
    lines.append(f"虚拟盘净值: {virtual_balance:.2f} USDT")
    return "\n".join(lines)


def _cmd_risk() -> str:
    from cryptobot.risk.monthly_circuit_breaker import check_circuit_breaker

    cb = check_circuit_breaker()

    icons = {"normal": "\U0001f7e2", "reduce": "\U0001f7e1", "suspend": "\U0001f534"}
    icon = icons.get(cb.action, "\u26aa")

    lines = [
        "\U0001f6e1 *风控概览*\n",
        f"{icon} 熔断状态: {cb.action}",
        f"连续亏损月: {cb.consecutive_loss_months}",
        f"仓位缩放: {cb.position_scale:.0%}",
        f"禁止做多: {'是' if cb.block_long else '否'}",
    ]

    if cb.resume_date:
        lines.append(f"恢复日期: {cb.resume_date}")

    lines.append(f"\n原因: {cb.reason}")
    return "\n".join(lines)


def _fetch_binance_prices(symbols: list[str]) -> dict[str, float]:
    """批量获取 Binance 永续合约实时价格"""
    if not symbols:
        return {}
    try:
        resp = httpx.get(BINANCE_TICKER_URL, timeout=5)
        resp.raise_for_status()
        all_prices = {t["symbol"]: float(t["price"]) for t in resp.json()}
        return {s: all_prices[s] for s in symbols if s in all_prices}
    except Exception as e:
        logger.warning("Binance 价格获取失败: %s", e)
        return {}


def _get_virtual_positions() -> tuple[list, dict[str, float]]:
    """加载所有虚拟盘持仓 + 实时价格

    Returns:
        (positions, prices) — positions 包含 (VirtualPosition, strategy) 元组
    """
    from cryptobot.strategy.virtual_portfolio import load_portfolio

    all_positions = []
    symbols = set()
    for strategy in _VIRTUAL_STRATEGIES:
        portfolio = load_portfolio(strategy)
        for pos in portfolio.positions:
            all_positions.append((pos, strategy))
            symbols.add(pos.symbol)

    prices = _fetch_binance_prices(list(symbols))
    return all_positions, prices


def _get_virtual_balance() -> tuple[float, float]:
    """获取虚拟盘合并余额

    Returns:
        (virtual_balance, mock_balance)
    """
    from cryptobot.strategy.virtual_portfolio import load_portfolio, get_unrealized_pnl
    from cryptobot.config import load_settings

    total_balance = 0.0
    symbols = set()
    portfolios = []
    for strategy in _VIRTUAL_STRATEGIES:
        portfolio = load_portfolio(strategy)
        total_balance += portfolio.current_balance
        portfolios.append(portfolio)
        for pos in portfolio.positions:
            symbols.add(pos.symbol)

    prices = _fetch_binance_prices(list(symbols))
    unrealized = sum(get_unrealized_pnl(p, prices) for p in portfolios)

    settings = load_settings()
    mock = settings.get("capital_strategy", {}).get("mock_balance", 0.0)

    return total_balance + unrealized, float(mock)


def _cmd_unknown() -> str:
    return "\u2753 未知命令，输入 /help 查看可用命令"


COMMANDS: dict[str, callable] = {
    "/help": _cmd_help,
    "/status": _cmd_status,
    "/signals": _cmd_signals,
    "/positions": _cmd_positions,
    "/alerts": _cmd_alerts,
    "/pnl": _cmd_pnl,
    "/edge": _cmd_edge,
    "/liq": _cmd_liq,
    "/weights": _cmd_weights,
    "/balance": _cmd_balance,
    "/risk": _cmd_risk,
}
