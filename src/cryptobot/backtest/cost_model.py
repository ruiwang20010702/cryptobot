"""交易成本模型

封装永续合约交易的各类成本：手续费、滑点、资金费率。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """交易成本配置"""

    taker_fee_pct: float = 0.04  # Taker 手续费 (%)
    slippage_pct: float = 0.05  # 滑点 (%)
    funding_rate_per_8h: float = 0.01  # 8小时资金费率 (%)
    volatile_slippage_multiplier: float = 3.0  # volatile 时滑点乘数


@dataclass(frozen=True)
class TradeCosts:
    """单笔交易的成本明细"""

    entry_fee_pct: float  # 入场手续费 (含杠杆)
    exit_fee_pct: float  # 出场手续费 (含杠杆)
    slippage_pct: float  # 滑点成本 (含杠杆)
    funding_pct: float  # 资金费率成本
    total_pct: float  # 总成本 (%)


def calc_trade_costs(
    config: CostConfig,
    duration_hours: float,
    leverage: int = 1,
    regime: str = "",
) -> TradeCosts:
    """计算单笔交易的总成本

    - 手续费和滑点与杠杆成正比（名义价值）
    - 资金费率按持仓时长线性累积（每8小时结算一次）
    - volatile regime 时滑点按 volatile_slippage_multiplier 放大
    """
    # 手续费: 名义价值 = 保证金 × 杠杆，费率作用于名义价值
    entry_fee = config.taker_fee_pct * leverage
    exit_fee = config.taker_fee_pct * leverage

    # 滑点: 同样作用于名义价值, volatile 时放大
    slip_base = config.slippage_pct
    if regime.startswith("volatile"):
        slip_base = slip_base * config.volatile_slippage_multiplier
    slippage = slip_base * leverage

    # 资金费率: 按持仓时长累积，每8小时结算一次
    funding_periods = max(0, duration_hours / 8)
    funding = config.funding_rate_per_8h * funding_periods * leverage

    total = entry_fee + exit_fee + slippage + funding

    return TradeCosts(
        entry_fee_pct=round(entry_fee, 6),
        exit_fee_pct=round(exit_fee, 6),
        slippage_pct=round(slippage, 6),
        funding_pct=round(funding, 6),
        total_pct=round(total, 6),
    )


# Binance 永续合约结算时间 (UTC)
_SETTLEMENT_HOURS = frozenset((0, 8, 16))

# 各币种默认滑点 (%)
_SYMBOL_SLIPPAGE: dict[str, float] = {
    "BTCUSDT": 0.01,
    "ETHUSDT": 0.02,
}
_DEFAULT_HOURLY_SLIPPAGE = 0.03


@dataclass(frozen=True)
class HourlyCostProfile:
    """每小时成本概况"""

    hour_utc: int  # 0-23
    avg_slippage: float  # 该时段平均滑点 %
    funding_rate_applies: bool  # 该时段是否有资金费率结算
    total_cost: float  # 总成本 %


def calc_hourly_cost_profile(
    symbol: str,
    leverage: int = 3,
    config: CostConfig | None = None,
) -> list[HourlyCostProfile]:
    """生成 24 小时成本概况

    结算时段 (0, 8, 16 UTC) funding_rate_applies=True
    滑点按币种用默认值
    total_cost = taker_fee * 2 + slippage + (funding_rate if applies else 0)
    所有成本均乘以杠杆 (名义价值)
    """
    cfg = config or CostConfig()
    slippage = _SYMBOL_SLIPPAGE.get(symbol, _DEFAULT_HOURLY_SLIPPAGE)
    base_cost = (cfg.taker_fee_pct * 2 + slippage) * leverage

    profiles = []
    for hour in range(24):
        is_settlement = hour in _SETTLEMENT_HOURS
        funding = cfg.funding_rate_per_8h * leverage if is_settlement else 0.0
        total = base_cost + funding
        profiles.append(
            HourlyCostProfile(
                hour_utc=hour,
                avg_slippage=round(slippage * leverage, 6),
                funding_rate_applies=is_settlement,
                total_cost=round(total, 6),
            )
        )
    return profiles
