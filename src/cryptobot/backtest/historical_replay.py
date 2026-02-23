"""历史回放引擎 — 用历史K线驱动 LLM 生成多样化交易信号

纯技术面分析 + 单次 LLM 调用（技术分析+交易决策合并），
在过去 N 天历史上产出 50-200+ 信号用于回测统计验证。

核心流程:
1. 智能下载: 每币种/每时间框架一次 API → 全量缓存
2. 逐日切片: 按 as_of 时间点切片 K 线 → 计算技术指标
3. LLM 批次: 合并 prompt → call_claude_parallel
4. 交易模拟: simulate_trade() → _build_report() → BacktestReport
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

from cryptobot.config import DATA_OUTPUT_DIR, get_all_symbols

logger = logging.getLogger(__name__)

_PROGRESS_DIR = DATA_OUTPUT_DIR / "backtest"


def _progress_file_for(config: "ReplayConfig") -> Path:
    """根据配置生成唯一进度文件名"""
    key = f"{config.days}_{config.interval_hours}"
    return _PROGRESS_DIR / f"replay_progress_{key}.json"

# K 线每根跨度（小时）
_TF_HOURS = {"1h": 1, "4h": 4, "1d": 24}
# 指标计算需要的最小 K 线数
_MIN_BARS = 100
# 每个时间框架下载的 K 线数
_DOWNLOAD_LIMIT = 1500

TIMEFRAMES = ["1h", "4h", "1d"]


# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayConfig:
    """历史回放配置"""

    days: int = 90
    symbols: list[str] = field(default_factory=list)
    interval_hours: int = 24
    llm_model: str = "sonnet"
    max_concurrent: int = 5
    initial_capital: float = 10000.0
    max_leverage: int = 5


@dataclass(frozen=True)
class ReplaySnapshot:
    """单币种某时刻的技术分析快照"""

    symbol: str
    as_of: str  # ISO format
    current_price: float
    tech_indicators: dict
    multi_timeframe: dict
    support_resistance: dict


# ── K 线下载与切片 ────────────────────────────────────────────────────────


def _download_paginated(
    symbol: str,
    tf: str,
    needed_bars: int,
    end_time_ms: int | None = None,
) -> pd.DataFrame:
    """分页下载 K 线，支持超过 1500 根

    Binance 每次最多 1500 根。需要更多时，用前一批最早时间戳作为
    下一页的 endTime 进行分页拼接。
    """
    from cryptobot.indicators.calculator import download_klines

    frames: list[pd.DataFrame] = []
    remaining = needed_bars
    cursor_end = end_time_ms

    while remaining > 0:
        batch_size = min(remaining, _DOWNLOAD_LIMIT)
        df = download_klines(symbol, tf, batch_size, end_time=cursor_end)

        if df.empty:
            break

        frames.append(df)
        remaining -= len(df)

        # 如果返回不足请求数，说明已无更多数据
        if len(df) < batch_size:
            break

        # 下一页: endTime = 当前批次最早时间戳 - 1ms
        earliest_ts = int(df.index[0].timestamp() * 1000) - 1
        cursor_end = earliest_ts

        # 简单限流
        time.sleep(0.2)

    if not frames:
        return pd.DataFrame()

    # 按时间排序 + 去重
    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    return combined


def _download_full_klines(
    symbols: list[str],
    days: int = 90,
    end_time_ms: int | None = None,
) -> dict[tuple[str, str], pd.DataFrame]:
    """智能下载全量 K 线（自动分页覆盖完整回放周期）

    Args:
        symbols: 币种列表
        days: 回放天数，用于计算每 TF 所需 K 线数
        end_time_ms: 结束时间戳（毫秒），None 则取当前时间

    Returns:
        {(symbol, tf): DataFrame} 缓存
    """
    cache: dict[tuple[str, str], pd.DataFrame] = {}

    for sym in symbols:
        for tf in TIMEFRAMES:
            # 计算所需 K 线数: 回放天数 + 指标 lookback 缓冲
            tf_hours = _TF_HOURS[tf]
            needed = (days * 24) // tf_hours + _MIN_BARS * 2
            try:
                df = _download_paginated(sym, tf, needed, end_time_ms)
                if not df.empty:
                    cache[(sym, tf)] = df
                    logger.debug("下载 %s %s: %d 根 K 线 (需要 %d)", sym, tf, len(df), needed)
            except Exception as e:
                logger.warning("下载 %s %s 失败: %s", sym, tf, e)

    logger.info("K 线下载完成: %d 个缓存条目", len(cache))
    return cache


def _slice_klines_at(
    cache: dict[tuple[str, str], pd.DataFrame],
    symbol: str,
    as_of: datetime,
) -> dict[tuple[str, str], pd.DataFrame] | None:
    """按时间点切片，返回 klines_override 格式

    保留 as_of 之前的 K 线（含 as_of 所在根），每 TF 至少 _MIN_BARS 根。
    """
    result: dict[tuple[str, str], pd.DataFrame] = {}

    for tf in TIMEFRAMES:
        key = (symbol, tf)
        df = cache.get(key)
        if df is None or df.empty:
            return None

        # 确保 index 是 naive datetime 以便比较
        idx = df.index
        as_of_naive = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of

        sliced = df[idx <= as_of_naive]
        if len(sliced) < _MIN_BARS:
            return None

        # 保留最新 _MIN_BARS * 2 根（足够指标计算）
        result[key] = sliced.tail(_MIN_BARS * 2)

    return result


# ── 技术快照构建 ──────────────────────────────────────────────────────────


def _build_snapshot(
    symbol: str,
    as_of: datetime,
    sliced: dict[tuple[str, str], pd.DataFrame],
) -> ReplaySnapshot | None:
    """利用 klines_override 注入历史 K 线，构建技术分析快照"""
    from cryptobot.indicators.calculator import calc_all_indicators, klines_override
    from cryptobot.indicators.multi_timeframe import (
        calc_multi_timeframe,
        calc_support_resistance,
    )

    try:
        with klines_override(sliced):
            tech = calc_all_indicators(symbol, "4h")
            multi_tf = calc_multi_timeframe(symbol)
            sr = calc_support_resistance(symbol)
    except Exception as e:
        logger.warning("构建快照失败 %s @ %s: %s", symbol, as_of.isoformat(), e)
        return None

    current_price = tech.get("latest_close", 0)
    if not current_price:
        return None

    # 提取 4h close 用于 Hurst 指数计算
    key_4h = (symbol, "4h")
    if key_4h in sliced and "close" in sliced[key_4h].columns:
        tech = {**tech, "_closes_4h": sliced[key_4h]["close"].tolist()}

    return ReplaySnapshot(
        symbol=symbol,
        as_of=as_of.isoformat(),
        current_price=current_price,
        tech_indicators=tech,
        multi_timeframe=multi_tf,
        support_resistance=sr,
    )


# ── LLM 调用 ─────────────────────────────────────────────────────────────

REPLAY_TRADER_PROMPT = """\
你是一位专业的加密货币合约交易员，同时具备技术分析能力。

