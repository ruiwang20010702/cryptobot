"""资金感知策略测试

覆盖: 层级检测、参数合并、余额获取、Prompt addon、不可变性
"""

from unittest.mock import patch


from cryptobot.capital_strategy import (
    detect_capital_tier,
    merge_regime_capital_params,
    get_balance_from_freqtrade,
    _DEFAULT_TIERS,
)
from cryptobot.evolution.capital_prompts import get_capital_addon


# ─── 层级检测 ─────────────────────────────────────────────────────────────

class TestDetectCapitalTier:
    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_zero_balance(self, _):
        result = detect_capital_tier(0)
        assert result["tier"] == "micro"
        assert result["balance"] == 0

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_micro_boundary_below(self, _):
        result = detect_capital_tier(499)
        assert result["tier"] == "micro"
        assert result["params"]["max_coins"] == 2
        assert result["params"]["lev_cap"] == 3
        assert result["params"]["max_positions"] == 2

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_small_boundary_exact(self, _):
        """$500 应该是 small"""
        result = detect_capital_tier(500)
        assert result["tier"] == "small"
        assert result["params"]["max_coins"] == 3

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_small_upper(self, _):
        result = detect_capital_tier(1999)
        assert result["tier"] == "small"

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_medium_boundary(self, _):
        result = detect_capital_tier(2000)
        assert result["tier"] == "medium"
        assert result["params"]["conf_boost"] == 0
        assert result["params"]["max_coins"] == 5

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_large_boundary(self, _):
        result = detect_capital_tier(10000)
        assert result["tier"] == "large"
        assert result["params"]["max_coins"] == 10
        assert result["params"]["max_positions"] == 5

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_very_large(self, _):
        result = detect_capital_tier(50000)
        assert result["tier"] == "large"

    @patch("cryptobot.capital_strategy.load_settings", return_value={
        "capital_strategy": {
            "micro": {"max_coins": 1, "lev_cap": 2},
        }
    })
    def test_user_override(self, _):
        """用户可通过 settings.yaml 覆盖层级参数"""
        result = detect_capital_tier(100)
        assert result["tier"] == "micro"
        assert result["params"]["max_coins"] == 1
        assert result["params"]["lev_cap"] == 2
        # 未覆盖的字段保持默认
        assert result["params"]["max_positions"] == 2

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_returns_balance(self, _):
        result = detect_capital_tier(750.5)
        assert result["balance"] == 750.5

    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_params_exclude_range_fields(self, _):
        """params 不应包含 min_balance/max_balance"""
        result = detect_capital_tier(100)
        assert "min_balance" not in result["params"]
        assert "max_balance" not in result["params"]


# ─── 参数合并 ─────────────────────────────────────────────────────────────

class TestMergeParams:
    def test_micro_trending(self):
        """micro + trending: conf_boost=15 叠加到 trending min_conf=55"""
        regime = {"min_confidence": 55, "max_leverage": 5, "trailing_stop": True}
        capital = {"conf_boost": 15, "lev_cap": 3, "max_positions": 1,
                   "max_coins": 2, "take_profit_style": "quick",
                   "preferred_symbols": ["BTCUSDT"]}

        merged = merge_regime_capital_params(regime, capital)
        assert merged["min_confidence"] == 70  # 55 + 15
        assert merged["max_leverage"] == 3     # min(5, 3)
        assert merged["trailing_stop"] is True
        assert merged["max_positions"] == 1
        assert merged["max_coins"] == 2
        assert merged["take_profit_style"] == "quick"
        assert merged["preferred_symbols"] == ["BTCUSDT"]

    def test_micro_volatile(self):
        """micro + volatile: 双重保守"""
        regime = {"min_confidence": 70, "max_leverage": 2, "trailing_stop": True}
        capital = {"conf_boost": 15, "lev_cap": 3, "max_positions": 1,
                   "max_coins": 2, "take_profit_style": "quick",
                   "preferred_symbols": []}

        merged = merge_regime_capital_params(regime, capital)
        assert merged["min_confidence"] == 85  # 70 + 15
        assert merged["max_leverage"] == 2     # min(2, 3)

    def test_medium_ranging(self):
        """medium + ranging: conf_boost=0，不改变行为"""
        regime = {"min_confidence": 65, "max_leverage": 3, "trailing_stop": False}
        capital = {"conf_boost": 0, "lev_cap": 5, "max_positions": 3,
                   "max_coins": 5, "take_profit_style": "standard",
                   "preferred_symbols": []}

        merged = merge_regime_capital_params(regime, capital)
        assert merged["min_confidence"] == 65  # 65 + 0
        assert merged["max_leverage"] == 3     # min(3, 5)

    def test_large_trending(self):
        """large + trending: 完全不改变原行为"""
        regime = {"min_confidence": 55, "max_leverage": 5, "trailing_stop": True}
        capital = {"conf_boost": 0, "lev_cap": 5, "max_positions": 5,
                   "max_coins": 10, "take_profit_style": "standard",
                   "preferred_symbols": []}

        merged = merge_regime_capital_params(regime, capital)
        assert merged["min_confidence"] == 55
        assert merged["max_leverage"] == 5

    def test_immutability(self):
        """合并不应修改原始字典"""
        regime = {"min_confidence": 55, "max_leverage": 5, "trailing_stop": True}
        capital = {"conf_boost": 15, "lev_cap": 3, "max_positions": 1,
                   "max_coins": 2, "take_profit_style": "quick",
                   "preferred_symbols": []}

        regime_copy = dict(regime)
        capital_copy = dict(capital)

        merge_regime_capital_params(regime, capital)

        assert regime == regime_copy
        assert capital == capital_copy


