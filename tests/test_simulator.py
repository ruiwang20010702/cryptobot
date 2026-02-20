"""历史回放模拟器测试"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from cryptobot.indicators.calculator import klines_override, load_klines


# ─── klines_override 上下文管理器 ──────────────────────────────────────────

def test_klines_override_injects_data():
    """override 注入的数据应被 load_klines 返回"""
    fake_df = pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [100.0]},
        index=pd.to_datetime(["2026-01-01"]),
    )
    cache = {("FAKEUSDT", "4h"): fake_df}

    with klines_override(cache):
        result = load_klines("FAKEUSDT", "4h")
        assert result is fake_df


def test_klines_override_cleans_up():
    """退出上下文后 override 应被清理"""
    import cryptobot.indicators.calculator as calc_mod

    fake_df = pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [100.0]},
        index=pd.to_datetime(["2026-01-01"]),
    )
    cache = {("TESTUSDT", "4h"): fake_df}

    with klines_override(cache):
        assert calc_mod._klines_override is not None

    assert calc_mod._klines_override is None


def test_klines_override_passthrough():
    """未在 cache 中的 key 应走正常逻辑 (无本地文件+mock API 失败 → 抛异常)"""
    from unittest.mock import patch

    cache = {("FAKEUSDT", "4h"): pd.DataFrame()}

    with klines_override(cache):
        with patch(
            "cryptobot.indicators.calculator._fetch_klines_from_api",
            side_effect=RuntimeError("mock network error"),
        ):
            with pytest.raises(Exception):
                load_klines("NOTEXIST_USDT", "1h")


# ─── _lookup_fear_greed ───────────────────────────────────────────────────

def test_lookup_fear_greed_exact_match():
    """精确匹配日期"""
    from cryptobot.backtest.simulator import _lookup_fear_greed

    ts1 = int(datetime(2026, 2, 10, tzinfo=timezone.utc).timestamp())
    ts2 = int(datetime(2026, 2, 11, tzinfo=timezone.utc).timestamp())
    history = {
        "records": [
            {"value": 30, "classification": "Fear", "timestamp": ts2},
            {"value": 70, "classification": "Greed", "timestamp": ts1},
        ],
        "avg_7d": 50, "avg_30d": 50, "trend": "neutral",
    }

    result = _lookup_fear_greed(history, datetime(2026, 2, 10, 6, 0, tzinfo=timezone.utc))
    assert result["current_value"] == 70  # 距离 ts1 更近


def test_lookup_fear_greed_empty():
    """空记录返回默认值"""
    from cryptobot.backtest.simulator import _lookup_fear_greed

    result = _lookup_fear_greed({"records": []}, datetime.now(timezone.utc))
    assert result["current_value"] == 50


# ─── _evaluate_sim_signals ────────────────────────────────────────────────

def _make_klines_df(prices: list[tuple[float, float]]) -> pd.DataFrame:
    """生成假 1h K 线 (high, low)"""
    n = len(prices)
    dates = pd.date_range("2026-02-10", periods=n, freq="h")
    return pd.DataFrame({
        "open": [p[0] for p in prices],
        "high": [p[0] for p in prices],
        "low": [p[1] for p in prices],
        "close": [p[0] for p in prices],
        "volume": [100.0] * n,
    }, index=dates)


def test_evaluate_long_signal_win():
    """做多信号: 价格上涨 → MFE > MAE → win"""
    from cryptobot.backtest.simulator import _evaluate_sim_signals

    # 入场价 100, 之后涨到 110, 最低 98
    prices = [(110, 98)] * 10
    klines = {("TESTUSDT", "1h"): _make_klines_df(prices)}

    signals = [{
        "symbol": "TESTUSDT",
        "action": "long",
        "entry_price_range": [99, 101],
        "stop_loss": 95,
        "take_profit": [{"price": 108}],
        "sim_timestamp": "2026-02-10T00:00:00+00:00",
    }]

    result = _evaluate_sim_signals(signals, klines)
    assert len(result) == 1
    ev = result[0]["eval"]
    assert ev["mfe_pct"] == 10.0  # (110-100)/100*100
    assert ev["mae_pct"] == 2.0   # (100-98)/100*100
    assert ev["win_by_mfe"] is True
    assert ev["sl_hit"] is False
    assert ev["tp_hits"] == 1


def test_evaluate_short_signal():
    """做空信号: 价格下跌 → MFE 为下跌幅度"""
    from cryptobot.backtest.simulator import _evaluate_sim_signals

    # 入场价 100, 最高 102, 最低 90
    prices = [(102, 90)] * 10
    klines = {("TESTUSDT", "1h"): _make_klines_df(prices)}

    signals = [{
        "symbol": "TESTUSDT",
        "action": "short",
        "entry_price_range": [99, 101],
        "stop_loss": 105,
        "take_profit": [{"price": 92}],
        "sim_timestamp": "2026-02-10T00:00:00+00:00",
    }]

    result = _evaluate_sim_signals(signals, klines)
    assert len(result) == 1
    ev = result[0]["eval"]
    assert ev["mfe_pct"] == 10.0  # (100-90)/100*100
    assert ev["mae_pct"] == 2.0   # (102-100)/100*100
    assert ev["win_by_mfe"] is True
    assert ev["sl_hit"] is False
    assert ev["tp_hits"] == 1


# ─── _aggregate_results ──────────────────────────────────────────────────

def test_aggregate_results_basic():
    """聚合统计正确"""
    from cryptobot.backtest.simulator import _aggregate_results

    evaluated = [
        {"symbol": "BTCUSDT", "action": "long",
         "eval": {"mfe_pct": 5.0, "mae_pct": 2.0, "sl_hit": False,
                  "tp_hits": 1, "tp_total": 2, "bars_analyzed": 168, "win_by_mfe": True}},
        {"symbol": "ETHUSDT", "action": "short",
         "eval": {"mfe_pct": 3.0, "mae_pct": 4.0, "sl_hit": True,
                  "tp_hits": 0, "tp_total": 2, "bars_analyzed": 168, "win_by_mfe": False}},
    ]

    result = _aggregate_results(evaluated, days=14, interval_hours=12, total_cycles=28)
    assert result["signals_generated"] == 2
    assert result["overview"]["total"] == 2
    assert result["overview"]["win_rate_by_mfe"] == 0.5
    assert result["overview"]["avg_mfe_pct"] == 4.0
    assert result["overview"]["avg_mae_pct"] == 3.0
    assert result["overview"]["sl_hit"] == 1
    assert result["overview"]["tp_hit_any"] == 1
    assert "BTCUSDT" in result["by_symbol"]
    assert "long" in result["by_direction"]


def test_aggregate_results_empty():
    """空信号列表"""
    from cryptobot.backtest.simulator import _aggregate_results

    result = _aggregate_results([], days=7, interval_hours=24, total_cycles=7)
    assert result["signals_generated"] == 0
    assert result["overview"]["total"] == 0


# ─── CLI ──────────────────────────────────────────────────────────────────

def test_simulate_cli_help():
    """CLI help 可正常显示"""
    from click.testing import CliRunner
    from cryptobot.cli.backtest import simulate

    runner = CliRunner()
    result = runner.invoke(simulate, ["--help"])
    assert result.exit_code == 0
    assert "历史回放模拟" in result.output
    assert "--days" in result.output
    assert "--interval" in result.output


# ─── 完整模拟 (需要网络) ──────────────────────────────────────────────────

@pytest.mark.network
def test_full_simulation():
    """端到端模拟 (需要网络 + Claude 额度)"""
    from cryptobot.backtest.simulator import run_simulation

    result = run_simulation(days=1, interval_hours=24)
    assert "overview" in result
    assert "signals" in result
