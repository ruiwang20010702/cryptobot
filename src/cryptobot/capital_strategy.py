"""资金感知策略调整

根据账户余额自动调整交易参数:
- micro (<$500): 保守，最多 2 币，杠杆 ≤3x
- small ($500-2K): 保守，最多 3 币
- medium ($2K-10K): 标准（不改变现有行为）
- large ($10K+): 灵活（不改变现有行为）

设计原则: 与 regime 正交叠加，最终参数取更严格值。
medium/large 层级不改变现有行为（向后兼容）。
"""

import logging
from datetime import datetime, timezone

from cryptobot.config import load_settings

logger = logging.getLogger(__name__)

# ─── 默认层级定义 ────────────────────────────────────────────────────────

_DEFAULT_TIERS = {
    "micro": {
        "min_balance": 0,
        "max_balance": 500,
        "max_coins": 2,
        "conf_boost": 5,
        "lev_cap": 3,
        "max_positions": 2,
        "take_profit_style": "quick",
        "preferred_symbols": ["BTCUSDT", "ETHUSDT"],
    },
    "small": {
        "min_balance": 500,
        "max_balance": 2000,
        "max_coins": 3,
        "conf_boost": 5,
        "lev_cap": 3,
        "max_positions": 2,
        "take_profit_style": "moderate",
        "preferred_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    },
    "medium": {
        "min_balance": 2000,
        "max_balance": 10000,
        "max_coins": 5,
        "conf_boost": 0,
        "lev_cap": 5,
        "max_positions": 3,
        "take_profit_style": "standard",
        "preferred_symbols": [],
    },
    "large": {
        "min_balance": 10000,
        "max_balance": float("inf"),
        "max_coins": 10,
        "conf_boost": 0,
        "lev_cap": 5,
        "max_positions": 5,
        "take_profit_style": "standard",
        "preferred_symbols": [],
    },
}

# 层级检测顺序（从小到大）
_TIER_ORDER = ["micro", "small", "medium", "large"]


def _load_tier_config() -> dict:
    """从 settings.yaml 加载用户覆盖的层级配置

    Returns:
        合并后的层级配置（默认值 + 用户覆盖）
    """
    settings = load_settings()
    user_cfg = settings.get("capital_strategy", {})
    if not user_cfg:
        return {k: dict(v) for k, v in _DEFAULT_TIERS.items()}

    merged = {}
    for tier_name, defaults in _DEFAULT_TIERS.items():
        tier_override = user_cfg.get(tier_name, {})
        merged[tier_name] = {**defaults, **tier_override}
    return merged


def detect_capital_tier(balance: float) -> dict:
    """根据账户余额检测资金层级

    Args:
        balance: 账户余额 (USDT)

    Returns:
        {"tier": "micro", "balance": 300.0, "params": {...}}
    """
    tiers = _load_tier_config()

    for tier_name in _TIER_ORDER:
        params = tiers[tier_name]
        if params["min_balance"] <= balance < params["max_balance"]:
            return {
                "tier": tier_name,
                "balance": balance,
                "params": {k: v for k, v in params.items()
                           if k not in ("min_balance", "max_balance")},
            }

    # balance >= inf 不会发生，但兜底
    return {
        "tier": "large",
        "balance": balance,
        "params": {k: v for k, v in tiers["large"].items()
                   if k not in ("min_balance", "max_balance")},
    }


def merge_regime_capital_params(
    regime_params: dict,
    capital_params: dict,
    drawdown_factor: float = 1.0,
) -> dict:
    """合并 regime 和 capital 参数，取更严格值

    规则:
    - min_confidence: 取更高值 (regime_min + capital_boost)
    - max_leverage: 取更低值，再乘以 drawdown_factor
    - max_positions, max_coins: 取 capital 值（regime 无此概念）
    - trailing_stop: regime 控制
    - take_profit_style: capital 控制

    Args:
        regime_params: {"min_confidence": 55, "max_leverage": 5, "trailing_stop": True}
        capital_params: {"conf_boost": 15, "lev_cap": 3, "max_positions": 1, ...}
        drawdown_factor: 回撤杠杆缩放因子 (0~1.0)，默认 1.0 不缩放

    Returns:
        合并后的参数字典
    """
    regime_min_conf = regime_params.get("min_confidence", 55)
    capital_boost = capital_params.get("conf_boost", 0)

    base_leverage = min(
        regime_params.get("max_leverage", 5),
        capital_params.get("lev_cap", 5),
    )
    adjusted_leverage = max(1, int(base_leverage * drawdown_factor))

    return {
        "min_confidence": regime_min_conf + capital_boost,
        "max_leverage": adjusted_leverage,
        "trailing_stop": regime_params.get("trailing_stop", False),
        "max_positions": capital_params.get("max_positions", 5),
        "max_coins": capital_params.get("max_coins", 5),
        "take_profit_style": capital_params.get("take_profit_style", "standard"),
        "preferred_symbols": capital_params.get("preferred_symbols", []),
    }


# ─── 回撤感知动态杠杆 ──────────────────────────────────────────────────

_DRAWDOWN_TIERS = [
    (0.20, 1.0),   # 回撤 0-20%: 不变
    (0.40, 0.5),   # 回撤 20-40%: 减半
    (1.00, 0.25),  # 回撤 >40%: 1/4
]


def calc_drawdown_factor(lookback_days: int = 7) -> dict:
    """从 journal 已平仓交易计算近期回撤 → 杠杆缩放因子

    按时间排序构建净值曲线，计算 max drawdown，映射到杠杆因子。

    Returns:
        {"drawdown_pct": float, "leverage_factor": float,
         "sample_size": int, "tier": str}
    """
    try:
        from cryptobot.journal.storage import get_all_records
        from datetime import timedelta

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()

        closed = [
            r for r in get_all_records()
            if r.status == "closed"
            and r.timestamp >= cutoff
            and r.actual_pnl_pct is not None
        ]

        if not closed:
            return {
                "drawdown_pct": 0.0,
                "leverage_factor": 1.0,
                "sample_size": 0,
                "tier": "normal",
            }

        # 按时间排序，构建净值曲线
        closed.sort(key=lambda r: r.timestamp)
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in closed:
            equity *= (1 + r.actual_pnl_pct / 100)
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # 映射到杠杆因子
        factor = _DRAWDOWN_TIERS[-1][1]
        tier = "severe"
        for threshold, f in _DRAWDOWN_TIERS:
            if max_dd <= threshold:
                factor = f
                if threshold <= 0.20:
                    tier = "normal"
                elif threshold <= 0.40:
                    tier = "moderate"
                else:
                    tier = "severe"
                break

        return {
            "drawdown_pct": round(max_dd * 100, 1),
            "leverage_factor": factor,
            "sample_size": len(closed),
            "tier": tier,
        }
    except Exception as e:
        logger.error("回撤因子计算异常: %s", e)
        return {
            "drawdown_pct": 0.0,
            "leverage_factor": 1.0,
            "sample_size": 0,
            "tier": "error",
        }


def _extract_usdt_balance(balance_data: dict | None) -> float:
    """从 Freqtrade /balance 响应中提取 USDT 余额

    Args:
        balance_data: ft_api_get("/balance") 的返回值

    Returns:
        余额 (USDT)，无数据时返回 0.0
    """
    if not balance_data:
        return 0.0
    for cur in balance_data.get("currencies", []):
        if cur.get("currency") == "USDT":
            return float(cur.get("balance", 0))
    return 0.0


def get_balance_from_freqtrade() -> float:
    """从 Freqtrade API 获取 USDT 余额

    Freqtrade 离线时回退到 settings.yaml 中 capital_strategy.mock_balance。

    Returns:
        余额 (USDT)
    """
    from cryptobot.freqtrade_api import ft_api_get

    balance = _extract_usdt_balance(ft_api_get("/balance"))
    if balance > 0:
        return balance

    # Freqtrade 离线 → 尝试 mock_balance
    settings = load_settings()
    mock = settings.get("capital_strategy", {}).get("mock_balance", 0.0)
    if mock > 0:
        logger.info("Freqtrade 离线，使用 mock_balance: $%.0f", mock)
        return float(mock)

    logger.warning("Freqtrade 离线或余额为 0，返回 0")
    return 0.0
