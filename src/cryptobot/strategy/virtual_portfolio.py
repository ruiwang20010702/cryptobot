"""虚拟盘基础设施

不可变数据结构 + 纯函数，供资金费率套利和网格交易复用。
持久化到 data/output/virtual/{strategy}_portfolio.json。
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

VIRTUAL_DIR = DATA_OUTPUT_DIR / "virtual"


@dataclass(frozen=True)
class VirtualPosition:
    symbol: str
    side: str              # "long" | "short"
    entry_price: float
    amount: float          # 数量 (base currency)
    leverage: int
    opened_at: str         # ISO timestamp
    strategy: str          # "funding_arb" | "grid" | "ai_trend"


@dataclass(frozen=True)
class VirtualPortfolio:
    initial_balance: float
    current_balance: float
    positions: list[VirtualPosition] = field(default_factory=list)
    closed_trades: list[dict] = field(default_factory=list)
    updated_at: str = ""


def open_position(
    portfolio: VirtualPortfolio,
    position: VirtualPosition,
) -> VirtualPortfolio:
    """开仓: 返回新 portfolio (不可变)

    扣除保证金 = amount * entry_price / leverage
    """
    margin = position.amount * position.entry_price / position.leverage
    new_balance = portfolio.current_balance - margin
    if new_balance < 0:
        raise ValueError(
            f"余额不足: 需要 {margin:.2f}, 当前 {portfolio.current_balance:.2f}"
        )

    return VirtualPortfolio(
        initial_balance=portfolio.initial_balance,
        current_balance=round(new_balance, 4),
        positions=[*portfolio.positions, position],
        closed_trades=list(portfolio.closed_trades),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def close_position(
    portfolio: VirtualPortfolio,
    symbol: str,
    side: str,
    exit_price: float,
    strategy: str | None = None,
) -> VirtualPortfolio:
    """平仓: 计算 PnL, 返回新 portfolio

    找到第一个匹配 symbol+side+strategy 的仓位平仓。
    """
    target_idx = None
    for i, pos in enumerate(portfolio.positions):
        if pos.symbol == symbol and pos.side == side:
            if strategy is not None and pos.strategy != strategy:
                continue
            target_idx = i
            break

    if target_idx is None:
        raise ValueError(f"未找到匹配仓位: {symbol} {side}")

    pos = portfolio.positions[target_idx]

    # 计算 PnL
    if pos.side == "long":
        pnl_per_unit = exit_price - pos.entry_price
    else:
        pnl_per_unit = pos.entry_price - exit_price

    pnl = pnl_per_unit * pos.amount * pos.leverage
    margin = pos.amount * pos.entry_price / pos.leverage
    # 扣除往返手续费 (0.05% × 2 = 0.1%)
    pnl -= margin * 0.001
    pnl_pct = pnl / margin * 100 if margin > 0 else 0.0

    trade_record = {
        "symbol": pos.symbol,
        "side": pos.side,
        "entry_price": pos.entry_price,
        "exit_price": exit_price,
        "amount": pos.amount,
        "leverage": pos.leverage,
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 2),
        "strategy": pos.strategy,
        "opened_at": pos.opened_at,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }

    # 移除已平仓仓位
    remaining = [p for i, p in enumerate(portfolio.positions) if i != target_idx]

    return VirtualPortfolio(
        initial_balance=portfolio.initial_balance,
        current_balance=round(portfolio.current_balance + margin + pnl, 4),
        positions=remaining,
        closed_trades=[*portfolio.closed_trades, trade_record],
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def get_unrealized_pnl(
    portfolio: VirtualPortfolio,
    prices: dict[str, float],
) -> float:
    """计算所有仓位的未实现盈亏"""
    total_pnl = 0.0
    for pos in portfolio.positions:
        current_price = prices.get(pos.symbol)
        if current_price is None:
            continue
        if pos.side == "long":
            pnl_per_unit = current_price - pos.entry_price
        else:
            pnl_per_unit = pos.entry_price - current_price
        total_pnl += pnl_per_unit * pos.amount * pos.leverage
    return round(total_pnl, 4)


def get_portfolio_summary(
    portfolio: VirtualPortfolio,
    prices: dict[str, float] | None = None,
) -> dict:
    """虚拟盘汇总"""
    unrealized = get_unrealized_pnl(portfolio, prices) if prices else 0.0
    realized = sum(t.get("pnl", 0) for t in portfolio.closed_trades)
    total_pnl = realized + unrealized

    return {
        "initial_balance": portfolio.initial_balance,
        "current_balance": portfolio.current_balance,
        "unrealized_pnl": unrealized,
        "realized_pnl": round(realized, 4),
        "total_pnl": round(total_pnl, 4),
        "total_return_pct": round(
            total_pnl / portfolio.initial_balance * 100, 2
        ) if portfolio.initial_balance > 0 else 0.0,
        "open_positions": len(portfolio.positions),
        "closed_trades": len(portfolio.closed_trades),
    }


# ─── 持久化 ────────────────────────────────────────────────────────


def save_portfolio(portfolio: VirtualPortfolio, strategy: str) -> None:
    """原子写入 data/output/virtual/{strategy}_portfolio.json"""
    VIRTUAL_DIR.mkdir(parents=True, exist_ok=True)
    path = VIRTUAL_DIR / f"{strategy}_portfolio.json"

    data = {
        "initial_balance": portfolio.initial_balance,
        "current_balance": portfolio.current_balance,
        "positions": [asdict(p) for p in portfolio.positions],
        "closed_trades": portfolio.closed_trades,
        "updated_at": portfolio.updated_at,
    }

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(path)
    logger.info("虚拟盘已保存: %s (余额 %.2f)", strategy, portfolio.current_balance)


def load_portfolio(
    strategy: str,
    initial_balance: float = 10000.0,
) -> VirtualPortfolio:
    """加载或创建新 portfolio"""
    path = VIRTUAL_DIR / f"{strategy}_portfolio.json"
    if not path.exists():
        return VirtualPortfolio(
            initial_balance=initial_balance,
            current_balance=initial_balance,
            positions=[],
            closed_trades=[],
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("虚拟盘加载失败 %s: %s, 创建新盘", strategy, e)
        return VirtualPortfolio(
            initial_balance=initial_balance,
            current_balance=initial_balance,
            positions=[],
            closed_trades=[],
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    positions = [
        VirtualPosition(**p) for p in data.get("positions", [])
    ]

    return VirtualPortfolio(
        initial_balance=data.get("initial_balance", initial_balance),
        current_balance=data.get("current_balance", initial_balance),
        positions=positions,
        closed_trades=data.get("closed_trades", []),
        updated_at=data.get("updated_at", ""),
    )
