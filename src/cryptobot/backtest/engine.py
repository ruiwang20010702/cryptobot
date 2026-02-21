"""回测引擎 — 加载信号 → 模拟交易 → 构建净值曲线 → 统计

入口函数:
- run_backtest(): 对 AI 历史信号跑完整回测
- run_baseline_backtest(): 对基线策略跑回测
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from cryptobot.backtest.cost_model import CostConfig
from cryptobot.backtest.trade_simulator import TradeResult, simulate_trade
from cryptobot.backtest.equity_tracker import (
    BacktestMetrics,
    build_equity_curve,
    calc_metrics,
)
from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_BACKTEST_DIR = DATA_OUTPUT_DIR / "backtest"


@dataclass(frozen=True)
class BacktestReport:
    """回测报告"""

    config: dict
    metrics: BacktestMetrics
    trades: list[TradeResult]
    by_symbol: dict
    by_direction: dict
    signal_source: str
    total_signals_loaded: int


# ── 主入口 ────────────────────────────────────────────────────────────────


def run_backtest(
    days: int = 90,
    source: str = "archive",
    cost_config: CostConfig | None = None,
    initial_capital: float = 10000.0,
) -> BacktestReport:
    """对 AI 历史信号运行完整回测

    Args:
        days: 回溯天数
        source: 信号来源 ("archive" 或 "journal")
        cost_config: 成本配置
        initial_capital: 初始资金

    Returns:
        BacktestReport
    """
    if cost_config is None:
        cost_config = CostConfig()

    # 1. 加载信号
    if source == "journal":
        signals = _load_signals_from_journal(days)
    else:
        signals = _load_signals_from_archive(days)

    logger.info("加载 %d 个 AI 信号 (来源: %s, %d天)", len(signals), source, days)

    if not signals:
        return _empty_report(days, source, initial_capital)

    # 2. 下载 K 线
    klines_cache = _load_klines_for_signals(signals)

    # 3. 模拟交易
    trades = _simulate_all(signals, klines_cache, cost_config)
    logger.info("模拟完成: %d/%d 笔有效交易", len(trades), len(signals))

    # 4. 构建净值曲线 + 统计
    return _build_report(
        trades=trades,
        days=days,
        source=source,
        signal_source="ai",
        initial_capital=initial_capital,
        total_signals=len(signals),
    )


def run_baseline_backtest(
    days: int = 90,
    strategy: str = "random",
    cost_config: CostConfig | None = None,
    initial_capital: float = 10000.0,
) -> BacktestReport:
    """对基线策略运行回测

    Args:
        strategy: "random", "ma_cross", "rsi", "bollinger"
    """
    from cryptobot.backtest.baselines import (
        generate_random_signals,
        generate_ma_cross_signals,
        generate_rsi_signals,
        generate_bollinger_signals,
    )

    if cost_config is None:
        cost_config = CostConfig()

    # 1. 加载 AI 信号作为参考 (random 需要)
    ai_signals = _load_signals_from_archive(days)

    # 2. 下载 K 线
    symbols = list({s.get("symbol", "") for s in ai_signals if s.get("symbol")})
    if not symbols:
        from cryptobot.config import get_all_symbols
        symbols = get_all_symbols()

    klines_cache = _download_klines_batch(symbols)

    # 3. 生成基线信号
    if strategy == "random":
        signals = generate_random_signals(ai_signals, klines_cache)
    elif strategy == "ma_cross":
        signals = generate_ma_cross_signals(klines_cache)
    elif strategy == "rsi":
        signals = generate_rsi_signals(klines_cache)
    elif strategy == "bollinger":
        signals = generate_bollinger_signals(klines_cache)
    else:
        raise ValueError(f"未知策略: {strategy}")

    logger.info("生成 %d 个 %s 基线信号", len(signals), strategy)

    if not signals:
        return _empty_report(days, strategy, initial_capital)

    # 4. 模拟交易
    klines_1h = {s: df for s, df in klines_cache.items()}
    trades = _simulate_all(signals, klines_1h, cost_config)

    return _build_report(
        trades=trades,
        days=days,
        source=strategy,
        signal_source=strategy,
        initial_capital=initial_capital,
        total_signals=len(signals),
    )


# ── 信号加载 ──────────────────────────────────────────────────────────────


def _load_signals_from_archive(days: int) -> list[dict]:
    """从决策归档加载已批准的信号"""
    from cryptobot.archive.reader import list_archives, get_archive

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    archives = list_archives(limit=500)
    signals = []

    for summary in archives:
        ts = summary.get("timestamp", "")
        if ts < cutoff:
            continue

        archive = get_archive(summary["run_id"])
        if not archive:
            continue

        for sig in archive.get("approved_signals", []):
            sig["timestamp"] = sig.get("timestamp", ts)
            sig["signal_source"] = "ai"
            signals.append(sig)

    signals.sort(key=lambda s: s.get("timestamp", ""))
    return signals


def _load_signals_from_journal(days: int) -> list[dict]:
    """从交易记录加载已平仓信号"""
    from cryptobot.journal.storage import get_all_records

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    records = get_all_records()
    signals = []

    for r in records:
        if r.status != "closed" or r.timestamp < cutoff:
            continue
        signals.append({
            "symbol": r.symbol,
            "action": r.action,
            "entry_price_range": r.entry_price_range,
            "stop_loss": r.stop_loss,
            "take_profit": r.take_profit,
            "leverage": r.leverage,
            "confidence": r.confidence,
            "timestamp": r.timestamp,
            "signal_source": "ai",
        })

    signals.sort(key=lambda s: s.get("timestamp", ""))
    return signals


# ── K 线加载 ──────────────────────────────────────────────────────────────


def _load_klines_for_signals(signals: list[dict]) -> dict:
    """为所有信号的币种下载 1h K 线"""
    symbols = list({s.get("symbol", "") for s in signals if s.get("symbol")})
    return _download_klines_batch(symbols)


def _download_klines_batch(symbols: list[str]) -> dict:
    """批量下载 1h K 线，带本地缓存"""
    import pandas as pd
    from cryptobot.indicators.calculator import download_klines

    cache: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            cache[sym] = download_klines(sym, "1h", 500)
            logger.debug("下载 %s 1h K线: %d 根", sym, len(cache[sym]))
        except Exception as e:
            logger.warning("下载 %s K线失败: %s", sym, e)

    return cache


# ── 模拟执行 ──────────────────────────────────────────────────────────────


def _simulate_all(
    signals: list[dict],
    klines_cache: dict,
    cost_config: CostConfig,
) -> list[TradeResult]:
    """批量模拟所有信号"""
    trades = []
    for sig in signals:
        sym = sig.get("symbol", "")
        kl = klines_cache.get(sym)
        if kl is None or kl.empty:
            continue

        result = simulate_trade(sig, kl, cost_config)
        if result is not None:
            trades.append(result)

    return trades


# ── 报告生成 ──────────────────────────────────────────────────────────────


def _build_report(
    trades: list[TradeResult],
    days: int,
    source: str,
    signal_source: str,
    initial_capital: float,
    total_signals: int,
) -> BacktestReport:
    """构建回测报告"""
    if not trades:
        return _empty_report(days, source, initial_capital)

    equity_curve = build_equity_curve(trades, initial_capital)
    metrics = calc_metrics(equity_curve, trades, initial_capital)

    # 按币种分组统计
    by_symbol = _group_stats(trades, key=lambda t: t.symbol)
    by_direction = _group_stats(trades, key=lambda t: t.action)

    return BacktestReport(
        config={"days": days, "source": source, "initial_capital": initial_capital},
        metrics=metrics,
        trades=trades,
        by_symbol=by_symbol,
        by_direction=by_direction,
        signal_source=signal_source,
        total_signals_loaded=total_signals,
    )


def _group_stats(trades: list[TradeResult], key) -> dict:
    """按 key 分组计算统计"""
    groups: dict[str, list] = {}
    for t in trades:
        k = key(t)
        groups.setdefault(k, []).append(t)

    result = {}
    for name, group in sorted(groups.items()):
        wins = [t for t in group if t.net_pnl_pct > 0]
        pnls = [t.net_pnl_pct for t in group]
        result[name] = {
            "count": len(group),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(group), 3) if group else 0,
            "avg_pnl_pct": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            "total_pnl_usdt": round(
                sum(t.net_pnl_usdt for t in group), 2,
            ),
        }

    return result


def _empty_report(days: int, source: str, initial_capital: float) -> BacktestReport:
    """空报告"""
    return BacktestReport(
        config={"days": days, "source": source, "initial_capital": initial_capital},
        metrics=BacktestMetrics(
            total_trades=0, win_rate=0, profit_factor=0,
            sharpe_ratio=0, sortino_ratio=0, max_drawdown_pct=0,
            calmar_ratio=0, total_return_pct=0, annualized_return_pct=0,
            avg_trade_pnl_pct=0, best_trade_pct=0, worst_trade_pct=0,
            monthly_returns={},
        ),
        trades=[],
        by_symbol={},
        by_direction={},
        signal_source=source,
        total_signals_loaded=0,
    )


# ── 持久化 ────────────────────────────────────────────────────────────────


def save_report(report: BacktestReport) -> Path:
    """保存回测报告到 JSON"""
    _BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _BACKTEST_DIR / f"bt_{ts}.json"

    data = {
        "config": report.config,
        "signal_source": report.signal_source,
        "total_signals_loaded": report.total_signals_loaded,
        "metrics": asdict(report.metrics),
        "by_symbol": report.by_symbol,
        "by_direction": report.by_direction,
        "trades_count": len(report.trades),
        "trades_summary": [
            {
                "symbol": t.symbol,
                "action": t.action,
                "net_pnl_pct": t.net_pnl_pct,
                "exit_reason": t.exit_reason,
                "duration_hours": t.duration_hours,
            }
            for t in report.trades[:100]  # 最多保存 100 条明细
        ],
    }

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(path)
    logger.info("回测报告保存到 %s", path)
    return path
