"""Freqtrade 策略测试

由于 Freqtrade 不可导入，通过 mock 对象模拟依赖来测试纯逻辑。
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ─── Mock Freqtrade 依赖 ──────────────────────────────────────────────────


class _MockDecimalParameter:
    """模拟 DecimalParameter，只需 .value 属性"""
    def __init__(self, low, high, default, space="sell"):
        self.value = default


class _MockIStrategy:
    """模拟 IStrategy 基类"""
    pass


# 在导入策略前注入 mock 模块
_mock_ft_strategy = MagicMock()
_mock_ft_strategy.IStrategy = _MockIStrategy
_mock_ft_strategy.DecimalParameter = _MockDecimalParameter
sys.modules["freqtrade"] = MagicMock()
sys.modules["freqtrade.strategy"] = _mock_ft_strategy
sys.modules["pandas_ta"] = MagicMock()

# 现在可以安全导入 (必须在 mock 注入之后)
from freqtrade_strategies.AgentSignalStrategy import AgentSignalStrategy  # noqa: E402


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def strategy():
    """创建策略实例"""
    s = AgentSignalStrategy()
    s.stoploss = -0.05
    # 重置类级缓存，避免测试间干扰
    s._signal_cache = []
    s._signal_mtime = 0.0
    return s


@pytest.fixture
def mock_trade_long():
    """模拟多单 trade 对象"""
    return SimpleNamespace(
        is_short=False,
        pair="BTC/USDT:USDT",
        stake_amount=1000,
        orders=[],
    )


@pytest.fixture
def mock_trade_short():
    """模拟空单 trade 对象"""
    return SimpleNamespace(
        is_short=True,
        pair="ETH/USDT:USDT",
        stake_amount=500,
        orders=[],
    )


def _make_signal(symbol="BTCUSDT", action="long", **kwargs):
    """构建测试信号"""
    now = datetime.now(timezone.utc)
    base = {
        "symbol": symbol,
        "action": action,
        "leverage": 3,
        "stop_loss": kwargs.get("stop_loss"),
        "trailing_stop_pct": kwargs.get("trailing_stop_pct"),
        "take_profit": kwargs.get("take_profit", []),
        "confidence": 80,
        "position_size_usdt": kwargs.get("position_size_usdt"),
        "expires_at": (now + timedelta(hours=2)).isoformat(),
    }
    base.update(kwargs)
    return base


def _write_signal_file(tmp_path, signals):
    """写入测试信号文件"""
    signal_file = tmp_path / "signal.json"
    data = {
        "signals": signals,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    signal_file.write_text(json.dumps(data))
    return signal_file


# ─── TestCustomStoploss ───────────────────────────────────────────────────


class TestCustomStoploss:
    now = datetime.now(timezone.utc)

    def test_agent_trailing_stop_pct(self, strategy, mock_trade_long):
        """trailing_stop_pct=3, profit=8% → 返回 -(0.08-0.03)"""
        signal = _make_signal(trailing_stop_pct=3)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.08, after_fill=False,
            )
        assert result == pytest.approx(-(0.08 - 0.03))

    def test_agent_trailing_inactive(self, strategy, mock_trade_long):
        """profit=3% (< 5%) → 尾随不激活，回退到固定止损"""
        signal = _make_signal(trailing_stop_pct=3, stop_loss=92000)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=95000, current_profit=0.01, after_fill=False,
            )
        # 不激活尾随，进入固定止损逻辑
        expected_sl_pct = (95000 - 92000) / 95000
        assert result == pytest.approx(-expected_sl_pct)

    def test_agent_fixed_stop_loss_long(self, strategy, mock_trade_long):
        """多单止损价 92000, 当前 95000 → 返回负数"""
        signal = _make_signal(stop_loss=92000)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=95000, current_profit=0.02, after_fill=False,
            )
        expected = -(95000 - 92000) / 95000
        assert result == pytest.approx(expected)

    def test_agent_stop_loss_breached_long(self, strategy, mock_trade_long):
        """多单价格穿越止损 → 返回 self.stoploss"""
        signal = _make_signal(stop_loss=92000)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=90000, current_profit=-0.05, after_fill=False,
            )
        assert result == -0.05  # self.stoploss

    def test_agent_stop_loss_breached_short(self, strategy, mock_trade_short):
        """空单价格穿越止损 → 返回 self.stoploss"""
        signal = _make_signal(symbol="ETHUSDT", action="short", stop_loss=3800)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stoploss(
                "ETH/USDT:USDT", mock_trade_short, self.now,
                current_rate=3900, current_profit=-0.03, after_fill=False,
            )
        # 空单: sl_pct = (3800 - 3900) / 3900 < 0 → 穿越
        assert result == -0.05

    def test_tier3_trailing(self, strategy, mock_trade_long):
        """profit=25% → -(0.25-0.03) = -0.22"""
        with patch.object(strategy, "_get_signal_for_pair", return_value=None):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.25, after_fill=False,
            )
        assert result == pytest.approx(-(0.25 - 0.03))

    def test_tier2_trailing(self, strategy, mock_trade_long):
        """profit=12% → -(0.12-0.05) = -0.07"""
        with patch.object(strategy, "_get_signal_for_pair", return_value=None):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.12, after_fill=False,
            )
        assert result == pytest.approx(-(0.12 - 0.05))

    def test_tier1_breakeven(self, strategy, mock_trade_long):
        """profit=6% → -0.001"""
        with patch.object(strategy, "_get_signal_for_pair", return_value=None):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.06, after_fill=False,
            )
        assert result == -0.001

    def test_default_stoploss(self, strategy, mock_trade_long):
        """profit=2% → self.stoploss (-0.05)"""
        with patch.object(strategy, "_get_signal_for_pair", return_value=None):
            result = strategy.custom_stoploss(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.02, after_fill=False,
            )
        assert result == -0.05


# ─── TestCustomExit ───────────────────────────────────────────────────────


class TestCustomExit:
    now = datetime.now(timezone.utc)

    def test_last_tp_hit_long(self, strategy, mock_trade_long):
        """价格达到最后止盈 → 返回 exit 字符串"""
        signal = _make_signal(take_profit=[
            {"price": 100000, "pct": 30},
            {"price": 110000, "pct": 70},
        ])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_exit(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=110000, current_profit=0.15,
            )
        assert result == "take_profit_full_110000"

    def test_last_tp_not_hit(self, strategy, mock_trade_long):
        """价格未达止盈 → None"""
        signal = _make_signal(take_profit=[
            {"price": 100000, "pct": 30},
            {"price": 110000, "pct": 70},
        ])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_exit(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=105000, current_profit=0.10,
            )
        assert result is None

    def test_no_signal(self, strategy, mock_trade_long):
        """无信号 → None"""
        with patch.object(strategy, "_get_signal_for_pair", return_value=None):
            result = strategy.custom_exit(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.10,
            )
        assert result is None

    def test_no_take_profit_list(self, strategy, mock_trade_long):
        """空 take_profit → None"""
        signal = _make_signal(take_profit=[])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_exit(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.10,
            )
        assert result is None

    def test_tp_price_none_skipped(self, strategy, mock_trade_long):
        """take_profit 中 price=None → 跳过"""
        signal = _make_signal(take_profit=[{"price": None, "pct": 100}])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_exit(
                "BTC/USDT:USDT", mock_trade_long, self.now,
                current_rate=100000, current_profit=0.10,
            )
        assert result is None


# ─── TestAdjustTradePosition ─────────────────────────────────────────────


class TestAdjustTradePosition:
    now = datetime.now(timezone.utc)

    def test_partial_tp_triggered(self, strategy, mock_trade_long):
        """价格达到 TP1 → 返回负数减仓"""
        signal = _make_signal(take_profit=[
            {"price": 100000, "pct": 30},
            {"price": 110000, "pct": 70},
        ])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.adjust_trade_position(
                mock_trade_long, self.now,
                current_rate=100500, current_profit=0.05,
                min_stake=10, max_stake=5000,
                current_entry_rate=95000, current_exit_rate=100500,
                current_entry_profit=0, current_exit_profit=0.05,
            )
        assert result == pytest.approx(-300)  # 1000 * 30/100

    def test_already_filled_skipped(self, strategy, mock_trade_long):
        """同级别已执行 → 跳过"""
        signal = _make_signal(take_profit=[
            {"price": 100000, "pct": 30},
            {"price": 110000, "pct": 70},
        ])
        # 模拟已填充的订单
        filled_order = SimpleNamespace(
            ft_order_tag="tp_0_100000",
            ft_is_open=False,
        )
        mock_trade_long.orders = [filled_order]
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.adjust_trade_position(
                mock_trade_long, self.now,
                current_rate=100500, current_profit=0.05,
                min_stake=10, max_stake=5000,
                current_entry_rate=95000, current_exit_rate=100500,
                current_entry_profit=0, current_exit_profit=0.05,
            )
        assert result is None

    def test_none_orders_safe(self, strategy, mock_trade_long):
        """trade.orders=None → 不崩溃"""
        signal = _make_signal(take_profit=[
            {"price": 100000, "pct": 30},
            {"price": 110000, "pct": 70},
        ])
        mock_trade_long.orders = None
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.adjust_trade_position(
                mock_trade_long, self.now,
                current_rate=100500, current_profit=0.05,
                min_stake=10, max_stake=5000,
                current_entry_rate=95000, current_exit_rate=100500,
                current_entry_profit=0, current_exit_profit=0.05,
            )
        # orders=None → [] → 无已填充记录 → 触发 TP1
        assert result == pytest.approx(-300)

    def test_single_tp_returns_none(self, strategy, mock_trade_long):
        """只有一级 TP → None (由 custom_exit 处理)"""
        signal = _make_signal(take_profit=[{"price": 110000, "pct": 100}])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.adjust_trade_position(
                mock_trade_long, self.now,
                current_rate=110000, current_profit=0.10,
                min_stake=10, max_stake=5000,
                current_entry_rate=100000, current_exit_rate=110000,
                current_entry_profit=0, current_exit_profit=0.10,
            )
        assert result is None

    def test_tp_price_none_skipped(self, strategy, mock_trade_long):
        """tp_price is None → 跳过该级别"""
        signal = _make_signal(take_profit=[
            {"price": None, "pct": 30},
            {"price": 110000, "pct": 70},
        ])
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.adjust_trade_position(
                mock_trade_long, self.now,
                current_rate=105000, current_profit=0.05,
                min_stake=10, max_stake=5000,
                current_entry_rate=100000, current_exit_rate=105000,
                current_entry_profit=0, current_exit_profit=0.05,
            )
        assert result is None


# ─── TestCustomStakeAmount ────────────────────────────────────────────────


class TestCustomStakeAmount:
    now = datetime.now(timezone.utc)

    def test_signal_position_size(self, strategy):
        """有 position_size_usdt → 使用"""
        signal = _make_signal(position_size_usdt=800)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stake_amount(
                "BTC/USDT:USDT", self.now,
                current_rate=100000, proposed_stake=1000,
                min_stake=10, max_stake=5000,
                leverage=3, entry_tag=None, side="long",
            )
        assert result == 800

    def test_capped_by_max_stake(self, strategy):
        """超过 max_stake → 截断"""
        signal = _make_signal(position_size_usdt=8000)
        with patch.object(strategy, "_get_signal_for_pair", return_value=signal):
            result = strategy.custom_stake_amount(
                "BTC/USDT:USDT", self.now,
                current_rate=100000, proposed_stake=1000,
                min_stake=10, max_stake=5000,
                leverage=3, entry_tag=None, side="long",
            )
        assert result == 5000

    def test_no_signal_default(self, strategy):
        """无信号 → proposed_stake"""
        with patch.object(strategy, "_get_signal_for_pair", return_value=None):
            result = strategy.custom_stake_amount(
                "BTC/USDT:USDT", self.now,
                current_rate=100000, proposed_stake=1000,
                min_stake=10, max_stake=5000,
                leverage=3, entry_tag=None, side="long",
            )
        assert result == 1000


# ─── TestSignalCache ──────────────────────────────────────────────────────


class TestSignalCache:

    def test_cache_hit(self, strategy, tmp_path):
        """文件未变 → 返回缓存，mtime 不变"""
        signals = [_make_signal()]
        signal_file = _write_signal_file(tmp_path, signals)

        with patch("freqtrade_strategies.AgentSignalStrategy.SIGNAL_FILE", signal_file):
            # 第一次读取
            result1 = strategy._read_signals()
            assert len(result1) == 1
            assert strategy._signal_mtime > 0

            # 手动清空缓存内容但保留 mtime → 验证返回缓存而非重新读取
            sentinel = [{"symbol": "CACHED", "expires_at": signals[0]["expires_at"]}]
            strategy._signal_cache = sentinel

            result2 = strategy._read_signals()
            # 应该返回 sentinel (缓存) 而非重新解析文件
            assert result2 is sentinel

    def test_cache_miss(self, strategy, tmp_path):
        """文件变化 → 重新读取"""
        signals = [_make_signal()]
        signal_file = _write_signal_file(tmp_path, signals)

        with patch("freqtrade_strategies.AgentSignalStrategy.SIGNAL_FILE", signal_file):
            result1 = strategy._read_signals()
            assert len(result1) == 1

            # 修改文件内容（新增一个信号）
            new_signals = [_make_signal(), _make_signal(symbol="ETHUSDT")]
            data = {
                "signals": new_signals,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            signal_file.write_text(json.dumps(data))

            result2 = strategy._read_signals()
            assert len(result2) == 2

    def test_file_not_exists(self, strategy, tmp_path):
        """文件不存在 → 空列表"""
        fake_path = tmp_path / "nonexistent.json"
        with patch("freqtrade_strategies.AgentSignalStrategy.SIGNAL_FILE", fake_path):
            result = strategy._read_signals()
            assert result == []
