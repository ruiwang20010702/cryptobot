"""网格交易模块

支撑/阻力之间设定等距买卖网格，适合长期震荡区间。
虚拟盘运行，使用 virtual_portfolio 基础设施。
"""

import json
import logging
from dataclasses import dataclass, asdict
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

GRID_STATE_DIR = DATA_OUTPUT_DIR / "virtual"


@dataclass(frozen=True)
class GridConfig:
    symbol: str
    upper_price: float       # 网格上界
    lower_price: float       # 网格下界
    grid_count: int          # 网格数量 (5-20)
    total_investment: float  # 总投资额
    leverage: int            # 杠杆 (建议 1-2)


@dataclass(frozen=True)
class GridLevel:
    price: float
    side: str               # "buy" | "sell"
    amount: float           # 每格数量
    filled: bool


@dataclass(frozen=True)
class GridState:
    config: GridConfig
    levels: list[GridLevel]
    realized_pnl: float
    grid_count_filled: int
    created_at: str


def create_grid(config: GridConfig, wide_mode: bool = False) -> GridState:
    """生成等距网格

    从 lower_price 到 upper_price 均匀分布 grid_count 个价位。
    低于中间价的为 buy level，高于的为 sell level。
    wide_mode=True 时网格间距 ×2（等效 2×ATR），适合高波动市场。
    """
    if config.grid_count < 2:
        raise ValueError("网格数量至少为 2")
    if config.upper_price <= config.lower_price:
        raise ValueError("上界必须大于下界")

    effective_count = config.grid_count // 2 if wide_mode else config.grid_count
    effective_count = max(2, effective_count)
    step = (config.upper_price - config.lower_price) / effective_count
    mid_price = (config.upper_price + config.lower_price) / 2

    # 每格投资额
    per_grid = config.total_investment / config.grid_count
    # 每格数量 (以 mid_price 估算)
    amount_per_grid = per_grid / mid_price if mid_price > 0 else 0

    levels: list[GridLevel] = []
    for i in range(effective_count + 1):
        price = round(config.lower_price + step * i, 8)
        side = "buy" if price < mid_price else "sell"
        levels.append(GridLevel(
            price=price,
            side=side,
            amount=round(amount_per_grid, 8),
            filled=False,
        ))

    return GridState(
        config=config,
        levels=levels,
        realized_pnl=0.0,
        grid_count_filled=0,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def auto_detect_range(
    symbol: str,
    days: int = 30,
) -> tuple[float, float]:
    """自动检测支撑/阻力范围

    方法: 取最近 N 天 K 线的 percentile_10 / percentile_90
    """
    try:
        from cryptobot.indicators.calculator import load_klines
        df = load_klines(symbol, "1h")
    except Exception as e:
        raise ValueError(f"加载 {symbol} K 线失败: {e}") from e

    # 取最近 days * 24 根 1h K 线
    bars = days * 24
    closes = df["close"].astype(float).tolist()
    if len(closes) > bars:
        closes = closes[-bars:]

    if len(closes) < 10:
        raise ValueError(f"{symbol} K 线数据不足")

    sorted_closes = sorted(closes)
    n = len(sorted_closes)

    # percentile 10 和 90
    idx_10 = max(0, int(n * 0.10))
    idx_90 = min(n - 1, int(n * 0.90))

    lower = sorted_closes[idx_10]
    upper = sorted_closes[idx_90]

    return round(lower, 2), round(upper, 2)


def check_grid_triggers(
    state: GridState,
    current_price: float,
    portfolio: VirtualPortfolio,
) -> tuple[GridState, VirtualPortfolio]:
    """检查价格是否触发网格

    价格下穿某 buy level → 买入 (开多仓)
    价格上穿某 sell level → 卖出 (平多仓)
    返回新 state + 新 portfolio (不可变)
    """
    new_levels: list[GridLevel] = []
    realized_pnl = state.realized_pnl
    filled_count = state.grid_count_filled
    new_portfolio = portfolio

    for level in state.levels:
        if level.filled:
            new_levels.append(level)
            continue

        triggered = False

        if level.side == "buy" and current_price <= level.price:
            # 买入: 开多仓
            try:
                pos = VirtualPosition(
                    symbol=state.config.symbol,
                    side="long",
                    entry_price=level.price,
                    amount=level.amount,
                    leverage=state.config.leverage,
                    opened_at=datetime.now(timezone.utc).isoformat(),
                    strategy="grid",
                )
                new_portfolio = open_position(new_portfolio, pos)
                triggered = True
                filled_count += 1
                logger.info(
                    "网格买入 %s @ %.2f, 数量 %.6f",
                    state.config.symbol, level.price, level.amount,
                )
            except ValueError as e:
                logger.warning("网格买入失败: %s", e)

        elif level.side == "sell" and current_price >= level.price:
            # 卖出: 平多仓
            grid_longs = [
                p for p in new_portfolio.positions
                if p.symbol == state.config.symbol
                and p.side == "long"
                and p.strategy == "grid"
            ]
            if grid_longs:
                try:
                    new_portfolio = close_position(
                        new_portfolio,
                        state.config.symbol,
                        "long",
                        level.price,
                        "grid",
                    )
                    # 找到对应的已平仓交易
                    if new_portfolio.closed_trades:
                        last_trade = new_portfolio.closed_trades[-1]
                        realized_pnl += last_trade.get("pnl", 0)
                    triggered = True
                    filled_count += 1
                    logger.info(
                        "网格卖出 %s @ %.2f",
                        state.config.symbol, level.price,
                    )
                except ValueError as e:
                    logger.warning("网格卖出失败: %s", e)

        new_levels.append(GridLevel(
            price=level.price,
            side=level.side,
            amount=level.amount,
            filled=level.filled or triggered,
        ))

    # P14: 浮亏保护 — 单网格浮亏 > 5% 标记关闭
    protected_portfolio = new_portfolio
    for pos in list(new_portfolio.positions):
        if pos.strategy != "grid" or pos.symbol != state.config.symbol:
            continue
        if current_price <= 0 or pos.entry_price <= 0:
            continue
        if pos.side == "long":
            unrealized_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            unrealized_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        if unrealized_pct < -5.0:
            try:
                protected_portfolio = close_position(
                    protected_portfolio, pos.symbol, pos.side, current_price, "grid",
                )
                if protected_portfolio.closed_trades:
                    last_trade = protected_portfolio.closed_trades[-1]
                    realized_pnl += last_trade.get("pnl", 0)
                logger.info(
                    "网格浮亏保护 %s %s @ %.2f (浮亏 %.1f%%)",
                    pos.symbol, pos.side, current_price, unrealized_pct,
                )
            except ValueError:
                pass

    new_state = GridState(
        config=state.config,
        levels=new_levels,
        realized_pnl=round(realized_pnl, 4),
        grid_count_filled=filled_count,
        created_at=state.created_at,
    )

    return new_state, protected_portfolio


def calc_grid_metrics(state: GridState) -> dict:
    """网格统计"""
    total_levels = len(state.levels)
    filled_levels = sum(1 for lv in state.levels if lv.filled)

    return {
        "symbol": state.config.symbol,
        "upper_price": state.config.upper_price,
        "lower_price": state.config.lower_price,
        "grid_count": state.config.grid_count,
        "total_levels": total_levels,
        "filled_levels": filled_levels,
        "fill_rate": round(filled_levels / total_levels, 4) if total_levels > 0 else 0,
        "realized_pnl": state.realized_pnl,
        "created_at": state.created_at,
    }


# ─── 持久化 ────────────────────────────────────────────────────────


def save_grid_state(state: GridState) -> None:
    """保存网格状态"""
    GRID_STATE_DIR.mkdir(parents=True, exist_ok=True)
    symbol = state.config.symbol.lower()
    path = GRID_STATE_DIR / f"grid_{symbol}_state.json"

    data = {
        "config": asdict(state.config),
        "levels": [asdict(lv) for lv in state.levels],
        "realized_pnl": state.realized_pnl,
        "grid_count_filled": state.grid_count_filled,
        "created_at": state.created_at,
    }

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(path)


def load_grid_state(symbol: str) -> GridState | None:
    """加载网格状态"""
    path = GRID_STATE_DIR / f"grid_{symbol.lower()}_state.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    config = GridConfig(**data["config"])
    levels = [GridLevel(**lv) for lv in data.get("levels", [])]

    return GridState(
        config=config,
        levels=levels,
        realized_pnl=data.get("realized_pnl", 0.0),
        grid_count_filled=data.get("grid_count_filled", 0),
        created_at=data.get("created_at", ""),
    )


def run_grid_check(symbol: str) -> dict:
    """运行一次网格检查"""
    state = load_grid_state(symbol)
    if state is None:
        return {"error": f"未找到 {symbol} 的网格状态"}

    cfg = load_settings().get("strategies", {}).get("grid", {})
    virtual_balance = cfg.get("virtual_balance", 10000)
    portfolio = load_portfolio("grid", virtual_balance)

    # 获取当前价格
    try:
        from cryptobot.indicators.calculator import load_klines
        df = load_klines(symbol, "1h")
        current_price = float(df["close"].iloc[-1])
    except Exception as e:
        return {"error": f"获取 {symbol} 价格失败: {e}"}

    new_state, new_portfolio = check_grid_triggers(state, current_price, portfolio)

    # 保存
    save_grid_state(new_state)
    save_portfolio(new_portfolio, "grid")

    metrics = calc_grid_metrics(new_state)
    metrics["current_price"] = current_price

    return metrics
