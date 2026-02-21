"""交易成本模型

封装永续合约交易的各类成本：手续费、滑点、资金费率。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """交易成本配置"""

    taker_fee_pct: float = 0.04  # Taker 手续费 (%)
    slippage_pct: float = 0.05  # 滑点 (%)
    funding_rate_per_8h: float = 0.01  # 8小时资金费率 (%)


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
) -> TradeCosts:
    """计算单笔交易的总成本

    - 手续费和滑点与杠杆成正比（名义价值）
    - 资金费率按持仓时长线性累积（每8小时结算一次）
    """
    # 手续费: 名义价值 = 保证金 × 杠杆，费率作用于名义价值
    entry_fee = config.taker_fee_pct * leverage
    exit_fee = config.taker_fee_pct * leverage

    # 滑点: 同样作用于名义价值
    slippage = config.slippage_pct * leverage

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
