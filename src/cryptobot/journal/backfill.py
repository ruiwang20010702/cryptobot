"""历史信号回填

从 Binance 历史 4h K 线 + TA-Lib 技术指标生成模拟 closed SignalRecord。
不调用 LLM，零成本。目标：6 个月 × 10 币种，~120-150 笔 closed 记录。
"""

import hashlib
import logging
from dataclasses import dataclass, field

import pandas as pd

from cryptobot.config import get_all_symbols, get_pair_config
from cryptobot.indicators.calculator import (
    calc_all_indicators,
    download_klines,
    klines_override,
)
from cryptobot.journal.models import SignalRecord
from cryptobot.journal.storage import get_record, save_record

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────────────────────────

STEP_BARS = 12           # 每 12 根 4h 检查一次 (2天)
SIGNAL_THRESHOLD = 2.0   # |score| > 2 才触发
ATR_SL_MULTIPLIER = 1.5
ATR_TP1_MULTIPLIER = 2.0
MAX_BARS_HOLD = 48       # 8 天超时
MIN_WARMUP_BARS = 120    # 指标预热
KLINES_LIMIT = 1100      # 约 183 天
PROMPT_VERSION = "backfill-v1"


@dataclass
class BackfillResult:
    """回填结果汇总"""

    total_generated: int = 0
    total_saved: int = 0
    skipped_existing: int = 0
    by_symbol: dict = field(default_factory=dict)
    by_exit_reason: dict = field(default_factory=dict)
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    errors: list[str] = field(default_factory=list)


# ─── 公开 API ──────────────────────────────────────────────────────────────


def run_backfill(
    *,
    days: int = 180,
    symbols: list[str] | None = None,
    dry_run: bool = False,
) -> BackfillResult:
    """执行历史信号回填

    Args:
        days: 回溯天数，决定下载 K 线数量
        symbols: 指定币种列表，None 则全部
        dry_run: 预览模式，不写入记录
    """
    target_symbols = symbols or get_all_symbols()
    limit = max(MIN_WARMUP_BARS + 50, int(days * 24 / 4))  # 4h K 线数量

    result = BackfillResult()
    all_records: list[SignalRecord] = []

    for sym in target_symbols:
        try:
            records = _backfill_symbol(sym, limit=limit, dry_run=dry_run)
            result.by_symbol[sym] = len(records)
            all_records.extend(records)
        except Exception as e:
            msg = f"{sym}: {e}"
            logger.warning("回填失败 %s", msg)
            result.errors.append(msg)
            result.by_symbol[sym] = 0

    # 汇总统计
    result.total_generated = len(all_records)
    saved = [r for r in all_records if r.status == "closed"]
    result.total_saved = len(saved)
    result.skipped_existing = result.total_generated - result.total_saved

    for r in all_records:
        reason = r.exit_reason or "unknown"
        result.by_exit_reason[reason] = result.by_exit_reason.get(reason, 0) + 1

    wins = [r for r in all_records if (r.actual_pnl_pct or 0) > 0]
    result.win_rate = len(wins) / len(all_records) if all_records else 0.0

    pnls = [r.actual_pnl_pct for r in all_records if r.actual_pnl_pct is not None]
    result.avg_pnl_pct = sum(pnls) / len(pnls) if pnls else 0.0

    return result


# ─── 单币种回填 ────────────────────────────────────────────────────────────


