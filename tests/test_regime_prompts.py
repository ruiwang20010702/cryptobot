"""Regime Prompts 单元测试"""

from cryptobot.evolution.regime_prompts import get_regime_addon


class TestRegimePrompts:
    def test_trending_trader(self):
        addon = get_regime_addon("trending", "TRADER")
        assert "趋势市" in addon
        assert "顺势交易" in addon

    def test_trending_risk(self):
        addon = get_regime_addon("trending", "RISK_MANAGER")
        assert "趋势市" in addon
        assert "尾随止损" in addon

    def test_trending_analyst(self):
        addon = get_regime_addon("trending", "ANALYST")
        assert "趋势市" in addon

    def test_ranging_trader(self):
        addon = get_regime_addon("ranging", "TRADER")
        assert "震荡市" in addon
        assert "均值回归" in addon

    def test_volatile_trader(self):
        addon = get_regime_addon("volatile", "TRADER")
        assert "高波动" in addon
        assert "观望" in addon

    def test_unknown_regime(self):
        assert get_regime_addon("unknown", "TRADER") == ""

    def test_unknown_role(self):
        assert get_regime_addon("trending", "UNKNOWN") == ""

    def test_empty_regime(self):
        assert get_regime_addon("", "TRADER") == ""

    def test_all_regimes_have_three_roles(self):
        for regime in ("trending", "ranging", "volatile"):
            for role in ("TRADER", "RISK_MANAGER", "ANALYST"):
                addon = get_regime_addon(regime, role)
                assert addon, f"{regime}/{role} addon 为空"