# ─── 余额获取 ─────────────────────────────────────────────────────────────

class TestGetBalance:
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_normal_balance(self, mock_api):
        mock_api.return_value = {
            "currencies": [
                {"currency": "USDT", "balance": 850.5},
                {"currency": "BTC", "balance": 0.001},
            ]
        }
        assert get_balance_from_freqtrade() == 850.5

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_offline_returns_zero(self, mock_api):
        """Freqtrade 离线时返回 0"""
        mock_api.return_value = None
        assert get_balance_from_freqtrade() == 0.0

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_zero_balance_returns_zero(self, mock_api):
        """余额为 0 时返回 0"""
        mock_api.return_value = {
            "currencies": [{"currency": "USDT", "balance": 0}]
        }
        assert get_balance_from_freqtrade() == 0.0

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_no_usdt_returns_zero(self, mock_api):
        """无 USDT 余额时返回 0"""
        mock_api.return_value = {"currencies": [{"currency": "BTC", "balance": 1}]}
        assert get_balance_from_freqtrade() == 0.0


# ─── Prompt Addon ─────────────────────────────────────────────────────────

class TestCapitalPromptAddon:
    def test_micro_trader(self):
        addon = get_capital_addon("micro", "TRADER")
        assert "极度挑剔" in addon
        assert "80" in addon

    def test_micro_risk(self):
        addon = get_capital_addon("micro", "RISK_MANAGER")
        assert "1 个持仓" in addon
        assert "3x" in addon

    def test_micro_analyst(self):
        addon = get_capital_addon("micro", "ANALYST")
        assert "不确定因素" in addon

    def test_small_trader(self):
        addon = get_capital_addon("small", "TRADER")
        assert "保守" in addon

    def test_small_risk(self):
        addon = get_capital_addon("small", "RISK_MANAGER")
        assert "2 个持仓" in addon

    def test_medium_returns_empty(self):
        """medium 层级不注入任何 addon"""
        assert get_capital_addon("medium", "TRADER") == ""
        assert get_capital_addon("medium", "RISK_MANAGER") == ""
        assert get_capital_addon("medium", "ANALYST") == ""

    def test_large_returns_empty(self):
        """large 层级不注入任何 addon"""
        assert get_capital_addon("large", "TRADER") == ""
        assert get_capital_addon("large", "RISK_MANAGER") == ""

    def test_unknown_tier_returns_empty(self):
        assert get_capital_addon("unknown", "TRADER") == ""

    def test_unknown_role_returns_empty(self):
        assert get_capital_addon("micro", "UNKNOWN") == ""


# ─── 不可变性 ─────────────────────────────────────────────────────────────

class TestImmutability:
    @patch("cryptobot.capital_strategy.load_settings", return_value={})
    def test_default_tiers_not_mutated(self, _):
        """调用 detect 不应修改 _DEFAULT_TIERS"""
        import copy
        original = copy.deepcopy(_DEFAULT_TIERS)
        detect_capital_tier(100)
        detect_capital_tier(1000)
        detect_capital_tier(5000)
        assert _DEFAULT_TIERS == original
