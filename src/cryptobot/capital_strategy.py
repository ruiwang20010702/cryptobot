"""资金感知策略调整

根据账户余额自动调整交易参数:
- micro (<$500): 极度保守，最多 2 币，杠杆 ≤3x
- small ($500-2K): 保守，最多 3 币
- medium ($2K-10K): 标准（不改变现有行为）
- large ($10K+): 灵活（不改变现有行为）

设计原则: 与 regime 正交叠加，最终参数取更严格值。
medium/large 层级不改变现有行为（向后兼容）。
"""

import logging

from cryptobot.config import load_settings

logger = logging.getLogger(__name__)

# ─── 默认层级定义 ────────────────────────────────────────────────────────

_DEFAULT_TIERS = {
    "micro": {
        "min_balance": 0,
        "max_balance": 500,
        "max_coins": 2,
        "conf_boost": 15,
        "lev_cap": 3,
        "max_positions": 1,
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


def merge_regime_capital_params(regime_params: dict, capital_params: dict) -> dict:
    """合并 regime 和 capital 参数，取更严格值

    规则:
    - min_confidence: 取更高值 (regime_min + capital_boost)
    - max_leverage: 取更低值
    - max_positions, max_coins: 取 capital 值（regime 无此概念）
    - trailing_stop: regime 控制
    - take_profit_style: capital 控制

    Args:
        regime_params: {"min_confidence": 55, "max_leverage": 5, "trailing_stop": True}
        capital_params: {"conf_boost": 15, "lev_cap": 3, "max_positions": 1, ...}

    Returns:
        合并后的参数字典
    """
    regime_min_conf = regime_params.get("min_confidence", 55)
    capital_boost = capital_params.get("conf_boost", 0)

    return {
        "min_confidence": regime_min_conf + capital_boost,
        "max_leverage": min(
            regime_params.get("max_leverage", 5),
            capital_params.get("lev_cap", 5),
        ),
        "trailing_stop": regime_params.get("trailing_stop", False),
        "max_positions": capital_params.get("max_positions", 5),
        "max_coins": capital_params.get("max_coins", 5),
        "take_profit_style": capital_params.get("take_profit_style", "standard"),
        "preferred_symbols": capital_params.get("preferred_symbols", []),
    }


def get_balance_from_freqtrade() -> float:
    """从 Freqtrade API 获取 USDT 余额

    Returns:
        余额 (USDT)，离线时默认返回 1000.0
    """
    from cryptobot.freqtrade_api import ft_api_get

    balance_data = ft_api_get("/balance")
    if balance_data:
        for cur in balance_data.get("currencies", []):
            if cur.get("currency") == "USDT":
                val = float(cur.get("balance", 0))
                if val > 0:
                    return val

    logger.warning("Freqtrade 离线或余额为 0，使用默认 $1000")
    return 1000.0
