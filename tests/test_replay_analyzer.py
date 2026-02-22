"""replay_analyzer 单元测试

覆盖: 置信度分层/方向分析/回撤控制模拟/币种交叉表/时间分布/建议生成/主入口
"""

import math

import pytest

from cryptobot.backtest.trade_simulator import TradeResult
from cryptobot.backtest.replay_analyzer import (
    analyze_replay,
    _confidence_stratify,
    _direction_analysis,
    _drawdown_simulation,
    _symbol_cross_table,
    _time_distribution,
    _generate_recommendations,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────


def _make_trade(
    symbol: str = "BTCUSDT",
    action: str = "short",
    confidence: int = 70,
    net_pnl_pct: float = 5.0,
    net_pnl_usdt: float = 50.0,
    leverage: int = 5,
    duration_hours: float = 24.0,
    entry_time: str = "2025-12-01T00:00:00",
    exit_time: str = "2025-12-02T00:00:00",
    **kwargs,
) -> TradeResult:
    defaults = {
        "symbol": symbol,
        "action": action,
        "entry_price": 100.0,
        "exit_price": 105.0,
        "leverage": leverage,
        "confidence": confidence,
        "gross_pnl_pct": net_pnl_pct + 0.5,
        "costs_pct": 0.5,
        "net_pnl_pct": net_pnl_pct,
        "net_pnl_usdt": net_pnl_usdt,
        "exit_reason": "tp_full",
        "mfe_pct": 8.0,
        "mae_pct": 2.0,
        "duration_hours": duration_hours,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "signal_source": "replay",
    }
    defaults.update(kwargs)
    return TradeResult(**defaults)


# ── 置信度分层 ────────────────────────────────────────────────────────────


class TestConfidenceStratify:
    def test_three_buckets(self):
        """交易正确分到三个桶"""
        trades = [
            _make_trade(confidence=58, net_pnl_pct=-3.0),
            _make_trade(confidence=60, net_pnl_pct=5.0),
            _make_trade(confidence=68, net_pnl_pct=8.0),
            _make_trade(confidence=72, net_pnl_pct=-1.0),
            _make_trade(confidence=80, net_pnl_pct=12.0),
            _make_trade(confidence=90, net_pnl_pct=6.0),
        ]
        result = _confidence_stratify(trades)
        assert result["55-64"]["count"] == 2
        assert result["65-74"]["count"] == 2
        assert result["75+"]["count"] == 2

    def test_empty_list(self):
        result = _confidence_stratify([])
        for bucket in result.values():
            assert bucket["count"] == 0

    def test_all_same_bucket(self):
        """全部落在同一桶"""
        trades = [_make_trade(confidence=60) for _ in range(5)]
        result = _confidence_stratify(trades)
        assert result["55-64"]["count"] == 5
        assert result["65-74"]["count"] == 0
        assert result["75+"]["count"] == 0

    def test_win_rate_calculation(self):
        """胜率计算正确"""
        trades = [
            _make_trade(confidence=70, net_pnl_pct=5.0),
            _make_trade(confidence=72, net_pnl_pct=-3.0),
            _make_trade(confidence=68, net_pnl_pct=2.0),
            _make_trade(confidence=74, net_pnl_pct=-1.0),
        ]
        result = _confidence_stratify(trades)
        assert result["65-74"]["win_rate"] == 0.5

    def test_profit_factor(self):
        """盈亏比计算"""
        trades = [
            _make_trade(confidence=80, net_pnl_pct=10.0),
            _make_trade(confidence=85, net_pnl_pct=-5.0),
        ]
        result = _confidence_stratify(trades)
        assert result["75+"]["profit_factor"] == 2.0


# ── 方向分析 ──────────────────────────────────────────────────────────────


class TestDirectionAnalysis:
    def test_long_short_split(self):
        trades = [
            _make_trade(action="long"),
            _make_trade(action="short"),
            _make_trade(action="short"),
        ]
        result = _direction_analysis(trades)
        assert result["summary"]["long"]["count"] == 1
        assert result["summary"]["short"]["count"] == 2
        assert result["summary"]["short"]["ratio"] == pytest.approx(2 / 3, abs=0.01)

    def test_single_direction(self):
        """只有一个方向"""
        trades = [_make_trade(action="short") for _ in range(5)]
        result = _direction_analysis(trades)
        assert result["dominant_direction"] == "short"
        assert result["direction_bias"] > 0

    def test_monthly_trend(self):
        trades = [
            _make_trade(action="short", entry_time="2025-11-15T00:00:00"),
            _make_trade(action="long", entry_time="2025-11-20T00:00:00"),
            _make_trade(action="short", entry_time="2025-12-01T00:00:00"),
        ]
        result = _direction_analysis(trades)
        assert "2025-11" in result["monthly_trend"]
        assert result["monthly_trend"]["2025-11"]["short"] == 1
        assert result["monthly_trend"]["2025-11"]["long"] == 1

    def test_empty(self):
        result = _direction_analysis([])
        assert result["summary"] == {}


# ── 回撤控制模拟 ──────────────────────────────────────────────────────────


class TestDrawdownSimulation:
    def test_no_control_baseline(self):
        trades = [
            _make_trade(net_pnl_pct=10.0, entry_time="2025-12-01T01:00:00"),
            _make_trade(net_pnl_pct=-5.0, entry_time="2025-12-01T02:00:00"),
            _make_trade(net_pnl_pct=8.0, entry_time="2025-12-02T01:00:00"),
        ]
        result = _drawdown_simulation(trades)
        assert "no_control" in result
        assert result["no_control"]["trades_taken"] == 3
        assert result["no_control"]["trades_skipped"] == 0
        assert result["no_control"]["total_return_pct"] > 0

    def test_daily_limit_skips(self):
        """当日累计亏损达到限额时跳过后续交易"""
        trades = [
            _make_trade(net_pnl_pct=-4.0, entry_time="2025-12-01T01:00:00"),
            _make_trade(net_pnl_pct=-2.0, entry_time="2025-12-01T02:00:00"),
            _make_trade(net_pnl_pct=10.0, entry_time="2025-12-01T03:00:00"),
        ]
        result = _drawdown_simulation(trades, daily_limits=[5.0])
        # 第1笔 -4% → 累计-4% < -5%, 继续
        # 第2笔 -2% → 累计-6% >= -5%, 执行后累计-6%
        # 第3笔检查时累计-6% <= -5%，跳过
        sim = result["daily_limit_5.0pct"]
        assert sim["trades_skipped"] == 1
        assert sim["trades_taken"] == 2

    def test_dynamic_leverage_scales(self):
        """动态杠杆在大回撤时缩放 PnL"""
        result = _drawdown_simulation(
            [_make_trade(net_pnl_pct=5.0, entry_time="2025-12-01T01:00:00")],
        )
        assert "dynamic_leverage" in result
        # 单笔交易无回撤，scale=1.0
        dyn = result["dynamic_leverage"]
        no_ctrl = result["no_control"]
        assert abs(dyn["total_return_pct"] - no_ctrl["total_return_pct"]) < 0.01

    def test_empty_trades(self):
        result = _drawdown_simulation([])
        assert result == {}

    def test_all_strategies_present(self):
        trades = [_make_trade(entry_time=f"2025-12-0{i+1}T00:00:00") for i in range(3)]
        result = _drawdown_simulation(trades, daily_limits=[3.0, 5.0])
        assert "no_control" in result
        assert "daily_limit_3.0pct" in result
        assert "daily_limit_5.0pct" in result
        assert "dynamic_leverage" in result


# ── 币种×方向交叉表 ──────────────────────────────────────────────────────


class TestSymbolCrossTable:
    def test_multi_symbol_multi_direction(self):
        trades = [
            _make_trade(symbol="BTCUSDT", action="long"),
            _make_trade(symbol="BTCUSDT", action="short"),
            _make_trade(symbol="BTCUSDT", action="short"),
            _make_trade(symbol="ETHUSDT", action="long"),
        ]
        result = _symbol_cross_table(trades)
        assert result["BTCUSDT"]["long"]["count"] == 1
        assert result["BTCUSDT"]["short"]["count"] == 2
        assert result["ETHUSDT"]["long"]["count"] == 1
        assert "short" not in result["ETHUSDT"]

    def test_single_symbol(self):
        trades = [_make_trade(symbol="SOLUSDT", action="short") for _ in range(3)]
        result = _symbol_cross_table(trades)
        assert "SOLUSDT" in result
        assert result["SOLUSDT"]["short"]["count"] == 3

    def test_empty(self):
        assert _symbol_cross_table([]) == {}


# ── 时间分布 ──────────────────────────────────────────────────────────────


class TestTimeDistribution:
    def test_monthly_aggregation(self):
        trades = [
            _make_trade(entry_time="2025-11-05T00:00:00", net_pnl_pct=3.0),
            _make_trade(entry_time="2025-11-20T00:00:00", net_pnl_pct=-1.0),
            _make_trade(entry_time="2025-12-01T00:00:00", net_pnl_pct=5.0),
        ]
        result = _time_distribution(trades)
        assert result["monthly"]["2025-11"]["count"] == 2
        assert result["monthly"]["2025-12"]["count"] == 1

    def test_cross_month_boundary(self):
        trades = [
            _make_trade(entry_time="2025-11-30T23:00:00"),
            _make_trade(entry_time="2025-12-01T01:00:00"),
        ]
        result = _time_distribution(trades)
        assert "2025-11" in result["monthly"]
        assert "2025-12" in result["monthly"]

    def test_empty(self):
        result = _time_distribution([])
        assert result["monthly"] == {}
        assert result["weekly"] == {}


# ── 建议生成 ──────────────────────────────────────────────────────────────


class TestRecommendations:
    def test_high_bias_triggers_recommendation(self):
        """方向偏差高时生成建议"""
        direction = {
            "direction_bias": 0.45,
            "dominant_direction": "short",
            "summary": {
                "short": {"count": 180, "ratio": 0.93, "win_rate": 0.55},
                "long": {"count": 13, "ratio": 0.07, "win_rate": 0.40},
            },
        }
        recs = _generate_recommendations({}, direction, {})
        assert any("偏斜" in r for r in recs)

    def test_balanced_no_bias_rec(self):
        """均衡方向不触发偏差建议"""
        direction = {
            "direction_bias": 0.05,
            "dominant_direction": "short",
            "summary": {
                "short": {"count": 50, "ratio": 0.52, "win_rate": 0.55},
                "long": {"count": 46, "ratio": 0.48, "win_rate": 0.50},
            },
        }
        recs = _generate_recommendations({}, direction, {})
        assert not any("偏斜" in r for r in recs)

    def test_low_confidence_bucket_rec(self):
        """低置信度桶胜率低时触发建议"""
        conf = {"55-64": {"count": 50, "win_rate": 0.35}, "65-74": {}, "75+": {}}
        recs = _generate_recommendations(conf, {"summary": {}}, {})
        assert any("55-64" in r for r in recs)

    def test_high_drawdown_rec(self):
        """高回撤时推荐控制策略"""
        dd = {
            "no_control": {"max_drawdown_pct": 74.0, "calmar": 1.5},
            "daily_limit_5.0pct": {"max_drawdown_pct": 35.0, "calmar": 4.2},
        }
        recs = _generate_recommendations({}, {"summary": {}}, dd)
        assert any("回撤" in r for r in recs)


# ── 主入口 ────────────────────────────────────────────────────────────────


class TestAnalyzeReplay:
    def test_returns_complete_report(self):
        """mock 数据 → 完整 AnalysisReport"""
        trades = [
            _make_trade(confidence=60, action="short", net_pnl_pct=5.0,
                        entry_time="2025-11-01T00:00:00", exit_time="2025-11-02T00:00:00"),
            _make_trade(confidence=60, action="short", net_pnl_pct=-3.0,
                        entry_time="2025-11-02T00:00:00", exit_time="2025-11-03T00:00:00"),
            _make_trade(confidence=70, action="short", net_pnl_pct=8.0,
                        entry_time="2025-11-05T00:00:00", exit_time="2025-11-06T00:00:00"),
            _make_trade(confidence=80, action="long", net_pnl_pct=-2.0,
                        entry_time="2025-12-01T00:00:00", exit_time="2025-12-02T00:00:00"),
            _make_trade(confidence=85, action="short", net_pnl_pct=15.0,
                        entry_time="2025-12-10T00:00:00", exit_time="2025-12-11T00:00:00"),
        ]
        report = analyze_replay(trades)

        # 结构完整性
        assert report.confidence_buckets is not None
        assert report.direction_analysis is not None
        assert report.drawdown_simulation is not None
        assert report.symbol_heatmap is not None
        assert report.time_distribution is not None
        assert isinstance(report.recommendations, list)

        # 置信度桶
        assert report.confidence_buckets["55-64"]["count"] == 2
        assert report.confidence_buckets["65-74"]["count"] == 1
        assert report.confidence_buckets["75+"]["count"] == 2

        # 方向
        assert report.direction_analysis["summary"]["short"]["count"] == 4
        assert report.direction_analysis["summary"]["long"]["count"] == 1

    def test_empty_trades(self):
        report = analyze_replay([])
        assert report.drawdown_simulation == {}
        assert report.symbol_heatmap == {}
