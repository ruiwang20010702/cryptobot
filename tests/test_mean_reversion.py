"""布林带均值回归策略测试

覆盖:
- check_bb_entry: 做多/做空/无信号/数据不完整
- calc_bb_confidence: RSI 极端度/量能加分
- signal_to_dict: 格式转换
"""

from cryptobot.strategy.mean_reversion import (
    MeanReversionSignal,
    calc_bb_confidence,
    check_bb_entry,
    signal_to_dict,
)


class TestCheckBBEntry:
    def test_long_signal(self):
        """价格 <= bb_lower 且 RSI < 35 -> 做多"""
        tech = {
            "latest": {
                "close": 95,
                "bb_lower": 96,
                "bb_upper": 104,
                "bb_mid": 100,
                "rsi_14": 25,
                "atr_14": 2.0,
                "volume_ratio": 1.3,
            }
        }
        sig = check_bb_entry("BTCUSDT", tech)
        assert sig is not None
        assert sig.action == "long"
        assert sig.stop_loss == 94.0  # 96 - 2
        assert sig.take_profit == [{"price": 100, "ratio": 1.0}]
        assert sig.strategy_type == "bb_mean_reversion"
        assert sig.symbol == "BTCUSDT"

    def test_short_signal(self):
        """价格 >= bb_upper 且 RSI > 65 -> 做空"""
        tech = {
            "latest": {
                "close": 105,
                "bb_lower": 96,
                "bb_upper": 104,
                "bb_mid": 100,
                "rsi_14": 75,
                "atr_14": 2.0,
                "volume_ratio": 1.6,
            }
        }
        sig = check_bb_entry("ETHUSDT", tech)
        assert sig is not None
        assert sig.action == "short"
        assert sig.stop_loss == 106.0  # 104 + 2
        assert sig.take_profit == [{"price": 100, "ratio": 1.0}]
        assert sig.symbol == "ETHUSDT"

    def test_no_signal_normal_price(self):
        """价格在布林带内 -> 无信号"""
        tech = {
            "latest": {
                "close": 100,
                "bb_lower": 96,
                "bb_upper": 104,
                "bb_mid": 100,
                "rsi_14": 50,
                "atr_14": 2.0,
                "volume_ratio": 1.0,
            }
        }
        sig = check_bb_entry("BTCUSDT", tech)
        assert sig is None

    def test_no_signal_rsi_not_extreme(self):
        """价格触及下轨但 RSI 未超卖 -> 无信号"""
        tech = {
            "latest": {
                "close": 95,
                "bb_lower": 96,
                "bb_upper": 104,
                "bb_mid": 100,
                "rsi_14": 40,  # > 35, 不够极端
                "atr_14": 2.0,
                "volume_ratio": 1.0,
            }
        }
        sig = check_bb_entry("BTCUSDT", tech)
        assert sig is None

    def test_no_signal_missing_data(self):
        """数据不完整 -> 无信号"""
        assert check_bb_entry("BTCUSDT", {}) is None
        assert check_bb_entry("BTCUSDT", {"latest": {}}) is None
        assert check_bb_entry("BTCUSDT", {"latest": {"close": 100}}) is None

    def test_no_signal_empty_latest(self):
        """latest 为空 dict -> 无信号"""
        tech = {"latest": {"close": 0, "bb_lower": 0, "bb_upper": 0, "bb_mid": 0}}
        sig = check_bb_entry("BTCUSDT", tech)
        assert sig is None

    def test_long_signal_frozen(self):
        """MeanReversionSignal 是 frozen dataclass"""
        tech = {
            "latest": {
                "close": 95,
                "bb_lower": 96,
                "bb_upper": 104,
                "bb_mid": 100,
                "rsi_14": 25,
                "atr_14": 2.0,
                "volume_ratio": 1.0,
            }
        }
        sig = check_bb_entry("BTCUSDT", tech)
        assert sig is not None
        try:
            sig.action = "short"  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass  # 期望的行为


