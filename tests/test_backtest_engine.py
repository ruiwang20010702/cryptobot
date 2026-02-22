"""backtest engine 单元测试

覆盖: 空归档/mock 完整流程/报告持久化
"""

from dataclasses import dataclass
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from cryptobot.backtest.cost_model import CostConfig
from cryptobot.backtest.engine import (
    run_backtest,
    run_baseline_backtest,
    save_report,
    _build_report,
    _simulate_all,
    _group_stats,
    BacktestReport,
)
from cryptobot.backtest.trade_simulator import TradeResult


# ── 辅助 ──────────────────────────────────────────────────────────────────

def _make_klines(n: int = 200) -> pd.DataFrame:
    """构建简单的 1h K 线"""
    ts = pd.date_range("2026-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [110.0] * n,
            "low": [90.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
        },
        index=ts,
    )


def _make_signal(
    symbol: str = "BTCUSDT",
    action: str = "long",
    timestamp: str = "2026-01-01T00:00:00",
) -> dict:
    return {
        "symbol": symbol,
        "action": action,
        "entry_price_range": [99.0, 101.0],
        "stop_loss": 90.0,
        "take_profit": [{"price": 108.0, "ratio": 0.5}, {"price": 115.0, "ratio": 0.5}],
        "leverage": 3,
        "confidence": 80,
        "signal_source": "ai",
        "timestamp": timestamp,
    }


def _make_trade_result(**overrides) -> TradeResult:
    defaults = {
        "symbol": "BTCUSDT",
        "action": "long",
        "entry_price": 100.0,
        "exit_price": 105.0,
        "leverage": 3,
        "confidence": 80,
        "gross_pnl_pct": 15.0,
        "costs_pct": 0.5,
        "net_pnl_pct": 14.5,
        "net_pnl_usdt": 435.0,
        "exit_reason": "tp_full",
        "mfe_pct": 10.0,
        "mae_pct": 2.0,
        "duration_hours": 24.0,
        "entry_time": "2026-01-01T00:00:00",
        "exit_time": "2026-01-02T00:00:00",
        "signal_source": "ai",
    }
    defaults.update(overrides)
    return TradeResult(**defaults)


# ── 空数据 ────────────────────────────────────────────────────────────────

class TestEmptyData:
    @patch("cryptobot.backtest.engine._load_signals_from_archive", return_value=[])
    def test_empty_archive_returns_zero_metrics(self, mock_load):
        report = run_backtest(days=90)
        assert report.metrics.total_trades == 0
        assert report.total_signals_loaded == 0
        assert report.trades == []

    @patch("cryptobot.backtest.engine._load_signals_from_journal", return_value=[])
    def test_empty_journal_returns_zero_metrics(self, mock_load):
        report = run_backtest(days=90, source="journal")
        assert report.metrics.total_trades == 0


# ── 模拟流程 ──────────────────────────────────────────────────────────────

class TestSimulateAll:
    def test_simulates_matching_signals(self):
        """信号有对应 K 线时成功模拟"""
        signals = [_make_signal("BTCUSDT"), _make_signal("ETHUSDT")]
        klines = {
            "BTCUSDT": _make_klines(),
            "ETHUSDT": _make_klines(),
        }
        cost = CostConfig(taker_fee_pct=0, slippage_pct=0, funding_rate_per_8h=0)
        trades = _simulate_all(signals, klines, cost)
        assert len(trades) == 2
        assert all(isinstance(t, TradeResult) for t in trades)

    def test_skips_missing_klines(self):
        """缺少 K 线时跳过"""
        signals = [_make_signal("BTCUSDT"), _make_signal("DOGEUSDT")]
        klines = {"BTCUSDT": _make_klines()}
        cost = CostConfig()
        trades = _simulate_all(signals, klines, cost)
        assert len(trades) == 1
        assert trades[0].symbol == "BTCUSDT"


# ── 报告构建 ──────────────────────────────────────────────────────────────

