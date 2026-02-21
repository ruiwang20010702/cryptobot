"""Regime 级 Prompt Addon

不同市场状态使用不同 prompt 偏好:
- trending: 趋势跟踪、动量突破
- ranging: 支撑阻力、均值回归
- volatile: 风控优先、缩仓降杠杆
"""

# ─── Addon 定义 ─────────────────────────────────────────

_TRENDING_TRADER = """\

## 当前市场状态偏好: 趋势市
- 优先顺势交易，突破后追入
- 可适度宽止损 (1.5x ATR)，让趋势运行
- 分批建仓: 突破入 50%，回踩确认加 50%
- 关注 EMA 多头排列 + ADX>25 + 量能放大共振
- 趋势途中避免逆势操作"""

_TRENDING_RISK = """\

## 当前市场状态偏好: 趋势市
- 允许较宽止损空间，但必须跟随尾随止损
- 顺势持仓可适度放宽仓位上限
- 逆势信号应从严审核"""

_TRENDING_ANALYST = """\

## 当前市场状态: 趋势市
- 重点关注趋势延续信号: EMA 排列、ADX 强度、MACD 方向
- 突破/回踩确认模式优先级最高"""

_RANGING_TRADER = """\

## 当前市场状态偏好: 震荡市
- 优先在支撑/阻力附近反向操作，均值回归策略
- 止损要紧 (1x ATR)，震荡市不宜扛单
- 仓位偏小，单笔不超过标准仓位 70%
- 关注 RSI 超买超卖 + BB 带上下轨 + 前高前低
- 突破信号需多次确认，防假突破"""

_RANGING_RISK = """\

## 当前市场状态偏好: 震荡市
- 严格控制仓位，建议降低杠杆
- 盈亏比门槛提高到 2.0
- 假突破风险高，入场信号需多重确认"""

_RANGING_ANALYST = """\

## 当前市场状态: 震荡市
- 重点关注支撑阻力位: Pivot/Fibonacci/前高前低
- RSI 超买超卖信号权重提高
- 量能萎缩时趋势信号可靠度降低"""

_VOLATILE_TRADER = """\

## 当前市场状态偏好: 高波动市
- 优先观望，只在极高置信度 (>75) 时入场
- 宽止损 (2x ATR) 但仓位大幅缩减 (标准仓位 50%)
- 杠杆限制在 2x 以下
- 等待波动率收敛再寻找入场机会
- 极端行情下考虑不交易"""

_VOLATILE_RISK = """\

## 当前市场状态偏好: 高波动市
- 杠杆上限降至 2x
- 仓位上限降至标准的 50%
- 置信度门槛提高到 70+
- 密切关注爆仓距离，安全阈值提高到 40%"""

_VOLATILE_ANALYST = """\

## 当前市场状态: 高波动市
- 波动率数据 (ATR/BB width) 权重提升
- 关注清算聚集区域和资金费率极值
- 技术指标可靠度降低，以量价和资金流为准"""

# ─── Addon 映射 ──────────────────────────────────────────

_REGIME_ADDONS = {
    "trending": {
        "TRADER": _TRENDING_TRADER,
        "RISK_MANAGER": _TRENDING_RISK,
        "ANALYST": _TRENDING_ANALYST,
    },
    "ranging": {
        "TRADER": _RANGING_TRADER,
        "RISK_MANAGER": _RANGING_RISK,
        "ANALYST": _RANGING_ANALYST,
    },
    "volatile": {
        "TRADER": _VOLATILE_TRADER,
        "RISK_MANAGER": _VOLATILE_RISK,
        "ANALYST": _VOLATILE_ANALYST,
    },
}


def get_regime_addon(regime: str, role: str) -> str:
    """获取 regime + 角色对应的 addon 段落

    Args:
        regime: 市场状态 ("trending" / "ranging" / "volatile")
        role: 角色键 ("TRADER" / "RISK_MANAGER" / "ANALYST")

    Returns:
        addon 文本，无匹配则返回空字符串
    """
    regime_map = _REGIME_ADDONS.get(regime, {})
    return regime_map.get(role, "")