## 数据说明
你将收到该币种在特定历史时间点的技术指标快照。
注意: 本次分析仅基于技术面数据，链上/情绪/新闻不可用。

## 分析框架
1. 趋势判断: EMA 排列 + ADX 强度 + MACD 动量
2. 多时间框架共振: 1h/4h/1d 方向一致则信心更高
3. 动量: RSI + StochRSI + MFI
4. 波动率: 布林带位置 + ATR
5. 关键价位: 支撑/阻力位设定入场和止损

## 决策规则
- 置信度 < 55 → no_trade
- 止损基于 ATR + 关键价位
- 分批止盈（至少 2 级）
- 杠杆不超过 {max_leverage}x

## 输出格式
严格按 JSON Schema 输出。
"""


def _detect_replay_regime(snapshot: ReplaySnapshot) -> str:
    """基于快照技术指标 + Hurst 指数推断 regime + 平滑

    规则:
    - ATR% > 3 → volatile (优先)
    - ADX(0.7) + Hurst(0.3) 加权评分 > 0.5 → trending
    - 其他 → ranging

    回放模式下调用 regime_smoother (is_simulation=True) 做平滑，
    并标注 regime_source: "replay_estimated"。
    """
    from cryptobot.indicators.hurst import calc_hurst_exponent, classify_hurst
    from cryptobot.regime_smoother import smooth_regime_transition

    tech = snapshot.tech_indicators or {}
    volatility = tech.get("volatility", {})
    trend = tech.get("trend", {})

    atr_pct = volatility.get("atr_pct", 0)
    adx = trend.get("adx", 0)

    if atr_pct > 3:
        raw_regime = "volatile"
    else:
        # Hurst 计算: 从 _closes_4h 提取 (如有)
        closes = tech.get("_closes_4h", [])
        hurst_val = calc_hurst_exponent(closes) if closes else 0.5
        hurst_hint, hurst_conf = classify_hurst(hurst_val)

        # 加权评分: Hurst random 时中性 (不干预 ADX)
        adx_score = min(adx / 50.0, 1.0)
        if hurst_hint == "trending":
            hurst_trending = hurst_conf
        elif hurst_hint == "random":
            hurst_trending = 0.5
        else:
            hurst_trending = 0.0
        trending_score = adx_score * 0.7 + hurst_trending * 0.3

        raw_regime = "trending" if trending_score > 0.5 else "ranging"

    # 回放模式平滑 (is_simulation=True 跳过持久化)
    smoothed, _ = smooth_regime_transition(raw_regime, is_simulation=True)
    return smoothed


def _format_snapshot_prompt(snapshot: ReplaySnapshot, max_leverage: int) -> str:
    """将快照格式化为 LLM prompt"""
    data = {
        "symbol": snapshot.symbol,
        "as_of": snapshot.as_of,
        "current_price": snapshot.current_price,
        "tech_indicators": snapshot.tech_indicators,
        "multi_timeframe": snapshot.multi_timeframe,
        "support_resistance": snapshot.support_resistance,
    }

    return (
        f"## {snapshot.symbol} 技术分析快照 (截至 {snapshot.as_of})\n\n"
        f"当前价格: {snapshot.current_price}\n\n"
        f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```\n\n"
        f"请基于以上数据做出交易决策。杠杆上限 {max_leverage}x。"
    )


def _run_llm_batch(
    snapshots: list[ReplaySnapshot],
    config: ReplayConfig,
) -> list[dict | str]:
    """批量 LLM 调用（每个快照独立 regime addon）"""
    from cryptobot.workflow.llm import call_claude_parallel
    from cryptobot.workflow.prompts import TRADE_SCHEMA
    from cryptobot.evolution.regime_prompts import get_regime_addon

    base_prompt = REPLAY_TRADER_PROMPT.format(max_leverage=config.max_leverage)

    tasks = []
    for snap in snapshots:
        prompt = _format_snapshot_prompt(snap, config.max_leverage)

        # 检测 regime → 注入对应 addon
        regime = _detect_replay_regime(snap)
        regime_addon = get_regime_addon(regime, "TRADER")
        system_prompt = base_prompt + regime_addon

        tasks.append({
            "prompt": prompt,
            "model": config.llm_model,
            "role": "trader",
            "system_prompt": system_prompt,
            "json_schema": TRADE_SCHEMA,
        })

    return call_claude_parallel(tasks, max_workers=config.max_concurrent)


# ── 信号解析 ──────────────────────────────────────────────────────────────


def _parse_to_signal(
    llm_output: dict | str | None,
    snapshot: ReplaySnapshot,
) -> dict | None:
    """将 LLM 输出解析为标准信号 dict"""
    if llm_output is None:
        return None

    if isinstance(llm_output, str):
        try:
            llm_output = json.loads(llm_output)
        except json.JSONDecodeError:
            return None

    if not isinstance(llm_output, dict):
        return None

    action = llm_output.get("action")
    if action not in ("long", "short"):
        return None

    confidence = llm_output.get("confidence", 0)
    if confidence < 55:
        return None

    entry_range = llm_output.get("entry_price_range", [])
    stop_loss = llm_output.get("stop_loss")
    take_profit = llm_output.get("take_profit", [])
    leverage = llm_output.get("leverage", 3)

    if not entry_range or len(entry_range) < 2:
        return None
    if stop_loss is None:
        return None

    # 止损方向校验
    entry_mid = (entry_range[0] + entry_range[1]) / 2
    if action == "long" and stop_loss >= entry_mid:
        return None
    if action == "short" and stop_loss <= entry_mid:
        return None

    # 标准化 take_profit 格式
    tp_list = []
    if isinstance(take_profit, list):
        for tp in take_profit:
            if isinstance(tp, dict) and "price" in tp:
                tp_list.append(tp)
            elif isinstance(tp, (int, float)):
                tp_list.append({"price": tp, "ratio": 1.0 / max(1, len(take_profit))})

    return {
        "symbol": snapshot.symbol,
        "action": action,
        "entry_price_range": entry_range,
        "stop_loss": stop_loss,
        "take_profit": tp_list,
        "leverage": min(leverage, 5),
        "confidence": confidence,
        "timestamp": snapshot.as_of,
        "signal_source": "replay",
        "regime_source": "replay_estimated",
        "reasoning": llm_output.get("reasoning", ""),
    }


# ── 断点续跑 ─────────────────────────────────────────────────────────────


def _save_progress(
    signals: list[dict],
    completed_dates: list[str],
    config: ReplayConfig,
) -> None:
    """保存进度到 JSON"""
    _PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    progress_file = _progress_file_for(config)
    data = {
        "config": {
            "days": config.days,
            "symbols": config.symbols,
            "interval_hours": config.interval_hours,
        },
        "completed_dates": completed_dates,
        "signals": signals,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = progress_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(progress_file)


def _load_progress(config: ReplayConfig) -> tuple[list[dict], list[str]]:
    """加载已完成进度，返回 (signals, completed_dates)

    仅当配置匹配时才复用。
    """
    progress_file = _progress_file_for(config)
    if not progress_file.exists():
        return [], []

    try:
        data = json.loads(progress_file.read_text())
    except (json.JSONDecodeError, OSError):
        return [], []

    saved_config = data.get("config", {})
    if (
        saved_config.get("days") != config.days
        or saved_config.get("symbols") != config.symbols
        or saved_config.get("interval_hours") != config.interval_hours
    ):
        return [], []

    return data.get("signals", []), data.get("completed_dates", [])


def _clear_progress(config: ReplayConfig) -> None:
    """清除进度文件"""
    progress_file = _progress_file_for(config)
    if progress_file.exists():
        progress_file.unlink()


# ── 主入口 ────────────────────────────────────────────────────────────────


def run_historical_replay(
    config: ReplayConfig | None = None,
    resume: bool = False,
    on_day_done: callable = None,
):
    """历史回放主入口

    Args:
        config: 回放配置，None 使用默认
        resume: 是否断点续跑
        on_day_done: 每天完成回调 (day_idx, total_days, date_str, n_signals)

    Returns:
        BacktestReport
    """
    from cryptobot.backtest.engine import _build_report
    from cryptobot.backtest.trade_simulator import simulate_trade
    from cryptobot.backtest.cost_model import CostConfig

    if config is None:
        config = ReplayConfig()

    symbols = config.symbols or get_all_symbols()[:5]

    # Phase 0: 断点续跑
    if resume:
        signals, completed_dates = _load_progress(config)
        logger.info("断点续跑: 已完成 %d 天, %d 个信号", len(completed_dates), len(signals))
    else:
        signals, completed_dates = [], []

    # 生成采样日期
    now = datetime.now(timezone.utc)
    sample_dates = []
    for i in range(config.days, 0, -config.interval_hours // 24 if config.interval_hours >= 24 else -1):
        dt = now - timedelta(days=i)
        # 对齐到 UTC 0:00
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        sample_dates.append(dt)

    # 如果 interval_hours < 24，按小时间隔
    if config.interval_hours < 24:
        sample_dates = []
        total_hours = config.days * 24
        for h in range(total_hours, 0, -config.interval_hours):
            dt = now - timedelta(hours=h)
            dt = dt.replace(minute=0, second=0, microsecond=0)
            sample_dates.append(dt)

    # 过滤已完成的日期
    pending_dates = [
        d for d in sample_dates if d.isoformat() not in completed_dates
    ]

    if not pending_dates:
        logger.info("所有日期已完成")
    else:
        # Phase 1: 下载全量 K 线（自动分页覆盖完整回放周期）
        end_time_ms = int(now.timestamp() * 1000)
        klines_cache = _download_full_klines(symbols, config.days, end_time_ms)

        if not klines_cache:
            logger.error("无法下载 K 线数据")
            from cryptobot.backtest.engine import _empty_report
            return _empty_report(config.days, "replay", config.initial_capital)

        # Phase 2 + 3: 逐日构建快照 + LLM 决策
        for day_idx, as_of in enumerate(pending_dates):
            date_str = as_of.isoformat()
            day_signals = []

            # 构建每个币种的快照
            snapshots = []
            for sym in symbols:
                sliced = _slice_klines_at(klines_cache, sym, as_of)
                if sliced is None:
                    continue
                snap = _build_snapshot(sym, as_of, sliced)
                if snap is not None:
                    snapshots.append(snap)

            if not snapshots:
                logger.debug("日期 %s 无有效快照，跳过", date_str)
                completed_dates.append(date_str)
                if on_day_done:
                    on_day_done(day_idx, len(pending_dates), date_str, 0)
                continue

            # LLM 批次决策
            llm_results = _run_llm_batch(snapshots, config)

            for snap, llm_out in zip(snapshots, llm_results):
                sig = _parse_to_signal(llm_out, snap)
                if sig is not None:
                    day_signals.append(sig)

            signals.extend(day_signals)
            completed_dates.append(date_str)

            # 保存进度
            _save_progress(signals, completed_dates, config)

            if on_day_done:
                on_day_done(day_idx, len(pending_dates), date_str, len(day_signals))

            logger.info(
                "[%d/%d] %s — %d 个信号 (累计 %d)",
                day_idx + 1, len(pending_dates), date_str,
                len(day_signals), len(signals),
            )

    # Phase 4: 交易模拟 + 报告
    if not signals:
        from cryptobot.backtest.engine import _empty_report
        return _empty_report(config.days, "replay", config.initial_capital)

    cost_config = CostConfig()

    # 下载 1h K 线用于模拟（分页覆盖完整回放周期）
    klines_1h: dict[str, pd.DataFrame] = {}
    sim_symbols = list({s["symbol"] for s in signals})
    needed_sim_bars = config.days * 24 + 200
    for sym in sim_symbols:
        try:
            klines_1h[sym] = _download_paginated(sym, "1h", needed_sim_bars)
        except Exception as e:
            logger.warning("下载 %s 1h K线失败: %s", sym, e)

    trades = []
    for sig in signals:
        sym = sig.get("symbol", "")
        kl = klines_1h.get(sym)
        if kl is None or kl.empty:
            continue
        result = simulate_trade(sig, kl, cost_config)
        if result is not None:
            trades.append(result)

    logger.info("交易模拟完成: %d/%d 笔有效交易", len(trades), len(signals))

    report = _build_report(
        trades=trades,
        days=config.days,
        source="replay",
        signal_source="replay",
        initial_capital=config.initial_capital,
        total_signals=len(signals),
    )

    # 清除进度文件
    _clear_progress(config)

    return report
