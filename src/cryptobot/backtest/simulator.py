"""历史回放模拟器

用过去 N 天的历史数据重跑完整 AI 工作流，再和实际走势对比。

核心流程:
1. 下载历史 K 线 (10 币种 × 3 时间框架)
2. 生成时间切片点 (每 interval_hours 一个)
3. 逐个时间点: 切片 K 线 → 跑 AI 工作流 (screen→analyze→research→trade→risk_review)
4. 对每个信号用后续 K 线评估 MFE/MAE/SL/TP
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from cryptobot.config import DATA_OUTPUT_DIR, get_all_symbols

logger = logging.getLogger(__name__)

TIMEFRAMES = ["1h", "4h", "1d"]
EVAL_BARS = 168  # 7 天 × 24h


def run_simulation(
    days: int = 14,
    interval_hours: int = 12,
    on_cycle_done: Callable | None = None,
) -> dict:
    """运行历史回放模拟

    Args:
        days: 回溯天数
        interval_hours: 分析间隔 (小时)
        on_cycle_done: 每周期完成回调 (cycle_idx, total, as_of, signals)

    Returns:
        模拟结果 dict
    """
    from cryptobot.data.sentiment import get_fear_greed_index
    from cryptobot.indicators.calculator import download_klines, klines_override

    symbols = get_all_symbols()
    now = datetime.now(timezone.utc)

    # Step 1: 下载数据
    logger.info("下载历史 K 线: %d 币种 × %d 时间框架", len(symbols), len(TIMEFRAMES))
    klines_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for sym in symbols:
        for tf in TIMEFRAMES:
            try:
                klines_cache[(sym, tf)] = download_klines(sym, tf, 500)
            except Exception as e:
                logger.warning("下载失败 %s %s: %s", sym, tf, e)

    # 额外下载 1h 用于评估
    for sym in symbols:
        if (sym, "1h") not in klines_cache:
            try:
                klines_cache[(sym, "1h")] = download_klines(sym, "1h", 500)
            except Exception as e:
                logger.warning("下载 1h 失败 %s: %s", sym, e)

    # Fear & Greed 30 天历史
    fg_history = None
    try:
        fg_history = get_fear_greed_index(limit=30)
    except Exception as e:
        logger.warning("Fear & Greed 获取失败: %s", e)

    # Step 2: 生成时间点
    timepoints = _generate_timepoints(now, days, interval_hours, klines_cache)
    total_cycles = len(timepoints)
    logger.info("模拟时间点: %d 个", total_cycles)

    # Step 3: 逐个时间点运行
    all_signals = []
    for idx, as_of in enumerate(timepoints):
        cycle_signals = _run_single_cycle(
            as_of, symbols, klines_cache, fg_history, klines_override,
        )
        all_signals.extend(cycle_signals)

        if on_cycle_done:
            on_cycle_done(idx, total_cycles, as_of, cycle_signals)

    # Step 4: 评估
    evaluated = _evaluate_sim_signals(all_signals, klines_cache)
    result = _aggregate_results(evaluated, days, interval_hours, total_cycles)

    # 保存结果
    _save_result(result)
    return result


def _generate_timepoints(
    now: datetime, days: int, interval_hours: int,
    klines_cache: dict,
) -> list[datetime]:
    """生成模拟时间点，确保每个时间点都在 K 线数据范围内"""
    start = now - timedelta(days=days)
    points = []
    t = start
    while t < now:
        points.append(t)
        t += timedelta(hours=interval_hours)

    # 过滤: 确保每个时间点有足够的 K 线数据 (至少 100 根 4h)
    if not klines_cache:
        return points

    # 找所有 4h K 线的最早时间
    earliest = None
    for (sym, tf), df in klines_cache.items():
        if tf == "4h" and not df.empty:
            first_ts = df.index[0]
            if not first_ts.tzinfo:
                first_ts = first_ts.tz_localize(timezone.utc)
            if earliest is None or first_ts < earliest:
                earliest = first_ts

    if earliest is not None:
        # 需要至少 100 根 4h K 线 = 400h 的历史
        min_time = earliest + timedelta(hours=400)
        points = [p for p in points if p >= min_time]

    return points


def _run_single_cycle(
    as_of: datetime,
    symbols: list[str],
    klines_cache: dict[tuple[str, str], pd.DataFrame],
    fg_history: dict | None,
    klines_override_cm,
) -> list[dict]:
    """在给定时间点运行单个分析周期

    Returns:
        该周期生成的 approved_signals 列表
    """
    from cryptobot.workflow.nodes.collect import _detect_market_regime
    from cryptobot.workflow.nodes.screen import screen
    from cryptobot.workflow.nodes.analyze import analyze
    from cryptobot.workflow.nodes.research import research
    from cryptobot.workflow.nodes.trade import trade
    from cryptobot.workflow.nodes.risk import risk_review
    from cryptobot.workflow.utils import fetch_market_data

    # a. 切片 K 线
    sliced = {}
    for (sym, tf), df in klines_cache.items():
        idx = df.index
        if idx.tz is None:
            mask = idx <= as_of.replace(tzinfo=None)
        else:
            mask = idx <= as_of
        sliced[(sym, tf)] = df[mask].copy()

    # b. 在 override 上下文中获取市场数据
    with klines_override_cm(sliced):
        market_data, fg, _, _, errors = fetch_market_data(symbols)

    # c. 覆盖 fear_greed 为历史值
    if fg_history:
        fg = _lookup_fear_greed(fg_history, as_of)
    elif not fg:
        fg = {"current_value": 50, "current_classification": "Neutral"}

    # d. 市场状态检测
    regime = _detect_market_regime(market_data, fg)

    # e. 构建初始 state
    state = {
        "market_data": market_data,
        "fear_greed": fg,
        "market_overview": {},
        "global_news": {},
        "market_regime": regime,
        "errors": errors,
    }

    # f. 运行工作流 (screen → analyze → research → trade → risk_review)
    try:
        state.update(screen(state))
    except Exception as e:
        logger.warning("screen 失败 @ %s: %s", as_of, e)
        return []

    if not state.get("screened_symbols"):
        return []

    try:
        state.update(analyze(state))
    except Exception as e:
        logger.warning("analyze 失败 @ %s: %s", as_of, e)
        return []

    try:
        state.update(research(state))
    except Exception as e:
        logger.warning("research 失败 @ %s: %s", as_of, e)
        return []

    # 注入模拟持仓上下文
    import cryptobot.workflow.utils as wf_utils
    original_build = wf_utils._build_portfolio_context
    wf_utils._build_portfolio_context = _build_sim_portfolio_ctx
    try:
        state.update(trade(state))
    except Exception as e:
        logger.warning("trade 失败 @ %s: %s", as_of, e)
        return []

    # risk_review: 也用模拟持仓，同时 mock freqtrade_api
    import cryptobot.freqtrade_api as ft_mod
    original_ft = ft_mod.ft_api_get
    ft_mod.ft_api_get = _mock_ft_api_get
    try:
        state.update(risk_review(state))
    except Exception as e:
        logger.warning("risk_review 失败 @ %s: %s", as_of, e)
        return []
    finally:
        wf_utils._build_portfolio_context = original_build
        ft_mod.ft_api_get = original_ft

    # g. 收集 approved_signals，附加 sim_timestamp
    signals = state.get("approved_signals", [])
    for sig in signals:
        sig["sim_timestamp"] = as_of.isoformat()

    return signals


def _lookup_fear_greed(history: dict, as_of: datetime) -> dict:
    """从 30 天历史中按日期查找最近的 FG 值"""
    records = history.get("records", [])
    if not records:
        return {"current_value": 50, "current_classification": "Neutral"}

    as_of_ts = int(as_of.timestamp())
    best = records[0]  # 默认最新
    best_diff = abs(as_of_ts - best["timestamp"])

    for r in records:
        diff = abs(as_of_ts - r["timestamp"])
        if diff < best_diff:
            best = r
            best_diff = diff

    return {
        "current_value": best["value"],
        "current_classification": best.get("classification", "Neutral"),
        "records": records,
        "avg_7d": history.get("avg_7d", 50),
        "avg_30d": history.get("avg_30d", 50),
        "trend": history.get("trend", "neutral"),
        "count": len(records),
    }


def _build_sim_portfolio_ctx() -> str:
    """模拟固定持仓上下文: 10000 USDT 余额, 无持仓"""
    return (
        "### 账户状态\n"
        "USDT 余额: 10000.00 (可用: 10000.00, 已用: 0.00)\n"
        "当前持仓: 0 个\n"
        "多头仓位占比: 0.0%\n"
        "空头仓位占比: 0.0%\n"
        "总仓位占比: 0.0%\n"
    )


def _mock_ft_api_get(endpoint: str):
    """模拟 Freqtrade API 返回"""
    if endpoint == "/balance":
        return {
            "currencies": [
                {"currency": "USDT", "balance": 10000, "free": 10000, "used": 0}
            ]
        }
    if endpoint == "/status":
        return []
    return None


def _evaluate_sim_signals(
    signals: list[dict],
    klines_cache: dict[tuple[str, str], pd.DataFrame],
) -> list[dict]:
    """评估每个模拟信号的表现 (MFE/MAE/SL/TP)"""
    evaluated = []
    for sig in signals:
        symbol = sig.get("symbol", "")
        df_1h = klines_cache.get((symbol, "1h"))
        if df_1h is None or df_1h.empty:
            continue

        entry_range = sig.get("entry_price_range")
        if not entry_range or len(entry_range) != 2 or not entry_range[0]:
            continue

        entry_mid = (entry_range[0] + entry_range[1]) / 2
        is_long = sig.get("action") == "long"

        # 截取信号时间之后的 K 线
        sim_ts_str = sig.get("sim_timestamp", sig.get("timestamp", ""))
        try:
            sim_ts = datetime.fromisoformat(sim_ts_str)
            idx = df_1h.index
            if idx.tz is None:
                mask = idx >= sim_ts.replace(tzinfo=None)
            else:
                mask = idx >= sim_ts
            df_after = df_1h[mask].head(EVAL_BARS)
        except Exception:
            df_after = df_1h.tail(EVAL_BARS)

        if df_after.empty:
            continue

        highs = df_after["high"].values
        lows = df_after["low"].values
        max_high = float(max(highs))
        min_low = float(min(lows))

        if is_long:
            mfe_pct = (max_high - entry_mid) / entry_mid * 100
            mae_pct = (entry_mid - min_low) / entry_mid * 100
        else:
            mfe_pct = (entry_mid - min_low) / entry_mid * 100
            mae_pct = (max_high - entry_mid) / entry_mid * 100

        # 止损触发
        sl_hit = False
        stop_loss = sig.get("stop_loss")
        if stop_loss:
            sl_hit = (min_low <= stop_loss) if is_long else (max_high >= stop_loss)

        # 止盈触发
        tp_hits = 0
        for tp in sig.get("take_profit", []):
            tp_price = tp.get("price") if isinstance(tp, dict) else tp
            if tp_price is None:
                continue
            if is_long and max_high >= tp_price:
                tp_hits += 1
            elif not is_long and min_low <= tp_price:
                tp_hits += 1

        evaluated.append({
            **sig,
            "eval": {
                "entry_mid": round(entry_mid, 2),
                "mfe_pct": round(mfe_pct, 2),
                "mae_pct": round(mae_pct, 2),
                "sl_hit": sl_hit,
                "tp_hits": tp_hits,
                "tp_total": len(sig.get("take_profit", [])),
                "bars_analyzed": len(df_after),
                "win_by_mfe": mfe_pct > mae_pct,
            },
        })

    return evaluated


def _aggregate_results(
    evaluated: list[dict], days: int, interval_hours: int, total_cycles: int,
) -> dict:
    """汇总统计"""
    total = len(evaluated)
    if total == 0:
        return {
            "config": {"days": days, "interval_hours": interval_hours,
                       "total_cycles": total_cycles},
            "signals_generated": 0,
            "overview": {"total": 0},
            "by_symbol": {},
            "by_direction": {},
            "signals": [],
        }

    sl_hit = sum(1 for e in evaluated if e["eval"]["sl_hit"])
    tp_any = sum(1 for e in evaluated if e["eval"]["tp_hits"] > 0)
    win_mfe = sum(1 for e in evaluated if e["eval"]["win_by_mfe"])
    mfe_list = [e["eval"]["mfe_pct"] for e in evaluated]
    mae_list = [e["eval"]["mae_pct"] for e in evaluated]

    overview = {
        "total": total,
        "sl_hit": sl_hit,
        "tp_hit_any": tp_any,
        "win_rate_by_mfe": round(win_mfe / total, 3),
        "avg_mfe_pct": round(sum(mfe_list) / total, 2),
        "avg_mae_pct": round(sum(mae_list) / total, 2),
    }

    # 按币种
    by_symbol: dict[str, list] = {}
    for e in evaluated:
        by_symbol.setdefault(e["symbol"], []).append(e)
    by_symbol_stats = {
        sym: _group_eval_stats(group) for sym, group in sorted(by_symbol.items())
    }

    # 按方向
    by_dir: dict[str, list] = {}
    for e in evaluated:
        by_dir.setdefault(e["action"], []).append(e)
    by_dir_stats = {
        d: _group_eval_stats(group) for d, group in sorted(by_dir.items())
    }

    return {
        "config": {"days": days, "interval_hours": interval_hours,
                   "total_cycles": total_cycles},
        "signals_generated": total,
        "overview": overview,
        "by_symbol": by_symbol_stats,
        "by_direction": by_dir_stats,
        "signals": evaluated,
    }


def _group_eval_stats(group: list[dict]) -> dict:
    """计算一组评估信号的统计量"""
    n = len(group)
    win = sum(1 for e in group if e["eval"]["win_by_mfe"])
    mfe = [e["eval"]["mfe_pct"] for e in group]
    mae = [e["eval"]["mae_pct"] for e in group]
    return {
        "count": n,
        "win_rate": round(win / n, 3) if n else 0,
        "avg_mfe_pct": round(sum(mfe) / n, 2) if n else 0,
        "avg_mae_pct": round(sum(mae) / n, 2) if n else 0,
        "sl_hit": sum(1 for e in group if e["eval"]["sl_hit"]),
        "tp_hit_any": sum(1 for e in group if e["eval"]["tp_hits"] > 0),
    }


def _save_result(result: dict) -> Path:
    """保存模拟结果到 JSON 文件"""
    out_dir = DATA_OUTPUT_DIR / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"sim_{ts}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    logger.info("模拟结果保存到 %s", path)
    return path
