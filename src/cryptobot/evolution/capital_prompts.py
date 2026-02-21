"""资金层级 Prompt Addon

不同资金层级使用不同 prompt 偏好:
- micro: 极度保守，只做最确定的交易
- small: 保守，控制风险
- medium/large: 不注入额外 prompt（向后兼容）
"""

# ─── Addon 定义 ─────────────────────────────────────────

_MICRO_TRADER = """\

## 资金层级偏好: 微型账户 (<$500)
- 极度挑剔，只交易置信度 ≥80 的最确定机会
- 快进快出，不留过夜持仓风险
- 优先 BTC/ETH 等流动性最好的币种
- 单笔止损不超过保证金 1.5%
- 严格遵循入场区间，不追高杀低"""

_MICRO_RISK = """\

## 资金层级偏好: 微型账户 (<$500)
- 最多持有 1 个持仓，绝不同时持有多个
- 杠杆严格限制在 3x 以下
- 单笔最大亏损 ≤1.5% 账户余额
- 爆仓距离必须 >50%
- 任何不确定因素都应拒绝"""

_MICRO_ANALYST = """\

## 资金层级注意: 微型账户
- 任何不确定因素应降低 confidence 评分
- 数据不完整时 confidence 应低于 50
- 优先评估下行风险"""

_SMALL_TRADER = """\

## 资金层级偏好: 小型账户 ($500-2K)
- 保守交易，置信度 ≥65 才考虑入场
- 控制持仓数量，最多同时 2 个持仓
- 优先高流动性币种 (BTC/ETH/SOL)
- 分批止盈，不贪心"""

_SMALL_RISK = """\

## 资金层级偏好: 小型账户 ($500-2K)
- 最多持有 2 个持仓
- 杠杆限制在 3x 以下
- 单笔最大亏损 ≤2% 账户余额
- 同方向持仓不超过 1 个"""

_SMALL_ANALYST = """\

## 资金层级注意: 小型账户
- 不确定因素应适度降低 confidence 评分
- 优先评估风险收益比"""

# ─── Addon 映射 ──────────────────────────────────────────

_CAPITAL_ADDONS = {
    "micro": {
        "TRADER": _MICRO_TRADER,
        "RISK_MANAGER": _MICRO_RISK,
        "ANALYST": _MICRO_ANALYST,
    },
    "small": {
        "TRADER": _SMALL_TRADER,
        "RISK_MANAGER": _SMALL_RISK,
        "ANALYST": _SMALL_ANALYST,
    },
    # medium/large: 不注入任何 addon（向后兼容）
}


def get_capital_addon(tier: str, role: str) -> str:
    """获取资金层级 + 角色对应的 addon 段落

    Args:
        tier: 资金层级 ("micro" / "small" / "medium" / "large")
        role: 角色键 ("TRADER" / "RISK_MANAGER" / "ANALYST")

    Returns:
        addon 文本，medium/large 返回空字符串
    """
    tier_map = _CAPITAL_ADDONS.get(tier, {})
    return tier_map.get(role, "")