class TestBBConfidence:
    def test_base_score(self):
        """RSI 刚好在阈值边缘，量能正常 -> 基础分 50 + 少量加分"""
        score = calc_bb_confidence(
            rsi=34, close=96, bb_lower=96, bb_upper=104,
            volume_ratio=0.8, direction="long",
        )
        # rsi_extreme = 35-34 = 1, +0 (int(0.67)=0)
        # deviation = 0 (close == bb_lower)
        # volume < 1.0, +0
        assert score == 50

    def test_extreme_rsi_high_score(self):
        """RSI 非常极端 -> 高加分"""
        score = calc_bb_confidence(
            rsi=10, close=90, bb_lower=96, bb_upper=104,
            volume_ratio=1.6, direction="long",
        )
        # rsi_extreme = 35-10 = 25, min(int(25*0.67), 20) = min(16, 20) = 16
        # deviation = (96-90)/8 = 0.75, min(int(0.75*50), 15) = min(37, 15) = 15
        # volume > 1.5 -> +15
        # total = 50+16+15+15 = 96
        assert score == 96

    def test_volume_boost_tiers(self):
        """不同量能等级的加分"""
        base_args = {
            "rsi": 34, "close": 96, "bb_lower": 96, "bb_upper": 104,
            "direction": "long",
        }
        s_low = calc_bb_confidence(**base_args, volume_ratio=0.8)
        s_normal = calc_bb_confidence(**base_args, volume_ratio=1.1)
        s_mid = calc_bb_confidence(**base_args, volume_ratio=1.3)
        s_high = calc_bb_confidence(**base_args, volume_ratio=2.0)
        assert s_low < s_normal < s_mid < s_high

    def test_short_direction(self):
        """做空方向的置信度计算"""
        score = calc_bb_confidence(
            rsi=80, close=110, bb_lower=96, bb_upper=104,
            volume_ratio=1.6, direction="short",
        )
        # rsi_extreme = 80-65 = 15, min(int(15*0.67), 20) = min(10, 20) = 10
        # deviation = (110-104)/8 = 0.75, min(int(0.75*50), 15) = min(37, 15) = 15
        # volume > 1.5 -> +15
        # total = 50+10+15+15 = 90
        assert score == 90

    def test_max_capped_at_100(self):
        """置信度上限 100"""
        score = calc_bb_confidence(
            rsi=0, close=50, bb_lower=96, bb_upper=104,
            volume_ratio=5.0, direction="long",
        )
        assert score <= 100


class TestSignalToDict:
    def test_conversion(self):
        """验证转换格式正确"""
        sig = MeanReversionSignal(
            symbol="BTCUSDT",
            action="long",
            entry_price=100.0,
            stop_loss=94.0,
            take_profit=[{"price": 100, "ratio": 1.0}],
            confidence=70,
            strategy_type="bb_mean_reversion",
            reasoning="test",
        )
        d = signal_to_dict(sig)
        assert d["symbol"] == "BTCUSDT"
        assert d["action"] == "long"
        assert d["leverage"] == 2  # 均值回归固定低杠杆
        assert d["strategy_type"] == "bb_mean_reversion"
        assert d["stop_loss"] == 94.0
        assert d["confidence"] == 70
        # entry_price_range 近似 entry_price +/- 0.1%
        assert len(d["entry_price_range"]) == 2
        assert d["entry_price_range"][0] < 100.0 < d["entry_price_range"][1]

    def test_conversion_preserves_take_profit(self):
        """take_profit 列表原样保留"""
        tp = [{"price": 200, "ratio": 0.5}, {"price": 250, "ratio": 0.5}]
        sig = MeanReversionSignal(
            symbol="ETHUSDT",
            action="short",
            entry_price=300.0,
            stop_loss=310.0,
            take_profit=tp,
            confidence=65,
            strategy_type="bb_mean_reversion",
            reasoning="test",
        )
        d = signal_to_dict(sig)
        assert d["take_profit"] == tp