class TestBuildReport:
    def test_non_empty_report(self):
        """非空交易列表生成有效报告"""
        trades = [
            _make_trade_result(symbol="BTCUSDT", net_pnl_pct=5.0),
            _make_trade_result(symbol="BTCUSDT", net_pnl_pct=-2.0),
            _make_trade_result(symbol="ETHUSDT", net_pnl_pct=3.0),
        ]
        report = _build_report(
            trades=trades,
            days=30,
            source="archive",
            signal_source="ai",
            initial_capital=10000,
            total_signals=3,
        )
        assert report.metrics.total_trades == 3
        assert report.metrics.win_rate > 0
        assert "BTCUSDT" in report.by_symbol
        assert "ETHUSDT" in report.by_symbol

    def test_empty_trades_returns_zero_metrics(self):
        report = _build_report(
            trades=[], days=30, source="archive",
            signal_source="ai", initial_capital=10000, total_signals=0,
        )
        assert report.metrics.total_trades == 0


# ── 分组统计 ──────────────────────────────────────────────────────────────

class TestGroupStats:
    def test_groups_by_symbol(self):
        trades = [
            _make_trade_result(symbol="BTC", net_pnl_pct=5.0, net_pnl_usdt=50),
            _make_trade_result(symbol="BTC", net_pnl_pct=-3.0, net_pnl_usdt=-30),
            _make_trade_result(symbol="ETH", net_pnl_pct=8.0, net_pnl_usdt=80),
        ]
        stats = _group_stats(trades, key=lambda t: t.symbol)
        assert stats["BTC"]["count"] == 2
        assert stats["BTC"]["wins"] == 1
        assert stats["ETH"]["count"] == 1
        assert stats["ETH"]["wins"] == 1


# ── 报告保存 ──────────────────────────────────────────────────────────────

class TestSaveReport:
    def test_saves_to_file(self, tmp_path):
        """报告保存到 JSON 文件"""
        trades = [_make_trade_result()]
        report = _build_report(
            trades=trades, days=30, source="archive",
            signal_source="ai", initial_capital=10000, total_signals=1,
        )

        with patch("cryptobot.backtest.engine._BACKTEST_DIR", tmp_path):
            path = save_report(report)

        assert path.exists()
        assert path.suffix == ".json"

        import json
        data = json.loads(path.read_text())
        assert "metrics" in data
        assert data["trades_count"] == 1

    def test_saves_all_fields_without_truncation(self, tmp_path):
        """trades_summary 包含全量字段且不截断"""
        trades = [_make_trade_result(symbol=f"SYM{i}") for i in range(150)]
        report = _build_report(
            trades=trades, days=90, source="archive",
            signal_source="ai", initial_capital=10000, total_signals=150,
        )

        with patch("cryptobot.backtest.engine._BACKTEST_DIR", tmp_path):
            path = save_report(report)

        import json
        data = json.loads(path.read_text())
        summary = data["trades_summary"]

        # 不截断：150 笔全部保存
        assert len(summary) == 150

        # 全字段验证
        t = summary[0]
        expected_keys = {
            "symbol", "action", "entry_price", "exit_price",
            "leverage", "confidence", "gross_pnl_pct", "costs_pct",
            "net_pnl_pct", "net_pnl_usdt", "exit_reason",
            "mfe_pct", "mae_pct", "duration_hours",
            "entry_time", "exit_time", "signal_source",
        }
        assert expected_keys.issubset(set(t.keys()))


# ── 完整流程 mock ─────────────────────────────────────────────────────────

class TestFullFlowMocked:
    @patch("cryptobot.backtest.engine._load_signals_from_archive")
    @patch("cryptobot.backtest.engine._download_klines_batch")
    def test_run_backtest_with_signals(self, mock_klines, mock_signals):
        """mock 完整流程: 信号 + K线 → 报告"""
        mock_signals.return_value = [
            _make_signal("BTCUSDT"),
            _make_signal("BTCUSDT", action="short",
                         timestamp="2026-01-02T00:00:00"),
        ]
        mock_klines.return_value = {"BTCUSDT": _make_klines(300)}

        report = run_backtest(days=30)
        assert report.metrics.total_trades >= 1
        assert report.signal_source == "ai"

    @patch("cryptobot.backtest.engine._load_signals_from_archive")
    @patch("cryptobot.backtest.engine._download_klines_batch")
    @patch("cryptobot.backtest.baselines.generate_random_signals")
    def test_run_baseline_random(
        self, mock_gen, mock_klines, mock_archive
    ):
        """基线回测 mock"""
        mock_archive.return_value = [_make_signal()]
        mock_klines.return_value = {"BTCUSDT": _make_klines()}
        sig = _make_signal()
        sig["signal_source"] = "random"
        mock_gen.return_value = [sig]

        report = run_baseline_backtest(days=30, strategy="random")
        assert report.signal_source == "random"