def _backfill_symbol(
    symbol: str,
    limit: int = KLINES_LIMIT,
    dry_run: bool = False,
) -> list[SignalRecord]:
    """单币种回填，返回生成的记录列表"""
    df = download_klines(symbol, "4h", limit=limit)
    if len(df) < MIN_WARMUP_BARS + STEP_BARS:
        logger.warning("%s K 线不足 (%d)，跳过", symbol, len(df))
        return []

    pair_cfg = get_pair_config(symbol) or {}
    leverage = pair_cfg.get("default_leverage", 2)

    records: list[SignalRecord] = []
    bar_pos = MIN_WARMUP_BARS

    while bar_pos + MAX_BARS_HOLD < len(df):
        # 切片到当前位置
        slice_df = df.iloc[: bar_pos + 1]
        cache = {(symbol, "4h"): slice_df}

        with klines_override(cache):
            indicators = calc_all_indicators(symbol, "4h")

        signals = indicators.get("signals", {})
        score = signals.get("technical_score", 0)
        bias = signals.get("bias", "neutral")

        if abs(score) <= SIGNAL_THRESHOLD:
            bar_pos += STEP_BARS
            continue

        # 生成信号
        action = "long" if score > 0 else "short"
        entry_price = df.iloc[bar_pos]["close"]
        entry_time = df.index[bar_pos]
        atr_val = indicators.get("volatility", {}).get("atr_14")

        if not atr_val or atr_val <= 0:
            bar_pos += STEP_BARS
            continue

        sl_price = _calc_sl(entry_price, atr_val, action)
        tp_price = _calc_tp(entry_price, atr_val, action)

        # 模拟出场
        future_bars = df.iloc[bar_pos + 1: bar_pos + 1 + MAX_BARS_HOLD]
        exit_price, exit_reason, bars_held = _simulate_exit(
            future_bars, entry_price, sl_price, tp_price, action,
        )

        pnl_pct = _calc_pnl(entry_price, exit_price, action, leverage)
        duration_hours = bars_held * 4.0

        signal_id = _make_signal_id(symbol, str(entry_time))

        record = SignalRecord(
            signal_id=signal_id,
            symbol=symbol,
            action=action,
            timestamp=str(entry_time),
            confidence=_score_to_confidence(abs(score)),
            entry_price_range=[entry_price * 0.998, entry_price * 1.002],
            stop_loss=round(sl_price, 6),
            take_profit=[round(tp_price, 6)],
            leverage=leverage,
            reasoning=f"backfill: score={score:.1f}, bias={bias}",
            actual_entry_price=entry_price,
            actual_exit_price=exit_price,
            actual_pnl_pct=round(pnl_pct, 2),
            actual_pnl_usdt=None,
            exit_reason=exit_reason,
            duration_hours=round(duration_hours, 1),
            analyst_votes=_build_analyst_votes(bias),
            prompt_version=PROMPT_VERSION,
            status="closed",
        )

        if not dry_run:
            existing = get_record(signal_id)
            if existing:
                # 幂等：跳过已存在的记录
                pass
            else:
                save_record(record)

        records.append(record)

        # 出场后才跳到下一个检查点
        bar_pos += bars_held + 1

    return records


# ─── 辅助函数 ──────────────────────────────────────────────────────────────


def _make_signal_id(symbol: str, timestamp: str) -> str:
    """幂等 signal_id: md5(symbol + timestamp)[:12]"""
    raw = f"{symbol}:{timestamp}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _calc_sl(entry: float, atr: float, action: str) -> float:
    """ATR 止损"""
    offset = atr * ATR_SL_MULTIPLIER
    return entry - offset if action == "long" else entry + offset


def _calc_tp(entry: float, atr: float, action: str) -> float:
    """ATR 止盈"""
    offset = atr * ATR_TP1_MULTIPLIER
    return entry + offset if action == "long" else entry - offset


def _simulate_exit(
    future_bars: pd.DataFrame,
    entry: float,
    sl: float,
    tp: float,
    action: str,
) -> tuple[float, str, int]:
    """模拟出场逻辑

    Returns:
        (exit_price, exit_reason, bars_held)
    """
    for i in range(len(future_bars)):
        bar = future_bars.iloc[i]
        high, low = float(bar["high"]), float(bar["low"])

        # SL 优先于 TP（保守假设）
        if action == "long":
            if low <= sl:
                return sl, "sl_hit", i + 1
            if high >= tp:
                return tp, "tp_hit", i + 1
        else:
            if high >= sl:
                return sl, "sl_hit", i + 1
            if low <= tp:
                return tp, "tp_hit", i + 1

    # 超时：用最后一根 K 线收盘价
    if len(future_bars) > 0:
        return float(future_bars.iloc[-1]["close"]), "timeout", len(future_bars)
    return entry, "timeout", 0


def _calc_pnl(entry: float, exit_price: float, action: str, leverage: int) -> float:
    """计算盈亏百分比（含杠杆）"""
    if entry == 0:
        return 0.0
    if action == "long":
        raw = (exit_price - entry) / entry
    else:
        raw = (entry - exit_price) / entry
    return raw * leverage * 100


def _score_to_confidence(abs_score: float) -> int:
    """技术分数映射置信度"""
    if abs_score >= 7:
        return 85
    if abs_score >= 5:
        return 75
    if abs_score >= 3:
        return 70
    return 65


def _build_analyst_votes(bias: str) -> dict:
    """构造 analyst_votes"""
    return {
        "technical": bias,
        "onchain": "neutral",
        "fundamental": "neutral",
        "news": "neutral",
    }
