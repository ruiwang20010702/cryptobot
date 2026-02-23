"""资金费率套利策略

现货做多 + 永续做空 = delta 中性，赚取正资金费率（8h 一次）。
仅虚拟盘运行，使用 virtual_portfolio 基础设施。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR, load_settings
from cryptobot.strategy.virtual_portfolio import (
    VirtualPortfolio,
    VirtualPosition,
    close_position,
    load_portfolio,
    open_position,
    save_portfolio,
)

logger = logging.getLogger(__name__)

_RESULT_PATH = DATA_OUTPUT_DIR / "virtual" / "funding_arb_stats.json"


@dataclass(frozen=True)
class FundingArbSignal:
    symbol: str
    funding_rate: float      # 当前 8h 费率
    annualized_rate: float   # 年化费率
    action: str              # "open" | "close" | "hold"
    entry_threshold: float   # 开仓阈值
    exit_threshold: float    # 平仓阈值
    confidence: int


def _get_arb_config() -> dict:
    """读取套利配置"""
    settings = load_settings()
    return settings.get("strategies", {}).get("funding_arb", {})


def _annualize_rate(rate_8h: float) -> float:
    """8h 费率 → 年化: rate * 3 * 365"""
    return round(rate_8h * 3 * 365 * 100, 2)


def scan_funding_opportunities(
    symbols: list[str] | None = None,
    min_rate: float | None = None,
    volatile_mode: bool = False,
) -> list[FundingArbSignal]:
    """扫描正资金费率机会

    条件: funding_rate > min_rate AND 连续 3 期正费率
    volatile_mode=True 时提高阈值 (0.01% → 0.03%) 以过滤低质量机会
    """
    cfg = _get_arb_config()
    if not cfg.get("enabled", False):
        return []

    if min_rate is None:
        base_rate = cfg.get("min_funding_rate", 0.01) / 100  # 配置是百分比
        min_rate = base_rate * 3 if volatile_mode else base_rate

    if symbols is None:
        from cryptobot.config import get_all_symbols
        symbols = get_all_symbols()

    consecutive_required = cfg.get("consecutive_positive", 3)
    signals: list[FundingArbSignal] = []

    for symbol in symbols:
        try:
            from cryptobot.data.onchain import get_funding_rate
            data = get_funding_rate(symbol, limit=10)
        except Exception as e:
            logger.warning("获取 %s 资金费率失败: %s", symbol, e)
            continue

        rates = data.get("rates", [])
        if not rates:
            current_rate = data.get("current_rate", 0)
            if isinstance(current_rate, (int, float)):
                rates = [{"rate": current_rate}]

        if not rates:
            continue

        # 检查最近 N 期是否连续正费率
        recent = [r.get("rate", 0) for r in rates[-consecutive_required:]]
        all_positive = len(recent) >= consecutive_required and all(
            r > 0 for r in recent
        )
        current_rate = recent[-1] if recent else 0

        if current_rate <= min_rate or not all_positive:
            continue

        annualized = _annualize_rate(current_rate)
        confidence = min(90, int(50 + annualized))  # 年化越高越有信心

        signals.append(FundingArbSignal(
            symbol=symbol,
            funding_rate=current_rate,
            annualized_rate=annualized,
            action="open",
            entry_threshold=min_rate,
            exit_threshold=min_rate * 0.3,  # 费率降到阈值 30% 以下则平仓
            confidence=confidence,
        ))

    # 按年化收益排序
    signals.sort(key=lambda s: s.annualized_rate, reverse=True)

    # 限制最大头寸数
    max_positions = cfg.get("max_positions", 3)
    return signals[:max_positions]


def execute_arb_virtual(
    signal: FundingArbSignal,
    portfolio: VirtualPortfolio,
    spot_price: float,
    perp_price: float,
) -> VirtualPortfolio:
    """虚拟盘执行: 现货做多 + 永续做空

    实际上用一个 short 仓位模拟 delta 中性。
    收益来自 funding rate，而非价格变动。
    """
    cfg = _get_arb_config()
    position_size_pct = cfg.get("position_size_pct", 20)

    # 每个头寸用余额的 position_size_pct%
    margin = portfolio.current_balance * position_size_pct / 100
    if margin < 10:
        logger.warning("余额不足，跳过 %s 套利", signal.symbol)
        return portfolio

    # 用 perp 价格计算数量
    amount = margin / perp_price if perp_price > 0 else 0
    if amount <= 0:
        return portfolio

    now = datetime.now(timezone.utc).isoformat()

    # 开永续空仓（delta 中性对冲）
    short_pos = VirtualPosition(
        symbol=signal.symbol,
        side="short",
        entry_price=perp_price,
        amount=amount,
        leverage=1,  # 套利用 1x
        opened_at=now,
        strategy="funding_arb",
    )

    return open_position(portfolio, short_pos)


def check_arb_positions(
    portfolio: VirtualPortfolio,
    current_rates: dict[str, float],
) -> list[FundingArbSignal]:
    """检查现有套利仓位: 费率转负/反转 → 平仓信号"""
    cfg = _get_arb_config()
    min_rate = cfg.get("min_funding_rate", 0.01) / 100

    close_signals: list[FundingArbSignal] = []
    for pos in portfolio.positions:
        if pos.strategy != "funding_arb":
            continue

        rate = current_rates.get(pos.symbol, 0)
        exit_threshold = min_rate * 0.3

        # 费率反转保护: short 持仓但费率转正（不利），long 持仓但费率转负（不利）
        rate_reversed = (pos.side == "short" and rate < 0) or (
            pos.side == "long" and rate > 0
        )

        if rate < exit_threshold or rate_reversed:
            reason = "费率反转" if rate_reversed else "费率低于阈值"
            close_signals.append(FundingArbSignal(
                symbol=pos.symbol,
                funding_rate=rate,
                annualized_rate=_annualize_rate(rate),
                action="close",
                entry_threshold=min_rate,
                exit_threshold=exit_threshold,
                confidence=80,
            ))
            logger.info(
                "套利平仓信号 %s: %s (rate=%.4f%%, threshold=%.4f%%)",
                pos.symbol, reason, rate * 100, exit_threshold * 100,
            )

    return close_signals


def calc_arb_pnl(portfolio: VirtualPortfolio) -> dict:
    """计算套利 PnL 统计"""
    arb_trades = [
        t for t in portfolio.closed_trades
        if t.get("strategy") == "funding_arb"
    ]

    if not arb_trades:
        return {
            "total_trades": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        }

    total_pnl = sum(t.get("pnl", 0) for t in arb_trades)
    wins = sum(1 for t in arb_trades if t.get("pnl", 0) > 0)

    return {
        "total_trades": len(arb_trades),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(arb_trades), 2),
        "win_rate": round(wins / len(arb_trades), 4),
    }


def run_funding_scan() -> dict:
    """完整扫描流程: 扫描 → 执行 → 检查 → 保存"""
    cfg = _get_arb_config()
    if not cfg.get("enabled", False):
        return {"enabled": False, "reason": "funding_arb 未启用"}

    virtual_balance = cfg.get("virtual_balance", 10000)
    portfolio = load_portfolio("funding_arb", virtual_balance)

    # 1. 扫描新机会
    signals = scan_funding_opportunities()
    opened = 0

    for signal in signals:
        # 检查是否已有该币种的仓位
        existing = [p for p in portfolio.positions if p.symbol == signal.symbol]
        if existing:
            continue

        try:
            from cryptobot.data.onchain import get_funding_rate
            data = get_funding_rate(signal.symbol, limit=1)
            price = data.get("rates", [{}])[-1].get("mark_price", 0) if data.get("rates") else 0
            if price <= 0:
                continue
            portfolio = execute_arb_virtual(signal, portfolio, price, price)
            opened += 1
        except Exception as e:
            logger.warning("执行套利失败 %s: %s", signal.symbol, e)

    # 2. 检查现有仓位
    current_rates = {}
    for pos in portfolio.positions:
        if pos.strategy != "funding_arb":
            continue
        try:
            from cryptobot.data.onchain import get_funding_rate
            data = get_funding_rate(pos.symbol, limit=1)
            current_rates[pos.symbol] = data.get("current_rate", 0)
        except Exception as e:
            logger.warning("费率获取失败 %s: %s", pos.symbol, e)

    close_signals = check_arb_positions(portfolio, current_rates)
    closed = 0
    for sig in close_signals:
        try:
            from cryptobot.data.onchain import get_funding_rate
            data = get_funding_rate(sig.symbol, limit=1)
            price = data.get("rates", [{}])[-1].get("mark_price", 0) if data.get("rates") else 0
            if price > 0:
                portfolio = close_position(
                    portfolio, sig.symbol, "short", price, "funding_arb",
                )
                closed += 1
        except Exception as e:
            logger.warning("平仓失败 %s: %s", sig.symbol, e)

    save_portfolio(portfolio, "funding_arb")

    return {
        "enabled": True,
        "scanned": len(signals),
        "opened": opened,
        "closed": closed,
        "open_positions": len(portfolio.positions),
        "balance": portfolio.current_balance,
        "pnl_stats": calc_arb_pnl(portfolio),
    }
